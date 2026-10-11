import pytest

from call_topology.boxes import AgentEndpoint, Mixer, SelectiveForwarder, decode, encode
from call_topology.counts import call_counts
from call_topology.demo import forward, keys, loss, mix
from call_topology.e2e import DecryptError, open_, seal
from call_topology.rtp import Packet

KEY = b"k"


def pkt(ssrc, seq, level, samples=(1, 2)):
    return Packet(ssrc, seq, seq * 960, seal(KEY, seq, encode(list(samples))), audio_level=level)


def test_rtp_round_trip_with_extension_and_csrcs():
    p = Packet(0x1234, 65535, 2**32 - 1, b"\x01\x02", audio_level=37, csrcs=(7, 8))
    raw = p.to_bytes()
    assert raw[0] >> 6 == 2 and raw[0] & 0x10 and raw[0] & 0x0F == 2
    assert raw[20:22] == b"\xbe\xde"                    # one-byte extension profile
    assert Packet.from_bytes(raw) == p


def test_forwarder_rewrites_sequence_but_not_timestamp_or_payload():
    sfu = SelectiveForwarder(max_speakers=1)
    loud, quiet = pkt(1, 10, level=5), pkt(2, 50, level=60)
    for p in (loud, quiet):
        sfu.observe(p.to_bytes())
    assert "agent" not in sfu.forward(quiet.to_bytes(), "b", ["agent"])
    out = Packet.from_bytes(sfu.forward(loud.to_bytes(), "a", ["a", "agent"])["agent"])
    assert out.payload == loud.payload and out.timestamp == loud.timestamp
    assert "a" not in sfu.forward(loud.to_bytes(), "a", ["a"])    # never back to the sender


def run_leg(arrivals):
    """Send (seq, level) packets from source 1, against a steady source 2, to one agent."""
    sfu, seqs = SelectiveForwarder(max_speakers=1), []
    for seq, level in arrivals:
        p = pkt(1, seq, level)
        sfu.observe(p.to_bytes())
        sfu.observe(pkt(2, 900 + seq, 50).to_bytes())
        sent = sfu.forward(p.to_bytes(), "a", ["agent"]).get("agent")
        if sent:
            seqs.append(Packet.from_bytes(sent).seq)
    return seqs


def test_forwarder_closes_the_gaps_it_makes():
    assert run_leg([(100, 5), (101, 99), (102, 5)]) == [100, 101]


def test_forwarder_keeps_a_real_loss_visible():
    assert run_leg([(100, 5), (102, 5)]) == [100, 102]      # 101 never arrived


def test_mixer_needs_plaintext_and_never_sends_your_own_voice():
    a, b, c = pkt(1, 1, 5, (10, 10)), pkt(2, 1, 5, (20, 20)), pkt(3, 1, 5, (40, 40))
    assert Mixer(9).decode_input(a.to_bytes())[1] != [10, 10]       # ciphertext
    mixer = Mixer(9, key=KEY)
    frames = dict(mixer.decode_input(p.to_bytes()) for p in (a, b, c))
    out = Packet.from_bytes(mixer.mix_for(1, frames, timestamp=0))
    assert out.ssrc == 9 and out.csrcs == (2, 3) and decode(out.payload) == [60, 60]


def test_agent_needs_the_end_to_end_key():
    p = pkt(1, 1, 5, (7, 7))
    assert AgentEndpoint({1: "Ana"}, key=KEY).hear(p.to_bytes()) == ("Ana", [7, 7])
    with pytest.raises(DecryptError):
        AgentEndpoint({1: "Ana"}, key=b"other").hear(p.to_bytes())
    assert open_(KEY, seal(KEY, 3, b"xy")) == b"xy"


@pytest.mark.parametrize("people,placement,agent_dec,server_dec,server_enc,total,before_model", [
    (3, "sfu", 3, 0, 0, 12, 1),
    (3, "mixer-client", 1, 4, 4, 8, 2),
    (3, "in-mixer", 0, 3, 3, 6, 1),
    (1, "sfu", 1, 0, 0, 2, 1),
    (1, "mixer-client", 1, 2, 2, 4, 2),
    (1, "in-mixer", 0, 1, 1, 2, 1),
])
def test_counts(people, placement, agent_dec, server_dec, server_enc, total, before_model):
    c = call_counts(people, placement)
    assert (c["agent_decodes"], c["server_decodes"], c["server_encodes"], c["total_decodes"],
            c["decodes_before_model"]) == (agent_dec, server_dec, server_enc, total, before_model)


def test_unknown_placement():
    with pytest.raises(ValueError):
        call_counts(3, "mesh")


def test_demo_runs(capsys):
    forward("Chloe")
    assert "Chloe seq 5202 -> 5201" in capsys.readouterr().out
    loss()
    assert "seq 5002 -> 5002" in capsys.readouterr().out
    mix()
    out = capsys.readouterr().out
    assert "inputs    Ana 100, Ben 200, Chloe 300, agent 50" in out and "mixer decodes: 4, encodes: 4" in out
    keys()
    out = capsys.readouterr().out
    assert "payload bytes unchanged: True" in out and "recovers Ana's audio: False" in out
    assert "this box is not one of the ends" in out


def test_mixer_counts_its_work():
    m = Mixer(9, key=KEY)
    frames = dict(m.decode_input(pkt(s, 1, 5).to_bytes()) for s in (1, 2, 3))
    for s in frames:
        m.mix_for(s, frames, timestamp=0)
    assert (m.decodes, m.encodes) == (3, 3)
