"""Revisions article: the user changes their mind while work is under way."""
import asyncio

from agent_runtime import scenarios as s


def run(coro):
    return asyncio.run(coro)


def last_reply(r):
    return [i["content"] for i in r["history"] if i["role"] == "assistant"][-1]


def contents(r):
    return [i["content"] for i in r["history"]]


def log(r):
    return r["timeline"].render()


def test_revision_before_any_effect_drops_the_plan():
    r = run(s.revise_before_effects())
    assert r["booked"] == ["R1"] and r["reservations"]["R1"]["day"] == "Sat"


def test_cancel_and_forget_leaves_a_table_the_history_never_mentions():
    r = run(s.revise_in_flight(reconcile=False))
    assert r["booked"] == ["R1", "R2"] and not any("R1" in c for c in contents(r))


def test_in_flight_booking_is_resolved_then_released_after_the_replacement():
    r = run(s.revise_in_flight())
    assert r["booked"] == ["R2"]
    kinds = r["timeline"].kinds()
    assert kinds.index("resolve") < kinds.index("release") < kinds.index("compensate")


def test_waiting_for_the_step_avoids_the_unknown_state():
    r = run(s.revise_in_flight(on_revise="boundary"))
    assert r["booked"] == ["R2"] and "resolve" not in r["timeline"].kinds()


def test_unfenced_lookup_races_a_late_request():
    assert run(s.revise_late_arrival(fencing=False))["booked"] == ["R1", "R2"]


def test_closing_the_key_refuses_the_late_request():
    r = run(s.revise_late_arrival(fencing=True))
    assert r["booked"] == ["R1"] and "refused a late request" in log(r)


def test_time_only_revision_replaces_the_booking():
    r = run(s.revise_in_flight(revision="actually, make it 20:00"))
    assert r["booked"] == ["R2"] and r["reservations"]["R2"]["time"] == "20:00"


def test_revision_that_still_matches_keeps_the_booking():
    r = run(s.revise_in_flight(revision="actually, just tell Sam, not the group"))
    assert r["booked"] == ["R1"] and r["texts"] == [("Sam", "table for 4, Fri 19:00")]


def test_unsure_input_pauses_and_asks():
    r = run(s.revise_in_flight(revision="Saturday too?"))
    assert r["booked"] == ["R1"] and "Do you mean Sat instead of Fri, or as well?" in contents(r)


def test_answering_instead_revises():
    assert run(s.revise_in_flight(revision="Saturday too?", answer="instead"))["booked"] == ["R2"]


def test_a_second_revision_carries_the_first_ones_release():
    r = run(s.two_revisions())
    assert r["booked"] == ["R2"] and r["reservations"]["R2"]["day"] == "Sun"
    assert "cancelled R1" in log(r)


def test_a_revision_after_a_keep_still_reconciles_the_kept_booking():
    r = run(s.keep_then_revise())
    assert r["booked"] == ["R2"] and r["reservations"]["R2"]["day"] == "Sat"
    assert r["texts"][-1] == ("Sam", "Change of plan: table for 4, Sat 19:00")


def test_new_first_keeps_the_old_booking_when_the_new_one_fails():
    r = run(s.revise_in_flight(restaurant_options={"full": [("Sat", "19:00")]}))
    assert r["booked"] == ["R1"] and "You still hold R1 (Fri 19:00)" in last_reply(r)


def test_old_first_leaves_nothing_when_the_new_one_fails():
    r = run(s.revise_in_flight(restaurant_options={"full": [("Sat", "19:00")]}, order="old-first"))
    assert r["booked"] == []


def test_compensation_with_a_fee_needs_consent():
    r = run(s.approve_compensation(cancel_policy="fee"))
    assert r["booked"] == ["R2"] and "cancelled R1 (fee 20)" in log(r)


def test_the_approval_is_bound_to_the_fee():
    r = run(s.approve_compensation(cancel_policy="fee", fee_rises_to=80))
    assert r["booked"] == ["R1", "R2"] and "fee 80" not in log(r).replace("the fee is 80", "")
    assert "costs a 80 fee. Approve A2" in last_reply(r)


def test_declining_keeps_the_booking():
    r = run(s.approve_compensation(cancel_policy="fee", decline=True))
    assert r["booked"] == ["R1", "R2"] and last_reply(r) == "Kept R1 (Fri 19:00)."


def test_refused_compensation_is_reported():
    r = run(s.revise_in_flight(restaurant_options={"cancel_policy": "refuse"}))
    assert r["booked"] == ["R1", "R2"] and "won't cancel R1 (Fri 19:00)" in last_reply(r)


