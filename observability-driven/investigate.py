"""Tuesday's investigation, twice: once from the dashboard, once from the spans.

    python investigate.py              # the dashboard view, then the span view
    python investigate.py --csv FILE   # also write every request's version, cart size and milliseconds

The dashboard view reads only what the dashboard was drawn from: the checkout_seconds histogram, labelled by route
and status. The span view groups the slow requests by each attribute the spans carry.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from shop.checkout import BATCH_LIMIT, dashboard_histogram, run_tuesday

SLOW_MS = 500


def pct(xs: list[float], p: float) -> float:
    """The value at index floor(p x (n - 1)) of the sorted list: no interpolation, the same rule as the prototype."""
    s = sorted(xs)
    return s[int(p * (len(s) - 1))]


def ms(span) -> float:
    return (span.end_time - span.start_time) / 1e6


def field(span, key: str):
    """An attribute from the span, or from its resource, which is where the version lives."""
    if key in span.attributes:
        return span.attributes[key]
    return span.resource.attributes.get(key)


def cart_bucket(items: int) -> str:
    for hi, label in ((10, "1-10"), (20, "11-20"), (40, "21-40"), (80, "41-80")):
        if items <= hi:
            return label
    return "81+"


def histogram_quantile(registry: CollectorRegistry, q: float) -> float:
    """What a Prometheus panel would draw: PromQL's histogram_quantile over the cumulative buckets, in ms."""
    buckets = []
    for metric in registry.collect():
        for s in metric.samples:
            if s.name == "checkout_seconds_bucket":
                buckets.append((float(s.labels["le"]), s.value))
    buckets.sort()
    rank = q * buckets[-1][1]
    lo_bound, lo_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= rank:
            if bound == float("inf"):
                return lo_bound * 1000
            return (lo_bound + (bound - lo_bound) * (rank - lo_count) / (count - lo_count)) * 1000
        lo_bound, lo_count = bound, count
    return float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    args = ap.parse_args()

    exporter = InMemorySpanExporter()
    reg = CollectorRegistry()
    requests = run_tuesday(exporter, reg)
    before = [s * 1000 for v, s in requests if v == "v1"]
    after = [s * 1000 for v, s in requests if v == "v2"]

    print("== The dashboard: checkout_seconds{route=\"/checkout\", status=\"200\"}")
    for name, xs in (("before the deploy", before), ("after the deploy", after)):
        print(f"  {name:18s} p50 {pct(xs, .5):4.0f} ms   p99 {pct(xs, .99):4.0f} ms   "
              f"mean {sum(xs) / len(xs):3.0f} ms   ({len(xs)} requests)")
    # What a panel draws is an estimate from the buckets, so replay each half into a histogram of its own.
    for name, xs in (("before", before), ("after", after)):
        half = CollectorRegistry()
        h = dashboard_histogram(half)
        for x in xs:
            h.labels("/checkout", "200").observe(x / 1000)
        print(f"  {name}, as histogram_quantile reads the buckets: p50 {histogram_quantile(half, .5):.0f} ms   "
              f"p99 {histogram_quantile(half, .99):.0f} ms")
    print(f"  slowest request after the deploy: {max(after):.0f} ms")

    spans = exporter.get_finished_spans()
    v2 = [s for s in spans if field(s, "service.version") == "v2"]
    slow = [s for s in v2 if ms(s) > SLOW_MS]
    print()
    print(f"== The spans: {len(slow)} requests over {SLOW_MS} ms after the deploy, of {len(v2)}")
    everyone_slow = [s for s in spans if ms(s) > SLOW_MS]
    by_version = Counter(field(s, "service.version") for s in everyone_slow)
    print(f"  by service.version: v1 {by_version['v1']}, v2 {by_version['v2']}")
    all_buckets = Counter(cart_bucket(field(s, "cart.items")) for s in v2)
    slow_buckets = Counter(cart_bucket(field(s, "cart.items")) for s in slow)
    for b in ("1-10", "11-20", "21-40", "41-80", "81+"):
        print(f"  cart.items {b:>5s}: {slow_buckets[b]:3d} slow of {all_buckets[b]:5d}")
    tenants = Counter(field(s, "tenant.id") for s in slow)
    top, top_n = tenants.most_common(1)[0]
    print(f"  by tenant.id: {len(tenants)} different tenants, the most any one has is {top_n} ({top})")
    regions = Counter(field(s, "customer.region") for s in slow)
    print("  by customer.region: " + ", ".join(f"{r} {n}" for r, n in sorted(regions.items())))

    big = [ms(s) for s in v2 if field(s, "cart.items") > BATCH_LIMIT]
    over = sum(field(s, "cart.items") > BATCH_LIMIT for s in slow)
    print()
    print(f"== The slice: v2 with more than {BATCH_LIMIT} items: {len(big)} of {len(v2)} requests, "
          f"{100 * len(big) / len(v2):.1f}%")
    print(f"  p50 {pct(big, .5):.0f} ms   p99 {pct(big, .99):.0f} ms")
    print(f"  requests over {SLOW_MS} ms with more than {BATCH_LIMIT} items: {over} of {len(slow)}")
    rest = [ms(s) for s in v2 if field(s, "cart.items") <= BATCH_LIMIT]
    print(f"  everyone else on v2: p50 {pct(rest, .5):.0f} ms   p99 {pct(rest, .99):.0f} ms")
    # The customer who wrote in: the slow request with a cart nearest 60 items.
    near = min(slow, key=lambda s: (abs(field(s, "cart.items") - 60), -ms(s)))
    print(f"  one of them: tenant {field(near, 'tenant.id')}, {field(near, 'cart.items')} items, {ms(near):.0f} ms")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["version", "cart_items", "ms"])
            for s in spans:
                w.writerow([field(s, "service.version"), field(s, "cart.items"), f"{ms(s):.3f}"])


if __name__ == "__main__":
    main()
