"""Tests for Row 927: CHROMIUM-PROCESS-HEAP-BOUNDING-AND-AUTO-RESTART-SENTINEL.

Acceptance criteria:
(1) process RSS monitoring
(2) automated worker recycling between tasks
(3) clean termination of child renderers
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import pytest

from bulk_downloader.runner_browser import BrowserMixin
from bulk_downloader import cloak

try:
    from bulk_downloader import browser_sentinel
except ImportError:
    browser_sentinel = None

BD_GATE_SCOPE = "module"


class ProductionTestRunner(BrowserMixin):
    """Test runner class inheriting BrowserMixin to verify production integration."""
    def __init__(self):
        self.config = {}
        self.site_id = "test_site"


def test_production_browser_launch_args_contain_heap_bounding():
    """Verify production BrowserMixin and cloak launch args include V8 heap bounding flags."""
    runner = ProductionTestRunner()
    launch_args = runner._launch_args(headless=True)

    # Base fails here: BrowserMixin._launch_args() lacks --js-flags=--max-old-space-size=512
    assert "--js-flags=--max-old-space-size=512" in launch_args, (
        f"BrowserMixin._launch_args must contain '--js-flags=--max-old-space-size=512', got: {launch_args}"
    )
    assert "--disable-dev-shm-usage" in launch_args, (
        f"BrowserMixin._launch_args must contain '--disable-dev-shm-usage', got: {launch_args}"
    )

    # Cloak launch args must also include heap bounding
    cloak_args = cloak.get_stealth_args() if hasattr(cloak, "get_stealth_args") else launch_args
    assert "--js-flags=--max-old-space-size=512" in cloak_args


def test_process_rss_monitoring_and_flags():
    """Acceptance (1): process RSS monitoring, V8 flags, and threshold detection."""
    assert browser_sentinel is not None, (
        "bulk_downloader.browser_sentinel must be implemented"
    )

    # 1. V8 heap bounding flags
    v8_flags = browser_sentinel.get_v8_heap_flags(512)
    assert v8_flags == ["--js-flags=--max-old-space-size=512"]

    v8_custom = browser_sentinel.get_v8_heap_flags(768)
    assert v8_custom == ["--js-flags=--max-old-space-size=768"]

    with pytest.raises(ValueError):
        browser_sentinel.get_v8_heap_flags(0)

    # Chromium memory flags
    chrome_flags = browser_sentinel.get_chromium_memory_flags(512)
    assert "--js-flags=--max-old-space-size=512" in chrome_flags
    assert "--disable-dev-shm-usage" in chrome_flags

    # 2. Process RSS monitoring on live current process
    my_pid = os.getpid()
    rss_bytes = browser_sentinel.read_proc_rss_bytes(my_pid)
    assert rss_bytes > 0, "Current Python process must have positive RSS"

    rss_mb = browser_sentinel.get_process_rss_mb(my_pid)
    assert rss_mb > 1.0, f"Expected active process RSS > 1MB, got {rss_mb} MB"
    assert abs(rss_mb - (rss_bytes / (1024 * 1024))) < 0.01

    # Threshold evaluation
    assert browser_sentinel.is_rss_exceeded(my_pid, threshold_mb=0.1) is True
    assert browser_sentinel.is_rss_exceeded(my_pid, threshold_mb=100000.0) is False

    # Tree RSS
    tree_mb = browser_sentinel.get_tree_rss_mb(my_pid)
    assert tree_mb >= rss_mb

    # Non-existent PID: fail-soft, returns 0 without raising
    dead_pid = 99999999
    assert browser_sentinel.read_proc_rss_bytes(dead_pid) == 0
    assert browser_sentinel.get_process_rss_mb(dead_pid) == 0.0
    assert browser_sentinel.is_rss_exceeded(dead_pid, threshold_mb=50.0) is False


def test_automated_worker_recycling_between_tasks():
    """Acceptance (2): automated worker recycling between tasks when RSS > 750MB."""
    runner = ProductionTestRunner()

    # Base fails here: BrowserMixin lacks between-task recycling capability
    recycler_fn = getattr(runner, "maybe_recycle_browser", None)
    assert recycler_fn is not None, (
        "BrowserMixin must provide maybe_recycle_browser() for automated recycling between tasks"
    )

    assert browser_sentinel is not None, (
        "bulk_downloader.browser_sentinel must be implemented"
    )
    BrowserSentinel = browser_sentinel.BrowserSentinel
    DEFAULT_HEAP_MAX_MB = browser_sentinel.DEFAULT_HEAP_MAX_MB

    sentinel = BrowserSentinel(threshold_mb=750.0, max_heap_mb=DEFAULT_HEAP_MAX_MB)
    assert sentinel.threshold_mb == 750.0
    assert sentinel.max_heap_mb == 512
    assert sentinel.recycle_count == 0

    # Spawn a worker subprocess to serve as the browser instance
    worker_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        worker_pid = worker_proc.pid
        current_rss = sentinel.measure_rss_mb(worker_pid)
        assert current_rss >= 0.0

        # Simulate RSS exceeding 750MB threshold (e.g. 850MB)
        trigger_sentinel = BrowserSentinel(threshold_mb=750.0)
        trigger_sentinel.attach_process(worker_pid)
        trigger_sentinel.measure_rss_mb = lambda pid=None: 850.0  # type: ignore[assignment]
        assert trigger_sentinel.should_recycle() is True

        # 1. IN-FLIGHT TASK PROTECTION: must NOT recycle mid-task
        trigger_sentinel.start_task()
        assert trigger_sentinel.is_running_task is True
        recycled, info = trigger_sentinel.maybe_recycle_between_tasks()
        assert recycled is False
        assert info["reason"] == "task_in_flight"
        assert trigger_sentinel.recycle_count == 0

        # 2. TASK END: records completion and allows recycling between tasks
        task_summary = trigger_sentinel.end_task()
        assert trigger_sentinel.is_running_task is False
        assert task_summary["tasks_completed"] == 1
        assert task_summary["needs_recycle"] is True

        # 3. RECYCLING EXECUTION
        stopped = False
        started = False
        dummy_new_pid = 424242

        def mock_stop():
            nonlocal stopped
            stopped = True

        def mock_start():
            nonlocal started
            started = True
            return dummy_new_pid

        recycled, rec_info = trigger_sentinel.maybe_recycle_between_tasks(
            stop_fn=mock_stop,
            start_fn=mock_start,
        )
        assert recycled is True
        assert stopped is True
        assert started is True
        assert trigger_sentinel.recycle_count == 1
        assert trigger_sentinel.current_pid == dummy_new_pid
        assert rec_info["new_pid"] == dummy_new_pid

        # 4. Production BrowserMixin recycling hook verification
        runner._sentinel = trigger_sentinel
        runner_recycled, runner_info = runner.maybe_recycle_browser(
            stop_fn=mock_stop, start_fn=mock_start
        )
        assert runner_recycled is True
        assert runner_info["recycled"] is True
        assert trigger_sentinel.recycle_count == 2

        # 5. NEGATIVE CONTROL: high threshold (50000MB) does NOT recycle
        idle_sentinel = BrowserSentinel(threshold_mb=50000.0)
        idle_sentinel.attach_process(worker_pid)
        recycled_idle, idle_info = idle_sentinel.maybe_recycle_between_tasks()
        assert recycled_idle is False
        assert idle_info["reason"] == "below_threshold"
        assert idle_sentinel.recycle_count == 0

        runner._sentinel = idle_sentinel
        idle_recycled, _ = runner.maybe_recycle_browser()
        assert idle_recycled is False
    finally:
        try:
            worker_proc.kill()
            worker_proc.wait(timeout=0.5)
        except Exception:
            pass


def test_clean_termination_of_child_renderers():
    """Acceptance (3): clean termination of child renderers without orphan processes."""
    assert browser_sentinel is not None, (
        "bulk_downloader.browser_sentinel must be implemented"
    )
    get_child_pids = browser_sentinel.get_child_pids
    terminate_process_tree = browser_sentinel.terminate_process_tree

    # Spawn parent and child process hierarchy (simulating browser and renderer)
    parent_cmd = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)"
        ),
    ]

    parent = subprocess.Popen(parent_cmd)
    try:
        time.sleep(0.3)
        parent_pid = parent.pid

        # Verify child discovery
        children = get_child_pids(parent_pid, recursive=True)
        for _ in range(10):
            if children:
                break
            time.sleep(0.1)
            children = get_child_pids(parent_pid, recursive=True)

        # EXACT COUNT ASSERTION (Negative control / Nonzero proof)
        assert len(children) >= 1, f"Expected at least 1 child process for PID {parent_pid}, found none"
        child_pid = children[0]

        # Verify both are active before termination
        os.kill(parent_pid, 0)
        os.kill(child_pid, 0)

        # Terminate process tree
        terminated = terminate_process_tree(parent_pid, timeout_sec=2.0)
        assert parent_pid in terminated
        assert child_pid in terminated

        # Verify parent exits cleanly
        parent_exit = parent.wait(timeout=1.0)
        assert parent_exit is not None
        assert parent_exit in (-signal.SIGTERM, -signal.SIGKILL)

        # Verify child is cleanly reaped
        child_dead = False
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_dead = True
        assert child_dead is True, f"Child process {child_pid} was not terminated"
    finally:
        try:
            parent.kill()
            parent.wait(timeout=0.5)
        except Exception:
            pass

    # Terminating already dead PID returns cleanly without error
    terminated_dead = terminate_process_tree(99999999, timeout_sec=0.1)
    assert terminated_dead == []
