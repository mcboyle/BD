"""v3.66.113 — Phase 4 Template Review Workbench.

The load-bearing posture tests: the workbench records decisions via the EXISTING
inert store and never applies them, never rewrites sites_config, never auto-promotes
a template, and adds NO new POST surface (reuses /api/review/decide). Plus: every
item is non-auto-applying, before/after diffs are correct, decisions are surfaced,
and wiring (one GET route = 89, approve/reject → the inert decision endpoint).
"""
import json
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


class TestPostureNoApply:
    def test_module_does_not_write_or_apply(self):
        # read-only data + the established inert decision note only
        assert "json.dump(" not in _SRC          # 'json.dumps(' is read-only
        assert "write_text" not in _SRC
        assert "_store_save" not in _SRC          # reads _store_load, never saves
        for bad in ("def apply", "def promote", "auto_apply", "promote_template",
                    "_apply_detected_selectors", "retire_debt"):
            assert bad not in _SRC

    def test_does_not_rewrite_sites_config(self):
        # the cockpit must not write the live config; SITES_FILE/save not referenced
        assert "SITES_FILE" not in _SRC
        # the only sites_config touch is the read-only loader from earlier phases
        assert "_load_sites_config" in _SRC

    def test_no_new_post_endpoint_added(self):
        # approve/reject reuses the existing inert /api/review/decide — no new POST
        import re
        # the review-queue endpoint is GET
        assert '@bp.get("/api/template/review-queue")' in _CONSOLE
        assert '@bp.post("/api/template/review' not in _CONSOLE
        # the workbench page posts decisions to the EXISTING inert endpoint
        assert "'/api/review/decide'" in _CONSOLE or '"/api/review/decide"' in _CONSOLE

    def test_every_item_is_non_auto_applying(self):
        q = ct.template_review_queue()
        for it in q["items"]:
            assert it["applies_automatically"] is False
        assert q["_decision_endpoint"] == "/api/review/decide"


class TestDiff:
    def test_selector_diff_added_removed_unchanged(self):
        before = {"user_field": ["#a", "#b"]}
        after = {"user_field": ["#b", "#c"]}
        d = ct._selector_diff(before, after)
        g = d["user_field"]
        assert g["added"] == ["#c"]
        assert g["removed"] == ["#a"]
        assert g["unchanged"] == ["#b"]

    def test_scalar_diff_changed_flag(self):
        d = ct._selector_diff({"url_attribute": "src"}, {"url_attribute": "data-src"})
        assert d["url_attribute"]["changed"] is True
        d2 = ct._selector_diff({"url_attribute": "src"}, {"url_attribute": "src"})
        assert d2["url_attribute"]["changed"] is False

    def test_items_carry_diff_confidence_history_evidence(self):
        for it in ct.template_review_queue()["items"]:
            assert "diff" in it and "confidence_explanation" in it
            assert "change_history" in it and "evidence" in it
            assert "why" in it["confidence_explanation"]


class TestSuggestionsDataOnly:
    def test_video_suggestion_data_only(self):
        s = ct.suggested_video_template_update("anysite")
        assert s["applies_automatically"] is False
        assert "row_selectors" in s["suggested"]

    def test_login_suggestion_data_only(self):
        assert ct.suggested_login_template_update("anysite")["applies_automatically"] is False


class TestDecisionsAreRecordedInertly:
    def test_queue_surfaces_recorded_decisions(self):
        # the queue reads decisions from the operator store read-only
        q = ct.template_review_queue()
        for it in q["items"]:
            # decision is either None (pending) or the recorded dict — never applied
            assert it["decision"] is None or "decision" in it["decision"]

    def test_review_decide_records_without_acting(self):
        # the reused mechanism explicitly takes no action
        import tools.cockpit_core as cc
        src = Path(cc.__file__).read_text(encoding="utf-8")
        assert "No automatic action taken" in src


class TestWiring:
    def test_page_present(self):
        assert "PAGES.templatereview" in _CONSOLE and 'data-p="templatereview"' in _CONSOLE

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/review-queue").status_code == 200

    def test_post_surface_unchanged(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/template/review-queue" not in posts
        # the inert decision endpoint still exists and is the one reused
        assert "/cockpit/api/review/decide" in posts
