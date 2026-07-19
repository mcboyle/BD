"""v3.66.118 — Phase 9 Site Readiness Score (read-only composite).

Posture (read-only, no live fetch/replay/apply) + composite correctness (7
components, weights sum to 1, every input shown, neutral defaults flagged) +
discrimination (a well-templated site outscores an empty one) + wiring.
"""
import json, os, tempfile
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")

_CFG = [
    {"id": "goodsite", "name": "Good", "login_url": "https://g/login",
     "learned": {"login": {"user_field": ["#u", "input[name=user]"], "pass_field": ["#p"],
                           "submit_btn": ["button[type=submit]"]},
                 "download": {"row_selectors": [".jw-overlays a", ".jwplayer source"],
                              "url_attribute": "src", "trigger_selectors": [".jw-icon-quality"]}}},
    {"id": "emptysite", "name": "Empty"},
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

    def test_no_live_fetch_or_replay_or_apply(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    "web_fetch", "def apply", "auto_apply"):
            assert bad not in _SRC, f"readiness must not use {bad!r}"


class TestComposite:
    def test_weights_sum_to_one(self):
        assert round(sum(ct._READINESS_WEIGHTS.values()), 6) == 1.0

    def test_seven_components_present(self):
        r = _run(lambda: ct.site_readiness())
        for s in r["sites"]:
            for k in ("login_health", "video_health", "drift", "evidence_freshness",
                      "capture_quality", "template_maturity", "review_debt"):
                assert k in s["components"]

    def test_score_in_range_and_banded(self):
        r = _run(lambda: ct.site_readiness())
        for s in r["sites"]:
            assert 0 <= s["readiness"] <= 100
            assert s["band"] in ("ready", "caution", "not_ready")

    def test_inputs_and_weights_shown(self):
        r = _run(lambda: ct.site_readiness())
        for s in r["sites"]:
            assert "weights" in s and "inputs" in s  # transparent/defined

    def test_thin_signals_flagged(self):
        # capture/evidence/success-rate are thin in a fresh env → flagged, neutral 0.5
        r = _run(lambda: ct.site_readiness())
        for s in r["sites"]:
            if s["thin_signals"]:
                for k in s["thin_signals"]:
                    assert s["components"].get(k, None) == 0.5 or k == "login_success_rate"

    def test_freshness_curve(self):
        assert ct._freshness_from_days(5) == 1.0
        assert ct._freshness_from_days(20) == 0.8
        assert ct._freshness_from_days(60) == 0.5
        assert ct._freshness_from_days(200) == 0.2
        assert ct._freshness_from_days(None) == 0.5  # neutral when unknown


class TestDiscrimination:
    def test_well_templated_site_outscores_empty(self):
        r = _run(lambda: ct.site_readiness())
        by = {s["site"]: s for s in r["sites"]}
        assert by["goodsite"]["readiness"] > by["emptysite"]["readiness"]

    def test_empty_site_has_low_login_and_video_health(self):
        r = _run(lambda: ct.site_readiness())
        empty = next(s for s in r["sites"] if s["site"] == "emptysite")
        assert empty["components"]["login_health"] < 0.3
        assert empty["components"]["video_health"] < 0.3

    def test_summary_counts(self):
        r = _run(lambda: ct.site_readiness())
        assert r["ready"] + r["caution"] + r["not_ready"] == r["site_count"]


class TestWiring:
    def test_endpoint_and_page_present(self):
        assert '@bp.get("/api/template/site-readiness")' in _CONSOLE
        assert "PAGES.sitereadiness" in _CONSOLE and ('data-p="sitereadiness"' in _CONSOLE or "sitereadiness:[" in _CONSOLE)

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/site-readiness").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/template/site-readiness" not in posts
