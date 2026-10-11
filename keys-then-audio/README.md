# Keys, then audio: DTLS, SRTP and one call from cold start

From the post [Keys, then audio](https://mainlog.dev/posts/keys-then-audio/), the fourth in the series *Real-time
voice, from the packet up*.

| File | What it does |
|---|---|
| `call_timeline.py` | Two [aiortc](https://github.com/aiortc/aiortc) peers in one process, Alice offering and the server answering, over 127.0.0.1. Records every datagram either side sends, decodes the DTLS handshake into its messages, and prints when each phase completes, the DTLS roles, the SRTP profile and each side's fingerprint check. Then a spread over several calls, and one call with a tampered fingerprint |
| `srtp_one_packet.py` | Protects the first post's RTP packet with libsrtp under fixed, fake keys: the header byte-identical, the payload changed, a 10-byte tag added. Recomputes the same bytes from RFC 3711 alone, then shows the receiver refusing a replay, three flipped bits and the other direction's keys |
| `keys.py` | Where the keys come from: one DTLS exporter output (RFC 5764 section 4.2), the same 60 bytes on both sides, cut into two master keys and two master salts, then used to decrypt a packet Alice really sent |
| `sdp_fixtures.py` | Writes the SDP for Alice's call to `sdp/`: her offer gathered and trickled, the server's ICE-lite answer, and a deliberately broken offer, with real fingerprints of the certificates beside them. Then checks them: aiortc parses them, a real aiortc peer answers the offer, and the RFCs' rules hold, or for the broken one fail exactly where intended |
| `sdp/` | The SDP fixtures and the two certificates, committed. CI regenerates them and fails if a byte changes (all but the captured aiortc answer) |
| `requirements.txt` | aiortc (which brings aioice, PyAV and pyOpenSSL), pylibsrtp, and cryptography for the by-hand recomputation |

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python srtp_one_packet.py
.venv/bin/python sdp_fixtures.py
.venv/bin/python keys.py
.venv/bin/python call_timeline.py               # ten calls; --runs N for more
.venv/bin/python call_timeline.py --profile cm80 # offer only SRTP_AES128_CM_HMAC_SHA1_80
.venv/bin/python call_timeline.py --delay 25     # hold every datagram 25 ms: a 50 ms round trip
```

Tested with Python 3.13 on macOS (arm64), with the OpenSSL 4.0.3 that the pinned `cryptography` wheel ships.

## `srtp_one_packet.py`

Deterministic: the keys are fixed and fake, and the Opus bytes are the ones the first post printed, fixed here because
libopus builds can differ in them.

```text
Key derivation matches RFC 3711 appendix B.3 (cipher key, cipher salt, auth key).

Master key   00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f   (fake, fixed)
Master salt  a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab ac ad         (fake, fixed)
Session keys derived from them (RFC 3711 section 4.3), six for one direction:
  label 0x00  SRTP encryption key       16 B  54 43 59 b2 5c 40 7d 1f 93 4e af 8a 86 af 65 41
  label 0x01  SRTP authentication key   20 B  f4 2e c7 92 cc 52 44 21 41 90 23 d8 81 2e b2 b8 76 78 7c 0f
  label 0x02  SRTP salting key          14 B  dd 69 d3 df ee 28 bf a0 fc 55 f5 4b 20 f6
  label 0x03  SRTCP encryption key      16 B  cd 6a 07 84 02 e6 5a b7 14 cf 15 13 e3 99 e6 9d
  label 0x04  SRTCP authentication key  20 B  d4 8d 43 f7 2e 97 5e 5b 16 0f ac f3 1f 23 4b a9 93 81 51 0d
  label 0x05  SRTCP salting key         14 B  f7 ce 83 7b 5e 4b ff c2 5a 96 21 8f c6 c7

SRTP_AES128_CM_HMAC_SHA1_80, protected by libsrtp:
  RTP  packet  92 B   header 80 6f 03 f2 00 00 25 80 1f 2e 3d 4c   payload 4b 41 10 98 fe 7e b5 24 ... (80 B)
  SRTP packet 102 B   header 80 6f 03 f2 00 00 25 80 1f 2e 3d 4c   payload 62 23 76 62 5a f9 71 28 ... (80 B)   tag a3 29 b9 90 13 a1 c5 52 0a 5d (10 B)
  header byte-identical: yes; payload bytes changed: 80 of 80; added: 10 B, all of it tag
  what anyone on the path still reads: version 2, payload type 111, sequence 1010, timestamp 9600, SSRC 0x1f2e3d4c
  recomputed from RFC 3711 alone: identical, byte for byte
  the 16 zero bytes that end the Opus frame leave as 63 69 3d de c7 93 89 c5 ...

The receiver, with the same keys:
  the packet as sent:                   accepted, payload restored
  the same packet again (a replay):     rejected (replay check failed (bad index))
  one bit flipped in the payload:       rejected (authentication failure)
  one bit flipped in the sequence no.:  rejected (authentication failure)
  one bit flipped in the tag:           rejected (authentication failure)
  with the other direction's keys:      rejected (authentication failure)
  and the untouched packet, after all that: accepted

The rollover counter (ROC), which is never sent:
  sequence 65535, then 0: libsrtp's second packet equals the RFC computation with ROC 1: yes; with ROC 0: no
  a receiver given both accepts the first: accepted; the second: accepted. It counts the wraps itself, from the sequence numbers

SRTP_AEAD_AES_128_GCM, the same packet (12-byte master salt):
  SRTP packet 108 B   header 80 6f 03 f2 00 00 25 80 1f 2e 3d 4c   tag 8b 05 ba d0 08 22 dc b1 72 7c 42 4a ee 98 38 d1 (16 B)
  header byte-identical: yes; recomputed from RFC 7714, salt padded as libsrtp does: identical, byte for byte
```

One thing the recomputation turned up: RFC 7714 says the GCM profile "MUST use the (128-bit) AES_CM PRF KDF described in
[RFC3711]", but that KDF is written for a 14-byte salt and GCM's is 12. libsrtp pads it with two zero bytes on the
right; aligning it to the right instead, as RFC 3711 aligns its inputs, gives keys libsrtp does not use.

## `sdp_fixtures.py`

Deterministic too: the certificates come from fixed, fake private keys, signed with deterministic ECDSA (RFC 6979).

```text
Wrote sdp/alice-offer.sdp          28 lines,  966 B
Wrote sdp/alice-offer-trickle.sdp  25 lines,  722 B
Wrote sdp/server-answer.sdp        25 lines,  734 B
Wrote sdp/alice-offer-broken.sdp   25 lines,  889 B
  Alice's fingerprint   sha-256 A9:5C:8D:BA:4C:00:E3:59:80:B8:8E:45:F1:DA:8E:5C:5D:01:AB:C7:32:0C:89:69:94:EE:DC:72:61:A1:00:DD
  server's fingerprint  sha-256 BE:71:5C:E2:A6:DB:52:F3:30:4E:C9:69:0C:42:33:31:9E:40:3E:39:9E:89:60:7D:3A:84:79:BD:8E:52:24:67

1. aiortc's parser:
  offer        : aiortc read 1 m= section, audio on UDP/TLS/RTP/SAVPF, codec audio/opus pt 111
    ICE ufrag alce, pwd 23 chars, lite False, candidates: host 192.168.1.42:50004, srflx 192.0.2.10:54321, relay 198.51.100.50:49152
    DTLS setup -> role auto, fingerprint sha-256 A9:5C:8D:BA:4C:00:E3:59..., rtcp-mux True, BUNDLE ['0']
  offer trickle: aiortc read 1 m= section, audio on UDP/TLS/RTP/SAVPF, codec audio/opus pt 111
    ICE ufrag alce, pwd 23 chars, lite False, candidates: none yet
    DTLS setup -> role auto, fingerprint sha-256 A9:5C:8D:BA:4C:00:E3:59..., rtcp-mux True, BUNDLE ['0']
  answer       : aiortc read 1 m= section, audio on UDP/TLS/RTP/SAVPF, codec audio/opus pt 111
    ICE ufrag srv7, pwd 24 chars, lite True, candidates: host 198.51.100.20:50004
    DTLS setup -> role client, fingerprint sha-256 BE:71:5C:E2:A6:DB:52:F3..., rtcp-mux True, BUNDLE ['0']

2. A real aiortc peer connection, given Alice's offer: answered it, choosing a=setup:active and a=rtpmap:111 opus/48000/2

3. The rules:
  ok    alice-offer.sdp: 21 rules
  ok    alice-offer-trickle.sdp: 16 rules
  ok    server-answer.sdp: 18 rules
  ok    alice-offer-broken.sdp: fails exactly the 6 rules it was built to, and passes the other 15
          fails: a=rtcp-mux is present (RFC 5761; RFC 8834 section 4.5 requires support)
          fails: candidate 2 (srflx) carries raddr and rport (RFC 8839 section 5.1)
          fails: every BUNDLE mid has an m= section with that a=mid (RFC 9143)
          fails: ice-pwd is 22 to 256 characters (RFC 8839 section 5.4)
          fails: m= port and c= address are a candidate's, the default (RFC 8839 section 4.2.1.2)
          fails: there is a c= line, in the media section or the session (RFC 8866 section 5.7)

Every sample is as intended.
```

| File | What it is |
|---|---|
| `sdp/alice-offer.sdp` | Alice's offer once gathering is done: her host, srflx and relay candidates from the third post |
| `sdp/alice-offer-trickle.sdp` | The same offer as it leaves before gathering, with trickle ICE: port 9, `IN IP4 0.0.0.0`, no candidates (RFC 9429 section 5.2.1) |
| `sdp/server-answer.sdp` | The assistant's answer: `a=ice-lite`, one host candidate on 198.51.100.20:50004, `a=setup:active` |
| `sdp/alice-offer-broken.sdp` | Alice's offer with five mistakes: no `c=` line, a 21-character password, a srflx candidate without `raddr`, a BUNDLE group naming a mid no section has, and no `a=rtcp-mux` |
| `sdp/aiortc-answer.sdp` | The answer a real aiortc peer gave to Alice's offer, captured once with `--capture-aiortc`. Its values are random, so CI does not regenerate it. It has no `a=tls-id`, which RFC 8842 section 5.3 asks an answer to carry |

The offers follow the layout of JSEP's example offer (RFC 9429 section 7.1), cut to one audio section, with the
candidates, ufrag and SSRC of the earlier posts. They are shaped like a browser's, not captured from one. Every password is
made of ice-chars only, letters, digits, `+` and `/`, as RFC 8839's grammar requires; aiortc would accept a hyphen,
but a sample that teaches the format should not need one.

## `keys.py`

Fresh keys every run, so these bytes change each time; the shape does not.

```text
Alice is the DTLS server, the server is the DTLS client; profile SRTP_AES128_CM_SHA1_80

Each side's exporter output, label EXTRACTOR-dtls_srtp, 60 B: identical on both sides, and never sent
  bytes  0 to 15  client_write_SRTP_master_key   16 B  92 3b d5 62 69 aa ...
  bytes 16 to 31  server_write_SRTP_master_key   16 B  75 a6 56 85 40 22 ...
  bytes 32 to 45  client_write_SRTP_master_salt  14 B  38 82 90 b9 1e 9d ...
  bytes 46 to 59  server_write_SRTP_master_salt  14 B  d8 d8 6a 44 e6 01 ...

Alice's first media packet, 485 B of SRTP, decrypted with the server_write key and salt (Alice is the DTLS server): 475 B of RTP, sequence 19042
The same packet with the client_write key and salt, the other direction's: rejected (authentication failure)
```

## `call_timeline.py`

Times vary from run to run and machine to machine; the order, the roles, the flights and the checks do not. One run
of `--runs 30`, the first call in full:

```text
One call, every datagram up to the first decoded audio frame (ms from the start, as sent; loopback, one machine):

     1.84  server -> Alice   STUN    88 B  Binding request
     2.04  Alice  -> server  STUN    64 B  Binding response
     2.23  Alice  -> server  STUN    92 B  Binding request
     2.33  server -> Alice   STUN    64 B  Binding response
    26.44  server -> Alice   DTLS   173 B  flight 1: ClientHello
    26.93  Alice  -> server  DTLS   727 B  flight 2: ServerHello, Certificate (2 pieces), ServerKeyExchange, CertificateRequest, ServerHelloDone
    27.31  server -> Alice   DTLS   659 B  flight 3: Certificate (2 pieces), ClientKeyExchange, CertificateVerify (2 pieces), ChangeCipherSpec, Finished (encrypted)
    27.57  Alice  -> server  DTLS   668 B  flight 4: NewSessionTicket (3 pieces), ChangeCipherSpec, Finished (encrypted)
    30.27  Alice  -> server  SRTP   491 B
    30.86  server -> Alice   SRTP   491 B
  ...      8 more media packets before the server's first decoded frame

ICE:  2 Binding requests, 2 responses, 308 bytes
DTLS: 4 flights, 4 datagrams, 2227 bytes; record-layer versions on the wire: DTLS 1.0, DTLS 1.2

Offer a=setup:actpass, answer a=setup:active
  Alice  is the DTLS server: DTLSv1.2, ECDHE-ECDSA-AES128-GCM-SHA256, SRTP profile SRTP_AEAD_AES_256_GCM
  server is the DTLS client: DTLSv1.2, ECDHE-ECDSA-AES128-GCM-SHA256, SRTP profile SRTP_AEAD_AES_256_GCM
  Alice  checks server's certificate: the SDP says sha-256 58:CF:B2:B0:79:B6..., the certificate hashes to 58:CF:B2:B0:79:B6...: match
  server checks Alice's certificate: the SDP says sha-256 DE:E7:CA:D0:4A:8F..., the certificate hashes to DE:E7:CA:D0:4A:8F...: match
  First media packet: 475 B of RTP left as 491 B of SRTP, 16 B added

Phases, in the order they happened:
     0.66  offer
     1.39  answer
     1.84  first check
     2.33  last check answered
    22.43  ICE completed (server)
    26.44  first DTLS
    26.53  ICE completed (Alice)
    27.57  last DTLS
    27.74  DTLS connected (Alice)
    29.30  DTLS connected (server)
    30.27  first SRTP sent
    30.38  first SRTP received
   111.71  first frame decoded
```

Then the spread over all 30 and the tampered call:

```text
Spread over 30 calls, ms (loopback, one machine):
                                                   min  median     max
  offer created, candidates gathered              0.38    0.79    0.95
  ICE checks on the wire, first to last           0.34    0.57    1.76
  ICE: answer to both sides completed            21.22   23.11   27.30
  DTLS on the wire, first to last flight          0.91    2.18    3.48
  DTLS: first flight to both sides keyed          1.96    4.22    6.67
  first side keyed to its first SRTP sent         1.69    2.56    4.90
  first SRTP received to first frame decoded     77.90   82.68   86.52
  cold start to first frame decoded             104.79  114.54  119.27

Every call ran gather, check, handshake, media, in that order: yes
No media packet left before the last handshake datagram, in any call: yes

One more call, with one character of the server's fingerprint changed in the answer Alice receives:
  Alice's DTLS transport ends failed. Alice sent 0 media packets and read none; the server, whose own check of Alice passed, sent 11
```

### How to read these numbers

They are **loopback numbers on one machine**. Nothing crosses a network, so they exclude every round trip, all packet
loss and so every DTLS retransmission, STUN and TURN gathering (no STUN server is configured, and only 127.0.0.1 is
gathered), the signalling server (the offer and answer are handed over by a function call), and a browser's own work.

What is left is mostly two timers in the libraries, not the protocols:

| Span | Median, three runs of 30 | Where it comes from |
|---|---|---|
| ICE checks on the wire | 0.46 to 0.86 ms | The four STUN messages themselves |
| ICE, answer to completed | 22.8 to 23.3 ms | aioice starts a check, then sleeps 20 ms before looking at the check list again (`await asyncio.sleep(0.02)` in `Connection.connect`), so completion is reported on the next tick |
| DTLS on the wire | 1.48 to 2.41 ms | Four flights, about 2.2 KB |
| First packet received to first frame decoded | 81.8 to 82.7 ms | aiortc's audio jitter buffer releases its first frame only once it holds four (`JitterBuffer(capacity=16, prefetch=4)` in `rtcrtpreceiver.py`): 4 x 20 ms of audio |

The first call in a process is slower than the rest: it pays one-time library setup. Creating the first
DTLS context took about 4 ms on this machine, later ones about 0.15 ms.

### The same call with a round trip

Loopback hides the thing a real call pays for, which is round trips. `--delay MS` holds every datagram that long before
sending it, STUN, DTLS and media alike, as a link with that one-way delay would. It is symmetric, steady and lossless,
which a real path is not, so it shows how the phases scale with distance rather than what any real network costs.
Medians over 30 calls each, on an Apple M3 Pro:

| Span | Loopback | 25 ms each way (RTT 50) | 50 ms each way (RTT 100) |
|---|---|---|---|
| ICE: answer to both sides completed | 23.1 ms | 56.4 ms | 106.9 ms |
| DTLS: first flight to both sides keyed | 4.2 ms | 110.6 ms | 210.7 ms |
| First SRTP received to first frame decoded | 82.7 ms | 80.3 ms | 80.1 ms |
| Cold start to first frame decoded | 114.5 ms | 255.5 ms | 404.5 ms |

The DTLS handshake grows by two round trips, four one-way trips for its four flights, and ICE by one; the jitter
buffer's wait does not move. The loopback column is the second of three 30-call runs, whose medians for the cold start
were 114.4, 114.5 and 111.1 ms.

With the delay, the timeline also shows the two sides keyed at different times: Alice, the DTLS server, has her keys
once she has sent her last flight and starts sending media at once; the server, the DTLS client, has its keys only when
that flight arrives, half a round trip later.

### What differs from Alice's real call

* **DTLS 1.2, not 1.3.** aiortc's DTLS is OpenSSL's, and it negotiated DTLS 1.2. Chrome has offered DTLS 1.3 for WebRTC
  since Chrome 137 and still accepts 1.2, so a call from Chrome to a server like this one would use 1.2.
* **No cookie exchange.** The DTLS server here (Alice) sends no HelloVerifyRequest, so the handshake is four flights.
  RFC 6347 allows that, and says a server should do the exchange by default, which would add two flights.
* **The SRTP profile.** aiortc offers SRTP_AEAD_AES_256_GCM first, so two aiortc peers settle on it, with a 16-byte tag.
  `--profile cm80` offers only SRTP_AES128_CM_HMAC_SHA1_80, the profile every WebRTC implementation must support, and
  the tag becomes 10 bytes.
* **The server is not ICE-lite.** aiortc has no ICE-lite mode, so the server here runs full ICE and sends its own check,
  the first STUN line above. An ICE-lite server only answers.
* **Who sends the ClientHello: the same.** As the answerer, the server chose `a=setup:active`, the role JSEP recommends,
  so it is the DTLS client and Alice the server, as with the ICE-lite server in the post.
* **The audio.** aiortc's Opus encoder runs at 96 kb/s and adds RTP header extensions, so its packets are larger than
  the first post's 120 bytes.
* **The tampered fingerprint.** Alice's check fails and her side of the call stops: she sends no media and reads none.
  The server, whose own check of Alice's certificate passed, has already started sending. The fingerprint check is
  each side's own.

The addresses in the SDP fixtures are from the documentation ranges (RFC 5737); the demo itself only uses 127.0.0.1.
