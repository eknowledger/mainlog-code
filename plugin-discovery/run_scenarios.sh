#!/bin/sh
# Build every package, then run each scenario in a fresh virtual environment.
# Usage: sh run_scenarios.sh [WORKDIR]    (default: a new temporary directory)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-$(mktemp -d)}
PY=${PYTHON:-python3}
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONUNBUFFERED=1   # keep print() and log lines in the order they happen

rm -rf "$WORK/dist" "$WORK/dist-2" "$WORK/extra" "$WORK"/src-*
mkdir -p "$WORK/dist" "$WORK/dist-2"
"$PY" -m venv --clear "$WORK/build-env"
"$WORK/build-env/bin/pip" -q install "setuptools>=68" wheel
build() {   # build SOURCE_DIR OUT_DIR: build a wheel from a copy, keeping the tree clean
    copy="$WORK/src-$(basename "$1")-$(basename "$2")"
    cp -R "$1" "$copy"
    "$WORK/build-env/bin/pip" -q wheel --no-deps --no-build-isolation -w "$2" "$copy"
}
for pkg in "$HERE/textpipe" "$HERE"/plugins/*; do build "$pkg" "$WORK/dist"; done
# A textpipe 2.0, used only to show what pip does on a later downgrade.
cp -R "$HERE/textpipe" "$WORK/textpipe-2.0"
sed 's/^version = "1.4"/version = "2.0"/' "$HERE/textpipe/pyproject.toml" > "$WORK/textpipe-2.0/pyproject.toml"
build "$WORK/textpipe-2.0" "$WORK/dist-2"

env_with() {   # env_with NAME WHEEL...: a fresh environment with textpipe 1.4 and these plugins
    name=$1; shift
    "$PY" -m venv --clear "$WORK/$name"
    "$WORK/$name/bin/pip" -q install packaging "$WORK/dist/textpipe-1.4-py3-none-any.whl"
    for wheel in "$@"; do
        "$WORK/$name/bin/pip" -q install --no-deps "$WORK/dist/$wheel"
    done
}
trace() { "$WORK/$1/bin/python" "$HERE/demo/trace.py" "$2"; }

echo "#### opening: shout and future (installed with --no-deps)"
env_with opening textpipe_shout-0.1.0-py3-none-any.whl textpipe_future-0.1.0-py3-none-any.whl
trace opening listing
trace opening opening
trace opening stricter

echo "#### broken: shout and broken"
env_with broken textpipe_shout-0.1.0-py3-none-any.whl textpipe_broken-0.1.0-py3-none-any.whl
trace broken broken

echo "#### collision: shout and loud both register 'shout'"
env_with collision textpipe_shout-0.1.0-py3-none-any.whl textpipe_loud-0.1.0-py3-none-any.whl
trace collision collision

echo "#### choose: shout and caps are both Casing filters"
env_with choose textpipe_shout-0.1.0-py3-none-any.whl textpipe_caps-0.1.0-py3-none-any.whl
trace choose choose

echo "#### which-copy: textpipe-shout 0.1.0 installed, a 0.2.0 copy (code + .dist-info) first on PYTHONPATH"
env_with which-copy textpipe_shout-0.1.0-py3-none-any.whl
"$WORK/which-copy/bin/pip" -q install --no-deps --target "$WORK/extra" \
    "$WORK/dist/textpipe_shout-0.2.0-py3-none-any.whl"
ls "$WORK/extra"
# (called directly: a variable assignment before a shell function can outlive the call)
PYTHONPATH="$WORK/extra" "$WORK/which-copy/bin/python" "$HERE/demo/trace.py" which-copy

echo "#### three checks: stand-in entry points against both registries"
"$WORK/opening/bin/python" "$HERE/demo/three_checks.py"

echo "#### pip at install time: textpipe-future into an environment with textpipe 1.4"
"$PY" -m venv --clear "$WORK/resolve"
"$WORK/resolve/bin/pip" -q install packaging "$WORK/dist/textpipe-1.4-py3-none-any.whl"
"$WORK/resolve/bin/pip" install --no-index --find-links "$WORK/dist" textpipe-future 2>&1 \
    | grep -E "ERROR" || true

echo "#### pip later: textpipe-future installed properly with textpipe 2.0, then textpipe==1.4"
"$PY" -m venv --clear "$WORK/later"
"$WORK/later/bin/pip" -q install --no-index --find-links "$WORK/dist-2" --find-links "$WORK/dist" \
    packaging textpipe-future 2>/dev/null || \
  "$WORK/later/bin/pip" -q install packaging "$WORK/dist-2/textpipe-2.0-py3-none-any.whl" \
    "$WORK/dist/textpipe_future-0.1.0-py3-none-any.whl"
"$WORK/later/bin/pip" list 2>/dev/null | grep -i textpipe
status=0
"$WORK/later/bin/pip" install --no-index --find-links "$WORK/dist" "textpipe==1.4" \
    > "$WORK/later-install.log" 2>&1 || status=$?
grep -E "ERROR|requires|Successfully" "$WORK/later-install.log" || true
echo "pip install exited with status $status"
"$WORK/later/bin/pip" check || true
