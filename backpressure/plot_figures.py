"""Draws the post's two charts from shed_sim.py's runs.

    pip install -r requirements.txt
    python3 plot_figures.py --out figures/

answered-over-time.png  answered in time per 10 s, four runs, the sale shaded
how-many-and-what-kind.png  answered / told no / waited for nothing, three policies
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from shed_sim import Config, simulate, summarise

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
RED, BLUE, AQUA, AMBER = "#e34948", "#2a78d6", "#1a9e6f", "#c96d05"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
})


def timeline(out: Path) -> None:
    base = Config()
    runs = [
        ("Unbounded queue, 3 retries", dict(policy="fifo", retries=3), RED, (0, (4, 3))),
        ("Unbounded queue", dict(policy="fifo"), RED, "-"),
        ("Bounded queue (50)", dict(policy="bounded"), BLUE, "-"),
        ("Queue timeout", dict(policy="codel"), AQUA, "-"),
    ]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.axvspan(base.spike_start, base.spike_end, color="#fbeedd", zorder=0)
    ax.text(90, 104, "the sale, 150/s", ha="center", color=MUTED, fontsize=10)
    for label, kw, color, style in runs:
        w = simulate(replace(base, **kw)).windows
        xs = [i * 10 for i in range(len(w) + 1)]
        ax.step(xs, w + [w[-1]], where="post", color=color, linestyle=style, linewidth=2, label=label)
    ax.set_xlim(0, 300)
    ax.set_ylim(-3, 112)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_xticks(range(0, 301, 60), [f"{s} s" for s in range(0, 301, 60)])
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("When people first tried")
    ax.set_ylabel("Answered in time")
    # Direct labels where each line is alone.
    ax.annotate("unbounded queue:\nback at 210 s", xy=(205, 50), xytext=(222, 40), color=RED, fontsize=10,
                arrowprops={"arrowstyle": "-", "color": RED})
    ax.text(222, 9, "with 3 retries: never", color=RED, fontsize=10)
    ax.text(124, 78, "bounded queue and\nqueue timeout: ~65%", color=INK, fontsize=10)
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.02), ncol=4, frameon=False, fontsize=10, handlelength=2.5)
    fig.tight_layout()
    fig.savefig(out / "answered-over-time.png", dpi=160, facecolor="white", metadata={"Software": None})
    plt.close(fig)


def split(out: Path) -> None:
    rows = [("Unbounded queue", "fifo"), ("Bounded queue (50)", "bounded"), ("Queue timeout", "codel")]
    fig, ax = plt.subplots(figsize=(10, 3.2))
    for i, (label, policy) in enumerate(rows):
        s = summarise(simulate(replace(Config(), policy=policy)))
        left = 0.0
        y = len(rows) - 1 - i
        for value, color, name in ((s.answered, AMBER, "answered in time"), (s.refused, BLUE, "told no at once"),
                                   (s.timed_out, RED, "waited for nothing")):
            if value <= 0:
                continue
            ax.barh(y, value, left=left, color=color, height=0.56, edgecolor="white", linewidth=2)
            if value >= 6:
                ax.text(left + value / 2, y, f"{value:.1f}%", ha="center", va="center", color="white",
                        fontsize=10.5, weight="bold")
            left += value
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)])
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.tick_params(axis="y", length=0, labelcolor=INK)
    ax.spines["left"].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (AMBER, BLUE, RED)]
    ax.legend(handles, ["answered in time", "told no at once", "waited for nothing"], loc="lower left",
              bbox_to_anchor=(0.0, 1.0), ncol=3, frameon=False, fontsize=10)
    ax.set_xlabel("People who first arrived during the sale")
    fig.tight_layout()
    fig.savefig(out / "how-many-and-what-kind.png", dpi=160, facecolor="white", metadata={"Software": None})
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    timeline(a.out)
    split(a.out)


if __name__ == "__main__":
    main()
