"""Run every scenario (run_scenarios.sh) and write each one's output to runs.json, and the lab's five traces to lab-runs.json, for the post's replay lab.

The lab shows these recorded traces rather than simulating a plugin loader in the browser: what the reader sees is
what textpipe printed in a fresh virtual environment. Re-run after changing textpipe or a plugin, and copy lab-runs.json to the
site as src/labs/plugin-runs.json.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

here = Path(__file__).parent
with tempfile.TemporaryDirectory() as work:
    out = subprocess.run(["sh", str(here / "run_scenarios.sh"), work], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, check=True).stdout  # warnings go to stderr, and belong in the trace
runs, title = {}, None
for line in out.splitlines():
    if line.startswith("#### "):
        title = line[5:]
        runs[title] = []
    elif title is not None:
        runs[title].append(line)
(here / "runs.json").write_text(json.dumps({k: "\n".join(v) for k, v in runs.items()}, indent=1) + "\n")


def part(title, scenario):
    """One trace.py scenario's lines out of a run_scenarios.sh section."""
    lines, on = [], False
    for line in runs[title]:
        if line.startswith("== textpipe"):
            on = line.endswith(f"scenario: {scenario}")
        elif on:
            lines.append(line)
    return "\n".join(lines)


# The lab's keys, so the site's copy can be regenerated rather than cut by hand.
opening = "opening: shout and future (installed with --no-deps)"
lab = {
    "future-basic": part(opening, "opening"),
    "future-stricter": part(opening, "stricter"),
    "broken": part("broken: shout and broken", "broken"),
    "collision": part("collision: shout and loud both register 'shout'", "collision"),
    "choose": part("choose: shout and caps are both Casing filters", "choose"),
}
(here / "lab-runs.json").write_text(json.dumps(lab, indent=1) + "\n")
print(len(runs), "scenarios;", len(lab), "lab traces in lab-runs.json")
sys.exit(0)
