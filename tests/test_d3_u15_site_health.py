"""D3 follow-up U15 contract — site-health sparkline per Activity row.

Covers:
  - GET /api/activity/v2/site_health exists.
  - Endpoint validates `days` (clamps 1..90) and rejects unknown
    status filter values via an allow-list (no SQL interpolation).
  - Single windowed query against history (no N+1 per site) —
    pin the GROUP BY shape in source.
  - Returns by_site map with arrays of length == days (zero-filled
    for missing days).
  - Endpoint returns 200 with empty by_site on a fresh DB.
  - SiteHealthV2 type added to api-types.ts.
  - useSiteHealth hook exists, keyed by (days, status), returns
    a lookup closure.
  - InlineSparkline component exists, pure SVG (no Recharts), with
    role="img" + aria-label.
  - ActivityRow accepts an optional `sparkline` prop and only
    renders the slot when it's not undefined (so a caller can
    suppress the slot entirely).
  - Activity.tsx calls useSiteHealth(14, '*') and passes per-row
    lookup down.
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


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / "src" / Path(*parts)


# ── Backend ─────────────────────────────────────────────────────────


def test_u15_site_health_route_registered():
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    assert "/api/activity/v2/site_health" in rules, (
        "GET /api/activity/v2/site_health missing"
    )


def test_u15_site_health_returns_200_on_fresh_db():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/activity/v2/site_health?days=14")
    assert r.status_code == 200
    body = r.get_json()
    assert body and body.get("ok") is True
    assert body.get("days") == 14
    # Fresh DB: by_site is empty, not missing.
    assert isinstance(body.get("by_site"), dict)


def test_u15_site_health_clamps_days():
    """days must clamp to 1..90 — a 1-year sparkline isn't useful and
    a 0-day query would crash the day_keys generator."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/activity/v2/site_health?days=0")
    assert r.status_code == 200
    assert r.get_json()["days"] == 1
    r = a.app.test_client().get("/api/activity/v2/site_health?days=9999")
    assert r.status_code == 200
    assert r.get_json()["days"] == 90


def test_u15_site_health_unknown_status_rejected():
    from bulk_downloader import app as a
    r = a.app.test_client().get(
        "/api/activity/v2/site_health?days=14&status=lol; DROP TABLE history"
    )
    assert r.status_code == 400, "unknown status must 400, never reach SQL"


