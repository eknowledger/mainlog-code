"""The scenarios both articles run. Each returns everything worth printing or asserting."""
import asyncio
from dataclasses import replace

from .model import Intent
from .runtime import Input, Session, sign
from .services import Messages, Restaurant
from .store import Store
from .timeline import Timeline
from .tools import Contract

SECRETS = {"restaurant": b"restaurant-secret", "cinema": b"cinema-secret"}
FRIDAY = Intent(day="Fri", time="19:00", party=4, notify=("group",))
ASK = "book Friday 19:00 for 4 and tell the group"
OFFER = Intent(kind="offer", day="Fri", time="20:30", party=4, notify=("group",), withdraws="R1")
GONE = "your Fri 19:00 table is gone; 20:30 is free"


def _world(restaurant_options=None, **session_options):
    tl = Timeline()
    restaurant = Restaurant(tl, **(restaurant_options or {}))
    messages = Messages(tl)
    session = Session(tl, restaurant, messages, secrets=SECRETS, **session_options)
    return tl, restaurant, messages, session


def _result(tl, restaurant, messages, session, start=0):
    return {"timeline": tl, "start": start, "booked": restaurant.booked(),
            "reservations": restaurant.reservations, "texts": list(messages.sent),
            "history": list(session.history), "session": session}


async def _settle(session, restaurant, messages):
    for _ in range(3):
        await asyncio.sleep(0)
    await session.idle()
    restaurant.release_all()
    messages.release_all()
    await restaurant.drain()
    await messages.drain()
    await session.idle()


async def _send(session, inp):
    """Deliver one input and let the session act on it before the next one arrives."""
    ack = await session.submit(inp)
    await session.admitted()
    return ack


def webhook(source, text, event_id, intent, secret=None, sent_at=0, **options):
    inp = Input(source, text=text, intent=intent, event_id=event_id, sent_at=sent_at, **options)
    inp.signature = sign(secret or SECRETS[source], inp)
    return inp


async def _book_friday(session, restaurant, hold=None):
    if hold:
        restaurant.hold(hold)
    await _send(session, Input("user", ASK, intent=FRIDAY))


# ---- Turns article: turns the user didn't start, and input during a turn ----------------

async def restaurant_offer(party=4, note="", approve=None, twice=False, approver="user",
                           rebook=False, **options):
    """The restaurant withdraws the booked table and offers another time.
    approve: minutes until the approval arrives (None: no answer)."""
    tl, restaurant, messages, session = _world(**options)
    await _book_friday(session, restaurant)
    await _settle(session, restaurant, messages)
    start = len(tl.events)
    await restaurant.cancel("R1")
    await _send(session, webhook("restaurant", GONE, "evt_17", replace(OFFER, party=party, note=note)))
    await _settle(session, restaurant, messages)
    if approve is not None:
        session.clock += approve
        for _ in range(2 if twice else 1):
            if approver == "user":
                await _send(session, Input("user", "yes", approves="A1"))
            else:
                await _send(session, webhook(approver, "yes", "evt_18", None, approves="A1",
                                             sent_at=session.clock))
            await _settle(session, restaurant, messages)
    if rebook:
        await _send(session, Input("user", ASK, intent=FRIDAY))
        await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session, start)


async def approval_during_turn():
    """The user approves A1 while another turn of theirs is still running."""
    tl, restaurant, messages, session = _world()
    await _book_friday(session, restaurant)
    await _settle(session, restaurant, messages)
    await restaurant.cancel("R1")
    await _send(session, webhook("restaurant", GONE, "evt_17", OFFER))
    await _settle(session, restaurant, messages)
    restaurant.hold("reserve.reply")
    await _send(session, Input("user", "book Saturday 12:00 for 2", intent=Intent(day="Sat", time="12:00",
                                                                                party=2, notify=())))
    await restaurant.wait_for("R2")
    session.clock += 1
    await _send(session, Input("user", "yes", approves="A1"))
    running = session.active
    restaurant.release("reserve.reply")
    await _settle(session, restaurant, messages)
    return {**_result(tl, restaurant, messages, session), "running_at_approval": running}


