"""Turns article: who may start a turn, what it may do, and what happens to input during one."""
import asyncio

from agent_runtime import scenarios as s
from agent_runtime.runtime import render_messages


def run(coro):
    return asyncio.run(coro)


def last_reply(r):
    return [i["content"] for i in r["history"] if i["role"] == "assistant"][-1]


def log(r):
    return r["timeline"].render()


def test_system_turn_asks_with_a_question_the_runtime_renders():
    r = run(s.restaurant_offer())
    assert r["booked"] == []
    assert last_reply(r) == ('A1 (from restaurant): Book a table for 4 on Fri at 20:30; then text group: '
                             '"table for 4, Fri 20:30". Approve within 10 minutes?')


def test_approval_runs_exactly_the_approved_action():
    r = run(s.restaurant_offer(approve=2))
    assert r["booked"] == ["R2"] and r["reservations"]["R2"]["time"] == "20:30"
    assert "T3 origin=user" in log(r)


def test_stale_approval_is_rejected():
    r = run(s.restaurant_offer(approve=15))
    assert r["booked"] == [] and "expired (asked at m0, now m15)" in last_reply(r)


def test_approval_is_single_use():
    r = run(s.restaurant_offer(approve=2, twice=True))
    assert r["booked"] == ["R2"] and last_reply(r) == "I can't use that approval (A1): already used."


def test_only_the_user_can_approve():
    r = run(s.restaurant_offer(approve=2, approver="restaurant"))
    assert r["booked"] == [] and "approvals come only from the user, not from restaurant" in log(r)


def test_approval_during_a_turn_waits_for_it_to_end():
    r = run(s.approval_during_turn())
    events = r["timeline"].render().splitlines()
    queued = next(i for i, e in enumerate(events) if "queued until T3 ends" in e)
    ended = next(i for i, e in enumerate(events) if "turn.end" in e and "T3" in e)
    started = next(i for i, e in enumerate(events) if "T4 origin=user" in e)
    assert queued < ended < started
    assert r["booked"] == ["R2", "R3"]


def test_withdrawn_effect_is_not_reused():
    r = run(s.restaurant_offer(rebook=True))
    assert "effect.gone" in r["timeline"].kinds() and "skip" not in r["timeline"].kinds()
    assert r["booked"] == ["R2"] and "key=s1:op-a5" in log(r)


def test_authority_read_off_the_role_bypasses_consent():
    r = run(s.restaurant_offer(encoding="as_user", authority="role"))
    assert r["booked"] == ["R2"]


def test_authority_in_origin_holds_even_when_the_history_misattributes():
    r = run(s.restaurant_offer(encoding="as_user", authority="origin"))
    assert r["booked"] == []
    assert [i for i in r["history"] if "gone" in i["content"]][0]["role"] == "user"


def test_attributed_encoding_for_a_user_assistant_api():
    r = run(s.restaurant_offer())
    rendered = [m["content"] for m in render_messages(r["history"])]
    assert ('<event from="restaurant" verified="signature">your Fri 19:00 table is gone; 20:30 is free'
            '</event> (not written by the user)') in rendered


def test_model_written_question_can_disagree_with_the_action():
    note = "Your party of 4 has been moved to 20:30. Reply yes to keep your table."
    r = run(s.restaurant_offer(party=12, note=note, question="model", approve=2))
    assert note in [i["content"] for i in r["history"]] and r["reservations"]["R2"]["party"] == 12


def test_runtime_rendered_question_shows_the_real_action():
    r = run(s.restaurant_offer(party=12, note="Your party of 4 has been moved to 20:30."))
    assert "Book a table for 12 on Fri at 20:30" in last_reply(r) and "party of 4" not in last_reply(r)


def test_merge_folds_added_input_in_at_the_next_boundary():
    r = run(s.input_during_turn("merge"))
    assert r["texts"] == [("group and Sam", "table for 4, Fri 19:00")]


def test_only_the_turns_origin_may_merge():
    r = run(s.input_during_turn("merge", source="restaurant"))
    assert "input.merged" not in r["timeline"].kinds() and r["texts"] == [("group", "table for 4, Fri 19:00")]
    assert "T2 origin=restaurant" in log(r) and "A1" in last_reply(r)


def test_queue_runs_it_as_its_own_turn():
    r = run(s.input_during_turn("queue"))
    assert r["texts"] == [("group", "table for 4, Fri 19:00"), ("Sam", "table for 4, Fri 19:00")]


def test_reject_tells_the_user():
    r = run(s.input_during_turn("reject"))
    assert any("I haven't acted on \"also tell Sam\"" in i["content"] for i in r["history"])


