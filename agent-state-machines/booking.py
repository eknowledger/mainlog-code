"""The dinner booking, as data: the tools, the two designs' prompts, the scripted run, and the state machine's table.

Everything the other scripts measure is defined here once, so the live run, the table check and the lab all read
the same prompts and the same table.
"""
from __future__ import annotations

import json


def tool(name, desc, props, req):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": req}}


S = {"type": "string"}
TOOLS = {
    "check_availability": tool("check_availability",
        "Checks which tables the restaurant has free for a party on a given day and time. Returns a list of slots with "
        "their times and whether a deposit is needed. It does not hold or reserve anything, so it is safe to call at "
        "any point. Use it before proposing a time to the user.",
        {"day": {**S, "description": "Day of the week, e.g. Fri"}, "time": {**S, "description": "24h time, e.g. 19:00"},
         "party": {"type": "integer", "description": "Number of people"}}, ["day", "time", "party"]),
    "reserve_table": tool("reserve_table",
        "Books a table at the restaurant for the party, day and time given. This commits a real reservation and may "
        "charge a no-show fee, so only call it after the user has confirmed the exact slot. Send the idempotency key "
        "so a retry never books twice. Returns the reservation ID.",
        {"day": S, "time": S, "party": {"type": "integer"}, "key": {**S, "description": "Idempotency key"}},
        ["day", "time", "party", "key"]),
    "cancel_reservation": tool("cancel_reservation",
        "Cancels an existing reservation by its ID. Cancelling releases the table and it can be taken by someone "
        "else, so this cannot be undone. A late cancellation may incur a fee; the result says whether one applied.",
        {"reservation_id": S}, ["reservation_id"]),
    "lookup_reservation": tool("lookup_reservation",
        "Looks up a reservation by ID or by idempotency key and returns its current state: committed, cancelled or "
        "not found. Use it when a booking request's outcome is unknown.",
        {"reservation_id": S, "key": S}, []),
    "pay_deposit": tool("pay_deposit",
        "Pays the deposit the restaurant requires for some bookings, using the user's saved card. Charges real money "
        "and needs the user's explicit approval of the amount. Returns a receipt ID.",
        {"reservation_id": S, "amount_usd": {"type": "number"}}, ["reservation_id", "amount_usd"]),
    "get_contacts": tool("get_contacts",
        "Returns the members of a named group from the user's contacts, with their phone numbers. Read only.",
        {"group": {**S, "description": "Group name, e.g. 'dinner group'"}}, ["group"]),
    "text_group": tool("text_group",
        "Sends one text message to every member of a group. A sent message cannot be recalled, so send it only once "
        "the booking is confirmed, and never more than once per booking.",
        {"to": S, "text": S}, ["to", "text"]),
    "notify_user": tool("notify_user",
        "Shows the user a short message in the app. Use it to report progress, ask a question, or explain a failure.",
        {"text": S}, ["text"]),
}

FREE_SYSTEM = (
    "You are a dinner-booking assistant acting for the user. Book restaurant tables, handle deposits, and tell the "
    "user's group. Rules: never reserve before checking availability; never reserve or pay without the user's "
    "explicit confirmation of the exact slot and amount; text the group only after the booking is committed, and only "
    "once; if a booking outcome is unknown, look it up before trying again; never cancel a reservation unless the user "
    "asked. Ask the user when anything is ambiguous. When the task is complete, say so and stop."
)

