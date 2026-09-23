"""Row 1021: Decoupling Monolithic SiteRunner into Single-Responsibility Subsystems (RunnerDecoupling).

Decomposes monolithic SiteRunner state, coordination, and lifecycle into modular,
single-responsibility subsystems (Lifecycle, Queue, Transport, Telemetry) coordinated
by a central SubsystemRegistry.

Tests operate against live SiteRunner instances, validating bidirectional state synchronization,
delegation of runner operations (set_state, pause, resume, stop, trigger_rate_limit, add_url,
_update_job, record_bytes, record_error), and backward-compatible state mirrors.

RED on baseline: runner lacks RunnerSubsystemManager and BaseRunnerSubsystem.
"""
from __future__ import annotations

import queue

try:
    from bulk_downloader.runner_decoupling import (
        BaseRunnerSubsystem,
        LifecycleSubsystem,
        QueueSubsystem,
        RunnerSubsystemManager,
        TelemetrySubsystem,
        TransportSubsystem,
    )
except ImportError:
    BaseRunnerSubsystem = None
    LifecycleSubsystem = None
    QueueSubsystem = None
    RunnerSubsystemManager = None
    TelemetrySubsystem = None
    TransportSubsystem = None

BD_GATE_SCOPE = "repo-wide"


def test_runner_decoupling_contract():
    """Verify runner module exposes RunnerSubsystemManager and subsystem classes."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader import runner

    assert hasattr(runner, "RunnerSubsystemManager") and hasattr(runner, "BaseRunnerSubsystem"), (
        "Baseline SiteRunner lacks decoupled subsystem architecture (RunnerDecoupling)"
    )


def test_siterunner_subsystems_initialized():
    """Verify SiteRunner initializes and exposes decoupled subsystems."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("test_site_decoupling", {"concurrency": 2})
    assert hasattr(runner, "subsystems"), "SiteRunner must expose subsystems manager"

    lifecycle = runner.get_subsystem("lifecycle")
    assert lifecycle is not None
    assert lifecycle.name == "lifecycle"
    assert isinstance(lifecycle, LifecycleSubsystem)

    q_sub = runner.get_subsystem("queue")
    assert q_sub is not None
    assert q_sub.name == "queue"
    assert isinstance(q_sub, QueueSubsystem)

    transport = runner.get_subsystem("transport")
    assert transport is not None
    assert transport.name == "transport"
    assert isinstance(transport, TransportSubsystem)

    telemetry = runner.get_subsystem("telemetry")
    assert telemetry is not None
    assert telemetry.name == "telemetry"
    assert isinstance(telemetry, TelemetrySubsystem)

    registered = runner.subsystems.list_subsystems()
    assert set(registered) == {"lifecycle", "queue", "transport", "telemetry"}


