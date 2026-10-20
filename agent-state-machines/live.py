"""A small live run: every design's requests sent to Claude, to record real token counts and time.

The steps are still scripted (booking.RUN): each request carries the same history in every design, so the counts
differ only by design. What is live is the model and its tokenizer, the hidden tool-use prompt the API adds, prompt
caching as the service applies it, and the time each call takes. Writes live-run.json, which demo.py and
export_runs.py read; CI never calls a model and checks the recorded file instead.

    ANTHROPIC_API_KEY=... .venv/bin/python live.py      # about 66 calls, well under a dollar

On Amazon Bedrock instead, set CLAUDE_ON_BEDROCK=1 with your usual AWS credentials and region, and MODEL_PREFIX to
your account's model ID prefix if it needs one.

Each (model, design, repetition) gets its own tag at the start of the system prompt, so one run can never read a
cache entry another one wrote: the free-form and fixed-tools designs share a tool list, and a cache entry lasts five
minutes.
"""
from __future__ import annotations

import os
import json
import sys
import time

import anthropic

import booking

MODELS = [("claude-opus-5-5", 2), ("claude-sonnet-5", 1)]   # (model, repetitions)
PREFIX = os.environ.get("MODEL_PREFIX", "")
DESIGNS = ["free", "states", "fixed"]


def call(client, model, req, tag):
    kwargs = dict(model=PREFIX + model, max_tokens=4000,
                  system=[{"type": "text", "text": f"[{tag}] {req['system']}", "cache_control": {"type": "ephemeral"}}],
                  messages=[{"role": "user", "content": req["user"]}])
    if req["tools"]:
        kwargs["tools"] = req["tools"]
    t0 = time.monotonic()
    r = client.messages.create(**kwargs)
    seconds = time.monotonic() - t0
    picked = [b.name for b in r.content if b.type == "tool_use"]
    u = r.usage
    return {"seconds": round(seconds, 3), "input": u.input_tokens, "cache_write": u.cache_creation_input_tokens or 0,
            "cache_read": u.cache_read_input_tokens or 0, "output": u.output_tokens, "stop": r.stop_reason,
            "tools_called": picked, "ntools": len(req["tools"])}


def main():
    bedrock = os.environ.get("CLAUDE_ON_BEDROCK") == "1"
    client = anthropic.AnthropicBedrock(max_retries=0) if bedrock else anthropic.Anthropic(max_retries=0)
    out = {"sdk": anthropic.__version__, "calls": []}
    for model, reps in MODELS:
        for rep in range(1, reps + 1):
            for design in DESIGNS:
                tag = f"{design}-{rep}"
                steps = [(i + 1, booking.RUN[i][0], booking.request(design, i)) for i in range(len(booking.RUN))]
                if design == "free":
                    steps.append((8, "final", booking.final_request()))
                for step, state, req in steps:
                    rec = call(client, model, req, tag)
                    allowed = booking.STATES[state][1] if state in booking.STATES and design != "free" else None
                    rec.update(model=model, rep=rep, design=design, step=step, state=state,
                               expected=booking.RUN[step - 1][1] if step <= len(booking.RUN) else None,
                               outside_allowed=[t for t in rec["tools_called"] if allowed is not None and t not in allowed])
                    out["calls"].append(rec)
                    print(model, design, rep, step, state, rec["input"], rec["cache_write"], rec["cache_read"],
                          rec["output"], rec["seconds"], rec["tools_called"], file=sys.stderr)
    with open("live-run.json", "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(len(out["calls"]), "calls recorded in live-run.json")


if __name__ == "__main__":
    main()
