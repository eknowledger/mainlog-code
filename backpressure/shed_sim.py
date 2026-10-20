"""What each overload policy does to the people waiting. A model, not a measurement.

A shop's checkout service has 8 workers and each request takes 80 ms on average, so it can answer about
100 requests a second. It normally sees 70. At 60 s a sale opens and 150 a second arrive for a minute.
One request in five places an order; the rest are the price previews the checkout page asks for while a
customer edits their cart. The client gives each attempt 2 seconds,
and the person gives up altogether 10 seconds after they first tried.

Run it with no arguments for the post's tables. Standard library only, and seeded, so every machine and
every Python version prints the same numbers. The random generator is mulberry32 rather than Python's own,
so the site's Overload and queue tool, which runs the same model in TypeScript, prints them too.

    python3 shed_sim.py                       # the tables the post quotes
    python3 shed_sim.py --timeline            # answered per 10 s window, every policy, for the chart
    python3 shed_sim.py --csv out.csv         # the same, one row per window
    python3 shed_sim.py --policy fifo --retries 3 --spike 150   # one run, with your own numbers
"""

from __future__ import annotations

import argparse
import csv
import heapq
import math
import sys
from collections import deque
from dataclasses import dataclass, field, replace

M32 = 0xFFFFFFFF

# The names the post uses, in the order its tables list them.
POLICIES = {
    "fifo": "unbounded queue (FIFO)",
    "fifo_skip": "skip the abandoned",
    "bounded": "bounded queue, reject when full",
    "priority": "bounded, orders first",
    "codel": "queue timeout (CoDel-style)",
    "lifo": "newest first (adaptive LIFO + CoDel)",
}


def imul(a: int, b: int) -> int:
    """JavaScript's Math.imul: the low 32 bits of the product. The sign does not change those bits."""
    return (a * b) & M32


class Mulberry32:
    """The same generator the site's tools use, so a TypeScript port draws the same numbers in the same order."""

    def __init__(self, seed: int) -> None:
        self.state = seed & M32

    def random(self) -> float:
        self.state = (self.state + 0x6D2B79F5) & M32
        t = self.state
        t = imul(t ^ (t >> 15), t | 1)
        t ^= (t + imul(t ^ (t >> 7), t | 61)) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296

    def exponential(self, mean: float) -> float:
        # 1 - u because u can be exactly 0, and log(0) is not a number.
        return -math.log(1 - self.random()) * mean


@dataclass(frozen=True)
class Config:
    workers: int = 8
    service_ms: float = 80.0  # mean; exponential
    base: float = 70.0  # requests a second, before and after the sale
    spike: float = 150.0  # during it
    spike_start: float = 60.0
    spike_end: float = 120.0
    end: float = 300.0
    checkout_share: float = 0.2
    timeout: float = 2.0  # how long the client waits for one attempt
    give_up: float = 10.0  # how long the person waits in all, from their first try
    retries: int = 0  # attempts after the first, per person
    backoff: float = 1.0  # mean seconds before a retry, spread +-50%
    policy: str = "fifo"
    queue_bound: int = 50  # for bounded and priority
    codel_target_ms: float = 5.0  # Fail at Scale's M
    codel_interval_ms: float = 100.0  # Fail at Scale's N
    reject_cost_ms: float = 0.0  # worker time spent before a bounded queue can say no
    seed: int = 1


@dataclass
class Attempt:
    user: int
    checkout: bool
    t0: float  # when this attempt was sent
    tries: int  # retries before this one
    gave_up: bool = False
    finished: bool = False
    refused: bool = False
    limit: float = 0.0  # the queue timeout, for codel and lifo: set as the request arrives


@dataclass
class User:
    first: float
    checkout: bool
    outcome: str = ""  # answered, late, refused, timed_out
    latency: float = math.nan  # first try to the answer, for the answered
    attempts: int = 0


@dataclass
class Result:
    config: Config
    users: list[User]
    wasted: int  # requests a worker finished for someone who had already given up
    useful: int
    attempts: int
    windows: list[float] = field(default_factory=list)  # answered share per 10 s, by first try


