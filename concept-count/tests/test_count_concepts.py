import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from count_concepts import count  # noqa: E402
from run_comparison import readme_block  # noqa: E402


def test_counts_only_framework_imports():
    c = count("import os\nfrom json import dumps\nfrom fw.core import Router, Request\n", {"fw"})
    assert c.imports == {"Router", "Request"}


def test_members_follow_assignment_and_unpacking():
    src = (
        "from fw import Runner, pair\n"
        "runner = Runner()\n"
        "a, b = pair()\n"
        "runner.run()\n"
        "b.close()\n"
        "other = object()\n"
        "other.ignored()\n"
    )
    c = count(src, {"fw"})
    assert c.members == {"run", "close"}
    assert c.names == {"Runner", "pair", "run", "close"}


def test_annotated_parameters_are_framework_objects():
    c = count("from fw import Context\nasync def entry(ctx: Context):\n    await ctx.connect()\n", {"fw"})
    assert c.members == {"connect"}


def test_hooks_decorated_passed_and_overridden():
    src = (
        "from fw import Agent, tool, serve\n"
        "@tool\n"
        "def lookup(city): ...\n"
        "def handler(ctx): ...\n"
        "class MyAgent(Agent):\n"
        "    def __init__(self): ...\n"
        "    def on_enter(self):\n"
        "        self.session.say('hi')\n"
        "serve(entry=handler)\n"
    )
    c = count(src, {"fw"})
    assert c.hooks == {"lookup", "handler", "MyAgent.on_enter"}
    assert {"session", "say"} <= c.members


def test_options_and_selector_strings():
    src = (
        "import os\n"
        "from fw import STT, Agent\n"
        "STT('vendor/model-3', language='multi', key=os.environ['VENDOR_KEY'])\n"
        "Agent(instructions='You are a friendly assistant.', turn={'resume': True})\n"
    )
    c = count(src, {"fw"})
    assert c.options == {"language", "key", "instructions", "turn", "resume"}
    # prose and environment-variable names are not framework selectors
    assert c.strings == {"vendor/model-3", "multi"}


def test_lines_skip_blank_and_comment_lines():
    c = count("# comment\n\nfrom fw import A\n\nA()  # trailing comment counts\n", {"fw"})
    assert c.lines == 2


def test_readme_block_takes_first_python_block_after_heading():
    md = "# T\n```python\nskip\n```\n### Simple\ntext\n```python\nkeep = 1\n```\n```python\nno\n```\n"
    assert readme_block(md, "### Simple") == "keep = 1\n"


def test_members_merge_same_attribute_across_objects():
    src = "from fw import A, B\na = A()\nb = B()\na.run()\nb.run()\n"
    assert count(src, {"fw"}).members == {"run"}


def test_keyword_string_value_counts_as_option_and_string():
    c = count("from fw import STT\nSTT(model='nova-3')\n", {"fw"})
    assert "model" in c.options and "nova-3" in c.strings


def test_plain_import_counts_as_a_name():
    c = count("import fw\nfw.mark.slow\n", {"fw"})
    assert c.imports == {"fw"} and c.members == {"mark", "slow"}


def test_hook_prefix_counts_hooks_and_their_parameters():
    src = "def fw_setup(parser):\n    parser.addoption('--x')\ndef helper(y):\n    y.z()\n"
    c = count(src, {"fw"}, hook_prefixes=("fw_",))
    assert c.hooks == {"fw_setup"}
    assert c.members == {"addoption"}
    assert count(src, {"fw"}).hooks == set()


def test_settings_and_import_paths():
    src = "from pathlib import Path\nDEBUG = True\nAPPS = ['pkg.contrib.admin', 'en-us', '1.2.3']\nlower = 1\n"
    c = count(src, {"pkg"}, settings=True)
    assert c.settings == {"DEBUG", "APPS"}
    assert c.import_paths == {"pkg.contrib.admin"}
    assert c.names == set()
    assert count(src, {"pkg"}).settings == set()


def test_rst_block_scoped_to_section():
    from run_comparison import rst_block
    rst = "A\n-\n\n    # content of x.py\n    one = 1\n\nB\n-\n\n    # content of x.py\n    two = 2\n\nafter\n"
    assert rst_block(rst, "# content of x.py", "B") == "# content of x.py\ntwo = 2\n"


def test_string_namespace_counts_made_up_attributes_as_strings():
    src = "import fw\n@fw.mark.slow\ndef test_a():\n    pass\n"
    c = count(src, {"fw"}, hook_prefixes=("test_",), string_namespaces=("mark",))
    assert c.members == {"mark"} and c.strings == {"slow"}
    assert count(src, {"fw"}).members == {"mark", "slow"}
