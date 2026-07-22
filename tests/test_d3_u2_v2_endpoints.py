"""D3 U2 contract — /api/*/v2 endpoints for the React SPA.

Pins the SPA-facing shape of the six v2 endpoints:
  - /api/dashboard/v2
  - /api/dashboard/v2/sparkline
  - /api/sites/v2
  - /api/activity/v2
  - /api/queue/v2
  - /api/health/v2

These are read-mostly adapters over the existing v1 data sources;
the v1 endpoints stay unchanged so / and /m keep working. If the
shape here drifts, the SPA breaks silently — pin it.

The tests target empty / cold-start state: no runners, no rows. That's
the worst case for a malformed response (None-vs-[] mistakes, etc.).
Tests that exercise non-empty state belong in U3+ once real fixtures
land.
"""
from __future__ import annotations

import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Route registration ─────────────────────────────────────────────────


def test_d3_u2_routes_registered():
    """All 6 v2 endpoints must be registered on the Flask app. If one
    quietly disappears the SPA gets 404s instead of empty buckets;
    catch it here, not in U3 polling."""
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    expected = {
        "/api/dashboard/v2",
        "/api/dashboard/v2/sparkline",
        "/api/sites/v2",
        "/api/activity/v2",
        "/api/queue/v2",
        "/api/health/v2",
    }
    missing = expected - rules
    assert not missing, f"missing v2 routes: {sorted(missing)}"


def test_d3_u2_does_not_shadow_v1():
    """/api/dashboard, /api/sites, /api/health (the v1 endpoints) must
    keep existing. D3 is additive, not replacing — / and /m keep
    working through the existing UIs until v3.65.0."""
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    for v1 in ("/api/dashboard", "/api/sites", "/api/health"):
        assert v1 in rules, f"v1 route {v1} disappeared — D3 is additive"


# ── /api/dashboard/v2 ──────────────────────────────────────────────────


def test_d3_u2_dashboard_v2_empty_state_shape():
    """Cold-start shape with no runners. Every key must be present and
    correctly typed; empty arrays MUST be arrays, not null/None."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/dashboard/v2").get_json()
    assert body.get("ok") is True
    for k in ("attention", "by_site"):
        assert isinstance(body.get(k), list), f"{k} must be list, got {type(body.get(k))}"
        assert body[k] == [], f"{k} must be empty array in cold-start state"
    assert isinstance(body.get("today"), dict)
    for k in ("done", "running", "failed"):
        assert body["today"].get(k) == 0, f"today.{k} must be 0 cold"
    assert body.get("active_workers") == 0
    assert body.get("sites_count") == 0
    assert isinstance(body.get("ts"), int)


def test_d3_u2_1_dashboard_v2_workers_fields_present():
    """V2.1 (D3 visual redesign): /api/dashboard/v2 emits
    workers_active + workers_total alongside the existing
    active_workers field (back-compat). The SPA Home tab's
    display-mode status line reads these to render "{N} of {M}
    workers".

    Cold-start contract:
      • workers_active is an int and equals 0 (no runners, no active)
      • workers_total is None (honest empty-fleet signal — matches
        DANGER_MAP "workers_total is None on empty fleet — never the
        magic 16", same invariant as /api/widgets/data enforces in
        tests/test_u49_workers_total_honest.py)
      • active_workers (the original field) is unchanged
    """
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/dashboard/v2").get_json()
    assert body.get("ok") is True
    assert "workers_active" in body, (
        "V2.1 contract: /api/dashboard/v2 must emit workers_active "
        "(SPA Home status line consumes it)"
    )
    assert "workers_total" in body, (
        "V2.1 contract: /api/dashboard/v2 must emit workers_total "
        "(SPA Home status line renders 'N of M workers' from it)"
    )
    assert isinstance(body["workers_active"], int)
    assert body["workers_active"] == 0
    # Empty fleet → workers_total must be None, never an int.
    # See DANGER_MAP "workers_total is None (key absent) on empty
    # fleet — never the magic 16". The widget renderer pattern is to
    # null-guard on this key; the SPA's Home status line uses the
    # same None-falls-back-to-active-only logic.
    assert body["workers_total"] is None, (
        f"workers_total must be None on empty fleet (got "
        f"{body['workers_total']!r}). Substituting a magic 16 or "
        f"falling back to total_cap+1 is the v3.63.6 regression "
        f"this contract pins shut."
    )


def test_d3_u2_1_dashboard_v2_active_workers_back_compat():
    """V2.1 added two new fields but did NOT rename or remove the
    pre-existing active_workers field. Any external consumer reading
    the v2 endpoint pre-V2.1 keeps working."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/dashboard/v2").get_json()
    assert "active_workers" in body, (
        "active_workers field removed by V2.1 — back-compat broken. "
        "active_workers (sites-with-running-state) is a different "
        "metric from workers_active (concurrent download workers); "
        "keep both."
    )
    # active_workers and workers_active are semantically distinct.
    # On a cold-start with no runners both are 0; that's expected.
    # The test asserting they CAN differ in non-cold state lives in
    # an integration test once real-runners fixtures exist.


