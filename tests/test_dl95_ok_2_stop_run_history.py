"""Stopping an attempt closes its history without rewriting unrelated runs."""
import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe
URL = "https://example.test/video/1"


@pytest.fixture
def live_runner(tmp_path, monkeypatch):
    from bulk_downloader import db, run_history
    from bulk_downloader.runner import SiteRunner

    db.db_init()
    run_history.init()
    monkeypatch.setattr(SiteRunner, "start_scheduler", lambda self: None)
    monkeypatch.setattr(SiteRunner, "_start_auto_retry", lambda self: None)
    runner = SiteRunner("stop-history", {"download_dir": str(tmp_path / "downloads")})
    runner.jobs[URL] = {"status": "pending"}
    runner._update_job(URL, "running", "Claimed")
    rid = runner.jobs[URL]["_run_id"]
    assert run_history.get_run(rid)["status"] == "running"
    yield runner, rid, run_history
    runner.stop()


def _finish_events(history, rid):
    return [event for event in history.get_timeline(rid)
            if event["event_type"] == "finish"]


def test_stop_closes_running_attempt_once(live_runner):
    runner, rid, history = live_runner
    runner.stop()
    row = history.get_run(rid)
    assert row["status"] == "stopped", f"STOP_LEFT_ATTEMPT_OPEN: {row}"
    assert row["finished_at"] is not None
    first_events = _finish_events(history, rid)
    assert first_events, "Stop must record completion in the timeline"
    runner.stop()
    assert history.get_run(rid) == row
    assert _finish_events(history, rid) == first_events


# dl95-dailymotion-2 maps a per-job Cancel ("stopped") to a "cancelled" run.
@pytest.mark.parametrize("terminal, expected",
                         [("needs_review", "needs_review"), ("stopped", "cancelled")])
def test_attempt_ending_status_closes_history(live_runner, terminal, expected):
    runner, rid, history = live_runner
    runner._update_job(URL, terminal, "Attempt ended")
    row = history.get_run(rid)
    assert row["status"] == expected, f"TERMINAL_LEFT_ATTEMPT_OPEN: {row}"
    assert row["finished_at"] is not None
    first_events = _finish_events(history, rid)
    runner.stop()
    assert history.get_run(rid) == row
    assert _finish_events(history, rid) == first_events


def test_stop_preserves_completed_other_site_and_unstarted_jobs(live_runner):
    runner, rid, history = live_runner
    runner._update_job(URL, "done", "Downloaded")
    completed = history.get_run(rid)
    completed_events = history.get_timeline(rid)
    other_id = history.record_run_start("other-site", URL)
    other = history.get_run(other_id)
    runner.jobs["https://example.test/unstarted"] = {"status": "pending"}
    count = len(history.list_runs())
    runner.stop()
    assert history.get_run(rid) == completed
    assert history.get_timeline(rid) == completed_events
    assert history.get_run(other_id) == other
    assert len(history.list_runs()) == count
    assert runner.jobs["https://example.test/unstarted"]["status"] == "stopped"


def test_stale_worker_cannot_overwrite_stopped_history(live_runner):
    runner, rid, history = live_runner
    generation = runner._worker_run_generation
    runner.stop()
    before = history.get_run(rid)
    assert before["status"] == "stopped", f"STOP_LEFT_ATTEMPT_OPEN: {before}"
    timeline = history.get_timeline(rid)
    result = runner._update_job(URL, "done", "Late worker", _run_generation=generation)
    assert result is False
    assert history.get_run(rid) == before
    assert history.get_timeline(rid) == timeline
    assert runner.jobs[URL]["status"] == "stopped"


def test_retry_after_review_keeps_attempts_separate(live_runner):
    runner, first, history = live_runner
    runner._update_job(URL, "needs_review", "Review required")
    runner._update_job(URL, "pending", "Approved")
    runner._update_job(URL, "running", "Retry")
    second = runner.jobs[URL]["_run_id"]
    assert second != first
    runner.stop()
    assert history.get_run(first)["status"] == "needs_review"
    assert history.get_run(second)["status"] == "stopped"


def test_history_failure_cannot_break_stop(live_runner, monkeypatch):
    runner, rid, history = live_runner
    calls = []

    def broken_finish(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("isolated advisory failure")

    monkeypatch.setattr(history, "record_run_finish", broken_finish)
    runner.stop()
    assert calls, "Stop must attempt to close history"
    assert runner._stop.is_set()
    assert runner.jobs[URL]["status"] == "stopped"
    assert runner._state == "stopped"
