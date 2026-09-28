# scenariokit: a pytest plugin that passes from source and fails when installed

From the post [Green on my laptop, red in CI](https://mainlog.dev/posts/pytest-plugin-ci-only-failures/).

A small, made-up package that reproduces two CI-only failures:

1. The plugin, once pytest discovers it, collects the README example in
   `tests/examples/` as a test. That test then fails with `Connection refused`
   because no development server is running.
2. The plugin's own `pytester` test passes `-p scenariokit.pytest_plugin`. Once the
   package is installed, the nested pytest run also loads the plugin from its entry
   point and crashes with `ValueError: Plugin already registered under a different
   name`. The outer test reports this as an unmatched output pattern; the ValueError
   is in its captured stderr.

`before-fixes/` shows the original problem. `after-fixes/` adds the two changes from
the article: `--ignore=tests/examples` in `pyproject.toml`, and `plugin_args()` in
`tests/test_pytest_plugin.py`.

## Reproduce

Recorded with Python 3.12.12, pytest 9.1.1, pluggy 1.6.0, hatchling 1.32.4 and
build 1.6.1 on macOS. Other versions may word the errors differently.

From source, the way the suite passed locally:

```sh
cd before-fixes
python -m venv --clear /tmp/source-run
/tmp/source-run/bin/pip install pytest
PYTHONPATH=src /tmp/source-run/bin/python -m pytest      # 2 passed
```

Against the installed wheel, the way CI ran it:

```sh
cd before-fixes
python -m pip install build
python -m build --wheel
python -m venv --clear /tmp/wheel-check
/tmp/wheel-check/bin/pip install pytest dist/scenariokit-0.1.0-py3-none-any.whl
/tmp/wheel-check/bin/python -c "import scenariokit; print(scenariokit.__file__)"
/tmp/wheel-check/bin/python -m pytest                    # 2 failed, 1 passed
```

The printed path should be inside `/tmp/wheel-check`. Repeat the second block in
`after-fixes/` to get `2 passed`; `--clear` makes sure the environment starts empty,
so pip can't keep an older wheel with the same version number.

Without a server, the scenario failure prints a long traceback. Add `--tb=short`
to the pytest command for a readable one.

Make sure nothing is listening on `127.0.0.1:7357`, the scenario's default server
address, or the first failure won't appear.

## Scope

The plugin is intentionally minimal: module-level async scenarios, one request
per call, no server lifecycle management. It exists to demonstrate the source
versus installed-package difference, not as a testing library.
