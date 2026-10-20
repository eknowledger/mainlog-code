"""The memory post's claims about this scenario, one test each."""
from memory_demo.sim import POLICIES, Store, run, table


def counts(policy):
    c, _ = run(policy)
    return c["stale"], c["missing"], c["injected"]


def test_every_policy_sees_the_same_89_reads():
    assert {run(p)[0]["reads"] for p in POLICIES} == {89}


def test_time_based_policies_keep_what_is_read_and_lose_what_is_rare():
    assert counts("keep") == (49, 0, 29)
    assert counts("ttl-refresh") == (49, 1, 29)       # refresh keeps the stale facts alive, loses the favourite
    assert counts("ttl-fixed") == (3, 25, 2)          # fixed: little stale, a lot missing
    assert counts("disuse-30") == (49, 1, 29)


def test_contradiction_trades_staleness_for_injection():
    assert counts("contra-chat") == (28, 0, 0)        # never sees the cancellation
    assert counts("contra-all") == (0, 0, 29)         # sees it, and stores the restaurant's sentence too


def test_writer_and_dependency_wins_by_construction_and_loses_on_a_wrong_tag():
    assert counts("provenance") == (0, 0, 0)
    assert counts("provenance-misfiled") == (28, 0, 0)


def test_the_write_gate_refuses_the_restaurants_claim():
    s = Store("provenance")
    assert s.write(2, "restaurant", "deposit_preapproved", "yes", "claim", None).startswith("refused")
    assert s.write(0, "user", "ana_diet", "vegetarian", "stated", None) == "written"


def test_day_81_under_keep_forever():
    _, snap = run("keep", snapshot_day=81)
    assert snap["ana_diet"][0] == ["eats fish", "vegetarian"]
    assert snap["next_dinner"][0] == ["R1 Fri", "R2 Sat"] and snap["next_dinner"][1] is None
    assert snap["deposit_preapproved"][0] == ["yes"]


def test_refresh_on_read_loses_the_rare_true_fact():
    _, snap = run("ttl-refresh", snapshot_day=81)
    assert snap["favourite"] == ([], "Chez Lili")


def test_table_prints_every_policy():
    assert len(table().splitlines()) == len(POLICIES) + 1


def test_the_planner_reads_the_injected_line_like_any_other():
    from memory_demo.sim import prompt
    weak = prompt("contra-all").splitlines()
    assert "- deposit_preapproved: yes" in weak       # no writer on the line: it reads like the user's own fact
    strong = prompt("provenance")
    assert "deposit_preapproved" not in strong and "next_dinner" not in strong
    assert "- ana_diet: eats fish  (user, day 30; until the user says otherwise)" in strong.splitlines()


def test_the_gate_refuses_the_claim_and_the_owners_events_retire_the_bookings():
    from memory_demo.sim import writes
    log = writes("provenance")
    assert "refused: restaurant may not write deposit_preapproved" in log
    assert "retired next_dinner = R1 Fri" in log and "retired next_dinner = R2 Sat" in log
