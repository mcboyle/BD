"""Row 1011: Zero-Copy Memory-Mapped I/O (mmap_size) & Page Cache Auto-Tuner.

Provides dynamic, architecture-aware SQLite configuration for:
1. Zero-copy memory-mapped I/O (PRAGMA mmap_size) allowing the kernel and SQLite
   to share page mappings directly without user-space buffer copies during reads.
2. Page cache auto-tuning (PRAGMA cache_size) based on database size, available
   system RAM, and workload profile (balanced, read-heavy, memory-constrained, high-throughput).
3. Architecture-aware safety limits: bounds mmap address space to 64 MiB on 32-bit platforms
   to prevent virtual address exhaustion, while allowing scalable multi-gigabyte mappings on 64-bit.
4. Non-fatal, graceful execution: pragma application never disrupts caller operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
import sqlite3
import sys
import threading
from typing import Any, Optional, Tuple, Union

log = logging.getLogger(__name__)

# Standard limits and defaults
DEFAULT_PAGE_SIZE = 4096
DEFAULT_32BIT_MAX_MMAP = 64 * 1024 * 1024        # 64 MiB ceiling for 32-bit platforms
DEFAULT_64BIT_MAX_MMAP = 1024 * 1024 * 1024      # 1 GiB ceiling for 64-bit platforms
DEFAULT_MIN_CACHE_KB = 2048                       # 2 MiB floor
DEFAULT_MAX_CACHE_KB = 256 * 1024                 # 256 MiB ceiling


class TuningProfile(str, Enum):
    """Workload tuning profiles for SQLite connections."""

    BALANCED = "balanced"
    READ_HEAVY = "read_heavy"
    MEMORY_CONSTRAINED = "memory_constrained"
    HIGH_THROUGHPUT = "high_throughput"
    CUSTOM = "custom"


@dataclass(frozen=True)
class TuningConfig:
    """Configuration parameters for memory-mapped I/O and page cache tuning."""

    profile: TuningProfile = TuningProfile.BALANCED
    mmap_size_bytes: Optional[int] = None
    cache_size_kb: Optional[int] = None
    max_mmap_bytes: int = DEFAULT_64BIT_MAX_MMAP
    max_cache_kb: int = DEFAULT_MAX_CACHE_KB
    min_cache_kb: int = DEFAULT_MIN_CACHE_KB
    enable_mmap: bool = True
    detect_system_ram: bool = True


@dataclass(frozen=True)
class TuningStatus:
    """Current live SQLite connection tuning state."""

    mmap_size_bytes: int
    cache_size_raw: int
    cache_size_kb: int
    page_size: int
    page_count: int
    db_size_bytes: int
    zero_copy_active: bool


@dataclass(frozen=True)
class TuningResult:
    """Outcome of applying memory-mapped I/O and page cache auto-tuning."""

    profile: TuningProfile
    applied: bool
    db_path: Optional[str] = None
    page_size: int = DEFAULT_PAGE_SIZE
    page_count: int = 0
    db_size_bytes: int = 0
    initial_mmap_size: int = 0
    configured_mmap_size: int = 0
    effective_mmap_size: int = 0
    initial_cache_size: int = 0
    configured_cache_size: int = 0
    effective_cache_size_kb: int = 0
    zero_copy_active: bool = False
    error: Optional[str] = None


def is_64bit_platform() -> bool:
    """Check if Python is running on a 64-bit platform."""
    return sys.maxsize > 2**32


def detect_available_ram() -> Optional[int]:
    """Safely detect available system memory in bytes without heavy dependencies."""
    try:
        # 1. Linux sysconf if available
        if hasattr(os, "sysconf"):
            pagesize = os.sysconf("SC_PAGE_SIZE")
            if "SC_AVPHYS_PAGES" in os.sysconf_names:
                avail_pages = os.sysconf("SC_AVPHYS_PAGES")
                if pagesize > 0 and avail_pages > 0:
                    return pagesize * avail_pages
            if "SC_PHYS_PAGES" in os.sysconf_names:
                phys_pages = os.sysconf("SC_PHYS_PAGES")
                if pagesize > 0 and phys_pages > 0:
                    # Estimate available as ~50% of total if available pages unknown
                    return (pagesize * phys_pages) // 2

        # 2. Linux /proc/meminfo fallback
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        parts = line.split()
                        return int(parts[1]) * 1024  # kB to bytes
    except Exception as exc:
        log.debug("RAM detection fallback: %s", exc)

    return None


def calculate_tuning_parameters(
    db_size_bytes: int,
    available_ram_bytes: Optional[int] = None,
    profile: TuningProfile = TuningProfile.BALANCED,
    config: Optional[TuningConfig] = None,
    is_64bit: Optional[bool] = None,
) -> Tuple[int, int]:
    """Calculate target mmap_size (bytes) and cache_size (KiB).

    Returns:
        tuple (mmap_size_bytes, cache_size_kb)
    """
    if config is None:
        config = TuningConfig(profile=profile)

    if is_64bit is None:
        is_64bit = is_64bit_platform()

    # Determine max mmap ceiling
    max_mmap = config.max_mmap_bytes
    if not is_64bit:
        max_mmap = min(max_mmap, DEFAULT_32BIT_MAX_MMAP)

    # If custom config explicitly specified values
    if profile == TuningProfile.CUSTOM:
        mmap_val = config.mmap_size_bytes if config.mmap_size_bytes is not None else 0
        cache_val = config.cache_size_kb if config.cache_size_kb is not None else DEFAULT_MIN_CACHE_KB
        return min(mmap_val, max_mmap), max(config.min_cache_kb, min(cache_val, config.max_cache_kb))

    # Memory constrained profile
    if profile == TuningProfile.MEMORY_CONSTRAINED:
        return 0, config.min_cache_kb

    # Ram baseline (default 2 GiB assumption if detection failed)
    ram = available_ram_bytes if (available_ram_bytes and available_ram_bytes > 0) else (2 * 1024 * 1024 * 1024)

    # Cache sizing based on RAM and profile
    if profile == TuningProfile.READ_HEAVY:
        # Allocate up to 5% of available RAM or up to max_cache_kb
        target_cache_kb = max(32768, min(ram // (20 * 1024), config.max_cache_kb))
        # Mmap up to 2x DB size, capped by max_mmap and 25% of RAM
        mmap_cap = min(max_mmap, ram // 4)
        target_mmap = max(64 * 1024 * 1024, min(db_size_bytes * 2, mmap_cap))
    elif profile == TuningProfile.HIGH_THROUGHPUT:
        # Allocate up to 8% of available RAM
        target_cache_kb = max(65536, min(ram // (12 * 1024), config.max_cache_kb))
        mmap_cap = min(max_mmap * 2 if is_64bit else max_mmap, ram // 2)
        target_mmap = max(128 * 1024 * 1024, min(db_size_bytes * 2, mmap_cap))
    else:  # BALANCED
        # Default ~32 MiB cache, bounded between min and max
        target_cache_kb = max(16384, min(ram // (64 * 1024), 65536))
        # Mmap DB size with 32 MiB floor and max_mmap ceiling
        mmap_cap = min(max_mmap, ram // 8)
        target_mmap = max(32 * 1024 * 1024, min(max(db_size_bytes, 32 * 1024 * 1024), mmap_cap))

    # Final clamps
    if not config.enable_mmap:
        target_mmap = 0

    target_mmap = min(target_mmap, max_mmap)
    target_cache_kb = max(config.min_cache_kb, min(target_cache_kb, config.max_cache_kb))

    return target_mmap, target_cache_kb


def inspect_connection(conn: Any) -> TuningStatus:
    """Inspect the current mmap, cache, and page metrics of a SQLite connection."""
    mmap_size = 0
    cache_size = 0
    page_size = DEFAULT_PAGE_SIZE
    page_count = 0

    try:
        row = conn.execute("PRAGMA mmap_size;").fetchone()
        if row is not None:
            mmap_size = int(row[0])
    except Exception as exc:
        log.debug("PRAGMA mmap_size inspect failed: %s", exc)

    try:
        row = conn.execute("PRAGMA cache_size;").fetchone()
        if row is not None:
            cache_size = int(row[0])
    except Exception as exc:
        log.debug("PRAGMA cache_size inspect failed: %s", exc)

    try:
        row = conn.execute("PRAGMA page_size;").fetchone()
        if row is not None:
            page_size = int(row[0])
    except Exception as exc:
        log.debug("PRAGMA page_size inspect failed: %s", exc)

    try:
        row = conn.execute("PRAGMA page_count;").fetchone()
        if row is not None:
            page_count = int(row[0])
    except Exception as exc:
        log.debug("PRAGMA page_count inspect failed: %s", exc)

    # Convert cache_size to KiB
    # In SQLite, if cache_size is negative, it represents -KiB. If positive, it is page count.
    if cache_size < 0:
        cache_size_kb = abs(cache_size)
    else:
        cache_size_kb = (cache_size * page_size) // 1024

    db_size = page_count * page_size
    zero_copy = mmap_size > 0

    return TuningStatus(
        mmap_size_bytes=mmap_size,
        cache_size_raw=cache_size,
        cache_size_kb=cache_size_kb,
        page_size=page_size,
        page_count=page_count,
        db_size_bytes=db_size,
        zero_copy_active=zero_copy,
    )


def auto_tune_connection(
    conn: Any,
    profile: TuningProfile = TuningProfile.BALANCED,
    config: Optional[TuningConfig] = None,
) -> TuningResult:
    """Apply zero-copy mmap_size and page cache tuning to a SQLite connection."""
    return _GLOBAL_TUNER.tune(conn, profile=profile, config=config)


class MmapPageCacheTuner:
    """Auto-tuner for SQLite zero-copy memory-mapped I/O and page cache."""

    def __init__(
        self,
        default_profile: TuningProfile = TuningProfile.BALANCED,
        config: Optional[TuningConfig] = None,
    ):
        self.default_profile = default_profile
        self.config = config or TuningConfig(profile=default_profile)
        self._lock = threading.Lock()
        self._total_tuned = 0
        self._total_zero_copy = 0
        self._total_errors = 0

    def inspect(self, conn: Any) -> TuningStatus:
        """Inspect connection pragma and page states."""
        return inspect_connection(conn)

    def tune(
        self,
        conn: Any,
        profile: Optional[TuningProfile] = None,
        config: Optional[TuningConfig] = None,
    ) -> TuningResult:
        """Apply zero-copy mmap_size and page cache tuning to a SQLite connection."""
        active_profile = profile or self.default_profile
        active_config = config or (
            self.config if profile is None else TuningConfig(profile=active_profile)
        )

        try:
            # 1. Initial inspection
            initial_status = self.inspect(conn)

            # 2. Compute parameters
            ram = detect_available_ram() if active_config.detect_system_ram else None
            mmap_target, cache_target_kb = calculate_tuning_parameters(
                db_size_bytes=initial_status.db_size_bytes,
                available_ram_bytes=ram,
                profile=active_profile,
                config=active_config,
            )

            # 3. Apply PRAGMA mmap_size
            effective_mmap = initial_status.mmap_size_bytes
            if active_config.enable_mmap and mmap_target >= 0:
                conn.execute(f"PRAGMA mmap_size = {int(mmap_target)};")
                row = conn.execute("PRAGMA mmap_size;").fetchone()
                if row is not None:
                    effective_mmap = int(row[0])

            # 4. Apply PRAGMA cache_size (using negative KiB notation)
            # In SQLite: PRAGMA cache_size = -N sets cache size to N KiB.
            # configured_cache_size records the request (raw PRAGMA notation),
            # symmetric with configured_mmap_size; the read-back lives in
            # effective_cache_size_kb so the two can be compared.
            configured_cache = -int(cache_target_kb)
            conn.execute(f"PRAGMA cache_size = {configured_cache};")

            # Re-inspect to confirm effective status
            final_status = self.inspect(conn)

            with self._lock:
                self._total_tuned += 1
                if final_status.zero_copy_active:
                    self._total_zero_copy += 1

            return TuningResult(
                profile=active_profile,
                applied=True,
                page_size=final_status.page_size,
                page_count=final_status.page_count,
                db_size_bytes=final_status.db_size_bytes,
                initial_mmap_size=initial_status.mmap_size_bytes,
                configured_mmap_size=mmap_target,
                effective_mmap_size=final_status.mmap_size_bytes,
                initial_cache_size=initial_status.cache_size_raw,
                configured_cache_size=configured_cache,
                effective_cache_size_kb=final_status.cache_size_kb,
                zero_copy_active=final_status.zero_copy_active,
                error=None,
            )

        except Exception as exc:
            log.warning("SQLite auto-tuning failed: %s", exc)
            with self._lock:
                self._total_errors += 1
            return TuningResult(
                profile=active_profile,
                applied=False,
                error=str(exc),
            )

    def get_telemetry(self) -> dict[str, Any]:
        """Return cumulative telemetry counters."""
        with self._lock:
            return {
                "total_tuned": self._total_tuned,
                "total_zero_copy": self._total_zero_copy,
                "total_errors": self._total_errors,
                "default_profile": self.default_profile.value,
            }

    def reset_telemetry(self) -> None:
        """Reset internal telemetry counters."""
        with self._lock:
            self._total_tuned = 0
            self._total_zero_copy = 0
            self._total_errors = 0


# Global instance
_GLOBAL_TUNER = MmapPageCacheTuner()


def get_default_tuner() -> MmapPageCacheTuner:
    """Get the global MmapPageCacheTuner singleton."""
    return _GLOBAL_TUNER


def get_tuning_metrics() -> dict[str, Any]:
    """Retrieve current auto-tuner metrics."""
    return _GLOBAL_TUNER.get_telemetry()
