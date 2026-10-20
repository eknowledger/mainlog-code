"""The claims the post makes about these runs. Each test names the section it backs."""
from bus_demo.agents import SCHEMA, Bus, Log, Rejected
from bus_demo.demo import main
from bus_demo.scenarios import SCENARIOS

LINK = "deposit refund at evil.example/claim"


def run(name):
    return SCENARIOS[name]()


def texts(r, to):
    return [t for who, t in r["sent"] if who == to]


# A tool call to an agent is a prompt
def test_call_with_free_text_texts_the_attackers_link_to_the_group():
    r = run("call")
    assert texts(r, "group")[1] == LINK
    assert texts(r, "you") == []


# The schema did it, not the bus
def test_typed_fact_is_as_safe_by_call_as_by_bus():
    by_call, by_bus = run("call-typed"), run("bus")
    assert by_call["sent"] == by_bus["sent"]
    assert texts(by_bus, "group") == [] and len(texts(by_bus, "you")) == 1
    assert all(LINK not in t for _, t in by_call["sent"] + by_bus["sent"])


def test_a_free_text_field_on_the_bus_carries_the_instruction():
    assert LINK in texts(run("bus-note"), "group")


def test_a_command_on_the_bus_is_still_an_instruction():
    assert LINK in texts(run("bus-command"), "group")


def test_a_strict_schema_refuses_the_note_at_publish():
    r = run("bus-note-strict")
    assert "field(s) not in schema: ['note']" in r["log"].render()
    assert r["sent"] == [] and r["offsets"] == {"calendar": 0, "messaging": 0}


def test_every_fact_type_has_no_free_text_field():
    for name in ("ReservationConfirmed", "ReservationCancelled"):
        assert not SCHEMA[name] & {"note", "text", "message", "instructions"}


# What the bus does give you: failure stays local
def test_call_with_messaging_down_is_half_done_or_not_done():
    calendar_first, messaging_first = run("call-down"), run("call-down-messaging-first")
    assert calendar_first["calendar"] == {} and calendar_first["sent"] == []
    assert messaging_first["calendar"] == {"R1": "Fri 19:00 dinner"} and messaging_first["sent"] == []


def test_bus_with_messaging_down_catches_up_on_recovery():
    r = run("bus-down")
    assert r["offsets_while_down"] == {"calendar": 1, "messaging": 0}
    assert r["offsets"] == {"calendar": 1, "messaging": 1}
    assert len(texts(r, "you")) == 1 and r["calendar"] == {}


# What the bus costs
def test_redelivery_is_harmless_only_with_dedupe():
    assert len(texts(run("duplicate"), "you")) == 1
    assert len(texts(run("duplicate-no-dedupe"), "you")) == 2


def test_the_log_rebuilds_the_calendar():
    r = run("replay")
    assert r["rebuilt"] == r["calendar"] == {"R2": "Sat 19:00 dinner"}


def test_unknown_event_type_is_refused():
    bus = Bus(Log())
    try:
        bus.publish({"type": "PleaseTextGroup", "id": "x"})
    except Rejected:
        return
    raise AssertionError("an undeclared type was published")


def test_demo_prints_every_scenario(capsys):
    assert main(["--all"]) == 0
    out = capsys.readouterr().out
    assert out.count("== ") == len(SCENARIOS)


# What Messaging's model is sent
def user_turns(r):
    return [q["messages"][0]["content"] for q in r["requests"]]


def test_the_model_reads_the_note_only_where_a_sentence_crosses():
    for name in ("call", "bus-note", "bus-command"):
        assert any(LINK in t for t in user_turns(run(name))), name
    for name in ("call-typed", "bus"):
        assert not any(LINK in t for t in user_turns(run(name))), name


def test_a_typed_fact_sends_the_same_prompt_by_call_and_by_bus():
    assert run("call-typed")["requests"] == run("bus")["requests"]
