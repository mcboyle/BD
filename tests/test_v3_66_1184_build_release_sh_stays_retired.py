"""Tombstone the zip-era release shell wrapper after its retirement.

The supported packaging implementation is ``tools/build_release.py``.  The
shell wrapper froze v3.66.137/v3.66.148 zip paths and had no execution callers;
restoring it would revive a second, contradictory release path.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from tracked_source import tracked_source_files


REPO_ROOT = Path(__file__).resolve().parents[1]
RETIRED = "scripts/" + "build_release.sh"
CURRENT = "tools/build_release.py"


def _tracked_paths() -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = {path for path in proc.stdout.split("\0") if path}
    assert len(paths) > 1_000, "tracked-file denominator unexpectedly collapsed"
    return paths


def test_zip_era_release_wrapper_is_not_tracked():
    assert RETIRED not in _tracked_paths()


def test_current_release_documentation_names_the_python_builder():
    readme = (REPO_ROOT / "project-knowledge" / "README.md").read_text(
        encoding="utf-8"
    )
    assert CURRENT in readme
    assert RETIRED not in readme


def test_no_tracked_source_executes_the_retired_wrapper():
    offenders: list[str] = []
    for relative, kind in tracked_source_files(REPO_ROOT):
        if relative == __file__.removeprefix(str(REPO_ROOT) + "/"):
            continue
        path = REPO_ROOT / relative
        source = path.read_text(encoding="utf-8", errors="replace")
        if RETIRED not in source:
            continue
        if kind == "python":
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and RETIRED in node.value
                ):
                    offenders.append(f"{relative}:{node.lineno}")
        else:
            for lineno, line in enumerate(source.splitlines(), 1):
                if RETIRED in line.split("#", 1)[0]:
                    offenders.append(f"{relative}:{lineno}")
    assert not offenders, f"retired release wrapper is still executable: {offenders}"


BD_GATE_SCOPE = "repo-wide"
