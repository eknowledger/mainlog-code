"""A real WebRTC call between two aiortc peers in one process, with the network in the middle made worse on purpose.

    .venv/bin/python capture_call.py            # 10 s of audio, 30 ms each way, every 40th packet dropped
    .venv/bin/python capture_call.py --seconds 20

Alice's peer sends audio (aiortc's silent track, Opus-encoded, 20 ms a packet) to the server's peer over loopback,
through ICE, DTLS and SRTP exactly as a browser call would. Every datagram either side sends is held for 30 ms
before it leaves, which gives the call the second post's 60 ms round trip, and one of every 40 of Alice's RTP
packets is thrown away instead: 2.5%, chosen by sequence number so the same packets go every time.

The hook sits where aiortc hands a packet to SRTP, so it sees each RTCP packet in the clear before it is
encrypted, which is the only place a capture can. On the wire every one of them is SRTCP (the fourth post).

It then reads the server's receiver reports the way Alice's side has to, from the bytes, and checks them
against what was injected. The RTCP timing is aiortc's, randomised, so the exact report count and times differ
from run to run; the losses must match exactly and the round trip must land near 60 ms.
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import time

from aiortc import RTCConfiguration, RTCPeerConnection, rtcrtpsender
from aiortc.clock import current_ntp_time
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcdtlstransport import RTCDtlsTransport
from aiortc.rtp import RtcpPacket, RtcpRrPacket, RtcpSrPacket, is_rtcp

ALICE_SSRC = 0x1F2E3D4C  # the series' values, as in rtcp_reports.py
SERVER_SSRC = 0x7A3B9C5D
FIRST_SEQ = 1000
ONE_WAY = 0.030
NTP_UNIX_OFFSET = 2_208_988_800
DROP_EVERY = 40  # drop seq where (seq - 1000) % 40 == 39: 1039, 1079, ...

rtcrtpsender.random_sequence_number = lambda: FIRST_SEQ  # start at the first post's sequence number

first_rtp: list[float] = []  # when Alice's first RTP packet left, the zero of the report's timeline
dropped: list[int] = []
# (wallclock when handed to SRTP, middle 32 bits of its NTP timestamp, how far that timestamp lags the send, in s)
sent_srs: list[tuple[float, int, float]] = []
received_rrs: list[tuple[float, int, bytes]] = []  # (wallclock at Alice, NTP middle 32 at Alice, plaintext bytes)
original_send = RTCDtlsTransport._send_rtp


def middle32(ntp: int) -> int:
    return (ntp >> 16) & 0xFFFFFFFF


async def impaired_send(self: RTCDtlsTransport, data: bytes) -> None:
    """Every packet waits 30 ms; some of Alice's RTP never leaves. Order is kept, since every delay is equal."""
    now = time.time()
    if is_rtcp(data):
        for packet in RtcpPacket.parse(data):
            if isinstance(packet, RtcpSrPacket) and packet.ssrc == ALICE_SSRC:
                ntp = packet.sender_info.ntp_timestamp
                stamped = (ntp >> 32) - NTP_UNIX_OFFSET + (ntp & 0xFFFFFFFF) / (1 << 32)
                sent_srs.append((now, middle32(ntp), now - stamped))
            if isinstance(packet, RtcpRrPacket) and packet.ssrc == SERVER_SSRC:
                # Stamp it when it would reach Alice, the "A" of A - LSR - DLSR, on the clock aiortc uses for NTP.
                loop = asyncio.get_running_loop()
                loop.call_later(
                    ONE_WAY, lambda d=data: received_rrs.append((time.time(), middle32(current_ntp_time()), d))
                )
    else:
        ssrc = struct.unpack_from("!I", data, 8)[0]
        seq = struct.unpack_from("!H", data, 2)[0]
        if ssrc == ALICE_SSRC and not first_rtp:
            first_rtp.append(now)
        if ssrc == ALICE_SSRC and (seq - FIRST_SEQ) % DROP_EVERY == DROP_EVERY - 1:
            dropped.append(seq)
            return

    async def later() -> None:
        try:
            await original_send(self, data)
        except ConnectionError:
            pass

    asyncio.get_running_loop().call_later(ONE_WAY, lambda: asyncio.ensure_future(later()))


RTCDtlsTransport._send_rtp = impaired_send  # type: ignore[method-assign]


