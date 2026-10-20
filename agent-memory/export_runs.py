"""Write memory-runs.json, every run the post's lab can show, in the shape the lab reads.

The lab draws these runs rather than re-implementing the store in the browser, so what a reader sees is sim.py's own
output for each setting. One run per policy, and per time setting for the three time-based policies. CI checks the
file is current; copy it to the site's src/labs/ after changing sim.py.
"""
import json

from memory_demo.sim import POLICIES, RARE, SESSIONS, memories, run, sessions

KEYS = ["ana_diet", "next_dinner", "deposit_preapproved", "favourite"]
TTL = [3, 7, 14, 30]           # for ttl-refresh and ttl-fixed
DISUSE = [7, 30, 60, 90]       # for disuse-30; 90 is longer than the run, so nothing is ever deleted
CODE = {"ok": "o", "STALE": "s", "MISSING": "m", "INJECTED": "i"}
SNAPSHOT = 81

SETTINGS = {"ttl-refresh": ("ttl", TTL), "ttl-fixed": ("ttl", TTL), "disuse-30": ("disuse", DISUSE)}


def one(policy, **settings):
    c, _ = run(policy, **settings)
    by_day = sessions(policy, **settings)
    # One string per key, one letter per session; the favourite is read only on RARE days, "-" elsewhere.
    strip = {k: "".join(CODE[by_day[d][k]] if k in by_day[d] else "-" for d in SESSIONS) for k in KEYS}
    return {"reads": c["reads"], "stale": c["stale"], "missing": c["missing"], "injected": c["injected"],
            "strip": strip, "memories": memories(policy, SNAPSHOT, **settings)}


def export():
    runs = {}
    for policy in POLICIES:
        name, values = SETTINGS.get(policy, (None, [None]))
        for v in values:
            runs[f"{policy}:{v}" if name else policy] = one(policy, **({name: v} if name else {}))
    return {"sessions": SESSIONS, "rare": sorted(RARE), "snapshot": SNAPSHOT, "keys": KEYS, "ttl": TTL,
            "disuse": DISUSE, "runs": runs}


if __name__ == "__main__":
    out = export()
    with open("memory-runs.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print("memory-runs.json:", len(out["runs"]), "runs")
