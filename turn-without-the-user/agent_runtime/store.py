"""What a session persists. Pass the same Store to a restarted Session to recover."""


class Store:
    def __init__(self, keep_ops=True):
        self.keep_ops = keep_ops  # False: the operations log lives only in the process's memory
        self.seen = {}            # (source, event id) -> minute first seen, for dropping redeliveries
        self.inbox = {}           # the same key -> {"input", "done"}: admitted, and acted on yet?
        self.moot = set()         # topics a later input has made moot
        self.ops = {}             # operation id -> Op, written before the first send
        self.turn = None          # the running turn's record: intent, releases, kept effects
        self.actions = {}         # pending consent requests
        self.approved = []        # approved actions waiting for the running turn to end
        self.withdrawn = set()    # effects a source reported gone: a replay of one is stale
        self.history = []
        self.counters = {"op": 0, "action": 0}

    def admit(self, key, inp, minute):
        """One write: the dedupe entry and the inbox entry land together, or neither does."""
        inp.key = key
        self.seen[key] = minute
        self.inbox[key] = {"input": inp, "done": False}

    def restart(self):
        """The process died. Anything kept only in memory is gone."""
        if not self.keep_ops:
            self.ops = {}
