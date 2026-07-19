"""v3.66.109 — Band F (Forecasting & Trends), data-gated by design.

Band F is the data-blocked tail of the 130-feature roadmap (forecasting / trend /
velocity / calibration / sustainability / correlation). The whole point is that
these metrics WITHHOLD rather than fabricate while the corpus has only a few
distinct days. These tests assert exactly that: the gate fires, no numbers are
invented, no model/network call happens, every roadmap feature ID has a home, and
the page is wired and serves.
"""
from pathlib import Path

from tools import cockpit_core as cc

_SRC = Path(cc.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(cc.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")

# every data-blocked Band F feature id this band must give a home to
_BAND_F_IDS = {
    "39", "45", "50", "51", "52", "53", "54", "59", "66", "70", "75",
    "98", "99", "100", "101", "102", "106", "107", "108", "111",
    "113", "114", "117", "118", "128",
}


class TestGate:
    def test_distinct_days_is_small_today(self):
        # corpus currently has only a few distinct days — the reason Band F gates
        assert cc._distinct_days() < cc._MIN_TREND_DAYS

    def test_overview_withholds_everything_now(self):
        ov = cc.forecasting_overview()
        assert ov["metric_count"] >= 24
        # with insufficient history EVERY metric is withheld — none available
        assert ov["gated_count"] == ov["metric_count"]
        for p in ov["panels"]:
            for m in p["metrics"]:
                assert m["status"] in ("insufficient_history", "error")
                assert m["value"] is None

    def test_no_fabricated_numbers_when_gated(self):
        # a withheld metric must not carry a projection/estimate anywhere in value
        ov = cc.forecasting_overview()
        for p in ov["panels"]:
            for m in p["metrics"]:
                assert m["value"] is None, f"{m['feature']} fabricated a value while gated"

    def test_gate_explains_itself(self):
        ov = cc.forecasting_overview()
        m = ov["panels"][0]["metrics"][0]
        assert "_note" in m and ("distinct days" in m["_note"] or "forecast" in m["_note"])


class TestCoverage:
    def test_every_band_f_id_has_a_home(self):
        ov = cc.forecasting_overview()
        seen = {m["feature"] for p in ov["panels"] for m in p["metrics"]}
        missing = _BAND_F_IDS - seen
        assert not missing, f"Band F ids with no home: {sorted(missing)}"

    def test_six_panels(self):
        ov = cc.forecasting_overview()
        names = {p["panel"] for p in ov["panels"]}
        assert names == {"forecasting", "trends", "velocity", "calibration",
                         "sustainability", "correlation"}

    def test_calibration_gates_on_records_not_days(self):
        cal = cc.calibration_panel()
        assert "required_records" in cal["gate"]
        assert cal["gate"]["records"] == 0  # no forecast-log subsystem yet


class TestPostureClean:
    def test_band_f_makes_no_model_or_network_call(self):
        # the band must be pure computation — no model invocation, no HTTP
        block_start = _SRC.index("Band F — Forecasting & Trends")
        block = _SRC[block_start:]
        for bad in ("requests.", "urllib", "http", "ollama", "subprocess",
                    "openai", "_model", "generate("):
            assert bad not in block, f"Band F should not reference {bad!r}"

    def test_band_f_writes_nothing(self):
        block_start = _SRC.index("Band F — Forecasting & Trends")
        block = _SRC[block_start:]
        for bad in ("open(", ".write(", "write_text", ".replace(", "json.dump"):
            assert bad not in block, f"Band F should be read-only; found {bad!r}"

    def test_projection_helper_has_no_rng(self):
        block_start = _SRC.index("def _linear_projection")
        block = _SRC[block_start:_SRC.index("def _gated")]
        # check for actual RNG *usage*, not the word (the docstring says "No RNG")
        assert "import random" not in block
        assert "random." not in block
        assert "np.random" not in block


class TestWiring:
    def test_endpoint_and_page_present(self):
        assert '@bp.get("/api/forecasting")' in _CONSOLE
        assert "PAGES.forecasting" in _CONSOLE
        assert ('data-p="forecasting"' in _CONSOLE or "forecasting:[" in _CONSOLE)

    def test_endpoint_serves_and_route_count(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        assert "/cockpit/api/forecasting" in rules
        c = app.test_client()
        assert c.get("/cockpit/api/forecasting").status_code == 200

    def test_band_f_added_no_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        # Band F is read-only — the forecasting endpoint is not a POST
        assert "/cockpit/api/forecasting" not in posts
