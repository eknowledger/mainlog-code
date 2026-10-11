"""What each box in a voice call sends, receives and does every 20 ms, for mesh, SFU, MCU and a server that is the
other peer.

    python3 topology_cost.py                  # the post's tables: N = 2, 3, 5, 10, then the one-to-one case
    python3 topology_cost.py --srtp-tag 10    # the same with SRTP's 10-byte authentication tag on every packet
    python3 topology_cost.py -n 10 --loudest 3
    python3 topology_cost.py -n 5 --bitrate 16 # any constant Opus bitrate, and --frame-ms 10, 20, 40 or 60

Standard library only, and deterministic: it is arithmetic, not a measurement. The packet is post 1's: 80 bytes of Opus
(constant 32 kb/s, 20 ms frames) in 12 bytes of RTP, 8 of UDP and 20 of IPv4, 50 packets a second. Like post 1 it counts
no SRTP tag and no header extensions unless asked to (--srtp-tag, --audio-level).

Assumptions, each a choice rather than a fact about any product:
- Every participant talks all the time and sends every 20 ms: no silence suppression (DTX), so these are the busiest
  case. With --loudest K the SFU forwards only K streams, chosen by the audio level each sender writes in its header
  (RFC 6464), without decoding anything.
- Mesh uses one encoder per peer connection, as RFC 7667 section 3.4 draws the independent-session mesh. A client that
  shares one encoder across connections does 1 encode instead of N-1; it still encrypts N-1 times, with N-1 keys.
- The MCU builds one mix per participant, everyone but themselves (RFC 7667 section 3.6.1: "N or N+1 mixes").
- The server that is the other peer talks back while the user talks: one stream each way, every frame.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

FRAME_MS = 20
RTP_B, UDP_B, IPV4_B = 12, 8, 20
AUDIO_LEVEL_B = 8  # RFC 8285 one-byte form: 4 B (0xBEDE + length) + 1 B ID/len + 1 B level + 2 B padding
NAMES = ["Alice", "Bob", "Carol", "Dan", "Erin", "Frank", "Grace", "Heidi", "Ivan", "Judy"]


@dataclass(frozen=True)
class Packet:
    srtp_tag: int = 0
    audio_level: bool = False
    bitrate_kbps: int = 32
    frame_ms: int = FRAME_MS

    @property
    def payload(self) -> int:
        """Opus bytes per frame at a constant bitrate: 32 kb/s x 20 ms / 8 = 80 B, post 1's."""
        return self.bitrate_kbps * self.frame_ms // 8

    @property
    def pps(self) -> float:
        return 1000 / self.frame_ms

    @property
    def size(self) -> int:
        return self.payload + RTP_B + UDP_B + IPV4_B + self.srtp_tag + (AUDIO_LEVEL_B if self.audio_level else 0)

    @property
    def kbps(self) -> float:
        return self.size * 8 * self.pps / 1000


@dataclass(frozen=True)
class Work:
    """Operations in one 20 ms frame. protect = SRTP encrypt and tag; unprotect = verify the tag and decrypt."""

    encode: int = 0
    protect: int = 0
    unprotect: int = 0
    decode: int = 0
    mix: int = 0


@dataclass(frozen=True)
class Cost:
    name: str
    up: int  # streams one client sends
    down: int  # streams one client receives
    server_in: int | None  # streams; None means there is no server
    server_out: int | None
    client: Work
    server: Work | None


def mesh(n: int) -> Cost:
    peers = n - 1
    return Cost("mesh", peers, peers, None, None,
                Work(encode=peers, protect=peers, unprotect=peers, decode=peers, mix=1 if peers > 1 else 0), None)


def sfu(n: int, loudest: int | None = None) -> Cost:
    k = n if loudest is None else min(loudest, n)
    heard = min(k, n - 1)  # a listener who is not among the k hears all k; a selected speaker hears k - 1
    out = k * (n - 1)  # each forwarded stream goes to everyone but its sender
    name = "SFU" if loudest is None or k == n else f"SFU, {k} loudest"
    return Cost(name, 1, heard, n, out,
                Work(encode=1, protect=1, unprotect=heard, decode=heard, mix=1 if heard > 1 else 0),  # each client
                Work(unprotect=n, protect=out))  # the server


def mcu(n: int) -> Cost:
    return Cost("MCU", 1, 1, n, n,
                Work(encode=1, protect=1, unprotect=1, decode=1),
                Work(unprotect=n, decode=n, mix=n if n > 2 else 0, encode=n, protect=n))  # with 2, a "mix" is one voice


def other_peer() -> Cost:
    # The decode may happen at the edge or behind it; somewhere, the model needs the sound, and the reply is encoded.
    return Cost("server is the peer", 1, 1, 1, 1,
                Work(encode=1, protect=1, unprotect=1, decode=1),
                Work(unprotect=1, decode=1, encode=1, protect=1))


def kb(streams: int | None, pkt: Packet) -> str:
    return "-" if streams is None else f"{round(streams * pkt.kbps, 3):g}"


