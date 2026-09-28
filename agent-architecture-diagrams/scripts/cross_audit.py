#!/usr/bin/env python3
"""cross_audit.py: an ENGINE-INDEPENDENT auditor for layered architecture SVGs.

Purpose
-------
This is a separate implementation of SVG geometry checks. It imports no drawing
engine code, so it can expose mistakes shared by an engine and its own auditor.
If the reports disagree, inspect the drawing and the findings before deciding
which check needs correction.

Checks and the failure modes they cover
--------------------------------------
C1  non-orthogonal segments      A checker tested only axis-aligned segments and
                                 returned False for diagonals, so diagonals were
                                 structurally invisible to every other check.
C2  node interior crossings       A checker tested node rects but silently skipped
                                 any node the edge attached to *anywhere*, so an
                                 edge could run along that node's far side.
C3  edge->node clearance         Geometry can be legal and still look broken: an
                                 edge passing 9px from a box reads as touching it.
C4  parallel-run separation       Distinct edges can share a long span, including
                                 exactly the same coordinates. Zero gap must be
                                 treated as overlap, never skipped as if it were
                                 a comparison of an edge with itself.
C5  alongside a lane border       Tests container borders as well as node borders.
                                 Note a segment CROSSING a border perpendicular is
                                 correct and must not be flagged, so this measures
                                 parallel-and-alongside only.
C6  arrowhead burial              This parser does not resolve SVG <marker>
                                 geometry. For explicit <polygon> heads it tests
                                 bounding-box burial in nodes. It does NOT check
                                 arrow-wire collisions, attachment, or ownership.
C7  label collisions              Labels were checked against boxes and other
                                 labels but never against edge paths. A label
                                 sitting on a line reads as a break in the line.
C8  edge-pair crossings           Counts distinct pairs, not intersection locations;
                                 repeated crossings by one pair count only once.
C9  bends; C9b straight edges     Reports median/maximum bends and straightness.
                                 It does not prove a route has minimum bends.
C10 effective font size           A width cap keyed off the TITLE font while the
                                 legend was smaller let text fall to 8.7px at the
                                 display width. Always key off the smallest font.
C10b aspect at display width      A diagram that is geometrically perfect and 1500px
                                 tall does not fit the pane it is read in.
C11 manifest counts               Compares node/edge COUNTS when metadata exists.
                                 It does not compare IDs or endpoints to the source;
                                 missing metadata passes this optional check.

Parsing
-------
Prefers explicit data-role attributes, including node, outcome, stage, spine,
group, and edge. The fallback for unannotated SVGs uses geometric heuristics and
can misclassify lanes and nodes; findings then require manual interpretation.
Understands <polyline points> and <path d="M ... L ..."> edges. Label bounds are
estimates, not measured glyphs. This is a complementary check, not a replacement
for the native auditor's semantic, endpoint, arrow, and text checks.

Usage
-----
    python3 cross_audit.py diagram.svg [more.svg ...]
    python3 cross_audit.py --json diagram.svg
    python3 cross_audit.py --strict diagram.svg     # exit 1 on any FAIL

Defaults are defined in DEF below and can be overridden with command-line flags.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys

# ------------------------------------------------------------------ thresholds
DEF = dict(
    min_node_clear=14.0,   # px, edge to a node it does not attach to
    min_edge_gap=20.0,     # px, between parallel overlapping runs
    min_lane_clear=13.0,   # px, edge running alongside a lane border
    max_crossings=5,       # edge-pair crossings
    max_bend_median=3,
    max_bend_max=5,
    min_font_at_target=9.5,
    max_height_at_target=900.0,
    target_width=1200.0,
    par_overlap=10.0,      # min shared span for two runs to count as parallel
    along_len=28.0,        # min shared span to count as "alongside" a border
)

AX = 1.0  # tolerance for calling a segment axis-aligned


# ---------------------------------------------------------------------- parsing
def _attrs(svg: str, tag: str):
    for m in re.finditer(r"<" + tag + r"\b([^>]*)>", svg):
        yield dict(re.findall(r'([a-zA-Z_:-]+)="([^"]*)"', m.group(1))), m.group(1)


def _pts(s: str):
    return [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+)[ ,]+(-?[\d.]+)", s)]


def parse(svg: str):
    nodes, lanes, unknown = [], [], []
    for d, _ in _attrs(svg, "rect"):
        if "x" not in d or "width" not in d:
            continue
        x, y = float(d["x"]), float(d["y"])
        box = (x, y, x + float(d["width"]), y + float(d["height"]))
        area = float(d["width"]) * float(d["height"])
        role = d.get("data-role", "")
        ident = d.get("id", "?")
        if role in ("node", "outcome"):
            # `outcome` is a first-class element in the design language (a verdict
            # box between stages). It is a drawn element and counts toward the
            # manifest, so it must not be mistaken for a dropped node.
            nodes.append((ident, box))
        elif role in ("stage", "spine", "lane", "band"):
            lanes.append((ident, box))
        elif role in ("group",):
            pass
        elif area > 3000:
            unknown.append((ident, box, area))
    if not nodes and unknown:
        # heuristic fallback: the few largest rects are lanes, the rest are nodes
        unknown.sort(key=lambda r: -r[2])
        cut = max(1, min(8, len(unknown) // 4))
        lanes += [(i, b) for i, b, _ in unknown[:cut]]
        nodes += [(i, b) for i, b, _ in unknown[cut:]]

    edges = []
    for d, raw in _attrs(svg, "polyline"):
        if "points" not in d:
            continue
        if d.get("data-role") not in (None, "", "edge"):
            continue
        edges.append((d.get("id", f"pl{len(edges)}"), _pts(d["points"]),
                      "dash" if "dasharray" in raw else "solid"))
    for d, raw in _attrs(svg, "path"):
        dd = d.get("d", "")
        if not dd.strip().startswith(("M", "m")):
            continue
        if re.search(r"[CcQqSsAaTt]", dd):        # curved: not an orthogonal edge
            continue
        p = _pts(dd)
        if len(p) >= 2:
            edges.append((d.get("id", f"p{len(edges)}"), p,
                          "dash" if "dasharray" in raw else "solid"))
    edges = [e for e in edges if len(e[1]) >= 2]

    heads = []
    for d, _ in _attrs(svg, "polygon"):
        if "points" in d:
            p = _pts(d["points"])
            if p:
                heads.append((min(q[0] for q in p), min(q[1] for q in p),
                              max(q[0] for q in p), max(q[1] for q in p)))
    markers = len(list(_attrs(svg, "marker")))

    labels = []
    for m in re.finditer(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        d = dict(re.findall(r'([a-zA-Z_:-]+)="([^"]*)"', m.group(1)))
        if "x" not in d:
            continue
        txt = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        fs = float(d.get("font-size", 12) or 12)
        anchor = d.get("text-anchor", "start")
        labels.append(dict(t=txt, x=float(d["x"]), y=float(d["y"]), fs=fs,
                           anchor=anchor, role=d.get("data-role", "")))

    W = re.search(r'<svg[^>]*\bwidth="([\d.]+)', svg)
    H = re.search(r'<svg[^>]*\bheight="([\d.]+)', svg)
    manifest = None
    mm = re.search(r"<metadata[^>]*>(.*?)</metadata>", svg, re.S)
    if mm:
        try:
            manifest = json.loads(mm.group(1).replace("&quot;", '"').strip())
        except Exception:
            manifest = None
    return dict(nodes=nodes, lanes=lanes, edges=edges, heads=heads, markers=markers,
                labels=labels, W=float(W.group(1)) if W else 0.0,
                H=float(H.group(1)) if H else 0.0, manifest=manifest)


# -------------------------------------------------------------------- geometry
def ov(a0, a1, b0, b1):
    return min(max(a0, a1), max(b0, b1)) - max(min(a0, a1), min(b0, b1))


def d_pt_rect(p, r):
    return math.hypot(max(r[0] - p[0], 0, p[0] - r[2]), max(r[1] - p[1], 0, p[1] - r[3]))


def d_seg_rect(p, q, r):
    n = max(2, int(math.dist(p, q) / 2))
    return min(d_pt_rect((p[0] + (q[0] - p[0]) * t / n, p[1] + (q[1] - p[1]) * t / n), r)
               for t in range(n + 1))


def seg_cross(a, b, c, d):
    """proper crossing, excluding shared endpoints (a T-junction is not a crossing)"""
    o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    for e1 in (a, b):
        for e2 in (c, d):
            if math.dist(e1, e2) < 1.5:
                return False
    return ((o(c, d, a) > 0) != (o(c, d, b) > 0)) and ((o(a, b, c) > 0) != (o(a, b, d) > 0))


def label_box(l):
    w = sum(0.63 if (c.isupper() or c.isdigit()) else 0.32 if c in "iljtfr .,:;'|()" else 0.53
            for c in l["t"]) * l["fs"]
    x = l["x"] - (w / 2 if l["anchor"] == "middle" else w if l["anchor"] == "end" else 0)
    return (x, l["y"] - l["fs"] * 0.8, x + w, l["y"] + l["fs"] * 0.25)


# ---------------------------------------------------------------------- checks
def audit(svg_text: str, cfg: dict) -> dict:
    G = parse(svg_text)
    nodes, lanes, edges = G["nodes"], G["lanes"], G["edges"]
    segs = [(i, e[1][k], e[1][k + 1]) for i, e in enumerate(edges) for k in range(len(e[1]) - 1)]
    eid = lambda i: edges[i][0]
    R, F = [], []

    def rec(name, value, ok, want, detail=None):
        R.append(dict(check=name, value=value, want=want, ok=bool(ok),
                      detail=(detail or [])[:6]))
        if not ok:
            F.append(name)

    # C1 orthogonality
    diag = [eid(i) for i, p, q in segs if abs(p[0] - q[0]) > 0.6 and abs(p[1] - q[1]) > 0.6]
    rec("C1 non-orthogonal segments", len(diag), not diag, "== 0", diag)

    # C2 node interiors
    hits = []
    for i, p, q in segs:
        for nid, b in nodes:
            if abs(p[0] - q[0]) < AX and b[0] + 1 < p[0] < b[2] - 1 and ov(p[1], q[1], b[1] + 1, b[3] - 1) > 1:
                hits.append(f"{eid(i)}->{nid}")
            if abs(p[1] - q[1]) < AX and b[1] + 1 < p[1] < b[3] - 1 and ov(p[0], q[0], b[0] + 1, b[2] - 1) > 1:
                hits.append(f"{eid(i)}->{nid}")
    rec("C2 segments crossing a node", len(hits), not hits, "== 0", hits)

    # C3 clearance to unattached nodes
    minc, tight = float("inf"), []
    for i, p, q in segs:
        ends = (edges[i][1][0], edges[i][1][-1])
        for nid, b in nodes:
            if min(d_pt_rect(e, b) for e in ends) < 4:
                continue
            dd = d_seg_rect(p, q, b)
            minc = min(minc, dd)
            if dd < cfg["min_node_clear"]:
                tight.append(f"{eid(i)}~{nid}@{dd:.1f}px")
    rec("C3 min edge->node clearance",
        None if minc == float("inf") else round(minc, 1),
        minc == float("inf") or minc >= cfg["min_node_clear"],
        f">= {cfg['min_node_clear']}", tight)

    # C4 parallel separation, INCLUDING exact overlap (no lower bound)
    pairs = []
    for a in range(len(segs)):
        ia, p, q = segs[a]
        for b in range(a + 1, len(segs)):
            ib, u, v = segs[b]
            if ia == ib:
                continue
            if abs(p[1] - q[1]) < AX and abs(u[1] - v[1]) < AX and ov(p[0], q[0], u[0], v[0]) > cfg["par_overlap"]:
                g = abs(p[1] - u[1])
            elif abs(p[0] - q[0]) < AX and abs(u[0] - v[0]) < AX and ov(p[1], q[1], u[1], v[1]) > cfg["par_overlap"]:
                g = abs(p[0] - u[0])
            else:
                continue
            pairs.append((g, ia, ib))
    pairs.sort()
    bad = [f"{eid(a)}|{eid(b)}@{g:.1f}px" for g, a, b in pairs if g < cfg["min_edge_gap"]]
    rec("C4 parallel pairs too close", len(bad), not bad, "== 0", bad)
    rec("C4b closest parallel gap",
        round(pairs[0][0], 1) if pairs else None,
        (not pairs) or pairs[0][0] >= cfg["min_edge_gap"], f">= {cfg['min_edge_gap']}")

    # C5 alongside a lane border (parallel only; perpendicular crossing is fine)
    along = []
    for i, p, q in segs:
        if math.dist(p, q) < cfg["along_len"]:
            continue
        horiz = abs(p[1] - q[1]) < AX
        for lid, (x0, y0, x1, y1) in lanes:
            if horiz:
                if ov(p[0], q[0], x0, x1) < cfg["along_len"]:
                    continue
                for by in (y0, y1):
                    if abs(p[1] - by) < cfg["min_lane_clear"]:
                        along.append(f"{eid(i)}~{lid}@{abs(p[1]-by):.1f}px")
            else:
                if ov(p[1], q[1], y0, y1) < cfg["along_len"]:
                    continue
                for bx in (x0, x1):
                    if abs(p[0] - bx) < cfg["min_lane_clear"]:
                        along.append(f"{eid(i)}~{lid}@{abs(p[0]-bx):.1f}px")
    rec("C5 alongside a lane border", len(along), not along, "== 0", along)

    # C6 arrowhead geometry
    if G["heads"]:
        buried = []
        for hb in G["heads"]:
            for nid, nb in nodes:
                if hb[0] < nb[2] and hb[2] > nb[0] and hb[1] < nb[3] and hb[3] > nb[1]:
                    inside = min(nb[2] - hb[0], hb[2] - nb[0], nb[3] - hb[1], hb[3] - nb[1])
                    if inside > 3:
                        buried.append(f"head@{hb[0]:.0f},{hb[1]:.0f}->{nid}")
        rec("C6 arrowheads buried in a node", len(buried), not buried, "== 0", buried)
    else:
        rec("C6 arrowheads auditable", f"no <polygon>; {G['markers']} <marker>", False,
            "emit heads as <polygon>",
            ["this auditor does not resolve <marker> geometry; emit heads as real polygons"])

    # C7 labels vs edges
    el = [l for l in G["labels"] if l["role"] in ("", "edge-label", "label") and l["t"]]
    on_edge = []
    for l in el:
        lb = label_box(l)
        for i, p, q in segs:
            if d_seg_rect(p, q, lb) < 1.0:
                on_edge.append(f"{l['t'][:18]!r}~{eid(i)}")
                break
    rec("C7 labels sitting on an edge", len(on_edge), not on_edge, "== 0", on_edge)

    # C8 crossings
    cr = sorted({(min(ia, ib), max(ia, ib))
                 for x, (ia, p, q) in enumerate(segs)
                 for ib, u, v in segs[x + 1:] if ia != ib and seg_cross(p, q, u, v)})
    rec("C8 edge-pair crossings", len(cr), len(cr) <= cfg["max_crossings"],
        f"<= {cfg['max_crossings']}", [f"{eid(a)}x{eid(b)}" for a, b in cr])

    # C9 bends
    bends = [len(e[1]) - 2 for e in edges] or [0]
    med, mx = sorted(bends)[len(bends) // 2], max(bends)
    rec("C9 bends median / max", f"{med} / {mx}",
        med <= cfg["max_bend_median"] and mx <= cfg["max_bend_max"],
        f"<= {cfg['max_bend_median']} / {cfg['max_bend_max']}")
    rec("C9b perfectly straight edges", f"{sum(1 for b in bends if b == 0)}/{len(bends)}", True, "higher is better")

    # C10 legibility + aspect at the display width
    W, H, tw = G["W"], G["H"], cfg["target_width"]
    fs = sorted({l["fs"] for l in G["labels"]}) or [12.0]
    eff = min(fs) * tw / W if W else 0
    rec("C10 smallest effective font", round(eff, 1), eff >= cfg["min_font_at_target"],
        f">= {cfg['min_font_at_target']} @ {tw:.0f}px wide")
    hh = tw * H / W if W else 0
    rec("C10b height at target width", round(hh), hh <= cfg["max_height_at_target"],
        f"<= {cfg['max_height_at_target']:.0f}")

    # C11 manifest completeness
    man = G["manifest"]
    if isinstance(man, dict) and "nodes" in man and "edges" in man:
        dn, de = len(man["nodes"]), len(man["edges"])
        ok = dn == len(nodes) and de == len(edges)
        rec("C11 manifest vs drawn", f"declared {dn}n/{de}e, drawn {len(nodes)}n/{len(edges)}e",
            ok, "equal", [] if ok else ["layout dropped or duplicated elements"])
    else:
        rec("C11 manifest present", "no <metadata> manifest", True,
            "optional but recommended")

    return dict(file=None, passed=not F, failed=F,
                counts=dict(lanes=len(lanes), nodes=len(nodes), edges=len(edges),
                            segments=len(segs), arrowheads=len(G["heads"])),
                canvas=[W, H], results=R)


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description="Engine-independent audit of layered architecture SVGs.")
    ap.add_argument("svg", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any file fails")
    for k, v in DEF.items():
        ap.add_argument("--" + k.replace("_", "-"), type=type(v), default=v)
    a = ap.parse_args()
    cfg = {k: getattr(a, k) for k in DEF}

    worst = 0
    out = []
    for path in a.svg:
        try:
            res = audit(open(path, encoding="utf-8").read(), cfg)
        except Exception as e:                      # never fail silently
            print(f"{path}: PARSE ERROR {e}", file=sys.stderr)
            worst = 1
            continue
        res["file"] = path
        out.append(res)
        if not res["passed"]:
            worst = 1
        if a.json:
            continue
        c = res["counts"]
        print(f"\n=== {path}")
        print(f"    {c['lanes']} lanes · {c['nodes']} nodes · {c['edges']} edges · "
              f"{c['segments']} segments · {c['arrowheads']} arrowheads · "
              f"canvas {res['canvas'][0]:.0f}x{res['canvas'][1]:.0f}")
        for r in res["results"]:
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"  [{mark}] {r['check']:<34} {str(r['value']):>14}   want {r['want']}")
            for d in r["detail"]:
                print(f"           - {d}")
        print(f"  ==> {'PASS' if res['passed'] else 'FAIL: ' + ', '.join(res['failed'])}")
    if a.json:
        print(json.dumps(out, indent=2))
    return worst if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())
