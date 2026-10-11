"""RTP fixed header (RFC 3550 section 5.1) plus one RFC 6464 audio-level
header extension in the one-byte form of RFC 8285."""
import struct
from dataclasses import dataclass, field, replace

AUDIO_LEVEL_EXT_ID = 1  # negotiated in SDP in a real call


@dataclass(frozen=True)
class Packet:
    ssrc: int
    seq: int
    timestamp: int
    payload: bytes
    audio_level: int | None = None  # RFC 6464: 0 = loudest, 127 = silence (-dBov)
    csrcs: tuple[int, ...] = field(default_factory=tuple)
    payload_type: int = 111

    def to_bytes(self) -> bytes:
        ext = b""
        if self.audio_level is not None:
            element = bytes([(AUDIO_LEVEL_EXT_ID << 4) | 0, self.audio_level & 0x7F])
            element += b"\x00" * 2  # pad to a 32-bit word
            ext = struct.pack("!HH", 0xBEDE, len(element) // 4) + element
        first = 0x80 | (0x10 if ext else 0) | len(self.csrcs)
        header = struct.pack("!BBHII", first, self.payload_type & 0x7F,
                             self.seq & 0xFFFF, self.timestamp & 0xFFFFFFFF, self.ssrc)
        header += b"".join(struct.pack("!I", c) for c in self.csrcs)
        return header + ext + self.payload

    @classmethod
    def from_bytes(cls, data: bytes) -> "Packet":
        first, pt, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
        assert first >> 6 == 2, "not RTP version 2"
        cc, offset = first & 0x0F, 12
        csrcs = struct.unpack(f"!{cc}I", data[offset:offset + 4 * cc]) if cc else ()
        offset += 4 * cc
        level = None
        if first & 0x10:
            profile, words = struct.unpack("!HH", data[offset:offset + 4])
            body = data[offset + 4:offset + 4 + 4 * words]
            offset += 4 + 4 * words
            if profile == 0xBEDE and body and body[0] >> 4 == AUDIO_LEVEL_EXT_ID:
                level = body[1] & 0x7F
        return cls(ssrc, seq, ts, data[offset:], level, tuple(csrcs), pt & 0x7F)

    def with_(self, **changes) -> "Packet":
        return replace(self, **changes)
