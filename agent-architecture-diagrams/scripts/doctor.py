#!/usr/bin/env python3
"""doctor.py: preflight for the architecture-diagram engine.

The engine needs dependencies that are not guaranteed on an arbitrary machine.
Without this check the failures are confusing and late: a missing Pillow surfaces
as a text-measurement error deep in layout, and a missing `rsvg-convert` produces
a diagram nobody can look at. Run this before the first build on a new machine.

    python3 doctor.py            # human readable
    python3 doctor.py --json     # machine readable
    python3 doctor.py --quiet    # exit code only

Exit 0 = ready. Exit 1 = at least one hard requirement missing.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from font_config import load_font

HERE = Path(__file__).resolve().parent


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except Exception:
        return None
    if r.returncode:
        return None
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else ""


def checks() -> list[dict]:
    out: list[dict] = []

    def add(name, ok, found, hard, fix):
        out.append(dict(name=name, ok=bool(ok), found=found, hard=hard, fix=fix))

    # 1. the interpreter running this must itself have Pillow, because the engine
    #    measures text with it. A system python without Pillow is the common trap.
    try:
        from PIL import ImageFont  # noqa: F401
        import PIL
        add("Python + Pillow", True, f"python {sys.version.split()[0]} · Pillow {PIL.__version__}",
            True, "")
    except Exception as e:
        add("Python + Pillow", False, f"python {sys.version.split()[0]} · Pillow MISSING ({e})",
            True, f"{sys.executable} -m pip install pillow   "
                  f"(or run the engine with an interpreter that has it)")

    # 2. Node runs the installed libavoid WASM router.
    node = shutil.which("node")
    ver = _run(["node", "--version"]) if node else None
    try:
        major = int(ver.lstrip("v").split(".")[0]) if ver and ver.startswith("v") else 0
    except ValueError:
        major = 0
    add("Node.js >= 20", bool(node) and major >= 20,
        f"{node} {ver}" if node else "not on PATH", True,
        "install Node 20+ (nvm install 22), or add it to PATH")

    # 3. the pinned router must be installed; nothing is downloaded during rendering.
    wasm = HERE.parent / "node_modules" / "libavoid-js" / "dist" / "libavoid.wasm"
    entry = HERE.parent / "node_modules" / "libavoid-js" / "dist" / "index-node.mjs"
    add("installed libavoid", wasm.is_file() and entry.is_file(),
        f"{wasm.name} {wasm.stat().st_size // 1024}KB" if wasm.is_file() else "MISSING",
        True, "run npm ci from the skill directory")

    # 4. raster preview. The engine will not publish a diagram it cannot rasterise,
    #    because an unviewable diagram cannot be visually reviewed.
    rsvg = shutil.which("rsvg-convert")
    rsvg_version = _run([rsvg, "--version"]) if rsvg else None
    add("rsvg-convert", rsvg_version is not None, rsvg_version or "not runnable", True,
        "brew install librsvg   (or equivalent for your platform)")

    # 5. font metrics. The emitted SVG requests Arial; measuring with different
    #    metrics than the renderer uses is how label collisions get missed.
    for bold in (False, True):
        face = "bold" if bold else "regular"
        variable = "DIAGRAM_FONT_BOLD" if bold else "DIAGRAM_FONT_REGULAR"
        try:
            loaded = load_font(14, bold)
            add(f"Arial {face}", True, str(loaded.path), True, "")
        except Exception as exc:
            add(f"Arial {face}", False, str(exc), True,
                f"set {variable} to a loadable Arial {face} font file; "
                "install the same face for the SVG renderer")

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    res = checks()
    hard_fail = [c for c in res if c["hard"] and not c["ok"]]
    soft_fail = [c for c in res if not c["hard"] and not c["ok"]]

    if a.json:
        print(json.dumps(dict(ready=not hard_fail, checks=res), indent=2))
    elif not a.quiet:
        for c in res:
            mark = "ok  " if c["ok"] else ("FAIL" if c["hard"] else "warn")
            print(f"  [{mark}] {c['name']:<32} {c['found']}")
            if not c["ok"] and c["fix"]:
                print(f"         fix: {c['fix']}")
        if hard_fail:
            print(f"\n  NOT READY: {len(hard_fail)} hard requirement(s) missing. "
                  f"Fix the above before building; do not report a diagram as failed "
                  f"when the engine never ran.")
        elif soft_fail:
            print("\n  READY, with warnings. Font metrics may differ from the renderer's.")
        else:
            print("\n  READY.")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
