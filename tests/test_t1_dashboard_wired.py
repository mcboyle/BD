"""Current dashboard SPA contract.

Proves the 13 read endpoints remain wired and the dashboard route plus its
inbound navigation link exist.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent

# The 13 read endpoints consumed by the SPA /dashboard route.
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
