"""The SDP for Alice's call, written out and then checked three ways. These are the SDP explainer's samples.

    python sdp_fixtures.py                   # writes sdp/*.sdp and sdp/*.pem, then checks them
    python sdp_fixtures.py --capture-aiortc  # also saves the answer a real aiortc peer gives to Alice's offer

The files:

  alice-offer.sdp          Alice's offer once her candidates are gathered: host, srflx and relay, as in the third post
  alice-offer-trickle.sdp  The same offer as it leaves before gathering, with trickle ICE: port 9, 0.0.0.0, no candidates
  server-answer.sdp        The assistant's answer: ICE-lite, one host candidate on a public address, a=setup:active
  alice-offer-broken.sdp   Alice's offer with five mistakes of the kind hand-written examples make, for the error path
  aiortc-answer.sdp        Captured once with --capture-aiortc; random values, so it is not regenerated

The offers follow the layout of JSEP's example offer (RFC 9429 section 7.1), cut down to one audio section, with the
candidates, ufrags and SSRC of the earlier posts. They are shaped like a browser's, not captured from one, and the post
should not call them captures. The answer is an ICE-lite server's, as in the third post (RFC 8445 section 2.5).

The fingerprints are real: each is the SHA-256 of a self-signed ECDSA P-256 certificate written beside the SDP, made
from a fixed and clearly fake private key and signed deterministically (RFC 6979), so the files are the same on every
run. Check one with: openssl x509 -in sdp/server-cert.pem -noout -fingerprint -sha256

The checks:
  1. aiortc's SDP parser reads the offers and the answer, and the script prints what it read.
  2. A real aiortc peer connection takes the offer as a remote offer and answers it, sending nothing anywhere.
  3. The rules the RFCs set on the values, one by one. The broken offer must fail exactly the rules it was built to.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import re
from pathlib import Path

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc import sdp as aiortc_sdp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import call_timeline  # noqa: F401  (loopback only, for the aiortc answer below)

OUT = Path(__file__).with_name("sdp")
SSRC = 0x1F2E3D4C  # the first post's SSRC, 523124044

ALICE = {"ufrag": "alce", "pwd": "alice+password+23+chars"}  # "+" is an ice-char; "-" is not
SERVER = {"ufrag": "srv7", "pwd": "server+password+24+chars"}  # the third post's, which signed its STUN sample

# Kept empty on purpose: a sample that breaks a rule it was not built to break is a failure, not a footnote. The third
# post's password once had hyphens, which RFC 8839's grammar does not allow, and was changed to "+" for that reason.
RULE_PWD_CHARS = "ice-pwd uses only ice-chars: letters, digits, + and / (RFC 8839 section 5.1)"
KNOWN_DEVIATIONS: set[tuple[str, str]] = set()

ALICE_CANDIDATES = [
    "a=candidate:1 1 udp 2130706431 192.168.1.42 50004 typ host",
    "a=candidate:2 1 udp 1694498815 192.0.2.10 54321 typ srflx raddr 192.168.1.42 rport 50004",
    "a=candidate:3 1 udp 16777215 198.51.100.50 49152 typ relay raddr 192.0.2.10 rport 54321",
]


def certificate(name: str) -> tuple[x509.Certificate, str]:
    """A self-signed P-256 certificate from a fixed, fake key, and its SHA-256 fingerprint as SDP writes it."""
    scalar = int.from_bytes(hashlib.sha256(f"{name}: demo key, not a secret".encode()).digest())
    key = ec.derive_private_key(scalar % (2**256 - 2**224), ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256(), ecdsa_deterministic=True)
    )
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return cert, ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def sdp(lines: list[str]) -> str:
    return "\r\n".join(lines) + "\r\n"  # RFC 8866 section 5: every line ends in CRLF


def offer(fingerprint: str, trickle: bool = False, broken: bool = False) -> str:
    """Alice's offer. trickle: as it leaves before gathering. broken: with five mistakes, each marked below."""
    if trickle:
        m, c, rtcp, candidates = "m=audio 9 UDP/TLS/RTP/SAVPF 111", "c=IN IP4 0.0.0.0", ["a=rtcp:9 IN IP4 0.0.0.0"], []
    else:
        m, c, rtcp = "m=audio 50004 UDP/TLS/RTP/SAVPF 111", "c=IN IP4 192.168.1.42", []
        candidates = [*ALICE_CANDIDATES, "a=end-of-candidates"]
    pwd = ALICE["pwd"]
    if broken:
        c = None  # 1: no c= line anywhere
        pwd = "alice+password+21char"  # 2: 21 characters, one short
        candidates = [x.split(" raddr")[0] if "srflx" in x else x for x in candidates]  # 3: srflx without raddr/rport
    lines = [
        "v=0",
        "o=- 4611731400430051336 2 IN IP4 0.0.0.0",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0",
        "a=ice-options:trickle ice2",
        m,
        c,
        None if broken else "a=mid:0",  # 4: BUNDLE names mid 0, but no section has it
        "a=sendrecv",
        "a=rtpmap:111 opus/48000/2",
        "a=fmtp:111 minptime=10;useinbandfec=1",
        "a=rtcp-fb:111 transport-cc",
        "a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level",
        "a=extmap:2 urn:ietf:params:rtp-hdrext:sdes:mid",
        "a=msid:alice-mic alice-mic-track",
        f"a=ssrc:{SSRC} cname:alice",
        f"a=ice-ufrag:{ALICE['ufrag']}",
        f"a=ice-pwd:{pwd}",
        f"a=fingerprint:sha-256 {fingerprint}",
        "a=setup:actpass",
        "a=tls-id:1f2e3d4c5b6a79880a1b2c3d4e5f6071",
        *rtcp,
        None if broken else "a=rtcp-mux",  # 5: no rtcp-mux, so RTCP would need a port of its own
        "a=rtcp-rsize",
        *candidates,
    ]
    return sdp([x for x in lines if x is not None])


