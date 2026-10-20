"""Find near-duplicate functions in a repository, then ask git whether each pair has been changing together.

    python3 cochange.py REPO SRC_DIR                     # every near-duplicate pair, and their clone groups
    python3 cochange.py REPO SRC_DIR --pair A B          # one pair, commit by commit
    python3 cochange.py REPO SRC_DIR --json out.json     # everything, for figures and checks

A and B name a function as FILE:QUALNAME, for example httpx/_client.py:Client._send_single_request.

Two functions are near-duplicates when their token streams, with every identifier, string and number replaced by a
placeholder, are at least --threshold alike by difflib's ratio. Their history comes from `git log -L`, which follows
a range of lines back through every commit that changed it.

Three rules decide what counts, and each is there because a count without it was wrong:

  * Only history from after both copies existed. `git log -L` happily follows a range back past the moment it became
    a copy, and a change made when there was only one copy is not a one-sided change.
  * Not sweeping commits: a commit touching more than --sweep files (formatting, a rename, a licence header) is
    counted apart, because it touches both copies for reasons that say nothing about whether they belong together.
  * Each pair once, and each clone group once. A group of five copies has ten pairs, and summing pairs counts every
    group change ten times over, which is what made the first version's repo-wide percentages meaningless.

Python 3.12+, standard library only, and `git` on the PATH.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import itertools
import json
import keyword
import pathlib
import re
import subprocess
import sys
import textwrap
import tokenize
import warnings
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------------------------------------------------
# What two functions look like


def shape(src: str) -> list[str]:
    """The token stream with names, strings and numbers replaced, so `get(url)` and `head(link)` look the same."""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NAME:
            out.append(tok.string if keyword.iskeyword(tok.string) else "ID")
        elif tok.type == tokenize.STRING:
            out.append("STR")
        elif tok.type == tokenize.NUMBER:
            out.append("NUM")
        elif tok.type == tokenize.OP:
            out.append(tok.string)
    return out


def similarity(a: list[str], b: list[str]) -> float:
    """difflib's ratio, 2M/T. autojunk is off: it would ignore common tokens such as `(` in anything over 200 long."""
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass
class Function:
    file: str
    qualname: str
    start: int
    end: int
    tokens: list[str] = field(repr=False)

    @property
    def key(self) -> str:
        return f"{self.file}:{self.qualname}"

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


def _defs(tree: ast.AST):
    """Every function and method, with its qualified name: `Client.get` and `AsyncClient.get` are different functions."""
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    yield name, child
                stack.append((child, f"{name}."))
            else:
                stack.append((child, prefix))


