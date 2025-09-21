import asyncio
import sys
import os

# Make shared/ importable when running from B/Client/
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from B.shared.uap import *

HELLO_TIMEOUT = 2.0   # seconds
ALIVE_TIMEOUT = 2.0   # seconds

class ClientState:
    HELLO_WAIT = "Hello Wait"
    READY = "Ready"
    READY_TIMER = "Ready Timer"
    CLOSING = "Closing"
    CLOSED = "Closed"

class UAPClient(asyncio.DatagramProtocol):
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.transport = None
        self.state = ClientState.HELLO_WAIT
        self.session_id = new_session_id()
        self.seq = 0
        self.lclock = 0
        self.timer_task = None
        self.stdin_task = None
        self.is_tty = sys.stdin.isatty()
        self.closing_sent = False
        self.file_send_delay = float(os.getenv("UAP_FILE_SEND_DELAY", "0.0"))  # seconds; set to small value (e.g., 0.001) to throttle file input

    # Logical clock helpers
    def bump_clock(self):
        self.lclock += 1

    def merge_clock(self, their: int):
        self.lclock = max(self.lclock, their) + 1

    async def _wait_until_sendable(self) -> bool:
        # Wait until we leave HELLO_WAIT (HELLO or timeout)
        while self.state == ClientState.HELLO_WAIT:
            await asyncio.sleep(0.01)
        return self.state in (ClientState.READY, ClientState.READY_TIMER)
    
    # DatagramProtocol hooks
    def connection_made(self, transport):
        self.transport = transport
        # Start stdin watch immediately (needed for Hello Wait -> Closing on EOF)
        self.stdin_task = asyncio.create_task(self.stdin_reader())
        # FSM (1) Start -> Hello Wait: / HELLO; set timer
        self.send_hello()

    def datagram_received(self, data, addr):
        hdr, payload = split_packet(data)
        if not hdr:
            return
        # Receive event updates logical clock
        self.merge_clock(hdr["lclock"])
        cmd = hdr["cmd"]

        # Global rule: GOODBYE from server => Closed (from any state)
        if cmd == CMD_GOODBYE:
            #print("GOODBYE received from server")
            self.state = ClientState.CLOSED
            self.cancel_timer()
            self.shutdown()
            return

        # (2) Hello Wait -> Ready: HELLO / cancel timer
        if self.state == ClientState.HELLO_WAIT and cmd == CMD_HELLO:
            self.cancel_timer()
            self.state = ClientState.READY
            return

        # (12) Ready -> Ready: ALIVE / (ignore)
        if self.state == ClientState.READY and cmd == CMD_ALIVE:
            return

        # (5) Ready Timer -> Ready: ALIVE / cancel timer
        if self.state == ClientState.READY_TIMER and cmd == CMD_ALIVE:
            self.cancel_timer()
            self.state = ClientState.READY
            return

        # (9) Closing -> Closing: ALIVE / (ignore)
        if self.state == ClientState.CLOSING and cmd == CMD_ALIVE:
            return

    def connection_lost(self, exc):
        # Socket closed; nothing else to do
        pass

    # Timers
    def set_timer(self, timeout_s: float):
        self.cancel_timer()
        self.timer_task = asyncio.create_task(self._timeout(timeout_s))

    def cancel_timer(self):
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()

    async def _timeout(self, timeout_s: float):
        try:
            await asyncio.sleep(timeout_s)
        except asyncio.CancelledError:
            return
        # Timeout is an event that bumps the clock when we act on it
        if self.state == ClientState.HELLO_WAIT:
            # (3) Hello Wait -> Closing: Timeout / GOODBYE
            await self.send_goodbye_and_enter_closing()
        elif self.state == ClientState.READY_TIMER:
            # (6) Ready Timer -> Closing: Timeout / GOODBYE
            await self.send_goodbye_and_enter_closing()
        elif self.state == ClientState.CLOSING:
            # (10) Closing -> Closed: Timeout /
            self.bump_clock()
            self.state = ClientState.CLOSED
            self.shutdown()

    # Sending helpers
    def sendpkt(self, cmd: int, payload: bytes = b""):
        self.bump_clock()
        pkt = encode(cmd, self.seq, self.session_id, self.lclock, payload)
        self.transport.sendto(pkt)
        self.seq += 1

    def send_hello(self):
        self.state = ClientState.HELLO_WAIT
        self.sendpkt(CMD_HELLO)
        self.set_timer(HELLO_TIMEOUT)

    def send_data(self, line: bytes):
        self.sendpkt(CMD_DATA, line)

    async def send_goodbye_and_enter_closing(self):
        if not self.closing_sent:
            self.sendpkt(CMD_GOODBYE)
            self.closing_sent = True
        self.state = ClientState.CLOSING
        self.set_timer(ALIVE_TIMEOUT)

    # Stdin handling
    async def stdin_reader(self):
        if sys.stdin.isatty():
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
            while True:
                line = await reader.readline()
                if not line:
                    
                    self.bump_clock()  # EOF event
                    await self.send_goodbye_and_enter_closing()
                    print("GOODBYE received from server")
                    print("eof")
                    return
                s = line.rstrip(b"\n")
                # STDIN event
                self.bump_clock()
                if self.is_tty and s == b"q":
                    print("eof")
                    self.bump_clock()  # 'q' as stdin event
                    await self.send_goodbye_and_enter_closing()
                    return
                if self.state == ClientState.READY:
                    self.send_data(s)
                    self.state = ClientState.READY_TIMER
                    self.set_timer(ALIVE_TIMEOUT)
                    continue
                if self.state == ClientState.READY_TIMER:
                    self.send_data(s)
                    continue
        else:
            # File or pipe: block until HELLO completes to avoid early DATA
            await self._wait_until_sendable()
            for line in sys.stdin:
                s_txt = line.rstrip("\n")
                s = s_txt.encode()
                # STDIN event per line
                self.bump_clock()
                if self.state == ClientState.READY:
                    self.send_data(s)
                    self.state = ClientState.READY_TIMER
                    self.set_timer(ALIVE_TIMEOUT)
                elif self.state == ClientState.READY_TIMER:
                    self.send_data(s)
                if self.file_send_delay > 0:
                    await asyncio.sleep(self.file_send_delay)
            print("eof")
            self.bump_clock()  # EOF event
            await self.send_goodbye_and_enter_closing()

    def shutdown(self):
        try:
            self.transport.close()
        except Exception:
            pass
        # Let the event loop exit naturally

async def main():
    if len(sys.argv) != 3:
        print("Usage: ./client <hostname> <portnum>")
        sys.exit(1)
    host, port = sys.argv[1], int(sys.argv[2])
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UAPClient(host, port),
        remote_addr=(host, port),
    )
    try:
        # Keep process alive until socket closes
        while not transport.is_closing():
            await asyncio.sleep(0.05)
    finally:
        transport.close()

if __name__ == "__main__":
    asyncio.run(main())
