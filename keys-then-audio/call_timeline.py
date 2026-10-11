"""One call from cold start: two aiortc peers in one process, timed phase by phase from their own packets.

    python call_timeline.py                 # ten calls: the first in full, then the spread
    python call_timeline.py --runs 30
    python call_timeline.py --profile cm80  # offer only SRTP_AES128_CM_HMAC_SHA1_80, to see its 10-byte tag

"Alice" makes the offer and "server" answers, as in the post. Every datagram either side sends is recorded on its way
out, so the timeline is built from the packets rather than from library callbacks. One UDP port carries three
protocols, told apart by the first byte (RFC 7983 section 7):

  * STUN, 0 to 3: the ICE connectivity checks and their answers
  * DTLS, 20 to 63: the handshake, decoded here record by record into its messages
  * SRTP and SRTCP, 128 to 191: media, once the keys exist

Times are milliseconds from just before Alice creates her offer, by time.perf_counter.

What is fixed, so the run measures the protocols and nothing else: ICE gathers on 127.0.0.1 only, so no packet leaves
the machine, and no STUN server is configured, so there are no server-reflexive candidates (aiortc would otherwise ask
stun.l.google.com). Signalling is a function call: the offer and answer go straight from one peer to the other.

So these are loopback numbers on one machine. They exclude every network round trip, all packet loss (and so every
DTLS retransmission), STUN and TURN gathering, the signalling server, and a browser's own work. What they show is the
order of the phases, the packets each one needs, and how little of the time is the protocols' own computation.

The last line runs one more call with one character of the server's fingerprint changed in the answer Alice receives.
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import math
import statistics
import struct
import time
from dataclasses import dataclass, field

import aioice.ice
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError
from aiortc.rtcdtlstransport import SRTP_AES128_CM_SHA1_80, X509_DIGEST_ALGORITHMS, certificate_digest
from av import AudioFrame

# Loopback only, so nothing leaves the machine. aioice leaves 127.0.0.1 out by default.
aioice.ice.get_host_addresses = lambda use_ipv4, use_ipv6: ["127.0.0.1"]

RATE, PTIME = 48_000, 0.020
SAMPLES = int(RATE * PTIME)  # 960, as in the first post

HANDSHAKE_TYPES = {
    1: "ClientHello", 2: "ServerHello", 3: "HelloVerifyRequest", 4: "NewSessionTicket", 11: "Certificate",
    12: "ServerKeyExchange", 13: "CertificateRequest", 14: "ServerHelloDone", 15: "CertificateVerify",
    16: "ClientKeyExchange", 20: "Finished",
}
RECORD_VERSIONS = {0xFEFF: "DTLS 1.0", 0xFEFD: "DTLS 1.2"}


class Tone(AudioStreamTrack):
    """A 440 Hz tone at 48 kHz in 20 ms frames, standing in for Alice's voice."""

    def __init__(self) -> None:
        super().__init__()
        self._n = 0
        self._t0: float | None = None

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        if self._t0 is None:
            self._t0 = time.time()
        else:
            await asyncio.sleep(self._t0 + self._n * PTIME - time.time())
        start = self._n * SAMPLES
        pcm = struct.pack(
            f"<{SAMPLES}h", *(int(0.5 * 32767 * math.sin(2 * math.pi * 440 * (start + k) / RATE)) for k in range(SAMPLES))
        )
        frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES)
        frame.planes[0].update(pcm)
        frame.sample_rate, frame.pts, frame.time_base = RATE, start, fractions.Fraction(1, RATE)
        self._n += 1
        return frame


def classify(data: bytes) -> str:
    first = data[0]
    if first <= 3:
        return "STUN"
    if 20 <= first <= 63:
        return "DTLS"
    if 128 <= first <= 191:
        return "SRTCP" if 192 <= data[1] <= 223 else "SRTP"  # RTCP packet types sit where RTP's marker+PT would
    return "?"


