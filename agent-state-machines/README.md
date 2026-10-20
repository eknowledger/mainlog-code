# agent-state-machines: what a state machine buys an agent

The companion code for [What a state machine buys an agent: fewer model calls and a table you can
test](https://mainlog.dev/posts/agent-state-machines/). One dinner booking, seven scripted steps, sent to a real model
in three designs, plus the state machine's full table of states and events.

    python demo.py            # the recorded run's totals, the call counts, the table, the behaviour space
    python demo.py --steps    # every recorded call
    python demo.py --table    # all 119 cells of the table and the decision in each
    python -m pip install pytest==8.3.5 && python -m pytest -q tests

`demo.py`, `export_runs.py` and the tests need only the standard library. `live.py` is the one script that calls a
model, and the only one that needs `requirements.txt` and an API key.

## What's here

- `booking.py`: the booking as data. The eight tools, the free-form system prompt, the seven states with their
  instructions and allowed tools, the scripted run, and `decide()`, the outcome of every state against every event.
- `live.py`: sends every design's request at every step to Claude and records the usage the API
  reports (uncached input, cache writes, cache reads, output) and the wall time of each call, in `live-run.json`.
  The steps are scripted, so each request carries the same history in every design and the model's choices don't
  change what is sent next. Each run tags its system prompt so it can't read another run's cache entry.
- `live-run.json`: the run quoted in the post, 66 calls: Claude Opus 5.5 twice and Claude Sonnet 5 once, anthropic
  1.13.0. `demo-output.txt` and `steps-output.txt` are saved outputs of `demo.py`.
- `demo.py`: totals per design at Anthropic's published multipliers: a cache write 1.25x an input token, a read
  0.05x on Opus 5.5 and 0.1x on Sonnet 5, and an output token 5x on both. It prints input cost and the total with
  output separately. Other platforms price separately: read the totals as ratios at Anthropic's multipliers.
- `export_runs.py`: writes `sm-runs.json`, the recorded run in the shape the post's lab replays.
- `tests/`: every one of the 119 cells has a declared outcome; the cross-cutting events are in no drawn transition;
  a late reservation, payment or text is never ignored; the recorded run matches the requests `booking.py` builds.

## What it doesn't show

- The model's choices don't drive the run. That keeps the comparison to the design alone; it also means nothing here
  measures whether one design makes a model choose better.
- One recorded run. The token counts are the API's own and repeat exactly; the times are wall clock from
  this machine and vary from run to run.
- The history is a text transcript in one user message, the same in every design, and isn't cached. A real agent's
  history grows faster and its caching matters more.
