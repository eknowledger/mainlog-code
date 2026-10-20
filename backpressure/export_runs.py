"""Exports the runs the post's overload-policy lab replays.

    python3 export_runs.py > runs.json

The lab does not simulate anything in the browser: it shows these recorded runs, so what a reader sees is
exactly what shed_sim.py printed. One run per combination of the lab's four controls, keyed
"policy/retries/spike/timeout", each with its 10 s timeline and the sale's summary.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace

from shed_sim import POLICIES, Config, simulate, summarise

RETRIES = (0, 1, 3)
SPIKES = (120, 150, 200)
TIMEOUTS = (1, 2, 4)


def main() -> None:
    runs = {}
    for policy in POLICIES:
        for retries in RETRIES:
            for spike in SPIKES:
                for timeout in TIMEOUTS:
                    cfg = replace(Config(), policy=policy, retries=retries, spike=float(spike), timeout=float(timeout))
                    r = simulate(cfg)
                    s = summarise(r)
                    # Stuck rather than slow: a queue that can hold people past their timeout, after the sale, gets
                    # more tries a second than it can serve. Tries that fit in a person's patience, not all of them.
                    tries = min(1 + retries, math.floor((cfg.give_up - 1e-9) / (cfg.timeout + cfg.backoff)) + 1)
                    capacity = cfg.workers / (cfg.service_ms / 1000)
                    stuck = s.recovered is None and policy in ("fifo", "fifo_skip") and cfg.base * tries >= capacity
                    runs[f"{policy}/{retries}/{spike}/{timeout}"] = {
                        "w": [round(v) for v in r.windows],
                        "ok": round(s.answered, 1),
                        "no": round(s.refused, 1),
                        "to": round(s.timed_out + s.late, 1),
                        "p50": round(s.p50_ms) if s.p50_ms == s.p50_ms else None,
                        "p99": round(s.p99_ms) if s.p99_ms == s.p99_ms else None,
                        "co": round(s.checkout_answered, 1),
                        "tries": round(s.attempts_per_user, 2),
                        "wasted": s.wasted,
                        "rec": None if s.recovered is None else round(s.recovered),
                        "stuck": stuck,
                    }
    out = {
        "about": "Recorded by mainlog-code/backpressure/export_runs.py from shed_sim.py; do not edit by hand.",
        "policies": POLICIES,
        "retries": RETRIES,
        "spikes": SPIKES,
        "timeouts": TIMEOUTS,
        "window": 10,
        "sale": [Config().spike_start, Config().spike_end],
        "runs": runs,
    }
    json.dump(out, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
