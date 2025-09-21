import sys
import os
import socket
import time
import select
from typing import Dict, Tuple

# Make shared/ importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
	sys.path.insert(0, ROOT)

from A.shared.uap import *  # MAGIC, VERSION, encode/split_packet, CMD_*

# Configurable inactivity timeout before server closes a session with GOODBYE
SESSION_TIMEOUT = float(os.environ.get("UAP_SESSION_TIMEOUT", "15.0"))  # seconds


class Server:
	"""
	Single-threaded UDP server implementing the UAP protocol.

	- Accepts clients that start with HELLO; responds HELLO and enters Receive.
	- For each DATA: prints line with session id and client sequence, sends ALIVE.
	- Detects duplicates and lost packets and prints messages accordingly.
	- On client GOODBYE: prints notice, replies GOODBYE, closes session.
	- On inactivity timeout: sends GOODBYE and closes session.

	Output format mirrors the threaded reference server for grading.
	"""

	def __init__(self, port: int):
		self.port = port
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# Allow quick restarts
		try:
			self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		except Exception:
			pass
		self.sock.bind(("0.0.0.0", port))

		# Global logical clock and sequence for server-originated packets
		self.lclock = 0
		self.server_seq = 0

		# Active sessions keyed by session_id
		self.sessions: Dict[int, Session] = {}

		# Non-blocking loop cadence
		self.poll_interval = 0.05  # seconds

	# Logical clock helpers
	def bump_clock(self):
		self.lclock += 1
		return self.lclock

	def merge_clock(self, their: int):
		self.lclock = max(self.lclock, their) + 1
		return self.lclock

	# Send helper using global server lclock and server_seq
	def send(self, addr: Tuple[str, int], cmd: int, session_id: int, payload: bytes = b""):
		self.bump_clock()
		pkt = encode(cmd, self.server_seq, session_id, self.lclock, payload)
		self.server_seq += 1
		self.sock.sendto(pkt, addr)

	def _cleanup_timeouts(self, now_s: float):
		# Collect expired sessions first to avoid modifying dict while iterating
		expired: list[Session] = []
		for sess in list(self.sessions.values()):
			if sess.state == "Receive" and now_s >= sess.deadline_s:
				expired.append(sess)
		for sess in expired:
			# timeout / GOODBYE -> Done
			self.bump_clock()
			self.send(sess.addr, CMD_GOODBYE, sess.session_id)
			sess.state = "Done"
			print(f"0x{sess.session_id:08x} Session closed", flush=True)
			self.sessions.pop(sess.session_id, None)

	def _check_stdin_quit(self) -> bool:
		"""Return True if the user pressed 'q' (interactive TTY only)."""
		if not sys.stdin.isatty():
			return False
		# On Windows, select() doesn't work with stdin; do a non-blocking peek
		try:
			import msvcrt  # type: ignore
			while msvcrt.kbhit():
				ch = msvcrt.getwch()
				if ch in ("\r", "\n"):
					# ignore newlines
					continue
				if ch.lower() == "q":
					# consume the rest of the line if any
					return True
			return False
		except Exception:
			# Fallback: try a non-blocking select on POSIX
			try:
				r, _, _ = select.select([sys.stdin], [], [], 0)
				if r:
					line = sys.stdin.readline()
					if line.rstrip("\n").lower() == "q":
						return True
			except Exception:
				pass
			return False

	def serve_forever(self):
		print(f"Waiting on port {self.port}...", flush=True)
		self.sock.setblocking(False)
		try:
			while True:
				# 1) Poll socket for any inbound datagrams
				try:
					r, _, _ = select.select([self.sock], [], [], self.poll_interval)
				except (OSError, ValueError):
					break  # socket closed or error

				if r:
					try:
						data, addr = self.sock.recvfrom(65535)
					except BlockingIOError:
						data = None
					if data:
						hdr, payload = split_packet(data)
						if hdr:
							self._handle_packet(hdr, payload, addr)

				# 2) Handle session timeouts
				self._cleanup_timeouts(time.time())

				# 3) Check interactive quit
				if self._check_stdin_quit():
					# Bump logical clock for stdin event and close all sessions
					self.bump_clock()
					# Print marker that server received 'q'
					print("q", flush=True)
					for sess in list(self.sessions.values()):
						# First send a DATA message so clients can display a goodbye note
						self.send(sess.addr, CMD_DATA, sess.session_id, b"Good bye from server")
						# Then send GOODBYE control
						self.send(sess.addr, CMD_GOODBYE, sess.session_id)
						print(f"0x{sess.session_id:08x} Session closed", flush=True)
					self.sessions.clear()
					break
		finally:
			try:
				self.sock.close()
			except Exception:
				pass

	def _handle_packet(self, hdr: dict, payload: bytes, addr: Tuple[str, int]):
		# Update server logical clock and lookup session
		self.merge_clock(hdr["lclock"])  # receive event
		sess_id = hdr["session"]
		cmd = hdr["cmd"]
		client_seq = hdr["seq"]

		sess = self.sessions.get(sess_id)
		if sess is None:
			# New sessions must start with HELLO
			if cmd != CMD_HELLO:
				self.send(addr, CMD_GOODBYE, sess_id)
				return
			# Create and acknowledge session
			sess = Session(server=self, addr=addr, session_id=sess_id)
			self.sessions[sess_id] = sess
			sess.handle_hello(client_seq)
			return

		# Existing session dispatch
		if sess.state != "Receive":
			return

		if cmd == CMD_HELLO:
			# Protocol error: close immediately
			self.send(addr, CMD_GOODBYE, sess_id)
			sess.state = "Done"
			print(f"0x{sess.session_id:08x} Session closed", flush=True)
			self.sessions.pop(sess.session_id, None)
			return

		if cmd == CMD_GOODBYE:
			# GOODBYE / GOODBYE -> Done
			print(f"0x{sess.session_id:08x} [{client_seq}] GOODBYE from client.", flush=True)
			self.send(addr, CMD_GOODBYE, sess_id)
			sess.state = "Done"
			print(f"0x{sess.session_id:08x} Session closed", flush=True)
			self.sessions.pop(sess.session_id, None)
			return

		if cmd != CMD_DATA:
			# Unknown/unsupported -> close
			self.send(addr, CMD_GOODBYE, sess_id)
			sess.state = "Done"
			print(f"0x{sess.session_id:08x} Session closed", flush=True)
			self.sessions.pop(sess.session_id, None)
			return

		# DATA handling
		if client_seq < sess.last_received:
			# From the past -> protocol error: close
			self.send(addr, CMD_GOODBYE, sess_id)
			sess.state = "Done"
			print(f"0x{sess.session_id:08x} Session closed", flush=True)
			self.sessions.pop(sess.session_id, None)
			return

		if client_seq == sess.last_received:
			# Duplicate
			print(f"0x{sess.session_id:08x} [{client_seq}] Duplicate packet!", flush=True)
			return

		if client_seq > sess.next_expected:
			# Print one line per lost packet sequence
			for missing in range(sess.next_expected, client_seq):
				print(f"0x{sess.session_id:08x} [{missing}] Lost packet!", flush=True)

		# Normal accept of this DATA
		txt = payload.decode("utf-8", errors="replace")
		print(f"0x{sess.session_id:08x} [{client_seq}] {txt}", flush=True)

		# Respond with ALIVE and refresh inactivity deadline
		self.send(addr, CMD_ALIVE, sess_id)
		sess.last_received = client_seq
		sess.next_expected = client_seq + 1
		sess.deadline_s = time.time() + SESSION_TIMEOUT


class Session:
	def __init__(self, server: Server, addr: Tuple[str, int], session_id: int):
		self.server = server
		self.addr = addr
		self.session_id = session_id
		self.state = "Init"
		self.last_received = -1
		self.next_expected = 0
		self.deadline_s = time.time() + SESSION_TIMEOUT

	def handle_hello(self, client_seq: int):
		# Include HELLO in flow and start Receive state
		print(f"0x{self.session_id:08x} [{client_seq}] Session created", flush=True)
		self.server.send(self.addr, CMD_HELLO, self.session_id)
		self.state = "Receive"
		self.last_received = client_seq
		self.next_expected = client_seq + 1
		self.deadline_s = time.time() + SESSION_TIMEOUT


def main():
	if len(sys.argv) != 2:
		print("Usage: ./server <portnum>")
		sys.exit(1)
	port = int(sys.argv[1])
	Server(port).serve_forever()


if __name__ == "__main__":
	main()

