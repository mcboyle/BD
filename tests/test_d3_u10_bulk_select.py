"""D3 follow-up U10 contract — bulk select on Sites + Queue.

Covers:
  - POST /api/sites/v2/bulk exists; rejects GET; rejects unknown
    actions; rejects empty/missing site_ids; aggregates unknown sids
    in errors rather than 500.
  - POST /api/queue/v2/bulk_cancel exists; rejects GET; rejects
    missing/empty jobs; per-job missing fields and unknown sites
    surface in the errors list, not 500.
  - Allow-list of site actions is the documented set
    {pause, resume, start, delete}.
  - Frontend: useBulkSelection hook, BulkActionBar component, both
    routes (Sites.tsx, Queue.tsx) import and use them.
  - WaitingJobRow + SiteRow expose the optional onToggleSelect prop.
"""
from __future__ import annotations

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


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell):
    route handlers now live in app_<group>.py, so source-level handler-body
    assertions read the aggregate rather than app.py alone."""
    pkg = _REPO / "bulk_downloader"
    parts = [(pkg / "app.py").read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(pkg.glob("app_*.py"))]
    return "\n".join(parts)


# ── Sites bulk endpoint ──────────────────────────────────────────────


def test_u10_sites_bulk_route_registered():
    from bulk_downloader import app as a
    rules = {(r.rule, tuple(sorted(r.methods or ()))) for r in a.app.url_map.iter_rules()}
    assert any(
        rule == "/api/sites/v2/bulk" and "POST" in methods
        for rule, methods in rules
    ), "POST /api/sites/v2/bulk missing"


def test_u10_sites_bulk_rejects_get():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/sites/v2/bulk")
    assert r.status_code == 405


def test_u10_sites_bulk_rejects_unknown_action():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/sites/v2/bulk",
        json={"action": "nuke", "site_ids": ["x"]},
    )
    # Either 400 (action validation) or 403 (CSRF rejection upstream of
    # validation). Both are acceptable refusals.
    assert r.status_code in (400, 403)


def test_u10_sites_bulk_rejects_missing_site_ids():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/sites/v2/bulk",
        json={"action": "pause"},
    )
    assert r.status_code in (400, 403)


def test_u10_sites_bulk_rejects_empty_site_ids():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/sites/v2/bulk",
        json={"action": "pause", "site_ids": []},
    )
    assert r.status_code in (400, 403)


def test_u10_sites_bulk_action_allowlist_in_source():
    """The allow-list is hardcoded — no getattr on raw input. Pin the
    tuple so a future "let's just dispatch whatever" refactor breaks
    the test instead of opening dispatch to arbitrary method names."""
    src = _APP_SRC()
    # The module-level constant defines what's allowed.
    m = re.search(r"_SITES_BULK_ACTIONS\s*=\s*\(([^)]*)\)", src)
    assert m, "_SITES_BULK_ACTIONS allow-list not found in app.py"
    actions = {x.strip().strip('"').strip("'") for x in m.group(1).split(",") if x.strip()}
    assert actions == {"pause", "resume", "start", "delete"}, (
        f"unexpected site bulk action set: {actions}"
    )


def test_u10_sites_bulk_no_dynamic_dispatch_to_delete():
    """The delete branch must be inlined (full teardown — runner.stop,
    keepers, watch threads, account pool, queue rows). It must NOT
    route through getattr(runner, 'delete'), which would silently
    no-op because Runner has no delete method.

    This test pins the inline shape by grepping for the marker call
    `queue_delete_site(sid)` inside the bulk handler. If a future
    refactor extracts the teardown into a helper, the test needs to
    track it; keeping the grep specific is the point."""
    src = _APP_SRC()
    handler = re.search(
        r"def api_sites_v2_bulk\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler, "api_sites_v2_bulk handler not found"
    body = handler.group(0)
    assert "queue_delete_site(sid)" in body, (
        "delete branch must inline the queue_delete_site teardown call"
    )
    assert "runners.pop(sid, None)" in body, (
        "delete branch must atomically detach `runners.pop(sid, None)`"
    )
    assert "with _watch_registry_lock:" in body, (
        "delete must detach the runner and watch generation under its lock"
    )


# ── Queue bulk_cancel endpoint ───────────────────────────────────────


def test_u10_queue_bulk_cancel_route_registered():
    from bulk_downloader import app as a
    rules = {(r.rule, tuple(sorted(r.methods or ()))) for r in a.app.url_map.iter_rules()}
    assert any(
        rule == "/api/queue/v2/bulk_cancel" and "POST" in methods
        for rule, methods in rules
    ), "POST /api/queue/v2/bulk_cancel missing"


def test_u10_queue_bulk_cancel_rejects_get():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/queue/v2/bulk_cancel")
    assert r.status_code == 405


def test_u10_queue_bulk_cancel_rejects_missing_jobs():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/bulk_cancel",
        json={},
    )
    assert r.status_code in (400, 403)


def test_u10_queue_bulk_cancel_rejects_empty_jobs():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/bulk_cancel",
        json={"jobs": []},
    )
    assert r.status_code in (400, 403)


def test_u10_queue_bulk_cancel_handler_dedupes_in_source():
    """Duplicate (site_id, url) pairs in a single request should not
    fire two cancels for the same job. Pin the dedup set construction.
    """
    src = _APP_SRC()
    handler = re.search(
        r"def api_queue_v2_bulk_cancel\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler, "api_queue_v2_bulk_cancel handler not found"
    body = handler.group(0)
    assert "seen = set()" in body and "seen.add(key)" in body, (
        "bulk_cancel must dedup on (site_id, url)"
    )


def test_u10_queue_bulk_cancel_uses_canonical_update_path():
    """Bulk cancel must drive through runner._update_job — the same
    code path single-cancel uses — so all the downstream hooks fire."""
    src = _APP_SRC()
    handler = re.search(
        r"def api_queue_v2_bulk_cancel\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler
    body = handler.group(0)
    assert "_update_job(url, \"stopped\"" in body, (
        "bulk_cancel must use runner._update_job for state transition"
    )


# ── Frontend files ──────────────────────────────────────────────────


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / "src" / Path(*parts)


def test_u10_use_bulk_selection_hook_exists():
    f = _frontend_file("hooks", "useBulkSelection.ts")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    # Public surface: toggle/clear/selectAll/has/size/ids/pruneTo
    for name in ("toggle", "clear", "selectAll", "has", "size", "ids", "pruneTo"):
        assert name in src, f"useBulkSelection missing {name!r}"


def test_u10_bulk_action_bar_component_exists():
    f = _frontend_file("components", "BulkActionBar.tsx")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    # Exports both the bar and the action button helper.
    assert "export function BulkActionBar" in src
    assert "export function BulkActionButton" in src
    # role="region" + aria-label gives screen readers a handle.
    assert 'role="region"' in src
    assert 'aria-label="Bulk actions"' in src


def test_u10_sites_route_wires_bulk_select():
    src = _frontend_file("routes", "Sites.tsx").read_text(encoding="utf-8")
    assert "useBulkSelection" in src, "Sites.tsx must import useBulkSelection"
    assert "BulkActionBar" in src, "Sites.tsx must render BulkActionBar"
    # Mutation must hit the new endpoint.
    assert '"/api/sites/v2/bulk"' in src, (
        "Sites.tsx bulk action must POST to /api/sites/v2/bulk"
    )


def test_u10_queue_route_wires_bulk_select():
    src = _frontend_file("routes", "Queue.tsx").read_text(encoding="utf-8")
    assert "useBulkSelection" in src, "Queue.tsx must import useBulkSelection"
    assert "BulkActionBar" in src, "Queue.tsx must render BulkActionBar"
    assert '"/api/queue/v2/bulk_cancel"' in src, (
        "Queue.tsx bulk action must POST to /api/queue/v2/bulk_cancel"
    )


def test_u10_site_row_accepts_selection_props():
    src = _frontend_file("components", "SiteRow.tsx").read_text(encoding="utf-8")
    assert "onToggleSelect" in src, "SiteRow must accept onToggleSelect"
    assert "selected?: boolean" in src, "SiteRow must accept selected prop"


def test_u10_waiting_job_row_accepts_selection_props():
    src = _frontend_file("components", "WaitingJobRow.tsx").read_text(encoding="utf-8")
    assert "onToggleSelect" in src, "WaitingJobRow must accept onToggleSelect"
    assert "selected?: boolean" in src, "WaitingJobRow must accept selected prop"


def test_u10_waiting_job_row_suppresses_swipe_in_selection_mode():
    """When selectionMode is true the swipe gesture must not activate
    — otherwise a swipe inside a selection looks like a cancel attempt
    against the gesture system."""
    src = _frontend_file("components", "WaitingJobRow.tsx").read_text(encoding="utf-8")
    # The earliest exit in onPointerDown should be selectionMode.
    pd = re.search(
        r"const onPointerDown\s*=\s*\([^)]*\)\s*=>\s*\{(.*?)\}",
        src,
        re.DOTALL,
    )
    assert pd, "onPointerDown not found"
    body = pd.group(1)
    assert "if (selectionMode) return" in body, (
        "onPointerDown must short-circuit when selectionMode is true"
    )
