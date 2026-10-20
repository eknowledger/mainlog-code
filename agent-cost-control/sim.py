"""Five bad minutes at a shop's support chat: which checks change what customers see. A seeded simulation.

Every rate here is invented to show a mechanism; none is a measurement of any provider. The mechanisms are real:
a status-code breaker can only trip on what returns an error, and the failures that cost the most time return 200.

    python sim.py              # the three policies the post compares, and each check on its own
    python sim.py --seeds      # the same three policies over seeds 1 to 5
    python sim.py --task 350   # one conversation's calls, under each policy

The chat: 1,000 conversations start one a second. Each needs four good model calls (read the question, look up the
order, look up the delivery, write the answer) within ten turns. From 300 s to 600 s the primary model's provider is
having a bad five minutes. A smaller fallback model is always up, and worse at tool calls.

Time is simulated with an event queue, so a breaker opened by one conversation at 340 s is seen by every other
conversation's next call after 340 s, and by none before it.
"""
from __future__ import annotations

import heapq
import random
import sys
from dataclasses import dataclass, field

TASKS, ARRIVAL = 1000, 1.0
INCIDENT = (300.0, 600.0)
STEPS_NEEDED, MAX_TURNS = 4, 10          # 10 is openai-agents' DEFAULT_MAX_TURNS
SDK_RETRIES = 2                          # the anthropic and openai SDKs' DEFAULT_MAX_RETRIES
BACKOFF = [0.5, 1.0]                     # their waits before retries 1 and 2, before jitter
OUT = 300                                # tokens in a full answer
TTFT = 0.8                               # seconds to the first token
TPS = {"primary": 60, "fallback": 90}    # tokens a second when healthy
SLOW_TPS = 2                             # tokens a second in a slow stream: every gap is well under any read timeout
STICKY = 0.9                             # once a model repeats a call, how likely it repeats it again

# The checks. Each is one line of the policy; the lab switches them independently.
CHECKS = ("breaker", "stream", "valid", "loop", "task")
ABORT_AT, MIN_TPS = 3.0, 15              # stream check: under 15 tokens/s at 3 s, abort
BREAKER_FAILS, BREAKER_OPEN = 5, 30.0    # breaker: open after 5 failures in a row, for 30 s, then one trial call
DEADLINE, TOKEN_CAP = 60.0, 40_000       # task check: a conversation gets 60 s and 40k tokens


@dataclass(frozen=True)
class Rates:
    overloaded: float                    # fast 529, retried by the SDK, nothing billed
    slow: float                          # 200, but the tokens trickle
    malformed: float                     # 200, full answer billed, the tool call doesn't parse
    loop: float                          # 200, valid, repeats the last tool call: no progress


HEALTHY = Rates(0.01, 0.01, 0.02, 0.02)
INCIDENTS = {                            # what the primary does during the bad five minutes
    "both": Rates(0.40, 0.20, 0.06, 0.02),
    "overloaded": Rates(0.60, 0.05, 0.04, 0.02),
    "slow": Rates(0.10, 0.40, 0.06, 0.02),
}
FALLBACK = Rates(0.01, 0.01, 0.08, 0.04)


class Breaker:
    """Closed, open, half-open. Counts the failures it is shown; which failures it is shown is the policy."""

    def __init__(self):
        self.state, self.fails, self.until, self.trips, self.trial = "closed", 0, 0.0, 0, False

    def route(self, now):
        if self.state == "open" and now >= self.until:
            self.state, self.trial = "half-open", False
        if self.state == "open" or (self.state == "half-open" and self.trial):
            return "fallback"
        if self.state == "half-open":
            self.trial = True            # this call is the one trial
        return "primary"

    def record(self, now, failed):
        if self.state == "half-open" and self.trial:
            if failed:
                self.state, self.until, self.trips = "open", now + BREAKER_OPEN, self.trips + 1
            else:
                self.state, self.fails = "closed", 0
            self.trial = False
            return
        self.fails = self.fails + 1 if failed else 0
        if self.state == "closed" and self.fails >= BREAKER_FAILS:
            self.state, self.until, self.fails, self.trips = "open", now + BREAKER_OPEN, 0, self.trips + 1


@dataclass
class Task:
    id: int
    start: float
    now: float
    progress: int = 0
    turns: int = 0
    attempt: int = 0
    tokens: int = 0
    last_loop: bool = False
    invalid: int = 0
    end: str | None = None               # answered, handed (to a person, and the customer told), failed
    why: str = ""
    log: list = field(default_factory=list)


def rates(model, t, incident):
    if model == "fallback":
        return FALLBACK
    return INCIDENTS[incident] if INCIDENT[0] <= t < INCIDENT[1] else HEALTHY


def one_call(rng, model, t, task, incident):
    """(outcome, seconds, tokens billed) for one attempt. Input grows with every turn the conversation has used."""
    inp = 2000 + 600 * (task.progress + task.turns)
    if task.last_loop and rng.random() < STICKY:
        return "loop", TTFT + OUT / TPS[model], inp + OUT
    r, x = rates(model, t, incident), rng.random()
    if x < r.overloaded:
        return "overloaded", 0.2, 0
    x -= r.overloaded
    if x < r.slow:
        return "slow", TTFT + OUT / SLOW_TPS, inp + OUT
    x -= r.slow
    if x < r.malformed:
        return "malformed", TTFT + OUT / TPS[model], inp + OUT
    x -= r.malformed
    if x < r.loop:
        return "loop", TTFT + OUT / TPS[model], inp + OUT
    return "ok", TTFT + OUT / TPS[model], inp + OUT


