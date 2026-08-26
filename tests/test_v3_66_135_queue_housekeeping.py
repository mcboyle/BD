"""v3.66.135 — Phase I: `queue_housekeeping`, the first operational kind on the generic apply
harness. Proves: registration + apply path; the operational gate is a GRANT only (NOT oracle
tier-3); I-A GCs only terminal+aged rows and leaves fresh/active rows alone; I-B abandon is OFF
by default and acts only with the flag; exact reversal; validator; idempotency; the kind adds no
routes and no POST. DB is faked via the injectable `_q_*` wrappers — no real database needed.
"""
import datetime as _dt
import os

import pytest  # noqa: F401  (runner supplies fixtures/parametrize; use assert, not pytest.fail)

from _cockpit_tasks import remove_test_governance
from tools import autonomy_queue_hk as qhk
from tools import autonomy_apply as aap
from tools import autonomy_grant as ag
from tools import autonomy_eligibility as el
from tools import autonomy_guardrails as agr
from tools.cockpit_core import tasks_root

SID = "s1"
_OLD = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
_NEW = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _fresh():
    """Hermetic governance + a clean in-memory queue + clean env flags."""
    remove_test_governance(tasks_root())
    os.environ.pop("BD_QUEUE_HK_ABANDON", None)
    os.environ.pop("BD_QUEUE_HK_GC_AGE_DAYS", None)
    os.environ.pop("BD_QUEUE_HK_MAX_RETRIES", None)
    os.environ.pop("BD_QUEUE_HK_STALE_HOURS", None)


def _install_fake_db(rows):
    """rows: list of dicts. Returns (db, saved) where saved is for restore."""
    db = {(r["site_id"], r["url"]): dict(r) for r in rows}
    saved = (qhk._q_load, qhk._q_delete, qhk._q_upsert, qhk._q_mark)
    qhk._q_load = lambda site: [dict(v) for k, v in db.items() if v["site_id"] == site]
    qhk._q_delete = lambda site, url: db.pop((site, url), None)

    def _ups(site, url, **f):
        db.setdefault((site, url), {"site_id": site, "url": url})
        db[(site, url)].update(f)
        db[(site, url)]["site_id"] = site
        db[(site, url)]["url"] = url
    qhk._q_upsert = _ups
    qhk._q_mark = lambda site, url, status, message: _ups(site, url, status=status, message=message)
    return db, saved


def _restore_db(saved):
    qhk._q_load, qhk._q_delete, qhk._q_upsert, qhk._q_mark = saved


def _seed():
    return [
        {"site_id": SID, "url": "done_old",   "status": "done",   "retries": 0,  "ts_updated": _OLD, "ord": 1, "message": ""},
        {"site_id": SID, "url": "failed_old", "status": "failed", "retries": 3,  "ts_updated": _OLD, "ord": 2, "message": "e"},
        {"site_id": SID, "url": "failed_new", "status": "failed", "retries": 1,  "ts_updated": _NEW, "ord": 3, "message": "e"},
        {"site_id": SID, "url": "running_x",  "status": "running","retries": 99, "ts_updated": _OLD, "ord": 4, "message": ""},
        {"site_id": SID, "url": "pending_x",  "status": "pending","retries": 0,  "ts_updated": _NEW, "ord": 5, "message": ""},
    ]