def simulate(cfg: Config) -> Result:
    if cfg.policy not in POLICIES:
        raise ValueError(f"unknown policy {cfg.policy!r}; choose from {', '.join(POLICIES)}")
    arrivals = Mulberry32(cfg.seed)
    service = Mulberry32(cfg.seed + 1)
    backoffs = Mulberry32(cfg.seed + 2)
    mean_service = cfg.service_ms / 1000
    target = cfg.codel_target_ms / 1000
    interval = cfg.codel_interval_ms / 1000

    events: list[tuple[float, int, str, Attempt]] = []
    seq = 0

    def push(t: float, kind: str, a: Attempt) -> None:
        nonlocal seq
        seq += 1
        heapq.heappush(events, (t, seq, kind, a))

    users: list[User] = []
    t = 0.0
    while True:
        rate = cfg.spike if cfg.spike_start <= t < cfg.spike_end else cfg.base
        t += arrivals.exponential(1 / rate)
        if t >= cfg.end:
            break
        checkout = arrivals.random() < cfg.checkout_share
        users.append(User(first=t, checkout=checkout))
        push(t, "arrive", Attempt(len(users) - 1, checkout, t, 0))

    queue: deque[Attempt] = deque()
    rejects: deque[Attempt] = deque()  # only when saying no costs worker time
    busy = 0
    last_empty = 0.0
    wasted = useful = attempts = 0

    def settle(a: Attempt, outcome: str, now: float) -> None:
        """This attempt has failed: retry if the person still has time and retries, else record it."""
        u = users[a.user]
        if a.tries < cfg.retries:
            at = now + cfg.backoff * (0.5 + backoffs.random())
            if at < u.first + cfg.give_up:
                push(at, "arrive", Attempt(a.user, a.checkout, at, a.tries + 1))
                return
        u.outcome = outcome

    def refuse(a: Attempt, now: float) -> None:
        a.refused = True
        settle(a, "refused", now)

    def start(now: float) -> None:
        nonlocal busy, last_empty
        while busy < cfg.workers and (rejects or queue):
            if rejects:
                a = rejects.popleft()
                busy += 1
                push(now + cfg.reject_cost_ms / 1000, "rejected", a)
                continue
            congested = now - last_empty > interval
            a = queue.pop() if cfg.policy == "lifo" and congested else queue.popleft()
            if cfg.policy in ("codel", "lifo"):
                # The limit set when it arrived, enforced as a worker picks it up.
                if now - a.t0 > a.limit:
                    if not a.gave_up:
                        refuse(a, now)
                    continue
            if cfg.policy == "fifo_skip" and a.gave_up:
                continue
            busy += 1
            push(now + service.exponential(mean_service), "done", a)
        if not queue:
            last_empty = now

    def full_reject(a: Attempt, now: float) -> None:
        if cfg.reject_cost_ms > 0:
            rejects.append(a)
        else:
            refuse(a, now)

    while events:
        now, _, kind, a = heapq.heappop(events)
        if kind == "arrive":
            attempts += 1
            users[a.user].attempts += 1
            push(a.t0 + cfg.timeout, "timeout", a)
            bounded = cfg.policy in ("bounded", "priority")
            if bounded and len(queue) >= cfg.queue_bound:
                if cfg.policy == "priority" and a.checkout:
                    # Make room by turning away the newest price preview still queued.
                    for i in range(len(queue) - 1, -1, -1):
                        if not queue[i].checkout:
                            victim = queue[i]
                            del queue[i]
                            full_reject(victim, now)
                            break
                    else:
                        full_reject(a, now)
                        start(now)
                        continue
                else:
                    full_reject(a, now)
                    start(now)
                    continue
            if cfg.policy in ("codel", "lifo"):
                # Fail at Scale's onNewRequest: if the queue has not been empty for the last N, this request
                # may wait M; otherwise it may wait N. Decided now, from the queue as this request finds it.
                if not queue:
                    last_empty = now
                a.limit = target if now - last_empty > interval else interval
            queue.append(a)
            start(now)
        elif kind == "timeout":
            if a.finished or a.refused:
                continue
            a.gave_up = True
            settle(a, "timed_out", now)
        elif kind == "rejected":
            busy -= 1
            if not a.gave_up:
                refuse(a, now)
            start(now)
        else:  # done
            busy -= 1
            if a.gave_up:
                wasted += 1
            else:
                a.finished = True
                useful += 1
                u = users[a.user]
                if now - u.first <= cfg.give_up:
                    u.outcome, u.latency = "answered", now - u.first
                else:
                    u.outcome = "late"
            start(now)

    result = Result(cfg, users, wasted, useful, attempts)
    result.windows = windows(result, 10)
    return result


