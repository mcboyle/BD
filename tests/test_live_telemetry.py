"""Regression coverage for live runner telemetry on health endpoints."""
from __future__ import annotations

import threading
import queue
from pathlib import Path

import pytest


def _telemetry_runner(monkeypatch, *, url="https://example.test/video.mp4"):
    """Build the smallest real SiteRunner surface needed by telemetry tests."""
    from bulk_downloader import runner as runner_mod

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "telemetry"
    runner.config = {}
    runner.jobs = {
        url: {
            "status": "running",
            "message": "downloading",
            "file_size": 0,
        }
    }
    runner.urls = [url]
    runner._lock = threading.Lock()
    runner._worker_heartbeats = {}
    runner._worker_current_urls = {}
    runner._worker_url_generations = {}
    runner._worker_run_generation = 1
    runner._job_progress_samples = {}
    runner._job_status_version = 0
    runner._completion_notification_token = None
    runner._completion_notification_serial = 0
    runner._claimed_completion_notification = None
    runner._worker_heartbeats_lock = threading.Lock()
    runner._run_lifecycle_lock = threading.RLock()
    runner.log_event = lambda *args, **kwargs: None
    monkeypatch.setattr(runner_mod, "queue_upsert", lambda *args, **kwargs: None)
    return runner


class _RunnerWithStatus:
    def __init__(self, status):
        self._status = status

    def get_status(self, *, light):
        assert light is True
        return self._status


def test_health_uses_current_and_legacy_runner_count_schemas(fresh_app):
    """The v1 health probe sums nested counts without dropping legacy ones."""
    from bulk_downloader.app_state import runners

    runners["current"] = _RunnerWithStatus({"counts": {"pending": 7, "running": 1}})
    runners["legacy"] = _RunnerWithStatus({"queued": 3, "active": 2})

    body = fresh_app.get("/api/health").get_json()

    assert body["queue_depth"] == 10
    assert body["active_downloads"] == 3


def test_byte_advance_refreshes_only_mapped_worker_heartbeat(monkeypatch):
    from bulk_downloader import runner as runner_mod

    url = "https://example.test/video.mp4"
    runner = _telemetry_runner(monkeypatch, url=url)
    runner._worker_current_urls = {3: url, 7: "https://example.test/other.mp4"}
    runner._worker_heartbeats = {3: 10.0, 7: 20.0}
    now = [100.0]
    monkeypatch.setattr(runner_mod.time, "time", lambda: now[0])

    runner._update_job(url, "running", "first bytes", file_size=1024)

    assert runner._worker_heartbeats == {3: 100.0, 7: 20.0}
    assert runner._job_progress_samples[url]["bytes"] == 1024


def test_unchanged_bytes_do_not_refresh_worker_heartbeat(monkeypatch):
    from bulk_downloader import runner as runner_mod

    url = "https://example.test/video.mp4"
    runner = _telemetry_runner(monkeypatch, url=url)
    runner.jobs[url]["file_size"] = 1024
    runner._worker_current_urls = {3: url}
    runner._worker_heartbeats = {3: 10.0}
    runner._job_progress_samples[url] = {
        "bytes": 1024, "at": 10.0, "bps": 12.0,
    }
    monkeypatch.setattr(runner_mod.time, "time", lambda: 100.0)

    runner._update_job(url, "running", "same bytes", file_size=1024)

    assert runner._worker_heartbeats[3] == 10.0
    assert runner._job_progress_samples[url] == {
        "bytes": 1024, "at": 10.0, "bps": 12.0,
    }


