"""Content-sized semantic lanes with bounded, graph-informed placement choices.

The input contains no coordinates or per-edge bend instructions. Stage order and
declared ordering constraints are hard; other positions can respond to routing.
"""
import copy
from collections import defaultdict
from typography import width, TITLE_SIZE, BODY_SIZE
from quality import MIN_PARALLEL_GAP, NODE_CLEARANCE
from ports import straight_plan, packed_positions

SLOT_GAP = 44

def validate(spec):
    elements = sum((spec.get(k, []) for k in ("stages", "spines", "groups", "nodes", "outcomes")), [])
    ids = [n["id"] for n in elements]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate element IDs")
    by_id = {n["id"]: n for n in elements}
    containers = {n["id"] for key in ("stages", "spines", "groups") for n in spec.get(key, [])}
    endpoints = {n["id"] for key in ("nodes", "outcomes") for n in spec.get(key, [])}
    edge_ids = [e["id"] for e in spec["edges"]]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("Duplicate edge IDs")
    for e in spec["edges"]:
        if e["from"] not in endpoints or e["to"] not in endpoints:
            raise ValueError(f"Unknown endpoint in {e['id']}")
    for n in spec.get("nodes", []) + spec.get("groups", []):
        parents = [n[k] for k in ("parent", "group", "spine", "stage") if n.get(k)]
        if len(parents) != 1 or parents[0] not in containers:
            raise ValueError(f"{n['id']} needs exactly one existing container")
        seen = {n["id"]}
        cur = parents[0]
        while cur:
            if cur in seen:
                raise ValueError("Containment cycle")
            seen.add(cur)
            obj = by_id[cur]
            cur = next((obj[k] for k in ("parent", "group", "spine", "stage") if obj.get(k)), None)
    stage_ids = {s["id"] for s in spec["stages"]}
    for n in spec.get("outcomes", []):
        if n["after"] not in stage_ids:
            raise ValueError(f"Unknown stage for outcome {n['id']}")
    for order in spec.get("orders", []):
        if any(i not in by_id for i in order):
            raise ValueError("Ordering constraint contains unknown ID")


def parent(n):
    return next((n[k] for k in ("parent", "group", "spine", "stage") if n.get(k)), None)


