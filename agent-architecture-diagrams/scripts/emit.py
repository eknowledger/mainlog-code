"""SVG emission and label placement against actual nodes, wires and arrowheads."""
import json
import math
from html import escape
from pathlib import Path
from geometry import bounds, expand, rect_overlap, segment_rect, arrow
from typography import text_box, width, FONT_FAMILY, TITLE_SIZE, BODY_SIZE, LABEL_SIZE

PALETTE = {
    "safety": ("#fff4f4", "#dc3545", "#a51f2d"),
    "route": ("#f0fdf5", "#22a356", "#18713e"),
    "speech": ("#eff6ff", "#3b6ded", "#234aa6"),
    "context": ("#fff9ed", "#da8800", "#9d6100"),
    "model": ("#f7f2ff", "#8b52ed", "#6632b3"),
    "legacy": ("#f8fafc", "#8393a7", "#34465c"),
}
VERDICTS = {"ok": ("#edfaf2", "#229451", "#18713e"), "stop": ("#fff0f0", "#dc3545", "#a51f2d"),
            "warn": ("#fff8e8", "#d28a00", "#8c5900")}


def place_labels(data):
    leaves = data["nodes"] + data.get("outcomes", [])
    blocks = [expand(bounds(n), 5) for n in leaves]
    containers = data["stages"] + data.get("spines", []) + data.get("groups", [])
    for c in containers:
        blocks.append(expand(text_box(c["label"].upper(), c["x"] + 12, c["y"] + 21, 12, True, "start"), 5))
    segs = [(e["id"], p, q) for e in data["edges"] for p, q in zip(e["points"], e["points"][1:])]
    triangles = [arrow(e["points"]) for e in data["edges"]]
    for tri in triangles:
        blocks.append((min(p[0] for p in tri) - 3, min(p[1] for p in tri) - 3,
                       max(p[0] for p in tri) + 3, max(p[1] for p in tri) + 3))
    placed = []
    failed = []
    for edge in sorted(data["edges"], key=lambda e: (-len(e.get("label") or ""), e["id"])):
        label = edge.get("label")
        if not label:
            continue
        w = width(label, LABEL_SIZE)
        candidates = []
        for p, q in zip(edge["points"], edge["points"][1:]):
            length = math.dist(p, q)
            if length < 28:
                continue
            horizontal = abs(p[1] - q[1]) < .01
            for fraction in (.5, .35, .65, .2, .8):
                mx, my = p[0] + (q[0] - p[0]) * fraction, p[1] + (q[1] - p[1]) * fraction
                offsets = [(0, -7), (0, 17)] if horizontal else [(w / 2 + 9, 4), (-w / 2 - 9, 4)]
                for dx, dy in offsets:
                    x, y = mx + dx, my + dy
                    box = expand(text_box(label, x, y, LABEL_SIZE), 3)
                    if box[0] < 10 or box[1] < 95 or box[2] > data["W"] - 10 or box[3] > data["H"] - 8:
                        continue
                    if any(rect_overlap(box, r) for r in blocks + placed):
                        continue
                    if any(segment_rect(a, b, expand(box, 2)) for _, a, b in segs):
                        continue
                    # Label backgrounds must not erase a visible container wall.
                    if any(segment_rect(a, b, expand(box, 2)) for c in containers for a, b in wall_segments(c)):
                        continue
                    candidates.append((abs(fraction - .5) * 20 - min(length, 300) / 60 + abs(dy) / 20, x, y, box))
        if not candidates:
            failed.append(edge["id"])
            continue
        _, x, y, box = min(candidates)
        edge["labelPosition"] = (x, y)
        placed.append(box)
    return failed


def wall_segments(c):
    x, y, r, b = bounds(c)
    return [((x, y), (r, y)), ((r, y), (r, b)), ((r, b), (x, b)), ((x, b), (x, y))]