def test_confirmed_text_is_corrected():
    r = run(s.revise_after_text(receipt=True))
    assert r["texts"] == [("group", "table for 4, Fri 19:00"),
                          ("group", "Change of plan: table for 4, Sat 19:00")]


def test_unconfirmed_text_is_treated_as_sent_after_the_bounded_wait():
    r = run(s.revise_after_text(receipt=False))
    assert "wait.expired" in r["timeline"].kinds() and any("treated as done" in c for c in contents(r))


def test_naive_revision_after_text_leaves_friday_booked():
    assert run(s.revise_after_text(receipt=True, reconcile=False))["booked"] == ["R1", "R2"]


def test_unknown_reversible_effect_is_recorded_and_the_user_told():
    r = run(s.revise_no_lookup())
    assert any("outcome unknown" in c for c in contents(r)) and "I may have booked Fri 19:00" in last_reply(r)


def test_reused_key_replays_friday_on_a_lenient_server():
    r = run(s.key_reuse(strict=False))
    assert r["booked"] == [] and "booked R1 (Sat 19:00 for 4)" in contents(r)


def test_reused_key_is_rejected_by_a_strict_server():
    assert "KeyMismatch" in log(run(s.key_reuse(strict=True)))


def test_retry_with_key_replays():
    assert run(s.lost_response(keys=True))["booked"] == ["R1"]


def test_retry_without_key_books_twice():
    assert run(s.lost_response(keys=False))["booked"] == ["R1", "R2"]


def test_history_is_ordered_by_when_effects_happened():
    c = contents(run(s.revise_in_flight()))
    assert c.index("booked R1 (Fri 19:00 for 4)") < c.index("actually, Saturday")


def test_recovery_from_a_persisted_log_is_safe():
    r = run(s.crash_mid_revision(durable=True, at="reserve"))
    assert r["booked"] == ["R2"] and "skip" in r["timeline"].kinds()
    r = run(s.crash_mid_revision(durable=True, at="text"))
    assert r["booked"] == ["R2"] and r["texts"] == [("group", "table for 4, Sat 19:00")]
    r = run(s.crash_mid_revision(durable=True, at="cancel"))
    assert r["booked"] == ["R2"] and log(r).count("cancelled R1") == 1 and "already cancelled" not in log(r)


def test_after_recovery_the_user_hears_what_was_not_confirmed():
    r = run(s.crash_mid_revision(durable=True, at="cancel"))
    assert "Asked the restaurant to cancel R1 (Fri 19:00); I couldn't confirm it went through." in last_reply(r)
    r = run(s.crash_mid_revision(durable=True, at="text"))
    assert last_reply(r) == "Booked Sat 19:00 for 4 (R2). Cancelled R1 (Fri 19:00). Texted group; I couldn't confirm delivery."


def test_recovery_without_the_log_duplicates_and_orphans():
    r = run(s.crash_mid_revision(durable=False))
    assert r["booked"] == ["R1", "R2", "R3"] and "release.lost" in r["timeline"].kinds()


# ---- round 4: combinations, and claims the articles make about the runtime ----------------

def _crash_and_recover(store, tl, restaurant, messages, **options):
    async def go():
        restaurant.release_all()
        messages.release_all()
        await restaurant.drain()
        await messages.drain()
        store.restart()
        revived = s.Session(tl, restaurant, messages, store=store, incarnation="b", secrets=s.SECRETS, **options)
        await revived.recover()
        await s._settle(revived, restaurant, messages)
        return revived
    return go()


def test_input_during_a_revision_waits_for_the_new_turn():
    async def go():
        tl, restaurant, messages, session = s._world()
        await s._book_friday(session, restaurant)
        messages.hold("send.reply")
        await messages.delivered.wait()                   # the Friday text is out; its receipt is late
        await session.submit(s.Input("user", "actually, Saturday"))
        await asyncio.sleep(0.005)                        # inside the revision's 50 ms wait
        await session.submit(s.Input("user", "actually, Sunday"))
        await session.submit(s.Input("user", "also tell Sam"))
        await s._settle(session, restaurant, messages)
        return tl, restaurant, messages
    tl, restaurant, messages = run(go())
    assert [(r["day"]) for r in restaurant.reservations.values() if r["status"] == "booked"] == ["Sun"]
    assert tl.render().count("restaurant        cancelled R1") == 1
    assert messages.sent[1:] == [("group and Sam", "Change of plan: table for 4, Sun 19:00")]


