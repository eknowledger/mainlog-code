from pathlib import Path

from assert_no_import import assert_import_does_not_load


def test_runtime_import():
    assert_import_does_not_load(
        "runtime_helpers",
        "optional_toolkit",
        source_root=Path(__file__).resolve().parent,
    )


if __name__ == "__main__":
    test_runtime_import()
    print("PASS: runtime_helpers leaves optional_toolkit unloaded")
