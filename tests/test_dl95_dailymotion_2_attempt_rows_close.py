"""dl95-dailymotion-2 (RESULT-dailymotion.md#D2): a run whose job failed must get finished_at + a terminal status.

The prior cut (bd-cx-worker-2, run_history.reconcile_restored_runs) was REFUTED by lens A6-A: the rows the tester saw
stay "running" were duplicate twins written by the MOD3 shadow-read seam, not restart orphans. That seam and the
boot reconcile are both on the lane now (row127-shadow-rewrite, 069736ece); the MOD3 test below pins the twin.

What still leaves a run row open is the attempt that FAILS AND RETRIES: _handle_failure moves the job
running -> pending (retries+1). The run-history hook closed a run only on a terminal job status, so that attempt's
row stayed status=running, finished_at=null for the life of the service, and the next claim opened another row.

These tests drive the REAL claim transition, the REAL failure handler and the REAL run_history store in a tmp BD
home. No browser, no network (the MOD3 DSN points at a closed local port).
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

URL = "https://example.invalid/dl95-dailymotion-2/video"
UNREACHABLE = "postgresql://nobody@127.0.0.1:1/none"


def _make_runner(clean_workdir, monkeypatch, mod3=False):
    from bulk_downloader import run_history
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    if mod3:
        # The .95 profile of the capture: dual-write + shadow-read + cutover requested.
        monkeypatch.setenv("MOD3_PG_DSN", UNREACHABLE)
        monkeypatch.setenv("MOD3_SHADOW_READ", "1")
        monkeypatch.setenv("MOD3_CUTOVER", "1")
    else:
        monkeypatch.setenv("MOD3_PG_DSN", "")
        monkeypatch.setenv("MOD3_SHADOW_READ", "0")
        monkeypatch.delenv("MOD3_CUTOVER", raising=False)
    db_init()
    run_history.init()  # the app does this at boot; without it every run write fails open
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    r = SiteRunner("dm2", {"name": "dm2", "max_retries": 2})
    r._drain_url_queue()
    r.jobs[URL] = {"status": "pending", "message": ""}
    return r


@pytest.fixture
def runner(clean_workdir, monkeypatch):
    return _make_runner(clean_workdir, monkeypatch)


def _claim(r):
    """The worker claim path: _claim_worker_item, then the transition _process_worker_url publishes."""
    r.jobs[URL]["retry_after"] = 0
    outcome, _gen = r._claim_worker_item(0, URL)
    assert outcome == "claimed", outcome
    r._update_job(URL, "running", "Claimed by worker",
                  _transition_prev_status="pending", _memory_already_updated=True)


def _runs():
    from bulk_downloader import db
    with db.db_conn() as cx:
        return [tuple(row) for row in cx.execute(
            "SELECT id, status, finished_at FROM job_runs WHERE url=? ORDER BY id", (URL,))]


def _finish_events(rid):
    from bulk_downloader import db
    with db.db_conn() as cx:
        return cx.execute("SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='finish'",
                          (rid,)).fetchone()[0]


def _claimed(r):
    _claim(r)
    runs = _runs()
    # Positive control: the claim DID open a run row, so a closed row below is a measurement, not a no-op.
    assert len(runs) == 1 and runs[0][1] == "running" and runs[0][2] is None, runs
    assert r.jobs[URL].get("_run_id") == runs[0][0], r.jobs[URL]
    return runs[0][0]


def test_a_failed_attempt_that_retries_closes_its_run_row(runner):
    rid = _claimed(runner)
    runner._handle_failure(URL, "Connection timed out")
    job = runner.jobs[URL]
    assert job["status"] == "pending" and job.get("retries") == 1, f"retry branch not taken: {job}"
    runs = _runs()
    assert len(runs) == 1, runs
    _, status, finished = runs[0]
    assert finished is not None and status != "running", (
        f"D2_RETRY_ATTEMPT_LEFT_RUNNING: run {rid} status={status} finished_at={finished} after its attempt failed")
    assert status == "failed", runs
    assert _finish_events(rid) > 0, f"no finish event for run {rid}"
    assert "_run_id" not in job, f"closed attempt id still on the job: {job}"
    # A later terminal status with no new claim (operator cancel of the waiting retry) must not re-close it.
    closed = (_runs(), _finish_events(rid))
    runner._update_job(URL, "cancelled", "Cancelled by user")
    assert (_runs(), _finish_events(rid)) == closed, f"D2_CLOSED_ATTEMPT_RECLOSED: {closed} -> {_runs()}"


def test_retry_then_final_failure_leaves_no_running_row(runner):
    first = _claimed(runner)
    runner._handle_failure(URL, "Connection timed out")
    closed_first = (_runs()[0], _finish_events(first))
    _claim(runner)
    second = runner.jobs[URL].get("_run_id")
    assert second and second != first, runner.jobs[URL]
    runner._handle_failure(URL, "HTTP 404 Not Found")
    assert runner.jobs[URL]["status"] in ("failed", "tombstone"), runner.jobs[URL]
    runs = _runs()
    open_rows = [r for r in runs if r[1] == "running" or r[2] is None]
    assert not open_rows, f"D2_RUNNING_ROW_AFTER_FAILED_JOB: {open_rows} of {runs}"
    assert [r[0] for r in runs] == [first, second], runs
    # The later attempt must not re-close the first one (its id was popped when it ended).
    assert (runs[0], _finish_events(first)) == closed_first, (closed_first, runs)
    assert runs[1][1] == "tombstone" and _finish_events(second) > 0, runs


def test_needs_review_exit_closes_the_attempt(runner):
    rid = _claimed(runner)
    runner._update_job(URL, "needs_review", "No download button found")
    runs = _runs()
    assert runs[0][2] is not None, f"D2_REVIEW_ATTEMPT_LEFT_RUNNING: {runs}"
    assert runs[0][1] == "needs_review", runs
    assert _finish_events(rid) > 0, runs


def test_progress_inside_an_attempt_keeps_the_row_open(runner):
    """Negative control: only LEAVING running ends an attempt."""
    rid = _claimed(runner)
    runner._update_job(URL, "running", "Downloading 10%", file_size=1000)
    assert _runs() == [(rid, "running", None)], _runs()
    assert runner.jobs[URL].get("_run_id") == rid, runner.jobs[URL]


def test_mod3_shadow_read_writes_one_run_per_attempt(clean_workdir, monkeypatch):
    """Lens A6-A F1: under MOD3_SHADOW_READ=1 + MOD3_CUTOVER=1 the seam re-ran the INSERT, leaving a 'running' twin."""
    r = _make_runner(clean_workdir, monkeypatch, mod3=True)
    r.config["max_retries"] = 0  # the capture's shape: first attempt fails terminally as "failed"
    _claim(r)
    r._handle_failure(URL, "Connection timed out")
    assert r.jobs[URL]["status"] == "failed", r.jobs[URL]
    runs = _runs()
    assert len(runs) == 1, f"D2_DUPLICATE_RUN_TWIN: {runs}"
    assert runs[0][1] == "failed" and runs[0][2] is not None, f"D2_MOD3_RUN_LEFT_OPEN: {runs}"


# GEN 2 (lens A10-A R1): the attempt id must leave the job INSIDE the status
# writer. Worker B's claim is forced into the window between A's lock release
# and A's run-history hook (A's post-lock log_event), where B stores rid_B.
RACE_URL = "https://example.invalid/dl95-dailymotion-2/race"


def _race_runs():
    from bulk_downloader import db
    with db.db_conn() as cx:
        return [tuple(row) for row in cx.execute(
            "SELECT id, status, finished_at FROM job_runs WHERE url=? ORDER BY id",
            (RACE_URL,))]


def _race_claim(r, idx):
    r.jobs[RACE_URL]["retry_after"] = 0
    outcome, _gen = r._claim_worker_item(idx, RACE_URL)
    assert outcome == "claimed", outcome
    r._update_job(RACE_URL, "running", "Claimed by worker",
                  _transition_prev_status="pending", _memory_already_updated=True)


def test_a_release_never_closes_the_next_claims_row(clean_workdir, monkeypatch):
    from bulk_downloader import run_history
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner
    monkeypatch.setenv("MOD3_PG_DSN", "")
    monkeypatch.setenv("MOD3_SHADOW_READ", "0")
    monkeypatch.delenv("MOD3_CUTOVER", raising=False)
    db_init()
    run_history.init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    r = SiteRunner("dm2race", {"name": "dm2race", "max_retries": 2})
    r._drain_url_queue()
    r.jobs[RACE_URL] = {"status": "pending", "message": ""}
    _race_claim(r, 0)
    rid_a = r.jobs[RACE_URL]["_run_id"]

    real_log_event = r.log_event
    fired = []

    def log_event(kind, message, url=None, extra=None):
        real_log_event(kind, message, url=url, extra=extra)
        if kind == "state" and str(message).startswith("pending:") and not fired:
            fired.append(1)
            _race_claim(r, 1)   # worker B claims inside A's post-lock window

    monkeypatch.setattr(r, "log_event", log_event)
    r._update_job(RACE_URL, "pending", "Cluster rate limit hit (4/4)")
    assert fired, "D2_RACE_WINDOW_NOT_EXERCISED"
    runs = _race_runs()
    by_id = {row[0]: row for row in runs}
    rid_b = max(by_id)
    assert rid_b != rid_a and r.jobs[RACE_URL]["status"] == "running", (runs, r.jobs[RACE_URL])
    assert by_id[rid_a][2] is not None, f"D2_RACE_A_LEFT_OPEN: A's row {by_id[rid_a]} of {runs}"
    assert by_id[rid_b][2] is None, f"D2_RACE_B_CLOSED_WHILE_RUNNING: B's row {by_id[rid_b]} of {runs}"
    assert r.jobs[RACE_URL].get("_run_id") == rid_b, (
        f"D2_RACE_B_ID_STOLEN: {r.jobs[RACE_URL].get('_run_id')} runs={runs}")
