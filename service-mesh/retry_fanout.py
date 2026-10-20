"""How retries multiply down a call chain. Arithmetic, not a simulation.

A user request goes through `depth` calls in a row: frontend to cart, cart to pricing, pricing to inventory is
depth 3. At every call, the mesh's proxy may retry, and the service's own code may retry around it. A retry
layer that WRAPS another multiplies: if the code makes 3 attempts and each goes through a proxy that makes 3,
that call makes 9. And every call below sees all the attempts of every call above.

    python3 retry_fanout.py                    # the post's table
    python3 retry_fanout.py --depth 4 --mesh 2 --app 1 --client 0 --fail 50

With independent failures at the bottom, each with probability p, the expected number of calls reaching the
bottom is worked out level by level: a layer making n attempts, over something that fails with probability f,
makes 1 + f + ... + f^(n-1) attempts on average, and fails with probability f^n.
"""

from __future__ import annotations

import argparse
import sys


def layers(depth: int, mesh: int, app: int) -> list[int]:
    """The retry layers from the bottom up, as attempts each: the mesh's proxy, then the code around it, per call."""
    out = []
    for _ in range(depth):
        out.append(1 + mesh)
        if app:
            out.append(1 + app)
    return out


def expected(depth: int, mesh: int, app: int, client: int, p: float, budget: float | None = None) -> tuple[float, float]:
    """Expected calls reaching the bottom per user request, and the chance the user's request still fails.

    A retry budget caps each layer's retries at that share of its requests, so a layer multiplies by at most
    1 + budget. The failure chance ignores the budget: a capped layer fails more often than this says.
    """
    calls, fail = 1.0, p
    for n in layers(depth, mesh, app) + ([1 + client] if client else []):
        m = sum(fail**j for j in range(n))
        calls *= m if budget is None else min(m, 1 + budget)
        fail = fail**n
    return calls, fail


def worst(depth: int, mesh: int, app: int, client: int, budget: float | None = None) -> float:
    w = 1.0
    for n in layers(depth, mesh, app) + ([1 + client] if client else []):
        w *= n if budget is None else min(n, 1 + budget)
    return w


def line(depth: int, mesh: int, app: int, client: int, p: float) -> str:
    e, f = expected(depth, mesh, app, client, p)
    return (
        f"depth {depth}, mesh retries {mesh}, app retries {app}, client retries {client}, bottom fails {p:.0%}: "
        f"{e:.2f} calls reach the bottom per user request (worst case {worst(depth, mesh, app, client):g}), "
        f"and {f:.4%} of user requests still fail"
    )


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--depth", type=int)
    ap.add_argument("--mesh", type=int, default=2)
    ap.add_argument("--app", type=int, default=0)
    ap.add_argument("--client", type=int, default=0)
    ap.add_argument("--fail", type=float, default=100.0, help="percent of calls to the bottom that fail")
    ap.add_argument("--budget", type=float, help="retry budget, percent of requests, on every layer")
    a = ap.parse_args(argv)
    if a.depth is not None:
        b = None if a.budget is None else a.budget / 100
        if b is None:
            print(line(a.depth, a.mesh, a.app, a.client, a.fail / 100))
        else:
            e, _ = expected(a.depth, a.mesh, a.app, a.client, a.fail / 100, b)
            print(f"with a {a.budget:g}% budget: {e:.2f} calls reach the bottom (worst case {worst(a.depth, a.mesh, a.app, a.client, b):.3f})")
        return
    print("Calls reaching the bottom of a 3-call chain, per user request, when the bottom is down:")
    for mesh, app, label in ((0, 0, "no retries"), (1, 0, "1 retry per call"), (2, 0, "Istio's default over gRPC, 2 retries per call"),
                             (2, 2, "Istio's 2, inside code that retries twice")):
        print(f"  {label:48s} {worst(3, mesh, app, 0):4g}")
    print()
    print("The same chain when the bottom only sometimes fails:")
    for p in (0.01, 0.10, 0.50, 0.90, 1.0):
        print("  " + line(3, 2, 0, 0, p))
    print()
    print("Depth matters as much as the count:")
    for depth in (1, 2, 3, 4, 5):
        print(f"  depth {depth}: {worst(depth, 2, 0, 0):g} calls at the bottom with 2 retries per call")
    print()
    print("A retry budget caps what each layer can add, whatever the count:")
    for b in (0.2, 0.1):
        print(f"  {b:.0%} budget on each of 3 calls with 2 retries: at most {worst(3, 2, 0, 0, b):.3f} calls at the bottom")


if __name__ == "__main__":
    main(sys.argv[1:])
