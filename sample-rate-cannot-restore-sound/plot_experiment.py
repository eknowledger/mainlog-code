"""Render the measured spectra and the experiment's rate ladder."""
from pathlib import Path
import argparse
import json
import platform
import numpy as np
import scipy
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from resampling_demo import make_signals, level_db, prominent_tones

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
signals = make_signals()
results = {}
for name, (x, fs) in signals.items():
    results[name] = {"rate_hz": fs, "samples": len(x), "duration_s": len(x)/fs,
                     "prominent_tones_hz": prominent_tones(x, fs),
                     "levels_db": {str(f): round(level_db(x, fs, f), 3)
                                   for f in [1000, 2000, 3000, 6000] if f < fs/2}}
(args.out / "results.json").write_text(json.dumps({
    "environment": {"python": platform.python_version(), "numpy": np.__version__,
                    "scipy": scipy.__version__, "matplotlib": matplotlib.__version__},
    "signals": results,
    "method": "1s synthetic signal, 0.25-amplitude tones; level measured in middle 0.8s relative to one original tone; prominent means above -40dB, not audibility. Spectra use Hann window on same middle 0.8s."
}, indent=2) + "\n")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "axes.titleweight": "bold"})
fig, axes = plt.subplots(4, 1, figsize=(10, 9.4), sharex=True, sharey=True)
series = [("original", "1. Original: 48 kHz", "#1d4e89"),
          ("downsampled", "2. Properly filtered: 48 → 8 kHz", "#b96715"),
          ("upsampled", "3. More samples again: 8 → 48 kHz", "#237357"),
          ("unfiltered", "4. No filter: keep every sixth sample", "#ad3d44")]
for ax, (name, title, color) in zip(axes, series):
    x, fs = signals[name]
    x = x[fs//10:-fs//10]
    window = np.hanning(len(x))
    amplitude = 2 * np.abs(np.fft.rfft(x * window)) / window.sum()
    freqs = np.fft.rfftfreq(len(x), 1/fs)
    level = 20*np.log10(np.maximum(amplitude / 0.25, 1e-6))
    ax.plot(freqs/1000, level, color=color, linewidth=1.5)
    ax.axhline(-40, color="#94a3b8", linestyle=":", linewidth=.8)
    if fs == 8000:
        ax.axvspan(4, 8, color="#f1f5f9", zorder=-1)
        ax.text(6, -82, "Above this signal's Nyquist limit", ha="center", color="#64748b", fontsize=10)
    ax.set_title(title, loc="left", fontsize=12, pad=8)
    ax.set_ylim(-100, 8); ax.set_xlim(0, 8)
    ax.set_yticks([-80, -40, 0]); ax.grid(axis="x", alpha=.18)
    for f in results[name]["prominent_tones_hz"]:
        ax.annotate(f"{f/1000:g} kHz", xy=(f/1000, 0), xytext=(f/1000 + .18, -22),
                    color=color, fontsize=10, arrowprops={"arrowstyle": "-", "color": color})
axes[2].annotate("No recovered 6 kHz tone\n(below plotted range)", xy=(6, -96),
                 xytext=(6, -24), ha="center", fontsize=10, color="#237357",
                 arrowprops={"arrowstyle": "->", "color": "#237357"})
axes[3].text(4.8, -24, "Unfiltered 6 kHz appears at 2 kHz",
             ha="center", fontsize=10, color="#ad3d44")
axes[-1].set_xlabel("Frequency (kHz)")
fig.supylabel("Level relative to one original tone (dB)", x=.016, fontsize=11)
fig.suptitle("More samples don't restore the lost band", x=.08, ha="left", fontsize=17, weight="bold", y=.985)
fig.subplots_adjust(left=.085, right=.98, bottom=.06, top=.93, hspace=.48)
fig.savefig(args.out / "spectra.png", dpi=160, facecolor="white", metadata={"Software": None})
fig.savefig(args.out / "spectra.svg", facecolor="white")
plt.close(fig)

# A numerical transformation figure, not a production architecture.
fig, ax = plt.subplots(figsize=(12, 3.1))
ax.set_xlim(0, 12); ax.set_ylim(0, 3.1); ax.axis("off")
xs=[.12,3.12,6.12,9.12]
labels=[("48 kHz", "48,000 samples", "1 + 3 + 6 kHz"),
        ("8 kHz", "8,000 samples", "1 + 3 kHz"),
        ("48 kHz", "48,000 samples", "1 + 3 kHz"),
        ("16 kHz", "16,000 samples", "1 + 3 kHz")]
for i,(x, lab) in enumerate(zip(xs, labels)):
    box=FancyBboxPatch((x,.84),2.72,1.36,boxstyle="round,pad=.02,rounding_size=.07",
                       facecolor="#eef4fb" if i==0 else "#f1f6f3",edgecolor="#57718c",linewidth=1.2)
    ax.add_patch(box)
    ax.text(x+1.36,1.88,lab[0],ha="center",fontsize=17,weight="bold",color="#16304b")
    ax.text(x+1.36,1.49,lab[1],ha="center",fontsize=11,color="#334155")
    ax.text(x+1.36,1.12,lab[2],ha="center",fontsize=12,color="#237357" if i else "#1d4e89")
    if i<3:
        ax.add_patch(FancyArrowPatch((x+2.76,1.55),(xs[i+1]-.03,1.55),arrowstyle="-|>",mutation_scale=12,color="#475569",linewidth=1.3))
for x,text in [(2.98,"÷6"),(5.98,"×6"),(8.98,"÷3")]:
    ax.text(x,2.34,text,ha="center",fontsize=12,color="#475569")
ax.text(.12,2.87,"One second of audio at every step",ha="left",fontsize=17,weight="bold",color="#16304b")
ax.text(3.05,.42,"6 kHz is filtered out here",ha="center",fontsize=11,color="#a85b14")
ax.text(7.45,.42,"The sample count grows; the missing tone does not return",ha="center",fontsize=10.5,color="#334155")
ax.text(.12,.05,"Tones shown above the experiment's −40 dB reporting cutoff. Finite filters leave small residuals.",fontsize=9,color="#64748b")
fig.subplots_adjust(left=.01,right=.99,top=.97,bottom=.03)
fig.savefig(args.out / "rate-ladder.png",dpi=150,facecolor="white",metadata={"Software": None})
fig.savefig(args.out / "rate-ladder.svg",facecolor="white")
plt.close(fig)
print(json.dumps(results,indent=2))
