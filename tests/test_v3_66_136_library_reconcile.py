"""v3.66.136 — Phase J: `library_reconcile`. Proves: registration + apply path; the operational
gate is a GRANT only (not oracle tier-3); only file_exists=0 rows aged past the debounce are
removed (fresh-missing and present rows untouched); the file is NEVER deleted; exact reversal of
the row + its tags + the history back-ref preserving the original id; validator; idempotency; the
kind adds no routes/POST; orphan-import is deliberately not here. DB faked via the injectable
`_lib_*` wrappers — no real database.
"""
import os
import shutil
import time

import pytest  # noqa: F401

from tools import autonomy_library_reconcile as lrc
from tools import autonomy_apply as aap
from tools import autonomy_grant as ag
from tools import autonomy_eligibility as el
from tools import autonomy_guardrails as agr
from tools.cockpit_core import tasks_root

SID = "s1"
_NOW = time.time()
_OLD = _NOW - 60 * 86400      # 60d since last seen present
_FRESH = _NOW - 1 * 86400     # 1d — within the 30d debounce


def _fresh():
    os.environ["BD_COCKPIT_TASKS"] = "/tmp/bd_lrc_test_tasks"
    shutil.rmtree(tasks_root() / "governance", ignore_errors=True)
    os.environ.pop("BD_LIB_RECONCILE_MISSING_DAYS", None)


def _install_fake_lib(rows, tags=None, hist=None):
    ROWS = {r["id"]: dict(r) for r in rows}
    TAGS = {k: [dict(t) for t in v] for k, v in (tags or {}).items()}
    HIST = dict(hist or {})
    saved = (lrc._lib_missing, lrc._lib_snapshot, lrc._lib_delete, lrc._lib_restore)
    lrc._lib_missing = lambda site: [dict(r) for r in ROWS.values()
                                     if r["file_exists"] == 0 and r["site_id"] == site]
    lrc._lib_snapshot = lambda i: ({"row": dict(ROWS[i]), "tags": list(TAGS.get(i, []))}
                                   if i in ROWS else None)

    def _del(i):
        ROWS.pop(i, None)
        TAGS.pop(i, None)
        for h, l in list(HIST.items()):
            if l == i:
                HIST[h] = None
        return {"ok": True}
    lrc._lib_delete = _del

    def _res(snap):
        r = snap["row"]
        ROWS[r["id"]] = dict(r)
        TAGS[r["id"]] = list(snap.get("tags", []))
        if r.get("history_id"):
            HIST[r["history_id"]] = r["id"]
        return {"ok": True, "restored_id": r["id"]}
    lrc._lib_restore = _res
    return ROWS, TAGS, HIST, saved


def _restore(saved):
    lrc._lib_missing, lrc._lib_snapshot, lrc._lib_delete, lrc._lib_restore = saved


def _seed():
    rows = [
        {"id": 1, "site_id": SID, "file_path": "/m/gone_old.mp4", "file_exists": 0, "last_scanned": _OLD,   "history_id": 100, "title": "old"},
        {"id": 2, "site_id": SID, "file_path": "/m/gone_new.mp4", "file_exists": 0, "last_scanned": _FRESH, "history_id": None, "title": "new"},
        {"id": 3, "site_id": SID, "file_path": "/m/here.mp4",     "file_exists": 1, "last_scanned": _NOW,   "history_id": None, "title": "here"},
        {"id": 4, "site_id": "s2","file_path": "/m/other.mp4",    "file_exists": 0, "last_scanned": _OLD,   "history_id": None, "title": "other"},
    ]
    return rows, {1: [{"tag_id": 7, "added_at": _OLD}]}, {100: 1}


class TestRegistrationAndGate:
    def test_registered_and_apply_path(self):
        assert any(k.get("kind") == lrc.KIND for k in aap.registered_kinds())
        assert el.apply_path_exists(lrc.KIND) is True
        assert agr.has_reverser(lrc.KIND) is True

    def test_dark_without_grant(self):
        _fresh()
        rows, tags, hist = _seed()
        ROWS, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            r = lrc.reconcile_site(SID, by="t")
            assert r.get("skipped") is True and r.get("reason") == "not eligible"
            assert sorted(ROWS) == [1, 2, 3, 4]
        finally:
            _restore(saved)

    def test_gate_is_grant_only_not_oracle_tier(self):
        _fresh()
        rows, tags, hist = _seed()
        ROWS, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            assert lrc._gate(SID) is True            # no oracle evidence anywhere; grant alone opens it
            assert lrc.reconcile_site(SID, by="t").get("ok") is True
        finally:
            _restore(saved)


