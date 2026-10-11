"""The STUN decoder tool's samples, made by aioice rather than typed, so the tool decodes what a real ICE stack sends.

    python samples.py

Three messages: an ICE connectivity check from Alice's browser to the assistant's server, the server's answer, and
the 401 a TURN server sends to an Allocate that has no credentials yet. Fixed transaction IDs and made-up
credentials, so the bytes are reproducible; the addresses are from the documentation ranges.
"""

from aioice import stun

PASSWORD = b"server+password+24+chars"  # ice-chars only: letters, digits, + and / (RFC 8839 section 5.4)


def check() -> bytes:
    m = stun.Message(message_method=stun.Method.BINDING, message_class=stun.Class.REQUEST, transaction_id=bytes.fromhex("8f1e2d3c4b5a69788796a5b4"))
    m.attributes["USERNAME"] = "srv7:alce"  # the receiver's ufrag, then the sender's (RFC 8445 section 7.2.2)
    m.attributes["PRIORITY"] = 2**24 * 110 + 2**8 * 65535 + 255  # what a peer-reflexive candidate from this check would get
    m.attributes["ICE-CONTROLLING"] = 0x1D2C3B4A59687786
    m.attributes["USE-CANDIDATE"] = None
    m.add_message_integrity(PASSWORD)
    return bytes(m)


def answer() -> bytes:
    r = stun.Message(message_method=stun.Method.BINDING, message_class=stun.Class.RESPONSE, transaction_id=bytes.fromhex("8f1e2d3c4b5a69788796a5b4"))
    r.attributes["XOR-MAPPED-ADDRESS"] = ("192.0.2.10", 54321)
    r.add_message_integrity(PASSWORD)
    return bytes(r)


def turn_401() -> bytes:
    e = stun.Message(message_method=stun.Method.ALLOCATE, message_class=stun.Class.ERROR, transaction_id=bytes.fromhex("0102030405060708090a0b0c"))
    e.attributes["ERROR-CODE"] = (401, "Unauthorized")
    e.attributes["REALM"] = "example.org"
    e.attributes["NONCE"] = b"obMatJos2AAACf499k954d6OL34oL9FSTvy64sA"
    e.attributes["FINGERPRINT"] = stun.message_fingerprint(bytes(e))
    return bytes(e)


if __name__ == "__main__":
    for name, b in (("ICE connectivity check", check()), ("its success response", answer()), ("TURN 401 to an Allocate", turn_401())):
        stun.parse_message(b)  # aioice reads its own output back, FINGERPRINT included
        print(f"{name} ({len(b)} B): {b.hex()}")
