"""Fetch the pinned examples in sources.json, count each, and print a table.

    python3 run_comparison.py            # fetch (cached in .cache/) and count
    python3 run_comparison.py --detail   # also list every counted name
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from count_concepts import count

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
RAW = "https://raw.githubusercontent.com"
COLUMNS = ("names", "hooks", "options", "strings", "settings", "import_paths")


def fetch(example: dict) -> str:
    cached = CACHE / f"{example['id']}.txt"
    if not cached.exists():
        CACHE.mkdir(exist_ok=True)
        url = f"{RAW}/{example['repo']}/{example['commit']}/{example['path']}"
        with urllib.request.urlopen(url, timeout=30) as response:
            cached.write_bytes(response.read())
    text = cached.read_text(encoding="utf-8")
    if "block" in example:
        text = readme_block(text, example["block"])
    for key, value in example.get("template_vars", {}).items():
        text = text.replace("{{ %s }}" % key, value)
    if "rst_after" in example:
        text = rst_block(text, example["rst_after"], example.get("rst_section"))
    return text


def readme_block(markdown: str, heading: str) -> str:
    """The first ```python block after a heading line."""
    lines = markdown.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    open_fence = next(i for i in range(start, len(lines)) if lines[i].strip().lower() == "```python")
    close_fence = next(i for i in range(open_fence + 1, len(lines)) if lines[i].strip() == "```")
    return "\n".join(lines[open_fence + 1:close_fence]) + "\n"


def rst_block(rst: str, first_line: str, section: str | None = None) -> str:
    """The indented code block that starts at first_line, after a section heading if given."""
    lines = rst.splitlines()
    begin = next(i for i, line in enumerate(lines) if line.strip() == section) if section else 0
    start = next(i for i in range(begin, len(lines)) if lines[i].strip() == first_line)
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        block.append(line[indent:])
    return "\n".join(block).rstrip() + "\n"


def main() -> int:
    examples = json.loads((HERE / "sources.json").read_text())["example"]
    rows = []
    print("| Example | Names | Hooks | Options | Strings | Settings | Import paths |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for ex in examples:
        c = count(fetch(ex), set(ex["packages"]), tuple(ex.get("hook_prefixes", ())), ex.get("settings", False),
                  tuple(ex.get("string_namespaces", ())))
        row = {"id": ex["id"], **{k: sorted(getattr(c, k)) for k in ("imports", "members", *COLUMNS[1:])},
               "names": len(c.names), "lines": c.lines}
        rows.append(row)
        print(f"| {ex['label']} | {len(c.names)} | {len(c.hooks)} | {len(c.options)} | {len(c.strings)} | "
              f"{len(c.settings)} | {len(c.import_paths)} |")
    (HERE / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    if "--detail" in sys.argv:
        for r in rows:
            print(f"\n{r['id']}")
            for k in ("imports", "members", *COLUMNS[1:]):
                print(f"  {k}: {', '.join(r[k]) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
