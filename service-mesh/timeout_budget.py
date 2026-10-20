"""Do a call chain's timeouts and retries fit inside each other? Arithmetic, for the worst case.

Each call in a chain has a per-try timeout and a number of retries. The worst case is the bottom service hanging:
it accepts the request and never answers. Then every try at the bottom runs to its timeout, the call above sees
a failure only after all of them, and so on up. A retry that cannot finish before its caller gives up is work
nobody is waiting for.

    python3 timeout_budget.py                 # the shop's chain, as the post shows it
    python3 timeout_budget.py chain.txt       # your own: one line per call, top first

A line is `caller -> callee  timeout  retries`, with the timeout in ms or s, or `none`. Between tries the caller
waits `--backoff` ms (25 by default, Istio's base interval).
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass

SHOP = """\
frontend -> checkout    none   2
checkout -> pricing     1s     2
pricing -> inventory    500ms  2
"""

FIXED = """\
frontend -> checkout    1.8s   0
checkout -> pricing     800ms  1
pricing -> inventory    225ms  2
"""


@dataclass
class Call:
    name: str
    timeout: float  # seconds; math.inf for none
    retries: int


def parse(text: str) -> list[Call]:
    calls = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^(.+?)\s+(none|[\d.]+\s*(?:ms|s)?)\s+(\d+)$", line)
        if not m:
            raise ValueError(f"cannot read {raw!r}: expected 'caller -> callee  timeout  retries'")
        name, t, r = m.groups()
        name = re.sub(r"\s*(->|→)\s*", " -> ", name.strip())
        if t == "none":
            timeout = math.inf
        elif t.endswith("ms"):
            timeout = float(t[:-2]) / 1000
        elif t.endswith("s"):
            timeout = float(t[:-1])
        else:
            timeout = float(t) / 1000
        calls.append(Call(name, timeout, int(r)))
    return calls


def worst_cases(calls: list[Call], backoff: float) -> list[float]:
    """Worst-case seconds each call takes when the bottom hangs, top first."""
    out = [0.0] * len(calls)
    below = math.inf  # the bottom service never answers
    for i in range(len(calls) - 1, -1, -1):
        c = calls[i]
        one_try = min(c.timeout, below)
        out[i] = (1 + c.retries) * one_try + c.retries * backoff
        below = out[i]
    return out


def fits(calls: list[Call], worst: list[float], backoff: float) -> list[int | None]:
    """For each call but the last, how many of its callee's tries finish inside one of its own tries."""
    out: list[int | None] = []
    for i in range(len(calls) - 1):
        outer, inner = calls[i], calls[i + 1]
        inner_try = min(inner.timeout, worst[i + 2] if i + 2 < len(calls) else math.inf)
        if math.isinf(outer.timeout):
            out.append(1 + inner.retries)
        elif math.isinf(inner_try):
            out.append(0)
        else:
            n = math.floor((outer.timeout + backoff) / (inner_try + backoff) + 1e-9)
            out.append(min(1 + inner.retries, n))
    out.append(None)
    return out


def top_fit(calls: list[Call], worst: list[float], backoff: float, deadline: float) -> int:
    """How many of the top call's tries finish inside the deadline of whatever calls the top."""
    one_try = min(calls[0].timeout, worst[1] if len(calls) > 1 else math.inf)
    if math.isinf(one_try):
        return 0
    return min(1 + calls[0].retries, math.floor((deadline + backoff) / (one_try + backoff) + 1e-9))


def fmt(s: float) -> str:
    if math.isinf(s):
        return "unbounded"
    # Half up, as JavaScript's toFixed does for these values, so the site's tool prints the same.
    return f"{math.floor(s * 1000 + 0.5):.0f} ms" if s < 1 else f"{math.floor(s * 100 + 0.5) / 100:.2f} s"


def report(calls: list[Call], backoff: float, deadline: float) -> None:
    worst = worst_cases(calls, backoff)
    fit = fits(calls, worst, backoff)
    print(f"  {'call':26s} {'per try':>8s} {'tries':>5s}  {'worst case':>10s}  callee's tries that fit in one of its tries")
    for c, w, f, nxt in zip(calls, worst, fit, calls[1:] + [None]):
        t = "none" if math.isinf(c.timeout) else fmt(c.timeout)
        fit_text = "" if f is None else f"{f} of {1 + nxt.retries}"
        print(f"  {c.name:26s} {t:>8s} {1 + c.retries:>5d}  {fmt(w):>10s}  {fit_text}")
    verdict = "fits" if worst[0] <= deadline else "does not fit"
    print(f"  the top call can take {fmt(worst[0])} when the bottom hangs; the user's app waits {fmt(deadline)}, so it {verdict}")
    lines = []
    tf = top_fit(calls, worst, backoff, deadline)
    if tf < 1 + calls[0].retries:
        lines.append((tf, 1 + calls[0].retries, "the app", calls[0].name.split(" -> ")[0], deadline, worst[0], " in all"))
    for c, f, n, wn in zip(calls, fit, calls[1:], worst[1:]):
        if f is not None and f < 1 + n.retries:
            caller, callee = c.name.split(" -> ")
            lines.append((f, 1 + n.retries, caller, callee, c.timeout, wn, " per try"))
    for f, tries, caller, callee, given, need, per in lines:
        done = f"none can finish, so all {tries}" if f == 0 else f"only {f} can finish, and the other {tries - f}"
        print(f"  {caller} gives {callee} {fmt(given)}{per}, but {callee}'s {tries} tries need {fmt(need)}: "
              f"{done} are work nobody waits for")
    for c in calls:
        if math.isinf(c.timeout):
            print(f"  {c.name}: no timeout, so a hung {c.name.split(' -> ')[1]} holds this call until something above gives up")
    attempts = 1
    for c in calls:
        attempts *= 1 + c.retries
    print(f"  if every layer retried every failure and the bottom failed fast, it would see {attempts} tries per user request")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?")
    ap.add_argument("--backoff", type=float, default=25.0, help="ms between tries")
    ap.add_argument("--deadline", type=float, default=2000.0, help="ms the user's app waits for one attempt")
    a = ap.parse_args(argv)
    backoff, deadline = a.backoff / 1000, a.deadline / 1000
    if a.file:
        report(parse(open(a.file).read()), backoff, deadline)
        return
    print("This assumes every layer retries a try that timed out. Istio's default retryOn does not: it needs gateway-error,")
    print("5xx or reset added, or retries in the code. With the defaults, a timed-out try is not retried at all.")
    print()
    print("The shop's checkout chain: 2 retries everywhere, no timeout at the top, two timeouts set by hand, all retried:")
    report(parse(SHOP), backoff, deadline)
    print()
    print("The same chain with timeouts that shrink going down, and retries only where they fit:")
    report(parse(FIXED), backoff, deadline)


if __name__ == "__main__":
    main(sys.argv[1:])
