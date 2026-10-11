# The receiver talks back: RTCP, round-trip time and bandwidth estimation

From the post [The receiver talks back](https://mainlog.dev/posts/receiver-talks-back/), the fifth in the series
*Real-time voice, from the packet up*.

| File | What it does |
|---|---|
| `rtcp_reports.py` | Builds Alice's sender report and the server's receiver report byte by byte, reads them back, and shows the arithmetic: the 8-bit loss fraction, RFC 3550's jitter estimator over modelled arrival times, round-trip time from LSR and DLSR, and how often RTCP may be sent at all. Checks itself against RFC 3550's own Figure 2, then has aiortc parse every byte. `--jitter-csv` prints the estimator packet by packet |
| `capture_call.py` | A real call between two aiortc peers in one process, through ICE, DTLS and SRTP, with 30 ms added each way and one in 40 of Alice's packets dropped. Reads the server's receiver reports from the bytes and checks every loss count and the round trip against what was injected |
| `backoff_sim.py` | A model, not a measurement: a sender running the loss-based half of Google Congestion Control on receiver reports, with Opus between 6 and 32 kb/s, through a path that narrows and through loss that backing off cannot fix. `--csv` prints one scenario per second, for a chart; `--reports` prints every report of one run, which is what the site's *Bitrate under loss* tool shows and is checked against |
| `requirements.txt` | aiortc, for the cross-check and the live call; the other two scripts need nothing |

## Run it

```sh
python3 rtcp_reports.py          # standard library only
python3 rtcp_reports.py --jitter-csv  # the jitter estimator packet by packet, 243 rows, for a chart
python3 backoff_sim.py
python3 backoff_sim.py --reports squeeze --interval 2   # every report of one run
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python rtcp_reports.py # the same, plus aiortc's parse of the same bytes
.venv/bin/python capture_call.py # about 10 s
```

`rtcp_reports.py` prints the two packets and what Alice learns from them:

```text
Alice's SR + SDES (56 B), sent 07:17:04.250 UTC:
  80 c8 00 06 1f 2e 3d 4c ee 69 d8 f0 40 00 00 00 00 02 ee 00 00 00 00 c8 00 00 3e 80 81 ca 00 06 1f 2e 3d 4c 01 10 4b 39 67 47 79 58 38 4f 41 4b 38 61 48 38 4d 79 00 00
  NTP 0xee69d8f040000000 = 3999914224 s + 0.25 s since 1900; RTP timestamp 192000; 200 packets, 16000 payload octets
  its middle 32 bits, which the server will echo as LSR: 0xd8f04000 = 0xd8f0:4000 (55536.250000 s)

The server, 5.020 s into the call:
  expected 250 (seq 1000 to 1249), received 244, lost 6: 1037, 1088, 1089, 1142, 1190, 1221
  fraction lost = (6 << 8) // 250 = 6, which reads back as 6/256 = 2.34% (the true share is 2.40%)
  interarrival jitter: 130.29 units in floating point, 130 as the RFC's scaled integer, = 2.708 ms at 48 kHz
  first steps, |D| then J: seq 1001: 129 -> 8.06, seq 1002: 148 -> 16.81, seq 1003: 17 -> 16.82, seq 1004: 346 -> 37.39
  DLSR: the SR arrived at 4.030 s and the RR leaves at 5.020 s, 0.990 s later = 64880 units of 1/65536 s

The server's RR + SDES (60 B):
  81 c9 00 07 7a 3b 9c 5d 1f 2e 3d 4c 06 00 00 06 00 00 04 e1 00 00 00 82 d8 f0 40 00 00 00 fd 70 81 ca 00 06 7a 3b 9c 5d 01 10 73 2b 72 4e 4d 30 4d 37 4d 62 55 6c 49 31 45 44 00 00
  report block: 1f 2e 3d 4c 06 00 00 06 00 00 04 e1 00 00 00 82 d8 f0 40 00 00 00 fd 70

Alice decodes it (RR + SDES), and it arrives 5.050 s into the call:
  fraction lost 6 = 2.34%, cumulative lost 6, highest seq 1249, jitter 130 units
  A    0xd8f1:4ccc (55537.299988 s)
  LSR  0xd8f0:4000 (55536.250000 s)
  DLSR 0x0000:fd70 (0.989990 s)
  round trip = A - LSR - DLSR = 3932 / 65536 s = 59.998 ms (the model's is 60 ms; the difference is the 16-bit fraction)
  the server's clock never enters the sum: DLSR is a duration, A and LSR are both Alice's clock

How often (RFC 3550 6.3.1), for a 48 kb/s session:
  RTCP gets 5% = 2400 b/s; average compound RTCP packet 86 B with IPv4 and UDP
  n*C = 2 x 86 / 300 = 0.573 s; with the 5 s minimum, Td = 5 s, so T falls in [2.052, 6.156] s, 4.104 s on average
  the reduced minimum, 360 / 48 = 7.5 s, is no help below 72 kb/s
  without the 5 s floor (RTP/AVPF, RFC 4585 3.2): about 0.471 s on average

aiortc parses both compounds to the same fields, and re-serialises them to the same bytes.
```

Either hex line pastes straight into the site's *RTP / RTCP inspector*. Before any of that it reproduces RFC 3550's
Figure 2 (A 0xb710:8000, LSR 0xb705:2000, DLSR 0x0005:4000, a round trip of 6.125 s), so the arithmetic is checked
against the spec rather than only against itself.

`backoff_sim.py`, the narrowing path, every fifth second of 120 (the table runs to the end):

```text
     t   capacity |  RR every 5 s: opus  wire   loss  action   |  RR every 1 s: opus  wire   loss  action
    30s     100   |              32.0  48.0   0.0%  ceiling  |              32.0  48.0   0.0%  ceiling
    35s      30   |              23.0  39.0  37.5%  decrease |              16.3  32.3   7.0%  hold
    40s      30   |              18.5  34.5  23.0%  decrease |              16.3  32.3   7.0%  hold
    45s      30   |              16.3  32.3  12.9%  decrease |              16.3  32.3   7.0%  hold
    50s      30   |              16.3  32.3   7.0%  hold     |              16.3  32.3   7.0%  hold
    60s      30   |              16.3  32.3   7.0%  hold     |              16.3  32.3   7.0%  hold
    65s     100   |              17.9  33.9   0.0%  increase |              25.2  41.2   0.0%  increase
    70s     100   |              19.6  35.6   0.0%  increase |              32.0  48.0   0.0%  ceiling

  RR every 5 s: lowest Opus 16.3 kb/s, first reached at 45 s; at 60 s, when the loss stops, 16.3 kb/s with 7.0% reported loss; 237 packets lost in all; back at 32 kb/s 45 s later
  RR every 1 s: lowest Opus 16.3 kb/s, first reached at 33 s; at 60 s, when the loss stops, 16.3 kb/s with 7.0% reported loss; 132 packets lost in all; back at 32 kb/s 9 s later
```

and the loss backing off cannot fix:

```text
  RR every 5 s: lowest Opus 19.3 kb/s, first reached at 60 s; at 60 s, when the loss stops, 19.3 kb/s with 14.8% reported loss; 200 packets lost in all; back at 32 kb/s 35 s later
  RR every 1 s: lowest Opus 6.0 kb/s, first reached at 51 s; at 60 s, when the loss stops, 6.0 kb/s with 14.8% reported loss; 200 packets lost in all; back at 32 kb/s 16 s later
```

`--reports` takes the model's options, which are the site tool's inputs: `--interval` (seconds between reports, 5 by
default), `--frame` (20, 40 or 60 ms of audio per packet, which sets the header overhead), `--ceiling` (the Opus rate
the sender starts at and returns to, 32 kb/s), `--capacity` (what the squeeze narrows the path to, 30 kb/s) and `--loss`
(the radio link's loss from 40 s, 15%). The table and `--csv` take all but `--interval`, since they always set 5 s against 1 s; with none of
the options, every output is the one above. Longer packets in the squeeze, for example:

```text
$ python3 backoff_sim.py --reports squeeze --interval 5 --frame 60 | tail -2
  first decrease at 35 s, 5 s after the loss began at 30 s, on 50/256 = 19.5%
  RR every 5 s: lowest Opus 26.5 kb/s, first reached at 40 s; at 60 s, when the loss stops, 26.5 kb/s with 5.5% reported loss; 45 packets lost in all; back at 32 kb/s 20 s later
```

At 60 ms a packet carries 5.33 kb/s of headers instead of 16, so the same 32 kb/s of Opus overshoots a 30 kb/s path by
less, and the sender settles inside the hold band at 31.8 kb/s on the wire.

`capture_call.py` differs from run to run, because aiortc spaces its reports at random between 0.5 and 1.5 s. One run:

```text
Alice sent 9 SRs; the server sent 9 RRs; the network dropped 12 of Alice's RTP packets: 1039, 1079, 1119, 1159, 1199, 1239, 1279, 1319, 1359, 1399, 1439, 1479

  RR at   highest  cum.lost  fraction      lost here  injected here  RTT (DLSR, by wallclock)  RTT (NTP)
    1.17s     1055         1    4/256= 1.6%    1 of  56              1                         -          -
    2.43s     1118         2    4/256= 1.6%    1 of  63              1                   61.5 ms    72.8 ms
    3.92s     1193         4    6/256= 2.3%    2 of  75              2                   63.1 ms    67.6 ms
    5.39s     1266         6    7/256= 2.7%    2 of  73              2                   62.3 ms    75.9 ms
    6.29s     1311         7    5/256= 2.0%    1 of  45              1                   61.1 ms    63.2 ms
    6.80s     1336         8   10/256= 3.9%    1 of  25              1                   60.7 ms    62.8 ms
    8.06s     1398         9    4/256= 1.6%    1 of  62              1                   62.1 ms    78.6 ms
    9.16s     1454        11    9/256= 3.5%    2 of  56              2                   61.1 ms    62.1 ms
    9.76s     1484        12    8/256= 3.1%    1 of  30              1                   62.8 ms    79.1 ms

Every RR's new losses matched the packets dropped in its interval: 9 of 9.
Round trip from LSR/DLSR, against the SR's real send time: 60.7 to 63.1 ms over 8 reports (the model is 60 ms).
The same, by RFC 3550's A - LSR - DLSR on the NTP clock: 62.1 to 79.1 ms. aiortc stamps its SR with the time of its last RTP packet, not the time the SR is sent,
  and in this run that stamp lagged the send by 1.0 to 17.3 ms, and that lag accounts for the difference report by report, to within 0.01 ms.
aiortc's own estimate, smoothed (getStats, remote-inbound-rtp): 62.3 ms; its fractionLost 8, packetsLost 12.
```

What holds on every run: each report's new losses equal the packets dropped in its interval, and the round trip
measured against the SR's real send time lands a few milliseconds above the 60 ms the model adds, the rest being
the time the two peers take to handle a packet.

## Why it is built this way

- **The values are the series'.** Alice's SSRC 0x1F2E3D4C, sequence numbers from 1000 and timestamps 960 apart are
  the first post's packet; the 30 ms each way is the second post's. The server's SSRC, 0x7A3B9C5D, and both CNAMEs
  are fixed so the bytes repeat; real ones are random (RFC 3550 section 5.1, RFC 7022 section 4.2).
- **The network in `rtcp_reports.py` is a model, stated in one place.** Queueing is 0 to 8 ms per packet from a
  seeded generator, so the jitter is the same on every machine and every Python version. For a uniform 0 to 8 ms
  queue the expected gap between neighbours is 8/3 = 2.67 ms, and the estimator lands on 2.708 ms.
- **The live call hooks in where aiortc hands a packet to SRTP**, the one place RTCP is still in the clear. On the
  wire every one of these packets is SRTCP, which is the fourth post. Loopback has no loss or delay of its own, so
  everything the reports show was put there by the hook.
- **Two round trips in the live call, on purpose.** RFC 3550 section 6.4.1 says an SR's NTP timestamp is the
  wallclock time "when this report was sent". aiortc puts the time of its last RTP packet there instead, so a
  receiver computing A - LSR - DLSR from its bytes reads up to one packet interval high. aiortc's own estimate is
  unaffected, because it times the SR itself.
- **The simulation is one rule, applied as written.** draft-ietf-rmcat-gcc-02 section 6, once per report, on the
  report's 8-bit fraction. It leaves out the delay-based half, which section 7 says to disable when the receiver
  sends only receiver reports, and it is not libwebrtc's current code, which ramps up by time rather than per
  report and has a newer loss estimator besides.

The addresses and identifiers are examples; nothing here points at a real host.
