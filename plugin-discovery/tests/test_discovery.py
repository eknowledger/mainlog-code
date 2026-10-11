"""Each discovery outcome, with stand-in entry points: nothing is installed but textpipe.

Run with: pip install packaging pytest ./textpipe && pytest tests
"""

import importlib.metadata
import logging

import pytest

from textpipe import Casing, Filter
from textpipe.errors import AmbiguousPluginError, PluginCollisionError, PluginNotFoundError
from textpipe.registry import Registry
from textpipe.stricter import StricterRegistry
from textpipe.versions import parse_version_prefix


class FakePackage:
    def __init__(self, name, requires=None):
        self.name, self.requires = name, requires


class FakeEntryPoint:
    """Quacks like importlib.metadata.EntryPoint, and counts how often it's imported."""

    def __init__(self, name, target, package="plugin-pkg", requires=None):
        self.name, self._target, self.loads = name, target, 0
        self.dist = FakePackage(package, requires)

    def load(self):
        self.loads += 1
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def plugin(api="1.0", base=Filter):
    return type("P", (base,), {"api_version": api})


def registry(*eps, host="1.4", cls=Registry):
    return cls(loader=lambda group: list(eps), host_version=host)


@pytest.mark.parametrize("version, parsed", [
    ("1.4", (1, 4)), ("2.0rc1", (2, 0)), ("1.0.12345", (1, 0, 12345)),
    ("1.4.0", (1, 4, 0)), ("latest", ()), ("1.x.3", (1,)), (" 2 . 1", (2, 1)),
])
def test_parse_version_prefix(version, parsed):
    assert parse_version_prefix(version) == parsed


@pytest.mark.parametrize("declared, loads", [
    ("1.0", True), ("2.0rc1", False), ("1.0.12345", True), ("1.4.0", False),
])
def test_the_article_table_against_host_1_4(declared, loads):
    ep = FakeEntryPoint("p", plugin(api=declared))
    assert ("p" in registry(ep, host="1.4").available()) is loads


def test_a_non_string_api_version_counts_as_failed_to_load(caplog):
    with caplog.at_level(logging.WARNING):
        assert registry(FakeEntryPoint("p", plugin(api=2.0))).available() == {}
    assert "could not import plugin" in caplog.text


def test_incompatible_plugin_is_imported_before_it_is_skipped(caplog):
    ep = FakeEntryPoint("summarize", plugin(api="2.0"))
    with caplog.at_level(logging.WARNING):
        assert registry(ep).available() == {}
    assert ep.loads == 1                                  # its module ran
    assert "targets textpipe API 2.0" in caplog.text


def test_broken_import_is_skipped_not_raised(caplog):
    ep = FakeEntryPoint("translate", ModuleNotFoundError("No module named 'language_tables'"))
    with caplog.at_level(logging.WARNING):
        assert registry(ep).available() == {}
    assert "could not import plugin 'translate'" in caplog.text


def test_same_name_from_two_packages_raises_and_caches_nothing():
    reg = registry(FakeEntryPoint("shout", plugin(), package="textpipe-shout"),
                   FakeEntryPoint("shout", plugin(), package="textpipe-loud"))
    for _ in range(2):                                    # nothing cached: it raises again
        with pytest.raises(PluginCollisionError,
                           match=r"Two packages register 'shout'.*textpipe-loud .* and textpipe-shout"):
            reg.available()


def test_collision_with_a_skipped_plugin_is_not_reported():
    a = FakeEntryPoint("shout", plugin(), package="textpipe-shout")
    b = FakeEntryPoint("shout", plugin(api="2.0"), package="textpipe-loud")
    assert list(registry(a, b).available()) == ["shout"]


def test_1_4_0_is_newer_than_1_4_here_but_not_for_packaging():
    ep = FakeEntryPoint("shout", plugin(api="1.4.0"))
    assert registry(ep, host="1.4").available() == {}              # (1, 4, 0) > (1, 4)
    assert "shout" in registry(ep, host="1.4", cls=StricterRegistry).available()


