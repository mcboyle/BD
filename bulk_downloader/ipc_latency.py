"""Row 989: Inter-Seat IPC Latency Matrix & Clock Skew Monitor.

Provides real-time peer monitoring across fleet seats, measuring IPC round-trip
latency, percentile distributions, network jitter, and inter-node clock skew
via Cristian's 4-timestamp exchange algorithm.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any


def calculate_cristian_skew(
    t0_ns: int,
    t1_ns: int,
    t2_ns: int,
    t3_ns: int,
) -> tuple[float, float]:
    """Compute clock offset and network round-trip delay using Cristian's algorithm.

    Args:
        t0_ns: Client packet transmission timestamp (client clock, nanoseconds).
        t1_ns: Server packet arrival timestamp (server clock, nanoseconds).
        t2_ns: Server echo transmission timestamp (server clock, nanoseconds).
        t3_ns: Client echo arrival timestamp (client clock, nanoseconds).

    Returns:
        (offset_ms, network_rtt_ms)
        offset_ms: Clock offset of server relative to client. Positive means server ahead.
        network_rtt_ms: Round-trip transit delay excluding server processing time.
    """
    total_elapsed_ns = max(0, t3_ns - t0_ns)
    server_proc_ns = max(0, t2_ns - t1_ns)
    net_rtt_ns = max(0, total_elapsed_ns - server_proc_ns)

    # Offset = ((T1 - T0) + (T2 - T3)) / 2
    offset_ns = ((t1_ns - t0_ns) + (t2_ns - t3_ns)) / 2.0

    offset_ms = offset_ns / 1_000_000.0
    net_rtt_ms = net_rtt_ns / 1_000_000.0
    return offset_ms, net_rtt_ms


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
    """A single four-timestamp IPC ping-pong observation."""

    sender_seat: str
    receiver_seat: str
    t0_ns: int
    t1_ns: int
    t2_ns: int
    t3_ns: int
    timestamp: float = field(default_factory=time.time)

    @property
    def total_duration_ms(self) -> float:
        return max(0, self.t3_ns - self.t0_ns) / 1_000_000.0

    @property
    def server_processing_ms(self) -> float:
        return max(0, self.t2_ns - self.t1_ns) / 1_000_000.0

    @property
    def network_rtt_ms(self) -> float:
        return max(0.0, self.total_duration_ms - self.server_processing_ms)

    @property
    def clock_offset_ms(self) -> float:
        offset_ns = ((self.t1_ns - self.t0_ns) + (self.t2_ns - self.t3_ns)) / 2.0
        return offset_ns / 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_seat": self.sender_seat,
            "receiver_seat": self.receiver_seat,
            "t0_ns": self.t0_ns,
            "t1_ns": self.t1_ns,
            "t2_ns": self.t2_ns,
            "t3_ns": self.t3_ns,
            "total_duration_ms": self.total_duration_ms,
            "server_processing_ms": self.server_processing_ms,
            "network_rtt_ms": self.network_rtt_ms,
            "clock_offset_ms": self.clock_offset_ms,
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


class IPCLatencyMatrix:
    """Maintains NxN inter-seat pairwise latency history and aggregates statistics."""

    def __init__(self, max_history_per_pair: int = 1000) -> None:
        self._max_history = max_history_per_pair
        # Keyed by (sender_seat, receiver_seat) -> list of rtt_ms
        self._pairs: dict[tuple[str, str], list[float]] = {}

    def record_sample(self, sample: IPCPingSample) -> None:
        self.record_rtt(sample.sender_seat, sample.receiver_seat, sample.network_rtt_ms)

    def record_rtt(self, sender_seat: str, receiver_seat: str, rtt_ms: float) -> None:
        key = (sender_seat, receiver_seat)
        if key not in self._pairs:
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


@dataclass
class ClockSkewSample:
    """A clock skew observation between two seats."""

    seat_a: str
    seat_b: str
    offset_ms: float
    rtt_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClockSkewAnomaly:
    """Detected clock skew anomaly exceeding severity threshold."""

    seat_a: str
    seat_b: str
    skew_ms: float
    severity: str  # "WARNING" or "CRITICAL"
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClockSkewMonitor:
    """Monitors inter-seat clock skew drift and flags divergent node clocks."""

    def __init__(
        self,
        warn_skew_threshold_ms: float = 25.0,
        crit_skew_threshold_ms: float = 75.0,
        max_samples_per_pair: int = 500,
    ) -> None:
        self.warn_skew_threshold_ms = warn_skew_threshold_ms
        self.crit_skew_threshold_ms = crit_skew_threshold_ms
        self._max_samples = max_samples_per_pair
        # Keyed by (seat_a, seat_b) -> list of ClockSkewSample
        self._skew_history: dict[tuple[str, str], list[ClockSkewSample]] = {}

    def record_skew_sample(
        self,
        seat_a: str,
        seat_b: str,
        offset_ms: float,
        rtt_ms: float,
    ) -> ClockSkewSample:
        sample = ClockSkewSample(
            seat_a=seat_a,
            seat_b=seat_b,
            offset_ms=float(offset_ms),
            rtt_ms=float(rtt_ms),
        )
        key = (seat_a, seat_b)
        if key not in self._skew_history:
            self._skew_history[key] = []
        buf = self._skew_history[key]
        buf.append(sample)
        if len(buf) > self._max_samples:
            self._skew_history[key] = buf[-self._max_samples:]
        return sample

    def get_skew_samples(self, seat_a: str, seat_b: str) -> list[ClockSkewSample]:
        return list(self._skew_history.get((seat_a, seat_b), []))

    def detect_anomalies(self) -> list[ClockSkewAnomaly]:
        """Detect clock skew anomalies based on the latest sample for each pair."""
        anomalies: list[ClockSkewAnomaly] = []
        for (sa, sb), samples in self._skew_history.items():
            if not samples:
                continue
            latest = samples[-1]
            abs_skew = abs(latest.offset_ms)
            if abs_skew >= self.crit_skew_threshold_ms:
                anomalies.append(
                    ClockSkewAnomaly(
                        seat_a=sa,
                        seat_b=sb,
                        skew_ms=latest.offset_ms,
                        severity="CRITICAL",
                    )
                )
            elif abs_skew >= self.warn_skew_threshold_ms:
                anomalies.append(
                    ClockSkewAnomaly(
                        seat_a=sa,
                        seat_b=sb,
                        skew_ms=latest.offset_ms,
                        severity="WARNING",
                    )
                )
        return anomalies

    def get_max_cluster_clock_divergence(self) -> float:
        """Compute maximum clock skew spread observed across all recorded pairs."""
        if not self._skew_history:
            return 0.0

        offsets: list[float] = []
        for samples in self._skew_history.values():
            if samples:
                offsets.append(samples[-1].offset_ms)

        if not offsets:
            return 0.0
        return max(offsets) - min(offsets)

    def reset(self) -> None:
        self._skew_history.clear()


class InterSeatIPCMonitor:
    """Unified coordinator managing seat nodes, latency matrix, and clock skew."""

    def __init__(
        self,
        warn_skew_threshold_ms: float = 25.0,
        crit_skew_threshold_ms: float = 75.0,
    ) -> None:
        self._seats: dict[str, SeatNode] = {}
        self.latency_matrix = IPCLatencyMatrix()
        self.clock_skew_monitor = ClockSkewMonitor(
            warn_skew_threshold_ms=warn_skew_threshold_ms,
            crit_skew_threshold_ms=crit_skew_threshold_ms,
        )

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
        t1_ns: int,
        t2_ns: int,
        t3_ns: int,
    ) -> IPCPingSample:
        sample = IPCPingSample(
            sender_seat=sender_seat,
            receiver_seat=receiver_seat,
            t0_ns=t0_ns,
            t1_ns=t1_ns,
            t2_ns=t2_ns,
            t3_ns=t3_ns,
        )
        self.latency_matrix.record_sample(sample)
        self.clock_skew_monitor.record_skew_sample(
            sender_seat,
            receiver_seat,
            sample.clock_offset_ms,
            sample.network_rtt_ms,
        )
        return sample

    def export_telemetry(self) -> dict[str, Any]:
        """Aggregate full fleet IPC telemetry report."""
        return {
            "seats": {sid: node.to_dict() for sid, node in self._seats.items()},
            "latency_matrix": self.latency_matrix.get_matrix(),
            "highest_latency_pairs": [
                s.to_dict() for s in self.latency_matrix.get_highest_latency_pairs()
            ],
            "clock_skew": {
                "max_cluster_divergence_ms": self.clock_skew_monitor.get_max_cluster_clock_divergence(),
            },
            "anomalies": [a.to_dict() for a in self.clock_skew_monitor.detect_anomalies()],
            "timestamp": time.time(),
        }

    def export_telemetry_json(self) -> str:
        return json.dumps(self.export_telemetry(), indent=2)

    def reset(self) -> None:
        self._seats.clear()
        self.latency_matrix.reset()
        self.clock_skew_monitor.reset()


def record_ipc_ping(
    sender_seat: str,
    receiver_seat: str,
    t0_ns: int,
    t1_ns: int,
    t2_ns: int,
    t3_ns: int,
    monitor: InterSeatIPCMonitor | None = None,
) -> IPCPingSample:
    """Convenience helper to record an IPC ping across seats."""
    target_monitor = monitor or _DEFAULT_MONITOR
    return target_monitor.record_ping(
        sender_seat=sender_seat,
        receiver_seat=receiver_seat,
        t0_ns=t0_ns,
        t1_ns=t1_ns,
        t2_ns=t2_ns,
        t3_ns=t3_ns,
    )


_DEFAULT_MONITOR = InterSeatIPCMonitor()
