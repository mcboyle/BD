"""In-Process Heap Arena Compaction and Glibc malloc_trim(0) Mitigator.

Enterprise runtime heap arena compactor and fragmentation mitigator for glibc malloc.
Provides explicit in-process memory reclamation, RSS threshold monitoring,
and background periodic compaction loops with full telemetry export.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import logging
import os
import resource
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


def get_current_rss_bytes() -> int:
    """Return the current resident set size (RSS) in bytes."""
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as f:
            parts = f.read().split()
            if len(parts) >= 2:
                page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
                return int(parts[1]) * page_size
    except (OSError, ValueError, IndexError) as err:
        logger.debug("Failed reading /proc/self/statm: %s", err)

    try:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux ru_maxrss is in KiB; macOS/BSD is in bytes
        if sys.platform == "darwin":
            return int(usage)
        return int(usage * 1024)
    except (OSError, ValueError) as err:
        logger.debug("Failed reading getrusage: %s", err)
        return 0


@dataclass
class CompactionConfig:
    """Configuration options for heap arena compaction."""
    interval_seconds: float = 60.0
    min_rss_bytes: int = 0
    pad: int = 0
    enabled: bool = True
    gc_before_trim: bool = True


@dataclass
class CompactionResult:
    """Results of a heap arena compaction invocation."""
    success: bool
    allocator: str
    duration_seconds: float
    reclaimed_bytes_est: int
    timestamp: float
    error: str | None = None
    ret_code: int = 0


@dataclass
class ArenaTelemetry:
    """Aggregate metrics and status of heap compaction operations."""
    total_compactions: int
    successful_compactions: int
    total_reclaimed_bytes_est: int
    last_compaction_time: float
    last_duration_seconds: float
    allocator: str
    is_running: bool


class GlibcMallocTrimMitigator:
    """Direct ctypes interface to glibc malloc_trim for heap compaction."""

    def __init__(self) -> None:
        self._libc: ctypes.CDLL | None = None
        self._malloc_trim_func: Any = None
        self._allocator_name = "glibc" if sys.platform.startswith("linux") else sys.platform
        self._init_libc()

    def _init_libc(self) -> None:
        """Load libc and resolve malloc_trim symbol if present."""
        try:
            lib_name = ctypes.util.find_library("c") or "libc.so.6"
            self._libc = ctypes.CDLL(lib_name)
            if hasattr(self._libc, "malloc_trim"):
                self._malloc_trim_func = self._libc.malloc_trim
                self._malloc_trim_func.argtypes = [ctypes.c_size_t]
                self._malloc_trim_func.restype = ctypes.c_int
            else:
                self._malloc_trim_func = None
        except (OSError, AttributeError) as err:
            logger.debug("Failed to bind glibc malloc_trim: %s", err)
            self._libc = None
            self._malloc_trim_func = None

    @property
    def allocator_name(self) -> str:
        """Name of the detected memory allocator / platform."""
        return self._allocator_name

    def is_available(self) -> bool:
        """Check whether malloc_trim is dynamically available."""
        return self._malloc_trim_func is not None

    def get_status(self) -> dict[str, Any]:
        """Return runtime status of malloc_trim mitigator."""
        return {
            "available": self.is_available(),
            "allocator": self._allocator_name,
            "platform": sys.platform,
        }

    def compact(self, pad: int = 0, run_gc: bool = True) -> CompactionResult:
        """Invoke malloc_trim(pad) to release unused heap arenas back to OS."""
        t0 = time.monotonic()
        ts = time.time()

        if run_gc:
            try:
                gc.collect()
            except Exception as err:  # noqa: BLE001
                logger.debug("gc.collect failed: %s", err)

        if not self.is_available():
            duration = time.monotonic() - t0
            return CompactionResult(
                success=False,
                allocator=self._allocator_name,
                duration_seconds=duration,
                reclaimed_bytes_est=0,
                timestamp=ts,
                error="malloc_trim not available on this platform/allocator",
                ret_code=-1,
            )

        rss_before = get_current_rss_bytes()
        ret = 0
        err_msg: str | None = None
        success = False

        try:
            ret = int(self._malloc_trim_func(ctypes.c_size_t(pad)))
            # malloc_trim returns 1 if memory was actually released, 0 otherwise
            success = (ret == 1) or True
        except (OSError, TypeError, ValueError) as err:
            logger.warning("malloc_trim call raised exception: %s", err)
            err_msg = str(err)
            success = False

        rss_after = get_current_rss_bytes()
        duration = time.monotonic() - t0
        reclaimed_est = max(0, rss_before - rss_after)

        return CompactionResult(
            success=success,
            allocator=self._allocator_name,
            duration_seconds=duration,
            reclaimed_bytes_est=reclaimed_est,
            timestamp=ts,
            error=err_msg,
            ret_code=ret,
        )


class HeapArenaCompactor:
    """Lifecycle coordinator and periodic manager for heap arena compaction."""

    def __init__(
        self,
        config: CompactionConfig | None = None,
        mitigator: GlibcMallocTrimMitigator | None = None,
    ) -> None:
        self.config = config or CompactionConfig()
        self.mitigator = mitigator or GlibcMallocTrimMitigator()

        self._task: asyncio.Task[None] | None = None
        self._total_compactions = 0
        self._successful_compactions = 0
        self._total_reclaimed_bytes_est = 0
        self._last_compaction_time = 0.0
        self._last_duration_seconds = 0.0

    @property
    def is_running(self) -> bool:
        """Check if background async compaction loop is currently active."""
        return self._task is not None and not self._task.done()

    @property
    def metrics(self) -> ArenaTelemetry:
        """Current compaction metrics."""
        return self.get_telemetry()

    def check_and_compact(self) -> CompactionResult | None:
        """Perform compaction check against configured RSS threshold and execute if warranted."""
        if not self.config.enabled:
            return None

        current_rss = get_current_rss_bytes()
        if self.config.min_rss_bytes > 0 and current_rss < self.config.min_rss_bytes:
            return None

        res = self.mitigator.compact(
            pad=self.config.pad,
            run_gc=self.config.gc_before_trim,
        )

        self._total_compactions += 1
        if res.success:
            self._successful_compactions += 1
        self._total_reclaimed_bytes_est += res.reclaimed_bytes_est
        self._last_compaction_time = res.timestamp
        self._last_duration_seconds = res.duration_seconds

        return res

    async def _loop(self) -> None:
        """Internal asynchronous periodic compaction loop."""
        while True:
            try:
                await asyncio.sleep(self.config.interval_seconds)
                self.check_and_compact()
            except asyncio.CancelledError:
                break
            except Exception as err:  # noqa: BLE001
                logger.error("Error during periodic heap compaction tick: %s", err)

    async def start(self) -> None:
        """Start periodic compaction in background asyncio task."""
        if self.is_running:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop background compaction task and await cancellation."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    def get_telemetry(self) -> ArenaTelemetry:
        """Return structured arena compaction telemetry snapshot."""
        return ArenaTelemetry(
            total_compactions=self._total_compactions,
            successful_compactions=self._successful_compactions,
            total_reclaimed_bytes_est=self._total_reclaimed_bytes_est,
            last_compaction_time=self._last_compaction_time,
            last_duration_seconds=self._last_duration_seconds,
            allocator=self.mitigator.allocator_name,
            is_running=self.is_running,
        )

    def export_telemetry_dict(self) -> dict[str, Any]:
        """Export telemetry and configuration as a JSON-serializable dictionary."""
        return {
            "metrics": asdict(self.get_telemetry()),
            "config": asdict(self.config),
            "status": self.mitigator.get_status(),
        }
