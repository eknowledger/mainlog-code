"""Runs for the article. Each subcommand shows one claim.

    python -m call_topology.demo forward [--speaker NAME]
    python -m call_topology.demo loss
    python -m call_topology.demo mix
    python -m call_topology.demo keys
"""
import argparse

from .boxes import AgentEndpoint, Mixer, SelectiveForwarder, decode, encode
from .e2e import DecryptError, seal
from .rtp import Packet

PEOPLE = {0xA1: "Ana", 0xB2: "Ben", 0xC3: "Chloe"}
AGENT_SSRC, MIXER_SSRC = 0xD4, 0xE5
E2E_KEY = b"shared-by-the-ends-only"
FRAME = 960  # 20 ms at 48 kHz
# RFC 6464 levels per 20 ms frame (0 is loudest). Chloe goes quiet in frame 1.
LEVELS = {0xA1: [10, 10, 10], 0xB2: [20, 12, 25], 0xC3: [15, 90, 14]}


def packet(ssrc, i, samples=None):
    n = list(PEOPLE).index(ssrc) if ssrc in PEOPLE else 3
    samples = samples or [100 * (n + 1)] * 4          # four samples keeps output short
    level = LEVELS[ssrc][i] if ssrc in LEVELS else 30
    return Packet(ssrc, 5000 + 100 * n + i, i * FRAME,
                  seal(E2E_KEY, 7001 + 3 * i + n, encode(samples)), audio_level=level)


def frame(i):
    for ssrc, name in PEOPLE.items():
        yield name, packet(ssrc, i)


def forward(speaker=None):
    """Three 20 ms frames through a forwarder that sends the 2 loudest to the agent."""
    sfu, agent = SelectiveForwarder(max_speakers=2), AgentEndpoint(PEOPLE, key=E2E_KEY)
    receivers = list(PEOPLE.values()) + ["agent"]
    for i in range(3):
        packets = list(frame(i))
        for _, pkt in packets:
            sfu.observe(pkt.to_bytes())
        for name, pkt in packets:
            if speaker and name != speaker:
                continue
            sent = sfu.forward(pkt.to_bytes(), name, receivers).get("agent")
            if sent is None:
                print(f"{name:<5} seq {pkt.seq}          level {pkt.audio_level:>2}: not forwarded")
                continue
            out = Packet.from_bytes(sent)
            who, samples = agent.hear(sent)
            print(f"{name:<5} seq {pkt.seq} -> {out.seq}  ts {out.timestamp:<4}"
                  f"  payload bytes unchanged: {out.payload == pkt.payload}"
                  f"  agent decodes {who}: {samples}")


def loss():
    """Ana's packet 5001 is lost before it reaches the forwarder."""
    sfu = SelectiveForwarder(max_speakers=2)
    for i in range(3):
        pkt = packet(0xA1, i)
        if i == 1:
            print(f"Ana   seq {pkt.seq}          lost on the network")
            continue
        sfu.observe(pkt.to_bytes())
        out = Packet.from_bytes(sfu.forward(pkt.to_bytes(), "Ana", ["agent"])["agent"])
        print(f"Ana   seq {pkt.seq} -> {out.seq}")


def mix():
    """One 20 ms frame from everyone, including the agent's reply, through a mixer."""
    mixer = Mixer(MIXER_SSRC, key=E2E_KEY)
    names = {**PEOPLE, AGENT_SSRC: "agent"}
    inputs = [packet(s, 0) for s in PEOPLE] + [packet(AGENT_SSRC, 0, samples=[50] * 4)]
    frames = dict(mixer.decode_input(p.to_bytes()) for p in inputs)
    print("inputs    " + ", ".join(f"{names[s]} {frames[s][0]}" for s in frames))
    for listener in frames:
        out = Packet.from_bytes(mixer.mix_for(listener, frames, timestamp=0))
        who = ", ".join(names[c] for c in out.csrcs)
        print(f"to {names[listener]:<6} SSRC {out.ssrc:#x}  CSRC {who:<18} samples {decode(out.payload)}")
    print(f"mixer decodes: {mixer.decodes}, encodes: {mixer.encodes}")


def keys():
    """The same end-to-end sealed packet at a forwarder, a keyless mixer, and the agent."""
    sealed, ana = packet(0xA1, 0), [100] * 4
    sfu = SelectiveForwarder()
    sfu.observe(sealed.to_bytes())
    relayed = Packet.from_bytes(sfu.forward(sealed.to_bytes(), "Ana", ["agent"])["agent"])
    guess = Mixer(MIXER_SSRC).decode_input(sealed.to_bytes())[1]
    print(f"forwarding server, no key: relays it, payload bytes unchanged: {relayed.payload == sealed.payload}")
    print(f"mixer, no key:             recovers Ana's audio: {guess[:4] == ana or guess[4:8] == ana}")
    print(f"agent with the key:        hears {AgentEndpoint(PEOPLE, key=E2E_KEY).hear(sealed.to_bytes())}")
    try:
        AgentEndpoint(PEOPLE, key=b"wrong key").hear(sealed.to_bytes())
    except DecryptError as exc:
        print(f"agent with the wrong key:  {exc}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", choices=["forward", "loss", "mix", "keys"])
    ap.add_argument("--speaker")
    a = ap.parse_args()
    forward(a.speaker) if a.run == "forward" else globals()[a.run]()
