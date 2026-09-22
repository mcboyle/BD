"""Tests for Row 1059: Distributed Mutex Contention & Lock Queue Monitor.

Validates mutex lock contention tracking, waiter queue depth monitoring,
hold duration latency measurement, reentrancy depth tracking, try-lock contention,
and non-intrusive runtime integration through app_state and /api/health/v2.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import threading
import time
import unittest


class TestRow1059MutexContentionLockMonitor(unittest.TestCase):
    """Test suite for Row 1059 mutex contention and lock queue monitor."""

    def test_positive_control(self) -> None:
        """Positive control: verify existing app_state capabilities exist."""
        from bulk_downloader import app_state
        self.assertTrue(callable(getattr(app_state, "site_lifecycle_lock", None)))

    def test_row1059_capability_present(self) -> None:
        """RED test: verify row 1059 capability is exposed on bulk_downloader.app_state."""
        from bulk_downloader import app_state
        self.assertTrue(
            hasattr(app_state, "get_lock_contention_report"),
            "Row 1059 capability missing: bulk_downloader.app_state has no get_lock_contention_report",
        )

    def test_uncontended_lock_metrics(self) -> None:
        """Verify lock metrics recorded accurately in uncontended single-thread path."""
        from bulk_downloader.lock_monitor import MonitoredLock

        raw_lock = threading.Lock()
        lock = MonitoredLock("test_uncontended", raw_lock)

        with lock:
            self.assertTrue(lock.locked())
            time.sleep(0.01)

        self.assertFalse(lock.locked())
        metrics = lock.get_metrics()
        self.assertEqual(metrics.acquire_count, 1)
        self.assertEqual(metrics.contention_count, 0)
        self.assertGreater(metrics.hold_time_total, 0.0)
        self.assertEqual(metrics.current_waiters, 0)

    def test_contended_lock_metrics_and_queue_depth(self) -> None:
        """Verify contention count, wait time, and waiter queue depth under multi-thread contention (M4 kill)."""
        from bulk_downloader.lock_monitor import MonitoredLock

        raw_lock = threading.Lock()
        lock = MonitoredLock("test_contended", raw_lock)

        holder_started = threading.Event()
        waiter_attempted = threading.Event()
        release_holder = threading.Event()

        def holder():
            with lock:
                holder_started.set()
                release_holder.wait(timeout=2.0)

        def waiter():
            holder_started.wait(timeout=2.0)
            waiter_attempted.set()
            with lock:
                pass

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=waiter)

        t1.start()
        t2.start()

        waiter_attempted.wait(timeout=2.0)
        time.sleep(0.05)  # Allow waiter to block on lock

        # While t1 holds and t2 waits, current_waiters must be at least 1 (M4 killer)
        self.assertGreaterEqual(lock.get_metrics().current_waiters, 1)

        release_holder.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        metrics = lock.get_metrics()
        self.assertEqual(metrics.acquire_count, 2)
        self.assertGreaterEqual(metrics.contention_count, 1)
        self.assertGreater(metrics.wait_time_total, 0.0)
        self.assertEqual(metrics.current_waiters, 0)

    def test_failed_nonblocking_acquire_is_contention(self) -> None:
        """Verify failed non-blocking acquire is counted as contention (F3 / PROBE-B)."""
        from bulk_downloader.lock_monitor import MonitoredLock

        raw_lock = threading.Lock()
        lock = MonitoredLock("test_nonblocking_contention", raw_lock)

        with lock:
            # While held, a non-blocking try-lock attempt must fail and record contention
            acquired = lock.acquire(blocking=False)
            self.assertFalse(acquired)

        metrics = lock.get_metrics()
        self.assertEqual(metrics.acquire_count, 1)
        self.assertEqual(metrics.contention_count, 1)

    def test_rlock_reentrancy_hold_time_tracking(self) -> None:
        """Verify reentrant RLock tracks hold time across reentrancy depth without halving (F4 / PROBE-C)."""
        from bulk_downloader.lock_monitor import MonitoredLock

        raw_rlock = threading.RLock()
        lock = MonitoredLock("test_rlock", raw_rlock)

        with lock:
            time.sleep(0.05)
            with lock:
                time.sleep(0.05)

        metrics = lock.get_metrics()
        self.assertEqual(metrics.acquire_count, 2)
        self.assertEqual(metrics.contention_count, 0)
        # Total hold time across the re-entrant block must be ~0.10s, not halved to ~0.05s
        self.assertGreaterEqual(metrics.hold_time_total, 0.08)

    def test_release_from_non_holder_refused(self) -> None:
        """Verify release() by a non-holder thread is refused with RuntimeError (F5 / PROBE-E)."""
        from bulk_downloader.lock_monitor import MonitoredLock

        raw_lock = threading.Lock()
        lock = MonitoredLock("test_cross_thread_release", raw_lock)

        holder_ready = threading.Event()
        release_done = threading.Event()
        non_holder_error = []

        def holder():
            with lock:
                holder_ready.set()
                release_done.wait(timeout=2.0)

        def non_holder():
            holder_ready.wait(timeout=2.0)
            # Try acquire with small timeout -> fails
            acq = lock.acquire(blocking=True, timeout=0.01)
            self.assertFalse(acq)
            # Attempt release without holding lock -> must raise RuntimeError
            try:
                lock.release()
            except RuntimeError as e:
                non_holder_error.append(str(e))
            finally:
                release_done.set()

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=non_holder)

        t1.start()
        t2.start()

        t2.join(timeout=2.0)
        t1.join(timeout=2.0)

        self.assertEqual(len(non_holder_error), 1)
        self.assertIn("cannot release un-acquired lock", non_holder_error[0])
        self.assertFalse(lock.locked())

    def test_registry_register_idempotence(self) -> None:
        """Verify register() preserves existing MonitoredLock instance and accumulated metrics (M6)."""
        from bulk_downloader.lock_monitor import register_monitored_lock

        lock_1 = register_monitored_lock("test_idempotence_lock")
        with lock_1:
            pass
        self.assertEqual(lock_1.get_metrics().acquire_count, 1)

        # Re-registering must return the same instance and preserve counters
        lock_2 = register_monitored_lock("test_idempotence_lock")
        self.assertIs(lock_1, lock_2)
        self.assertEqual(lock_2.get_metrics().acquire_count, 1)

    def test_app_state_real_lock_acquisition_telemetry(self) -> None:
        """Verify real acquisitions on app_state locks record in telemetry report (F1 / PROBE-A / M5)."""
        from bulk_downloader import app_state

        # Hammer real _watch_registry_lock
        with app_state._watch_registry_lock:
            time.sleep(0.01)

        report = app_state.get_lock_contention_report()
        self.assertIsInstance(report, dict)
        self.assertIn("locks", report)
        self.assertIn("app_state._watch_registry_lock", report["locks"])

        lock_metrics = report["locks"]["app_state._watch_registry_lock"]
        # Must assert numeric counts > 0, not just key existence (M5 kill)
        self.assertGreaterEqual(lock_metrics["acquire_count"], 1)
        self.assertGreater(lock_metrics["hold_time_total"], 0.0)
        self.assertGreaterEqual(report["total_acquires"], 1)

    def test_app_state_concurrent_contention_telemetry(self) -> None:
        """Verify concurrent multi-threaded contention on app_state lock is measured (PROBE-A)."""
        from bulk_downloader import app_state

        stop_event = threading.Event()

        def hammer():
            while not stop_event.is_set():
                with app_state._watch_registry_lock:
                    time.sleep(0.002)

        t1 = threading.Thread(target=hammer)
        t2 = threading.Thread(target=hammer)

        t1.start()
        t2.start()

        time.sleep(0.06)
        stop_event.set()

        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        report = app_state.get_lock_contention_report()
        lock_metrics = report["locks"]["app_state._watch_registry_lock"]
        self.assertGreaterEqual(lock_metrics["acquire_count"], 2)
        self.assertGreaterEqual(lock_metrics["contention_count"], 1)
        self.assertGreater(lock_metrics["wait_time_total"], 0.0)

    def test_app_health_v2_caller_integration(self) -> None:
        """Verify get_lock_contention_report is consumed by /api/health/v2 production endpoint (F2)."""
        from bulk_downloader import app as a

        client = a.app.test_client()
        resp = client.get("/api/health/v2")
        self.assertIn(resp.status_code, (200, 503))

        data = resp.get_json()
        self.assertIsInstance(data, dict)
        self.assertIn("lock_contention", data)
        self.assertIn("locks", data["lock_contention"])
        self.assertIn("app_state._watch_registry_lock", data["lock_contention"]["locks"])
        self.assertIn("total_acquires", data["lock_contention"])

    def test_monitored_lock_api_superset_of_raw_rlock(self) -> None:
        """H678 RED node: MonitoredLock must be an API superset of threading.RLock.

        Verifies that MonitoredLock answers every attribute from runtime
        dir(threading.RLock()), delegating RLock attributes like _is_owned()
        which is required by secrets_family tests.
        """
        from bulk_downloader.lock_monitor import MonitoredLock

        raw_rlock = threading.RLock()
        monitored = MonitoredLock("test_rlock_superset", raw_rlock)

        raw_attrs = dir(raw_rlock)
        monitored_dir = dir(monitored)

        for attr in raw_attrs:
            if not attr.startswith("__"):
                self.assertIn(
                    attr,
                    monitored_dir,
                    f"MonitoredLock missing attribute {attr!r} from dir(threading.RLock())",
                )
                self.assertTrue(
                    hasattr(monitored, attr),
                    f"MonitoredLock hasattr({attr!r}) is False for threading.RLock attribute",
                )

        self.assertFalse(monitored._is_owned(), "_is_owned() must return False before acquire")
        with monitored:
            self.assertTrue(monitored._is_owned(), "_is_owned() must return True when held")
        self.assertFalse(monitored._is_owned(), "_is_owned() must return False after release")


if __name__ == "__main__":
    unittest.main()
