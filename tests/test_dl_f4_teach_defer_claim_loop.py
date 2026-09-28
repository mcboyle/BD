"""dl-f4 (findings/APP-DOWNLOAD-TEST-20260928.md#F4): a URL deferred by auto-teach looped.

While one URL sat in needs_review waiting for the operator to teach selectors, every other claimed URL hit
_handle_auto_teach_check's deferral branch, which reset it to pending with a raw dict write and put it straight
back on the worker queue. Two effects, both measured on test5 (harness-work/UIUX-20260928/download/):

- the raw write bypassed _update_job's run-history hook, so the job_runs row opened by the claim never closed
  (runs-loop.json: 20 rows for one URL, all status=running, finished_at=null);
- the immediate requeue made the workers re-claim the same URL every 5s until teach finished
  (events-claim-loop.json), each claim opening another run row.

These tests drive the REAL claim (_claim_worker_item + the _update_job transition _process_worker_url makes), the
REAL handler and the REAL run_history store in a tmp BD home. No browser, no network.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import queue
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

TEACH = "https://example.invalid/dl-f4-teach-target"
WAITER = "https://example.invalid/dl-f4-waiter"


class _Session:
    """The surface teach_commit / teach_cancel dereference on the takeover session."""

    target_url = TEACH

    def commit(self, timeout=None):
        return True, []

    def cancel(self, timeout=None):
        return None

    def finalize(self, timeout=None):
        return True, "ok", [], {}  # no learned selectors: the flow still ends, so parked URLs must still resume


@pytest.fixture
def runner(clean_workdir, monkeypatch):
    from bulk_downloader import run_history
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    # Isolated SQLite only: an inherited MOD3 dual-write profile (test2) re-executes the run INSERT through the
    # shadow connection and the run table then holds two rows per claim -- a failure of the host, not the subject.
    monkeypatch.setenv("MOD3_PG_DSN", "")
    monkeypatch.setenv("MOD3_SHADOW_READ", "0")
    db_init()
    run_history.init()  # the app does this at boot; without it every run write fails open and nothing is measured
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    r = SiteRunner("dlf4", {"name": "dlf4", "auto_teach_first_run": True})
    r._drain_url_queue()
    # teach_commit ends with start(); spawning browser workers is not this test's subject.
    monkeypatch.setattr(r, "start", lambda *a, **k: None)
    r.jobs[TEACH] = {"status": "needs_review", "message": "teach me", "auto_teach_seen": True}
    r.jobs[WAITER] = {"status": "pending", "message": ""}
    return r


def _claim(r, url):
    """The worker claim path: _claim_worker_item, then the transition _process_worker_url publishes."""
    outcome, _gen = r._claim_worker_item(0, url)
    assert outcome == "claimed", outcome
    r._update_job(url, "running", "Claimed by worker",
                  _transition_prev_status="pending", _memory_already_updated=True)


def _queued(r):
    items = []
    while True:
        try:
            item = r._url_queue.get_nowait()
        except queue.Empty:
            return items
        items.append(item[1] if isinstance(item, tuple) else item)


def _runs(url):
    from bulk_downloader import db
    with db.db_conn() as cx:
        return [tuple(row) for row in cx.execute(
            "SELECT id, status, finished_at FROM job_runs WHERE url=? ORDER BY id", (url,))]


def _defer(r):
    _claim(r, WAITER)
    runs = _runs(WAITER)
    # Positive control: the claim DID open a run row, so a closed/absent row below is a measurement, not a no-op.
    assert len(runs) == 1 and runs[0][1] == "running" and runs[0][2] is None, runs
    assert r.jobs[WAITER].get("_run_id") == runs[0][0], r.jobs[WAITER]
    assert r._handle_auto_teach_check(WAITER, r.jobs[WAITER]) is True, (
        "handler did not take the deferral branch; nothing was measured")
    assert r.jobs[WAITER]["status"] == "pending", r.jobs[WAITER]


def test_deferred_claim_closes_its_run_row(runner):
    _defer(runner)
    runs = _runs(WAITER)
    assert len(runs) == 1, runs
    rid, status, finished = runs[0]
    assert finished is not None, f"dl-f4: deferred claim left run {rid} open (status={status})"
    assert status == "deferred", runs
    assert "_run_id" not in runner.jobs[WAITER], runner.jobs[WAITER]


def test_deferred_url_is_not_requeued_while_teach_pending(runner):
    _defer(runner)
    q = _queued(runner)
    assert WAITER not in q, f"dl-f4: deferred URL went straight back on the worker queue: {q}"
    assert runner.jobs[TEACH]["status"] == "needs_review"


@pytest.mark.parametrize("end", ["teach_commit", "teach_cancel", "finish_manual_download", "cancel_manual_download"])
def test_parked_url_is_requeued_when_teach_ends(runner, end):
    _defer(runner)
    assert _queued(runner) == []
    runner._manual_download_session = _Session()
    if end == "teach_commit":
        ok, _msg = runner.teach_commit({})
    else:
        ok, _msg = getattr(runner, end)()
    assert ok, (end, _msg)
    q = _queued(runner)
    assert q.count(WAITER) == 1, f"{end}: parked URL not requeued exactly once: {q}"
    assert runner.jobs[WAITER]["status"] == "pending"
    assert "auto_teach_waiting" not in runner.jobs[WAITER]


def test_reclaim_after_release_opens_a_fresh_run(runner):
    """One claim, one run: after release the URL is claimable again and gets its own row."""
    _defer(runner)
    runner._manual_download_session = _Session()
    runner.teach_commit({})
    _queued(runner)
    _claim(runner, WAITER)
    runs = _runs(WAITER)
    assert [r[1] for r in runs] == ["deferred", "running"], runs
    assert runs[0][2] is not None and runs[1][2] is None, runs


def test_deleting_the_teach_target_releases_parked_urls(runner):
    """bd-cx-worker-1 REFUTE F1: bulk_delete of the only teach target left the parked URL pending with no queue entry."""
    _defer(runner)
    assert runner.bulk_delete([TEACH]) == 1
    q = _queued(runner)
    assert q.count(WAITER) == 1, f"dl-f4: PENDING_WAITER_STRANDED after teach target delete: {q} {runner.jobs[WAITER]}"
    assert "auto_teach_waiting" not in runner.jobs[WAITER]


def test_parked_urls_stay_parked_while_a_teach_target_remains(runner):
    """Negative control for the release: deleting an unrelated job must not release while TEACH is still pending."""
    runner.jobs["https://example.invalid/dl-f4-other"] = {"status": "pending", "message": ""}
    _defer(runner)
    assert runner.bulk_delete(["https://example.invalid/dl-f4-other"]) == 1
    runner._release_teach_waiters_if_unblocked()
    assert WAITER not in _queued(runner)
    assert runner.jobs[WAITER].get("auto_teach_waiting") is True


def test_idle_poll_releases_after_a_raw_status_change(runner):
    """Paths with no explicit release (mark/skip write the status directly): the idle-worker poll is the catch-all."""
    _defer(runner)
    runner.jobs[TEACH]["status"] = "done"  # the shape api_jobs_mark writes
    assert _queued(runner) == []
    runner._release_teach_waiters_if_unblocked()
    assert _queued(runner).count(WAITER) == 1


def test_worker_idle_poll_calls_the_release():
    """The catch-all is wired where idle workers poll (the queue.Empty handler), not only defined."""
    import ast
    import inspect
    import textwrap

    from bulk_downloader.runner import SiteRunner
    tree = ast.parse(textwrap.dedent(inspect.getsource(SiteRunner._worker_loop)))
    handlers = [h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
                and h.type is not None and ast.unparse(h.type) == "queue.Empty"]
    assert handlers, "no queue.Empty handler in _worker_loop; the probe cannot see the poll"
    calls = {ast.unparse(c.func) for h in handlers for n in h.body for c in ast.walk(n) if isinstance(c, ast.Call)}
    assert "self._release_teach_waiters_if_unblocked" in calls, f"dl-f4: idle poll has no release: {sorted(calls)}"
