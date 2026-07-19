"""v3.66.13 conftest dedup helper (AST-based, safe variant).

Strip the per-file `isolated_bd_home` fixture from every tests/*.py
that defines one. Canonical version now lives in tests/conftest.py.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
TESTS_DIR = THIS.parent.parent / "tests"
CONFTEST = TESTS_DIR / "conftest.py"


def find_fixture_lines(text: str) -> tuple[int, int] | None:
    """Return (start_line_0idx, end_line_0idx_exclusive) of the
    `isolated_bd_home` fixture, OR None if not present.
    `start_line` is the @pytest.fixture decorator line.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "isolated_bd_home":
            continue
        # Prefer decorator line as start if present
        if node.decorator_list:
            start = node.decorator_list[0].lineno - 1
        else:
            start = node.lineno - 1
        end = node.end_lineno  # exclusive when treated as 0-indexed slice end
        return start, end
    return None


def strip(text: str) -> tuple[str, bool]:
    found = find_fixture_lines(text)
    if not found:
        return text, False
    start, end = found
    lines = text.splitlines(keepends=True)
    # Eat trailing blank lines so we don't leave a gap
    while end < len(lines) and lines[end].strip() == "":
        end += 1
    new = "".join(lines[:start] + lines[end:])
    return new, True


def main() -> int:
    apply = "--apply" in sys.argv
    touched = 0
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.resolve() == CONFTEST.resolve():
            continue  # canonical fixture lives here
        text = path.read_text()
        new, changed = strip(text)
        if changed:
            touched += 1
            tag = "REWROTE" if apply else "WOULD-REWRITE"
            delta = len(text) - len(new)
            print(f"  {tag} {path.name:60s} -{delta} chars")
            if apply:
                path.write_text(new)
    verb = "rewrote" if apply else "would rewrite"
    print(f"\n{verb} {touched} files")
    if not apply:
        print("re-run with --apply to actually do it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
