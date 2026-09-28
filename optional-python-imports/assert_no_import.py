"""Check optional import boundaries in a fresh Python interpreter."""
from pathlib import Path
import subprocess
import sys


def assert_import_does_not_load(module, forbidden, *, source_root=None):
    """Check one import in a child using this interpreter and installed environment."""
    child = r"""
import contextlib
import importlib
import sys

module, forbidden, source_root = sys.argv[1:]
if source_root:
    sys.path.insert(0, source_root)

def loaded():
    return sorted(name for name in tuple(sys.modules)
                  if name == forbidden or name.startswith(forbidden + "."))

if loaded():
    raise RuntimeError("Invalid test setup: forbidden module already loaded")

with contextlib.redirect_stdout(sys.stderr):
    importlib.import_module(module)
if loaded():
    raise AssertionError("Import loaded forbidden modules: " + ", ".join(loaded()))
print("IMPORT_CONTRACT_OK")
"""
    root = str(Path(source_root).resolve()) if source_root is not None else ""
    result = subprocess.run(
        [sys.executable, "-I", "-c", child, module, forbidden, root],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or result.stdout != "IMPORT_CONTRACT_OK\n":
        raise AssertionError(
            f"Import contract failed for {module!r} (exit {result.returncode})\n"
            f"{result.stdout}{result.stderr}"
        )
