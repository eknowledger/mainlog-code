# Why a higher sample rate cannot restore lost sound

From the post [Why a higher sample rate cannot restore lost sound](https://mainlog.dev/posts/sample-rate-cannot-restore-sound/).

Three tones, 1, 3 and 6 kHz, taken from 48 kHz down to 8 kHz and back up. The 6 kHz tone does not come back, and
a conversion without a filter invents a false 2 kHz tone instead.

| File | What it is |
|---|---|
| `resampling_demo.py` | The measurement: which tones are still prominent at each stage |
| `resample_walkthrough.py` | The short version from the post: sample counts and durations only |
| `plot_experiment.py` | Recreates the rate-ladder and spectra figures |
| `plot_sampling_basics.py` | Recreates the introductory sound-and-samples figure |
| `requirements.txt` | NumPy, SciPy and Matplotlib at the versions the post used |

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python resampling_demo.py
```

```text
original     48000 Hz  48000 samples  1.000 s  [1000, 3000, 6000]
downsampled   8000 Hz   8000 samples  1.000 s  [1000, 3000]
upsampled    48000 Hz  48000 samples  1.000 s  [1000, 3000]
consumer     16000 Hz  16000 samples  1.000 s  [1000, 3000]
unfiltered    8000 Hz   8000 samples  1.000 s  [1000, 2000, 3000]
```

"Prominent" means above -40 dB relative to one original tone: a reporting cutoff, not a hearing threshold.

To recreate the figures: `.venv/bin/python plot_experiment.py --out figures` and
`.venv/bin/python plot_sampling_basics.py --out figures`.