def table(costs: list[Cost], pkt: Packet) -> None:
    print(f"  {'':22}{'one client':>22}{'the server':>24}")
    print(f"  {'pattern':22}{'up kb/s':>11}{'down kb/s':>11}{'in kb/s':>12}{'out kb/s':>12}{'out pkt/s':>11}")
    for c in costs:
        pps = "-" if c.server_out is None else f"{round(c.server_out * pkt.pps, 3):g}"
        print(f"  {c.name:22}{kb(c.up, pkt):>11}{kb(c.down, pkt):>11}"
              f"{kb(c.server_in, pkt):>12}{kb(c.server_out, pkt):>12}{pps:>11}")
    print()
    label = f"per {pkt.frame_ms:g} ms frame"
    print(f"  {label:22}{'one client':^30}  {'the server':^40}".rstrip())
    head = f"{'enc':>5}{'prot':>6}{'unprot':>7}{'dec':>5}{'mix':>5}"
    print(f"  {'pattern':22}{head}   {'unprot':>7}{'dec':>5}{'mix':>5}{'enc':>5}{'prot':>6}")
    for c in costs:
        w, s = c.client, c.server
        cl = f"{w.encode:>5}{w.protect:>6}{w.unprotect:>7}{w.decode:>5}{w.mix:>5}"
        sv = "     no server" if s is None else f"{s.unprotect:>7}{s.decode:>5}{s.mix:>5}{s.encode:>5}{s.protect:>6}"
        print(f"  {c.name:22}{cl}   {sv}")


def walk_three(pkt: Packet) -> None:
    """One 20 ms frame of Alice's voice on a three-person call, box by box."""
    print("One 20 ms frame of Alice's voice, Alice, Bob and Carol on the call:")
    print("  mesh  Alice encodes 2 (one encoder per connection), encrypts 2 (one key for Bob, one for Carol),")
    print(f"        sends 2 x {pkt.size} B")
    print(f"        Bob and Carol each decrypt 1 and decode 1 of hers; no server")
    print(f"  SFU   Alice encodes 1, encrypts 1, sends 1 x {pkt.size} B")
    print(f"        the SFU decrypts 1 with Alice's key, encrypts 2 (Bob's key, Carol's key), sends 2 x {pkt.size} B")
    print(f"        Bob and Carol each decrypt 1 and decode 1 of hers, and mix it with the other speaker locally")
    print(f"  MCU   Alice encodes 1, encrypts 1, sends 1 x {pkt.size} B")
    print(f"        the MCU decrypts 1 and decodes 1 of hers; it puts her samples into Bob's mix and Carol's mix,")
    print(f"        then encodes and encrypts those mixes, 1 each, which also carry the third person's voice")
    print(f"        Bob and Carol each decrypt 1 and decode 1 mixed stream")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, action="append", help="participants (repeatable); default 2, 3, 5, 10")
    ap.add_argument("--srtp-tag", type=int, default=0, choices=(0, 4, 10, 16),
                    help="SRTP tag bytes per packet: 10 for AES128_CM_HMAC_SHA1_80, 16 for AES-GCM; 0 as post 1 counts")
    ap.add_argument("--audio-level", action="store_true", help="add the RFC 6464 audio-level extension, 8 B")
    ap.add_argument("--loudest", type=int, help="the SFU forwards only this many streams")
    ap.add_argument("--bitrate", type=int, default=32, help="Opus kb/s, constant; default 32, post 1's")
    ap.add_argument("--frame-ms", type=int, default=FRAME_MS, choices=(10, 20, 40, 60), help="default 20")
    args = ap.parse_args()
    if args.bitrate * args.frame_ms % 8:
        ap.error("bitrate x frame length must be a whole number of bytes")
    pkt = Packet(args.srtp_tag, args.audio_level, args.bitrate, args.frame_ms)
    extra = f" + {pkt.srtp_tag} B SRTP tag" if pkt.srtp_tag else ""
    extra += f" + {AUDIO_LEVEL_B} B audio level" if pkt.audio_level else ""
    print(f"One stream: {pkt.payload} B Opus + {RTP_B} B RTP + {UDP_B} B UDP + {IPV4_B} B IPv4{extra} = {pkt.size} B, "
          f"{pkt.pps:g} packets/s = {round(pkt.kbps, 3):g} kb/s\n")
    sizes = args.n or [2, 3, 5, 10]
    for n in sizes:
        who = ", ".join(NAMES[:n]) if n <= 3 else f"{n} people"
        print(f"N = {n} ({who}), everyone talking, every stream forwarded")
        costs = [mesh(n), sfu(n), mcu(n)]
        if args.loudest and args.loudest < n:
            costs.insert(2, sfu(n, args.loudest))
        table(costs, pkt)
        print()
        if n == 3 and not args.n:
            walk_three(pkt)
    if not args.n and not args.loudest:
        print("N = 10, the SFU forwarding only the 3 loudest, picked from the senders' audio levels (RFC 6464)")
        table([sfu(10), sfu(10, 3)], pkt)
        print()
    print("One user and a service that is the other peer (RFC 7667 Topo-Point-to-Point), per session")
    table([other_peer()], pkt)
    print("  assumed: the user's audio is decoded to be used, at the edge or behind it, and the reply is encoded")
    print(f"  1,000 such sessions: {round(pkt.kbps, 3):g} Mb/s in and {round(pkt.kbps, 3):g} Mb/s out, and every 20 ms "
          "1,000 each of decrypt, decode, encode, encrypt")
    if not pkt.srtp_tag and not args.n:
        tag10 = Packet(10, pkt.audio_level, pkt.bitrate_kbps, pkt.frame_ms)
        gcm = Packet(16, pkt.audio_level, pkt.bitrate_kbps, pkt.frame_ms)
        print(f"\nWith SRTP's tag: {tag10.size} B = {round(tag10.kbps, 3):g} kb/s per stream with "
              f"AES128_CM_HMAC_SHA1_80 (10 B), {gcm.size} B = {round(gcm.kbps, 3):g} kb/s\n"
              "with AES-GCM (16 B). Every kb/s above scales by the same factor.")


if __name__ == "__main__":
    main()
