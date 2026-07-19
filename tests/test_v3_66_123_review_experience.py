"""v3.66.123 — Phase D: human review experience.

Read-only surfaces that make human approval of Class C changes better-informed — NOT
autonomy. Tests: evidence chain links snapshot + diff + review window + site evidence;
before/after diff renders added/removed; rollback preview does NOT execute; unified
decision audit spans sources; dashboard orders by deadline; the review DECISION still
flows through the existing audited path (no new mutation here); Class C auto still
impossible; wiring (+5 GET = 112, no POST).
"""
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools import autonomy_guardrails as gr
from tools import autonomy_review as rv
from tools.cockpit_core import tasks_root

_SRC = Path(rv.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(rv.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


def _make_change():
    """A realistic Class C change with a linked decision snapshot + pending review."""
    snap = ap.record_decision_snapshot(
        {"action_class": "C", "action": "selector_promotion", "site": "demosite",
         "scores_used": {"confidence": 0.91}, "thresholds_used": {"min": 0.8}}, "system")
    rec = gr.record_change("staging_json", "demosite_dl.json",
                           {"row_selectors": [".a"]}, {"row_selectors": [".a", ".b"]},
                           by="system", action_class="C", snapshot_id=snap["id"])
    gr.register_pending(rec["id"], "C", "demosite", "system")
    return rec["id"], snap["id"]


class TestEvidenceChain:
    def test_links_snapshot_diff_window(self):
        _fresh()
        cid, sid = _make_change()
        ec = rv.evidence_chain(cid)
        assert ec["ok"]
        assert ec["decision_snapshot"]["id"] == sid
        assert ec["decision_snapshot"]["policy_hash"]      # reconstructable why
        assert ec["review"]["deadline"]                    # fail-closed C
        assert ec["diff"]["row_selectors"]["to"] == [".a", ".b"]

    def test_missing_change(self):
        _fresh()
        assert rv.evidence_chain("nope")["ok"] is False

    def test_degrades_without_snapshot(self):
        _fresh()
        rec = gr.record_change("staging_json", "x.json", {"a": 1}, {"a": 2}, by="system",
                               action_class="C")  # no snapshot_id
        ec = rv.evidence_chain(rec["id"])
        assert "note" in ec["decision_snapshot"]   # honest 'none linked'


class TestDiff:
    def test_added_removed_rendered(self):
        _fresh()
        cid, _ = _make_change()
        d = rv.change_diff(cid)
        assert d["ok"]
        assert any(".b" in a for a in d["summary"]["added"])
        assert d["summary"]["removed"] == []


class TestRollbackPreviewDoesNotExecute:
    def test_preview_shows_restore_without_executing(self):
        _fresh()
        cid, _ = _make_change()
        rp = rv.rollback_preview(cid)
        assert rp["ok"]
        assert rp["would_restore"] == {"row_selectors": [".a"]}
        assert rp["currently_applied"] == {"row_selectors": [".a", ".b"]}
        # crucially: nothing was reverted
        assert gr.change_record(cid)["rolled_back"] is False

    def test_reports_reversible(self):
        _fresh()
        cid, _ = _make_change()
        assert rv.rollback_preview(cid)["reversible"] is True


class TestDecisionAudit:
    def test_spans_sources(self):
        _fresh()
        ap.set_policy_level("B", "approve_each", "mboyle", "edit")
        gr.record_change("staging_json", "x.json", {"a": 1}, {"a": 2}, by="system")
        gr.guardrail_failure("simulated", "system")          # a guardrail alert
        ap.unfreeze("mboyle", "clear")
        au = rv.decision_audit()
        sources = {e["source"] for e in au["events"]}
        assert {"policy", "change", "guardrail"} <= sources

    def test_sorted_desc(self):
        _fresh()
        ap.set_policy_level("B", "approve_each", "mboyle", "e1")
        ap.set_policy_level("B", "suggest", "mboyle", "e2")
        ts = [e["ts"] for e in rv.decision_audit()["events"] if e["ts"]]
        assert ts == sorted(ts, reverse=True)


class TestDashboard:
    def test_lists_pending_with_pointers(self):
        _fresh()
        cid, _ = _make_change()
        rd = rv.review_dashboard()
        assert rd["pending_count"] == 1
        item = rd["pending"][0]
        assert item["change_id"] == cid
        assert f"change_id={cid}" in item["evidence_chain"]

    def test_soonest_deadline_first(self):
        _fresh()
        # two pending; the one with the earlier deadline should sort first
        c1, _ = _make_change()
        c2 = gr.record_change("staging_json", "y.json", {"a": 1}, {"a": 2}, by="system",
                              action_class="C")["id"]
        gr.register_pending(c2, "C", "site2", "system")
        d = gr._load_pending()
        d["pending"][c1]["deadline"] = "2000-01-01T00:00:00+00:00"  # earliest
        gr._save_pending(d)
        rd = rv.review_dashboard()
        assert rd["pending"][0]["change_id"] == c1


class TestPostureNoAutonomyNoMutation:
    def test_class_c_auto_still_impossible(self):
        _fresh()
        # Phase E completed the guardrail set, so the level flip itself succeeds — but
        # automation is not enabled: C stays at Approve-each by default and the per-site
        # eligibility gate is empty (autonomy_oracle.class_c_site_eligible).
        assert ap.load_policy()["levels"]["C"] == "approve_each"
        assert ap.can_autonomously("C")["allowed"] is False

    def test_module_does_not_mutate_or_decide(self):
        # the review module must not apply/rollback/decide — it only reads. Decisions
        # flow through the existing audited path (mark_reviewed / /api/review/decide).
        # (rollback_preview is read-only — it previews, never executes — so we ban the
        # actual mutating constructs, not the substring it shares.)
        for bad in ("def rollback(", "def mark_reviewed(", "def apply",
                    "_store_save(", "_save_pending(", "set_policy_level(",
                    "safety_demote(", ".freeze("):
            assert bad not in _SRC, f"review module must not {bad!r}"

    def test_no_live_fetch_or_push(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", "web_fetch", ".replay("):
            assert bad not in _SRC

    def test_references_existing_decision_path(self):
        assert "mark_reviewed" in _SRC or "/api/review/decide" in _SRC


class TestWiring:
    def test_endpoints_and_pages_present(self):
        for r in ("dashboard", "evidence", "diff", "rollback-preview", "audit"):
            assert f'@bp.get("/api/review/{r}")' in _CONSOLE
        for pg in ("reviewexp", "reviewevidence", "reviewrollback", "reviewaudit"):
            assert f"PAGES.{pg}" in _CONSOLE
        assert 'data-p="reviewexp"' in _CONSOLE

    def test_delegated_in_page_link_handler(self):
        # in-page [data-p] links are routed via a delegated handler (fixes the earlier
        # governance sub-view buttons too)
        assert "a[data-p]" in _CONSOLE and "dataset.bound" in _CONSOLE

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for r in ("dashboard", "evidence", "diff", "rollback-preview", "audit"):
            assert c.get(f"/cockpit/api/review/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("dashboard", "evidence", "diff", "rollback-preview", "audit"):
            assert f"/cockpit/api/review/{r}" not in posts