# Per state: the instruction the model gets there and the tools it may call. Which state comes next is not here:
# it is the table below, and the table is code.
STATES = {
    "gather":   ("Extract day, time and party size from the user's request. Ask only for what is missing.",
                 ["notify_user"]),
    "search":   ("Check availability for the requested slot and pick the closest free option.",
                 ["check_availability"]),
    "confirm":  ("Ask the user to confirm the exact slot, and the deposit if one is needed.", ["notify_user"]),
    "book":     ("Reserve the confirmed slot with its idempotency key; pay the approved deposit if required.",
                 ["reserve_table", "lookup_reservation", "pay_deposit"]),
    "notify":   ("Tell the dinner group the confirmed booking, once.", ["get_contacts", "text_group"]),
    "report":   ("Tell the user what happened and what is still outstanding.", ["notify_user"]),
    # Where the agent goes when the model can't be used: the provider is down, or a budget tripped the breaker
    # (deciding *when* to trip is the cost-control post's subject). Its message is a template, so it needs no model.
    "degraded": ("Tell the user exactly what is done and what is waiting, without the model.", []),
}

# One scripted run, the same seven steps in every design: (state, the tool the step calls, what the step adds to the
# history). Scripted, so that every difference between two designs' requests is the design and nothing else.
RUN = [
    ("gather", "notify_user", "user: Friday at 7 for four, and tell the group"),
    ("search", "check_availability", "tool: Fri 19:00 free for 4, deposit $20"),
    ("confirm", "notify_user", "user: yes, book it and pay the deposit"),
    ("book", "reserve_table", "tool: committed R1 Fri 19:00 x4"),
    ("book", "pay_deposit", "tool: receipt P1 $20"),
    ("notify", "get_contacts", "tool: dinner group = Ana, Ben, Chloe"),
    ("notify", "text_group", "tool: delivered to 3"),
]

# In the state machine that runs steps as code, a step needs the model only when its action can't be computed from
# what is already known. Availability, the confirmation question, the booking, the deposit and the contact lookup are
# all determined by the slots and the pending action; reading the request and writing the group's text are not.
MODEL_STEPS = [1, 7]          # 1-based step numbers


def history(i: int) -> str:
    """What the model has seen before step i (0-based): every earlier step's observation, as one transcript."""
    return "\n".join(obs for _, _, obs in RUN[:i]) or RUN[0][2]


def request(design: str, i: int) -> dict:
    """The request a design sends at step i (0-based). History goes in the user turn, identical in every design."""
    state = RUN[i][0]
    instr, allowed = STATES[state]
    if design == "free":
        return {"tools": list(TOOLS.values()), "system": FREE_SYSTEM, "user": history(i)}
    if design == "states":
        return {"tools": [TOOLS[t] for t in allowed],
                "system": f"You are a dinner-booking assistant. Current step: {state}. {instr}", "user": history(i)}
    if design == "fixed":
        # The cache-stable variant: the tool list and system prompt never change, the state rides in the message,
        # and code refuses a call outside the state's set.
        return {"tools": list(TOOLS.values()), "system": FREE_SYSTEM,
                "user": f"{history(i)}\n\nCurrent step: {state}. {instr} Allowed tools: {', '.join(allowed)}."}
    raise ValueError(design)


def final_request() -> dict:
    """The free-form loop's eighth call: everything is done, and the model has to notice and say so."""
    return {"tools": list(TOOLS.values()), "system": FREE_SYSTEM, "user": "\n".join(o for _, _, o in RUN)}


# ---------------------------------------------------------------------------------------------------------------------
# The table: every state against every event the code can observe. An event is something code sees, a tool result,
# a user reply, a webhook, the breaker opening, never a phrase in the model's text.
EVENTS = [
    "slots_complete", "slots_missing",            # gather's result
    "slot_free", "none_free",                     # check_availability's result
    "user_yes", "user_no",                        # the user answers the confirmation
    "committed", "unknown", "refused",            # reserve_table's result
    "paid",                                       # the deposit is settled, or none was due
    "sent", "send_failed",                        # text_group's result
    "told",                                       # the user has been shown a message
    "user_revises",                               # the user changes the day, time or party mid-task
    "slot_gone",                                  # the restaurant says the table is no longer free
    "model_unavailable",                          # the breaker opened: no model calls for now
    "model_back",                                 # the breaker closed
]

