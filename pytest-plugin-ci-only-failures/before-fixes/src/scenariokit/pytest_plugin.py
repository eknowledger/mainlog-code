"""Let pytest collect and run @scenario functions."""
import asyncio

import pytest

from .context import ScenarioContext


def pytest_addoption(parser):
    parser.addoption("--scenario-server", default="127.0.0.1:7357",
                     help="host:port of the running development server")


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makeitem(collector, name, obj):
    scenario_name = getattr(obj, "_scenario_name", None)
    module = getattr(collector, "obj", None)
    if scenario_name is None or obj.__module__ != getattr(module, "__name__", None):
        return None  # not a scenario defined in this file: normal collection
    return ScenarioItem.from_parent(
        collector, name=f"scenario[{scenario_name}]", fn=obj)


class ScenarioItem(pytest.Item):
    def __init__(self, *, fn, **kwargs):
        super().__init__(**kwargs)
        self.fn = fn

    def runtest(self):
        ctx = ScenarioContext(self.config.getoption("--scenario-server"))
        asyncio.run(self.fn(ctx))

    def reportinfo(self):
        return self.path, None, self.name
