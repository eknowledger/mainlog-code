"""One STUN round trip, in the standard library only: build a Binding request, answer it, decode the reply.

    python stun.py                      # a local STUN server on 127.0.0.1, so it runs anywhere, CI included
    python stun.py --public stun.l.google.com:19302    # ask a real server how it sees you (not run in CI)

STUN (RFC 8489) is small enough to write by hand. A message is a 20-byte header: a type, a length, the fixed magic
cookie 0x2112A442 and a 96-bit transaction ID, then attributes. The server's answer carries the source address it
saw in XOR-MAPPED-ADDRESS, XORed with the cookie so that no well-meaning NAT rewrites it on the way back.

The script also builds the post's example pair, a request from a laptop behind NAT and the reply naming the NAT's
public address, with the same encoder and addresses from the documentation ranges (RFC 5737), so every byte the post
shows comes from code rather than from a hand-typed hex dump. The XOR is easy to get wrong (the port takes only the
cookie's top 16 bits), so the arithmetic is printed and can be checked.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import struct
import threading
import zlib

MAGIC = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
XOR_MAPPED_ADDRESS = 0x0020
FINGERPRINT = 0x8028
FINGERPRINT_XOR = 0x5354554E


def header(kind: int, length: int, txid: bytes) -> bytes:
    return struct.pack("!HHI", kind, length, MAGIC) + txid


def with_fingerprint(message: bytes) -> bytes:
    """Append FINGERPRINT: CRC-32 of the message so far, with the length already counting the 8-byte attribute."""
    kind, length = struct.unpack("!HH", message[:4])
    patched = struct.pack("!HH", kind, length + 8) + message[4:]
    crc = (zlib.crc32(patched) & 0xFFFFFFFF) ^ FINGERPRINT_XOR
    return patched + struct.pack("!HHI", FINGERPRINT, 4, crc)


def binding_request(txid: bytes) -> bytes:
    return header(BINDING_REQUEST, 0, txid)


def xor_mapped_address(ip: str, port: int) -> bytes:
    """XOR-MAPPED-ADDRESS for IPv4 (RFC 8489 section 14.2): port XOR the cookie's top 16 bits, address XOR the cookie."""
    x_port = port ^ (MAGIC >> 16)
    x_addr = int(ipaddress.IPv4Address(ip)) ^ MAGIC
    return struct.pack("!HHBBHI", XOR_MAPPED_ADDRESS, 8, 0, 0x01, x_port, x_addr)


def binding_success(txid: bytes, ip: str, port: int) -> bytes:
    attr = xor_mapped_address(ip, port)
    return header(BINDING_SUCCESS, len(attr), txid) + attr


def parse(message: bytes) -> dict:
    kind, length, cookie = struct.unpack("!HHI", message[:8])
    assert cookie == MAGIC, "not STUN: wrong magic cookie"
    out = {"type": kind, "length": length, "txid": message[8:20], "attributes": {}}
    at = 20
    while at < 20 + length:
        a_type, a_len = struct.unpack("!HH", message[at : at + 4])
        value = message[at + 4 : at + 4 + a_len]
        if a_type == XOR_MAPPED_ADDRESS:
            _, family, x_port, x_addr = struct.unpack("!BBHI", value)
            assert family == 0x01, "IPv4 only in this demo"
            out["attributes"]["XOR-MAPPED-ADDRESS"] = (str(ipaddress.IPv4Address(x_addr ^ MAGIC)), x_port ^ (MAGIC >> 16))
        at += 4 + a_len + (-a_len % 4)
    return out


def hexdump(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def serve(sock: socket.socket) -> None:
    """A STUN server in five lines: answer every Binding request with the address the request came from."""
    while True:
        data, addr = sock.recvfrom(2048)
        msg = parse(data)
        if msg["type"] == BINDING_REQUEST:
            sock.sendto(binding_success(msg["txid"], addr[0], addr[1]), addr)


def ask(server: tuple[str, int]) -> tuple[str, int, float]:
    import time

    txid = os.urandom(12)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(3)
        start = time.perf_counter()
        s.sendto(binding_request(txid), server)
        data, _ = s.recvfrom(2048)
        rtt = (time.perf_counter() - start) * 1000
    msg = parse(data)
    assert msg["type"] == BINDING_SUCCESS and msg["txid"] == txid, "not our answer"
    ip, port = msg["attributes"]["XOR-MAPPED-ADDRESS"]
    return ip, port, rtt


def example() -> None:
    # A fixed transaction ID so the post's bytes are reproducible; a real client draws 12 random bytes.
    txid = bytes.fromhex("b7e7a701bc5ca4935d339347")
    req = binding_request(txid)
    res = binding_success(txid, "192.0.2.10", 54321)
    print("The post's example: a laptop behind NAT asks; the server answers with the NAT's public address")
    print(f"  Binding request  ({len(req)} B)  {hexdump(req)}")
    print(f"  Binding success  ({len(res)} B)  {hexdump(res)}")
    port, ip = 54321, int(ipaddress.IPv4Address("192.0.2.10"))
    print(f"  port    {port} = 0x{port:04x}  XOR 0x{MAGIC >> 16:04x} = 0x{port ^ (MAGIC >> 16):04x}")
    print(f"  address 192.0.2.10 = 0x{ip:08x}  XOR 0x{MAGIC:08x} = 0x{ip ^ MAGIC:08x}")
    back = parse(res)["attributes"]["XOR-MAPPED-ADDRESS"]
    print(f"  decoded back: {back[0]}:{back[1]}")
    fp = with_fingerprint(req)
    print(f"  with FINGERPRINT ({len(fp)} B)  {hexdump(fp)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--public", help="host:port of a public STUN server to ask instead of the local one")
    args = p.parse_args()
    example()
    print()
    if args.public:
        host, port = args.public.rsplit(":", 1)
        ip, mapped, rtt = ask((socket.gethostbyname(host), int(port)))
        print(f"{args.public} sees you as {ip}:{mapped}, in {rtt:.0f} ms")
        return
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    threading.Thread(target=serve, args=(srv,), daemon=True).start()
    ip, mapped, _ = ask(srv.getsockname())
    print(f"A real round trip to a local STUN server at {srv.getsockname()[0]}:{srv.getsockname()[1]}:")
    print(f"  it saw the request come from {ip}:{mapped}. No NAT on loopback, so that is the client's own socket.")


if __name__ == "__main__":
    main()