def layout(spec, config=None):
    validate(spec)
    cfg = dict(width=1280, gap=44, outer=54, grow=1.35, mode="align", orders={}, groupPad=16, spineGap=44,
               columnGap=2 * NODE_CLEARANCE + MIN_PARALLEL_GAP)
    cfg.update(config or {})
    data = copy.deepcopy(spec)
    leaves = data["nodes"]
    groups = data.get("groups", [])
    stages = data["stages"]
    spines = data.get("spines", [])
    outcomes = data.get("outcomes", [])
    all_elements = leaves + groups + stages + spines + outcomes
    by_id = {n["id"]: n for n in all_elements}
    children = defaultdict(list)
    for n in leaves + groups:
        children[parent(n)].append(n)
    def leaf_ids(n):
        kids = children[n["id"]]
        return sum((leaf_ids(k) for k in kids), []) if kids else [n["id"]]
    # Ordering constraints can refer to leaves inside nested groups. Keep each
    # group intact while ordering its sibling blocks consistently with those leaves.
    for order in data.get("orders", []):
        rank = {nid: i for i, nid in enumerate(order)}
        for kids in list(children.values()):
            if sum(any(nid in rank for nid in leaf_ids(k)) for k in kids) >= 2:
                kids.sort(key=lambda k: min([rank[nid] for nid in leaf_ids(k) if nid in rank] or [999]))
    stage_rank = {s["id"]: i for i, s in enumerate(stages)}
    for sp in spines:
        children[sp["id"]].sort(key=lambda n: stage_rank.get(n.get("atStage"), 999))
    for container, order in cfg["orders"].items():
        rank = {nid: i for i, nid in enumerate(order)}
        children[container].sort(key=lambda n: rank.get(n["id"], 999))
    for n in leaves:
        n.update(role="node", w=max(150, width(n["title"], TITLE_SIZE, True) + 28,
                                   max([width(s, BODY_SIZE) + 28 for s in n.get("lines", [])] or [0])),
                 h=38 + 16 * len(n.get("lines", [])))
        n["flowOffset"] = n["h"] / 2
    for n in outcomes:
        n.update(role="outcome", title=n["label"], lines=[], w=width(n["label"], TITLE_SIZE, True) + 32, h=38)

    def measure_group(g, default_direction="row"):
        direction = g.get("direction", default_direction)
        pad = cfg["groupPad"]
        top, bottom = pad + 18, pad + 6
        kids = children[g["id"]]
        for n in kids:
            if n in groups:
                measure_group(n, direction)
        if direction == "row":
            w = sum(n["w"] for n in kids) + max(0, len(kids) - 1) * SLOT_GAP
            above = max([n["flowOffset"] for n in kids] or [0])
            below = max([n["h"] - n["flowOffset"] for n in kids] or [0])
            h = above + below
        else:
            w = max([n["w"] for n in kids] or [0])
            # Two opposite-direction wires need clearance on both nodes plus
            # the enforced wire separation. A 44px slot leaves only 12px.
            h = sum(n["h"] for n in kids) + max(0, len(kids) - 1) * cfg["columnGap"]
        g.update(role="group", direction=direction, padding=pad, headerInset=top,
                 w=max(w + pad * 2, width(g["label"], 12, True) + pad * 2), h=h + top + bottom)
        g["flowOffset"] = top + above if direction == "row" else g["h"] / 2

    for g in groups:
        if parent(g) not in {x["id"] for x in groups}:
            measure_group(g, "column" if parent(g) in {s["id"] for s in spines} else "row")
    sx = cfg["outer"]
    for sp in spines:
        kids = children[sp["id"]]
        sp.update(role="spine", x=sx, w=max([n["w"] for n in kids] + [width(sp["label"], 12, True)]) + 36)
        sx += sp["w"] + cfg["spineGap"]
    content_x = sx
    W = max(cfg["width"], content_x + cfg["outer"] + max(
        [sum(n["w"] for n in children[s["id"]]) + max(0, len(children[s["id"]]) - 1) * SLOT_GAP + 40 for s in stages] or [0]))
    lane_width = W - content_x - cfg["outer"]
    y = 106
    rows = []
    # A spine ends at a stage boundary, so that stage must reserve enough height
    # for its anchored control nodes too, even when its own row has shorter boxes.
    spine_end_heights = defaultdict(float)
    for sp in spines:
        end_stage = sp.get("toStage", stages[-1]["id"])
        for n in children[sp["id"]]:
            if n.get("atStage") == end_stage:
                spine_end_heights[end_stage] = max(spine_end_heights[end_stage], n["h"])

    def place_tree(n, x, y):
        n.update(x=x, y=y)
        if n.get("role") != "group":
            return
        px, py = x + n["padding"], y + n["headerInset"]
        for kid in children[n["id"]]:
            place_tree(kid, px, y + n["flowOffset"] - kid["flowOffset"] if n["direction"] == "row" else py)
            if n["direction"] == "row":
                px += kid["w"] + SLOT_GAP
            else:
                py += kid["h"] + cfg["columnGap"]

    def move_tree(n, dx):
        n["x"] += dx
        for kid in children[n["id"]]:
            move_tree(kid, dx)

    def descendants(n):
        if n.get("role") != "group":
            return [n]
        return sum((descendants(k) for k in children[n["id"]]), [])

    for si, stage in enumerate(stages):
        kids = children[stage["id"]]
        tot = sum(n["w"] for n in kids)
        avail = lane_width - 40 - max(0, len(kids) - 1) * SLOT_GAP
        grow = min(cfg["grow"], avail / tot) if tot else 1
        for n in kids:
            if n["role"] == "node":
                n["w"] *= max(1, grow)
        row_height = max([n["h"] for n in kids] + [spine_end_heights[stage["id"]]])
        stage.update(role="stage", x=content_x, y=y, w=lane_width, h=row_height + 64, rank=si)
        slack = lane_width - 40 - sum(n["w"] for n in kids)
        if cfg["mode"] == "pack":
            gap = SLOT_GAP
            x = content_x + 20 + max(0, (slack - gap * max(0, len(kids) - 1)) / 2)
        else:
            gap = slack / (len(kids) - 1) if len(kids) > 1 else 0
            x = content_x + 20 + (slack / 2 if len(kids) == 1 else 0)
        for n in kids:
            place_tree(n, x, y + 38 + (row_height - n["h"]) / 2)
            for leaf in descendants(n):
                leaf["rank"] = si
            x += n["w"] + gap
        rows.append((stage, kids))
        y += stage["h"]
        after = [o for o in outcomes if o["after"] == stage["id"]]
        if after:
            required = sum(o["w"] for o in after) + max(0, len(after) - 1) * 48
            ox = content_x + (lane_width - required) / 2
            y += 42
            for oc in after:
                oc.update(x=ox, y=y, rank=si + .5)
                ox += oc["w"] + 48
            y += 38
        if si < len(stages) - 1:
            y += cfg["gap"]
    H = y + 30
    for sp in spines:
        start = next((s for s in stages if s["id"] == sp.get("fromStage")), stages[0])
        end = next((s for s in stages if s["id"] == sp.get("toStage")), stages[-1])
        sp.update(y=start["y"], h=end["y"] + end["h"] - start["y"])
        kids = children[sp["id"]]
        needed = sum(n["h"] for n in kids) + max(0, len(kids) - 1) * 40
        cy = sp["y"] + 40 + (0 if any(n.get("atStage") for n in kids) else max(0, (sp["h"] - 64 - needed) / 2))
        for n in kids:
            anchor = next((s for s in stages if s["id"] == n.get("atStage")), None)
            ny = max(cy, anchor["y"] + 38) if anchor else cy
            place_tree(n, sp["x"] + (sp["w"] - n["w"]) / 2, ny)
            for leaf in descendants(n):
                leaf["rank"] = anchor["rank"] if anchor else (ny - 106) / max(1, H - 136) * (len(stages) - 1)
            cy = ny + n["h"] + 40
        if cy - 40 > sp["y"] + sp["h"] - 16:
            raise ValueError(f"Spine {sp['id']} cannot contain its children at this height")
    if cfg["mode"] == "align":
        # Barycentric relaxation followed by projection onto row-order constraints.
        # Positions, not graph meaning, are the feedback search's degrees of freedom.
        neighbors = defaultdict(list)
        for e in data["edges"]:
            weight = 2 if e["kind"] in ("command", "data") else 1
            neighbors[e["from"]].append((e["to"], weight))
            neighbors[e["to"]].append((e["from"], weight))
        for sweep in range(4):
            for stage, kids in (rows if sweep % 2 == 0 else reversed(rows)):
                if len(kids) == 0:
                    continue
                desired = []
                for n in kids:
                    center = n["x"] + n["w"] / 2
                    peers = [(by_id[nid]["x"] + by_id[nid]["w"] / 2, w)
                             for leaf in descendants(n) for nid, w in neighbors[leaf["id"]]
                             if by_id[nid].get("rank") != stage["rank"]]
                    target = (center * 2 + sum(x * w for x, w in peers)) / (2 + sum(w for _, w in peers))
                    desired.append(target - n["w"] / 2)
                low = stage["x"] + 20
                for i, n in enumerate(kids):
                    desired[i] = max(low, desired[i])
                    low = desired[i] + n["w"] + SLOT_GAP
                high = stage["x"] + stage["w"] - 20
                for i in range(len(kids) - 1, -1, -1):
                    n = kids[i]
                    desired[i] = min(desired[i], high - n["w"])
                    high = desired[i] - SLOT_GAP
                for n, x in zip(kids, desired):
                    move_tree(n, x - n["x"])
    # Declarative semantic ordering is never traded for a shorter route.
    for order in data.get("orders", []):
        if any(by_id[a]["x"] >= by_id[b]["x"] for a, b in zip(order, order[1:])):
            raise ValueError("Candidate violates declared order")
    data.update(W=round(W, 3), H=round(H, 3), config=cfg)
    return data


