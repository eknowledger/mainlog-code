"""Tool contracts: what each tool does to the outside world, and what the runtime may do about it."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Contract:
    effect: str           # "none", "reversible" (has a compensating tool) or "irreversible"
    needs_consent: bool   # acts for the user: a turn the user didn't start must ask first
    keyed: bool = False   # the service accepts an idempotency key
    lookup: bool = False  # the service can say, by key, whether a request landed
    fence: bool = False   # the service can close a key, so a request that lands later is refused
    wait: float = 0.0     # on a revision: how long to keep waiting for a reply that can't be looked up
    undo: str = None      # the compensating tool, if there is one


CONTRACTS = {
    # A read. Safe to drop mid-flight here; a read that places a hold would not be.
    "check_availability": Contract(effect="none", needs_consent=False),
    "reserve_table": Contract(effect="reversible", needs_consent=True, keyed=True, lookup=True,
                              fence=True, undo="cancel_reservation"),
    # No lookup, so a revision waits for the delivery receipt, up to a bound (50 ms in the demo).
    "text_group": Contract(effect="irreversible", needs_consent=True, wait=0.05),
    # Irreversible: a released table can be taken by someone else. Acts for the user, so a
    # turn the user didn't start must ask; a fee needs the user's consent whoever started it.
    "cancel_reservation": Contract(effect="irreversible", needs_consent=True),
    # Tells the user something. A sent message can't be recalled, but it doesn't act for them.
    "notify_user": Contract(effect="irreversible", needs_consent=False),
}
