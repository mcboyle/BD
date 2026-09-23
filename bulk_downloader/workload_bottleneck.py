"""bulk_downloader.workload_bottleneck -- Automated Workload Bottleneck Anomaly Detector.

Monitors operational workload metrics (worker thread pool saturation, URL queue depth,
socket/download throughput rates, and processing latencies). Employs rolling statistical
baselines and EWMA anomaly scoring to detect and classify execution bottlenecks before
they manifest as unhandled system stalls or timeout cascades.

Thread-safe and decoupled from underlying transport layers.
"""
from __future__ import annotations

import collections
import dataclasses
import enum
import logging
import math
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class BottleneckType(enum.Enum):
    """Classification of workload bottleneck anomalies."""
    WORKER_EXHAUSTION = "worker_exhaustion"
    QUEUE_BACKLOG = "queue_backlog"
    THROUGHPUT_COLLAPSE = "throughput_collapse"
    LATENCY_SPIKE = "latency_spike"
    RESOURCE_CONTENTION = "resource_contention"


class AnomalySeverity(enum.Enum):
    """Severity classification for detected bottleneck events."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclasses.dataclass(frozen=True)
class AnomalyEvent:
    """Represents a discrete bottleneck anomaly detection event."""
    timestamp: float
    bottleneck_type: BottleneckType
    severity: AnomalySeverity
    metric_name: str
    observed_value: float
    baseline_value: float
    anomaly_score: float
    details: str
    site_id: Optional[str] = None
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "bottleneck_type": self.bottleneck_type.value,
            "severity": self.severity.value,
            "metric_name": self.metric_name,
            "observed_value": self.observed_value,
            "baseline_value": self.baseline_value,
            "anomaly_score": self.anomaly_score,
            "details": self.details,
            "site_id": self.site_id,
            "recommended_action": self.recommended_action,
        }


class _RollingMetricSeries:
    """Sliding-window statistical tracking series with baseline exclusion for outlier detection."""

    def __init__(self, window_size: int = 100, alpha: float = 0.2):
        self.window_size = max(10, window_size)
        self.alpha = alpha
        self.samples: collections.deque[float] = collections.deque(maxlen=self.window_size)
        self.ewma: Optional[float] = None
        self._count = 0

    def add(self, value: float) -> None:
        self.samples.append(value)
        self._count += 1
        if self.ewma is None:
            self.ewma = value
        else:
            self.ewma = (self.alpha * value) + ((1.0 - self.alpha) * self.ewma)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def mean(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)

    @property
    def stddev(self) -> float:
        n = len(self.samples)
        if n < 2:
            return 0.0
        m = self.mean
        variance = sum((x - m) ** 2 for x in self.samples) / (n - 1)
        return math.sqrt(max(0.0, variance))

    def baseline_stats(self, exclude_latest: bool = True) -> tuple[float, float]:
        """Compute mean and stddev, excluding the latest sample when evaluating outliers."""
        samples = list(self.samples)
        if exclude_latest and len(samples) > 1:
            samples = samples[:-1]
        n = len(samples)
        if n == 0:
            return 0.0, 0.0
        m = sum(samples) / n
        if n < 2:
            return m, 0.0
        variance = sum((x - m) ** 2 for x in samples) / (n - 1)
        return m, math.sqrt(max(0.0, variance))

    def z_score_of_latest(self) -> tuple[float, float, float]:
        """Compute (latest_value, baseline_mean, z_score) against prior baseline."""
        if not self.samples:
            return 0.0, 0.0, 0.0
        latest = self.samples[-1]
        if len(self.samples) < 2:
            return latest, latest, 0.0

        m, sd = self.baseline_stats(exclude_latest=True)
        if sd <= 1e-9:
            diff = abs(latest - m)
            denom = max(1e-3, abs(m) * 0.1) if m != 0.0 else 1.0
            z = diff / denom if diff > 1e-6 else 0.0
            return latest, m, z

        z = abs(latest - m) / sd
        return latest, m, z


# A per-file rate is only a throughput observation once the transfer is big or long
# enough for bandwidth, not request latency, to dominate it: a 2 KB caption that
# takes 1 s says nothing about the link. Samples under BOTH floors are ignored.
MIN_THROUGHPUT_SAMPLE_BYTES = 1 << 20
MIN_THROUGHPUT_SAMPLE_SECONDS = 2.0


class WorkloadBottleneckDetector:
    """Automated statistical bottleneck anomaly detector for download workloads."""

    def __init__(
        self,
        window_size: int = 100,
        z_score_threshold: float = 3.0,
        min_samples_for_baseline: int = 5,
        worker_saturation_threshold: float = 0.85,
        throughput_collapse_threshold: float = 0.2,
    ):
        self.window_size = window_size
        self.z_score_threshold = z_score_threshold
        self.min_samples_for_baseline = min_samples_for_baseline
        self.worker_saturation_threshold = worker_saturation_threshold
        self.throughput_collapse_threshold = throughput_collapse_threshold

        self._lock = threading.RLock()
        self._series: dict[tuple[str, Optional[str]], _RollingMetricSeries] = {}
        self._anomalies: collections.deque[AnomalyEvent] = collections.deque(maxlen=500)
        self._callbacks: list[Callable[[AnomalyEvent], None]] = []
        # Failures this detector isolated instead of raising; reported by get_metrics_summary().
        self.callback_failures = 0
        self.ingest_failures = 0

    def _get_series(self, metric_name: str, site_id: Optional[str]) -> _RollingMetricSeries:
        key = (metric_name, site_id)
        series = self._series.get(key)
        if series is None:
            series = _RollingMetricSeries(window_size=self.window_size)
            self._series[key] = series
        return series

    def record_metric(
        self,
        metric_name: str,
        value: float,
        site_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record an arbitrary numeric telemetry metric into rolling series."""
        with self._lock:
            series = self._get_series(metric_name, site_id)
            series.add(float(value))
            # Auto-check on ingest if baseline established
            if series.count > self.min_samples_for_baseline:
                self.detect_anomalies(metric_name=metric_name, site_id=site_id)

    def record_worker_state(
        self,
        active_workers: int,
        max_workers: int,
        queued_jobs: int,
        site_id: Optional[str] = None,
    ) -> Optional[AnomalyEvent]:
        """Record worker pool allocation and detect saturation/exhaustion anomalies."""
        with self._lock:
            max_w = max(1, max_workers)
            saturation = active_workers / float(max_w)
            series_sat = self._get_series("worker_saturation", site_id)
            series_sat.add(saturation)
            series_q = self._get_series("queued_jobs", site_id)
            series_q.add(float(queued_jobs))

            if saturation >= self.worker_saturation_threshold and queued_jobs > 0:
                severity = (
                    AnomalySeverity.CRITICAL
                    if (saturation >= 1.0 and queued_jobs >= 20)
                    else AnomalySeverity.WARNING
                )
                event = AnomalyEvent(
                    timestamp=time.time(),
                    bottleneck_type=BottleneckType.WORKER_EXHAUSTION,
                    severity=severity,
                    metric_name="worker_saturation",
                    observed_value=saturation,
                    baseline_value=self.worker_saturation_threshold,
                    anomaly_score=saturation / self.worker_saturation_threshold,
                    details=(
                        f"Worker saturation at {saturation * 100:.1f}% ({active_workers}/{max_w}) "
                        f"with {queued_jobs} queued jobs on site {site_id or 'all'}"
                    ),
                    site_id=site_id,
                    recommended_action=(
                        "Scale worker concurrency or drain backlog before admitting new tasks."
                    ),
                )
                self._emit_anomaly(event)
                return event
            return None

    def record_transfer_sample(
        self,
        bytes_transferred: int,
        duration_seconds: float,
        site_id: Optional[str] = None,
    ) -> Optional[AnomalyEvent]:
        """Record download transfer sample and detect throughput collapse.

        Samples below both MIN_THROUGHPUT_SAMPLE_BYTES and MIN_THROUGHPUT_SAMPLE_SECONDS
        are latency-dominated: they are neither judged nor added to the baseline.
        """
        if (bytes_transferred < MIN_THROUGHPUT_SAMPLE_BYTES
                and duration_seconds < MIN_THROUGHPUT_SAMPLE_SECONDS):
            return None
        with self._lock:
            dur = max(0.001, duration_seconds)
            bps = bytes_transferred / dur
            series = self._get_series("transfer_throughput_bps", site_id)

            anomaly_event: Optional[AnomalyEvent] = None
            if series.count >= self.min_samples_for_baseline:
                baseline = series.mean
                if baseline > 10_000.0 and bps < (baseline * self.throughput_collapse_threshold):
                    score = baseline / max(1.0, bps)
                    anomaly_event = AnomalyEvent(
                        timestamp=time.time(),
                        bottleneck_type=BottleneckType.THROUGHPUT_COLLAPSE,
                        severity=AnomalySeverity.CRITICAL,
                        metric_name="transfer_throughput_bps",
                        observed_value=bps,
                        baseline_value=baseline,
                        anomaly_score=score,
                        details=(
                            f"Throughput collapse detected on site {site_id or 'default'}: "
                            f"observed {bps:.1f} B/s vs baseline {baseline:.1f} B/s"
                        ),
                        site_id=site_id,
                        recommended_action=(
                            "Verify network path, inspect rate limiting, or rotate upstream mirrors."
                        ),
                    )
                    self._emit_anomaly(anomaly_event)

            series.add(bps)
            return anomaly_event

    def record_queue_latency(
        self,
        queue_wait_seconds: float,
        site_id: Optional[str] = None,
    ) -> Optional[AnomalyEvent]:
        """Record queue latency observation and detect queue wait spikes."""
        with self._lock:
            # Add then evaluate once: record_metric would auto-detect and this
            # method would detect again, emitting the same anomaly twice.
            self._get_series("queue_latency_seconds", site_id).add(float(queue_wait_seconds))
            anomalies = self.detect_anomalies("queue_latency_seconds", site_id=site_id)
            return anomalies[-1] if anomalies else None

    def detect_anomalies(
        self,
        metric_name: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> list[AnomalyEvent]:
        """Evaluate series statistical baselines and flag active anomaly outliers."""
        with self._lock:
            anomalies: list[AnomalyEvent] = []
            for (m_name, s_id), series in self._series.items():
                if metric_name is not None and m_name != metric_name:
                    continue
                if site_id is not None and s_id != site_id:
                    continue
                if series.count <= self.min_samples_for_baseline:
                    continue

                latest, baseline_mean, z = series.z_score_of_latest()
                if z >= self.z_score_threshold:
                    if "queue" in m_name:
                        b_type = BottleneckType.QUEUE_BACKLOG
                    elif "latency" in m_name:
                        b_type = BottleneckType.LATENCY_SPIKE
                    elif "contention" in m_name:
                        b_type = BottleneckType.RESOURCE_CONTENTION
                    else:
                        b_type = BottleneckType.LATENCY_SPIKE

                    severity = (
                        AnomalySeverity.CRITICAL if z >= (self.z_score_threshold * 1.5)
                        else AnomalySeverity.WARNING
                    )
                    event = AnomalyEvent(
                        timestamp=time.time(),
                        bottleneck_type=b_type,
                        severity=severity,
                        metric_name=m_name,
                        observed_value=latest,
                        baseline_value=baseline_mean,
                        anomaly_score=z,
                        details=(
                            f"Statistical anomaly in {m_name} (site {s_id or 'default'}): "
                            f"value {latest:.2f} deviates with z-score {z:.2f} (mean {baseline_mean:.2f})"
                        ),
                        site_id=s_id,
                        recommended_action="Inspect resource contention and throttle intake rate.",
                    )
                    self._emit_anomaly(event)
                    anomalies.append(event)
            return anomalies

    def _emit_anomaly(self, event: AnomalyEvent) -> None:
        self._anomalies.append(event)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                # A broken listener must not cost the other listeners the event.
                with self._lock:
                    self.callback_failures += 1
                logger.warning("workload bottleneck listener %r failed", cb, exc_info=True)

    def observe_completed_transfer(
        self,
        bytes_fetched: Any,
        duration_seconds: Any,
        site_id: Optional[str] = None,
    ) -> None:
        """Download-path entry: one completed file's wire throughput.

        A file that fetched no bytes (already on disk) or took no time is not a
        sample. Never raises into a completed download: a sample that cannot be
        recorded is counted in ``ingest_failures`` and logged.
        """
        try:
            fetched, seconds = int(bytes_fetched or 0), float(duration_seconds)
            if fetched > 0 and seconds > 0:
                self.record_transfer_sample(fetched, seconds, site_id=site_id)
        except Exception:
            with self._lock:
                self.ingest_failures += 1
            logger.warning("workload bottleneck: transfer sample dropped", exc_info=True)

    def register_callback(self, callback: Callable[[AnomalyEvent], None]) -> None:
        """Register listener invoked whenever a bottleneck anomaly is identified."""
        with self._lock:
            self._callbacks.append(callback)

    def get_active_anomalies(self, limit: int = 50) -> list[AnomalyEvent]:
        """Return the most recently recorded anomaly events."""
        with self._lock:
            return list(self._anomalies)[-max(1, limit):]

    def get_metrics_summary(self) -> dict[str, Any]:
        """Return aggregated summary of monitored workload series."""
        with self._lock:
            summary = {
                "total_series": len(self._series),
                "total_samples": sum(s.count for s in self._series.values()),
                "total_anomalies": len(self._anomalies),
                "callback_failures": self.callback_failures,
                "ingest_failures": self.ingest_failures,
                "metrics": {},
            }
            for (m_name, s_id), series in self._series.items():
                label = f"{m_name}@{s_id}" if s_id else m_name
                summary["metrics"][label] = {
                    "count": series.count,
                    "mean": series.mean,
                    "stddev": series.stddev,
                    "ewma": series.ewma,
                }
            return summary

    def get_health_status(self) -> dict[str, Any]:
        """Evaluate overall workload health status based on recent anomalies."""
        with self._lock:
            recent_critical = [
                a for a in self._anomalies
                if a.severity == AnomalySeverity.CRITICAL and (time.time() - a.timestamp) < 60.0
            ]
            recent_warning = [
                a for a in self._anomalies
                if a.severity == AnomalySeverity.WARNING and (time.time() - a.timestamp) < 60.0
            ]
            if recent_critical:
                status = "degraded"
            elif recent_warning:
                status = "warning"
            else:
                status = "healthy"

            return {
                "status": status,
                "critical_count": len(recent_critical),
                "warning_count": len(recent_warning),
                "active_anomalies": len(self._anomalies),
            }

    def reset(self) -> None:
        """Clear all active metric series and stored anomalies."""
        with self._lock:
            self._series.clear()
            self._anomalies.clear()
            self._callbacks.clear()


# ─── Singleton & Module-Level Convenience Functions ─────────────────────────

_DETECTOR_LOCK = threading.Lock()
_GLOBAL_DETECTOR: Optional[WorkloadBottleneckDetector] = None


def get_bottleneck_detector() -> WorkloadBottleneckDetector:
    """Retrieve global singleton WorkloadBottleneckDetector."""
    global _GLOBAL_DETECTOR
    if _GLOBAL_DETECTOR is None:
        with _DETECTOR_LOCK:
            if _GLOBAL_DETECTOR is None:
                _GLOBAL_DETECTOR = WorkloadBottleneckDetector()
    return _GLOBAL_DETECTOR


def reset_bottleneck_detector() -> None:
    """Reset the global singleton detector."""
    global _GLOBAL_DETECTOR
    with _DETECTOR_LOCK:
        if _GLOBAL_DETECTOR is not None:
            _GLOBAL_DETECTOR.reset()
        _GLOBAL_DETECTOR = None


def record_workload_metric(
    metric_name: str,
    value: float,
    site_id: Optional[str] = None,
) -> None:
    """Convenience helper to record workload metric into the global detector."""
    get_bottleneck_detector().record_metric(metric_name, value, site_id=site_id)
