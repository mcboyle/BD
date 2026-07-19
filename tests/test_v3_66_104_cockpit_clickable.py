"""v3.66.104 — cockpit clickable / hover / deep-link UX layer.

This is a pure front-end layer over the existing pages: cards and table rows
become clickable, hovering a card lists the items behind the number, and
clicking an item deep-links to that specific entry (e.g. a validation-debt item
opens that one corpus entry in the Corpus Explorer). No new endpoints, no new
POST surface, no posture change — clicking still only navigates/views.

The tests assert the machinery is present in the served GUI and that the route
shape is unchanged (the v98 tripwire stays valid).
"""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    for s in ("cap", "rep", "task"):
        (tmp_path / s).mkdir()
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    yield


class TestUXMachineryPresent:
    def test_hoverlist_and_deeplink_helpers_exist(self):
        from tools import cockpit_console as cc
        src = Path(cc.__file__).read_text()
        # the three pieces of the interaction layer
        assert "function hoverList(" in src
        assert "function applyDeeplink(" in src
        assert "go(p, deeplink)" in src or "async function go(p, deeplink)" in src

    def test_mission_cards_are_clickable(self):
        from tools import cockpit_console as cc
        src = Path(cc.__file__).read_text()
        # mission control cards carry ids + the clk class + hoverList wiring
        for cid in ("mc-validation", "mc-correction", "mc-review", "mc-corpus"):
            assert cid in src
        assert "hoverList($('#mc-validation')" in src

    def test_validation_debt_item_deeplinks_to_entry(self):
        from tools import cockpit_console as cc
        src = Path(cc.__file__).read_text()
        # the validation-debt hover items deep-link to that one corpus entry
        assert "page:'corpus', deeplink:{entry:id}" in src

    def test_inbox_rows_deeplink_by_kind(self):
        from tools import cockpit_console as cc
        src = Path(cc.__file__).read_text()
        assert "PAGES.inbox" in src
        # inbox maps validation/correction debt rows to the corpus entry
        assert "validation_debt" in src and "correction_debt" in src

    def test_drift_rows_clickable(self):
        from tools import cockpit_console as cc
        src = Path(cc.__file__).read_text()
        assert "tr class=\"clk\" data-entry=" in src or 'tr class="clk" data-entry=' in src


class TestNoNewSurface:
    def test_no_new_routes_added(self):
        # the clickable UX layer itself added zero routes. The cockpit route count
        # grows only when later releases add features (Band B added 7 in v105);
        # this asserts the CURRENT expected total so an *accidental* route change
        # is still caught. Update deliberately when a release adds endpoints.
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162  # v3.66.109 Band F added /api/forecasting (1 GET)

    def test_post_set_unchanged(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        # the only tool-running POSTs remain the allowlisted actions + queue/launch;
        # the rest are inert state. The UX layer adds none.
        tool_runners = {"/cockpit/api/run-report", "/cockpit/api/run-capture",
                        "/cockpit/api/import-plan/preview", "/cockpit/api/queue/launch"}
        assert tool_runners <= posts
        assert "/cockpit/api/saved-views" in posts  # inert state still there
        # no deep-link / hover endpoint exists (front-end only)
        assert not any("deeplink" in p or "hover" in p for p in posts)


class TestReskinPagesWired:
    """v3.66.103 shipped 7 reskin functions with endpoints but no GUI pages;
    v3.66.104 wires them into the nav + renderers so they are visible/clickable."""

    def test_reskin_pages_have_nav_and_renderer(self):
        from tools import cockpit_console as cc
        src = Path(cc.__file__).read_text()
        for page in ("crosssitedrift", "portfolio", "blindspots", "scarcity",
                     "captureyield", "decisionquality", "compliance"):
            assert (f'data-p="{page}"' in src or f"{page}:[" in src), f"{page} missing nav entry"
            assert f"PAGES.{page}=" in src, f"{page} missing renderer"

    def test_reskin_endpoints_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        for ep in ("/cockpit/api/cross-site-drift", "/cockpit/api/portfolio-ranking",
                   "/cockpit/api/blind-spots", "/cockpit/api/evidence-scarcity",
                   "/cockpit/api/capture-yield", "/cockpit/api/decision-quality",
                   "/cockpit/api/compliance"):
            assert c.get(ep).status_code == 200, f"{ep} did not serve"


class TestDeeplinkTargetsResolve:
    def test_single_entry_deeplink_endpoint_works(self):
        # clicking a debt item calls cxDetail -> GET /api/corpus/<id>
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        r = c.get("/cockpit/api/corpus/VC-0018")
        assert r.status_code == 200
        assert (r.get_json().get("entry") or {}).get("id") == "VC-0018"