def functions(repo: pathlib.Path, src: str, min_lines: int = 6) -> list[Function]:
    """Functions whose body, after any docstring, is at least `min_lines` long."""
    found = []
    for path in sorted((repo / src).rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for qualname, node in _defs(tree):
            body = node.body
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                if len(body) == 1:
                    continue  # a docstring and nothing else
                first = body[1]
            if node.end_lineno - first.lineno + 1 < min_lines:
                continue
            source = textwrap.dedent("\n".join(lines[first.lineno - 1 : node.end_lineno]))
            try:
                tokens = shape(source)
            except (tokenize.TokenError, IndentationError, SyntaxError):
                continue
            # The range git follows is the whole def, decorators and signature included: a change to the signature
            # is a change to the function.
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            found.append(Function(str(path.relative_to(repo)), qualname, start, node.end_lineno, tokens))
    return found


def near_duplicates(fns: list[Function], threshold: float) -> list[tuple[float, Function, Function]]:
    pairs = []
    for x, y in itertools.combinations(fns, 2):
        if x.file == y.file and x.start <= y.end and y.start <= x.end:
            continue  # one is nested in the other: a function and its own inner function are not two copies
        lx, ly = len(x.tokens), len(y.tokens)
        if min(lx, ly) / max(lx, ly) < threshold:
            continue  # ratio can't reach the threshold: it is at most 2*min/(lx+ly)
        r = similarity(x.tokens, y.tokens)
        if r >= threshold:
            pairs.append((r, x, y))
    pairs.sort(key=lambda p: (-p[0], p[1].key, p[2].key))
    return pairs


# ---------------------------------------------------------------------------------------------------------------------
# What git says about them


@dataclass(frozen=True)
class Commit:
    sha: str
    ts: int
    date: str
    subject: str


class Git:
    def __init__(self, repo: pathlib.Path, sweep: int):
        self.repo = repo
        self.sweep = sweep
        self._files: dict[str, int] = {}
        self._history: dict[str, dict[str, Commit]] = {}
        self._traced: dict[str, set[str]] = {}
        self._spans: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}

    def run(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True).stdout

    def history(self, f: Function) -> dict[str, Commit]:
        """Every commit `git log -L` says changed this function's lines while they were this function, keyed by SHA.

        `git log -L` follows a range of lines, not a function. Walk far enough back and the same lines belonged to
        something else: httpx's sync `Client._send_single_request` traces back into 2019, through months when httpx
        had no sync client and the lines were the async client's. So the history stops at the first commit, going
        back, where the traced lines are not mostly inside a function of this qualified name in that commit's version
        of the file. Renamed files are followed, because `-L` follows them.
        """
        if f.key not in self._history:
            out = self.run("log", "--format=%x00%H %ct %cs %s", "-L", f"{f.start},{f.end}:{f.file}")
            commits = {}
            blocks = out.split("\0")[1:]
            self._traced[f.key] = {b.split(" ", 1)[0] for b in blocks}
            for block in blocks:  # newest first
                header, _, patch = block.partition("\n")
                sha, ts, date, *subject = header.split(" ", 3)
                path = next((m.group(1) for m in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.M)), f.file)
                hunk = re.search(r"^@@@? (?:-\d+(?:,\d+)? )+\+(\d+)(?:,(\d+))? @@", patch, re.M)  # @@@ for a merge
                if hunk is None:
                    continue  # a merge -L lists without a diff of its own: nothing changed here
                lo = int(hunk.group(1))
                hi = lo + int(hunk.group(2) or 1) - 1
                span = self.spans(sha, path).get(f.qualname)
                if span is None or min(hi, span[1]) - max(lo, span[0]) + 1 < (hi - lo + 1) / 2:
                    break
                commits[sha] = Commit(sha, int(ts), date, subject[0] if subject else "")
            self._history[f.key] = commits
        return self._history[f.key]

    def spans(self, sha: str, path: str) -> dict[str, tuple[int, int]]:
        """Each function's first and last line in this commit's version of the file, decorators included."""
        key = (sha, path)
        if key not in self._spans:
            try:
                text = self.run("show", f"{sha}:{path}")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)  # old files with "\s" in plain strings
                    tree = ast.parse(text)
                self._spans[key] = {
                    name: (min([n.lineno, *(d.lineno for d in n.decorator_list)]), n.end_lineno) for name, n in _defs(tree)
                }
            except (subprocess.CalledProcessError, SyntaxError, ValueError):
                self._spans[key] = {}
        return self._spans[key]

    def traced(self, f: Function) -> set[str]:
        """Every commit `git log -L` listed for the function's lines, before the history was cut where it stopped."""
        self.history(f)
        return self._traced[f.key]

    def files_touched(self, sha: str) -> int:
        if sha not in self._files:
            self._files[sha] = len(self.run("show", "--format=", "--name-only", sha).split())
        return self._files[sha]

    def sweeping(self, sha: str) -> bool:
        return self.files_touched(sha) > self.sweep


@dataclass
class PairCount:
    """After both copies existed: commits that changed both, only the first, only the second, and sweeping ones."""

    since: Commit | None  # the commit that created the younger copy, as far as `git log -L` can tell
    together: list[Commit]
    only_first: list[Commit]
    only_second: list[Commit]
    swept: list[Commit]
    before: int  # commits from before both existed, left out

    @property
    def one_sided(self) -> int:
        return len(self.only_first) + len(self.only_second)


