"""Time one 20 ms frame of server work on this machine: an MCU's decrypt, decode, mix, encode and encrypt for N
participants, against an SFU's decrypt and re-encrypt of the same packets.

    python measure.py              # N = 3, 5, 10
    python measure.py -n 20

A measurement on one machine, not a benchmark. Both paths run from Python, so both carry the cost of a Python call per
operation; a server written in C or Go pays less of that, for both. Read the ratio between the two, not the
microseconds, and do not compare them across machines.

The codec is the libopus inside PyAV's wheel, configured as post 1's (32 kb/s constant, 20 ms frames). SRTP is libsrtp
through pylibsrtp, with SRTP_AES128_CM_HMAC_SHA1_80, the profile RFC 8827 makes mandatory for WebRTC. Every participant
has their own keys and the server has its own context for each, so nothing is shared that a real call would not share.
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import os
import platform
import statistics
import struct
import time
from fractions import Fraction

import av
import numpy as np
from pylibsrtp import Policy, Session

RATE, FRAME_MS = 48_000, 20
SAMPLES = RATE * FRAME_MS // 1000
BITRATE = 32_000
FRAMES = 50  # one second of 20 ms frames
PROFILE = Policy.SRTP_PROFILE_AES128_CM_SHA1_80
KEY_LEN = 30  # 16 B AES key + 14 B salt


def libopus_version() -> str:
    """The version string of the libopus inside PyAV's wheel, the one doing the encoding."""
    base = os.path.dirname(av.__file__)
    paths = glob.glob(os.path.join(base, ".dylibs", "libopus*"))  # macOS wheels
    paths += glob.glob(os.path.join(base, "..", "av.libs", "libopus*"))  # Linux wheels
    for path in paths:
        lib = ctypes.CDLL(path)
        lib.opus_get_version_string.restype = ctypes.c_char_p
        return lib.opus_get_version_string().decode()
    return "libopus (version unknown)"


def encoder() -> av.CodecContext:
    enc = av.CodecContext.create("libopus", "w")
    enc.sample_rate, enc.layout, enc.format, enc.bit_rate = RATE, "mono", "s16", BITRATE
    enc.time_base = Fraction(1, RATE)
    enc.options = {"vbr": "off", "application": "voip", "frame_duration": str(FRAME_MS)}
    return enc


def decoder() -> av.CodecContext:
    dec = av.CodecContext.create("libopus", "r")
    dec.sample_rate, dec.layout = RATE, "mono"
    return dec


def voice(i: int, n_frames: int) -> np.ndarray:
    """Post 2's synthetic vowel (harmonics under two formants, pulsed four times a second), at a pitch and phase of its
    own for each participant, and quiet enough that ten of them summed rarely clip."""
    t = np.arange(n_frames * SAMPLES) / RATE
    pitch = 140 * (1 + 0.08 * i)
    s = np.zeros_like(t)
    for k in range(1, 30):
        f = pitch * k
        gain = np.exp(-(((f - 700) / 250) ** 2)) + 0.6 * np.exp(-(((f - 1200) / 300) ** 2)) + 0.02
        s += gain * np.sin(2 * np.pi * f * t) / k
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t + i)
    return (np.clip(0.12 * s * envelope, -1, 1) * 32767).astype(np.int16)


def encode_frame(enc: av.CodecContext, pcm: np.ndarray, pts: int) -> list[bytes]:
    frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate, frame.pts = RATE, pts
    return [bytes(p) for p in enc.encode(frame)]


def rtp(seq: int, ts: int, ssrc: int, payload: bytes) -> bytes:
    return struct.pack("!BBHII", 0x80, 111, seq & 0xFFFF, ts, ssrc) + payload


def session(key: bytes, outbound: bool) -> Session:
    kind = Policy.SSRC_ANY_OUTBOUND if outbound else Policy.SSRC_ANY_INBOUND
    return Session(policy=Policy(key=key, ssrc_type=kind, srtp_profile=PROFILE))


def opus_frames(n: int, frames: int) -> list[list[bytes]]:
    """`frames` Opus packets per participant; the first comes out a frame late, so encode one extra."""
    out = []
    for i in range(n):
        enc, pcm, got = encoder(), voice(i, frames + 1), []
        for f in range(frames + 1):
            got += encode_frame(enc, pcm[f * SAMPLES:(f + 1) * SAMPLES], f * SAMPLES)
        out.append(got[:frames])
    return out


