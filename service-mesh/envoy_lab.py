"""Shared plumbing: Envoy configs, certificates, processes. Used by bench_hops.py and retry_chain.py.

Every proxy here is a real Envoy, started from the `envoy` binary on PATH, configured by a JSON file this module
writes. There is no Kubernetes and no traffic redirection: each proxy listens on a localhost port and forwards to
the next one, which is what a sidecar does once the redirect has delivered the request to it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


def envoy_version() -> str:
    out = subprocess.run(["envoy", "--version"], capture_output=True, text=True, check=True).stdout
    # "envoy  version: <sha>/1.39.3/Distribution/RELEASE/BoringSSL"
    return out.split("version:")[1].strip().split("/")[1]


def make_certs(directory: Path) -> dict[str, str]:
    """A throwaway CA and one leaf certificate valid for both ends of a mutual TLS connection."""
    d = directory
    run = lambda *args: subprocess.run(["openssl", *args], cwd=d, check=True, capture_output=True)  # noqa: E731
    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
        "-subj", "/CN=mesh-lab-ca", "-keyout", "ca.key", "-out", "ca.crt")
    (d / "leaf.ext").write_text(
        "subjectAltName=DNS:mesh.local,URI:spiffe://mesh.local/ns/shop/sa/checkout\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
    )
    run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-subj", "/CN=mesh.local",
        "-keyout", "leaf.key", "-out", "leaf.csr")
    run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "2",
        "-extfile", "leaf.ext", "-out", "leaf.crt")
    return {"ca": str(d / "ca.crt"), "cert": str(d / "leaf.crt"), "key": str(d / "leaf.key")}


def _tls_common(certs: dict[str, str]) -> dict:
    return {
        "tls_certificates": [
            {"certificate_chain": {"filename": certs["cert"]}, "private_key": {"filename": certs["key"]}}
        ],
        "validation_context": {"trusted_ca": {"filename": certs["ca"]}},
    }


def _h2_options() -> dict:
    return {
        "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
            "@type": "type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions",
            "explicit_http_config": {"http2_protocol_options": {}},
        }
    }


# Istio raises all four of Envoy's circuit breakers to this (cluster_traffic_policy.go at 1.31.1).
MAX_UINT32 = 4294967295


def proxy_config(
    name: str,
    listen: int,
    admin: int,
    upstream: int,
    *,
    downstream_tls: dict[str, str] | None = None,
    upstream_tls: dict[str, str] | None = None,
    retry_policy: dict | None = None,
    http2_upstream: bool = False,
    istio_breakers: bool = False,
) -> dict:
    route: dict = {"cluster": "next", "timeout": "0s"}  # Istio's default: no HTTP timeout
    if retry_policy:
        route["retry_policy"] = retry_policy
    hcm = {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": name,
        "codec_type": "AUTO",
        "route_config": {
            "virtual_hosts": [{"name": "all", "domains": ["*"], "routes": [{"match": {"prefix": "/"}, "route": route}]}]
        },
        "http_filters": [
            {
                "name": "envoy.filters.http.router",
                "typed_config": {"@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"},
            }
        ],
    }
    chain: dict = {"filters": [{"name": "envoy.filters.network.http_connection_manager", "typed_config": hcm}]}
    if downstream_tls:
        chain["transport_socket"] = {
            "name": "envoy.transport_sockets.tls",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext",
                "require_client_certificate": True,
                "common_tls_context": _tls_common(downstream_tls),
            },
        }
    cluster: dict = {
        "name": "next",
        "type": "STATIC",
        "connect_timeout": "1s",
        "load_assignment": {
            "cluster_name": "next",
            "endpoints": [
                {"lb_endpoints": [{"endpoint": {"address": {"socket_address": {"address": "127.0.0.1", "port_value": upstream}}}}]}
            ],
        },
    }
    if istio_breakers:
        cluster["circuit_breakers"] = {
            "thresholds": [
                {
                    "max_connections": MAX_UINT32,
                    "max_pending_requests": MAX_UINT32,
                    "max_requests": MAX_UINT32,
                    "max_retries": MAX_UINT32,
                }
            ]
        }
    if http2_upstream:
        cluster["typed_extension_protocol_options"] = _h2_options()
    if upstream_tls:
        cluster["transport_socket"] = {
            "name": "envoy.transport_sockets.tls",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext",
                "sni": "mesh.local",
                "common_tls_context": _tls_common(upstream_tls),
            },
        }
    return {
        "admin": {"address": {"socket_address": {"address": "127.0.0.1", "port_value": admin}}},
        "static_resources": {
            "listeners": [
                {
                    "name": "in",
                    "address": {"socket_address": {"address": "127.0.0.1", "port_value": listen}},
                    "filter_chains": [chain],
                }
            ],
            "clusters": [cluster],
        },
    }


@dataclass
class Proxy:
    name: str
    admin: int
    proc: subprocess.Popen

    def stats(self) -> dict[str, int]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.admin}/stats?format=json", timeout=5) as r:
            data = json.load(r)
        return {s["name"]: s.get("value", 0) for s in data["stats"] if "value" in s}

    def reset(self) -> None:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.admin}/reset_counters", method="POST"), timeout=5).read()


class Fleet:
    """Starts proxies, waits until each is ready, and stops them all on exit."""

    def __init__(self, concurrency: int = 2) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="mesh-lab-"))
        self.concurrency = concurrency
        self.proxies: list[Proxy] = []
        self._certs: dict[str, str] | None = None

    @property
    def certs(self) -> dict[str, str]:
        if self._certs is None:
            self._certs = make_certs(self.dir)
        return self._certs

    def start(self, name: str, config: dict) -> Proxy:
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(config))
        log = open(self.dir / f"{name}.log", "w")
        proc = subprocess.Popen(
            ["envoy", "-c", str(path), "--concurrency", str(self.concurrency), "--disable-hot-restart",
             "--log-level", "warn"],
            stdout=log, stderr=subprocess.STDOUT,
        )
        p = Proxy(name, config["admin"]["address"]["socket_address"]["port_value"], proc)
        self.proxies.append(p)
        return p

    def wait_ready(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        for p in self.proxies:
            while True:
                if p.proc.poll() is not None:
                    raise RuntimeError(f"{p.name} exited: " + (self.dir / f"{p.name}.log").read_text()[-2000:])
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{p.admin}/ready", timeout=1) as r:
                        if r.read().strip() == b"LIVE":
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{p.name} not ready")
                time.sleep(0.05)

    def stop(self) -> None:
        for p in self.proxies:
            p.proc.terminate()
        for p in self.proxies:
            try:
                p.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.proc.kill()
        self.proxies = []

    def __enter__(self) -> "Fleet":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


def cpu_seconds(pid: int) -> float:
    """Cumulative user + system CPU of a process, from ps, which is all macOS and Linux have in common."""
    out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    # "M:SS.cc", "H:MM:SS" or "D-HH:MM:SS"
    days = 0
    if "-" in out:
        d, out = out.split("-", 1)
        days = int(d)
    parts = [float(x) for x in out.split(":")]
    seconds = 0.0
    for x in parts:
        seconds = seconds * 60 + x
    return days * 86400 + seconds


def rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024


def free_port_block(start: int, count: int) -> int:
    """The first port at or above `start` from which `count` consecutive ports are free."""
    port = start
    while True:
        ok = True
        for p in range(port, port + count):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", p))
                except OSError:
                    ok = False
                    break
        if ok:
            return port
        port += count


def machine() -> str:
    import platform

    if platform.system() == "Darwin":
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        cores = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True).stdout.strip()
        ver = platform.mac_ver()[0]
        return f"{chip}, {cores} cores, macOS {ver}"
    cores = os.cpu_count()
    return f"{platform.processor() or platform.machine()}, {cores} cores, {platform.system()} {platform.release()}"
