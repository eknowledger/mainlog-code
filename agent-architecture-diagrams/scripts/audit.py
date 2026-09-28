"""Independent audit of the emitted SVG, not the layout or router's model.

Deliberately does not import geometry, layout, emitter, or their audit helpers.
Explicitly checks terminal attachments, all crossings (including repeated pairs),
stroke overlap, arrow polygons, text, containment, size, and declared semantics.
"""
import argparse
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from font_config import load_font
from quality import MIN_PARALLEL_GAP, PARALLEL_SHARED_SPAN, NODE_CLEARANCE


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def contact(a, b, c, d):
    den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    if abs(den) < 1e-8:
        return None
    t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den
    u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / den
    if -1e-7 <= t <= 1 + 1e-7 and -1e-7 <= u <= 1 + 1e-7:
        return (round(a[0] + t * (b[0] - a[0]), 3), round(a[1] + t * (b[1] - a[1]), 3))
    return None


def inside(p, r, strict=False):
    eps = 1e-5 if strict else -1e-5
    return r[0] + eps <= p[0] <= r[2] - eps and r[1] + eps <= p[1] <= r[3] - eps


def walls(r):
    x, y, w, h = r
    return [((x, y), (w, y)), ((w, y), (w, h)), ((w, h), (x, h)), ((x, h), (x, y))]


def hits_rect(a, b, r):
    return inside(a, r) or inside(b, r) or any(contact(a, b, c, d) is not None for c, d in walls(r))


def point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) if length else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def distance(a, b, c, d):
    if contact(a, b, c, d) is not None:
        return 0.0
    return min(point_segment(a, c, d), point_segment(b, c, d), point_segment(c, a, b), point_segment(d, a, b))


def seg_rect_dist(a, b, r):
    return 0 if hits_rect(a, b, r) else min(distance(a, b, c, d) for c, d in walls(r))


def overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > .01 and min(a[3], b[3]) - max(a[1], b[1]) > .01


def padded(r, p):
    return (r[0] - p, r[1] - p, r[2] + p, r[3] + p)


def points(element):
    return [tuple(map(float, pair.split(","))) for pair in element.attrib["points"].split()]


def polygon_hits_segment(poly, a, b, padding=0):
    cs = [cross(poly[i], poly[(i + 1) % len(poly)], a) for i in range(len(poly))]
    ds = [cross(poly[i], poly[(i + 1) % len(poly)], b) for i in range(len(poly))]
    contained = (all(x >= 0 for x in cs) or all(x <= 0 for x in cs) or
                 all(x >= 0 for x in ds) or all(x <= 0 for x in ds))
    return contained or any(distance(a, b, poly[i], poly[(i + 1) % len(poly)]) <= padding for i in range(len(poly)))