def mode(packet: bytes) -> str:
    """The coding mode in an Opus packet's first byte (RFC 6716 section 3.1): configs 0-11 SILK, 12-15 hybrid."""
    config = packet[0] >> 3
    return "SILK" if config < 12 else "hybrid" if config < 16 else "CELT"


def run(n: int, frames: int, opus: list[list[bytes]]
        ) -> tuple[list[float], list[float], list[float], dict[str, list[float]]]:
    """Per-frame server times in microseconds for MCU, SFU and the one-to-one peer, over one call `frames` long.

    Every codec context and SRTP session lives for the whole call, as on a real server; a fresh libopus encoder runs
    several times slower for its first few hundred milliseconds, so the first second is dropped as warm-up."""
    up_keys = [os.urandom(KEY_LEN) for _ in range(n)]  # each participant's key towards the server
    down_keys = [os.urandom(KEY_LEN) for _ in range(n)]  # the server's key towards each participant
    ssrcs = [0x1F2E3D4C + i for i in range(n)]
    # What arrives: each participant's packets, protected by that participant (client work, not timed).
    senders = [session(k, True) for k in up_keys]
    wire = [[senders[i].protect(rtp(f, f * SAMPLES, ssrcs[i], opus[i][f])) for f in range(frames)] for i in range(n)]

    # MCU: unprotect, decode, mix everyone-but-you, encode, protect. Its own SSRC per outgoing mix.
    m_in = [session(k, False) for k in up_keys]
    m_out = [session(k, True) for k in down_keys]
    decs, encs = [decoder() for _ in range(n)], [encoder() for _ in range(n)]
    mcu_t: list[float] = []
    by_mode: dict[str, list[float]] = {"SILK": [], "hybrid": [], "CELT": []}
    for f in range(frames):
        t0 = time.perf_counter_ns()
        pcm = []
        for i in range(n):
            payload = m_in[i].unprotect(wire[i][f])[12:]
            pcm.append(np.concatenate([fr.to_ndarray().reshape(-1) for fr in decs[i].decode(av.Packet(payload))]))
        stacked = np.stack(pcm).astype(np.int32)
        total = stacked.sum(axis=0)
        for i in range(n):
            mix = np.clip(total - stacked[i], -32768, 32767).astype(np.int16)
            e0 = time.perf_counter_ns()
            packets = encode_frame(encs[i], mix, f * SAMPLES)
            e1 = time.perf_counter_ns()
            for p in packets:
                m_out[i].protect(rtp(f, f * SAMPLES, 0x4D430000 + i, p))
                if f >= FRAMES:
                    by_mode[mode(p)].append((e1 - e0) / 1000)
        mcu_t.append((time.perf_counter_ns() - t0) / 1000)

    # SFU: unprotect each arrival once, re-protect a copy for every other participant under that receiver's key.
    s_in = [session(k, False) for k in up_keys]
    s_out = [session(k, True) for k in down_keys]
    sfu_t = []
    for f in range(frames):
        t0 = time.perf_counter_ns()
        for i in range(n):
            plain = s_in[i].unprotect(wire[i][f])
            for j in range(n):
                if j != i:
                    s_out[j].protect(plain)
        sfu_t.append((time.perf_counter_ns() - t0) / 1000)

    # The server that is the other peer: one user's frame in, decoded; one reply frame encoded and sent.
    p_in, p_out = session(up_keys[0], False), session(down_keys[0], True)
    dec, enc = decoder(), encoder()
    reply = voice(1, frames + 1)
    peer_t = []
    for f in range(frames):
        t0 = time.perf_counter_ns()
        payload = p_in.unprotect(wire[0][f])[12:]
        for fr in dec.decode(av.Packet(payload)):
            fr.to_ndarray()
        for p in encode_frame(enc, reply[f * SAMPLES:(f + 1) * SAMPLES], f * SAMPLES):
            p_out.protect(rtp(f, f * SAMPLES, 0x50450000, p))
        peer_t.append((time.perf_counter_ns() - t0) / 1000)
    return mcu_t[FRAMES:], sfu_t[FRAMES:], peer_t[FRAMES:], by_mode


