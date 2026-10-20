"""The browser tool's arithmetic, in Python, as the reference its fixtures are checked against.

    python3 tool_reference.py COPY_A COPY_B [HISTORY_A HISTORY_B] [--since SHA_OR_DATE] [--ignore WORDS]

The "Copy drift" tool on the site runs in the browser and cannot use Python's tokenize or difflib, so it ports
both. This file does the same sums with the real ones, so a fixture that agrees with it agrees with Python:
similarity over the body (or with --whole, the whole pasted function), dedented, names set aside, similarity as written, and the together / only A /
only B counts after the younger copy's first commit, or after the commit or date given with --since.

It differs from cochange.py in one way, because the tool sees only what is pasted: it cannot see how many files a
commit touched, so a sweeping commit has to be named in --ignore.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import io
import json
import keyword
import pathlib
import re
import textwrap
import tokenize

KEPT = {tokenize.NAME, tokenize.STRING, tokenize.NUMBER, tokenize.OP}


def tokens(src: str) -> list[tuple[str, str]]:
    """(shape, text) per token, as cochange.shape makes them: f-string literal text is not a token in Python 3.12+."""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(textwrap.dedent(src)).readline):
        if tok.type not in KEPT:
            continue
        if tok.type == tokenize.NAME:
            out.append((tok.string if keyword.iskeyword(tok.string) else "ID", tok.string))
        elif tok.type == tokenize.STRING:
            out.append(("STR", tok.string))
        elif tok.type == tokenize.NUMBER:
            out.append(("NUM", tok.string))
        else:
            out.append((tok.string, tok.string))
    return out


def body(ts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """What follows the signature and the docstring: the part cochange.py compares."""
    texts = [t for _, t in ts]
    if "def" not in texts:
        return ts
    depth = 0
    for i in range(texts.index("def") + 1, len(ts)):
        t = texts[i]
        if t in "([{":
            depth += 1
        elif t in ")]}":
            depth -= 1
        elif t == ":" and depth == 0:
            rest = ts[i + 1 :]
            return rest[1:] if len(rest) > 1 and rest[0][0] == "STR" else rest
    return ts


def ratio(a: list[str], b: list[str]) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


ONE_LINE = re.compile(r"^([0-9a-f]{7,40})\s+(\S+)\s+(.*)$")


def history(text: str) -> list[dict]:
    """The one-line shape only, `<sha> <ISO date> <subject>`: what the post tells readers to paste."""
    seen, out = set(), []
    for line in text.splitlines():
        m = ONE_LINE.match(line.strip())
        if not m:
            continue
        ts = dt.datetime.fromisoformat(m.group(2).replace("Z", "+00:00")).timestamp()
        if m.group(1)[:7] not in seen:
            seen.add(m.group(1)[:7])
            out.append({"sha": m.group(1), "ts": ts, "subject": m.group(3)})
    return out


def count(a: list[dict], b: list[dict], since: str, ignore: list[str]) -> dict:
    start = max(min(a, key=lambda c: c["ts"]), min(b, key=lambda c: c["ts"]), key=lambda c: c["ts"])["ts"]
    if since:
        hit = [c for c in a + b if c["sha"].startswith(since[:7])]
        start = hit[0]["ts"] if hit else dt.datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
    ka = {c["sha"][:7]: c for c in a}
    kb = {c["sha"][:7]: c for c in b}
    words = [w.strip().lower() for w in ignore if w.strip()]
    res = {"together": [], "only_a": [], "only_b": [], "ignored": [], "before": 0}
    for k, c in sorted({**kb, **ka}.items(), key=lambda kv: kv[1]["ts"]):
        if c["ts"] <= start:
            res["before"] += 1
        elif any(w in c["subject"].lower() or (re.fullmatch(r"[0-9a-f]{7,40}", w) and c["sha"].startswith(w[:7])) for w in words):
            res["ignored"].append(k)
        elif k in ka and k in kb:
            res["together"].append(k)
        elif k in ka:
            res["only_a"].append(k)
        else:
            res["only_b"].append(k)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=pathlib.Path)
    ap.add_argument("b", type=pathlib.Path)
    ap.add_argument("history", nargs="*", type=pathlib.Path)
    ap.add_argument("--since", default="")
    ap.add_argument("--ignore", default="")
    ap.add_argument("--whole", action="store_true", help="compare the whole function, signature and docstring too")
    o = ap.parse_args()
    ta, tb = tokens(o.a.read_text()), tokens(o.b.read_text())
    if not o.whole:
        ta, tb = body(ta), body(tb)
    out = {
        "tokens": [len(ta), len(tb)],
        "shape": ratio([s for s, _ in ta], [s for s, _ in tb]),
        "text": ratio([t for _, t in ta], [t for _, t in tb]),
    }
    if len(o.history) == 2:
        c = count(history(o.history[0].read_text()), history(o.history[1].read_text()), o.since, o.ignore.split(","))
        out |= {k: (len(v) if isinstance(v, list) else v) for k, v in c.items()}
        out["one_sided"] = c["only_a"] + c["only_b"]
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
