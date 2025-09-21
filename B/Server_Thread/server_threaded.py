import sys, os, socket, threading, time
from typing import Dict, Tuple

# Make shared/ importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from B.shared.uap import *  # MAGIC, VERSION, encode/split_packet, CMD_*

# Idle time before the server closes a session with GOODBYE.
# Made configurable for interactive testing; defaults to a more forgiving value.
SESSION_TIMEOUT = float(os.environ.get("UAP_SESSION_TIMEOUT", "15.0"))  # seconds

def now_ns():
    return time.time_ns()

class Server:
    def __init__(self, port: int):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        self.shutdown = threading.Event()
        self.lock = threading.RLock()
        self.sessions: Dict[int, "Session"] = {}
        self.server_seq = 0  # sequence numbers for all outgoing packets (global)
        self.lclock = 0      # server logical clock (global)
        self.latency_sum_ns = 0
        self.latency_cnt = 0

    # Logical clock helpers
    def bump_clock(self):
        with self.lock:
            self.lclock += 1
            return self.lclock

    def merge_clock(self, their: int):
        with self.lock:
            self.lclock = max(self.lclock, their) + 1
            return self.lclock

    # Send helper that uses global server_seq and lclock
    def send(self, addr: Tuple[str, int], cmd: int, session_id: int, payload: bytes = b""):
        with self.lock:
            self.bump_clock()
            pkt = encode(cmd, self.server_seq, session_id, self.lclock, payload)
            self.server_seq += 1
        self.sock.sendto(pkt, addr)

    def add_session(self, sess: "Session"):
        with self.lock:
            self.sessions[sess.session_id] = sess

    def remove_session(self, sess: "Session"):
        with self.lock:
            self.sessions.pop(sess.session_id, None)

    def close_all_sessions(self):
        with self.lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            s.close_due_to_server_shutdown()

    # Threads
    def run_network_loop(self):
        print(f"Waiting on port {self.port}...", flush=True)
        while not self.shutdown.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                break  # socket closed
            hdr, payload = split_packet(data)
            if not hdr:
                continue
            # latency and logical clock update on every receive
            self.merge_clock(hdr["lclock"])
            sess_id = hdr["session"]
            cmd = hdr["cmd"]
            with self.lock:
                sess = self.sessions.get(sess_id)

            if sess is None:
                # New session must start with HELLO, else terminate immediately
                if cmd != CMD_HELLO:
                    # Send GOODBYE to inform client and ignore
                    self.send(addr, CMD_GOODBYE, sess_id)
                    continue
                # Create session and process HELLO
                sess = Session(server=self, addr=addr, session_id=sess_id)
                self.add_session(sess)
                sess.handle_hello(hdr)
                continue

            # Existing session: dispatch
            sess.handle_packet(hdr, payload)

    def run_stdin_loop(self):
        # If not attached to a TTY (e.g., background or redirected stdin), do not
        # treat EOF as a shutdown signal. Just wait until shutdown is requested
        # elsewhere (e.g., process kill) to avoid immediate exit.
        if not sys.stdin.isatty():
            self.shutdown.wait()
            return
        for line in sys.stdin:
            s = line.rstrip("\n")
            if s == "q":
                break
        # Either 'q' or EOF -> shutdown
        # STDIN/EOF are events that should bump logical clock
        self.bump_clock()
        self.shutdown.set()
        # Send GOODBYE to all active sessions
        self.close_all_sessions()
        try:
            self.sock.close()
        except Exception:
            pass

    def serve_forever(self):
        t_net = threading.Thread(target=self.run_network_loop, name="net", daemon=True)
        t_in = threading.Thread(target=self.run_stdin_loop, name="stdin", daemon=True)
        t_net.start()
        t_in.start()
        # Wait for stdin thread to request shutdown
        t_in.join()
        # Ensure net loop stops
        t_net.join(timeout=0.5)
        with self.lock:
            remaining = list(self.sessions.values())
        for s in remaining:
            s.force_close_print()

