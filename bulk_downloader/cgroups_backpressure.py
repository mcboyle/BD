"""cgroups_backpressure -- Cgroups v2 High-Water Mark Dynamic Backpressure Controller & Circuit Breaker.

Provides Linux cgroups v2 memory telemetry extraction, pressure stall information (PSI) parsing,
dynamic high-water mark throttling calculation, and a three-state circuit breaker (CLOSED, OPEN, HALF_OPEN)
for graceful load shedding and OOM prevention.
"""
from __future__ import annotations

import enum
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class CgroupV2State(str, enum.Enum):
    """Dynamic backpressure operational state."""
    NORMAL = "NORMAL"
    ADVISORY = "ADVISORY"
    CRITICAL = "CRITICAL"


class CircuitState(str, enum.Enum):
    """Circuit breaker state."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when task admission is requested while the circuit breaker is OPEN."""


@dataclass
class CgroupV2Metrics:
    """Snapshot of cgroups v2 memory and pressure metrics."""
    current_bytes: int = 0
    high_bytes: int | None = None
    max_bytes: int | None = None
    high_events: int = 0
    max_events: int = 0
    oom_events: int = 0
    oom_kill_events: int = 0
    pressure_some_avg10: float = 0.0
    pressure_full_avg10: float = 0.0
    is_cgroup_v2_available: bool = False
    # memory.stat inactive_file: clean page cache, the first memory the
    # kernel reclaims under memory.high/memory.max.
    inactive_file_bytes: int = 0

    @property
    def effective_limit_bytes(self) -> int | None:
        """Return the effective upper threshold in bytes (high_bytes preferred, max_bytes fallback)."""
        if self.high_bytes is not None and self.high_bytes > 0:
            return self.high_bytes
        if self.max_bytes is not None and self.max_bytes > 0:
            return self.max_bytes
        return None

    @property
    def working_set_bytes(self) -> int:
        """memory.current less inactive_file (the kubelet/cAdvisor working
        set). Written-out downloads fill the cgroup with page cache that the
        kernel reclaims on demand, and a held intake frees none of it: counted
        as pressure it would trip the breaker and then keep it OPEN."""
        return max(self.current_bytes - self.inactive_file_bytes, 0)

    @property
    def usage_ratio(self) -> float | None:
        """Return the working-set ratio against the effective upper limit, or None if unconstrained."""
        limit = self.effective_limit_bytes
        if limit is None or limit <= 0:
            return None
        return min(max(self.working_set_bytes / limit, 0.0), 1.0)


