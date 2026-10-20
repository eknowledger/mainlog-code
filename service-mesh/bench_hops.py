"""What one mesh hop costs: latency and proxy CPU, measured with real Envoy proxies on localhost.

A hop is what a sidecar mesh puts on every call between two services: the caller's outbound proxy, a connection
between the two proxies, and the callee's inbound proxy. We chain 0 to 4 of them in front of a tiny backend, with
and without mutual TLS on the proxy-to-proxy connection:

    client ─▶ [out ─(mTLS)─▶ in] ─▶ [out ─(mTLS)─▶ in] ─▶ ... ─▶ backend      (0 to 4 hops)

Two measurements per configuration, because they answer different questions and suffer different noise:

  latency  one connection, one request at a time, so nothing queues and each request's time is the path's
           own: the proxies' work plus the localhost round trips. Medians, over thousands of requests.
  CPU      an open-loop load at a fixed rate, with each proxy's CPU read from ps before and after: the cost
           per proxy per 1000 requests a second, which is how Istio publishes its own figure.

    python3 bench_hops.py                      # 5 repetitions, interleaved: about 8 minutes
    python3 bench_hops.py --quick              # 1 repetition, short: what CI runs
    python3 bench_hops.py --json results.json  # also write every run

What it measures is the proxy, not a mesh: no Kubernetes, no iptables redirect, no control plane, everything on one
machine, which also runs the client and the backend. The absolute numbers are this machine's; the differences
between hop counts are the proxies'.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import envoy_lab as lab

BODY = b"x" * 1024  # Istio's published figures use 1 KB payloads


# ---------------------------------------------------------------- the backend


class Backend(asyncio.Protocol):
    RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nContent-Type: text/plain\r\n\r\n" % len(BODY) + BODY

    def connection_made(self, transport):
        self.t = transport
        self.buf = b""

    def data_received(self, data):
        self.buf += data
        while b"\r\n\r\n" in self.buf:
            _, self.buf = self.buf.split(b"\r\n\r\n", 1)
            self.t.write(self.RESPONSE)


def serve(port: int) -> None:
    async def main():
        server = await asyncio.get_running_loop().create_server(Backend, "127.0.0.1", port, backlog=1024)
        async with server:
            await server.serve_forever()

    asyncio.run(main())


# ---------------------------------------------------------------- the clients


class Conn:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.r, self.w = reader, writer

    async def get(self) -> None:
        self.w.write(b"GET / HTTP/1.1\r\nHost: checkout\r\n\r\n")
        head = await self.r.readuntil(b"\r\n\r\n")
        if not head.startswith(b"HTTP/1.1 200"):
            raise RuntimeError(head[:80])
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        await self.r.readexactly(length)


async def sequential(port: int, n: int, warmup: int) -> list[float]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    c = Conn(r, w)
    loop = asyncio.get_running_loop()
    out = []
    for k in range(n + warmup):
        t = loop.time()
        await c.get()
        if k >= warmup:
            out.append(loop.time() - t)
    w.close()
    return out


async def load(port: int, rate: float, seconds: float, warmup: float, conns: int = 32) -> list[float]:
    """Open loop: request k is due at t0 + k/rate, and its latency counts from then, queueing included."""
    pool: asyncio.Queue[Conn] = asyncio.Queue()
    for _ in range(conns):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        await pool.put(Conn(r, w))
    loop = asyncio.get_running_loop()
    latencies: list[float] = []
    tasks = []
    total = int(rate * (seconds + warmup))
    t0 = loop.time() + 0.1

    async def one(scheduled: float, record: bool) -> None:
        c = await pool.get()
        try:
            await c.get()
        finally:
            await pool.put(c)
        if record:
            latencies.append(loop.time() - scheduled)

    for k in range(total):
        scheduled = t0 + k / rate
        delay = scheduled - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        tasks.append(asyncio.ensure_future(one(scheduled, k >= rate * warmup)))
    await asyncio.gather(*tasks)
    while not pool.empty():
        pool.get_nowait().w.close()
    return latencies


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


# ---------------------------------------------------------------- one configuration


def run_config(hops: int, mtls: bool, rate: float, seconds: float, warmup: float, backend_port: int, base: int,
               sequential_n: int) -> dict:
    with lab.Fleet(concurrency=2) as fleet:
        certs = fleet.certs if mtls else None
        # Built from the backend outwards, so each proxy knows its upstream port.
        upstream = backend_port
        proxies = []
        for i in range(hops, 0, -1):
            listen_in, listen_out = base + 4 * i, base + 4 * i + 1
            admin_in, admin_out = base + 4 * i + 2, base + 4 * i + 3
            proxies.append(fleet.start(f"in{i}", lab.proxy_config(f"in{i}", listen_in, admin_in, upstream, downstream_tls=certs)))
            proxies.append(fleet.start(f"out{i}", lab.proxy_config(f"out{i}", listen_out, admin_out, listen_in, upstream_tls=certs)))
            upstream = listen_out
        fleet.wait_ready()
        lat = asyncio.run(sequential(upstream, sequential_n, max(200, sequential_n // 10)))
        before = {p.name: lab.cpu_seconds(p.proc.pid) for p in proxies}
        started = time.monotonic()
        loaded = asyncio.run(load(upstream, rate, seconds, warmup))
        elapsed = time.monotonic() - started
        after = {p.name: lab.cpu_seconds(p.proc.pid) for p in proxies}
        rss = [lab.rss_mb(p.proc.pid) for p in proxies]
    cpu = sum(after[n] - before[n] for n in before)
    return {
        "hops": hops,
        "mtls": mtls,
        "rate": rate,
        "sequential": len(lat),
        "p50_ms": pct(lat, 0.50) * 1000,
        "p90_ms": pct(lat, 0.90) * 1000,
        "p99_ms": pct(lat, 0.99) * 1000,
        "loaded_requests": len(loaded),
        "loaded_p50_ms": pct(loaded, 0.50) * 1000,
        "loaded_p99_ms": pct(loaded, 0.99) * 1000,
        # CPU over the loaded run, warmup included, as cores; then per proxy per 1000 req/s.
        "proxy_cpu_cores": cpu / elapsed,
        "cpu_per_proxy_per_1000rps": (cpu / elapsed) / max(1, 2 * hops) / (rate / 1000) if hops else 0.0,
        "rss_mb_per_proxy": statistics.mean(rss) if rss else 0.0,
    }


def summarise(runs: list[dict], max_hops: int) -> None:
    def med(hops: int, mtls: bool, key: str) -> float:
        return statistics.median(r[key] for r in runs if r["hops"] == hops and (r["mtls"] == mtls or hops == 0))

    print("Median over repetitions:")
    print(f"  {'hops':>4}  {'':5}  {'p50 ms':>7}  {'p90 ms':>7}  {'p99 ms':>7}  {'+p50/hop':>8}  "
          f"{'proxy cores':>11}  {'vCPU/proxy/1000rps':>18}  {'RSS MB/proxy':>12}")
    b50 = med(0, False, "p50_ms")
    print(f"  {0:>4}  {'-':5}  {b50:7.3f}  {med(0, False, 'p90_ms'):7.3f}  {med(0, False, 'p99_ms'):7.3f}")
    for mtls in (False, True):
        for h in range(1, max_hops + 1):
            p50 = med(h, mtls, "p50_ms")
            print(f"  {h:>4}  {'mTLS' if mtls else 'plain':5}  {p50:7.3f}  {med(h, mtls, 'p90_ms'):7.3f}  "
                  f"{med(h, mtls, 'p99_ms'):7.3f}  {(p50 - b50) / h * 1000:6.0f}µs  "
                  f"{med(h, mtls, 'proxy_cpu_cores'):11.3f}  {med(h, mtls, 'cpu_per_proxy_per_1000rps'):18.3f}  "
                  f"{med(h, mtls, 'rss_mb_per_proxy'):12.1f}")
    for mtls in (False, True):
        xs = list(range(0, max_hops + 1))
        for key in ("p50_ms", "p90_ms"):
            ys = [med(0, False, key)] + [med(h, mtls, key) for h in range(1, max_hops + 1)]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
            print(f"  per hop, {'mTLS' if mtls else 'plain'}, {key[:3]}: {slope * 1000:.0f} µs "
                  f"(least squares over 0 to {max_hops} hops)")
        cpu = statistics.median(r["cpu_per_proxy_per_1000rps"] for r in runs if r["hops"] > 0 and r["mtls"] == mtls)
        print(f"  CPU, {'mTLS' if mtls else 'plain'}: {cpu:.3f} vCPU per proxy per 1000 req/s "
              f"(median over every run with a proxy), so {2 * cpu:.3f} per hop")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--rate", type=float, default=1000.0)
    ap.add_argument("--reps", type=int)
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--json", metavar="FILE")
    a = ap.parse_args(argv)
    if a.serve:
        serve(a.serve)
        return
    seconds = 3.0 if a.quick else 10.0
    warmup = 1.0 if a.quick else 2.0
    reps = a.reps or (1 if a.quick else 5)
    sequential_n = 500 if a.quick else 4000

    base = lab.free_port_block(24000, 4 * (a.max_hops + 2))
    backend_port = base
    backend = subprocess.Popen([sys.executable, __file__, "--serve", str(backend_port)])
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                asyncio.run(asyncio.wait_for(asyncio.open_connection("127.0.0.1", backend_port), 1))
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

        print(f"Envoy {lab.envoy_version()}, 2 worker threads per proxy; {lab.machine()}; Python {sys.version.split()[0]}")
        print(f"load average before: {' '.join(f'{x:.2f}' for x in os.getloadavg())} (other work on this machine adds noise)")
        print(f"Latency: {sequential_n} sequential requests on one connection. CPU: {a.rate:g} requests a second, "
              f"open loop, {seconds:g} s after {warmup:g} s of warmup. {reps} repetition(s), interleaved; "
              "1 KB responses over HTTP/1.1")
        print()
        runs = []
        configs = [(0, False)] + [(h, m) for m in (False, True) for h in range(1, a.max_hops + 1)]
        for rep in range(reps):
            for hops, mtls in configs:
                r = run_config(hops, mtls, a.rate, seconds, warmup, backend_port, base, sequential_n)
                r["rep"] = rep
                runs.append(r)
                print(f"  rep {rep + 1}  {hops} hop(s) {'mTLS ' if mtls else 'plain'}  p50 {r['p50_ms']:6.3f} ms  "
                      f"p90 {r['p90_ms']:6.3f} ms  p99 {r['p99_ms']:6.3f} ms  | at {a.rate:g}/s: "
                      f"p50 {r['loaded_p50_ms']:6.3f} ms, proxy CPU {r['proxy_cpu_cores']:.3f} cores, "
                      f"RSS {r['rss_mb_per_proxy']:.1f} MB/proxy", flush=True)
        print()
        print(f"load average after: {' '.join(f'{x:.2f}' for x in os.getloadavg())}")
        summarise(runs, a.max_hops)
        if a.json:
            Path(a.json).write_text(json.dumps(runs, indent=1))
    finally:
        backend.terminate()
        backend.wait()


if __name__ == "__main__":
    main(sys.argv[1:])
