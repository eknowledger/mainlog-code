"""Count what each agent placement costs in a call with N people and one agent.

Assumptions, all stated in the article: audio only; one stream per person;
the forwarder sends every stream to everyone (no speaker limit); the mixer
decodes every input and sends each endpoint its own mix of everyone else
(RFC 7667 section 3.6.1). A mixer that mixes only the loudest few decodes
fewer streams, and non-speakers can share one mix (N+1 mixes). Decode counts
are a proxy for cost and delay, not a measurement.
"""
import argparse

PLACEMENTS = {
    "sfu": "agent joins through a forwarding server",
    "mixer-client": "agent joins a mixing server as a client",
    "in-mixer": "agent runs inside the mixing server",
}

# Steps between one person's microphone and the model, and between the
# agent's reply and one listener. "buffer" is where a box lines packets up in
# time before decoding (a mixer also re-times its inputs, RFC 3550 section 3).
PATHS = {
    "sfu": (["encode", "forward", "buffer", "decode"],
            ["encode", "forward", "buffer", "decode"]),
    "mixer-client": (["encode", "buffer", "decode", "mix", "encode", "buffer", "decode"],
                     ["encode", "buffer", "decode", "mix", "encode", "buffer", "decode"]),
    "in-mixer": (["encode", "buffer", "decode"],
                 ["mix", "encode", "buffer", "decode"]),
}


def call_counts(people: int, placement: str) -> dict:
    endpoints = people + 1  # everyone plus the agent
    if placement == "sfu":
        c = dict(streams_to_agent=people, agent_decodes=people, agent_encodes=1,
                 server_decodes=0, server_encodes=0, down_per_person=endpoints - 1,
                 model_hears="each person separately")
    elif placement == "mixer-client":
        c = dict(streams_to_agent=1, agent_decodes=1, agent_encodes=1,
                 server_decodes=endpoints, server_encodes=endpoints, down_per_person=1,
                 model_hears="one mix")
    elif placement == "in-mixer":
        c = dict(streams_to_agent=0, agent_decodes=0, agent_encodes=0,
                 server_decodes=people, server_encodes=people, down_per_person=1,
                 model_hears="each person separately")
    else:
        raise ValueError(placement)
    to_model, to_listener = PATHS[placement]
    c["decodes_before_model"] = to_model.count("decode")
    c["decodes_before_listener"] = to_listener.count("decode")
    c["total_decodes"] = c["agent_decodes"] + c["server_decodes"] + people * c["down_per_person"]
    return c


LABELS = {
    "model_hears": "What the model hears",
    "agent_decodes": "Streams the agent decodes",
    "server_decodes": "Decodes on the server",
    "server_encodes": "Encodes on the server",
    "decodes_before_model": "Decodes between a microphone and the model",
    "decodes_before_listener": "Decodes between the agent's reply and a listener",
    "total_decodes": "Decodes in the whole call",
}
HEADERS = {"sfu": "Through a forwarding server", "mixer-client": "Client of a mixer",
           "in-mixer": "Inside the mixer"}


def table(people: int) -> str:
    rows = ["| | " + " | ".join(HEADERS.values()) + " |", "|---" * (len(HEADERS) + 1) + "|"]
    for key, label in LABELS.items():
        rows.append(f"| {label} | " + " | ".join(str(call_counts(people, p)[key]) for p in HEADERS) + " |")
    return "\n".join(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--people", type=int, default=3)
    print(table(ap.parse_args().people))
