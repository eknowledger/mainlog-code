from .pipeline import record


class Node:
    def __rshift__(self, other):
        record(self, other)
        return other

    def __repr__(self):
        return type(self).__name__


class OpusDecoder(Node):
    pass


class OpusEncoder(Node):
    pass
