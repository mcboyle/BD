"""bulk_downloader.sqlite_freelist_vacuum -- Row 1015: Adaptive SQLite Freelist Monitor & Vacuum.

Provides SQLite B-tree freelist ratio monitoring, page reclamation threshold calculation,
and non-blocking idle-cycle incremental vacuuming.
Follows Fleet Rule 21 (zero site logins touched).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class FreelistMetrics:
    page_count: int
    freelist_count: int
    page_size: int
    freelist_bytes: int
    freelist_ratio: float
    auto_vacuum_mode: int
    timestamp: float


class FreelistMonitor:
    """Monitors SQLite B-tree freelist allocation and fragmentation ratios."""

    def __init__(self, freelist_ratio_threshold: float = 0.15) -> None:
        self.freelist_ratio_threshold = float(freelist_ratio_threshold)

    def inspect(self, conn: sqlite3.Connection) -> FreelistMetrics:
        """Inspect current SQLite B-tree page counts and freelist metrics."""
        cursor = conn.cursor()
        page_count = 0
        freelist_count = 0
        page_size = 4096
        auto_vacuum = 0

        try:
            row = cursor.execute("PRAGMA page_count").fetchone()
            if row and row[0] is not None:
                page_count = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.debug("PRAGMA page_count failed: %s", exc)

        try:
            row = cursor.execute("PRAGMA freelist_count").fetchone()
            if row and row[0] is not None:
                freelist_count = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.debug("PRAGMA freelist_count failed: %s", exc)

        try:
            row = cursor.execute("PRAGMA page_size").fetchone()
            if row and row[0] is not None:
                page_size = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.debug("PRAGMA page_size failed: %s", exc)

        try:
            row = cursor.execute("PRAGMA auto_vacuum").fetchone()
            if row and row[0] is not None:
                auto_vacuum = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.debug("PRAGMA auto_vacuum failed: %s", exc)

        freelist_ratio = float(freelist_count) / float(page_count) if page_count > 0 else 0.0
        freelist_bytes = freelist_count * page_size

        return FreelistMetrics(
            page_count=page_count,
            freelist_count=freelist_count,
            page_size=page_size,
            freelist_bytes=freelist_bytes,
            freelist_ratio=round(freelist_ratio, 4),
            auto_vacuum_mode=auto_vacuum,
            timestamp=time.time(),
        )

    def should_vacuum(self, conn: sqlite3.Connection) -> bool:
        """Determine if freelist ratio exceeds threshold and contains reclaimable pages."""
        metrics = self.inspect(conn)
        return metrics.freelist_ratio >= self.freelist_ratio_threshold and metrics.freelist_count > 0


class IncrementalVacuumController:
    """Controls bounded incremental vacuuming during idle windows."""

    def __init__(
        self,
        pages_per_step: int = 100,
        monitor: FreelistMonitor | None = None,
    ) -> None:
        self.pages_per_step = max(1, int(pages_per_step))
        self.monitor = monitor or FreelistMonitor()

    def step_vacuum(self, conn: sqlite3.Connection, pages: int | None = None) -> int:
        """Reclaim up to N freelist pages in a single incremental vacuum step."""
        cursor = conn.cursor()
        target_pages = self.pages_per_step if pages is None or pages <= 0 else int(pages)

        before = 0
        try:
            row = cursor.execute("PRAGMA freelist_count").fetchone()
            if row and row[0] is not None:
                before = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.warning("PRAGMA freelist_count query failed: %s", exc)
            return 0

        if before <= 0:
            return 0

        try:
            cursor.execute(f"PRAGMA incremental_vacuum({target_pages})")
            cursor.fetchall()
        except (sqlite3.Error, OSError) as exc:
            log.warning("PRAGMA incremental_vacuum failed: %s", exc)
            return 0

        after = 0
        try:
            row = cursor.execute("PRAGMA freelist_count").fetchone()
            if row and row[0] is not None:
                after = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.warning("PRAGMA freelist_count post-vacuum failed: %s", exc)
            return 0

        return max(0, before - after)

    def run_idle_cycle(
        self,
        conn: sqlite3.Connection,
        is_idle_callback: Callable[[], bool] | None = None,
        max_duration_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """Run incremental vacuuming across bounded steps while idle."""
        start_time = time.monotonic()
        cursor = conn.cursor()

        # Check auto_vacuum mode (2 = INCREMENTAL)
        auto_vacuum = 0
        try:
            row = cursor.execute("PRAGMA auto_vacuum").fetchone()
            if row and row[0] is not None:
                auto_vacuum = int(row[0])
        except (sqlite3.Error, OSError) as exc:
            log.warning("PRAGMA auto_vacuum failed: %s", exc)

        if auto_vacuum != 2:
            return {
                "supported": False,
                "pages_freed": 0,
                "steps_completed": 0,
                "interrupted": False,
                "initial_freelist": 0,
                "final_freelist": 0,
                "duration_seconds": 0.0,
            }

        initial_freelist = 0
        try:
            row = cursor.execute("PRAGMA freelist_count").fetchone()
            if row and row[0] is not None:
                initial_freelist = int(row[0])
        except (sqlite3.Error, OSError):
            pass

        current_freelist = initial_freelist
        pages_freed = 0
        steps_completed = 0
        interrupted = False

        while current_freelist > 0:
            if is_idle_callback is not None and not is_idle_callback():
                interrupted = True
                break

            if (time.monotonic() - start_time) >= max_duration_seconds:
                interrupted = True
                break

            step_pages = min(self.pages_per_step, current_freelist)
            freed = self.step_vacuum(conn, pages=step_pages)
            if freed <= 0:
                break

            pages_freed += freed
            steps_completed += 1
            current_freelist = max(0, current_freelist - freed)

        final_freelist = current_freelist
        try:
            row = cursor.execute("PRAGMA freelist_count").fetchone()
            if row and row[0] is not None:
                final_freelist = int(row[0])
        except (sqlite3.Error, OSError):
            pass

        return {
            "supported": True,
            "pages_freed": pages_freed,
            "steps_completed": steps_completed,
            "interrupted": interrupted,
            "initial_freelist": initial_freelist,
            "final_freelist": final_freelist,
            "duration_seconds": round(time.monotonic() - start_time, 4),
        }
