"""Three domain agents, two ways of wiring them, and nothing else.

Bookings owns the restaurant integration, Calendar owns the user's calendar, Messaging owns texts. When the
restaurant cancels a booking, Bookings has to tell the other two. It can call them (the agent-as-tool style, where
the caller's model writes the callee's input) or publish a fact on a bus that each of them subscribes to.

The "models" are scripted and deterministic, so every difference between two runs comes from the wiring:

- Bookings' model briefs another agent the way a summary does: it folds the restaurant's note into its instruction.
- Messaging's model reads every "text the group: X" it is given as a request to send X. That stands in for a model
  following text it was handed. It is the channel being shown, not how often a real model would follow it.

Messaging's model is sent a real request, a system prompt, one tool and a user turn, so what it reads can be printed
(`python -m bus_demo.prompts`). Only its reply is scripted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RESTAURANT = "Chez Lili"


class Log:
    """One append-only log every component writes to, numbered like the runtime posts' logs."""

    def __init__(self):
        self.lines = []

    def add(self, kind, detail):
        self.lines.append((len(self.lines) + 1, kind, detail))

    def render(self):
        return "\n".join(f"{n:>2}  {kind:<17} {detail}" for n, kind, detail in self.lines)


class Down(Exception):
    pass


# ------------------------------------------------------------------ what Messaging's model is sent

# Messaging's system prompt and its one tool. The same in every run: only the user turn differs, and the user turn is
# where the wiring shows. A call hands over the string Bookings' model wrote; a fact is rendered by code from typed
# fields, so whatever reaches the model is a field the contract allowed.
MESSAGING_SYSTEM = (
    "You are Messaging, the agent that sends texts for one user. You can text the user, and the user's dinner "
    "group. Telling the user what happened is always fine. Texting the group acts for the user, so do it only "
    "when your task asks for it. Send each text with send_text."
)
SEND_TEXT = {
    "name": "send_text",
    "description": "Send one text message.",
    "input_schema": {
        "type": "object",
        "properties": {"to": {"enum": ["you", "group"]}, "text": {"type": "string"}},
        "required": ["to", "text"],
    },
}
FACT_HEADER = "An event, as typed fields:"


def render_fact(ev):
    """The user turn for a fact: code lays out the fields the event carries, one per line, in schema order."""
    return "\n".join([FACT_HEADER] + [f"{k}: {v}" for k, v in ev.items()])


def messaging_request(task):
    """Everything Messaging's model receives, in the shape of a Messages API request."""
    return {"system": MESSAGING_SYSTEM, "tools": [SEND_TEXT], "messages": [{"role": "user", "content": task}]}


# What Bookings' model is offered, to reach Messaging by call. As a tool, with one string field, the way an
# agent-as-tool is by default; or with typed parameters, so its model fills in fields rather than writing a sentence.
MESSAGING_AS_TOOL = {
    "name": "messaging",
    "description": "Ask the messaging agent to send texts.",
    "input_schema": {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]},
}
MESSAGING_AS_TYPED_TOOL = {
    "name": "reservation_cancelled",
    "description": "Tell the messaging agent that a reservation was cancelled.",
    "input_schema": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "reservation": {"type": "string"}, "slot": {"type": "string"},
                       "source": {"enum": ["restaurant", "bookings"]}},
        "required": ["id", "reservation", "slot", "source"],
        "additionalProperties": False,
    },
}


# ------------------------------------------------------------------ the scripted models

def bookings_brief(webhook):
    """Bookings' model, asked to tell the messaging agent what happened. Like any summary, it keeps the note."""
    return f"Tell the group their {webhook['slot']} dinner at {RESTAURANT} is cancelled. {webhook['note']}"


def messaging_model(request):
    """Messaging's scripted model: reads the user turn and returns the texts it would send, as (to, text).

    A sentence is a task: its first sentence goes to the group as the update. A fact is news for the user, written
    from its fields. Either way, any "text the group: X" it reads is taken as a request to send X: that stands in for
    a model following text it was handed.
    """
    task = request["messages"][0]["content"]
    if task.startswith(FACT_HEADER):
        f = dict(line.split(": ", 1) for line in task.splitlines()[1:])
        texts = []
        if f.get("type") == "ReservationCancelled":
            texts.append(("you", f"Your {f['slot']} table at {RESTAURANT} ({f['reservation']}) "
                                 f"was cancelled by the restaurant."))
    else:
        texts = [("group", "dinner update: " + task.split(". ")[0])]
    if "text the group:" in task:
        texts.append(("group", task.split("text the group:")[1].strip()))
    return texts


# ------------------------------------------------------------------ the agents