def dtls_messages(data: bytes) -> tuple[list[str], set[str]]:
    """Name what one datagram carries, record by record (RFC 6347 sections 4.1 and 4.2.2).

    A handshake message can be split across records (each piece carries the same message_seq and its own offset), so
    a message is named once, at its first piece, with the number of pieces it came in.
    """
    names: list[str] = []
    pieces: dict[int, int] = {}
    versions: set[str] = set()
    i = 0
    while i + 13 <= len(data):
        ctype, version, epoch = data[i], int.from_bytes(data[i + 1 : i + 3]), int.from_bytes(data[i + 3 : i + 5])
        length = int.from_bytes(data[i + 11 : i + 13])
        body = data[i + 13 : i + 13 + length]
        versions.add(RECORD_VERSIONS.get(version, hex(version)))
        if ctype == 20:
            names.append("ChangeCipherSpec")
        elif ctype == 22 and epoch == 0:
            j = 0
            while j + 12 <= len(body):
                msg_type, msg_seq = body[j], int.from_bytes(body[j + 4 : j + 6])
                frag_len = int.from_bytes(body[j + 9 : j + 12])
                if msg_seq not in pieces:
                    names.append(f"{HANDSHAKE_TYPES.get(msg_type, f'type {msg_type}')}#{msg_seq}")
                pieces[msg_seq] = pieces.get(msg_seq, 0) + 1
                j += 12 + frag_len
        elif ctype == 22:
            names.append("Finished (encrypted)")  # epoch 1 is under the new keys: only the record header is readable
        elif ctype == 21:
            names.append("Alert (encrypted)" if epoch else "Alert")
        i += 13 + length

    def label(name: str) -> str:
        base, _, seq = name.partition("#")
        if not seq:
            return base
        n = pieces[int(seq)]
        return f"{base} ({n} pieces)" if n > 1 else base

    return [label(n) for n in names], versions


@dataclass
class Packet:
    t: float
    side: str
    kind: str
    size: int
    what: str = ""


@dataclass
class Call:
    t0: float = field(default_factory=time.perf_counter)
    packets: list[Packet] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)
    versions: set[str] = field(default_factory=set)
    rtp_sizes: list[tuple[int, int]] = field(default_factory=list)

    def now(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, self.now())


# STUN checks leave through aioice's own sockets rather than the transport's send, so they are caught one level down.
_stun_hooks: list = []
_original_send_stun = aioice.ice.StunProtocol.send_stun

# --delay: every datagram, STUN, DTLS or media, is held this long before it is sent, as a link with that one-way delay
# would hold it. Order is kept, since every packet waits the same time. Nothing is lost.
ONE_WAY_DELAY = 0.0


def _send_stun(self, message, addr):  # noqa: ANN001
    for hook in _stun_hooks:
        hook(self, message)
    if ONE_WAY_DELAY:
        asyncio.get_running_loop().call_later(ONE_WAY_DELAY, _original_send_stun, self, message, addr)
    else:
        _original_send_stun(self, message, addr)


aioice.ice.StunProtocol.send_stun = _send_stun