def audit(path, spec=None, max_crossings=3, min_edge_gap=MIN_PARALLEL_GAP):
    if not math.isfinite(min_edge_gap) or min_edge_gap <= 0:
        raise ValueError("Minimum parallel gap must be finite and positive")
    root = ET.parse(path).getroot()
    W, H = map(float, (root.get("width"), root.get("height")))
    ns = "{http://www.w3.org/2000/svg}"
    violations = []
    def fail(kind, *ids, **detail):
        violations.append(dict(kind=kind, elements=list(ids), **detail))
    if any("transform" in e.attrib for e in root.iter()):
        raise ValueError("Transforms are unsupported; refusing to report a false pass")
    rects = {}
    containers = {}
    parents = {}
    for e in root.findall(ns + "rect"):
        role = e.get("data-role")
        if role not in ("node", "outcome", "stage", "spine", "group"):
            continue
        x, y, w, h = (float(e.get(k)) for k in ("x", "y", "width", "height"))
        rid = e.get("id")
        if rid in rects or rid in containers:
            fail("duplicate-element", rid)
        (rects if role in ("node", "outcome") else containers)[rid] = (x, y, x + w, y + h)
        parents[rid] = e.get("data-parent")
    for i, (aid, a) in enumerate(rects.items()):
        for bid, b in list(rects.items())[i + 1:]:
            if overlap(a, b):
                fail("node-overlap", aid, bid)
    for nid, r in {**rects, **containers}.items():
        if r[0] < 0 or r[1] < 0 or r[2] > W or r[3] > H:
            fail("canvas-escape", nid)
        if parents[nid]:
            p = containers.get(parents[nid])
            if p is None or not (inside(r[:2], p) and inside(r[2:], p)):
                fail("containment", nid, parents[nid])
    edges = {}
    segs = []
    min_clearance = math.inf
    bends = []
    for e in root.findall(ns + "polyline"):
        if e.get("data-role") != "edge":
            continue
        eid = e.get("id")
        pts = points(e)
        if eid in edges:
            fail("duplicate-edge", eid)
        edges[eid] = dict(points=pts, source=e.get("data-from"), target=e.get("data-to"), stroke=float(e.get("stroke-width")))
        bends.append(len(pts) - 2)
        for end, nid in ((pts[0], e.get("data-from")), (pts[-1], e.get("data-to"))):
            if nid not in rects or min(point_segment(end, a, b) for a, b in walls(rects[nid])) > .1:
                fail("detached-endpoint", eid, nid)
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            if math.dist(a, b) < .01:
                fail("zero-segment", eid, segment=k)
            if abs(a[0] - b[0]) > .01 and abs(a[1] - b[1]) > .01:
                fail("diagonal-segment", eid, segment=k)
            if not inside(a, (1.5, 1.5, W - 1.5, H - 1.5)) or not inside(b, (1.5, 1.5, W - 1.5, H - 1.5)):
                fail("edge-canvas-escape", eid, segment=k)
            segs.append((eid, k, a, b, float(e.get("stroke-width"))))
            for nid, r in rects.items():
                terminal = (k == 0 and nid == e.get("data-from")) or (k == len(pts) - 2 and nid == e.get("data-to"))
                if hits_rect(a, b, padded(r, -.2)):
                    fail("edge-through-node", eid, nid, segment=k)
                if not terminal:
                    gap = seg_rect_dist(a, b, r)
                    min_clearance = min(min_clearance, gap)
                    if gap < NODE_CLEARANCE - .05:
                        fail("node-clearance", eid, nid, segment=k, gap=round(gap, 2))
            for cid, r in containers.items():
                for c, d in walls(r):
                    same_horizontal = abs(a[1] - b[1]) < .01 and c[1] == d[1]
                    same_vertical = abs(a[0] - b[0]) < .01 and c[0] == d[0]
                    if same_horizontal or same_vertical:
                        axis = 0 if same_horizontal else 1
                        common = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(min(a[axis], b[axis]), min(c[axis], d[axis]))
                        gap = abs(a[1 - axis] - c[1 - axis])
                        if common > 24 and gap < 12:
                            fail("edge-along-container", eid, cid, segment=k, gap=round(gap, 2))
    crossings = set()
    min_parallel = math.inf
    for i, (aid, ak, a, b, aw) in enumerate(segs):
        for bid, bk, c, d, bw in segs[i + 1:]:
            if aid == bid and abs(ak - bk) <= 1:
                # Adjacent segments may share only their single bend, not retrace.
                if cross(a, b, c) != 0 or cross(a, b, d) != 0:
                    continue
            h = abs(a[1] - b[1]) < .01 and abs(c[1] - d[1]) < .01
            v = abs(a[0] - b[0]) < .01 and abs(c[0] - d[0]) < .01
            if h or v:
                axis = 0 if h else 1
                common = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(min(a[axis], b[axis]), min(c[axis], d[axis]))
                gap = abs(a[1 - axis] - c[1 - axis])
                if common > .01:
                    if aid != bid:
                        min_parallel = min(min_parallel, gap)
                    if gap <= (aw + bw) / 2 + .5:
                        fail("overlapping-strokes", aid, bid, segments=[ak, bk], length=round(common, 2))
                    elif common > PARALLEL_SHARED_SPAN and gap < min_edge_gap:
                        fail("crowded-parallel", aid, bid, segments=[ak, bk],
                             gap=round(gap, 3), sharedSpan=round(common, 3), required=min_edge_gap)
                continue
            p = contact(a, b, c, d)
            if p is not None:
                if aid == bid:
                    fail("self-crossing", aid, point=p)
                else:
                    crossings.add((min(aid, bid), max(aid, bid), *p))
    if len(crossings) > max_crossings:
        fail("crossing-budget", count=len(crossings), limit=max_crossings)
    fonts = {}
    texts = []
    labels = {}
    for e in root.findall(ns + "text"):
        size = float(e.get("font-size"))
        bold = int(e.get("font-weight", "400")) >= 600
        key = (size, bold)
        if key not in fonts:
            fonts[key] = load_font(size, bold)
        anchor = {"middle": "ms", "start": "ls", "end": "rs"}[e.get("text-anchor", "start")]
        value = "".join(e.itertext())
        box = fonts[key].getbbox(value, anchor=anchor)
        x, y = float(e.get("x")), float(e.get("y"))
        r = (x + box[0] / 4, y + box[1] / 4, x + box[2] / 4, y + box[3] / 4)
        owner, role = e.get("data-owner"), e.get("data-role")
        texts.append((owner, role, r, value))
        if role == "edge-label":
            labels[owner] = value
        if role in ("node-label", "container-label"):
            box = (rects if role == "node-label" else containers).get(owner)
            if box is None or not (inside(r[:2], box) and inside(r[2:], box)):
                fail("text-overflow", owner, text=value)
        if not inside(r[:2], (0, 0, W, H)) or not inside(r[2:], (0, 0, W, H)):
            fail("text-canvas-escape", owner, text=value)
        for eid, k, a, b, sw in segs:
            if hits_rect(a, b, padded(r, sw / 2 + 1)):
                fail("edge-over-text", eid, owner, text=value)
        if role == "edge-label":
            for nid, box in rects.items():
                if overlap(padded(r, 2), box):
                    fail("label-over-node", owner, nid)
    for i, (aid, _, a, at) in enumerate(texts):
        for bid, _, b, bt in texts[i + 1:]:
            if overlap(a, b):
                fail("text-overlap", aid, bid, texts=[at, bt])
    polygons = [(e.get("data-owner"), points(e)) for e in root.findall(ns + "polygon") if e.get("data-role") == "arrow"]
    if {eid for eid, _ in polygons} != set(edges) or len(polygons) != len(edges):
        fail("missing-or-duplicate-arrow")
    for owner, poly in polygons:
        if owner not in edges:
            fail("unknown-arrow-owner", owner)
            continue
        if math.dist(poly[0], edges[owner]["points"][-1]) > .1:
            fail("detached-arrow", owner)
        if any(not inside(p, (0, 0, W, H)) for p in poly):
            fail("arrow-canvas-escape", owner)
        for eid, k, a, b, sw in segs:
            if eid != owner and polygon_hits_segment(poly, a, b, sw / 2 + .5):
                fail("arrow-over-edge", owner, eid, segment=k)
        for tid, _, r, value in texts:
            if any(inside(p, r) for p in poly) or any(polygon_hits_segment(poly, a, b) for a, b in walls(r)):
                fail("arrow-over-text", owner, tid, text=value)
        for nid, r in rects.items():
            if nid != edges[owner]["target"] and (any(inside(p, r) for p in poly) or any(polygon_hits_segment(poly, a, b) for a, b in walls(r))):
                fail("arrow-over-node", owner, nid)
    for i, (aid, a) in enumerate(polygons):
        for bid, b in polygons[i + 1:]:
            if any(polygon_hits_segment(a, b[k], b[(k + 1) % len(b)]) for k in range(len(b))):
                fail("arrow-over-arrow", aid, bid)
    # Opaque label backgrounds are also drawn pixels. They must not hide a
    # crossing, a border, or an arrow even when the glyph bounds are clear.
    for e in root.findall(ns + "rect"):
        if e.get("data-role") != "label-background":
            continue
        x, y, w, h = (float(e.get(k)) for k in ("x", "y", "width", "height"))
        r = (x, y, x + w, y + h)
        owner = e.get("data-owner")
        for eid, k, a, b, sw in segs:
            if hits_rect(a, b, padded(r, sw / 2)):
                fail("background-over-edge", owner, eid)
        for cid, box in containers.items():
            if any(hits_rect(a, b, r) for a, b in walls(box)):
                fail("background-over-border", owner, cid)
        for aid, poly in polygons:
            if any(inside(p, r) for p in poly) or any(polygon_hits_segment(poly, a, b) for a, b in walls(r)):
                fail("background-over-arrow", owner, aid)
    manifest_element = root.find(ns + "metadata")
    manifest = json.loads(manifest_element.text)
    if spec is not None:
        manifest = dict(nodes=[n["id"] for n in spec["nodes"] + spec.get("outcomes", [])],
                        edges=spec["edges"], stageOrder=[s["id"] for s in spec["stages"]], orders=spec.get("orders", []))
    if set(manifest["nodes"]) != set(rects):
        fail("semantic-nodes")
    if {e["id"] for e in manifest["edges"]} != set(edges):
        fail("semantic-edges")
    for e in manifest["edges"]:
        drawn = edges.get(e["id"], {})
        if drawn.get("source") != e["from"] or drawn.get("target") != e["to"]:
            fail("semantic-endpoints", e["id"])
        if e.get("label") and labels.get(e["id"]) != e["label"]:
            fail("missing-label", e["id"])
    for a, b in zip(manifest["stageOrder"], manifest["stageOrder"][1:]):
        if containers[a][3] >= containers[b][1]:
            fail("stage-order", a, b)
    for order in manifest.get("orders", []):
        for a, b in zip(order, order[1:]):
            if rects[a][0] >= rects[b][0]:
                fail("node-order", a, b)
    display = float((spec or {}).get("targetWidth") or 1200)
    eff = min(size for size, _ in fonts) * display / W
    height = H * display / W
    median = sorted(bends)[len(bends) // 2] if bends else 0
    if eff < 9.5:
        fail("font-size", effective=eff)
    if height > 900:
        fail("height", at1200=height)
    if median > 3 or max(bends, default=0) > 5:
        fail("bend-budget", median=median, maximum=max(bends, default=0))
    return dict(passed=not violations,
                thresholds=dict(maxCrossings=max_crossings, minParallelGap=min_edge_gap, parallelSharedSpan=PARALLEL_SHARED_SPAN,
                                parallelSharedSpanComparison=">", distanceUnits="source SVG pixels, centerline"),
                nodes=len(rects), edges=len(edges), crossingCount=len(crossings),
                crossings=sorted(crossings), minNodeClearance=round(min_clearance, 2) if math.isfinite(min_clearance) else None,
                minParallelGap=round(min_parallel, 2) if math.isfinite(min_parallel) else None,
                fontAt1200=round(eff, 2), heightAt1200=round(height, 1), medianBends=median,
                maxBends=max(bends, default=0), totalBends=sum(bends), straightEdges=bends.count(0),
                violations=violations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("svg", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--crossings", type=int, default=3)
    parser.add_argument("--min-edge-gap", type=float, default=MIN_PARALLEL_GAP)
    args = parser.parse_args()
    result = audit(args.svg, json.loads(args.spec.read_text()) if args.spec else None,
                   args.crossings, args.min_edge_gap)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
