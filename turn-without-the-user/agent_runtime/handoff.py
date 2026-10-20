"""Handing a task from one agent to another: what each handoff style carries, and what the receiver can do with it.

Used by the third article, Handing off between agents. The concierge, the agent the user talks to, books Friday at
19:00 for four at Chez Lili. The restaurant doesn't answer within its 10 s timeout, so the concierge hands the task to
Bookings, the agent that owns the restaurant integration. The booking request is now in doubt: it may have landed
(and only the reply was lost) or it may still be travelling. Who books, and how many tables does the restaurant hold?

Same fake restaurant, same keys and same log format as the rest of this runtime. The receiving "model" is scripted to
the best case: it uses everything it can see sensibly, so every bad outcome comes from what didn't cross. The
transcript is what the concierge's model saw; the record is what its runtime knew, which the model never saw.
"""
import asyncio
import json
import textwrap

from .services import KeyClosed, Restaurant
from .timeline import Timeline

SLOT = ("Fri", "19:00", 4)
CONCIERGE_KEY = "s1:op-a2"       # the concierge's runtime keyed its booking request; its model never saw the key
BOOKINGS_KEY = "s2:op-a1"        # a request Bookings makes is a new operation in its own session

SEARCH_RESULT = json.dumps([
    {"name": n, "cuisine": c, "rating": r, "address": f"{i} Rue Example, Paris",
     "slots": ["18:30", "19:00", "20:30", "21:00"], "phone": f"+33 1 00 00 00 0{i}"}
    for i, (n, c, r) in enumerate([("Chez Lili", "bistro", 4.6), ("Le Quai", "seafood", 4.4),
                                   ("Osteria 9", "italian", 4.5), ("Maison Bo", "thai", 4.2),
                                   ("Le Petit Four", "french", 4.7)])])

# What the concierge's model saw: its conversation, as chat items.
TRANSCRIPT = [
    {"role": "user", "content": "Book Friday at seven for four at Chez Lili and text the group once it's confirmed."},
    {"role": "assistant", "tool_call": {"name": "search_restaurants", "args": {"q": "Chez Lili", "near": "Paris"}}},
    {"role": "tool", "name": "search_restaurants", "content": SEARCH_RESULT},
    {"role": "assistant", "tool_call": {"name": "check_availability", "args": {"day": "Fri", "time": "19:00", "party": 4}}},
    {"role": "tool", "name": "check_availability", "content": "available"},
    {"role": "assistant", "content": "Friday at 19:00 is free. Booking it now."},
    {"role": "assistant", "tool_call": {"name": "reserve_table", "args": {"day": "Fri", "time": "19:00", "party": 4}}},
    {"role": "tool", "name": "reserve_table", "content": "error: no reply from the restaurant within 10 s"},
    {"role": "assistant", "content": "The restaurant isn't answering. Handing over to Bookings: Friday at 19:00 for four."},
]

# What the concierge's runtime knew. Written by the runtime, never by the model.
RECORD = {
    "task": "book Fri 19:00 for 4 at Chez Lili, then text the group",
    "origin": "user",
    "constraints": ["text the group only after the booking is confirmed"],
    "open_operations": [{"op": "op-a2", "tool": "reserve_table", "args": {"day": "Fri", "time": "19:00", "party": 4},
                         "key": CONCIERGE_KEY, "state": "unknown"}],
    "pending_actions": [],
}


def _is_tool_item(m):
    return m["role"] == "tool" or "tool_call" in m


def full_transcript():
    return {"words": TRANSCRIPT, "record": None}


def tools_removed():
    """The OpenAI Agents SDK's remove_all_tools filter: tool calls, their outputs and approvals are dropped."""
    return {"words": [m for m in TRANSCRIPT if not _is_tool_item(m)], "record": None}


def last_pair():
    """LangChain's handoffs guide: the last AI message and the transfer's tool message, not the whole history."""
    last_ai = [m for m in TRANSCRIPT if m["role"] == "assistant" and "content" in m][-1]
    return {"words": [last_ai, {"role": "tool", "name": "transfer_to_bookings",
                                "content": "Transferred to Bookings"}], "record": None}


def model_brief(mentions_call):
    """A delegation tool in CrewAI's style: the sender's model writes a task and a context for the coworker."""
    context = "User wants Chez Lili, Friday 19:00, party of 4; text the group once confirmed."
    if mentions_call:
        context += " I already sent the booking request but the restaurant didn't reply within 10 s."
    brief = {"task": "Book the table", "context": context, "coworker": "Bookings"}
    return {"words": [{"role": "user", "content": json.dumps(brief)}], "record": None}


def runtime_record():
    """The sending runtime writes the record; the words are cut to the user's own request."""
    return {"words": [TRANSCRIPT[0]], "record": RECORD}