def render(data, path):
    o = []
    def attr(value):
        return escape(str(value), quote=True)
    def text(value, x, y, size, color="#475569", bold=False, anchor="middle", owner="", role="text"):
        o.append(f'<text data-role="{role}" data-owner="{attr(owner)}" x="{x:.3f}" y="{y:.3f}" '
                 f'font-size="{size}" font-weight="{700 if bold else 400}" text-anchor="{anchor}" '
                 f'fill="{color}">{escape(value)}</text>')
    def rectangle(n, role, fill, stroke, stroke_width=1.5):
        o.append(f'<rect id="{attr(n["id"])}" data-role="{role}" data-parent="{attr(n.get("parent") or n.get("group") or n.get("stage") or n.get("spine") or "")}" '
                 f'x="{n["x"]:.3f}" y="{n["y"]:.3f}" width="{n["w"]:.3f}" height="{n["h"]:.3f}" '
                 f'rx="5" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{data["W"]:.3f}" height="{data["H"]:.3f}" '
             f'viewBox="0 0 {data["W"]:.3f} {data["H"]:.3f}" font-family="{FONT_FAMILY}" role="img">')
    o.append(f'<title>{escape(data["title"])}</title>')
    manifest = dict(nodes=[n["id"] for n in data["nodes"] + data.get("outcomes", [])],
                    edges=[{k: e[k] for k in ("id", "from", "to", "label")} for e in data["edges"]],
                    stageOrder=[s["id"] for s in data["stages"]], orders=data.get("orders", []))
    o.append(f'<metadata id="diagram-manifest">{escape(json.dumps(manifest))}</metadata>')
    o.append(f'<rect width="{data["W"]}" height="{data["H"]}" fill="#ffffff"/>')
    text(data["title"], 24, 32, 22, "#17253b", True, "start", role="heading")
    text(data.get("subtitle", ""), 24, 56, 12, anchor="start", role="heading")
    text("Layer colour identifies responsibility", 24, 82, 12, anchor="start", role="heading")
    kinds = {e["kind"] for e in data["edges"]}
    legend = []
    if kinds - {"read", "event", "data"}:
        legend.append("Solid: command")
    if kinds & {"read", "event"}:
        legend.append("Dashed: read / event")
    if "data" in kinds:
        legend.append("Heavy: data / media")
    text("   ·   ".join(legend), data["W"] - 24, 82, 12, anchor="end", role="heading")
    containers = data["stages"] + data.get("spines", []) + data.get("groups", [])
    for c in containers:
        fill, stroke, tc = PALETTE.get(c.get("category", "legacy"), PALETTE["legacy"])
        if c["role"] == "group":
            fill, stroke = "#ffffff", "#a4afbf"
        rectangle(c, c["role"], fill, stroke, 1.4)
    leaves = data["nodes"] + data.get("outcomes", [])
    for n in leaves:
        fill, stroke, _ = VERDICTS[n["verdict"]] if n["role"] == "outcome" else PALETTE.get(n.get("category", "legacy"), PALETTE["legacy"])
        rectangle(n, n["role"], fill, stroke, 1.6)
    for e in data["edges"]:
        points = " ".join(f"{x:.3f},{y:.3f}" for x, y in e["points"])
        dash = ' stroke-dasharray="6 5"' if e["kind"] in ("read", "event") else ""
        sw = 2.5 if e["kind"] == "data" else 1.6
        o.append(f'<polyline id="{attr(e["id"])}" data-role="edge" data-from="{attr(e["from"])}" data-to="{attr(e["to"])}" '
                 f'points="{points}" fill="none" stroke="#52647a" stroke-width="{sw}" stroke-linejoin="round"{dash}/>')
        tri = " ".join(f"{x:.3f},{y:.3f}" for x, y in arrow(e["points"]))
        o.append(f'<polygon data-role="arrow" data-owner="{attr(e["id"])}" points="{tri}" fill="#52647a"/>')
    for e in data["edges"]:
        if e.get("labelPosition"):
            x, y = e["labelPosition"]
            box = expand(text_box(e["label"], x, y, LABEL_SIZE), 2)
            o.append(f'<rect data-role="label-background" data-owner="{attr(e["id"])}" x="{box[0]:.3f}" y="{box[1]:.3f}" '
                     f'width="{box[2]-box[0]:.3f}" height="{box[3]-box[1]:.3f}" fill="#ffffff"/>')
            text(e["label"], x, y, LABEL_SIZE, owner=e["id"], role="edge-label")
    for c in containers:
        _, _, tc = PALETTE.get(c.get("category", "legacy"), PALETTE["legacy"])
        text(c["label"].upper(), c["x"] + 12, c["y"] + 21, 12, tc, True, "start", c["id"], "container-label")
    for n in leaves:
        _, _, tc = VERDICTS[n["verdict"]] if n["role"] == "outcome" else PALETTE.get(n.get("category", "legacy"), PALETTE["legacy"])
        text(n["title"], n["x"] + n["w"] / 2, n["y"] + 24, TITLE_SIZE, tc, True, owner=n["id"], role="node-label")
        for i, line in enumerate(n.get("lines", [])):
            text(line, n["x"] + n["w"] / 2, n["y"] + 42 + i * 16, BODY_SIZE, owner=n["id"], role="node-label")
    o.append("</svg>")
    Path(path).write_text("\n".join(o) + "\n")
