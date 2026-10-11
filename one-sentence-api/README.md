# The one-sentence test: example code

From the post [If you can't explain it in one sentence, it doesn't belong in your API](https://mainlog.dev/posts/one-sentence-api/).

Reconstructed, generic versions of two design proposals and their rewrites. `mf` is a
tiny stand-in framework so the rewrite can run; it prints the graph and fakes one model
turn. It is not a media stack.

| File | What it shows |
|---|---|
| `proposals/voice_agent.py` | The voice-agent proposal: new base class, `self` inside module-level tools, a lifecycle callback, imports from a package (`mf_all`) that doesn't exist |
| `proposals/worker.py` | The worker proposal: scaling turns the app into a `JobContext` function started by `Worker(WorkerOptions(...))` |
| `proposals/tool_as_proposed.py` | The proposal's tool on its own, so you can watch it fail |
| `rewrites/voice_agent.py` | The rewrite: plain `Session`, explicit `ctx`, greeting as configuration |
| `rewrites/voice_agent_with_config.py` | The rewrite with provider settings and the Opus steps kept in the code (36 lines), for a fair comparison. It runs against the stand-in too |
| `rewrites/app.toml` | The worker rewrite's configuration. The deploy tooling that would read it is part of the review's design; it isn't included |
| `count_lines.py` | Counts non-blank, non-comment lines |

## Run it

Recorded with Python 3.12.12 on macOS. Code is formatted with black 25.9.0, which the line counts assume. Run from this directory.

```sh
# The proposal's tool imports fine...
python -c "import sys; sys.path.insert(0, 'proposals'); import tool_as_proposed"

# ...and fails when the model calls it.
python -c "import sys, asyncio; sys.path.insert(0, 'proposals'); import tool_as_proposed; asyncio.run(tool_as_proposed.transfer_call('+15550100'))"
# NameError: name 'self' is not defined

# A linter sees it without running anything (pyflakes 3.4.0):
python -m pyflakes proposals/tool_as_proposed.py
# proposals/tool_as_proposed.py:9:11: undefined name 'self'

# The rewrite runs against the stand-in framework.
SIP_TRUNK=my-trunk CALLEE=+15551234567 PYTHONPATH=. python rewrites/voice_agent.py

# Line counts used in the article.
python count_lines.py proposals/worker.py rewrites/voice_agent.py rewrites/voice_agent_with_config.py
```
