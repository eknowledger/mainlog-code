"""ICE's arithmetic for Alice's call: every candidate's priority, every pair's, and the order the checks run in.

    python ice_priority.py

RFC 8445 gives both formulas. A candidate's priority packs three preferences into one 32-bit number:

    priority = 2^24 * type preference + 2^8 * local preference + (256 - component ID)

with type preferences of 126 for host, 110 for peer-reflexive, 100 for server-reflexive and 0 for relayed. A pair's
priority combines the two sides' candidates, G from the controlling agent and D from the controlled one:

    pair priority = 2^32 * min(G, D) + 2 * max(G, D) + (1 if G > D else 0)

Each number is computed here from the formula, then again by aioice, the ICE implementation under aiortc, and the
script stops if they ever disagree. Then the step that is easiest to get wrong: a server-reflexive candidate is never
checked on its own. Pairs are formed, then each reflexive local candidate is replaced by its base, the host address the
packets really leave from, and duplicates are pruned (RFC 8445 section 6.1.2.4). aioice is built the same way, pairing
each of its sockets with each remote candidate. The addresses are from the documentation ranges (RFC 5737).
"""

from __future__ import annotations

from aioice.candidate import Candidate, candidate_priority as aioice_candidate_priority
from aioice.ice import candidate_pair_priority as aioice_pair_priority

TYPE_PREF = {"host": 126, "prflx": 110, "srflx": 100, "relay": 0}


def priority(kind: str, local_pref: int = 65535, component: int = 1) -> int:
    return (1 << 24) * TYPE_PREF[kind] + (1 << 8) * local_pref + (256 - component)


def pair_priority(g: int, d: int) -> int:
    return (1 << 32) * min(g, d) + 2 * max(g, d) + (1 if g > d else 0)


# Alice's browser is the controlling agent: a full ICE agent that gathered three candidates.
ALICE = [
    ("host", "192.168.1.42", 50004),   # her laptop's own address (a browser shows it as a .local name)
    ("srflx", "192.0.2.10", 54321),   # what the STUN server saw: her router's public address
    ("relay", "198.51.100.50", 49152),  # an address on a TURN server, relaying for her
]
# The assistant's server runs ICE-lite: one host candidate on a public address, no checks of its own.
SERVER = [("host", "198.51.100.20", 50004)]


def main() -> None:
    print("Candidates (component 1, local preference 65535):")
    side = {}
    for who, cands in (("Alice", ALICE), ("server", SERVER)):
        for kind, ip, port in cands:
            p = priority(kind)
            assert p == aioice_candidate_priority(1, kind), (kind, p)
            side[(who, kind)] = (p, ip, port)
            print(f"  {who:<6} {kind:<5} {ip}:{port:<6} priority {p:>10}  = 2^24*{TYPE_PREF[kind]} + 2^8*65535 + 255")
    print()

    # RFC 8445 6.1.2.2 and 6.1.2.3: every local candidate with every remote one of the same component, each priced.
    pairs = []
    for kind, ip, port in ALICE:
        g = side[("Alice", kind)][0]
        for skind, sip, sport in SERVER:
            d = side[("server", skind)][0]
            pp = pair_priority(g, d)
            local = Candidate(foundation="1", component=1, transport="udp", priority=g, host=ip, port=port, type=kind)
            remote = Candidate(foundation="2", component=1, transport="udp", priority=d, host=sip, port=sport, type=skind)
            assert pp == aioice_pair_priority(local, remote, True), (kind, pp)
            # 6.1.2.4: a reflexive local candidate is replaced by its base, the address the packets actually leave from.
            base = ALICE[0] if kind == "srflx" else (kind, ip, port)
            pairs.append({"pp": pp, "name": f"Alice {kind} -> server {skind}", "base": (base[1], base[2]), "remote": (sip, sport)})
    pairs.sort(key=lambda x: x["pp"], reverse=True)
    print("Pairs formed, highest priority first:")
    for x in pairs:
        print(f"  {x['name']:<28} {x['pp']:>20}   sent from {x['base'][0]}:{x['base'][1]}")
    print()

    # 6.1.2.4: a pair is redundant if a higher-priority pair has the same base and the same remote candidate.
    kept, seen = [], set()
    for x in pairs:
        key = (x["base"], x["remote"])
        if key in seen:
            print(f"Pruned: {x['name']}. Its base is Alice's host address, so it would send exactly the check above it.")
            continue
        seen.add(key)
        kept.append(x)
    print()
    print("The checks, in the order they run:")
    for i, x in enumerate(kept, 1):
        print(f"  {i}. from {x['base'][0]}:{x['base'][1]} to {x['remote'][0]}:{x['remote'][1]}   ({x['name']})")
    print()
    print("Check 1 leaves Alice's laptop, her router rewrites its source to 192.0.2.10:54321, and the server answers")
    print("that address. The answer's XOR-MAPPED-ADDRESS is 192.0.2.10:54321, her server-reflexive candidate, so the")
    print(f"valid pair is Alice srflx -> server host, priority {pair_priority(side[('Alice', 'srflx')][0], side[('server', 'host')][0])}.")
    print()
    print("Every priority matched aioice.")


if __name__ == "__main__":
    main()