def test_siterunner_lifecycle_operations_drive_subsystem():
    """Verify SiteRunner lifecycle operations (set_state, pause, resume, stop) drive LifecycleSubsystem without divergence."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("test_site_lifecycle", {"concurrency": 2})
    lifecycle = runner.get_subsystem("lifecycle")
    assert lifecycle is not None

    # Initial state
    assert runner._state == "idle"
    assert lifecycle.state == "idle"
    assert lifecycle.is_active() is False

    # Transition to running
    runner.set_state("running")
    assert runner._state == "running"
    assert lifecycle.state == "running"
    assert lifecycle.is_active() is True

    # Pause runner
    runner.pause()
    assert runner._state == "paused"
    assert lifecycle.state == "paused"
    assert lifecycle.is_active() is True
    assert runner._pause.is_set() is False

    # Resume runner
    runner.resume()
    assert runner._state == "running"
    assert lifecycle.state == "running"
    assert lifecycle.is_active() is True
    assert runner._pause.is_set() is True

    # Stop runner
    runner.stop()
    assert runner._state == "stopped"
    assert lifecycle.state == "stopped"
    assert lifecycle.is_active() is False
    assert runner._stop.is_set() is True

    # Subsystem-driven operations update runner._state
    lifecycle.pause()
    assert runner._state == "paused"
    lifecycle.resume()
    assert runner._state == "running"

    status = lifecycle.status()
    assert status["state"] == "running"
    assert status["is_stopped"] is False


def test_siterunner_transport_rate_limit_and_accumulators():
    """Verify SiteRunner trigger_rate_limit, stop, and accumulators synchronize with TransportSubsystem."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("test_site_transport", {})
    transport = runner.get_subsystem("transport")
    assert transport is not None

    # Rate limit initial state
    assert runner._rl_autostart is False
    assert transport.rl_autostart is False

    # Trigger rate limit on SiteRunner drives TransportSubsystem
    runner.trigger_rate_limit("https://example.com/429", "Rate limit test")
    assert runner._rl_autostart is True
    assert transport.rl_autostart is True

    # Stop runner clears rate limit autostart across runner and subsystem
    runner.stop()
    assert runner._rl_autostart is False
    assert transport.rl_autostart is False

    # Direct subsystem update reflects on runner
    transport.set_rate_limit_autostart(True)
    assert runner._rl_autostart is True
    transport.set_rate_limit_autostart(False)
    assert runner._rl_autostart is False

    # Byte accumulator registration and flushing
    class MockAccumulator:
        def __init__(self, val: int = 4096) -> None:
            self.val = val
            self.flushed = False

        def flush(self) -> int:
            self.flushed = True
            return self.val

    acc = MockAccumulator(4096)
    runner.register_accumulator(acc)
    assert transport.contains_accumulator(acc) is True
    assert acc in runner._daily_byte_accumulators

    flushed = transport.flush_accumulators()
    assert flushed == 4096
    assert acc.flushed is True

    runner.unregister_accumulator(acc)
    assert transport.contains_accumulator(acc) is False
    assert acc not in runner._daily_byte_accumulators


def test_siterunner_queue_operations_drive_subsystem():
    """Verify SiteRunner queue operations (add_url, record_job, _update_job) synchronize with QueueSubsystem."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("test_site_queue", {})
    q_sub = runner.get_subsystem("queue")
    assert q_sub is not None

    # Add URL via SiteRunner
    url = "https://example.com/video1.mp4"
    runner.add_url(url)
    assert url in runner.urls
    assert url in q_sub.urls
    assert runner._url_queue.qsize() == 1

    # Record job via SiteRunner
    runner.record_job(url, {"url": url, "status": "pending", "file_size": 0})
    assert q_sub.get_job(url) is not None
    assert q_sub.get_job(url)["status"] == "pending"

    # Update job via _update_job
    runner._update_job(url, "done", "Download completed", file_size=1048576)
    job = q_sub.get_job(url)
    assert job is not None
    assert job["status"] == "done"
    assert job["file_size"] == 1048576

    status = q_sub.status()
    assert status["total_urls"] >= 1
    assert status["total_jobs"] >= 1


def test_siterunner_telemetry_operations_drive_subsystem():
    """Verify SiteRunner byte transfers and error logging drive TelemetrySubsystem snapshots."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("test_site_telemetry", {})
    telemetry = runner.get_subsystem("telemetry")
    assert telemetry is not None

    # Initial telemetry state
    initial_snap = telemetry.snapshot()
    assert initial_snap["total_bytes"] == 0
    assert initial_snap["speed_bps"] == 0.0

    # Job progress update advances bytes in TelemetrySubsystem
    url = "https://example.com/stream.bin"
    runner._update_job(url, "running", "Downloading", file_size=524288)
    assert telemetry.total_bytes == 524288

    snap = telemetry.snapshot()
    assert snap["total_bytes"] == 524288
    assert snap["speed_bps"] > 0

    # Incremental advancement
    runner._update_job(url, "running", "Downloading chunk 2", file_size=1048576)
    assert telemetry.total_bytes == 1048576

    # Direct byte recording
    runner.record_bytes(100000)
    assert telemetry.total_bytes == 1148576

    # Error logging via _update_job and log_event
    runner._update_job(url, "failed", "Connection reset by peer")
    assert telemetry.snapshot()["errors"].get("Connection reset by peer", 0) >= 1

    runner.record_error("timeout")
    assert telemetry.snapshot()["errors"].get("timeout") == 1

    runner.log_event("network", "DNS resolution failed")
    assert telemetry.snapshot()["errors"].get("network") == 1


