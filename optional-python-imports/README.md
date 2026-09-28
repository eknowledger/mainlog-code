# Keeping optional Python imports optional

From the post [Keeping optional Python imports optional](https://mainlog.dev/posts/optional-python-imports/).

A test that checks importing a library leaves an optional dependency unloaded, and proves it finished the import
before saying so. It runs the import in a fresh interpreter and requires both a completion marker and exit status 0.

| File | What it is |
|---|---|
| `runtime_helpers.py` | The made-up library: `normalize()`, plus `decorate()`, whose import of the optional module is lazy |
| `optional_toolkit.py` | The made-up optional dependency |
| `assert_no_import.py` | The helper: one import in a child interpreter, checked for the forbidden namespace |
| `test_import_contract.py` | The test |

## Run it

No dependencies beyond the standard library. From this directory:

```sh
python3 test_import_contract.py
```

```text
PASS: runtime_helpers leaves optional_toolkit unloaded
```

Move `import optional_toolkit` to the top of `runtime_helpers.py` and run it again: it fails with
`AssertionError: Import loaded forbidden modules: optional_toolkit`. CI makes that edit on every run to prove the
test can fail.

Checked with Python 3.12 and 3.9.