def advance(rng, task, checks, breaker, incident, fallback_next):
    """Run the task's next attempt at task.now. Returns the model the attempt after this one must use, if forced."""
    on = checks.__contains__
    if on("task") and (task.now - task.start >= DEADLINE or task.tokens >= TOKEN_CAP):
        task.end, task.why = "handed", "task budget"
        return None
    model = fallback_next or (breaker.route(task.now) if on("breaker") else "primary")
    outcome, secs, billed = one_call(rng, model, task.now, task, incident)
    if outcome == "slow" and on("stream"):
        # The first token came at 0.8 s and by 3 s only a handful followed: abort, keep what was billed. Whether a
        # provider bills tokens generated before an abort is not documented; this assumes it does.
        outcome, secs = "aborted", ABORT_AT
        billed = 2000 + 600 * (task.progress + task.turns) + int((ABORT_AT - TTFT) * SLOW_TPS)
    task.now += secs
    task.tokens += billed
    task.log.append((round(task.now, 1), model, outcome))
    if on("breaker") and model == "primary":
        seen = {"overloaded"} | ({"aborted"} if on("stream") else set()) | ({"malformed"} if on("valid") else set())
        breaker.record(task.now, outcome in seen)

    if outcome in ("overloaded", "aborted"):
        if task.attempt < SDK_RETRIES:
            task.now += BACKOFF[task.attempt] * (1 - 0.25 * rng.random())
            task.attempt += 1
            return "fallback" if outcome == "aborted" else None   # a slow primary is retried elsewhere
        task.end, task.why = ("handed" if on("task") else "failed"), f"{outcome} after {SDK_RETRIES} retries"
        return None
    task.attempt = 0
    task.turns += 1
    if outcome in ("ok", "slow"):
        # A slow stream that is allowed to finish is a good answer, only late.
        task.progress, task.last_loop = task.progress + 1, False
        if task.progress == STEPS_NEEDED:
            task.end = "answered"
            return None
    elif outcome == "loop":
        # A loop outcome already repeats the previous call, so a second loop in a row is the third identical call.
        if on("loop") and task.last_loop:
            task.end, task.why = "handed", "the same call three times in a row"
            return None
        task.last_loop = True
    elif outcome == "malformed":
        task.invalid += 1
        if on("valid") and task.invalid >= 2:
            task.end, task.why = "handed", "two answers that don't parse"
            return None
    if task.turns >= MAX_TURNS:
        task.end, task.why = ("handed" if on("task") else "failed"), "turn limit"
    return None


def simulate(checks=(), incident="both", seed=7, keep_logs=False):
    rng = random.Random(seed)
    breaker = Breaker()
    tasks = [Task(i, i * ARRIVAL, i * ARRIVAL) for i in range(TASKS)]
    forced = {}
    queue = [(t.now, t.id) for t in tasks]
    heapq.heapify(queue)
    while queue:
        _, i = heapq.heappop(queue)
        t = tasks[i]
        forced[i] = advance(rng, t, set(checks), breaker, incident, forced.get(i))
        if t.end is None:
            heapq.heappush(queue, (t.now, i))
    secs = sorted(t.now - t.start for t in tasks)
    pct = lambda q: secs[int(q * (len(secs) - 1))]  # noqa: E731
    count = lambda e: sum(t.end == e for t in tasks)  # noqa: E731
    return {
        "answered": count("answered"), "handed": count("handed"), "failed": count("failed"),
        "p50": pct(0.5), "p99": pct(0.99), "max": secs[-1], "tokens": sum(t.tokens for t in tasks),
        "over2m": sum(x > 120 for x in secs),
        "trips": breaker.trips, "tasks": tasks if keep_logs else None,
    }


POLICIES = {"defaults": (), "breaker": ("breaker",), "all checks": CHECKS}


def row(name, s):
    return (f"{name:12} {s['answered']:5} {s['handed']:6} {s['failed']:6} {s['over2m']:5} {s['p50']:7.1f} {s['p99']:7.1f} "
            f"{s['max']:7.1f} {s['tokens'] / 1e6:7.2f} {s['trips']:5}")


HEAD = f"{'policy':12} {'answer':>5} {'handed':>6} {'failed':>6} {'>2min':>5} {'p50 s':>7} {'p99 s':>7} {'max s':>7} {'Mtok':>7} {'trips':>5}"

if __name__ == "__main__":
    print(f"{TASKS} conversations, one a second; the primary is bad from {INCIDENT[0]:.0f} s to {INCIDENT[1]:.0f} s "
          f"(simulation, invented rates, {'seeds 1 to 5' if '--seeds' in sys.argv else 'seed 7'})")
    if "--seeds" in sys.argv:
        print(f"{'seed':4} {HEAD}")
        for seed in range(1, 6):
            for name, checks in POLICIES.items():
                print(f"{seed:4} {row(name, simulate(checks, seed=seed))}")
    elif "--task" in sys.argv:
        n = int(sys.argv[sys.argv.index("--task") + 1])
        for name, checks in POLICIES.items():
            t = simulate(checks, keep_logs=True)["tasks"][n]
            print(f"\n{name}: conversation {n} started at {t.start:.0f} s, ended {t.end} after {t.now - t.start:.1f} s"
                  f"{' (' + t.why + ')' if t.why else ''}, {t.tokens:,} tokens")
            for at, model, outcome in t.log:
                print(f"  {at - t.start:6.1f} s  {model:8} {outcome}")
    else:
        for incident in INCIDENTS:
            print(f"\nincident: {incident} {INCIDENTS[incident]}")
            print(HEAD)
            for name, checks in POLICIES.items():
                print(row(name, simulate(checks, incident)))
            for c in CHECKS[1:]:
                print(row(f"only {c}", simulate((c,), incident)))
