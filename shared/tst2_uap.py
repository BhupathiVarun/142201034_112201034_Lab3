from uap import *

# Test values
cmd = CMD_HELLO
seq = 42
sess = 12345678
lclock = 99
payload = b"test payload"

# Encode a packet
pkt = encode(cmd, seq, sess, lclock, payload)
print(f"Full packet (hex): {pkt.hex()}")
print(f"Packet length: {len(pkt)} bytes")
print()

# Parse each header field manually
print("Header Fields (hex and decimal):")
print("=" * 50)

# Magic (2 bytes)
magic_bytes = pkt[0:2]
magic_val = int.from_bytes(magic_bytes, 'big')
print(f"Magic:     {magic_bytes.hex()} = {magic_val} (expected: 50273)")

# Version (1 byte)
version_bytes = pkt[2:3]
version_val = int.from_bytes(version_bytes, 'big')
print(f"Version:   {version_bytes.hex()} = {version_val}")

# Command (1 byte)
cmd_bytes = pkt[3:4]
cmd_val = int.from_bytes(cmd_bytes, 'big')
print(f"Command:   {cmd_bytes.hex()} = {cmd_val} (CMD_HELLO)")

# Sequence (4 bytes)
seq_bytes = pkt[4:8]
seq_val = int.from_bytes(seq_bytes, 'big')
print(f"Sequence:  {seq_bytes.hex()} = {seq_val}")

# Session (4 bytes)
sess_bytes = pkt[8:12]
sess_val = int.from_bytes(sess_bytes, 'big')
print(f"Session:   {sess_bytes.hex()} = {sess_val}")

# Logical Clock (8 bytes)
lclock_bytes = pkt[12:20]
lclock_val = int.from_bytes(lclock_bytes, 'big')
print(f"L.Clock:   {lclock_bytes.hex()} = {lclock_val}")

# Timestamp (8 bytes)
ts_bytes = pkt[20:28]
ts_val = int.from_bytes(ts_bytes, 'big')
print(f"Timestamp: {ts_bytes.hex()} = {ts_val}")

print()
print(f"Payload ({len(payload)} bytes): {payload}")
print(f"Payload (hex): {payload.hex()}")

# Decode the packet using your function
hdr, data = split_packet(pkt)
print()
print("Decoded header:", hdr)
print("Decoded payload:", data)

# Check round-trip correctness
assert hdr["cmd"] == cmd
assert hdr["seq"] == seq
assert hdr["session"] == sess
assert hdr["lclock"] == lclock
assert data == payload
print("uap.py test passed!")