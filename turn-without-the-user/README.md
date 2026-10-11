# agent-runtime: turns, consent, revisions, and effects

A small, deterministic agent runtime used by two articles, [When a turn doesn't start with the
user](https://mainlog.dev/posts/turn-without-the-user/) and [When the user changes their mind
mid-task](https://mainlog.dev/posts/user-changes-their-mind/). `export_runs.py` writes `runs.json`, every scenario's
log, which the articles' replay labs show. Standard library
only; tested on Python 3.9 and 3.12. No language model: a scripted model reads each phrase
the scenarios use into a structured result, plans steps, and writes replies from facts, so
every outcome comes from the runtime's rules.

    python -m agent_runtime.demo --list              # 52 scenario names
    python -m agent_runtime.demo offer --history     # one scenario, with the model's history
    python -m agent_runtime.demo offer --messages    # the history as a user/assistant API gets it
    python -m pip install pytest && python -m pytest tests

## What's here

- `runtime.py`: the session. Admission (signature over the payload and its timestamp,
  dedupe by source and event ID written with the inbox entry, persist then acknowledge,
  supersession), turns with an origin and execution boundaries, consent
  as a pending action the runtime renders and the user approves once, input during a turn
  (merge, queue, reject, interrupt), revision (resolve by key, diff, secure the replacement,
  compensate, correct, record), and crash recovery from the store. `submit()` returns the
  acknowledgement as soon as the input is persisted; the session then acts on inputs one at a
  time, in arrival order, so input that arrives during a revision waits for the new turn.
- `tools.py`: each tool's contract: effect, consent, whether its service takes idempotency
  keys, can look them up, and can close them, and how long a revision waits for a reply
  that can't be looked up.
- `services.py`: a fake restaurant and a fake messaging service. Each request runs as its
  own server-side task, so a caller that stops waiting doesn't stop the work. `hold()` delays
  a request's arrival or its reply, which is how a scenario lands input at an exact moment.
- `store.py`: what survives a restart: inbox (with what has been acted on), dedupe IDs,
  operations log, the running turn's record, pending consent requests, history. Recovery
  resolves in-flight operations, resumes the turn, and replays acknowledged input.
- `scenarios.py`: every case the articles show. `demo-output.txt` is a saved run of all of them.
  Scenarios deliver most input with `_send()`, which waits for the session to act on it before
  the next input arrives, so each log reads in arrival order.
- `tests/`: 72 tests. The ones at the end of each file cover combinations (input during a
  revision, crashes at each point, tampered fields, typed answers) and the claims the
  articles make about this runtime.

## Session options the scenarios switch

| Option | Default | Other values |
|---|---|---|
| `authority` | `origin` (consent from the verified source) | `role` (read off the message role: the bug) |
| `encoding` | `attributed` (system input as attributed data) | `as_user` |
| `question` | `runtime` (rendered from the pending action) | `model` (the source's own framing) |
| `during` | `merge` | `queue`, `reject`, `interrupt` |
| `notices` | `now` (delivered on arrival; no plan, no consent) | `boundary` (between steps, by deadline), `queue` |
| `key_scope` | `operation` | `turn`, `step` |
| `reconcile` | `True` | `False` (cancel and restart) |
| `on_revise` | `contract` (per tool) | `boundary` (always wait) |
| `fencing` | `True` (close the key when resolving) | `False` (look it up only) |
| `order` | `new-first` | `old-first` |

## Scenarios by section

Each log in the articles is one of these runs. `--history` shows the model's history and
`--messages` the history as a user/assistant API receives it.

| Article: section | Scenarios |
|---|---|
| Turns: opening, and authority lives in the turn's origin | `offer`, `offer-as-user`, `offer-as-user-origin --history`, `offer --messages` |
| Turns: consent is a single-use action | `offer-hostile-model-question --history`, `offer-hostile`, `offer-approve`, `offer-approved-by-webhook`, `offer-stale-approval`, `offer-approve-twice`, `approval-during-turn` |
| Turns: input that arrives while a turn is running | `during-merge`, `during-queue`, `during-reject`, `during-merge-from-webhook`, `during-interrupt-turn-keys`, `during-interrupt`, `offer-rebook` |
| Turns: system input | `busy`, `busy-boundary`, `busy-queue-only`, `notice-last-step`, `tampered-and-replayed`, `crash-after-ack` |
| Revisions: opening, and stopping the wait doesn't stop the work | `revise-in-flight-naive`, `revise-in-flight` |
| Revisions: reading the revision | `revise-early`, `revise-unsure`, `revise-time`, `revise-keep`, `two-revisions`, `keep-then-revise` |
| Revisions: closing the key | `revise-late-arrival-unfenced`, `revise-late-arrival` |
| Revisions: keys name the operation | `key-reuse-lenient`, `key-reuse-strict`, `lost-response`, `lost-response-no-key` |
| Revisions: or wait for the call to finish | `revise-in-flight-wait`, `revise-after-text`, `revise-after-text-no-receipt`, `revise-after-text-naive` |
| Revisions: secure the replacement, then undo the old | `revise-full-old-first`, `revise-full`, `revise-fee`, `revise-fee-rises`, `revise-fee-declined`, `revise-refused`, `revise-no-lookup` |
| Revisions: a crash in the middle | `crash-in-memory`, `crash-durable`, `crash-during-text`, `crash-during-cancel` |
| Revisions: what the history says | `revise-after-text --history` |

## What this demo doesn't do

It shows each rule in one process. It isn't a hardened runtime, and these are known gaps:

- **Durability is simulated.** The store is a Python object standing in for a database; its
  "one write" is an assignment, and a crash is simulated by cancelling tasks.
- **One session, one running turn at a time.**
- **Revisions apply only to a running turn.** Text that revises a turn that has already
  ended, including one that ended waiting for a consent request, is read as text the runtime
  can't act on.
- **Open consent requests aren't rechecked against a revision,** except the fee requests the
  revised turn raised itself: those are voided and asked again if the new plan still releases
  the table. A restaurant's offer that is still open, for example, is left as it is.
- **Offers aren't versioned.** An approval is checked for single use and expiry, not against
  the offer's current state; if the slot has gone, the booking step fails.
- **Events are acted on in arrival order.** There are no sequence numbers, and events aren't
  used as hints to re-read the current state. A redelivery after the three-day dedupe window
  is acted on again.
- **Supersession matches a `topic` string** the sender sets; there's no entity resolution.
- **No provider message format.** The history places each effect where it happened; it doesn't
  pair tool calls with results the way a model API requires, so there are no placeholder results.
- **A correction isn't ordered after a delayed original.**
- **No guard against compensating an effect the replacement just returned;**
  `key-reuse-lenient` shows the failure.
- **Narrow language.** The scripted model reads only the phrases in `model.py`; anything else
  is new, unrelated input. The unsure pause understands "instead", "as well" and "both".
- **The user channel is trusted by its source name;** only webhooks are verified.
- **Time is a minute counter** the scenarios set, for deadlines, approvals, the signature
  tolerance and the dedupe window. The bounded wait uses real time (50 ms).
