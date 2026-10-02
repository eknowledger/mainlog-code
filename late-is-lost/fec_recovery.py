"""Lose one Opus packet and get it back three ways: not at all, by concealment, and from the next packet's FEC.

    python fec_recovery.py

Encodes with libopus through PyAV, as the first post's demo does, with FEC switched on. Decodes by calling
libopus directly through ctypes, because recovering a frame from in-band FEC means asking the decoder to read the
NEXT packet's redundant copy (`opus_decode(..., decode_fec=1)`), which PyAV does not expose. (The encoder's
settings cannot go that way too: `opus_encoder_ctl` takes a variable argument list, which ctypes does not pass
correctly on every platform, Apple silicon included.) The signal is a synthetic voiced sound, a 140 Hz harmonic series shaped like a vowel and pulsed four
times a second like syllables, rather than a recording, so the run is reproducible and the numbers are the
same on every machine with the same libopus.

Each recovery is scored against the frame the decoder would have produced had nothing been lost, as a
signal-to-noise ratio over that 20 ms: how far the stand-in is from the real thing, in dB.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import sys
from fractions import Fraction

import av
import numpy as np

RATE = 48_000
FRAME_MS = 20
SAMPLES = RATE * FRAME_MS // 1000  # 960
BITRATE = 32_000
EXPECTED_LOSS = 10  # percent, what the encoder is told to plan for
LOST = 25  # which frame the network drops, half a second in
FRAMES = 50

def load_opus() -> ctypes.CDLL:
    for name in (ctypes.util.find_library("opus"), "libopus.so.0", "/opt/homebrew/lib/libopus.dylib", "libopus.dylib"):
        if not name:
            continue
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    sys.exit("libopus not found: apt install libopus0, or brew install opus")


opus = load_opus()
opus.opus_encoder_create.restype = ctypes.c_void_p
opus.opus_decoder_create.restype = ctypes.c_void_p
opus.opus_get_version_string.restype = ctypes.c_char_p
pcm_t = ctypes.c_int16 * SAMPLES


def voiced(n: int) -> list[int]:
    """n samples of a vowel-like sound: harmonics of 140 Hz under two formant peaks, pulsed at 4 Hz."""
    out = []
    for i in range(n):
        t = i / RATE
        s = 0.0
        for k in range(1, 30):
            f = 140 * k
            # Two formants, near 700 Hz and 1200 Hz, roughly an "ah".
            gain = math.exp(-(((f - 700) / 250) ** 2)) + 0.6 * math.exp(-(((f - 1200) / 300) ** 2)) + 0.02
            s += gain * math.sin(2 * math.pi * f * t) / k
        envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 4 * t)
        out.append(int(max(-1.0, min(1.0, 0.35 * s * envelope)) * 32767))
    return out


def encode(pcm: list[int], fec: bool = True) -> list[bytes]:
    """20 ms Opus frames at a constant 32 kb/s, with in-band FEC on and the encoder told to plan for 10% loss."""
    enc = av.CodecContext.create("libopus", "w")
    enc.sample_rate = RATE
    enc.layout = "mono"
    enc.format = "s16"
    enc.bit_rate = BITRATE
    enc.time_base = Fraction(1, RATE)
    enc.options = {"vbr": "off", "application": "voip", "frame_duration": str(FRAME_MS), "fec": "1" if fec else "0", "packet_loss": str(EXPECTED_LOSS if fec else 0)}
    samples = np.array(pcm, dtype=np.int16)
    packets: list[bytes] = []
    for i in range(0, len(samples), SAMPLES):
        frame = av.AudioFrame.from_ndarray(samples[i : i + SAMPLES].reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = i
        packets.extend(bytes(p) for p in enc.encode(frame))
    packets.extend(bytes(p) for p in enc.encode(None))
    return packets


def decoder() -> ctypes.c_void_p:
    err = ctypes.c_int()
    dec = ctypes.c_void_p(opus.opus_decoder_create(RATE, 1, ctypes.byref(err)))
    assert err.value == 0, err.value
    return dec


def decode(dec: ctypes.c_void_p, packet: bytes | None, fec: bool = False) -> list[int]:
    out = pcm_t()
    # The last argument asks for the FEC copy inside `packet` rather than `packet`'s own frame. No packet at all
    # (None) asks the decoder to conceal.
    n = opus.opus_decode(dec, packet, len(packet) if packet else 0, out, SAMPLES, 1 if fec else 0)
    assert n == SAMPLES, n
    return list(out)


def snr(reference: list[int], estimate: list[int]) -> float:
    signal = sum(x * x for x in reference)
    noise = sum((x - y) ** 2 for x, y in zip(reference, estimate))
    return math.inf if noise == 0 else 10 * math.log10(signal / noise)


def play(packets: list[bytes], lost: int, recover: str) -> list[int]:
    """Decode the stream as a receiver would, with frame `lost` missing and stood in for as `recover` says."""
    dec = decoder()
    out: list[int] = []
    for f, p in enumerate(packets):
        if f != lost:
            out += decode(dec, p)
        elif recover == "fec":
            out += decode(dec, packets[f + 1], fec=True)
        elif recover == "plc":
            out += decode(dec, None)
        else:
            decode(dec, None)  # the decoder's state moves on as a real receiver's would
            out += [0] * SAMPLES
    return out


def main() -> None:
    source = voiced(SAMPLES * FRAMES)
    packets = encode(source)
    dec = decoder()
    clean = [s for p in packets for s in decode(dec, p)]
    window = slice(LOST * SAMPLES, (LOST + 1) * SAMPLES)

    has_lbrr = getattr(opus, "opus_packet_has_lbrr", None)
    lbrr = f", {'carries' if has_lbrr(packets[LOST + 1], len(packets[LOST + 1])) == 1 else 'does not carry'} an FEC copy" if has_lbrr else ""
    print(f"decoder {opus.opus_get_version_string().decode()}; {BITRATE // 1000} kb/s, FEC on, planning for {EXPECTED_LOSS}% loss")
    print(f"{FRAMES} frames of {FRAME_MS} ms; frame {LOST} is lost; packet {LOST + 1} is {len(packets[LOST + 1])} B{lbrr}")
    for name, recover in (("silence", "silence"), ("concealment (PLC)", "plc"), ("FEC from the next packet", "fec")):
        print(f"  {name:<26} {snr(clean[window], play(packets, LOST, recover)[window]):5.1f} dB SNR over the lost 20 ms")

    # Not one lucky frame: every position from the fifth to the second-last, so the warm-up and the last
    # packet (which has no next packet to recover from) are left out.
    positions = range(5, FRAMES - 1)
    if has_lbrr:
        carried = [lost for lost in positions if has_lbrr(packets[lost + 1], len(packets[lost + 1])) == 1]
        print(f"  {len(carried)} of {len(positions)} packets carry an FEC copy of the frame before them")
    for name, recover in (("concealment (PLC)", "plc"), ("FEC from the next packet", "fec")):
        scores = []
        for lost in positions:
            w = slice(lost * SAMPLES, (lost + 1) * SAMPLES)
            scores.append(snr(clean[w], play(packets, lost, recover)[w]))
        ordered = sorted(scores)
        print(f"  {name:<26} {sum(scores) / len(scores):5.1f} dB on average over {len(scores)} positions, median {ordered[len(ordered) // 2]:.1f}, worst {ordered[0]:.1f}")
        if has_lbrr:
            # Opus re-encodes only the frames it judges important, so where the next packet has no copy the
            # decoder falls back to concealment and the two scores are identical. Scored apart, so the average
            # above is not read as what FEC does when it can act.
            covered = [x for lost, x in zip(positions, scores) if lost in carried]
            print(f"  {'':<26} {sum(covered) / len(covered):5.1f} dB where the next packet carries a copy ({len(covered)} positions)")


if __name__ == "__main__":
    main()