STYLES = {
    "full": ("full transcript", full_transcript),
    "no-tools": ("transcript, tools removed", tools_removed),
    "last-pair": ("last message + transfer", last_pair),
    "brief": ("model brief, mentions the call", lambda: model_brief(True)),
    "brief-omits": ("model brief, omits the call", lambda: model_brief(False)),
    "record": ("runtime handoff record", runtime_record),
}


def compact(value):
    """JSON with no spaces after separators, the way JavaScript's JSON.stringify writes it, so these byte counts
    are the ones the handoff-auditor tool shows for the same payload. Everything here is ASCII, so characters are
    bytes."""
    return json.dumps(value, separators=(",", ":"))


def sizes(handoff):
    """Bytes of words (what the next model reads) and of record (what the next runtime enforces), as compact JSON."""
    words = len(compact(handoff["words"]))
    record = len(compact(handoff["record"])) if handoff["record"] else 0
    return words, record


# What a careful reader takes as a sign that a booking went out: the call itself, or the sending model's own prose
# about it. "Booking it now" survives a filter that drops the call, and so does "the restaurant isn't answering".
BOOKING_SIGNS = ('"reserve_table"', "sent the booking request", "Booking it now", "isn't answering",
                 "didn't reply")


def _sees_unresolved_booking(words):
    """The best a model can do with words: notice any sign that a booking was sent and never confirmed. Returns the
    sign it noticed, or None."""
    text = json.dumps(words)
    seen = next((sign for sign in BOOKING_SIGNS if sign in text), None)
    resolved = '"reserve_table", "content": "R' in text
    return None if resolved else seen


def _find_by_details(restaurant, day, time, party):
    """Lookup by details, the most a transcript allows: any booking that matches, whoever made it."""
    for rid, r in sorted(restaurant.reservations.items()):
        if (r["day"], r["time"], r["party"]) == (day, time, party) and r["status"] == "booked":
            return rid
    return None


async def _bookings_receives(tl, restaurant, handoff):
    """Bookings, after the handoff. It acts on everything it was given, and on nothing it wasn't."""
    record = handoff["record"]
    in_doubt = [o for o in (record or {}).get("open_operations", []) if o["state"] == "unknown"]
    if in_doubt:
        op = in_doubt[0]
        rid = restaurant.close(op["key"])
        tl.add("resolve", f"{op['key']}: {rid or 'nothing has landed'}; key closed")
        if rid:
            return rid
    elif sign := _sees_unresolved_booking(handoff["words"]):
        tl.add("model.read", "sees reserve_table sent, and no result" if sign == '"reserve_table"'
               else f'reads "{sign}", and no result')
        rid = _find_by_details(restaurant, *SLOT)
        tl.add("lookup.details", f"Fri 19:00 x4: {rid or 'nothing found'} (no key to close)")
        if rid:
            return rid
    else:
        tl.add("model.read", "sees no booking in progress")
    tl.add("tool.sent", f"s2 reserve_table(Fri, 19:00, 4) key={BOOKINGS_KEY}")
    rid = await restaurant.reserve(*SLOT, key=BOOKINGS_KEY)
    tl.add("tool.result", f"s2 reserve_table -> {rid}")
    return rid


async def handoff_run(style="full", delayed=False, someone_else=False):
    """One run: the concierge's request goes missing, the concierge hands off, Bookings acts, the network settles.

    delayed: the request is still in the network at the handoff, and lands afterwards.
    otherwise: it landed, and only the reply was lost.
    someone_else: another customer already holds an identical booking (Fri 19:00 for 4).
    """
    tl = Timeline()
    restaurant = Restaurant(tl)
    if someone_else:
        restaurant.reservations["R1"] = {"day": "Fri", "time": "19:00", "party": 4, "status": "booked", "key": None,
                                         "owner": "another customer's"}
        tl.add("restaurant", "R1 Fri 19:00 x4 is another customer's booking")
    tl.add("user", TRANSCRIPT[0]["content"])
    tl.add("tool.sent", f"s1 reserve_table(Fri, 19:00, 4) key={CONCIERGE_KEY}")
    if delayed:
        restaurant.hold("reserve.arrive")
    else:
        restaurant.lose_next_response = True
    request = asyncio.ensure_future(restaurant.reserve(*SLOT, key=CONCIERGE_KEY))
    try:
        await asyncio.wait_for(asyncio.shield(request), timeout=0.01)
    except (asyncio.TimeoutError, TimeoutError):
        pass
    tl.add("tool.unknown", "s1 reserve_table: no reply within 10 s")

    label, make = STYLES[style]
    handoff = make()
    words, record = sizes(handoff)
    tl.add("handoff", f"concierge -> bookings: {label} (words {words} B, record "
                      f"{f'{record} B' if record else 'none'})")
    rid = await _bookings_receives(tl, restaurant, handoff)

    if delayed:
        restaurant.release("reserve.arrive")
    try:
        await request
    except (KeyClosed, TimeoutError):
        pass
    await restaurant.drain()
    mine = sorted(r for r, v in restaurant.reservations.items() if v["key"] is not None and v["status"] == "booked")
    # Bookings promises the text only if something it was given says the group is waiting for one.
    told = "text the group" in json.dumps(handoff["words"]) or any("text the group" in c for c in
                                                                  (handoff["record"] or {}).get("constraints", []))
    reply = f"Booked Fri 19:00 for 4 ({rid}).{' Texting the group.' if told else ''}"
    # The shape demo.show() prints, so a handoff run reads like every other scenario's.
    return {"timeline": tl, "start": 0, "reservations": restaurant.reservations, "texts": [],
            "history": [{"role": "assistant", "content": reply}], "yours": mine,
            "words": words, "record": record, "style": label}


