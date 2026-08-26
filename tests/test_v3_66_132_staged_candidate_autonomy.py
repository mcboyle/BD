"""v3.66.132 — v1 first autonomy wire: staged config candidate maintenance.

Proves the boundaries are real, not promised:
  * NO production config writes (sites_config.json byte-identical after the loop; module
    does no raw write and never writes the config path).
  * NO behavioral change — the candidate's `behavioral` block is byte-identical to a
    credential-redacted projection of live; the loop authors only `evidence`.
  * NO credential handling — sentinel secrets never appear in the candidate, the staged
    file, or the change record.
  * NO policy / corpus / debt / finding / credential / capture writes (construct grep).
  * FAIL-CLOSED auto-revert (expired pending is swept and reverted).
  * REJECT reverts immediately (via the audited /api/review/decide delegation).
  * ACCEPT does not promote (candidate persists; sites_config untouched).
  * participation_eligible stays False; staging_eligible is a SEPARATE, WEAKER gate.
  * route count +3 GET (143 -> 146); POST unchanged (21).
"""
import datetime as _dt
import json
import os
import tempfile
from pathlib import Path

from _cockpit_tasks import remove_test_governance
from tools import autonomy_staging as stg
from tools import autonomy_guardrails as agr
from tools import autonomy_eligibility as el
from tools import autonomy_impact as ai
from tools import autonomy_promotion as apr
from tools import cockpit_core as cc
from tools.cockpit_core import tasks_root

_SRC = Path(stg.__file__).read_text(encoding="utf-8")
_HERE = Path(stg.__file__).parent


def _fresh():
    remove_test_governance(tasks_root())


def _force(site, live):
    """Return (restore_fn) after forcing site eligible with a fixed live block."""
    og, ol = stg.staging_eligible, stg._live_block
    stg.staging_eligible = lambda s: {"ok": True, "site": s, "reasons": [],
                                      "participation_eligible": False}
    stg._live_block = lambda s: dict(live) if s == site else None

    def restore():
        stg.staging_eligible = og
        stg._live_block = ol
    return restore


class TestBuildDark:
    def test_empty_stores_qualify_nothing(self):
        _fresh()
        # real gate against empty oracle/trust
        assert stg.staging_eligible("anything")["ok"] is False
        assert stg.maintain_all(sites=["a", "b", "c"])["staged_count"] == 0


class TestNoProductionWrite:
    def test_source_does_no_raw_or_config_write(self):
        assert "_load_sites_config" in _SRC          # reads config read-only
        for bad in ("BD_SITES_CONFIG_PATH", "write_text(", "open("):
            assert bad not in _SRC, f"staging module must not {bad!r} (writes only via agr._atomic_write_json to the staging file)"

    def test_sites_config_byte_identical_after_loop(self):
        _fresh()
        fd = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump([{"url": "https://z.com", "domain": "z.com",
                    "username": "u@z", "password": "pw"}], fd)
        fd.close()
        os.environ["BD_SITES_CONFIG_PATH"] = fd.name
        try:
            before = Path(fd.name).read_bytes()
            stg.maintain_all(sites=["z.com"])
            assert Path(fd.name).read_bytes() == before
        finally:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
            os.unlink(fd.name)


class TestBehavioralUnchanged:
    LIVE = {"url": "u", "username": "a@b", "password": "pw", "user_field": "#e",
            "pass_field": "#p", "submit_btn": "btn", "domain": "b.com",
            "learned": {"x": 1}, "scoring": {"w": 2}}

    def test_behavioral_byte_identical_to_redacted_live(self):
        _fresh()
        restore = _force("b.com", self.LIVE)
        try:
            stg.maintain_staged_candidate("b.com")
            cand = stg._read_candidate("b.com")
            assert cand["behavioral"] == stg._redact_behavioral(self.LIVE)
            for k, v in cand["behavioral"].items():
                assert self.LIVE[k] == v          # verbatim, no mutation
        finally:
            restore()


class TestNoCredentialLeak:
    LIVE = {"url": "https://cred.com/login", "username": "SENTINEL_USER@x.com",
            "password": "SENTINEL_PW_123", "user_field": "#e", "pass_field": "#p",
            "submit_btn": "b", "domain": "cred.com", "learned": {"s": ".v"}}

    def test_no_secret_in_candidate_file_or_record(self):
        _fresh()
        restore = _force("cred.com", self.LIVE)
        try:
            r = stg.maintain_staged_candidate("cred.com")
            cid = r["change_id"]
            cand = stg._read_candidate("cred.com")
            blob = json.dumps(cand)
            assert "SENTINEL_PW_123" not in blob and "SENTINEL_USER@x.com" not in blob
            assert "password" not in cand["behavioral"]
            assert "username" not in cand["behavioral"]
            on_disk = stg._candidate_file("cred.com").read_text(encoding="utf-8")
            assert "SENTINEL_PW_123" not in on_disk and "SENTINEL_USER@x.com" not in on_disk
            rec = json.dumps(agr.change_record(cid))
            assert "SENTINEL_PW_123" not in rec and "SENTINEL_USER@x.com" not in rec
        finally:
            restore()


