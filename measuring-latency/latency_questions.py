"""Every latency number answers one question. Seeded, standard library only.

A pricing service on four hosts. Every request takes about 20 ms, and host 4 has a stall (think of a
garbage-collection pause) that adds 300 to 600 ms to 2% of its requests. We draw 100,000 requests and ask
each statistic the one question it can answer:

  the mean        how much work is in flight, so how much capacity we need (Little's law)
  a percentile    how slow one request is, at a given rank
  a share         what fraction of requests kept a promise ("under 100 ms")

and then what happens when we roll them up across hosts, and when a page waits for 20 calls at once.

    python3 latency_questions.py                 # the run the post quotes
    python3 latency_questions.py --busy 0.7      # put 70% of the traffic on the stalling host
    python3 latency_questions.py --csv fanout    # pages over the threshold by calls per page, for a chart
    python3 latency_questions.py --csv sweep     # averaged vs true p99 as traffic moves onto host 4

The random numbers come from mulberry32 and Box-Muller rather than Python's own generator, so the site's
lab, which runs the same model in JavaScript, draws the same requests and shows the same numbers.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

HOSTS = 4
REQUESTS = 100_000
STALL_HOST = 3  # host 4, counting from one as the output does
THRESHOLD_MS = 100.0
RATE_PER_S = 2000  # for Little's law; illustrative, like the whole service
PAGES = 20_000
SEED = 7


def mulberry32(seed: int):
    """A small 32-bit generator with a published reference implementation, easy to port exactly."""
    state = seed & 0xFFFFFFFF

    def imul(a: int, b: int) -> int:
        return (a * b) & 0xFFFFFFFF

    def next_float() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = state
        t = imul(t ^ (t >> 15), t | 1)
        t ^= (t + imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return next_float


@dataclass(frozen=True)
class Model:
    busy_share: float = 0.25  # share of traffic routed to the stalling host
    stall_rate: float = 0.02  # share of that host's requests that hit the stall
    seed: int = SEED


def draw(model: Model) -> list[list[float]]:
    """100,000 requests, routed to hosts, each with its latency in ms. Same order of draws as the lab."""
    rand = mulberry32(model.seed)
    hosts: list[list[float]] = [[] for _ in range(HOSTS)]
    if not 0 <= model.busy_share < 1:
        raise ValueError("busy_share must be at least 0 and below 1")
    other = (1 - model.busy_share) / (HOSTS - 1)
    for _ in range(REQUESTS):
        # Route: host 4 takes busy_share, the other three split the rest evenly.
        u = rand()
        host = STALL_HOST if u < model.busy_share else min(int((u - model.busy_share) / other), HOSTS - 2)
        # Body: lognormal around 20 ms, by Box-Muller from two uniforms (1 - u keeps log away from zero).
        z = math.sqrt(-2 * math.log(1 - rand())) * math.cos(2 * math.pi * rand())
        latency = 20 * math.exp(0.35 * z)
        stall = rand()
        extra = rand()
        if host == STALL_HOST and stall < model.stall_rate:
            latency += 300 + 300 * extra
        hosts[host].append(latency)
    return hosts


def percentile(values: list[float], p: float) -> float:
    """Nearest rank: the smallest measurement with at least p% of the measurements at or below it."""
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def fanout(pooled: list[float], calls: int, seed: int) -> list[float]:
    """Pages that make `calls` requests in parallel and wait for the slowest. Each call is a random request.
    Seeded per page size, so a page size gives the same pages whichever others are run beside it."""
    rand = mulberry32(seed * 1000 + calls)
    n = len(pooled)
    return [max(pooled[int(rand() * n)] for _ in range(calls)) for _ in range(PAGES)]


def report(model: Model) -> None:
    hosts = draw(model)
    pooled = [x for h in hosts for x in h]
    n = len(pooled)

    print(f"== {n:,} requests, {model.busy_share:.0%} of them on host {STALL_HOST + 1}, "
          f"which stalls {model.stall_rate:.0%} of its requests by 300 to 600 ms")
    for i, h in enumerate(hosts):
        print(f"host {i + 1}: {len(h):6,} requests  mean {mean(h):6.1f}  p50 {percentile(h, 50):5.1f}  "
              f"p99 {percentile(h, 99):6.1f}  under {THRESHOLD_MS:.0f} ms {sum(x < THRESHOLD_MS for x in h) / len(h):8.3%}")

    m = mean(pooled)
    p99 = percentile(pooled, 99)
    print("\n== the questions")
    print(f"mean {m:.1f} ms, p50 {percentile(pooled, 50):.1f}, p99 {p99:.1f}, p99.9 {percentile(pooled, 99.9):.1f}")
    print(f"Little's law: {RATE_PER_S} req/s x {m:.1f} ms = {RATE_PER_S * m / 1000:.1f} requests in flight on average")

    print("\n== rolling up four hosts")
    weighted = sum(mean(h) * len(h) for h in hosts) / n
    plain = mean([mean(h) for h in hosts])
    print(f"means, weighted by requests: {weighted:.1f} ms = pooled mean {m:.1f} ms; unweighted average of means {plain:.1f} ms")
    avg99 = mean([percentile(h, 99) for h in hosts])
    ratio = avg99 / p99
    direction = "too high" if ratio > 1 else "too low"
    print(f"average of host p99s {avg99:.1f} ms vs true p99 {p99:.1f} ms: {ratio:.2f}x, {direction}")
    good = [sum(x < THRESHOLD_MS for x in h) for h in hosts]
    print(f"under {THRESHOLD_MS:.0f} ms: counts {good} sum to {sum(good):,} of {n:,} = {sum(good) / n:.3%}; "
          f"pooled {sum(x < THRESHOLD_MS for x in pooled) / n:.3%}")

    print(f"\n== a page waits for its slowest call ({PAGES:,} pages each)")
    q = 1 - sum(good) / n
    for calls in (1, 5, 20, 50):
        pages = fanout(pooled, calls, model.seed)
        over = sum(x >= THRESHOLD_MS for x in pages) / PAGES
        print(f"{calls:2d} call{'s' if calls > 1 else ' '}: page p50 {percentile(pages, 50):5.1f} ms, page p99 {percentile(pages, 99):5.1f}, "
              f"pages over {THRESHOLD_MS:.0f} ms {over:5.1%} (1 - (1 - q)^N predicts {1 - (1 - q) ** calls:5.1%})")
    for calls in (5, 20, 50):
        print(f"{calls:2d} calls: a page's median is a request's p{100 * 0.5 ** (1 / calls):.1f}, "
              f"and a page's p99 is a request's p{100 * 0.99 ** (1 / calls):.2f}")


def csv(kind: str, model: Model) -> None:
    if kind == "fanout":
        hosts = draw(model)
        pooled = [x for h in hosts for x in h]
        q = sum(x >= THRESHOLD_MS for x in pooled) / len(pooled)
        print("calls,pages_over_pct,predicted_pct")
        for calls in (1, 2, 5, 10, 20, 30, 50, 100):
            pages = fanout(pooled, calls, model.seed)
            over = sum(x >= THRESHOLD_MS for x in pages) / PAGES
            print(f"{calls},{100 * over:.2f},{100 * (1 - (1 - q) ** calls):.2f}")
    elif kind == "sweep":
        print("busy_share,avg_host_p99,true_p99,share_under_pct")
        for step in range(1, 20):
            share = step * 0.05
            hosts = draw(Model(busy_share=share, stall_rate=model.stall_rate, seed=model.seed))
            pooled = [x for h in hosts for x in h]
            avg99 = mean([percentile(h, 99) for h in hosts])
            under = sum(x < THRESHOLD_MS for x in pooled) / len(pooled)
            print(f"{share:.2f},{avg99:.1f},{percentile(pooled, 99):.1f},{100 * under:.3f}")
    else:
        raise SystemExit(f"unknown --csv {kind!r}: fanout or sweep")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--busy", type=float, default=0.25, help="share of traffic on the stalling host (default 0.25)")
    ap.add_argument("--stall", type=float, default=0.02, help="share of its requests that stall (default 0.02)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--csv", choices=["fanout", "sweep"])
    args = ap.parse_args()
    model = Model(args.busy, args.stall, args.seed)
    if args.csv:
        csv(args.csv, model)
    else:
        report(model)


if __name__ == "__main__":
    main()
