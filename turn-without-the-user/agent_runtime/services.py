"""Fake external services. Their state is the ground truth the runtime has to track.

Each request runs as its own server-side task. Cancelling the caller's wait does not stop
it, the same way closing a socket doesn't recall a request that has already been sent.
"""
import asyncio


class Full(Exception):
    pass


class KeyClosed(Exception):
    pass


class KeyMismatch(Exception):
    pass


class FeeRequired(Exception):
    pass


class Refused(Exception):
    pass


class _Service:
    """hold(name) stops the next request that reaches a stage until release(name). Stages:
    "<op>.arrive" (the request is still travelling) and "<op>.reply" (the work is done and
    the reply is late). Holds apply to one request each, oldest released first."""

    def _init(self, timeline):
        self.tl = timeline
        self._gates = {}         # name -> gates waiting for a request to reach the stage
        self._held = {}          # name -> gates a request is stopped at
        self._tasks = []

    def hold(self, name):
        self._gates.setdefault(name, []).append(asyncio.Event())

    def release(self, name):
        waiting = self._held.get(name) or self._gates.get(name)
        waiting.pop(0).set()

    def release_all(self):
        for gates in list(self._held.values()) + list(self._gates.values()):
            for gate in gates:
                gate.set()
        self._held, self._gates = {}, {}

    async def _stage(self, name):
        gates = self._gates.get(name)
        if gates:
            gate = gates.pop(0)
            self._held.setdefault(name, []).append(gate)
            await gate.wait()
            if gate in self._held.get(name, []):
                self._held[name].remove(gate)

    async def _serve(self, work):
        task = asyncio.get_running_loop().create_task(work)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        self._tasks.append(task)
        return await asyncio.shield(task)

    async def drain(self):
        while any(not t.done() for t in self._tasks):
            await asyncio.wait([t for t in self._tasks if not t.done()])


class Restaurant(_Service):
    def __init__(self, timeline, *, strict_keys=True, full=(), cancel_policy="free", fee=20):
        self._init(timeline)
        self.reservations = {}   # id -> {"day", "time", "party", "status", "key"}
        self.commit_at = {}      # id -> timeline position when it was committed
        self._by_key = {}        # idempotency key -> reservation id
        self._closed = set()     # keys closed by a lookup; a later request with one is refused
        self.strict_keys = strict_keys   # reject a reused key whose parameters differ
        self.full = set(full)            # (day, time) slots with no tables left
        self.cancel_policy = cancel_policy   # "free", "fee" or "refuse"
        self.fee = fee
        self.charged = {}        # id -> fee charged when it was cancelled
        self.committed = asyncio.Event()
        self.lose_next_response = False

    async def check(self, day, time, party):
        async def work():
            await self._stage("check.reply")
            return "full" if (day, time) in self.full else "available"
        return await self._serve(work())

    async def reserve(self, day, time, party, key=None):
        return await self._serve(self._reserve(day, time, party, key))

    async def _reserve(self, day, time, party, key):
        await self._stage("reserve.arrive")
        if key is not None and key in self._closed:
            self.tl.add("restaurant", f"refused a late request: key {key} is closed")
            raise KeyClosed(key)
        if key is not None and key in self._by_key:
            rid = self._by_key[key]
            r = self.reservations[rid]
            if self.strict_keys and (r["day"], r["time"], r["party"]) != (day, time, party):
                self.tl.add("restaurant", f"rejected key {key}: it was used for {rid} {r['day']} {r['time']}")
                raise KeyMismatch(key)
            self.tl.add("restaurant", f"replayed {rid} for key {key}")
            await self._stage("reserve.reply")
            return rid
        if (day, time) in self.full:
            self.tl.add("restaurant", f"no table {day} {time}")
            raise Full(f"{day} {time}")
        rid = f"R{len(self.reservations) + 1}"
        self.reservations[rid] = {"day": day, "time": time, "party": party, "status": "booked", "key": key}
        if key is not None:
            self._by_key[key] = rid
        self.tl.add("restaurant", f"committed {rid} {day} {time} x{party}")
        self.commit_at[rid] = len(self.tl.events)
        self.committed.set()
        if self.lose_next_response:
            self.lose_next_response = False
            raise TimeoutError("response lost")
        await self._stage("reserve.reply")
        return rid

    async def wait_for(self, rid):
        while rid not in self.reservations:
            await asyncio.sleep(0)

    def lookup(self, key):
        return self._by_key.get(key)

    def close(self, key):
        """Look a key up and close it in one step: a request with this key that arrives
        from now on is refused."""
        self._closed.add(key)
        return self._by_key.get(key)

    def cancel_terms(self, rid):
        if self.reservations[rid]["status"] == "cancelled":
            return ("free", 0)
        if self.cancel_policy == "refuse":
            return ("refused", None)
        if self.cancel_policy == "fee":
            return ("fee", self.fee)
        return ("free", 0)

    async def cancel(self, rid, max_fee=None):
        """Cancel. When a fee applies, the caller must say the most it agreed to pay."""
        async def work():
            r = self.reservations[rid]
            if r["status"] == "cancelled":
                self.tl.add("restaurant", f"{rid} was already cancelled")
                return rid
            if self.cancel_policy == "refuse":
                self.tl.add("restaurant", f"refused to cancel {rid}: inside the cancellation window")
                raise Refused(rid)
            if self.cancel_policy == "fee" and (max_fee is None or self.fee > max_fee):
                if max_fee is not None:
                    self.tl.add("restaurant", f"refused to cancel {rid}: the fee is {self.fee}, "
                                              f"above the {max_fee} agreed")
                raise FeeRequired(self.fee)
            r["status"] = "cancelled"
            self.charged[rid] = self.fee if self.cancel_policy == "fee" else 0
            fee = f" (fee {self.fee})" if self.charged[rid] else ""
            self.tl.add("restaurant", f"cancelled {rid}{fee}")
            await self._stage("cancel.reply")
            return rid
        return await self._serve(work())

    def booked(self):
        return sorted(r for r, v in self.reservations.items() if v["status"] == "booked")


class Messages(_Service):
    """Sends texts. A sent text can't be taken back, and there is no way to ask afterwards
    whether a particular request was delivered."""

    def __init__(self, timeline):
        self._init(timeline)
        self.sent = []
        self.delivered_at = {}   # message id -> timeline position when it was delivered
        self.delivered = asyncio.Event()

    async def send(self, to, text):
        async def work():
            self.sent.append((to, text))
            self.tl.add("messages", f"delivered to {to}: {text!r}")
            mid = f"M{len(self.sent)}"
            self.delivered_at[mid] = len(self.tl.events)
            if to != "you":
                self.delivered.set()
            await self._stage("send.reply" if to != "you" else "notify.reply")
            return mid
        return await self._serve(work())
