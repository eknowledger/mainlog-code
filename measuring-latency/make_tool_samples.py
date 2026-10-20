"""The samples in the site's Percentiles across hosts tool, cut from seeded runs of the same model.

    python3 make_tool_samples.py

The three histogram samples are every one of the 100,000 requests of the run the post quotes (seed 7), counted
into Prometheus buckets per host: cumulative counts, each bucket counting the requests at or under its `le` edge.
The first two use the Prometheus client libraries' default edges, in seconds, with each host's `_sum` and
`_count`; the third uses finer edges, in milliseconds, as a CSV.

The two raw-latency samples are below.

Seed 32 instead of 7, picked because its first few requests per host include the stalls each sample needs: one slow
request among 200 when traffic is equal, four on host 4 when it has 140 of 200. Each host's values are its first 50
(or 20, or 140) requests in draw order, rounded to whole milliseconds. With so few, each host's nearest-rank p99 is
its largest value or close to it, so the ratios differ from the 100,000-request run, but the directions are the same.
"""

from latency_questions import Model, draw

# The Prometheus client libraries' default buckets, in seconds, written the way the exposition format writes them.
DEFAULT_EDGES = ["0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1", "2.5", "5", "10"]
FINE_EDGES_MS = [10, 20, 30, 40, 50, 100, 250, 500]
METRIC = "http_request_duration_seconds"


def at_or_under(values: list[float], edge_ms: float) -> int:
    return sum(1 for v in values if v <= edge_ms)


def histogram(busy: float) -> str:
    """One host's buckets after another, in the text format a /metrics page or a query result shows."""
    hosts = draw(Model(busy_share=busy))
    lines = [f"# TYPE {METRIC} histogram"]
    for i, values in enumerate(hosts):
        instance = f'instance="host-{i + 1}"'
        for edge in DEFAULT_EDGES:
            lines.append(f'{METRIC}_bucket{{{instance},le="{edge}"}} {at_or_under(values, float(edge) * 1000)}')
        lines.append(f'{METRIC}_bucket{{{instance},le="+Inf"}} {len(values)}')
        lines.append(f"{METRIC}_sum{{{instance}}} {sum(values) / 1000:.3f}")
        lines.append(f"{METRIC}_count{{{instance}}} {len(values)}")
    return "\n".join(lines)


def histogram_csv(busy: float) -> str:
    hosts = draw(Model(busy_share=busy))
    lines = ["instance,le,count"]
    for i, values in enumerate(hosts):
        lines += [f"host-{i + 1},{edge},{at_or_under(values, edge)}" for edge in FINE_EDGES_MS]
        lines.append(f"host-{i + 1},+Inf,{len(values)}")
    return "\n".join(lines)


def sample(busy: float, sizes: list[int], seed: int = 32) -> str:
    hosts = draw(Model(busy_share=busy, seed=seed))
    return "\n\n".join(f"host {i + 1}: " + " ".join(str(round(x)) for x in hosts[i][: sizes[i]]) for i in range(4))


if __name__ == "__main__":
    print("== buckets: equal traffic, one host stalls")
    print(histogram(0.25))
    print("\n== buckets: most traffic on the stalling host")
    print(histogram(0.70))
    print("\n== buckets as CSV, finer edges in ms: equal traffic")
    print(histogram_csv(0.25))
    print("\n== equal traffic, one host stalls")
    print(sample(0.25, [50, 50, 50, 50]))
    print("\n== most traffic on the stalling host")
    print(sample(0.70, [20, 20, 20, 140]))
