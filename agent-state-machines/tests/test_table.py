"""The claims the post makes about the table and the recorded run."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import booking  # noqa: E402
import demo  # noqa: E402

KINDS = {"move", "stay", "defer", "reject"}


def test_every_cell_has_a_declared_outcome():
    t = booking.table()
    assert len(t) == len(booking.STATES) * len(booking.EVENTS) == 119
    for (state, event), (kind, nxt, why) in t.items():
        assert kind in KINDS, (state, event)
        assert why, (state, event)
        if kind == "move":
            assert nxt in booking.STATES or nxt in ("done", "(resume)"), (state, event, nxt)


def test_the_drawn_machine_covers_thirteen_cells():
    drawn = [cell for cell, (_, _, why) in booking.table().items() if why == "the path the machine was drawn for"]
    assert len(drawn) == len(booking.TRANSITIONS) == 13


def test_book_leaves_only_once_the_deposit_is_paid():
    # The run reserves at step 4 and pays at step 5, both in book: the reservation alone doesn't finish the state.
    assert booking.decide("book", "committed")[:2] == ("stay", "book")
    assert booking.decide("book", "paid")[:2] == ("move", "notify")


def test_cross_cutting_events_are_never_drawn():
    for event in ("user_revises", "slot_gone", "model_unavailable", "model_back"):
        assert not any(e == event for (_, e) in booking.TRANSITIONS)


def test_an_effect_in_the_world_is_never_ignored():
    # A late 'committed', 'paid' or 'sent' is a reservation, a payment or a text that exists: rejecting it would lose
    # track of it.
    for (state, event), (kind, _, _) in booking.table().items():
        if event in ("committed", "paid", "sent") and (state, event) not in booking.TRANSITIONS:
            assert kind != "reject", (state, event)


def test_the_breaker_matters_only_where_the_model_is_called():
    for state in set(booking.STATES) - {"degraded"}:
        kind, nxt, _ = booking.decide(state, "model_unavailable")
        assert (nxt == "degraded") == (state in booking.NEEDS_MODEL)


def test_every_state_but_degraded_is_reachable():
    t = booking.table()
    reached = {"gather"}
    for (_, _), (_, nxt, _) in t.items():
        reached.add(nxt)
    assert set(booking.STATES) <= reached


def test_degraded_needs_no_model_and_no_tools():
    assert booking.STATES["degraded"][1] == []
    assert "degraded" not in booking.NEEDS_MODEL


def test_designs_share_history():
    for i in range(len(booking.RUN)):
        users = {d: booking.request(d, i)["user"] for d in ("free", "states", "fixed")}
        assert users["free"] == users["states"]
        assert users["fixed"].startswith(users["free"])


def test_recorded_run_matches_the_requests():
    run = demo.load()
    assert len(run["calls"]) == 66
    for c in run["calls"]:
        if c["step"] <= 7:
            assert c["ntools"] == len(booking.request(c["design"], c["step"] - 1)["tools"])
    # A cache entry is written once per prefix and read after; a read never exceeds what was written earlier.
    for model in {c["model"] for c in run["calls"]}:
        for design in ("free", "states", "fixed"):
            cs = demo.calls_for(run, model, design, 1)
            written = 0
            for c in cs:
                assert c["cache_read"] <= max(written, 0) or c["cache_read"] == 0
                written = max(written, c["cache_write"])


def test_the_lab_file_is_current():
    lab = json.loads((Path(__file__).resolve().parents[1] / "sm-runs.json").read_text())
    assert [m["name"] for m in lab["models"]] == ["Claude Opus 5.5", "Claude Sonnet 5"]
    assert len(lab["models"][0]["designs"]["free"]) == 8
    assert len(lab["models"][0]["designs"]["code"]) == 2
