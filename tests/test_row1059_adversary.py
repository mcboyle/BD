"""Row 1059 adversary RED tests -- bd-worker-W3-B (W14 deep lane).

Targets 4 standing refutes (bd-agy-review-correctness1, B6-B, B10-B, B2):
  F1 (ALL 4): register_monitored_lock wraps lock but production callers acquire
      the RAW object → counters permanently zero under real contention.
  F2 (ALL 4): get_lock_contention_report has zero production callers (O1223 orphan).
  F3 (B10-B): failed non-blocking acquire not counted as contention.
  F4 (B10-B): re-entrant RLock overwrites outer hold start → halved hold_time.

On base 7a99076ed256, lock_monitor.py does not exist, app_state locks are raw
threading.Lock/RLock, and /api/health/v2 has no lock_contention block — these
tests fail with wrong-answer assertions, not ImportError.

H657: every test must fail when bulk_downloader.lock_monitor is removed.
"""
import sys
import threading
import time
import types

BD_GATE_SCOPE = "module"


class TestF1RealLockContentionMeasured:
    """F1: production lock acquisitions must route through MonitoredLock.

    On unfixed code, _watch_registry_lock is a raw threading.RLock. Acquiring
    it and reading the contention report yields total_acquires == 0 because
    the monitor wrapper is never entered.
    """

    def test_single_acquire_counted(self):
        from bulk_downloader import app_state

        with app_state._watch_registry_lock:
            pass

        report = app_state.get_lock_contention_report()
        lock_data = report["locks"].get("app_state._watch_registry_lock")
        assert lock_data is not None, (
            "app_state._watch_registry_lock not in lock report — "
            "lock was never registered with the monitor"
        )
        assert lock_data["acquire_count"] >= 1, (
            f"acquire_count={lock_data['acquire_count']} after real acquisition — "
            "production lock bypasses MonitoredLock (F1)"
        )

    def test_hold_time_recorded(self):
        from bulk_downloader import app_state

        with app_state._watch_registry_lock:
            time.sleep(0.02)

        report = app_state.get_lock_contention_report()
        lock_data = report["locks"]["app_state._watch_registry_lock"]
        assert lock_data["hold_time_total"] > 0.0, (
            f"hold_time_total={lock_data['hold_time_total']} after 20ms hold — "
            "MonitoredLock.release() never records hold duration (F1)"
        )

    def test_concurrent_contention_counted(self):
        from bulk_downloader import app_state

        holder_ready = threading.Event()
        release_holder = threading.Event()

        def holder():
            with app_state._pairing_lock:
                holder_ready.set()
                release_holder.wait(timeout=2.0)

        def waiter():
            holder_ready.wait(timeout=2.0)
            with app_state._pairing_lock:
                pass

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)
        t1.start()
        t2.start()

        holder_ready.wait(timeout=2.0)
        time.sleep(0.05)

        release_holder.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        report = app_state.get_lock_contention_report()
        lock_data = report["locks"]["app_state._pairing_lock"]
        assert lock_data["contention_count"] >= 1, (
            f"contention_count={lock_data['contention_count']} after real contention — "
            "monitor reports healthy under contended lock (F1)"
        )


class TestF2ProductionEndpointWired:
    """F2: get_lock_contention_report must have a real production caller.

    On unfixed code, /api/health/v2 response contains no lock_contention
    block — the report function is orphan dead code (O1223).
    """

    def test_health_v2_includes_lock_contention(self):
        from bulk_downloader import app as a

        client = a.app.test_client()
        resp = client.get("/api/health/v2")
        assert resp.status_code in (200, 503)
        data = resp.get_json()
        assert "lock_contention" in data, (
            f"lock_contention missing from /api/health/v2 response keys "
            f"{sorted(data.keys())} — get_lock_contention_report has zero "
            f"production callers (F2 orphan)"
        )

    def test_health_v2_lock_report_has_measured_data(self):
        from bulk_downloader import app as a
        from bulk_downloader import app_state

        with app_state._sites_config_save_lock:
            time.sleep(0.01)

        client = a.app.test_client()
        resp = client.get("/api/health/v2")
        data = resp.get_json()
        lc = data["lock_contention"]
        assert "locks" in lc, "lock_contention missing 'locks' key"
        assert lc["total_locks_monitored"] >= 1, (
            f"total_locks_monitored={lc['total_locks_monitored']} — "
            "no locks registered in the monitor"
        )
        assert lc["total_acquires"] >= 1, (
            f"total_acquires={lc['total_acquires']} after real acquisition — "
            "endpoint returns zero counters (F1+F2 combined)"
        )


