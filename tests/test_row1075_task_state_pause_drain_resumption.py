"""Row 1075 -- Transactional Task State Pause, Drain, and Resumption Engine.

Guards:
- Transactional task state transitions (IDLE, RUNNING, PAUSED, DRAINING, DRAINED, TIMED_OUT, RESUMING)
- Checkpoint copy-safety (deep copy isolation against caller mutation)
- In-flight operation tracking with three-state drain (drained, timed_out, error)
- Condition signaling and immediate wake-up upon in-flight clearance
- Timeout propagation and active-task classification
- SiteRunner production lifecycle integration (drain calls pause on running runner, catching M11)
- RED test with in-flight task timing out

Base invariant: RED test must fail with AssertionError on unpatched baseline,
never with an ImportError during pytest collection.
"""
from __future__ import annotations

import copy
import threading
import time
import pytest

from bulk_downloader import runner

BD_GATE_SCOPE = "repo-wide"


def _get_engine_module():
    try:
        from bulk_downloader import task_drain_engine
        return task_drain_engine
    except (ImportError, ModuleNotFoundError):
        return None


# ---------------------------------------------------------------------------
# 1. Baseline capability probe (fails with AssertionError on base)
# ---------------------------------------------------------------------------

def test_task_drain_engine_integrated_in_siterunner():
    """RED gate: proves SiteRunner provides drain capability and task state integration."""
    assert hasattr(runner.SiteRunner, "drain"), (
        "Row 1075 capability missing: bulk_downloader.runner.SiteRunner has no drain method"
    )


def test_task_drain_engine_module_available():
    """RED gate: proves task_drain_engine module exists and exports required interfaces."""
    mod = _get_engine_module()
    assert mod is not None, (
        "Row 1075 capability missing: bulk_downloader.task_drain_engine module does not exist"
    )
    assert hasattr(mod, "TaskStatePauseDrainEngine"), "TaskStatePauseDrainEngine class missing"
    assert hasattr(mod, "TaskState"), "TaskState enum missing"
    assert hasattr(mod, "DrainStatus"), "DrainStatus enum missing"
    assert hasattr(mod, "DrainResult"), "DrainResult class missing"
    assert hasattr(mod, "get_task_drain_engine"), "get_task_drain_engine factory missing"


# ---------------------------------------------------------------------------
# 2. Checkpoint copy-safety (kills M3/M4/M5)
# ---------------------------------------------------------------------------