def test_siterunner_backward_compatibility_and_state_mirrors():
    """Verify SiteRunner maintains backward compatibility and identical object references for mirrors."""
    assert RunnerSubsystemManager is not None, "Row 1021 capability missing: RunnerSubsystemManager not exposed"
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("compat_site", {})
    assert runner._stop is runner.get_subsystem("lifecycle").stop_event
    assert runner._pause is runner.get_subsystem("lifecycle").pause_event
    assert runner._pause.is_set() is True  # Pre-set pause_event (M1 kill)
    assert runner._url_queue is runner.get_subsystem("queue").url_queue
    assert runner.jobs is runner.get_subsystem("queue").jobs
    assert runner.urls is runner.get_subsystem("queue").urls
    assert runner._daily_byte_accumulators is runner.get_subsystem("transport").accumulators
    assert runner._daily_byte_accumulators_lock is runner.get_subsystem("transport").lock
    assert runner._worker_threads is runner.get_subsystem("lifecycle").worker_threads

    assert runner.get_subsystem("nonexistent") is None
    assert runner.get_subsystem("lifecycle") is runner.lifecycle_subsystem


# ── r3 (ORDERS-2323 / RULING-2318 E1): clear_completed keeps the jobs/urls mirror ──


def _e1_runner(site):
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner(site, {"concurrency": 1})
    assert r.queue_subsystem is not None
    return r


def test_e1_clear_completed_keeps_jobs_alias_and_drops_done():
    r = _e1_runner("row1021_e1_a")
    qs = r.queue_subsystem
    for i in range(3):
        r._update_job_current(f"http://x/{i}", "done", "m")
    r.clear_completed()
    r._update_job_current("http://x/new", "queued", "m")
    assert r.jobs is qs.jobs, "E1: clear_completed rebound runner.jobs away from queue_subsystem.jobs"
    assert r.urls is qs.urls, "E1: clear_completed rebound runner.urls away from queue_subsystem.urls"
    assert sorted(qs.jobs) == ["http://x/new"], f"E1: subsystem kept cleared jobs: {sorted(qs.jobs)}"


def test_e1_add_url_after_clear_lands_in_runner_urls():
    r = _e1_runner("row1021_e1_b")
    r._update_job_current("http://x/0", "done", "m")
    r.clear_completed()
    r.add_url("http://y/1")
    assert "http://y/1" in r.urls, "E1: add_url after clear_completed never reached runner.urls"


def test_e1_repeated_clear_cycles_do_not_grow_subsystem():
    r = _e1_runner("row1021_e1_c")
    qs = r.queue_subsystem
    for k in range(100):
        r._update_job_current(f"http://g/{k}", "done", "m")
        r.clear_completed()
    assert len(r.jobs) == 0 and len(qs.jobs) == 0, f"E1: growth runner={len(r.jobs)} subsystem={len(qs.jobs)}"


def test_e1_control_clear_completed_keeps_active_jobs():
    """Positive control: the probe sees a surviving non-done job in both views."""
    r = _e1_runner("row1021_e1_d")
    r._update_job_current("http://x/keep", "queued", "m")
    r._update_job_current("http://x/drop", "done", "m")
    r.clear_completed()
    assert "http://x/keep" in r.jobs and "http://x/drop" not in r.jobs


def test_a_failing_accumulator_flush_is_counted_not_swallowed():
    """T66' DP-13 (runner_decoupling flush_accumulators): a raising flush was dropped by a bare
    `except: pass` -- its bytes vanished with no trace. The others still flush; the failure is counted."""
    from bulk_downloader.runner_decoupling import TransportSubsystem

    class Broken:
        def flush(self):
            raise OSError("ledger write failed")

    class Good:
        def flush(self):
            return 512

    transport = TransportSubsystem(runner=None)
    transport.register_accumulator(Broken())
    transport.register_accumulator(Good())
    assert transport.flush_accumulators() == 512
    assert transport.status().get("flush_failures") == 1, (
        "T66' DP-13: a failed accumulator flush left no count in TransportSubsystem.status()")