@dataclass
class Messaging:
    log: Log
    up: bool = True
    dedupe: bool = True
    sent: list = field(default_factory=list)
    seen: set = field(default_factory=set)
    requests: list = field(default_factory=list)

    def as_tool(self, instruction):
        """Called as a tool: one string, written by the caller's model, which becomes this agent's task."""
        if not self.up:
            raise Down("messaging unavailable")
        self._ask(instruction)
        return "ok"

    def on_command(self, ev):
        """A command on the bus: "text the group this". Typed, but its payload is still someone else's words."""
        self._ask(ev["text"])

    def handle(self, ev):
        """The bus subscription: commands and facts arrive on the same log, told apart by their type."""
        if ev["type"] == "GroupTextRequested":
            self.on_command(ev)
        else:
            self.on_fact(ev)

    def on_fact(self, ev):
        """A fact: something that happened. Code renders its fields; the model decides what it means for whom."""
        if self.dedupe and ev["id"] in self.seen:
            self.log.add("messaging", f"dropped {ev['id']}: already handled")
            return
        self.seen.add(ev["id"])
        # Every field the event carries reaches the model, a free-text one included, and it reads that like any text.
        self._ask(render_fact(ev))

    def _ask(self, task):
        self.requests.append(messaging_request(task))
        for to, text in messaging_model(self.requests[-1]):
            self._send(to, text)

    def _send(self, to, text):
        self.sent.append((to, text))
        self.log.add("messages", f"delivered to {to}: {text!r}")


@dataclass
class Calendar:
    log: Log
    up: bool = True
    events: dict = field(default_factory=dict)

    def as_tool(self, instruction):
        if not self.up:
            raise Down("calendar unavailable")
        self.events.pop("R1", None)
        self.log.add("calendar", "removed R1")
        return "ok"

    def on_fact(self, ev):
        if ev["type"] == "ReservationConfirmed":
            self.events[ev["reservation"]] = f"{ev['slot']} dinner"
            self.log.add("calendar", f"added {ev['reservation']} {ev['slot']}")
        elif ev["type"] == "ReservationCancelled" and ev["reservation"] in self.events:
            del self.events[ev["reservation"]]
            self.log.add("calendar", f"removed {ev['reservation']}")


# ------------------------------------------------------------------ the bus

# Every field each event type may carry. A fact has no free-text field: the restaurant's note has nowhere to go.
SCHEMA = {
    "ReservationConfirmed": {"type", "id", "reservation", "slot", "source"},
    "ReservationCancelled": {"type", "id", "reservation", "slot", "source"},
    # A command dressed as an event: typed, schema-valid, and still an instruction, because `text` is free text.
    "GroupTextRequested": {"type", "id", "text", "source"},
}
# A looser contract, as a team adds "a note, in case it's useful". Used by the bus-note scenario.
LOOSE_SCHEMA = dict(SCHEMA, ReservationCancelled=SCHEMA["ReservationCancelled"] | {"note"})


class Rejected(Exception):
    pass


@dataclass
class Bus:
    """A durable, ordered log of events, with an offset kept for each subscriber."""

    log: Log
    schema: dict = field(default_factory=lambda: SCHEMA)
    events: list = field(default_factory=list)
    subscribers: list = field(default_factory=list)
    offsets: dict = field(default_factory=dict)

    def subscribe(self, name, handler, agent):
        self.subscribers.append((name, handler, agent))
        self.offsets.setdefault(name, 0)

    def publish(self, ev):
        allowed = self.schema.get(ev["type"])
        if allowed is None:
            raise Rejected(f"unknown event type {ev['type']}")
        extra = set(ev) - allowed
        if extra:
            self.log.add("bus.rejected", f"{ev['id']}: field(s) not in schema: {sorted(extra)}")
            raise Rejected(sorted(extra))
        self.events.append(ev)
        self.log.add("bus.published", f"{ev['id']} {ev['type']} at offset {len(self.events) - 1}")

    def deliver(self):
        """Hand each subscriber everything after its offset. A subscriber that is down keeps its place."""
        for name, handler, agent in self.subscribers:
            i = self.offsets.get(name, 0)
            if not agent.up:
                if i < len(self.events):
                    self.log.add("bus.waiting", f"{name} is down; offset stays {i}")
                continue
            while i < len(self.events):
                handler(self.events[i])
                i += 1
            self.offsets[name] = i

    def redeliver(self, name, offset):
        """At-least-once delivery: after a lost acknowledgement, a subscriber gets an event again."""
        self.offsets[name] = offset
        self.log.add("bus.redelivered", f"{name} from offset {offset}")
