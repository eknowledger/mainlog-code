"""The counting rules, each on a small repository built commit by commit, so the expected numbers are known by hand."""

from __future__ import annotations

import difflib
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cochange  # noqa: E402

BODY = """\
    total = 0
    for item in items:
        if item.ok:
            total += item.size
        else:
            total -= 1
    return total
"""


def fn(name: str, edit: str = "") -> str:
    """A function of eight lines. An edit changes a string inside the body, so it lands inside the traced range."""
    return f"def {name}(items):\n    label = {edit!r}\n{BODY}"


class Repo:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.ts = 1_700_000_000
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict | None = None) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True, env=env
        ).stdout

    def write(self, path: str, text: str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, message: str) -> str:
        self.ts += 3600  # an hour apart, so commit times are strictly ordered
        date = f"@{self.ts} +0000"
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        }
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", message, env=env)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> Repo:
    return Repo(tmp_path)


def by_key(r: Repo) -> dict[str, cochange.Function]:
    return {f.key: f for f in cochange.functions(r.root, "pkg")}


def test_shape_renames_names_but_keeps_keywords_and_operators():
    assert cochange.shape("return a + b") == ["return", "ID", "+", "ID"]
    assert cochange.shape('x = "s" * 2') == ["ID", "=", "STR", "*", "NUM"]
    assert cochange.shape("for url in urls: get(url)") == cochange.shape("for link in links: head(link)")


def test_similarity_is_difflibs_ratio_without_autojunk():
    a = ["ID", "(", ")"] * 100
    b = ["ID", "(", ")"] * 99 + ["ID", "[", "]"]
    assert cochange.similarity(a, b) == difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    assert cochange.similarity(a, a) == 1.0


def test_qualified_names_tell_methods_of_different_classes_apart(repo: Repo):
    body = "\n".join("    " + line for line in fn("get").splitlines())
    repo.write("pkg/m.py", f"class Client:\n{body}\n\nclass AsyncClient:\n{body}\n")
    repo.commit("two classes")
    assert {"pkg/m.py:Client.get", "pkg/m.py:AsyncClient.get"} <= set(by_key(repo))


def test_counts_together_and_one_sided_only_after_both_copies_exist(repo: Repo):
    repo.write("pkg/a.py", fn("first"))
    repo.commit("first copy")
    repo.write("pkg/a.py", fn("first", "changed while alone"))
    repo.commit("a change with only one copy")  # before the second existed: must not count
    repo.write("pkg/b.py", fn("second"))
    repo.commit("second copy")  # the birth of the younger copy: not a change either
    repo.write("pkg/a.py", fn("first", "both 1"))
    repo.write("pkg/b.py", fn("second", "both 1"))
    repo.commit("both")
    repo.write("pkg/a.py", fn("first", "only a"))
    repo.commit("only the first")
    repo.write("pkg/b.py", fn("second", "only b"))
    repo.commit("only the second")
    repo.write("pkg/b.py", fn("second", "only b again"))
    repo.commit("only the second, again")

    fns = by_key(repo)
    git = cochange.Git(repo.root, sweep=20)
    p = cochange.count_pair(git, fns["pkg/a.py:first"], fns["pkg/b.py:second"])
    assert p.since.subject == "second copy"
    assert [c.subject for c in p.together] == ["both"]
    assert [c.subject for c in p.only_first] == ["only the first"]
    assert [c.subject for c in p.only_second] == ["only the second", "only the second, again"]
    assert p.before == 3  # first copy, the lone change, the second copy's birth


def test_sweeping_commits_are_set_apart(repo: Repo):
    repo.write("pkg/a.py", fn("first"))
    repo.write("pkg/b.py", fn("second"))
    repo.commit("both copies")
    repo.write("pkg/a.py", fn("first", "reformatted"))
    repo.write("pkg/b.py", fn("second", "reformatted"))
    for i in range(20):
        repo.write(f"other/f{i}.txt", "reformatted\n")
    repo.commit("reformat 22 files")
    fns = by_key(repo)
    p = cochange.count_pair(cochange.Git(repo.root, sweep=20), fns["pkg/a.py:first"], fns["pkg/b.py:second"])
    assert p.together == [] and [c.subject for c in p.swept] == ["reformat 22 files"]
    p = cochange.count_pair(cochange.Git(repo.root, sweep=30), fns["pkg/a.py:first"], fns["pkg/b.py:second"])
    assert [c.subject for c in p.together] == ["reformat 22 files"]


def test_history_stops_where_the_function_did_not_exist(repo: Repo):
    # The same lines first belonged to another function. `git log -L` follows them back anyway; the count must not.
    repo.write("pkg/a.py", fn("older_name"))
    repo.commit("lines born as another function")
    repo.write("pkg/a.py", fn("older_name", "edited under the old name"))
    repo.commit("edit under the old name")
    repo.write("pkg/a.py", fn("current"))
    repo.commit("rename")
    repo.write("pkg/a.py", fn("current", "edited"))
    repo.commit("edit under the current name")
    git = cochange.Git(repo.root, sweep=20)
    traced = git.run("log", "--format=%s", "-L", "1,8:pkg/a.py").split("\n")
    assert "lines born as another function" in traced  # -L really does go back that far
    history = git.history(by_key(repo)["pkg/a.py:current"])
    assert [c.subject for c in sorted(history.values(), key=lambda c: c.ts)] == ["rename", "edit under the current name"]
    cut = git.traced(by_key(repo)["pkg/a.py:current"]) - set(history)
    assert len(cut) == 2  # what the pair report prints as "traced other code"


def test_clone_groups_count_each_change_once(repo: Repo):
    for name in ("a", "b", "c"):
        repo.write(f"pkg/{name}.py", fn(name))
    repo.commit("three copies")
    for name in ("a", "b", "c"):
        repo.write(f"pkg/{name}.py", fn(name, "    # all"))
    repo.commit("all three")
    repo.write("pkg/a.py", fn("a", "two of three"))
    repo.write("pkg/b.py", fn("b", "two of three"))
    repo.commit("two of three")

    fns = list(by_key(repo).values())
    pairs = cochange.near_duplicates(fns, 0.85)
    assert len(pairs) == 3
    groups = cochange.clone_groups(pairs)
    assert [len(g) for g in groups] == [3]
    changes, swept = cochange.count_group(cochange.Git(repo.root, 20), groups[0])
    assert [(c.commit.subject, c.consistent) for c in changes] == [("all three", True), ("two of three", False)]
    assert swept == 0
