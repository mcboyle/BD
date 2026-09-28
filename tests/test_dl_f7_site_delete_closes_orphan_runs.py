"""dl-f7 (O1508 download test, findings/APP-DOWNLOAD-TEST-20260928.md#F7) -- deleting a site
must close its still-"running" job_runs rows.

Measured: DELETE /api/sites/ec82cec5 left 60 job_runs rows, 44 of them "running" forever --
/api/runs?status=running keeps listing downloads of a site that no longer exists, and
nothing can ever finish them (the runner that would call record_run_finish is gone).

The runs are CLOSED, not purged: job_runs is the run-history record, and history /
session_history / retention_audit are retained BY DESIGN (dev_suite.db_tools.orphan_rows,
D-7). Closed rows keep site_id + url, carry status "cancelled" and reason_code
"site_deleted". The history rows keep their stored site_name, so a deleted site's
downloads stay attributable.

Both delete paths are covered: api_delete and the inlined teardown in api_sites_v2_bulk.
"""

import pytest

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.bd_module_wipe


def _bd():
    import bulk_downloader.app_state as st
    from bulk_downloader import app_sites_id_core as core
    from bulk_downloader import db
    from bulk_downloader import run_history as rh
    from bulk_downloader.app import app

    db.db_init()
    rh.init()
    return st, app, core, db, rh


class _FakeRunner:
    """The bulk delete branch skips any sid without a runner."""

    def retire_scheduler(self, timeout=12.0):
        return True

    def retire_auto_retry(self, timeout=2.0):
        return True

    def retire_workers(self, timeout=5.0):
        return True

    def stop(self):
        pass

    def _stop_auto_retry(self):
        pass


def _runs(db, sid):
    with db.db_conn() as cx:
        return [
            dict(r)
            for r in cx.execute(
                "SELECT id, status, reason_code, finished_at, url FROM job_runs WHERE site_id=? ORDER BY id",
                (sid,),
            ).fetchall()
        ]


def _seed(st, db, rh, sid, name):
    st.s_cfg[sid] = {"url": "http://example.invalid", "name": name}
    st.s_meta[sid] = {"status": "idle"}
    running = [
        rh.record_run_start(sid, f"http://example.invalid/{sid}/{i}") for i in range(2)
    ]
    done = rh.record_run_start(sid, f"http://example.invalid/{sid}/done")
    rh.record_run_finish(done, "done")
    db.db_log(sid, name, f"http://example.invalid/{sid}/done", "failed", message="seed")
    assert all(running) and done, "seeding failed: run_history wrote nothing"
    return running, done


def _assert_closed(db, sid, running, done):
    rows = {r["id"]: r for r in _runs(db, sid)}
    assert set(rows) == {*running, done}, (
        f"dl-f7: run rows were purged, not closed: {rows}"
    )
    for rid in running:
        r = rows[rid]
        assert r["status"] == "cancelled", (
            f"dl-f7: run {rid} of deleted site left {r['status']!r}"
        )
        assert r["reason_code"] == "site_deleted", r
        assert r["finished_at"], r
    assert rows[done]["status"] == "done" and rows[done]["reason_code"] is None, (
        f"dl-f7: an already-finished run was rewritten: {rows[done]}"
    )


def test_api_delete_closes_running_runs_and_keeps_history():
    st, app, core, db, rh = _bd()
    gone, live = "dlf7_gone", "dlf7_live"
    running, done = _seed(st, db, rh, gone, "gone-site")
    live_running, _ = _seed(st, db, rh, live, "live-site")
    st.runners.pop(gone, None)

    with app.test_request_context(f"/api/sites/{gone}", method="DELETE"):
        core.api_delete(gone)

    assert gone not in st.s_cfg, "precondition failed: the site was never deleted"
    _assert_closed(db, gone, running, done)
    assert {r["status"] for r in _runs(db, live) if r["id"] in live_running} == {
        "running"
    }, "dl-f7: deleting one site closed another site's runs -- the close is unscoped"
    listed = {r["site_id"] for r in rh.list_runs(status="running")}
    assert gone not in listed and live in listed, listed
    hist = [h for h in db.db_search(site_id=gone) if h.get("site_id") == gone]
    assert hist and all(h.get("site_name") == "gone-site" for h in hist), (
        f"dl-f7: history rows of a deleted site must be retained with their site name: {hist}"
    )


def test_bulk_delete_closes_running_runs(monkeypatch):
    st, app, core, db, rh = _bd()
    gone = "dlf7_bulk_gone"
    running, done = _seed(st, db, rh, gone, "bulk-gone")
    st.runners[gone] = _FakeRunner()
    monkeypatch.setattr(core, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(core, "_rate_check", lambda *a, **k: True)

    with app.test_request_context(
        "/api/sites/v2/bulk",
        method="POST",
        json={"action": "delete", "site_ids": [gone]},
    ):
        core.api_sites_v2_bulk()

    assert gone not in st.s_cfg, (
        "precondition failed: the bulk handler never ran the delete branch"
    )
    _assert_closed(db, gone, running, done)


def test_close_site_runs_counts_what_it_closed():
    _st, _app, _core, db, rh = _bd()
    a = rh.record_run_start("dlf7_count", "http://example.invalid/1")
    b = rh.record_run_start("dlf7_count", "http://example.invalid/2")
    rh.record_run_finish(b, "failed")
    assert rh.close_site_runs("dlf7_count") == 1
    assert rh.close_site_runs("dlf7_count") == 0, (
        "a second close must find nothing left running"
    )
    assert rh.close_site_runs("dlf7_never_ran") == 0
    with db.db_conn() as cx:
        evs = [
            r["event_type"]
            for r in cx.execute(
                "SELECT event_type FROM run_events WHERE run_id=?", (a,)
            )
        ]
    assert "finish" in evs, "a closed run must carry a finish event on its timeline"