def instrument(call: Call, pc: RTCPeerConnection, side: str, profile: str) -> None:
    """Record every datagram this peer sends, and when each phase completes."""
    dtls = pc.getTransceivers()[0].sender.transport
    ice = dtls.transport
    if profile == "cm80":
        dtls._srtp_profiles = [SRTP_AES128_CM_SHA1_80]

    original_send, original_recv = ice._send, ice._recv

    async def send(data: bytes) -> None:
        kind, what = classify(data), ""
        if kind == "DTLS":
            names, versions = dtls_messages(data)
            call.versions |= versions
            what = ", ".join(names)
        elif kind == "SRTP":
            call.mark(f"{side}: first SRTP sent")
        call.packets.append(Packet(call.now(), side, kind, len(data), what))
        if ONE_WAY_DELAY:
            async def later() -> None:
                try:
                    await original_send(data)
                except ConnectionError:
                    pass  # the call ended while this packet was in flight

            asyncio.get_running_loop().call_later(ONE_WAY_DELAY, lambda: asyncio.ensure_future(later()))
        else:
            await original_send(data)

    async def recv() -> bytes:
        data = await original_recv()
        if data and classify(data) == "SRTP":
            call.mark(f"{side}: first SRTP received")
        return data

    ice._send, ice._recv = send, recv

    def stun_hook(proto, message) -> None:  # noqa: ANN001
        if proto.receiver is ice._connection:
            kind = "request" if message.message_class.name == "REQUEST" else "response"
            call.packets.append(Packet(call.now(), side, "STUN", len(bytes(message)), f"Binding {kind}"))

    _stun_hooks.append(stun_hook)

    # The plaintext RTP size beside the protected one: the difference is what SRTP adds.
    original_send_rtp = dtls._send_rtp

    async def send_rtp(data: bytes) -> None:
        await original_send_rtp(data)
        if classify(data) == "SRTP" and len(call.rtp_sizes) < 3:
            call.rtp_sizes.append((len(data), call.packets[-1].size))

    dtls._send_rtp = send_rtp

    @ice.on("statechange")
    def _ice() -> None:
        if ice.state == "completed":
            call.mark(f"{side}: ICE completed")

    @dtls.on("statechange")
    def _dtls() -> None:
        if dtls.state == "connected":
            call.mark(f"{side}: DTLS connected")


def dtls_facts(pc: RTCPeerConnection, remote_sdp: str) -> dict:
    """What this side's DTLS ended up with, and its own check of the other side's certificate."""
    dtls = pc.getTransceivers()[0].sender.transport
    ssl = dtls._ssl
    algorithm, sdp_fp = next(x for x in remote_sdp.splitlines() if x.startswith("a=fingerprint:"))[14:].split(" ", 1)
    peer = ssl.get_peer_certificate(as_cryptography=True)
    return {
        "role": dtls._role,
        "version": ssl.get_protocol_version_name(),
        "cipher": ssl.get_cipher_name(),
        "profile": ssl.get_selected_srtp_profile().decode(),
        "algorithm": algorithm,
        "fingerprint_sdp": sdp_fp,
        "fingerprint_seen": certificate_digest(peer, algorithm) if algorithm in X509_DIGEST_ALGORITHMS else "?",
    }


@dataclass
class Result:
    call: Call
    alice: dict
    server: dict
    setup: tuple[str, str]


async def one_call(profile: str = "default", tamper: bool = False) -> Result | str:
    config = RTCConfiguration(iceServers=[])
    alice, server = RTCPeerConnection(config), RTCPeerConnection(config)
    call = Call()
    decoded = asyncio.Event()

    @server.on("track")
    def on_track(track) -> None:  # noqa: ANN001
        async def first_frame() -> None:
            try:
                await track.recv()
            except MediaStreamError:
                return
            call.mark("server: first audio frame decoded")
            decoded.set()

        asyncio.ensure_future(first_frame())

    alice.addTrack(Tone())
    instrument(call, alice, "Alice", profile)
    call.t0 = time.perf_counter()
    try:
        await alice.setLocalDescription(await alice.createOffer())  # aiortc gathers here, before the offer exists
        call.mark("Alice: offer ready")
        await server.setRemoteDescription(alice.localDescription)
        server.addTrack(Tone())
        instrument(call, server, "server", profile)
        await server.setLocalDescription(await server.createAnswer())
        call.mark("server: answer ready")
        answer = server.localDescription.sdp
        if tamper:
            line = next(x for x in answer.splitlines() if x.startswith("a=fingerprint:"))
            answer = answer.replace(line, line[:-1] + ("0" if line[-1] != "0" else "1"))
        await alice.setRemoteDescription(RTCSessionDescription(answer, "answer"))

        if tamper:
            dtls = alice.getTransceivers()[0].sender.transport
            for _ in range(300):
                if dtls.state in ("failed", "connected"):
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.2)  # long enough for media to start, if it were going to
            sent = {who: sum(p.kind == "SRTP" and p.side == who for p in call.packets) for who in ("Alice", "server")}
            read = "read some" if "Alice: first SRTP received" in call.marks else "read none"
            return (f"{dtls.state}. Alice sent {sent['Alice']} media packets and {read}; the server, whose own check "
                    f"of Alice passed, sent {sent['server']}")
        await asyncio.wait_for(decoded.wait(), timeout=10)
        setup = tuple(
            next(x for x in sdp.splitlines() if x.startswith("a=setup:"))[8:]
            for sdp in (alice.localDescription.sdp, answer)
        )
        return Result(call, dtls_facts(alice, answer), dtls_facts(server, alice.localDescription.sdp), setup)
    finally:
        _stun_hooks.clear()
        await alice.close()
        await server.close()


