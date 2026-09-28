from importlib.metadata import entry_points

pytest_plugins = ["pytester"]

SCENARIO_FILE = """
from scenariokit import scenario

@scenario("demo")
async def demo(ctx):
    pass
"""


def plugin_args():
    """Load the plugin explicitly only when pytest won't find it by itself."""
    installed = {ep.name for ep in entry_points(group="pytest11")}
    return [] if "scenariokit" in installed else ["-p", "scenariokit.pytest_plugin"]


def test_scenario_becomes_a_test_item(pytester):
    pytester.makepyfile(test_demo=SCENARIO_FILE)
    result = pytester.runpytest(*plugin_args(), "--collect-only", "-q")
    result.stdout.fnmatch_lines(["*scenario[[]demo[]]*"])
