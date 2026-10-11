#!/bin/sh
# Reproduce every cell of the article's pytest / Flask / stevedore table.
# Builds its own tiny plugins, then tries each case in a fresh virtual environment.
# Usage: sh real_hosts/run.sh [WORKDIR]
set -eu
WORK=${1:-$(mktemp -d)}
PY=${PYTHON:-python3}
export PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
PYTEST="pytest==9.1.1 pluggy==1.6.0"
FLASK="flask==3.1.3 click==8.5.0"
STEVEDORE="stevedore==5.9.1"

rm -rf "$WORK/pkgs" "$WORK/dist"; mkdir -p "$WORK/pkgs" "$WORK/dist" "$WORK/empty"
"$PY" -m venv --clear "$WORK/build-env"
"$WORK/build-env/bin/pip" -q install "setuptools>=68" wheel

plugin() {   # plugin DIST MODULE GROUP NAME VALUE DEPENDENCIES BODY
    d="$WORK/pkgs/$1"; mkdir -p "$d/src/$2"
    printf '%s\n' "$7" > "$d/src/$2/__init__.py"
    printf '%s\n' '[build-system]' 'requires = ["setuptools>=68"]' \
        'build-backend = "setuptools.build_meta"' '[project]' "name = \"$1\"" \
        'version = "0.1.0"' "dependencies = [$6]" "[project.entry-points.\"$3\"]" \
        "$4 = \"$5\"" '[tool.setuptools.packages.find]' 'where = ["src"]' > "$d/pyproject.toml"
    "$WORK/build-env/bin/pip" -q wheel --no-deps --no-build-isolation -w "$WORK/dist" "$d" >/dev/null
}
plugin pt-broken pt_broken pytest11 broken pt_broken '' 'import language_tables'
plugin pt-newer pt_newer pytest11 newer pt_newer '"pytest>=99"' 'print("pt_newer imported")'
plugin pt-hook pt_hook pytest11 hook pt_hook '' 'def pytest_future_hook(config): pass'
plugin pt-a pt_a pytest11 dup pt_a '' 'print("pt_a imported")'
plugin pt-b pt_b pytest11 dup pt_b '' 'print("pt_b imported")'
CMD='import click
print("%s imported")
@click.command()
def cmd():
    print("%s command ran")'
plugin fl-broken fl_broken flask.commands broken fl_broken:cmd '' 'import language_tables'
plugin fl-newer fl_newer flask.commands newer fl_newer:cmd '"flask>=99"' "$(printf "$CMD" fl_newer fl_newer)"
plugin fl-a fl_a flask.commands dup fl_a:cmd '' "$(printf "$CMD" fl_a fl_a)"
plugin fl-b fl_b flask.commands dup fl_b:cmd '' "$(printf "$CMD" fl_b fl_b)"
plugin sd-broken sd_broken demo.drivers broken sd_broken:D '' 'import language_tables'
plugin sd-newer sd_newer demo.drivers newer sd_newer:D '"stevedore>=99"' 'class D: pass'
plugin sd-a sd_a demo.drivers dup sd_a:D '' 'print("sd_a imported")
class D: pass'
plugin sd-b sd_b demo.drivers dup sd_b:D '' 'print("sd_b imported")
class D: pass'

env_with() {   # env_with NAME "HOST PINS" PLUGIN_DIST...
    name=$1; pins=$2; shift 2
    "$PY" -m venv --clear "$WORK/$name" >/dev/null
    "$WORK/$name/bin/pip" -q install $pins >/dev/null
    for p in "$@"; do "$WORK/$name/bin/pip" -q install --no-deps "$WORK/dist/$p-0.1.0-py3-none-any.whl" >/dev/null; done
}
last_lines() { grep -E "$1" | tail -n 1; }
cd "$WORK/empty"

echo "## pytest $PYTEST"
env_with pt-broken "$PYTEST" pt_broken
echo "import fails: $("$WORK/pt-broken/bin/python" -m pytest -q -p no:cacheprovider 2>&1 | last_lines Error)"
env_with pt-newer "$PYTEST" pt_newer
echo "newer host (declares pytest>=99): $("$WORK/pt-newer/bin/python" -m pytest -q -p no:cacheprovider 2>&1 | head -n 1)"
env_with pt-hook "$PYTEST" pt_hook
echo "newer host (a hook this pytest lacks): $("$WORK/pt-hook/bin/python" -m pytest -q -p no:cacheprovider 2>&1 | last_lines PluginValidationError | cut -c1-80)"
env_with pt-dup "$PYTEST" pt_a pt_b
echo "two distributions, one name: $("$WORK/pt-dup/bin/python" -m pytest -q -p no:cacheprovider 2>&1 | grep imported | tr '\n' ' ')"

echo "## Flask $FLASK"
env_with fl-broken "$FLASK" fl_broken
echo "import fails: $("$WORK/fl-broken/bin/flask" --help 2>&1 | last_lines Error)"
env_with fl-newer "$FLASK" fl_newer
echo "newer host (declares flask>=99): $("$WORK/fl-newer/bin/flask" newer 2>&1 | tr '\n' ' ')"
env_with fl-dup "$FLASK" fl_a fl_b
echo "two distributions, one name: $("$WORK/fl-dup/bin/flask" dup 2>&1 | tr '\n' ' ')"

echo "## stevedore $STEVEDORE"
env_with sd-broken "$STEVEDORE" sd_broken
"$WORK/sd-broken/bin/python" - <<'PY'
import logging
from stevedore import driver, extension
em = extension.ExtensionManager("demo.drivers")
print("import fails, ExtensionManager, logging not configured: names", em.names(), "(nothing printed)")
logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
em = extension.ExtensionManager("demo.drivers")
try:
    driver.DriverManager("demo.drivers", "broken")
except Exception as exc:
    print("import fails, DriverManager:", type(exc).__name__, exc)
PY
env_with sd-newer "$STEVEDORE" sd_newer
"$WORK/sd-newer/bin/python" -c "
from stevedore import driver
print('newer host (declares stevedore>=99): DriverManager loaded', driver.DriverManager('demo.drivers', 'newer').driver)"
env_with sd-dup "$STEVEDORE" sd_a sd_b
"$WORK/sd-dup/bin/python" - <<'PY'
from stevedore import driver, extension
em = extension.ExtensionManager("demo.drivers")
print("two distributions, one name, ExtensionManager: names", em.names(), "| em['dup'] from", em["dup"].plugin.__module__,
      "| found in order", [e.plugin.__module__ for e in em.extensions])
try:
    driver.DriverManager("demo.drivers", "dup")
except Exception as exc:
    print("two distributions, one name, DriverManager:", type(exc).__name__)
PY
