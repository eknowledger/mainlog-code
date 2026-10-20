"""Run the post's scenarios and print each log and its outcome.

    python -m bus_demo.demo --list
    python -m bus_demo.demo call bus
    python -m bus_demo.demo --all > demo-output.txt
"""
from __future__ import annotations

import sys

from .scenarios import SCENARIOS


def show(name):
    r = SCENARIOS[name]()
    print(f"== {name}")
    print(r["log"].render())
    group = [t for to, t in r["sent"] if to == "group"]
    you = [t for to, t in r["sent"] if to == "you"]
    print(f"-- group received: {len(group)}" + "".join(f"\n--   {t!r}" for t in group))
    print(f"-- you received: {len(you)}" + "".join(f"\n--   {t!r}" for t in you))
    held = ", ".join(f"{k} ({v.replace(' dinner', '')})" for k, v in sorted(r["calendar"].items()))
    print(f"-- calendar holds: {held or 'nothing'}")
    if r.get("offsets_while_down") is not None:
        print("-- offsets while messaging was down: "
              + ", ".join(f"{k}={v}" for k, v in r["offsets_while_down"].items()))
    if r["offsets"] is not None:
        print("-- offsets: " + ", ".join(f"{k}={v}" for k, v in r["offsets"].items()))
    if "rebuilt" in r:
        rebuilt = ", ".join(f"{k} ({v.replace(' dinner', '')})" for k, v in sorted(r["rebuilt"].items()))
        print(f"-- rebuilt calendar holds: {rebuilt or 'nothing'}")


def main(argv):
    if not argv or argv[0] == "--list":
        print("\n".join(SCENARIOS))
        return 0
    names = list(SCENARIOS) if argv[0] == "--all" else argv
    for i, name in enumerate(names):
        if i:
            print()
        show(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