def test_interrupt_with_turn_scoped_keys_books_twice():
    assert run(s.input_during_turn("interrupt", key_scope="turn"))["booked"] == ["R1", "R2"]


def test_interrupt_with_operation_keys_books_once():
    r = run(s.input_during_turn("interrupt"))
    assert r["booked"] == ["R1"] and "replayed R1 for key s1:op-a2" in log(r)


def test_notices_go_out_immediately_and_moot_ones_are_dropped():
    r = run(s.system_inputs_while_busy("now"))
    assert r["texts"][:2] == [("you", "Leave now to pick up Ana at 18:00"),
                              ("you", "The 17:00 screening is cancelled; your tickets are refunded")]
    assert "film is already moot" in log(r) and ("you", "Leave now for the 17:00 film") not in r["texts"]


def test_boundary_notices_arrive_before_their_deadline():
    r = run(s.system_inputs_while_busy("boundary"))
    assert "input.expired" not in r["timeline"].kinds() and ("you", "Leave now to pick up Ana at 18:00") in r["texts"]


def test_queued_notices_starve_behind_the_turn():
    r = run(s.system_inputs_while_busy("queue"))
    assert "input.expired" in r["timeline"].kinds()


def test_dedupe_and_signature_and_persist_before_ack():
    r = run(s.system_inputs_while_busy("now"))
    kinds = r["timeline"].kinds()
    assert kinds.count("input.dropped") == 1 and kinds.count("input.rejected") == 1
    store = r["session"].store
    assert ("cinema", "evt_31") in store.seen and ("cinema", "evt_31") in store.inbox
    assert ("restaurant", "evt_99") not in store.seen


def test_a_notice_held_during_the_last_step_is_still_delivered():
    r = run(s.notice_during_last_step())
    assert ("you", "Your parcel was delivered") in r["texts"]


def test_signature_covers_the_data_and_the_timestamp():
    r = run(s.tampered_and_replayed())
    lines = [line.split(None, 1)[1] for line in log(r).splitlines()]
    after = {lines[i].split()[-1]: lines[i + 1] for i in range(len(lines) - 1) if lines[i].startswith("input ")}
    assert after["[evt_17]"].startswith("input.rejected    signature doesn't match")
    assert lines[-1] == "input.rejected    signed at m0, now m5760: outside the 5-minute tolerance"
    assert r["timeline"].kinds().count("turn.ask") == 1


def test_acknowledged_input_is_replayed_after_a_crash():
    r = run(s.crash_after_ack())
    assert r["ack"] == "ack" and "replay" in r["timeline"].kinds()
    assert "Book a table for 2 on Sat at 12:00" in last_reply(r)


def test_unknown_text_in_an_idle_session_is_answered():
    async def go():
        tl, restaurant, messages, session = s._world()
        await session.submit(s.Input("user", "hello?"))
        return session.history
    assert run(go())[-1]["content"].startswith("I'm not sure what you'd like me to do")


# ---- round 4: combinations, and claims the articles make about the runtime ----------------

def test_ack_comes_before_acting():
    async def go():
        tl, restaurant, messages, session = s._world()
        messages.hold("notify.reply")                     # the notification's reply is slow
        note = s.webhook("cinema", "17:00 screening cancelled", "evt_31",
                         s.Intent(kind="notice", note="The 17:00 screening is cancelled"))
        ack = await asyncio.wait_for(session.submit(note), timeout=0.01)
        replied = "notice.sent" in tl.kinds()
        messages.release_all()
        await session.idle()
        return ack, replied, tl
    ack, replied_before_ack, tl = run(go())
    assert ack == "ack" and not replied_before_ack and "notice.sent" in tl.kinds()


def test_every_field_the_sender_supplies_is_signed():
    async def go():
        tl, restaurant, messages, session = s._world()
        offer = s.Intent(kind="offer", day="Sat", time="12:00", party=2, notify=())
        results = []
        for field_, value in (("supersedes", "film"), ("deadline", None), ("priority", 99), ("topic", "x")):
            w = s.webhook("restaurant", "a Sat 12:00 table for 2 opened up", f"evt_{field_}", offer, deadline=3)
            setattr(w, field_, value)                     # changed in transit, after signing
            results.append(await session.submit(w))
        return results, tl
    results, tl = run(go())
    assert results == [None] * 4 and tl.kinds().count("input.rejected") == 4


