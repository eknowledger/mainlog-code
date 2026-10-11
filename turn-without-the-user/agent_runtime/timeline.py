"""An append-only event log. Every component writes to the same one."""


class Timeline:
    def __init__(self):
        self.events = []

    def add(self, kind, detail):
        self.events.append((len(self.events) + 1, kind, detail))

    def kinds(self):
        return [kind for _, kind, _ in self.events]

    def render(self, start=0):
        return "\n".join(f"{n:>2}  {kind:<17} {detail}" for n, kind, detail in self.events[start:])