def test_engine_checkpoint_copy_safety():
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    initial = {"cursor": 10, "nested": {"counter": 1}}
    task = engine.register_task("task_copy_safe", checkpoint_data=initial)

    # Mutating initial source dictionary must not mutate stored checkpoint
    initial["nested"]["counter"] = 999
    assert task.checkpoint_data["nested"]["counter"] == 1

    # Mutating update dictionary must not mutate stored checkpoint
    update_dict = {"batch": [1, 2, 3]}
    engine.pause_task("task_copy_safe", checkpoint_data=update_dict)
    update_dict["batch"].append(4)
    task_paused = engine.get_task_status("task_copy_safe")
    assert task_paused.checkpoint_data["batch"] == [1, 2, 3]

    # Mutating returned checkpoint on resume must not mutate stored checkpoint
    _, restored = engine.resume_task("task_copy_safe")
    restored["batch"].append(99)
    assert engine.get_task_status("task_copy_safe").checkpoint_data["batch"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Three-state drain, in-flight tracking & timeout propagation (kills M1/M2/M12/M14)
# ---------------------------------------------------------------------------

def test_engine_drain_with_no_in_flight_succeeds_immediately():
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    engine.register_task("task_idle")

    res = engine.drain_task("task_idle", timeout_seconds=1.0)
    assert res.status == mod.DrainStatus.DRAINED.value
    assert res.is_drained is True
    assert res.in_flight_remaining == 0
    assert res.elapsed_seconds < 0.1
    assert engine.get_task_status("task_idle").state == mod.TaskState.DRAINED.value


def test_engine_drain_with_task_still_in_flight_times_out():
    """RED test per PM order: in-flight operations that do not settle return timed_out."""
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    engine.register_task("task_blocked")
    op_id = engine.acquire_operation("task_blocked", "active_download_1")
    assert engine.in_flight_count("task_blocked") == 1

    start_t = time.time()
    res = engine.drain_task("task_blocked", timeout_seconds=0.08)
    elapsed = time.time() - start_t

    assert res.status == mod.DrainStatus.TIMED_OUT.value
    assert res.is_drained is False
    assert res.in_flight_remaining == 1
    assert elapsed >= 0.07
    assert engine.get_task_status("task_blocked").state == mod.TaskState.TIMED_OUT.value

    # Release op and drain again -> succeeds
    engine.release_operation("task_blocked", op_id)
    assert engine.in_flight_count("task_blocked") == 0
    res2 = engine.drain_task("task_blocked", timeout_seconds=0.5)
    assert res2.status == mod.DrainStatus.DRAINED.value


def test_engine_drain_waits_and_wakes_on_condition(monkeypatch):
    """Kills M6/M7: drainer wakes immediately upon in-flight clearance without waiting full timeout."""
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    engine.register_task("task_async")
    op_id = engine.acquire_operation("task_async", "op_async_1")

    def delayed_release():
        time.sleep(0.04)
        engine.release_operation("task_async", op_id)

    th = threading.Thread(target=delayed_release)
    th.start()

    res = engine.drain_task("task_async", timeout_seconds=2.0)
    th.join()

    assert res.status == mod.DrainStatus.DRAINED.value
    assert res.elapsed_seconds < 0.5  # Did not sleep full 2.0s


def test_engine_drain_unknown_task_returns_error():
    """Kills M12: draining an unregistered task returns error status, not exception."""
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    res = engine.drain_task("completely_unknown_task")
    assert res.status == mod.DrainStatus.ERROR.value
    assert res.error is not None


def test_engine_drain_timeout_propagation():
    """Kills M14: timeout_seconds is strictly enforced."""
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    engine.register_task("task_timed")
    engine.acquire_operation("task_timed", "op_stalled")

    res = engine.drain_task("task_timed", timeout_seconds=0.10)
    assert res.status == mod.DrainStatus.TIMED_OUT.value
    assert 0.09 <= res.elapsed_seconds <= 0.25


# ---------------------------------------------------------------------------
# 4. Active task classification (kills M8)
# ---------------------------------------------------------------------------

def test_engine_active_task_classification():
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    engine.register_task("t_run")
    t_drain = engine.register_task("t_drain")
    t_drain.state = mod.TaskState.DRAINING.value

    engine.register_task("t_pause")
    engine.pause_task("t_pause")

    engine.register_task("t_drained")
    engine.drain_task("t_drained")

    active = engine.list_active_tasks()
    active_ids = {t.task_id for t in active}
    assert active_ids == {"t_run", "t_drain"}


# ---------------------------------------------------------------------------
# 5. SiteRunner production integration & drain-calls-pause (kills M11)
# ---------------------------------------------------------------------------

def test_siterunner_drain_pauses_running_runner_and_drains(monkeypatch):
    """Kills M11 (Fix to flip): SiteRunner.drain() called on running runner MUST pause it."""
    mod = _get_engine_module()
    assert mod is not None, "task_drain_engine module missing"

    engine = mod.TaskStatePauseDrainEngine()
    monkeypatch.setattr(mod, "_GLOBAL_ENGINE", engine)

    cfg = {
        "name": "test_site_m11",
        "url": "https://example.com",
        "save_dir": "/tmp/test_bd_downloads",
    }
    r = runner.SiteRunner("test_site_m11", cfg)
    r._state = "running"
    engine.register_task(r.site_id)

    # Calling r.drain() on RUNNING runner without pre-pausing
    drain_status = r.drain(timeout_seconds=0.1)

    # Must be paused on the runner instance (catches M11 if self.pause() is missing in drain)
    assert r._state == "paused", "SiteRunner.drain() did not transition runner state to 'paused'"
    assert drain_status == "drained"
    assert engine.get_task_status(r.site_id).state == mod.TaskState.DRAINED.value


def test_siterunner_drain_times_out_with_in_flight_work(monkeypatch):
    """Proves SiteRunner.drain() returns timed_out and stays paused when in-flight work exists."""
    mod = _get_engine_module()
    assert mod is not None

    engine = mod.TaskStatePauseDrainEngine()
    monkeypatch.setattr(mod, "_GLOBAL_ENGINE", engine)

    cfg = {"name": "test_site_timeout", "url": "https://example.com", "save_dir": "/tmp/test_bd_downloads"}
    r = runner.SiteRunner("test_site_timeout", cfg)
    r._state = "running"
    engine.register_task(r.site_id)
    op_id = engine.acquire_operation(r.site_id, "chunk_download_1")

    # Drain with active in-flight operation
    drain_status = r.drain(timeout_seconds=0.08)
    assert drain_status == "timed_out"
    assert r._state == "paused"
    assert engine.get_task_status(r.site_id).state == mod.TaskState.TIMED_OUT.value

    # Resume runner
    r.resume()
    assert r._state == "running"
    assert engine.get_task_status(r.site_id).state == mod.TaskState.RUNNING.value
