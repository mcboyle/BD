"""Row 1029: Generational Garbage Collection Tuning and Dynamic Cycle Collection Pauser (AdaptiveGCController).

While download workers are active the controller holds a *workload lease*:
it measures every cyclic collection through ``gc.callbacks`` and adapts the
generation-0 threshold to the measured collector overhead (share of wall time
spent inside collections). When the last worker releases its lease the
baseline thresholds come back and the collections the raised thresholds
deferred are paid in one full collection, off the hot path.

The collector is process-global, so leases are reference counted: several
site runners share one tuning, and only the last one out restores it.
"""
from __future__ import annotations

import contextlib
import gc
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

log = logging.getLogger("bulk_downloader.adaptive_gc")


@dataclass
class AdaptiveGCConfig:
    enabled: bool = True
    default_gen0: int = 700
    default_gen1: int = 10
    default_gen2: int = 10
    high_throughput_multiplier: float = 2.5
    # Adaptation policy: keep collector overhead near target_overhead.
    target_overhead: float = 0.02
    step: float = 1.5
    max_gen0: int = 50_000
    adapt_interval_s: float = 1.0


def decide_gen0(gc_seconds: float, wall_seconds: float, gen0: int,
                baseline_gen0: int, config: AdaptiveGCConfig) -> Tuple[int, str]:
    """Pure policy: the next gen0 threshold for a measured window.

    Overhead above target -> collect less often (raise gen0, capped).
    Overhead under a quarter of target -> step back toward the baseline.
    """
    if wall_seconds <= 0:
        return gen0, "hold"
    overhead = gc_seconds / wall_seconds
    if overhead > config.target_overhead and gen0 < config.max_gen0:
        return min(config.max_gen0, max(gen0 + 1, int(gen0 * config.step))), "raise"
    if overhead < config.target_overhead / 4 and gen0 > baseline_gen0:
        return max(baseline_gen0, int(gen0 / config.step)), "lower"
    return gen0, "hold"


