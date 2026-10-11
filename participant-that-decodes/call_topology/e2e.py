"""A TOY end-to-end frame cipher, standing in for SFrame (RFC 9605) so the demo
needs only the standard library. Do not use it to protect anything."""
import hashlib
import hmac


class DecryptError(Exception):
    pass


def _stream(key: bytes, nonce: int, n: int) -> bytes:
    out, counter = b"", 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce.to_bytes(8, "big") + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:n]


def seal(key: bytes, nonce: int, frame: bytes) -> bytes:
    body = bytes(a ^ b for a, b in zip(frame, _stream(key, nonce, len(frame))))
    tag = hmac.new(key, nonce.to_bytes(8, "big") + body, hashlib.sha256).digest()[:8]
    return nonce.to_bytes(8, "big") + body + tag


def open_(key: bytes, sealed: bytes) -> bytes:
    nonce, body, tag = int.from_bytes(sealed[:8], "big"), sealed[8:-8], sealed[-8:]
    good = hmac.new(key, sealed[:8] + body, hashlib.sha256).digest()[:8]
    if not hmac.compare_digest(tag, good):
        raise DecryptError("wrong key: this box is not one of the ends")
    return bytes(a ^ b for a, b in zip(body, _stream(key, nonce, len(body))))
