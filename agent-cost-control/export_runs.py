"""Write breaker-runs.json: every combination of the five checks, under each of the three incidents, for the lab.

32 combinations x 3 incidents = 96 seeded runs, about six seconds. The lab replays these, so the page runs no
simulation, and tests/test_sim.py checks that the lab's three headline cells are sim.py's own rows.
"""
import itertools
import json
import statistics

import sim

BIN = 20  # seconds of start time per column of the lab's chart

out = {"checks": list(sim.CHECKS), "incidents": list(sim.INCIDENTS), "bin": BIN, "window": list(sim.INCIDENT),
       "runs": {}}
for incident in sim.INCIDENTS:
    for bits in itertools.product((0, 1), repeat=len(sim.CHECKS)):
        checks = tuple(c for c, b in zip(sim.CHECKS, bits) if b)
        s = sim.simulate(checks, incident, keep_logs=True)
        bins = []
        for b in range(0, sim.TASKS, BIN):
            ts = s["tasks"][b:b + BIN]
            d = [t.now - t.start for t in ts]
            bins.append([round(statistics.median(d)), round(max(d)), sum(t.end == "handed" for t in ts),
                         sum(t.end == "failed" for t in ts)])
        key = f"{incident}:{''.join(map(str, bits))}"
        out["runs"][key] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items() if k != "tasks"}
        out["runs"][key]["bins"] = bins
with open("breaker-runs.json", "w") as f:
    json.dump(out, f, separators=(",", ":"))
    f.write("\n")
print(len(out["runs"]), "runs")
