"""Current history, logs, and saved-search SPA contract.

Proves the 12 endpoint families remain SPA-wired, the /history route
is lazy-loaded with an inbound nav link, the four confirm-gated writes go
through the typed/one-step confirm dialog (never one-click), and the
write paths never dispatch directly from a page click.

run_tests.py conventions: zero-arg test functions; repo root from
__file__; no pytest builtins.
"""
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent

# The 12 endpoint families consumed by the SPA /history route.
T2_ENDPOINTS = [
    "/api/history",
    "/api/history/vacuum",
    "/api/session_history",
    "/api/events_all",
    "/api/ui_events",
    "/api/ui_events/download",
    "/api/logs/tail",
    "/api/logs/clear",
    "/api/search",
    "/api/saved_searches",
    "/api/saved_searches/digest",
    "/api/saved_searches/*",          # DELETE /api/saved_searches/{id}
]


def test_t2_full_literals_present_in_spa_source():
    """The wiring must be FULL /api/ string literals in frontend/src (the
    scanner cannot credit concatenated base vars). Checks the hook file
    carries every literal the tranche claims."""
    hooks = (REPO / "frontend" / "src" / "hooks" / "useHistoryData.ts").read_text(
        encoding="utf-8")
    needed = [
        '"/api/history/vacuum"',
        "/api/session_history?",
        "/api/events_all?",
        "/api/logs/tail?",
        '"/api/logs/clear"',
        "/api/search?q=",
        '"/api/saved_searches"',
        "/api/saved_searches/${id}",
        "/api/saved_searches/digest?",
        '"/api/ui_events"',
        '"/api/ui_events/download"',
    ]
    missing = [n for n in needed if n not in hooks]
    assert not missing, "full literals missing from useHistoryData.ts: " + repr(missing)
    # /api/history with the querystring template
    assert "`/api/history?${qs}`" in hooks, "/api/history literal missing"


def test_history_route_is_lazy_with_nav_link():
    """/history mounts via React.lazy (route-level code-splitting, the
    Phase 2 pattern) and has an inbound link from the command palette
    (nav_reachability inbound-link contract; tab bar stays frozen at 5)."""
    app_tsx = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert re.search(
        r"const\s+History\s*=\s*lazy\(\(\)\s*=>\s*import\(\"\./routes/History\"\)\)",
        app_tsx), "History must load via React.lazy"
    assert 'path="/history"' in app_tsx, "/history route not mounted"
    palette = (REPO / "frontend" / "src" / "components" / "CommandPalette.tsx"
               ).read_text(encoding="utf-8")
    assert 'go("/history")' in palette, "no inbound nav link to /history"


def test_t2_writes_are_confirm_gated_never_one_click():
    """The four T2 writes (vacuum, log clear, saved-search add/delete) must
    route through the arm -> confirm dialog: the route defines a Pending
    union covering each write, destructive ones carry a destructive-tier
    token (yes/no dialog, No default — v3.66.209 dropped the typing), and
    the mutations fire only inside confirmRun (no direct onClick mutate)."""
    route = (REPO / "frontend" / "src" / "routes" / "History.tsx").read_text(
        encoding="utf-8")
    for kind in ("vacuum", "logsClear", "savedAdd", "savedDelete"):
        assert f'kind: "{kind}"' in route, f"Pending kind {kind!r} missing"
    # destructive-tier tokens on the destructive three
    assert 'token: "VACUUM HISTORY"' in route
    assert 'token: "CLEAR LOGS"' in route
    assert "token: `DELETE ${s.id}`" in route
    # the dialog gate exists and mutations dispatch from confirmRun;
    # v3.66.209: destructive tier renders a yes/no dialog with No (cancel)
    # focused by default — no typed input remains anywhere in the route.
    assert "const confirmRun = () =>" in route
    assert "isTyped(pending)" in route, "tier seam (isTyped) removed"
    assert "autoFocus" in route, "No-default (autofocused cancel) missing"
    assert "setTyped" not in route, "typed-input machinery still present"
    # no write fires directly from a button: every onClick that touches a
    # write goes through arm(...) — assert no onClick={...mutate...} pattern.
    assert not re.search(r"onClick=\{[^}]*\.mutate", route), (
        "a write mutation is wired one-click (must go through arm/confirm)")
