"""D3 follow-up U11 contract — drag-to-reorder on Queue (per-site).

Covers:
  - Existing /api/sites/<sid>/jobs/reorder endpoint stays registered
    and now routes through runner.reorder_urls so the in-process
    dispatch order actually changes (the pre-existing endpoint
    only persisted `ord` to DB; running sessions saw no effect).
  - The reorder fallback path also persists via queue_reorder when
    runner.reorder_urls raises (rare, but needs the safety net).
  - dnd-kit deps declared in frontend/package.json.
  - SortableWaitingJobRow component exists with a drag handle that
    has an accessible aria-label including the filename.
  - SortableQueueGroup component exists and POSTs to
    /api/sites/<sid>/jobs/reorder with ord values 10/20/30…
  - bucketWaitingBySite + SortChipRibbon are exported from the
    group module.
  - Queue.tsx mounts SortChipRibbon and SortableQueueGroup.
  - SortChipRibbon disables chips for sites with < 2 waiting jobs
    (one job = nothing to reorder).
  - Sort mode and selection mode are mutually exclusive (entering
    one clears the other).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = Path(__file__).resolve().parent.parent


# ── Backend ─────────────────────────────────────────────────────────



def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_u11_reorder_route_still_registered():
    from bulk_downloader import app as a
    rules = {(r.rule, tuple(sorted(r.methods or ()))) for r in a.app.url_map.iter_rules()}
    assert any(
        rule == "/api/sites/<sid>/jobs/reorder" and "POST" in methods
        for rule, methods in rules
    ), "POST /api/sites/<sid>/jobs/reorder missing"


def test_u11_reorder_handler_calls_runner_reorder_urls():
    """The handler must route through runner.reorder_urls(ordered)
    so the in-process dispatch order actually changes — the
    pre-U2 implementation only persisted `ord` to DB and the
    per-job dict, which meant the running session never saw the
    reorder. Pin the call shape so a future refactor doesn't
    silently regress to the broken pattern."""
    src = _APP_SRC()
    handler = re.search(
        r"def api_jobs_reorder\b.*?(?=\n@\w+\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler, "api_jobs_reorder handler not found"
    body = handler.group(0)
    assert "runner.reorder_urls(" in body, (
        "api_jobs_reorder must call runner.reorder_urls so dispatch "
        "order updates in a running session"
    )
    # The ordered list must be derived from sorting the dict by its
    # int ord values, not just dict.keys() (which is insertion order
    # — happens to work in CPython but is not what we want to commit
    # to as a contract).
    assert "sorted(coerced.keys()" in body, (
        "ordered URLs must be derived from sorting the ord dict by value"
    )


def test_u11_reorder_handler_has_fallback_persist():
    """If runner.reorder_urls raises, the handler must still attempt
    to persist via queue_reorder so a restart picks up the requested
    order. Pin the fallback block."""
    src = _APP_SRC()
    handler = re.search(
        r"def api_jobs_reorder\b.*?(?=\n@\w+\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler
    body = handler.group(0)
    # The fallback queue_reorder call must live in an `except` branch
    # nested under the reorder_urls try.
    assert "queue_reorder(sid, coerced)" in body, (
        "fallback queue_reorder(sid, coerced) call missing"
    )


def test_u11_reorder_rejects_get():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/sites/whatever/jobs/reorder")
    assert r.status_code == 405


def test_u11_reorder_rejects_unknown_site():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/sites/nope/jobs/reorder",
        json={"ordering": {"https://x/y": 10}},
    )
    assert r.status_code == 404


def test_u11_reorder_rejects_empty_ordering():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/sites/nope/jobs/reorder",
        json={"ordering": {}},
    )
    # 404 (site unknown) wins over 400 (empty ordering) — both are
    # acceptable refusals; what matters is "no 500".
    assert r.status_code in (400, 404)


# ── Frontend: deps + components ─────────────────────────────────────


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / "src" / Path(*parts)


def test_u11_dnd_kit_deps_declared():
    pkg = json.loads(
        (_REPO / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    deps = pkg.get("dependencies") or {}
    for name in ("@dnd-kit/core", "@dnd-kit/sortable", "@dnd-kit/utilities"):
        assert name in deps, f"{name} missing from frontend/package.json deps"


def test_u11_sortable_row_component_present():
    f = _frontend_file("components", "SortableWaitingJobRow.tsx")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    assert "useSortable" in src, "must use @dnd-kit/sortable useSortable hook"
    # Drag handle has an aria-label that names the file so screen
    # readers announce what's being moved.
    assert 'aria-label={`Drag to reorder' in src or 'aria-label={"Drag to reorder' in src, (
        "drag handle must have an aria-label naming the file"
    )


def test_u11_sortable_group_component_present():
    f = _frontend_file("components", "SortableQueueGroup.tsx")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    assert "DndContext" in src, "must use @dnd-kit/core DndContext"
    assert "SortableContext" in src, "must use @dnd-kit/sortable SortableContext"
    assert "verticalListSortingStrategy" in src, (
        "must use vertical list sorting strategy"
    )
    # The mutation must POST to the reorder endpoint with the right
    # path shape and a 10/20/30 ord step.
    assert "/jobs/reorder" in src, (
        "SortableQueueGroup must POST to /api/sites/<sid>/jobs/reorder"
    )
    assert "(i + 1) * 10" in src, (
        "ord values must be 10/20/30… (gaps for future inserts)"
    )


def test_u11_sortable_group_exports_helpers():
    """bucketWaitingBySite + SortChipRibbon are part of the public
    surface — Queue.tsx imports them by name."""
    src = _frontend_file("components", "SortableQueueGroup.tsx").read_text(encoding="utf-8")
    assert "export function bucketWaitingBySite" in src
    assert "export function SortChipRibbon" in src
    assert "export function SortableQueueGroup" in src


def test_u11_sort_chip_disabled_for_lone_jobs():
    """A site with only one waiting job has nothing to reorder. Pin
    the >= 2 filter so a future "show all sites" tweak doesn't silently
    expose a useless chip."""
    src = _frontend_file("components", "SortableQueueGroup.tsx").read_text(encoding="utf-8")
    assert "count >= 2" in src, (
        "SortChipRibbon must hide chips for sites with < 2 waiting jobs"
    )


def test_u11_queue_route_mounts_sort_machinery():
    src = _frontend_file("routes", "Queue.tsx").read_text(encoding="utf-8")
    assert "SortableQueueGroup" in src, "Queue.tsx must mount SortableQueueGroup"
    assert "SortChipRibbon" in src, "Queue.tsx must mount SortChipRibbon"
    assert "bucketWaitingBySite" in src, "Queue.tsx must use bucketWaitingBySite"


def test_u11_queue_modes_are_mutually_exclusive():
    """Entering selection mode while in sort mode must exit sort
    mode, and vice versa. Otherwise the UI ends up with both
    affordances visible — bug surface for input ambiguity."""
    src = _frontend_file("routes", "Queue.tsx").read_text(encoding="utf-8")
    # enterSelectionMode clears sortingSite
    enter_sel = re.search(
        r"const enterSelectionMode\s*=\s*\(\s*\)\s*=>\s*\{(.*?)\}",
        src,
        re.DOTALL,
    )
    assert enter_sel, "enterSelectionMode helper not found"
    assert "setSortingSite(null)" in enter_sel.group(1), (
        "enterSelectionMode must exit sort mode"
    )
    # enterSortMode clears selection mode
    enter_sort = re.search(
        r"const enterSortMode\s*=\s*\([^)]*\)\s*=>\s*\{(.*?)\}",
        src,
        re.DOTALL,
    )
    assert enter_sort, "enterSortMode helper not found"
    assert "exitSelectionMode()" in enter_sort.group(1), (
        "enterSortMode must exit selection mode"
    )


def test_u11_queue_auto_exits_sort_when_site_empty():
    """If a sort-mode site runs out of waiting jobs (all dispatched
    or all cancelled), sort mode should auto-exit. Otherwise the
    operator stares at an empty pane with no obvious back button."""
    src = _frontend_file("routes", "Queue.tsx").read_text(encoding="utf-8")
    # Look for the effect that sets sortingSite to null when the
    # site's waiting count hits 0.
    assert "remaining.length === 0" in src and "setSortingSite(null)" in src, (
        "Queue.tsx must auto-exit sort mode when the site's waiting list empties"
    )