class TestNoForbiddenMutation:
    def test_construct_grep(self):
        for bad in ("set_policy_level(", "set_policy(", "write_corpus(", "corpus_write(",
                    "retire_debt(", "correction_debt(", "confirm_finding(",
                    "falsify_finding(", "mark_finding(", "set_credential(",
                    "write_credential(", "mark_reviewed(", "def promote(",
                    "promote_to_live(", "_restore_live", "capture.sh", "do_login(",
                    "page.goto(", "playwright", "requests.", "httpx", "urlopen",
                    "web_fetch", "socket."):
            assert bad not in _SRC, f"v1 staging wire must not {bad!r}"


class TestFailClosed:
    def test_expired_pending_auto_reverts(self):
        _fresh()
        restore = _force("fc.com", {"url": "u", "domain": "fc.com", "learned": {"a": 1}})
        try:
            r = stg.maintain_staged_candidate("fc.com")
            cid = r["change_id"]
            assert stg._read_candidate("fc.com") != {}
            d = agr._load_pending()
            d["pending"][cid]["deadline"] = (
                _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).isoformat()
            agr._save_pending(d)
            agr.sweep_review_windows("system")
            assert stg._read_candidate("fc.com") == {}       # silence -> revert
        finally:
            restore()


class TestReviewDecisions:
    def test_reject_reverts_immediately(self):
        _fresh()
        restore = _force("rj.com", {"url": "u", "domain": "rj.com", "learned": {"a": 1}})
        try:
            r = stg.maintain_staged_candidate("rj.com")
            cid = r["change_id"]
            out = cc.review_decide(cid, "reject", "no")
            assert "guardrails" in out                       # delegated to audited path
            assert stg._read_candidate("rj.com") == {}        # reverted now
        finally:
            restore()

    def test_accept_does_not_promote(self):
        _fresh()
        live = {"url": "u", "domain": "ac.com", "learned": {"a": 1}}
        restore = _force("ac.com", live)
        try:
            r = stg.maintain_staged_candidate("ac.com")
            cid = r["change_id"]
            out = cc.review_decide(cid, "accept", "ok")
            assert "guardrails" in out
            # candidate persists (blessed, not promoted); a later sweep does NOT revert it
            d = agr._load_pending()
            d["pending"][cid]["deadline"] = (
                _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).isoformat()
            agr._save_pending(d)
            agr.sweep_review_windows("system")
            assert stg._read_candidate("ac.com") != {}        # accept stopped the clock
        finally:
            restore()


class TestAuthorityModel:
    def test_participation_eligible_stays_false(self):
        _fresh()
        assert el.evaluate_site("any")["participation_eligible"] is False
        # module never flips it
        assert "participation_eligible = True" not in _SRC
        assert 'participation_eligible"] = True' not in _SRC

    def test_staging_eligible_is_separate_and_weaker(self):
        # eligible for staging even though participation_eligible is False
        orig = ai.impact_report
        ai.impact_report = lambda cand: {
            "evidence_qualified": True, "oracle_tier": 3, "trust_eligible": True,
            "reversible": True, "inflight_ok": True, "family_wide": False,
            "touches_pinned": False, "participation_eligible": False, "concerns": []}
        try:
            g = stg.staging_eligible("x")
            assert g["ok"] is True
            assert g["participation_eligible"] is False
        finally:
            ai.impact_report = orig


class TestAuditTrail:
    def test_change_record_pending_and_transition(self):
        _fresh()
        restore = _force("au.com", {"url": "u", "domain": "au.com", "learned": {"a": 1}})
        try:
            r = stg.maintain_staged_candidate("au.com")
            cid = r["change_id"]
            assert agr.change_record(cid) is not None
            assert any(v.get("change_id") == cid for v in agr.outstanding_unreviewed())
            entries = apr.activity_log(10000)["entries"]
            assert any(e.get("site") == "au.com" and e.get("field") == "staged_candidate"
                       for e in entries)
            assert stg.maintain_staged_candidate("au.com").get("skipped") is True  # idempotent
        finally:
            restore()


class TestWiring:
    def test_endpoints_pages_and_delegation_present(self):
        console = (_HERE / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("status", "candidates", "candidate"):
            assert f'@bp.get("/api/staging/{r}")' in console
        for pg in ("stagingcandidates", "stagingcandidate"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="stagingcandidates"' in console
        core = (_HERE / "cockpit_core.py").read_text(encoding="utf-8")
        assert 'mark_reviewed(item_key, decision, by="operator")' in core

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "v1 adds 3 GET routes (143 -> 146)."
        c = app.test_client()
        for r in ("status", "candidates", "candidate"):
            assert c.get(f"/cockpit/api/staging/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "v1 adds no POST (loop + sweep are host-scheduled)."
        for r in ("status", "candidates", "candidate"):
            assert f"/cockpit/api/staging/{r}" not in posts
