"""The budget nobody chose: the limits an agent inherits from its SDK and framework, read from the installed code.

    python defaults.py

Every value is imported or read from the pinned packages in requirements.txt, not typed in, so a version bump that
changes a default changes this output and fails CI.
"""
from __future__ import annotations

import inspect
import re
from importlib.metadata import version

import anthropic
import anthropic._base_client as abase
import anthropic._constants as ac
import openai._constants as oc
from agents import ModelSettings, run_config
from langgraph._internal import _config as lgc


def retry_after_cap() -> float:
    """The longest retry-after the SDK will sleep for: the number in its own min() call."""
    src = inspect.getsource(abase.BaseClient._calculate_retry_timeout)
    return float(re.search(r"min\(retry_after, ([\d_.]+)\)", src).group(1).replace("_", ""))


def backoff(retries: int) -> list[float]:
    """The SDK's waits between attempts, before jitter (which shortens each by up to a quarter)."""
    return [min(ac.INITIAL_RETRY_DELAY * 2.0 ** n, ac.MAX_RETRY_DELAY) for n in range(retries)]


if __name__ == "__main__":
    t = ac.DEFAULT_TIMEOUT
    print(f"anthropic {version('anthropic')}: timeout read={t.read:g}s connect={t.connect:g}s, "
          f"max_retries={ac.DEFAULT_MAX_RETRIES}, backoff {ac.INITIAL_RETRY_DELAY:g}s doubling to {ac.MAX_RETRY_DELAY:g}s, "
          f"retry-after obeyed up to {retry_after_cap():,.0f}s")
    print(f"  waits between its {ac.DEFAULT_MAX_RETRIES} retries: {backoff(ac.DEFAULT_MAX_RETRIES)} s, before jitter")
    o = oc.DEFAULT_TIMEOUT
    print(f"openai {version('openai')}: timeout read={o.read:g}s connect={o.connect:g}s, max_retries={oc.DEFAULT_MAX_RETRIES}")
    print(f"openai-agents {version('openai-agents')}: max_turns={run_config.DEFAULT_MAX_TURNS}, "
          f"ModelSettings().timeout={ModelSettings().timeout}")
    print(f"langgraph {version('langgraph')}: recursion_limit={lgc.DEFAULT_RECURSION_LIMIT}")
    # A stream can't be stopped by a per-read timeout as long as every gap is shorter than it. The longest a single
    # call can then take is one gap short of the timeout per token, for every token it is allowed to write.
    per_call = ac.DEFAULT_MAX_RETRIES + 1
    print(f"worst case, one call that answers at all, max_tokens=4096: up to 4096 x {t.read:g}s between tokens; "
          f"one that never answers: {per_call} attempts x {t.read:g}s + {sum(backoff(ac.DEFAULT_MAX_RETRIES)):g}s "
          f"= {per_call * t.read + sum(backoff(ac.DEFAULT_MAX_RETRIES)):,.1f}s")
    print(f"  x {run_config.DEFAULT_MAX_TURNS} turns, openai-agents' default limit: "
          f"{run_config.DEFAULT_MAX_TURNS * (per_call * t.read + sum(backoff(ac.DEFAULT_MAX_RETRIES))) / 3600:.1f} hours "
          f"before anything gives up")
