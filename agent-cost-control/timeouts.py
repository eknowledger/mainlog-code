"""Does the SDK's timeout bound a slow answer? The real anthropic SDK against fake_server.py, four runs.

    python timeouts.py

1. A stream that sends one token every 1.5 s, with timeout=3.0: every gap is under 3 s, so it never times out.
2. The same JSON answer, not streamed, its bytes 1.5 s apart: the same.
3. A stream that stalls for 4 s after three tokens: now a read takes longer than 3 s. Which exception arrives?
4. The fix: a deadline on the whole call, from outside the SDK, which stops the stream at 5 s however it trickles.
5. A server that sends nothing at all, with the SDK's default two retries: the timeout now works, three times over.
"""
from __future__ import annotations

import asyncio
import time

import anthropic

from fake_server import Slow

TIMEOUT = 3.0
ASK = dict(model="m", max_tokens=100, messages=[{"role": "user", "content": "hi"}])


def streamed(gap, stall_after=None, stall=0.0):
    with Slow(gap=gap, stall_after=stall_after, stall=stall) as url:
        client = anthropic.Anthropic(api_key="x", base_url=url, timeout=TIMEOUT, max_retries=0)
        t0 = time.monotonic()
        try:
            with client.messages.stream(**ASK) as s:
                text = "".join(s.text_stream)
            return f"completed in {time.monotonic() - t0:.1f}s, no timeout ({len(text.split())} tokens)"
        except anthropic.APITimeoutError:
            return f"anthropic.APITimeoutError after {time.monotonic() - t0:.1f}s"
        except Exception as e:  # noqa: BLE001 - which class escapes is the finding
            kind = f"{type(e).__module__.split('.')[0]}.{type(e).__name__}"
            return (f"{kind} after {time.monotonic() - t0:.1f}s; an anthropic.APIError? "
                    f"{isinstance(e, anthropic.APIError)}; an anthropic.APITimeoutError? "
                    f"{isinstance(e, anthropic.APITimeoutError)}")


def not_streamed(gap):
    with Slow(gap=gap) as url:
        client = anthropic.Anthropic(api_key="x", base_url=url, timeout=TIMEOUT, max_retries=0)
        t0 = time.monotonic()
        try:
            client.messages.create(**ASK)
            return f"completed in {time.monotonic() - t0:.1f}s, no timeout"
        except anthropic.APITimeoutError:
            return f"anthropic.APITimeoutError after {time.monotonic() - t0:.1f}s"


def silent(retries):
    slow = Slow(silent=TIMEOUT + 1.0)
    with slow as url:
        client = anthropic.Anthropic(api_key="x", base_url=url, timeout=TIMEOUT, max_retries=retries)
        t0 = time.monotonic()
        try:
            client.messages.create(**ASK)
            return "answered"
        except anthropic.APITimeoutError:
            return (f"anthropic.APITimeoutError after {time.monotonic() - t0:.1f}s, {slow.attempts} attempts "
                    f"({retries} retries)")


async def with_deadline(gap, deadline):
    with Slow(gap=gap) as url:
        client = anthropic.AsyncAnthropic(api_key="x", base_url=url, timeout=TIMEOUT, max_retries=0)
        t0, got = time.monotonic(), 0
        try:
            async with asyncio.timeout(deadline):
                async with client.messages.stream(**ASK) as s:
                    async for _ in s.text_stream:
                        got += 1
            return f"completed in {time.monotonic() - t0:.1f}s"
        except TimeoutError:
            return f"deadline {deadline:g}s: stopped after {time.monotonic() - t0:.1f}s with {got} of 12 tokens"


if __name__ == "__main__":
    print(f"1. stream, a token every 1.5s, timeout={TIMEOUT}s: {streamed(1.5)}")
    print(f"2. not streamed, 20 bytes every 1.5s, timeout={TIMEOUT}s: {not_streamed(1.5)}")
    print(f"3. stream, a 4s stall after 3 tokens, timeout={TIMEOUT}s: {streamed(1.5, stall_after=3, stall=4.0)}")
    print(f"4. stream, a token every 1.5s, timeout={TIMEOUT}s: {asyncio.run(with_deadline(1.5, 5.0))}")
    print(f"5. no response at all, timeout={TIMEOUT}s: {silent(2)}")