def count_pair(git: Git, x: Function, y: Function) -> PairCount:
    hx, hy = git.history(x), git.history(y)
    if not hx or not hy:
        # Nothing git could attribute to one of them under its current name: no history to compare.
        return PairCount(since=None, together=[], only_first=[], only_second=[], swept=[], before=len(hx) + len(hy))
    birth_x = min(hx.values(), key=lambda c: c.ts)
    birth_y = min(hy.values(), key=lambda c: c.ts)
    since = max(birth_x, birth_y, key=lambda c: c.ts)
    after = lambda h: {s for s, c in h.items() if c.ts > since.ts}  # noqa: E731 - strictly after: the birth is not a change
    ax, ay = after(hx), after(hy)
    every = {**hx, **hy}
    swept = sorted((every[s] for s in ax | ay if git.sweeping(s)), key=lambda c: c.ts)
    sw = {c.sha for c in swept}
    ax, ay = ax - sw, ay - sw
    by_time = lambda shas: sorted((every[s] for s in shas), key=lambda c: c.ts)  # noqa: E731
    return PairCount(
        since=since,
        together=by_time(ax & ay),
        only_first=by_time(ax - ay),
        only_second=by_time(ay - ax),
        swept=swept,
        before=len((set(hx) | set(hy)) - ax - ay - sw),
    )


# ---------------------------------------------------------------------------------------------------------------------
# Clone groups


def clone_groups(pairs: list[tuple[float, Function, Function]]) -> list[list[Function]]:
    """Connected components of the near-duplicate graph: copies of copies are one group, counted once."""
    parent: dict[str, str] = {}
    by_key: dict[str, Function] = {}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for _, x, y in pairs:
        for f in (x, y):
            by_key.setdefault(f.key, f)
            parent.setdefault(f.key, f.key)
        parent[find(x.key)] = find(y.key)
    groups: dict[str, list[Function]] = {}
    for k in by_key:
        groups.setdefault(find(k), []).append(by_key[k])
    return sorted((sorted(g, key=lambda f: f.key) for g in groups.values()), key=lambda g: (-len(g), g[0].key))


@dataclass
class GroupChange:
    commit: Commit
    existing: list[str]  # members that existed before this commit
    touched: list[str]  # of those, the ones it changed

    @property
    def consistent(self) -> bool:
        return self.touched == self.existing


def count_group(git: Git, members: list[Function]) -> tuple[list[GroupChange], int]:
    """Every non-sweeping commit that changed a member while at least two members existed. Also: how many were swept."""
    hist = {m.key: h for m in members if (h := git.history(m))}  # a member with no history can't be counted
    birth = {k: min(c.ts for c in h.values()) for k, h in hist.items()}
    every: dict[str, Commit] = {}
    for h in hist.values():
        every.update(h)
    changes, swept = [], 0
    for c in sorted(every.values(), key=lambda c: c.ts):
        existing = sorted(k for k in hist if birth[k] < c.ts)
        touched = [k for k in existing if c.sha in hist[k]]
        if len(existing) < 2 or not touched:
            continue
        if git.sweeping(c.sha):
            swept += 1
            continue
        changes.append(GroupChange(c, existing, touched))
    return changes, swept


# ---------------------------------------------------------------------------------------------------------------------
# Output


def _commit_line(c: Commit) -> str:
    return f"{c.sha[:7]} {c.date} {c.subject}"


def report_pair(git: Git, x: Function, y: Function, r: float) -> dict:
    p = count_pair(git, x, y)
    print(f"{x.key}  ~  {y.key}")
    print(f"  {r:.1%} alike, {x.lines} and {y.lines} lines")
    cut = (git.traced(x) | git.traced(y)) - set(git.history(x)) - set(git.history(y))
    print(f"  history stops where each copy got its name: {_n(len(cut), 'earlier commit')} traced other code")
    print(f"  both exist from {_commit_line(p.since)}" if p.since else "  no history git can attribute to both")
    print(f"  after that: together {len(p.together)}, only first {len(p.only_first)}, only second {len(p.only_second)}")
    print(f"  left out: {_n(p.before, 'commit')} from before both existed, {len(p.swept)} sweeping (over {git.sweep} files)")
    for label, cs in (("together", p.together), ("only first", p.only_first), ("only second", p.only_second)):
        for c in cs:
            print(f"    {label:<11} {_commit_line(c)}")
    return {
        "first": x.key,
        "second": y.key,
        "similarity": round(r, 4),
        "since": _commit_json(p.since) if p.since else None,
        "together": [_commit_json(c) for c in p.together],
        "only_first": [_commit_json(c) for c in p.only_first],
        "only_second": [_commit_json(c) for c in p.only_second],
        "swept": [_commit_json(c) for c in p.swept],
        "before": p.before,
    }


