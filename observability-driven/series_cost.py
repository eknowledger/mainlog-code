"""What the telemetry costs: series for the metric, microseconds for the span.

    python series_cost.py            # series counts, then the span timing (about 10 seconds)
    python series_cost.py --series   # series counts only

Series are counted the way a Prometheus server sees them: every non-comment line of the exposition prometheus_client
writes is one sample of one series. The span timing creates and ends spans through the real SDK, with
the attributes passed at creation as the post recommends, a BatchSpanProcessor and an exporter that drops everything, so it is the cost inside the service and not the cost of
shipping the data anywhere. Timing moves with the machine, which is why CI prints it and doesn't check it.
"""
from __future__ import annotations

import argparse
import platform
import sys
import time
from importlib.metadata import version

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest


def series(registry: CollectorRegistry) -> list[str]:
    return [line for line in generate_latest(registry).decode().splitlines() if line and not line.startswith("#")]


def count_series() -> None:
    reg = CollectorRegistry()
    h = Histogram("checkout_seconds", "Checkout latency", ["route", "status"], registry=reg)
    h.labels("/checkout", "200").observe(0.08)
    lines = series(reg)
    kinds = {}
    for line in lines:
        name = line.split("{")[0]
        kinds[name] = kinds.get(name, 0) + 1
    print(f"one histogram, one label set (route, status): {len(lines)} series")
    print("  " + ", ".join(f"{n} x{c}" for n, c in kinds.items()))

    reg = CollectorRegistry()
    h = Histogram("checkout_seconds", "Checkout latency", ["route", "status", "tenant"], registry=reg)
    for t in range(1, 501):
        h.labels("/checkout", "200", f"t{t}").observe(0.08)
    print(f"the same histogram with a tenant label, 500 tenants: {len(series(reg))} series")

    reg = CollectorRegistry()
    c = Counter("checkouts", "Checkouts", ["route", "status"], registry=reg)
    c.labels("/checkout", "200").inc()
    print(f"a counter, one label set: {len(series(reg))} series ({', '.join(l.split('{')[0] for l in series(reg))})")


class Drop(SpanExporter):
    def export(self, spans):
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def time_spans(attributes: int, n: int = 50_000, rounds: int = 5) -> float:
    provider = TracerProvider()
    # A queue that holds a whole round, flushed between rounds, so no span is dropped and the timing is of the
    # normal path rather than the cheaper queue-full one.
    provider.add_span_processor(BatchSpanProcessor(Drop(), max_queue_size=n + 1))
    tracer = provider.get_tracer("bench")
    attrs = {f"attr.{i}": i for i in range(attributes)}
    best = float("inf")
    for _ in range(rounds):
        t = time.perf_counter()
        for _ in range(n):
            # Attributes at creation, the way the shop's handler sets them, so a sampler could read them.
            tracer.start_span("POST /checkout", attributes=attrs).end()
        best = min(best, (time.perf_counter() - t) / n)
        provider.force_flush()
    provider.shutdown()
    return best * 1e6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", action="store_true", help="series counts only")
    args = ap.parse_args()
    print(f"prometheus_client {version('prometheus_client')}, opentelemetry-sdk {version('opentelemetry-sdk')}, "
          f"Python {sys.version.split()[0]}")
    print()
    count_series()
    if args.series:
        return
    print()
    print(f"one span, created and ended, best of 5 x 50,000, on {platform.system()} {platform.machine()}:")
    for k in (0, 4, 10):
        print(f"  {k:2d} attributes: {time_spans(k):5.1f} µs")


if __name__ == "__main__":
    main()
