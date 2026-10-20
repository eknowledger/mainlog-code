"""What the state machine bought, from the recorded live run and the table. No model is called here.

    python demo.py            # the live run's totals, the call counts, the table, the behaviour space
    python demo.py --steps    # every recorded call, step by step
    python demo.py --table    # all 119 cells and the decision in each

Prices are relative, at Anthropic's published multipliers: one unit is one uncached input token. A cache write costs
1.25 units, a cache read 0.05 on Claude Opus 5.5 or 0.1 on Claude Sonnet 5, and an output token 5 on both ($20 against
$4 per million on Opus 5.5, $10 against $2 on Sonnet 5). "Input cost" leaves output out; "total" includes it. Other
platforms price separately, so these are ratios between designs at Anthropic's multipliers, not a bill.
"""
from __future__ import annotations

import itertools
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import booking

HERE = Path(__file__).parent
READ = {"claude-opus-5-5": 0.05, "claude-sonnet-5": 0.10}
WRITE = 1.25
OUTPUT = 5.0
NAME = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5": "Claude Sonnet 5"}
DESIGN = {"free": "free-form", "states": "per-state tools", "fixed": "fixed tools + code check",
          "code": "state machine, code runs steps"}


def load():
    return json.loads((HERE / "live-run.json").read_text())


def sent(c):
    return c["input"] + c["cache_write"] + c["cache_read"]


def effective(c):
    """Input cost: what the request's input tokens cost, cache writes and reads included."""
    return c["input"] + WRITE * c["cache_write"] + READ[c["model"]] * c["cache_read"]


def total(c):
    """Input cost plus the output the model wrote."""
    return effective(c) + OUTPUT * c["output"]


def calls_for(run, model, design, rep, steps=7):
    """One design's calls in one repetition. 'code' is the state machine that runs steps as code: it makes only the
    model calls of booking.MODEL_STEPS, which are the per-state design's requests at those steps."""
    if design == "code":
        return [c for c in run["calls"] if c["model"] == model and c["design"] == "states" and c["rep"] == rep
                and c["step"] in booking.MODEL_STEPS]
    return [c for c in run["calls"] if c["model"] == model and c["design"] == design and c["rep"] == rep
            and c["step"] <= steps]


def summary(run):
    models = list(dict.fromkeys(c["model"] for c in run["calls"]))
    print(f"recorded run: {len(run['calls'])} calls to Claude, "
          f"anthropic {run['sdk']}")
    for model in models:
        reps = sorted({c["rep"] for c in run["calls"] if c["model"] == model})
        print(f"\n== {NAME[model]}, the seven scripted steps, repetition {reps[0]}")
        for design in ("free", "states", "fixed"):
            cs = calls_for(run, model, design, reps[0])
            print(f"  {DESIGN[design]:26} sent {sum(map(sent, cs)):6,}  cached-read {sum(c['cache_read'] for c in cs):6,}"
                  f"  input cost {sum(map(effective, cs)):8,.1f}  total {sum(map(total, cs)):8,.1f}")
        for rep in reps:
            free = calls_for(run, model, "free", rep)
            per = calls_for(run, model, "states", rep)
            closing = calls_for(run, model, "free", rep, steps=8)
            fs, ps = sum(map(sent, free)), sum(map(sent, per))
            fi, pi, ci = sum(map(effective, free)), sum(map(effective, per)), sum(map(effective, closing))
            ft, pt = sum(map(total, free)), sum(map(total, per))
            print(f"  rep {rep} per-state tools: {1 - ps / fs:.0%} fewer tokens sent; input cost {pi / fi - 1:+.0%} step for step,"
                  f" {pi / ci - 1:+.0%} counting the closing call; total {pt / ft - 1:+.1%}")
        print(f"\n== {NAME[model]}, the whole booking")
        for design, n in (("free", 8), ("code", 7)):
            for rep in reps:
                cs = calls_for(run, model, design, rep, steps=n)
                print(f"  {DESIGN[design]:31} rep {rep}: model calls {len(cs)}, model time {sum(c['seconds'] for c in cs):5.1f} s,"
                      f" sent {sum(map(sent, cs)):6,}, input cost {sum(map(effective, cs)):7,.1f}, output {sum(c['output'] for c in cs):5,},"
                      f" total {sum(map(total, cs)):7,.1f}")


def steps(run):
    print(f"{'model':16} {'design':8} rep step {'state':8} tools  input  write   read output      s  called")
    for c in run["calls"]:
        print(f"{NAME[c['model']]:16} {c['design']:8} {c['rep']:3} {c['step']:4} {c['state']:8} {c['ntools']:5} "
              f"{c['input']:6} {c['cache_write']:6} {c['cache_read']:6} {c['output']:6} {c['seconds']:6.2f}  "
              f"{','.join(c['tools_called']) or '(text)'}")


def table_report(full=False):
    t = booking.table()
    kinds = Counter(k for k, _, _ in t.values())
    holes = len(t) - len(booking.TRANSITIONS)
    print(f"\nstate machine table: {len(booking.STATES)} states x {len(booking.EVENTS)} events = {len(t)} cells, "
          f"{len(booking.TRANSITIONS)} drawn, {holes} needing a decision")
    print("  outcomes: " + ", ".join(f"{kinds[k]} {k}" for k in ("move", "stay", "defer", "reject")))
    reasons = Counter(w for (s, e), (_, _, w) in t.items() if (s, e) not in booking.TRANSITIONS)
    for why, n in reasons.most_common():
        print(f"  {n:3}  {why}")
    for e in ("user_revises", "slot_gone", "model_unavailable"):
        drawn = sum(1 for s in booking.STATES if (s, e) in booking.TRANSITIONS)
        print(f"  {e}: drawn in {drawn} of {len(booking.STATES)} states")
    if full:
        for (s, e), (k, n, why) in t.items():
            print(f"  {s:9} {e:18} {k:6} {n:9} {why}")


def behaviour_space():
    """Four-call sequences in which reserve_table comes before any check_availability, including never checking."""
    n = len(booking.TOOLS)
    count = 0
    for seq in itertools.product(list(booking.TOOLS), repeat=4):
        if "reserve_table" in seq and "check_availability" not in seq[: seq.index("reserve_table")]:
            count += 1
    print(f"\nfree-form: any of {n} tools at every step; of the {n ** 4:,} four-call sequences, {count:,} "
          f"({count / n ** 4:.0%}) reserve without having checked availability first (what the loop can express, "
          f"not what a model did)")


if __name__ == "__main__":
    run = load()
    if "--steps" in sys.argv:
        steps(run)
    elif "--table" in sys.argv:
        table_report(full=True)
    else:
        summary(run)
        table_report()
        behaviour_space()