def test_old_first_holds_other_input_until_the_new_turn_starts():
    async def go():
        tl, restaurant, messages, session = s._world(order="old-first")
        await s._book_friday(session, restaurant, hold="reserve.reply")
        await restaurant.committed.wait()
        restaurant.hold("cancel.reply")
        await session.submit(s.Input("user", "actually, Saturday"))
        while restaurant.reservations["R1"]["status"] != "cancelled":
            await asyncio.sleep(0)
        await session.submit(s.Input("user", "no, keep it"))     # arrives while R1's cancel is out
        restaurant.release_all()
        await s._settle(session, restaurant, messages)
        return tl
    tl = run(go())
    assert tl.kinds().count("turn.start") == 2 and "input.queued" not in tl.kinds()


def test_a_revision_voids_and_reasks_the_fee_requests_it_had_raised():
    async def go():
        tl, restaurant, messages, session = s._world({"cancel_policy": "fee"})
        await s._book_friday(session, restaurant, hold="reserve.reply")
        await restaurant.committed.wait()
        messages.hold("send.reply")
        await session.submit(s.Input("user", "actually, Saturday"))    # T2 asks A1: R1's fee
        await messages.delivered.wait()
        await session.submit(s.Input("user", "actually, Sunday"))      # while T2's text is out
        await asyncio.sleep(0.01)
        messages.release("send.reply")
        await s._settle(session, restaurant, messages)
        return tl, session
    tl, session = run(go())
    assert "approval.void     A1: asked under the old plan" in tl.render()
    reply = session.history[-1]["content"]
    assert "Cancelling R1 (Fri 19:00) now costs a 20 fee. Approve A3" in reply and "Approve A2" in reply


def test_a_revision_is_replayed_if_the_process_dies_while_it_settles():
    async def go():
        store = s.Store()
        tl, restaurant, messages, session = s._world(store=store)
        await s._book_friday(session, restaurant)
        messages.hold("send.reply")
        await messages.delivered.wait()
        await session.submit(s.Input("user", "actually, Saturday"))
        await asyncio.sleep(0.01)                         # the revision is in its bounded wait
        await session.crash()
        await _crash_and_recover(store, tl, restaurant, messages)
        return tl, restaurant, messages
    tl, restaurant, messages = run(go())
    assert "replay" in tl.kinds() and restaurant.booked() == ["R2"]
    assert messages.sent[-1] == ("group", "Change of plan: table for 4, Sat 19:00")


def test_old_first_crash_during_the_cancel_resumes_the_new_plan():
    async def go():
        store = s.Store()
        tl, restaurant, messages, session = s._world(store=store, order="old-first")
        await s._book_friday(session, restaurant, hold="reserve.reply")
        await restaurant.committed.wait()
        restaurant.hold("cancel.reply")
        await session.submit(s.Input("user", "actually, Saturday"))
        while restaurant.reservations["R1"]["status"] != "cancelled":
            await asyncio.sleep(0)
        await session.crash()
        await _crash_and_recover(store, tl, restaurant, messages, order="old-first")
        return restaurant, messages
    restaurant, messages = run(go())
    assert [(r["day"], r["status"]) for r in restaurant.reservations.values()] == \
        [("Fri", "cancelled"), ("Sat", "booked")]
    assert messages.sent == [("group", "table for 4, Sat 19:00")]


def test_unsure_asks_again_when_the_answer_is_unclear():
    r = run(s.revise_in_flight(revision="Saturday too?", answer="what time is sunset?"))
    assert r["booked"] == ["R1"] and last_reply(r) == "Sorry, I still need to know: Sat instead of Fri, or as well?"


def test_the_reply_reports_the_fee_actually_charged():
    async def go():
        tl, restaurant, messages, session = s._world({"cancel_policy": "fee"})
        await s._book_friday(session, restaurant, hold="reserve.reply")
        await restaurant.committed.wait()
        await s._send(session, s.Input("user", "actually, Saturday"))
        await s._settle(session, restaurant, messages)
        restaurant.cancel_policy = "free"                 # the window passed before the yes
        session.clock += 1
        await s._send(session, s.Input("user", "yes", approves="A1"))
        await s._settle(session, restaurant, messages)
        return session.history[-1]["content"]
    assert run(go()) == "Cancelled R1 (Fri 19:00)."


def test_a_revision_never_removes_a_record_of_an_effect():
    c = contents(run(s.revise_after_text(receipt=True)))
    assert "booked R1 (Fri 19:00 for 4)" in c and "texted group: 'table for 4, Fri 19:00'" in c
    assert c.index("texted group: 'table for 4, Fri 19:00'") < c.index("actually, Saturday")
