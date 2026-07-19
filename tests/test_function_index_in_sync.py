"""Drift test for KB_PLAN_v2 B.1 — FUNCTION_INDEX.md.

The function index earns its read-budget keep only if it stays in
sync with the five files it covers. Without this test, a new
function landing in app.py or runner.py without a regen run goes
silently undetected until somebody notices it isn't in the index.
By that point the index is no longer trustworthy and a fresh Claude
can't use it to plan work — worse than not having an index at all
(the plan calls this out explicitly under "what could undo this
work").

This test regenerates the index in-process and diffs against the
on-disk copy. Any discrepancy fails the suite — and since
`tools/build_release.py` runs the extracted suite as a gate before
emitting the zip, a stale index blocks the release.

Failure recovery: run

    python tools/build_function_index.py

at the repo root to rewrite the file, then commit the diff alongside
the function change.

Implementation notes:

  - Unlike the endpoint catalog (B.2), the function index is built
    by pure AST walking. No Flask app import, no
    `BD_DISABLE_KEEPALIVE`, no background subsystems to suppress.
    The regen is cheap (~0.2-0.5s) and we don't need to cache it
    across tests in the same way.

  - We still cache via a module-level lazy in case future expansion
    adds more tests to this file. Keeps the cost flat.

  - Uses `assert False, msg` rather than `pytest.fail` (the custom
    run_tests.py runner's pytest stub doesn't implement `fail`).
    Same substitution as test_endpoint_catalog_in_sync.py — see
    that file's docstring for the rationale.
"""
import difflib
import sys
from pathlib import Path

# `import pytest` is harmless under both real pytest and the custom
# run_tests.py stub. This file does NOT call pytest.fail() —
# `assert False, msg` works under both runners.
import pytest  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.build_function_index import (                                # noqa: E402
    INDEX_SCHEMA_VERSION,
    TARGETS,
    _index_path,
    render_index,
)


_INDEX = _index_path()

# Lazy module-level cache so the two diff-running tests share one
# AST walk. Pure-AST regen is ~0.2-0.5s, so the win is small, but
# the pattern matches test_endpoint_catalog_in_sync.py for symmetry
# (easier for future readers).
_REGENERATED_CACHE: str | None = None


def _get_regenerated() -> str:
    global _REGENERATED_CACHE
    if _REGENERATED_CACHE is None:
        _REGENERATED_CACHE = render_index(_REPO_ROOT)
    return _REGENERATED_CACHE


def test_function_index_exists():
    """The index file must exist at repo root. If it's missing,
    something's wrong with the release builder (or someone deleted
    it). Re-run `python tools/build_function_index.py` to recreate."""
    assert _INDEX.is_file(), (
        f"FUNCTION_INDEX.md missing at {_INDEX}. "
        f"Run `python tools/build_function_index.py` to generate."
    )


def test_function_index_targets_all_exist():
    """Every file the index claims to cover must actually exist. If
    one of the five files was renamed or moved, the index builder's
    TARGETS list needs updating in the same commit. This test exists
    so a stealth-rename gets caught at suite time, not at the next
    regen attempt."""
    missing = [t for t in TARGETS if not (_REPO_ROOT / t).is_file()]
    assert not missing, (
        f"build_function_index.py TARGETS references missing files: "
        f"{missing}. Either restore the files or update TARGETS in "
        f"tools/build_function_index.py."
    )


def test_function_index_in_sync():
    """On-disk FUNCTION_INDEX.md must match what the builder would
    generate from the current source tree. Drift means somebody
    added/removed/renamed/redocstring'd a function in a covered file
    without running the regen step.

    The error message includes the first ~30 lines of diff so the
    fix is usually obvious from the test output alone.
    """
    regenerated = _get_regenerated()
    on_disk = _INDEX.read_text(encoding="utf-8")
    if on_disk == regenerated:
        return
    diff_lines = list(difflib.unified_diff(
        on_disk.splitlines(keepends=True),
        regenerated.splitlines(keepends=True),
        fromfile="FUNCTION_INDEX.md (on disk)",
        tofile="FUNCTION_INDEX.md (regenerated)",
        n=2,
    ))
    snippet = "".join(diff_lines[:30])
    if len(diff_lines) > 30:
        snippet += f"... (+{len(diff_lines) - 30} more lines)\n"
    assert False, (
        "FUNCTION_INDEX.md is out of sync with source.\n"
        "Run `python tools/build_function_index.py` to regenerate.\n"
        "\nDrift:\n" + snippet
    )


def test_function_index_schema_version_in_header():
    """The header banner names a schema version. Catches header
    edits that don't go through a regen. Same tripwire as
    test_endpoint_catalog_in_sync.py."""
    needle = f"Schema version: {INDEX_SCHEMA_VERSION}"
    on_disk = _INDEX.read_text(encoding="utf-8")
    assert needle in on_disk, (
        f"FUNCTION_INDEX.md does not declare schema version "
        f"{INDEX_SCHEMA_VERSION}. Header was hand-edited or "
        f"INDEX_SCHEMA_VERSION was bumped without a regen. "
        f"Run `python tools/build_function_index.py`."
    )
    # Cache hit — cheap.
    assert needle in _get_regenerated()
