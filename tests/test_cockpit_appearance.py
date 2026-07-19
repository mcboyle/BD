"""Cockpit appearance + nav-tier invariants (Phase 1a + 1.0).

Custom-runner friendly: zero-arg test functions, repo root derived from
__file__, no pytest builtins. Reads tools/cockpit_console.py as text and
asserts structural invariants — this is the gate for changes to a
DEPLOY-EXCLUDED file whose internal nav nav_reachability.py does not crawl.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"

# Themes added in 1a (live = the default :root, not a [data-theme] block).
THEME_KEYS = [
    "ocean", "forest", "tech", "galaxy", "sunset",
    "golden", "arctic", "desert", "botanical", "minimalist",
]

# Frozen baseline of every nav data-p that existed at v3.66.340, BEFORE the
# 1.0 re-bucket. The reachability invariant: 1.0 regroups but drops NONE.
BASELINE_NAV_KEYS = {
    "activity", "artifacts", "assumptions", "authority", "autmetrics",
    "autonomycenter", "autopilot", "blindspots", "campaigns", "captureintel",
    "captures", "captureyield", "collections", "compliance", "confidence",
    "console", "corpus", "coverage", "crosssitedrift", "dashboard", "debt",
    "decisionquality", "diff", "downloadexplain", "drift", "driftintel",
    "eligibility", "escalations", "exec", "family", "familyhealth",
    "familyintel", "forecasting", "governance", "govhealth", "graph",
    "guardrails", "health", "housekeeping", "impact", "impactanalysis",
    "import", "investigate", "lessons", "logindrift", "loginreview",
    "logintemplates", "mission", "missioncontrol", "narrative", "notebook",
    "notifcenter", "novnc", "opportunity", "oracle", "packet", "portfolio",
    "portfolioopp", "priority", "promotionactivity", "queue", "queueintel",
    "release", "reports", "resources", "review", "reviewexp", "reviewops",
    "reviewroi", "risk", "rollbackcenter", "run", "savedviews", "scarcity",
    "scores", "search", "settings", "shell", "similarity", "siteplaybooks",
    "sitereadiness", "sites", "stagingcandidates", "systemstatus",
    "templateautopilot", "templatereview", "timeline", "trace", "trustdecay",
    "unifiedhealth", "validationops", "videotemplates", "warehouse",
}


def _src():
    return COCKPIT.read_text(encoding="utf-8")


def _tab_keys(src):
    """Container tabs are built by tabbar(group, [[key,label],...]); the data-sub
    value is interpolated (${t[0]}) so it never appears as a literal in source.
    Read the tab keys from the array-literal arguments on each tabbar(...) line."""
    keys = set()
    for line in src.splitlines():
        if "tabbar(" in line:
            keys |= set(re.findall(r"\['([a-z0-9_]+)',", line))
    return keys


def _reachable(src):
    """A page is reachable if it has a nav/card <a data-p> OR is mounted as a
    container tab. Phase 2 moves folded pages from nav anchors to tabs."""
    nav = set(re.findall(r'<a data-p="([a-z0-9_]+)"', src))
    return nav | _tab_keys(src)


# ── 1a — theme tokens + picker + persistence ────────────────────────────────

def test_theme_blocks_present():
    src = _src()
    for k in THEME_KEYS:
        assert f'[data-theme="{k}"]' in src, f"missing theme block: {k}"


def test_theme_blocks_define_core_tokens():
    """Each [data-theme] block must set the structural tokens, else the skin
    is half-applied and a theme would inherit live's colors."""
    import re
    blocks = re.findall(r'\[data-theme="[a-z]+"\]\{([^}]*)\}', _src())
    assert len(blocks) >= len(THEME_KEYS), "fewer theme blocks than themes"
    for body in blocks:
        for tok in ("--bg:", "--surface:", "--ink:", "--primary:"):
            assert tok in body, f"theme block missing {tok}: {body[:60]}"


def test_theme_persisted_and_picker_present():
    src = _src()
    assert "bd_cockpit_theme" in src, "theme not persisted to localStorage"
    assert 'id="theme_sel"' in src, "no theme picker control (#theme_sel)"


def test_layout_toggle_not_regressed():
    """1a must not break the pre-existing layout toggle."""
    assert "bd_cockpit_layout" in _src(), "layout toggle persistence lost"


# ── 1.0 — three tiers, zero pages dropped ───────────────────────────────────

def test_nav_tiers_present():
    src = _src()
    for tier in ("everyday", "advanced", "system"):
        assert f'data-tier="{tier}"' in src, f"missing nav tier: {tier}"
    # Advanced + System are collapsible drawers.
    assert 'class="navdrawer"' in src or "navdrawer" in src, "no drawer markup"


def test_nav_reachability_invariant_no_page_dropped():
    """Every data-p that existed before 1.0 still exists after the re-bucket."""
    keys = _reachable(_src())
    missing = BASELINE_NAV_KEYS - keys
    assert not missing, f"a page became unreachable (regression): {sorted(missing)}"


# ── 1b — layout menu + Tier-A layouts ───────────────────────────────────────

