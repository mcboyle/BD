"""Regression coverage for live runner telemetry on health endpoints."""
from __future__ import annotations

import threading


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
