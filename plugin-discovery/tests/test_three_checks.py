"""three_checks.py must report hosts that behave unlike textpipe correctly."""

import importlib.util
import pathlib

from textpipe.registry import Registry

spec = importlib.util.spec_from_file_location(
    "three_checks", pathlib.Path(__file__).parent.parent / "demo" / "three_checks.py")
three_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(three_checks)


class NoVersionCheck(Registry):
    """A host that never checks versions, and lets the last duplicate win."""

    def _check_version(self, cls):
        pass

    def _discover(self, group):
        return {ep.name: ep.load() for ep in self._loader(group=group)}


class LookupErrorHost(Registry):
    """A host whose not-found error isn't a KeyError."""

    def get(self, name, group="textpipe.filters"):
        if name not in self.available(group):
            raise LookupError(f"no plugin {name!r}")
        return self.available(group)[name]


def build(cls):
    return lambda eps: cls(loader=lambda group: eps, host_version="1.4")


def test_textpipe_registry():
    lines = three_checks.run_checks(build(Registry))
    assert lines[0] == "1. too-new plugin: refused: yes, imported: yes"
    assert lines[1].startswith("2. lookup: PluginNotFoundError: No usable plugin named 'new'")
    assert lines[2] == "3. collision, two requests: PluginCollisionError, PluginCollisionError"


def test_a_host_without_a_version_check_is_not_reported_as_refusing():
    lines = three_checks.run_checks(build(NoVersionCheck))
    assert lines[0] == "1. too-new plugin: refused: no, imported: yes"
    assert lines[1] == "2. lookup: not refused: get() returned the plugin"
    assert lines[2] == "3. collision, two requests: returned, returned"


def test_a_non_key_error_not_found_is_reported_not_raised():
    lines = three_checks.run_checks(build(LookupErrorHost))
    assert lines[1] == "2. lookup: LookupError: no plugin 'new'"


class RaisesOnTooNew(Registry):
    """A host that raises during discovery instead of skipping a too-new plugin."""

    def _discover(self, group):
        found = {}
        for ep in self._loader(group=group):
            cls = ep.load()
            if cls.api_version > self._host_version:
                raise RuntimeError("plugin too new")
            found[ep.name] = cls
        return found


def test_a_host_that_raises_on_a_too_new_plugin_is_reported_as_refusing():
    lines = three_checks.run_checks(build(RaisesOnTooNew))
    assert lines[0] == "1. too-new plugin: refused: yes (raised RuntimeError), imported: yes"