def test_layout_menu_present():
    src = _src()
    assert 'id="layout_sel"' in src, "layout toggle not upgraded to a menu (#layout_sel)"
    for v in ("'rail'", "'mode'", "'miller'", "'bottombar'"):
        assert v in src, f"layout option missing: {v}"


def test_layout_css_present():
    src = _src()
    for cls in (".app.rail", ".app.mode", ".app.miller", ".app.bottombar"):
        assert cls in src, f"layout CSS missing: {cls}"


# ── 1c — Settings appearance section + tier segmenter ───────────────────────

def test_tier_segmenter_present():
    assert 'id="tierseg"' in _src(), "mode/miller tier segmenter missing (#tierseg)"


def test_settings_appearance_section():
    src = _src()
    assert 'id="s_layout_sel"' in src and 'id="s_theme_sel"' in src, \
        "Settings page missing Appearance controls"


# ── 2.1 — Home container (cards landing, Scores rollup, default landing) ─────

def test_home_container_present():
    src = _src()
    assert "PAGES.home=" in src, "no Home container renderer (PAGES.home)"
    assert '<a data-p="home"' in src, "no Home nav entry"


def test_home_is_default_landing():
    src = _src()
    assert "go('home')" in src, "boot does not land on Home"
    assert '<a data-p="home" class="on">' in src, "Home not marked active by default"


def test_home_carries_scores_rollup():
    """Q2 sign-off: Home has a Scores rollup card linking into Insights > Scores."""
    import re
    m = re.search(r'PAGES\.home=.*?\n\};', _src(), re.S)
    assert m, "PAGES.home block not found"
    assert "card('scores'" in m.group(0), "Home missing the Scores rollup card"


def test_mission_still_reachable_after_home():
    """Home folds Mission Control but must not orphan it."""
    assert '<a data-p="mission"' in _src(), "Mission Control nav entry lost"


# ── 2.2-2.5 — tabbed containers + redirects ─────────────────────────────────

def test_phase2_containers_present():
    src = _src()
    for k in ("capturesc", "templatesc", "reviewc"):
        assert f"PAGES.{k}=" in src, f"missing container renderer PAGES.{k}"
        assert f'<a data-p="{k}"' in src, f"missing nav entry for {k}"


def test_folded_pages_mounted_as_tabs():
    keys = _tab_keys(_src())
    need = {"captures","autopilot","captureintel","queue","novnc","sitereadiness",
            "templatereview","videotemplates","templateautopilot","stagingcandidates","missioncontrol","logintemplates",
            "review","packet","reviewroi","escalations","reviewexp","loginreview","queueintel","reviewops"}
    missing = need - keys
    assert not missing, f"folded pages not mounted as tabs: {sorted(missing)}"


def test_old_routes_redirect():
    src = _src()
    assert "const REDIRECT=" in src, "no redirect map (2.5)"
    for k in ("autopilot", "templatereview", "reviewroi"):
        assert f"{k}:[" in src, f"redirect missing for {k}"


# ── Phase 3 — analyst tier: containers + Impact/Health merges + drill-downs ──

ANALYST_CONTAINERS = ["insightsc","impactc","familiesc","driftc","trustc","validationc","healthc","rollbackc","governancec"]


def test_phase3_analyst_containers_present():
    src = _src()
    for k in ANALYST_CONTAINERS:
        assert f"PAGES.{k}=" in src, f"missing analyst container PAGES.{k}"
        assert f'<a data-p="{k}"' in src, f"missing nav anchor for {k}"
    assert '<a data-p="oracle"' in src, "Oracle (single-page analyst dest) lost"


def test_impact_and_health_merges():
    keys = _tab_keys(_src())
    assert {"impact","impactanalysis"} <= keys, "Impact merge (31+50) tabs missing"
    assert {"unifiedhealth","health","systemstatus"} <= keys, "Health merge (56+86+87) tabs missing"


def test_site_twins_remain_drilldowns():
    src = _src()
    for k in ("eligibilitysite","trustsite","validationsite","impactsite","promotionsite"):
        assert re.search(r'data-dl="[^"]*"\s+data-p="' + k + '"', src), f"{k} not a drill-down link"


def test_analyst_routes_redirect():
    src = _src()
    for k in ("confidence","family","drift","governance","trustdecay","unifiedhealth"):
        assert f"{k}:[" in src, f"analyst redirect missing for {k}"


# ── Phase 4 — retire Centers, fold external links into System ────────────────

def test_phase4_centers_retired():
    src = _src()
    assert '<div class="navhead">Centers</div>' not in src, "Centers navsec not retired"
    for href in ("/cockpit/home", "/cockpit/actions", "/cockpit/settings",
                 "/cockpit/reports", "/cockpit/monitoring", "/cockpit/template-manager"):
        assert f'href="{href}"' not in src, f"redundant Center link still present: {href}"


def test_phase4_external_links_preserved():
    src = _src()
    for href in ("/framework/", "/fleet/", "/"):
        assert f'href="{href}"' in src, f"external app link lost: {href}"