class CgroupV2Reader:
    """Reads and parses memory and PSI telemetry from a cgroups v2 unified mount."""

    DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")

    def __init__(self, cgroup_path: os.PathLike | str | None = None) -> None:
        if cgroup_path is not None:
            self.cgroup_dir = Path(cgroup_path)
        else:
            self.cgroup_dir = self._resolve_active_cgroup()

    def _resolve_active_cgroup(self) -> Path:
        """Resolve current process cgroup v2 unified hierarchy path."""
        proc_cgroup = Path("/proc/self/cgroup")
        if proc_cgroup.is_file():
            try:
                for line in proc_cgroup.read_text(encoding="utf-8").splitlines():
                    # cgroup v2 format is "0::$PATH"
                    parts = line.split(":", 2)
                    if len(parts) == 3 and parts[0] == "0":
                        rel_path = parts[2].lstrip("/")
                        target = self.DEFAULT_CGROUP_ROOT / rel_path
                        if target.is_dir():
                            return target
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                # Unresolvable: the hierarchy root, as when no v2 entry matches.
                logger.debug("Failed reading %s: %s", proc_cgroup, exc)
                return self.DEFAULT_CGROUP_ROOT
        return self.DEFAULT_CGROUP_ROOT

    def _read_int_or_none(self, filename: str) -> int | None:
        filepath = self.cgroup_dir / filename
        if not filepath.is_file():
            return None
        try:
            content = filepath.read_text(encoding="utf-8").strip()
            if content == "max" or not content:
                return None
            return int(content)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            logger.debug("Failed parsing int from %s: %s", filepath, exc)
            return None

    def _read_memory_events(self) -> dict[str, int]:
        """high/max/oom/oom_kill counters from memory.events: zeros when the
        file is absent; an unreadable file keeps the counters read before the
        failure."""
        counts = {"high": 0, "max": 0, "oom": 0, "oom_kill": 0}
        events_file = self.cgroup_dir / "memory.events"
        if not events_file.is_file():
            return counts
        try:
            for line in events_file.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if len(parts) == 2:
                    k, v = parts[0], int(parts[1])
                    if k in counts:
                        counts[k] = v
            return counts
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            logger.debug("Failed reading memory events: %s", exc)
            return counts

    def _read_pressure_avg10(self) -> tuple[float, float]:
        """(some, full) avg10 from memory.pressure (PSI): 0.0 when the file is
        absent; an unreadable file keeps the values read before the failure."""
        avg10 = {"some": 0.0, "full": 0.0}
        pressure_file = self.cgroup_dir / "memory.pressure"
        if not pressure_file.is_file():
            return avg10["some"], avg10["full"]
        try:
            for line in pressure_file.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if parts and parts[0] in avg10:
                    for kv in parts[1:]:
                        if kv.startswith("avg10="):
                            avg10[parts[0]] = float(kv.split("=", 1)[1])
            return avg10["some"], avg10["full"]
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            logger.debug("Failed reading memory pressure: %s", exc)
            return avg10["some"], avg10["full"]

    def _read_inactive_file(self) -> int:
        """inactive_file from memory.stat; 0 -- nothing discounted from
        memory.current -- when the file is absent, unreadable or lacks it."""
        stat_file = self.cgroup_dir / "memory.stat"
        if not stat_file.is_file():
            return 0
        try:
            for line in stat_file.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] == "inactive_file":
                    return max(int(parts[1]), 0)
            return 0
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            logger.debug("Failed reading memory stat: %s", exc)
            return 0

    def read_metrics(self) -> CgroupV2Metrics:
        """Read and parse all relevant cgroup v2 memory telemetry."""
        if not self.cgroup_dir.is_dir():
            return CgroupV2Metrics(is_cgroup_v2_available=False)

        current_bytes = self._read_int_or_none("memory.current")
        if current_bytes is None:
            # Not a cgroup v2 directory or permissions restricted
            return CgroupV2Metrics(is_cgroup_v2_available=False)

        high_bytes = self._read_int_or_none("memory.high")
        max_bytes = self._read_int_or_none("memory.max")
        events = self._read_memory_events()
        some_avg10, full_avg10 = self._read_pressure_avg10()

        return CgroupV2Metrics(
            current_bytes=current_bytes,
            high_bytes=high_bytes,
            max_bytes=max_bytes,
            high_events=events["high"],
            max_events=events["max"],
            oom_events=events["oom"],
            oom_kill_events=events["oom_kill"],
            pressure_some_avg10=some_avg10,
            pressure_full_avg10=full_avg10,
            is_cgroup_v2_available=True,
            inactive_file_bytes=self._read_inactive_file(),
        )


@dataclass
class BackpressureConfig:
    """Tuning configuration for cgroups v2 backpressure and circuit breaker."""
    cgroup_path: str | None = None
    high_watermark_ratio: float = 0.80
    critical_watermark_ratio: float = 0.95
    recovery_ratio: float = 0.60
    cooldown_seconds: float = 5.0
    max_backpressure_delay_ms: float = 2000.0
    consecutive_critical_threshold: int = 2
    half_open_trial_permits: int = 1
    # A HALF_OPEN trial that never reports feedback (the probe died, or the
    # caller never calls record_feedback) must not pin the breaker shut:
    # after this long the breaker re-samples as if its cooldown had elapsed.
    half_open_trial_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not (0.0 < self.high_watermark_ratio < 1.0):
            raise ValueError(f"high_watermark_ratio must be between 0.0 and 1.0 (got {self.high_watermark_ratio})")
        if not (0.0 < self.critical_watermark_ratio <= 1.0):
            raise ValueError(f"critical_watermark_ratio must be between 0.0 and 1.0 (got {self.critical_watermark_ratio})")
        if self.high_watermark_ratio >= self.critical_watermark_ratio:
            raise ValueError(
                f"critical_watermark_ratio must be greater than high_watermark_ratio "
                f"({self.critical_watermark_ratio} <= {self.high_watermark_ratio})"
            )
        if self.recovery_ratio >= self.high_watermark_ratio:
            raise ValueError(
                f"recovery_ratio must be less than high_watermark_ratio "
                f"({self.recovery_ratio} >= {self.high_watermark_ratio})"
            )


