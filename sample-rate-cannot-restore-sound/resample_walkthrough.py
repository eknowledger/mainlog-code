import numpy as np
from scipy.signal import resample_poly

t = np.arange(48_000) / 48_000       # One second, sampled at 48 kHz
original = sum(0.25 * np.sin(2 * np.pi * f * t)
               for f in (1_000, 3_000, 6_000))

narrow = resample_poly(original, up=1, down=6)
expanded = resample_poly(narrow, up=6, down=1)
consumer = resample_poly(expanded, up=1, down=3)
unfiltered = original[::6]          # Deliberately wrong for this input

for name, samples, rate in [
    ("original", original, 48_000),
    ("downsampled", narrow, 8_000),
    ("upsampled", expanded, 48_000),
    ("consumer", consumer, 16_000),
    ("unfiltered", unfiltered, 8_000),
]:
    print(f"{name:12s} {len(samples):5d} samples  "
          f"{len(samples) / rate:.3f} s")
