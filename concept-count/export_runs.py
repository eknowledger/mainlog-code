"""Write the counter's output for each pinned example, with and without its flags, and each typo check's result, to
runs.json, for the post's replay labs.

The labs show these recorded runs rather than parsing Python in the browser, so what the reader sees is this script's
real output. Run `run_comparison.py` first so .cache/ holds the examples, and install requirements.txt for the typo
checks. Re-run after changing either script, and copy runs.json to the site.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import typo_checks
from run_comparison import fetch

here = Path(__file__).parent
sources = {e["id"]: e for e in json.loads((here / "sources.json").read_text())["example"]}
# The flags the article turns on for each example, as the reader would type them.
FLAGS = {
    "fastapi-readme": [],
    "django-settings": ["--settings"],
    "pytest-test-module": ["--hook-prefix", "test_", "--string-namespace", "mark"],
    "pytest-conftest": ["--hook-prefix", "pytest_", "--string-namespace", "mark"],
    "livekit-1.0.13-minimal": [],
    "livekit-1.8.3-minimal": [],
}
FILENAMES = {"fastapi-readme": "main.py", "django-settings": "settings.py", "pytest-test-module": "test_module.py",
             "pytest-conftest": "conftest.py", "livekit-1.0.13-minimal": "agent.py", "livekit-1.8.3-minimal": "agent.py"}

counts = {}
for ex_id, flags in FLAGS.items():
    src = fetch(sources[ex_id])  # the example block, cut from the cached page as run_comparison.py does
    packages = ",".join(sources[ex_id]["packages"])
    runs = {}
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / FILENAMES[ex_id]
        f.write_text(src)
        for variant in ([[], flags] if flags else [[]]):
            out = subprocess.run([sys.executable, str(here / "count_concepts.py"), packages, str(f), *variant],
                                 capture_output=True, text=True, check=True).stdout
            cmd = " ".join(["python3 count_concepts.py", packages, FILENAMES[ex_id], *variant])
            runs["flags" if variant else "plain"] = {"cmd": cmd, "out": out.replace(str(f), FILENAMES[ex_id]).rstrip()}
    counts[ex_id] = {"label": sources[ex_id]["label"], "file": FILENAMES[ex_id], "source": src.rstrip(), "runs": runs}

typos = []
for label, run in typo_checks.CASES:
    with tempfile.TemporaryDirectory() as tmp:
        typos.append({"label": label, "result": run(Path(tmp))})

(here / "runs.json").write_text(json.dumps({"counts": counts, "typos": typos}, indent=1, ensure_ascii=False) + "\n")
print(len(counts), "examples,", len(typos), "typo checks")
