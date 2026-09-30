"""Build one real voice packet from 20 ms of sound, write a capture Wireshark can open, and decode it back.

    python build_packet.py            # prints the packet, layer by layer, and writes voice.pcap

The sound is a 440 Hz tone rather than speech so the run is reproducible. The encoder is libopus (through PyAV) at a
constant 32 kb/s, which is what makes every 20 ms frame exactly 80 bytes; with variable bitrate, speech would average
about that and vary frame to frame.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

RATE = 48_000  # samples per second, the rate WebRTC's Opus runs at
FRAME_MS = 20
SAMPLES = RATE * FRAME_MS // 1000  # 960 samples in one 20 ms frame
BITRATE = 32_000

# Documentation addresses (RFC 5737), so nothing here points at a real host.
SRC, DST = "192.0.2.10", "198.51.100.20"
SRC_PORT, DST_PORT = 54_321, 50_004
PAYLOAD_TYPE = 111  # the dynamic payload type browsers commonly assign to Opus
SSRC = 0x1F2E3D4C


def tone(n_frames: int) -> np.ndarray:
    """n_frames x 20 ms of a 440 Hz sine at half scale, as 16-bit PCM."""
    t = np.arange(n_frames * SAMPLES) / RATE
    return (0.5 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)


def encode_opus(pcm: np.ndarray) -> list[bytes]:
    """Encode 16-bit mono PCM into 20 ms Opus frames at a constant 32 kb/s."""
    enc = av.CodecContext.create("libopus", "w")
    enc.sample_rate = RATE
    enc.layout = "mono"
    enc.format = "s16"
    enc.bit_rate = BITRATE
    enc.time_base = Fraction(1, RATE)
    enc.options = {"vbr": "off", "application": "voip", "frame_duration": str(FRAME_MS)}
    packets: list[bytes] = []
    for i in range(0, len(pcm), SAMPLES):
        frame = av.AudioFrame.from_ndarray(pcm[i : i + SAMPLES].reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = i
        packets.extend(bytes(p) for p in enc.encode(frame))
    packets.extend(bytes(p) for p in enc.encode(None))
    return packets


def decode_opus(payload: bytes) -> np.ndarray:
    """Decode one Opus frame back to PCM samples."""
    dec = av.CodecContext.create("libopus", "r")
    dec.sample_rate = RATE
    dec.layout = "mono"
    frames = dec.decode(av.Packet(payload))
    return np.concatenate([f.to_ndarray().reshape(-1) for f in frames])


@dataclass(frozen=True)
class Rtp:
    """The 12-byte RTP header (RFC 3550 section 5.1), with no CSRCs and no extension."""

    sequence: int
    timestamp: int
    ssrc: int = SSRC
    payload_type: int = PAYLOAD_TYPE
    marker: bool = False

    def pack(self) -> bytes:
        first = 2 << 6  # version 2, no padding, no extension, zero CSRCs
        second = (int(self.marker) << 7) | self.payload_type
        return struct.pack("!BBHII", first, second, self.sequence, self.timestamp, self.ssrc)


def checksum(data: bytes) -> int:
    """The internet checksum (RFC 1071): one's-complement sum of 16-bit words."""
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def ip_bytes(address: str) -> bytes:
    return bytes(int(part) for part in address.split("."))


def udp(payload: bytes) -> bytes:
    """An 8-byte UDP header plus payload, with the checksum over the IPv4 pseudo-header (RFC 768)."""
    length = 8 + len(payload)
    pseudo = ip_bytes(SRC) + ip_bytes(DST) + struct.pack("!BBH", 0, 17, length)
    header = struct.pack("!HHHH", SRC_PORT, DST_PORT, length, 0)
    return struct.pack("!HHHH", SRC_PORT, DST_PORT, length, checksum(pseudo + header + payload)) + payload


def ipv4(payload: bytes, ident: int) -> bytes:
    """A 20-byte IPv4 header with no options. DSCP 46 (Expedited Forwarding) is the marking voice usually carries."""
    tos = 46 << 2
    total = 20 + len(payload)
    header = struct.pack("!BBHHHBBH4s4s", 0x45, tos, total, ident, 0x4000, 64, 17, 0, ip_bytes(SRC), ip_bytes(DST))
    header = header[:10] + struct.pack("!H", checksum(header)) + header[12:]
    return header + payload


def write_pcap(path: Path, packets: list[bytes]) -> None:
    """A classic pcap file with link type 228 (raw IPv4), one packet every 20 ms."""
    with path.open("wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65_535, 228))
        for i, pkt in enumerate(packets):
            micros = i * FRAME_MS * 1000
            f.write(struct.pack("<IIII", micros // 1_000_000, micros % 1_000_000, len(pkt), len(pkt)))
            f.write(pkt)


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def describe(packet: bytes) -> None:
    """Read the packet back, layer by layer, from the bytes alone."""
    ip, rest = packet[:20], packet[20:]
    udp_h, rest = rest[:8], rest[8:]
    rtp_h, opus = rest[:12], rest[12:]

    _, tos, total, _, _, ttl, proto, ip_sum = struct.unpack("!BBHHHBBH", ip[:12])
    print(f"IPv4 header   20 B  {hexdump(ip)}")
    print(f"              total length {total} B, DSCP {tos >> 2}, TTL {ttl}, protocol {proto} (UDP), "
          f"{'.'.join(map(str, ip[12:16]))} -> {'.'.join(map(str, ip[16:20]))}, checksum 0x{ip_sum:04x}")

    sport, dport, length, udp_sum = struct.unpack("!HHHH", udp_h)
    print(f"UDP header     8 B  {hexdump(udp_h)}")
    print(f"              port {sport} -> {dport}, length {length} B (header + RTP + Opus), checksum 0x{udp_sum:04x}")

    first, second, seq, ts, ssrc = struct.unpack("!BBHII", rtp_h)
    print(f"RTP header    12 B  {hexdump(rtp_h)}")
    print(f"              version {first >> 6}, payload type {second & 0x7F} (Opus), sequence {seq}, "
          f"timestamp {ts}, SSRC 0x{ssrc:08x}")

    samples = decode_opus(opus)
    print(f"Opus payload  {len(opus)} B  {hexdump(opus[:16])} ... ({len(opus) - 16} more)")
    print(f"              decodes to {len(samples)} samples = {len(samples) * 1000 // RATE} ms of audio")
    print(f"Total        {len(packet)} B  ({len(packet) - len(opus)} B of headers, {len(opus)} B of sound)")


def main() -> None:
    frames = encode_opus(tone(51))[:50]  # one second; the extra frame covers the encoder's look-ahead
    sizes = sorted({len(f) for f in frames})
    print(f"20 ms of PCM: {SAMPLES} samples x 2 B = {SAMPLES * 2} B. Opus at {BITRATE // 1000} kb/s: "
          f"{', '.join(map(str, sizes))} B per frame\n")
    packets = [
        ipv4(udp(Rtp(sequence=1000 + i, timestamp=i * SAMPLES).pack() + frame), ident=0x3A00 + i)
        for i, frame in enumerate(frames)
    ]
    describe(packets[10])
    out = Path(__file__).with_name("voice.pcap")
    write_pcap(out, packets)
    print(f"\nWrote {len(packets)} packets, one second of sound, to {out.name}")


if __name__ == "__main__":
    main()