# The happy path and its obvious branches: what a first draft of the machine contains.
TRANSITIONS = {
    ("gather", "slots_complete"): "search",
    ("gather", "slots_missing"): "gather",
    ("search", "slot_free"): "confirm",
    ("search", "none_free"): "gather",
    ("confirm", "user_yes"): "book",
    ("confirm", "user_no"): "gather",
    ("book", "committed"): "book",               # the reservation exists; the deposit is still due
    ("book", "paid"): "notify",
    ("book", "unknown"): "book",
    ("book", "refused"): "gather",
    ("notify", "sent"): "done",
    ("notify", "send_failed"): "report",
    ("report", "told"): "done",
}

# Which states call the model, in the design that runs the other steps as code. The breaker matters only there.
NEEDS_MODEL = {"gather", "notify"}

# What each of a state's own results is: the event a state is waiting for.
EXPECTS = {
    "gather": {"slots_complete", "slots_missing"}, "search": {"slot_free", "none_free"},
    "confirm": {"user_yes", "user_no"}, "book": {"committed", "unknown", "refused", "paid"},
    "notify": {"sent", "send_failed"}, "report": {"told"}, "degraded": {"told"},
}


def decide(state: str, event: str) -> tuple[str, str, str]:
    """Every cell's outcome: (kind, next state, why). kind is move, stay, defer or reject.

    The first branch is the draft machine. Everything after it is a decision someone had to make, and each one is
    a test: tests/test_table.py walks all of them.
    """
    if (state, event) in TRANSITIONS:
        nxt = TRANSITIONS[(state, event)]
        return ("stay" if nxt == state else "move", nxt, "the path the machine was drawn for")
    if event == "user_revises":
        if state in ("gather", "search", "confirm"):
            return ("move", "gather", "nothing is committed yet, so start again from the new request")
        if state == "degraded":
            return ("defer", state, "reading the new request needs the model; hold it until the model is back")
        if state == "report":
            # report always comes after a booking or a text exists: starting again here would book a second table.
            return ("defer", state, "a booking exists; finish telling the user, then revise it rather than start again")
        return ("defer", state, "a booking or a text is in flight; let it resolve, then revise what it left behind")
    if event == "slot_gone":
        if state in ("gather", "search"):
            return ("stay", state, "no slot has been chosen yet, so the next availability check covers it")
        if state == "confirm":
            return ("move", "search", "the slot the user is being asked about is gone; search before asking")
        if state == "book":
            return ("defer", state, "the booking's own result decides: refused, or committed and then gone")
        if state == "degraded":
            # degraded may have been entered from gather, with nothing booked: let the resumed state decide.
            return ("defer", state, "hold it; the state the machine resumes into knows whether a booking exists")
        return ("move", "report", "a booking that existed is gone; tell the user and ask before rebooking")
    if event == "model_unavailable":
        if state in NEEDS_MODEL:
            return ("move", "degraded", "this state needs the model; say what is done and wait")
        return ("stay", state, "this state runs as code, so the model being down changes nothing here")
    if event == "model_back":
        if state == "degraded":
            return ("move", "(resume)", "return to the state it left, which the machine remembered")
        return ("stay", state, "nothing was waiting for the model")
    if event == "told" and state == "degraded":
        return ("stay", state, "the user knows; keep waiting for the model")
    if event == "committed":
        return ("move", "report", "a reservation exists that this state didn't make; it is real, so report it")
    if event == "paid":
        return ("move", "report", "a payment went through that this state didn't make; it is real money, so report it")
    if event == "sent":
        return ("move", "report", "a text went out that this state didn't send; it can't be recalled, so report it")
    return ("reject", state, "a result this state never asked for: reject it, which means log it and do nothing")


def table() -> dict[tuple[str, str], tuple[str, str, str]]:
    cells = {}
    for s in STATES:
        for e in EVENTS:
            kind, nxt, why = decide(s, e)
            cells[(s, e)] = ("stay" if kind == "move" and nxt == s else kind, nxt, why)
    return cells


def canonical(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=False)
