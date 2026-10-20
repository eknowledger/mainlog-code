"""Write lab-runs.json, the groups the post's which-attributes lab draws.

    python export_runs.py

The lab lets a reader untick attributes and group Tuesday's slow requests by what is left. A browser has no
OpenTelemetry SDK, so instead of re-running the shop it reads these counts, taken from the same 20,000 spans
investigate.py prints. Re-run after changing the shop, and copy lab-runs.json to the site as src/labs/attr-runs.json.
"""
from __future__ import annotations

import json
from collections import Counter

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from investigate import SLOW_MS, cart_bucket, field, ms, pct
from shop.checkout import run_tuesday

CART_ORDER = ["1-10", "11-20", "21-40", "41-80", "81+"]


def groups(spans, slow, key, order=None):
    every = Counter(key(s) for s in spans)
    hit = Counter(key(s) for s in slow)
    names = order or sorted(every, key=lambda k: (-hit[k], k))
    return [{"value": k, "slow": hit[k], "all": every[k]} for k in names]


def main() -> None:
    exporter = InMemorySpanExporter()
    requests = run_tuesday(exporter, CollectorRegistry())
    spans = exporter.get_finished_spans()
    slow = [s for s in spans if ms(s) > SLOW_MS]
    version = lambda s: field(s, "service.version")  # noqa: E731
    cart = lambda s: cart_bucket(field(s, "cart.items"))  # noqa: E731

    tenants = groups(spans, slow, lambda s: field(s, "tenant.id"))
    top = tenants[:6]
    rest = tenants[6:]
    out = {
        "slowMs": SLOW_MS,
        "total": len(spans),
        "slow": len(slow),
        "dashboard": {
            v: {"p50": round(pct(xs, .5)), "p99": round(pct(xs, .99))}
            for v in ("v1", "v2")
            for xs in [[sec * 1000 for vv, sec in requests if vv == v]]
        },
        "by": {
            "service.version": groups(spans, slow, version, ["v1", "v2"]),
            "cart.items": groups(spans, slow, cart, CART_ORDER),
            "tenant.id": top + [{
                "value": f"{len(rest)} other tenants",
                "slow": sum(g["slow"] for g in rest),
                "all": sum(g["all"] for g in rest),
            }],
            "customer.region": groups(spans, slow, lambda s: field(s, "customer.region")),
            "service.version+cart.items": groups(
                spans, slow, lambda s: f"{version(s)}, {cart(s)}",
                [f"{v}, {b}" for v in ("v2", "v1") for b in CART_ORDER]),
        },
        "tenantsWithSlow": sum(1 for g in tenants if g["slow"]),
    }
    with open("lab-runs.json", "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"{out['slow']} slow of {out['total']}, {len(out['by'])} groupings")


if __name__ == "__main__":
    main()