def windows(r: Result, step: float) -> list[float]:
    out = []
    a = 0.0
    while a < r.config.end - 1e-9:
        group = [u for u in r.users if a <= u.first < a + step]
        out.append(100 * sum(u.outcome == "answered" for u in group) / max(1, len(group)))
        a += step
    return out


def share(users: list[User], outcome: str) -> float:
    return 100 * sum(u.outcome == outcome for u in users) / max(1, len(users))


def pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return math.nan
    return sorted_values[int(q * (len(sorted_values) - 1))]


def recovered_after(r: Result, threshold: float = 90.0) -> float | None:
    """Seconds from the end of the sale to the first 10 s window from which every later one answers >= 90%."""
    starts = [i * 10.0 for i in range(len(r.windows))]
    for i, s in enumerate(starts):
        if s < r.config.spike_end:
            continue
        if all(w >= threshold for w in r.windows[i:]):
            return s - r.config.spike_end
    return None


@dataclass
class Summary:
    answered: float
    refused: float
    timed_out: float
    late: float
    p50_ms: float
    p99_ms: float
    checkout_answered: float
    attempts_per_user: float
    wasted: int
    useful: int
    recovered: float | None
    after_answered: float


def summarise(r: Result) -> Summary:
    c = r.config
    spike = [u for u in r.users if c.spike_start <= u.first < c.spike_end]
    after = [u for u in r.users if c.spike_end + 30 <= u.first < c.end]
    lat = sorted(u.latency for u in spike if u.outcome == "answered")
    return Summary(
        answered=share(spike, "answered"),
        refused=share(spike, "refused"),
        timed_out=share(spike, "timed_out"),
        late=share(spike, "late"),
        p50_ms=pct(lat, 0.5) * 1000,
        p99_ms=pct(lat, 0.99) * 1000,
        checkout_answered=share([u for u in spike if u.checkout], "answered"),
        attempts_per_user=sum(u.attempts for u in spike) / max(1, len(spike)),
        wasted=r.wasted,
        useful=r.useful,
        recovered=recovered_after(r),
        after_answered=share(after, "answered"),
    )


def recovery_text(s: Summary) -> str:
    return "not by the end" if s.recovered is None else f"{s.recovered:.0f} s"


def table(cfg: Config, title: str) -> None:
    print(title)
    print(
        f"  {'policy':38s} {'answered':>8s} {'fast no':>8s} {'timed out':>9s}  {'p50':>7s} {'p99':>7s}"
        f"  {'orders':>8s} {'tries':>5s} {'wasted':>7s}  recovered"
    )
    for name, label in POLICIES.items():
        s = summarise(simulate(replace(cfg, policy=name)))
        print(
            f"  {label:38s} {s.answered:7.1f}% {s.refused:7.1f}% {s.timed_out:8.1f}%"
            f"  {s.p50_ms:5.0f}ms {s.p99_ms:5.0f}ms  {s.checkout_answered:7.1f}% {s.attempts_per_user:5.2f}"
            f" {s.wasted:7d}  {recovery_text(s)}"
        )
    print()


def one_line(cfg: Config) -> str:
    s = summarise(simulate(cfg))
    return (
        f"{cfg.policy}, retries={cfg.retries}, spike {cfg.spike:g}/s, timeout {cfg.timeout:g} s: "
        f"answered {s.answered:.1f}%, fast no {s.refused:.1f}%, timed out {s.timed_out:.1f}%, "
        f"late {s.late:.1f}%; p50 {s.p50_ms:.0f} ms, p99 {s.p99_ms:.0f} ms; orders {s.checkout_answered:.1f}%; "
        f"{s.attempts_per_user:.2f} tries each; {s.wasted} served to nobody; "
        f"recovered: {recovery_text(s)}; answered from 150 s on: {s.after_answered:.1f}%"
    )