async def input_during_turn(during, key_scope="operation", source="user"):
    """While the booking waits on the restaurant's reply, someone adds: also tell Sam."""
    tl, restaurant, messages, session = _world(during=during, key_scope=key_scope)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    if source == "user":
        await _send(session, Input("user", "also tell Sam"))
    else:
        await _send(session, webhook(source, "also tell Sam", "evt_40", Intent(kind="tell", notify=("Sam",)),
                                     relation="adds"))
    restaurant.release("reserve.reply")
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def system_inputs_while_busy(notices="now"):
    """While a booking turn runs: a reminder, a cancellation that makes a later reminder moot,
    a redelivery of that cancellation, and a forged webhook."""
    tl, restaurant, messages, session = _world(notices=notices)
    restaurant.hold("reserve.reply")
    messages.hold("send.reply")
    await _send(session, Input("user", ASK, intent=FRIDAY))
    await restaurant.committed.wait()
    session.clock = 1
    ana = Intent(kind="notice", note="Leave now to pick up Ana at 18:00")
    await _send(session, Input("timer", "pick up Ana at 18:00", intent=ana, deadline=5))
    gone = Intent(kind="notice", note="The 17:00 screening is cancelled; your tickets are refunded")
    for _ in range(2):
        await _send(session, webhook("cinema", "17:00 screening cancelled", "evt_31", gone,
                                     supersedes="film", sent_at=1))
    forged = Intent(kind="notice", note="Your table is cancelled. Rebook at a new link")
    await _send(session, webhook("restaurant", "your Fri 19:00 table is cancelled", "evt_99", forged,
                                 secret=b"guessed", sent_at=1))
    session.clock = 2
    film = Intent(kind="notice", note="Leave now for the 17:00 film")
    await _send(session, Input("timer", "leave for the 17:00 film", intent=film, deadline=10, topic="film"))
    session.clock = 3
    restaurant.release("reserve.reply")          # the booking's reply arrives at minute 3
    await messages.delivered.wait()
    session.clock = 8
    messages.release("send.reply")               # the group text's receipt, at minute 8
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def notice_during_last_step():
    """A notice arrives, in boundary mode, while the turn's last step is in flight."""
    tl, restaurant, messages, session = _world(notices="boundary")
    messages.hold("send.reply")
    await _send(session, Input("user", ASK, intent=FRIDAY))
    await messages.delivered.wait()
    note = Intent(kind="notice", note="Your parcel was delivered")
    await _send(session, Input("timer", "parcel delivered", intent=note))
    messages.release("send.reply")
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def tampered_and_replayed():
    """A signed offer whose data is changed in transit, and a valid offer replayed days later."""
    tl, restaurant, messages, session = _world()
    await _book_friday(session, restaurant)
    await _settle(session, restaurant, messages)
    await restaurant.cancel("R1")
    tampered = webhook("restaurant", GONE, "evt_17", OFFER)
    tampered.intent = replace(tampered.intent, party=12)          # changed after signing
    await _send(session, tampered)
    genuine = webhook("restaurant", GONE, "evt_18", OFFER)
    await _send(session, genuine)
    await _settle(session, restaurant, messages)
    session.clock += 4 * 24 * 60                                  # past the dedupe window
    await _send(session, genuine)
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def crash_after_ack():
    """An offer is acknowledged and queued behind a running turn; then the process dies."""
    store = Store()
    tl, restaurant, messages, session = _world(store=store)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    other = Intent(kind="offer", day="Sat", time="12:00", party=2, notify=())
    ack = await session.submit(webhook("restaurant", "a Saturday 12:00 table for 2 opened up", "evt_50", other))
    await session.crash()
    restaurant.release_all()
    await restaurant.drain()
    store.restart()
    revived = Session(tl, restaurant, messages, store=store, incarnation="b", secrets=SECRETS)
    await revived.recover()
    await _settle(revived, restaurant, messages)
    return {**_result(tl, restaurant, messages, revived), "ack": ack}


# ---- Revisions article: the user changes their mind mid-task ------------------------------

