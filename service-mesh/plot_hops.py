"""Draws the post's per-hop chart from bench_hops.py's JSON.

    python3 plot_hops.py results/bench_hops-m3pro.json --out figures/
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

INK, MUTED, GRID, BLUE, ORANGE = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#eb6834"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.edgecolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    runs = json.loads(a.runs.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4.4))
    for mtls, color, label, dx in ((False, BLUE, "plain HTTP between the proxies", -0.06), (True, ORANGE, "mutual TLS between the proxies", 0.06)):
        hops = range(0, 5)
        meds = []
        for h in hops:
            xs = [r["p50_ms"] * 1000 for r in runs if r["hops"] == h and (r["mtls"] == mtls or h == 0)]
            meds.append(statistics.median(xs))
            ax.scatter([h + dx] * len(xs), xs, s=18, color=color, alpha=0.35, linewidths=0, zorder=2)
        ax.plot(list(hops), meds, color=color, linewidth=2, marker="o", markersize=7, label=label, zorder=3)
        ax.annotate(f"{meds[-1]:.0f} µs", xy=(4, meds[-1]), xytext=(4.12, meds[-1] + (-20 if mtls else -60)),
                    color=INK, fontsize=10)
    ax.set_xticks(range(5), ["no proxy", "1 hop", "2 hops", "3 hops", "4 hops"])
    ax.set_xlim(-0.3, 4.6)
    ax.set_ylim(0, 1250)
    ax.set_ylabel("Median request time (µs)")
    ax.set_xlabel("Sidecar pairs between the client and the backend, one request at a time, 4,000 per run, 5 runs")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.annotate("49 µs: the client and\nbackend alone", xy=(0, 55), xytext=(-0.2, 300), color=MUTED, fontsize=10,
                arrowprops={"arrowstyle": "-", "color": MUTED})
    ax.text(1.6, 1000, "about 0.22 ms per hop, with or without mTLS", color=INK, fontsize=11)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(a.out / "cost-per-hop.png", dpi=160, facecolor="white", metadata={"Software": None})


if __name__ == "__main__":
    main()