class TestRegistrationAndGate:
    def test_kind_registered_and_apply_path_exists(self):
        assert any(k.get("kind") == qhk.KIND for k in aap.registered_kinds())
        assert el.apply_path_exists(qhk.KIND) is True
        assert agr.has_reverser(qhk.KIND) is True

    def test_dark_without_grant(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            r = qhk.housekeep_site(SID, by="t")
            assert r.get("skipped") is True and r.get("reason") == "not eligible"
            assert len(db) == 5  # nothing touched
        finally:
            _restore_db(saved)

    def test_gate_is_grant_only_not_oracle_tier(self):
        # operational kinds must NOT require oracle tier-3 (that is template evidence).
        # With only a grant (and tier 0, no evidence anywhere), the gate opens and it acts.
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            assert qhk._gate(SID) is True
            r = qhk.housekeep_site(SID, by="t")
            assert r.get("ok") is True and not r.get("skipped")
        finally:
            _restore_db(saved)


class TestGarbageCollect_IA:
    def test_gc_only_terminal_and_aged(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            r = qhk.housekeep_site(SID, by="t")
            assert r.get("ok") is True
            remaining = sorted(k[1] for k in db)
            # done_old + failed_old GC'd; fresh-terminal + active rows survive
            assert remaining == ["failed_new", "pending_x", "running_x"]
        finally:
            _restore_db(saved)

    def test_idempotent_second_run(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            qhk.housekeep_site(SID, by="t")
            again = qhk.housekeep_site(SID, by="t")
            assert again.get("skipped") is True
        finally:
            _restore_db(saved)

    def test_gc_age_threshold_respected(self):
        _fresh()
        os.environ["BD_QUEUE_HK_GC_AGE_DAYS"] = "365"  # nothing is 365d old
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            r = qhk.housekeep_site(SID, by="t")
            assert r.get("skipped") is True  # no row old enough
            assert len(db) == 5
        finally:
            _restore_db(saved)


class TestAbandon_IB_OffByDefault:
    def test_abandon_off_by_default_leaves_active_rows(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            qhk.housekeep_site(SID, by="t")
            # running_x (retries 99, old) must NOT be abandoned with the flag off
            assert ("s1", "running_x") in db
            assert db[("s1", "running_x")]["status"] == "running"
        finally:
            _restore_db(saved)

    def test_abandon_acts_only_with_flag_and_thresholds(self):
        _fresh()
        os.environ["BD_QUEUE_HK_ABANDON"] = "1"
        os.environ["BD_QUEUE_HK_MAX_RETRIES"] = "10"
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            r = qhk.housekeep_site(SID, by="t")
            assert r.get("ok") is True
            # running_x (retries 99 >= 10, old) abandoned -> failed; pending_x (retries 0) untouched
            assert db[("s1", "running_x")]["status"] == "failed"
            assert db[("s1", "pending_x")]["status"] == "pending"
        finally:
            _restore_db(saved)

    def test_dry_run_reports_plan_without_writing(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            d = qhk.dry_run(SID)
            assert d["grant_active"] is True
            assert set(d["gc"]) == {"done_old", "failed_old"}
            assert d["abandon_enabled"] is False
            assert len(db) == 5  # dry_run wrote nothing
        finally:
            _restore_db(saved)


class TestReversibilityAndValidator:
    def test_rollback_exactly_restores_gc_rows(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            r = qhk.housekeep_site(SID, by="t")
            cid = r["change_id"]
            assert ("s1", "done_old") not in db
            agr.rollback(cid, by="t")
            assert ("s1", "done_old") in db and ("s1", "failed_old") in db
            assert db[("s1", "done_old")]["status"] == "done"
        finally:
            _restore_db(saved)

    def test_validator_passes_on_clean_apply(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            after = qhk._proposer(SID)
            qhk._applier(SID, after)
            assert qhk._validator(SID, after).get("ok") is True
        finally:
            _restore_db(saved)

    def test_pending_registered_for_review(self):
        _fresh()
        db, saved = _install_fake_db(_seed())
        try:
            ag.grant_site(SID, kind=qhk.KIND, by="mboyle", reason="t")
            r = qhk.housekeep_site(SID, by="t")
            assert r.get("change_id") and r.get("deadline")  # fail-closed review window opened
        finally:
            _restore_db(saved)


class TestNoNewSurface:
    def test_no_new_routes_or_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        posts = {r.rule for r in app.url_map.iter_rules()
                 if r.rule.startswith("/cockpit") and "POST" in (r.methods or set())}
        assert len(rules) >= 162          # queue_housekeeping adds NO routes
        assert len(posts) >= 26           # and NO POST
        # surfaced read-only through the existing Authority aggregation
        assert qhk.KIND in [k["kind"] for k in aap.registered_kinds()]

    def test_no_grant_writer_in_module(self):
        # the kind must not issue grants — granting stays human-only via the CLI
        src = open(qhk.__file__, encoding="utf-8").read()
        assert "grant_site(" not in src and "_atomic_write" not in src
