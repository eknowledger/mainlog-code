"""The two reports of Alice's call, built byte by byte, read back, and turned into loss, jitter and round-trip time.

    python3 rtcp_reports.py           # standard library only
    python3 rtcp_reports.py --jitter-csv  # the jitter estimator packet by packet, for a chart
    .venv/bin/python rtcp_reports.py  # with aiortc installed, also has aiortc parse every byte and compare

Alice's laptop sends her voice to the assistant's server as in the first post of the series: SSRC 0x1F2E3D4C,
sequence numbers from 1000, timestamps 960 apart, one 80-byte Opus frame every 20 ms. Four seconds in, it sends a
sender report (SR). A second later the server answers with a receiver report (RR) on the 250 packets it expected
and the 244 it got. Everything below follows from those inputs and RFC 3550; the network between them is a model,
stated where it is defined, so the run is the same every time.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import random
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

RATE = 48_000  # Opus's RTP clock rate (RFC 7587 section 4.1), so one timestamp unit is 1/48000 s
FRAME = 960  # timestamp units per 20 ms frame
OPUS_BYTES = 80  # one frame at 32 kb/s, as in the first post
FIRST_SEQ = 1000

ALICE_SSRC = 0x1F2E3D4C  # the first post's packet
# Picked for the example. Real SSRCs are random (RFC 3550 section 5.1), so neither side can predict the other's.
SERVER_SSRC = 0x7A3B9C5D

NTP_UNIX_OFFSET = 2_208_988_800  # seconds from 1900-01-01 (NTP's epoch) to 1970-01-01 (Unix's)
# The demo's wallclock: the call starts at 9:17:00.250 in Paris, which is 07:17:00.250 UTC in October.
CALL_START = datetime(2026, 10, 2, 7, 17, 0, 250_000, tzinfo=timezone.utc).timestamp()

# The network model: 30 ms each way, as in the second post, plus 0 to 8 ms of queueing per packet from a seeded
# generator, and six packets that never arrive, one of them a burst of two.
ONE_WAY = 0.030
QUEUE_MAX = 0.008
SEED = 917
LOST = {1037, 1088, 1089, 1142, 1190, 1221}

SR_AT = 4.000  # Alice's SR leaves 4 s into the call
RR_AT = 5.020  # the server's RR leaves 5.02 s in, by its own reckoning of when the SR arrived


def cname(who: str) -> str:
    """16 random-looking characters, the short-term CNAME RFC 7022 asks for, fixed here so the bytes repeat."""
    return base64.b64encode(hashlib.sha256(who.encode()).digest()[:12]).decode()


def ntp64(unix_seconds: float) -> int:
    """A 64-bit NTP timestamp: 32 bits of seconds since 1900, 32 bits of fraction (RFC 3550 section 4)."""
    seconds = int(unix_seconds)
    fraction = int((unix_seconds - seconds) * (1 << 32))
    return ((seconds + NTP_UNIX_OFFSET) << 32) | fraction


def middle32(ntp: int) -> int:
    """The compact form LSR uses: the low 16 bits of the seconds and the high 16 bits of the fraction."""
    return (ntp >> 16) & 0xFFFFFFFF


def fixed(value: int) -> str:
    """A 16.16 value the way RFC 3550's Figure 2 writes it: hex integer and fraction parts, then seconds."""
    return f"0x{value >> 16:04x}:{value & 0xFFFF:04x} ({value / 65536:.6f} s)"


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


# ---------------------------------------------------------------------------------------------------------------
# Building the packets
# ---------------------------------------------------------------------------------------------------------------


