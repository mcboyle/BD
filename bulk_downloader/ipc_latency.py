"""Row 989: Inter-Seat IPC Latency Matrix Monitor.

Provides real-time peer monitoring across fleet seats, measuring IPC round-trip
latency, percentile distributions, network jitter, and pair degradation.
Rescoped to latency only per ORDERS-0745 (clock skew dropped: remote timestamps
are not available over transport and fabricated zeros are prohibited).
"""
from __future__ import annotations

import collections
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SeatNode:
    """Represents an active seat node participating in inter-seat communication."""

    seat_id: str
    role: str = "worker"
    host: str = "localhost"
    pid: int = 0
    registered_at: float = field(default_factory=time.time)
    last_heartbeat_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IPCPingSample:
    """A single round-trip IPC ping observation measured on the client clock."""

    sender_seat: str
    receiver_seat: str
    t0_ns: int
    t3_ns: int
    rtt_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.rtt_ms <= 0.0 and self.t3_ns >= self.t0_ns:
            self.rtt_ms = max(0.0, (self.t3_ns - self.t0_ns) / 1_000_000.0)

    @property
    def total_duration_ms(self) -> float:
        return self.rtt_ms

    @property
    def network_rtt_ms(self) -> float:
        return self.rtt_ms

    @property
    def clock_offset_ms(self) -> float | None:
        """Clock offset is unmeasured; returns None (never a fabricated 0.0)."""
        return None

    @property
    def server_processing_ms(self) -> float | None:
        """Server processing time is unmeasured; returns None (never a fabricated 0.0)."""
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serializes IPC sample for response header (X-IPC-Sample).

        Omits clock_offset_ms and server_processing_ms per ORDERS-0745 because
        remote timestamps are not available over the transport.
        """
        return {
            "sender_seat": self.sender_seat,
            "receiver_seat": self.receiver_seat,
            "t0_ns": self.t0_ns,
            "t3_ns": self.t3_ns,
            "rtt_ms": round(self.rtt_ms, 4),
            "total_duration_ms": round(self.rtt_ms, 4),
            "timestamp": self.timestamp,
        }


@dataclass
class LatencySummary:
    """Statistical summary for an inter-seat communication link."""

    sender_seat: str
    receiver_seat: str
    sample_count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    jitter_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute_percentile(sorted_values: list[float], p: float) -> float:
    """Compute percentile using linear interpolation between nearest ranks."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (p / 100.0) * (len(sorted_values) - 1)
    k = math.floor(rank)
    d = rank - k
    if k + 1 < len(sorted_values):
        return sorted_values[k] + d * (sorted_values[k + 1] - sorted_values[k])
    return sorted_values[k]


# MAX_TRACKED_PAIRS = 1024: only product writer is the gateway star,
# one pair per routed service (RULING-0115 / ORDERS-0745).
MAX_TRACKED_PAIRS = 1024


