"""A scripted stand-in for the model. It reads words into structured intents, plans steps,
and writes replies from facts the runtime hands it. No randomness, no language model."""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Intent:
    kind: str = "book"          # "book"; "tell" (only notify); "offer" (the restaurant's alternative);
                                # "notice" (something to tell the user, no plan needed)
    day: str = "Fri"
    time: str = "19:00"
    party: int = 4
    notify: tuple = ("group",)
    note: str = ""              # free text the source sent with it, in its own words
    withdraws: str = ""         # a reservation the source reports is gone

    def describe(self):
        if self.kind == "notice":
            return self.note
        if self.kind == "tell":
            return f"also tell {' and '.join(self.notify)}"
        who = f", tell {' and '.join(self.notify)}" if self.notify else ""
        if self.kind == "offer":
            return f"offer: {self.day} {self.time} for {self.party}{who}"
        return f"book {self.day} {self.time} for {self.party}{who}"

    def adding(self, notify):
        return replace(self, notify=self.notify + tuple(n for n in notify if n not in self.notify))


# What the scripted model makes of each phrase the scenarios use. A real model would produce
# the same structure (a relation and the fields that change) from any wording.
READINGS = {
    "also tell Sam": ("adds", {"notify": ("Sam",)}),
    "what time is sunset?": ("independent", {}),
    "actually, Saturday": ("revises", {"day": "Sat"}),
    "actually, Sunday": ("revises", {"day": "Sun"}),
    "no, keep it": ("declines", {}),
    "yes": ("approves", {}),
    "actually, make it 20:00": ("revises", {"time": "20:00"}),
    "actually, just tell Sam, not the group": ("revises", {"notify": ("Sam",)}),
    "Saturday too?": ("unsure", {"day": "Sat"}),
}


class ScriptedModel:
    def read(self, text):
        """Classify a user message against the running turn: new, adds, independent,
        revises, or unsure, with the fields it changes."""
        return READINGS.get(text, ("new", {}))

    def plan(self, intent):
        if intent.kind == "notice":
            return ["notify_user"]
        if intent.kind == "tell":
            return ["text_group"]
        steps = ["reserve_table"] if intent.kind == "offer" else ["check_availability", "reserve_table"]
        return steps + (["text_group"] if intent.notify else [])

    def args(self, step, intent, correction=False):
        if step == "notify_user":
            return {"text": intent.note}
        if step == "text_group":
            lead = "Change of plan: " if correction else ""
            return {"to": " and ".join(intent.notify),
                    "text": f"{lead}table for {intent.party}, {intent.day} {intent.time}"}
        return {"day": intent.day, "time": intent.time, "party": intent.party}

    def ask(self, intent):
        """The unsafe way to write a consent question: from the source's own framing."""
        return intent.note or f"The restaurant can do {intent.day} {intent.time} instead. Shall I book it?"

    def reply(self, facts):
        """Turn the runtime's facts about this turn into what the agent tells the user."""
        if not facts:
            return "Nothing has changed."
        say = {
            "booked": "Booked {}.",
            "kept": "Kept {}; it already matches.",
            "cancelled": "Cancelled {}.",
            "texted": "Texted {}.",
            "cancelled_unconfirmed": "Asked the restaurant to cancel {}; I couldn't confirm it went through.",
            "texted_unconfirmed": "Texted {}; I couldn't confirm delivery.",
            "corrected": "{} had already gone out.",
            "assumed": "{} may already have reached them; I couldn't confirm it, so I've treated it as sent.",
            "unknown": "I may have booked {}: the restaurant never confirmed, and I can't check. "
                       "If a confirmation arrives, tell me and I'll cancel it.",
            "full": "{} is full.",
            "rejected": "The restaurant rejected the {} request: its key was already used for another booking.",
            "still_held": "You still hold {}; keep it, or shall I cancel it?",
            "fee": "Cancelling {} now costs a {} fee. Approve {} to cancel it anyway, or keep it.",
            "refused": "The restaurant won't cancel {} this close to the booking, so you still hold it.",
            "told": "{}",
            "moot": "{}",
            "gone": "The restaurant has withdrawn {}.",
        }
        unique = [f for i, f in enumerate(facts) if f not in facts[:i]]   # a resumed turn repeats some
        return " ".join(say[kind].format(*detail) for kind, detail in unique)
