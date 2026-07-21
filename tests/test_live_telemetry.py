"""Regression coverage for live runner telemetry on health endpoints."""
from __future__ import annotations

import threading
import queue
from pathlib import Path


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
    runner._worker_heartbeats_lock = threading.Lock()
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


def test_old_worker_generation_cannot_map_or_unmap_new_worker(monkeypatch):
    runner = _telemetry_runner(monkeypatch)
    old_url = "https://example.test/old.mp4"
    new_url = "https://example.test/new.mp4"
    entered = threading.Event()
    release = threading.Event()

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

    assert processed is False
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
