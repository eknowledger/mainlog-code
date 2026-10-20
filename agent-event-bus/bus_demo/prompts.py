"""Print what Messaging's model is sent in a run: the system prompt, its tool, and the user turn.

    python -m bus_demo.prompts                 # the four runs of the 2 x 2
    python -m bus_demo.prompts call bus-note   # any scenarios
    python -m bus_demo.prompts --tools         # what Bookings' model is offered to reach Messaging by call

Nothing here is written by hand: each request is the one the run built and handed to the scripted model.
"""
from __future__ import annotations

import json
import sys
import textwrap

from .agents import MESSAGING_AS_TOOL, MESSAGING_AS_TYPED_TOOL
from .scenarios import SCENARIOS

GRID = ["call", "call-typed", "bus-note", "bus"]


def wrap(label, text):
    pad = " " * 8
    lines = []
    for para in text.splitlines():
        lines += textwrap.wrap(para, 104, initial_indent=pad, subsequent_indent=pad) or [pad]
    lines[0] = f"{label:<8}" + lines[0][8:]
    return "\n".join(lines)


def show(name):
    r = SCENARIOS[name]()
    print(f"== {name}: Messaging's model is sent {len(r['requests'])} request(s)")
    for req in r["requests"]:
        print(wrap("system:", req["system"]))
        print(wrap("tools:", ", ".join(t["name"] for t in req["tools"])))
        print(wrap("user:", req["messages"][0]["content"]))


def main(argv):
    if argv == ["--tools"]:
        for tool in (MESSAGING_AS_TOOL, MESSAGING_AS_TYPED_TOOL):
            print(json.dumps(tool, indent=2))
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    unknown = [n for n in argv if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}; see python -m bus_demo.demo --list", file=sys.stderr)
        return 2
    for i, name in enumerate(argv or GRID):
        if i:
            print()
        show(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
