"""v3.66.112 — Phase 3 Template Drift Intelligence.

Posture (read-only, no writes, no live fetch/replay, no auto-apply) + drift
severity classification correctness + factual/honest timeline & frequency
(sparse/trend flags) + DEFINED stability/maturity composites (inputs shown) +
unified dashboard shape + wiring (+2 routes = 88, no POSTs).
"""
import json
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")


class TestPosture:
    def test_module_writes_nothing(self):
        # 'json.dumps(' is read-only serialization; the WRITE call is 'json.dump('
        assert "json.dump(" not in _SRC
        assert "write_text" not in _SRC
        assert '"w"' not in _SRC and "'w'" not in _SRC  # no write-mode opens

    def test_no_live_fetch_or_replay_constructs(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    ".fill(", ".click("):
            assert bad not in _SRC, f"drift intel must not use {bad!r}"

    def test_no_auto_apply_or_promote(self):
        for bad in ("def apply", "def promote", "auto_apply", "save_config",
                    "promote_template", "retire_debt"):
            assert bad not in _SRC

    def test_timeline_does_not_echo_query_strings(self):
        blob = json.dumps(ct.drift_timeline())
        # detail fields are redacted/short; no raw signing query should appear
        assert "token=" not in blob and "sig=" not in blob


class TestSeverityClassification:
    def test_critical_for_missing_or_identity(self):
        assert ct.classify_drift_severity("x", template_missing=True) == "critical"
        assert ct.classify_drift_severity("identity_change") == "critical"
        assert ct.classify_drift_severity("identity_and_rendition_change") == "critical"

    def test_high_for_selector_and_login_failure(self):
        for k in ("selector_zero_match", "selector_stale", "login_failing"):
            assert ct.classify_drift_severity(k) == "high"

    def test_medium_for_recoverable(self):
        for k in ("cookie_expired", "captcha_added", "rendition_drift", "drift_verdict"):
            assert ct.classify_drift_severity(k) == "medium"

    def test_low_default(self):
        assert ct.classify_drift_severity("something_else") == "low"

    def test_severity_rank_ordering(self):
        r = ct._SEVERITY_RANK
        assert r["critical"] > r["high"] > r["medium"] > r["low"]


class TestDriftTimelineAndFrequency:
    def test_timeline_shape_and_sparse_flag(self):
        t = ct.drift_timeline()
        assert "events" in t and "event_count" in t and "sparse" in t
        # below the trend threshold the sparse flag must be honest
        assert t["sparse"] == (t["event_count"] < ct._MIN_TREND_EVENTS)

    def test_frequency_trend_flag_is_honest(self):
        f = ct.drift_frequency()
        assert "total_events" in f and "trend_reliable" in f
        assert f["trend_reliable"] == (f["total_events"] >= ct._MIN_TREND_EVENTS)

    def test_events_are_factual_records(self):
        # every timeline event carries ts/kind/source/severity — a record, not a guess
        for e in ct.drift_timeline()["events"]:
            for k in ("ts", "kind", "source", "severity"):
                assert k in e

    def test_root_causes_shape(self):
        rc = ct.drift_root_causes()
        assert "sites" in rc and "site_count" in rc
        for s in rc["sites"]:
            for c in s["causes"]:
                assert "cause" in c and "severity" in c and "next" in c


class TestScores:
    def test_stability_is_defined_composite_with_inputs(self):
        st = ct.template_stability_score()
        for s in st["sites"]:
            assert 0 <= s["score"] <= 100
            assert s["band"] in ("stable", "watch", "unstable")
            # transparency: components, weights, and raw inputs all shown
            assert "components" in s and "weights" in s and "inputs" in s

    def test_maturity_is_defined_composite_with_inputs(self):
        mt = ct.template_maturity_score()
        for s in mt["sites"]:
            assert 0 <= s["score"] <= 100
            assert s["band"] in ("mature", "developing", "nascent")
            assert "components" in s and "weights" in s and "inputs" in s
            assert s["trust_note"] in ("trusted", "use with review")

    def test_maturity_distinct_from_framework(self):
        # framework maturity lives in cockpit_core; template maturity is separate
        assert "template_maturity_score" in _SRC
        core = Path((Path(ct.__file__).parent / "cockpit_core.py")).read_text(encoding="utf-8")
        assert "def maturity_score(" in core  # the framework one still exists separately


class TestUnifiedDashboard:
    def test_unified_shape(self):
        u = ct.unified_template_health()
        assert "sites" in u and "site_count" in u and "trusted_count" in u
        for s in u["sites"]:
            for k in ("site", "video_template", "login_template", "stability",
                      "maturity", "drift_events"):
                assert k in s

    def test_unified_runs_against_example_config(self):
        import os
        os.environ["BD_SITES_CONFIG_PATH"] = "sites_config.example.json"
        try:
            u = ct.unified_template_health()
        finally:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
        assert u["site_count"] == len(u["sites"])


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")
        for ep in ("unified-health", "drift-intel"):
            assert f'@bp.get("/api/template/{ep}")' in console
        for pg in ("unifiedhealth", "driftintel"):
            assert f"PAGES.{pg}" in console and (f'data-p="{pg}"' in console or f"{pg}:[" in console)

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for ep in ("unified-health", "drift-intel"):
            assert c.get(f"/cockpit/api/template/{ep}").status_code == 200

    def test_drift_intel_added_no_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for ep in ("unified-health", "drift-intel"):
            assert f"/cockpit/api/template/{ep}" not in posts

    def test_drift_intel_aggregate_keys(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        c = app.test_client()
        d = json.loads(c.get("/cockpit/api/template/drift-intel").get_data())
        for k in ("timeline", "frequency", "severity_summary", "root_causes",
                  "stability", "maturity"):
            assert k in d
