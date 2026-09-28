"""Build deterministic alternatives, select using emitted-SVG checks, render locally."""
import argparse
import copy
import hashlib
import itertools
import json
import sys
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from collections import Counter
from layout import layout, routing_input, parent
from emit import render, place_labels
from audit import audit
from feedback import fit_boundaries
from simplify import simplification_candidates
from typography import BODY_SIZE, LABEL_SIZE

HERE = Path(__file__).resolve().parent


def candidates(spec, quick=False, seeds=None):
    stages = spec["stages"]
    base = {s["id"]: [n["id"] for n in spec["nodes"] + spec.get("groups", []) if parent(n) == s["id"]] for s in stages}
    variants = [base]
    if not quick:
        for sid, order in base.items():
            if len(order) <= 1 or len(order) > 4:
                continue
            for perm in itertools.permutations(order):
                if list(perm) == order:
                    continue
                position = {n: i for i, n in enumerate(perm)}
                constraints = [o for o in spec.get("orders", []) if set(o) <= set(order)]
                if any(any(position[a] >= position[b] for a, b in zip(o, o[1:])) for o in constraints):
                    continue
                alt = copy.deepcopy(base)
                alt[sid] = list(perm)
                variants.append(alt)
    for orders in variants:
        for mode in ("align", "spread", "pack"):
            for port_mode in ("flow", "choice"):
                padding_options = (16, 24, 32) if spec.get("groups") else (16,)
                spine_options = (44, 76) if spec.get("spines") else (44,)
                for pad, spine_gap in itertools.product(padding_options, spine_options):
                    yield dict(orders=orders, mode=mode, groupPad=pad, spineGap=spine_gap), port_mode
    # A second, bounded neighborhood search combines improvements discovered
    # independently in different rows. It never changes declared relationships.
    if seeds:
        seen = set()
        emitted = 0
        for seed in seeds:
            cfg = copy.deepcopy(seed["config"])
            for sid, order in cfg["orders"].items():
                if len(order) > 4:
                    continue
                for perm in itertools.permutations(order):
                    pos = {nid: i for i, nid in enumerate(perm)}
                    constraints = [o for o in spec.get("orders", []) if set(o) <= set(order)]
                    if any(any(pos[a] >= pos[b] for a, b in zip(o, o[1:])) for o in constraints):
                        continue
                    alt = copy.deepcopy(cfg)
                    alt["orders"][sid] = list(perm)
                    for mode in (seed["ports"], "vertical"):
                        key = json.dumps([alt, mode], sort_keys=True)
                        if key not in seen:
                            seen.add(key)
                            yield alt, mode
                            emitted += 1
                            if emitted >= 180:
                                return
            for changes in ({"grow": 1.15}, {"grow": 1.45}, {"gap": 36}, {"gap": 52},
                            {"width": 1240}, {"width": 1320}, {"mode": "pack"}):
                alt = copy.deepcopy(cfg)
                alt.update(changes)
                key = json.dumps([alt, seed["ports"]], sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    yield alt, seed["ports"]


def score(report, data, max_crossings=3):
    issues = [v for v in report["violations"] if v["kind"] != "crossing-budget"]
    wire_length = sum(math.dist(a, b) for e in data["edges"] for a, b in zip(e["points"], e["points"][1:]))
    return (len(issues), max(0, report["crossingCount"] - max_crossings), report["crossingCount"],
            -report["straightEdges"], report["totalBends"], report["maxBends"],
            round(wire_length), round(data["H"]))


def topology_candidates(seeds):
    """Try connection sides for the most conflicted edges, using SVG evidence.

    Choices are discovered from the graph and audit, never fixture IDs. Unordered
    siblings in a small column group can also exchange positions. The normal
    layout validation still enforces every declared semantic order.
    """
    for _, data, report, mode in seeds:
        counts = Counter(eid for crossing in report["crossings"] for eid in crossing[:2])
        edges = {e["id"]: e for e in data["edges"]}
        for issue in report["violations"]:
            if issue["kind"] in ("crowded-parallel", "overlapping-strokes", "arrow-over-edge"):
                counts.update(eid for eid in issue["elements"] if eid in edges)
        elements = {n["id"]: n for n in data["nodes"] + data.get("groups", [])}
        for eid, _ in counts.most_common(2):
            cfg = data["config"]
            placements = [cfg]
            relevant_groups = set()
            for endpoint in ("from", "to"):
                nid = edges[eid][endpoint]
                while nid in elements:
                    node = elements[nid]
                    if node.get("role") == "group" and node.get("direction") == "column":
                        relevant_groups.add(nid)
                    nid = parent(node)
            for gid in sorted(relevant_groups):
                kids = [n["id"] for n in elements.values() if parent(n) == gid]
                order = cfg["orders"].get(gid, kids)
                if not 2 <= len(order) <= 3:
                    continue
                for perm in itertools.permutations(order):
                    if list(perm) == order:
                        continue
                    alt = copy.deepcopy(cfg)
                    alt["orders"][gid] = list(perm)
                    placements.append(alt)
            for placement in placements:
                for source, target in itertools.product(("N", "S", "W", "E"), repeat=2):
                    alt = copy.deepcopy(placement)
                    alt.setdefault("portSides", {})[eid] = dict(source=source, target=target)
                    yield alt, mode


class Infeasible(ValueError):
    """The request cannot be satisfied, established before any routing runs.

    Distinct from the other ValueErrors build() raises: "No feasible initial
    placements" is reached only after a full placement search, and a negative
    crossing limit is a usage error. Reporting either as "rejected without
    searching" would misdescribe what happened.
    """


def find_previous_search(out, spec_id):
    """Path to a prior search state for this diagram, or None.

    A rejected build writes its artifacts under out/rejected/<id>/, and --refine is
    only ever reached after a rejection, so looking in `out` alone meant the seed was
    never where refine looked. A passing run promotes to `out`, so check there first.
    """
    name = f"{spec_id}.search.json"
    for candidate in (out / name, out / "rejected" / spec_id / name):
        if candidate.exists():
            return candidate
    return None


def refine_seeds(out, spec_id, limit=4, stderr=None):
    """Seeds for a refine pass, warning on stderr when there are none.

    Returns an empty list rather than raising, so a refine with no prior run still
    produces a diagram. It must say so, because a refine that silently behaves like
    a first search is indistinguishable from one that did nothing at all.
    """
    previous = find_previous_search(out, spec_id)
    if previous is None:
        print(f"--refine: no previous search found in {out} or "
              f"{out / 'rejected' / spec_id}; running a plain search instead",
              file=stderr if stderr is not None else sys.stderr)
        return []
    return json.loads(previous.read_text())[:limit]


FONT_FLOOR = 9.5
# The auditor measures the smallest glyph size present in the SVG, so the forecast
# has to predict that same number. 12px is always present and is always the
# smallest: emit.py draws the subtitle, the responsibility note, the edge-style
# legend and every container label at 12 unconditionally, independent of whether
# any node carries `lines` or any edge carries a label. Node titles are larger at
# TITLE_SIZE. A titles-only diagram with no sub-lines and no edge labels still
# emits 12px text, which SmallestFontInvariantTests pins -- if the legend or
# container labels ever become conditional, this constant stops being valid and
# that test is what will say so.
SMALLEST_FONT = min(BODY_SIZE, LABEL_SIZE)


def legibility_forecast(spec):
    """Best achievable effective font size, computed before any routing.

    Canvas width is set by content plus padding. The search varies order, mode,
    group padding and attachment sides (none of which remove content), so if the
    tightest layout already puts text below the floor, every candidate will fail
    the same way no matter how long the search runs. Checking first turns a search
    that was always going to be rejected into an immediate, actionable answer.

    Returns (best_font, needed_display_width) or None when it cannot be computed.
    """
    display = float(spec.get("targetWidth") or 1200)
    widths = []
    for mode in ("align", "spread", "pack"):
        try:
            widths.append(layout(spec, dict(mode=mode, groupPad=16, spineGap=44))["W"])
        except Exception:
            continue
    if not widths:
        return None
    narrowest = min(widths)
    best_font = SMALLEST_FONT * display / narrowest
    needed = math.ceil(narrowest * FONT_FLOOR / SMALLEST_FONT)
    return best_font, needed


def crossings_only_budget(evaluated):
    """Lowest crossing budget that would accept a candidate this search already
    routed, or None when crossings are not the sole blocker.

    Routing is the expensive step and it has already happened, so this costs
    nothing. It exists because the alternative is the caller re-running the whole
    search at guessed budgets at roughly a minute each: the single largest
    source of wasted iteration on a dense graph.
    """
    counts = [r["crossingCount"] for _, _, r, _ in evaluated
              if {v["kind"] for v in r["violations"]} <= {"crossing-budget"}]
    return min(counts) if counts else None


def build(spec, out, quick=False, refine=False, recipe=None, max_crossings=None, untangle=False,
          optimize=False, ignore_forecast=False):
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if max_crossings is None:
        max_crossings = (recipe or {}).get("requirements", {}).get("maxCrossings", 3)
    if max_crossings < 0:
        raise ValueError("Crossing limit must be nonnegative")
    display = int(float(spec.get("targetWidth") or 1200))
    forecast = legibility_forecast(spec)
    if forecast:
        best_font, needed_width = forecast
        print(f'{spec["id"]}: smallest text forecast {best_font:.2f}px at {display}px display '
              f'(floor {FONT_FLOOR}px)', flush=True)
        if best_font < FONT_FLOOR and not ignore_forecast:
            raise Infeasible(
                f"This content cannot be legible at a {display}px display width. The most "
                f"compact layout puts the smallest text at {best_font:.2f}px against a "
                f"{FONT_FLOOR}px floor, and routing cannot change canvas width; every "
                f"candidate would fail the same way, so the search is skipped.\n"
                f"Fix it at the source, not by searching:\n"
                f"  - remove content (fewer nodes, fewer sub-lines, shorter labels), or\n"
                f"  - set targetWidth to at least {needed_width} if the diagram really will "
                f"be displayed that wide.\n"
                f"Pass --ignore-forecast to search anyway and keep the rejected artifact.")
    # A rejected build writes its search under out/rejected/<id>/ (see the
    # `destination` assignment below), but --refine is only ever reached AFTER a
    # failure, so looking in `out` alone meant the seed was never where refine
    # looked and it silently degraded to a plain search. Check both, newest first.
    seeds = refine_seeds(out, spec["id"]) if refine else None
    settings = [(recipe["config"], recipe["portMode"])] if recipe else candidates(spec, quick, seeds)
    evaluated = []
    all_scores = []
    seen = set()

    def evaluate(settings, phase, svg, budget=None):
        entries = []
        for cfg, port_mode in settings:
            key = json.dumps([cfg, port_mode], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            try:
                data = layout(spec, cfg)
                request = routing_input(data, port_mode)
            except ValueError:
                continue
            entries.append((data, port_mode, request))
            if budget is not None and len(entries) >= budget:
                break
        if not entries:
            return
        print(f'{spec["id"]}: routing {len(entries)} deterministic candidates ({phase})', flush=True)
        proc = subprocess.run(["node", str(HERE / "router.mjs")],
                              input=json.dumps([entry[2] for entry in entries]), text=True,
                              capture_output=True, timeout=120)
        if proc.returncode:
            raise RuntimeError(proc.stderr[-3500:])
        outputs = json.loads(proc.stdout)
        if len(outputs) != len(entries):
            raise RuntimeError("Router returned an incomplete candidate batch")
        for (data, port_mode, _), output in zip(entries, outputs):
            routes = {r["id"]: r["points"] for r in output["routes"]}
            for e in data["edges"]:
                e["points"] = routes[e["id"]]
            fit_boundaries(data)
            place_labels(data)
            render(data, svg)
            report = audit(svg, spec, max_crossings=max_crossings)
            rank = score(report, data, max_crossings)
            all_scores.append(dict(candidate=len(all_scores), phase=phase, score=rank,
                                   config=data["config"], ports=port_mode,
                                   issues=dict(Counter(v["kind"] for v in report["violations"]))))
            evaluated.append((rank, data, report, port_mode))

    with tempfile.TemporaryDirectory(prefix="diagram-candidates-") as td:
        svg = Path(td) / "candidate.svg"
        evaluate(settings, "placement", svg)
        if not evaluated:
            layout(spec)
            raise ValueError("No feasible initial placements")
        # A shortest-path router cannot correct a bad choice of attachment side
        # or sibling order on its own. Feed the emitted-SVG conflicts back into
        # those choices. Bound the extra work; never relax the acceptance gate.
        if not recipe or untangle or optimize:
            for round_index in range(2):
                evaluated.sort(key=lambda row: row[0])
                best = evaluated[0]
                if best[2]["passed"] and best[2]["crossingCount"] == 0:
                    break
                before = best[0]
                evaluate(topology_candidates(evaluated[:2]), f"connections-{round_index + 1}", svg, budget=192)
                evaluated.sort(key=lambda row: row[0])
                if evaluated[0][0] >= before:
                    break
        simplification_rounds = 0
        simplification_status = "replay"
        if not recipe or optimize:
            simplification_status = "budget-exhausted"
            for round_index in range(8):
                best = min(evaluated, key=lambda row: row[0])
                if not best[2]["passed"]:
                    simplification_status = "no-feasible-seed"
                    break
                if best[2]["straightEdges"] == best[2]["edges"]:
                    simplification_status = "all-straight"
                    break
                before = best[0]
                evaluate(simplification_candidates(best), f"simplify-{round_index + 1}", svg)
                simplification_rounds += 1
                if min(evaluated, key=lambda row: row[0])[0] >= before:
                    simplification_status = "no-improving-proposal"
                    break
    rank, data, report, ports = min(evaluated, key=lambda row: row[0])
    with tempfile.TemporaryDirectory(prefix=".diagram-stage-", dir=out) as staging_dir:
        staging = Path(staging_dir)
        svg = staging / f'{spec["id"]}.svg'
        render(data, svg)
        final_report = audit(svg, spec, max_crossings=max_crossings)
        source = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        final_report.update(config=data["config"], portMode=ports, candidateCount=len(all_scores),
                            simplification=dict(rounds=simplification_rounds, status=simplification_status),
                            elapsedSeconds=round(time.monotonic() - started, 2),
                            svgSha256=hashlib.sha256(svg.read_bytes()).hexdigest(), sourceSha256=source_hash)
        for size, suffix in ((1200, ""), (2750, ".detail")):
            png = staging / f'{spec["id"]}{suffix}.png'
            subprocess.run(["rsvg-convert", "--background-color", "white", "-w", str(size),
                            "--keep-aspect-ratio", str(svg), "-o", str(png)], check=True)
            final_report["detailPngSha256" if suffix else "previewSha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
        (staging / f'{spec["id"]}.audit.json').write_text(json.dumps(final_report, indent=2) + "\n")
        (staging / f'{spec["id"]}.geometry.json').write_text(json.dumps(data, indent=2) + "\n")
        (staging / f'{spec["id"]}.source.json').write_text(source)
        (staging / f'{spec["id"]}.recipe.json').write_text(json.dumps(dict(
            schemaVersion=1, config=data["config"], portMode=ports, sourceSha256=source_hash,
            router="libavoid-js@0.4.5", requirements=dict(maxCrossings=max_crossings)), indent=2) + "\n")
        # Keep search evidence when replaying a single selected recipe.
        if not recipe or untangle or optimize:
            (staging / f'{spec["id"]}.search.json').write_text(json.dumps(sorted(all_scores, key=lambda x: x["score"]), indent=2) + "\n")
        destination = out if final_report["passed"] else out / "rejected" / spec["id"]
        destination.mkdir(parents=True, exist_ok=True)
        # Only fully rendered and audited files are promoted. Publish the receipt
        # last; it binds the exact source, SVG and both PNGs with their hashes.
        for f in sorted(staging.iterdir(), key=lambda p: p.name.endswith(".audit.json")):
            os.replace(f, destination / f.name)
    print(json.dumps({k: v for k, v in final_report.items() if k not in ("violations", "crossings", "config")}, indent=2))
    if not final_report["passed"]:
        print("Unresolved:", dict(Counter(v["kind"] for v in final_report["violations"])))
        suggestion = crossings_only_budget(evaluated)
        if suggestion is not None and suggestion > max_crossings:
            print(f"Crossings are the only blocker: --crossings {suggestion} would accept a "
                  f"candidate from this search. Either reorder to remove the crossing, or raise "
                  f"the budget and disclose the count. Never drop an edge to pass.")
    return final_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--refine", action="store_true", help="Try bounded combinations around the previous best layouts")
    parser.add_argument("--recipe", type=Path, help="Regenerate from selected abstract placement settings; no search")
    parser.add_argument("--untangle", action="store_true", help="When replaying a recipe, re-enable the connection-side and column-order search (a plain build already runs it)")
    parser.add_argument("--crossings", type=int, help="Maximum allowed crossing locations; defaults to recipe requirement or 3")
    parser.add_argument("--optimize", action="store_true", help="When replaying a recipe, re-enable the search for straighter routes with fewer bends (a plain build already runs it)")
    parser.add_argument("--ignore-forecast", action="store_true", help="Search even when the content cannot be legible at the display width")
    args = parser.parse_args()
    try:
        result = build(json.loads(args.spec.read_text()), args.out, args.quick, args.refine,
                       json.loads(args.recipe.read_text()) if args.recipe else None, args.crossings, args.untangle,
                       args.optimize, args.ignore_forecast)
    except Infeasible as exc:
        # A legitimate verdict about the diagram, not a crash. A traceback here
        # reads as an engine defect and invites the caller to debug the engine
        # instead of the content.
        print(f"Rejected without searching: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        # Everything else: a usage error, or a failure after the search already
        # ran. Do not claim the search was skipped.
        print(f"Build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(0 if result["passed"] else 1)
