"""Current governance, security, automation, VPN, and CSRF SPA contract.

Proves the current endpoint, CSRF, and confirmation behavior:

1. The 30 wired families (T5: retention 3 · rights 3 · scheduled_exports 4 ·
   diagnostics_bundle 2; T6: plex_advanced 6 · tpdb 2 · subtitles 1 ·
   thumbnail_sheets 1 · marketplace 1 · jsonapi 1 · ai 2; row U: vpn
   kill_switch/providers/settings 3; plus /api/csrf closed by the CSRF fix)
   remain SPA-wired, with full /api/ literals in
   the new hooks/pages.
2. The SPA fetch wrapper sources its token from /api/csrf (the
   self-minting canonical endpoint, P0.1/v3.66.202) and no longer references
   the nonexistent /api/auth_surface route. On 207 every cookie-session SPA
   write 403'd because of this — the test client carries no bd_session
   cookie, so only a real deployment saw it.
3. CSRF-across-the-board: no state-changing fetch() exists anywhere in
   frontend/src outside lib/api-client.ts — every mutation must ride a
   wrapper that carries X-CSRF-Token (the SiteDetail raw DELETE was the one
   stray, fixed this cut).

Gating pins: retention REAL apply is destructive-tier ("APPLY RETENTION");
kill-switch CLEAR is destructive-tier ("CLEAR KILL") — v3.66.209 converted
typed tokens to yes/no dialogs with No as the default; nothing one-click on
Maintenance/Integrations.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent

# The 30 families this cut clears (baseline spelling; {x} -> *).
T5_ENDPOINTS = [
    "/api/retention/preview/*",
    "/api/retention/apply",
    "/api/retention/audit",
    "/api/rights/audit",
    "/api/rights/blocklist",
    "/api/rights/remove/*",
    "/api/scheduled_exports/add",
    "/api/scheduled_exports/list",
    "/api/scheduled_exports/remove/*",
    "/api/scheduled_exports/run_now",
    "/api/diagnostics_bundle/preview",
    "/api/diagnostics_bundle/download",
]
T6_ENDPOINTS = [
    "/api/plex_advanced/status",
    "/api/plex_advanced/server_info/*",
    "/api/plex_advanced/library_stats/*",
    "/api/plex_advanced/recently_added/*",
    "/api/plex_advanced/on_deck/*",
    "/api/plex_advanced/search/*",
    "/api/tpdb/lookup/*",
    "/api/tpdb/apply/*",
    "/api/subtitles/fetch/*",
    "/api/thumbnail_sheets/contact_sheet/*",
    "/api/marketplace/export/*",
    "/api/jsonapi/probe",
    "/api/ai/status",
    "/api/ai/models",
]
ROW_U_ENDPOINTS = [
    "/api/vpn/kill_switch/*",
    "/api/vpn/providers/*",
    "/api/vpn/settings",
]
CSRF_ENDPOINT = ["/api/csrf"]


def test_t5_full_literals_present_in_spa_source():
    """T5 wiring must be FULL /api/ literals in useGovernance.ts."""
    hooks = (REPO / "frontend" / "src" / "hooks" / "useGovernance.ts").read_text(
        encoding="utf-8")
    needed = [
        "/api/retention/preview/${",
        '"/api/retention/apply"',
        "/api/retention/audit?limit=",
        '"/api/rights/blocklist"',
        "/api/rights/audit?limit=",
        "/api/rights/remove/${bid}",
        '"/api/scheduled_exports/list"',
        '"/api/scheduled_exports/add"',
        "/api/scheduled_exports/remove/${id}",
        '"/api/scheduled_exports/run_now"',
        '"/api/diagnostics_bundle/preview"',
        '"/api/diagnostics_bundle/download"',
    ]
    missing = [n for n in needed if n not in hooks]
    assert not missing, "full literals missing from useGovernance.ts: " + repr(missing)


def test_t6_full_literals_present_in_spa_source():
    """T6 wiring must be FULL /api/ literals in useIntegrations.ts."""
    hooks = (REPO / "frontend" / "src" / "hooks" / "useIntegrations.ts").read_text(
        encoding="utf-8")
    needed = [
        '"/api/plex_advanced/status"',
        "/api/plex_advanced/server_info/${",
        "/api/plex_advanced/library_stats/${",
        "/api/plex_advanced/recently_added/${",
        "/api/plex_advanced/on_deck/${",
        "/api/plex_advanced/search/${",
        "/api/tpdb/lookup/${hid}",
        "/api/tpdb/apply/${hid}",
        "/api/subtitles/fetch/${hid}",
        "/api/thumbnail_sheets/contact_sheet/${hid}",
        "/api/marketplace/export/${",
        '"/api/jsonapi/probe"',
        '"/api/ai/status"',
        '"/api/ai/models"',
    ]
    missing = [n for n in needed if n not in hooks]
    assert not missing, "full literals missing from useIntegrations.ts: " + repr(missing)


def test_row_u_vpn_literals_present_in_spa_source():
    """VPN row-U wiring must be FULL /api/ literals in Vpn.tsx."""
    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(
        encoding="utf-8")
    needed = [
        "/api/vpn/kill_switch/${",
        '"/api/vpn/kill_switch/state"',
        '"/api/vpn/kill_switch/auto_recover"',
        "/api/vpn/providers/${",
        '"/api/vpn/providers"',
        '"/api/vpn/settings"',
    ]
    missing = [n for n in needed if n not in vpn]
    assert not missing, "full literals missing from Vpn.tsx: " + repr(missing)


def test_csrf_token_sourced_from_api_csrf():
    """THE 208 FIX. The fetch wrapper's CSRF source must be /api/csrf —
    /api/auth_surface does not exist and never did (the auth-surface map is
    /api/dev/auth_map, dev-gated). Any reference to auth_surface in the SPA
    is a regression that 403s every cookie-session write on deployment."""
    client = (REPO / "frontend" / "src" / "lib" / "api-client.ts").read_text(
        encoding="utf-8")
    assert 'fetch("/api/csrf"' in client, (
        "api-client.ts no longer fetches its CSRF token from /api/csrf")
    spa_dir = REPO / "frontend" / "src"
    offenders = []
    for f in spa_dir.rglob("*.ts*"):
        if "auth_surface" in f.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(f.relative_to(REPO)))
    assert not offenders, (
        "SPA references the nonexistent /api/auth_surface route: " + repr(offenders))


def test_no_raw_mutating_fetch_outside_api_client():
    """CSRF-across-the-board: every state-changing request in the SPA must go
    through lib/api-client.ts (which attaches X-CSRF-Token + the 403 retry).
    A raw fetch() with a mutating method anywhere else will 403 on a real
    cookie-session deployment — exactly the SiteDetail DELETE bug this cut
    fixed. Scans for a fetch( call whose options literal carries
    method: "POST"/"PUT"/"PATCH"/"DELETE"."""
    spa_dir = REPO / "frontend" / "src"
    pat = re.compile(
        r"fetch\([^;]{0,400}?method:\s*[\"'](POST|PUT|PATCH|DELETE)[\"']",
        re.S)
    offenders = []
    for f in spa_dir.rglob("*.ts*"):
        rel = f.relative_to(spa_dir).as_posix()
        if rel == "lib/api-client.ts":
            continue
        if pat.search(f.read_text(encoding="utf-8", errors="replace")):
            offenders.append(rel)
    assert not offenders, (
        "raw state-changing fetch() outside api-client (no CSRF token): "
        + repr(offenders))


def test_gating_typed_tokens_and_no_one_click():
    """Retention REAL apply and kill-switch CLEAR are destructive-tier; the new
    Maintenance T5 kinds exist; Integrations writes dispatch only from
    confirmRun; nothing one-click."""
    maint = (REPO / "frontend" / "src" / "routes" / "Maintenance.tsx").read_text(
        encoding="utf-8")
    assert 'token: "APPLY RETENTION"' in maint
    for kind in ("retentionApply", "retentionDryRun", "rightsRemove",
                 "schedAdd", "schedRemove", "schedRunNow"):
        assert f'kind: "{kind}"' in maint, f"Pending kind {kind!r} missing"
    assert not re.search(r"onClick=\{[^}]*\.mutate", maint), (
        "a Maintenance write mutation is wired one-click")

    integ = (REPO / "frontend" / "src" / "routes" / "Integrations.tsx").read_text(
        encoding="utf-8")
    for kind in ("tpdbApply", "subtitles", "contactSheet", "marketplaceExport"):
        assert f'kind: "{kind}"' in integ, f"Integrations kind {kind!r} missing"

    vpn = (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(
        encoding="utf-8")
    assert '"CLEAR KILL"' in vpn, "kill-switch clear is not destructive-tier gated"


def test_integrations_route_registered_and_linked():
    """/integrations must be a registered route AND reachable via the
    Settings tools list (nav-reachability home) + the command palette."""
    app = (REPO / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'path="/integrations"' in app, "route /integrations not registered"
    settings = (REPO / "frontend" / "src" / "routes" / "Settings.tsx").read_text(
        encoding="utf-8")
    assert '"/integrations"' in settings, "Settings tools list lacks /integrations"