def phases(r: Result) -> dict[str, float]:
    """Phase boundaries, from the packets where the packets can tell, from the state changes where they cannot."""
    m, pk = r.call.marks, r.call.packets
    stun = [p.t for p in pk if p.kind == "STUN"]
    dtls = [p for p in pk if p.kind == "DTLS" and "Alert" not in p.what]
    return {
        "offer": m["Alice: offer ready"],
        "answer": m["server: answer ready"],
        "first check": stun[0],
        "last check answered": max(stun),
        "ICE completed (server)": m["server: ICE completed"],
        "ICE completed (Alice)": m["Alice: ICE completed"],
        "ICE completed (both)": max(m["Alice: ICE completed"], m["server: ICE completed"]),
        "first DTLS": dtls[0].t,
        "last DTLS": dtls[-1].t,
        "DTLS connected (Alice)": m["Alice: DTLS connected"],
        "DTLS connected (server)": m["server: DTLS connected"],
        "DTLS connected (first)": min(m["Alice: DTLS connected"], m["server: DTLS connected"]),
        "DTLS connected (both)": max(m["Alice: DTLS connected"], m["server: DTLS connected"]),
        "first SRTP sent": min(m["Alice: first SRTP sent"], m["server: first SRTP sent"]),
        "first SRTP received": m["server: first SRTP received"],
        "first frame decoded": m["server: first audio frame decoded"],
    }


def print_call(r: Result) -> None:
    call = r.call
    end = call.marks["server: first audio frame decoded"]
    where = f"loopback with {ONE_WAY_DELAY * 1000:g} ms added each way" if ONE_WAY_DELAY else "loopback, one machine"
    print(f"One call, every datagram up to the first decoded audio frame (ms from the start, as sent; {where}):\n")
    flight, last_side, srtp_shown, hidden = 0, None, 0, 0
    for p in call.packets:
        if p.t > end:
            break
        if p.kind in ("SRTP", "SRTCP"):
            srtp_shown += 1
            if srtp_shown > 2:
                hidden += 1
                continue
        label = p.what
        if p.kind == "DTLS":
            if p.side != last_side:
                flight, last_side = flight + 1, p.side
            label = f"flight {flight}: {p.what}"
        arrow = "Alice  -> server" if p.side == "Alice" else "server -> Alice "
        print(f"  {p.t:7.2f}  {arrow}  {p.kind:<5} {p.size:>4} B  {label}")
    print(f"  ...      {hidden} more media packets before the server's first decoded frame\n")

    dtls = [p for p in call.packets if p.kind == "DTLS" and "Alert" not in p.what]
    stun = [p for p in call.packets if p.kind == "STUN"]
    print(f"ICE:  {sum('request' in p.what for p in stun)} Binding requests, "
          f"{sum('response' in p.what for p in stun)} responses, {sum(p.size for p in stun)} bytes")
    print(f"DTLS: {flight} flights, {len(dtls)} datagrams, {sum(p.size for p in dtls)} bytes; "
          f"record-layer versions on the wire: {', '.join(sorted(call.versions))}\n")

    print(f"Offer a=setup:{r.setup[0]}, answer a=setup:{r.setup[1]}")
    for who, facts in (("Alice", r.alice), ("server", r.server)):
        print(f"  {who:<6} is the DTLS {facts['role']}: {facts['version']}, {facts['cipher']}, "
              f"SRTP profile {facts['profile']}")
    for who, facts in (("Alice", r.alice), ("server", r.server)):
        other = "server" if who == "Alice" else "Alice"
        verdict = "match" if facts["fingerprint_sdp"] == facts["fingerprint_seen"] else "MISMATCH"
        print(f"  {who:<6} checks {other}'s certificate: the SDP says {facts['algorithm']} "
              f"{facts['fingerprint_sdp'][:17]}..., the certificate hashes to {facts['fingerprint_seen'][:17]}...: "
              f"{verdict}")
    plain, protected = call.rtp_sizes[0]
    print(f"  First media packet: {plain} B of RTP left as {protected} B of SRTP, {protected - plain} B added\n")

    ph = {k: v for k, v in phases(r).items() if "(both)" not in k and "(first)" not in k}
    print("Phases, in the order they happened:")
    for name, t in sorted(ph.items(), key=lambda kv: kv[1]):
        print(f"  {t:7.2f}  {name}")