def _n(k: int, noun: str) -> str:
    return f"{k} {noun}" + ("" if k == 1 else "s")


def _commit_json(c: Commit) -> dict:
    return {"sha": c.sha, "ts": c.ts, "date": c.date, "subject": c.subject}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=pathlib.Path)
    ap.add_argument("src", help="the directory to scan, relative to REPO")
    ap.add_argument("--min-lines", type=int, default=6, help="shortest function body to consider (default 6)")
    ap.add_argument("--threshold", type=float, default=0.85, help="how alike two functions must be (default 0.85)")
    ap.add_argument("--sweep", type=int, default=20, help="a commit touching more files than this is sweeping (default 20)")
    ap.add_argument("--pair", nargs=2, metavar=("A", "B"), help="report one pair, commit by commit: FILE:QUALNAME twice")
    ap.add_argument("--json", type=pathlib.Path, help="also write everything as JSON")
    a = ap.parse_args(argv)

    git = Git(a.repo, a.sweep)
    fns = functions(a.repo, a.src, a.min_lines)
    head = git.run("rev-parse", "--short=7", "HEAD").strip()

    if a.pair:
        by_key = {f.key: f for f in fns}
        missing = [k for k in a.pair if k not in by_key]
        if missing:
            print(f"not found (or shorter than {a.min_lines} lines): {', '.join(missing)}", file=sys.stderr)
            return 2
        x, y = by_key[a.pair[0]], by_key[a.pair[1]]
        print(f"{a.repo.name} at {head}")
        out = report_pair(git, x, y, similarity(x.tokens, y.tokens))
        if a.json:
            a.json.write_text(json.dumps(out, indent=1) + "\n")
        return 0

    pairs = near_duplicates(fns, a.threshold)
    groups = clone_groups(pairs)
    print(f"{a.repo.name} at {head}: {len(fns)} functions of {a.min_lines}+ lines under {a.src}/")
    print(f"  {len(pairs)} near-duplicate pairs (>= {a.threshold:.0%} alike), in {len(groups)} clone groups")

    print("\npairs: together / only first / only second, after both existed, sweeping commits left out")
    pair_rows = []
    for r, x, y in pairs:
        p = count_pair(git, x, y)
        print(f"  {r:.2f}  {x.key}  ~  {y.key}   {len(p.together)} / {len(p.only_first)} / {len(p.only_second)}")
        pair_rows.append(
            {
                "first": x.key,
                "second": y.key,
                "similarity": round(r, 4),
                "together": len(p.together),
                "only_first": len(p.only_first),
                "only_second": len(p.only_second),
            }
        )

    print("\nclone groups: changes to every existing member / to only some of them")
    group_rows = []
    totals = [0, 0, 0]
    for i, g in enumerate(groups, 1):
        changes, swept = count_group(git, g)
        consistent = sum(c.consistent for c in changes)
        partial = len(changes) - consistent
        totals[0] += consistent
        totals[1] += partial
        totals[2] += swept
        print(f"  G{i}  {len(g)} members  consistent {consistent}, partial {partial}  ({swept} sweeping left out)")
        for m in g:
            print(f"        {m.key}")
        group_rows.append(
            {
                "id": f"G{i}",
                "members": [m.key for m in g],
                "swept": swept,
                "changes": [
                    {**_commit_json(c.commit), "existing": c.existing, "touched": c.touched, "consistent": c.consistent}
                    for c in changes
                ],
            }
        )
    changed = totals[0] + totals[1]
    share = f" ({totals[1] / changed:.0%} partial)" if changed else ""
    print(f"\n  all groups: {totals[0]} consistent, {totals[1]} partial{share}, {totals[2]} sweeping left out")

    if a.json:
        a.json.write_text(
            json.dumps(
                {"repo": a.repo.name, "head": head, "functions": len(fns), "pairs": pair_rows, "groups": group_rows},
                indent=1,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
