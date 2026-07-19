"""T1 read-only dashboard tranche — migration pins (v3.66.205).

Proves the 13 legacy-only read endpoints are now SPA-wired (they drop out
of the legacy_parity legacy-only set) and the dashboard route + its inbound
nav link exist. RED on pristine v3.66.204 (none of these were wired yet).

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The 13 legacy-only read endpoints T1 ports into the SPA /dashboard route.
T1_ENDPOINTS = [
    "/api/dashboard",
    "/api/stats",
    "/api/stats/bandwidth",
    "/api/stats/timeline",
    "/api/hourly_stats",
    "/api/capacity",
    "/api/status",
    "/api/session_status",
    "/api/health/checklist",
    "/api/widgets/all",
    "/api/weather",
    "/api/changelog",
    "/api/route_urls",
]


def _load_legacy_parity():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_13_dashboard_endpoints_are_spa_wired():
    """None of the 13 T1 endpoints may remain in the legacy-only set —
    each must be reachable from SPA source as a full /api/ literal."""
    lp = _load_legacy_parity()
    legacy_only = set(lp.measure()["legacy_only"])
    still_legacy = [ep for ep in T1_ENDPOINTS if ep in legacy_only]
    assert not still_legacy, (
        "T1 endpoints still legacy-only (not SPA-wired): " + repr(still_legacy))


def test_dashboard_endpoints_use_full_literals_in_hook():
    """The wiring must use FULL '/api/…' string literals (not a concatenated
    base var) or the scanner won't credit them. Pin the hook source."""
    hook = (REPO / "frontend" / "src" / "hooks" / "useDashboardData.ts").read_text()
    missing = [ep for ep in T1_ENDPOINTS if f'"{ep}"' not in hook]
    assert not missing, f"hook missing full literals for: {missing}"


def test_dashboard_route_registered_and_lazy():
    """/dashboard is mounted in App.tsx and loaded via React.lazy (the first
    route-level code-split route)."""
    app = (REPO / "frontend" / "src" / "App.tsx").read_text()
    assert 'path="/dashboard"' in app, "/dashboard route not registered"
    assert "lazy(() => import" in app and "Dashboard" in app, (
        "Dashboard not lazily imported (code-splitting pattern)")


def test_dashboard_has_inbound_nav_link():
    """nav_reachability requires an inbound reference outside the route file
    and App.tsx — the command palette provides it."""
    palette = (REPO / "frontend" / "src" / "components" /
               "CommandPalette.tsx").read_text()
    assert 'go("/dashboard")' in palette, (
        "command palette missing /dashboard nav entry")


def test_ratchet_baseline_committed_at_106():
    """The shrunk baseline is committed (ratchet moved with the tranche).
    v3.66.207: converted to MONOTONIC (<=) — later tranches legitimately
    shrink the baseline below 106; it may never grow past it. (The == pin
    failed on-stash at 206 after T2 shrank 106 -> 94.)"""
    import json
    base = json.loads(
        (REPO / "reports" / "legacy_parity_baseline.json").read_text())
    assert base["legacy_only_count"] <= 106, (
        f"baseline grew past the T1 ceiling of 106: {base['legacy_only_count']}")