def unit_costs(opus: list[bytes]) -> dict[str, float]:
    """Median microseconds of each single operation, over one call's frames after warm-up."""
    key = os.urandom(KEY_LEN)
    tx, rx = session(key, True), session(key, False)
    dec, enc, pcm = decoder(), encoder(), voice(0, len(opus) + 1)
    times: dict[str, list[float]] = {"SRTP protect": [], "SRTP unprotect": [], "Opus decode": [], "Opus encode": []}
    for f, payload in enumerate(opus):
        packet = rtp(f, f * SAMPLES, 0x1F2E3D4C, payload)
        t0 = time.perf_counter_ns(); wire = tx.protect(packet); t1 = time.perf_counter_ns()
        rx.unprotect(wire); t2 = time.perf_counter_ns()
        for fr in dec.decode(av.Packet(payload)):
            fr.to_ndarray()
        t3 = time.perf_counter_ns()
        encode_frame(enc, pcm[f * SAMPLES:(f + 1) * SAMPLES], f * SAMPLES)
        t4 = time.perf_counter_ns()
        if f >= FRAMES:
            for name, dt in zip(times, (t1 - t0, t2 - t1, t3 - t2, t4 - t3)):
                times[name].append(dt / 1000)
    return {name: statistics.median(v) for name, v in times.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, action="append", help="participants (repeatable); default 3, 5, 10")
    ap.add_argument("--seconds", type=int, default=20, help="seconds of call to time, after one of warm-up")
    args = ap.parse_args()
    sizes = args.n or [3, 5, 10]
    print(f"One machine: {platform.machine()}, {platform.system()} {platform.release()}, Python "
          f"{platform.python_version()}, PyAV {av.__version__} ({libopus_version()}), one core.")
    print(f"Median and, in brackets, 95th percentile of {args.seconds * FRAMES} frames, after a second of warm-up.\n")
    frames = (args.seconds + 1) * FRAMES
    opus = opus_frames(max(sizes), frames)
    units = unit_costs(opus[0])
    print("  one operation, one voice: " + ", ".join(f"{k} {v:.1f} us" for k, v in units.items()) + "\n")
    print(f"  {'N':>3}  {'MCU us/frame':>14}  {'SFU us/frame':>14}  {'MCU / SFU':>10}  {'MCU share of 20 ms':>19}"
          f"  {'mixes in SILK':>13}  {'encode, CELT / SILK':>20}")
    for n in sizes:
        mcu_t, sfu_t, peer_t, by_mode = run(n, frames, opus)
        silk = len(by_mode['SILK']) / sum(map(len, by_mode.values()))
        cost = ' / '.join(f'{statistics.median(by_mode[k]):.0f} us' if by_mode[k] else '-' for k in ('CELT', 'SILK'))
        m, s = statistics.median(mcu_t), statistics.median(sfu_t)
        m95 = statistics.quantiles(mcu_t, n=20)[-1]
        s95 = statistics.quantiles(sfu_t, n=20)[-1]
        print(f"  {n:>3}  {m:>7.0f} ({m95:>5.0f})  {s:>6.0f} ({s95:>5.0f})  {m / s:>9.0f}x  {m / 20_000:>18.1%}"
              f"  {silk:>13.0%}  {cost:>20}")
    p = statistics.median(peer_t)
    probe = encoder()
    encode_frame(probe, voice(0, 1), 0)
    look = probe.initial_padding  # FFmpeg fills this from libopus's OPUS_GET_LOOKAHEAD
    print("\n  latency is not the processing time: each encode also delays the sound by the encoder's look-ahead,")
    print(f"  {look} samples = {look * 1000 / RATE:g} ms here, which an MCU's re-encode adds again for every listener.")
    print("\n  libopus picks a mode per frame from the sound, SILK (linear prediction) or CELT, and an encode in SILK")
    print("  cost several times one in CELT here. A mix of nine of these voices went into SILK for much of the call;")
    print("  so the MCU's cost depends on what it is mixing, not only on how many people are in it.")
    print(f"\n  one user, server is the peer: {p:.0f} us per frame (decrypt, decode, encode a reply, encrypt), "
          f"{p / 20_000:.1%} of 20 ms")


if __name__ == "__main__":
    main()
