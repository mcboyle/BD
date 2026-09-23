"""Row 984: Cgroups v2 High-Water Mark Dynamic Backpressure Controller and Circuit Breaker.

Validates cgroups v2 memory telemetry reading, pressure stall information (PSI) parsing,
dynamic high-water mark threshold evaluation, backpressure factor and delay calculation,
and circuit breaker state transitions (CLOSED, OPEN, HALF_OPEN) with fast-fail load shedding.

RED on baseline: bulk_downloader.cgroups_backpressure does not exist.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"


class _HostCgroupRoot:
    """Stands in for /sys/fs/cgroup: a test that resolves the host's cgroup
    hierarchy (or reads under it) fails instead of measuring the host."""

    def _host(self, *_args, **_kwargs):
        pytest.fail("row984: a test resolved the host's cgroup -- tests must "
                    "not read the host cgroup")

    __truediv__ = _host
    is_dir = _host
    is_file = _host


@pytest.fixture(autouse=True)
def _no_host_cgroup(monkeypatch, tmp_path):
    """VERIFY-r1 LOW-4: no test here reads the host's cgroup. The process-wide
    breaker is an inert controller over an empty directory (a test that needs
    one installs its own) and the host hierarchy is _HostCgroupRoot."""
    try:
        from bulk_downloader import cgroups_backpressure as cg
    except ImportError:
        return  # base: test_module_exports reports the missing module
    monkeypatch.setattr(cg.CgroupV2Reader, "DEFAULT_CGROUP_ROOT", _HostCgroupRoot())
    monkeypatch.setattr(cg, "_SHARED_CONTROLLER", cg.CgroupV2BackpressureController(
        config=cg.BackpressureConfig(cgroup_path=str(tmp_path / "no-cgroup"))))


def test_module_exports():
    """RED assertion 1: Base must provide cgroups v2 dynamic backpressure controller and circuit breaker."""
    try:
        from bulk_downloader import cgroups_backpressure
    except ImportError:
        pytest.fail("Base lacks Cgroups v2 backpressure controller & circuit breaker (bulk_downloader.cgroups_backpressure)")

    assert hasattr(cgroups_backpressure, "CgroupV2State")
    assert hasattr(cgroups_backpressure, "CircuitState")
    assert hasattr(cgroups_backpressure, "CgroupV2Metrics")
    assert hasattr(cgroups_backpressure, "CgroupV2Reader")
    assert hasattr(cgroups_backpressure, "BackpressureConfig")
    assert hasattr(cgroups_backpressure, "CgroupV2BackpressureController")
    assert hasattr(cgroups_backpressure, "CircuitBreakerOpenError")
    assert hasattr(cgroups_backpressure, "create_controller")


def test_cgroup_v2_reader_parses_metrics(tmp_path: Path):
    """Test reading memory.current, memory.high, memory.max, memory.events, and memory.pressure."""
    from bulk_downloader.cgroups_backpressure import CgroupV2Reader

    # Create synthetic cgroups v2 filesystem hierarchy
    cgroup_dir = tmp_path / "unified"
    cgroup_dir.mkdir(parents=True)

    (cgroup_dir / "memory.current").write_text("838860800\n", encoding="utf-8")  # 800 MB
    (cgroup_dir / "memory.high").write_text("943718400\n", encoding="utf-8")     # 900 MB
    (cgroup_dir / "memory.max").write_text("1073741824\n", encoding="utf-8")    # 1024 MB (1 GB)
    (cgroup_dir / "memory.events").write_text(
        "low 0\n"
        "high 14\n"
        "max 2\n"
        "oom 0\n"
        "oom_kill 0\n"
        "oom_group_kill 0\n",
        encoding="utf-8"
    )
    (cgroup_dir / "memory.pressure").write_text(
        "some avg10=2.45 avg60=1.12 avg300=0.45 total=1284900\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
        encoding="utf-8"
    )

    reader = CgroupV2Reader(cgroup_path=cgroup_dir)
    metrics = reader.read_metrics()

    assert metrics.current_bytes == 838860800
    assert metrics.high_bytes == 943718400
    assert metrics.max_bytes == 1073741824
    assert metrics.high_events == 14
    assert metrics.max_events == 2
    assert metrics.oom_events == 0
    assert metrics.pressure_some_avg10 == pytest.approx(2.45)
    assert metrics.pressure_full_avg10 == pytest.approx(0.0)
    assert metrics.is_cgroup_v2_available is True


def test_cgroup_v2_reader_handles_max_and_missing(tmp_path: Path):
    """Test handling of 'max' sentinel in memory.high/max and graceful degradation when files missing."""
    from bulk_downloader.cgroups_backpressure import CgroupV2Reader

    cgroup_dir = tmp_path / "limited"
    cgroup_dir.mkdir(parents=True)

    (cgroup_dir / "memory.current").write_text("209715200\n", encoding="utf-8")  # 200 MB
    (cgroup_dir / "memory.high").write_text("max\n", encoding="utf-8")
    (cgroup_dir / "memory.max").write_text("max\n", encoding="utf-8")

    reader = CgroupV2Reader(cgroup_path=cgroup_dir)
    metrics = reader.read_metrics()

    assert metrics.current_bytes == 209715200
    assert metrics.high_bytes is None
    assert metrics.max_bytes is None
    assert metrics.high_events == 0

    # Non-existent cgroup path
    missing_reader = CgroupV2Reader(cgroup_path=tmp_path / "non_existent")
    missing_metrics = missing_reader.read_metrics()
    assert missing_metrics.is_cgroup_v2_available is False
    assert missing_metrics.current_bytes == 0


def test_cgroup_v2_reader_degraded_telemetry_keeps_what_it_read(
    tmp_path: Path, monkeypatch
):
    """An unparsable memory.events / memory.pressure line keeps the values
    read before it and never hides memory.current vs memory.high; an
    unreadable /proc/self/cgroup resolves to the hierarchy root, not a raise."""
    from bulk_downloader import cgroups_backpressure as cg

    cgroup_dir = tmp_path / "degraded"
    cgroup_dir.mkdir()
    (cgroup_dir / "memory.current").write_text("480\n", encoding="utf-8")
    (cgroup_dir / "memory.high").write_text("500\n", encoding="utf-8")
    (cgroup_dir / "memory.events").write_text(
        "high 14\nmax lots\n", encoding="utf-8")
    (cgroup_dir / "memory.pressure").write_text(
        "some avg10=2.45 avg60=1.12 avg300=0.45 total=1284900\n"
        "full avg10=n/a avg60=0.00 avg300=0.00 total=0\n", encoding="utf-8")

    metrics = cg.CgroupV2Reader(cgroup_path=cgroup_dir).read_metrics()
    assert metrics.is_cgroup_v2_available is True
    assert metrics.usage_ratio == pytest.approx(0.96)
    assert metrics.high_events == 14 and metrics.max_events == 0
    assert metrics.pressure_some_avg10 == pytest.approx(2.45)
    assert metrics.pressure_full_avg10 == 0.0

    class _UnreadableProcCgroup:
        def __init__(self, *_args):
            pass

        def is_file(self):
            return True

        def read_text(self, **_kwargs):
            raise OSError("row984: /proc/self/cgroup unreadable")

    monkeypatch.setattr(cg, "Path", _UnreadableProcCgroup)
    reader = cg.CgroupV2Reader()
    assert reader.cgroup_dir == cg.CgroupV2Reader.DEFAULT_CGROUP_ROOT


def test_backpressure_factor_and_delay_calculation(tmp_path: Path):
    """Verify dynamic backpressure factor ramps up between high watermark and critical threshold."""
    from bulk_downloader.cgroups_backpressure import (
        BackpressureConfig,
        CgroupV2BackpressureController,
        CgroupV2State,
    )

    cgroup_dir = tmp_path / "pressure_test"
    cgroup_dir.mkdir(parents=True)
    # Total limit: 1000 MB
    (cgroup_dir / "memory.max").write_text("1048576000\n", encoding="utf-8")

    config = BackpressureConfig(
        cgroup_path=str(cgroup_dir),
        high_watermark_ratio=0.70,   # 700 MB
        critical_watermark_ratio=0.90, # 900 MB
        max_backpressure_delay_ms=1000.0,
    )
    ctrl = CgroupV2BackpressureController(config=config)

    # 1. Normal state (500 MB / 1000 MB = 50% < 70%)
    (cgroup_dir / "memory.current").write_text("524288000\n", encoding="utf-8")
    metrics = ctrl.get_metrics()
    assert ctrl.get_state() == CgroupV2State.NORMAL
    assert ctrl.compute_backpressure_factor() == 0.0
    assert ctrl.compute_delay_ms() == 0.0

    # 2. Advisory / throttled state (800 MB / 1000 MB = 80%, midway between 70% and 90%)
    (cgroup_dir / "memory.current").write_text("838860800\n", encoding="utf-8")
    metrics = ctrl.get_metrics()
    assert ctrl.get_state() == CgroupV2State.ADVISORY
    factor = ctrl.compute_backpressure_factor()
    assert 0.45 <= factor <= 0.55
    delay = ctrl.compute_delay_ms()
    assert 450.0 <= delay <= 550.0

    # 3. Critical state (950 MB / 1000 MB = 95% > 90%)
    (cgroup_dir / "memory.current").write_text("996147200\n", encoding="utf-8")
    metrics = ctrl.get_metrics()
    assert ctrl.get_state(metrics) == CgroupV2State.CRITICAL
    assert ctrl.compute_backpressure_factor() == 1.0
    assert ctrl.compute_delay_ms() == 1000.0


def test_circuit_breaker_transitions_and_load_shedding(tmp_path: Path):
    """Verify circuit breaker opens under critical pressure and sheds load with fast failure."""
    from bulk_downloader.cgroups_backpressure import (
        BackpressureConfig,
        CgroupV2BackpressureController,
        CircuitBreakerOpenError,
        CircuitState,
    )

    cgroup_dir = tmp_path / "cb_test"
    cgroup_dir.mkdir(parents=True)
    (cgroup_dir / "memory.max").write_text("1000000000\n", encoding="utf-8")

    config = BackpressureConfig(
        cgroup_path=str(cgroup_dir),
        high_watermark_ratio=0.70,
        critical_watermark_ratio=0.90,
        recovery_ratio=0.60,
        cooldown_seconds=0.1,  # Fast cooldown for unit test
        consecutive_critical_threshold=2,
    )
    ctrl = CgroupV2BackpressureController(config=config)

    assert ctrl.circuit_state == CircuitState.CLOSED
    admitted, reason = ctrl.check_admission()
    assert admitted is True

    # First critical read (950 MB)
    (cgroup_dir / "memory.current").write_text("950000000\n", encoding="utf-8")
    admitted, _ = ctrl.check_admission()
    # 1 critical check is below consecutive_critical_threshold=2
    assert admitted is True
    assert ctrl.circuit_state == CircuitState.CLOSED

    # Second critical read triggers circuit trip
    admitted, reason = ctrl.check_admission()
    assert admitted is False
    assert ctrl.circuit_state == CircuitState.OPEN
    assert "Circuit breaker OPEN" in reason

    # Calling require_admission() must raise CircuitBreakerOpenError
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        ctrl.require_admission()
    assert "Circuit breaker is OPEN" in str(exc_info.value)

    # Allow cooldown time to pass
    time.sleep(0.12)

    # Memory still critical -> probe fails and stays OPEN
    admitted, _ = ctrl.check_admission()
    assert admitted is False
    assert ctrl.circuit_state == CircuitState.OPEN

    # Memory recovers to 500 MB (< recovery_ratio 60%)
    (cgroup_dir / "memory.current").write_text("500000000\n", encoding="utf-8")
    time.sleep(0.12)

    # Trial probe in HALF_OPEN
    admitted, reason = ctrl.check_admission()
    assert admitted is True
    assert ctrl.circuit_state == CircuitState.HALF_OPEN

    # Success feedback closes the breaker
    ctrl.record_feedback(success=True)
    assert ctrl.circuit_state == CircuitState.CLOSED


def test_circuit_breaker_half_open_failure_reopens(tmp_path: Path):
    """Verify that failure during HALF_OPEN immediately reopens the breaker."""
    from bulk_downloader.cgroups_backpressure import (
        BackpressureConfig,
        CgroupV2BackpressureController,
        CircuitState,
    )

    cgroup_dir = tmp_path / "cb_half_open_fail"
    cgroup_dir.mkdir(parents=True)
    (cgroup_dir / "memory.max").write_text("1000000000\n", encoding="utf-8")
    (cgroup_dir / "memory.current").write_text("500000000\n", encoding="utf-8")

    config = BackpressureConfig(
        cgroup_path=str(cgroup_dir),
        cooldown_seconds=0.05,
    )
    ctrl = CgroupV2BackpressureController(config=config)

    # Manually trip
    ctrl.trip(reason="Manual test trip")
    assert ctrl.circuit_state == CircuitState.OPEN

    time.sleep(0.06)
    admitted, _ = ctrl.check_admission()
    assert admitted is True
    assert ctrl.circuit_state == CircuitState.HALF_OPEN

    # Task reported failure or high latency
    ctrl.record_feedback(success=False, reason="Worker OOM warning")
    assert ctrl.circuit_state == CircuitState.OPEN


def test_config_validation():
    """Verify invalid configuration bounds raise ValueError."""
    from bulk_downloader.cgroups_backpressure import BackpressureConfig

    with pytest.raises(ValueError, match="high_watermark_ratio"):
        BackpressureConfig(high_watermark_ratio=1.1)

    with pytest.raises(ValueError, match="critical_watermark_ratio must be greater"):
        BackpressureConfig(high_watermark_ratio=0.85, critical_watermark_ratio=0.80)

    with pytest.raises(ValueError, match="recovery_ratio must be less"):
        BackpressureConfig(high_watermark_ratio=0.70, recovery_ratio=0.75)


def test_create_controller_factory_and_fallback(tmp_path: Path):
    """Verify create_controller handles custom paths and graceful fallback without crashing."""
    from bulk_downloader.cgroups_backpressure import (
        CgroupV2State,
        CircuitState,
        create_controller,
    )

    # With non-existent cgroup path
    ctrl = create_controller(cgroup_path=str(tmp_path / "non_existent_path_984"))
    assert ctrl.circuit_state == CircuitState.CLOSED
    admitted, _ = ctrl.check_admission()
    assert admitted is True
    assert ctrl.compute_delay_ms() == 0.0
    assert ctrl.get_state() == CgroupV2State.NORMAL


# ── r2 (N6-A + P1-A refutes on 49c1f8fd) ─────────────────────────────────
# E1: the controller must govern the real intake -- SiteRunner's worker loop
# admits each dequeued URL through _resource_admission_hold (runner.py). On
# 49c1f8fd nothing in the product ever called check_admission.
# E2: a HALF_OPEN breaker whose trial never reports back must not refuse
# forever. E3: cooldown, trial cap, memory.high preference, critical-count
# reset and the shared accessor are pinned.

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class _SeqReader:
    """Reader returning a fixed usage ratio (mutable) against a 1000-byte limit."""

    def __init__(self, ratio, high=1000, max_=None):
        self.ratio = ratio
        self.high = high
        self.max_ = max_

    def read_metrics(self):
        from bulk_downloader.cgroups_backpressure import CgroupV2Metrics
        limit = self.high or self.max_
        return CgroupV2Metrics(current_bytes=int(self.ratio * limit),
                               high_bytes=self.high, max_bytes=self.max_,
                               is_cgroup_v2_available=True)


def _controller(monkeypatch, ratio, **cfg):
    from bulk_downloader import cgroups_backpressure as cg
    clock = _Clock()
    monkeypatch.setattr(cg, "time", clock)
    reader = _SeqReader(ratio)
    ctl = cg.CgroupV2BackpressureController(
        config=cg.BackpressureConfig(**cfg), reader=reader)
    return ctl, reader, clock


def _runner_probe(monkeypatch, controller):
    from bulk_downloader import daily_budget as db
    from bulk_downloader.runner import SiteRunner
    monkeypatch.setattr(db, "_GLOBAL_BUDGET", 0)
    probe = SiteRunner.__new__(SiteRunner)
    probe.site_id = "cgroup-probe"
    probe.config = {}
    probe._state = "running"
    probe._worker_context = threading.local()  # as SiteRunner.__init__ sets it
    probe._cgroup_controller = controller
    return probe


def _delay_s(probe):
    seam = getattr(probe, "_cgroup_backpressure_delay_s", None)
    assert seam is not None, (
        "row984: SiteRunner has no cgroup backpressure delay seam; the "
        "watermark ramp never slows the intake")
    return seam()


def test_runner_intake_holds_when_cgroup_breaker_trips(monkeypatch):
    ctl, _reader, _clock = _controller(monkeypatch, 0.97)
    probe = _runner_probe(monkeypatch, ctl)
    assert probe._resource_admission_hold() is None, "one critical sample admits"
    hold = probe._resource_admission_hold()
    assert hold is not None, (
        "row984: the runner's intake admitted work with the cgroup breaker "
        "tripped -- the controller is not wired into SiteRunner")
    assert hold["state"] == "cgroup_backpressure"
    assert probe._state == "cgroup_backpressure"
    again = probe._resource_admission_hold()
    assert again["state"] == "cgroup_backpressure" and "cooldown" in again["reason"]


def test_runner_intake_admits_under_the_watermark(monkeypatch):
    ctl, _reader, _clock = _controller(monkeypatch, 0.50)
    probe = _runner_probe(monkeypatch, ctl)
    for _ in range(3):
        assert probe._resource_admission_hold() is None
    assert _delay_s(probe) == 0.0


def test_runner_intake_delay_follows_the_watermark_ramp(monkeypatch):
    ctl, _reader, _clock = _controller(monkeypatch, 0.875)
    probe = _runner_probe(monkeypatch, ctl)
    assert _delay_s(probe) == pytest.approx(1.0), (
        "row984: 87.5% of memory.high (mid-ramp) must slow the intake by "
        "half of max_backpressure_delay_ms")


def test_runner_trial_feedback_closes_a_recovered_breaker(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, reader, clock = _controller(monkeypatch, 0.97)
    probe = _runner_probe(monkeypatch, ctl)
    ctl.trip("test")
    clock.now += 10
    reader.ratio = 0.30
    assert probe._resource_admission_hold() is None
    assert ctl.circuit_state == CircuitState.HALF_OPEN, (
        f"row984: the runner's intake never consulted the tripped breaker "
        f"(state {ctl.circuit_state!r} after cooldown + recovery)")
    feedback = getattr(probe, "_cgroup_trial_feedback", None)
    assert feedback is not None, "row984: runner reports no trial feedback"
    feedback()
    assert ctl.circuit_state == CircuitState.CLOSED


def test_half_open_without_feedback_does_not_wedge(monkeypatch):
    ctl, reader, clock = _controller(monkeypatch, 0.30)
    ctl.trip("test")
    clock.now += 10
    assert ctl.check_admission()[0] is True, "cooldown elapsed, recovered: trial"
    assert ctl.check_admission()[0] is False, "trial permit cap is 1"
    clock.now += 3600
    admitted, reason = ctl.check_admission()
    assert admitted is True, (
        f"row984: HALF_OPEN refused after its trial timed out ({reason!r}); a "
        f"trial that never reports feedback wedges the breaker forever")
    reader.ratio = 0.97
    ctl.check_admission()  # consume permit
    clock.now += 3600
    assert ctl.check_admission()[0] is False, "timed-out trial re-samples: still high -> OPEN"


def test_open_breaker_refuses_inside_cooldown_even_when_recovered(monkeypatch):
    ctl, _reader, clock = _controller(monkeypatch, 0.10)
    ctl.trip("test")
    clock.now += ctl.config.cooldown_seconds / 2
    admitted, reason = ctl.check_admission()
    assert admitted is False and "cooldown" in reason


def test_memory_high_is_preferred_over_memory_max():
    from bulk_downloader.cgroups_backpressure import CgroupV2Metrics
    m = CgroupV2Metrics(current_bytes=480, high_bytes=500, max_bytes=1000,
                        is_cgroup_v2_available=True)
    assert m.usage_ratio == pytest.approx(0.96)


def test_non_consecutive_critical_samples_do_not_trip(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, reader, _clock = _controller(monkeypatch, 0.97)
    for ratio in (0.97, 0.50, 0.97):
        reader.ratio = ratio
        assert ctl.check_admission()[0] is True
    assert ctl.circuit_state == CircuitState.CLOSED


def test_cgroup_controller_accessor_is_one_shared_instance(monkeypatch, tmp_path):
    from bulk_downloader import cgroups_backpressure as cg
    from bulk_downloader.ingress_flow_controller import IngressFlowController
    built = []

    def create_controller():
        built.append(cg.CgroupV2BackpressureController(
            config=cg.BackpressureConfig(cgroup_path=str(tmp_path / "cg"))))
        return built[-1]

    # VERIFY-r1 LOW-4: the process-wide breaker is built here from a factory
    # that never reads the host cgroup, and the prior one is restored after.
    monkeypatch.setattr(cg, "_SHARED_CONTROLLER", None)
    monkeypatch.setattr(cg, "create_controller", create_controller)
    first = IngressFlowController().get_cgroup_controller()
    assert IngressFlowController().get_cgroup_controller() is first, (
        "row984: each IngressFlowController built its own cgroup breaker; a "
        "trip seen by one intake was invisible to the next")
    shared = getattr(cg, "get_cgroup_controller", None)
    assert shared is not None and shared() is first and shared() is first
    assert built == [first], "row984: the process-wide breaker was built twice"


def test_runner_unmeasurable_trial_reopens_the_breaker(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, reader, clock = _controller(monkeypatch, 0.97)
    probe = _runner_probe(monkeypatch, ctl)
    ctl.trip("test")
    clock.now += 10
    reader.ratio = 0.30
    assert probe._resource_admission_hold() is None
    assert ctl.circuit_state == CircuitState.HALF_OPEN

    def unreadable():
        raise OSError("memory.current vanished")

    monkeypatch.setattr(reader, "read_metrics", unreadable)
    probe._cgroup_trial_feedback()
    assert ctl.circuit_state == CircuitState.OPEN, (
        "row984: a trial whose memory could not be measured closed or kept "
        "the breaker half-open; it proved nothing about recovery")


# N4-A E1 (REFUTE @480fe396): the seams above were pinned, but nothing drove
# SiteRunner._worker_loop through them -- removing the loop's delay wait (M11)
# or its _cgroup_trial_feedback() call (M12) left every test green. These run
# the real worker loop with only external seams stubbed (the pattern of
# tests/test_row_296_vpn_runner_gate_holds_on_unmeasurable_tunnel.py) and reach
# the breaker the way the product does, through get_cgroup_controller(): no
# injected _cgroup_controller, which nothing in the product sets.

_LOOP_URL = "https://example.test/row-984.mp4"
_TWO_URLS = (_LOOP_URL, "https://example.test/row-984-2.mp4")
_THREE_URLS = _TWO_URLS + ("https://example.test/row-984-3.mp4",)


class _LoopStop:
    """SiteRunner._stop for one worker-loop pass: records each wait in order
    with the URL runs and never sleeps. end_on_wait ends the pass at the first
    wait (a hold); a pass that keeps waiting is ended after five waits."""

    def __init__(self, events, end_on_wait):
        self._event = threading.Event()
        self._events = events
        self._end_on_wait = end_on_wait

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()

    def wait(self, timeout=None):
        self._events.append(("wait", timeout))
        waits = sum(1 for event in self._events if event[0] == "wait")
        if self._end_on_wait or waits >= 5:
            self._event.set()
        return self._event.is_set()


def _worker_loop_pass(monkeypatch, controller, *, end_on_wait=False,
                      during_url=None, urls=(_LOOP_URL,), claim=None,
                      trial_feedback=None, stop_at_global_cap=False):
    """One real _worker_loop pass over `urls` (queued in order) with
    `controller` as the process-wide breaker; it ends once every URL ran.
    Returns (runner, events): events holds ("wait", seconds),
    ("event", kind, message) and ("url", url, circuit). claim: the
    _process_worker_url result (default processed); trial_feedback replaces
    runner._cgroup_trial_feedback; stop_at_global_cap: the runner stops
    while the dequeued URL waits on the global concurrency cap."""
    import contextlib
    import queue

    from bulk_downloader import cgroups_backpressure as cg
    from bulk_downloader import daily_budget as db
    from bulk_downloader import (
        global_config,
        maintenance,
        netns_isolation,
        smart_wakeup,
    )
    from bulk_downloader import runner as runner_mod

    monkeypatch.setattr(cg, "_SHARED_CONTROLLER", controller)
    monkeypatch.setattr(db, "_GLOBAL_BUDGET", 0)
    events = []
    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "row-984"
    runner.config = {"max_concurrent": 1}
    runner.jobs = {url: {"status": "pending"} for url in urls}
    runner.cookies = []
    runner._cookies_updated_at = 0.0
    runner._state = "running"
    runner._worker_heartbeats_lock = threading.Lock()
    runner._worker_heartbeats = {}
    runner._worker_run_generation = 1
    runner._worker_context = threading.local()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._session_ok = threading.Event()
    runner._session_ok.set()
    runner._stop = _LoopStop(events, end_on_wait)
    runner._url_queue = queue.Queue()
    for url in urls:
        runner._url_queue.put((1, url))
    ran = []

    def process_worker_url(
        worker_idx, browser, url, *, persistent_ctx, run_generation
    ):
        events.append(("url", url, controller.circuit_state))
        if during_url is not None:
            during_url()
        ran.append(url)
        if len(ran) >= len(urls):
            runner._stop.set()
        return claim or runner_mod.SiteRunner._WORKER_CLAIM_PROCESSED

    class _StopAtGlobalCap:
        """runner._global_sem stand-in: the runner stops while the URL waits."""

        def acquire(self, timeout=None):
            runner._stop.set()
            return False

        def release(self):
            raise AssertionError("row984: released a cap never acquired")

    def queue_ran_dry():
        runner._stop.set()  # the queue ran dry: end the pass

    runner._launch_browser = (
        lambda *, worker_idx, netns: (None, None, None, "row-984-test"))
    runner._effective_concurrency = lambda: 1
    runner._generation_item_is_processable = lambda generation, url: True
    runner._try_steal_job = queue_ran_dry
    runner._process_worker_url = process_worker_url
    runner._maybe_drift_recover = lambda: None
    if trial_feedback is not None:
        runner._cgroup_trial_feedback = trial_feedback
    runner.log_event = (
        lambda kind, message, **_kw: events.append(("event", kind, message)))

    monkeypatch.setattr(runner_mod, "_VPN_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(runner_mod.vpn_runtime, "maybe_wait_for_vpn",
                        lambda site_id, *, timeout: True)
    monkeypatch.setattr(runner_mod, "_global_sem",
                        _StopAtGlobalCap() if stop_at_global_cap else None)
    monkeypatch.setattr(netns_isolation, "capture_netns",
                        lambda *_args, **_kwargs: contextlib.nullcontext(None))
    monkeypatch.setattr(maintenance, "is_action_paused", lambda action: False)
    monkeypatch.setattr(smart_wakeup, "should_wake_now",
                        lambda **_kwargs: {"wake": True})
    monkeypatch.setattr(global_config, "get_config", dict)

    runner._worker_loop(worker_idx=0, run_generation=1)
    return runner, events


def test_worker_loop_holds_the_url_while_the_breaker_is_open(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, _reader, _clock = _controller(monkeypatch, 0.97)
    ctl.trip("test")
    runner, events = _worker_loop_pass(monkeypatch, ctl, end_on_wait=True)
    assert [e for e in events if e[0] == "url"] == [], (
        f"row984: the worker loop ran a URL while the process-wide cgroup "
        f"breaker was OPEN (events {events!r}) -- its intake never consulted "
        f"get_cgroup_controller()")
    assert [e[:2] for e in events] == [
        ("event", "resource_admission_hold"),
        ("wait", ctl.config.cooldown_seconds)]
    assert "cooldown" in events[0][2]
    assert runner._state == "cgroup_backpressure"
    assert runner._url_queue.qsize() == 1, "the held URL is requeued, not lost"
    assert ctl.circuit_state == CircuitState.OPEN


def test_worker_loop_waits_the_watermark_delay_before_the_url_runs(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, _reader, _clock = _controller(monkeypatch, 0.875)
    _runner, events = _worker_loop_pass(monkeypatch, ctl)
    assert [e[0] for e in events] == ["wait", "url"], (
        f"row984: at 87.5% of memory.high (mid-ramp) the worker loop did not "
        f"slow the intake before the URL ran (events {events!r}) -- the "
        f"watermark delay was computed and never waited")
    assert events[0][1] == pytest.approx(1.0)
    assert events[1] == ("url", _LOOP_URL, CircuitState.CLOSED)


@pytest.mark.parametrize(
    ("ratio_after_url", "expected"),
    ((0.30, "CLOSED"), (0.97, "OPEN")),
    ids=("recovered", "still_critical"),
)
def test_worker_loop_reports_the_half_open_trial_outcome(
    monkeypatch, ratio_after_url, expected
):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, reader, clock = _controller(monkeypatch, 0.97)
    ctl.trip("test")
    clock.now += ctl.config.cooldown_seconds + 1
    reader.ratio = 0.30  # recovered: the next admission is the HALF_OPEN trial

    def the_url_moves_memory():
        reader.ratio = ratio_after_url

    _runner, events = _worker_loop_pass(
        monkeypatch, ctl, during_url=the_url_moves_memory)
    assert events == [("url", _LOOP_URL, CircuitState.HALF_OPEN)], (
        f"the dequeued URL must run, undelayed, as the HALF_OPEN trial "
        f"(events {events!r})")
    assert ctl.circuit_state == CircuitState(expected), (
        f"row984: the HALF_OPEN trial URL left memory at "
        f"{ratio_after_url:.0%} and the worker loop left the breaker "
        f"{ctl.circuit_state.value} -- the loop never reported the trial's "
        f"outcome")


# VERIFY-r1 HIGH-1: usage_ratio counted reclaimable page cache. A downloader
# fills its cgroup with the page cache of the files it writes; at memory.max
# that cache alone tripped the breaker, and a held intake makes the kernel
# reclaim none of it, so the breaker stayed OPEN (measured in real cgroups).
# The breaker measures the working set: memory.current less memory.stat
# inactive_file. These go through the real reader over a cgroup directory.

def _cgroup_files(path, *, current, inactive_file, limit=1000):
    """A cgroup v2 directory: memory.max `limit`, memory.current `current`
    of which `inactive_file` is clean page cache (memory.stat)."""
    path.mkdir(exist_ok=True)
    (path / "memory.max").write_text(f"{limit}\n", encoding="utf-8")
    (path / "memory.current").write_text(f"{current}\n", encoding="utf-8")
    anon = current - inactive_file
    (path / "memory.stat").write_text(
        f"anon {anon}\nfile {inactive_file}\ninactive_anon 0\n"
        f"active_anon {anon}\ninactive_file {inactive_file}\nactive_file 0\n",
        encoding="utf-8")
    return path


def test_working_set_discounts_inactive_file_cache(tmp_path: Path):
    from bulk_downloader.cgroups_backpressure import CgroupV2Reader
    cgroup = _cgroup_files(tmp_path / "cg", current=1000, inactive_file=850)
    metrics = CgroupV2Reader(cgroup_path=cgroup).read_metrics()
    assert metrics.current_bytes == 1000 and metrics.inactive_file_bytes == 850
    assert metrics.working_set_bytes == 150
    assert metrics.usage_ratio == pytest.approx(0.15), (
        "row984: usage_ratio counted clean page cache (memory.stat "
        "inactive_file) as memory pressure")
    # cache counted after memory.current was read may exceed it: clamp at 0
    (cgroup / "memory.stat").write_text("inactive_file 1200\n", encoding="utf-8")
    racing = CgroupV2Reader(cgroup_path=cgroup).read_metrics()
    assert racing.working_set_bytes == 0 and racing.usage_ratio == 0.0
    # no readable inactive_file: nothing is discounted
    (cgroup / "memory.stat").write_text("inactive_file lots\n", encoding="utf-8")
    assert CgroupV2Reader(cgroup_path=cgroup).read_metrics().usage_ratio == 1.0
    (cgroup / "memory.stat").unlink()
    assert CgroupV2Reader(cgroup_path=cgroup).read_metrics().usage_ratio == 1.0


def test_worker_loop_page_cache_at_the_limit_neither_slows_nor_trips(
    monkeypatch, tmp_path
):
    from bulk_downloader import cgroups_backpressure as cg
    cgroup = _cgroup_files(tmp_path / "cg", current=999, inactive_file=900)
    ctl = cg.CgroupV2BackpressureController(
        config=cg.BackpressureConfig(cgroup_path=str(cgroup)))
    _runner, events = _worker_loop_pass(monkeypatch, ctl, urls=_THREE_URLS)
    assert events == [("url", url, cg.CircuitState.CLOSED) for url in _THREE_URLS], (
        f"row984: memory.current at memory.max, 90% of it clean page cache, "
        f"slowed or tripped the intake (events {events!r}) -- page cache is "
        f"not memory pressure")
    assert ctl.circuit_state == cg.CircuitState.CLOSED


def test_worker_loop_open_breaker_recovers_while_page_cache_stays(
    monkeypatch, tmp_path
):
    from bulk_downloader import cgroups_backpressure as cg
    clock = _Clock()
    monkeypatch.setattr(cg, "time", clock)
    cgroup = _cgroup_files(tmp_path / "cg", current=970, inactive_file=0)
    ctl = cg.CgroupV2BackpressureController(
        config=cg.BackpressureConfig(cgroup_path=str(cgroup)))
    _runner, events = _worker_loop_pass(monkeypatch, ctl, urls=_TWO_URLS)
    assert ctl.circuit_state == cg.CircuitState.OPEN, (
        f"a 97% working set trips the intake (events {events!r})")
    # The working set is freed; the page cache it left behind stays, as the
    # held intake never makes the kernel reclaim it.
    _cgroup_files(cgroup, current=970, inactive_file=900)
    clock.now += ctl.config.cooldown_seconds + 1
    _runner, events = _worker_loop_pass(monkeypatch, ctl)
    assert events == [("url", _LOOP_URL, cg.CircuitState.HALF_OPEN)], (
        f"row984: the working set fell to 7% of memory.max, page cache kept "
        f"memory.current at 97%, and the OPEN breaker never re-admitted "
        f"(events {events!r})")
    assert ctl.circuit_state == cg.CircuitState.CLOSED


# VERIFY-r1 LOW-1: the per-URL finally called _cgroup_trial_feedback
# unguarded, and its except-handler called record_feedback again: a raise
# from either ended the worker thread ("worker_loop fatal").

def test_worker_loop_survives_a_raising_trial_feedback(monkeypatch, capsys):
    ctl, _reader, _clock = _controller(monkeypatch, 0.30)

    def trial_feedback(*_args, **_kwargs):
        raise RuntimeError("row984: trial feedback blew up")

    _runner, events = _worker_loop_pass(
        monkeypatch, ctl, urls=_TWO_URLS, trial_feedback=trial_feedback)
    err = capsys.readouterr().err
    assert [e[1] for e in events if e[0] == "url"] == list(_TWO_URLS), (
        f"row984: a raising trial feedback ended the worker after its first "
        f"URL (events {events!r}; stderr {err!r})")
    assert "worker_loop fatal" not in err


def test_worker_loop_survives_a_breaker_whose_feedback_raises(
    monkeypatch, capsys
):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, reader, clock = _controller(monkeypatch, 0.97, half_open_trial_permits=2)
    ctl.trip("test")
    clock.now += ctl.config.cooldown_seconds + 1
    reader.ratio = 0.30

    def record_feedback(*_args, **_kwargs):
        raise RuntimeError("row984: breaker refused the report")

    monkeypatch.setattr(ctl, "record_feedback", record_feedback)
    _runner, events = _worker_loop_pass(monkeypatch, ctl, urls=_TWO_URLS)
    err = capsys.readouterr().err
    assert events == [("url", url, CircuitState.HALF_OPEN) for url in _TWO_URLS], (
        f"row984: a breaker whose record_feedback raises ended the worker "
        f"(events {events!r}; stderr {err!r})")
    assert "worker_loop fatal" not in err
    assert "cgroup trial feedback failed" not in err, (
        "row984: _cgroup_trial_feedback let the breaker's raise escape")


# VERIFY-r1 LOW-2: the breaker is process-wide, and HALF_OPEN was settled by
# whichever URL finished first -- a URL admitted before the trip, or a claim
# that ran nothing. Only the URL admitted as the trial settles it now.

def test_worker_loop_only_the_trial_url_settles_half_open(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, _reader, clock = _controller(monkeypatch, 0.30)
    taken = []

    def another_intake_trips_and_takes_the_trial():
        ctl.trip("another intake saw the critical watermark")
        clock.now += ctl.config.cooldown_seconds + 1
        taken.append(ctl.check_admission())

    _runner, events = _worker_loop_pass(
        monkeypatch, ctl, during_url=another_intake_trips_and_takes_the_trial)
    assert events == [("url", _LOOP_URL, CircuitState.CLOSED)]
    assert taken == [(True, "Admitted for HALF_OPEN trial probe")]
    assert ctl.circuit_state == CircuitState.HALF_OPEN, (
        f"row984: a URL admitted before the trip settled the HALF_OPEN trial "
        f"another intake is running (breaker {ctl.circuit_state.value})")


@pytest.mark.parametrize("claim", ("stale", "ineligible"))
def test_worker_loop_trial_whose_claim_never_ran_hands_the_permit_back(
    monkeypatch, claim
):
    from bulk_downloader.cgroups_backpressure import CircuitState
    from bulk_downloader.runner import SiteRunner
    ctl, reader, clock = _controller(monkeypatch, 0.97)
    ctl.trip("test")
    clock.now += ctl.config.cooldown_seconds + 1
    reader.ratio = 0.30
    _runner, events = _worker_loop_pass(
        monkeypatch, ctl,
        claim=getattr(SiteRunner, f"_WORKER_CLAIM_{claim.upper()}"))
    assert events == [("url", _LOOP_URL, CircuitState.HALF_OPEN)]
    assert ctl.circuit_state == CircuitState.HALF_OPEN, (
        f"row984: a {claim} claim settled the HALF_OPEN trial it never ran")
    assert ctl.check_admission() == (True, "Admitted for HALF_OPEN probe permit"), (
        "row984: the trial that never ran kept its permit; the next URL "
        "waits out the trial timeout")


@pytest.mark.parametrize("urls", ((_LOOP_URL,), _TWO_URLS),
                         ids=("single_url", "two_urls"))
def test_worker_loop_trial_stopped_before_it_ran_hands_the_permit_back(
    monkeypatch, urls,
):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, reader, clock = _controller(monkeypatch, 0.97)
    ctl.trip("test")
    clock.now += ctl.config.cooldown_seconds + 1
    reader.ratio = 0.30
    _runner, events = _worker_loop_pass(
        monkeypatch, ctl, urls=urls, stop_at_global_cap=True)
    assert [e for e in events if e[0] == "url"] == []
    assert _runner._url_queue.unfinished_tasks == len(urls) - 1, (
        "row984: stopping at the global cap completed more than the "
        "dequeued trial URL")
    assert ctl.circuit_state == CircuitState.HALF_OPEN, (
        "row984: a trial URL stopped at the global cap, before it ran, "
        "settled the breaker")
    assert ctl.check_admission() == (True, "Admitted for HALF_OPEN probe permit")


def test_trial_ticket_of_an_expired_half_open_episode_is_ignored(monkeypatch):
    from bulk_downloader.cgroups_backpressure import CircuitState
    ctl, _reader, clock = _controller(monkeypatch, 0.30)
    assert ctl.check_admission_with_trial() == (True, "Admission granted", None)
    ctl.trip("test")
    clock.now += ctl.config.cooldown_seconds + 1
    admitted, _reason, expired = ctl.check_admission_with_trial()
    assert admitted and expired is not None
    clock.now += ctl.config.half_open_trial_timeout_seconds + 1
    admitted, _reason, current = ctl.check_admission_with_trial()
    assert admitted and current not in (None, expired), (
        "a timed-out trial re-samples into a new HALF_OPEN episode")
    ctl.record_feedback(True, trial=expired)
    ctl.release_trial(expired)
    assert ctl.circuit_state == CircuitState.HALF_OPEN, (
        "row984: a timed-out trial's report settled the next episode")
    assert ctl.check_admission()[0] is False, (
        "row984: a timed-out trial handed back the current trial's permit")
    ctl.record_feedback(False, "still high", trial=current)
    assert ctl.circuit_state == CircuitState.OPEN
