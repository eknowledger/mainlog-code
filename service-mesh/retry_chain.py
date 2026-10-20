"""How many calls reach the bottom of a chain when every hop's proxy retries. Real Envoy, counted.

The shop's checkout path is three calls deep: frontend calls checkout, checkout calls pricing, pricing calls inventory.
In a sidecar mesh each caller's outbound proxy carries the retry policy, so the chain is three retrying proxies:

    client ─▶ [frontend's proxy] ─▶ [checkout's proxy] ─▶ [pricing's proxy] ─▶ inventory

Each proxy here gets Istio's default retry policy, copied from Istio 1.31.1's source (2 retries; retry on
connect-failure, refused-stream, unavailable, cancelled and retriable-status-codes, with no status codes listed),
and Istio's raised circuit breakers. Then inventory fails, three ways, and we count what reaches it:

    python3 retry_chain.py          # about 15 s

There are no services between the proxies, so each proxy hands its caller exactly what it got. A real service in
the middle might turn a failure into something else; this is the case where it passes it on unchanged.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import envoy_lab as lab

ISTIO_RETRY_ON = "connect-failure,refused-stream,unavailable,cancelled,retriable-status-codes"


def retry_policy(retry_on: str = ISTIO_RETRY_ON, status_codes: list[int] | None = None, num: int = 2) -> dict:
    policy = {
        "retry_on": retry_on,
        "num_retries": num,
        "retry_host_predicate": [
            {
                "name": "envoy.retry_host_predicates.previous_hosts",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.retry.host.previous_hosts.v3.PreviousHostsPredicate"
                },
            }
        ],
        "host_selection_retry_max_attempts": 5,
    }
    if status_codes:
        policy["retriable_status_codes"] = status_codes
    return policy


class Counter503(asyncio.Protocol):
    """Inventory, alive but refusing: every request gets an immediate 503, like a full bounded queue."""

    count = 0

    def connection_made(self, transport):
        self.t = transport
        self.buf = b""

    def data_received(self, data):
        self.buf += data
        while b"\r\n\r\n" in self.buf:
            _, self.buf = self.buf.split(b"\r\n\r\n", 1)
            Counter503.count += 1
            self.t.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 1\r\n\r\n")


def start_503_server(port: int) -> None:
    def run():
        async def main():
            server = await asyncio.get_running_loop().create_server(Counter503, "127.0.0.1", port)
            async with server:
                await server.serve_forever()

        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.3)


class Hang(asyncio.Protocol):
    """Inventory, hung: it accepts every request and never answers."""

    count = 0

    def connection_made(self, transport):
        self.buf = b""

    def data_received(self, data):
        self.buf += data
        while b"\r\n\r\n" in self.buf:
            _, self.buf = self.buf.split(b"\r\n\r\n", 1)
            Hang.count += 1


def start_hang_server(port: int) -> None:
    def run():
        async def main():
            server = await asyncio.get_running_loop().create_server(Hang, "127.0.0.1", port)
            async with server:
                await server.serve_forever()

        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.3)


def timeout_trap(title: str, retry_on: str, users: int = 5) -> int:
    """One proxy with a 300 ms per-try timeout in front of a hung inventory: is the timed-out try retried?"""
    base = lab.free_port_block(26200, 4)
    start_hang_server(base)
    Hang.count = 0
    policy = retry_policy(retry_on=retry_on)
    policy["per_try_timeout"] = "0.3s"
    with lab.Fleet(concurrency=1) as fleet:
        fleet.start("pricing", lab.proxy_config("pricing", base + 1, base + 2, base, retry_policy=policy, istio_breakers=True))
        fleet.wait_ready()
        seen, took = set(), []
        for _ in range(users):
            t = time.monotonic()
            seen.add(str(http_get(base + 1)))
            took.append(time.monotonic() - t)
    per_user = Hang.count / users
    print(title)
    print(f"  retry_on {retry_on}")
    print(f"  {users} requests; the client saw: {', '.join(sorted(seen))}, after {min(took):.2f} to {max(took):.2f} s")
    print(f"  tries reaching inventory: {Hang.count}, {per_user:g} per request")
    print()
    return round(per_user)


def http_get(port: int) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/quote", timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def grpc_call(port: int) -> str:
    """One unary gRPC call over cleartext HTTP/2, by curl, returning the grpc-status the client sees."""
    frame = b"\x00\x00\x00\x00\x00"  # an empty message: compressed flag 0, length 0
    out = subprocess.run(
        ["curl", "-s", "--http2-prior-knowledge", "-o", "/dev/null", "-D", "-", "-X", "POST",
         "-H", "content-type: application/grpc", "-H", "te: trailers", "--data-binary", "@-",
         f"http://127.0.0.1:{port}/shop.Inventory/Reserve"],
        input=frame, capture_output=True, check=True,
    ).stdout.decode()
    for line in out.splitlines():
        if line.lower().startswith("grpc-status:"):
            return line.split(":", 1)[1].strip()
    return "none"


def chain(fleet: lab.Fleet, base: int, bottom: int, policy: dict, http2: bool) -> list[lab.Proxy]:
    """Three outbound proxies, built from the bottom up. Returns them top first."""
    names = ["pricing", "checkout", "frontend"]  # each is the CALLER's proxy: pricing's calls inventory
    upstream = bottom
    proxies = []
    for i, name in enumerate(names):
        listen, admin = base + 10 + 2 * i, base + 11 + 2 * i
        p = fleet.start(
            name,
            lab.proxy_config(name, listen, admin, upstream, retry_policy=policy, http2_upstream=http2,
                             istio_breakers=True),
        )
        proxies.append(p)
        upstream = listen
    fleet.wait_ready()
    return list(reversed(proxies))


def stat(p: lab.Proxy, key: str) -> int:
    return p.stats().get(key, 0)


def scenario(title: str, policy: dict, bottom: str, protocol: str, users: int = 20, client_attempts: int = 1) -> int:
    base = lab.free_port_block(26000, 20)
    bottom_port = base
    if bottom == "503":
        start_503_server(bottom_port)
        Counter503.count = 0
    with lab.Fleet(concurrency=1) as fleet:
        top, *_rest = proxies = chain(fleet, base, bottom_port, policy, http2=protocol == "grpc")
        entry = base + 10 + 2 * 2  # the frontend proxy's listener
        seen = set()
        for _ in range(users):
            for _attempt in range(client_attempts):
                result = grpc_call(entry) if protocol == "grpc" else http_get(entry)
                seen.add(str(result))
                if result in (200, "0"):
                    break
        bottom_proxy = proxies[-1]  # pricing's proxy, the one that calls inventory
        if bottom == "refused":
            reached = stat(bottom_proxy, "cluster.next.upstream_cx_connect_fail")
        else:
            reached = Counter503.count
        retries = [stat(p, "cluster.next.upstream_rq_retry") for p in proxies]
    per_user = reached / users
    print(f"{title}")
    print(f"  {users} user requests x {client_attempts} client attempt(s); the client saw: {', '.join(sorted(seen))}")
    print(f"  retries by frontend's, checkout's and pricing's proxies: {retries[0]}, {retries[1]}, {retries[2]}")
    print(f"  attempts reaching inventory: {reached}, {per_user:g} per user request")
    print()
    return round(per_user)


def main() -> None:
    print(f"Envoy {lab.envoy_version()}; {lab.machine()}")
    print("Three retrying proxies in a row, each with Istio 1.31.1's default retry policy unless stated.")
    print()
    a = scenario("1. Inventory is down (connection refused), HTTP/1.1:",
                 retry_policy(), "refused", "http")
    b = scenario("2. Inventory is down, gRPC (HTTP/2):",
                 retry_policy(), "refused", "grpc")
    c = scenario("3. Inventory is up but answers every request with 503, HTTP/1.1:",
                 retry_policy(), "503", "http")
    d = scenario("4. The same 503s, with 503 added to retriable status codes:",
                 retry_policy(status_codes=[503]), "503", "http")
    e = scenario("5. The same, and the client retries twice on top:",
                 retry_policy(status_codes=[503]), "503", "http", client_attempts=3)
    f = timeout_trap("6. Inventory hangs; pricing's proxy has a 300 ms per-try timeout and Istio's default retryOn:",
                     ISTIO_RETRY_ON)
    g = timeout_trap("7. The same, with gateway-error added to retryOn:", ISTIO_RETRY_ON + ",gateway-error")
    print("Attempts reaching inventory per user request:")
    print(f"  down, HTTP: {a}; down, gRPC: {b}; 503, Istio's default: {c}; 503 made retriable: {d}; "
          f"and a client retrying twice on top: {e}")
    print(f"A timed-out try, retried? With Istio's default retryOn, {f} try; with gateway-error added, {g} tries")


if __name__ == "__main__":
    main()
    sys.exit(0)
