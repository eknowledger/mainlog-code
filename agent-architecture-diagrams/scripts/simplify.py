"""Bounded whole-diagram proposals to prefer straight edges and fewer bends."""
import copy
import itertools
from geometry import bounds, expand, segment_rect
from ports import straight_plan
from quality import NODE_CLEARANCE


def simplification_candidates(seed):
    _, data, _, mode = seed
    cfg = data["config"]
    nodes = {n["id"]: n for n in data["nodes"] + data.get("outcomes", [])}
    bent = sorted((e for e in data["edges"] if len(e["points"]) > 2),
                  key=lambda e: (-len(e["points"]), e["id"]))
    # Whole-router alternatives sometimes simplify several connections together.
    for penalty in (120, 240):
        if cfg.get("bendPenalty", 35) != penalty:
            alt = copy.deepcopy(cfg)
            alt["bendPenalty"] = penalty
            yield alt, mode
    # Opposing ports can share an axis even when the boxes' centers do not.
    alignable = bent + [e for e in data["edges"] if len(e["points"]) == 2 and e["id"] in cfg.get("straightEdges", {})]
    for edge in alignable:
        a, b = nodes[edge["from"]], nodes[edge["to"]]
        for axis in ("horizontal", "vertical"):
            for fraction in (.5, .25, .75, 0, 1):
                plan = straight_plan(a, b, axis, fraction)
                if plan is None:
                    continue
                _, (p, q) = plan
                if any(segment_rect(p, q, expand(bounds(n), NODE_CLEARANCE - .05))
                       for nid, n in nodes.items() if nid not in (a["id"], b["id"])):
                    continue
                alt = copy.deepcopy(cfg)
                alt.setdefault("straightEdges", {})[edge["id"]] = dict(axis=axis, fraction=fraction)
                alt.get("portSides", {}).pop(edge["id"], None)
                yield alt, mode
    # A different attachment side can remove an initial or final dogleg.
    for edge in bent:
        for source, target in itertools.product(("N", "S", "W", "E"), repeat=2):
            alt = copy.deepcopy(cfg)
            alt.setdefault("portSides", {})[edge["id"]] = dict(source=source, target=target)
            alt.get("straightEdges", {}).pop(edge["id"], None)
            yield alt, mode
