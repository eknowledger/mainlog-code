"""The claims the post makes: about the SDK's timeout, the defaults, and the simulation's rows."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import anthropic  # noqa: E402

import sim  # noqa: E402
import timeouts  # noqa: E402


def test_a_trickling_stream_outlives_the_timeout():
    out = timeouts.streamed(0.5)              # 12 tokens 0.5 s apart against a 3 s timeout: about 6 s, no error
    assert out.startswith("completed in") and "12 tokens" in out


def test_a_mid_stream_stall_escapes_the_sdks_own_exceptions():
    out = timeouts.streamed(0.2, stall_after=2, stall=4.0)
    assert out.startswith("httpx2.ReadTimeout"), out
    assert "an anthropic.APIError? False" in out


def test_a_deadline_from_outside_stops_the_stream():
    import asyncio
    out = asyncio.run(timeouts.with_deadline(1.0, 2.5))
    assert out.startswith("deadline 2.5s: stopped after 2.5s"), out


def test_defaults_are_the_pinned_ones():
    out = subprocess.run([sys.executable, "defaults.py"], cwd=HERE, capture_output=True, text=True, check=True).stdout
    for line in ("timeout read=600s connect=5s, max_retries=2", "retry-after obeyed up to 4,294,967s",
                 "max_turns=10, ModelSettings().timeout=None", "recursion_limit=10007"):
        assert line in out, line


def test_the_status_breaker_never_trips_on_a_slow_provider():
    assert sim.simulate(("breaker",), "slow")["trips"] == 0


def test_every_conversation_ends_once():
    for checks in ((), sim.CHECKS):
        s = sim.simulate(checks)
        assert s["answered"] + s["handed"] + s["failed"] == sim.TASKS


def test_with_the_task_check_nobody_is_left_without_an_answer_or_a_person():
    for incident in sim.INCIDENTS:
        assert sim.simulate(("task",), incident)["failed"] == 0


def test_the_task_deadline_alone_cannot_stop_a_call_in_progress():
    # Checked between calls, so one 150 s stream runs past a 60 s deadline: the per-call check is what stops it.
    assert sim.simulate(("task",))["max"] > 150
    assert sim.simulate(("task", "stream"))["max"] < 70


def test_the_lab_grid_is_the_simulation():
    grid = json.loads((HERE / "breaker-runs.json").read_text())
    assert len(grid["runs"]) == 96
    for key, checks in (("both:00000", ()), ("both:10000", ("breaker",)), ("both:11111", sim.CHECKS)):
        s = sim.simulate(checks)
        r = grid["runs"][key]
        assert (r["answered"], r["handed"], r["failed"], r["p99"]) == (s["answered"], s["handed"], s["failed"],
                                                                       round(s["p99"], 1))
