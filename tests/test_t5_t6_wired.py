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
   the SPA's PRODUCT source outside lib/api-client.ts — every mutation must
   ride a wrapper that carries X-CSRF-Token (the SiteDetail raw DELETE was
   the one stray, fixed this cut). "Product source" excludes *.test.ts(x)
   and *.spec.ts(x); see _SPEC_FILE_RE for why that population, and for the
   guards that keep the exclusion from becoming a hole of its own.

Gating pins: retention REAL apply is destructive-tier ("APPLY RETENTION");
kill-switch CLEAR is destructive-tier ("CLEAR KILL") — v3.66.209 converted
typed tokens to yes/no dialogs with No as the default; nothing one-click on
Maintenance/Integrations.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import ast
import re
import tempfile
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


# ── population rule for the SPA product scanner ─────────────────────
#
# A SCANNER'S POPULATION IS ITS DENOMINATOR, IN BOTH DIRECTIONS. The gate below
# asks a question about DEPLOYED behaviour: a raw state-changing fetch() ships
# to a browser without X-CSRF-Token and 403s on a real cookie-session
# deployment. A Vitest spec never ships. It stubs global fetch and asserts
# against the stub, so a raw mutating fetch inside one is a NEGATIVE CONTROL,
# not a vulnerability -- and frontend/src/lib/api-client.csrf.test.ts contains
# exactly that: proof that a bare fetch is observably tokenless, which is what
# makes the token assertions beside it mean anything.
#
# This is the same population defect v3.66.1217 fixed in
# tools/gui_parity_inventory.py, where rglob("*.ts*") let a FIXTURE vouch for an
# endpoint no product code called. Same glob, same over-inclusion, opposite
# consequence: there a spec manufactured a pass, here it manufactured a failure.
# The rule is deliberately IDENTICAL to that one, and
# test_the_population_rule_matches_the_parity_inventory fails if the two drift.
#
# THE NARROWING IS ITSELF A HOLE UNLESS GUARDED, so it is:
#   * both halves of the population proven nonzero on the real tree (a rule that
#     quietly matched everything would empty the scan and pass forever);
#   * a planted product offender still flagged by the same scan that ignores a
#     planted spec;
#   * no product module may IMPORT a spec module, which is the only route by
#     which shipped code could hide behind the .test. suffix.
_SPEC_FILE_RE = re.compile(r"\.(test|spec)\.tsx?$")

_MUTATING_FETCH_RE = re.compile(
    r"fetch\([^;]{0,400}?method:\s*[\"'](POST|PUT|PATCH|DELETE)[\"']",
    re.S)

_SPEC_IMPORT_RE = re.compile(
    r"""(?:from|import\()\s*["'][^"']*\.(?:test|spec)["']""")


def _scan_raw_mutating_fetch(src_root):
    """(offenders, scanned, excluded) for one SPA source tree.

    `offenders` are product files issuing a raw state-changing fetch();
    `scanned` and `excluded` are the two halves of the population, returned so
    callers can assert both are nonzero instead of trusting the rule.
    """
    src_root = Path(src_root)
    offenders, scanned, excluded = [], [], []
    for f in sorted(src_root.rglob("*.ts*")):
        rel = f.relative_to(src_root).as_posix()
        if _SPEC_FILE_RE.search(f.name):
            excluded.append(rel)
            continue
        scanned.append(rel)
        if rel == "lib/api-client.ts":
            continue                      # this module IS the wrapper
        if _MUTATING_FETCH_RE.search(f.read_text(encoding="utf-8",
                                                 errors="replace")):
            offenders.append(rel)
    return offenders, scanned, excluded


def _inventory_spec_pattern():
    """The parity inventory's _TEST_FILE_RE pattern, read without importing it.

    tools/gui_parity_inventory.py mutates sys.path at import time, which is not
    something a gate sharing a pytest process with two dozen others should do.
    The subject here is a regex LITERAL, so ast reads it exactly. Absent or
    unparseable is a failure, not a pass: an unmeasurable rule is UNKNOWN.
    """
    src = (REPO / "tools" / "gui_parity_inventory.py").read_text(
        encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_TEST_FILE_RE"
                   for t in node.targets):
            continue
        call = node.value
        if isinstance(call, ast.Call) and call.args:
            arg = call.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
    raise AssertionError(
        "tools/gui_parity_inventory.py no longer defines _TEST_FILE_RE as a "
        "literal re.compile(...). The two population rules can no longer be "
        "compared, so re-derive this gate rather than deleting the check")