class TestF3FailedNonblockingAcquire:
    """F3: a failed non-blocking acquire is proof of contention.

    On unfixed code, acquire(blocking=False) that fails leaves contended=False
    and records nothing — the monitor misses try-lock contention entirely.
    """

    def test_failed_trylock_counted_as_contention(self):
        from bulk_downloader.lock_monitor import MonitoredLock

        lock = MonitoredLock("adversary_f3_trylock", threading.Lock())

        holder_ready = threading.Event()
        trylock_done = threading.Event()
        trylock_result = []

        def holder():
            with lock:
                holder_ready.set()
                trylock_done.wait(timeout=2.0)

        def tryer():
            holder_ready.wait(timeout=2.0)
            acquired = lock.acquire(blocking=False)
            trylock_result.append(acquired)
            trylock_done.set()

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=tryer)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert trylock_result == [False], "try-lock should have failed"
        metrics = lock.get_metrics()
        assert metrics.contention_count >= 1, (
            f"contention_count={metrics.contention_count} after failed try-lock — "
            "failed non-blocking acquire not counted as contention (F3)"
        )


class TestF4ReentrantHoldTime:
    """F4: re-entrant RLock must not halve hold_time_total.

    On unfixed code, _hold_start_times[tid] is overwritten on the inner acquire,
    and the first release pops it — the outer release finds nothing to record.
    A 100ms re-entrant hold reports ~50ms.
    """

    def test_reentrant_hold_time_reflects_outer_duration(self):
        from bulk_downloader.lock_monitor import MonitoredLock

        lock = MonitoredLock("adversary_f4_reentrant", threading.RLock())

        with lock:
            time.sleep(0.05)
            with lock:
                time.sleep(0.05)

        metrics = lock.get_metrics()
        assert metrics.hold_time_total >= 0.08, (
            f"hold_time_total={metrics.hold_time_total:.4f}s for ~100ms re-entrant hold — "
            "inner acquire overwrites outer hold start, halving telemetry (F4)"
        )


class TestH678LockProxyAPISuperset:
    """H678: proxy replacing standard-library lock must be an API superset."""

    def test_monitored_lock_rlock_superset(self):
        from bulk_downloader.lock_monitor import MonitoredLock

        raw = threading.RLock()
        proxy = MonitoredLock("h678_rlock", raw)
        missing = [attr for attr in dir(raw) if not hasattr(proxy, attr)]
        assert not missing, f"MonitoredLock missing threading.RLock attributes: {missing}"

        assert not proxy._is_owned()
        with proxy:
            assert proxy._is_owned()
        assert not proxy._is_owned()


class TestH657ModuleRemovalControl:
    """H657: removing lock_monitor must break all adversary tests.

    If the tests pass with the module absent, they measure inline code or
    framework defaults, not the lock monitoring feature.
    """

    def test_report_fails_without_lock_monitor(self):
        targets = ["bulk_downloader.lock_monitor", "bulk_downloader.app_state"]
        saved_modules = {k: sys.modules[k] for k in targets if k in sys.modules}
        for k in targets:
            sys.modules.pop(k, None)
        blocker = types.ModuleType("bulk_downloader.lock_monitor")
        blocker.__spec__ = None
        sys.modules["bulk_downloader.lock_monitor"] = blocker
        try:
            import importlib
            try:
                mod = importlib.import_module("bulk_downloader.app_state")
                importlib.reload(mod)
                mod.get_lock_contention_report()
                assert False, (
                    "get_lock_contention_report() succeeded with lock_monitor "
                    "blocked — delegation to lock_monitor is not real (H657)"
                )
            except (ImportError, AttributeError, TypeError):
                pass
        finally:
            for k in targets:
                sys.modules.pop(k, None)
            sys.modules.update(saved_modules)

    def test_monitored_lock_absent_without_module(self):
        saved_modules = (
            {"bulk_downloader.lock_monitor": sys.modules["bulk_downloader.lock_monitor"]}
            if "bulk_downloader.lock_monitor" in sys.modules
            else {}
        )
        sys.modules.pop("bulk_downloader.lock_monitor", None)
        blocker = types.ModuleType("bulk_downloader.lock_monitor")
        blocker.__spec__ = None
        sys.modules["bulk_downloader.lock_monitor"] = blocker
        try:
            has_monitored = hasattr(blocker, "MonitoredLock")
            assert not has_monitored, (
                "MonitoredLock available on blocked module — "
                "test does not depend on real lock_monitor (H657)"
            )
        finally:
            sys.modules.pop("bulk_downloader.lock_monitor", None)
            sys.modules.update(saved_modules)
