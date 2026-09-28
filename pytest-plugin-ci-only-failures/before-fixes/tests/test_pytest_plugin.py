pytest_plugins = ["pytester"]

SCENARIO_FILE = """
from scenariokit import scenario

@scenario("demo")
async def demo(ctx):
    pass
"""


def test_scenario_becomes_a_test_item(pytester):
    pytester.makepyfile(test_demo=SCENARIO_FILE)
    result = pytester.runpytest("-p", "scenariokit.pytest_plugin",
                                "--collect-only", "-q")
    result.stdout.fnmatch_lines(["*scenario[[]demo[]]*"])
