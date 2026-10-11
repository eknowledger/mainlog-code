"""Run discovery against whatever is installed, and print what happened."""

import logging
import sys
from importlib.metadata import distributions, entry_points

from textpipe import Casing, __version__
from textpipe.registry import GROUP, Registry
from textpipe.stricter import StricterRegistry

logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")

scenario = sys.argv[1] if len(sys.argv) > 1 else "opening"
registry = StricterRegistry() if scenario == "stricter" else Registry()


def show(call):
    try:
        result = call()
        print(result if not isinstance(result, type) else f"{result.__module__}:{result.__name__}")
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")


print(f"== textpipe {__version__}, scenario: {scenario}")
if scenario == "listing":
    for ep in entry_points(group=GROUP):
        print(ep.name, ep.value, ep.dist.name)
elif scenario in ("opening", "stricter", "broken"):
    show(lambda: registry.get("summarize" if scenario != "broken" else "translate"))
    print("usable:", sorted(registry.available()))
elif scenario == "collision":
    show(registry.available)
elif scenario == "choose":
    print("select(Casing):", end=" "); show(lambda: registry.select(Casing))
    print("select(Casing, pin='caps'):", end=" "); show(lambda: registry.select(Casing, pin="caps"))
elif scenario == "which-copy":
    for ep in entry_points(group=GROUP):
        print(f"entry_points: {ep.name} -> {ep.dist.name} {ep.dist.version}")
    copies = [f"{d.metadata['Name']} {d.version}" for d in distributions()
              if d.metadata["Name"] == "textpipe-shout"]
    print("distributions:", ", ".join(copies))