class IPCLatencyMatrix:
    """Maintains NxN inter-seat pairwise latency history and aggregates statistics."""

    def __init__(self, max_history_per_pair: int = 1000, max_pairs: int = MAX_TRACKED_PAIRS) -> None:
        self._max_history = max_history_per_pair
        self._max_pairs = max_pairs
        self._pairs: collections.OrderedDict[tuple[str, str], list[float]] = collections.OrderedDict()

    def record_sample(self, sample: IPCPingSample) -> None:
        self.record_rtt(sample.sender_seat, sample.receiver_seat, sample.rtt_ms)

    def record_rtt(self, sender_seat: str, receiver_seat: str, rtt_ms: float) -> None:
        key = (sender_seat, receiver_seat)
        if key in self._pairs:
            self._pairs.move_to_end(key)
        else:
            if len(self._pairs) >= self._max_pairs:
                self._pairs.popitem(last=False)
            self._pairs[key] = []
        buf = self._pairs[key]
        buf.append(max(0.0, float(rtt_ms)))
        if len(buf) > self._max_history:
            self._pairs[key] = buf[-self._max_history:]

    def get_pair_summary(self, sender_seat: str, receiver_seat: str) -> LatencySummary | None:
        key = (sender_seat, receiver_seat)
        samples = self._pairs.get(key)
        if not samples:
            return None

        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        min_v = sorted_samples[0]
        max_v = sorted_samples[-1]
        mean_v = sum(sorted_samples) / n
        p50_v = _compute_percentile(sorted_samples, 50.0)
        p95_v = _compute_percentile(sorted_samples, 95.0)
        p99_v = _compute_percentile(sorted_samples, 99.0)

        # RFC 3550 style jitter: average absolute differences between consecutive samples
        if n > 1:
            diffs = [abs(samples[i] - samples[i - 1]) for i in range(1, n)]
            jitter = sum(diffs) / len(diffs)
        else:
            jitter = 0.0

        return LatencySummary(
            sender_seat=sender_seat,
            receiver_seat=receiver_seat,
            sample_count=n,
            min_ms=round(min_v, 4),
            max_ms=round(max_v, 4),
            mean_ms=round(mean_v, 4),
            p50_ms=round(p50_v, 4),
            p95_ms=round(p95_v, 4),
            p99_ms=round(p99_v, 4),
            jitter_ms=round(jitter, 4),
        )

    def get_matrix(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Returns 2D mapping: sender -> receiver -> LatencySummary dict."""
        res: dict[str, dict[str, dict[str, Any]]] = {}
        for (src, dst) in self._pairs:
            summary = self.get_pair_summary(src, dst)
            if summary:
                if src not in res:
                    res[src] = {}
                res[src][dst] = summary.to_dict()
        return res

    def get_degraded_links(self, threshold_ms: float = 100.0) -> list[LatencySummary]:
        """Returns all pairs whose average latency exceeds the threshold."""
        degraded: list[LatencySummary] = []
        for (src, dst) in self._pairs:
            summary = self.get_pair_summary(src, dst)
            if summary and summary.mean_ms >= threshold_ms:
                degraded.append(summary)
        return sorted(degraded, key=lambda s: s.mean_ms, reverse=True)

    def get_highest_latency_pairs(self, top_n: int = 5) -> list[LatencySummary]:
        """Returns top-N highest latency links by average latency."""
        all_summaries: list[LatencySummary] = []
        for (src, dst) in self._pairs:
            summary = self.get_pair_summary(src, dst)
            if summary:
                all_summaries.append(summary)
        return sorted(all_summaries, key=lambda s: s.mean_ms, reverse=True)[:top_n]

    def reset(self) -> None:
        self._pairs.clear()


class ClockSkewMonitor:
    """Explicit NOT-MEASURED state for clock skew monitoring (ORDERS-0745).

    Clock skew cannot be measured over transport without remote timestamps;
    reporting 0.0 would be fabricated data.
    """

    def record_skew_sample(self, *args: Any, **kwargs: Any) -> None:
        """No-op: clock skew samples are not recorded."""
        return

    def detect_anomalies(self) -> list[dict[str, Any]]:
        """No skew anomalies: skew is not measured."""
        return []

    def get_max_cluster_clock_divergence(self) -> float | None:
        """Returns None (unknown / not measured; never a fabricated 0.0)."""
        return None

    def reset(self) -> None:
        pass


class InterSeatIPCMonitor:
    """Coordinator managing seat nodes and inter-seat latency matrix."""

    def __init__(self) -> None:
        self._seats: dict[str, SeatNode] = {}
        self.latency_matrix = IPCLatencyMatrix()
        self.clock_skew_monitor = ClockSkewMonitor()

    def register_seat(
        self,
        seat_id: str,
        role: str = "worker",
        host: str = "localhost",
        pid: int = 0,
    ) -> SeatNode:
        if not seat_id or not isinstance(seat_id, str):
            raise ValueError("seat_id must be a non-empty string")
        node = SeatNode(seat_id=seat_id, role=role, host=host, pid=pid)
        self._seats[seat_id] = node
        return node

    def has_seat(self, seat_id: str) -> bool:
        return seat_id in self._seats

    def get_seats(self) -> dict[str, SeatNode]:
        return dict(self._seats)

    def record_ping(
        self,
        sender_seat: str,
        receiver_seat: str,
        t0_ns: int,
        t3_ns: int,
        *args: Any,
        **kwargs: Any,
    ) -> IPCPingSample:
        """Records an IPC ping observation and updates the pairwise latency matrix."""
        sample = IPCPingSample(
            sender_seat=sender_seat,
            receiver_seat=receiver_seat,
            t0_ns=t0_ns,
            t3_ns=t3_ns,
        )
        self.latency_matrix.record_sample(sample)
        return sample

    def export_telemetry(self) -> dict[str, Any]:
        """Aggregate full fleet IPC latency telemetry report."""
        return {
            "seats": {sid: node.to_dict() for sid, node in self._seats.items()},
            "latency_matrix": self.latency_matrix.get_matrix(),
            "highest_latency_pairs": [
                s.to_dict() for s in self.latency_matrix.get_highest_latency_pairs()
            ],
            "degraded_links": [
                s.to_dict() for s in self.latency_matrix.get_degraded_links()
            ],
            "clock_skew": {
                "max_cluster_divergence_ms": None,
                "status": "NOT_MEASURED",
            },
            "timestamp": time.time(),
        }

    def export_telemetry_json(self) -> str:
        return json.dumps(self.export_telemetry(), indent=2)

    def reset(self) -> None:
        self._seats.clear()
        self.latency_matrix.reset()
        self.clock_skew_monitor.reset()


_DEFAULT_MONITOR = InterSeatIPCMonitor()


def get_default_ipc_monitor() -> InterSeatIPCMonitor:
    """Returns the default process-wide IPC monitor."""
    return _DEFAULT_MONITOR


def record_ipc_ping(
    sender_seat: str,
    receiver_seat: str,
    t0_ns: int,
    t3_ns: int,
    *args: Any,
    monitor: InterSeatIPCMonitor | None = None,
    **kwargs: Any,
) -> IPCPingSample:
    """Convenience helper to record an IPC ping across seats."""
    target_monitor = monitor or _DEFAULT_MONITOR
    return target_monitor.record_ping(
        sender_seat=sender_seat,
        receiver_seat=receiver_seat,
        t0_ns=t0_ns,
        t3_ns=t3_ns,
    )
