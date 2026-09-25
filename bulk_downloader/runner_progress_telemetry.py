"""Hierarchical Multi-Stream Progress Telemetry Renderer.

Provides hierarchical stream progress tracking, metric rollup aggregation
(bytes, throughput, ETA, status composite), telemetry snapshots and delta
feeds for SSE/telemetry persistence, and terminal tree rendering with
box-drawing characters, progress bars, ANSI color control, and width
constraints.

Bounds (row 986): the delta feed keeps one sequence number per live stream,
never one entry per event; finished leaf streams beyond
``max_finished_streams`` are evicted and their bytes folded into the parent,
so a parent's rollup stays exact; the parent graph refuses cycles and every
walk (ancestors, rollup, render) carries a visited set. A finished stream (or
one with no declared size) adds only the bytes it moved to its ancestors'
total, so a failed or cancelled transfer's remainder never holds a rollup
below 100% or inflates its ETA.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ANSI_RESET = "\033[0m"


def _now() -> float:
    return time.time()


class StreamStatus(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_FINISHED_STATUSES = frozenset(
    {StreamStatus.COMPLETED, StreamStatus.FAILED, StreamStatus.CANCELLED})


def _finite_or_none(value: float | None) -> float | None:
    """An ETA the JSON status payload can carry: inf/NaN become unknown."""
    if value is None or not math.isfinite(value):
        return None
    return value


@dataclass
class StreamNode:
    stream_id: str
    parent_id: str | None = None
    label: str = ""
    total_bytes: int | None = None
    completed_bytes: int = 0
    total_items: int | None = None
    completed_items: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    status: StreamStatus = StreamStatus.IDLE
    started_at: float | None = None
    updated_at: float = field(default_factory=_now)
    completed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    version: int = 0
    # Evicted finished descendants, folded in so the rollup stays exact.
    retired_streams: int = 0
    retired_completed_bytes: int = 0
    retired_total_bytes: int = 0

    @property
    def percent(self) -> float:
        if self.total_bytes is not None and self.total_bytes > 0:
            pct = (self.completed_bytes / self.total_bytes) * 100.0
            return max(0.0, min(100.0, pct))
        return 0.0

    @property
    def expected_bytes(self) -> int:
        """What this stream adds to its ancestors' total: its declared size
        while it can still move bytes; once finished (completed, failed or
        cancelled -- a dead stream moves no more) or with no declared size,
        only what it moved, so its remainder is never outstanding work."""
        if self.status in _FINISHED_STATUSES or self.total_bytes is None:
            return self.completed_bytes
        return max(self.total_bytes, self.completed_bytes)

    def start(self) -> None:
        self.status = StreamStatus.ACTIVE
        now = _now()
        if self.started_at is None:
            self.started_at = now
        # A restarted stream (a retry) is no longer in error.
        self.metadata.pop("error", None)
        self.updated_at = now
        self.version += 1

    def update(
        self,
        completed_bytes: int | None = None,
        delta_bytes: int | None = None,
        speed_bps: float | None = None,
        total_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        if total_bytes is not None:
            self.total_bytes = total_bytes
        if completed_bytes is not None:
            self.completed_bytes = max(0, completed_bytes)
        elif delta_bytes is not None:
            self.completed_bytes = max(0, self.completed_bytes + delta_bytes)

        if speed_bps is not None:
            speed = float(speed_bps)
            if not math.isfinite(speed):
                speed = 0.0
            self.speed_bps = max(0.0, speed)
            if self.total_bytes is None:
                # No declared size: the remaining time is unknown, not zero.
                self.eta_seconds = None
            elif self.total_bytes > self.completed_bytes:
                rem = self.total_bytes - self.completed_bytes
                self.eta_seconds = _finite_or_none(rem / self.speed_bps) if self.speed_bps > 0 else None
            else:
                self.eta_seconds = 0.0

        if metadata:
            self.metadata.update(metadata)

        if self.status == StreamStatus.IDLE:
            self.status = StreamStatus.ACTIVE
            if self.started_at is None:
                self.started_at = now

        self.updated_at = now
        self.version += 1

    def complete(self) -> None:
        now = _now()
        self.status = StreamStatus.COMPLETED
        if self.total_bytes is not None and self.completed_bytes < self.total_bytes:
            self.completed_bytes = self.total_bytes
        self.completed_at = now
        self.speed_bps = 0.0
        self.eta_seconds = 0.0
        self.updated_at = now
        self.version += 1

    def fail(self, error_message: str | None = None) -> None:
        self.status = StreamStatus.FAILED
        if error_message:
            self.metadata["error"] = error_message
        # A dead stream moves no bytes: it stops feeding its ancestors'
        # summed speed and ETA.
        self.speed_bps = 0.0
        self.eta_seconds = None
        self.updated_at = _now()
        self.version += 1

    def cancel(self) -> None:
        """An operator stop: the stream ends without failing (no error) and
        moves no more bytes."""
        self.status = StreamStatus.CANCELLED
        self.metadata.pop("error", None)
        self.speed_bps = 0.0
        self.eta_seconds = None
        self.updated_at = _now()
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "parent_id": self.parent_id,
            "label": self.label,
            "total_bytes": self.total_bytes,
            "completed_bytes": self.completed_bytes,
            "percent": round(self.percent, 2),
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds,
            "status": self.status.value,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "metadata": dict(self.metadata),
            "children": list(self.children),
            "version": self.version,
            "retired_streams": self.retired_streams,
            "retired_completed_bytes": self.retired_completed_bytes,
            "retired_total_bytes": self.retired_total_bytes,
        }


@dataclass
class ProgressSnapshot:
    sequence_id: int
    timestamp: float
    streams: list[dict[str, Any]]

    @property
    def stream_count(self) -> int:
        return len(self.streams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "timestamp": self.timestamp,
            "stream_count": self.stream_count,
            "streams": self.streams,
        }

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class HierarchicalProgressTracker:
    """Thread-safe hierarchical multi-stream progress tracker.

    Memory is bounded by live streams, not by events: ``_modified_at`` keeps
    the last modifying sequence per stream, at most ``max_finished_streams``
    finished leaves are retained (older ones are evicted, their bytes folded
    into the parent), and the removal log is capped the same way.
    """

    def __init__(self, max_finished_streams: int = 100) -> None:
        self._lock = threading.RLock()
        self._streams: dict[str, StreamNode] = {}
        self._roots: list[str] = []
        self._sequence_id: int = 0
        self._max_finished = max(0, int(max_finished_streams))
        # stream id -> sequence of its latest modification (delta feed)
        self._modified_at: dict[str, int] = {}
        # finished stream ids, oldest first (dict keeps insertion order)
        self._finished: dict[str, None] = {}
        # evicted stream id -> sequence of its removal (bounded log)
        self._removed_at: dict[str, int] = {}
        # newest removal sequence that fell out of the log: a delta from
        # before it cannot list every removal and must resync
        self._removal_floor: int = 0

    def create_stream(
        self,
        stream_id: str,
        parent_id: str | None = None,
        label: str = "",
        total_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StreamNode:
        parent_id = parent_id or None
        with self._lock:
            if parent_id is not None and self._closes_cycle(stream_id, parent_id):
                msg = f"stream {stream_id!r} cannot be placed under {parent_id!r}: that closes a parent cycle"
                raise ValueError(msg)
            node = self._streams.get(stream_id)
            if node is not None:
                self._bump_sequence(stream_id)
                if parent_id is not None and parent_id != node.parent_id:
                    # Proper re-parent: the old ancestors lose this subtree.
                    self._touch_ancestors(stream_id)
                    self._detach(node)
                    node.parent_id = parent_id
                    self._attach(node)
                if label:
                    node.label = label
                if total_bytes is not None:
                    node.total_bytes = total_bytes
                if metadata:
                    node.metadata.update(metadata)
                self._touch_ancestors(stream_id)
                return node

            node = StreamNode(
                stream_id=stream_id,
                parent_id=parent_id,
                label=label or stream_id,
                total_bytes=total_bytes,
                metadata=dict(metadata or {}),
            )
            self._streams[stream_id] = node
            self._removed_at.pop(stream_id, None)
            self._attach(node)
            # Streams created earlier under this (then missing) id join it.
            for other in self._streams.values():
                if other.parent_id == stream_id and other.stream_id not in node.children:
                    node.children.append(other.stream_id)
            self._bump_sequence(stream_id)
            self._touch_ancestors(stream_id)
            return node

    def get_stream(self, stream_id: str) -> StreamNode | None:
        with self._lock:
            return self._streams.get(stream_id)

    def start_stream(self, stream_id: str) -> None:
        with self._lock:
            node = self._streams.get(stream_id)
            if node:
                node.start()
                self._finished.pop(stream_id, None)
                self._bump_sequence(stream_id)
                self._touch_ancestors(stream_id)

    def update_stream(
        self,
        stream_id: str,
        completed_bytes: int | None = None,
        delta_bytes: int | None = None,
        speed_bps: float | None = None,
        total_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            node = self._streams.get(stream_id)
            if node:
                node.update(
                    completed_bytes=completed_bytes,
                    delta_bytes=delta_bytes,
                    speed_bps=speed_bps,
                    total_bytes=total_bytes,
                    metadata=metadata,
                )
                self._bump_sequence(stream_id)
                self._touch_ancestors(stream_id)

    def complete_stream(self, stream_id: str) -> None:
        with self._lock:
            node = self._streams.get(stream_id)
            if node:
                node.complete()
                self._bump_sequence(stream_id)
                self._touch_ancestors(stream_id)
                self._mark_finished(stream_id)

    def fail_stream(self, stream_id: str, error_message: str | None = None) -> None:
        with self._lock:
            node = self._streams.get(stream_id)
            if node:
                node.fail(error_message)
                self._bump_sequence(stream_id)
                # The ancestors' composite status just turned FAILED: the
                # delta feed must carry them too.
                self._touch_ancestors(stream_id)
                self._mark_finished(stream_id)

    def cancel_stream(self, stream_id: str) -> None:
        """End a stream the operator stopped: CANCELLED, not FAILED, so no
        ancestor's composite turns FAILED; it is finished like the others."""
        with self._lock:
            node = self._streams.get(stream_id)
            if node:
                node.cancel()
                self._bump_sequence(stream_id)
                self._touch_ancestors(stream_id)
                self._mark_finished(stream_id)

    def report_transfer(
        self,
        stream_id: str,
        parent_id: str | None,
        completed_bytes: int,
        total_bytes: int | None = None,
        speed_bps: float | None = None,
        label: str = "",
    ) -> None:
        """Record one transfer tick: the stream is created under ``parent_id``
        when new, (re)activated when idle or finished (a retry), then moved to
        ``completed_bytes``."""
        with self._lock:
            node = self._streams.get(stream_id)
            if node is None or (parent_id is not None and node.parent_id != parent_id):
                self.create_stream(stream_id, parent_id=parent_id, label=label, total_bytes=total_bytes)
                node = self._streams[stream_id]
            if node.status != StreamStatus.ACTIVE:
                self.start_stream(stream_id)
            self.update_stream(
                stream_id, completed_bytes=completed_bytes,
                speed_bps=speed_bps, total_bytes=total_bytes)

    def _bump_sequence(self, stream_id: str) -> None:
        self._sequence_id += 1
        self._record_modified(stream_id)

    def _record_modified(self, stream_id: str) -> None:
        self._modified_at[stream_id] = self._sequence_id

    def _closes_cycle(self, stream_id: str, parent_id: str) -> bool:
        """True when hanging ``stream_id`` under ``parent_id`` loops the parent chain."""
        seen: set[str] = set()
        cursor: str | None = parent_id
        while cursor is not None and cursor not in seen:
            if cursor == stream_id:
                return True
            seen.add(cursor)
            parent = self._streams.get(cursor)
            cursor = parent.parent_id if parent is not None else None
        return False

    def _attach(self, node: StreamNode) -> None:
        if node.parent_id is None:
            if node.stream_id not in self._roots:
                self._roots.append(node.stream_id)
            return
        parent = self._streams.get(node.parent_id)
        if parent is not None and node.stream_id not in parent.children:
            parent.children.append(node.stream_id)

    def _detach(self, node: StreamNode) -> None:
        if node.parent_id is None:
            if node.stream_id in self._roots:
                self._roots.remove(node.stream_id)
            return
        parent = self._streams.get(node.parent_id)
        if parent is not None and node.stream_id in parent.children:
            parent.children.remove(node.stream_id)

    def _touch_ancestors(self, stream_id: str) -> None:
        """Mark every ancestor modified at the current sequence (visited-set bounded)."""
        node = self._streams.get(stream_id)
        seen = {stream_id}
        cursor = node.parent_id if node is not None else None
        now = _now()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            parent = self._streams.get(cursor)
            if parent is None:
                return
            parent.updated_at = now
            parent.version += 1
            self._record_modified(cursor)
            cursor = parent.parent_id

    def _mark_finished(self, stream_id: str) -> None:
        self._finished.pop(stream_id, None)
        self._finished[stream_id] = None
        while len(self._finished) > self._max_finished:
            victim_id = next(iter(self._finished))
            del self._finished[victim_id]
            victim = self._streams.get(victim_id)
            if victim is not None and not victim.children and victim.status in _FINISHED_STATUSES:
                self._retire(victim)

    def _retire(self, victim: StreamNode) -> None:
        """Evict a finished leaf; its bytes fold into its parent's rollup."""
        self._sequence_id += 1
        self._touch_ancestors(victim.stream_id)
        parent = self._streams.get(victim.parent_id) if victim.parent_id is not None else None
        if parent is not None:
            parent.retired_streams += 1 + victim.retired_streams
            parent.retired_completed_bytes += victim.completed_bytes + victim.retired_completed_bytes
            parent.retired_total_bytes += victim.expected_bytes + victim.retired_total_bytes
        self._detach(victim)
        del self._streams[victim.stream_id]
        self._modified_at.pop(victim.stream_id, None)
        self._removed_at[victim.stream_id] = self._sequence_id
        while len(self._removed_at) > max(1, self._max_finished):
            dropped_id = next(iter(self._removed_at))
            self._removal_floor = self._removed_at.pop(dropped_id)
        if (parent is not None and not parent.children
                and parent.status in _FINISHED_STATUSES and parent.stream_id not in self._finished):
            # A finished parent whose last child just left is a leaf now.
            self._finished[parent.stream_id] = None

    def get_rollup(self, stream_id: str) -> dict[str, Any]:
        with self._lock:
            node = self._streams.get(stream_id)
            if not node:
                return {}

            if not node.children and not node.retired_streams:
                return {
                    "stream_id": node.stream_id,
                    "label": node.label,
                    "completed_bytes": node.completed_bytes,
                    "total_bytes": node.total_bytes or 0,
                    "percent": round(node.percent, 2),
                    "speed_bps": node.speed_bps,
                    "eta_seconds": node.eta_seconds,
                    "status": node.status.value,
                    "active_children_count": 1 if node.status == StreamStatus.ACTIVE else 0,
                    "total_children_count": 0,
                    "retired_children_count": 0,
                }

            # Aggregate every descendant: iterative, each stream once.
            total_comp = node.retired_completed_bytes
            total_expected = node.retired_total_bytes
            retired = node.retired_streams
            total_speed = 0.0
            child_statuses: list[StreamStatus] = []
            active_count = 0
            all_descendants = 0
            seen = {stream_id}
            pending = list(node.children)
            while pending:
                cid = pending.pop()
                cnode = self._streams.get(cid)
                if cid in seen or cnode is None:
                    continue
                seen.add(cid)
                all_descendants += 1
                if cnode.status == StreamStatus.ACTIVE:
                    active_count += 1
                child_statuses.append(cnode.status)
                total_comp += cnode.retired_completed_bytes
                total_expected += cnode.retired_total_bytes
                retired += cnode.retired_streams
                if not cnode.children:
                    total_comp += cnode.completed_bytes
                    total_expected += cnode.expected_bytes
                    total_speed += cnode.speed_bps
                else:
                    pending.extend(cnode.children)

            # Determine composite status
            if node.status == StreamStatus.FAILED or any(s == StreamStatus.FAILED for s in child_statuses):
                composite_status = StreamStatus.FAILED
            elif node.status == StreamStatus.CANCELLED:
                composite_status = StreamStatus.CANCELLED
            elif all(s == StreamStatus.COMPLETED for s in child_statuses) and child_statuses:
                composite_status = StreamStatus.COMPLETED
            elif any(s == StreamStatus.ACTIVE for s in child_statuses) or node.status == StreamStatus.ACTIVE:
                composite_status = StreamStatus.ACTIVE
            elif any(s == StreamStatus.PAUSED for s in child_statuses):
                composite_status = StreamStatus.PAUSED
            elif any(s == StreamStatus.QUEUED for s in child_statuses):
                composite_status = StreamStatus.QUEUED
            else:
                composite_status = node.status

            # If node had explicitly declared total_bytes, prefer that if larger
            if node.total_bytes is not None and node.total_bytes > total_expected:
                total_expected = node.total_bytes

            pct = (total_comp / total_expected * 100.0) if total_expected > 0 else 0.0
            pct = max(0.0, min(100.0, pct))

            eta = None
            if total_speed > 0 and total_expected > total_comp:
                eta = _finite_or_none((total_expected - total_comp) / total_speed)

            return {
                "stream_id": node.stream_id,
                "label": node.label,
                "completed_bytes": total_comp,
                "total_bytes": total_expected,
                "percent": round(pct, 2),
                "speed_bps": total_speed,
                "eta_seconds": round(eta, 1) if eta is not None else None,
                "status": composite_status.value,
                "active_children_count": active_count,
                "total_children_count": all_descendants,
                "retired_children_count": retired,
            }

    def take_snapshot(self) -> ProgressSnapshot:
        with self._lock:
            streams_data = [node.to_dict() for node in self._streams.values()]
            return ProgressSnapshot(
                sequence_id=self._sequence_id,
                timestamp=_now(),
                streams=streams_data,
            )

    def get_delta_since(self, seq_id: int) -> dict[str, Any]:
        with self._lock:
            updated = [
                self._streams[sid].to_dict()
                for sid, seq in self._modified_at.items()
                if seq > seq_id and sid in self._streams
            ]
            removed = [sid for sid, seq in self._removed_at.items() if seq > seq_id]
            return {
                "since_sequence_id": seq_id,
                "current_sequence_id": self._sequence_id,
                "updated_streams": updated,
                "removed_stream_ids": removed,
                "resync_required": seq_id < self._removal_floor,
            }


