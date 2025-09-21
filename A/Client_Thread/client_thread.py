import socket
import sys
import os
import threading
import time
from typing import Optional

# Make shared/ importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
	sys.path.insert(0, ROOT)

from shared.uap import *  # MAGIC, VERSION, encode/split_packet, CMD_*

HELLO_TIMEOUT = 2.0  # seconds
ALIVE_TIMEOUT = 2.0  # seconds


class ClientState:
	HELLO_WAIT = "Hello Wait"
	READY = "Ready"
	READY_TIMER = "Ready Timer"
	CLOSING = "Closing"
	CLOSED = "Closed"


class UAPClient:
	"""
	Threaded UDP client implementing the UAP protocol handshake and timers.

	- Sends HELLO and waits for server HELLO or timeout.
	- Reads stdin; for each line sends DATA. Transitions to Ready Timer and sets
	  an ALIVE timeout. Receiving ALIVE cancels the timer and returns to Ready.
	- On EOF (or input 'q' when interactive), sends GOODBYE and waits for
	  ALIVE-timeout before closing if the server doesn't respond with GOODBYE.
	- At any time, server GOODBYE transitions to CLOSED immediately.
	"""

	def __init__(self, host: str, port: int):
		self.addr = (host, port)
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# connect() binds a default remote; send/recv without addr
		self.sock.connect(self.addr)
		self.sock.settimeout(0.1)

		# Protocol bookkeeping
		self.session_id = new_session_id()
		self.seq = 0
		self.lclock = 0
		self.state = ClientState.HELLO_WAIT
		self.closing_sent = False

		# Concurrency
		self.lock = threading.RLock()
		self.shutdown_evt = threading.Event()
		self.timer: Optional[threading.Timer] = None

		# Input mode
		self.is_tty = sys.stdin.isatty()
		self.file_send_delay = float(os.getenv("UAP_FILE_SEND_DELAY", "0.0"))

	# Logical clock helpers
	def bump_clock(self):
		with self.lock:
			self.lclock += 1
			return self.lclock

	def merge_clock(self, their: int):
		with self.lock:
			self.lclock = max(self.lclock, their) + 1
			return self.lclock

	# Timer helpers
	def _cancel_timer(self):
		t = self.timer
		self.timer = None
		if t is not None:
			t.cancel()

	def _set_timer(self, seconds: float):
		self._cancel_timer()
		self.timer = threading.Timer(seconds, self._on_timeout)
		self.timer.daemon = True
		self.timer.start()

	def _on_timeout(self):
		with self.lock:
			st = self.state
		if st == ClientState.HELLO_WAIT:
			# Hello timeout -> send GOODBYE and enter Closing
			self._send_goodbye_and_enter_closing()
		elif st == ClientState.READY_TIMER:
			# Alive timeout -> send GOODBYE and enter Closing
			self._send_goodbye_and_enter_closing()
		elif st == ClientState.CLOSING:
			# Final timeout -> close
			# Timeout is an event; bump logical clock even if no packet is sent
			self.bump_clock()
			with self.lock:
				self.state = ClientState.CLOSED
			self.shutdown_evt.set()

	# Network helpers
	def _sendpkt(self, cmd: int, payload: bytes = b""):
		with self.lock:
			self.lclock += 1
			pkt = encode(cmd, self.seq, self.session_id, self.lclock, payload)
			self.seq += 1
		self.sock.send(pkt)

	def _send_hello(self):
		with self.lock:
			self.state = ClientState.HELLO_WAIT
		self._sendpkt(CMD_HELLO)
		self._set_timer(HELLO_TIMEOUT)

	def _send_data(self, line: bytes):
		self._sendpkt(CMD_DATA, line)

	def _send_goodbye_and_enter_closing(self):
		with self.lock:
			if not self.closing_sent:
				self._sendpkt(CMD_GOODBYE)
				self.closing_sent = True
			self.state = ClientState.CLOSING
		self._set_timer(ALIVE_TIMEOUT)

	# Threads
	def recv_loop(self):
		while not self.shutdown_evt.is_set():
			try:
				data = self.sock.recv(65535)
			except socket.timeout:
				continue
			except OSError:
				break
			hdr, payload = split_packet(data)
			if not hdr:
				continue
			# Receive bumps logical clock
			self.merge_clock(hdr["lclock"])
			cmd = hdr["cmd"]

			# If server sends a DATA message (e.g., goodbye note), print it to stdout
			if cmd == CMD_DATA:
				try:
					msg = payload.decode("utf-8", errors="replace")
					print(msg, flush=True)
				except Exception:
					pass


			# Global rule: server GOODBYE => Closed
			if cmd == CMD_GOODBYE:
				with self.lock:
					self.state = ClientState.CLOSED
				print("GOODBYE received from server",flush=True)
				self._cancel_timer()
				self.shutdown_evt.set()
				try:
					self.sock.close()
				except Exception:
					pass
				break

			with self.lock:
				st = self.state

			if st == ClientState.HELLO_WAIT and cmd == CMD_HELLO:
				# Hello complete
				self._cancel_timer()
				with self.lock:
					self.state = ClientState.READY
				continue

			if st == ClientState.READY and cmd == CMD_ALIVE:
				# keep-alive acknowledgement when idle; ignore
				continue

			if st == ClientState.READY_TIMER and cmd == CMD_ALIVE:
				# Data ack
				self._cancel_timer()
				with self.lock:
					self.state = ClientState.READY
				continue

			if st == ClientState.CLOSING and cmd == CMD_ALIVE:
				# Ignore alive while closing
				continue

	def stdin_loop(self):
		# Start by sending HELLO
		self._send_hello()

		if self.is_tty:
			# Interactive: line-by-line with immediate reaction
			for line in sys.stdin:
				s = line.rstrip("\n").encode()
				# Stdin is an event that bumps clock through _sendpkt
				with self.lock:
					st = self.state
				if s == b"q":
					# Send GOODBYE first, then confirm EOF to user and request shutdown
					self._send_goodbye_and_enter_closing()
					print("eof")
					self.shutdown_evt.set()
					return
				if st == ClientState.READY:
					self._send_data(s)
					with self.lock:
						self.state = ClientState.READY_TIMER
					self._set_timer(ALIVE_TIMEOUT)
				elif st == ClientState.READY_TIMER:
					self._send_data(s)
			else:
				# EOF: Send GOODBYE first, then print confirmation and request shutdown
				self._send_goodbye_and_enter_closing()
				print("eof")
				self.shutdown_evt.set()
				return
		else:
			# Piped/file input: buffer until HELLO completes to avoid early DATA
			while True:
				with self.lock:
					st = self.state
				if st != ClientState.HELLO_WAIT:
					break
				if self.shutdown_evt.wait(0.01):
					return

			for line in sys.stdin:
				s = line.rstrip("\n").encode()
				with self.lock:
					st = self.state
				if st == ClientState.READY:
					self._send_data(s)
					with self.lock:
						self.state = ClientState.READY_TIMER
					self._set_timer(ALIVE_TIMEOUT)
				elif st == ClientState.READY_TIMER:
					self._send_data(s)
				if self.file_send_delay > 0:
					time.sleep(self.file_send_delay)
			# EOF: Send GOODBYE first, then print confirmation and request shutdown
			self._send_goodbye_and_enter_closing()
			print("eof")
			self.shutdown_evt.set()
			return

		# If we fall through (no EOF/'q'), block until shutdown requested (e.g., server GOODBYE)
		self.shutdown_evt.wait()

	def run(self):
		t_recv = threading.Thread(target=self.recv_loop, name="recv", daemon=True)
		# Run stdin reader on its own daemon thread so we can exit promptly on server GOODBYE
		t_in = threading.Thread(target=self.stdin_loop, name="stdin", daemon=True)
		t_recv.start()
		t_in.start()
		# Wait until either stdin requests shutdown or server GOODBYE arrives
		self.shutdown_evt.wait()
		# Attempt clean shutdown
		try:
			self.sock.close()
		except Exception:
			pass
		# Give threads a moment to exit
		t_recv.join(timeout=0.5)
		t_in.join(timeout=0.5)


def main():
	if len(sys.argv) != 3:
		print("Usage: ./client <hostname> <portnum>")
		sys.exit(1)
	host, port = sys.argv[1], int(sys.argv[2])
	client = UAPClient(host, port)
	client.run()


if __name__ == "__main__":
	main()