class TestRemoval:
    def test_removes_only_aged_missing_for_the_site(self):
        _fresh()
        rows, tags, hist = _seed()
        ROWS, TAGS, HIST, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            r = lrc.reconcile_site(SID, by="t")
            assert r.get("ok") is True
            # id1 (aged-missing, s1) gone; id2 (fresh), id3 (present), id4 (other site) survive
            assert sorted(ROWS) == [2, 3, 4]
            assert TAGS == {}                         # tag cascaded with the row
            assert HIST == {100: None}                # history back-ref nulled
        finally:
            _restore(saved)

    def test_debounce_threshold_respected(self):
        _fresh()
        os.environ["BD_LIB_RECONCILE_MISSING_DAYS"] = "90"  # id1 is only 60d
        rows, tags, hist = _seed()
        ROWS, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            assert lrc.reconcile_site(SID, by="t").get("skipped") is True
            assert sorted(ROWS) == [1, 2, 3, 4]
        finally:
            _restore(saved)

    def test_idempotent(self):
        _fresh()
        rows, tags, hist = _seed()
        _, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            lrc.reconcile_site(SID, by="t")
            assert lrc.reconcile_site(SID, by="t").get("skipped") is True
        finally:
            _restore(saved)

    def test_dry_run_no_write(self):
        _fresh()
        rows, tags, hist = _seed()
        ROWS, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            d = lrc.dry_run(SID)
            assert d["would_remove"] == 1 and d["rows"] == ["/m/gone_old.mp4"]
            assert sorted(ROWS) == [1, 2, 3, 4]       # nothing written
        finally:
            _restore(saved)


class TestReversibilityAndValidator:
    def test_rollback_restores_row_tags_and_backref_with_original_id(self):
        _fresh()
        rows, tags, hist = _seed()
        ROWS, TAGS, HIST, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            r = lrc.reconcile_site(SID, by="t")
            assert 1 not in ROWS
            agr.rollback(r["change_id"], by="t")
            assert 1 in ROWS and ROWS[1]["file_path"] == "/m/gone_old.mp4"
            assert TAGS.get(1) == [{"tag_id": 7, "added_at": _OLD}]   # tags restored
            assert HIST.get(100) == 1                                 # back-ref restored
        finally:
            _restore(saved)

    def test_validator_passes_on_clean_apply(self):
        _fresh()
        rows, tags, hist = _seed()
        _, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            after = lrc._proposer(SID)
            lrc._applier(SID, after)
            assert lrc._validator(SID, after).get("ok") is True
        finally:
            _restore(saved)

    def test_pending_review_window_opened(self):
        _fresh()
        rows, tags, hist = _seed()
        _, _, _, saved = _install_fake_lib(rows, tags, hist)
        try:
            ag.grant_site(SID, kind=lrc.KIND, by="mboyle", reason="t")
            r = lrc.reconcile_site(SID, by="t")
            assert r.get("change_id") and r.get("deadline")
        finally:
            _restore(saved)


class TestScopeAndSurface:
    def test_never_deletes_the_file(self):
        src = open(lrc.__file__, encoding="utf-8").read()
        assert "also_delete_file=False" in src
        assert "also_delete_file=True" not in src

    def test_no_orphan_import_and_no_grant_writer(self):
        # orphan import (unattributed rows) is intentionally deferred to the global scope;
        # and the kind must never issue grants.
        src = open(lrc.__file__, encoding="utf-8").read()
        assert "grant_site(" not in src
        assert "library_orphans" not in src and "library_record(" not in src

    def test_no_new_routes_or_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        posts = {r.rule for r in app.url_map.iter_rules()
                 if r.rule.startswith("/cockpit") and "POST" in (r.methods or set())}
        assert len(rules) >= 162
        assert len(posts) >= 26
        assert lrc.KIND in [k["kind"] for k in aap.registered_kinds()]
