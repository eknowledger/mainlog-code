# What a service mesh costs per hop, and what it saves per team

From the post [What a service mesh costs per hop](https://mainlog.dev/posts/service-mesh/), in the collection
*Down to the wire*.

| File | What it does |
|---|---|
| `bench_hops.py` | Measures one mesh hop with real Envoy proxies on localhost: 0 to 4 sidecar pairs in front of a small backend, with and without mutual TLS between the proxies. Latency one request at a time; CPU per proxy at 1,000 requests a second |
| `retry_chain.py` | Three Envoy proxies in a row (frontend's, checkout's and pricing's), each with Istio 1.31.1's default retry policy and circuit breakers, and a bottom service, inventory, that fails three ways. Counts the calls that reach it. Then one proxy with a 300 ms per-try timeout in front of a hung inventory, to show whether a timed-out try is retried |
| `retry_fanout.py` | The fan-out arithmetic: calls at the bottom of a chain by depth and retries per layer, worst case and on average |
| `timeout_budget.py` | Whether a chain's per-try timeouts and retries fit inside each other when the bottom hangs, assuming every layer retries a timed-out try (Istio's default does not) |
| `envoy_lab.py` | Shared: Envoy configs, throwaway certificates, starting and stopping proxies, reading CPU from `ps` |
| `plot_hops.py` | Draws the post's per-hop chart from `bench_hops.py --json`. Needs matplotlib |
| `test_models.py` | The arithmetic scripts' numbers and rules |
| `results/` | The recorded runs every number in the post comes from, with the Envoy version and the machine |

## Run it

```sh
brew install envoy                  # 1.39.3 here; on Linux, copy the binary out of the envoyproxy/envoy image
python3 bench_hops.py --json results.json    # about 20 minutes: 9 configurations x 5 runs
python3 bench_hops.py --quick                # one short run, to check it works
python3 retry_chain.py                       # about a minute
python3 retry_fanout.py
python3 timeout_budget.py
python3 -m unittest test_models.py
```

`retry_chain.py`, on Envoy 1.39.3:

```text
Attempts reaching inventory per user request:
  down, HTTP: 3; down, gRPC: 27; 503, Istio's default: 1; 503 made retriable: 27; and a client retrying twice on top: 81
A timed-out try, retried? With Istio's default retryOn, 1 try; with gateway-error added, 3 tries
```

`bench_hops.py`, Envoy 1.39.3, Apple M3 Pro, macOS 26.6.2, 5 runs (the full output is in `results/`):

```text
  per hop, plain, p50: 222 µs (least squares over 0 to 4 hops)
  CPU, plain: 0.114 vCPU per proxy per 1000 req/s (median over every run with a proxy), so 0.228 per hop
  per hop, mTLS, p50: 233 µs (least squares over 0 to 4 hops)
  CPU, mTLS: 0.112 vCPU per proxy per 1000 req/s (median over every run with a proxy), so 0.224 per hop
```

## What it measures, and what it doesn't

- **The proxy, not the mesh.** No Kubernetes, no iptables redirect, no control plane, no telemetry filters. Each proxy
  runs Envoy's router and nothing else, with 2 worker threads as Istio's sidecars have.
- **One machine.** The client and backend are Python and share the CPU with the proxies, so the absolute numbers are
  that machine's and the differences between hop counts are the proxies'. The tail under load moved by tens of
  milliseconds from run to run while the machine did other work, so the post reports medians and the 90th percentile
  of the unloaded path, not a loaded p99.
- **Istio's defaults, copied, not Istio.** `retry_chain.py` puts the retry policy and circuit breakers from Istio
  1.31.1's source into plain Envoy config: `connect-failure,refused-stream,unavailable,cancelled,retriable-status-codes`
  with 2 retries, the previous-hosts predicate, and every circuit breaker at 4294967295. There are no services
  between the proxies, so each proxy passes its caller exactly the error it got.
- The certificates are generated per run, valid for two days, and never leave the temporary directory.
