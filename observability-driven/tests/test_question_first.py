"""The question, written before the pricing change, and the test that makes the spans answer it.

    pytest -q tests

The test runs the real handler with an in-memory exporter and asserts that the span it emits carries every
attribute the question needs, with a type a query can group by. `test_red_step` is the same check against the
instrumentation as it was before the question was written: it fails, and is marked as expected to, so CI shows the
red step of the loop without going red itself.
"""
from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from shop.checkout import ALL_ATTRIBUTES, BEFORE_THE_QUESTION, Checkout, Request, dashboard_histogram

QUESTION = "If checkout gets slower after the pricing change, which carts is it slower for, and on which version?"
NEEDS = {"cart.items": int, "service.version": str}


def one_checkout(record) -> object:
    exporter = InMemorySpanExporter()
    service = Checkout("v2", exporter, dashboard_histogram(CollectorRegistry()), record)
    service.handle(Request(items=61, tenant="t176", region="eu-west", base=0.075), start_ns=0)
    [span] = exporter.get_finished_spans()
    return span


def assert_can_answer(span) -> None:
    fields = {**span.resource.attributes, **span.attributes}
    missing = [k for k in NEEDS if k not in fields]
    assert not missing, f"the span can't answer {QUESTION!r}: it has no {', '.join(missing)}"
    for key, kind in NEEDS.items():
        assert isinstance(fields[key], kind), f"{key} is {type(fields[key]).__name__}, and a query needs {kind.__name__}"


def test_the_span_answers_the_question():
    assert_can_answer(one_checkout(ALL_ATTRIBUTES))


@pytest.mark.xfail(strict=True, reason="the red step: the span as it was before the question was written")
def test_red_step():
    assert_can_answer(one_checkout(BEFORE_THE_QUESTION))


def test_cart_items_is_the_count_of_lines():
    span = one_checkout(ALL_ATTRIBUTES)
    assert span.attributes["cart.items"] == 61


def test_the_span_lasts_as_long_as_the_request():
    span = one_checkout(ALL_ATTRIBUTES)
    # 75 ms of other work, 61 lines at 0.8 ms, and v2's 61 calls at 12 ms: 855.8 ms.
    assert (span.end_time - span.start_time) / 1e6 == pytest.approx(855.8)
