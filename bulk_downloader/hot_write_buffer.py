"""hot_write_buffer -- Ephemeral In-Memory Hot Write Buffer for High-Frequency Queue State.

Buffers transient queue state transitions, progress updates, and speed metrics in memory,
coalescing rapid writes per queue item, with time-based and capacity-based flush triggers,
and immediate durability guarantees for terminal states (Phase 150 / Row 1013).
"""
from __future__ import annotations

import logging
import threading
import time
import types
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Self

logger = logging.getLogger(__name__)


class BufferHealthState(str, Enum):
    """Three-state health verdict per Fleet Rule O1224."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class BufferHealthResult:
    """Detailed buffer health verdict with fail-closed guarantee (never fails open)."""

    state: BufferHealthState
    is_healthy: bool
    details: str = ""


@dataclass
class QueueItemState:
    """State record for an active or completed queue item."""

    item_id: str
    state: str = "queued"
    progress_percent: float = 0.0
    bytes_downloaded: int = 0
    speed_bps: float = 0.0
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    is_terminal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert state record to dictionary payload."""
        return asdict(self)


@dataclass
class BufferFlushPolicy:
    """Configuration governing hot buffer flushing behavior."""

    max_buffer_size: int = 500
    flush_interval_seconds: float = 1.0
    auto_flush_terminal: bool = True

    def __post_init__(self) -> None:
        if self.max_buffer_size <= 0:
            raise ValueError(f"max_buffer_size must be positive (got {self.max_buffer_size})")
        if self.flush_interval_seconds < 0:
            raise ValueError(f"flush_interval_seconds must be non-negative (got {self.flush_interval_seconds})")


