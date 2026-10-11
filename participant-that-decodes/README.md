# Where does the audio get decoded?

A small model of an AI agent in a multi-party call. The demo needs only Python 3.10 or newer; the tests need pytest.

```
python -m call_topology.demo forward --speaker Chloe  # sequence rewrite through a forwarder
python -m call_topology.demo loss                     # a network loss stays a gap
python -m call_topology.demo mix                      # what a mixer decodes and sends
python -m call_topology.demo keys                     # who can use an end-to-end sealed packet
python -m call_topology.counts --people 8             # decode counts per agent placement
python -m pytest tests                                # needs pytest
```

| Module | What it shows |
|---|---|
| `rtp.py` | The RTP fixed header (RFC 3550 section 5.1) and the RFC 6464 audio-level header extension in the one-byte form of RFC 8285 |
| `boxes.py` | A selective forwarder that reads headers only, a media-mixing mixer that decodes and sends each listener everyone but themselves, and an agent endpoint |
| `e2e.py` | A **toy** end-to-end frame cipher standing in for SFrame (RFC 9605), so the demo needs only the standard library. It is not real cryptography |
| `counts.py` | Decodes per placement, and decode steps between a microphone and the model |

The demo codec is raw 16-bit PCM, so "decoding" is unpacking bytes. Real calls use a codec
such as Opus; that changes the cost of each decode, not where the decodes happen.

Assumptions in `counts.py`: audio only, one stream per person, the forwarder sends every
stream to every endpoint, and the mixer decodes every input and sends each endpoint its own
mix of everyone else (RFC 7667 section 3.6.1). The agent counts as one more endpoint. A
mixer that mixes only the loudest few decodes less. Decode counts are a proxy, not a
measurement.

Simplifications: the forwarder picks speakers from each packet's level, where a real one
smooths levels over time (RFC 6464 section 5). It shifts sequence numbers only for packets
it drops on purpose, so a real loss still shows as a gap. It keeps the sender's SSRC;
mediasoup gives each receiver its own.
