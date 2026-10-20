"""The shop's checkout service, as a model with real telemetry.

The latency is a model: nothing sleeps, and each request's time is drawn from a seeded random generator and then
spent on a virtual clock. The telemetry is real: every request is an OpenTelemetry span with explicit start and end
times, so a span's duration *is* the request's modelled time, and every request is observed by a prometheus_client
histogram labelled the way the shop's dashboard was drawn, by route and status.

Version 2 changes how a cart is priced. The new pricing service takes at most 40 lines in one batch call, and v2
falls back to one call per line above that. That is the bug, and it is planted: the post says so, because the point
is not that the search is clever but that the attribute which finds it has to be recorded before anyone needs it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Iterator

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from prometheus_client import CollectorRegistry, Histogram

BATCH_LIMIT = 40           # lines the v2 pricing service takes in one call
PER_LINE_CALL = 0.012      # seconds for one pricing call, when v2 falls back to a call per line
PRICE_PER_LINE = 0.0008    # seconds of pricing work per line, in both versions
REGIONS = ("eu-west", "us-east", "us-west", "ap-south")

# Everything the handler knows how to put on its span. The post's question needs the first two.
ALL_ATTRIBUTES = frozenset({"service.version", "cart.items", "tenant.id", "customer.region"})
# What the span carried before the question was written: the version (from the deploy) and the tenant, which
# support tickets are filed under. Nobody had asked about cart size yet.
BEFORE_THE_QUESTION = frozenset({"service.version", "tenant.id"})


@dataclass(frozen=True)
class Request:
    items: int
    tenant: str
    region: str
    base: float  # seconds of everything that isn't pricing


def traffic(n: int, seed: int = 7) -> Iterator[Request]:
    """The shop's checkout traffic: cart sizes with a long tail, 500 tenants, four regions."""
    rng = random.Random(seed)
    regions = random.Random(seed + 1)  # its own stream, so adding regions moved none of the other draws
    for _ in range(n):
        items = min(200, int(rng.lognormvariate(1.6, 0.9)) + 1)
        tenant = f"t{rng.randint(1, 500)}"
        base = rng.lognormvariate(-2.6, 0.35)  # about 75 ms at the median
        yield Request(items, tenant, regions.choice(REGIONS), base)


def pricing_seconds(version: str, items: int) -> float:
    """How long pricing a cart takes. v2's fallback is the regression."""
    t = PRICE_PER_LINE * items
    if version == "v2" and items > BATCH_LIMIT:
        t += PER_LINE_CALL * items
    return t


class Checkout:
    """One deployed version of the service, with its own tracer, as two deployments would have."""

    def __init__(self, version: str, exporter: SpanExporter, histogram: Histogram,
                 record: Iterable[str] = ALL_ATTRIBUTES):
        self.version = version
        self.record = frozenset(record)
        resource = {"service.name": "checkout"}
        if "service.version" in self.record:
            # The version describes the process, not the request, so OpenTelemetry puts it on the resource.
            resource["service.version"] = version
        provider = TracerProvider(resource=Resource.create(resource))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.tracer = provider.get_tracer("shop.checkout")
        self.histogram = histogram

    def handle(self, req: Request, start_ns: int) -> float:
        """Serve one checkout starting at `start_ns` on the virtual clock. Returns its duration in seconds."""
        attributes = {
            "http.request.method": "POST",
            "http.route": "/checkout",
            "http.response.status_code": 200,
            "tenant.id": req.tenant,
            "customer.region": req.region,
            "cart.items": req.items,  # the line the question asked for
        }
        # Set at creation rather than later, so a sampler deciding whether to keep the span can see them.
        span = self.tracer.start_span("POST /checkout", start_time=start_ns, attributes=self._recorded(attributes))
        seconds = req.base + pricing_seconds(self.version, req.items)
        span.end(end_time=start_ns + round(seconds * 1e9))
        self.histogram.labels("/checkout", "200").observe(seconds)
        return seconds

    def _recorded(self, attributes: dict) -> dict:
        # Switchable only so the red step of the test and the post's lab can replay what an unrecorded attribute
        # costs. A real handler would pass the dict as it is.
        always = {"http.request.method", "http.route", "http.response.status_code"}
        return {k: v for k, v in attributes.items() if k in always or k in self.record}


def dashboard_histogram(registry: CollectorRegistry) -> Histogram:
    """The metric the dashboard was drawn from: latency by route and status, default buckets."""
    return Histogram("checkout_seconds", "Checkout latency", ["route", "status"], registry=registry)


def run_tuesday(exporter: SpanExporter, registry: CollectorRegistry, record: Iterable[str] = ALL_ATTRIBUTES,
                n: int = 20_000, deploy_at: int = 10_000) -> list[tuple[str, float]]:
    """Tuesday: n checkouts, v1 until request `deploy_at`, then v2. Returns (version, seconds) per request."""
    histogram = dashboard_histogram(registry)
    services = {v: Checkout(v, exporter, histogram, record) for v in ("v1", "v2")}
    t0 = 1_760_000_000 * 10**9
    out = []
    for i, req in enumerate(traffic(n)):
        version = "v2" if i >= deploy_at else "v1"
        out.append((version, services[version].handle(req, t0 + i * 100_000_000)))
    return out