def answer(fingerprint: str) -> str:
    return sdp([
        "v=0",
        "o=- 7208271330925374214 2 IN IP4 0.0.0.0",
        "s=-",
        "t=0 0",
        "a=ice-lite",
        "a=group:BUNDLE 0",
        "m=audio 50004 UDP/TLS/RTP/SAVPF 111",
        "c=IN IP4 198.51.100.20",
        "a=mid:0",
        "a=sendrecv",
        "a=rtpmap:111 opus/48000/2",
        "a=fmtp:111 minptime=10;useinbandfec=1",
        "a=rtcp-fb:111 transport-cc",
        "a=extmap:2 urn:ietf:params:rtp-hdrext:sdes:mid",
        "a=msid:assistant-voice assistant-voice-track",
        "a=ssrc:2712847316 cname:assistant",
        f"a=ice-ufrag:{SERVER['ufrag']}",
        f"a=ice-pwd:{SERVER['pwd']}",
        f"a=fingerprint:sha-256 {fingerprint}",
        "a=setup:active",
        "a=tls-id:a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "a=rtcp-mux",
        "a=rtcp-rsize",
        "a=candidate:1 1 udp 2130706431 198.51.100.20 50004 typ host",
        "a=end-of-candidates",
    ])


TYPE_PREF = {"host": 126, "prflx": 110, "srflx": 100, "relay": 0}  # RFC 8445 section 5.1.2.2
ICE_CHARS = re.compile(r"[A-Za-z0-9+/]+")


