"""What splitting a pull request costs in waiting, measured on merged PRs. Standard library only.

For each PR: its size (lines added plus deleted) and its pickup wait, the hours from when it was ready for review
(the last "ready for review" event, or its creation if it never was a draft) to the first review by someone other
than its author. Bots, and PRs merged with no human review, are left out.

Then a straight-line fit of log(wait) on log(size). Its slope e is the elasticity: double a PR's size and its wait
is multiplied by 2^e. From e, what a feature waits if it is split into k PRs:

  one after another     k pieces each wait for their own pickup, so the expected total is k x (1/k)^e = k^(1-e)
                        times one PR's. Exact in expectation under the fit, whatever the scatter around it.
  side by side          the feature waits for the slowest of k pickups. If they are independent, that depends on
                        the scatter, so it is computed exactly from the fit's own residuals.
  side by side, in      one reviewer takes all k at once, so the feature waits for one pickup of a PR k times
  one sitting           smaller: k^(-e).

    python3 split_cost.py data/*.jsonl           # the post's tables, from the committed snapshot
    python3 split_cost.py --csv data/grafana_grafana.jsonl   # size,wait pairs, for a chart or the PR split cost tool

Every multiple above reads e as if size drove the wait. The report prints the other end too, as if size had no
effect at all: then one after another is k times one PR, side by side is the slowest of k ordinary pickups, and one
sitting is one pickup, x1. The truth is somewhere between. This is correlation, not cause. A small PR is often a small fix by a maintainer, picked up fast for that reason; the
fit describes these repositories in this window, not what your next PR will do.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

BINS = [(1, 10), (11, 50), (51, 200), (201, 500), (501, 2000), (2001, None)]
# A log needs a positive wait, so a PR reviewed under 3 minutes after it was ready counts as 3 minutes. Most of these
# are drafts reviewed before their author marked them ready; the report shows e with them left out, too.
FLOOR_H = 0.05
SPLITS = (2, 4, 8)
BOOTSTRAPS = 1000
DRAWS = 20_000


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def is_bot(author: dict | None) -> bool:
    """GitHub's own Bot type, an app's `name[bot]` login, or a user account named as a bot.

    Checked by hand against every login containing "bot" in the snapshot: zaniebot is a User account whose profile
    says "A bot for @zanieb", and subotac is a person, which a plain substring match wrongly dropped.
    """
    if not author:
        return True
    login = author["login"].lower()
    return author["__typename"] == "Bot" or login.endswith("[bot]") or login.endswith("bot")


def load(path: Path, counts: dict | None = None) -> list[dict]:
    """One row per merged PR with a human review: size, pickup wait in hours, review threads, dates.

    If `counts` is given, it is filled with why the others were left out."""
    rows = []
    if counts is not None:
        counts.update(total=0, bots=0, unreviewed=[], floored=0)
    for line in path.open():
        p = json.loads(line)
        if counts is not None:
            counts["total"] += 1
        if is_bot(p["author"]):
            if counts is not None:
                counts["bots"] += 1
            continue
        ready_events = p["timelineItems"]["nodes"]
        ready = ts(ready_events[0]["createdAt"]) if ready_events else ts(p["createdAt"])
        reviews = [
            r for r in p["reviews"]["nodes"]
            if r["submittedAt"] and not is_bot(r["author"]) and r["author"]["login"] != p["author"]["login"]
        ]
        if not reviews:
            if counts is not None:
                counts["unreviewed"].append(p["additions"] + p["deletions"])
            continue
        first = min(ts(r["submittedAt"]) for r in reviews)
        hours = (first - ready).total_seconds() / 3600
        if counts is not None and hours < FLOOR_H:
            counts["floored"] += 1
        rows.append({
            "size": max(1, p["additions"] + p["deletions"]),
            "wait": max(FLOOR_H, hours),
            "floored": hours < FLOOR_H,
            "threads": p["reviewThreads"]["totalCount"],
            # Any review by a bot or an AI reviewer, under the same is_bot rule as the filter above.
            "bot_review": any(r["submittedAt"] and is_bot(r["author"]) for r in p["reviews"]["nodes"]),
            "created": p["createdAt"][:10],
        })
    return rows


def fit(pairs: list[tuple[int, float]]) -> tuple[float, float, float]:
    """Least squares on log-log. Returns (e, a, r): slope, intercept, correlation."""
    xs = [math.log(s) for s, _ in pairs]
    ys = [math.log(w) for _, w in pairs]
    mx, my = st.mean(xs), st.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    e = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return e, my - e * mx, st.correlation(xs, ys)


def bootstrap(pairs: list[tuple[int, float]], rng: random.Random) -> tuple[float, float]:
    """The middle 95% of e over resamples of the PRs, with replacement."""
    es = sorted(fit([rng.choice(pairs) for _ in pairs])[0] for _ in range(BOOTSTRAPS))
    return es[int(0.025 * BOOTSTRAPS)], es[int(0.975 * BOOTSTRAPS) - 1]


def slowest_of(k: int, values: list[float]) -> float:
    """Expected largest of k independent draws from `values`, over their mean. Exact, not sampled.

    The largest of k draws is the i-th smallest value with probability (i/n)^k - ((i-1)/n)^k, so the expectation is a
    weighted sum over the sorted values. No randomness, so the answer can't depend on what else was computed first.
    """
    s = sorted(values)
    n = len(s)
    return sum(x * ((i / n) ** k - ((i - 1) / n) ** k) for i, x in enumerate(s, 1)) / st.mean(s)


def median_multiples(k: int, e: float, residuals: list[float], rng: random.Random) -> tuple[float, float, float]:
    """The same three ways on medians instead of means, sampled: (one after another, side by side, one sitting)."""
    one = st.median(residuals)
    piece = k ** -e
    serial = st.median(sum(rng.choice(residuals) for _ in range(k)) for _ in range(DRAWS)) * piece / one
    s = sorted(residuals)
    side = s[min(len(s) - 1, math.ceil(0.5 ** (1 / k) * len(s)) - 1)] * piece / one
    return serial, side, piece


def bot_share(rows: list[dict], lo: int, hi: int) -> float:
    """The percentage of PRs from lo to hi lines with any review by a bot."""
    b = [r["bot_review"] for r in rows if lo <= r["size"] <= hi]
    return 100 * sum(b) / len(b) if b else 0.0


def label(lo: int, hi: int | None) -> str:
    return f"{lo}-{hi}" if hi else f"{lo}+"


def report(path: Path) -> None:
    counts: dict = {}
    rows = load(path, counts)
    name = path.stem.replace("_", "/", 1)
    pairs = [(r["size"], r["wait"]) for r in rows]
    dates = sorted(r["created"] for r in rows)
    unreviewed = counts["unreviewed"]
    print(f"== {name}: {len(rows)} of {counts['total']} merged PRs by people, with a review by someone else, "
          f"created {dates[0]} to {dates[-1]}")
    print(f"left out: {counts['bots']} written by bots; {len(unreviewed)} by people with no review by anyone else"
          + (f", median {st.median(unreviewed):.0f} lines against {st.median(r['size'] for r in rows):.0f} for those kept"
             if unreviewed else ""))
    print(f"{'lines':>9} {'PRs':>5} {'median pickup':>14} {'review threads per 100 lines':>29}")
    for lo, hi in BINS:
        b = [r for r in rows if r["size"] >= lo and (hi is None or r["size"] <= hi)]
        if len(b) < 5:
            continue
        threads = 100 * sum(r["threads"] for r in b) / sum(r["size"] for r in b)
        print(f"{label(lo, hi):>9} {len(b):>5} {st.median(r['wait'] for r in b):>12.2f} h {threads:>29.2f}")

    e, a, r = fit(pairs)
    lo, hi = bootstrap(pairs, random.Random(1))
    print(f"fit: e = {e:.2f} (95% bootstrap {lo:.2f} to {hi:.2f}), r = {r:.2f}, n = {len(pairs)}; "
          f"doubling a PR multiplies its expected wait by {2 ** e:.2f}")
    unfloored = [(x["size"], x["wait"]) for x in rows if not x["floored"]]
    print(f"     without the {counts['floored']} PRs reviewed within 3 minutes of being ready: "
          f"e = {fit(unfloored)[0]:.2f}, n = {len(unfloored)}")
    residuals = [w / math.exp(a + e * math.log(s)) for s, w in pairs]
    waits = [w for _, w in pairs]
    print("split into k PRs, expected waiting against one PR's, if size drives the pickup as the fit says"
          " | if size has no effect:")
    print(f"{'k':>4} {'one after another':>24} {'side by side':>14} {'in one sitting':>15} | "
          f"{'after':>6} {'side':>6} {'sitting':>8}")
    for k in SPLITS:
        serial = f"x{k ** (1 - e):.2f} ({k ** (1 - hi):.2f} to {k ** (1 - lo):.2f})"
        side = f"x{k ** -e * slowest_of(k, residuals):.2f}"
        print(f"{k:>4} {serial:>24} {side:>14} {'x' + format(k ** -e, '.2f'):>15} | "
              f"{'x' + str(k):>6} {'x' + format(slowest_of(k, waits), '.2f'):>6} {'x1.00':>8}")
    m = median_multiples(8, e, residuals, random.Random(3))
    print(f"on medians instead of means, k = 8: one after another x{m[0]:.2f}, side by side x{m[1]:.2f}, "
          f"in one sitting x{m[2]:.2f}")
    one = math.exp(a + e * math.log(800))
    print(f"with a bot review: {bot_share(rows, 11, 50):.0f}% of PRs of 11-50 lines, {bot_share(rows, 501, 2000):.0f}% of "
          f"501-2000; one 800-line PR on the fit: median pickup {one * st.median(residuals):.1f} h, "
          f"mean {one * st.mean(residuals):.1f} h")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--csv", action="store_true", help="print size,wait_hours pairs instead of the report")
    args = ap.parse_args()
    if args.csv:
        print("size,wait_hours")
        for f in args.files:
            for r in load(f):
                print(f"{r['size']},{r['wait']:.2f}")
        return
    for f in args.files:
        report(f)


if __name__ == "__main__":
    sys.exit(main())