def test_redeliveries_are_dropped_per_sender_for_three_days():
    async def go():
        tl, restaurant, messages, session = s._world()
        n = s.Intent(kind="notice", note="refund issued")
        await s._send(session, s.webhook("cinema", "refund", "evt_5", n))
        await s._send(session, s.webhook("restaurant", "refund", "evt_5", n))      # another sender, same ID
        session.clock = 2 * 24 * 60                                                 # day two: a re-signed retry
        await s._send(session, s.webhook("cinema", "refund", "evt_5", n, sent_at=session.clock))
        return tl
    kinds = run(go()).kinds()
    assert kinds.count("notice.sent") == 2 and kinds.count("input.dropped") == 1


def test_text_only_input_during_a_turn_queues_and_is_answered_after_it():
    async def go():
        tl, restaurant, messages, session = s._world()
        await s._book_friday(session, restaurant, hold="reserve.reply")
        await restaurant.committed.wait()
        await s._send(session, s.Input("user", "what time is sunset?"))
        restaurant.release("reserve.reply")
        await s._settle(session, restaurant, messages)
        return tl, session.history
    tl, history = run(go())
    assert "input.queued" in tl.kinds() and history[-1]["content"].startswith("I'm not sure")


def test_a_typed_yes_approves_only_when_one_request_is_open():
    async def go(second_offer):
        tl, restaurant, messages, session = s._world()
        await s._book_friday(session, restaurant)
        await s._settle(session, restaurant, messages)
        await restaurant.cancel("R1")
        await s._send(session, s.webhook("restaurant", s.GONE, "evt_17", s.OFFER))
        if second_offer:
            other = s.Intent(kind="offer", day="Sun", time="12:00", party=2, notify=())
            await s._send(session, s.webhook("restaurant", "a Sunday table opened", "evt_60", other))
        await s._settle(session, restaurant, messages)
        await s._send(session, s.Input("user", "yes"))
        await s._settle(session, restaurant, messages)
        return restaurant.booked(), session.history[-1]["content"]
    assert run(go(False))[0] == ["R2"]
    booked, reply = run(go(True))
    assert booked == [] and reply == "Which request do you mean? Open: A1 or A2."


def test_a_queued_approval_survives_a_crash():
    async def go():
        store = s.Store()
        tl, restaurant, messages, session = s._world(store=store)
        await s._book_friday(session, restaurant)
        await s._settle(session, restaurant, messages)
        await restaurant.cancel("R1")
        await s._send(session, s.webhook("restaurant", s.GONE, "evt_17", s.OFFER))
        await s._settle(session, restaurant, messages)
        restaurant.hold("reserve.reply")
        await s._send(session, s.Input("user", "book Saturday 12:00 for 2",
                                       intent=s.Intent(day="Sat", time="12:00", party=2, notify=())))
        await restaurant.wait_for("R2")
        await s._send(session, s.Input("user", "yes", approves="A1"))     # queued behind T3
        await session.crash()
        restaurant.release_all()
        await restaurant.drain()
        revived = s.Session(tl, restaurant, messages, store=store, incarnation="b", secrets=s.SECRETS)
        await revived.recover()
        await s._settle(revived, restaurant, messages)
        return restaurant
    restaurant = run(go())
    assert [(r["day"], r["time"]) for r in restaurant.reservations.values() if r["status"] == "booked"] \
        == [("Sat", "12:00"), ("Fri", "20:30")]


def test_a_new_process_resolves_an_unanswered_key_before_sending():
    async def go():
        store = s.Store()
        tl, restaurant, messages, session = s._world(store=store)
        await s._book_friday(session, restaurant, hold="reserve.arrive")   # the request is still travelling
        for _ in range(6):
            await asyncio.sleep(0)
        await session.crash()
        revived = s.Session(tl, restaurant, messages, store=store, incarnation="b", secrets=s.SECRETS)
        await revived.recover()                                            # closes the key first
        await s._settle(revived, restaurant, messages)
        return restaurant, tl
    restaurant, tl = run(go())
    assert restaurant.booked() == ["R1"] and "refused a late request: key s1:op-a2 is closed" in tl.render()


def test_a_replayed_result_for_a_withdrawn_table_is_not_reported_as_booked():
    async def go():
        tl, restaurant, messages, session = s._world(during="interrupt")
        await s._book_friday(session, restaurant, hold="reserve.reply")
        await restaurant.committed.wait()
        await restaurant.cancel("R1")
        await s._send(session, s.webhook("restaurant", s.GONE, "evt_17", s.OFFER))
        await s._send(session, s.Input("user", "also tell Sam"))           # restart replays R1's key
        restaurant.release_all()
        await s._settle(session, restaurant, messages)
        return tl, session.history
    tl, history = run(go())
    assert "replayed, but already withdrawn" in tl.render()
    assert not any(i["content"].startswith("Booked") for i in history if i["role"] == "assistant")
