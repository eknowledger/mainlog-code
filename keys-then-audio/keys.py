"""Where a call's SRTP keys come from: one DTLS exporter output, cut into four pieces (RFC 5764 section 4.2).

    python keys.py

Runs one loopback call between two aiortc peers, offering only SRTP_AES128_CM_HMAC_SHA1_80 so the sizes are the ones
RFC 5764 describes: a 16-byte master key and a 14-byte master salt per direction. After the handshake, each side asks
its own DTLS connection for the keying material exporter (RFC 5705) with the label "EXTRACTOR-dtls_srtp", and gets
2 * (16 + 14) = 60 bytes. The two sides never send those bytes to each other; they compute them, and get the same ones.

Then the proof that the cut is right: the first media packet Alice really sent is decrypted with a libsrtp session
built from nothing but those 60 bytes, cut as RFC 5764 says.

The keys are fresh every run, which is the point, so the bytes printed here change each time and the post quotes none
of them. The demo prints only their first bytes.
"""

from __future__ import annotations

import asyncio

import pylibsrtp
from aiortc import RTCConfiguration, RTCPeerConnection
from aiortc.rtcdtlstransport import SRTP_AES128_CM_SHA1_80
from pylibsrtp import Policy, Session

import call_timeline  # noqa: F401  (for its loopback-only patch and the tone)
from call_timeline import Tone, classify

KEY, SALT = 16, 14  # SRTP_AES128_CM_HMAC_SHA1_80


def short(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data[:6]) + " ..."


async def main() -> None:
    config = RTCConfiguration(iceServers=[])
    alice, server = RTCPeerConnection(config), RTCPeerConnection(config)
    alice.addTrack(Tone())
    captured: list[bytes] = []

    await alice.setLocalDescription(await alice.createOffer())
    await server.setRemoteDescription(alice.localDescription)
    server.addTrack(Tone())
    await server.setLocalDescription(await server.createAnswer())
    a_dtls = alice.getTransceivers()[0].sender.transport
    s_dtls = server.getTransceivers()[0].sender.transport
    for dtls in (a_dtls, s_dtls):
        dtls._srtp_profiles = [SRTP_AES128_CM_SHA1_80]
    ice_send = a_dtls.transport._send

    async def send(data: bytes) -> None:
        if classify(data) == "SRTP" and not captured:
            captured.append(data)
        await ice_send(data)

    a_dtls.transport._send = send
    await alice.setRemoteDescription(server.localDescription)
    for _ in range(500):
        if captured:
            break
        await asyncio.sleep(0.01)

    try:
        print(f"Alice is the DTLS {a_dtls._role}, the server is the DTLS {s_dtls._role}; "
              f"profile {a_dtls._ssl.get_selected_srtp_profile().decode()}\n")
        length = 2 * (KEY + SALT)
        exported = {who: d._ssl.export_keying_material(b"EXTRACTOR-dtls_srtp", length)
                    for who, d in (("Alice", a_dtls), ("server", s_dtls))}
        same = exported["Alice"] == exported["server"]
        print(f"Each side's exporter output, label EXTRACTOR-dtls_srtp, {length} B: "
              f"{'identical on both sides' if same else 'DIFFERENT'}, and never sent")
        material = exported["Alice"]
        cut = {
            "client_write_SRTP_master_key": material[0:KEY],
            "server_write_SRTP_master_key": material[KEY : 2 * KEY],
            "client_write_SRTP_master_salt": material[2 * KEY : 2 * KEY + SALT],
            "server_write_SRTP_master_salt": material[2 * KEY + SALT :],
        }
        offset = 0
        for name, value in cut.items():
            print(f"  bytes {offset:>2} to {offset + len(value) - 1:>2}  {name:<30} {len(value):>2} B  {short(value)}")
            offset += len(value)
        print()

        writer = "server" if a_dtls._role == "server" else "client"
        key = cut[f"{writer}_write_SRTP_master_key"] + cut[f"{writer}_write_SRTP_master_salt"]
        packet = captured[0]
        session = Session(Policy(key=key, ssrc_type=Policy.SSRC_ANY_INBOUND,
                                 srtp_profile=Policy.SRTP_PROFILE_AES128_CM_SHA1_80))
        plain = session.unprotect(packet)
        print(f"Alice's first media packet, {len(packet)} B of SRTP, decrypted with the {writer}_write key and salt "
              f"(Alice is the DTLS {writer}): {len(plain)} B of RTP, sequence {int.from_bytes(plain[2:4])}")
        reader = "client" if writer == "server" else "server"
        wrong = Session(Policy(key=cut[f"{reader}_write_SRTP_master_key"] + cut[f"{reader}_write_SRTP_master_salt"],
                               ssrc_type=Policy.SSRC_ANY_INBOUND, srtp_profile=Policy.SRTP_PROFILE_AES128_CM_SHA1_80))
        try:
            wrong.unprotect(packet)
            verdict = "accepted"
        except pylibsrtp.Error as exc:
            verdict = f"rejected ({exc})"
        print(f"The same packet with the {reader}_write key and salt, the other direction's: {verdict}")
    finally:
        await alice.close()
        await server.close()


if __name__ == "__main__":
    asyncio.run(main())