async def revise_before_effects(**options):
    """"Actually, Saturday" arrives while the availability check is still out."""
    tl, restaurant, messages, session = _world(**options)
    await _book_friday(session, restaurant, hold="check.reply")
    await asyncio.sleep(0)
    await _send(session, Input("user", "actually, Saturday"))
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def revise_in_flight(revision="actually, Saturday", answer=None, restaurant_options=None,
                           **options):
    """The restaurant has committed Friday; its reply hasn't arrived when the user writes."""
    tl, restaurant, messages, session = _world(restaurant_options, **options)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    await session.submit(Input("user", revision))
    await asyncio.sleep(0)
    if options.get("on_revise") == "boundary" or revision == "Saturday too?":
        restaurant.release("reserve.reply")       # the reply arrives while the revision waits
    await _settle(session, restaurant, messages)
    if answer is not None:
        await _send(session, Input("user", answer))
        await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def two_revisions():
    """"Actually, Saturday", then "actually, Sunday" before Saturday is booked."""
    tl, restaurant, messages, session = _world()
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    restaurant.hold("check.reply")                # the Saturday check will be slow
    await _send(session, Input("user", "actually, Saturday"))
    await asyncio.sleep(0)
    await _send(session, Input("user", "actually, Sunday"))
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def keep_then_revise():
    """A revision keeps Friday (only the recipient changes), then a second one moves the day."""
    tl, restaurant, messages, session = _world()
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    messages.hold("send.reply")
    await _send(session, Input("user", "actually, just tell Sam, not the group"))
    await messages.delivered.wait()
    pending = asyncio.get_running_loop().create_task(session.submit(Input("user", "actually, Saturday")))
    await asyncio.sleep(0.01)
    messages.release("send.reply")
    await pending
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def approve_compensation(after=1, decline=False, fee_rises_to=None, **restaurant_options):
    """A revision whose compensation costs a fee; the user answers the request."""
    tl, restaurant, messages, session = _world(restaurant_options)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    await _send(session, Input("user", "actually, Saturday"))
    await _settle(session, restaurant, messages)
    session.clock += after
    if fee_rises_to is not None:
        restaurant.fee = fee_rises_to
    if decline:
        await _send(session, Input("user", "no, keep it"))
    else:
        await _send(session, Input("user", "yes", approves="A1"))
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def revise_late_arrival(fencing=True):
    """The Friday request is still travelling when the user revises: a lookup finds nothing."""
    tl, restaurant, messages, session = _world(fencing=fencing)
    await _book_friday(session, restaurant, hold="reserve.arrive")
    for _ in range(6):
        await asyncio.sleep(0)
    await _send(session, Input("user", "actually, Saturday"))
    await asyncio.sleep(0)
    await session.idle()
    restaurant.release("reserve.arrive")          # the Friday request lands, late
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def revise_after_text(receipt=True, **options):
    """Friday is booked and the group text has gone out; its receipt is late."""
    tl, restaurant, messages, session = _world(**options)
    await _book_friday(session, restaurant, hold=None)
    messages.hold("send.reply")
    await messages.delivered.wait()
    pending = asyncio.get_running_loop().create_task(session.submit(Input("user", "actually, Saturday")))
    await asyncio.sleep(0.01)
    if receipt:
        messages.release("send.reply")            # arrives inside the 50 ms the contract allows
    await pending
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


NO_LOOKUP = {"reserve_table": Contract(effect="reversible", needs_consent=True, wait=0.05,
                                       undo="cancel_reservation")}


async def revise_no_lookup():
    """A restaurant with no idempotency keys and no lookup, and a reply that never comes."""
    tl, restaurant, messages, session = _world(contracts=NO_LOOKUP)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    await _send(session, Input("user", "actually, Saturday"))
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def key_reuse(strict):
    """A revision that reuses the Friday request's key (keys scoped to the step, not the
    operation), on a server that can look keys up but not close them."""
    tl, restaurant, messages, session = _world({"strict_keys": strict}, key_scope="step", fencing=False)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    await _send(session, Input("user", "actually, Saturday"))
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def lost_response(keys=True):
    """The restaurant commits, the reply is lost, and the runtime sends the request again."""
    options = {} if keys else {"contracts": NO_LOOKUP}
    tl, restaurant, messages, session = _world(**options)
    restaurant.lose_next_response = True
    await _send(session, Input("user", "book Friday 19:00 for 4", intent=Intent(notify=())))
    await _settle(session, restaurant, messages)
    return _result(tl, restaurant, messages, session)


async def crash_mid_revision(durable=True, at="reserve"):
    """The process dies partway through the Saturday turn: after the Saturday table is
    committed but before the reply (at="reserve"), or while the group text is out
    (at="text"). A new process starts from the same store."""
    store = Store(keep_ops=durable)
    tl, restaurant, messages, session = _world(store=store)
    await _book_friday(session, restaurant, hold="reserve.reply")
    await restaurant.committed.wait()
    if at == "reserve":
        restaurant.hold("reserve.reply")          # the Saturday reply will be late too
    elif at == "cancel":
        restaurant.hold("cancel.reply")           # Friday's cancellation reply will be late
    else:
        messages.hold("send.reply")               # the Saturday text's receipt will be late
    await _send(session, Input("user", "actually, Saturday"))
    if at == "reserve":
        await restaurant.wait_for("R2")
    elif at == "cancel":
        while restaurant.reservations["R1"]["status"] != "cancelled":
            await asyncio.sleep(0)
    else:
        await messages.delivered.wait()
    await session.crash()
    restaurant.release_all()
    messages.release_all()
    await restaurant.drain()
    await messages.drain()
    store.restart()
    revived = Session(tl, restaurant, messages, store=store, incarnation="b", secrets=SECRETS)
    await revived.recover()
    await _settle(revived, restaurant, messages)
    return _result(tl, restaurant, messages, revived)
