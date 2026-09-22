"""Row 1059 ADVERSARY RED tests (W14, bd-worker-W3-B).

Four standing refutes (B2, B7-B, B10-B, B12-B) all converge:
  F1 (HIGH): register_monitored_lock wraps 4 locks in app_state.py but the
      codebase has ~25 distinct threading.Lock/RLock objects across auth_throttle,
      events, feature_flags, cloak, circuit_breaker, session_keeper, etc.
      The 80+ acquisition sites on those raw locks bypass MonitoredLock entirely,
      so the contention report shows confident zeros under real contention.
  F2: The health endpoint wraps get_lock_contention_report in try/except pass,
      silently discarding failures.

These tests exercise contention on locks that are NOT wrapped by MonitoredLock
and assert the report SHOULD capture them. Currently RED: only 4 app_state locks
are wrapped, the rest are invisible to the monitor.

H657 control: removing lock_monitor.py must break these tests.
"""
from __future__ import annotations

import sys
import threading
import types

BD_GATE_SCOPE = "module"


def _contend_on_lock(lock, hold_seconds=0.1):
    """Create real contention: one thread holds, another waits."""
    import time
    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with lock:
            holder_ready.set()
            release.wait(timeout=5.0)

    def waiter():
        holder_ready.wait(timeout=5.0)
        with lock:
            pass

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    holder_ready.wait(timeout=5.0)
    t2.start()
    time.sleep(hold_seconds)
    release.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)


class TestContentionReportCoversAllProductionLocks:
    """F1: contention report must cover ALL production locks, not just 4 in app_state."""

    def test_auth_throttle_lock_appears_in_report(self):
        """auth_throttle._lock has production contention but is not monitored."""
        from bulk_downloader import auth_throttle
        from bulk_downloader.lock_monitor import get_global_contention_report

        _contend_on_lock(auth_throttle._lock)
        report = get_global_contention_report()
        lock_names = set(report["locks"].keys())
        matching = [n for n in lock_names if "auth_throttle" in n]
        assert matching, (
            f"F1: auth_throttle._lock is a real production lock with contention "
            f"but does not appear in the contention report. "
            f"Only {len(lock_names)} locks monitored: {sorted(lock_names)}"
        )

    def test_events_fence_lock_appears_in_report(self):
        """events._fence_lock serializes event dispatch but is not monitored."""
        from bulk_downloader import events
        from bulk_downloader.lock_monitor import get_global_contention_report

        _contend_on_lock(events._fence_lock)
        report = get_global_contention_report()
        lock_names = set(report["locks"].keys())
        matching = [n for n in lock_names if "events" in n or "fence" in n]
        assert matching, (
            f"F1: events._fence_lock is a real production lock with contention "
            f"but does not appear in the contention report. "
            f"Only {len(lock_names)} locks monitored: {sorted(lock_names)}"
        )

    def test_circuit_breaker_lock_appears_in_report(self):
        """circuit_breaker._lock guards state transitions but is not monitored."""
        from bulk_downloader import circuit_breaker
        from bulk_downloader.lock_monitor import get_global_contention_report

        _contend_on_lock(circuit_breaker._lock)
        report = get_global_contention_report()
        lock_names = set(report["locks"].keys())
        matching = [n for n in lock_names if "circuit" in n]
        assert matching, (
            f"F1: circuit_breaker._lock is a real production lock with contention "
            f"but does not appear in the contention report. "
            f"Only {len(lock_names)} locks monitored: {sorted(lock_names)}"
        )

    def test_report_covers_minimum_lock_population(self):
        """The codebase has 20+ distinct lock objects; report must cover most of them."""
        from bulk_downloader.lock_monitor import get_global_contention_report

        report = get_global_contention_report()
        monitored = report["total_locks_monitored"]
        assert monitored >= 10, (
            f"F1: only {monitored} locks monitored but the codebase has 20+ "
            f"distinct threading.Lock/RLock objects. Monitored: "
            f"{sorted(report['locks'].keys())}"
        )


class TestHealthEndpointReportsContention:
    """F2: api_health_v2 must expose lock_contention with real data."""

    def test_health_v2_lock_contention_present(self):
        """The lock_contention key must appear in api_health_v2."""
        import flask

        from bulk_downloader.app_health import register_routes

        app = flask.Flask(__name__)
        app.config["TESTING"] = True
        register_routes(app)
        with app.test_client() as client:
            resp = client.get("/api/health/v2")
            assert resp.status_code in (200, 503)
            data = resp.get_json()
            lc = data.get("lock_contention")
            assert lc is not None, (
                "F2: api_health_v2 response lacks lock_contention key — "
                "get_lock_contention_report not wired or try/except swallowed it"
            )
            assert "total_locks_monitored" in lc, (
                f"F2: lock_contention missing total_locks_monitored: {lc}"
            )


class TestLockMonitorModuleIsRequired:
    """H657 negative control: removing lock_monitor must break the import chain."""

    def test_app_state_fails_without_lock_monitor(self):
        """app_state.py imports from lock_monitor at module scope; blocking it must fail."""
        targets = ["bulk_downloader.lock_monitor", "bulk_downloader.app_state"]
        saved_modules = {k: sys.modules[k] for k in targets if k in sys.modules}
        for k in targets:
            sys.modules.pop(k, None)

        blocker = types.ModuleType("bulk_downloader.lock_monitor")
        blocker.__spec__ = None
        sys.modules["bulk_downloader.lock_monitor"] = blocker
        try:
            import importlib
            from importlib import reload
            try:
                mod = importlib.import_module("bulk_downloader.app_state")
                reload(mod)
            except (ImportError, AttributeError):
                pass
            else:
                report_fn = getattr(mod, "get_lock_contention_report", None)
                if report_fn is not None:
                    try:
                        result = report_fn()
                    except (ImportError, AttributeError, TypeError):
                        pass
                    else:
                        assert result is None or result.get("total_locks_monitored", 0) == 0, (
                            "H657: lock_monitor module is blocked but "
                            "get_lock_contention_report still returns monitored locks"
                        )
        finally:
            for k in targets:
                sys.modules.pop(k, None)
            sys.modules.update(saved_modules)