def report() -> None:
    base = Config()
    print(
        f"The checkout service: {base.workers} workers x {base.service_ms:g} ms = "
        f"{base.workers / (base.service_ms / 1000):g} requests a second at most. "
        f"Normally {base.base:g}/s; {base.spike:g}/s from {base.spike_start:g} s to {base.spike_end:g} s; "
        f"{base.end:g} s in all; {base.checkout_share:.0%} of requests place an order."
    )
    print(
        f"Each attempt waits {base.timeout:g} s; a person gives up {base.give_up:g} s after first trying. "
        "'Answered' means a successful response within those 10 s. Shares are of the people who first "
        "arrived during the sale."
    )
    print()
    table(base, "The sale, no retries:")
    table(replace(base, retries=3), "The sale, 3 retries after a timeout or a no, 1 s apart (+-50%):")

    print("Answered per 10 s window, by when people first tried (the sale is windows 6 to 11):")
    for name in ("fifo", "bounded", "codel"):
        for retries in (0, 1, 3):
            w = simulate(replace(base, policy=name, retries=retries)).windows
            print(f"  {name:8s} retries={retries}  " + " ".join(f"{v:3.0f}" for v in w))
    print()

    print("Normal load, no sale, no retries: how much each policy turns away when nothing is wrong")
    for name in ("fifo", "bounded", "codel", "lifo"):
        s = summarise(simulate(replace(base, policy=name, spike=base.base)))
        print(f"  {POLICIES[name]:38s} answered {s.answered:5.1f}%, fast no {s.refused:4.1f}%")
    print()

    print("Where the no is said: worker time a bounded queue spends before it can refuse")
    for cost in (0, 20, 80):
        s = summarise(simulate(replace(base, policy="bounded", reject_cost_ms=cost)))
        print(
            f"  refusing costs {cost:2d} ms: answered {s.answered:5.1f}%, fast no {s.refused:5.1f}%, "
            f"timed out {s.timed_out:5.1f}%, p50 {s.p50_ms:4.0f} ms"
        )
    print()

    print("A queue bound only sheds if it is shorter than the timeout: bound / 100 per second against 2 s")
    for bound, retries in ((50, 2), (150, 2), (300, 2), (1024, 2), (1024, 0)):
        print("  bound " + str(bound).rjust(4) + ": " + one_line(replace(base, policy="bounded", queue_bound=bound, retries=retries)))
    print("  none:       " + one_line(replace(base, policy="fifo", retries=2)))
    print()

    print("The fluid view: after the sale, an unbounded queue drains only if every person's tries fit")
    for retries in (0, 1, 2, 3):
        offered = base.base * (1 + retries)
        cap = base.workers / (base.service_ms / 1000)
        verdict = "drains" if offered < cap else "never drains"
        print(f"  retries={retries}: {base.base:g}/s x {1 + retries} tries = {offered:g}/s against {cap:g}/s, {verdict}")


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeline", action="store_true", help="answered per 10 s window, every policy")
    p.add_argument("--csv", metavar="FILE", help="write the timeline for every policy, 0 and 3 retries")
    p.add_argument("--policy", choices=list(POLICIES))
    for name in ("spike", "base", "timeout", "give_up", "backoff", "service_ms", "codel_target_ms", "codel_interval_ms", "reject_cost_ms"):
        p.add_argument("--" + name.replace("_", "-"), type=float)
    for name in ("retries", "workers", "queue_bound", "seed"):
        p.add_argument("--" + name.replace("_", "-"), type=int)
    args = p.parse_args(argv)
    overrides = {
        k: v for k, v in vars(args).items() if v is not None and k not in ("timeline", "csv", "policy")
    }
    cfg = replace(Config(), **overrides)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["window_start_s", "policy", "retries", "answered_pct"])
            for name in POLICIES:
                for retries in (0, 3):
                    r = simulate(replace(cfg, policy=name, retries=retries))
                    for i, v in enumerate(r.windows):
                        w.writerow([i * 10, name, retries, f"{v:.1f}"])
        print(f"wrote {args.csv}")
        return
    if args.timeline:
        for name in POLICIES:
            for retries in (0, 1, 3):
                w = simulate(replace(cfg, policy=name, retries=retries)).windows
                print(f"{name:9s} retries={retries}  " + " ".join(f"{v:3.0f}" for v in w))
        return
    if args.policy:
        print(one_line(replace(cfg, policy=args.policy)))
        return
    report()


if __name__ == "__main__":
    main(sys.argv[1:])
