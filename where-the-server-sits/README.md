# Where the server sits: mesh, MCU, SFU, and the server that is the other peer

From the post [Where the server sits](https://mainlog.dev/posts/where-the-server-sits/), the sixth in the series
*Real-time voice, from the packet up*.

| File | What it does |
|---|---|
| `topology_cost.py` | The arithmetic behind the post and its *Call topology cost* tool: for N people on a call, each client's upload and download, the server's traffic, and how many encrypts, decrypts, decodes, mixes and encodes each box does every 20 ms, for mesh, SFU and MCU, and for one user talking to a server that is the other peer. Standard library only, and deterministic |
| `measure.py` | Times the server's work for one 20 ms frame on this machine: a real MCU (libopus decode, mix, libopus encode, SRTP) against a real SFU (SRTP decrypt and re-encrypt only) |
| `requirements.txt` | PyAV, which ships libopus in its wheels, NumPy, and pylibsrtp, which ships libsrtp; for `measure.py` only |

## Run it

```sh
python3 topology_cost.py                   # standard library only
python3 topology_cost.py --srtp-tag 10     # the same with SRTP's 10-byte tag on every packet
python3 topology_cost.py -n 10 --loudest 3 # an SFU forwarding only the three loudest
python3 topology_cost.py -n 5 --bitrate 16 # another constant Opus bitrate; --frame-ms 10, 20, 40 or 60
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python measure.py
```

## The arithmetic

The packet is [post 1's](../voice-packet/): 80 bytes of Opus at a constant 32 kb/s, in 12 bytes of RTP, 8 of UDP and
20 of IPv4, 50 times a second. Like post 1, the default counts no SRTP tag and no header extensions; the last line says
what the tag adds. Everyone talks all the time, so these are the busiest case. `topology_cost.py` prints:

```text
One stream: 80 B Opus + 12 B RTP + 8 B UDP + 20 B IPv4 = 120 B, 50 packets/s = 48 kb/s

N = 2 (Alice, Bob), everyone talking, every stream forwarded
                                    one client              the server
  pattern                   up kb/s  down kb/s     in kb/s    out kb/s  out pkt/s
  mesh                           48         48           -           -          -
  SFU                            48         48          96          96        100
  MCU                            48         48          96          96        100

  per 20 ms frame                 one client                           the server
  pattern                 enc  prot unprot  dec  mix    unprot  dec  mix  enc  prot
  mesh                      1     1      1    1    0        no server
  SFU                       1     1      1    1    0         2    0    0    0     2
  MCU                       1     1      1    1    0         2    2    0    2     2

N = 3 (Alice, Bob, Carol), everyone talking, every stream forwarded
                                    one client              the server
  pattern                   up kb/s  down kb/s     in kb/s    out kb/s  out pkt/s
  mesh                           96         96           -           -          -
  SFU                            48         96         144         288        300
  MCU                            48         48         144         144        150

  per 20 ms frame                 one client                           the server
  pattern                 enc  prot unprot  dec  mix    unprot  dec  mix  enc  prot
  mesh                      2     2      2    2    1        no server
  SFU                       1     1      2    2    1         3    0    0    0     6
  MCU                       1     1      1    1    0         3    3    3    3     3

One 20 ms frame of Alice's voice, Alice, Bob and Carol on the call:
  mesh  Alice encodes 2 (one encoder per connection), encrypts 2 (one key for Bob, one for Carol),
        sends 2 x 120 B
        Bob and Carol each decrypt 1 and decode 1 of hers; no server
  SFU   Alice encodes 1, encrypts 1, sends 1 x 120 B
        the SFU decrypts 1 with Alice's key, encrypts 2 (Bob's key, Carol's key), sends 2 x 120 B
        Bob and Carol each decrypt 1 and decode 1 of hers, and mix it with the other speaker locally
  MCU   Alice encodes 1, encrypts 1, sends 1 x 120 B
        the MCU decrypts 1 and decodes 1 of hers; it puts her samples into Bob's mix and Carol's mix,
        then encodes and encrypts those mixes, 1 each, which also carry the third person's voice
        Bob and Carol each decrypt 1 and decode 1 mixed stream

N = 5 (5 people), everyone talking, every stream forwarded
                                    one client              the server
  pattern                   up kb/s  down kb/s     in kb/s    out kb/s  out pkt/s
  mesh                          192        192           -           -          -
  SFU                            48        192         240         960       1000
  MCU                            48         48         240         240        250

  per 20 ms frame                 one client                           the server
  pattern                 enc  prot unprot  dec  mix    unprot  dec  mix  enc  prot
  mesh                      4     4      4    4    1        no server
  SFU                       1     1      4    4    1         5    0    0    0    20
  MCU                       1     1      1    1    0         5    5    5    5     5

N = 10 (10 people), everyone talking, every stream forwarded
                                    one client              the server
  pattern                   up kb/s  down kb/s     in kb/s    out kb/s  out pkt/s
  mesh                          432        432           -           -          -
  SFU                            48        432         480        4320       4500
  MCU                            48         48         480         480        500

  per 20 ms frame                 one client                           the server
  pattern                 enc  prot unprot  dec  mix    unprot  dec  mix  enc  prot
  mesh                      9     9      9    9    1        no server
  SFU                       1     1      9    9    1        10    0    0    0    90
  MCU                       1     1      1    1    0        10   10   10   10    10

N = 10, the SFU forwarding only the 3 loudest, picked from the senders' audio levels (RFC 6464)
                                    one client              the server
  pattern                   up kb/s  down kb/s     in kb/s    out kb/s  out pkt/s
  SFU                            48        432         480        4320       4500
  SFU, 3 loudest                 48        144         480        1296       1350

  per 20 ms frame                 one client                           the server
  pattern                 enc  prot unprot  dec  mix    unprot  dec  mix  enc  prot
  SFU                       1     1      9    9    1        10    0    0    0    90
  SFU, 3 loudest            1     1      3    3    1        10    0    0    0    27

One user and a service that is the other peer (RFC 7667 Topo-Point-to-Point), per session
                                    one client              the server
  pattern                   up kb/s  down kb/s     in kb/s    out kb/s  out pkt/s
  server is the peer             48         48          48          48         50

  per 20 ms frame                 one client                           the server
  pattern                 enc  prot unprot  dec  mix    unprot  dec  mix  enc  prot
  server is the peer        1     1      1    1    0         1    1    0    1     1
  assumed: the user's audio is decoded to be used, at the edge or behind it, and the reply is encoded
  1,000 such sessions: 48 Mb/s in and 48 Mb/s out, and every 20 ms 1,000 each of decrypt, decode, encode, encrypt

With SRTP's tag: 130 B = 52 kb/s per stream with AES128_CM_HMAC_SHA1_80 (10 B), 136 B = 54.4 kb/s
with AES-GCM (16 B). Every kb/s above scales by the same factor.
```

Columns, per 20 ms frame: `enc` Opus encodes, `prot` SRTP encrypts (protect), `unprot` SRTP verifies and decrypts
(unprotect), `dec` Opus decodes, `mix` mixes built. The choices behind the counts are in the script's docstring:
one encoder per peer connection in the mesh, one mix per listener in the MCU (everyone but themselves), and a reply
playing while the user talks in the one-to-one case.

## The measurement

`measure.py` runs the MCU and the SFU on the same packets: each participant's own SRTP keys
(SRTP_AES128_CM_HMAC_SHA1_80), the server's own keys towards each of them, and every codec and SRTP context alive for
the whole call. On an Apple M3 Pro it printed:

```text
One machine: arm64, Darwin 25.6.0, Python 3.13.5, PyAV 19.0.0 (libopus 1.6.1), one core.
Median and, in brackets, 95th percentile of 1000 frames, after a second of warm-up.

  one operation, one voice: SRTP protect 1.5 us, SRTP unprotect 1.0 us, Opus decode 44.3 us, Opus encode 334.6 us

    N    MCU us/frame    SFU us/frame   MCU / SFU   MCU share of 20 ms  mixes in SILK   encode, CELT / SILK
    3     1187 ( 1231)       7 (    9)        162x                5.9%             0%            338 us / -
    5     1977 ( 2035)      20 (   21)         98x                9.9%             0%            339 us / -
   10    10431 (14210)      77 (   86)        135x               52.2%            81%      337 us / 1242 us

  latency is not the processing time: each encode also delays the sound by the encoder's look-ahead,
  312 samples = 6.5 ms here, which an MCU's re-encode adds again for every listener.

  libopus picks a mode per frame from the sound, SILK (linear prediction) or CELT, and an encode in SILK
  cost several times one in CELT here. A mix of nine of these voices went into SILK for much of the call;
  so the MCU's cost depends on what it is mixing, not only on how many people are in it.

  one user, server is the peer: 384 us per frame (decrypt, decode, encode a reply, encrypt), 1.9% of 20 ms
```

⚠️ **This is one machine's measurement, not a benchmark.** Both paths are driven from Python, so both carry a Python
call per operation, included in the SFU's 1 to 2 microseconds per packet; a server written in C or Go pays less of
it. The numbers move from run to run and between machines. What holds is the shape: the SFU's work is SRTP only,
growing with the packets it sends, N x (N-1); the MCU's is a full codec cycle per participant, and cost about 100 to 160
times the SFU's at the three sizes here. The look-ahead line is fixed by libopus and the sample rate, not the machine.

## Why it is built this way

- **Post 1's packet**, so the series agrees with itself: 120 B and 48 kb/s per stream, before SRTP.
- **The synthetic voice from post 2** (harmonics of a pitch under two formants, pulsed like syllables), at a different
  pitch per participant, rather than a tone, because the encoder's cost depends on what it is given. A fresh encoder ran several times slower for its first few hundred milliseconds, so the first second is
  dropped. And libopus chooses SILK or CELT frame by frame from the sound: one voice stayed in CELT at about 340 us an
  encode, while the nine-voice mixes of the ten-person call spent 81% of their frames in SILK at about 1,240 us. That
  is why the MCU's time jumps between N = 5 and N = 10, and it is a property of this signal, not a law: real speech
  may choose differently.
- **Contexts that live for the call.** A codec or SRTP context created per frame would measure set-up, not work.
