"""Resident Memory Page Fault & Heap Growth Telemetry.

Provides telemetry on process resident set size (RSS), virtual memory, minor
and major page faults, and heap growth measured as the change in the data
segment (VmData: heap plus anonymous mappings). A field that cannot be read on
this platform is None and is named in ``unavailable`` -- never a 0 that looks
like a measurement. Bounded retention prevents unevicted memory growth (Rule J1).
Surfaced through the diagnostics bundle (``process_memory``).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    import resource
except ImportError:
    resource = None  # type: ignore


@dataclass
class ResidentMemoryMetrics:
    rss_bytes: int | None
    vms_bytes: int | None
    peak_rss_bytes: int | None
    heap_data_bytes: int | None


@dataclass
class PageFaultMetrics:
    minor_faults: int | None
    major_faults: int | None
    minor_fault_rate: float | None
    major_fault_rate: float | None


@dataclass
class HeapGrowthMetrics:
    heap_growth_delta: int | None
    growth_rate_bytes_sec: float | None
    spike_detected: bool


@dataclass
class MemoryTelemetrySnapshot:
    timestamp: float
    resident: ResidentMemoryMetrics
    page_faults: PageFaultMetrics
    heap: HeapGrowthMetrics
    unavailable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _delta(cur: int | None, prev: int | None) -> int | None:
    return None if cur is None or prev is None else cur - prev


class ResidentMemoryPageFaultTelemetry:
    """Collects and tracks resident memory, page faults, and heap growth telemetry."""

    def __init__(
        self,
        max_history: int = 100,
        growth_spike_threshold_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.max_history = max(1, int(max_history))
        self.growth_spike_threshold_bytes = int(growth_spike_threshold_bytes)
        self._history: deque[MemoryTelemetrySnapshot] = deque(maxlen=self.max_history)
        self._lock = threading.Lock()
        self._last: tuple[float, int | None, int | None, int | None] | None = None

    def _read_proc_status(self) -> dict[str, int]:
        """Memory fields (bytes) from /proc/self/status; a field it cannot read is absent."""
        try:
            # The Name line is the raw 15-byte comm, so a multi-byte process name can end
            # mid-character: replace it rather than lose the Vm* lines to a decode error.
            with open("/proc/self/status", "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as exc:
            logger.debug("/proc/self/status unreadable: %s", exc)
            return {}
        wanted = ("VmRSS", "VmSize", "VmHWM", "VmData")
        res: dict[str, int] = {}
        for line in lines:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if key in wanted and parts and parts[0].isdecimal():
                res[key] = int(parts[0]) * 1024  # kB to bytes
        return res

    def _get_rusage(self) -> tuple[int, int, int] | None:
        """Return (minor_faults, major_faults, max_rss_bytes), or None when unavailable."""
        if resource is None:
            return None
        try:
            ru = resource.getrusage(resource.RUSAGE_SELF)
        except (OSError, ValueError) as exc:
            logger.debug("resource.getrusage call failed: %s", exc)
            return None
        # ru_maxrss is in kilobytes on Linux, bytes on macOS/BSD
        scale = 1024 if sys.platform.startswith("linux") else 1
        return ru.ru_minflt, ru.ru_majflt, ru.ru_maxrss * scale

    def sample(self) -> MemoryTelemetrySnapshot:
        """Capture one telemetry snapshot and compute rates against the previous sample."""
        now = time.time()
        proc = self._read_proc_status()
        ru = self._get_rusage()
        minflt, majflt, ru_maxrss = ru if ru is not None else (None, None, None)
        peak = proc.get("VmHWM", ru_maxrss)
        hdata = proc.get("VmData")

        with self._lock:
            prev, self._last = self._last, (now, minflt, majflt, hdata)
            dt = now - prev[0] if prev is not None else 0.0
            d_min = _delta(minflt, prev[1]) if prev else None
            d_maj = _delta(majflt, prev[2]) if prev else None
            d_heap = _delta(hdata, prev[3]) if prev else None

            def rate(d: int | None) -> float | None:
                return round(d / dt, 2) if d is not None and dt > 0 else None

            resident = ResidentMemoryMetrics(
                rss_bytes=proc.get("VmRSS"),
                vms_bytes=proc.get("VmSize"),
                peak_rss_bytes=peak,
                heap_data_bytes=hdata,
            )
            faults = PageFaultMetrics(
                minor_faults=minflt,
                major_faults=majflt,
                minor_fault_rate=rate(d_min),
                major_fault_rate=rate(d_maj),
            )
            heap = HeapGrowthMetrics(
                heap_growth_delta=d_heap,
                growth_rate_bytes_sec=rate(d_heap),
                spike_detected=d_heap is not None and d_heap >= self.growth_spike_threshold_bytes,
            )
            unavailable = [
                f"{group}.{name}"
                for group, metrics in (("resident", resident), ("page_faults", faults))
                for name, value in asdict(metrics).items()
                if value is None and not name.endswith("_rate")
            ]
            snapshot = MemoryTelemetrySnapshot(
                timestamp=now, resident=resident, page_faults=faults, heap=heap,
                unavailable=unavailable,
            )
            self._history.append(snapshot)
            return snapshot

    def get_history(self) -> list[MemoryTelemetrySnapshot]:
        """Return a copy of the captured snapshots."""
        with self._lock:
            return list(self._history)

    def reset(self) -> None:
        """Clear historical snapshots and state."""
        with self._lock:
            self._history.clear()
            self._last = None


_default_collector = ResidentMemoryPageFaultTelemetry()


def get_memory_telemetry_snapshot() -> dict[str, Any]:
    """Convenience function returning the latest memory and page fault telemetry snapshot."""
    return _default_collector.sample().to_dict()