def test_d3_u2_dashboard_v2_sparkline_shape():
    """Sparkline endpoint returns history as a list, even cold."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/dashboard/v2/sparkline").get_json()
    assert body.get("ok") is True
    assert isinstance(body.get("history"), list)
    assert "current" in body
    assert isinstance(body.get("ts"), int)


# ── /api/sites/v2 ──────────────────────────────────────────────────────


def test_d3_u2_sites_v2_empty_state_shape():
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/sites/v2").get_json()
    assert body.get("ok") is True
    assert isinstance(body.get("sites"), list)
    assert body.get("sites") == []
    assert body.get("count") == 0


# ── /api/activity/v2 ───────────────────────────────────────────────────


def test_d3_u2_activity_v2_default_window_is_24h():
    """Default ?window must be 24h so a no-arg request returns a
    bounded window (not 'all', which would scan the whole history)."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/activity/v2").get_json()
    assert body.get("ok") is True
    assert body.get("window") == "24h"
    assert isinstance(body.get("items"), list)
    # In cold state: zero rows, delta = 0 (not None — 0-0 = 0)
    assert body.get("count_current_period") == 0
    assert body.get("count_prev_period") == 0
    assert body.get("delta_abs") == 0


def test_d3_u2_activity_v2_window_all_omits_delta():
    """?window=all has no previous period, so delta MUST be None
    (the SPA branches on null vs zero — '+0 vs prev period' would be
    misleading copy)."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/activity/v2?window=all").get_json()
    assert body.get("ok") is True
    assert body.get("window") == "all"
    assert body.get("count_prev_period") is None
    assert body.get("delta_abs") is None
    assert body.get("delta_pct") is None


def test_d3_u2_activity_v2_rejects_invalid_window():
    """Unknown window string returns 400. Pin the error path so the
    SPA can show a sensible message instead of a 200-with-empty-items."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/activity/v2?window=bogus")
    assert r.status_code == 400
    body = r.get_json()
    assert body.get("ok") is False


def test_d3_u2_activity_v2_accepts_canonical_windows():
    """Each canonical window resolves to a 200 with the matching field."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    for w in ("24h", "7d", "30d", "all"):
        body = c.get(f"/api/activity/v2?window={w}").get_json()
        assert body.get("ok") is True, f"window={w} failed: {body}"
        assert body.get("window") == w


# ── /api/queue/v2 ──────────────────────────────────────────────────────


def test_d3_u2_queue_v2_empty_state_shape():
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/queue/v2").get_json()
    assert body.get("ok") is True
    assert isinstance(body.get("running"), list)
    assert isinstance(body.get("waiting"), list)
    assert body.get("running") == []
    assert body.get("waiting") == []
    assert body.get("done_today_count") == 0
    assert body.get("waiting_truncated_count") == 0


# ── /api/health/v2 ─────────────────────────────────────────────────────


def test_d3_u2_health_v2_shape():
    """Health v2 is a superset of /api/health — must have version,
    db_ok, db_journal_mode, disks list, ollama dict, last_suite dict."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/health/v2")
    body = r.get_json()
    # 200 or 503 depending on whether the DB is healthy in the test
    # env. Both are valid 'ok' responses for the contract.
    assert r.status_code in (200, 503)
    assert "version" in body
    assert isinstance(body.get("uptime_s"), (int, float))
    assert isinstance(body.get("db_ok"), bool)
    assert isinstance(body.get("db_journal_mode"), str)
    assert isinstance(body.get("disks"), list)
    assert isinstance(body.get("ollama"), dict)
    assert "reachable" in body["ollama"]
    assert isinstance(body.get("last_suite"), dict)
    assert "available" in body["last_suite"]


