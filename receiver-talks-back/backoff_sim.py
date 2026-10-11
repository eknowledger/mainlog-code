"""A model of Alice's sender backing off and recovering, driven only by the loss in receiver reports.

    python3 backoff_sim.py               # both scenarios, as tables
    python3 backoff_sim.py --csv squeeze # one scenario, one row per second, for a chart
    python3 backoff_sim.py --reports squeeze --interval 2   # every report of one run, for the site's tool

This is a simulation, not a measurement. The controller is the loss-based half of Google Congestion Control as
draft-ietf-rmcat-gcc-02 section 6 states it, which section 7 says is what a sender should run when the receiver
sends nothing but RTCP receiver reports:

    loss > 10%       As(i) = As(i-1) * (1 - 0.5p)
    2% to 10%        As(i) = As(i-1)
    loss < 2%        As(i) = 1.05 * As(i-1)

applied once per report, to the loss the report carries (its 8-bit fraction, so p = fraction / 256).

As is the rate the path is believed to carry, on the wire. Opus gets what is left after the headers: Alice's
packets carry 40 bytes of IPv4, UDP and RTP 50 times a second, 16 kb/s that does not shrink with the codec (the
first post); longer frames, --frame 40 or 60, send fewer packets and so fewer headers. Opus is held between
6 kb/s, its floor (RFC 6716 section 2.1.1), and 32 kb/s, the first post's rate, taken as the ceiling the app asked
for.

The network is a single bottleneck with no queue: whatever is sent above its capacity is lost, so the loss is
1 - capacity / rate. Real paths queue first and lose later, which is what the delay-based half of GCC watches,
and is not modelled here. Nothing is random, so every run prints the same numbers.

The site's *Bitrate under loss* tool is a port of this file, and its fixtures are this file's output. Its inputs are
the options: --frame, --ceiling, --capacity and --loss change the model, --interval the report period. With none of
them the output is the post's, unchanged.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass

HEADER_BYTES = 40  # IPv4 20 + UDP 8 + RTP 12, on every packet whatever the codec sends
OPUS_MIN = 6.0  # RFC 6716 section 2.1.1
STEP_MS = 20  # the model advances 20 ms at a time: one packet at 20 ms frames, a fraction of one at 40 or 60
STEP = STEP_MS / 1000


@dataclass(frozen=True)
class Model:
    """The sender: its frame length, which sets the header overhead, and the Opus rate it starts at and returns to."""

    frame_ms: int = 20
    opus_max: float = 32.0

    @property
    def packets_per_s(self) -> float:
        return 1000 / self.frame_ms

    @property
    def headers(self) -> float:
        return HEADER_BYTES * 8 * self.packets_per_s / 1000

    @property
    def wire_min(self) -> float:
        return OPUS_MIN + self.headers

    @property
    def wire_max(self) -> float:
        return self.opus_max + self.headers


def num(x: float) -> str:
    """16 for a whole number, 5.33 otherwise, so the default run prints exactly what the post quotes."""
    return f"{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{x:.2f}"


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    seconds: int
    capacity: Callable[[float], float]  # kb/s the path carries at time t
    random_loss: Callable[[float], float]  # loss that happens whatever the rate, e.g. a weak radio link
    clears: float  # when the last loss ends, for the recovery figure


def scenarios(squeeze_kbps: float = 30.0, radio_loss: float = 0.15) -> dict[str, Scenario]:
    """The post's two networks. The squeeze's narrow capacity and the radio's second loss are the tool's inputs."""
    return {
        "squeeze": Scenario(
            "squeeze",
            f"The path narrows to {num(squeeze_kbps)} kb/s from 30 s to 60 s, then opens again; no other loss",
            120,
            lambda t: squeeze_kbps if 30 <= t < 60 else 100.0,
            lambda t: 0.0,
            60.0,
        ),
        "radio": Scenario(
            "radio",
            "Plenty of capacity throughout, but loss that sending less cannot fix: "
            f"5% from 20 s, {num(radio_loss * 100)}% from 40 s, none from 60 s",
            120,
            lambda t: 100.0,
            lambda t: 0.05 if 20 <= t < 40 else radio_loss if 40 <= t < 60 else 0.0,
            60.0,
        ),
    }


SCENARIOS = scenarios()
# When each scenario's loss begins, for the first reaction in --reports.
ONSET = {"squeeze": 30.0, "radio": 20.0}


def rule(m: Model, rate: float, p: float) -> tuple[float, str]:
    """draft-ietf-rmcat-gcc-02 section 6, then held inside what the codec can do."""
    if p > 0.10:
        new, why = rate * (1 - 0.5 * p), "decrease"
    elif p >= 0.02:
        new, why = rate, "hold"
    else:
        new, why = rate * 1.05, "increase"
    if new >= m.wire_max:
        return m.wire_max, "ceiling" if why == "increase" else why
    if new <= m.wire_min:
        return m.wire_min, "floor"
    return new, why


@dataclass
class Row:
    t: float
    capacity: float
    wire: float
    opus: float
    loss: float  # the true share lost over the last report interval
    reported: int  # the 8-bit fraction the RR carried
    action: str


def run(s: Scenario, interval: float, m: Model = Model()) -> tuple[list[Row], float]:
    """Step 20 ms at a time; every `interval` seconds a receiver report arrives and the rule runs once.

    Returns the state after each report, and the number of packets lost over the whole run. Sent and lost are
    expected counts, not whole packets, so a 60 ms frame sends a third of a packet per step.
    """
    rate = m.wire_max
    rows: list[Row] = [Row(0.0, s.capacity(0), rate, rate - m.headers, 0.0, 0, "start")]
    sent = lost = total_lost = 0.0
    per_step = STEP_MS / m.frame_ms
    steps_per_report = round(interval / STEP)
    for k in range(1, round(s.seconds / STEP) + 1):
        t = k * STEP
        mid = t - STEP / 2
        over = max(0.0, 1 - s.capacity(mid) / rate)
        p = 1 - (1 - over) * (1 - s.random_loss(mid))  # the two kinds of loss compound
        sent += per_step
        lost += p * per_step
        total_lost += p * per_step
        if k % steps_per_report == 0:
            fraction = math.floor(lost * 256 / sent)  # RFC 3550 A.3, on the model's expected and lost counts
            rate, action = rule(m, rate, fraction / 256)
            rows.append(Row(t, s.capacity(mid), rate, rate - m.headers, lost / sent, fraction, action))
            sent = lost = 0.0
    return rows, total_lost


def at(rows: list[Row], t: float) -> Row:
    """The state in force at time t: the last report at or before it."""
    return [r for r in rows if r.t <= t + 1e-9][-1]


def recovery(rows: list[Row], after: float, m: Model = Model()) -> float | None:
    """Seconds from `after` until Opus is back at its ceiling."""
    for r in rows:
        if r.t > after and r.opus >= m.opus_max - 1e-9:
            return r.t - after
    return None


def summary(label: str, s: Scenario, rows: list[Row], lost: float, m: Model) -> str:
    low = min(rows, key=lambda r: (r.opus, r.t))
    end = at(rows, s.clears)
    back = recovery(rows, s.clears, m)
    return (
        f"  RR every {label}: lowest Opus {low.opus:.1f} kb/s, first reached at {low.t:.0f} s; "
        f"at {s.clears:.0f} s, when the loss stops, {end.opus:.1f} kb/s "
        f"with {end.reported / 256:.1%} reported loss; {lost:.0f} packets lost in all; "
        + (
            # A run that never left the ceiling has nothing to come back from, whatever the first report says.
            f"never below {num(m.opus_max)} kb/s"
            if low.opus >= m.opus_max - 1e-9
            else f"back at {num(m.opus_max)} kb/s {back:.0f} s later"
            if back is not None
            else "not back at the ceiling by the end"
        )
    )


def table(s: Scenario, m: Model) -> None:
    (every5, lost5), (every1, lost1) = run(s, 5.0, m), run(s, 1.0, m)
    print(f"{s.title}.")
    print("  Opus kb/s after each report, with an RR every 5 s and every 1 s; loss is what the last RR reported.\n")
    print("     t   capacity |  RR every 5 s: opus  wire   loss  action   |  RR every 1 s: opus  wire   loss  action")
    for t in range(0, s.seconds + 1, 5):
        a, b = at(every5, t), at(every1, t)
        print(
            f"  {t:4d}s  {s.capacity(t - 0.01 if t else 0):6.0f}   |  "
            f"{a.opus:16.1f} {a.wire:5.1f}  {a.reported / 256:5.1%}  {a.action:8} |  "
            f"{b.opus:16.1f} {b.wire:5.1f}  {b.reported / 256:5.1%}  {b.action}"
        )
    print()
    for label, rows, lost in (("5 s", every5, lost5), ("1 s", every1, lost1)):
        print(summary(label, s, rows, lost, m))
    print()


def csv(s: Scenario, m: Model) -> None:
    (every5, _), (every1, _) = run(s, 5.0, m), run(s, 1.0, m)
    print("t_s,capacity_kbps,opus_kbps_rr5s,wire_kbps_rr5s,loss_rr5s,opus_kbps_rr1s,wire_kbps_rr1s,loss_rr1s")
    for t in range(0, s.seconds + 1):
        a, b = at(every5, t), at(every1, t)
        print(
            f"{t},{s.capacity(t - 0.01 if t else 0):.0f},{a.opus:.2f},{a.wire:.2f},{a.reported / 256:.4f},"
            f"{b.opus:.2f},{b.wire:.2f},{b.reported / 256:.4f}"
        )


def reports(s: Scenario, interval: float, m: Model) -> None:
    """Every report of one run: what it carried, what the rule did, and the rate after. The tool's table, row by row."""
    rows, lost = run(s, interval, m)
    label = f"{interval:g} s"
    print(f"{s.title}.")
    print(
        f"  RR every {label}; {num(m.frame_ms)} ms frames, so {num(m.headers)} kb/s of headers; "
        f"Opus {OPUS_MIN:.0f} to {num(m.opus_max)} kb/s, {num(m.wire_min)} to {num(m.wire_max)} kb/s on the wire.\n"
    )
    print("     t  capacity  fraction        action     opus    wire")
    for r in rows[1:]:
        print(
            f"  {r.t:4.0f}s  {r.capacity:6.0f}    {r.reported:3d}/256 {r.reported / 256:6.2%}  "
            f"{r.action:8}  {r.opus:6.2f}  {r.wire:6.2f}"
        )
    print()
    onset = ONSET[s.name]
    first = next((r for r in rows if r.t > onset and r.action in ("decrease", "floor")), None)
    if first is None:
        print("  first decrease: none")
    else:
        print(
            f"  first decrease at {first.t:.0f} s, {first.t - onset:.0f} s after the loss began at {onset:.0f} s, "
            f"on {first.reported}/256 = {first.reported / 256:.1%}"
        )
    print(summary(label, s, rows, lost, m))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", choices=sorted(SCENARIOS), help="print one scenario as CSV, one row per second")
    parser.add_argument(
        "--reports", choices=sorted(SCENARIOS), help="print every report of one scenario at one --interval"
    )
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between reports, for --reports (5)")
    parser.add_argument("--frame", type=int, choices=(20, 40, 60), default=20, help="ms of audio per packet (20)")
    parser.add_argument("--ceiling", type=float, default=32.0, help="Opus kb/s to start at and return to (32)")
    parser.add_argument("--capacity", type=float, default=30.0, help="kb/s the squeeze narrows the path to (30)")
    parser.add_argument("--loss", type=float, default=15.0, help="%% random loss in the radio's second phase (15)")
    args = parser.parse_args()
    if args.ceiling < OPUS_MIN:
        parser.error(f"--ceiling must be at least the Opus floor, {OPUS_MIN:.0f} kb/s")
    steps = args.interval / STEP
    if args.interval <= 0 or abs(steps - round(steps)) > 1e-9:
        parser.error(f"--interval must be a positive multiple of {STEP_MS} ms")
    m = Model(args.frame, args.ceiling)
    chosen = scenarios(args.capacity, args.loss / 100)
    if args.reports:
        reports(chosen[args.reports], args.interval, m)
        return
    if args.csv:
        csv(chosen[args.csv], m)
        return
    print(
        f"Headers {num(m.headers)} kb/s at {num(m.packets_per_s)} packets a second; Opus {OPUS_MIN:.0f} to "
        f"{num(m.opus_max)} kb/s, so {num(m.wire_min)} to {num(m.wire_max)} kb/s on the wire.\n"
    )
    for s in chosen.values():
        table(s, m)


if __name__ == "__main__":
    main()