class HotWriteBuffer:
    """Thread-safe in-memory buffer coalescing queue item updates before persistent flush."""

    def __init__(
        self,
        policy: BufferFlushPolicy | None = None,
        flush_sink: Callable[[list[dict[str, Any]]], Any] | None = None,
        fallback_reader: Callable[[str], Any] | None = None,
    ) -> None:
        self.policy = policy or BufferFlushPolicy()
        self.flush_sink = flush_sink
        self.fallback_reader = fallback_reader

        self._lock = threading.RLock()
        self._items: dict[str, QueueItemState] = {}
        self._dirty_ids: set[str] = set()
        self._last_flush_time = time.monotonic()

        self._total_recorded_updates = 0
        self._total_flushed_batches = 0
        self._total_flushed_items = 0

    @property
    def dirty_count(self) -> int:
        with self._lock:
            return len(self._dirty_ids)

    @property
    def total_buffered(self) -> int:
        with self._lock:
            return len(self._items)

    def check_health(self) -> BufferHealthResult:
        """Check operational buffer health with three-state fail-closed guarantee (O1224)."""
        if self.policy is None or self.policy.max_buffer_size <= 0:
            return BufferHealthResult(
                state=BufferHealthState.UNVERIFIABLE,
                is_healthy=False,
                details="Buffer flush policy invalid or unconfigured",
            )
        with self._lock:
            if len(self._dirty_ids) > (self.policy.max_buffer_size * 2):
                return BufferHealthResult(
                    state=BufferHealthState.DEGRADED,
                    is_healthy=False,
                    details="Buffer dirty count exceeds safe capacity threshold",
                )
            return BufferHealthResult(
                state=BufferHealthState.HEALTHY,
                is_healthy=True,
                details=f"Buffer healthy: {len(self._items)} items ({len(self._dirty_ids)} dirty)",
            )

    def record_update(
        self,
        item_id: str | int,
        state: str = "downloading",
        progress_percent: float = 0.0,
        bytes_downloaded: int = 0,
        speed_bps: float = 0.0,
        metadata: dict[str, Any] | None = None,
        is_terminal: bool = False,
    ) -> QueueItemState:
        """Record a state update, coalescing with existing dirty state in memory."""
        str_id = str(item_id)
        now_ts = time.time()
        meta = dict(metadata) if metadata else {}

        with self._lock:
            self._total_recorded_updates += 1

            existing = self._items.get(str_id)
            if existing is not None:
                existing.state = state
                existing.progress_percent = progress_percent
                existing.bytes_downloaded = bytes_downloaded
                existing.speed_bps = speed_bps
                existing.updated_at = now_ts
                existing.is_terminal = is_terminal
                if meta:
                    existing.metadata.update(meta)
                record = existing
            else:
                record = QueueItemState(
                    item_id=str_id,
                    state=state,
                    progress_percent=progress_percent,
                    bytes_downloaded=bytes_downloaded,
                    speed_bps=speed_bps,
                    updated_at=now_ts,
                    metadata=meta,
                    is_terminal=is_terminal,
                )
                self._items[str_id] = record

            self._dirty_ids.add(str_id)

            # Auto-flush triggers
            if is_terminal and self.policy.auto_flush_terminal or len(self._dirty_ids) >= self.policy.max_buffer_size:
                self.flush()

            return record

    def get_state(self, item_id: str | int) -> QueueItemState | None:
        """Fetch item state, consulting hot in-memory overlay first, then fallback store."""
        str_id = str(item_id)
        with self._lock:
            if str_id in self._items:
                return self._items[str_id]

        if self.fallback_reader:
            try:
                raw = self.fallback_reader(str_id)
                if raw is not None:
                    if isinstance(raw, QueueItemState):
                        return raw
                    if isinstance(raw, dict):
                        return QueueItemState(
                            item_id=str(raw.get("item_id", str_id)),
                            state=str(raw.get("state", "queued")),
                            progress_percent=float(raw.get("progress_percent", 0.0)),
                            bytes_downloaded=int(raw.get("bytes_downloaded", 0)),
                            speed_bps=float(raw.get("speed_bps", 0.0)),
                            updated_at=float(raw.get("updated_at", time.time())),
                            metadata=dict(raw.get("metadata", {})),
                            is_terminal=bool(raw.get("is_terminal", False)),
                        )
            except (OSError, ValueError, TypeError) as exc:
                logger.debug("Fallback reader error for item %s: %s", str_id, exc)

        return None

    def flush(self) -> int:
        """Flush all pending dirty items to the configured sink callable."""
        with self._lock:
            if not self._dirty_ids:
                return 0

            dirty_payloads = [
                self._items[item_id].to_dict()
                for item_id in self._dirty_ids
                if item_id in self._items
            ]
            count = len(dirty_payloads)

            if self.flush_sink and count > 0:
                try:
                    self.flush_sink(dirty_payloads)
                except Exception as exc:
                    logger.error("Error executing hot write buffer flush sink: %s", exc)
                    raise

            self._dirty_ids.clear()
            self._last_flush_time = time.monotonic()
            self._total_flushed_batches += 1
            self._total_flushed_items += count
            return count

    def flush_if_stale(self) -> int:
        """Flush dirty items if flush_interval_seconds has elapsed since last flush."""
        with self._lock:
            if not self._dirty_ids:
                return 0
            now = time.monotonic()
            if now - self._last_flush_time >= self.policy.flush_interval_seconds:
                return self.flush()
            return 0

    def clear(self) -> None:
        """Clear all in-memory items and dirty state without flushing."""
        with self._lock:
            self._items.clear()
            self._dirty_ids.clear()

    def stats(self) -> dict[str, Any]:
        """Return operational telemetry for the write buffer."""
        with self._lock:
            return {
                "buffered_items": len(self._items),
                "dirty_items": len(self._dirty_ids),
                "total_recorded_updates": self._total_recorded_updates,
                "total_flushed_batches": self._total_flushed_batches,
                "total_flushed_items": self._total_flushed_items,
            }

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.flush()


def create_hot_write_buffer(
    max_buffer_size: int = 500,
    flush_interval_seconds: float = 1.0,
    auto_flush_terminal: bool = True,
    flush_sink: Callable[[list[dict[str, Any]]], Any] | None = None,
    fallback_reader: Callable[[str], Any] | None = None,
) -> HotWriteBuffer:
    """Convenience factory creating a HotWriteBuffer instance with specified tuning."""
    policy = BufferFlushPolicy(
        max_buffer_size=max_buffer_size,
        flush_interval_seconds=flush_interval_seconds,
        auto_flush_terminal=auto_flush_terminal,
    )
    return HotWriteBuffer(
        policy=policy,
        flush_sink=flush_sink,
        fallback_reader=fallback_reader,
    )