def header(count: int, packet_type: int, body: bytes) -> bytes:
    """The 4-byte RTCP common header. The length is in 32-bit words MINUS ONE (RFC 3550 section 6.4.1)."""
    assert len(body) % 4 == 0, "every RTCP packet ends on a 32-bit boundary"
    return struct.pack("!BBH", (2 << 6) | count, packet_type, (len(body) + 4) // 4 - 1) + body


@dataclass(frozen=True)
class ReportBlock:
    """One reception report block, 24 bytes: what one receiver knows about one source."""

    ssrc: int
    fraction_lost: int  # 8-bit fixed point, binary point at the left
    cumulative_lost: int  # 24-bit signed
    highest_seq: int  # extended: cycles in the top 16 bits
    jitter: int  # timestamp units
    lsr: int  # middle 32 bits of the last SR's NTP timestamp
    dlsr: int  # 1/65536 s since that SR arrived

    def pack(self) -> bytes:
        lost24 = self.cumulative_lost & 0xFFFFFF  # two's complement in 24 bits
        return struct.pack(
            "!IIIIII",
            self.ssrc,
            (self.fraction_lost << 24) | lost24,
            self.highest_seq,
            self.jitter,
            self.lsr,
            self.dlsr,
        )


def sender_report(ssrc: int, ntp: int, rtp_ts: int, packets: int, octets: int, blocks: list[ReportBlock]) -> bytes:
    """SR, packet type 200: the sender's SSRC, 20 bytes of sender info, then its report blocks."""
    body = struct.pack("!IQIII", ssrc, ntp, rtp_ts, packets, octets) + b"".join(b.pack() for b in blocks)
    return header(len(blocks), 200, body)


def receiver_report(ssrc: int, blocks: list[ReportBlock]) -> bytes:
    """RR, packet type 201: the reporter's SSRC and its report blocks, with no sender info."""
    return header(len(blocks), 201, struct.pack("!I", ssrc) + b"".join(b.pack() for b in blocks))


def sdes_cname(ssrc: int, name: str) -> bytes:
    """SDES, packet type 202, one chunk with a CNAME item, null-terminated and padded to 32 bits."""
    item = bytes([1, len(name)]) + name.encode() + b"\0"
    chunk = struct.pack("!I", ssrc) + item
    chunk += b"\0" * (-len(chunk) % 4)
    return header(1, 202, chunk)


# ---------------------------------------------------------------------------------------------------------------
# Reading them back, from the bytes alone
# ---------------------------------------------------------------------------------------------------------------


def parse(data: bytes) -> list[dict]:
    """Walk a compound packet. Each sub-packet's length says where the next one starts."""
    out, at = [], 0
    while at < len(data):
        first, ptype, words = struct.unpack_from("!BBH", data, at)
        end = at + (words + 1) * 4
        count, body = first & 0x1F, data[at + 4 : end]
        packet: dict = {"type": ptype, "bytes": end - at}
        blocks_at = None
        if ptype == 200:
            ssrc, ntp, rtp_ts, pkts, octets = struct.unpack_from("!IQIII", body)
            packet.update(ssrc=ssrc, ntp=ntp, rtp_ts=rtp_ts, packets=pkts, octets=octets)
            blocks_at = 24
        elif ptype == 201:
            packet["ssrc"] = struct.unpack_from("!I", body)[0]
            blocks_at = 4
        elif ptype == 202:
            ssrc, kind, length = struct.unpack_from("!IBB", body)
            packet.update(ssrc=ssrc, item=kind, text=body[6 : 6 + length].decode())
        if blocks_at is not None:
            packet["blocks"] = []
            for i in range(count):
                ssrc, word, hi, jit, lsr, dlsr = struct.unpack_from("!IIIIII", body, blocks_at + 24 * i)
                lost = word & 0xFFFFFF
                lost = lost - (1 << 24) if lost & 0x800000 else lost
                packet["blocks"].append(ReportBlock(ssrc, word >> 24, lost, hi, jit, lsr, dlsr))
        out.append(packet)
        at = end
    return out


# ---------------------------------------------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------------------------------------------


def arrivals() -> dict[int, float]:
    """When each of Alice's packets reaches the server, in seconds since the call began. Lost ones are absent."""
    rng = random.Random(SEED)
    times = {}
    for i in range(int(RR_AT / 0.020) + 1):
        seq = FIRST_SEQ + i
        queue = rng.uniform(0, QUEUE_MAX)  # drawn for every packet, lost or not, so the sequence is stable
        if seq not in LOST:
            times[seq] = i * 0.020 + ONE_WAY + queue
    return {seq: t for seq, t in times.items() if t <= RR_AT}


def jitter(arrived: dict[int, float]) -> tuple[float, int, list[tuple[int, int, float]]]:
    """RFC 3550 section 6.4.1, J(i) = J(i-1) + (|D(i-1,i)| - J(i-1))/16, in timestamp units.

    Kept both ways Appendix A.8 shows: in floating point, and as an integer scaled by 16. The report carries the
    integer part. Packets are taken in order of arrival, as the RFC says, which here is also sequence order.
    """
    j_float, j_q4, trace = 0.0, 0, []
    previous = None
    for seq in sorted(arrived, key=arrived.get):
        r = round(arrived[seq] * RATE)  # the receiver's clock, in timestamp units
        s = (seq - FIRST_SEQ) * FRAME  # the packet's RTP timestamp
        transit = r - s
        if previous is not None:
            d = abs(transit - previous)
            j_float += (d - j_float) / 16
            j_q4 += d - ((j_q4 + 8) >> 4)
            trace.append((seq, d, j_float))
        previous = transit
    return j_float, j_q4 >> 4, trace


def figure2() -> None:
    """RFC 3550's own worked example (Figure 2), so the arithmetic is checked against the spec, not just itself."""
    a, lsr, dlsr = 0xB7108000, 0xB7052000, 0x00054000
    rtt = (a - lsr - dlsr) & 0xFFFFFFFF
    print("RFC 3550 Figure 2, the spec's own example:")
    print(f"  A    {fixed(a)}\n  LSR  {fixed(lsr)}\n  DLSR {fixed(dlsr)}\n  A - LSR - DLSR = {fixed(rtt)}")
    assert rtt == 0x00062000 and rtt / 65536 == 6.125
    print("  matches the RFC: 6.125 s\n")


def jitter_csv() -> None:
    """One row per packet that arrived, in arrival order: what the estimator saw and where it stood after it."""
    arrived = arrivals()
    _, _, trace = jitter(arrived)
    print("seq,arrival_ms,abs_d_units,abs_d_ms,jitter_units,jitter_ms")
    for seq, d, j in trace:
        print(f"{seq},{arrived[seq] * 1000:.3f},{d},{d / RATE * 1000:.3f},{j:.2f},{j / RATE * 1000:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--jitter-csv", action="store_true", help="print the jitter estimator per packet as CSV")
    if parser.parse_args().jitter_csv:
        jitter_csv()
        return
    figure2()

    # --- Alice's sender report, 4 s in -----------------------------------------------------------------------
    sent = int(SR_AT / 0.020)  # packets sent before the SR: seq 1000 to 1199
    sr_ntp = ntp64(CALL_START + SR_AT)
    sr_rtp = int(SR_AT * RATE)  # the RTP clock at the same instant, same origin as the data packets
    alice = sender_report(ALICE_SSRC, sr_ntp, sr_rtp, sent, sent * OPUS_BYTES, []) + sdes_cname(
        ALICE_SSRC, cname("alice")
    )
    sr_wall = datetime.fromtimestamp(CALL_START + SR_AT, timezone.utc)
    print(f"Alice's SR + SDES ({len(alice)} B), sent {sr_wall:%H:%M:%S.%f}"[:-3] + " UTC:")
    print(f"  {hexdump(alice)}")
    print(
        f"  NTP 0x{sr_ntp:016x} = {sr_ntp >> 32} s + {(sr_ntp & 0xFFFFFFFF) / (1 << 32)} s since 1900; "
        f"RTP timestamp {sr_rtp}; "
        f"{sent} packets, {sent * OPUS_BYTES} payload octets"
    )
    lsr = middle32(sr_ntp)
    print(f"  its middle 32 bits, which the server will echo as LSR: 0x{lsr:08x} = {fixed(lsr)}\n")

    # --- What the server saw ---------------------------------------------------------------------------------
    arrived = arrivals()
    highest = max(arrived)
    expected = highest - FIRST_SEQ + 1  # RFC 3550 A.3: extended_max - base_seq + 1, no wrap in this call
    received = len(arrived)
    lost = expected - received
    fraction = (lost << 8) // expected  # A.3: the first report, so the interval is the whole call so far
    j_float, j_int, trace = jitter(arrived)
    sr_arrives = SR_AT + ONE_WAY  # the SR rides the same path; the model gives it no queueing
    dlsr = int((RR_AT - sr_arrives) * 65536)

    print(f"The server, {RR_AT:.3f} s into the call:")
    print(
        f"  expected {expected} (seq {FIRST_SEQ} to {highest}), received {received}, lost {lost}: "
        f"{', '.join(map(str, sorted(LOST)))}"
    )
    print(
        f"  fraction lost = ({lost} << 8) // {expected} = {fraction}, which reads back as {fraction}/256 = "
        f"{fraction / 256:.2%} (the true share is {lost / expected:.2%})"
    )
    print(
        f"  interarrival jitter: {j_float:.2f} units in floating point, {j_int} as the RFC's scaled integer, "
        f"= {j_int / RATE * 1000:.3f} ms at 48 kHz"
    )
    print("  first steps, |D| then J: " + ", ".join(f"seq {s}: {d} -> {j:.2f}" for s, d, j in trace[:4]))
    print(
        f"  DLSR: the SR arrived at {sr_arrives:.3f} s and the RR leaves at {RR_AT:.3f} s, "
        f"{RR_AT - sr_arrives:.3f} s later = {dlsr} units of 1/65536 s\n"
    )

    block = ReportBlock(ALICE_SSRC, fraction, lost, highest, j_int, lsr, dlsr)
    server = receiver_report(SERVER_SSRC, [block]) + sdes_cname(SERVER_SSRC, cname("server"))
    print(f"The server's RR + SDES ({len(server)} B):")
    print(f"  {hexdump(server)}")
    print(f"  report block: {hexdump(block.pack())}\n")

    # --- Alice reads it back ---------------------------------------------------------------------------------
    parts = parse(server)
    got = parts[0]["blocks"][0]
    assert parts[0]["type"] == 201 and parts[1]["type"] == 202 and got == block
    assert parse(alice)[0]["ntp"] == sr_ntp
    rr_arrives = RR_AT + ONE_WAY
    a = middle32(ntp64(CALL_START + rr_arrives))
    rtt = (a - got.lsr - got.dlsr) & 0xFFFFFFFF
    print(
        f"Alice decodes it ({' + '.join({200: 'SR', 201: 'RR', 202: 'SDES'}[p['type']] for p in parts)}), "
        f"and it arrives {rr_arrives:.3f} s into the call:"
    )
    print(
        f"  fraction lost {got.fraction_lost} = {got.fraction_lost / 256:.2%}, cumulative lost {got.cumulative_lost}, "
        f"highest seq {got.highest_seq}, jitter {got.jitter} units"
    )
    print(f"  A    {fixed(a)}\n  LSR  {fixed(got.lsr)}\n  DLSR {fixed(got.dlsr)}")
    print(
        f"  round trip = A - LSR - DLSR = {rtt} / 65536 s = {rtt / 65536 * 1000:.3f} ms "
        f"(the model's is {2 * ONE_WAY * 1000:.0f} ms; the difference is the 16-bit fraction)"
    )

    # The server's clock never enters: DLSR is a duration it measured, A and LSR are both Alice's clock.
    print("  the server's clock never enters the sum: DLSR is a duration, A and LSR are both Alice's clock\n")

    # --- How often this can happen ---------------------------------------------------------------------------
    rtcp_interval(len(alice), len(server))

    cross_check(alice, server, block, sr_ntp)


def rtcp_interval(sr_bytes: int, rr_bytes: int) -> None:
    """RFC 3550 section 6.3.1 for this call: two members, one sender, 5% of the session bandwidth."""
    ip_udp = 28  # the interval counts lower-layer headers (section 6.2), IPv4 and UDP here
    session = (12 + OPUS_BYTES + ip_udp) * 8 * 50  # 120-byte packets, 50 a second: the first post's 48 kb/s
    rtcp_bw = 0.05 * session / 8  # bytes per second
    avg = ((sr_bytes + ip_udp) + (rr_bytes + ip_udp)) / 2
    members, senders = 2, 1
    # More than a quarter of the members send, so senders and receivers share the whole RTCP bandwidth.
    assert senders > 0.25 * members
    n_c = members * avg / rtcp_bw
    td = max(5.0, n_c)
    lo, hi = 0.5 * td / 1.21828, 1.5 * td / 1.21828
    reduced = 360 / (session / 1000)
    print(f"How often (RFC 3550 6.3.1), for a {session // 1000} kb/s session:")
    print(f"  RTCP gets 5% = {rtcp_bw * 8:.0f} b/s; average compound RTCP packet {avg:.0f} B with IPv4 and UDP")
    print(
        f"  n*C = {members} x {avg:.0f} / {rtcp_bw:.0f} = {n_c:.3f} s; with the 5 s minimum, Td = {td:.0f} s, "
        f"so T falls in [{lo:.3f}, {hi:.3f}] s, {td / 1.21828:.3f} s on average"
    )
    print(f"  the reduced minimum, 360 / {session // 1000} = {reduced:.1f} s, is no help below 72 kb/s")
    print(f"  without the 5 s floor (RTP/AVPF, RFC 4585 3.2): about {n_c / 1.21828:.3f} s on average\n")


def cross_check(alice: bytes, server: bytes, block: ReportBlock, sr_ntp: int) -> None:
    """aiortc's RTCP parser, an independent implementation, reads the same bytes."""
    try:
        from aiortc.rtp import RtcpPacket, RtcpRrPacket, RtcpSdesPacket, RtcpSrPacket
    except ImportError:
        print("aiortc is not installed; skipping the independent parse (pip install -r requirements.txt)")
        return
    sr, sdes_a = RtcpPacket.parse(alice)
    rr, sdes_s = RtcpPacket.parse(server)
    assert isinstance(sr, RtcpSrPacket) and isinstance(rr, RtcpRrPacket)
    assert isinstance(sdes_a, RtcpSdesPacket) and isinstance(sdes_s, RtcpSdesPacket)
    assert sr.ssrc == ALICE_SSRC and sr.sender_info.ntp_timestamp == sr_ntp
    info = rr.reports[0]
    theirs = (info.ssrc, info.fraction_lost, info.packets_lost, info.highest_sequence, info.jitter, info.lsr, info.dlsr)
    ours = (
        block.ssrc,
        block.fraction_lost,
        block.cumulative_lost,
        block.highest_seq,
        block.jitter,
        block.lsr,
        block.dlsr,
    )
    assert theirs == ours, (theirs, ours)
    assert bytes(sr) + bytes(sdes_a) == alice and bytes(rr) + bytes(sdes_s) == server
    print("aiortc parses both compounds to the same fields, and re-serialises them to the same bytes.")


if __name__ == "__main__":
    main()
