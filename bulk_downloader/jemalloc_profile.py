"""Native Memory Allocator Runtime Profiling & Heap Arena Fragmentation Suppressor.

Enterprise runtime profiler for heap arena fragmentation, native allocator introspection
(jemalloc, glibc malloc, tcmalloc, mimalloc), and automatic arena purging / decay.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AllocatorType(str, Enum):
    JEMALLOC = "jemalloc"
    GLIBC = "glibc"
    TCMALLOC = "tcmalloc"
    MIMALLOC = "mimalloc"
    SYSTEM = "system"


@dataclass
class ArenaStats:
    allocator: str
    allocated_bytes: int
    active_bytes: int
    resident_bytes: int
    metadata_bytes: int
    fragmentation_ratio: float
    arenas_count: int
    timestamp: float
    # Why native allocator stats (or RSS) could not be read; None when the numbers are measured.
    native_error: str | None = None


@dataclass
class PurgeResult:
    success: bool
    allocator: str
    purged_at: float
    decay_only: bool
    bytes_reclaimed_est: int
    error: str | None = None


class JemallocProfile:
    """Runtime native memory allocator profiler and arena telemetry collector."""

    def __init__(self) -> None:
        self._rss_error: str | None = None
        self._libc: ctypes.CDLL | None = None
        self._allocator_type: AllocatorType | None = None
        self._init_ctypes()

    def _init_ctypes(self) -> None:
        try:
            if sys.platform.startswith("linux") or sys.platform == "darwin":
                self._libc = ctypes.CDLL(None)
            elif sys.platform == "win32":
                self._libc = ctypes.cdll.msvcrt
        except Exception as exc:
            logger.debug("Failed to load standard C library via ctypes: %s", exc, exc_info=True)
            self._libc = None

    def detect_allocator(self) -> AllocatorType:
        """Detect the active memory allocator used by the process."""
        if self._allocator_type is not None:
            return self._allocator_type

        # Check for jemalloc via mallctl symbol
        if self._libc and hasattr(self._libc, "mallctl"):
            self._allocator_type = AllocatorType.JEMALLOC
            return self._allocator_type

        # Check for tcmalloc
        if self._libc and hasattr(self._libc, "tc_version"):
            self._allocator_type = AllocatorType.TCMALLOC
            return self._allocator_type

        # Check for mimalloc
        if self._libc and hasattr(self._libc, "mi_version"):
            self._allocator_type = AllocatorType.MIMALLOC
            return self._allocator_type

        # Check for glibc malloc
        if self._libc and hasattr(self._libc, "malloc_trim"):
            self._allocator_type = AllocatorType.GLIBC
            return self._allocator_type

        self._allocator_type = AllocatorType.SYSTEM
        return self._allocator_type

    def is_jemalloc_active(self) -> bool:
        """Return True if jemalloc is active in the current process."""
        return self.detect_allocator() == AllocatorType.JEMALLOC

    @staticmethod
    def compute_fragmentation(allocated_bytes: int, active_bytes: int) -> float:
        """Compute heap fragmentation ratio: (active - allocated) / active."""
        if active_bytes <= 0 or allocated_bytes >= active_bytes:
            return 0.0
        ratio = (active_bytes - allocated_bytes) / active_bytes
        return max(0.0, min(1.0, float(ratio)))

    def _invoke_mallctl(self, command: str) -> int:
        """Execute a jemalloc mallctl command."""
        if not self._libc or not hasattr(self._libc, "mallctl"):
            raise OSError("mallctl symbol is not available in libc")
        # int mallctl(const char *name, void *oldp, size_t *oldlenp, void *newp, size_t newlen);
        mallctl_fn = self._libc.mallctl
        mallctl_fn.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        mallctl_fn.restype = ctypes.c_int
        cmd_bytes = command.encode("ascii")
        rc = mallctl_fn(cmd_bytes, None, None, None, 0)
        return rc

    def _invoke_malloc_trim(self, pad: int = 0) -> int:
        """Execute glibc malloc_trim to release memory back to the OS."""
        if not self._libc or not hasattr(self._libc, "malloc_trim"):
            raise OSError("malloc_trim symbol is not available in libc")
        trim_fn = self._libc.malloc_trim
        trim_fn.argtypes = [ctypes.c_size_t]
        trim_fn.restype = ctypes.c_int
        return trim_fn(pad)

    def _read_proc_status_rss(self) -> int:
        """Read resident set size (RSS) from /proc/self/status on Linux."""
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            return int(parts[1]) * 1024  # kB to bytes
        except OSError as exc:
            self._rss_error = f"RSS read failed: {exc}"
            logger.debug("RSS read failed: %s", exc)
        return 0

    def get_arena_stats(self) -> ArenaStats:
        """Collect current heap arena allocation statistics and fragmentation."""
        alloc_type = self.detect_allocator()
        now = time.time()
        self._rss_error = None
        rss = self._read_proc_status_rss()
        native_error: str | None = None

        if alloc_type == AllocatorType.JEMALLOC and self._libc and hasattr(self._libc, "mallctl"):
            try:
                # Query jemalloc epoch to refresh cached stats
                epoch = ctypes.c_uint64(1)
                epoch_len = ctypes.c_size_t(ctypes.sizeof(epoch))
                rc = self._libc.mallctl(
                    b"epoch",
                    ctypes.byref(epoch),
                    ctypes.byref(epoch_len),
                    ctypes.byref(epoch),
                    epoch_len,
                )
                if rc != 0:
                    raise OSError(rc, "mallctl epoch failed")

                allocated = ctypes.c_size_t(0)
                active = ctypes.c_size_t(0)
                metadata = ctypes.c_size_t(0)
                resident = ctypes.c_size_t(0)
                sz = ctypes.c_size_t(ctypes.sizeof(ctypes.c_size_t))

                for name, value in ((b"stats.allocated", allocated), (b"stats.active", active),
                                    (b"stats.metadata", metadata), (b"stats.resident", resident)):
                    rc = self._libc.mallctl(name, ctypes.byref(value), ctypes.byref(sz), None, 0)
                    if rc != 0:
                        raise OSError(rc, f"mallctl {name.decode()} failed")

                alloc_val = allocated.value
                act_val = active.value
                res_val = resident.value if resident.value > 0 else rss
                frag = self.compute_fragmentation(alloc_val, act_val)

                return ArenaStats(
                    allocator=alloc_type.value,
                    allocated_bytes=alloc_val,
                    active_bytes=act_val,
                    resident_bytes=res_val,
                    metadata_bytes=metadata.value,
                    fragmentation_ratio=frag,
                    arenas_count=1,
                    timestamp=now,
                )
            except Exception as exc:
                native_error = f"mallctl stats: {exc}"
                logger.debug("mallctl stats collection error: %s", exc, exc_info=True)

        if alloc_type == AllocatorType.GLIBC and self._libc and hasattr(self._libc, "mallinfo2"):
            try:
                class Mallinfo2(ctypes.Structure):
                    _fields_ = [
                        ("arena", ctypes.c_size_t),
                        ("ordblks", ctypes.c_size_t),
                        ("smblks", ctypes.c_size_t),
                        ("hblks", ctypes.c_size_t),
                        ("hblkhd", ctypes.c_size_t),
                        ("usmblks", ctypes.c_size_t),
                        ("fsmblks", ctypes.c_size_t),
                        ("uordblks", ctypes.c_size_t),
                        ("fordblks", ctypes.c_size_t),
                        ("keepcost", ctypes.c_size_t),
                    ]

                self._libc.mallinfo2.restype = Mallinfo2
                mi = self._libc.mallinfo2()
                alloc_val = mi.uordblks + mi.hblkhd
                act_val = mi.arena + mi.hblkhd
                frag = self.compute_fragmentation(alloc_val, act_val)
                return ArenaStats(
                    allocator=alloc_type.value,
                    allocated_bytes=alloc_val,
                    active_bytes=act_val,
                    resident_bytes=rss if rss > 0 else act_val,
                    metadata_bytes=0,
                    fragmentation_ratio=frag,
                    arenas_count=1,
                    timestamp=now,
                )
            except Exception as exc:
                native_error = f"glibc mallinfo2: {exc}"
                logger.debug("glibc mallinfo2 call failed: %s", exc, exc_info=True)

        # Fallback for system allocator or unsupported platforms:
        return ArenaStats(
            allocator=alloc_type.value,
            allocated_bytes=rss,
            active_bytes=rss,
            resident_bytes=rss,
            metadata_bytes=0,
            fragmentation_ratio=0.0,
            arenas_count=1,
            timestamp=now,
            native_error="; ".join(e for e in (native_error, self._rss_error) if e) or None,
        )


class HeapArenaFragmentationSuppressor:
    """Active heap arena fragmentation suppressor with decay/purge triggers and async loop."""

    def __init__(
        self,
        profiler: JemallocProfile | None = None,
        default_threshold: float = 0.25,
    ) -> None:
        self.profiler = profiler or JemallocProfile()
        self.default_threshold = max(0.01, min(0.99, float(default_threshold)))
        self.total_purges: int = 0
        self.last_purge_timestamp: float | None = None
        self.worker_failures: int = 0
        self.last_worker_error: str | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    @property
    def is_loop_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def purge_arenas(self, decay_only: bool = False, run_gc: bool = True) -> PurgeResult:
        """Trigger explicit arena memory decay or purge to return pages to OS."""
        now = time.time()
        allocator = self.profiler.detect_allocator().value
        reclaimed_est = 0
        err_msg: str | None = None
        success = False

        if run_gc:
            gc.collect()

        stats_before = self.profiler.get_arena_stats()

        try:
            if self.profiler.is_jemalloc_active():
                cmd = "arena.4096.decay" if decay_only else "arena.4096.purge"
                rc = self.profiler._invoke_mallctl(cmd)
                success = (rc == 0)
            elif self.profiler.detect_allocator() == AllocatorType.GLIBC:
                rc = self.profiler._invoke_malloc_trim(0)
                success = (rc in (0, 1))
            else:
                # System fallback: gc already ran
                success = True
        except Exception as exc:
            err_msg = str(exc)
            logger.debug("Native arena purge error: %s", exc, exc_info=True)
            success = False

        stats_after = self.profiler.get_arena_stats()
        if stats_before.resident_bytes > stats_after.resident_bytes:
            reclaimed_est = stats_before.resident_bytes - stats_after.resident_bytes

        self.total_purges += 1
        self.last_purge_timestamp = now

        return PurgeResult(
            success=success,
            allocator=allocator,
            purged_at=now,
            decay_only=decay_only,
            bytes_reclaimed_est=reclaimed_est,
            error=err_msg,
        )

    def check_and_suppress(self, threshold: float | None = None) -> PurgeResult | None:
        """Check fragmentation and trigger purge if threshold is exceeded."""
        raw_thresh = self.default_threshold if threshold is None else float(threshold)
        target_thresh = max(0.01, min(0.99, raw_thresh))
        stats = self.profiler.get_arena_stats()
        if stats.fragmentation_ratio > target_thresh:
            logger.info(
                "Heap fragmentation %.3f exceeds threshold %.3f; triggering arena purge",
                stats.fragmentation_ratio,
                target_thresh,
            )
            return self.purge_arenas()
        return None

    async def _suppression_worker(self, interval_seconds: float, threshold: float | None) -> None:
        """Periodic background worker loop."""
        while not self._stop_event.is_set():
            try:
                self.check_and_suppress(threshold=threshold)
            except Exception as exc:
                self.worker_failures += 1
                self.last_worker_error = str(exc)
                logger.warning("Suppression worker tick failed: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def start_suppression_loop(
        self,
        interval_seconds: float = 30.0,
        threshold: float | None = None,
    ) -> None:
        """Start asynchronous background periodic suppression loop."""
        if self.is_loop_running:
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(
            self._suppression_worker(interval_seconds=interval_seconds, threshold=threshold)
        )

    async def stop_suppression_loop(self) -> None:
        """Stop asynchronous background suppression loop cleanly."""
        if self._loop_task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._loop_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not self._loop_task.done():
                self._loop_task.cancel()
        finally:
            self._loop_task = None

    def get_telemetry(self) -> dict[str, Any]:
        """Generate structured dictionary of allocator profiling and suppressor telemetry."""
        arena_stats = self.profiler.get_arena_stats()
        return {
            "allocator": self.profiler.detect_allocator().value,
            "arena_stats": asdict(arena_stats),
            "suppressor": {
                "default_threshold": self.default_threshold,
                "total_purges": self.total_purges,
                "last_purge_timestamp": self.last_purge_timestamp,
                "loop_running": self.is_loop_running,
                "worker_failures": self.worker_failures,
                "last_worker_error": self.last_worker_error,
            },
        }

    def export_telemetry_json(self) -> str:
        """Export telemetry snapshot as a JSON-formatted string."""
        return json.dumps(self.get_telemetry(), indent=2)
