"""v3.66.103 — Phase 2 TRIVIAL reskins: read-only views/pivots over existing data.

These features are thin regroupings/summaries of data the cockpit already
exposes (drift_ops, coverage_heatmap, site data, review_roi). The tests confirm
they execute against the real corpus, return the expected shape, and remain
read-only (no corpus mutation, no new POST surface).
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    for s in ("cap", "rep", "task"):
        (tmp_path / s).mkdir()
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    yield


class TestReskinsReturnShape:
    def test_cross_site_drift(self):
        d = cc.cross_site_drift()
        assert "by_site" in d and "n_sites" in d

    def test_portfolio_ranking_sorted(self):
        r = cc.portfolio_ranking()["ranking"]
        # sorted by entries desc
        entries = [x["entries"] for x in r]
        assert entries == sorted(entries, reverse=True)

    def test_portfolio_ranking_has_health(self):
        for row in cc.portfolio_ranking()["ranking"]:
            assert 0.0 <= row["health"] <= 1.0

    def test_blind_spots_shape(self):
        d = cc.blind_spots()
        for k in ("untested_assumptions", "sites_without_captures",
                  "categories_without_confirmed_evidence"):
            assert k in d

    def test_compliance_summary_verdict(self):
        assert cc.compliance_summary()["verdict"] in ("compliant", "attention")

    def test_evidence_scarcity_index_in_range(self):
        s = cc.evidence_scarcity()
        assert 0.0 <= s["scarcity_index"] <= 1.0

    def test_capture_yield_shape(self):
        d = cc.capture_yield()
        assert "captures_present" in d and "confirmed_by_site" in d

    def test_decision_quality_confirm_rate(self):
        d = cc.decision_quality()
        assert 0.0 <= d["confirm_rate"] <= 1.0

    def test_exec_monthly_quarterly(self):
        assert cc.exec_summary("monthly")["period"] == "monthly"
        assert cc.exec_summary("quarterly")["period"] == "quarterly"


class TestReskinsAreReadOnly:
    def test_no_corpus_mutation(self):
        before = len(cc._corpus())
        cc.cross_site_drift(); cc.portfolio_ranking(); cc.blind_spots()
        cc.compliance_summary(); cc.evidence_scarcity(); cc.capture_yield()
        cc.decision_quality()
        assert len(cc._corpus()) == before

    def test_reskins_are_get_only(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        post_rules = {r.rule for r in app.url_map.iter_rules() if "POST" in r.methods}
        views = ["/cockpit/api/cross-site-drift", "/cockpit/api/portfolio-ranking",
                 "/cockpit/api/blind-spots", "/cockpit/api/compliance",
                 "/cockpit/api/evidence-scarcity", "/cockpit/api/capture-yield",
                 "/cockpit/api/decision-quality"]
        assert not (set(views) & post_rules)


class TestReskinsDeriveFromExisting:
    def test_cross_site_drift_matches_drift_ops(self):
        # cross_site_drift is a pivot of drift_ops — same total drift count
        total_pivot = sum(r["drift_count"] for r in cc.cross_site_drift()["by_site"])
        total_flat = cc.drift_ops()["n"]
        assert total_pivot == total_flat

    def test_compliance_derives_from_readiness(self):
        rr = cc.release_readiness()
        comp = cc.compliance_summary()
        # same posture-scan figures
        assert comp["posture"]["with_leaks"] == rr["posture_scan"]["with_leaks"]