def table():
    """The article's table: every style, against both restaurant behaviours."""
    async def cell(style, delayed):
        return await handoff_run(style, delayed)
    lines = [f"{'handoff style':32} {'words B':>8} {'record B':>9} | tables if it landed | if it was delayed"]
    for style, (label, _) in STYLES.items():
        landed = asyncio.run(cell(style, False))
        late = asyncio.run(cell(style, True))
        lines.append(f"{label:32} {landed['words']:8} {landed['record']:9} | "
                     f"{len(landed['yours']):>19} | {len(late['yours'])}")
    return "\n".join(lines)


def _says(m):
    """One item as the receiving model reads it: who it's from, and what it says, as lines."""
    if "tool_call" in m:
        call = m["tool_call"]
        return "assistant, tool call", [f"{call['name']} {json.dumps(call['args'])}"]
    if m["role"] == "tool":
        return f"tool result, {m['name']}", [m["content"]]
    try:
        brief = json.loads(m["content"])
    except ValueError:
        return m["role"], [m["content"]]
    return m["role"], [f"{k}: {v}" for k, v in brief.items()]       # a model-written brief, field by field


def model_view(style, width=88):
    """What Bookings' model reads after a handoff in this style: the words, item by item, in the demo's plain chat
    form. A provider's API wraps the same items in its own message shape. Long items are cut and say so; the record,
    when there is one, is printed after the words because it goes to Bookings' runtime, never to its model."""
    label, make = STYLES[style]
    handoff = make()
    words, record = sizes(handoff)
    out = [f"-- Bookings' model reads: {label}, {len(handoff['words'])} item(s), {words} B"]
    for n, m in enumerate(handoff["words"], 1):
        who, says = _says(m)
        if m["role"] == "tool" and len(says[0]) > width:    # bulk: cut it, and say how much there was
            says = [f"{says[0][:width - 30]}... ({len(compact(m))} B in all)"]
        lines = [w for line in says for w in textwrap.wrap(line, width, subsequent_indent="  ")]
        out.append(f"{n:2}  {who:34} {lines[0]}")
        out += [f"{'':38}{line}" for line in lines[1:]]
    if handoff["record"]:
        out.append(f"-- not for the model: the record, {record} B, which Bookings' runtime acts on first")
        out += [f"    {line}" for line in json.dumps(handoff["record"], indent=2).splitlines()]
    else:
        out.append("-- no record: Bookings' runtime has nothing of its own to act on")
    return "\n".join(out)


def keeps(style):
    """What crosses in each style, for the article's figure. For each fact a receiver needs, where it is: in the words
    (as the user or the sending model put it), in the record, or nowhere (None)."""
    handoff = STYLES[style][1]()
    words, record = json.dumps(handoff["words"]), json.dumps(handoff["record"] or {})
    sign = _sees_unresolved_booking(handoff["words"])
    return {
        "what to book": "the user's words" if "Book Friday at seven" in words
                        else "the model's words" if "19:00" in words else None,
        "a booking went out": "the call" if sign == '"reserve_table"' else "the model's words" if sign
                              else "the record" if '"open_operations": [{' in record else None,
        "the request's key": "the record" if CONCIERGE_KEY in record else "the words" if CONCIERGE_KEY in words else None,
        "whose authority": "the record" if '"origin"' in record else None,
        "text once confirmed": "the user's words" if "once it's confirmed" in words
                               else "the model's words" if "once confirmed" in words
                               else "the record" if "after the booking is confirmed" in record else None,
        "the search result": "the words" if "Le Petit Four" in words else None,
    }


if __name__ == "__main__":
    import sys
    if "--sees" in sys.argv:
        names = [a for a in sys.argv[1:] if a != "--sees"] or list(STYLES)
        print("\n\n".join(model_view(n) for n in names))
    else:
        print(table())
