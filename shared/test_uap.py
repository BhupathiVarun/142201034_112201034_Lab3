from uap import *

# Test values
cmd = CMD_HELLO
seq = 42
sess = 12345678
lclock = 99
payload = b"test payload"

# Encode a packet
pkt = encode(cmd, seq, sess, lclock, payload)
print("magic",pkt[0:2])
print(f"Encoded packet (hex): {pkt.hex()}")

# Decode the packet
hdr, data = split_packet(pkt)
print("Decoded header:", hdr)
print("Decoded payload:", data)

# Check round-trip correctness
assert hdr["cmd"] == cmd
assert hdr["seq"] == seq
assert hdr["session"] == sess
assert hdr["lclock"] == lclock
assert data == payload
print("uap.py test passed!")