def test_d3_u2_health_v2_uses_current_runner_counts_schema():
    """Health v2 sums the current nested pending/running runner schema."""
    from bulk_downloader import app as a
    from bulk_downloader.app_state import runners

    class _RunnerWithStatus:
        def get_status(self, *, light):
            assert light is True
            return {"counts": {"pending": 7, "running": 1}}

    runners["current"] = _RunnerWithStatus()

    body = a.app.test_client().get("/api/health/v2").get_json()

    assert body["queue_depth"] == 7
    assert body["active_downloads"] == 1


def test_d3_u2_health_v2_ollama_cache_attribute():
    """Verify the Ollama cache mechanism is wired — second request
    within 30s reuses the cached value. We check the function has
    the cache attribute after the first call; the cache itself is
    process-local but the contract is that it exists."""
    from bulk_downloader import app as a
    from bulk_downloader import app_health as ah
    c = a.app.test_client()
    c.get("/api/health/v2")
    assert hasattr(ah.api_health_v2, "_ollama_cache"), (
        "Ollama cache attribute missing — endpoint will hammer "
        "127.0.0.1:11434 on every request."
    )


# ── Avatar color determinism ───────────────────────────────────────────


def test_d3_u2_avatar_color_deterministic():
    """Same name MUST yield the same color across calls. SPA
    refetches on adaptive polling; a flickering avatar color is
    immediately visible and would be a bad first impression."""
    from bulk_downloader import app as a
    a1 = a._m2_avatar_color("WowGirls")
    a2 = a._m2_avatar_color("WowGirls")
    a3 = a._m2_avatar_color("WowGirls")
    assert a1 == a2 == a3, f"non-deterministic: {a1}, {a2}, {a3}"


def test_d3_u2_avatar_color_case_insensitive():
    """The name lookup folds case — "VideoSiteOne" and "videositeone"
    yield the same color (matches the mockup behaviour where the
    avatar follows the brand, not the casing)."""
    from bulk_downloader import app as a
    assert a._m2_avatar_color("VideoSiteOne") == a._m2_avatar_color("videositeone")


def test_d3_u2_avatar_color_returns_valid_hex():
    """Every output must be a 7-char hex string (#RRGGBB)."""
    from bulk_downloader import app as a
    for name in ["A", "ZZZZ", "1234", "", "site with spaces"]:
        c = a._m2_avatar_color(name)
        assert isinstance(c, str)
        assert len(c) == 7
        assert c.startswith("#")
        assert all(ch in "0123456789abcdefABCDEF" for ch in c[1:])


# ── Module-level invariant ─────────────────────────────────────────────


def test_d3_u2_no_module_level_work():
    """U2 helpers must be pure module-level definitions — no threads
    started, no DB queries at import time, no network probes. Same
    constraint as U1's /m2 handler. The Ollama cache is REQUEST-level
    (created on first call), NOT import-level — verify it's absent
    until /api/health/v2 has been hit."""
    # Re-import cleanly — autouse fixture already cleared sys.modules.
    from bulk_downloader import app as a
    from bulk_downloader import app_health as ah
    # The cache attribute must be absent before any request.
    assert not hasattr(ah.api_health_v2, "_ollama_cache"), (
        "_ollama_cache must not exist at import time — it would mean "
        "the endpoint probed Ollama during module load, which runs "
        "during every test import."
    )