class Session:
    def __init__(self, server: Server, addr: Tuple[str, int], session_id: int):
        self.server = server
        self.addr = addr
        self.session_id = session_id
        self.timer: threading.Timer | None = None
        self.lock = threading.RLock()
        self.state = "Receive"  # after HELLO
        self.next_expected = 0  # we will set to 1 after HELLO is processed
        self.last_received = -1 # last client seq seen
        # latency aggregation per session
        self.latency_sum_ns = 0
        self.latency_cnt = 0

    # Timer helpers
    def _set_timer(self):
        self._cancel_timer()
        self.timer = threading.Timer(SESSION_TIMEOUT, self._on_timeout)
        self.timer.daemon = True
        self.timer.start()

    def _cancel_timer(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _on_timeout(self):
        with self.lock:
            if self.state != "Receive":
                return
            # timeout / GOODBYE -> Done
            self.server.bump_clock()
            self.server.send(self.addr, CMD_GOODBYE, self.session_id)
            self.state = "Done"
        self._cancel_timer()
        self._print_avg_latency()
        print(f"0x{self.session_id:08x} Session closed", flush=True)
        self.server.remove_session(self)

    # Latency helpers
    def _accumulate_latency(self, hdr):
        try:
            dt = now_ns() - hdr["ts_ns"]
        except Exception:
            return -1
        if dt >= 0:
            self.latency_sum_ns += dt
            self.latency_cnt += 1
        return dt

    def _print_avg_latency(self):
        # Suppress avg-latency output to match required server output format
        return

    # Public API used by Server
    def handle_hello(self, hdr):
        with self.lock:
            client_seq = hdr["seq"]
            # include HELLO in latency stats (uses client timestamp)
            self._accumulate_latency(hdr)
            print(f"0x{self.session_id:08x} [{client_seq}] Session created", flush=True)
            # Reply HELLO; set inactivity timer
            self.server.send(self.addr, CMD_HELLO, self.session_id)
            self.next_expected = client_seq + 1
            self.last_received = client_seq
            self._set_timer()

    def handle_packet(self, hdr, payload: bytes):
        cmd = hdr["cmd"]
        client_seq = hdr["seq"]

        with self.lock:
            if self.state != "Receive":
                return

            # Any unexpected command in Receive => protocol error -> close
            if cmd == CMD_HELLO:
                # protocol error: close immediately
                self.server.send(self.addr, CMD_GOODBYE, self.session_id)
                self.state = "Done"
                self._cancel_timer()
                self._print_avg_latency()
                print(f"0x{self.session_id:08x} Session closed", flush=True)
                self.server.remove_session(self)
                return

            if cmd == CMD_GOODBYE:
                # GOODBYE / GOODBYE -> Done
                print(f"0x{self.session_id:08x} [{client_seq}] GOODBYE from client.", flush=True)
                self.server.send(self.addr, CMD_GOODBYE, self.session_id)
                self.state = "Done"
                self._cancel_timer()
                self._print_avg_latency()
                print(f"0x{self.session_id:08x} Session closed", flush=True)
                self.server.remove_session(self)
                return

            if cmd != CMD_DATA:
                # Unknown/unsupported -> close
                self.server.send(self.addr, CMD_GOODBYE, self.session_id)
                self.state = "Done"
                self._cancel_timer()
                self._print_avg_latency()
                print(f"0x{self.session_id:08x} Session closed", flush=True)
                self.server.remove_session(self)
                return

            # DATA handling: lost/duplicate/out-of-order checks
            if client_seq < self.last_received:
                # From the past -> protocol error: close
                self.server.send(self.addr, CMD_GOODBYE, self.session_id)
                self.state = "Done"
                self._cancel_timer()
                self._print_avg_latency()
                print(f"0x{self.session_id:08x} Session closed", flush=True)
                self.server.remove_session(self)
                return

            if client_seq == self.last_received:
                # Duplicate
                print(f"0x{self.session_id:08x} [{client_seq}] Duplicate packet!", flush=True)
                return

            if client_seq > self.next_expected:
                # Print one "Lost packet!" line for each missing seq
                for missing in range(self.next_expected, client_seq):
                    print(f"0x{self.session_id:08x} [{missing}] Lost packet!", flush=True)

            # Normal accept of this DATA
            text = payload.decode("utf-8", errors="replace")
            print(f"0x{self.session_id:08x} [{client_seq}] {text}", flush=True)
            # per-packet one-way latency (from client timestamp)
            dt_ns = self._accumulate_latency(hdr)
            # Suppress per-packet latency output to match required server output format
            # ALIVE; cancel timer; set timer (Receive -> Receive)
            self.server.send(self.addr, CMD_ALIVE, self.session_id)
            self._cancel_timer()
            self._set_timer()

            self.last_received = client_seq
            self.next_expected = client_seq + 1

    # Shutdown paths
    def close_due_to_server_shutdown(self):
        with self.lock:
            if self.state != "Receive":
                return
            self.server.bump_clock()
            self.server.send(self.addr, CMD_GOODBYE, self.session_id)
            self.state = "Done"
        self._cancel_timer()
        self._print_avg_latency()
        print(f"0x{self.session_id:08x} Session closed", flush=True)
        self.server.remove_session(self)

    def force_close_print(self):
        with self.lock:
            if self.state == "Done":
                return
            self.state = "Done"
        self._cancel_timer()
        self._print_avg_latency()
        print(f"0x{self.session_id:08x} Session closed", flush=True)
        self.server.remove_session(self)


def main():
    if len(sys.argv) != 2:
        print("Usage: ./server <portnum>")
        sys.exit(1)
    port = int(sys.argv[1])
    srv = Server(port)
    srv.serve_forever()

if __name__ == "__main__":
    main()
