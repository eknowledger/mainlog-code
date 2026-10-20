# observability-driven: write the question, then make a test prove the spans can answer it

From the post [Write the question before the code: observability-driven development, tested](https://mainlog.dev/posts/observability-driven/).

A shop's checkout service, modelled, with real telemetry. On Tuesday version 2 ships a new way of pricing a cart,
and carts over 40 lines get slow. The dashboard, drawn in advance from a latency histogram labelled by route and
status, barely moves. The spans, whose attributes were chosen by writing the question down first, find the slice.

The bug is planted and so is the attribute that finds it. The point is not that the search is clever: it is that
`cart.items` was on the span before anyone needed it, because a test said it had to be.

```text
shop/checkout.py              the service: traffic, v1 and v2 pricing, one span and one histogram observation per request
investigate.py                Tuesday twice: what the dashboard says, then the slow requests grouped by each attribute
tests/test_question_first.py  the question, and the test that the emitted span carries what it needs (plus the red step)
series_cost.py                series a prometheus_client histogram writes, and the time to create and end one span
export_runs.py                writes lab-runs.json, the groups the post's lab draws
demo-output.txt               the output the post quotes
```

The latency is a model (seeded, nothing sleeps; each span gets explicit start and end times on a virtual clock).
The spans, the histogram, the series counts and the span timing are real.

## Run it

Python 3.13 was used for the post.

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python investigate.py               # add --csv requests.csv for a row per request
.venv/bin/python series_cost.py               # about 10 seconds; --series skips the timing
.venv/bin/python -m pytest -q tests -rx       # 3 passed, 1 xfailed: the red step
.venv/bin/python -m pytest -q tests --runxfail  # shows the red step failing, with its message
```

The span timing depends on the machine. The post's numbers are from an Apple M3 Pro, best of 5 runs of 50,000
spans, with a `BatchSpanProcessor` and an exporter that drops everything.
