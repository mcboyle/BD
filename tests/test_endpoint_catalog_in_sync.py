"""Drift test for KB_PLAN_v2 B.2 — ENDPOINT_CATALOG.md.

The catalog earns its read-budget keep only if it stays in sync with
`app.url_map`. Without this test, a new Flask route landing without
a regen run goes silently undetected for three releases until somebody
notices `/api/foo` isn't in the catalog. By that point the catalog
is no longer trustworthy and a fresh Claude can't use it to plan
work — worse than not having a catalog at all (the plan calls this
out explicitly under "what could undo this work").

This test regenerates the catalog in-process and diffs against the
on-disk copy. Any discrepancy fails the suite — and since
`tools/build_release.py` runs the extracted suite as a gate before
emitting the zip, a stale catalog blocks the release.

Failure recovery: run

    python tools/build_endpoint_catalog.py

at the repo root to rewrite the file, then commit the diff with the
route change.

Implementation notes:

  - `BD_DISABLE_KEEPALIVE=1` is set BEFORE importing the catalog
    builder so the background keepalive subsystems don't start.

  - The custom run_tests.py runner does NOT honor pytest fixtures
    beyond a fixed allowlist (clean_workdir, fresh_app, tmp_path,
    monkeypatch, aiassist_module). So this file uses module-level
    state for the regenerated content — populated lazily on first
    access — instead of a `@pytest.fixture`. The lazy population
    means a fast `python run_tests.py -k some_other_filter` won't
    pay the app-import cost just because this file gets imported.

  - The schema-version assertion (CATALOG_SCHEMA_VERSION) is a tripwire
    for accidental header edits. If the catalog's header text changes,
    the generator's schema version should be bumped at the same time;
    the test catches the case where someone edits the header in the
    builder but forgets to regenerate on-disk, and the case where
    someone touches the on-disk file directly without going through
    the builder.
"""
import os

# Must set BEFORE importing the catalog builder. The builder defers
# its own `from bulk_downloader.app import app` into a function, but
# the moment that function fires, BD_DISABLE_KEEPALIVE must already
# be in os.environ — otherwise the keepalive subsystems start.
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import difflib                                                          # noqa: E402
import sys                                                              # noqa: E402
from pathlib import Path                                                # noqa: E402

# `import pytest` is harmless under both real pytest and the custom
# run_tests.py stub, but this file does NOT call pytest.fail()/
# pytest.raises() — the stub doesn't implement `fail`, and a plain
# `assert False, msg` works under both runners.
import pytest  # noqa: F401, E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Importing the builder is safe — it does NOT import bulk_downloader
# at module scope; that's deferred into _import_app(). So this import
# is cheap.
from tools.build_endpoint_catalog import (                              # noqa: E402
    CATALOG_SCHEMA_VERSION,
    _catalog_path,
    _import_app,
    render_catalog,
)


_CATALOG = _catalog_path()

# Lazy-populated module-level cache for the regenerated content.
# Filled on first call to _get_regenerated(). Subsequent calls in
# the same process return the cached string, so the two tests below
# share one app import.
_REGENERATED_CACHE: str | None = None


def _get_regenerated() -> str:
    """Render the catalog once per test process and cache. Importing
    the app is the expensive step (~1-2s); we pay it once."""
    global _REGENERATED_CACHE
    if _REGENERATED_CACHE is None:
        app = _import_app()
        _REGENERATED_CACHE = render_catalog(app)
    return _REGENERATED_CACHE


def test_endpoint_catalog_exists():
    """The catalog file must exist at repo root. If it's missing,
    something's wrong with the release builder (or someone deleted
    it). Re-run `python tools/build_endpoint_catalog.py` to recreate."""
    assert _CATALOG.is_file(), (
        f"ENDPOINT_CATALOG.md missing at {_CATALOG}. "
        f"Run `python tools/build_endpoint_catalog.py` to generate."
    )


def test_endpoint_catalog_in_sync():
    """On-disk ENDPOINT_CATALOG.md must match what the builder would
    generate from the current source tree. Any drift means somebody
    added/removed/edited a route (or its docstring) without running
    the regen step.

    The error message includes the first ~30 lines of diff so the
    fix is usually obvious from the test output alone — no need to
    re-run the builder by hand to see what changed.
    """
    regenerated = _get_regenerated()
    on_disk = _CATALOG.read_text(encoding="utf-8")
    if on_disk == regenerated:
        return
    diff_lines = list(difflib.unified_diff(
        on_disk.splitlines(keepends=True),
        regenerated.splitlines(keepends=True),
        fromfile="ENDPOINT_CATALOG.md (on disk)",
        tofile="ENDPOINT_CATALOG.md (regenerated)",
        n=2,
    ))
    snippet = "".join(diff_lines[:30])
    if len(diff_lines) > 30:
        snippet += f"... (+{len(diff_lines) - 30} more lines)\n"
    # NOTE: assert False rather than pytest.fail — the custom
    # run_tests.py stub doesn't implement pytest.fail. The assertion
    # message carries the diff to the test output.
    assert False, (
        "ENDPOINT_CATALOG.md is out of sync with app.url_map.\n"
        "Run `python tools/build_endpoint_catalog.py` to regenerate.\n"
        "\nDrift:\n" + snippet
    )


def test_endpoint_catalog_schema_version_in_header():
    """The header banner names a schema version. If the on-disk
    catalog's header has a different schema number than the
    builder's CATALOG_SCHEMA_VERSION constant, the header was edited
    without a builder bump (or vice versa). Catches the case where
    someone modifies the catalog's header in source control without
    going through a regen.
    """
    needle = f"Schema version: {CATALOG_SCHEMA_VERSION}"
    on_disk = _CATALOG.read_text(encoding="utf-8")
    assert needle in on_disk, (
        f"ENDPOINT_CATALOG.md does not declare schema version "
        f"{CATALOG_SCHEMA_VERSION}. Header was hand-edited or "
        f"CATALOG_SCHEMA_VERSION was bumped without a regen. "
        f"Run `python tools/build_endpoint_catalog.py`."
    )
    # And the regenerated version must also have it — defensive but
    # cheap (cache hit).
    assert needle in _get_regenerated()
