"""Phase 150 / Row 1055 -- Distributed Ingestion Throughput Capacity & Bandwidth Aggregator.

Provides cluster-wide ingestion bandwidth telemetry, sliding-window throughput rate
aggregation across multi-node federations, dynamic capacity headroom tracking, admission
control with capacity reservation (three-state fail-closed under O1224), and inactive
node eviction.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AdmissionState(str, Enum):
    """Three-state admission control outcome per Fleet Rule O1224."""

    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class AdmissionResult:
    """Detailed admission verdict with fail-closed guarantee (never fails open)."""

    state: AdmissionState
    is_admitted: bool
    reason: str = ""
    headroom_bps: float = 0.0


@dataclass
class NodeThroughputSample:
    """Telemetry sample representing bytes ingested over a duration on a cluster node."""

    node_id: str
    site_id: str
    bytes_ingested: int
    timestamp: float
    duration_seconds: float = 1.0


@dataclass
class IngestionCapacityProfile:
    """Cluster-wide and per-node ingestion capacity configuration."""

    cluster_capacity_bps: float = 100_000_000.0  # 100 MB/s default
    node_capacity_bps: float = 25_000_000.0     # 25 MB/s default
    window_seconds: float = 10.0
    node_ttl_seconds: float = 30.0
    headroom_target_ratio: float = 0.1         # 10% headroom target


class BandwidthAggregator:
    """Thread-safe sliding-window bandwidth aggregator for distributed node clusters."""

    def __init__(
        self,
        window_seconds: float = 10.0,
        cluster_capacity_bps: float = 100_000_000.0,
        node_ttl_seconds: float = 30.0,
    ) -> None:
        self.window_seconds = max(0.1, float(window_seconds))
        self.cluster_capacity_bps = float(cluster_capacity_bps)
        self.node_ttl_seconds = max(1.0, float(node_ttl_seconds))

        self._lock = threading.Lock()
        self._samples: dict[str, list[NodeThroughputSample]] = {}
        self._node_last_seen: dict[str, float] = {}
        self._node_total_bytes: dict[str, int] = {}
        self._reservations: dict[str, dict[str, Any]] = {}

    def record_throughput(
        self,
        node_id: str,
        site_id: str,
        bytes_ingested: int,
        duration_seconds: float = 1.0,
        now: float | None = None,
    ) -> None:
        """Record an ingestion sample for a specific node and site."""
        current_time = time.monotonic() if now is None else now
        sample = NodeThroughputSample(
            node_id=str(node_id),
            site_id=str(site_id),
            bytes_ingested=max(0, int(bytes_ingested)),
            timestamp=current_time,
            duration_seconds=max(0.001, float(duration_seconds)),
        )

        with self._lock:
            self._record_sample_locked(sample, current_time)

    def record_sample_at(
        self,
        node_id: str,
        site_id: str,
        bytes_ingested: int,
        timestamp: float,
        duration_seconds: float = 1.0,
    ) -> None:
        """Record a historical or explicitly timestamped sample for test validation."""
        sample = NodeThroughputSample(
            node_id=str(node_id),
            site_id=str(site_id),
            bytes_ingested=max(0, int(bytes_ingested)),
            timestamp=float(timestamp),
            duration_seconds=max(0.001, float(duration_seconds)),
        )
        with self._lock:
            self._record_sample_locked(sample, sample.timestamp)

    def _record_sample_locked(self, sample: NodeThroughputSample, current_time: float) -> None:
        node_id = sample.node_id
        if node_id not in self._samples:
            self._samples[node_id] = []
            self._node_total_bytes[node_id] = 0

        self._samples[node_id].append(sample)
        self._node_total_bytes[node_id] += sample.bytes_ingested
        self._node_last_seen[node_id] = max(self._node_last_seen.get(node_id, 0.0), current_time)
        self._prune_samples_locked(node_id, current_time)

    def _prune_samples_locked(self, node_id: str, current_time: float) -> None:
        cutoff = current_time - self.window_seconds
        samples = self._samples.get(node_id, [])
        self._samples[node_id] = [s for s in samples if s.timestamp >= cutoff]

    def _clean_expired_reservations_locked(self, current_time: float) -> None:
        expired = [
            lease_id
            for lease_id, res in self._reservations.items()
            if res["expires_at"] < current_time
        ]
        for lease_id in expired:
            del self._reservations[lease_id]

    def get_active_throughput_bps(self, node_id: str, now: float | None = None) -> float:
        """Calculate active throughput (Bps) for a node over the sliding window."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            self._prune_samples_locked(node_id, current_time)
            samples = self._samples.get(node_id, [])
            if not samples:
                return 0.0

            total_bytes = sum(s.bytes_ingested for s in samples)
            span = max(s.duration_seconds for s in samples)
            window_span = max(span, min(self.window_seconds, current_time - samples[0].timestamp if samples else 1.0))
            if window_span <= 0:
                return 0.0
            return float(total_bytes) / window_span

    def get_node_stats(self, node_id: str, now: float | None = None) -> dict[str, Any] | None:
        """Return comprehensive telemetry statistics for a specific node."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            if node_id not in self._samples and node_id not in self._node_last_seen:
                return None

            self._prune_samples_locked(node_id, current_time)
            samples = self._samples.get(node_id, [])
            total_bytes = sum(s.bytes_ingested for s in samples)
            span = max([s.duration_seconds for s in samples], default=1.0)
            window_span = max(span, min(self.window_seconds, current_time - samples[0].timestamp if samples else 1.0))
            current_bps = (float(total_bytes) / window_span) if window_span > 0 else 0.0

            return {
                "node_id": node_id,
                "current_bps": current_bps,
                "total_bytes": self._node_total_bytes.get(node_id, 0),
                "samples_in_window": len(samples),
                "last_seen": self._node_last_seen.get(node_id, 0.0),
            }

    def get_cluster_stats(self, now: float | None = None) -> dict[str, Any]:
        """Aggregate throughput and capacity metrics across all active nodes in the cluster."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            self._clean_expired_reservations_locked(current_time)
            active_node_ids = [
                nid
                for nid, last_seen in self._node_last_seen.items()
                if (current_time - last_seen) <= self.node_ttl_seconds
            ]

            total_bps = 0.0
            total_samples = 0
            node_breakdown: dict[str, dict[str, Any]] = {}

            for nid in active_node_ids:
                self._prune_samples_locked(nid, current_time)
                samples = self._samples.get(nid, [])
                total_samples += len(samples)
                s_bytes = sum(s.bytes_ingested for s in samples)
                s_span = max([s.duration_seconds for s in samples], default=1.0)
                w_span = max(s_span, min(self.window_seconds, current_time - samples[0].timestamp if samples else 1.0))
                n_bps = (float(s_bytes) / w_span) if w_span > 0 else 0.0
                total_bps += n_bps
                node_breakdown[nid] = {
                    "current_bps": n_bps,
                    "total_bytes": self._node_total_bytes.get(nid, 0),
                    "samples": len(samples),
                }

            reserved_bps = sum(res["bps"] for res in self._reservations.values())
            effective_used_bps = total_bps + reserved_bps
            headroom_bps = max(0.0, self.cluster_capacity_bps - effective_used_bps)
            utilization_ratio = min(1.0, effective_used_bps / self.cluster_capacity_bps) if self.cluster_capacity_bps > 0 else 1.0

            return {
                "active_nodes": len(active_node_ids),
                "nodes": node_breakdown,
                "total_throughput_bps": total_bps,
                "reserved_bps": reserved_bps,
                "effective_used_bps": effective_used_bps,
                "capacity_bps": self.cluster_capacity_bps,
                "headroom_bps": headroom_bps,
                "utilization_ratio": utilization_ratio,
                "total_samples": sum(len(s) for s in self._samples.values()),
            }

    def check_admission(self, requested_bps: float, now: float | None = None) -> AdmissionResult:
        """Check admission against cluster capacity with three-state fail-closed guarantee (O1224)."""
        if self.cluster_capacity_bps <= 0:
            return AdmissionResult(
                state=AdmissionState.UNVERIFIABLE,
                is_admitted=False,
                reason="Cluster capacity is non-positive or unmeasurable",
                headroom_bps=0.0,
            )

        if requested_bps < 0:
            return AdmissionResult(
                state=AdmissionState.UNVERIFIABLE,
                is_admitted=False,
                reason="Requested bandwidth cannot be negative",
                headroom_bps=0.0,
            )

        current_time = time.monotonic() if now is None else now
        with self._lock:
            self._clean_expired_reservations_locked(current_time)
            total_active_bps = 0.0
            for nid, last_seen in self._node_last_seen.items():
                if (current_time - last_seen) <= self.node_ttl_seconds:
                    samples = self._samples.get(nid, [])
                    if samples:
                        s_bytes = sum(s.bytes_ingested for s in samples)
                        s_span = max([s.duration_seconds for s in samples], default=1.0)
                        w_span = max(s_span, min(self.window_seconds, current_time - samples[0].timestamp))
                        if w_span > 0:
                            total_active_bps += float(s_bytes) / w_span

            reserved_bps = sum(res["bps"] for res in self._reservations.values())
            effective_used_bps = total_active_bps + reserved_bps
            headroom_bps = max(0.0, self.cluster_capacity_bps - effective_used_bps)

            if requested_bps == 0:
                return AdmissionResult(
                    state=AdmissionState.ADMITTED,
                    is_admitted=True,
                    headroom_bps=headroom_bps,
                )

            if (effective_used_bps + requested_bps) <= self.cluster_capacity_bps:
                return AdmissionResult(
                    state=AdmissionState.ADMITTED,
                    is_admitted=True,
                    headroom_bps=max(0.0, headroom_bps - requested_bps),
                )
            else:
                return AdmissionResult(
                    state=AdmissionState.REJECTED,
                    is_admitted=False,
                    reason=f"Insufficient cluster headroom: requested {requested_bps:.1f} Bps, available {headroom_bps:.1f} Bps",
                    headroom_bps=headroom_bps,
                )

    def can_admit(self, requested_bps: float, now: float | None = None) -> bool:
        """Check whether the cluster has sufficient bandwidth headroom to admit a new stream."""
        return self.check_admission(requested_bps, now=now).is_admitted

    def reserve_capacity(
        self,
        node_id: str,
        requested_bps: float,
        ttl_seconds: float = 10.0,
        now: float | None = None,
    ) -> str | None:
        """Reserve a slice of bandwidth capacity for a pending or high-priority ingestion stream."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            # Check admission under lock to ensure reservation consistency
            self._clean_expired_reservations_locked(current_time)
            if self.cluster_capacity_bps <= 0 or requested_bps <= 0:
                return None

            total_active_bps = 0.0
            for nid, last_seen in self._node_last_seen.items():
                if (current_time - last_seen) <= self.node_ttl_seconds:
                    samples = self._samples.get(nid, [])
                    if samples:
                        s_bytes = sum(s.bytes_ingested for s in samples)
                        s_span = max([s.duration_seconds for s in samples], default=1.0)
                        w_span = max(s_span, min(self.window_seconds, current_time - samples[0].timestamp))
                        if w_span > 0:
                            total_active_bps += float(s_bytes) / w_span

            reserved_bps = sum(res["bps"] for res in self._reservations.values())
            if (total_active_bps + reserved_bps + requested_bps) > self.cluster_capacity_bps:
                return None

            lease_id = str(uuid.uuid4())[:8]
            self._reservations[lease_id] = {
                "node_id": str(node_id),
                "bps": float(requested_bps),
                "expires_at": current_time + max(0.1, float(ttl_seconds)),
            }
            return lease_id

    def release_reservation(self, lease_id: str) -> bool:
        """Release a previously reserved capacity lease."""
        with self._lock:
            if lease_id in self._reservations:
                del self._reservations[lease_id]
                return True
            return False

    def prune_stale_nodes(self, now: float | None = None) -> list[str]:
        """Evict nodes that have exceeded the inactivity TTL."""
        current_time = time.monotonic() if now is None else now
        pruned = []
        with self._lock:
            for nid, last_seen in list(self._node_last_seen.items()):
                if (current_time - last_seen) > self.node_ttl_seconds:
                    pruned.append(nid)
                    self._samples.pop(nid, None)
                    self._node_last_seen.pop(nid, None)
                    self._node_total_bytes.pop(nid, None)
            self._clean_expired_reservations_locked(current_time)
        return pruned


_GLOBAL_AGGREGATOR: BandwidthAggregator | None = None
_GLOBAL_AGGREGATOR_LOCK = threading.Lock()


def create_bandwidth_aggregator(
    window_seconds: float = 10.0,
    cluster_capacity_bps: float = 100_000_000.0,
    node_ttl_seconds: float = 30.0,
) -> BandwidthAggregator:
    """Factory helper creating a standalone BandwidthAggregator."""
    return BandwidthAggregator(
        window_seconds=window_seconds,
        cluster_capacity_bps=cluster_capacity_bps,
        node_ttl_seconds=node_ttl_seconds,
    )


def get_bandwidth_aggregator() -> BandwidthAggregator:
    """Return the process-wide shared BandwidthAggregator instance."""
    global _GLOBAL_AGGREGATOR
    if _GLOBAL_AGGREGATOR is None:
        with _GLOBAL_AGGREGATOR_LOCK:
            if _GLOBAL_AGGREGATOR is None:
                _GLOBAL_AGGREGATOR = create_bandwidth_aggregator()
    return _GLOBAL_AGGREGATOR
