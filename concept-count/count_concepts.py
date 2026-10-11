"""Count the framework concepts an example makes its user write.

The unit is a name the user has to know to write the example:

* imports  - names imported from the framework's packages
* members  - attributes the example reads or calls on framework objects
* hooks    - user functions the framework calls back (decorated functions,
             methods on framework subclasses, functions handed to framework calls)
* options  - keyword arguments (and dict keys inside them) passed to framework calls
* strings  - short string values passed to framework calls that select behavior,
             such as "provider/model" identifiers or event names

Two columns cover concepts delivered by convention rather than by call:

* settings     - module-level UPPER_CASE names, for frameworks that read a
                 settings module by name (opt in with --settings)
* import paths - strings anywhere in the file that look like dotted Python
                 import paths ("package.module.Name"), which the framework
                 imports for you later

"Names" is imports plus members: the vocabulary. The other columns are reported
separately because they are different kinds of thing to learn.

Functions whose names start with a given prefix (--hook-prefix pytest_) count
as hooks, and their parameters as framework objects, because frameworks that
find functions by name also pass them framework objects.

Some framework objects accept any attribute name (pytest.mark.<anything>). With
--string-namespace mark, attributes read on such a namespace count as strings,
because the project makes those names up and the framework can't check them
the way it checks its own.

Tracking is deliberately simple and flow-insensitive: a local name is a
framework object if it is imported from the framework, assigned from an
expression rooted in a framework object, or annotated with a framework type.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field

MAX_SELECTOR_LEN = 40  # longer strings are prose (instructions, prompts)
IMPORT_PATH = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){2,}$")
SETTING = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass
class Count:
    imports: set[str] = field(default_factory=set)
    members: set[str] = field(default_factory=set)
    hooks: set[str] = field(default_factory=set)
    options: set[str] = field(default_factory=set)
    strings: set[str] = field(default_factory=set)
    settings: set[str] = field(default_factory=set)
    import_paths: set[str] = field(default_factory=set)
    lines: int = 0

    @property
    def names(self) -> set[str]:
        return self.imports | self.members


def _root(node: ast.AST) -> str | None:
    """The leftmost name of a.b.c, a(...).b, a[...].b, and so on."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Await):
            node = node.value
        else:
            return None


def _targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [n for elt in node.elts for n in _targets(elt)]
    return []


def _is_selector(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_SELECTOR_LEN and not any(c.isspace() for c in value)


def _scan_argument(node: ast.AST, result: Count, user_functions: set[str], is_fw) -> None:
    """Collect hooks, dict-key options and selector strings from one argument.

    Does not look inside calls or subscripts on non-framework objects, so
    os.environ["API_KEY"] is the user's business, not a framework string.
    """
    if isinstance(node, (ast.Call, ast.Subscript)) and not is_fw(node):
        return
    if isinstance(node, ast.Name) and node.id in user_functions:
        result.hooks.add(node.id)
    elif isinstance(node, ast.Constant) and _is_selector(node.value):
        result.strings.add(node.value)
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                result.options.add(key.value)
            _scan_argument(value, result, user_functions, is_fw)
        return
    for child in ast.iter_child_nodes(node):
        _scan_argument(child, result, user_functions, is_fw)


def count(source: str, packages: set[str], hook_prefixes: tuple[str, ...] = (), settings: bool = False,
          string_namespaces: tuple[str, ...] = ()) -> Count:
    tree = ast.parse(source)
    result = Count()

    def from_framework(module: str | None) -> bool:
        return bool(module) and module.split(".")[0] in packages

    # Pass 1: find local names bound to framework objects (repeat until stable).
    fw: set[str] = set()
    user_functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and from_framework(node.module):
            fw.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if from_framework(a.name):
                    fw.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            user_functions.add(node.name)
            if hook_prefixes and node.name.startswith(hook_prefixes):
                fw.update(a.arg for a in node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls"))

    def is_fw(node: ast.AST | None) -> bool:
        return node is not None and _root(node) in fw

    changed = True
    while changed:
        before = len(fw)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and is_fw(node.value):
                for t in node.targets:
                    fw.update(_targets(t))
            elif isinstance(node, ast.AnnAssign) and (is_fw(node.annotation) or is_fw(node.value)):
                fw.update(_targets(node.target))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if is_fw(item.context_expr) and item.optional_vars is not None:
                        fw.update(_targets(item.optional_vars))
            elif isinstance(node, (ast.For, ast.AsyncFor)) and is_fw(node.iter):
                fw.update(_targets(node.target))
            elif isinstance(node, ast.arg) and is_fw(node.annotation):
                fw.add(node.arg)
            elif isinstance(node, ast.ClassDef) and any(is_fw(b) for b in node.bases):
                fw.update({node.name, "self"})
        changed = len(fw) != before

    # Pass 2: count.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and from_framework(node.module):
            result.imports.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            result.imports.update(a.asname or a.name for a in node.names if from_framework(a.name))
        elif isinstance(node, ast.Attribute) and is_fw(node.value):
            if isinstance(node.value, ast.Attribute) and node.value.attr in string_namespaces:
                result.strings.add(node.attr)  # e.g. pytest.mark.<anything>: a name the project makes up
            else:
                result.members.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(is_fw(d) for d in node.decorator_list) or (hook_prefixes and node.name.startswith(hook_prefixes)):
                result.hooks.add(node.name)
        elif isinstance(node, ast.ClassDef) and any(is_fw(b) for b in node.bases):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name != "__init__" \
                        and not any(is_fw(d) for d in item.decorator_list):
                    result.hooks.add(f"{node.name}.{item.name}")
        elif isinstance(node, ast.Call) and is_fw(node.func):
            values = list(node.args)
            for kw in node.keywords:
                if kw.arg:
                    result.options.add(kw.arg)
                values.append(kw.value)
            for value in values:
                _scan_argument(value, result, user_functions, is_fw)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and IMPORT_PATH.match(node.value):
            result.import_paths.add(node.value)

    if settings:
        for stmt in tree.body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
            for t in targets:
                result.settings.update(n for n in _targets(t) if SETTING.match(n))

    result.lines = sum(1 for line in source.splitlines() if line.strip() and not line.strip().startswith("#"))
    return result


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Count the framework concepts an example makes its user write.")
    parser.add_argument("packages", help="the framework's top-level package names, comma-separated")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--hook-prefix", action="append", default=[], help="functions named like this are hooks (repeatable)")
    parser.add_argument("--settings", action="store_true", help="count module-level UPPER_CASE names as settings")
    parser.add_argument("--string-namespace", action="append", default=[], help="attributes read on this namespace count as strings (repeatable)")
    args = parser.parse_args(argv[1:])
    for path in args.files:
        with open(path, encoding="utf-8") as f:
            c = count(f.read(), set(args.packages.split(",")), tuple(args.hook_prefix), args.settings, tuple(args.string_namespace))
        print(f"{path}: {len(c.names)} names ({len(c.imports)} imported, {len(c.members)} members), "
              f"{len(c.hooks)} hooks, {len(c.options)} options, {len(c.strings)} strings, "
              f"{len(c.settings)} settings, {len(c.import_paths)} import paths, {c.lines} lines")
        for label in ("imports", "members", "hooks", "options", "strings", "settings", "import_paths"):
            print(f"  {label}: {', '.join(sorted(getattr(c, label))) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
