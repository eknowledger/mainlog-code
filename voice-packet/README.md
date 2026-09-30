# What's inside one voice packet, and why it rides UDP

From the post [What's inside one voice packet, and why it rides UDP](https://mainlog.dev/posts/voice-packet/), the
first in the series *Real-time voice, from the packet up*.

| File | What it does |
|---|---|
| `build_packet.py` | Encodes 20 ms of sound with Opus, wraps it in real RTP, UDP and IPv4 headers (checksums included), prints the packet layer by layer, and writes one second of it to `voice.pcap` |
| `one_lost_packet.py` | A model of one lost packet delivered over UDP and over TCP, printing which frames still play on time |
| `draw_timeline.py` | Draws that model as `one_lost_packet.svg`, the figure in the post |
| `requirements.txt` | PyAV, which ships libopus in its wheels, and NumPy |

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python build_packet.py
python3 one_lost_packet.py          # standard library only
```

`build_packet.py` prints:

```text
20 ms of PCM: 960 samples x 2 B = 1920 B. Opus at 32 kb/s: 80 B per frame

IPv4 header   20 B  45 b8 00 78 3a 0a 40 00 40 11 13 61 c0 00 02 0a c6 33 64 14
              total length 120 B, DSCP 46, TTL 64, protocol 17 (UDP), 192.0.2.10 -> 198.51.100.20, checksum 0x1361
UDP header     8 B  d4 31 c3 54 00 64 7b d8
              port 54321 -> 50004, length 100 B (header + RTP + Opus), checksum 0x7bd8
RTP header    12 B  80 6f 03 f2 00 00 25 80 1f 2e 3d 4c
              version 2, payload type 111 (Opus), sequence 1010, timestamp 9600, SSRC 0x1f2e3d4c
Opus payload  80 B  ...
              decodes to 960 samples = 20 ms of audio
Total        120 B  (40 B of headers, 80 B of sound)
```

The Opus bytes can differ between libopus builds, so they are elided here, and so can the UDP checksum that covers them;
everything else is fixed by the inputs.

## Open the capture in Wireshark

`voice.pcap` is raw IPv4 (link type 228), one packet every 20 ms. Wireshark shows it as UDP until told it is RTP:
**Analyze > Decode As**, port 50004, **RTP**. Then **Telephony > RTP > RTP Streams** shows the stream with its sequence
numbers and timing, and the checksums verify.

The addresses are documentation ranges (RFC 5737); nothing here points at a real host.
