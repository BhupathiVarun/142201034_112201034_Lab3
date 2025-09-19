import struct
import time
import random

MAGIC = 0xC461
VERSION = 1

CMD_HELLO   = 0
CMD_DATA    = 1
CMD_ALIVE   = 2
CMD_GOODBYE = 3

# Struct format: >HBBIIQQ (big-endian)
# H: 2 bytes, B: 1 byte, I: 4 bytes, Q: 8 bytes
HDR_FMT = ">HBBIIQQ"
HDR_LEN = struct.calcsize(HDR_FMT)

def now_ns():
    return time.time_ns()

def new_session_id():
    return random.getrandbits(32)

def pack_header(cmd, seq, sess, lclock, ts_ns):
    return struct.pack(HDR_FMT, MAGIC, VERSION, cmd, seq, sess, lclock, ts_ns)

def unpack_header(buf):
    if len(buf) < HDR_LEN:
        return None
    magic, version, cmd, seq, sess, lclock, ts_ns = struct.unpack(HDR_FMT, buf[:HDR_LEN])
    if magic != MAGIC or version != VERSION:
        return None
    return {
        "cmd": cmd,
        "seq": seq,
        "session": sess,
        "lclock": lclock,
        "ts_ns": ts_ns
    }

def encode(cmd, seq, sess, lclock, payload=b""):
    return pack_header(cmd, seq, sess, lclock, now_ns()) + payload

def split_packet(pkt):
    hdr = unpack_header(pkt)
    if not hdr:
        return None, None
    return hdr, pkt[HDR_LEN:]