class AdaptiveGCController:
    """Manages generational GC thresholds and safe critical-section pausing."""

    def __init__(self, config: Optional[AdaptiveGCConfig] = None) -> None:
        self.config = config or AdaptiveGCConfig()
        self._initial_thresholds = gc.get_threshold()
        self._lock = threading.RLock()
        self._pause_count = 0
        self._last_pause_reason = ""
        self._last_pause_time = 0.0
        # Workload lease + measurement state.
        self._leases: Dict[str, int] = {}
        self._baseline: Optional[Tuple[int, int, int]] = None
        self._gc_started: Dict[int, float] = {}
        self._window_start = 0.0
        self._window_gc_s = 0.0
        self._stats = {"collections": [0, 0, 0], "gc_seconds": 0.0,
                       "adaptations": [], "deferred_collect": None}

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._pause_count > 0

    @property
    def active_workloads(self) -> int:
        with self._lock:
            return sum(self._leases.values())

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"collections": list(self._stats["collections"]),
                    "gc_seconds": self._stats["gc_seconds"],
                    "adaptations": list(self._stats["adaptations"]),
                    "deferred_collect": self._stats["deferred_collect"],
                    "active_workloads": sum(self._leases.values()),
                    "thresholds": list(gc.get_threshold())}

    def get_thresholds(self) -> Tuple[int, int, int]:
        return gc.get_threshold()

    def set_thresholds(self, gen0: int, gen1: int, gen2: int) -> None:
        with self._lock:
            gc.set_threshold(gen0, gen1, gen2)

    def tune_for_workload(self, workload_type: str = "high_throughput") -> Tuple[int, int, int]:
        with self._lock:
            if workload_type == "high_throughput":
                g0, g1, g2 = self._initial_thresholds
                multiplier = self.config.high_throughput_multiplier
                tuned = (int(g0 * multiplier), int(g1 * 1.5), int(g2 * 1.5))
                gc.set_threshold(*tuned)
                return tuned
            return gc.get_threshold()

    def restore_defaults(self) -> None:
        with self._lock:
            gc.set_threshold(*self._initial_thresholds)

    # -- workload lease: measured, adaptive tuning while workers run --------

    def begin_workload(self, owner: str) -> bool:
        """Take a lease. The first lease in the process installs the
        collector monitor and the high-throughput thresholds."""
        if not self.config.enabled:
            return False
        with self._lock:
            first = not self._leases
            self._leases[owner] = self._leases.get(owner, 0) + 1
            if first:
                self._baseline = gc.get_threshold()
                self._gc_started.clear()
                self._window_start = time.perf_counter()
                self._window_gc_s = 0.0
                g0, g1, g2 = self._baseline
                gc.set_threshold(int(g0 * self.config.high_throughput_multiplier), g1, g2)
                if self._on_gc not in gc.callbacks:
                    gc.callbacks.append(self._on_gc)
            return True

    def end_workload(self, owner: str) -> Optional[Dict[str, Any]]:
        """Release a lease. The last one out removes the monitor, restores
        the baseline and runs the full collection the tuning deferred."""
        with self._lock:
            held = self._leases.get(owner, 0)
            if held <= 0:
                return None
            if held == 1:
                del self._leases[owner]
            else:
                self._leases[owner] = held - 1
            if self._leases:
                return None
            with contextlib.suppress(ValueError):
                gc.callbacks.remove(self._on_gc)
            if self._baseline is not None:
                gc.set_threshold(*self._baseline)
                self._baseline = None
            report = self.collect_adaptive(generation=2)
            self._stats["deferred_collect"] = report
            return report

    @contextlib.contextmanager
    def workload(self, owner: str) -> Iterator[None]:
        took = self.begin_workload(owner)
        try:
            yield
        finally:
            if took:
                self.end_workload(owner)

    def _on_gc(self, phase: str, info: Dict[str, Any]) -> None:
        # Runs inside the collector: no allocation-heavy work, never raise.
        gen = info.get("generation", 0)
        now = time.perf_counter()
        if phase == "start":
            self._gc_started[gen] = now
            return
        began = self._gc_started.pop(gen, None)
        if began is None:
            return
        spent = now - began
        self._stats["collections"][gen] += 1
        self._stats["gc_seconds"] += spent
        self._window_gc_s += spent
        if now - self._window_start >= self.config.adapt_interval_s:
            # Never block inside the collector: if another thread holds the
            # lock, this window rolls into the next one.
            if not self._lock.acquire(blocking=False):
                return
            try:
                self.adapt(self._window_gc_s, now - self._window_start)
            finally:
                self._lock.release()
            self._window_start = now
            self._window_gc_s = 0.0

    def adapt(self, gc_seconds: float, wall_seconds: float) -> str:
        """Apply the policy to one measured window while a lease is held."""
        with self._lock:
            if self._baseline is None:
                return "idle"
            g0, g1, g2 = gc.get_threshold()
            new_g0, action = decide_gen0(gc_seconds, wall_seconds, g0,
                                         self._baseline[0], self.config)
            if new_g0 != g0:
                gc.set_threshold(new_g0, g1, g2)
                self._stats["adaptations"].append((action, g0, new_g0))
                del self._stats["adaptations"][:-32]
            return action

    # -- explicit critical sections ------------------------------------------

    def pause_cycle_collection(self, reason: str = "critical_section") -> bool:
        with self._lock:
            if self._pause_count == 0:
                gc.disable()
                self._last_pause_reason = reason
                self._last_pause_time = time.time()
            self._pause_count += 1
            return True

    def resume_cycle_collection(self) -> bool:
        with self._lock:
            if self._pause_count > 0:
                self._pause_count -= 1
                if self._pause_count == 0:
                    gc.enable()
            return True

    @contextlib.contextmanager
    def critical_section_gc_paused(self, reason: str = "critical") -> Iterator[None]:
        self.pause_cycle_collection(reason=reason)
        try:
            yield
        finally:
            self.resume_cycle_collection()

    def collect_adaptive(self, generation: int = 2) -> Dict[str, Any]:
        with self._lock:
            before_objs = len(gc.get_objects())
            t0 = time.time()
            collected = gc.collect(generation)
            dur_ms = (time.time() - t0) * 1000.0
            after_objs = len(gc.get_objects())

            return {
                "ok": True,
                "generation": generation,
                "collected_count": collected,
                "objects_before": before_objs,
                "objects_after": after_objs,
                "objects_freed": before_objs - after_objs,
                "duration_ms": round(dur_ms, 3),
                "current_counts": list(gc.get_count()),
                "thresholds": list(gc.get_threshold()),
            }


_GLOBAL_CONTROLLER: Optional[AdaptiveGCController] = None
_CONTROLLER_LOCK = threading.Lock()


def get_adaptive_gc_controller() -> AdaptiveGCController:
    global _GLOBAL_CONTROLLER
    if _GLOBAL_CONTROLLER is None:
        with _CONTROLLER_LOCK:
            if _GLOBAL_CONTROLLER is None:
                _GLOBAL_CONTROLLER = AdaptiveGCController()
    return _GLOBAL_CONTROLLER
