# agent-cost-control: circuit breakers for inference

The companion code for [Circuit breakers for inference: tripping on what returns
200](https://mainlog.dev/posts/agent-cost-control/). The real Anthropic SDK against a local server that answers
slowly on purpose, the defaults an agent inherits read from the installed packages, and a seeded simulation of a
support chat through five bad minutes at its model provider.

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python timeouts.py    # about 50 s: what timeout=3.0 does and doesn't stop
    .venv/bin/python defaults.py    # the timeouts, retries and turn limits nobody chose
    .venv/bin/python sim.py         # the policies, three incidents (stdlib only)
    .venv/bin/python sim.py --task 350
    .venv/bin/python -m pytest -q tests

No API key and no network: `fake_server.py` stands in for the Messages endpoint on 127.0.0.1.

## What's here

- `fake_server.py`: a stand-in endpoint. Streams one token every `gap` seconds as server-sent events, or trickles a
  JSON answer 20 bytes at a time, can stall once mid-stream, or can say nothing at all.
- `timeouts.py`: five runs. With `timeout=3.0`, an 18.1 s stream and a 16.5 s JSON answer both complete, because the
  timeout bounds each read, not the call. A 4 s stall mid-stream raises `httpx2.ReadTimeout`, which is not an
  `anthropic.APIError`. A deadline from outside the SDK, `asyncio.timeout`, stops the stream at 5.0 s. A server that
  never answers times out three times, the SDK's two retries included. Saved output in `timeouts-output.txt`.
- `defaults.py`: every value imported from the pinned packages: anthropic 1.13.0, openai 3.28.0, openai-agents 0.23.1,
  langgraph 1.2.14. Saved output in `defaults-output.txt`.
- `sim.py`: the support chat. 1,000 conversations, one a second, each needing four good model calls in ten turns,
  with the primary model bad from 300 s to 600 s. Five checks, each switchable: a status breaker, a stream-rate check
  that aborts a trickling stream, a validity check, a loop guard, and a per-conversation budget of 60 s and 40,000
  tokens. Time runs on an event queue, so a breaker opened at 340 s is seen by calls after 340 s and none before.
  Saved output in `sim-output.txt`.
- `export_runs.py`: writes `breaker-runs.json`, all 32 combinations of the checks under each of three incidents, for
  the post's lab.
- `tests/`: the timeout findings, the pinned defaults, and the simulation's claims, including that the lab's file
  is the simulation's own output.

## What it doesn't show

- **The rates are invented.** Every failure share in `sim.py` was chosen to show a mechanism. Nothing here says how
  often any provider fails, or how.
- **Billing for an aborted stream.** No provider documents whether tokens generated before a client disconnects are
  billed. The simulation assumes they are.
- The fake server is not the API. It reproduces the SDK's view of a slow answer, which is what the timeout findings
  are about; it can't show what a real provider does under load.
