"""Row 1029: Generational Garbage Collection Tuning and Dynamic Cycle Collection Pauser (AdaptiveGCController).

Validates generational GC threshold tuning, critical-section cycle collection pausing,
measured (overhead-driven) adaptation, the reference-counted workload lease and its
deferred full collection, and the product caller: every download worker holds a lease.

RED on baseline: fails with explicit AssertionError (capability missing), not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import contextlib
import gc
import types
import weakref

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import adaptive_gc
except ImportError:
    adaptive_gc = None


@pytest.fixture(autouse=True)
def _gc_state_restored():
    """The collector is process-global: every test leaves it as it found it."""
    thresholds, enabled, callbacks = gc.get_threshold(), gc.isenabled(), list(gc.callbacks)
    yield
    gc.set_threshold(*thresholds)
    gc.callbacks[:] = callbacks
    if enabled:
        gc.enable()
    else:
        gc.disable()


def _require(name):
    assert adaptive_gc is not None, (
        "Row 1029 capability missing: Generational Garbage Collection Tuning and "
        "Dynamic Cycle Collection Pauser (AdaptiveGCController) not implemented in bulk_downloader.adaptive_gc"
    )
    assert hasattr(adaptive_gc, name), f"Row 1029 component missing: bulk_downloader.adaptive_gc.{name}"
    return getattr(adaptive_gc, name)


class _Cycle:
    def __init__(self):
        self.me = self


def test_positive_control_force_gc_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader.dev_suite import introspection

    assert hasattr(introspection, "force_gc")
    assert callable(introspection.force_gc)
    res = introspection.force_gc()
    assert res["ok"] is True
    assert "unreachable_collected" in res


def test_adaptive_gc_capability_implemented():
    """RED assertion 1: capability must be implemented with semantic AssertionError on base."""
    _require("AdaptiveGCController")


def test_gc_threshold_tuning_and_restore():
    """Verify threshold tuning for high-throughput mode and clean restoration."""
    AdaptiveGCController = _require("AdaptiveGCController")

    ctrl = AdaptiveGCController()
    orig = ctrl.get_thresholds()

    try:
        tuned = ctrl.tune_for_workload("high_throughput")
        current = ctrl.get_thresholds()
        assert current == tuned
        # High throughput increases gen0 threshold to delay minor collections
        assert current[0] > orig[0]
    finally:
        ctrl.restore_defaults()
        assert ctrl.get_thresholds() == orig


def test_dynamic_cycle_collection_pause():
    """Verify pausing cycle collection during critical sections and auto-resuming."""
    AdaptiveGCController = _require("AdaptiveGCController")

    ctrl = AdaptiveGCController()
    gc.enable()
    assert gc.isenabled() is True

    with ctrl.critical_section_gc_paused(reason="high_speed_stream"):
        assert ctrl.is_paused is True
        assert gc.isenabled() is False

    assert ctrl.is_paused is False
    assert gc.isenabled() is True


def test_collect_adaptive_really_collects():
    """collect_adaptive must run the collection it reports, not just time it."""
    AdaptiveGCController = _require("AdaptiveGCController")

    ctrl = AdaptiveGCController()
    gc.disable()  # nothing but collect_adaptive may reclaim the cycle
    ref = weakref.ref(_Cycle())
    assert ref() is not None
    res = ctrl.collect_adaptive(generation=2)
    assert ref() is None, f"row1029: collect_adaptive left an unreachable cycle alive ({res!r})"
    assert res["ok"] is True and res["generation"] == 2
    assert res["collected_count"] >= 1
    assert res["duration_ms"] >= 0.0


def test_policy_follows_measured_overhead():
    """E2: the threshold decision is a function of measured collector overhead."""
    decide = _require("decide_gen0")
    cfg = _require("AdaptiveGCConfig")(target_overhead=0.02, step=1.5, max_gen0=5000)

    assert decide(0.10, 1.0, 1000, 700, cfg) == (1500, "raise")    # 10% > 2%
    assert decide(0.10, 1.0, 4000, 700, cfg) == (5000, "raise")    # capped
    assert decide(0.10, 1.0, 5000, 700, cfg) == (5000, "hold")     # at cap
    assert decide(0.001, 1.0, 1500, 700, cfg) == (1000, "lower")   # 0.1% < 0.5%
    assert decide(0.001, 1.0, 800, 700, cfg) == (700, "lower")     # floored at baseline
    assert decide(0.001, 1.0, 700, 700, cfg) == (700, "hold")
    assert decide(0.01, 1.0, 1500, 700, cfg) == (1500, "hold")     # inside the band
    assert decide(0.5, 0.0, 1500, 700, cfg) == (1500, "hold")      # no window, no decision


def test_lease_measures_real_collections_and_adapts_from_them():
    """E2: the monitor sees the collector's own start/stop events and the
    measured window, not a caller-supplied number, drives the threshold."""
    AdaptiveGCController = _require("AdaptiveGCController")
    AdaptiveGCConfig = _require("AdaptiveGCConfig")
    assert hasattr(AdaptiveGCController, "begin_workload"), (
        "row1029: no workload lease -- nothing measures the collector")

    gc.set_threshold(700, 10, 10)
    # Any measured overhead is over a zero target, and every window is due.
    ctrl = AdaptiveGCController(AdaptiveGCConfig(target_overhead=0.0, adapt_interval_s=0.0))
    assert ctrl.begin_workload("t") is True
    try:
        tuned = gc.get_threshold()[0]
        assert tuned > 700
        for _ in range(3):
            gc.collect(0)
        stats = ctrl.stats()
        assert stats["collections"][0] >= 3 and stats["gc_seconds"] > 0, stats
        assert gc.get_threshold()[0] > tuned, (
            f"row1029: measured collections did not move the threshold ({stats!r})")
        assert stats["adaptations"] and stats["adaptations"][0][0] == "raise"
    finally:
        ctrl.end_workload("t")
    assert gc.get_threshold() == (700, 10, 10)
    assert ctrl._on_gc not in gc.callbacks

    # The same measurement lowers a raised threshold when overhead is negligible.
    ctrl = AdaptiveGCController(AdaptiveGCConfig(target_overhead=1e9, adapt_interval_s=0.0))
    ctrl.begin_workload("t")
    try:
        before = gc.get_threshold()[0]
        gc.collect(0)
        assert gc.get_threshold()[0] < before
    finally:
        ctrl.end_workload("t")


def test_last_lease_restores_and_pays_the_deferred_collection():
    AdaptiveGCController = _require("AdaptiveGCController")
    assert hasattr(AdaptiveGCController, "begin_workload"), "row1029: no workload lease"

    gc.set_threshold(700, 10, 10)
    ctrl = AdaptiveGCController()
    ctrl.begin_workload("site-a/0")
    ctrl.begin_workload("site-b/0")
    tuned = gc.get_threshold()
    assert tuned[0] > 700 and ctrl.active_workloads == 2
    gc.disable()  # only the lease's own collection may reclaim the cycle
    ref = weakref.ref(_Cycle())

    assert ctrl.end_workload("site-a/0") is None
    assert gc.get_threshold() == tuned, "one runner finishing must not untune the others"
    assert ref() is not None

    report = ctrl.end_workload("site-b/0")
    assert ctrl.active_workloads == 0
    assert gc.get_threshold() == (700, 10, 10)
    assert ref() is None, f"row1029: the deferred full collection never ran ({report!r})"
    assert report["generation"] == 2
    assert ctrl.end_workload("site-b/0") is None  # releasing twice is a no-op


def test_disabled_controller_takes_no_lease():
    AdaptiveGCController = _require("AdaptiveGCController")
    AdaptiveGCConfig = _require("AdaptiveGCConfig")
    assert hasattr(AdaptiveGCController, "workload"), "row1029: no workload lease"
    before = gc.get_threshold()
    ctrl = AdaptiveGCController(AdaptiveGCConfig(enabled=False))
    with ctrl.workload("x"):
        assert gc.get_threshold() == before and ctrl.active_workloads == 0


def test_download_worker_holds_a_lease_for_its_lifetime(monkeypatch):
    """E1: the product caller. A download worker tunes the collector while it
    runs and releases it when it exits, even when its browser fails."""
    AdaptiveGCController = _require("AdaptiveGCController")
    from bulk_downloader import runner

    gc.set_threshold(700, 10, 10)
    ctrl = AdaptiveGCController()
    monkeypatch.setattr(adaptive_gc, "_GLOBAL_CONTROLLER", ctrl)
    monkeypatch.setattr(runner, "_VPN_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(runner.netns_isolation, "capture_netns",
                        lambda *a, **k: contextlib.nullcontext(None))
    seen = {}

    def launch_browser(worker_idx, netns):
        seen["workloads"] = getattr(ctrl, "active_workloads", 0)
        seen["gen0"] = gc.get_threshold()[0]
        raise RuntimeError("browser launch failed (test)")

    worker = types.SimpleNamespace(site_id="site-gc", config={},
                                   _launch_browser=launch_browser)
    runner.SiteRunner._worker_loop(worker, 0, 1)

    assert seen, "the worker never reached browser launch"
    assert seen["workloads"] == 1 and seen["gen0"] > 700, (
        f"row1029: a download worker ran without an adaptive GC lease ({seen!r})")
    assert ctrl.active_workloads == 0
    assert gc.get_threshold() == (700, 10, 10)
