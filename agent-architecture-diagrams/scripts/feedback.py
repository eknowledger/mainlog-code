"""Fit container boundaries into clear space after routes reveal channel traffic.

The router's polylines remain untouched. Content containment, ordered lanes,
readable titles and a visible gap between boundaries remain hard constraints.
"""
from geometry import segment_rect, expand
from typography import text_box
from layout import parent


def fit_boundaries(data):
    segs = [(a, b) for e in data["edges"] for a, b in zip(e["points"], e["points"][1:])]
    stages = data["stages"]
    elements = data["nodes"] + data.get("groups", [])
    children = {s["id"]: [n for n in elements if parent(n) == s["id"]] for s in stages}
    data["H"] = max(data["H"], max(p[1] for e in data["edges"] for p in e["points"]) + 30)

    def clear_y(stage, y, title=False):
        for a, b in segs:
            if abs(a[1] - b[1]) < .01:
                common = min(max(a[0], b[0]), stage["x"] + stage["w"]) - max(min(a[0], b[0]), stage["x"])
                if common > 24 and abs(a[1] - y) < 14:
                    return False
        if title:
            r = expand(text_box(stage["label"].upper(), stage["x"] + 12, y + 21, 12, True, "start"), 4)
            if any(segment_rect(a, b, r) for a, b in segs):
                return False
        return True

    changes = []
    # Stage side walls are shared visual alignment lines. Move all of them
    # together when an outside return path occupies the previous boundary.
    stage_children = [n for kids in children.values() for n in kids]
    def clear_x(x):
        for stage in stages:
            for a, b in segs:
                if abs(a[0] - b[0]) < .01:
                    common = min(max(a[1], b[1]), stage["y"] + stage["h"]) - max(min(a[1], b[1]), stage["y"])
                    if common > 24 and abs(a[0] - x) < 14:
                        return False
        return True
    if stage_children:
        old_left, old_right = stages[0]["x"], stages[0]["x"] + stages[0]["w"]
        min_left = max([s["x"] + s["w"] + 18 for s in data.get("spines", [])] or [18])
        max_left = min(n["x"] for n in stage_children) - 14
        min_right = max(n["x"] + n["w"] for n in stage_children) + 14
        lefts = [x for x in range(int(min_left), int(max_left) + 1, 2) if clear_x(x)]
        rights = [x for x in range(int(min_right), int(data["W"] - 16), 2) if clear_x(x)]
        new_left = min(lefts, key=lambda x: (abs(x - old_left), x)) if lefts else old_left
        new_right = min(rights, key=lambda x: (abs(x - old_right), x)) if rights else old_right
        for stage in stages:
            stage["x"], stage["w"] = new_left, new_right - new_left
    if stages and children[stages[0]["id"]]:
        first = stages[0]
        max_top = min(n["y"] for n in children[first["id"]]) - 30
        tops = [y for y in range(102, int(max_top) + 1, 2) if clear_y(first, y, True)]
        if tops:
            old_bottom = first["y"] + first["h"]
            top = min(tops, key=lambda y: (abs(y - first["y"]), y))
            first["y"], first["h"] = top, old_bottom - top
    # Each between-stage interval is independent: choose both facing boundaries
    # together rather than moving one into the other.
    for upper, lower in zip(stages, stages[1:]):
        if not children[upper["id"]] or not children[lower["id"]]:
            continue
        hi_content = max(n["y"] + n["h"] for n in children[upper["id"]])
        lo_content = min(n["y"] for n in children[lower["id"]])
        old_bottom, old_top = upper["y"] + upper["h"], lower["y"]
        after = [o for o in data.get("outcomes", []) if o["after"] == upper["id"]]
        max_bottom = min([o["y"] - 18 for o in after] or [lo_content - 48])
        min_top = max([o["y"] + o["h"] + 18 for o in after] or [hi_content + 30])
        bottoms = [y for y in range(int(hi_content + 14), int(max_bottom) + 1, 2) if clear_y(upper, y)]
        tops = [y for y in range(int(min_top), int(lo_content - 30) + 1, 2) if clear_y(lower, y, True)]
        valid = [(abs(b - old_bottom) + abs(t - old_top), b, t) for b in bottoms for t in tops if t - b >= 18]
        if not valid:
            continue
        _, b, t = min(valid)
        old_lower_bottom = lower["y"] + lower["h"]
        upper["h"] = b - upper["y"]
        lower["y"], lower["h"] = t, old_lower_bottom - t
        if abs(b - old_bottom) + abs(t - old_top) > .01:
            changes.append(dict(upper=upper["id"], lower=lower["id"], bottom=b, top=t))
    if stages:
        last = stages[-1]
        content_bottom = max(n["y"] + n["h"] for n in children[last["id"]])
        old = last["y"] + last["h"]
        candidates = [y for y in range(int(content_bottom + 14), int(data["H"] - 12), 2) if clear_y(last, y)]
        if candidates:
            bottom = min(candidates, key=lambda y: (abs(y - old), y))
            last["h"] = bottom - last["y"]
    # Preserve each spine's declared coverage after stage boundaries change.
    for sp in data.get("spines", []):
        first = next((s for s in stages if s["id"] == sp.get("fromStage")), stages[0])
        last = next((s for s in stages if s["id"] == sp.get("toStage")), stages[-1])
        sp["y"], sp["h"] = first["y"], last["y"] + last["h"] - first["y"]
    # The same boundary rule applies to groups and to the sides of a spine.
    # Work from the outside in so a child can use newly available parent space.
    all_containers = {c["id"]: c for c in stages + data.get("spines", []) + data.get("groups", [])}
    todo = data.get("spines", []) + data.get("groups", [])
    def depth(c):
        pid = parent(c)
        return 1 + depth(all_containers[pid]) if pid else 0
    for c in sorted(todo, key=depth):
        kids = [n for n in elements if parent(n) == c["id"]]
        if not kids:
            continue
        p = all_containers.get(parent(c))
        x0 = p["x"] + 12 if p else 18
        x1 = p["x"] + p["w"] - 12 if p else min(s["x"] for s in stages) - 18
        child_left, child_right = min(n["x"] for n in kids), max(n["x"] + n["w"] for n in kids)
        def clear_side(x):
            return not any(abs(a[0] - b[0]) < .01 and abs(a[0] - x) < 14 and
                           min(max(a[1], b[1]), c["y"] + c["h"]) - max(min(a[1], b[1]), c["y"]) > 24 for a, b in segs)
        lefts = [x for x in range(int(x0), int(child_left - 12) + 1, 2) if clear_side(x)]
        rights = [x for x in range(int(child_right + 12), int(x1) + 1, 2) if clear_side(x)]
        old_left, old_right = c["x"], c["x"] + c["w"]
        left = min(lefts, key=lambda x: (abs(x - old_left), x)) if lefts else old_left
        right = min(rights, key=lambda x: (abs(x - old_right), x)) if rights else old_right
        c["x"], c["w"] = left, right - left
        if p:
            child_top = min(n["y"] for n in kids)
            tops = [y for y in range(int(p["y"] + 30), int(child_top - 30) + 1, 2) if clear_y(c, y, True)]
            if tops:
                old_bottom = c["y"] + c["h"]
                old_top = c["y"]
                top = min(tops, key=lambda y: (abs(y - old_top), y))
                c["y"], c["h"] = top, old_bottom - top
                c["flowOffset"] += old_top - top
            child_bottom = max(n["y"] + n["h"] for n in kids)
            bottoms = [y for y in range(int(child_bottom + 12), int(p["y"] + p["h"] - 12) + 1, 2) if clear_y(c, y)]
            if bottoms:
                old = c["y"] + c["h"]
                c["h"] = min(bottoms, key=lambda y: (abs(y - old), y)) - c["y"]
    data["boundaryFeedback"] = changes
    return data
