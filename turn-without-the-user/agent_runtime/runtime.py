"""The runtime: who may start a turn, what a turn may do, what happens to input during one,
and how a revision is reconciled with work already under way."""
import asyncio
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field, fields, replace

from .model import Intent, ScriptedModel
from .services import FeeRequired, Full, KeyClosed, KeyMismatch
from .store import Store
from .tools import CONTRACTS

INTERNAL = {"timer"}        # sources inside the runtime's own process: nothing to verify
TOLERANCE = 5               # minutes a signed timestamp may be off before the input is refused
DONE = ("committed", "assumed", "compensated")


UNSIGNED = ("signature", "at", "key")          # the signature itself, and what the receiver sets


def _payload(inp):
    """Every field the sender supplies, so that none of them can change in transit."""
    data = {f.name: getattr(inp, f.name) for f in fields(inp) if f.name not in UNSIGNED}
    data["intent"] = asdict(inp.intent) if inp.intent else None
    return json.dumps(data, sort_keys=True)


def sign(secret, inp):
    """What a webhook sender attaches: an HMAC over its timestamp and the whole payload."""
    return hmac.new(secret, _payload(inp).encode(), hashlib.sha256).hexdigest()


@dataclass
class Input:
    """An utterance or event, split into the parts a runtime decides about separately."""
    source: str                 # who produced it: "user", "restaurant", "cinema", "timer"
    text: str = ""              # the words as received; the history records these
    intent: Intent = None       # the structured request or report, when the source sends one
    relation: str = None        # to the running turn: "adds", "revises"... (the model reads it if unset)
    event_id: str = None        # the sender's ID, for deduplicating redeliveries
    sent_at: int = None         # the sender's timestamp, covered by the signature
    signature: str = None       # proves which sender it came from, and that nothing changed
    priority: int = 0           # among queued inputs with the same deadline, higher goes first
    deadline: int = None        # minute after which it is no longer worth acting on
    topic: str = None           # what it is about, so a later input can make it moot
    supersedes: str = None      # the topic this input makes moot
    approves: str = None        # the consent request (action ID) a user's "yes" answers
    at: int = 0                 # minute it arrived
    key: tuple = None           # its inbox key once admitted


@dataclass
class Op:
    """One logical operation on the outside world. Written to the store before the first send."""
    id: str
    key: str                    # the idempotency key, derived from the operation, not the attempt
    step: str
    args: dict
    turn: str
    state: str = "planned"      # planned, sent, done, committed, unknown, assumed, failed,
                                # cancelled, compensated
    result: str = None
    sent_at: int = None
    at: int = None              # timeline position when the effect happened
    release: bool = False       # no longer wanted: compensate once a replacement holds
    charged: int = None         # for a cancellation: the fee the reply says was charged


@dataclass
class Action:
    """A consent request: exactly what will run if the user approves it, once, in time."""
    id: str
    steps: list                 # [(tool, args), ...], fixed when the question is asked
    origin: str
    asked_at: int
    ttl: int = 10
    used: bool = False
    op: str = None              # for a compensation: the operation it would undo


@dataclass
class Turn:
    id: str
    origin: str                 # who started it; decides what it may do without asking
    intent: Intent
    steps: list
    fixed: list = None          # an approved action's (tool, args): run exactly these
    correction: bool = False    # an earlier message has to be corrected, not repeated
    release: list = field(default_factory=list)    # op IDs to compensate once a replacement holds
    kept: list = field(default_factory=list)       # op IDs that already satisfy the intent
    reuse: list = field(default_factory=list)      # ops of the turn this one restarts or resumes
    facts: list = field(default_factory=list)      # what to tell the user when it ends
    asked: list = field(default_factory=list)      # op IDs whose compensation waits for consent
    ops: list = field(default_factory=list)
    held: list = field(default_factory=list)
    pos: int = 0                # next step to run
    pending: dict = None        # an unsure revision's changes, waiting for the user's answer
    pause: bool = False
    stop: bool = False          # set by a revision: stop at the next boundary
    superseded: bool = False
    done: bool = False
    task: asyncio.Task = None


