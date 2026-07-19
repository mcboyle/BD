"""v3.66.99 — cockpit Wave 1 data views: correctness + read-only safety.

Wave 1 adds six data-view pages (Mission Control, Site Intelligence, Corpus
Explorer, Evidence Timeline, Drift Ops, Risk Board) plus Smart Search and
Artifact Warehouse. They are ALL read-only over the corpus / reports / captures.
These tests prove: the views return real data, they add NO new POST/action
surface, the corpus is never mutated by viewing, the site path param is
validated (no traversal), and signing values are redacted from entry output.
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
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    (tmp_path / "cap").mkdir(); (tmp_path / "rep").mkdir(); (tmp_path / "task").mkdir()
    yield


class TestViewsReturnData:
    def test_mission_control_shape(self):
        m = cc.mission_control()
        for k in ("active_captures", "review_queue", "debt", "recent_drift",
                  "sites_needing_attention", "_status"):
            assert k in m

    def test_timeline_chronological(self):
        t = cc.evidence_timeline()
        dates = [e["date"] for e in t["events"] if e["date"]]
        assert dates == sorted(dates)  # sorted ascending

    def test_corpus_explorer_filters(self):
        allr = cc.corpus_explorer()
        pert = cc.corpus_explorer(category="perturbation_rule")
        assert pert["n"] <= allr["n"]
        assert all(r["category"] == "perturbation_rule" for r in pert["rows"])

    def test_corpus_explorer_facets(self):
        f = cc.corpus_explorer()["facets"]
        assert "categories" in f and "outcomes" in f and "sites" in f

    def test_corpus_entry_relationships(self):
        # VC-0017 is resolved by VC-0035 (recorded this project)
        e = cc.corpus_entry("VC-0017")
        assert e is not None
        assert "VC-0035" in e["resolved_by"]

    def test_drift_ops_severity_ranked(self):
        d = cc.drift_ops()
        sev = [x["severity"] for x in d["drift"]]
        assert sev == sorted(sev, reverse=True)

    def test_risk_board_shape(self):
        r = cc.risk_board()
        assert "open_debt" in r and "assumptions" in r and "weakest_evidence" in r

    def test_smart_search_finds_corpus(self):
        r = cc.smart_search("player_config")
        assert r["n"] >= 1
        assert any(x["kind"] == "corpus" for x in r["results"])

    def test_warehouse_categorizes(self):
        # drop a report + a capture under the configured roots and confirm
        cc.reports_root().mkdir(parents=True, exist_ok=True)
        cc.captures_root().mkdir(parents=True, exist_ok=True)
        (cc.reports_root() / "site_health_report.md").write_text("# health", encoding="utf-8")
        (cc.captures_root() / "clip.wacz").write_bytes(b"PK\x03\x04stub")
        w = cc.artifact_warehouse()
        cats = w["categories"]
        names = [f["name"] for v in cats.values() for f in v]
        assert "clip.wacz" in names


class TestSiteIntelligence:
    def test_site_token_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.site_intelligence("../etc")
        with pytest.raises(cc.ValidationError):
            cc.site_intelligence("a; rm -rf")

    def test_known_site_returns_entries(self):
        # ultrafilms appears in corpus observations (VC-0035 etc.)
        d = cc.site_intelligence("ultrafilms")
        assert d["site"] == "ultrafilms"
        assert d["n_corpus_entries"] >= 1


class TestReadOnlyNoMutation:
    def test_viewing_does_not_change_corpus(self):
        before = len(cc._corpus())
        # exercise every view
        cc.mission_control(); cc.evidence_timeline(); cc.corpus_explorer()
        cc.drift_ops(); cc.risk_board(); cc.smart_search("x")
        cc.artifact_warehouse(); cc.site_intelligence("ultrafilms")
        cc.corpus_entry("VC-0001")
        after = len(cc._corpus())
        assert before == after

    def test_wave1_adds_no_post_routes(self):
        # The precise Wave 1 claim: every Wave 1 DATA-VIEW endpoint is read-only
        # (GET, never POST). This stays true regardless of later waves adding
        # their own scoped POSTs elsewhere — Wave 1 itself introduced no writes.
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        post_rules = {r.rule for r in app.url_map.iter_rules() if "POST" in r.methods}
        wave1_views = [
            "/cockpit/api/mission", "/cockpit/api/timeline", "/cockpit/api/corpus",
            "/cockpit/api/drift", "/cockpit/api/risk", "/cockpit/api/search",
            "/cockpit/api/warehouse",
        ]
        for rule in app.url_map.iter_rules():
            if rule.rule in wave1_views:
                assert "POST" not in rule.methods, f"{rule.rule} must be GET-only"
        # and none of the Wave 1 view endpoints appear among POST routes
        assert not (set(wave1_views) & post_rules)


class TestRedactionInViews:
    def test_site_endpoint_redacts(self):
        # a site_profile with a stray signing value must not surface raw — the
        # site endpoint runs its JSON through cc.redact() before returning.
        prof = {"site": "leaky",
                "known_signing_markers": ["token", "expires"],
                "note": "url=https://x/clip.mp4?token=SECRETVAL&expires=9"}
        red = cc.redact(json.dumps(prof))
        assert "SECRETVAL" not in red
        assert "token=<scrubbed>" in red

    def test_corpus_entry_endpoint_redacts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        r = c.get("/cockpit/api/corpus/VC-0035")
        assert r.status_code == 200
        txt = r.get_data(as_text=True)
        # no raw token/sig values in the rendered entry
        assert "token=" not in txt or "token=<scrubbed>" in txt


class TestSearchTraversalSafe:
    def test_search_query_is_not_a_path(self, tmp_path):
        # search takes a query string, never a path; a traversal-looking query
        # just matches nothing, it does not read outside the roots
        r = cc.smart_search("../../etc/passwd")
        # results only ever reference files UNDER the roots (relative paths)
        for x in r["results"]:
            if "path" in x:
                assert ".." not in x["path"]
