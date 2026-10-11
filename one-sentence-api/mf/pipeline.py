_current = None


class Pipeline:
    """Container for a media graph. Nodes wired with >> are recorded here."""

    def __init__(self, name):
        global _current
        self.name, self.edges = name, []
        _current = self


def record(a, b):
    if _current is not None:
        _current.edges.append((a, b))
