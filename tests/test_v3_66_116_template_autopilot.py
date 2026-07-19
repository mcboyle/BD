"""v3.66.116 — Phase 7 Template Autopilot (operator-guided orchestration).

Read-only orchestration. Posture (recognition-only detect = no fetch; data-only
suggestions; ends at review; no apply/writes) + chain completeness + site detection
(id/URL/miss) + honest sample download analysis + wiring (+1 GET route = 94, no POSTs).
"""
import json, os, tempfile
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")

_CFG = [{"id": "vipsite", "name": "VIP Site", "login_url": "https://vip.example.com/login",
         "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"], "submit_btn": ["button[type=submit]"]},
                     "download": {"row_selectors": [".jw-overlays a"], "url_attribute": "src",
                                  "trigger_selectors": [".jw-icon-quality"]}}}]


def _with_cfg(fn):
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
        assert "write_text" not in _SRC
        assert "_store_save" not in _SRC

    def test_no_live_fetch_or_replay(self):
        # detection must be recognition-only; the autopilot never fetches/replays
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    ".fill(", ".click(", "ollama.", "web_fetch"):
            assert bad not in _SRC, f"autopilot must not use {bad!r}"

    def test_no_auto_apply(self):
        for bad in ("def apply", "def promote", "auto_apply", "_apply_detected_selectors"):
            assert bad not in _SRC

    def test_suggested_updates_step_is_data_only(self):
        r = _with_cfg(lambda: ct.template_autopilot("vipsite"))
        sug = next(s for s in r["steps"] if s["step"] == "generate_suggested_updates")
        assert sug["status"] == "data_only"


class TestDetectSite:
    def test_detect_by_id(self):
        assert _with_cfg(lambda: ct._detect_site("vipsite")) is not None

    def test_detect_by_url_host(self):
        assert _with_cfg(lambda: ct._detect_site("https://vip.example.com/video/9")) is not None

    def test_detect_miss_returns_none(self):
        assert _with_cfg(lambda: ct._detect_site("https://unknown.org/x")) is None

    def test_unrecognized_url_not_fetched(self):
        r = _with_cfg(lambda: ct.template_autopilot("https://unknown.org/x"))
        assert r["detected_site"] is None
        assert r["steps"][0]["status"] == "not_recognized"
        # the run must make clear no fetch happened
        assert "fetch" in (r["steps"][0]["detail"].lower() + r["_note"].lower())


class TestChain:
    def test_all_steps_in_order(self):
        r = _with_cfg(lambda: ct.template_autopilot("vipsite"))
        steps = [s["step"] for s in r["steps"]]
        assert steps == ["detect_site", "load_login_template", "login_health_check",
                         "load_video_template", "download_analysis", "drift_check",
                         "generate_suggested_updates", "review_queue"]

    def test_ends_at_review_with_human_decision(self):
        r = _with_cfg(lambda: ct.template_autopilot("vipsite"))
        review = next(s for s in r["steps"] if s["step"] == "review_queue")
        assert "human decision" in review["next"].lower()
        assert "detected_site" in r and r["detected_site"] == "vipsite"

    def test_download_analysis_is_sample_without_live_candidates(self):
        r = _with_cfg(lambda: ct.template_autopilot("vipsite"))
        da = next(s for s in r["steps"] if s["step"] == "download_analysis")
        assert da["result"]["is_sample"] is True

    def test_empty_target_handled(self):
        r = ct.template_autopilot("")
        assert r["human_decision_required"] is False
        assert r["steps"][0]["status"] == "failed"


class TestWiring:
    def test_endpoint_and_page_present(self):
        assert '@bp.get("/api/template/autopilot")' in _CONSOLE
        assert "PAGES.templateautopilot" in _CONSOLE and ('data-p="templateautopilot"' in _CONSOLE or "templateautopilot:[" in _CONSOLE)
        assert "function apRun" in _CONSOLE

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/autopilot?target=x").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/template/autopilot" not in posts
