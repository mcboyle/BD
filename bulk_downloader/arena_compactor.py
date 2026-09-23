"""Paced glibc arena trimming with native results and observed process RSS."""

from __future__ import annotations

import ctypes
import logging
import resource
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _read_process_rss_bytes() -> int | None:
    """Read current RSS in bytes, or None when the observation is unavailable."""
    try:
        statm_path = Path("/proc/self/statm")
        if statm_path.is_file():
            parts = statm_path.read_text(encoding="utf-8").split()
            if len(parts) >= 2:
                pages = int(parts[1])
                if pages >= 0:
                    return pages * resource.getpagesize()
    except (OSError, ValueError):
        pass
    return None


@dataclass
class CompactionResult:
    """Outcome and telemetry of a glibc memory arena compaction run."""

    trimmed: bool
    rss_before_bytes: int | None
    rss_after_bytes: int | None
    rss_delta_bytes: int | None
    duration_ms: float
    glibc_available: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trimmed": self.trimmed,
            "rss_before_bytes": self.rss_before_bytes,
            "rss_after_bytes": self.rss_after_bytes,
            "rss_delta_bytes": self.rss_delta_bytes,
            "duration_ms": round(self.duration_ms, 3),
            "glibc_available": self.glibc_available,
            "status": self.status,
        }


class ArenaCompactor:
    """Paced glibc memory arena compactor."""

    def __init__(
        self,
        min_trim_interval_seconds: float = 1.0,
    ) -> None:
        self.min_trim_interval_seconds = min_trim_interval_seconds
        self._last_trim_time: float = 0.0
        self._trim_count: int = 0
        self._lock = threading.Lock()
        self._libc_handle: Any | None = None
        self._init_glibc_bindings()

    def _init_glibc_bindings(self) -> None:
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim.argtypes = [ctypes.c_size_t]
            libc.malloc_trim.restype = ctypes.c_int
            self._libc_handle = libc
        except (OSError, AttributeError) as exc:
            logger.debug("glibc malloc_trim not available: %s", exc)
            self._libc_handle = None

    def compact_arenas(self, force: bool = False, pad: int = 0) -> CompactionResult:
        """Trim fragmented memory arenas and release cached heap pages back to the kernel."""
        with self._lock:
            now = time.monotonic()
            rss_before = _read_process_rss_bytes()

            # Pacing guard: skip redundant trims within interval unless forced
            if (
                not force
                and self._last_trim_time > 0
                and (now - self._last_trim_time) < self.min_trim_interval_seconds
            ):
                return CompactionResult(
                    trimmed=False,
                    rss_before_bytes=rss_before,
                    rss_after_bytes=rss_before,
                    rss_delta_bytes=0 if rss_before is not None else None,
                    duration_ms=0.0,
                    glibc_available=self._libc_handle is not None,
                    status="paced",
                )

            start = time.perf_counter()
            glibc_available = self._libc_handle is not None
            trimmed = False
            status = "unavailable"

            if self._libc_handle is not None:
                try:
                    trimmed = self._libc_handle.malloc_trim(ctypes.c_size_t(pad)) == 1
                except OSError as exc:
                    status = "error"
                    logger.warning("malloc_trim invocation error: %s", exc)
                else:
                    self._trim_count += 1
                    status = "released" if trimmed else "no_release"

            duration_ms = (time.perf_counter() - start) * 1000.0
            rss_after = _read_process_rss_bytes()
            rss_delta = (
                rss_before - rss_after
                if rss_before is not None and rss_after is not None
                else None
            )

            self._last_trim_time = now

            return CompactionResult(
                trimmed=trimmed,
                rss_before_bytes=rss_before,
                rss_after_bytes=rss_after,
                rss_delta_bytes=rss_delta,
                duration_ms=duration_ms,
                glibc_available=glibc_available,
                status=status,
            )

    def get_compactor_status(self) -> dict[str, Any]:
        """Report binding availability, completed native calls, and pacing."""
        with self._lock:
            return {
                "glibc_available": self._libc_handle is not None,
                "trim_count": self._trim_count,
                "min_trim_interval_seconds": self.min_trim_interval_seconds,
            }

    def to_dict(self) -> dict[str, Any]:
        return self.get_compactor_status()


# Global singleton instance
_GLOBAL_ARENA_COMPACTOR: ArenaCompactor | None = None


def get_arena_compactor() -> ArenaCompactor:
    """Retrieve or initialize the global ArenaCompactor singleton."""
    global _GLOBAL_ARENA_COMPACTOR
    if _GLOBAL_ARENA_COMPACTOR is None:
        _GLOBAL_ARENA_COMPACTOR = ArenaCompactor()
    return _GLOBAL_ARENA_COMPACTOR


def compact_glibc_arenas(force: bool = False, pad: int = 0) -> CompactionResult:
    """Convenience helper to compact glibc memory arenas."""
    return get_arena_compactor().compact_arenas(force=force, pad=pad)
