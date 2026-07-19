"""Phase 9.8 -- render/route QA brain (RED-first).

The deterministic logic behind the screenshot/route generator: enumerate routes
from App.tsx + settings sections (no hand-maintained lists), substitute probes for
`:param`, a drift guard that every App.tsx route appears in the manifest, per-view
quality analysis (console/page errors, overflow, missing h1/purpose/primary-action),
domain-ownership validation, and a deterministic index.html builder. The Playwright
render loop lives in the dev harness; this module is what it (and these tests) call.
"""

from bulk_downloader import render_qa


_APP = '''
  <Routes>
    <Route path="/" element={<Home />} />
    <Route path="/dashboard" element={<Dashboard />} />
    <Route path="/sites/:siteId" element={<SiteDetail />} />
    <Route path="/settings" element={<Settings />} />
    <Route path="*" element={<NotFound />} />
  </Routes>
'''

_SCHEMA = '''
export const SETTINGS_SECTIONS: SettingsSection[] = [
  "Downloads",
  "AI assist",
  "Advanced",
];
'''


# ── enumeration ──────────────────────────────────────────────────────────
def test_enumerate_routes_skips_splat():
    routes = render_qa.enumerate_routes(_APP)
    assert "/" in routes and "/dashboard" in routes and "/sites/:siteId" in routes
    assert "*" not in routes
    assert len(routes) == 4


def test_enumerate_routes_real_app_count():
    # against the real App.tsx in the tree, count must match (minus the splat)
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    txt = open(os.path.join(here, "frontend/src/App.tsx")).read()
    routes = render_qa.enumerate_routes(txt)
    raw = txt.count('path="')
    assert len(routes) == raw - 1        # exactly one splat "*" excluded
    assert "*" not in routes


def test_resolve_url_substitutes_param():
    assert render_qa.resolve_url("/sites/:siteId") == "/sites/1"
    assert render_qa.resolve_url("/") == "/"
    assert render_qa.resolve_url("/sites/:siteId/inspect") == "/sites/1/inspect"


def test_settings_sections_and_anchors():
    secs = render_qa.enumerate_settings_sections(_SCHEMA)
    assert secs == ["Downloads", "AI assist", "Advanced"]
    anchors = render_qa.settings_anchor_urls(secs)
    assert "/settings#downloads" in anchors
    assert "/settings#ai-assist" in anchors


def test_slugify():
    assert render_qa.slugify("AI assist") == "ai-assist"
    assert render_qa.slugify("Tools & operations") == "tools-operations"


# ── drift guard ──────────────────────────────────────────────────────────
def test_drift_check_flags_missing_route():
    expected = render_qa.expected_manifest_routes(_APP)
    manifest = list(expected - {"/dashboard"})
    res = render_qa.drift_check(expected, manifest)
    assert res["ok"] is False
    assert "/dashboard" in res["missing"]


def test_drift_check_ok_when_covered():
    expected = render_qa.expected_manifest_routes(_APP)
    res = render_qa.drift_check(expected, list(expected))
    assert res["ok"] is True
    assert res["missing"] == []


# ── per-view analysis ────────────────────────────────────────────────────
def test_analyze_view_flags_seeded_errors_and_overflow():
    flags = render_qa.analyze_view({
        "console_errors": 2, "page_errors": 1, "failed_requests": 1,
        "horizontal_overflow_px": 30, "h1": "Dashboard",
        "purpose_present": True, "primary_action_present": True,
    })
    assert "console_error" in flags
    assert "page_error" in flags
    assert "failed_request" in flags
    assert "horizontal_overflow" in flags


def test_analyze_view_flags_missing_h1_purpose_primary():
    flags = render_qa.analyze_view({
        "console_errors": 0, "page_errors": 0, "failed_requests": 0,
        "horizontal_overflow_px": 0, "h1": "",
        "purpose_present": False, "primary_action_present": False,
    })
    assert "missing_h1" in flags
    assert "missing_purpose" in flags
    assert "missing_primary_action" in flags


def test_analyze_view_clean_has_no_flags():
    flags = render_qa.analyze_view({
        "console_errors": 0, "page_errors": 0, "failed_requests": 0,
        "horizontal_overflow_px": 0, "h1": "Home",
        "purpose_present": True, "primary_action_present": True,
    })
    assert flags == []


# ── domain ownership ─────────────────────────────────────────────────────
def test_domain_for():
    assert render_qa.domain_for("/dashboard") == "dashboard"
    assert render_qa.domain_for("/cockpit/reports") == "cockpit"
    assert render_qa.domain_for("/sites/1") == "spa"


def test_domain_ownership_flags():
    f = render_qa.domain_ownership_flags(
        {"url": "/dashboard", "domain": "dashboard",
         "write_capability": "dangerous-action-present", "has_warning_treatment": False})
    assert "dashboard_with_write" in f
    assert "action_without_warning" in f
    g = render_qa.domain_ownership_flags(
        {"url": "/cockpit/x", "domain": "cockpit",
         "write_capability": "read-only", "governance_purpose": False})
    assert "cockpit_missing_governance_purpose" in g


# ── index + mock modes ───────────────────────────────────────────────────
def test_build_index_html_contains_routes():
    manifest = [{"route": "/dashboard", "theme": "dark", "status": "ok",
                 "flags": [], "diff_score": 0.0},
                {"route": "/queue", "theme": "light", "status": "ok",
                 "flags": ["console_error"], "diff_score": 1.2}]
    html = render_qa.build_index_html(manifest)
    assert "/dashboard" in html and "/queue" in html
    assert "<html" in html.lower()


def test_mock_data_modes_constant():
    assert set(["empty", "populated", "error", "stale", "warning-heavy"]).issubset(
        set(render_qa.MOCK_DATA_MODES))
