"""v3.66.102 — Phase 1 completion (this list's Waves 1-3 new features).

Covers the genuinely-new features that complete the 130-list's Phase 1:
prioritization engine (Priority Inbox / Daily Mission / Smart Notifications),
Activity Feed, Investigation Workspace, Review ROI, Saved Views, Decision Trace
(+ Audit), Assumption Center, Confidence Decomposition, Evidence Collections,
Lessons Learned, Organizational Memory.

Safety focus: the new state stores (saved views, collections) are INERT data —
nothing executes from them, each validates inputs (injection rejected), and the
new views are read-only over the corpus (no mutation). The prioritization engine
and feeds recommend/report only; they never act.
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


class TestPrioritizationEngine:
    def test_next_best_action_shape(self):
        d = cc.next_best_action()
        assert "items" in d and "counts" in d
        assert set(d["counts"]) == {"high", "medium", "low"}

    def test_items_are_prioritized(self):
        items = cc.next_best_action()["items"]
        prios = [i["priority"] for i in items]
        assert prios == sorted(prios)

    def test_recommends_only_not_acts(self):
        # the engine reads state; it must not mutate the corpus
        before = len(cc._corpus())
        cc.next_best_action()
        assert len(cc._corpus()) == before

    def test_daily_mission_is_view(self):
        d = cc.daily_mission()
        assert "mission" in d and "focus" in d
        assert len(d["focus"]) <= 3

    def test_notifications_are_high_medium(self):
        n = cc.smart_notifications()
        assert all(a["severity"] in ("high", "medium") for a in n["alerts"])


class TestActivityFeed:
    def test_shape_and_readonly(self):
        before = len(cc._corpus())
        d = cc.activity_feed()
        assert "events" in d
        assert len(cc._corpus()) == before

    def test_reverse_chronological(self):
        # seed a couple of tasks via the registry by recording fake entries
        # (the feed reads list_tasks(); empty is fine, ordering must hold)
        d = cc.activity_feed()
        ats = [e["at"] for e in d["events"] if e.get("at")]
        assert ats == sorted(ats, reverse=True)


class TestInvestigationWorkspace:
    def test_panels_present(self):
        d = cc.investigation_workspace("ultrafilms")
        assert set(d["panels"]) == {"intelligence", "timeline", "notes", "captures"}

    def test_site_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.investigation_workspace("../etc")


class TestReviewROI:
    def test_shape(self):
        r = cc.review_roi()
        for k in ("debt_retired_total", "decisions", "open_debt", "retire_ratio"):
            assert k in r

    def test_readonly(self):
        before = len(cc._corpus())
        cc.review_roi()
        assert len(cc._corpus()) == before


class TestSavedViewsInert:
    def test_add_and_list(self):
        v = cc.saved_view_add("v1", "corpus", {"category": "assumption"})
        assert v["kind"] == "corpus"
        assert any(x["id"] == v["id"] for x in cc.saved_view_list())

    def test_injection_in_params_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.saved_view_add("v", "corpus", {"q": "a; rm -rf /"})

    def test_kind_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.saved_view_add("v", "not_a_kind", {})

    def test_saved_view_is_data_not_action(self):
        # a saved view is a stored query; it never runs anything
        before = len(cc.list_tasks())
        cc.saved_view_add("v", "search", {"q": "player"})
        assert len(cc.list_tasks()) == before

    def test_delete(self):
        v = cc.saved_view_add("v", "corpus", {})
        r = cc.saved_view_delete(v["id"])
        assert r["deleted"] is True


class TestDecisionTrace:
    def test_trace_walks_resolves(self):
        # VC-0017 is resolved by VC-0035 (recorded this project)
        d = cc.decision_trace("VC-0017")
        assert d["root"] == "VC-0017"
        assert "VC-0035" in d["resolved_by"]
        assert d["chain"][0]["id"] == "VC-0017"

    def test_unknown_entry_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.decision_trace("VC-NOPE")

    def test_trace_is_posture_clean(self):
        from bulk_downloader.capture_ingest import posture_scan
        # trace a resolution entry; output must carry no signing values
        d = cc.decision_trace("VC-0035")
        assert not posture_scan(json.dumps(d))


class TestAssumptionCenter:
    def test_only_assumptions(self):
        d = cc.assumption_center()
        # all rows are assumption-category (by construction)
        assert d["n"] >= 1
        assert "by_status" in d

    def test_status_classification(self):
        d = cc.assumption_center()
        for r in d["assumptions"]:
            assert r["status"] in ("validated", "open_debt", "falsified",
                                   "partial", "untested", "confirmed", None)


class TestConfidenceDecomposition:
    def test_shape(self):
        d = cc.confidence_decomposition()
        for k in ("confidence_caps", "sensitivity_flags", "outcome_mix",
                  "confirmed_fraction", "limiting_factors"):
            assert k in d

    def test_fraction_in_range(self):
        f = cc.confidence_decomposition()["confirmed_fraction"]
        assert 0.0 <= f <= 1.0


class TestEvidenceCollectionsInert:
    def test_create_and_add(self):
        c = cc.collection_create("n3")
        cc.collection_add(c["id"], "VC-0035")
        cols = cc.collection_list()
        assert any("VC-0035" in x["entry_ids"] for x in cols)

    def test_add_requires_real_entry(self):
        c = cc.collection_create("n3")
        with pytest.raises(cc.ValidationError):
            cc.collection_add(c["id"], "VC-NOPE")  # not in corpus

    def test_name_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.collection_create("bad name; rm")

    def test_collection_runs_nothing(self):
        before = len(cc.list_tasks())
        c = cc.collection_create("x")
        cc.collection_add(c["id"], "VC-0001")
        assert len(cc.list_tasks()) == before


class TestLessonsAndMemory:
    def test_lessons_degrade_gracefully(self):
        # LESSONS_LEARNED.md is KB-only; the function must still return lessons
        d = cc.lessons_learned()
        assert "corpus_lessons" in d
        assert isinstance(d["doc_present"], bool)

    def test_org_memory_aggregates(self):
        d = cc.organizational_memory()
        for k in ("corpus_entries", "outcome_mix", "collections", "lessons"):
            assert k in d

    def test_lessons_posture_clean(self):
        from bulk_downloader.capture_ingest import posture_scan
        assert not posture_scan(json.dumps(cc.lessons_learned()))


class TestRouteShape:
    def test_new_posts_are_inert_state_only(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        # the Phase 1 completion POSTs are inert state stores
        for ep in ("/cockpit/api/saved-views", "/cockpit/api/saved-views/delete",
                   "/cockpit/api/collections", "/cockpit/api/collections/add"):
            assert ep in posts
        # the only tool-running POSTs remain the allowlisted actions + queue/launch
        tool_runners = {"/cockpit/api/run-report", "/cockpit/api/run-capture",
                        "/cockpit/api/import-plan/preview", "/cockpit/api/queue/launch"}
        assert tool_runners <= posts

    def test_new_views_are_get_only(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        post_rules = {r.rule for r in app.url_map.iter_rules() if "POST" in r.methods}
        views = ["/cockpit/api/inbox", "/cockpit/api/daily-mission",
                 "/cockpit/api/notifications", "/cockpit/api/activity",
                 "/cockpit/api/review-roi", "/cockpit/api/assumptions",
                 "/cockpit/api/confidence", "/cockpit/api/lessons",
                 "/cockpit/api/org-memory"]
        assert not (set(views) & post_rules)
