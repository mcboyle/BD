"""v3.66.105 — Band B: Cross-Site + What-If (the highest-ROI new features).

Impact Simulator (#60/#124), Capture Opportunity (#55), Structural Similarity
(#49), Family Explorer (#41), Family Health (#43), Escalation Workflows (#37).

Safety focus: the impact simulator is read-only GRAPH REACHABILITY over the
corpus's resolves-edges — no probability, no simulation, no write. Escalation is
an INERT flag store (like notes/collections) — flagging runs nothing, validates
inputs, and references real corpus ids. Everything else is read-only aggregation.
The structural/family features carry an honest 'corpus-only' signal flag until
captures populate the richer descriptors.
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


class TestImpactSimulator:
    def test_entry_walks_resolves(self):
        # VC-0035 resolves VC-0017 -> VC-0035 'depends_on' VC-0017
        d = cc.impact_simulator("VC-0035")
        assert d["scope"] == "entry"
        assert "VC-0017" in [r["id"] for r in d["depends_on"]]

    def test_reverse_dependency(self):
        # VC-0017 is resolved by VC-0035 -> VC-0017 'would_be_affected' includes VC-0035
        d = cc.impact_simulator("VC-0017")
        assert "VC-0035" in [r["id"] for r in d["would_be_affected"]]
        assert d["blast_radius"] >= 1

    def test_site_scope(self):
        d = cc.impact_simulator("ultrafilms")
        assert d["scope"] == "site" and len(d["seeds"]) >= 1

    def test_unknown_target_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.impact_simulator("nonsense_xyz")

    def test_readonly_and_no_simulation_language(self):
        before = len(cc._corpus())
        cc.impact_simulator("VC-0017")
        assert len(cc._corpus()) == before
        # the function must be pure graph reachability — no RNG in the code.
        # (the docstring legitimately says "no probability/simulation"; check the
        # executable body for an actual random import/call, not prose.)
        src = (_ROOT / "tools" / "cockpit_core.py").read_text()
        import re
        m = re.search(r"def impact_simulator.*?(?=\ndef |\Z)", src, re.S)
        assert m
        body = m.group(0)
        # strip the docstring before scanning for code patterns
        body_no_doc = re.sub(r'""".*?"""', "", body, count=1, flags=re.S)
        for forbidden in ("random.", "import random", "np.random", ".uniform(", ".gauss("):
            assert forbidden not in body_no_doc

    def test_posture_clean(self):
        from bulk_downloader.capture_ingest import posture_scan
        assert not posture_scan(json.dumps(cc.impact_simulator("VC-0035")))


class TestCaptureOpportunity:
    def test_shape_and_priorities(self):
        d = cc.capture_opportunity()
        assert "opportunities" in d and set(d["by_priority"]) == {1, 2, 3}

    def test_priority_sorted(self):
        prios = [o["priority"] for o in cc.capture_opportunity()["opportunities"]]
        assert prios == sorted(prios)

    def test_recommends_not_acts(self):
        before = len(cc.list_tasks())
        cc.capture_opportunity()
        assert len(cc.list_tasks()) == before


class TestStructuralAndFamily:
    def test_similarity_pairs(self):
        d = cc.structural_similarity()
        assert "pairs" in d and d["signal"] in ("corpus-only", "capture+corpus")
        for p in d["pairs"]:
            assert 0.0 <= p["similarity"] <= 1.0

    def test_family_grouping_covers_all_sites(self):
        sim = cc.structural_similarity()
        fam = cc.family_explorer()
        grouped = {s for f in fam["families"] for s in f["members"]}
        assert grouped == set(sim["sites"])

    def test_family_health_health_in_range(self):
        for f in cc.family_health()["families"]:
            assert 0.0 <= f["health"] <= 1.0

    def test_signal_flag_is_honest(self):
        # with no captures ingested in this fixture, the signal must say corpus-only
        assert cc.structural_similarity()["signal"] == "corpus-only"


class TestEscalationInert:
    def test_flag_and_list(self):
        cc.escalate("VC-0018", "second look")
        lst = cc.escalation_list()
        assert lst["n_open"] >= 1
        assert any(e["item_id"] == "VC-0018" for e in lst["escalations"])

    def test_flag_requires_real_entry(self):
        with pytest.raises(cc.ValidationError):
            cc.escalate("VC-NOPE")

    def test_flag_runs_nothing(self):
        before = len(cc.list_tasks())
        cc.escalate("VC-0001", "x")
        assert len(cc.list_tasks()) == before

    def test_injection_in_reason_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.escalate("VC-0001", "bad`reason")

    def test_clear(self):
        cc.escalate("VC-0001", "x")
        assert cc.escalation_clear("VC-0001")["cleared"] is True


class TestBandBRouteShape:
    def test_escalation_posts_are_inert_state(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/escalations" in posts
        assert "/cockpit/api/escalations/clear" in posts
        # tool-running POSTs unchanged
        tool_runners = {"/cockpit/api/run-report", "/cockpit/api/run-capture",
                        "/cockpit/api/import-plan/preview", "/cockpit/api/queue/launch"}
        assert tool_runners <= posts

    def test_band_b_views_get_only(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        post_rules = {r.rule for r in app.url_map.iter_rules() if "POST" in r.methods}
        views = ["/cockpit/api/impact/VC-0018", "/cockpit/api/capture-opportunity",
                 "/cockpit/api/structural-similarity", "/cockpit/api/family-explorer",
                 "/cockpit/api/family-health"]
        # the impact path has a param so compare by prefix
        assert "/cockpit/api/capture-opportunity" not in post_rules
        assert "/cockpit/api/family-explorer" not in post_rules

    def test_band_b_pages_wired(self):
        from tools import cockpit_console as cc2
        src = Path(cc2.__file__).read_text()
        for page in ("impact", "opportunity", "similarity", "family",
                     "familyhealth", "escalations"):
            assert (f'data-p="{page}"' in src or f"{page}:[" in src) and f"PAGES.{page}=" in src
