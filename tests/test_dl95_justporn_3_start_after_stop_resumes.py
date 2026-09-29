"""dl95-justporn-3 (O1513 on test2, DOT95-LANE/live-dl95-justporn-1/LIVE-RESULT-A5-A.md + JPS2__1-start.png):
after Stop, a later Start "returned {ok:true} but the site stayed 'stopped' with 0 workers", and the interrupted
jobs were never re-claimed.

Why: stop() parks every pending/running job as "stopped" ("Stopped"), and start() admits only "pending". So
Stop -> Start found nothing to run, returned early without a worker, and the start route still answers
{"ok": true}. The fix: Start re-queues what Stop parked (and a "running" job when no worker of the runner is
alive). A user pause (bulk_pause, ``_paused_by_user``) stays paused, and a download-window re-entry does not
re-queue the job a live worker holds (lens REFUTE, .review/VERDICT-correctness-bd-worker-A9-A.md).

Real SiteRunner and real start()/stop(); only _worker_loop is a stand-in (no browser): it claims one URL from
the real queue, publishes "running / Finding download button..." through the real _update_job, then waits for
the stop signal, like a worker parked in page scraping.
"""

from __future__ import annotations

import time

import pytest

BD_GATE_SCOPE = "module"

URLS = [
    "https://www.justporn.com/video/22959/a",
    "https://www.justporn.com/video/28903/b",
]


@pytest.fixture
def runner(monkeypatch, tmp_path):
    from bulk_downloader import runner as rmod

    claimed = []

    def fake_worker_loop(self, idx, run_generation):
        item = self._url_queue.get()
        if item is None:
            return
        _gen, url = item
        claimed.append(url)
        self._update_job(
            url, "running", "Finding download button...", _run_generation=run_generation
        )
        self._stop.wait(10)

    monkeypatch.setattr(rmod.SiteRunner, "_worker_loop", fake_worker_loop)
    persisted = []
    real_bulk_update = rmod.queue_bulk_update

    def recording_bulk_update(site_id, urls, **kw):
        persisted.append((site_id, sorted(urls), kw.get("status")))
        return real_bulk_update(site_id, urls, **kw)

    monkeypatch.setattr(rmod, "queue_bulk_update", recording_bulk_update)
    r = rmod.SiteRunner(
        "jp3fixture",
        {
            "name": "justporn-fixture",
            "download_dir": str(tmp_path),
            "max_concurrent": 1,
            "disk_threshold_gb": 0,
            "auto_teach_first_run": False,
        },
    )
    r.load_urls(URLS)
    r.claimed = claimed
    r.persisted = persisted
    yield r
    r.stop()


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _statuses(r):
    return {u: r.jobs[u]["status"] for u in URLS}


def test_start_after_stop_resumes_the_interrupted_jobs(runner):
    runner.start()
    assert _wait(lambda: runner.claimed), (
        "precondition: the first start did not run a worker"
    )
    runner.stop()
    assert set(_statuses(runner).values()) == {"stopped"}, _statuses(runner)
    assert _wait(lambda: not any(t.is_alive() for t in runner._worker_threads)), (
        "fixture worker did not exit"
    )

    outcome = runner.start()
    assert outcome is None
    assert runner.state() == "running", (
        f"Start after Stop left the site {runner.state()!r} with jobs {_statuses(runner)}: "
        "the API would still have answered ok:true"
    )
    assert _wait(lambda: len(runner.claimed) == 2), (
        f"the parked job was never re-claimed: {runner.claimed}"
    )
    assert all(s in ("pending", "running") for s in _statuses(runner).values()), (
        _statuses(runner)
    )
    # The queue table learns it too, so a restart does not bring the parked state back.
    assert ("jp3fixture", sorted(URLS), "pending") in runner.persisted, runner.persisted


def test_a_user_pause_stays_paused(runner):
    assert runner.bulk_pause([URLS[1]]) == 1
    runner.stop()
    runner.start()
    assert runner.jobs[URLS[1]]["status"] == "stopped" and runner.jobs[URLS[1]].get(
        "_paused_by_user"
    )
    assert _wait(lambda: runner.claimed == [URLS[0]]), runner.claimed


def test_a_stale_running_job_is_requeued(runner):
    # A "running" job with no live worker (a stale write) is re-claimable on Start.
    assert not any(t.is_alive() for t in runner._worker_threads), "precondition: no live owner"
    with runner._lock:
        runner.jobs[URLS[0]].update(
            {"status": "running", "message": "Finding download button..."}
        )
    runner.start()
    assert _wait(lambda: URLS[0] in runner.claimed), runner.claimed


def test_other_stopped_jobs_are_not_resurrected(runner):
    # Only Stop's own parking ("Stopped") is undone; a job stopped for another reason keeps its state.
    with runner._lock:
        runner.jobs[URLS[1]].update(
            {"status": "stopped", "message": "Removed by retention"}
        )
    runner.start()
    assert _wait(lambda: runner.claimed == [URLS[0]])
    time.sleep(0.2)
    assert runner.jobs[URLS[1]]["status"] == "stopped"


def test_window_reentry_does_not_reclaim_a_job_a_live_worker_holds(runner):
    # app.py's download window leaves with _state="window_paused" (workers stay alive) and re-enters with start().
    runner.start()
    assert _wait(lambda: len(runner.claimed) == 1), runner.claimed
    held = runner.claimed[0]
    assert _wait(lambda: runner.jobs[held]["status"] == "running")
    live = [t for t in runner._worker_threads if t.is_alive()]
    assert live, "precondition: the worker holding the job is alive"

    runner._state = "window_paused"
    runner.start()
    _wait(lambda: len(runner.claimed) >= 3, timeout=1.5)

    assert any(t.is_alive() for t in live), "precondition: the original worker is still live"
    assert runner.claimed.count(held) == 1, (
        f"a job held by a LIVE worker was re-queued and claimed again: "
        f"claims={runner.claimed!r} status={runner.jobs[held]!r}"
    )