def rules(text: str, kind: str, cert: x509.Certificate) -> list[tuple[str, bool]]:
    """The RFCs' rules on the values, each as (what, passed). A missing line fails its rule rather than raising."""
    lines = text.split("\r\n")[:-1]

    def attr(name: str) -> list[str]:
        return [x.split(":", 1)[1] for x in lines if x.startswith(f"a={name}:")]

    def first(name: str) -> str:
        return (attr(name) or [""])[0]

    media_at = next(i for i, x in enumerate(lines) if x.startswith("m="))
    session, media = lines[:media_at], lines[media_at:]
    m_port = int(lines[media_at].split()[1])
    c_lines = [x for x in lines if x.startswith("c=")]
    c_addr = c_lines[-1].split()[2] if c_lines else None
    fp = first("fingerprint").split(" ")[-1]
    cands = [x.split() for x in attr("candidate")]
    bundle = next((x for x in session if x.startswith("a=group:BUNDLE")), "a=group:BUNDLE").split()[1:]
    out = [
        ("every line ends in CRLF, as RFC 8866 section 5 asks", text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")),
        ("v=, o=, s=, t= come first, in that order (RFC 8866 section 5)", [x[:2] for x in lines[:4]] == ["v=", "o=", "s=", "t="]),
        ("there is a c= line, in the media section or the session (RFC 8866 section 5.7)", c_addr is not None),
        ("ice-ufrag is 4 to 256 characters (RFC 8839 section 5.4)", 4 <= len(first("ice-ufrag")) <= 256),
        ("ice-pwd is 22 to 256 characters (RFC 8839 section 5.4)", 22 <= len(first("ice-pwd")) <= 256),
        ("ice-ufrag uses only ice-chars: letters, digits, + and / (RFC 8839 section 5.1)",
         ICE_CHARS.fullmatch(first("ice-ufrag")) is not None),
        (RULE_PWD_CHARS, ICE_CHARS.fullmatch(first("ice-pwd")) is not None),
        ("the fingerprint is the SHA-256 of the certificate beside it",
         fp == ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))),
        ("the proto is UDP/TLS/RTP/SAVPF (RFC 5764 section 8, JSEP)", lines[media_at].split()[2] == "UDP/TLS/RTP/SAVPF"),
        ("every BUNDLE mid has an m= section with that a=mid (RFC 9143)", bool(bundle) and set(bundle) == set(attr("mid"))),
        ("a=rtcp-mux is present (RFC 5761; RFC 8834 section 4.5 requires support)", "a=rtcp-mux" in media),
        ("a=tls-id is present (RFC 8842 sections 5.2 and 5.3)", bool(attr("tls-id"))),
        ("opus is opus/48000/2 (RFC 7587 section 7)", "111 opus/48000/2" in attr("rtpmap")),
    ]
    if cands:
        out.append(("m= port and c= address are a candidate's, the default (RFC 8839 section 4.2.1.2)",
                    any(c[4] == c_addr and int(c[5]) == m_port for c in cands)))
    else:
        out.append(("with no candidates yet, port 9 and IN IP4 0.0.0.0 (RFC 9429 section 5.2.1)",
                    m_port == 9 and c_addr == "0.0.0.0"))
    for c in cands:
        typ = c[7]
        expected = 2**24 * TYPE_PREF[typ] + 2**8 * 65535 + (256 - int(c[1]))
        out.append((f"candidate {c[0]} ({typ}) priority is RFC 8445's formula", int(c[3]) == expected))
        if typ != "host":
            out.append((f"candidate {c[0]} ({typ}) carries raddr and rport (RFC 8839 section 5.1)",
                        "raddr" in c and "rport" in c))
    if kind == "offer":
        out += [
            ("the offer's a=setup is actpass (RFC 8842 section 5.2, RFC 9429 section 5.2.1)", attr("setup") == ["actpass"]),
            ("a full agent: no a=ice-lite (RFC 8839 section 4.2.1.4)", "a=ice-lite" not in lines),
        ]
    else:
        out += [
            ("the answer's a=setup is active or passive (RFC 9429 section 5.3.1)", first("setup") in ("active", "passive")),
            ("a=ice-lite is at session level, \"a session-level attribute only\" (RFC 8839 section 5.3)",
             "a=ice-lite" in session),
            ("an ICE-lite answer carries host candidates only (RFC 8445 section 2.5)", all(c[7] == "host" for c in cands)),
        ]
    return out


