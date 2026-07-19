"""v3.66.119 — Phase 10 Operator Mission Control (read-only capstone).

Posture (read-only roll-up; nothing acts) + four zones present and populated
correctly + recommended actions are suggestions (deduped, prioritized, tied to a
site) + wiring (+1 GET route = 97, no POST). Final phase of template intelligence.
"""
import json, os, tempfile
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")

_CFG = [
    {"id": "goodsite", "login_url": "https://g/login",
     "learned": {"login": {"user_field": ["#u", "input[name=user]"], "pass_field": ["#p"],
                           "submit_btn": ["button[type=submit]"]},
                 "download": {"row_selectors": [".jw-overlays a", ".jwplayer source"],
                              "url_attribute": "src", "trigger_selectors": [".jw-icon-quality"]}}},
    {"id": "emptysite"},
]


def _run(fn):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(_CFG, f); f.close()
    os.environ["BD_SITES_CONFIG_PATH"] = f.name
    try:
        return fn()
    finally:
        os.environ.pop("BD_SITES_CONFIG_PATH", None)


class TestPosture:
    def test_read_only_no_writes(self):
        assert "json.dump(" not in _SRC      # 'json.dumps(' is read-only
        assert "write_text" not in _SRC and "_store_save" not in _SRC

    def test_no_live_fetch_replay_or_apply(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    "web_fetch", "def apply", "auto_apply"):
            assert bad not in _SRC, f"mission control must not use {bad!r}"

    def test_status_says_nothing_acts(self):
        m = _run(lambda: ct.operator_mission_control())
        assert "nothing here acts" in m["_status"].lower()


class TestFourZones:
    def test_all_four_zones_present(self):
        m = _run(lambda: ct.operator_mission_control())
        for z in ("needs_attention", "healthy", "active_work", "recommended_actions"):
            assert z in m

    def test_needs_attention_flags_broken_templates(self):
        m = _run(lambda: ct.operator_mission_control())
        na = m["needs_attention"]
        assert "emptysite" in na["broken_login_templates"]
        assert "emptysite" in na["broken_video_templates"]
        for k in ("high_drift_sites", "open_reviews", "open_debt", "not_ready_sites", "count"):
            assert k in na

    def test_healthy_lists_ready_sites(self):
        m = _run(lambda: ct.operator_mission_control())
        ready = {r["site"] for r in m["healthy"]["ready_sites"]}
        assert "goodsite" in ready
        for k in ("trusted_templates", "fresh_evidence", "count"):
            assert k in m["healthy"]

    def test_active_work_shape(self):
        m = _run(lambda: ct.operator_mission_control())
        aw = m["active_work"]
        for k in ("captures_running", "running_tasks", "review_queue",
                  "recent_drift_7d", "recent_drift_corpus"):
            assert k in aw


class TestRecommendedActions:
    def test_actions_are_suggestions_tied_to_site(self):
        m = _run(lambda: ct.operator_mission_control())
        for a in m["recommended_actions"]:
            assert "action" in a and "site" in a and "why" in a and "priority" in a
            assert a["action"] in ("run_capture", "review_template",
                                   "refresh_evidence", "investigate_drift")

    def test_actions_deduped_by_site_and_action(self):
        m = _run(lambda: ct.operator_mission_control())
        keys = [(a["site"], a["action"]) for a in m["recommended_actions"]]
        assert len(keys) == len(set(keys))  # no duplicate (site, action)

    def test_actions_sorted_by_priority(self):
        m = _run(lambda: ct.operator_mission_control())
        prios = [a["priority"] for a in m["recommended_actions"]]
        assert prios == sorted(prios)

    def test_empty_site_recommended_for_review(self):
        m = _run(lambda: ct.operator_mission_control())
        review_sites = {a["site"] for a in m["recommended_actions"] if a["action"] == "review_template"}
        assert "emptysite" in review_sites


class TestComposesExistingMission:
    def test_reuses_ops_mission_control(self):
        # the capstone composes the existing ops mission_control, not a reimplementation
        assert "from tools.cockpit_core import mission_control" in _SRC


class TestWiring:
    def test_endpoint_and_page_present(self):
        assert '@bp.get("/api/template/mission-control")' in _CONSOLE
        assert "PAGES.missioncontrol" in _CONSOLE and ('data-p="missioncontrol"' in _CONSOLE or "missioncontrol:[" in _CONSOLE)

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/mission-control").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/template/mission-control" not in posts