def test_worker_url_mapping_clears_when_processing_raises(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    runner.jobs["https://example.test/video.mp4"]["status"] = "pending"

    def fail(*args, **kwargs):
        raise RuntimeError("download exploded")

    runner._process_one = fail

    try:
        runner._process_worker_url(4, object(), "https://example.test/video.mp4")
    except RuntimeError as exc:
        assert str(exc) == "download exploded"
    else:
        raise AssertionError("expected processing failure")

    assert runner._worker_current_urls == {}


def test_worker_mapping_clears_when_claim_transition_publication_raises(
        monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner.jobs[url]["status"] = "pending"
    processed = []

    def fail_transition(*args, **kwargs):
        raise RuntimeError("transition publication exploded")

    runner._update_job = fail_transition
    runner._process_one = lambda *args, **kwargs: processed.append(args)

    with pytest.raises(RuntimeError, match="transition publication exploded"):
        runner._process_worker_url(4, object(), url, run_generation=1)

    assert processed == []
    assert runner._worker_current_urls == {}
    assert runner._worker_url_generations == {}


def test_old_worker_generation_cannot_map_or_unmap_new_worker(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    old_url = "https://example.test/old.mp4"
    new_url = "https://example.test/new.mp4"
    entered = threading.Event()
    release = threading.Event()
    runner.jobs[old_url] = {"status": "pending", "file_size": 0}

    def block(*args, **kwargs):
        entered.set()
        assert release.wait(2.0)

    runner._process_one = block
    old_worker = threading.Thread(
        target=runner._process_worker_url,
        args=(4, object(), old_url),
        kwargs={"run_generation": 1},
    )
    old_worker.start()
    assert entered.wait(2.0)

    with runner._worker_heartbeats_lock:
        runner._worker_run_generation = 2
        runner._worker_current_urls.clear()
        runner._worker_url_generations.clear()
        runner._worker_current_urls[4] = new_url
        runner._worker_url_generations[4] = 2

    release.set()
    old_worker.join(2.0)
    assert not old_worker.is_alive()
    assert runner._worker_current_urls == {4: new_url}
    assert runner._worker_url_generations == {4: 2}

    calls = []
    runner._process_one = lambda *args, **kwargs: calls.append(args)
    processed = runner._process_worker_url(
        4, object(), old_url, run_generation=1)

    assert processed == runner._WORKER_CLAIM_STALE
    assert calls == []
    assert runner._worker_current_urls == {4: new_url}
    assert runner._worker_url_generations == {4: 2}


def test_watchdog_rejects_old_generation_and_changed_heartbeat_snapshots(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    runner._hung_workers = []
    old_snapshot = {2: 0.0}
    hung = [{"worker_idx": 2, "last_beat_age_s": 1000.0}]

    runner._worker_run_generation = 2
    assert runner._publish_watchdog_snapshot(1, old_snapshot, hung) is False
    assert runner._hung_workers == []

    runner._worker_heartbeats = {2: 999.0}
    assert runner._publish_watchdog_snapshot(2, old_snapshot, hung) is False
    assert runner._hung_workers == []

    current_snapshot = {2: 999.0}
    assert runner._publish_watchdog_snapshot(2, current_snapshot, hung) is True
    assert runner._hung_workers == hung


def test_old_watch_done_cannot_mutate_new_generation(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    new_url = "https://example.test/new.mp4"
    runner._worker_run_generation = 2
    runner._stop = threading.Event()
    runner._url_queue = queue.Queue()
    runner._url_queue.put((2, new_url))
    runner.jobs = {new_url: {"status": "pending", "retry_after": 0}}
    runner._state = "running"

    class Worker:
        def __init__(self):
            self.joins = 0

        def join(self, timeout=None):
            self.joins += 1

    old_worker = Worker()
    new_worker = Worker()
    runner._worker_threads = [new_worker]

    runner._watch_done(run_generation=1, worker_threads=(old_worker,))

    assert runner._url_queue.qsize() == 1
    assert runner._url_queue.unfinished_tasks == 1
    assert runner._worker_threads == [new_worker]
    assert old_worker.joins == 0
    assert new_worker.joins == 0
    assert runner._state == "running"


def test_generation_requeue_is_deduplicated_and_cannot_resurrect_stopped_work(
        monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner._worker_run_generation = 2
    runner._url_queue = queue.Queue()
    runner.jobs[url]["status"] = "pending"
    runner._url_queue.put((2, url))

    assert runner._requeue_generation_item(2, url) is False
    assert list(runner._url_queue.queue) == [(2, url)]
    assert runner._url_queue.unfinished_tasks == 1

    runner._url_queue.get_nowait()
    runner._url_queue.task_done()
    runner.jobs[url]["status"] = "stopped"
    assert runner._requeue_generation_item(2, url) is False
    assert runner._url_queue.empty()

    runner.jobs[url]["status"] = "pending"
    assert runner._requeue_generation_item(1, url) is False
    assert runner._url_queue.empty()


def test_generation_requeue_holds_job_lock_through_queue_publication(monkeypatch):
    """A stop/status writer cannot slip between eligibility and q._put."""
    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner._url_queue = queue.Queue()
    runner.jobs[url]["status"] = "pending"
    entered_put = threading.Event()
    release_put = threading.Event()
    status_changed = threading.Event()
    original_put = runner._url_queue._put

    def blocked_put(item):
        entered_put.set()
        assert release_put.wait(2.0)
        original_put(item)

    runner._url_queue._put = blocked_put

    requeue_thread = threading.Thread(
        target=runner._requeue_generation_item, args=(1, url))
    requeue_thread.start()
    assert entered_put.wait(2.0)

    def mark_stopped():
        with runner._lock:
            runner.jobs[url]["status"] = "stopped"
        status_changed.set()

    status_thread = threading.Thread(target=mark_stopped)
    status_thread.start()
    assert not status_changed.wait(0.1)

    release_put.set()
    requeue_thread.join(2.0)
    status_thread.join(2.0)
    assert not requeue_thread.is_alive()
    assert not status_thread.is_alive()
    assert runner.jobs[url]["status"] == "stopped"
    assert runner._generation_item_is_processable(1, url) is False


@pytest.mark.parametrize("old_status,retry_after", [
    ("done", 0),
    ("pending", 1),
])
def test_watch_done_commit_rejects_generation_changed_at_barrier(
        monkeypatch, old_status, retry_after):
    """An old overseer cannot finalize, restart, or notify a replacement run."""
    from bulk_downloader import plugins, push

    runner = _telemetry_runner(monkeypatch)
    runner._stop = threading.Event()
    runner._url_queue = queue.Queue()
    runner._worker_threads = []
    runner._state = "running"
    runner.config = {"name": "Telemetry"}
    runner.jobs = {
        "https://example.test/old.mp4": {
            "status": old_status, "retry_after": retry_after,
        },
    }
    entered_commit = threading.Event()
    release_commit = threading.Event()
    starts = []
    pushes = []
    emits = []
    original_finalize = runner._finalize_watch_done

    def blocked_finalize(*args, **kwargs):
        entered_commit.set()
        assert release_commit.wait(2.0)
        return original_finalize(*args, **kwargs)

    runner._finalize_watch_done = blocked_finalize
    runner.start = lambda: starts.append("restart")
    monkeypatch.setattr(push, "send_push", lambda **kwargs: pushes.append(kwargs))
    monkeypatch.setattr(plugins, "emit", lambda *args, **kwargs: emits.append(args))

    overseer = threading.Thread(
        target=runner._watch_done, kwargs={
            "run_generation": 1, "worker_threads": (),
        })
    overseer.start()
    assert entered_commit.wait(2.0)
    with runner._run_lifecycle_lock:
        with runner._worker_heartbeats_lock:
            runner._worker_run_generation = 2
        with runner._lock:
            runner.jobs = {
                "https://example.test/new.mp4": {
                    "status": "pending", "retry_after": 0,
                }
            }
        runner._state = "running"
    release_commit.set()
    overseer.join(2.0)

    assert not overseer.is_alive()
    assert runner._state == "running"
    assert starts == []
    assert pushes == []
    assert emits == []


def test_stop_and_replacement_start_cannot_overtake_worker_publication(monkeypatch):
    """Start publication is one lifecycle transaction with stop/start."""
    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    publish_reached = threading.Event()
    publish_release = threading.Event()
    stop_done = threading.Event()

    class PublicationBarrierConfig(dict):
        def get(self, key, default=None):
            if (key == "max_concurrent"
                    and threading.current_thread().name == "start-one"):
                publish_reached.set()
                assert publish_release.wait(2.0)
            return super().get(key, default)

    runner.config = PublicationBarrierConfig({
        "auto_teach_first_run": False,
        "max_concurrent": 1,
    })
    runner.jobs[url].update({"status": "pending", "retry_after": 0})
    runner._state = "idle"
    runner._hung_workers = []
    runner._stop = threading.Event()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._url_queue = queue.Queue()
    runner._worker_threads = []
    runner._rl_autostart = False
    runner.is_rate_limited = lambda: False
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type("Log", (), {
        "warning": lambda *args, **kwargs: None,
        "debug": lambda *args, **kwargs: None,
    })()
    runner._stop_auto_retry = lambda: None
    worker_runs = []
    runner._worker_loop = lambda *args: worker_runs.append(args)
    runner._watch_done = lambda *args: None
    runner._watchdog_loop = lambda *args: None
    initial_generation = runner._worker_run_generation

    first = threading.Thread(target=runner.start, name="start-one")
    first.start()
    assert publish_reached.wait(2.0)

    def stop_run():
        runner.stop()
        stop_done.set()

    stopper = threading.Thread(target=stop_run, name="stop-between-starts")
    stopper.start()
    try:
        assert not stop_done.wait(0.1)
    finally:
        publish_release.set()
        first.join(2.0)
        stopper.join(2.0)

    assert not first.is_alive()
    assert not stopper.is_alive()
    with runner._lock:
        runner.jobs[url].update({"status": "pending", "retry_after": 0})
    runner.start()
    replacement_pool = runner._worker_threads

    assert runner._state == "running"
    assert runner._worker_run_generation == initial_generation + 2
    assert runner._worker_threads is replacement_pool
    assert worker_runs == [
        (0, initial_generation + 1),
        (0, initial_generation + 2),
    ]


def test_retry_before_notification_claim_invalidates_completion(monkeypatch):
    """A real retry mutation must beat a not-yet-claimed completion notice."""
    from bulk_downloader import plugins, push, runner_queue

    runner = _telemetry_runner(monkeypatch)
    failed_url = "https://example.test/failed.mp4"
    runner._stop = threading.Event()
    runner._state = "running"
    runner.config = {"name": "Telemetry"}
    runner.jobs = {
        failed_url: {"status": "failed", "retry_after": 0},
    }
    runner.urls = [failed_url]
    pushes = []
    emits = []
    monkeypatch.setattr(
        runner_queue, "queue_bulk_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(push, "send_push", lambda **kwargs: pushes.append(kwargs))
    monkeypatch.setattr(plugins, "emit", lambda *args, **kwargs: emits.append(args))

    action, token = runner._finalize_watch_done(1)
    assert action == "notify"
    entered_claim = threading.Event()
    release_claim = threading.Event()
    original_claim = runner._claim_completion_notification

    def blocked_claim(*args, **kwargs):
        entered_claim.set()
        assert release_claim.wait(2.0)
        return original_claim(*args, **kwargs)

    runner._claim_completion_notification = blocked_claim
    notifier = threading.Thread(
        target=runner._notify_watch_done_if_current, args=(1, token))
    notifier.start()
    assert entered_claim.wait(2.0)

    assert runner.bulk_retry([failed_url]) == 1
    release_claim.set()
    notifier.join(2.0)

    assert not notifier.is_alive()
    assert runner.jobs[failed_url]["status"] == "pending"
    assert pushes == []
    assert emits == []


def test_bulk_pause_completed_before_worker_claim_prevents_processing(monkeypatch):
    """Eligibility is decided atomically at the final pre-process claim."""
    runner = _telemetry_runner(monkeypatch)
    from bulk_downloader import runner_queue

    url = "https://example.test/video.mp4"
    runner.jobs[url]["status"] = "pending"
    monkeypatch.setattr(
        runner_queue, "queue_bulk_update", lambda *args, **kwargs: None)
    entered_claim = threading.Event()
    release_claim = threading.Event()
    processed = []
    original_claim = runner._claim_worker_item

    def blocked_claim(*args, **kwargs):
        entered_claim.set()
        assert release_claim.wait(2.0)
        return original_claim(*args, **kwargs)

    runner._claim_worker_item = blocked_claim
    runner._process_one = lambda *args, **kwargs: processed.append(args)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(runner._process_worker_url(
            3, object(), url, run_generation=1)))
    worker.start()
    assert entered_claim.wait(2.0)

    assert runner.bulk_pause([url]) == 1
    release_claim.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert result == [runner._WORKER_CLAIM_INELIGIBLE]
    assert processed == []


def test_bulk_pause_after_worker_claim_is_after_processing_began(monkeypatch):
    """Once claimed/running, pending-only pause cannot cancel in-flight work."""
    from bulk_downloader import runner_queue

    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner.jobs[url]["status"] = "pending"
    monkeypatch.setattr(
        runner_queue, "queue_bulk_update", lambda *args, **kwargs: None)
    process_entered = threading.Event()
    process_release = threading.Event()
    result = []

    def blocked_process(*args, **kwargs):
        process_entered.set()
        assert process_release.wait(2.0)

    runner._process_one = blocked_process
    worker = threading.Thread(
        target=lambda: result.append(runner._process_worker_url(
            3, object(), url, run_generation=1)))
    worker.start()
    assert process_entered.wait(2.0)

    assert runner.jobs[url]["status"] == "running"
    assert runner.bulk_pause([url]) == 0
    process_release.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert result == [runner._WORKER_CLAIM_PROCESSED]


def test_multi_worker_auto_teach_deferral_releases_claim_to_pending(monkeypatch):
    """A second claimed worker must not requeue itself as stuck running."""
    runner = _telemetry_runner(monkeypatch)
    first = "https://example.test/teach-first.mp4"
    second = "https://example.test/teach-second.mp4"
    runner.config = {"auto_teach_first_run": True, "learned": {}}
    runner.jobs = {
        first: {"status": "pending", "file_size": 0},
        second: {"status": "pending", "file_size": 0},
    }
    runner.urls = [first, second]
    runner._url_queue = queue.Queue()
    runner._stop = threading.Event()
    runner._stop.set()  # make the deferral backoff return immediately
    runner._auto_teach_logged = False
    runner.log_event = lambda *args, **kwargs: None

    def auto_teach_only(browser, url, persistent_ctx=None):
        with runner._lock:
            job = dict(runner.jobs[url])
        assert runner._handle_auto_teach_check(url, job) is True

    runner._process_one = auto_teach_only

    assert runner._process_worker_url(
        0, object(), first, run_generation=1) == runner._WORKER_CLAIM_PROCESSED
    assert runner.jobs[first]["status"] == "needs_review"
    assert runner._process_worker_url(
        1, object(), second, run_generation=1) == runner._WORKER_CLAIM_PROCESSED

    assert runner.jobs[second]["status"] == "pending"
    assert list(runner._url_queue.queue) == [second]


def test_claim_publishes_running_transition_effects_exactly_once(monkeypatch):
    """Atomic claim still records the normal pending-to-running lifecycle."""
    from bulk_downloader import run_history

    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner.jobs[url]["status"] = "pending"
    starts = []
    lifecycle = []
    state_logs = []
    monkeypatch.setattr(
        run_history, "record_run_start",
        lambda site_id, started_url: starts.append((site_id, started_url)) or "r1")
    monkeypatch.setattr(
        run_history, "emit_lifecycle",
        lambda *args, **kwargs: lifecycle.append((args, kwargs)))
    runner.log_event = lambda kind, message, **kwargs: state_logs.append(
        (kind, message, kwargs))

    def normal_process(*args, **kwargs):
        runner._update_job(url, "running", "Opening page")

    runner._process_one = normal_process
    result = runner._process_worker_url(0, object(), url, run_generation=1)

    assert result == runner._WORKER_CLAIM_PROCESSED
    assert starts == [("telemetry", url)]
    assert len(lifecycle) == 1
    assert [event for event in state_logs
            if event[0] == "state" and event[1].startswith("running:")] == [
        ("state", "running: Claimed by worker", {
            "url": url, "extra": {"prev": "pending"},
        })
    ]
    assert runner.jobs[url]["_run_id"] == "r1"


def test_accounts_writer_waits_for_lifecycle_transaction(monkeypatch):
    """A non-QueueMixin status writer cannot mutate outside lifecycle."""
    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner.jobs[url]["status"] = "failed"
    runner._rotate_account_if_available = lambda reason: True
    done = threading.Event()

    def recover():
        runner.trigger_rate_limit(url, "test recovery")
        done.set()

    with runner._run_lifecycle_lock:
        worker = threading.Thread(target=recover)
        worker.start()
        assert not done.wait(0.1)
        assert runner.jobs[url]["status"] == "failed"

    worker.join(2.0)
    assert not worker.is_alive()
    assert runner.jobs[url]["status"] == "pending"


def test_scheduler_auto_retry_uses_status_writer(monkeypatch):
    from bulk_downloader import runner_scheduler

    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner.config = {
        "auto_retry_failed": True,
        "auto_retry_review": False,
        "auto_retry_classify": False,
        "auto_retry_schedule": "1s",
        "auto_retry_max_attempts": 3,
    }
    runner.jobs[url].update({
        "status": "failed",
        "next_auto_retry_at": 1,
        "auto_retry_count": 0,
        "message": "temporary",
    })
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type("Log", (), {"info": lambda *args, **kwargs: None})()
    monkeypatch.setattr(runner_scheduler.time, "time", lambda: 10.0)
    monkeypatch.setattr(
        runner_scheduler, "queue_upsert", lambda *args, **kwargs: None)
    version = runner._job_status_version

    runner._auto_retry_scan()

    assert runner.jobs[url]["status"] == "pending"
    assert runner._job_status_version == version + 1


@pytest.mark.parametrize("method_name", [
    "teach_cancel",
    "cancel_manual_download",
])
def test_takeover_cancel_requeues_through_status_writer(monkeypatch, method_name):
    runner = _telemetry_runner(monkeypatch)
    url = "https://example.test/video.mp4"
    runner.jobs[url].update({
        "status": "needs_review",
        "auto_teach_seen": True,
    })
    runner._url_queue = queue.Queue()
    runner._auto_teach_logged = True
    runner.log = type("Log", (), {"debug": lambda *args, **kwargs: None})()
    cancelled = []
    runner._manual_download_session = type("Session", (), {
        "target_url": url,
        "cancel": lambda self, timeout: cancelled.append(timeout),
    })()
    version = runner._job_status_version

    ok, _ = getattr(runner, method_name)()

    assert ok is True
    assert cancelled
    assert runner.jobs[url]["status"] == "pending"
    assert list(runner._url_queue.queue) == [url]
    assert runner._job_status_version == version + 1


@pytest.mark.parametrize("mode", ["teach", "manual"])
def test_takeover_completion_requeues_waiters_through_status_writer(
        monkeypatch, mode):
    from bulk_downloader import learn

    runner = _telemetry_runner(monkeypatch)
    target = "https://example.test/target.mp4"
    waiting = "https://example.test/waiting.mp4"
    runner.config = {"learned": {}}
    runner.jobs = {
        target: {"status": "needs_review", "auto_teach_seen": True},
        waiting: {"status": "needs_review", "auto_teach_seen": True},
    }
    runner.urls = [target, waiting]
    runner._url_queue = queue.Queue()
    runner._auto_teach_logged = True
    runner._override_suppresses_persist = lambda: False
    runner._persist_learned_to_draft = lambda: None
    runner.start = lambda: None
    runner.log = type("Log", (), {
        "warning": lambda *args, **kwargs: None,
        "error": lambda *args, **kwargs: None,
        "debug": lambda *args, **kwargs: None,
    })()
    monkeypatch.setattr(learn, "merge_learned", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        learn, "classify_download",
        lambda *args, **kwargs: {"trigger_selectors": [".download"]})

    if mode == "teach":
        runner._manual_download_session = type("TeachSession", (), {
            "target_url": target,
            "commit": lambda self, timeout: (True, []),
        })()
        invoke = lambda: runner.teach_commit({
            "trigger_selectors": [".download"],
        })
    else:
        runner._manual_download_session = type("ManualSession", (), {
            "target_url": target,
            "finalize": lambda self, timeout: (
                True, "ok", [], {"clicks": []}),
        })()
        invoke = runner.finish_manual_download
    version = runner._job_status_version

    ok, _ = invoke()

    assert ok is True
    assert runner.jobs[target]["status"] == "done"
    assert runner.jobs[waiting]["status"] == "pending"
    assert list(runner._url_queue.queue) == [waiting]
    assert runner._job_status_version >= version + 2


def test_start_clears_stale_worker_tracking(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    runner.jobs.clear()
    runner.urls.clear()
    runner._state = "idle"
    runner._rl_until = 0
    runner.is_rate_limited = lambda: False
    runner._worker_heartbeats = {2: 1.0}
    runner._worker_current_urls = {2: "https://stale.test/video.mp4"}
    runner._job_progress_samples = {
        "https://stale.test/video.mp4": {
            "bytes": 1024, "at": 1.0, "bps": 100.0,
        }
    }
    runner._hung_workers = [{"worker_idx": 2, "last_beat_age_s": 9999.0}]

    runner.start()

    assert runner._worker_heartbeats == {}
    assert runner._worker_current_urls == {}
    assert runner._job_progress_samples == {}
    assert runner._hung_workers == []


def test_start_clears_stale_tracking_before_rate_limit_return(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    runner._state = "idle"
    runner.is_rate_limited = lambda: True
    runner._worker_heartbeats = {1: 1.0}
    runner._worker_current_urls = {1: "https://stale.test/video.mp4"}
    runner._job_progress_samples = {
        "https://stale.test/video.mp4": {
            "bytes": 1024, "at": 1.0, "bps": 100.0,
        }
    }
    runner._hung_workers = [{"worker_idx": 1, "last_beat_age_s": 9999.0}]

    runner.start()

    assert runner._worker_heartbeats == {}
    assert runner._worker_current_urls == {}
    assert runner._job_progress_samples == {}
    assert runner._hung_workers == []


def test_current_throughput_is_live_then_decays_when_samples_are_stale(monkeypatch):
    from bulk_downloader import runner as runner_mod

    url = "https://example.test/video.mp4"
    runner = _telemetry_runner(monkeypatch, url=url)
    now = [10.0]
    monkeypatch.setattr(runner_mod.time, "time", lambda: now[0])

    runner._update_job(url, "running", "first sample", file_size=1024)
    now[0] = 12.0
    runner._update_job(url, "running", "second sample", file_size=3072)

    assert runner._current_throughput_bps(now=12.0) == 1024.0
    runner.jobs[url]["status"] = "done"
    assert runner._current_throughput_bps(now=12.0) == 0.0
    runner.jobs[url]["status"] = "running"
    assert runner._current_throughput_bps(now=18.0) == 0.0


def test_get_status_reports_current_not_completion_throughput(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    runner._state = "running"
    runner._login_status = "ok"
    runner._throughput_ewma_bps = 999999.0
    runner._hung_workers = []
    runner.is_rate_limited = lambda: False
    runner.cookie_info = lambda: {}
    runner.sched_next_str = lambda: ""
    runner.is_awaiting_manual_login = lambda: False
    runner.is_awaiting_manual_download = lambda: False
    runner._cookie_age_hours = lambda: 0.0
    runner._learned_summary = lambda: {}
    runner.jd_health = lambda: {}
    runner.qb_health = lambda: {}
    runner._current_throughput_bps = lambda now=None: 321.0

    assert runner.get_status(light=True)["bytes_per_sec"] == 321.0


class _DashboardRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self.jobs = {}
        self.cookies = []
        self._throughput_ewma_bps = 999999.0

    def state(self):
        return "running"

    def is_rate_limited(self):
        return False

    def _current_throughput_bps(self, now=None):
        return 654.0


def test_dashboard_aggregates_current_not_completion_throughput(fresh_app):
    from bulk_downloader.app_state import runners, s_cfg

    runners["live"] = _DashboardRunner()
    s_cfg["live"] = {}

    body = fresh_app.get("/api/dashboard").get_json()

    assert body["throughput_bps"] == 654.0


class _StreamResponse:
    def __init__(self, chunks, *, status_code=200, content_length=None):
        self.status_code = status_code
        self.headers = {
            "Content-Length": str(
                sum(len(chunk) for chunk in chunks)
                if content_length is None else content_length)
        }
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, chunk_size=None):
        yield from self._chunks


def _tick_clock():
    value = [0.0]

    def tick():
        value[0] += 1.1
        return value[0]

    return tick


def test_direct_http_progress_reports_cumulative_file_size(monkeypatch, tmp_path):
    import httpx
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import runner_transport as transport

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "telemetry"
    runner.config = {"user_agent": "test"}
    runner._stop = threading.Event()
    runner._download_proxy_url = lambda: None
    updates = []
    runner._update_job = lambda *args, **extra: updates.append(extra["file_size"])
    response = _StreamResponse([b"ab", b"cde", b"f"])
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: response)
    monkeypatch.setattr(transport.time, "time", _tick_clock())
    monkeypatch.setattr(transport, "_SUPERVISOR_AVAILABLE", False)

    ok = runner._do_direct_http_download(
        "https://page.test/v", "https://cdn.test/v.mp4",
        str(tmp_path / "v.mp4"))

    assert ok is True
    assert updates == [2, 5, 6]


def test_sequential_resume_progress_reports_absolute_file_size(monkeypatch, tmp_path):
    import httpx
    from bulk_downloader import rate_limit
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import runner_transport as transport

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "telemetry"
    runner.config = {"parallel_chunks": 1, "use_curl_cffi": False}
    runner._stop = threading.Event()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._pick_fastest_mirror = lambda url: url
    runner._recommended_chunk_bytes = lambda: 1024
    runner._current_cap_mbps = lambda: 0
    runner._download_proxy_url = lambda: None
    runner._observe_throughput = lambda *args: None
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type("Log", (), {"warning": lambda *args, **kwargs: None})()
    updates = []
    runner._update_job = lambda *args, **extra: updates.append(extra["file_size"])

    final_path = Path(tmp_path) / "resume.mp4"
    part_path = final_path.with_suffix(final_path.suffix + ".part")
    part_path.write_bytes(b"abcd")
    response = _StreamResponse(
        [b"ef", b"gh"], status_code=206, content_length=4)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: response)
    monkeypatch.setattr(transport.time, "time", _tick_clock())
    monkeypatch.setattr(transport, "record_bandwidth", lambda delta: None)
    slot = type("Slot", (), {"release": lambda self: None})()
    monkeypatch.setattr(rate_limit, "acquire", lambda url: slot)

    size = runner._http_download(
        "https://page.test/v", object(),
        type("Ctx", (), {"cookies": lambda self: []})(),
        "https://cdn.test/v.mp4", final_path)

    assert size == 8
    assert updates == [6, 8]
