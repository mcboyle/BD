"""Row 995: per-worker-thread context switch and CPU affinity telemetry.

Every reading is of the CALLING thread: getrusage(RUSAGE_THREAD) where the
platform has it, else /proc/thread-self/status for the switch counters. A
platform that offers neither reports None -- never the whole process's counters
under a thread's name. SiteRunner calls this from each worker thread at its
heartbeat and publishes the latest snapshot per worker in get_status().
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

try:
    import resource
except ImportError:  # Windows: no getrusage -- /proc next, else None
    resource = None  # type: ignore[assignment]


def _proc_thread_switches() -> tuple[int, int] | None:
    try:
        with open("/proc/thread-self/status", encoding="utf-8") as f:
            fields = dict(line.split(":", 1) for line in f if ":" in line)
        return (int(fields["voluntary_ctxt_switches"]),
                int(fields["nonvoluntary_ctxt_switches"]))
    except (OSError, KeyError, ValueError):
        return None


def current_thread_telemetry() -> dict[str, Any]:
    """Snapshot of the calling thread: switches, CPU time and affinity."""
    vol = invol = utime = stime = None
    rusage_thread = getattr(resource, "RUSAGE_THREAD", None)
    if rusage_thread is not None:
        ru = resource.getrusage(rusage_thread)
        vol, invol = int(ru.ru_nvcsw), int(ru.ru_nivcsw)
        utime, stime = float(ru.ru_utime), float(ru.ru_stime)
    else:
        switches = _proc_thread_switches()
        if switches is not None:
            vol, invol = switches
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    return {
        "thread_name": threading.current_thread().name,
        "native_id": threading.get_native_id(),
        "ts": time.time(),
        "voluntary_ctxt_switches": vol,
        "involuntary_ctxt_switches": invol,
        "user_time_s": utime,
        "system_time_s": stime,
        "cpu_affinity": affinity,
        "cpu_affinity_pinned": (None if affinity is None
                                else len(affinity) < (os.cpu_count() or len(affinity))),
    }