def test_a_prerelease_counts_as_the_release_here():
    ep = FakeEntryPoint("shout", plugin(api="2.0rc1"))
    assert "shout" in registry(ep, host="2.0").available()        # (2, 0) == (2, 0)


def test_a_plugin_for_an_older_major_version_still_loads():
    ep = FakeEntryPoint("shout", plugin(api="1.0"))
    assert "shout" in registry(ep, host="2.0").available()
    assert registry(ep, host="2.0", cls=StricterRegistry).available() == {}


def test_two_plugins_of_one_kind_need_a_pin():
    reg = registry(FakeEntryPoint("shout", plugin(base=Casing), package="a"),
                   FakeEntryPoint("caps", plugin(base=Casing), package="b"))
    with pytest.raises(AmbiguousPluginError, match="caps, shout"):
        reg.select(Casing)
    assert reg.select(Casing, pin="caps") is reg.get("caps")


def test_not_found_says_why_only_if_the_registry_remembers():
    ep = FakeEntryPoint("summarize", plugin(api="2.0"))
    with pytest.raises(PluginNotFoundError, match=r"No usable plugin named 'summarize'.*\(usable: none\)"):
        registry(ep).get("summarize")
    with pytest.raises(PluginNotFoundError, match="installed but not usable: .*targets textpipe API 2.0"):
        registry(ep, cls=StricterRegistry).get("summarize")


def test_stricter_registry_skips_on_metadata_without_importing():
    ep = FakeEntryPoint("summarize", plugin(api="2.0"), requires=["textpipe>=2.0"])
    assert registry(ep, cls=StricterRegistry).available() == {}
    assert ep.loads == 0


def test_a_requirement_behind_an_extra_does_not_count():
    ep = FakeEntryPoint("shout", plugin(), requires=['textpipe>=2.0; extra == "gpu"'])
    assert "shout" in registry(ep, cls=StricterRegistry).available()


def test_an_unreadable_requirement_skips_that_plugin_only():
    bad = FakeEntryPoint("bad", plugin(), package="bad-pkg", requires=["textpipe >>= 1"])
    good = FakeEntryPoint("shout", plugin(), package="good-pkg", requires=["textpipe>=1.0"])
    reg = registry(bad, good, cls=StricterRegistry)
    assert list(reg.available()) == ["shout"]
    assert bad.loads == 0 and "unreadable requirement" in reg.skipped[("textpipe.filters", "bad")]


def test_a_marker_that_cannot_be_evaluated_skips_that_plugin_only():
    odd = FakeEntryPoint("odd", plugin(), package="odd-pkg",
                         requires=['textpipe>=1.0; os_name ~= "posix"'])
    good = FakeEntryPoint("shout", plugin(), package="good-pkg")
    reg = registry(odd, good, cls=StricterRegistry)
    assert list(reg.available()) == ["shout"]
    assert odd.loads == 0


class RealImport(FakeEntryPoint):
    """A stand-in whose load() really imports a module, as EntryPoint.load does."""

    def __init__(self, name, value, package):
        super().__init__(name, None, package=package)
        self.value = value

    def load(self):
        return importlib.metadata.EntryPoint(self.name, self.value, "textpipe.filters").load()


def test_after_a_collision_imports_are_not_rerun(tmp_path, monkeypatch):
    (tmp_path / "import_log.py").write_text("imports = []\n")
    for mod, cls in (("collide_a", "A"), ("collide_b", "B")):
        (tmp_path / f"{mod}.py").write_text(
            "import import_log\nfrom textpipe import Filter\n"
            f"import_log.imports.append({mod!r})\n"
            f"class {cls}(Filter):\n    pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    reg = registry(RealImport("shout", "collide_a:A", "pkg-a"),
                   RealImport("shout", "collide_b:B", "pkg-b"))
    for _ in range(2):
        with pytest.raises(PluginCollisionError):
            reg.available()
    import import_log
    assert import_log.imports == ["collide_a", "collide_b"]   # each module ran once


def test_not_found_is_also_a_key_error():
    with pytest.raises(KeyError):
        registry().get("nothing")