class CgroupV2BackpressureController:
    """Controller evaluating memory saturation and managing dynamic backpressure and circuit breaker."""

    def __init__(
        self,
        config: BackpressureConfig | None = None,
        reader: CgroupV2Reader | None = None,
    ) -> None:
        self.config = config or BackpressureConfig()
        self.reader = reader or CgroupV2Reader(cgroup_path=self.config.cgroup_path)
        self._lock = threading.RLock()

        self._circuit_state = CircuitState.CLOSED
        self._last_trip_time: float = 0.0
        self._consecutive_critical_count: int = 0
        self._active_trial_permits: int = 0
        self._half_open_since: float = 0.0
        # Tickets of the current HALF_OPEN episode's trials: only work
        # admitted as a trial may settle the breaker (record_feedback) or
        # hand its permit back (release_trial).
        self._trial_seq: int = 0
        self._trials: set[int] = set()

    @property
    def circuit_state(self) -> CircuitState:
        with self._lock:
            return self._circuit_state

    def get_metrics(self) -> CgroupV2Metrics:
        """Fetch fresh cgroup v2 metrics snapshot."""
        return self.reader.read_metrics()

    def get_state(self, metrics: CgroupV2Metrics | None = None) -> CgroupV2State:
        """Evaluate operational watermark tier from current or supplied metrics."""
        m = metrics or self.get_metrics()
        ratio = m.usage_ratio
        if ratio is None or not m.is_cgroup_v2_available:
            return CgroupV2State.NORMAL

        if ratio >= self.config.critical_watermark_ratio:
            return CgroupV2State.CRITICAL
        if ratio >= self.config.high_watermark_ratio:
            return CgroupV2State.ADVISORY
        return CgroupV2State.NORMAL

    def compute_backpressure_factor(self, metrics: CgroupV2Metrics | None = None) -> float:
        """Compute normalized throttling factor from 0.0 (no throttling) to 1.0 (full throttling)."""
        m = metrics or self.get_metrics()
        ratio = m.usage_ratio
        if ratio is None or not m.is_cgroup_v2_available:
            return 0.0

        if ratio <= self.config.high_watermark_ratio:
            return 0.0
        if ratio >= self.config.critical_watermark_ratio:
            return 1.0

        # Linear ramp between high_watermark_ratio and critical_watermark_ratio
        span = self.config.critical_watermark_ratio - self.config.high_watermark_ratio
        factor = (ratio - self.config.high_watermark_ratio) / span
        return min(max(factor, 0.0), 1.0)

    def compute_delay_ms(self, metrics: CgroupV2Metrics | None = None) -> float:
        """Calculate dynamic sleep delay in milliseconds to throttle inbound task scheduling."""
        factor = self.compute_backpressure_factor(metrics=metrics)
        return factor * self.config.max_backpressure_delay_ms

    def trip(self, reason: str = "Explicit trip") -> None:
        """Manually trip the circuit breaker into OPEN state."""
        with self._lock:
            self._circuit_state = CircuitState.OPEN
            self._last_trip_time = time.monotonic()
            self._consecutive_critical_count = 0
            self._active_trial_permits = 0
            self._trials.clear()
            logger.warning("CgroupV2BackpressureController: breaker tripped to OPEN: %s", reason)

    def reset(self) -> None:
        """Reset circuit breaker to CLOSED and clear counters."""
        with self._lock:
            self._circuit_state = CircuitState.CLOSED
            self._consecutive_critical_count = 0
            self._active_trial_permits = 0
            self._trials.clear()

    def check_admission(self) -> tuple[bool, str]:
        """Check whether a new task or request can be admitted without breaching safety margins."""
        admitted, reason, _trial = self.check_admission_with_trial()
        return admitted, reason

    def check_admission_with_trial(self) -> tuple[bool, str, int | None]:
        """check_admission, plus a ticket when the admission is a HALF_OPEN
        trial (None otherwise). Only that trial settles the breaker: report
        its outcome with record_feedback(..., trial=ticket), or hand the
        permit back with release_trial(ticket) if the work never ran."""
        with self._lock:
            now = time.monotonic()

            # 1. Breaker is HALF_OPEN: allow limited trial probes. A trial
            #    that outlives half_open_trial_timeout_seconds without
            #    feedback re-opens with its cooldown already served, so the
            #    OPEN branch below re-samples memory instead of wedging.
            if self._circuit_state == CircuitState.HALF_OPEN:
                if self._active_trial_permits < self.config.half_open_trial_permits:
                    self._active_trial_permits += 1
                    return True, "Admitted for HALF_OPEN probe permit", self._issue_trial()
                if now - self._half_open_since < self.config.half_open_trial_timeout_seconds:
                    return False, "Circuit breaker HALF_OPEN: maximum trial permits active", None
                logger.warning("CgroupV2BackpressureController: HALF_OPEN trial timed out without feedback; re-sampling")
                self._circuit_state = CircuitState.OPEN
                self._last_trip_time = now - self.config.cooldown_seconds
                self._trials.clear()

            # 2. Breaker is OPEN: verify cooldown & recovery
            if self._circuit_state == CircuitState.OPEN:
                elapsed = now - self._last_trip_time
                if elapsed < self.config.cooldown_seconds:
                    return False, f"Circuit breaker OPEN: cooldown active ({elapsed:.2f}s < {self.config.cooldown_seconds}s)", None

                # Cooldown has elapsed -> sample memory to test recovery
                metrics = self.get_metrics()
                ratio = metrics.usage_ratio

                if ratio is not None and ratio >= self.config.recovery_ratio:
                    # Still elevated, prolong open state
                    self._last_trip_time = now
                    return False, f"Circuit breaker OPEN: usage {ratio:.1%} exceeds recovery threshold {self.config.recovery_ratio:.1%}", None

                # Recovering -> transition to HALF_OPEN
                self._circuit_state = CircuitState.HALF_OPEN
                self._active_trial_permits = 1
                self._half_open_since = now
                logger.info("CgroupV2BackpressureController: transitioning to HALF_OPEN trial")
                return True, "Admitted for HALF_OPEN trial probe", self._issue_trial()

            # 3. Breaker is CLOSED: evaluate memory pressure
            metrics = self.get_metrics()
            state = self.get_state(metrics=metrics)

            if state == CgroupV2State.CRITICAL:
                self._consecutive_critical_count += 1
                if self._consecutive_critical_count >= self.config.consecutive_critical_threshold:
                    self.trip(
                        reason=(
                            f"Usage ratio {metrics.usage_ratio:.1%} reached critical threshold "
                            f"{self.config.critical_watermark_ratio:.1%} for {self._consecutive_critical_count} samples"
                        )
                    )
                    return False, "Circuit breaker OPEN: critical memory limit exceeded", None
            else:
                self._consecutive_critical_count = 0

            return True, "Admission granted", None

    def _issue_trial(self) -> int:
        self._trial_seq += 1
        self._trials.add(self._trial_seq)
        return self._trial_seq

    def require_admission(self) -> None:
        """Assert admission or raise CircuitBreakerOpenError."""
        admitted, reason = self.check_admission()
        if not admitted:
            raise CircuitBreakerOpenError(f"Circuit breaker is OPEN: {reason}")

    def record_feedback(self, success: bool, reason: str | None = None, trial: int | None = None) -> None:
        """Record task outcome feedback to govern HALF_OPEN recovery. A
        ticketed report (trial=) counts only for a trial of the current
        HALF_OPEN episode: one that timed out proves nothing now."""
        with self._lock:
            if self._circuit_state == CircuitState.HALF_OPEN and (
                    trial is None or trial in self._trials):
                if success:
                    logger.info("CgroupV2BackpressureController: trial succeeded; resetting breaker to CLOSED")
                    self.reset()
                else:
                    logger.warning("CgroupV2BackpressureController: trial failed (%s); reopening breaker", reason)
                    self.trip(reason=reason or "Trial probe execution failure")

    def release_trial(self, trial: int) -> None:
        """Hand back the permit of a HALF_OPEN trial whose work never ran
        (e.g. its claim was stale): the next admission becomes the trial."""
        with self._lock:
            if self._circuit_state == CircuitState.HALF_OPEN and trial in self._trials:
                self._trials.discard(trial)
                self._active_trial_permits = max(self._active_trial_permits - 1, 0)


