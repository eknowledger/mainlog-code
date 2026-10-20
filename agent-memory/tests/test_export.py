"""The lab's runs file: current with sim.py, and the same numbers as `python -m memory_demo` at the post's settings."""
import json
import pathlib

from export_runs import export
from memory_demo.sim import DISUSE_DAYS, POLICIES, TTL_DAYS, prompt, table

RUNS = pathlib.Path(__file__).resolve().parent.parent / "memory-runs.json"


def committed():
    return json.loads(RUNS.read_text())


def at_post_settings(policy):
    key = {"ttl-refresh": f"ttl-refresh:{TTL_DAYS}", "ttl-fixed": f"ttl-fixed:{TTL_DAYS}",
           "disuse-30": f"disuse-30:{DISUSE_DAYS}"}.get(policy, policy)
    return committed()["runs"][key]


def test_the_file_is_what_export_writes():
    assert committed() == export()


def test_the_lab_numbers_match_the_table_row_for_row():
    rows = table().splitlines()[1:]
    for (policy, label), row in zip(POLICIES.items(), rows):
        r = at_post_settings(policy)
        assert row == f"{label:48} {r['reads']:>5} {r['stale']:>5} {r['missing']:>7} {r['injected']:>8}"


def test_the_strip_adds_up_to_the_counts():
    for r in committed()["runs"].values():
        letters = "".join(r["strip"].values())
        assert (letters.count("s"), letters.count("m"), letters.count("i")) == (r["stale"], r["missing"],
                                                                                 r["injected"])
        assert len(letters.replace("-", "")) == r["reads"] == 89


def test_the_day_81_memories_are_the_prompts_lines():
    for policy in POLICIES:
        lines = prompt(policy).splitlines()
        memories = at_post_settings(policy)["memories"]
        start = lines.index(memories[0])
        assert lines[start:start + len(memories)] == memories


def test_the_lessons_the_post_states():
    runs = committed()["runs"]
    wrong = {k: r["stale"] + r["missing"] + r["injected"] for k, r in runs.items()}
    # Refreshed on read, any TTL longer than the 3 days between reads keeps the stale facts alive.
    assert {wrong[f"ttl-refresh:{t}"] for t in (7, 14, 30)} == {79}
    # A longer fixed TTL trades missing reads for stale ones.
    fixed = [(runs[f"ttl-fixed:{t}"]["stale"], runs[f"ttl-fixed:{t}"]["missing"]) for t in (3, 7, 14, 30)]
    assert [s for s, _ in fixed] == sorted(s for s, _ in fixed) and [m for _, m in fixed] == sorted(
        (m for _, m in fixed), reverse=True)
    # No disuse window inside the run deletes the stale facts; only one longer than the run keeps the favourite.
    assert {wrong[f"disuse-30:{w}"] for w in (7, 30, 60)} == {79} and wrong["disuse-30:90"] == wrong["keep"] == 78
    assert runs["contra-all"]["injected"] == 29
    assert wrong["provenance"] == 0 and wrong["provenance-misfiled"] == 28