def routing_input(data, port_mode="flow"):
    nodes = data["nodes"] + data.get("outcomes", [])
    by_id = {n["id"]: n for n in nodes}
    requests = []
    sides = defaultdict(list)
    for e in data["edges"]:
        a, b = by_id[e["from"]], by_id[e["to"]]
        alignment = data["config"].get("straightEdges", {}).get(e["id"])
        aligned = straight_plan(a, b, **alignment) if alignment else None
        if alignment and aligned is None:
            raise ValueError(f"No facing overlap for straight edge {e['id']}")
        ac, bc = a["x"] + a["w"] / 2, b["x"] + b["w"] / 2
        same_row = abs(a["y"] + a["h"] / 2 - b["y"] - b["h"] / 2) < 20
        same_stage = abs(a["rank"] - b["rank"]) < .01 and abs(ac - bc) > 40
        if same_row or same_stage:
            pair = ("E", "W") if ac < bc else ("W", "E")
        elif b["rank"] >= a["rank"]:
            pair = ("S", "N")
        else:
            left_cost, right_cost = ac + bc, 2 * data["W"] - ac - bc
            pair = ("W", "W") if left_cost <= right_cost else ("E", "E")
        if a["id"] == b["id"]:
            pair = ("E", "S")
        request = {"id": e["id"]}
        for index, (end, n, other, preferred) in enumerate(zip(("source", "target"), (a, b), (b, a), pair)):
            local_chain = (same_row or same_stage) and parent(a) == parent(b)
            forward = b["rank"] > a["rank"]
            modes = [preferred] if port_mode == "flow" or local_chain or (port_mode == "vertical" and forward) else ["N", "S", "W", "E"]
            selected_side = data["config"].get("portSides", {}).get(e["id"], {}).get(end)
            if selected_side:
                if selected_side not in ("N", "S", "W", "E"):
                    raise ValueError(f"Invalid connection side for {e['id']} {end}")
                modes = [selected_side]
            if aligned:
                modes = [aligned[0][index]]
            req = {"node": n["id"], "sites": [], "preferred": preferred, "mandatory": len(modes) == 1}
            if aligned:
                px, py = aligned[1][index]
                req["alignedPosition"] = ((px - n["x"]) / n["w"], (py - n["y"]) / n["h"])
            request[end] = req
            for side in modes:
                other_coord = other["x"] + other["w"] / 2 if side in ("N", "S") else other["y"] + other["h"] / 2
                sides[n["id"], side].append((other_coord, e["id"], end, req))
        requests.append(request)
    for (nid, side), endpoints in sides.items():
        n = by_id[nid]
        endpoints.sort(key=lambda p: p[:3])
        length = n["w"] if side in ("N", "S") else n["h"]
        gap = min(24, (length - 24) / max(1, len(endpoints) - 1))
        start = (length - gap * (len(endpoints) - 1)) / 2
        positions = {i: start + i * gap for i in range(len(endpoints))}
        if any("alignedPosition" in req for _, _, _, req in endpoints):
            coordinate_axis = 0 if side in ("N", "S") else 1
            positions = packed_positions(length,
                [req["alignedPosition"][coordinate_axis] * length if "alignedPosition" in req else None
                 for _, _, _, req in endpoints],
                [req["mandatory"] for _, _, _, req in endpoints])
        for i, (_, _, _, request) in enumerate(endpoints):
            if i not in positions:
                continue
            f = positions[i] / length
            x, y = {"N": (f, 0), "S": (f, 1), "W": (0, f), "E": (1, f)}[side]
            if "alignedPosition" in request:
                x, y = request["alignedPosition"]
            request["sites"].append(dict(side=side, x=x, y=y, cost=0 if side == request["preferred"] else 120))
    if any(not e[end]["sites"] for e in requests for end in ("source", "target")):
        raise ValueError("No available port side for a connection")
    obstacles = []
    for c in data["stages"] + data.get("spines", []) + data.get("groups", []):
        # Reserve only the actual title, not the entire header band. Wires may
        # cross the unused portion of a lane border perpendicularly.
        obstacles.append(dict(id="title:" + c["id"], x=c["x"] + 8, y=c["y"] + 7,
                              w=min(c["w"] - 16, width(c["label"].upper(), 12, True) + 8), h=15))
    return dict(nodes=nodes, edges=requests, obstacles=obstacles,
                options=dict(clearance=NODE_CLEARANCE, edgeGap=MIN_PARALLEL_GAP,
                             crossingPenalty=1000, segmentPenalty=data["config"].get("bendPenalty", 35)))
