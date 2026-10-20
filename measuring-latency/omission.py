"""Coordinated omission, on a real server: one that freezes for 200 ms every second, measured two ways.

The server is a TCP echo on localhost, in a process of its own. Once a second a task blocks its whole event loop
for 200 ms with time.sleep, the way a stop-the-world garbage-collection pause blocks a process. The client, in
this process, is meant to send 200 requests a second, one every 5 ms, for 10 seconds, on one connection, waiting
for each reply before sending the next, which is how most load-test loops are written.

Both runs follow that same loop. They differ only in where the clock starts:

  from send time       when the request was actually written. During a freeze the client is stuck waiting
                       for one reply, so the 39 requests it should have sent meanwhile are sent late, all at
                       once, and each looks fast. This is what an ordinary load test records.
  from scheduled time  when the request was due on the 5 ms schedule. A request due 120 ms into a freeze is
                       charged the 80 ms it spent waiting for the freeze to end, which a user would have felt.
                       This is the correction wrk2 makes.

    python3 omission.py               # 10 s per run, two runs
    python3 omission.py --seconds 3   # quicker, for a test

Timings depend on the machine, so the post quotes one run on a laptop and CI checks a band.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import multiprocessing
import time

STALL_EVERY_S = 1.0
STALL_FOR_S = 0.2
RATE_PER_S = 200


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while await reader.readline():
        writer.write(b"ok\n")
        await writer.drain()


async def staller() -> None:
    while True:
        # Serve for the rest of the second, then freeze, so a freeze starts every STALL_EVERY_S and not every
        # STALL_EVERY_S + STALL_FOR_S: the header line says "every 1 s" and that is now what the server does.
        await asyncio.sleep(STALL_EVERY_S - STALL_FOR_S)
        time.sleep(STALL_FOR_S)  # blocks this process's whole event loop; the client, in another process, keeps its clock


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


async def run(port: int, seconds: float, from_schedule: bool) -> list[float]:
    """One request every 1/RATE s on one connection, each sent only after the previous reply."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    latencies = []
    start = time.perf_counter()
    i = 0
    while True:
        due = start + i / RATE_PER_S
        if due > start + seconds:
            break
        now = time.perf_counter()
        if now < due:
            await asyncio.sleep(due - now)
        sent = time.perf_counter()
        writer.write(b"x\n")
        await writer.drain()
        await reader.readline()
        done = time.perf_counter()
        latencies.append((done - (due if from_schedule else sent)) * 1000)
        i += 1
    writer.close()
    await writer.wait_closed()
    return latencies


def summary(name: str, lat: list[float]) -> str:
    slow = sum(x >= 10 for x in lat)
    return (f"{name:20s} n={len(lat):5d}  p50 {percentile(lat, 50):7.2f}  p90 {percentile(lat, 90):7.2f}  "
            f"p99 {percentile(lat, 99):7.2f}  max {max(lat):7.2f}  mean {sum(lat) / len(lat):6.2f} ms  "
            f"over 10 ms: {slow}")


async def serve(conn) -> None:
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    conn.send(server.sockets[0].getsockname()[1])
    await staller()


def server_process(conn) -> None:
    """The server and its freezes, in another process, so the client's own clock never freezes with it."""
    asyncio.run(serve(conn))


async def main(seconds: float) -> None:
    parent, child = multiprocessing.Pipe()
    proc = multiprocessing.Process(target=server_process, args=(child,), daemon=True)
    proc.start()
    port = parent.recv()
    print(f"server freezes {STALL_FOR_S * 1000:.0f} ms every {STALL_EVERY_S:.0f} s; "
          f"client due to send {RATE_PER_S} requests a second for {seconds:g} s")
    results = {}
    for name, from_schedule in (("from send time", False), ("from scheduled time", True)):
        results[name] = await run(port, seconds, from_schedule)
        print(summary(name, results[name]))
    proc.terminate()
    proc.join()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=10.0)
    asyncio.run(main(ap.parse_args().seconds))