SPANS = [
    ("offer created, candidates gathered", None, "offer"),
    ("ICE checks on the wire, first to last", "first check", "last check answered"),
    ("ICE: answer to both sides completed", "answer", "ICE completed (both)"),
    ("DTLS on the wire, first to last flight", "first DTLS", "last DTLS"),
    ("DTLS: first flight to both sides keyed", "first DTLS", "DTLS connected (both)"),
    ("first side keyed to its first SRTP sent", "DTLS connected (first)", "first SRTP sent"),
    ("first SRTP received to first frame decoded", "first SRTP received", "first frame decoded"),
    ("cold start to first frame decoded", None, "first frame decoded"),
]


def print_spread(results: list[Result]) -> None:
    where = f"loopback with {ONE_WAY_DELAY * 1000:g} ms added each way" if ONE_WAY_DELAY else "loopback, one machine"
    print(f"\nSpread over {len(results)} calls, ms ({where}):")
    print(f"  {'':<44} {'min':>7} {'median':>7} {'max':>7}")
    all_ph = [phases(r) for r in results]
    for label, start, end in SPANS:
        values = [ph[end] - (ph[start] if start else 0.0) for ph in all_ph]
        print(f"  {label:<44} {min(values):7.2f} {statistics.median(values):7.2f} {max(values):7.2f}")
    in_order = all(
        ph["offer"] < ph["first check"] <= ph["last check answered"] < ph["first DTLS"] <= ph["last DTLS"]
        < ph["first SRTP sent"] < ph["first SRTP received"] < ph["first frame decoded"]
        for ph in all_ph
    )
    no_early_media = all(
        min(p.t for p in r.call.packets if p.kind == "SRTP")
        > max(p.t for p in r.call.packets if p.kind == "DTLS" and "Alert" not in p.what)
        for r in results
    )
    print(f"\nEvery call ran gather, check, handshake, media, in that order: {'yes' if in_order else 'NO'}")
    print(f"No media packet left before the last handshake datagram, in any call: {'yes' if no_early_media else 'NO'}")


async def main(runs: int, profile: str) -> None:
    results = []
    for i in range(runs):
        r = await one_call(profile)
        assert isinstance(r, Result)
        results.append(r)
        if i == 0:
            print_call(r)
    print_spread(results)
    print("\nOne more call, with one character of the server's fingerprint changed in the answer Alice receives:")
    print(f"  Alice's DTLS transport ends {await one_call(profile, tamper=True)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--profile", choices=["default", "cm80"], default="default")
    parser.add_argument("--delay", type=float, default=0.0, metavar="MS",
                        help="hold every datagram this many ms before sending it: a one-way delay, so the RTT is twice it")
    args = parser.parse_args()
    ONE_WAY_DELAY = args.delay / 1000
    asyncio.run(main(args.runs, args.profile))