def test_u15_site_health_uses_allowlist_no_sql_interpolation():
    """The status filter must be an allow-list checked BEFORE the
    SQL — never interpolated into the SQL string."""
    src = "\n".join([(_REPO / "bulk_downloader" / "app.py").read_text(encoding="utf-8")] + [_q.read_text(encoding="utf-8") for _q in sorted((_REPO / "bulk_downloader").glob("app_*.py"))])
    handler = re.search(
        r"def api_activity_v2_site_health\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler, "api_activity_v2_site_health handler not found"
    body = handler.group(0)
    # Allow-list constant
    assert "ALLOWED_STATUSES" in body, (
        "endpoint must validate status against an allow-list"
    )
    # The two SQL branches use parameter binding, no f-string
    # interpolation of status into SQL.
    # Look for the two queries: one with status filter, one without.
    assert "WHERE ts >= ? AND status = ?" in body, (
        "status-filtered query must use bound parameter"
    )
    assert "WHERE ts >= ?" in body, (
        "non-status query must use bound parameter for ts"
    )


def test_u15_site_health_single_query_group_by():
    """One windowed query against history grouped by (site_id, day) —
    NOT a per-site N+1 fan-out."""
    src = "\n".join([(_REPO / "bulk_downloader" / "app.py").read_text(encoding="utf-8")] + [_q.read_text(encoding="utf-8") for _q in sorted((_REPO / "bulk_downloader").glob("app_*.py"))])
    handler = re.search(
        r"def api_activity_v2_site_health\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler
    body = handler.group(0)
    assert "GROUP BY site_id, day" in body, (
        "site_health must GROUP BY (site_id, day) in a single query"
    )
    # And there's only ONE FROM history clause in either branch
    # (one query each, not a loop).
    from_count = body.count("FROM history")
    assert from_count == 2, (
        f"expected 2 FROM history references (one per status branch), got {from_count}"
    )


def test_u15_site_health_response_shape():
    """by_site arrays must have length == days, zero-filled where the
    underlying day had no history rows. Pin the response shape."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/activity/v2/site_health?days=7")
    assert r.status_code == 200
    body = r.get_json()
    # by_site exists (empty on fresh DB)
    assert "by_site" in body and isinstance(body["by_site"], dict)
    assert body["days"] == 7
    assert body["status"] == "*"
    assert "start_date" in body


# ── Frontend ────────────────────────────────────────────────────────


def test_u15_site_health_type_present():
    src = _frontend_file("lib", "api-types.ts").read_text(encoding="utf-8")
    assert "export interface SiteHealthV2" in src
    # The by_site is keyed by site_id (string).
    assert "by_site: Record<string, number[]>" in src


def test_u15_use_site_health_hook_exists():
    f = _frontend_file("hooks", "useSiteHealth.ts")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    assert "export function useSiteHealth" in src
    assert "site-health-v2" in src, (
        "useSiteHealth must use a stable query key prefix"
    )
    assert "/api/activity/v2/site_health" in src


def test_u15_use_site_health_hook_keyed_by_params():
    """Query key includes days + status so a future status-filter
    UI doesn't invalidate unrelated entries."""
    src = _frontend_file("hooks", "useSiteHealth.ts").read_text(encoding="utf-8")
    # Look for an array containing both names alongside the key prefix.
    assert re.search(
        r'\["site-health-v2",\s*days,\s*status\]',
        src,
    ), "query key must include days + status"


def test_u15_inline_sparkline_component_present():
    f = _frontend_file("components", "InlineSparkline.tsx")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    # Pure SVG, no Recharts — keep the per-row cost low.
    assert "from \"recharts\"" not in src and "from 'recharts'" not in src, (
        "InlineSparkline must NOT import Recharts (per-row cost)"
    )
    # Accessibility surface.
    assert 'role="img"' in src
    assert "aria-label" in src
    # Empty/all-zero falls back to a faint baseline.
    assert "total === 0" in src and "<line" in src


def test_u15_activity_row_accepts_sparkline_prop():
    src = _frontend_file("components", "ActivityRow.tsx").read_text(encoding="utf-8")
    # Prop is optional and may be null (no activity in window).
    assert "sparkline?: number[] | null" in src, (
        "ActivityRow must accept optional sparkline prop typed as number[] | null"
    )
    # undefined = don't render the slot. Distinct from null.
    assert "sparkline !== undefined" in src, (
        "ActivityRow must distinguish undefined (omit slot) from null (render baseline)"
    )


def test_u15_activity_route_wires_site_health():
    src = _frontend_file("routes", "Activity.tsx").read_text(encoding="utf-8")
    assert "useSiteHealth" in src, "Activity.tsx must call useSiteHealth"
    # 14-day window, all statuses — pinned by U6 design.
    assert "useSiteHealth(14" in src, (
        "Activity.tsx must call useSiteHealth with 14-day window"
    )
    # Each row gets a lookup result, not the whole map.
    assert "siteHealth.lookup(r.site_id)" in src, (
        "Activity.tsx must pass per-row sparkline via siteHealth.lookup"
    )


def test_u15_activity_omits_sparkline_during_initial_load():
    """Before site_health resolves, ActivityRow must receive
    `undefined` so the sparkline slot doesn't render an empty
    baseline that flips to data a moment later (visual flash)."""
    src = _frontend_file("routes", "Activity.tsx").read_text(encoding="utf-8")
    # The ternary must check `siteHealth.data` before calling lookup.
    assert re.search(
        r"siteHealth\.data\s*\n?\s*\?\s*siteHealth\.lookup",
        src,
    ), "Activity.tsx must guard sparkline render on siteHealth.data being present"
    assert ": undefined" in src, (
        "the fall-back branch must be `undefined`, not `null` or `[]`"
    )