def _clean_label(label: str) -> str:
    """A label is data, not terminal control: escapes and control chars go."""
    return _CONTROL_CHAR_RE.sub("?", _ANSI_ESCAPE_RE.sub("", label))


def _char_columns(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_width(text: str) -> int:
    """Terminal columns ``text`` occupies: ANSI escapes take none."""
    return sum(_char_columns(ch) for ch in _ANSI_ESCAPE_RE.sub("", text))


def _fit_visible_width(text: str, width: int) -> str:
    """Cap ``text`` at ``width`` visible columns; escapes are kept intact
    (never cut mid-sequence) and a reset closes any style left open."""
    if visible_width(text) <= width:
        return text
    ellipsis = "..." if width >= len("...") else ""
    budget = max(0, width - len(ellipsis))
    kept: list[str] = []
    used = 0
    pos = 0
    styled = False
    while pos < len(text):
        escape = _ANSI_ESCAPE_RE.match(text, pos)
        if escape is not None:
            kept.append(escape.group(0))
            styled = True
            pos = escape.end()
            continue
        cols = _char_columns(text[pos])
        if used + cols > budget:
            break
        kept.append(text[pos])
        used += cols
        pos += 1
    if styled:
        kept.append(_ANSI_RESET)
    return "".join(kept) + ellipsis


class ProgressTelemetryRenderer:
    """Renders hierarchical stream telemetry into formatted terminal trees."""

    def __init__(
        self,
        tracker: HierarchicalProgressTracker,
        bar_width: int = 15,
        max_width: int = 100,
        use_ansi: bool = True,
        show_speed: bool = True,
        show_eta: bool = True,
        show_bytes: bool = True,
    ) -> None:
        self.tracker = tracker
        self.bar_width = bar_width
        self.max_width = max_width
        self.use_ansi = use_ansi
        self.show_speed = show_speed
        self.show_eta = show_eta
        self.show_bytes = show_bytes

    def format_bytes(self, num_bytes: float | None) -> str:
        if num_bytes is None:
            return "0 B"
        num = float(num_bytes)
        if not math.isfinite(num):
            return "-- B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(num) < 1024.0 or unit == "TB":
                return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
            num /= 1024.0
        return f"{num:.1f} PB"

    def format_speed(self, speed_bps: float) -> str:
        if not math.isfinite(speed_bps):
            return "-- B/s"
        return f"{self.format_bytes(int(speed_bps))}/s"

    def format_duration(self, seconds: float | None) -> str:
        if seconds is None or not math.isfinite(seconds):
            return "--:--"
        sec = round(seconds)
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def render_bar(self, percent: float, width: int) -> str:
        width = max(5, width)
        filled_len = round((percent / 100.0) * width)
        filled_len = max(0, min(width, filled_len))
        if filled_len == width:
            bar = "=" * width
        elif filled_len > 0:
            bar = "=" * (filled_len - 1) + ">" + " " * (width - filled_len)
        else:
            bar = " " * width
        return f"[{bar}] {percent:5.1f}%"

    def _render_line(self, node: StreamNode, lead: str) -> str:
        rollup = self.tracker.get_rollup(node.stream_id)
        comp = rollup.get("completed_bytes", node.completed_bytes)
        tot = rollup.get("total_bytes", node.total_bytes or 0)
        pct = rollup.get("percent", node.percent)
        spd = rollup.get("speed_bps", node.speed_bps)
        eta = rollup.get("eta_seconds", node.eta_seconds)

        bar = self.render_bar(pct, self.bar_width)

        metrics_parts = []
        if self.show_bytes:
            metrics_parts.append(f"{self.format_bytes(comp)} / {self.format_bytes(tot)}")
        if self.show_speed and spd > 0:
            metrics_parts.append(self.format_speed(spd))
        if self.show_eta and eta is not None:
            metrics_parts.append(f"ETA {self.format_duration(eta)}")

        metrics_str = f"({', '.join(metrics_parts)})" if metrics_parts else ""

        # Status styling. The line's numbers are its subtree's rollup, so the
        # tag is the rollup's composite status too (a site root is never
        # started itself; its files are).
        status = StreamStatus(rollup.get("status", node.status.value))
        status_tag = f"[{status.value.upper()}]"
        if self.use_ansi:
            if status == StreamStatus.COMPLETED:
                status_tag = f"\033[32m{status_tag}\033[0m"
            elif status == StreamStatus.ACTIVE:
                status_tag = f"\033[36m{status_tag}\033[0m"
            elif status == StreamStatus.FAILED:
                status_tag = f"\033[31m{status_tag}\033[0m"

        # rstrip only: the leading tree indentation is part of the picture.
        raw_line = f"{lead}{status_tag} {_clean_label(node.label)} {bar} {metrics_str}".rstrip()
        # Width constraint, in visible columns (escapes cost none).
        return _fit_visible_width(raw_line, self.max_width)

    def render_tree(self, root_id: str) -> str:
        with self.tracker._lock:  # one consistent picture of the tree
            if not self.tracker.get_stream(root_id):
                return f"Stream {_clean_label(repr(root_id))} not found."

            lines: list[str] = []
            seen: set[str] = set()
            # (stream id, indentation for its line, connector, indentation for its children)
            pending: list[tuple[str, str, str, str]] = [(root_id, "", "", "")]
            while pending:
                nid, prefix, connector, child_prefix = pending.pop()
                node = self.tracker.get_stream(nid)
                if nid in seen or node is None:
                    continue
                seen.add(nid)
                lines.append(self._render_line(node, prefix + connector))
                child_ids = [cid for cid in node.children if cid not in seen]
                for idx in range(len(child_ids) - 1, -1, -1):
                    last = idx == len(child_ids) - 1
                    pending.append((
                        child_ids[idx], child_prefix,
                        "└── " if last else "├── ",
                        child_prefix + ("    " if last else "│   "),
                    ))
            return "\n".join(lines)


def create_progress_tracker(max_finished_streams: int = 100) -> HierarchicalProgressTracker:
    return HierarchicalProgressTracker(max_finished_streams=max_finished_streams)


def render_progress_hierarchy(
    tracker: HierarchicalProgressTracker,
    root_id: str,
    bar_width: int = 15,
    max_width: int = 100,
    use_ansi: bool = False,
) -> str:
    renderer = ProgressTelemetryRenderer(
        tracker,
        bar_width=bar_width,
        max_width=max_width,
        use_ansi=use_ansi,
    )
    return renderer.render_tree(root_id)