async def call(seconds: float) -> tuple[RTCPeerConnection, RTCPeerConnection]:
    # No STUN server: both peers are on this machine, and the run should not depend on reaching the internet.
    alice = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    server = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    sender = alice.addTrack(AudioStreamTrack())
    sender._ssrc = ALICE_SSRC
    alice.getTransceivers()[0].receiver._set_rtcp_ssrc(ALICE_SSRC)

    await alice.setLocalDescription(await alice.createOffer())
    await server.setRemoteDescription(alice.localDescription)
    for transceiver in server.getTransceivers():
        # A side that only receives still has an SSRC, to sign its reports with. aiortc draws it at random.
        transceiver.sender._ssrc = SERVER_SSRC
        transceiver.receiver._set_rtcp_ssrc(SERVER_SSRC)
    await server.setLocalDescription(await server.createAnswer())
    await alice.setRemoteDescription(server.localDescription)
    await asyncio.sleep(seconds)
    return alice, server


def report(alice_stats: dict) -> None:
    if not first_rtp or not received_rrs:
        raise SystemExit("The call never carried audio and reports; nothing to check.")
    print(
        f"Alice sent {len(sent_srs)} SRs; the server sent {len(received_rrs)} RRs; "
        f"the network dropped {len(dropped)} of Alice's RTP packets: {', '.join(map(str, dropped))}\n"
    )
    print("  RR at   highest  cum.lost  fraction      lost here  injected here  RTT (DLSR, by wallclock)  RTT (NTP)")
    prev_highest, prev_lost = FIRST_SEQ - 1, 0
    rtts_wall, rtts_ntp, matched, worst_gap = [], [], 0, 0.0
    t0 = first_rtp[0]
    for wall, a, data in received_rrs:
        rr = next(p for p in RtcpPacket.parse(data) if isinstance(p, RtcpRrPacket))
        block = next(b for b in rr.reports if b.ssrc == ALICE_SSRC)
        injected = sum(1 for s in dropped if prev_highest < s <= block.highest_sequence)
        lost_here = block.packets_lost - prev_lost
        expected_here = block.highest_sequence - prev_highest
        assert lost_here == injected, (block, injected)
        matched += 1
        rtt_wall = rtt_ntp = None
        sr = next(((t, lag) for t, m, lag in sent_srs if m == block.lsr), None)
        if block.lsr and sr is not None:
            rtt_wall = (wall - sr[0] - block.dlsr / 65536) * 1000
            rtt_ntp = ((a - block.lsr - block.dlsr) & 0xFFFFFFFF) / 65536 * 1000
            worst_gap = max(worst_gap, abs(rtt_ntp - sr[1] * 1000 - rtt_wall))
            rtts_wall.append(rtt_wall)
            rtts_ntp.append(rtt_ntp)
        print(
            f"  {wall - t0:6.2f}s  {block.highest_sequence:7d}  {block.packets_lost:8d}  "
            f"{block.fraction_lost:3d}/256={block.fraction_lost / 256:5.1%}  "
            f"{lost_here:3d} of {expected_here:3d}  {injected:13d}  "
            f"{'-' if rtt_wall is None else f'{rtt_wall:6.1f} ms':>24}  "
            f"{'-' if rtt_ntp is None else f'{rtt_ntp:6.1f} ms':>9}"
        )
        prev_highest, prev_lost = block.highest_sequence, block.packets_lost

    print(f"\nEvery RR's new losses matched the packets dropped in its interval: {matched} of {len(received_rrs)}.")
    if not rtts_wall:
        raise SystemExit("No report echoed an SR, so there is no round trip to compute.")
    print(
        f"Round trip from LSR/DLSR, against the SR's real send time: {min(rtts_wall):.1f} to "
        f"{max(rtts_wall):.1f} ms over {len(rtts_wall)} reports (the model is {2 * ONE_WAY * 1000:.0f} ms)."
    )
    print(
        f"The same, by RFC 3550's A - LSR - DLSR on the NTP clock: {min(rtts_ntp):.1f} to {max(rtts_ntp):.1f} ms. "
        "aiortc stamps its SR with the time of its last RTP packet, not the time the SR is sent,"
    )
    lags = [lag * 1000 for _, _, lag in sent_srs]
    print(
        f"  and in this run that stamp lagged the send by {min(lags):.1f} to {max(lags):.1f} ms, "
        f"and that lag accounts for the difference report by report, to within {worst_gap:.2f} ms."
    )
    assert worst_gap < 1.0, worst_gap
    rtt = alice_stats.get("roundTripTime")
    if rtt is not None:
        print(
            f"aiortc's own estimate, smoothed (getStats, remote-inbound-rtp): {rtt * 1000:.1f} ms; "
            f"its fractionLost {alice_stats.get('fractionLost')}, packetsLost {alice_stats.get('packetsLost')}."
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()
    alice, server = await call(args.seconds)
    stats = await alice.getSenders()[0].getStats()
    remote = next((s for s in stats.values() if s.type == "remote-inbound-rtp"), None)
    await alice.close()
    await server.close()
    report({} if remote is None else vars(remote))


if __name__ == "__main__":
    asyncio.run(main())
