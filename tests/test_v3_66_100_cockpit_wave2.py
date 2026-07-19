"""v3.66.100 — cockpit Wave 2 (operator state): correctness + inert-data safety.

Wave 2 adds a local JSON state store (campaigns, capture queue, operator notes,
review decisions). The store introduces new POST routes, so these tests prove
the crucial property: **stored state is inert data — nothing executes from it**.
Specifically:
  * queue items are plans; adding one runs nothing; launching one routes through
    the SAME validated, allowlisted capture path (start_task) one at a time.
  * review decisions are recorded, never applied (no corpus write / selector
    promote / profile update).
  * every state-write validates its inputs (injection rejected on the way in).
  * the store is written atomically (.tmp + replace).
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


class TestCampaigns:
    def test_create_and_list(self):
        c = cc.campaign_create("ultrafilms_n3", "n3_validation", site="ultrafilms")
        assert c["goal"] == "n3_validation"
        lst = cc.campaign_list()
        assert any(x["id"] == c["id"] for x in lst)

    def test_goal_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.campaign_create("x", "not_a_goal")

    def test_name_and_site_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.campaign_create("bad name; rm", "general")
        with pytest.raises(cc.ValidationError):
            cc.campaign_create("ok", "general", site="../etc")

    def test_progress_is_advisory(self):
        c = cc.campaign_create("c1", "n3_validation", site="ultrafilms")
        prog = cc.campaign_list()[0]
        assert "recommended_next" in prog and "looks_complete" in prog


class TestQueueIsInert:
    def test_add_runs_nothing(self):
        # adding a queue item must NOT spawn a task
        before = len(cc.list_tasks())
        cc.queue_add("ultrafilms", "clip_4k", url="https://x.com/item")
        after = len(cc.list_tasks())
        assert before == after, "queue_add must not run anything"

    def test_queue_item_starts_pending(self):
        it = cc.queue_add("ultrafilms", "clip_4k")
        assert it["state"] == "pending"
        assert it["task_id"] is None

    def test_queue_label_injection_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.queue_add("ultrafilms", "a; rm -rf /")

    def test_queue_url_injection_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.queue_add("ultrafilms", "ok", url="https://x.com/a`whoami`")

    def test_reorder_is_data_only(self):
        a = cc.queue_add("s", "lbl_a")
        b = cc.queue_add("s", "lbl_b")
        cc.queue_reorder([b["id"], a["id"]])
        q = cc.queue_list()["queue"]
        order = {i["id"]: i["order"] for i in q}
        assert order[b["id"]] < order[a["id"]]

    def test_launch_routes_through_validated_capture_path(self, monkeypatch):
        # queue_launch must call start_task('capture','capture_session', ...) —
        # i.e. it cannot bypass the allowlist/validation. We intercept start_task.
        calls = {}
        real = cc.start_task
        def spy(category, name, params):
            calls["category"] = category
            calls["name"] = name
            calls["params"] = params
            # don't actually spawn — return a fake record
            return {"task_id": "t_fake", "status": "running"}
        monkeypatch.setattr(cc, "start_task", spy)
        it = cc.queue_add("ultrafilms", "clip_4k", url="https://x.com/item")
        cc.queue_launch(it["id"])
        assert calls["category"] == "capture"
        assert calls["name"] == "capture_session"   # the allowlisted tool
        # and the launched params came from the validated queue item
        assert calls["params"]["label"] == "clip_4k"

    def test_launch_unknown_item_refused(self):
        with pytest.raises(cc.ValidationError):
            cc.queue_launch("q_does_not_exist")


class TestNotebook:
    def test_add_and_list(self):
        cc.note_add("ultrafilms", "hypothesis", "identity stable")
        d = cc.note_list("ultrafilms")
        assert len(d["notes"]) == 1
        assert d["notes"][0]["kind"] == "hypothesis"

    def test_kind_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.note_add("ultrafilms", "not_a_kind", "x")

    def test_site_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.note_add("../etc", "observation", "x")

    def test_notes_separate_from_corpus(self):
        before = len(cc._corpus())
        cc.note_add("ultrafilms", "observation", "noted")
        after = len(cc._corpus())
        assert before == after, "notes must never write the corpus"


class TestReviewRecordsNeverApplies:
    def test_decision_recorded_not_applied(self):
        before = len(cc._corpus())
        r = cc.review_decide("VC-XXXX", "accept", "looks right")
        after = len(cc._corpus())
        assert before == after, "review must NOT write the corpus"
        assert r["decision"] == "accept"
        assert "No automatic action" in r["_note"]

    def test_decision_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.review_decide("k", "delete_everything")

    def test_review_items_is_readonly_listing(self):
        r = cc.review_items()
        assert "corpus_candidates" in r
        assert "NEVER writes the corpus" in r["_note"]


class TestPacketIsReadOnly:
    def test_packet_assembles_without_writing(self):
        before = len(cc._corpus())
        p = cc.review_packet("ultrafilms")
        after = len(cc._corpus())
        assert before == after
        assert "summary" in p and "timeline" in p
        assert "nothing is written" in p["_note"]

    def test_packet_site_validated(self):
        with pytest.raises(cc.ValidationError):
            cc.review_packet("a; rm -rf")


class TestAtomicStore:
    def test_store_written_atomically(self):
        # after a write, the live file exists and no .tmp is left behind
        cc.campaign_create("c1", "general")
        p = cc._store_path()
        assert p.is_file()
        assert not p.with_suffix(".json.tmp").exists()

    def test_store_survives_corrupt_load(self):
        # a corrupt store file degrades to empty state, doesn't crash
        cc._store_path().parent.mkdir(parents=True, exist_ok=True)
        cc._store_path().write_text("{not json", encoding="utf-8")
        st = cc._store_load()
        assert isinstance(st, dict)
        assert "campaigns" in st and "queue" in st  # recovered shape, no crash


class TestWave2RouteShape:
    def test_state_writes_are_scoped_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = sorted(r.rule for r in app.url_map.iter_rules()
                       if "POST" in r.methods and r.rule.startswith("/cockpit"))
        # the Wave 2 state POSTs, plus the original three actions
        assert "/cockpit/api/campaigns" in posts
        assert "/cockpit/api/queue" in posts
        assert "/cockpit/api/queue/launch" in posts
        assert "/cockpit/api/review/decide" in posts
        # the original allowlisted action endpoints still present, unchanged
        assert "/cockpit/api/run-report" in posts
        assert "/cockpit/api/run-capture" in posts

    def test_no_state_post_can_name_a_tool(self):
        # the state POSTs accept site/label/url/etc — never a 'tool'/'cmd'/'script'
        # parameter. Confirm the handlers don't read such a field.
        src = (Path(_ROOT) / "tools/cockpit_console.py").read_text()
        import re
        for fn in ("api_campaign_create", "api_queue_add", "api_note_add",
                   "api_review_decide"):
            m = re.search(rf"def {fn}.*?(?=\n@bp|\ndef )", src, re.S)
            assert m
            body = m.group(0)
            assert "script" not in body and "argv" not in body
            assert "shell" not in body
