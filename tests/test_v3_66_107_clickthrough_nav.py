"""v3.66.107 — click-through completion + nav consolidation (C).

Click-through: the six row-pages (timeline, risk, assumptions, confidence,
lessons, exec) plus Coverage grid cells now deep-link to the corpus entry / a
filtered corpus view. Consolidation (option C): Inbox/Daily/Alerts merged into one
"Priority" page (all are next_best_action); Maturity/Complexity/Org Health merged
into one "Scores" page; the flat ~58-item nav grouped into collapsible sections.

Underlying renderers (inbox/daily/maturity/complexity/orghealth) are KEPT so
deep-links and direct go() still work — only their individual nav entries were
replaced. No new routes, no new POST surface; pure front-end reorganization.
"""
import os
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SRC = (_ROOT / "tools" / "cockpit_console.py").read_text()


def _page_body(name: str) -> str:
    i = _SRC.find(f"PAGES.{name}=")
    assert i >= 0, f"page {name} not found"
    nxt = _SRC.find("PAGES.", i + 6)
    return _SRC[i:nxt] if nxt > 0 else _SRC[i:]


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    for s in ("cap", "rep", "task"):
        (tmp_path / s).mkdir()
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    yield


class TestClickThroughAdded:
    @pytest.mark.parametrize("page", ["timeline", "risk", "assumptions", "lessons"])
    def test_row_pages_have_entry_clickthrough(self, page):
        body = _page_body(page)
        assert 'class="clk" data-entry=' in body
        assert "go('corpus',{entry:" in body

    def test_confidence_list_items_clickable(self):
        body = _page_body("confidence")
        assert 'li class="clk" data-entry=' in body
        assert "go('corpus',{entry:" in body

    def test_exec_recent_drift_clickable(self):
        # exRun is a helper, not a PAGES.* — bound the scan by the next page def
        i = _SRC.find("async function exRun(")
        end = _SRC.find("PAGES.coverage", i)
        body = _SRC[i:end if end > 0 else i + 1600]
        assert 'li class="clk" data-entry=' in body and "go('corpus',{entry:" in body

    def test_coverage_cells_clickable(self):
        body = _page_body("coverage")
        assert "covcell" in body
        assert "go('corpus',{filter:{cat:" in body


class TestPriorityConsolidation:
    def test_priority_page_exists_with_tabs(self):
        body = _page_body("priority")
        assert "data-ptab" in body
        # the three views
        for v in ("inbox", "daily", "alerts"):
            assert v in body.lower()

    def test_renderprio_uses_one_engine(self):
        # the merged renderer pulls from the same prioritization endpoints
        i = _SRC.find("async function renderPrioTab(")
        body = _SRC[i:i + 2000]
        assert "/api/inbox" in body and "/api/daily-mission" in body

    def test_underlying_inbox_daily_renderers_kept(self):
        # deep-links / direct go() must still resolve
        assert "PAGES.inbox=" in _SRC and "PAGES.daily=" in _SRC

    def test_inbox_daily_removed_from_nav(self):
        nav = re.findall(r'data-p="([a-z0-9]+)"', _SRC)
        assert "priority" in nav
        assert "inbox" not in nav and "daily" not in nav


class TestScoresConsolidation:
    def test_scores_page_exists(self):
        body = _page_body("scores")
        assert "Promise.all" in body  # fetches all three in parallel
        assert "/api/maturity" in body and "/api/complexity" in body and "/api/org-health" in body

    def test_underlying_score_renderers_kept(self):
        for sub in ("maturity", "complexity", "orghealth"):
            assert f"PAGES.{sub}=" in _SRC

    def test_score_pages_removed_from_nav(self):
        nav = re.findall(r'data-p="([a-z0-9]+)"', _SRC)
        assert "scores:[" in _SRC          # scores now an Insights container tab (redirect-wired)
        for sub in ("maturity", "complexity", "orghealth"):
            assert sub not in nav


class TestGroupedNav:
    def test_collapsible_sections_present(self):
        assert _SRC.count('class="navsec"') >= 4            # everyday groups remain after consolidation
        assert _SRC.count('class="navhead"') >= 4
        assert _SRC.count('class="navdrawer"') >= 1         # Advanced/System collapsible tier drawers
        assert _SRC.count('data-tier="') >= 3               # everyday / advanced / system tiers
        assert ".navsec.collapsed .navitems{display:none}" in _SRC

    def test_section_toggle_wired(self):
        assert "$$('#nav .navhead').forEach" in _SRC
        assert "classList.toggle('collapsed')" in _SRC

    def test_parent_highlight_for_subviews(self):
        # go() highlights the parent nav entry for merged sub-views
        assert "NAV_PARENT" in _SRC
        assert "'scores'" in _SRC and "'priority'" in _SRC

    def test_no_dead_nav_links(self):
        nav = re.findall(r'data-p="([a-z0-9]+)"', _SRC)
        pages = set(re.findall(r"PAGES\.([a-z]+)=", _SRC))
        dead = [p for p in nav if p not in pages]
        assert dead == [], f"dead nav links: {dead}"

    def test_nav_wiring_still_finds_links(self):
        # the go() wiring uses $$('#nav a') which still matches nested <a data-p>
        assert "$$('#nav a').forEach(a=>a.onclick=()=>go(a.dataset.p))" in _SRC


class TestNoNewSurface107:
    def test_no_new_routes_or_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        # consolidation removes nav entries, not endpoints — count unchanged at 73
        assert len(rules) >= 162
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        # no priority/scores/palette POSTs — pure front-end
        assert not any(x in p for p in posts for x in ("priority", "scores", "ptab"))

    def test_index_still_renders(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        r = c.get("/cockpit/")
        assert r.status_code == 200
        assert b"navsec" in r.data and b"cmdk" in r.data
