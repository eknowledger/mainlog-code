"""The handoff article's claims about this runtime: what each style carries, and what the receiver can do with it."""
import asyncio

from agent_runtime.handoff import STYLES, handoff_run, keeps, model_view, sizes


def run(style, delayed=False, **kw):
    return asyncio.run(handoff_run(style, delayed, **kw))


def tables(r):
    return len(r["yours"])


def log(r):
    return r["timeline"].render()


def test_full_transcript_is_right_only_when_the_request_landed():
    assert tables(run("full")) == 1
    assert tables(run("full", delayed=True)) == 2


def test_removing_tools_leaves_only_the_models_own_prose():
    # The filter drops the call; the best a careful reader can do is notice "Booking it now" and look up by details.
    assert tables(run("no-tools")) == 1 and tables(run("no-tools", delayed=True)) == 2
    assert 'reads "Booking it now", and no result' in log(run("no-tools"))


def test_a_brief_that_omits_the_call_books_twice_either_way():
    assert tables(run("brief-omits")) == 2 and tables(run("brief-omits", delayed=True)) == 2
    assert "sees no booking in progress" in log(run("brief-omits"))


def test_what_the_model_reads_never_has_the_key():
    for style in STYLES:
        words = model_view(style).split("-- not for the model")[0]
        assert "s1:op-a2" not in words, style
    assert '"key": "s1:op-a2"' in model_view("record")


def test_the_receiver_promises_only_what_it_was_told():
    assert not run("last-pair")["history"][-1]["content"].endswith("Texting the group.")
    assert keeps("last-pair")["text once confirmed"] is None
    assert run("record")["history"][-1]["content"].endswith("Texting the group.")


def test_only_the_record_carries_the_key_and_the_origin():
    for style in STYLES:
        k = keeps(style)
        assert (k["the request's key"] is not None) == (style == "record"), style
        assert (k["whose authority"] is not None) == (style == "record"), style


def test_every_words_only_style_books_twice_when_the_request_was_delayed():
    for style in STYLES:
        if style != "record":
            assert tables(run(style, delayed=True)) == 2, style


def test_the_record_gives_one_table_either_way_and_refuses_the_late_request():
    assert tables(run("record")) == 1
    late = run("record", delayed=True)
    assert tables(late) == 1
    assert "refused a late request: key s1:op-a2 is closed" in log(late)


def test_the_key_is_never_in_the_words():
    for style, (_, make) in STYLES.items():
        h = make()
        assert "s1:op-a2" not in str(h["words"]), style


def test_the_record_cuts_the_words_and_adds_the_state():
    full_words, _ = sizes(STYLES["full"][1]())
    words, record = sizes(STYLES["record"][1]())
    assert words < full_words / 10 and record > 0


def test_lookup_by_details_adopts_someone_elses_booking():
    r = run("full", delayed=True, someone_else=True)
    assert "R1" not in r["yours"]
    assert r["history"][-1]["content"].startswith("Booked Fri 19:00 for 4 (R1)")