async def aiortc_answers(offer_sdp: str) -> str:
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))

    async def stay_put() -> None:  # the candidates are documentation addresses: answer, but send nothing to them
        return None

    pc._RTCPeerConnection__connect = stay_put
    try:
        await pc.setRemoteDescription(RTCSessionDescription(offer_sdp, "offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        return pc.localDescription.sdp
    finally:
        await pc.close()


def show_parsed(name: str, text: str) -> None:
    parsed = aiortc_sdp.SessionDescription.parse(text)
    m = parsed.media[0]
    cands = ", ".join(f"{c.type} {c.ip}:{c.port}" for c in m.ice_candidates) or "none yet"
    print(f"  {name}: aiortc read {len(parsed.media)} m= section, {m.kind} on {m.profile}, "
          f"codec {m.rtp.codecs[0].mimeType} pt {m.rtp.codecs[0].payloadType}")
    print(f"    ICE ufrag {m.ice.usernameFragment}, pwd {len(m.ice.password)} chars, lite {m.ice.iceLite}, "
          f"candidates: {cands}")
    print(f"    DTLS setup -> role {m.dtls.role}, fingerprint {m.dtls.fingerprints[0].algorithm} "
          f"{m.dtls.fingerprints[0].value[:23]}..., rtcp-mux {m.rtcp_mux}, BUNDLE {parsed.group[0].items}")


# The broken offer must fail these, and only these. Five mistakes; the missing c= line also breaks the default rule.
BROKEN_FAILS = {
    "there is a c= line, in the media section or the session (RFC 8866 section 5.7)",
    "ice-pwd is 22 to 256 characters (RFC 8839 section 5.4)",
    "candidate 2 (srflx) carries raddr and rport (RFC 8839 section 5.1)",
    "every BUNDLE mid has an m= section with that a=mid (RFC 9143)",
    "a=rtcp-mux is present (RFC 5761; RFC 8834 section 4.5 requires support)",
    "m= port and c= address are a candidate's, the default (RFC 8839 section 4.2.1.2)",
}


def main(capture: bool) -> None:
    OUT.mkdir(exist_ok=True)
    alice_cert, alice_fp = certificate("alice")
    server_cert, server_fp = certificate("assistant")
    samples = {
        "alice-offer.sdp": ("offer", offer(alice_fp), alice_cert),
        "alice-offer-trickle.sdp": ("offer", offer(alice_fp, trickle=True), alice_cert),
        "server-answer.sdp": ("answer", answer(server_fp), server_cert),
        "alice-offer-broken.sdp": ("offer", offer(alice_fp, broken=True), alice_cert),
    }
    for name, (_, text, _) in samples.items():
        (OUT / name).write_bytes(text.encode())
    (OUT / "alice-cert.pem").write_bytes(alice_cert.public_bytes(serialization.Encoding.PEM))
    (OUT / "server-cert.pem").write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    for name, (_, text, _) in samples.items():
        print(f"Wrote sdp/{name:<24} {len(text.splitlines()):>2} lines, {len(text):>4} B")
    print(f"  Alice's fingerprint   sha-256 {alice_fp}")
    print(f"  server's fingerprint  sha-256 {server_fp}\n")

    o, a = samples["alice-offer.sdp"][1], samples["server-answer.sdp"][1]
    print("1. aiortc's parser:")
    show_parsed("offer        ", o)
    show_parsed("offer trickle", samples["alice-offer-trickle.sdp"][1])
    show_parsed("answer       ", a)

    answered = asyncio.run(aiortc_answers(o))
    setup = next(x for x in answered.splitlines() if x.startswith("a=setup:"))
    codec = next(x for x in answered.splitlines() if x.startswith("a=rtpmap:"))
    print(f"\n2. A real aiortc peer connection, given Alice's offer: answered it, choosing {setup} and {codec}")
    if capture:
        (OUT / "aiortc-answer.sdp").write_bytes(answered.replace("\r\n", "\n").replace("\n", "\r\n").encode())
        print("   saved as sdp/aiortc-answer.sdp")
    print()

    print("3. The rules:")
    failed = 0
    for name, (kind, text, cert) in samples.items():
        results = rules(text, kind, cert)
        if name == "alice-offer-broken.sdp":
            fails = {what for what, passed in results if not passed}
            ok = fails == BROKEN_FAILS
            failed += not ok
            print(f"  {'ok  ' if ok else 'FAIL'}  {name}: fails exactly the {len(BROKEN_FAILS)} rules it was built to, "
                  f"and passes the other {len(results) - len(fails)}")
            for what in sorted(fails):
                print(f"          fails: {what}")
            continue
        for what, passed in results:
            if not passed and (name, what) in KNOWN_DEVIATIONS:
                print(f"  KNOWN {name}: {what}")
            elif not passed:
                failed += 1
                print(f"  FAIL  {name}: {what}")
        clean = all(p or (name, w) in KNOWN_DEVIATIONS for w, p in results)
        print(f"  {'ok  ' if clean else 'FAIL'}  {name}: {len(results)} rules")
    print(f"\n{'Every sample is as intended.' if not failed else f'{failed} FAILED.'}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture-aiortc", action="store_true")
    main(parser.parse_args().capture_aiortc)
