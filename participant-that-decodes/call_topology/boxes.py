"""The three boxes an AI agent can meet in a call: a selective forwarder,
a media-mixing mixer, and the agent endpoint itself."""
from array import array

from .e2e import open_
from .rtp import Packet


def encode(samples: list[int]) -> bytes:
    """The demo codec is raw 16-bit PCM, so encoding is just packing."""
    return array("h", samples).tobytes()


def decode(frame: bytes) -> list[int]:
    return array("h", frame).tolist()


class SelectiveForwarder:
    """Forwards the loudest sources to each receiver, reading headers only."""

    def __init__(self, max_speakers: int = 2):
        self.max_speakers = max_speakers
        self.levels: dict[int, int] = {}   # ssrc -> latest RFC 6464 level
        self.skipped: dict[tuple[str, int], int] = {}  # (receiver, ssrc) -> packets dropped on purpose

    def observe(self, data: bytes) -> None:
        """Read the level, never the audio. A real forwarder smooths it over time."""
        pkt = Packet.from_bytes(data)
        self.levels[pkt.ssrc] = pkt.audio_level          # 0 is loudest, 127 is silence

    def forward(self, data: bytes, sender: str, receivers: list[str]) -> dict[str, bytes]:
        pkt = Packet.from_bytes(data)
        loudest = sorted(self.levels, key=self.levels.get)[: self.max_speakers]
        out = {}
        for receiver in receivers:
            if receiver == sender:
                continue
            leg = (receiver, pkt.ssrc)
            if pkt.ssrc not in loudest:
                if leg in self.skipped:                  # leg already started
                    self.skipped[leg] += 1               # close the gap we made
                continue
            offset = self.skipped.setdefault(leg, 0)     # a real loss stays a gap
            out[receiver] = pkt.with_(seq=pkt.seq - offset).to_bytes()  # payload unchanged
        return out


def clip(sample: int) -> int:
    return max(-32768, min(32767, sample))


class Mixer:
    """Decodes every input and sends each listener a mix of everyone else."""

    def __init__(self, ssrc: int, key: bytes | None = None):
        self.ssrc, self.key, self.seq = ssrc, key, 0
        self.decodes = self.encodes = 0

    def decode_input(self, data: bytes) -> tuple[int, list[int]]:
        pkt = Packet.from_bytes(data)
        frame = open_(self.key, pkt.payload) if self.key else pkt.payload
        self.decodes += 1
        return pkt.ssrc, decode(frame)

    def mix_for(self, listener: int, frames: dict[int, list[int]], timestamp: int) -> bytes:
        others = [ssrc for ssrc in frames if ssrc != listener]    # never your own voice
        length = len(frames[others[0]])
        mixed = [clip(sum(frames[s][i] for s in others)) for i in range(length)]
        self.seq += 1
        self.encodes += 1
        return Packet(self.ssrc, self.seq, timestamp, encode(mixed),
                      csrcs=tuple(others)).to_bytes()             # who is in it, not their voices


class AgentEndpoint:
    """An ordinary endpoint that needs decoded audio for its model."""

    def __init__(self, names: dict[int, str], key: bytes | None = None):
        self.names, self.key = names, key       # ssrc -> name comes from signalling

    def hear(self, data: bytes) -> tuple[str, list[int]]:
        pkt = Packet.from_bytes(data)
        frame = open_(self.key, pkt.payload) if self.key else pkt.payload
        if pkt.csrcs:
            who = "mix of " + ", ".join(self.names[c] for c in pkt.csrcs)
        else:
            who = self.names[pkt.ssrc]
        return who, decode(frame)
