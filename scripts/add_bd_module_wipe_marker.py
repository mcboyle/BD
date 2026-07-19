"""v3.66.13 dedup companion: add `pytestmark = pytest.mark.bd_module_wipe`
to test files that previously had their own autouse `isolated_bd_home`
fixture.

These files relied on the bulk_downloader.* sys.modules wipe to re-read
env vars on import. Without the marker, the canonical conftest fixture
skips the wipe (because most tests don't need it and a few break under
it).

The list of affected files is exactly the set the strip script
rewrote (46 files). We re-detect them here by looking at the git diff
since baseline — any tests/test_*.py that the dedup touched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

MARKER_LINE = "pytestmark = pytest.mark.bd_module_wipe"
MARKER_COMMENT = (
    "# v3.66.13: this file used to define its own autouse isolated_bd_home\n"
    "# fixture; the canonical one lives in tests/conftest.py. The marker\n"
    "# opts this file back into the sys.modules wipe (needed so the package\n"
    "# re-reads env vars at import; see conftest.py for the full rationale).\n"
)


def files_to_mark() -> list[Path]:
    """Files modified-but-not-added since baseline that live under tests/.
    The strip script's rewrite is the only thing in that set right now.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return sorted(
        REPO_ROOT / p for p in out.splitlines()
        if p.startswith("tests/test_") and p.endswith(".py")
    )


def already_has_marker(text: str) -> bool:
    return "bd_module_wipe" in text


def add_marker(path: Path, apply: bool) -> bool:
    text = path.read_text()
    if already_has_marker(text):
        return False
    # Insert after the import block: locate the last import statement
    # at module top level (no indent), then add a blank line + the
    # marker comment + the marker line.
    lines = text.splitlines(keepends=True)
    last_import = -1
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if not line.startswith((" ", "\t")) and (
                stripped.startswith("import ")
                or stripped.startswith("from ")):
            last_import = idx
    if last_import < 0:
        # Fallback: insert at top, after any docstring
        last_import = 0
    # Ensure we have a `import pytest` somewhere — if not, add it.
    import_block_text = "".join(lines[:last_import + 1])
    needs_pytest = "import pytest" not in import_block_text
    insert_at = last_import + 1
    # Skip any immediately-following blank lines
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1
    insertion = ""
    if needs_pytest:
        insertion += "import pytest\n"
    insertion += "\n" + MARKER_COMMENT + MARKER_LINE + "\n\n"
    new = "".join(lines[:insert_at]) + insertion + "".join(lines[insert_at:])
    if apply:
        path.write_text(new)
    return True


def main() -> int:
    apply = "--apply" in sys.argv
    touched = 0
    for path in files_to_mark():
        # Only target test files that don't already have an
        # isolated_bd_home fixture (those weren't part of the dedup).
        # The strip script wouldn't have changed them either.
        text = path.read_text()
        if "def isolated_bd_home(tmp_path)" in text:
            print(f"  SKIP  {path.name} (still defines local fixture)")
            continue
        if already_has_marker(text):
            print(f"  SKIP  {path.name} (already marked)")
            continue
        changed = add_marker(path, apply=apply)
        if changed:
            touched += 1
            tag = "MARKED" if apply else "WOULD-MARK"
            print(f"  {tag} {path.name}")
    verb = "marked" if apply else "would mark"
    print(f"\n{verb} {touched} files")
    if not apply:
        print("re-run with --apply to make changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