def test_no_raw_mutating_fetch_outside_api_client():
    """CSRF-across-the-board: every state-changing request the SPA SHIPS must go
    through lib/api-client.ts (which attaches X-CSRF-Token + the 403 retry).
    A raw fetch() with a mutating method anywhere else will 403 on a real
    cookie-session deployment — exactly the SiteDetail DELETE bug this cut
    fixed. Scans product source for a fetch( call whose options literal carries
    method: "POST"/"PUT"/"PATCH"/"DELETE"; see _SPEC_FILE_RE for the
    population and for the guards that keep it honest."""
    offenders, scanned, _ = _scan_raw_mutating_fetch(REPO / "frontend" / "src")
    assert scanned, "the SPA product population is empty; this gate scanned nothing"
    assert not offenders, (
        "raw state-changing fetch() outside api-client (no CSRF token): "
        + repr(offenders))


def test_the_scan_discriminates_product_from_spec():
    """RED CONTROL, on a tree built for the purpose so the real one is untouched.

    All three planted files carry the identical offending call. Exactly one must
    be reported: a rule that flagged neither would have silently disarmed the
    gate, and the assertion above would then pass over anything at all."""
    body = ('await fetch("/api/x", {\n  method: "POST",\n'
            '  body: JSON.stringify({a: 1}),\n});\n')
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "routes").mkdir()
        (root / "lib").mkdir()
        (root / "routes" / "Bad.tsx").write_text(body, encoding="utf-8")
        (root / "lib" / "thing.test.ts").write_text(body, encoding="utf-8")
        (root / "lib" / "other.spec.tsx").write_text(body, encoding="utf-8")
        offenders, scanned, excluded = _scan_raw_mutating_fetch(root)

    assert offenders == ["routes/Bad.tsx"], (
        "the scan no longer reports exactly the product offender: %r" % offenders)
    assert scanned == ["routes/Bad.tsx"], (
        "product half of the population is wrong: %r" % scanned)
    assert sorted(excluded) == ["lib/other.spec.tsx", "lib/thing.test.ts"], (
        "spec half of the population is wrong: %r" % excluded)


def test_the_real_population_is_nonzero_on_both_sides():
    """A rule that excluded everything, or nothing, would make one of the two
    claims above vacuous. Measured against the real tree, not asserted."""
    _, scanned, excluded = _scan_raw_mutating_fetch(REPO / "frontend" / "src")
    assert len(scanned) > 50, (
        "only %d product SPA files scanned; the population rule is eating "
        "product source" % len(scanned))
    assert excluded, (
        "no *.test.ts(x)/*.spec.ts(x) file was excluded, so the exclusion is "
        "untested by the real tree and this gate proves nothing about it")
    assert "lib/api-client.ts" in scanned, (
        "the wrapper itself left the population; the api-client exemption is "
        "now unreachable and the gate's shape has changed underneath it")


def test_no_product_module_imports_a_spec_module():
    """THE FAIL-CLOSED COMPLEMENT to the narrowing.

    Excluding *.test.ts(x) is only safe while the suffix means "not shipped".
    An `import ... from "./thing.test"` in product source would ship it and make
    the exclusion a laundering route -- the exact shape of defect this
    repository keeps re-finding inside its own fixes (CLAUDE.md A7)."""
    # POSITIVE CONTROL FIRST. This scan legitimately finds nothing today, and an
    # empty result from a dead regex is indistinguishable from an empty result
    # from a clean tree. So the pattern is made to fire on what it forbids, and
    # to stay silent on an ordinary import, before the verdict is trusted.
    for probe in ('import x from "./thing.test";',
                  'export * from "../lib/other.spec";',
                  'const m = await import("./thing.test");'):
        assert _SPEC_IMPORT_RE.search(probe), (
            "the spec-import guard no longer matches %r, so its zero-offender "
            "result proves nothing" % probe)
    assert not _SPEC_IMPORT_RE.search('import x from "./thing";'), (
        "the spec-import guard fires on an ordinary import; it would report "
        "every product module and stop meaning anything")

    src = REPO / "frontend" / "src"
    offenders = []
    for f in sorted(src.rglob("*.ts*")):
        if _SPEC_FILE_RE.search(f.name):
            continue
        if _SPEC_IMPORT_RE.search(f.read_text(encoding="utf-8",
                                              errors="replace")):
            offenders.append(f.relative_to(src).as_posix())
    assert not offenders, (
        "product SPA module(s) import a spec module, so the *.test.* exclusion "
        "can hide shipped code: " + repr(offenders))


def test_the_population_rule_matches_the_parity_inventory():
    """ONE definition of "is this SPA product source", in the two places that
    both need it. v3.66.1217 wrote it for tools/gui_parity_inventory.py; if that
    one changes, this scanner must change with it or the two gates begin
    disagreeing about what the SPA actually is."""
    assert _SPEC_FILE_RE.pattern == _inventory_spec_pattern(), (
        "population rules drifted: this file uses %r, "
        "tools/gui_parity_inventory.py uses %r"
        % (_SPEC_FILE_RE.pattern, _inventory_spec_pattern()))


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
