"""T5/T6/row-U governance, integrations, VPN, and CSRF contract -- EXECUTED.

WHAT THIS FILE USED TO BE (backlog row 184). It made three runtime claims by
reading source text: 30 endpoint families were "wired" when literals appeared;
the CSRF source was correct when ``fetch("/api/csrf"`` appeared; and the
security boundary was intact when a case-sensitive regex found no uppercase
``method: "POST"`` raw fetch outside api-client. Fetch case-normalizes standard
methods, so ``method: "post"`` was a live CSRF-less write that passed. The
same regex also missed whitespace before the colon. Its one-click scan had the
rows-182/185 handler hole: ``const fire = () => x.mutate(); onClick={fire}``.

WHAT IT IS NOW. Following rows 182 and 185, runtime properties delegate through
the fail-closed Vitest bridge:

* ``T5T6.wired.test.tsx`` renders the real Maintenance/Integrations/VPN routes,
  drives their controls until all 29 governance/integrations/VPN families fire,
  proves the independent 29 + /api/csrf = 30 denominator, and clicks gated
  controls before and after confirmation. A named-handler evasion fixture
  proves clicks reach that spelling too.
* ``api-client.csrf.test.ts`` executes a real apiPost and observes both the
  /api/csrf fetch and X-CSRF-Token on the outgoing write.
* ``rawFetchBoundary.test.ts`` parses the complete product population with the
  TypeScript parser. It structurally ignores comments/prose, normalizes method
  case/whitespace/underscores/quote style, resolves local options/method/Request
  indirection, fails unresolved visible shapes closed, and ships lowercase,
  spacing/mixed-case, indirection, and negative fixtures. Its docstring declares
  the residual whole-program evasion surface explicitly.
* ``T5T6.route.test.tsx`` resolves all three real App routes, supplies an
  unrelated-path negative control, and selects the command-palette item.

The three literal tests remain because their subject is genuinely textual:
``tools/gui_parity_inventory.py`` can only credit full /api/ literals. They are
floors for that scanner, not evidence of runtime wiring.

One declared floor remains: Settings.tsx must carry the /integrations tools
link. Runtime reachability and the palette path are exercised; rendering the
entire settings editor merely to select this one static tools-list Link expands
the fixture into unrelated environment/global-config state. The floor is
comment-stripped and exact, so prose cannot satisfy it. A computed Link target
is its declared residual blind spot.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
from pathlib import Path

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent

_SPEC_DENOMINATORS = {
    "src/routes/T5T6.wired.test.tsx": 9,
    "src/lib/api-client.csrf.test.ts": 3,
    "src/lib/rawFetchBoundary.test.ts": 7,
    "src/routes/T5T6.route.test.tsx": 5,
    "src/routes/T5T6.transform-control.test.tsx": 1,
}


def _strip_ts_comments(text):
    """Quote-aware // and /* */ stripper; strings/templates stay intact."""
    out = []
    i, n = 0, len(text)
    quote = None
    in_line = False
    in_block = False
    while i < n:
        char = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            if char == "\n":
                in_line = False
                out.append(char)
            i += 1
            continue
        if in_block:
            if char == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            if char == "\n":
                out.append(char)
            i += 1
            continue
        if quote:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if char == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def test_t5_full_literals_present_for_the_parity_scanner():
    """TEXTUAL SUBJECT: the parity inventory requires full /api/ literals."""
    hooks = _strip_ts_comments(
        (REPO / "frontend" / "src" / "hooks" / "useGovernance.ts").read_text(
            encoding="utf-8"))
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
    assert len(needed) == 12, "T5 literal denominator drifted"
    missing = [item for item in needed if item not in hooks]
    assert not missing, "full literals missing from useGovernance.ts: " + repr(missing)


def test_t6_full_literals_present_for_the_parity_scanner():
    """TEXTUAL SUBJECT: the parity inventory requires full /api/ literals."""
    hooks = _strip_ts_comments(
        (REPO / "frontend" / "src" / "hooks" / "useIntegrations.ts").read_text(
            encoding="utf-8"))
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
    assert len(needed) == 14, "T6 literal denominator drifted"
    missing = [item for item in needed if item not in hooks]
    assert not missing, "full literals missing from useIntegrations.ts: " + repr(missing)


def test_row_u_vpn_literals_present_for_the_parity_scanner():
    """TEXTUAL SUBJECT: row-U families stay visible to parity inventory."""
    vpn = _strip_ts_comments(
        (REPO / "frontend" / "src" / "routes" / "Vpn.tsx").read_text(
            encoding="utf-8"))
    needed = [
        "/api/vpn/kill_switch/${",
        '"/api/vpn/kill_switch/state"',
        '"/api/vpn/kill_switch/auto_recover"',
        "/api/vpn/providers/${",
        '"/api/vpn/providers"',
        '"/api/vpn/settings"',
    ]
    assert len(needed) == 6, "VPN full-literal floor drifted"
    missing = [item for item in needed if item not in vpn]
    assert not missing, "full literals missing from Vpn.tsx: " + repr(missing)


def test_t5_t6_vpn_endpoint_and_confirmation_contract_at_runtime():
    """All 29 non-CSRF families execute; one-click writes stay gated."""
    spec = "src/routes/T5T6.wired.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_csrf_token_is_sourced_and_attached_at_runtime():
    """The wrapper fetches /api/csrf and sends its token on a real apiPost."""
    spec = "src/lib/api-client.csrf.test.ts"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_no_raw_state_changing_fetch_in_product_source():
    """Parsed global absence floor plus shipped evasion and negative fixtures."""
    spec = "src/lib/rawFetchBoundary.test.ts"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t5_t6_vpn_routes_are_reachable_at_runtime():
    """Resolve real routes, negative control, and command-palette navigation."""
    spec = "src/routes/T5T6.route.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_integrations_settings_link_comment_stripped_floor():
    """DECLARED FLOOR: Settings' static tools link names /integrations exactly."""
    source = (REPO / "frontend" / "src" / "routes" / "Settings.tsx").read_text(
        encoding="utf-8")
    stripped = _strip_ts_comments(source)
    assert len(stripped) < len(source), "comment stripper removed nothing from Settings.tsx"
    anchor = '{ to: "/integrations", label: "Integrations"'
    assert stripped.count(anchor) == 1, (
        "Settings must carry exactly one live /integrations tools link; found %d"
        % stripped.count(anchor))


def test_settings_link_floor_ignores_commented_evasion_fixture():
    """EVASION FIXTURE: a commented old link cannot hide a repathed live one."""
    anchor = '{ to: "/integrations", label: "Integrations"'
    fixture = (
        '// old: { to: "/integrations", label: "Integrations" }\n'
        '{ to: "/integrations-v2", label: "Integrations" }\n'
    )
    assert fixture.count(anchor) == 1, "fixture no longer defeats the retired raw scan"
    stripped = _strip_ts_comments(fixture)
    assert stripped.count(anchor) == 0, (
        "commented /integrations link survived stripping and can manufacture green")


def test_transform_control_imports_without_asserting_runtime_behaviour():
    """Mutation control only: a semantic endpoint mutant must ESCAPE here."""
    spec = "src/routes/T5T6.transform-control.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])