def create_controller(
    cgroup_path: str | None = None,
    high_watermark_ratio: float = 0.80,
    critical_watermark_ratio: float = 0.95,
    recovery_ratio: float = 0.60,
    cooldown_seconds: float = 5.0,
    max_backpressure_delay_ms: float = 2000.0,
) -> CgroupV2BackpressureController:
    """Factory helper creating a CgroupV2BackpressureController with default or specified settings."""
    config = BackpressureConfig(
        cgroup_path=cgroup_path,
        high_watermark_ratio=high_watermark_ratio,
        critical_watermark_ratio=critical_watermark_ratio,
        recovery_ratio=recovery_ratio,
        cooldown_seconds=cooldown_seconds,
        max_backpressure_delay_ms=max_backpressure_delay_ms,
    )
    return CgroupV2BackpressureController(config=config)


_SHARED_CONTROLLER: CgroupV2BackpressureController | None = None
_SHARED_CONTROLLER_LOCK = threading.Lock()


def get_cgroup_controller() -> CgroupV2BackpressureController:
    """The process-wide controller. The memory it measures is the process's
    own cgroup, so every intake must share one breaker: a trip seen by one
    runner has to hold the others too."""
    global _SHARED_CONTROLLER
    with _SHARED_CONTROLLER_LOCK:
        if _SHARED_CONTROLLER is None:
            _SHARED_CONTROLLER = create_controller()
        return _SHARED_CONTROLLER
