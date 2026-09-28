"""Draw one 1 kHz cycle with two different sample densities."""
from pathlib import Path
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

p = argparse.ArgumentParser()
p.add_argument("--out", type=Path, required=True)
out = p.parse_args().out
out.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                     "axes.spines.top": False, "axes.spines.right": False})
fig, axes = plt.subplots(2, 1, figsize=(9, 5.8), sharex=True, sharey=True)
continuous_t = np.linspace(0, .001, 1001)
for ax, fs, color in zip(axes, [8000, 48000], ["#b36618", "#206b99"]):
    t = np.arange(fs // 1000) / fs
    ax.plot(continuous_t * 1000, np.sin(2*np.pi*1000*continuous_t),
            color="#8190a2", linewidth=2, label="Same 1 kHz sound wave")
    ax.scatter(t * 1000, np.sin(2*np.pi*1000*t), color=color,
               s=32, zorder=3, label="Stored measurements (samples)")
    ax.axhline(0, color="#cbd5e1", linewidth=.8)
    ax.set_title(f"{fs//1000} kHz sample rate: {fs//1000} measurements in this millisecond",
                 loc="left", fontsize=12, weight="bold", pad=10)
    ax.set_ylim(-1.3,1.3); ax.set_xlim(-.01,1.01)
    ax.set_yticks([-1,0,1]); ax.grid(axis="x",alpha=.2)
    ax.set_ylabel("Wave height\n(relative units)")
axes[0].legend(loc="upper right", fontsize=9, framealpha=.95)
axes[1].set_xticks([0,.25,.5,.75,1]);axes[1].set_xlabel("Time (milliseconds)")
fig.suptitle("The beep stays the same. The number of samples changes.",
             x=.10,ha="left",fontsize=14,weight="bold",y=.99)
fig.text(.10,.012,"Each curve completes one cycle in 1 ms: 1,000 cycles per second. Dots cover 0 ≤ t < 1 ms.",fontsize=9,color="#475569")
fig.subplots_adjust(left=.10,right=.98,bottom=.12,top=.88,hspace=.48)
fig.savefig(out / "sound-and-samples.png",dpi=160,facecolor="white",metadata={"Software":None})
fig.savefig(out / "sound-and-samples.svg",facecolor="white")
print("Plotted 8 and 48 samples over the same 1 ms cycle.")
