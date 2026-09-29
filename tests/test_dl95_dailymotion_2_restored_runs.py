"""Restoring a site's queue terminates attempts owned by its previous runner."""

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe


def _store():
    from bulk_downloader import db, run_history
    from bulk_downloader.runner_queue import QueueMixin

    db.db_init()
    run_history.init()

    class RestoredRunner(QueueMixin):
        def __init__(self, site_id):
            self.site_id = site_id
            self.jobs = {}
            self.urls = []

    return db, run_history, RestoredRunner


@pytest.mark.parametrize("saved_status, expected", [("running", "cancelled"), ("failed", "cancelled")])
def test_restore_closes_previous_attempt_and_preserves_other_runs(saved_status, expected):
    db, rh, runner_type = _store()
    site, url = "d2-restored", "https://example.invalid/test"
    db.queue_upsert(site, url, status=saved_status, message="previous failure")
    stale = rh.record_run_start(site, url)
    completed = rh.record_run_start(site, url + "/done")
    rh.record_run_finish(completed, "done")
    live = rh.record_run_start("other-live-site", url)
    assert stale and completed and live, "D2_SEED_FAILED"
    runner = runner_type(site)
    runner._restore_queue()
    run = rh.get_run(stale)
    assert run["status"] == expected, f"D2_RESTART_ORPHAN: {run}"
    assert run["finished_at"], "D2_RESTART_MISSING_FINISH"
    assert run["reason_code"] == "service_restart"
    assert rh.get_run(completed)["status"] == "done"
    assert rh.get_run(live)["status"] == "running", "D2_OTHER_SITE_REWRITTEN"
    assert runner.jobs[url]["status"] == ("pending" if saved_status == "running" else "failed")
    before = rh.get_timeline(stale)
    assert [e["event_type"] for e in before].count("finish") == 1
    runner_type(site)._restore_queue()
    assert rh.get_timeline(stale) == before, "D2_RESTORE_DUPLICATES_FINISH"


def test_restore_empty_queue_closes_orphan_but_schema_init_does_not():
    _db, rh, runner_type = _store()
    stale = rh.record_run_start("empty-restored", "https://example.invalid/removed")
    assert stale, "D2_SEED_FAILED"
    rh.init()
    assert rh.get_run(stale)["status"] == "running", "D2_SCHEMA_INIT_CLOSED_ACTIVE_RUN"
    runner_type("empty-restored")._restore_queue()
    run = rh.get_run(stale)
    assert run["status"] == "cancelled", f"D2_EMPTY_QUEUE_ORPHAN: {run}"
    assert run["finished_at"]


def test_restore_history_error_is_advisory_and_transactional():
    db, rh, runner_type = _store()
    site, url = "d2-rollback", "https://example.invalid/rollback"
    db.queue_upsert(site, url, status="running")
    stale = rh.record_run_start(site, url)
    assert stale, "D2_SEED_FAILED"
    with db.db_conn() as cx:
        cx.execute("CREATE TRIGGER d2_reject_finish BEFORE INSERT ON run_events "
                   "WHEN NEW.event_type='finish' BEGIN "
                   "SELECT RAISE(ABORT, 'D2_FINISH_REJECTED'); END")
    try:
        runner = runner_type(site)
        runner._restore_queue()
        assert runner.jobs[url]["status"] == "pending", "D2_HISTORY_BLOCKED_RESTORE"
        run = rh.get_run(stale)
        assert run["status"] == "running" and run["finished_at"] is None
        assert [e["event_type"] for e in rh.get_timeline(stale)] == ["start"]
    finally:
        with db.db_conn() as cx:
            cx.execute("DROP TRIGGER d2_reject_finish")
