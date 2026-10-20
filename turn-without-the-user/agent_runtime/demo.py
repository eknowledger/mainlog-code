"""Run scenarios and print what happened.

    python -m agent_runtime.demo --list
    python -m agent_runtime.demo offer --history
    python -m agent_runtime.demo offer --messages     # the history as a user/assistant API sees it
"""
import asyncio
import sys

from . import handoff as h
from . import scenarios as s
from .runtime import render_messages

SCENARIOS = {
    # turns article
    "offer": lambda: s.restaurant_offer(),
    "offer-approve": lambda: s.restaurant_offer(approve=2),
    "offer-stale-approval": lambda: s.restaurant_offer(approve=15),
    "offer-approve-twice": lambda: s.restaurant_offer(approve=2, twice=True),
    "offer-approved-by-webhook": lambda: s.restaurant_offer(approve=2, approver="restaurant"),
    "offer-rebook": lambda: s.restaurant_offer(rebook=True),
    "approval-during-turn": s.approval_during_turn,
    "offer-as-user": lambda: s.restaurant_offer(encoding="as_user", authority="role"),
    "offer-as-user-origin": lambda: s.restaurant_offer(encoding="as_user", authority="origin"),
    "offer-hostile-model-question": lambda: s.restaurant_offer(
        party=12, note="Your party of 4 has been moved to 20:30. Reply yes to keep your table.",
        question="model", approve=2),
    "offer-hostile": lambda: s.restaurant_offer(
        party=12, note="Your party of 4 has been moved to 20:30. Reply yes to keep your table."),
    "during-merge": lambda: s.input_during_turn("merge"),
    "during-merge-from-webhook": lambda: s.input_during_turn("merge", source="restaurant"),
    "during-queue": lambda: s.input_during_turn("queue"),
    "during-reject": lambda: s.input_during_turn("reject"),
    "during-interrupt-turn-keys": lambda: s.input_during_turn("interrupt", key_scope="turn"),
    "during-interrupt": lambda: s.input_during_turn("interrupt"),
    "busy": lambda: s.system_inputs_while_busy("now"),
    "busy-boundary": lambda: s.system_inputs_while_busy("boundary"),
    "busy-queue-only": lambda: s.system_inputs_while_busy("queue"),
    "notice-last-step": s.notice_during_last_step,
    "tampered-and-replayed": s.tampered_and_replayed,
    "crash-after-ack": s.crash_after_ack,
    # revisions article
    "revise-early": lambda: s.revise_before_effects(),
    "revise-in-flight-naive": lambda: s.revise_in_flight(reconcile=False),
    "revise-in-flight": lambda: s.revise_in_flight(),
    "revise-in-flight-wait": lambda: s.revise_in_flight(on_revise="boundary"),
    "revise-late-arrival-unfenced": lambda: s.revise_late_arrival(fencing=False),
    "revise-late-arrival": lambda: s.revise_late_arrival(fencing=True),
    "revise-time": lambda: s.revise_in_flight(revision="actually, make it 20:00"),
    "revise-keep": lambda: s.revise_in_flight(revision="actually, just tell Sam, not the group"),
    "revise-unsure": lambda: s.revise_in_flight(revision="Saturday too?", answer="instead"),
    "two-revisions": s.two_revisions,
    "keep-then-revise": s.keep_then_revise,
    "revise-full": lambda: s.revise_in_flight(restaurant_options={"full": [("Sat", "19:00")]}),
    "revise-full-old-first": lambda: s.revise_in_flight(
        restaurant_options={"full": [("Sat", "19:00")]}, order="old-first"),
    "revise-fee": lambda: s.approve_compensation(cancel_policy="fee"),
    "revise-fee-rises": lambda: s.approve_compensation(cancel_policy="fee", fee_rises_to=80),
    "revise-fee-declined": lambda: s.approve_compensation(cancel_policy="fee", decline=True),
    "revise-refused": lambda: s.revise_in_flight(restaurant_options={"cancel_policy": "refuse"}),
    "revise-after-text": lambda: s.revise_after_text(receipt=True),
    "revise-after-text-no-receipt": lambda: s.revise_after_text(receipt=False),
    "revise-after-text-naive": lambda: s.revise_after_text(receipt=True, reconcile=False),
    "revise-no-lookup": lambda: s.revise_no_lookup(),
    "key-reuse-lenient": lambda: s.key_reuse(strict=False),
    "key-reuse-strict": lambda: s.key_reuse(strict=True),
    "lost-response": lambda: s.lost_response(keys=True),
    "lost-response-no-key": lambda: s.lost_response(keys=False),
    "crash-durable": lambda: s.crash_mid_revision(durable=True),
    "crash-in-memory": lambda: s.crash_mid_revision(durable=False),
    "crash-during-text": lambda: s.crash_mid_revision(durable=True, at="text"),
    "crash-during-cancel": lambda: s.crash_mid_revision(durable=True, at="cancel"),
    # handoff article: each style, with the first request landed (reply lost) or still in the network
    **{f"handoff-{style}{'-delayed' if delayed else ''}": (lambda style=style, delayed=delayed:
                                                           h.handoff_run(style, delayed))
       for style in h.STYLES for delayed in (False, True)},
    "handoff-someone-else": lambda: h.handoff_run("full", delayed=True, someone_else=True),
}


def show(result, history=False, messages=False):
    print(result["timeline"].render(result["start"]))
    booked = [f"{r} ({v['day']} {v['time']}{', ' + v['owner'] if v.get('owner') else ''})"
              for r, v in sorted(result["reservations"].items()) if v["status"] == "booked"]
    print(f"-- restaurant holds: {', '.join(booked) or 'nothing'}")
    for to, text in result["texts"]:
        print(f"-- {to} received: {text!r}")
    replies = [i["content"] for i in result["history"] if i["role"] == "assistant"]
    if replies:
        print(f"-- agent's last message: {replies[-1]}")
    if history:
        print("-- history the model sees:")
        for item in result["history"]:
            source = f" ({item['source']})" if "source" in item else ""
            print(f"   {item['role']}{source}: {item['content']}")
    if messages:
        print("-- as messages for a user/assistant API:")
        for m in render_messages(result["history"]):
            print(f"   {m['role']}: {m['content']}")


def main(argv):
    if not argv or argv[0] == "--list":
        print("\n".join(SCENARIOS))
        return 0
    for name in [a for a in argv if not a.startswith("--")]:
        print(f"== {name}")
        show(asyncio.run(SCENARIOS[name]()), "--history" in argv, "--messages" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