class Session:
    def __init__(self, timeline, restaurant, messages, *, store=None, session_id="s1",
                 incarnation="a", secrets=None, contracts=None, during="merge",
                 notices="now", encoding="attributed", authority="origin",
                 question="runtime", reconcile=True, on_revise="contract", fencing=True,
                 order="new-first", key_scope="operation", dedupe_window=3 * 24 * 60):
        self.tl, self.restaurant, self.messages = timeline, restaurant, messages
        self.store = store or Store()
        self.session_id, self.incarnation = session_id, incarnation
        self.secrets = secrets or {}
        self.contracts = {**CONTRACTS, **(contracts or {})}
        self.during = during            # user input that adds to a running turn: merge, queue, reject, interrupt
        self.notices = notices          # notices: send "now", at the turn's next "boundary", or "queue" behind it
        self.encoding = encoding        # system input in the history: "attributed" or "as_user"
        self.authority = authority      # where consent comes from: the turn's "origin", or the history "role"
        self.question = question        # consent question written by the "runtime" or the "model"
        self.reconcile = reconcile      # on revision: resolve, diff, compensate (else cancel and restart)
        self.on_revise = on_revise      # in-flight calls: per "contract", always wait ("boundary"), or cancel
        self.fencing = fencing          # resolve by closing the key, not just looking it up
        self.order = order              # "new-first": secure the replacement, then compensate
        self.key_scope = key_scope      # "operation", "turn" or "step"
        self.dedupe_window = dedupe_window   # minutes; at least the sender's retry window
        self.model = ScriptedModel()
        self.active = None
        self.queue = []
        self.held_notices = []
        self.clock = 0
        self.dead = False
        self._lock = asyncio.Lock()     # input is acted on one at a time, in arrival order
        self._tasks = []                # admissions and turns, so idle() and crash() can find them
        self._admissions = []

    @property
    def history(self):
        return self.store.history

    # ---- admission -----------------------------------------------------------

    async def submit(self, inp):
        """Receive one input. Returns "ack" once it is safe for the sender to stop retrying."""
        inp.at = self.clock
        label = inp.text or inp.intent.describe()
        self.tl.add("input", f"{inp.source}: {label}" + (f" [{inp.event_id}]" if inp.event_id else ""))
        reason = self._unverified(inp)
        if reason:
            self.tl.add("input.rejected", reason)
            return None
        self._forget_old_ids()
        key = (inp.source, inp.event_id) if inp.event_id else ("local", len(self.store.inbox))
        if key in self.store.seen:
            self.tl.add("input.dropped", f"duplicate delivery of {inp.event_id}")
            return "ack"
        self.store.admit(key, inp, self.clock)               # dedupe entry and inbox, one write...
        if inp.event_id is not None:
            self.tl.add("input.persisted", f"{inp.event_id}; now acknowledged")   # ...then ack
        self._schedule(inp)                                  # ...then act, after the ack
        return "ack"

    def _schedule(self, inp):
        task = asyncio.get_running_loop().create_task(self._admit_in_order(inp))
        self._tasks.append(task)
        self._admissions.append(task)

    async def admitted(self):
        """Wait until the input received so far has been acted on; turns may still be running."""
        while True:
            pending = [t for t in self._admissions if not t.done()]
            if not pending:
                return
            await asyncio.wait(pending)

    async def _admit_in_order(self, inp):
        async with self._lock:          # a revision settles before the next input is read
            if not self.dead:
                await self._admit(inp)

    def _unverified(self, inp):
        if inp.source == "user" or inp.source in INTERNAL:
            return None
        secret = self.secrets.get(inp.source)
        if secret is None or inp.signature is None or not hmac.compare_digest(inp.signature, sign(secret, inp)):
            return f"signature doesn't match {inp.source}'s secret"
        if inp.sent_at is None or abs(self.clock - inp.sent_at) > TOLERANCE:
            return f"signed at m{inp.sent_at}, now m{self.clock}: outside the {TOLERANCE}-minute tolerance"
        return None

    def _forget_old_ids(self):
        self.store.seen = {k: t for k, t in self.store.seen.items() if self.clock - t <= self.dedupe_window}

    def _done(self, inp):
        if inp.key in self.store.inbox:
            self.store.inbox[inp.key]["done"] = True

    async def _admit(self, inp):
        if inp.approves:
            return self._approve(inp)
        if inp.topic is not None and inp.topic in self.store.moot:
            self.tl.add("input.superseded", f"\"{inp.text}\": {inp.topic} is already moot")
            return self._done(inp)
        if inp.supersedes:
            self._supersede(inp)
        if inp.intent is not None and inp.intent.withdraws:
            self._withdrawn(inp)
        notice = inp.intent is not None and inp.intent.kind == "notice"
        turn = self.active
        if notice and (turn is None or self.notices == "now"):
            return await self._notify(inp, turn)
        if turn is None:
            if inp.intent is None:
                return self._idle_text(inp)
            return self._start(inp)
        if turn.pause and inp.source == "user":
            return await self._answer(inp)
        if notice:
            return self._hold_notice(inp)
        if inp.source != turn.origin:                     # only the turn's origin may change it
            return self._enqueue(inp, why=f"from {inp.source}; {turn.id}'s origin is {turn.origin}")
        relation, changes = (inp.relation, {}) if inp.relation else self.model.read(inp.text)
        self.tl.add("model.read", f"{relation} {turn.id}" + self._show(changes))
        if relation in ("declines", "approves"):
            return self._answer_request(inp, relation)
        if relation == "revises":
            if self.reconcile:
                return await self._revise(inp, changes)
            return await self._restart(inp, replace(turn.intent, **changes))
        if relation == "unsure":
            return self._ask_which(inp, changes)
        if relation == "adds":
            return await self._during(inp, changes)
        return self._enqueue(inp)                             # independent, or a new request

    @staticmethod
    def _show(changes):
        return ": " + ", ".join(f"{k}={v}" for k, v in changes.items()) if changes else ""

    async def _during(self, inp, changes):
        turn = self.active
        if self.during == "merge":
            turn.held.append((inp, changes))
            self.tl.add("input.held", f"until {turn.id}'s next step")
        elif self.during == "queue":
            self._enqueue(inp, Intent(kind="tell", day=turn.intent.day, time=turn.intent.time,
                                      party=turn.intent.party, notify=changes["notify"]))
        elif self.during == "reject":
            self.tl.add("input.rejected", f"{turn.id} is still running; told the user")
            self._record_input(inp)
            self._done(inp)
            self._say(f"Still working on \"{turn.intent.describe()}\"; I haven't acted on "
                      f"\"{inp.text}\". Send it again when I'm done.")
        elif self.during == "interrupt":
            await self._restart(inp, turn.intent.adding(changes["notify"]))

    def _enqueue(self, inp, intent=None, why=None):
        self.queue.append((inp, intent or inp.intent))
        self.queue.sort(key=lambda q: (q[0].deadline if q[0].deadline is not None else 10 ** 9,
                                       -q[0].priority, q[0].at))
        due = f", deadline m{inp.deadline}" if inp.deadline is not None else ""
        self.tl.add("input.queued", f"behind {self.active.id}{due}" + (f": {why}" if why else ""))

    def _hold_notice(self, inp):
        if self.notices == "queue":
            return self._enqueue(inp)
        self.held_notices.append(inp)
        due = f" (deadline m{inp.deadline})" if inp.deadline is not None else ""
        self.tl.add("input.held", f"notice for {self.active.id}'s next step{due}")

    def _supersede(self, inp):
        self.store.moot.add(inp.supersedes)
        moot = [q[0] for q in self.queue if q[0].topic == inp.supersedes]
        moot += [n for n in self.held_notices if n.topic == inp.supersedes]
        for m in moot:
            self.tl.add("input.superseded", f"\"{m.text}\" by {inp.event_id or inp.source}")
            self._done(m)
        self.queue = [q for q in self.queue if q[0].topic != inp.supersedes]
        self.held_notices = [n for n in self.held_notices if n.topic != inp.supersedes]

    def _withdrawn(self, inp):
        """The source reports that an effect it holds is gone: the record has to say so."""
        self.store.withdrawn.add(inp.intent.withdraws)
        for op in self.store.ops.values():
            if op.step == "reserve_table" and op.result == inp.intent.withdraws and op.state == "committed":
                op.state = "cancelled"
                self.tl.add("effect.gone", f"{op.result}: reported by {inp.source}")

    def _idle_text(self, inp):
        relation, _ = self.model.read(inp.text)
        if relation in ("declines", "approves"):
            return self._answer_request(inp, relation)
        self._record_input(inp)
        self._done(inp)
        self.tl.add("model.read", f"{relation}: nothing running to apply it to")
        self._say("I'm not sure what you'd like me to do; nothing is in progress.")

    # ---- the history the model reads ----------------------------------------

    def _role_for(self, inp):
        return "user" if inp.source == "user" or self.encoding == "as_user" else "event"

    def _origin(self, inp):
        if self.authority == "role":                 # the bug: authority read off the message role
            return "user" if self._role_for(inp) == "user" else inp.source
        return inp.source                           # authority from the verified source

    def _record(self, item, at=None):
        """Place each record where it happened, not where the runtime learned of it."""
        item["at"] = len(self.tl.events) if at is None else at
        i = len(self.history)
        while i > 0 and self.history[i - 1]["at"] > item["at"]:
            i -= 1
        self.history.insert(i, item)

    def _record_input(self, inp):
        role = self._role_for(inp)
        item = {"role": role, "content": inp.text or inp.intent.describe()}
        if role == "event":
            item["source"] = inp.source
        self._record(item)

    def _say(self, text):
        self._record({"role": "assistant", "content": text})

    # ---- turns -----------------------------------------------------------------

    def _new_turn_id(self):
        self.store.counters["turn"] = self.store.counters.get("turn", 0) + 1
        return f"T{self.store.counters['turn']}"

    def _write_turn(self, turn):
        """The running turn's record: what a new process needs to resume it."""
        self.store.turn = {"id": turn.id, "origin": turn.origin, "intent": turn.intent, "fixed": turn.fixed,
                           "correction": turn.correction, "release": turn.release, "kept": turn.kept,
                           "facts": turn.facts}

    def _start(self, inp, *, intent=None, origin=None, fixed=None, correction=False,
               release=(), kept=(), reuse=(), facts=(), resume=None, tid=None):
        tid = resume or tid or self._new_turn_id()
        origin = origin or self._origin(inp)
        intent = intent or inp.intent
        if inp is not None and intent.kind == "notice" and inp.deadline is not None \
                and self.clock > inp.deadline:
            self.tl.add("input.expired", f"\"{inp.text}\" (deadline m{inp.deadline}, now m{self.clock})")
            self._done(inp)
            return None
        steps = [s for s, _ in fixed] if fixed else self.model.plan(intent)
        turn = Turn(tid, origin, intent, steps, fixed=fixed, correction=correction,
                    release=list(release), kept=list(kept), reuse=list(reuse), facts=list(facts))
        if inp is not None:
            self._record_input(inp)
            self._done(inp)
        self._write_turn(turn)
        self.active = turn
        self.tl.add("turn.resume" if resume else "turn.start", f"{tid} origin={origin}")
        turn.task = asyncio.get_running_loop().create_task(self._run(turn))
        self._tasks.append(turn.task)
        return turn

    async def _run(self, turn):
        try:
            while turn.pos < len(turn.steps):
                if turn.stop:
                    return
                await self._boundary(turn)          # the one place a plan may change
                if turn.pause:
                    self.tl.add("turn.paused", f"{turn.id} before {turn.steps[turn.pos]}")
                if turn.stop or turn.pause:
                    return
                step = turn.steps[turn.pos]
                if self._satisfied(turn, step):
                    turn.pos += 1
                    continue
                if self.contracts[step].needs_consent and turn.origin != "user":
                    self._ask(turn, turn.steps[turn.pos:])
                    turn.done = True
                    return
                op = self._op(turn, step)
                turn.pos += 1
                if op.state in DONE:
                    self.tl.add("skip", f"{step}: {op.id} already {op.state}" +
                                (f" as {op.result}" if op.result else ""))
                    self._fact(turn, op)
                elif not await self._execute(op, turn):
                    turn.done = True
                    return
                if op.step == "reserve_table" and turn.release:
                    await self._release(turn)
            if not turn.stop:
                turn.done = True
        finally:
            if not self.dead and not turn.superseded and turn.done:
                await self._deliver_held(turn)
                if turn.facts:
                    self._say(self.model.reply(turn.facts))
                self.tl.add("turn.end", turn.id)
                self._finish(turn)

    async def _boundary(self, turn):
        for inp, changes in turn.held:
            turn.intent = turn.intent.adding(changes.get("notify", ()))
            self._record_input(inp)
            self._done(inp)
            self.tl.add("input.merged", f"{turn.id} now: {turn.intent.describe()}")
        turn.held.clear()
        if self.store.turn is not None:
            self.store.turn["intent"] = turn.intent
        await self._deliver_held(turn)

    async def _deliver_held(self, turn):
        due = sorted(self.held_notices, key=lambda n: n.deadline if n.deadline is not None else 10 ** 9)
        self.held_notices = []
        for n in due:
            await self._notify(n, turn)

    async def _notify(self, inp, turn):
        self._done(inp)
        if inp.deadline is not None and self.clock > inp.deadline:
            self.tl.add("input.expired", f"\"{inp.text}\" (deadline m{inp.deadline}, now m{self.clock})")
            return
        self._record_input(inp)
        await self.messages.send("you", inp.intent.note)
        where = "now" if turn is None or self.notices == "now" else f"at {turn.id}'s boundary"
        self.tl.add("notice.sent", f"{where}: {inp.intent.note!r}")

    def _satisfied(self, turn, step):
        for op_id in turn.kept:
            op = self.store.ops.get(op_id)
            if op is None or op.state != "committed":
                continue
            if op.step == "reserve_table" and step in ("check_availability", "reserve_table"):
                if step == "reserve_table":
                    self.tl.add("skip", f"{step}: {op.result} already matches")
                return True
            if op.step == step:
                self.tl.add("skip", f"{step}: already done as {op.result}")
                return True
        return False

    def _op(self, turn, step, args=None):
        if args is None:
            args = dict(turn.fixed[turn.pos][1]) if turn.fixed else \
                self.model.args(step, turn.intent, turn.correction)
        c = self.contracts[step]
        for op in turn.reuse:                       # the turn this one restarts or resumes
            same_operation = c.keyed and self.key_scope == "operation" and op.state in ("sent", "unknown")
            if op.step == step and op.args == args and not op.release and (same_operation or op.state in DONE):
                op.turn = turn.id
                turn.ops.append(op)
                return op
        self.store.counters["op"] += 1
        op_id = f"op-{self.incarnation}{self.store.counters['op']}"
        key = None
        if c.keyed:
            key = {"operation": f"{self.session_id}:{op_id}", "turn": f"{turn.id}:{step}",
                   "step": f"{self.session_id}:{step}"}[self.key_scope]
        op = Op(op_id, key, step, args, turn.id)
        if c.effect != "none":
            self.store.ops[op_id] = op              # persisted before the first send
        turn.ops.append(op)
        return op

    async def _execute(self, op, turn):
        c = self.contracts[op.step]
        shown = ", ".join(str(v) for v in op.args.values())
        self.tl.add("tool.sent", f"{op.turn} {op.step}({shown})" + (f" key={op.key}" if op.key else ""))
        op.state, op.sent_at = "sent", len(self.tl.events)
        try:
            try:
                result = await self._invoke(op)
            except TimeoutError:
                self.tl.add("tool.unknown", f"{op.step}: no reply; sending the same request again")
                result = await self._invoke(op)
        except asyncio.CancelledError:
            if not self.dead:
                op.state = "unknown" if self.reconcile and c.effect != "none" else "cancelled"
                self.tl.add("tool." + op.state, f"{op.turn} {op.step}: stopped waiting")
            raise
        except (Full, KeyMismatch, KeyClosed) as e:
            op.state = "failed"
            self.tl.add("tool.failed", f"{op.turn} {op.step}: {type(e).__name__}")
            self._blocked(turn, op, "full" if isinstance(e, Full) else "rejected")
            return False
        except FeeRequired as e:
            op.state = "failed"
            self.tl.add("tool.failed", f"{op.turn} {op.step}: the fee is now {e.args[0]}, "
                                       f"above the approved {op.args.get('fee')}")
            held = next((o.id for o in self.store.ops.values()
                         if o.step == "reserve_table" and o.result == op.args["id"]), None)
            self._ask_fee(turn, op.args["id"], e.args[0], held)
            return False
        op.result = result
        if op.step == "reserve_table" and result in self.store.withdrawn:
            op.state = "cancelled"                  # a replayed result for an effect already withdrawn
            self.tl.add("effect.gone", f"{result}: replayed, but already withdrawn by the restaurant")
            turn.facts.append(("gone", (self._name(result),)))
            return False
        if op.step == "check_availability" and result == "full":
            op.state = "done"
            self.tl.add("tool.result", f"{op.turn} {op.step} -> full")
            self._record({"role": "tool", "content": self._describe(op)})
            self._blocked(turn, op, "full")
            return False
        op.state = "done" if c.effect == "none" else "committed"
        op.at = (self.restaurant.commit_at.get(result) if op.step == "reserve_table"
                 else self.messages.delivered_at.get(result) if op.step in ("text_group", "notify_user")
                 else None)
        self.tl.add("tool.result", f"{op.turn} {op.step} -> {result}")
        if op.step == "cancel_reservation":
            for held in self.store.ops.values():
                if held.step == "reserve_table" and held.result == op.args["id"] and held.state == "committed":
                    held.state = "compensated"
        self._record_effect(op)
        self._fact(turn, op)
        return True

    async def _invoke(self, op):
        a = op.args
        if op.step == "check_availability":
            return await self.restaurant.check(a["day"], a["time"], a["party"])
        if op.step == "reserve_table":
            return await self.restaurant.reserve(a["day"], a["time"], a["party"], key=op.key)
        if op.step == "text_group":
            return await self.messages.send(a["to"], a["text"])
        if op.step == "notify_user":
            return await self.messages.send("you", a["text"])
        if op.step == "cancel_reservation":
            rid = await self.restaurant.cancel(a["id"], max_fee=a.get("fee"))
            op.charged = self.restaurant.charged.get(rid, 0)     # the reply says what was charged
            return rid
        raise ValueError(op.step)

    def _describe(self, op, note=""):
        a = op.args
        if op.step == "check_availability":
            return f"{a['day']} {a['time']} for {a['party']}: {op.result}"
        if op.step == "reserve_table":
            if op.state == "unknown":
                return f"reserve_table {a['day']} {a['time']} for {a['party']}: outcome unknown{note}"
            return f"booked {op.result} ({a['day']} {a['time']} for {a['party']}){note}"
        if op.step == "cancel_reservation":
            return f"cancelled {a['id']}" + (f" (fee {op.charged}, approved)" if op.charged else "") + note
        if op.step == "text_group":
            return f"texted {a['to']}: {a['text']!r}{note}"
        return f"told you: {a['text']!r}{note}"

    def _record_effect(self, op, note=""):
        self._record({"role": "tool", "content": self._describe(op, note),
                      "effect": self.contracts[op.step].effect != "none"}, at=op.at)

    def _fact(self, turn, op):
        a = op.args
        unconfirmed = "_unconfirmed" if op.state == "assumed" else ""   # sent, never acknowledged
        if op.step == "reserve_table":
            turn.facts.append(("booked", (f"{a['day']} {a['time']} for {a['party']} ({op.result})",)))
        elif op.step == "cancel_reservation":
            fee = f", fee {op.charged}" if op.charged else ""
            turn.facts.append(("cancelled" + unconfirmed, (f"{self._name(a['id'])}{fee}",)))
        elif op.step == "text_group":
            turn.facts.append(("texted" + unconfirmed, (a["to"] + (" a correction" if turn.correction else ""),)))

    def _name(self, rid):
        """A reservation as the user knows it: 'R1 (Fri 19:00)'."""
        for op in self.store.ops.values():
            if op.step == "reserve_table" and op.result == rid:
                return f"{rid} ({op.args['day']} {op.args['time']})"
        return rid

    def _blocked(self, turn, op, why):
        a = op.args
        turn.facts.append((why, (f"{a['day']} {a['time']}",)))
        for op_id in turn.release:
            old = self.store.ops.get(op_id)
            if old is not None and old.state == "committed":
                old.release = False
                turn.facts.append(("still_held", (f"{old.result} ({old.args['day']} {old.args['time']})",)))

    def _finish(self, turn):
        if self.active is turn:
            self.active = None
            self.store.turn = None
            self._next()

    def _next(self):
        while self.store.approved and self.active is None:
            self._run_action(self.store.actions[self.store.approved.pop(0)])
        while self.queue and self.active is None:
            inp, intent = self.queue.pop(0)
            if inp.deadline is not None and self.clock > inp.deadline:
                self.tl.add("input.expired", f"\"{inp.text}\" (deadline m{inp.deadline}, now m{self.clock})")
                self._done(inp)
                continue
            if intent is None:                  # text with nothing to plan, such as a question
                self._idle_text(inp)
                continue
            self._start(inp, intent=intent)

    async def idle(self):
        """Wait until every admitted input has been acted on and no turn is running."""
        while True:
            pending = [t for t in self._tasks if not t.done()]
            if not pending:
                break
            await asyncio.wait(pending)
            await asyncio.sleep(0)
        for t in self._tasks:
            if not t.cancelled() and t.exception() is not None:
                raise t.exception()

    # ---- consent ---------------------------------------------------------------

    def _new_action(self, steps, origin, op=None):
        self.store.counters["action"] += 1
        action = Action(f"A{self.store.counters['action']}", steps, origin, self.clock, op=op)
        self.store.actions[action.id] = action
        return action

    def _ask(self, turn, remaining):
        steps = [(s, self.model.args(s, turn.intent)) for s in remaining
                 if self.contracts[s].effect != "none"]
        action = self._new_action(steps, turn.origin)
        text = self._render(action) if self.question == "runtime" else self.model.ask(turn.intent)
        self.tl.add("turn.ask", f"{turn.id}: {action.id} needs the user's consent")
        turn.facts.append(("told", (text,)))

    def _ask_fee(self, turn, rid, fee, op_id):
        action = self._new_action([("cancel_reservation", {"id": rid, "fee": fee})], "user", op=op_id)
        self.tl.add("compensate.ask", f"cancelling {rid} costs a {fee} fee: {action.id} needs the user's consent")
        turn.facts.append(("fee", (self._name(rid), fee, action.id)))
        if op_id is not None:
            turn.asked.append(op_id)

    def _render(self, action):
        parts = []
        for tool, a in action.steps:
            if tool == "reserve_table":
                parts.append(f"book a table for {a['party']} on {a['day']} at {a['time']}")
            elif tool == "text_group":
                parts.append(f"text {a['to']}: \"{a['text']}\"")
            elif tool == "cancel_reservation":
                parts.append(f"cancel {a['id']}" + (f" for a {a['fee']} fee" if "fee" in a else ""))
        body = "; then ".join(parts)
        source = f" (from {action.origin})" if action.origin != "user" else ""
        return f"{action.id}{source}: {body[0].upper() + body[1:]}. Approve within {action.ttl} minutes?"

    def _approve(self, inp):
        self._record_input(inp)
        self._done(inp)
        action = self.store.actions.get(inp.approves)
        if inp.source != "user":
            reason = f"approvals come only from the user, not from {inp.source}"
        elif action is None:
            reason = "no such request"
        elif action.used or action.id in self.store.approved:
            reason = "already used"
        elif self.clock - action.asked_at > action.ttl:
            reason = f"expired (asked at m{action.asked_at}, now m{self.clock})"
        else:
            reason = None
        if reason:
            self.tl.add("approval.rejected", f"{inp.approves}: {reason}")
            if inp.source == "user":
                self._say(f"I can't use that approval ({inp.approves}): {reason}.")
            return None
        if self.active is not None:                 # checked now, run when the current turn ends
            self.store.approved.append(action.id)
            self.tl.add("approval", f"{action.id}: queued until {self.active.id} ends")
            return None
        return self._run_action(action)

    def _run_action(self, action):
        action.used = True                          # used once its turn starts
        self.tl.add("approval", f"{action.id}: runs exactly its {len(action.steps)} step(s)")
        turn = self._start(None, intent=self._intent_of(action), origin="user", fixed=action.steps)
        if action.op is not None:
            turn.release = []
        return turn

    def _open_actions(self):
        return [a for a in self.store.actions.values()
                if not a.used and a.id not in self.store.approved and self.clock - a.asked_at <= a.ttl]

    def _answer_request(self, inp, relation):
        """A typed "yes" or "no" names no request, so it is only clear with exactly one open."""
        open_ = self._open_actions()
        if relation == "approves" and len(open_) == 1:
            inp.approves = open_[0].id
            return self._approve(inp)
        self._record_input(inp)
        self._done(inp)
        if not open_:
            return self._say("Nothing is waiting for your answer.")
        if len(open_) > 1:
            return self._say("Which request do you mean? Open: " + " or ".join(a.id for a in open_) + ".")
        return self._decline(open_[0])

    def _decline(self, action):
        action.used = True
        self.tl.add("approval.declined", action.id)
        if action.op is not None:
            op = self.store.ops[action.op]
            op.release = False
            self._say(f"Kept {self._name(op.result)}.")
        else:
            self._say("Okay, I won't do that.")

    @staticmethod
    def _intent_of(action):
        for tool, a in action.steps:
            if tool == "reserve_table":
                return Intent(day=a["day"], time=a["time"], party=a["party"])
        return Intent(notify=())

    # ---- revision --------------------------------------------------------------

    async def _revise(self, inp, changes):
        old = self.active
        new = replace(old.intent, **changes)
        self.tl.add("intent.revised", f"{old.id}: " + ", ".join(
            f"{k} {getattr(old.intent, k)} -> {v}" for k, v in changes.items()))
        await self._settle_in_flight(old)
        old.superseded = True
        self.active = None
        dropped = len(old.steps) - old.pos
        if dropped:
            self.tl.add("dropped", f"{dropped} planned step(s) of {old.id} never started")
        # Everything the old turn holds: its own calls, what it had kept or still owed, and
        # compensations it asked consent for. Those requests were asked under the old plan.
        for a in self._open_actions():
            if a.op in old.asked:
                a.used = True
                self.tl.add("approval.void", f"{a.id}: asked under the old plan")
        carried = [self.store.ops[i] for i in old.release + old.kept + old.asked if i in self.store.ops]
        seen, kept, release = set(), [], []
        correction = old.correction                 # a correction owed by an earlier revision is still owed
        facts = [f for f in old.facts if f[0] in ("corrected", "assumed", "unknown")]
        for op in old.ops + old.reuse + carried:    # reuse: what a resumed or restarted turn did before
            if op.id in seen:
                continue
            seen.add(op.id)
            if op.step == "reserve_table" and op.state == "committed":
                op.release = False
                if self._matches(op.args, new):
                    kept.append(op.id)
                    self.tl.add("keep", f"{op.result}: day, time and party still match")
                    facts.append(("kept", (self._name(op.result),)))
                else:
                    op.release = True
                    release.append(op.id)
            elif op.step == "reserve_table" and op.state == "unknown":
                facts.append(("unknown", (f"{op.args['day']} {op.args['time']}",)))
            elif op.step == "text_group" and op.state in ("committed", "assumed"):
                if op.args == self.model.args("text_group", new):
                    kept.append(op.id)
                else:
                    correction = True
                    self.tl.add("irreversible", f"{op.args['to']} already has {op.args['text']!r}; "
                                                "the new plan corrects it")
                    kind = "corrected" if op.state == "committed" else "assumed"
                    if (kind, ("The earlier text",)) not in facts:
                        facts.append((kind, ("The earlier text",)))
        self._record_input(inp)                     # the user's own words
        tid = None
        if release and self.order == "old-first":
            tid = self._new_turn_id()
            turn = Turn(tid, old.origin, new, [], correction=correction, release=release, kept=kept,
                        facts=facts)
            self._write_turn(turn)                  # the new plan is on record before anything is undone
            self._done(inp)
            await self._release(turn)
            facts, release = turn.facts, []
        elif release:
            self.tl.add("release", ", ".join(self.store.ops[r].result for r in release)
                        + " once the replacement holds")
        new_turn = self._start(None, intent=new, origin=old.origin, correction=correction,
                               release=release, kept=kept, facts=facts, tid=tid)
        new_turn.held.extend(old.held)              # added input waiting for a boundary goes along
        self._done(inp)                             # acted on only once the new turn is on record

    @staticmethod
    def _matches(args, intent):
        return (args["day"], args["time"], args["party"]) == (intent.day, intent.time, intent.party)

    async def _settle_in_flight(self, old):
        """Find out what the old turn's in-flight call did, as its contract allows."""
        op = next((o for o in old.ops if o.state == "sent"), None)
        old.stop = True
        if op is None:                               # between steps: it stops at the boundary
            await asyncio.wait({old.task})
            return
        c = self.contracts[op.step]
        wait = None if self.on_revise == "boundary" else (
            c.wait if not c.lookup and c.effect != "none" and self.on_revise == "contract" else 0)
        if wait is None or wait > 0:
            self.tl.add("waiting", f"for {op.step}" + (f", up to {int(wait * 1000)} ms" if wait else " to finish"))
            done, _ = await asyncio.wait({old.task}, timeout=wait)
            if done:
                return
            self.tl.add("wait.expired", f"{op.step}: still no reply")
        await self._stop(old)
        if op.state == "unknown":
            self._resolve(op)

    async def _stop(self, turn):
        turn.superseded = True
        turn.task.cancel()
        try:
            await turn.task
        except asyncio.CancelledError:
            pass

    def _resolve(self, op):
        c = self.contracts[op.step]
        if c.lookup and op.key:
            if c.fence and self.fencing:
                rid = self.restaurant.close(op.key)
                self.tl.add("resolve", f"{op.key}: {rid or 'nothing has landed'}; key closed")
            else:
                rid = self.restaurant.lookup(op.key)
                self.tl.add("resolve", f"{op.key}: {rid or 'nothing has landed'}")
            if rid:
                op.state, op.result, op.at = "committed", rid, self.restaurant.commit_at[rid]
                self._record_effect(op)
            else:
                op.state = "failed"
        elif c.effect == "irreversible":
            op.state = "assumed"
            self.tl.add("resolve", f"{op.step}: no reply, no lookup; treated as done")
            op.at = op.sent_at
            self._record_effect(op, note=" (not confirmed; treated as done)")
        else:
            self.tl.add("resolve", f"{op.step}: no reply, no lookup; outcome unknown")
            op.at = op.sent_at
            self._record_effect(op, note=" (no reply, no way to check)")

    async def _release(self, turn):
        for op_id in list(turn.release):
            op = self.store.ops.get(op_id)
            if op is None:
                self.tl.add("release.lost", f"{op_id}: no record of it survived")
            elif op.state == "committed":
                await self._compensate(op, turn)
        turn.release.clear()

    async def _compensate(self, op, turn):
        rid = op.result
        terms, fee = self.restaurant.cancel_terms(rid)
        if terms == "refused":
            self.tl.add("compensate.failed", f"{rid}: the restaurant won't cancel inside its window")
            self._record({"role": "tool", "content": f"could not cancel {rid}: refused inside the "
                                                    "cancellation window", "effect": True})
            turn.facts.append(("refused", (self._name(rid),)))
            op.release = False
            return
        if terms == "fee":
            op.release = False                      # kept unless the user approves the fee
            return self._ask_fee(turn, rid, fee, op.id)
        if turn.origin != "user":
            action = self._new_action([("cancel_reservation", {"id": rid})], turn.origin, op=op.id)
            self.tl.add("compensate.ask", f"{turn.origin} can't cancel {rid} for the user: {action.id}")
            turn.facts.append(("told", (self._render(action),)))
            turn.asked.append(op.id)
            return
        undo = self._op(turn, "cancel_reservation", args={"id": rid})
        if undo.state in DONE:
            self.tl.add("skip", f"cancel_reservation: {undo.id} already {undo.state}")
            op.state = "compensated"
            self._fact(turn, undo)
            return
        self.tl.add("compensate", f"{rid}: the replacement holds" if self.order == "new-first"
                    else f"{rid}: before securing a replacement")
        if await self._execute(undo, turn):
            op.state = "compensated"

    # ---- unsure input ------------------------------------------------------------

    def _ask_which(self, inp, changes):
        turn = self.active
        self._record_input(inp)
        self._done(inp)
        turn.pause, turn.pending = True, changes
        field_, value = next(iter(changes.items()))
        self.tl.add("input.unsure", f"{turn.id} stops at its next step until the user answers")
        self._say(f"Do you mean {value} instead of {getattr(turn.intent, field_)}, or as well?")

    async def _answer(self, inp):
        turn = self.active
        self.tl.add("model.read", f"answer to {turn.id}: {inp.text}")
        if inp.text == "instead":
            turn.pause = False
            turn.task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
            return await self._revise(inp, turn.pending)
        self._record_input(inp)
        self._done(inp)
        if inp.text not in ("as well", "both"):     # anything else: still unclear, ask again
            field_, value = next(iter(turn.pending.items()))
            return self._say(f"Sorry, I still need to know: {value} instead of "
                             f"{getattr(turn.intent, field_)}, or as well?")
        turn.pause = False
        self.queue.append((inp, replace(turn.intent, **turn.pending)))
        turn.task = asyncio.get_running_loop().create_task(self._run(turn))
        self._tasks.append(turn.task)
        return None

    # ---- naive restart -------------------------------------------------------------

    async def _restart(self, inp, intent):
        """Stop waiting and start again: no resolution, no diff, no compensation."""
        old = self.active
        await self._stop(old)
        self.active = None
        self.tl.add("turn.cancelled", f"{old.id}; restarting")
        self._start(inp, intent=intent, origin=old.origin, reuse=old.ops)

    # ---- crash and recovery ---------------------------------------------------------

    async def crash(self):
        """The process dies: nothing gets to clean up or write down what it was doing."""
        self.dead = True
        self.tl.add("crash", "process killed")
        pending = [t for t in self._tasks if not t.done()]
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def recover(self):
        """A new process, same store: resolve what was in flight, resume the turn, then
        replay any input that was acknowledged but not yet acted on."""
        self.tl.add("restart", f"{len(self.store.ops)} operation(s) in the log")
        for op in list(self.store.ops.values()):
            if op.state in ("sent", "unknown"):
                self._resolve(op)
        rec = self.store.turn
        if rec is not None:
            own = [op for op in self.store.ops.values() if op.turn == rec["id"]]
            self._start(None, intent=rec["intent"], origin=rec["origin"], fixed=rec["fixed"],
                        correction=rec["correction"], release=rec["release"], kept=rec["kept"],
                        reuse=own, facts=rec["facts"], resume=rec["id"])
        else:
            self._next()                            # approvals that were waiting for a turn to end
        for key, entry in list(self.store.inbox.items()):
            if not entry["done"]:
                self.tl.add("replay", f"{entry['input'].event_id or key}: acknowledged, not yet acted on")
                self._schedule(entry["input"])
        await self.idle()


def render_messages(history):
    """The history as a provider with only user and assistant roles would receive it.
    System input keeps the user's role out of it: it arrives as attributed data."""
    out = []
    for item in history:
        if item["role"] == "event":
            out.append({"role": "user", "content": f"<event from=\"{item['source']}\" verified=\"signature\">"
                                                   f"{item['content']}</event> (not written by the user)"})
        elif item["role"] == "tool":
            out.append({"role": "user", "content": f"<tool_result>{item['content']}</tool_result>"})
        else:
            out.append({"role": item["role"], "content": item["content"]})
    return out
