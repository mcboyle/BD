"""Chromium Process Heap Bounding and Auto-Restart Sentinel (Row 927).

Fleet reliability memory leak protection:
Extended scraping runs accumulate V8 heap fragmentation, driving browser worker
RSS past 1.5GB; proactive recycling bounds memory growth.

Configures V8 heap bounding flags and monitors worker RSS, gracefully recycling
Chromium instances exceeding 750MB RSS between jobs.
Fleet Rule 21 compliant: zero site logins touched.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_HEAP_MAX_MB: int = 512
DEFAULT_RECYCLE_RSS_MB: float = 750.0
DEFAULT_TERMINATE_TIMEOUT_SEC: float = 5.0


def get_v8_heap_flags(max_heap_mb: int = DEFAULT_HEAP_MAX_MB) -> list[str]:
    """Return V8 heap bounding command-line flags for Chromium."""
    if max_heap_mb <= 0:
        raise ValueError(f"max_heap_mb must be positive, got {max_heap_mb}")
    return [f"--js-flags=--max-old-space-size={max_heap_mb}"]


def get_chromium_memory_flags(max_heap_mb: int = DEFAULT_HEAP_MAX_MB) -> list[str]:
    """Return standard Chromium launch flags enforcing heap and memory bounding."""
    flags = get_v8_heap_flags(max_heap_mb)
    flags.extend([
        "--disable-dev-shm-usage",
        "--memory-pressure-off",
    ])
    return flags


def read_proc_rss_bytes(pid: int) -> int:
    """Read Resident Set Size (RSS) in bytes for a given PID from /proc filesystem.

    Returns 0 if the process does not exist, /proc is unavailable, or access is denied.
    """
    if pid <= 0:
        return 0

    # Primary: check /proc/{pid}/status for VmRSS
    status_path = Path(f"/proc/{pid}/status")
    if status_path.is_file():
        try:
            with open(status_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            # VmRSS is reported in kB
                            return int(parts[1]) * 1024
        except (ProcessLookupError, FileNotFoundError, PermissionError, ValueError):
            return 0

    # Secondary: check /proc/{pid}/statm
    statm_path = Path(f"/proc/{pid}/statm")
    if statm_path.is_file():
        try:
            with open(statm_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().split()
                if len(content) >= 2:
                    rss_pages = int(content[1])
                    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
                    return rss_pages * page_size
        except (ProcessLookupError, FileNotFoundError, PermissionError, ValueError):
            return 0

    return 0


def get_process_rss_mb(pid: int) -> float:
    """Return RSS in megabytes for a given PID."""
    return read_proc_rss_bytes(pid) / (1024.0 * 1024.0)


def get_child_pids(pid: int, recursive: bool = True) -> list[int]:
    """Find all child process IDs spawned by the given parent PID.

    Reads /proc on Linux or walks process trees. Returns list of integer PIDs.
    """
    if pid <= 0:
        return []

    children: list[int] = []

    # Method 1: /proc/{pid}/task/{pid}/children (Linux kernel 3.5+)
    task_children_path = Path(f"/proc/{pid}/task/{pid}/children")
    if task_children_path.is_file():
        try:
            with open(task_children_path, "r", encoding="utf-8", errors="replace") as f:
                raw_pids = f.read().split()
                direct_children = [int(p) for p in raw_pids if p.isdigit()]
                children.extend(direct_children)
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            pass

    # Method 2: Scan /proc/*/status PPid if Method 1 found nothing or was unavailable
    if not children:
        proc_root = Path("/proc")
        if proc_root.is_dir():
            try:
                for entry in proc_root.iterdir():
                    if entry.name.isdigit():
                        child_pid = int(entry.name)
                        if child_pid == pid:
                            continue
                        status_file = entry / "status"
                        try:
                            with open(status_file, "r", encoding="utf-8", errors="replace") as f:
                                for line in f:
                                    if line.startswith("PPid:"):
                                        ppid = int(line.split()[1])
                                        if ppid == pid:
                                            children.append(child_pid)
                                        break
                        except (ProcessLookupError, FileNotFoundError, PermissionError, ValueError):
                            continue
            except (PermissionError, FileNotFoundError):
                pass

    if recursive and children:
        all_descendants = list(children)
        for child in children:
            all_descendants.extend(get_child_pids(child, recursive=True))
        return sorted(list(set(all_descendants)))

    return sorted(list(set(children)))


def get_tree_rss_bytes(pid: int) -> int:
    """Return the total combined RSS in bytes of a process and all its child renderers."""
    total = read_proc_rss_bytes(pid)
    children = get_child_pids(pid, recursive=True)
    for c_pid in children:
        total += read_proc_rss_bytes(c_pid)
    return total


def get_tree_rss_mb(pid: int) -> float:
    """Return total combined RSS in megabytes of a process and all its child renderers."""
    return get_tree_rss_bytes(pid) / (1024.0 * 1024.0)


def is_rss_exceeded(
    pid: int,
    threshold_mb: float = DEFAULT_RECYCLE_RSS_MB,
    include_children: bool = True,
) -> bool:
    """True if process RSS (optionally including child renderers) reaches or exceeds threshold_mb."""
    if include_children:
        return get_tree_rss_mb(pid) >= threshold_mb
    return get_process_rss_mb(pid) >= threshold_mb


def _is_process_active(pid: int) -> bool:
    """Check if a process exists and is actively running (not dead or zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    status_path = Path(f"/proc/{pid}/status")
    if status_path.is_file():
        try:
            with open(status_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("State:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1] not in ("Z", "X")
                        break
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            return False
    return True


def terminate_process_tree(
    pid: int,
    timeout_sec: float = DEFAULT_TERMINATE_TIMEOUT_SEC,
    sig: int = signal.SIGTERM,
) -> list[int]:
    """Cleanly terminate a process and all its child renderers.

    Sends `sig` (SIGTERM by default) to child processes first, then parent.
    Waits up to `timeout_sec` for termination; issues SIGKILL if any processes
    remain alive past the deadline.
    Returns list of terminated PIDs.
    """
    if pid <= 1 or pid == os.getpid():
        return []

    # Collect descendants before sending signals
    descendants = get_child_pids(pid, recursive=True)
    target_pids = descendants + [pid]
    terminated: list[int] = []

    # Phase 1: Graceful termination (SIGTERM)
    for p in target_pids:
        try:
            os.kill(p, sig)
            terminated.append(p)
        except (ProcessLookupError, PermissionError):
            pass

    # Phase 2: Wait for processes to exit
    deadline = time.monotonic() + max(0.1, timeout_sec)
    remaining = list(terminated)
    while remaining and time.monotonic() < deadline:
        still_alive: list[int] = []
        for p in remaining:
            if _is_process_active(p):
                still_alive.append(p)
        remaining = still_alive
        if remaining:
            time.sleep(0.05)

    # Phase 3: Force kill (SIGKILL) on stubborn survivors
    for p in remaining:
        try:
            os.kill(p, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    return terminated


class BrowserSentinel:
    """Manages Chromium process lifecycle, RSS monitoring, and between-task recycling."""

    def __init__(
        self,
        threshold_mb: float = DEFAULT_RECYCLE_RSS_MB,
        max_heap_mb: int = DEFAULT_HEAP_MAX_MB,
        include_children: bool = True,
    ) -> None:
        if threshold_mb <= 0:
            raise ValueError(f"threshold_mb must be positive, got {threshold_mb}")
        self.threshold_mb = float(threshold_mb)
        self.max_heap_mb = int(max_heap_mb)
        self.include_children = include_children

        self.current_pid: Optional[int] = None
        self.is_running_task: bool = False
        self.tasks_completed: int = 0
        self.recycle_count: int = 0
        self.last_rss_mb: float = 0.0

    def attach_process(self, pid: int) -> None:
        """Register the PID of the active Chromium process."""
        if pid <= 0:
            raise ValueError(f"Invalid process PID: {pid}")
        self.current_pid = pid

    def detach_process(self) -> None:
        """Clear the registered Chromium process PID."""
        self.current_pid = None

    def attach_from_browser(
        self,
        browser: Any = None,
        ctx: Any = None,
        pw: Any = None,
    ) -> Optional[int]:
        """Extract process PID from Playwright / Cloak browser handles and attach."""
        pid: Optional[int] = None
        if pw is not None:
            try:
                transport = getattr(
                    getattr(getattr(pw, "_impl_obj", None), "_connection", None),
                    "_transport",
                    None,
                )
                proc = getattr(transport, "_proc", None)
                driver_pid = getattr(proc, "pid", None)
                if driver_pid and driver_pid > 0:
                    children = get_child_pids(driver_pid, recursive=False)
                    pid = children[0] if children else driver_pid
            except Exception:
                pass

        if pid is None and browser is not None:
            for attr in ("_process", "process", "pid"):
                val = getattr(browser, attr, None)
                if isinstance(val, int) and val > 0:
                    pid = val
                    break
                if hasattr(val, "pid"):
                    pid = val.pid
                    break

        if pid is not None and pid > 0:
            self.attach_process(pid)
            return pid
        return None

    def get_launch_flags(self) -> list[str]:
        """Return Chromium launch flags configured for this sentinel."""
        return get_chromium_memory_flags(self.max_heap_mb)

    def measure_rss_mb(self, pid: Optional[int] = None) -> float:
        """Measure current RSS in MB for the attached or specified process."""
        target_pid = pid if pid is not None else self.current_pid
        if target_pid is None or target_pid <= 0:
            self.last_rss_mb = 0.0
            return 0.0

        if self.include_children:
            rss = get_tree_rss_mb(target_pid)
        else:
            rss = get_process_rss_mb(target_pid)

        self.last_rss_mb = rss
        return rss

    def should_recycle(self, pid: Optional[int] = None) -> bool:
        """Check if memory usage meets or exceeds the recycling threshold."""
        rss = self.measure_rss_mb(pid)
        return rss >= self.threshold_mb

    def start_task(self) -> None:
        """Mark the beginning of a scraping/download task."""
        self.is_running_task = True

    def end_task(self) -> dict[str, Any]:
        """Mark task completion, record metrics, and evaluate recycle status."""
        self.is_running_task = False
        self.tasks_completed += 1
        rss = self.measure_rss_mb()
        needs_recycle = rss >= self.threshold_mb

        return {
            "tasks_completed": self.tasks_completed,
            "current_pid": self.current_pid,
            "rss_mb": rss,
            "threshold_mb": self.threshold_mb,
            "needs_recycle": needs_recycle,
            "recycle_count": self.recycle_count,
        }

    def recycle(
        self,
        stop_fn: Optional[Callable[[], None]] = None,
        start_fn: Optional[Callable[[], Optional[int]]] = None,
        timeout_sec: float = DEFAULT_TERMINATE_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        """Gracefully terminate current Chromium instance and optionally start a new one."""
        old_pid = self.current_pid
        terminated_pids: list[int] = []

        if stop_fn is not None:
            try:
                stop_fn()
            except Exception as e:
                logger.warning("Custom stop_fn failed during recycle: %s", e)

        if old_pid is not None and old_pid > 0:
            terminated_pids = terminate_process_tree(old_pid, timeout_sec=timeout_sec)
            self.detach_process()

        self.recycle_count += 1
        new_pid: Optional[int] = None

        if start_fn is not None:
            try:
                res = start_fn()
                if isinstance(res, int) and res > 0:
                    new_pid = res
                    self.attach_process(new_pid)
            except Exception as e:
                logger.error("start_fn failed during recycle: %s", e)

        return {
            "recycled": True,
            "old_pid": old_pid,
            "new_pid": new_pid,
            "recycle_count": self.recycle_count,
            "terminated_pids": terminated_pids,
        }

    def maybe_recycle_between_tasks(
        self,
        stop_fn: Optional[Callable[[], None]] = None,
        start_fn: Optional[Callable[[], Optional[int]]] = None,
        timeout_sec: float = DEFAULT_TERMINATE_TIMEOUT_SEC,
    ) -> tuple[bool, dict[str, Any]]:
        """Recycle between tasks if memory threshold is exceeded and no task is running.

        Returns (recycled_boolean, detail_dict).
        """
        if self.is_running_task:
            return False, {"reason": "task_in_flight", "recycled": False}

        if not self.should_recycle():
            return False, {
                "reason": "below_threshold",
                "rss_mb": self.last_rss_mb,
                "threshold_mb": self.threshold_mb,
                "recycled": False,
            }

        result = self.recycle(stop_fn=stop_fn, start_fn=start_fn, timeout_sec=timeout_sec)
        return True, result


def runner_maybe_recycle_browser(
    self: Any,
    worker_idx: Optional[int] = None,
    stop_fn: Optional[Callable[[], None]] = None,
    start_fn: Optional[Callable[[], Optional[int]]] = None,
) -> tuple[bool, dict[str, Any]]:
    """Gracefully recycle Chromium instances exceeding 750MB RSS between jobs (Row 927)."""
    sentinel = getattr(self, "_sentinel", None)
    if sentinel is None:
        sentinel = BrowserSentinel()
        self._sentinel = sentinel
    return sentinel.maybe_recycle_between_tasks(stop_fn=stop_fn, start_fn=start_fn)


def runner_check_browser_rss(self: Any, worker_idx: Optional[int] = None) -> float:
    """Check active browser RSS in MB."""
    sentinel = getattr(self, "_sentinel", None)
    if sentinel is not None:
        return sentinel.measure_rss_mb()
    return 0.0

