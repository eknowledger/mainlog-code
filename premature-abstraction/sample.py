"""Draw the hand-check sample from cochange.py's JSON for httpx, and tally the classes read by hand.

    python3 cochange.py httpx-repo httpx --json httpx.json
    python3 sample.py httpx.json

The sample is 20 partial clone-group changes drawn with random.seed(2547). The labels in hand_check.json were made by
reading each commit's diff; this script only joins them to the draw, and fails if the draw no longer matches them,
which is what happens if the counting rules change and the sample has to be read again.
"""

from __future__ import annotations

import collections
import json
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent


def draw(result: dict, n: int = 20, seed: int = 2547) -> list[dict]:
    partial = [{"group": g["id"], **c} for g in result["groups"] for c in g["changes"] if not c["consistent"]]
    rng = random.Random(seed)
    return sorted(rng.sample(partial, n), key=lambda c: c["ts"]), len(partial)


def main(argv: list[str]) -> int:
    result = json.loads(pathlib.Path(argv[1]).read_text())
    labels = json.loads((HERE / "hand_check.json").read_text())
    sample, total = draw(result)
    changes = sum(len(g["changes"]) for g in result["groups"])
    print(f"{result['repo']} at {result['head']}: {changes} clone-group changes, {total} partial; sampled 20")
    drawn = [(c["group"], c["sha"][:7]) for c in sample]
    labelled = [(row["group"], row["sha"]) for row in labels["sample"]]
    if drawn != labelled:
        print("the draw no longer matches hand_check.json: read the new sample", file=sys.stderr)
        for d in drawn:
            print(f"  {d}", file=sys.stderr)
        return 1
    for c, row in zip(sample, labels["sample"]):
        print(f"  {row['class']:<12} {c['group']:<4} {c['sha'][:7]} {c['date']} {c['subject'][:60]}")
    tally = collections.Counter(row["class"] for row in labels["sample"])
    print("  sample: " + ", ".join(f"{k} {tally[k]}" for k in ("out_of_step", "on_purpose", "cosmetic", "miscounted")))
    pair = collections.Counter(row["class"] for row in labels["pair"])
    print("  _send_single_request, every one-sided change: " + ", ".join(f"{k} {v}" for k, v in sorted(pair.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
