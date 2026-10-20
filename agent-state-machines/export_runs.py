"""Write sm-runs.json, the recorded live run in the shape the post's lab reads.

The lab replays these numbers rather than calling a model, so what the reader sees is this run's real usage. Re-run
after live.py, and copy sm-runs.json to the site's src/labs/.
"""
import json

import booking
from demo import NAME, READ, WRITE, calls_for, load

run = load()
out = {"write": WRITE, "models": []}
for model in dict.fromkeys(c["model"] for c in run["calls"]):
    m = {"id": model, "name": NAME[model], "read": READ[model], "designs": {}}
    for design in ("free", "states", "fixed", "code"):
        cs = calls_for(run, model, design, 1, steps=8 if design == "free" else 7)
        m["designs"][design] = [[c["step"], c["state"], c["ntools"], c["input"], c["cache_write"], c["cache_read"],
                                 c["output"], c["seconds"]] for c in cs]
    out["models"].append(m)
out["columns"] = ["step", "state", "tools", "input", "cache_write", "cache_read", "output", "seconds"]
with open("sm-runs.json", "w") as f:
    json.dump(out, f, separators=(",", ":"))
    f.write("\n")
print("sm-runs.json:", sum(len(d) for m in out["models"] for d in m["designs"].values()), "rows")
