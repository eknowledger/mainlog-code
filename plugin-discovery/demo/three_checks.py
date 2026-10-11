"""Three checks to run against a plugin registry, using stand-in entry points.

To check your own host, change make_registry() to build your registry around the
stand-ins, and change discover() and lookup() to your registry's two calls: the one
that runs discovery and the one that fetches a plugin by name. If your host looks up
importlib.metadata.entry_points and calls it during discovery, patch that name to
return the stand-ins.

discover(registry) must return something you can test names against with `in`
(a dict of name -> plugin, as textpipe's available() returns, or a list of names).
lookup(registry, name) must return the plugin or raise.
"""

import logging
from types import SimpleNamespace

from textpipe import Filter
from textpipe.registry import Registry
from textpipe.stricter import StricterRegistry

logging.disable(logging.WARNING)  # these checks read results, not the log


class StandIn:
    """Enough of importlib.metadata.EntryPoint for textpipe's registry to use."""

    def __init__(self, name, cls, package, requires=()):
        self.name, self.cls, self.loads = name, cls, 0
        self.dist = SimpleNamespace(name=package, requires=list(requires))

    def load(self):
        self.loads += 1
        return self.cls


def too_new():
    return StandIn("new", type("New", (Filter,), {"api_version": "2.0"}), "new-pkg",
                   requires=["textpipe>=2.0"])


def run_checks(make_registry, discover=lambda reg: reg.available(), lookup=lambda reg, name: reg.get(name)):
    lines = []
    # 1. Is a too-new plugin refused, and was it imported first?
    ep = too_new()
    try:
        refused = "yes" if "new" not in discover(make_registry([ep])) else "no"
    except Exception as exc:                          # a host may raise instead of skipping
        refused = f"yes (raised {type(exc).__name__})"
    lines.append(f"1. too-new plugin: refused: {refused}, imported: {'yes' if ep.loads else 'no'}")
    # 2. Does "not found" say why?
    try:
        lookup(make_registry([too_new()]), "new")
    except Exception as exc:
        lines.append(f"2. lookup: {type(exc).__name__}: {exc}")
    else:
        lines.append("2. lookup: not refused: get() returned the plugin")
    # 3. What does a name collision do, on the first request and the next one?
    reg = make_registry([StandIn("dup", Filter, "pkg-a"), StandIn("dup", Filter, "pkg-b")])
    outcomes = []
    for _ in range(2):
        try:
            discover(reg)
            outcomes.append("returned")
        except Exception as exc:
            outcomes.append(type(exc).__name__)
    lines.append(f"3. collision, two requests: {', '.join(outcomes)}")
    return lines


if __name__ == "__main__":
    for cls in (Registry, StricterRegistry):
        print(cls.__name__)
        for line in run_checks(lambda eps, cls=cls: cls(loader=lambda group: eps, host_version="1.4")):
            print("  " + line)
