"""SRTP on one packet: what it encrypts, what it leaves readable, and what the receiver refuses.

    python srtp_one_packet.py

The packet is the one the first post built: Alice's RTP header (sequence 1010, timestamp 9600, SSRC 0x1F2E3D4C) and
the 80 bytes of Opus that build_packet.py produced from its 440 Hz tone. The Opus bytes are fixed here rather than
encoded again, because libopus builds can differ in them; these are the ones the first post shows.

The keys are fixed too, and fake: a master key of 00 01 .. 0f and a master salt of a0 a1 .. ad. In a real call they
come out of the DTLS handshake (keys.py shows how), and are never printed. Fixed keys make the output reproducible.

The packet is protected by libsrtp (through pylibsrtp), the library aiortc and the browsers' WebRTC stacks use. Then
the same bytes are computed again in a few lines of Python straight from RFC 3711, with the key derivation checked first
against the RFC's own test vectors, so the output shows what SRTP does rather than just what one library returns.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

import pylibsrtp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pylibsrtp import Policy, Session

# The first post's packet: RTP header, then 20 ms of Opus at 32 kb/s.
RTP_HEADER = bytes.fromhex("806f03f2000025801f2e3d4c")
OPUS = bytes.fromhex(
    "4b411098fe7eb5246000b41fa06177c378b7d770cbed5ce56a269e1dc8543518"
    "d2b7f7cb6adf8b0761dceac805ce3ec4caf1a6813262eafa0b85f09400e1613c"
    "00000000000000000000000000000000"
)
PACKET = RTP_HEADER + OPUS

# Clearly fake keying material, so the run is reproducible.
MASTER_KEY = bytes(range(16))  # 00 01 02 .. 0f
MASTER_SALT = bytes(range(0xA0, 0xAE))  # a0 a1 .. ad, 14 bytes
OTHER_KEY = bytes(range(0x10, 0x20))  # stands in for the other direction's master key
OTHER_SALT = bytes(range(0xB0, 0xBE))


def hx(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def aes_cm(key: bytes, iv: bytes, length: int) -> bytes:
    """AES in counter mode: the keystream for a 128-bit starting counter (RFC 3711 section 4.1.1)."""
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return enc.update(bytes(length)) + enc.finalize()


def kdf(master_key: bytes, master_salt: bytes, label: int, length: int, index_div_kdr: int = 0) -> bytes:
    """RFC 3711 section 4.3.1: x = (label || index DIV kdr) XOR master salt, right-aligned; the key is AES-CM(x * 2^16)."""
    key_id = (label << 48) | index_div_kdr  # one octet of label, then 48 bits
    x = key_id ^ int.from_bytes(master_salt)
    return aes_cm(master_key, (x << 16).to_bytes(16), length)


LABELS = {  # RFC 3711 section 4.3.2
    "SRTP encryption key": (0x00, 16),
    "SRTP authentication key": (0x01, 20),
    "SRTP salting key": (0x02, 14),
    "SRTCP encryption key": (0x03, 16),
    "SRTCP authentication key": (0x04, 20),
    "SRTCP salting key": (0x05, 14),
}


def check_kdf_against_rfc() -> None:
    """RFC 3711 appendix B.3, the key derivation test vectors."""
    mk, ms = bytes.fromhex("E1F97A0D3E018BE0D64FA32C06DE4139"), bytes.fromhex("0EC675AD498AFEEBB6960B3AABE6")
    assert kdf(mk, ms, 0, 16) == bytes.fromhex("C61E7A93744F39EE10734AFE3FF7A087")
    assert kdf(mk, ms, 2, 14) == bytes.fromhex("30CBBC08863D8C85D49DB34A9AE1")
    assert kdf(mk, ms, 1, 20) == bytes.fromhex("CEBE321F6FF7716B6FD4AB49AF256A156D38BAA4")
    print("Key derivation matches RFC 3711 appendix B.3 (cipher key, cipher salt, auth key).\n")


def by_hand(packet: bytes, roc: int, master_key: bytes = MASTER_KEY, master_salt: bytes = MASTER_SALT) -> bytes:
    """SRTP_AES128_CM_HMAC_SHA1_80 from RFC 3711 alone: encrypt the payload, then tag header + ciphertext + ROC."""
    k_e = kdf(master_key, master_salt, 0x00, 16)
    k_a = kdf(master_key, master_salt, 0x01, 20)
    k_s = kdf(master_key, master_salt, 0x02, 14)
    seq, ssrc = struct.unpack("!H", packet[2:4])[0], struct.unpack("!I", packet[8:12])[0]
    index = (roc << 16) | seq  # RFC 3711 section 3.3.1: i = 2^16 * ROC + SEQ
    iv = (int.from_bytes(k_s) << 16) ^ (ssrc << 64) ^ (index << 16)  # section 4.1.1
    header, payload = packet[:12], packet[12:]
    keystream = aes_cm(k_e, iv.to_bytes(16), len(payload))
    encrypted = bytes(a ^ b for a, b in zip(payload, keystream))
    tag = hmac.new(k_a, header + encrypted + struct.pack("!I", roc), hashlib.sha1).digest()[:10]  # section 4.2
    return header + encrypted + tag


def gcm_by_hand(packet: bytes, key: bytes, salt: bytes, roc: int = 0) -> bytes:
    """SRTP_AEAD_AES_128_GCM (RFC 7714): the header is associated data, authenticated but not encrypted.

    RFC 7714 section 11 says only that this profile "MUST use the (128-bit) AES_CM PRF KDF described in [RFC3711]",
    whose inputs are written for a 14-byte salt. The master salt here is 12. libsrtp pads it with two zero bytes on the
    right; right-aligning it instead gives different keys. This follows libsrtp, the one that interoperates.
    """
    padded = salt + b"\0\0"
    k_e = kdf(key, padded, 0x00, 16)
    k_s = kdf(key, padded, 0x02, 12)
    ssrc, seq = packet[8:12], packet[2:4]
    iv = bytes(a ^ b for a, b in zip(b"\0\0" + ssrc + struct.pack("!I", roc) + seq, k_s))  # RFC 7714 section 8.1
    return packet[:12] + AESGCM(k_e).encrypt(iv, packet[12:], packet[:12])


def session(key: bytes, salt: bytes, inbound: bool, profile: int = Policy.SRTP_PROFILE_AES128_CM_SHA1_80) -> Session:
    kind = Policy.SSRC_ANY_INBOUND if inbound else Policy.SSRC_ANY_OUTBOUND
    return Session(Policy(key=key + salt, ssrc_type=kind, srtp_profile=profile))


def try_unprotect(receiver: Session, packet: bytes) -> str:
    try:
        receiver.unprotect(packet)
    except pylibsrtp.Error as exc:
        return f"rejected ({exc})"
    return "accepted"


def flip(packet: bytes, byte: int, bit: int = 0) -> bytes:
    out = bytearray(packet)
    out[byte] ^= 1 << bit
    return bytes(out)


def main() -> None:
    check_kdf_against_rfc()

    print(f"Master key   {hx(MASTER_KEY)}   (fake, fixed)")
    print(f"Master salt  {hx(MASTER_SALT)}         (fake, fixed)")
    print("Session keys derived from them (RFC 3711 section 4.3), six for one direction:")
    for name, (label, length) in LABELS.items():
        print(f"  label 0x{label:02x}  {name:<25} {length:>2} B  {hx(kdf(MASTER_KEY, MASTER_SALT, label, length))}")
    print()

    sender = session(MASTER_KEY, MASTER_SALT, inbound=False)
    srtp = sender.protect(PACKET)
    header, body, tag = srtp[:12], srtp[12:-10], srtp[-10:]
    print("SRTP_AES128_CM_HMAC_SHA1_80, protected by libsrtp:")
    print(f"  RTP  packet {len(PACKET):>3} B   header {hx(PACKET[:12])}   payload {hx(OPUS[:8])} ... ({len(OPUS)} B)")
    print(f"  SRTP packet {len(srtp):>3} B   header {hx(header)}   payload {hx(body[:8])} ... ({len(body)} B)"
          f"   tag {hx(tag)} ({len(tag)} B)")
    print(f"  header byte-identical: {'yes' if header == PACKET[:12] else 'NO'}; "
          f"payload bytes changed: {sum(a != b for a, b in zip(body, OPUS))} of {len(OPUS)}; "
          f"added: {len(srtp) - len(PACKET)} B, all of it tag")
    seq, ts, ssrc = struct.unpack("!HII", header[2:12])
    print(f"  what anyone on the path still reads: version {header[0] >> 6}, payload type {header[1] & 0x7F}, "
          f"sequence {seq}, timestamp {ts}, SSRC 0x{ssrc:08x}")
    print(f"  recomputed from RFC 3711 alone: {'identical, byte for byte' if by_hand(PACKET, 0) == srtp else 'DIFFERENT'}")
    print(f"  the 16 zero bytes that end the Opus frame leave as {hx(body[-8:])} ...\n")

    print("The receiver, with the same keys:")
    receiver = session(MASTER_KEY, MASTER_SALT, inbound=True)
    print(f"  the packet as sent:                   {try_unprotect(receiver, srtp)}, "
          f"payload {'restored' if receiver_restores(srtp) else 'NOT restored'}")
    print(f"  the same packet again (a replay):     {try_unprotect(receiver, srtp)}")
    receiver = session(MASTER_KEY, MASTER_SALT, inbound=True)
    print(f"  one bit flipped in the payload:       {try_unprotect(receiver, flip(srtp, 12))}")
    print(f"  one bit flipped in the sequence no.:  {try_unprotect(receiver, flip(srtp, 3))}")
    print(f"  one bit flipped in the tag:           {try_unprotect(receiver, flip(srtp, len(srtp) - 1))}")
    other = session(OTHER_KEY, OTHER_SALT, inbound=True)
    print(f"  with the other direction's keys:      {try_unprotect(other, srtp)}")
    print(f"  and the untouched packet, after all that: {try_unprotect(receiver, srtp)}\n")

    # The rollover counter: sequence numbers are 16 bits, so they wrap every 65,536 packets, about 22 minutes at 50 a
    # second. The ROC counts the wraps. It is never sent, but it is in the tag and in the counter, so both ends must agree.
    print("The rollover counter (ROC), which is never sent:")
    sender = session(MASTER_KEY, MASTER_SALT, inbound=False)
    last = sender.protect(RTP_HEADER[:2] + struct.pack("!H", 65535) + RTP_HEADER[4:] + OPUS)
    wrapped_rtp = RTP_HEADER[:2] + struct.pack("!H", 0) + RTP_HEADER[4:] + OPUS
    wrapped = sender.protect(wrapped_rtp)
    print(f"  sequence 65535, then 0: libsrtp's second packet equals the RFC computation with ROC 1: "
          f"{'yes' if by_hand(wrapped_rtp, 1) == wrapped else 'no'}; with ROC 0: "
          f"{'yes' if by_hand(wrapped_rtp, 0) == wrapped else 'no'}")
    receiver = session(MASTER_KEY, MASTER_SALT, inbound=True)
    print(f"  a receiver given both accepts the first: {try_unprotect(receiver, last)}; the second: "
          f"{try_unprotect(receiver, wrapped)}. It counts the wraps itself, from the sequence numbers\n")

    gcm_key, gcm_salt = MASTER_KEY, MASTER_SALT[:12]
    gcm = session(gcm_key, gcm_salt, inbound=False, profile=Policy.SRTP_PROFILE_AEAD_AES_128_GCM).protect(PACKET)
    print("SRTP_AEAD_AES_128_GCM, the same packet (12-byte master salt):")
    print(f"  SRTP packet {len(gcm):>3} B   header {hx(gcm[:12])}   tag {hx(gcm[-16:])} ({len(gcm) - len(PACKET)} B)")
    print(f"  header byte-identical: {'yes' if gcm[:12] == PACKET[:12] else 'NO'}; recomputed from RFC 7714, salt padded as libsrtp does: "
          f"{'identical, byte for byte' if gcm_by_hand(PACKET, gcm_key, gcm_salt) == gcm else 'DIFFERENT'}")


def receiver_restores(srtp: bytes) -> bool:
    return session(MASTER_KEY, MASTER_SALT, inbound=True).unprotect(srtp) == PACKET


if __name__ == "__main__":
    main()
