"""A one-second, three-tone resampling experiment; no audio hardware needed."""
import numpy as np
from scipy.signal import resample_poly


def make_signals():
    fs = 48_000
    t = np.arange(fs) / fs
    original = sum(0.25 * np.sin(2 * np.pi * f * t)
                   for f in (1_000, 3_000, 6_000))
    narrow = resample_poly(original, up=1, down=6)
    expanded = resample_poly(narrow, up=6, down=1)
    consumer = resample_poly(expanded, up=1, down=3)
    naive = original[::6]
    return {
        "original": (original, 48_000),
        "downsampled": (narrow, 8_000),
        "upsampled": (expanded, 48_000),
        "consumer": (consumer, 16_000),
        "unfiltered": (naive, 8_000),
    }


def level_db(samples, fs, frequency):
    # Measure the middle 0.8 s, away from finite-record filter edges.
    x = samples[fs // 10 : -fs // 10]
    t = np.arange(len(x)) / fs
    amplitude = 2 * abs(np.dot(x, np.exp(-2j * np.pi * frequency * t))) / len(x)
    return 20 * np.log10(max(amplitude / 0.25, 1e-12))


def prominent_tones(samples, fs):
    # This -40 dB threshold is a reporting choice, not a hearing threshold.
    return [f for f in (1_000, 2_000, 3_000, 6_000)
            if f < fs / 2 and level_db(samples, fs, f) > -40]


if __name__ == "__main__":
    for name, (samples, fs) in make_signals().items():
        print(f"{name:12s} {fs:5d} Hz  {len(samples):5d} samples  "
              f"{len(samples)/fs:.3f} s  {prominent_tones(samples, fs)}")
