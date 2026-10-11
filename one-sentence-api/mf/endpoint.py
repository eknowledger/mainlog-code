from .nodes import Node


class Port(Node):
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    def __repr__(self):
        return f"{self.owner.kind}.{self.name}"


class Endpoint:
    """A media peer: a browser, a phone call, a streaming encoder."""

    def __init__(self, kind, **config):
        self.kind, self.config = kind, config
        self.audio_in, self.audio_out = Port(self, "audio_in"), Port(self, "audio_out")

    @classmethod
    def sip(cls, trunk, to):
        return cls("sip", trunk=trunk, to=to)

    async def transfer(self, destination):
        print(f"[endpoint] transferring call to {destination}")

    async def hangup(self):
        print("[endpoint] hanging up")
