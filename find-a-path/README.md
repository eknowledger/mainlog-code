# How two computers find a path: NAT, STUN, TURN and ICE

From the post [How two computers find a path](https://mainlog.dev/posts/find-a-path/), the third in the series
*Real-time voice, from the packet up*.

| File | What it does |
|---|---|
| `stun.py` | STUN in the standard library: builds a Binding request, answers it with a five-line server, decodes XOR-MAPPED-ADDRESS, and prints the post's example bytes with the XOR arithmetic. `--public host:port` asks a real server instead |
| `ice_priority.py` | RFC 8445's candidate and pair priorities for the post's example call, each checked against [aioice](https://github.com/aiortc/aioice), the ICE implementation under aiortc |
| `requirements.txt` | aioice, for the cross-check only; `stun.py` needs nothing |

## Run it

```sh
python3 stun.py                                   # local server, runs anywhere
python3 stun.py --public stun.l.google.com:19302  # how a real server sees you
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python ice_priority.py
```

`stun.py` prints the post's example first:

```text
  Binding request  (20 B)  00 01 00 00 21 12 a4 42 b7 e7 a7 01 bc 5c a4 93 5d 33 93 47
  Binding success  (32 B)  01 01 00 0c 21 12 a4 42 b7 e7 a7 01 bc 5c a4 93 5d 33 93 47 00 20 00 08 00 01 f5 23 e1 12 a6 48
  port    54321 = 0xd431  XOR 0x2112 = 0xf523
  address 192.0.2.10 = 0xc000020a  XOR 0x2112a442 = 0xe112a648
```

The addresses are from the documentation ranges, and the transaction ID is fixed so the bytes are reproducible; a
real client draws 12 random bytes. The `--public` mode prints your own public address, which is why it is not run
in CI and why the post does not show its output.
