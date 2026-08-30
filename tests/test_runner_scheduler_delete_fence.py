"""Scheduler-generation fences for single and bulk site deletion.

A site's scheduler owns a live reference to the runner and can call
``runner.start()`` after the route has begun teardown.  Deletion therefore
cannot remove the runner/config/queue until the published scheduler generation
has boundedly stopped, and a timed-out generation must keep its join handle.
"""
from __future__ import annotations

import threading
import time
import importlib
import queue

import pytest

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.bd_module_wipe


def _status(response):
    if isinstance(response, tuple):
        return response[1]
    return getattr(response, "status_code", 200)


def _bd():
    """Import the app only after canonical per-test isolation is active."""
    import bulk_downloader.app_state as st
    from bulk_downloader.app import app
    import bulk_downloader.app_sites_id_core as site_core
    return st, app, site_core


def _blocked_scheduler_runner():
    """Build a minimal real scheduler owner in the current module graph."""
    from bulk_downloader.runner_scheduler import SchedulerMixin

    class _BlockedSchedulerRunner(SchedulerMixin):
        def __init__(self):
            self.config = {"sched_enabled": True}
            self._sched_stop = threading.Event()
            self._sched_thread = None
            self._sched_lifecycle_lock = threading.RLock()
            self._sched_retired = False
            self._release_scheduler = threading.Event()
            self._auto_retry_stop = threading.Event()
            self._auto_retry_thread = None
            self._auto_retry_lifecycle_lock = threading.RLock()
            self._auto_retry_retired = False
            self._release_auto_retry = threading.Event()
            self.stop_calls = []

        def publish_blocked_scheduler(self):
            thread = threading.Thread(
                target=self._release_scheduler.wait,
                name="blocked-delete-scheduler",
                daemon=True,
            )
            self._sched_thread = thread
            thread.start()
            return thread

        def publish_blocked_auto_retry(self):
            thread = threading.Thread(
                target=self._release_auto_retry.wait,
                name="blocked-delete-auto-retry",
                daemon=True,
            )
            self._auto_retry_thread = thread
            thread.start()
            return thread

        def _sched_loop(self):
            raise AssertionError("delete resurrected a replacement scheduler")

        def stop(self):
            self.stop_calls.append("runner")

        def _stop_auto_retry(self):
            self.stop_calls.append("auto_retry")

        def retire_workers(self, timeout=5.0):
            self.stop()
            return True

        def cleanup(self):
            self._release_scheduler.set()
            self._release_auto_retry.set()
            thread = self._sched_thread
            if thread is not None:
                thread.join(timeout=2)
            auto_thread = self._auto_retry_thread
            if auto_thread is not None:
                auto_thread.join(timeout=2)
            try:
                self.stop_scheduler(timeout=2)
            except TypeError:
                # Pristine stop_scheduler had no timeout parameter.  Keep RED
                # cleanup from masking the behavioral assertion that caught it.
                self.stop_scheduler()
            try:
                SchedulerMixin._stop_auto_retry(self, timeout=2)
            except TypeError:
                SchedulerMixin._stop_auto_retry(self)

    return _BlockedSchedulerRunner()


def _blocked_worker_runner():
    """Build a SiteRunner owner with one deliberately blocked worker."""
    from bulk_downloader.runner import SiteRunner

    class _BlockedWorkerRunner(SiteRunner):
        def __init__(self):
            self.config = {"worker_teardown_wait_s": 0.01}
            self._run_lifecycle_lock = threading.RLock()
            self._worker_heartbeats_lock = threading.Lock()
            self._run_retired = False
            self._stop = threading.Event()
            self._release_worker = threading.Event()
            self._worker_threads = []
            self.stop_calls = []

        def publish_blocked_worker(self):
            thread = threading.Thread(
                target=self._release_worker.wait,
                name="blocked-delete-worker",
                daemon=True,
            )
            self._worker_threads = [thread]
            thread.start()
            return thread

        def retire_scheduler(self, timeout=12.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def stop(self):
            self.stop_calls.append("runner")
            self._stop.set()

        def _stop_auto_retry(self):
            self.stop_calls.append("auto_retry")

        def cleanup(self):
            self._release_worker.set()
            for thread in self._worker_threads:
                thread.join(timeout=2)

    return _BlockedWorkerRunner()


def _blocked_auxiliary_runner():
    """Build a real stop/retire owner with blocked auth/manual handles."""
    from bulk_downloader.runner import SiteRunner

    class _Session:
        def __init__(self, name):
            self.release = threading.Event()
            self._closed = threading.Event()
            self.cancel_calls = []
            self._thread = threading.Thread(
                target=self._run,
                name=name,
                daemon=True,
            )
            self._thread.start()

        def _run(self):
            self.release.wait()
            self._closed.set()

        def cancel(self, timeout=10):
            self.cancel_calls.append(timeout)
            return False

    class _BlockedAuxiliaryRunner(SiteRunner):
        def __init__(self):
            self.site_id = "blocked-auxiliary"
            self.config = {}
            self._run_lifecycle_lock = threading.RLock()
            self._worker_heartbeats_lock = threading.Lock()
            self._run_retired = False
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._worker_threads = []
            self._worker_run_generation = 0
            self._worker_generation_invalidated = False
            self._url_queue = queue.Queue()
            self._lock = threading.RLock()
            self.jobs = {}
            self._state = "idle"
            self._rl_autostart = False
            self._auto_retry_stop = threading.Event()
            self._auto_retry_thread = None
            self._auto_retry_lifecycle_lock = threading.RLock()
            self._test_download_session = _Session(
                "blocked-manual-download-owner")
            self._manual_download_session = self._test_download_session
            self._test_login_session = _Session(
                "blocked-manual-login-owner")
            self._manual_login_handle = self._test_login_session
            self._login_release = threading.Event()
            self._test_login_thread = threading.Thread(
                target=self._login_release.wait,
                name="blocked-auto-login-owner",
                daemon=True,
            )
            self._login_thread = self._test_login_thread
            self._login_thread.start()
            self._manual_snapshot_stop = threading.Event()
            self._snapshot_release = threading.Event()
            self._test_snapshot_thread = threading.Thread(
                target=self._snapshot_release.wait,
                name="blocked-manual-snapshot-owner",
                daemon=True,
            )
            self._manual_snapshot_thread = self._test_snapshot_thread
            self._manual_snapshot_thread.start()

        def cleanup(self):
            self._test_download_session.release.set()
            self._test_login_session.release.set()
            self._login_release.set()
            self._snapshot_release.set()
            for thread in (
                self._test_download_session._thread,
                self._test_login_session._thread,
                self._test_login_thread,
                self._test_snapshot_thread,
            ):
                thread.join(timeout=2)

    return _BlockedAuxiliaryRunner()


def _install_site(st, sid, runner):
    st.s_cfg[sid] = {"url": "https://example.invalid", "name": sid}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = runner
    st._watch_threads.pop(sid, None)
    st._watch_stops.pop(sid, None)


def _remove_site(st, sid):
    st.s_cfg.pop(sid, None)
    st.s_meta.pop(sid, None)
    st.runners.pop(sid, None)
    st._watch_threads.pop(sid, None)
    st._watch_stops.pop(sid, None)


def test_stop_scheduler_retains_a_live_generation_and_refuses_restart():
    """Dropping the timed-out handle lets start clear its stop event."""
    runner = _blocked_scheduler_runner()
    generation = runner.publish_blocked_scheduler()
    try:
        assert runner.stop_scheduler(timeout=0.01) is False
        assert runner._sched_thread is generation
        assert generation.is_alive()
        assert runner._sched_stop.is_set()

        assert runner.start_scheduler() is False
        assert runner._sched_thread is generation
        assert runner._sched_stop.is_set(), (
            "a replacement start cleared the old generation's stop signal")
    finally:
        runner.cleanup()


def test_stop_auto_retry_retains_a_live_generation_and_refuses_restart():
    """A timed-out queue scanner must not become an untracked writer."""
    runner = _blocked_scheduler_runner()
    SchedulerMixin = type(runner).__mro__[1]
    generation = runner.publish_blocked_auto_retry()
    try:
        assert SchedulerMixin._stop_auto_retry(
            runner, timeout=0.01) is False
        assert runner._auto_retry_thread is generation
        assert generation.is_alive()
        assert runner._auto_retry_stop.is_set()

        assert SchedulerMixin._start_auto_retry(runner) is False
        assert runner._auto_retry_thread is generation
        assert runner._auto_retry_stop.is_set(), (
            "a replacement scanner cleared the old generation's stop signal")
    finally:
        runner.cleanup()


def test_retire_workers_retains_live_handles_and_blocks_stale_start():
    """A timed-out download worker keeps its owner permanently fenced."""
    from bulk_downloader.runner import StartOutcome

    runner = _blocked_worker_runner()
    generation = runner.publish_blocked_worker()
    try:
        assert runner.retire_workers(timeout=0.01) is False
        assert runner._worker_threads == [generation]
        assert generation.is_alive()
        assert runner._run_retired is True
        assert runner.start() is StartOutcome.TEARDOWN_PENDING

        runner._release_worker.set()
        generation.join(timeout=2)
        assert runner.retire_workers(timeout=1.0) is True
        assert runner._worker_threads == []
    finally:
        runner.cleanup()


def test_retire_workers_retains_live_auth_and_manual_owner_handles():
    """Deletion cannot outlive independent Playwright/auth generations."""
    runner = _blocked_auxiliary_runner()
    download = runner._manual_download_session
    manual_login = runner._manual_login_handle
    login_thread = runner._login_thread
    snapshot_thread = runner._manual_snapshot_thread
    try:
        assert runner.retire_workers(timeout=0.01) is False
        assert runner._manual_download_session is download
        assert runner._manual_login_handle is manual_login
        assert runner._login_thread is login_thread
        assert runner._manual_snapshot_thread is snapshot_thread
        assert runner._manual_snapshot_stop.is_set()
        assert all(thread.is_alive() for thread in (
            download._thread, manual_login._thread,
            login_thread, snapshot_thread,
        ))
        assert runner.start_manual_download(
            "https://new.invalid") == (
                False, "Site runtime is being deleted")
        assert runner.start_manual_login() == (
            False, "Site runtime is being deleted")
        assert runner.start_captcha_solve_session(
            "https://captcha.invalid") == {
                "ok": False, "error": "site runtime is being deleted"}
        runner.login_async()
        assert runner._login_thread is login_thread

        download.release.set()
        manual_login.release.set()
        runner._login_release.set()
        runner._snapshot_release.set()
        for thread in (
            download._thread, manual_login._thread,
            login_thread, snapshot_thread,
        ):
            thread.join(timeout=2)

        assert runner.retire_workers(timeout=1.0) is True
        assert runner._manual_download_session is None
        assert runner._manual_login_handle is None
        assert runner._login_thread is None
        assert runner._manual_snapshot_thread is None
    finally:
        runner.cleanup()


def test_retire_workers_retains_ordinary_and_vnc_captcha_owners(
        monkeypatch):
    """Every captcha browser generation is a deletion lifecycle owner."""
    from bulk_downloader import takeover_vnc

    class _CaptchaOwner:
        def __init__(self, name):
            self.release = threading.Event()
            self._closed = threading.Event()
            self._thread = threading.Thread(
                target=self._run, name=name, daemon=True)
            self._thread.start()

        def _run(self):
            self.release.wait()
            self._closed.set()

        def cancel(self, timeout=10):
            return False

        def stop(self, timeout=10):
            return False

    runner = _blocked_auxiliary_runner()
    # This test isolates captcha ownership from the other auxiliary classes.
    runner._test_download_session.release.set()
    runner._test_login_session.release.set()
    runner._login_release.set()
    runner._snapshot_release.set()
    for thread in (
        runner._test_download_session._thread,
        runner._test_login_session._thread,
        runner._test_login_thread,
        runner._test_snapshot_thread,
    ):
        thread.join(timeout=2)

    ordinary = _CaptchaOwner("blocked-ordinary-captcha-owner")
    vnc = _CaptchaOwner("blocked-vnc-captcha-owner")
    ordinary_record = {"session_id": "ordinary", "handle": ordinary}
    vnc_record = {"session_id": "vnc", "handle": vnc, "kind": "vnc"}
    runner._captcha_solve_sessions = {
        "https://ordinary.invalid": ordinary_record,
        "https://vnc.invalid": vnc_record,
    }
    vnc_teardowns = []
    monkeypatch.setattr(
        takeover_vnc, "teardown", lambda sid: vnc_teardowns.append(sid))
    try:
        assert runner.retire_workers(timeout=0.01) is False
        assert runner._captcha_solve_sessions["https://ordinary.invalid"] \
            is ordinary_record
        assert runner._captcha_solve_sessions["https://vnc.invalid"] \
            is vnc_record
        assert ordinary._thread.is_alive() and vnc._thread.is_alive()
        assert vnc_teardowns == []

        ordinary.release.set()
        vnc.release.set()
        ordinary._thread.join(timeout=2)
        vnc._thread.join(timeout=2)
        assert runner.retire_workers(timeout=1.0) is True
        assert runner._captcha_solve_sessions == {}
        assert vnc_teardowns == ["vnc"]
    finally:
        ordinary.release.set()
        vnc.release.set()
        ordinary._thread.join(timeout=2)
        vnc._thread.join(timeout=2)
        runner.cleanup()


def test_retire_workers_resnapshots_session_published_by_inflight_launcher():
    """A launch admitted before retirement cannot publish after capture."""
    runner = _blocked_worker_runner()
    runner._auxiliary_start_threads = {}
    runner._login_thread = None
    runner._manual_snapshot_thread = None
    runner._manual_snapshot_stop = None
    runner._manual_download_session = None
    runner._manual_login_handle = None
    runner._captcha_solve_sessions = {}
    launch_entered = threading.Event()
    allow_publication = threading.Event()
    owner_published = threading.Event()
    retire_result = []

    class _LateCaptchaOwner:
        def __init__(self):
            self.release = threading.Event()
            self._closed = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                name="late-captcha-owner",
                daemon=True,
            )
            self._thread.start()

        def _run(self):
            self.release.wait()
            self._closed.set()

        def cancel(self, timeout=10):
            self.release.set()
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()

    late_owner = []

    def launch_then_publish():
        assert runner._begin_auxiliary_start() is True
        try:
            launch_entered.set()
            assert allow_publication.wait(2)
            owner = _LateCaptchaOwner()
            late_owner.append(owner)
            runner._captcha_solve_sessions["https://late.invalid"] = {
                "session_id": "late", "handle": owner}
            owner_published.set()
        finally:
            runner._end_auxiliary_start()

    launcher = threading.Thread(
        target=launch_then_publish,
        name="inflight-captcha-launcher",
        daemon=True,
    )
    retirer = threading.Thread(
        target=lambda: retire_result.append(runner.retire_workers(timeout=1.0)),
        name="captcha-retirer",
        daemon=True,
    )
    try:
        launcher.start()
        assert launch_entered.wait(2)
        retirer.start()
        deadline = time.monotonic() + 2
        while not runner._run_retired and time.monotonic() < deadline:
            time.sleep(0.001)
        assert runner._run_retired is True
        allow_publication.set()
        assert owner_published.wait(2)
        launcher.join(timeout=2)
        retirer.join(timeout=2)
        assert not retirer.is_alive()
        assert retire_result == [True]
        assert runner._captcha_solve_sessions == {}
        assert late_owner and not late_owner[0]._thread.is_alive()
    finally:
        allow_publication.set()
        launcher.join(timeout=2)
        if late_owner:
            late_owner[0].release.set()
            late_owner[0]._thread.join(timeout=2)
        retirer.join(timeout=2)
        runner.cleanup()


def test_captcha_end_pop_cannot_write_across_delete(monkeypatch):
    """A relay end call remains an owner after popping its session record."""
    st, flask_app, site_core = _bd()
    from bulk_downloader import account_pool, db

    sid = "captcha_end_delete_fence"
    runner = _blocked_worker_runner()
    runner._auxiliary_start_threads = {}
    runner._login_thread = None
    runner._manual_snapshot_thread = None
    runner._manual_snapshot_stop = None
    runner._manual_download_session = None
    runner._manual_login_handle = None
    cancel_entered = threading.Event()
    release_cancel = threading.Event()
    end_result = []
    updates = []
    queue_deletes = []

    class _BlockingCaptcha:
        def cancel(self, timeout=10):
            cancel_entered.set()
            release_cancel.wait(2)
            return True

    runner._captcha_solve_sessions = {
        "https://captcha.invalid": {
            "session_id": "captcha-end",
            "handle": _BlockingCaptcha(),
        }
    }
    runner._update_job = lambda *args, **kwargs: updates.append((args, kwargs))
    runner.log_event = lambda *args, **kwargs: None
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)

    end_thread = threading.Thread(
        target=lambda: end_result.append(runner.end_captcha_solve_session(
            "https://captcha.invalid", resolution="resolved")),
        name="blocked-captcha-end",
        daemon=True,
    )
    try:
        end_thread.start()
        assert cancel_entered.wait(2)
        assert runner._captcha_solve_sessions == {}

        monkeypatch.setattr(
            site_core, "_SITE_RUNNER_WORKER_STOP_TIMEOUT_S", 0.01)
        with flask_app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            response = site_core.api_delete(sid)
        assert _status(response) == 503
        assert st.runners.get(sid) is runner and sid in st.s_cfg
        assert queue_deletes == [] and updates == []

        release_cancel.set()
        end_thread.join(timeout=2)
        assert end_result == [True]
        assert len(updates) == 1

        with flask_app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            retry = site_core.api_delete(sid)
        assert _status(retry) == 200
        assert queue_deletes == [sid]
        assert sid not in st.runners and sid not in st.s_cfg
    finally:
        release_cancel.set()
        end_thread.join(timeout=2)
        _remove_site(st, sid)
        runner.cleanup()


def test_delete_refuses_config_and_queue_teardown_for_live_scheduler(
        monkeypatch):
    """Single delete must fail closed while its scheduler can still fire."""
    st, app, site_core = _bd()
    from bulk_downloader import account_pool, db, session_keeper

    sid = "live_scheduler_delete_fence"
    runner = _blocked_scheduler_runner()
    generation = runner.publish_blocked_scheduler()
    queue_deletes = []
    saves = []
    monkeypatch.setattr(site_core, "_SITE_SCHEDULER_STOP_TIMEOUT_S", 0.01,
                        raising=False)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)
    try:
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            response = site_core.api_delete(sid)

        assert _status(response) == 503
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == []
        assert saves == []
        assert runner.stop_calls == []
        assert runner._sched_thread is generation and generation.is_alive()

        # A concurrent updater that captured the runner before deletion may
        # still call start_scheduler(). Retirement must make that harmless.
        assert runner.start_scheduler() is False
        assert runner._sched_thread is generation
        assert runner._sched_stop.is_set()

        # The failed transaction restored the runner identity for a safe
        # retry.  Once the retained generation settles, retry can finish the
        # original deletion without ever clearing its stop signal.
        runner._release_scheduler.set()
        generation.join(timeout=2)
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            retry = site_core.api_delete(sid)
        assert _status(retry) == 200
        assert sid not in st.runners and sid not in st.s_cfg
        assert queue_deletes == [sid] and saves == [True]
    finally:
        _remove_site(st, sid)
        runner.cleanup()


def test_bulk_delete_reports_scheduler_survivor_without_removing_state(
        monkeypatch):
    """Bulk delete carries the identical per-site scheduler fence."""
    st, app, site_core = _bd()
    from bulk_downloader import account_pool, db, session_keeper

    sid = "live_scheduler_bulk_delete_fence"
    runner = _blocked_scheduler_runner()
    generation = runner.publish_blocked_scheduler()
    queue_deletes = []
    saves = []
    monkeypatch.setattr(site_core, "_SITE_SCHEDULER_STOP_TIMEOUT_S", 0.01,
                        raising=False)
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)
    try:
        with app.test_request_context(
            "/api/sites/v2/bulk",
            method="POST",
            json={"action": "delete", "site_ids": [sid]},
        ):
            response = site_core.api_sites_v2_bulk()

        payload = response.get_json()
        assert payload["applied_to"] == 0, payload
        assert payload["errors"] == [{
            "site_id": sid,
            "error": "scheduler worker did not stop",
        }], payload
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == []
        assert saves == []
        assert runner.stop_calls == []
        assert runner._sched_thread is generation and generation.is_alive()
        assert runner.start_scheduler() is False
        assert runner._sched_thread is generation
        assert runner._sched_stop.is_set()
    finally:
        _remove_site(st, sid)
        runner.cleanup()


def test_delete_refuses_teardown_for_live_auto_retry_writer(monkeypatch):
    """Single delete retains state while the queue scanner can still write."""
    st, app, site_core = _bd()
    from bulk_downloader import account_pool, db, session_keeper

    sid = "live_auto_retry_delete_fence"
    runner = _blocked_scheduler_runner()
    SchedulerMixin = type(runner).__mro__[1]
    generation = runner.publish_blocked_auto_retry()
    queue_deletes = []
    saves = []
    monkeypatch.setattr(site_core, "_SITE_AUTO_RETRY_STOP_TIMEOUT_S", 0.01,
                        raising=False)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)
    try:
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            response = site_core.api_delete(sid)

        assert _status(response) == 503
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == [] and saves == []
        assert runner.stop_calls == []
        assert runner._auto_retry_thread is generation
        assert generation.is_alive() and runner._auto_retry_stop.is_set()
        assert SchedulerMixin._start_auto_retry(runner) is False
        assert runner._auto_retry_thread is generation
        assert runner._auto_retry_stop.is_set()
        # Scheduler retirement happened first.  Keep that partial transaction
        # explicitly fail-closed until retry, rather than silently resurrecting
        # one background writer beside the other generation.
        assert runner._sched_retired is True
        assert runner.start_scheduler() is False
        assert runner._sched_thread is None

        runner._release_auto_retry.set()
        generation.join(timeout=2)
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            retry = site_core.api_delete(sid)
        assert _status(retry) == 200
        assert sid not in st.runners and sid not in st.s_cfg
        assert queue_deletes == [sid] and saves == [True]
    finally:
        _remove_site(st, sid)
        runner.cleanup()


def test_bulk_delete_reports_live_auto_retry_without_removing_state(
        monkeypatch):
    """Bulk delete carries the identical queue-scanner fence."""
    st, app, site_core = _bd()
    from bulk_downloader import account_pool, db, session_keeper

    sid = "live_auto_retry_bulk_delete_fence"
    runner = _blocked_scheduler_runner()
    SchedulerMixin = type(runner).__mro__[1]
    generation = runner.publish_blocked_auto_retry()
    queue_deletes = []
    saves = []
    monkeypatch.setattr(site_core, "_SITE_AUTO_RETRY_STOP_TIMEOUT_S", 0.01,
                        raising=False)
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)
    try:
        with app.test_request_context(
            "/api/sites/v2/bulk",
            method="POST",
            json={"action": "delete", "site_ids": [sid]},
        ):
            response = site_core.api_sites_v2_bulk()

        payload = response.get_json()
        assert payload["applied_to"] == 0, payload
        assert payload["errors"] == [{
            "site_id": sid,
            "error": "auto-retry worker did not stop",
        }], payload
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == [] and saves == []
        assert runner.stop_calls == []
        assert runner._auto_retry_thread is generation
        assert generation.is_alive() and runner._auto_retry_stop.is_set()
        assert SchedulerMixin._start_auto_retry(runner) is False
        assert runner._auto_retry_thread is generation
        assert runner._auto_retry_stop.is_set()
    finally:
        _remove_site(st, sid)
        runner.cleanup()


def test_delete_refuses_teardown_for_live_runner_worker(monkeypatch):
    """Single delete retains config/queue while a download can still write."""
    st, app, site_core = _bd()
    from bulk_downloader import account_pool, db
    from bulk_downloader.runner import StartOutcome

    sid = "live_runner_worker_delete_fence"
    runner = _blocked_worker_runner()
    generation = runner.publish_blocked_worker()
    queue_deletes = []
    saves = []
    monkeypatch.setattr(site_core, "_SITE_RUNNER_WORKER_STOP_TIMEOUT_S", 0.01,
                        raising=False)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)
    try:
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            response = site_core.api_delete(sid)

        assert _status(response) == 503
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == [] and saves == []
        assert runner._worker_threads == [generation]
        assert generation.is_alive() and runner._run_retired is True
        assert runner.start() is StartOutcome.TEARDOWN_PENDING

        runner._release_worker.set()
        generation.join(timeout=2)
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            retry = site_core.api_delete(sid)
        assert _status(retry) == 200
        assert sid not in st.runners and sid not in st.s_cfg
        assert queue_deletes == [sid] and saves == [True]
    finally:
        _remove_site(st, sid)
        runner.cleanup()


def test_bulk_delete_reports_live_runner_worker_without_removing_state(
        monkeypatch):
    """Bulk delete carries the identical runner-worker quiescence fence."""
    st, app, site_core = _bd()
    from bulk_downloader import account_pool, db

    sid = "live_runner_worker_bulk_delete_fence"
    runner = _blocked_worker_runner()
    generation = runner.publish_blocked_worker()
    queue_deletes = []
    saves = []
    monkeypatch.setattr(site_core, "_SITE_RUNNER_WORKER_STOP_TIMEOUT_S", 0.01,
                        raising=False)
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)
    try:
        with app.test_request_context(
            "/api/sites/v2/bulk",
            method="POST",
            json={"action": "delete", "site_ids": [sid]},
        ):
            response = site_core.api_sites_v2_bulk()

        payload = response.get_json()
        assert payload["applied_to"] == 0, payload
        assert payload["errors"] == [{
            "site_id": sid,
            "error": "runner worker did not stop",
        }], payload
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == [] and saves == []
        assert runner._worker_threads == [generation]
        assert generation.is_alive() and runner._run_retired is True
    finally:
        _remove_site(st, sid)
        runner.cleanup()


def test_stale_start_action_cannot_resurrect_runner_after_delete(monkeypatch):
    """A start callback captured before DELETE must observe retirement."""
    st, flask_app, site_core = _bd()
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import account_pool, db

    sid = "stale_start_after_delete"
    runner = _blocked_worker_runner()
    start_captured = threading.Event()
    allow_start = threading.Event()
    action_result = []
    queue_deletes = []
    saves = []

    class _GatedRunner(type(runner)):
        def __getattribute__(self, name):
            if name == "start" and object.__getattribute__(
                    self, "_gate_next_start"):
                object.__setattr__(self, "_gate_next_start", False)
                start_captured.set()
                assert allow_start.wait(2), "DELETE never released stale start"
            return super().__getattribute__(name)

    runner.__class__ = _GatedRunner
    runner._gate_next_start = True
    monkeypatch.setattr(app_module, "_rate_check", lambda _action: True)
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saves.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    _install_site(st, sid, runner)

    def issue_captured_start():
        with flask_app.test_request_context(
                f"/api/sites/{sid}/start", method="POST"):
            action_result.append(app_module._do_action(sid, "start"))

    action_thread = threading.Thread(
        target=issue_captured_start,
        name="captured-start-action",
        daemon=True,
    )
    try:
        action_thread.start()
        assert start_captured.wait(2)

        with flask_app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            delete_response = site_core.api_delete(sid)

        assert _status(delete_response) == 200
        assert sid not in st.runners and sid not in st.s_cfg
        assert queue_deletes == [sid] and saves == [True]

        allow_start.set()
        action_thread.join(timeout=2)
        assert not action_thread.is_alive()
        assert len(action_result) == 1
        assert _status(action_result[0]) == 409
        response = action_result[0][0]
        assert response.get_json()["blocked_by"] == "worker_teardown"
        assert runner._run_retired is True
        assert runner._worker_threads == []
    finally:
        allow_start.set()
        action_thread.join(timeout=2)
        _remove_site(st, sid)
        runner.cleanup()


def test_http_learned_import_commits_before_delete_can_teardown(monkeypatch):
    """Every HTTP <sid> writer shares delete's lifecycle transaction."""
    st, flask_app, site_core = _bd()
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import account_pool, db

    sid = "http_learned_delete_transaction"
    import_entered = threading.Event()
    allow_import = threading.Event()
    delete_started = threading.Event()
    delete_done = threading.Event()
    responses = {}
    events = []

    class _HttpRunner:
        def update_config(self, _cfg):
            events.append("import-entered")
            import_entered.set()
            assert allow_import.wait(2)
            events.append("import-committed")

        def retire_scheduler(self, timeout=2.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            events.append("delete-retired")
            return True

    runner = _HttpRunner()
    _install_site(st, sid, runner)
    monkeypatch.setattr(app_module, "boot_once", lambda: False)
    monkeypatch.setattr(app_module, "_accepted_tokens", lambda: [])
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(
        site_core, "_save_sites_config", lambda: events.append("saved"))
    monkeypatch.setattr(
        db, "queue_delete_site",
        lambda deleted_sid: events.append(f"queue-deleted:{deleted_sid}"),
    )
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)

    def issue_import():
        with flask_app.test_client() as client:
            responses["import"] = client.post(
                f"/api/sites/{sid}/learned/import",
                json={"learned": {"download": {"trigger": [".download"]}}},
            )

    def issue_delete():
        delete_started.set()
        with flask_app.test_client() as client:
            responses["delete"] = client.delete(f"/api/sites/{sid}")
        delete_done.set()

    import_thread = threading.Thread(
        target=issue_import, name="http-learned-import", daemon=True)
    delete_thread = threading.Thread(
        target=issue_delete, name="http-site-delete", daemon=True)
    try:
        import_thread.start()
        assert import_entered.wait(2)
        delete_thread.start()
        assert delete_started.wait(2)
        assert not delete_done.wait(0.1), (
            "DELETE crossed an in-flight learned/import transaction")
        assert st.runners.get(sid) is runner and sid in st.s_cfg

        allow_import.set()
        import_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        assert not import_thread.is_alive() and not delete_thread.is_alive()
        assert responses["import"].status_code == 200
        assert responses["delete"].status_code == 200
        assert events.index("import-committed") < events.index("delete-retired")
        assert events.index("delete-retired") < events.index(
            f"queue-deleted:{sid}")
        assert sid not in st.runners and sid not in st.s_cfg
    finally:
        allow_import.set()
        import_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        _remove_site(st, sid)


def test_http_auth_health_writer_commits_before_delete_can_teardown(
        monkeypatch):
    """Non-sites blueprints with a sid share the global site transaction."""
    st, flask_app, site_core = _bd()
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import account_pool, cookie_health, db

    sid = "http_auth_health_delete_transaction"
    check_entered = threading.Event()
    allow_check = threading.Event()
    delete_started = threading.Event()
    delete_done = threading.Event()
    responses = {}
    events = []

    class _HttpRunner:
        def retire_scheduler(self, timeout=2.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            events.append("delete-retired")
            return True

    def blocked_health_check(checked_sid, _cfg):
        assert checked_sid == sid
        events.append("health-entered")
        check_entered.set()
        assert allow_check.wait(2)
        events.append("health-committed")
        return {"status": "valid"}

    runner = _HttpRunner()
    _install_site(st, sid, runner)
    monkeypatch.setattr(app_module, "boot_once", lambda: False)
    monkeypatch.setattr(app_module, "_accepted_tokens", lambda: [])
    monkeypatch.setattr(cookie_health, "check_site", blocked_health_check)
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", lambda _sid: None)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)

    def issue_check():
        with flask_app.test_client() as client:
            responses["check"] = client.post(
                f"/api/auth_health/check/{sid}")

    def issue_delete():
        delete_started.set()
        with flask_app.test_client() as client:
            responses["delete"] = client.delete(f"/api/sites/{sid}")
        delete_done.set()

    check_thread = threading.Thread(
        target=issue_check, name="http-auth-health-check", daemon=True)
    delete_thread = threading.Thread(
        target=issue_delete, name="http-auth-health-delete", daemon=True)
    try:
        check_thread.start()
        assert check_entered.wait(2)
        delete_thread.start()
        assert delete_started.wait(2)
        assert not delete_done.wait(0.1), (
            "DELETE crossed a non-sites sid writer transaction")
        assert st.runners.get(sid) is runner and sid in st.s_cfg

        allow_check.set()
        check_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        assert not check_thread.is_alive() and not delete_thread.is_alive()
        assert responses["check"].status_code == 200
        assert responses["delete"].status_code == 200
        assert events.index("health-committed") < events.index("delete-retired")
        assert sid not in st.runners and sid not in st.s_cfg
    finally:
        allow_check.set()
        check_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        _remove_site(st, sid)


def test_takeover_session_sid_collision_does_not_enter_site_transaction(
        monkeypatch):
    """Captcha takeover ``sid`` names a session, not a site generation."""
    st, flask_app, _site_core = _bd()
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import captcha_relay

    sid = "takeover_session_that_matches_a_site"
    response_ready = threading.Event()
    responses = []
    _install_site(st, sid, object())
    monkeypatch.setattr(app_module, "boot_once", lambda: False)
    monkeypatch.setattr(app_module, "_accepted_tokens", lambda: [])
    monkeypatch.setattr(
        captcha_relay, "submit_takeover_input", lambda _sid, _event: "ok")

    lock = st.site_lifecycle_lock(sid)
    lock.acquire()

    def issue_takeover_input():
        with flask_app.test_client() as client:
            responses.append(client.post(
                f"/cockpit/api/takeover/{sid}/input",
                json={"type": "key", "key": "Escape"},
            ))
        response_ready.set()

    request_thread = threading.Thread(
        target=issue_takeover_input,
        name="takeover-session-site-id-collision",
        daemon=True,
    )
    try:
        request_thread.start()
        assert response_ready.wait(1), (
            "captcha/takeover session id was mistaken for a live site id")
        assert responses[0].status_code == 200
    finally:
        lock.release()
        request_thread.join(timeout=2)
        _remove_site(st, sid)


def test_auth_health_check_all_holds_site_transaction_through_publish(
        monkeypatch):
    """Nightly/check-all health writes cannot resurrect a deleted site row."""
    st, flask_app, site_core = _bd()
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import account_pool, cookie_health, db

    sid = "background_auth_health_delete_transaction"
    check_entered = threading.Event()
    allow_check = threading.Event()
    delete_done = threading.Event()
    responses = {}
    summaries = []
    events = []

    class _BackgroundRunner:
        def retire_scheduler(self, timeout=2.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            events.append("delete-retired")
            return True

    def blocked_health_check(checked_sid, _cfg):
        assert checked_sid == sid
        events.append("health-entered")
        check_entered.set()
        assert allow_check.wait(2)
        cookie_health._record(
            checked_sid, status="green", note="background committed")
        events.append("health-committed")
        return {"site_id": checked_sid, "status": "green"}

    _install_site(st, sid, _BackgroundRunner())
    st.s_cfg[sid]["cookies_file"] = "/unused/background-cookies.json"
    monkeypatch.setattr(app_module, "boot_once", lambda: False)
    monkeypatch.setattr(app_module, "_accepted_tokens", lambda: [])
    monkeypatch.setattr(cookie_health, "check_site", blocked_health_check)
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", lambda _sid: None)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)

    check_thread = threading.Thread(
        target=lambda: summaries.append(cookie_health.check_all_sites(st.s_cfg)),
        name="background-auth-health-check-all",
        daemon=True,
    )

    def issue_delete():
        with flask_app.test_client() as client:
            responses["delete"] = client.delete(f"/api/sites/{sid}")
        delete_done.set()

    delete_thread = threading.Thread(
        target=issue_delete, name="background-auth-health-delete", daemon=True)
    try:
        check_thread.start()
        assert check_entered.wait(2)
        delete_thread.start()
        assert not delete_done.wait(0.1), (
            "DELETE crossed an in-flight background auth-health publish")
        assert st.runners.get(sid) is not None and sid in st.s_cfg

        allow_check.set()
        check_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        assert not check_thread.is_alive() and not delete_thread.is_alive()
        assert summaries and summaries[0]["checked"] == 1
        assert responses["delete"].status_code == 200
        assert events.index("health-committed") < events.index("delete-retired")
        assert sid not in {row["site_id"] for row in cookie_health.status_all()}
        assert sid not in st.runners and sid not in st.s_cfg
    finally:
        allow_check.set()
        check_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        _remove_site(st, sid)


def test_global_url_router_cannot_enqueue_after_captured_runner_is_deleted(
        monkeypatch):
    """Folder/global intake rechecks permanent retirement at publication."""
    st, flask_app, site_core = _bd()
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import account_pool, content_rights, db, runner_queue

    sid = "global_route_delete_fence"
    runner = _blocked_worker_runner()
    runner.site_id = sid
    runner.config = {"webhook_events": "", "use_ytdlp_archive": False,
                     "use_playlist_extractor": False}
    runner.jobs = {}
    runner.urls = []
    runner._listing_titles = {}
    runner._lock = threading.RLock()
    runner.log_event = lambda *args, **kwargs: None

    class _Log:
        def error(self, *args, **kwargs):
            pass

        def debug(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    runner.log = _Log()
    policy_entered = threading.Event()
    allow_policy = threading.Event()
    route_result = []
    bulk_writes = []
    queue_deletes = []

    def blocked_policy(_url):
        policy_entered.set()
        assert allow_policy.wait(2)
        return None

    _install_site(st, sid, runner)
    st.s_cfg[sid].update({
        "login_url": "https://race.invalid/login",
        "url_patterns": [r"race\.invalid"],
    })
    monkeypatch.setattr(content_rights, "url_is_blocked", blocked_policy)
    monkeypatch.setattr(
        runner_queue, "queue_bulk_upsert",
        lambda *args, **kwargs: bulk_writes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)

    router = threading.Thread(
        target=lambda: route_result.append(app_module._route_urls_internal(
            ["https://race.invalid/video/1"])),
        name="captured-global-router",
        daemon=True,
    )
    try:
        router.start()
        assert policy_entered.wait(2)

        with flask_app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            delete_response = site_core.api_delete(sid)
        assert _status(delete_response) == 200
        assert queue_deletes == [sid]
        assert sid not in st.runners and sid not in st.s_cfg

        allow_policy.set()
        router.join(timeout=2)
        assert not router.is_alive()
        assert route_result
        assert runner.jobs == {} and runner.urls == []
        assert bulk_writes == []
    finally:
        allow_policy.set()
        router.join(timeout=2)
        _remove_site(st, sid)
        runner.cleanup()


def test_fresh_app_reset_joins_retained_scheduler_and_auto_retry_handles(
        tmp_path, monkeypatch):
    """Process reset cannot restore cwd/env beside detached live writers."""
    import conftest as fixture_module
    import bulk_downloader.app as app_module

    fixture_gen = fixture_module.fresh_app.__wrapped__(tmp_path, monkeypatch)
    next(fixture_gen)

    class _SlowBackgroundRunner:
        def __init__(self):
            self._sched_stop = threading.Event()
            self._auto_retry_stop = threading.Event()
            self._worker_threads = []
            self._sched_thread = threading.Thread(
                target=self._finish_scheduler,
                name="slow-fixture-scheduler",
                daemon=True,
            )
            self._auto_retry_thread = threading.Thread(
                target=self._finish_auto_retry,
                name="slow-fixture-auto-retry",
                daemon=True,
            )
            self._sched_thread.start()
            self._auto_retry_thread.start()

        def _finish_scheduler(self):
            self._sched_stop.wait(2)
            time.sleep(0.5)

        def _finish_auto_retry(self):
            self._auto_retry_stop.wait(2)
            time.sleep(0.5)

        def retire_scheduler(self, timeout=2.0):
            self._sched_stop.set()
            self._sched_thread.join(timeout=timeout)
            return not self._sched_thread.is_alive()

        def stop_scheduler(self):
            return self.retire_scheduler()

        def stop(self):
            pass

        def retire_auto_retry(self, timeout=2.0):
            self._auto_retry_stop.set()
            self._auto_retry_thread.join(timeout=timeout)
            return not self._auto_retry_thread.is_alive()

        def _stop_auto_retry(self):
            return self.retire_auto_retry()

    runner = _SlowBackgroundRunner()
    app_module.runners["slow-background-cleanup"] = runner
    try:
        with pytest.raises(StopIteration):
            next(fixture_gen)
        assert not runner._sched_thread.is_alive()
        assert not runner._auto_retry_thread.is_alive()
    finally:
        runner._sched_stop.set()
        runner._auto_retry_stop.set()
        runner._sched_thread.join(timeout=2)
        runner._auto_retry_thread.join(timeout=2)


def test_activation_rollback_retains_live_generation_and_retry_fails_closed(
        monkeypatch):
    """A partial config load cannot discard handles or latch on retry."""
    app_module = importlib.import_module("bulk_downloader.app")

    class _RollbackRunner:
        def __init__(self):
            self.release = threading.Event()
            self.stop_signal = threading.Event()
            self.calls = []
            self._sched_thread = threading.Thread(
                target=self.release.wait,
                name="blocked-activation-rollback",
                daemon=True,
            )
            self._sched_thread.start()

        def retire_scheduler(self, timeout=2.0):
            self.calls.append("scheduler")
            self.stop_signal.set()
            return not self._sched_thread.is_alive()

        # Pristine rollback called these two legacy methods.
        def stop_scheduler(self):
            return self.retire_scheduler()

        def stop(self):
            self.calls.append("legacy_stop")

        def retire_auto_retry(self, timeout=2.0):
            self.calls.append("auto_retry")
            return True

        def retire_workers(self, timeout=2.0):
            self.calls.append("workers")
            return True

        def cleanup(self):
            self.release.set()
            self._sched_thread.join(timeout=2)

    runner = _RollbackRunner()
    load_calls = []

    def fail_first_load():
        load_calls.append(True)
        if len(load_calls) == 1:
            app_module.s_cfg["partial"] = {"name": "partial"}
            app_module.s_meta["partial"] = {"status": "loading"}
            app_module.runners["partial"] = runner
            raise RuntimeError("planted partial load failure")

    app_module.s_cfg.clear()
    app_module.s_meta.clear()
    app_module.runners.clear()
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_PATH", None)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_READY", False)
    monkeypatch.setattr(app_module, "_load_sites_config", fail_first_load)
    monkeypatch.setattr(
        app_module, "_init_vpn_runtime", lambda: {"ok": True})
    monkeypatch.setattr(app_module, "_start_session_keepers", lambda: None)
    monkeypatch.setattr(app_module, "_start_watch_folder_threads", lambda: None)
    try:
        with pytest.raises(RuntimeError, match="rollback.*pending"):
            app_module._activate_configured_runtime_once("/tmp/partial-sites.json")
        assert app_module.runners.get("partial") is runner
        assert "partial" in app_module.s_cfg and "partial" in app_module.s_meta
        assert runner._sched_thread.is_alive()
        assert set(runner.calls) == {"scheduler", "auto_retry", "workers"}

        calls_before_retry = len(load_calls)
        with pytest.raises(RuntimeError, match="rollback.*pending"):
            app_module._activate_configured_runtime_once("/tmp/partial-sites.json")
        assert len(load_calls) == calls_before_retry
        assert app_module.runners.get("partial") is runner

        runner.release.set()
        runner._sched_thread.join(timeout=2)
        assert app_module._activate_configured_runtime_once(
            "/tmp/partial-sites.json") is True
        assert len(load_calls) == 2
        assert app_module.runners == {}
        assert app_module.s_cfg == {} and app_module.s_meta == {}
        assert app_module._SITE_RUNTIME_READY is True
    finally:
        runner.cleanup()


def test_vpn_init_failure_rolls_back_before_dependent_writers(monkeypatch):
    """A failed required-VPN gate cannot latch or start fail-open writers."""
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import vpn_runtime

    class _VpnRollbackRunner:
        def __init__(self):
            self.release = threading.Event()
            self._sched_stop = threading.Event()
            self._sched_thread = threading.Thread(
                target=self.release.wait,
                name="blocked-vpn-activation-rollback",
                daemon=True,
            )
            self._sched_thread.start()

        def retire_scheduler(self, timeout=2.0):
            self._sched_stop.set()
            self._sched_thread.join(timeout=timeout)
            return not self._sched_thread.is_alive()

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            return True

        def cleanup(self):
            self.release.set()
            self._sched_thread.join(timeout=2)

    runner = _VpnRollbackRunner()
    load_calls = []
    vpn_init_calls = []
    vpn_shutdown_calls = []
    dependent_starts = []

    def load_runtime():
        load_calls.append(True)
        if len(load_calls) == 1:
            app_module.s_cfg["vpn-required"] = {
                "name": "vpn-required",
                "vpn": {"required": True, "tunnel_id": "missing"},
            }
            app_module.s_meta["vpn-required"] = {"status": "loading"}
            app_module.runners["vpn-required"] = runner

    def init_vpn():
        vpn_init_calls.append(True)
        if len(vpn_init_calls) == 1:
            return {"ok": False, "reason": "required tunnel unavailable"}
        return {"ok": True}

    app_module.s_cfg.clear()
    app_module.s_meta.clear()
    app_module.runners.clear()
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_PATH", None)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_READY", False)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_ROLLBACK_PENDING", False)
    monkeypatch.setattr(app_module, "_load_sites_config", load_runtime)
    monkeypatch.setattr(app_module, "_init_vpn_runtime", init_vpn)
    monkeypatch.setattr(
        app_module, "_start_session_keepers",
        lambda: dependent_starts.append("keeper"),
    )
    monkeypatch.setattr(
        app_module, "_start_watch_folder_threads",
        lambda: dependent_starts.append("watch"),
    )
    monkeypatch.setattr(
        vpn_runtime, "shutdown",
        lambda: (vpn_shutdown_calls.append(True), True)[1],
    )
    try:
        with pytest.raises(RuntimeError, match="VPN.*failed|rollback.*pending"):
            app_module._activate_configured_runtime_once(
                "/tmp/vpn-required-sites.json")

        assert dependent_starts == []
        assert app_module._SITE_RUNTIME_READY is False
        assert app_module.runners.get("vpn-required") is runner
        assert runner._sched_thread.is_alive()
        assert app_module._SITE_RUNTIME_ROLLBACK_PENDING is True
        assert len(vpn_init_calls) == 1

        with pytest.raises(RuntimeError, match="rollback.*pending"):
            app_module._activate_configured_runtime_once(
                "/tmp/vpn-required-sites.json")
        assert len(load_calls) == 1
        assert len(vpn_init_calls) == 1
        assert dependent_starts == []

        runner.release.set()
        runner._sched_thread.join(timeout=2)
        assert app_module._activate_configured_runtime_once(
            "/tmp/vpn-required-sites.json") is True
        assert len(load_calls) == 2
        assert len(vpn_init_calls) == 2
        assert dependent_starts == ["keeper", "watch"]
        assert vpn_shutdown_calls
        assert app_module._SITE_RUNTIME_READY is True
    finally:
        runner.cleanup()


def test_dependent_start_failure_retains_watcher_until_proven_quiescent(
        monkeypatch):
    """Partial keeper/watch startup rolls back before VPN/config teardown."""
    app_module = importlib.import_module("bulk_downloader.app")
    from bulk_downloader import session_keeper, vpn_runtime

    class _CleanRunner:
        def retire_scheduler(self, timeout=2.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            return True

    load_calls = []
    keeper_starts = []
    keeper_stops = []
    watch_starts = []
    vpn_shutdowns = []
    release_watch = threading.Event()
    watch_stop = threading.Event()
    watch_thread = threading.Thread(
        target=release_watch.wait,
        name="blocked-activation-watch-owner",
        daemon=True,
    )

    def load_runtime():
        load_calls.append(True)
        if len(load_calls) == 1:
            app_module.s_cfg["partial-dependent"] = {"name": "partial"}
            app_module.s_meta["partial-dependent"] = {"status": "loading"}
            app_module.runners["partial-dependent"] = _CleanRunner()

    def start_watchers():
        watch_starts.append(True)
        if len(watch_starts) == 1:
            app_module._watch_stops["partial-dependent"] = watch_stop
            app_module._watch_threads["partial-dependent"] = watch_thread
            watch_thread.start()
            raise RuntimeError("planted dependent watch startup failure")

    app_module.s_cfg.clear()
    app_module.s_meta.clear()
    app_module.runners.clear()
    app_module._watch_threads.clear()
    app_module._watch_stops.clear()
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_PATH", None)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_READY", False)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_ROLLBACK_PENDING", False)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_RETIRE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(app_module, "_load_sites_config", load_runtime)
    monkeypatch.setattr(
        app_module, "_init_vpn_runtime", lambda: {"ok": True})
    monkeypatch.setattr(
        app_module, "_start_session_keepers",
        lambda: keeper_starts.append(True),
    )
    monkeypatch.setattr(app_module, "_start_watch_folder_threads", start_watchers)
    monkeypatch.setattr(
        session_keeper, "stop_site_keepers",
        lambda sid, timeout=5: (keeper_stops.append((sid, timeout)), True)[1],
    )
    monkeypatch.setattr(
        vpn_runtime, "shutdown",
        lambda: (vpn_shutdowns.append(True), True)[1],
    )
    try:
        with pytest.raises(RuntimeError, match="activation rollback.*pending"):
            app_module._activate_configured_runtime_once(
                "/tmp/partial-dependent-sites.json")
        assert app_module._SITE_RUNTIME_ROLLBACK_PENDING is True
        assert app_module.runners.get("partial-dependent") is not None
        assert app_module._watch_threads.get(
            "partial-dependent") is watch_thread
        assert watch_thread.is_alive() and watch_stop.is_set()
        assert keeper_starts == [True] and keeper_stops
        assert vpn_shutdowns == [], (
            "VPN leaf reset ran before its watcher producer was quiescent")

        release_watch.set()
        watch_thread.join(timeout=2)
        assert app_module._activate_configured_runtime_once(
            "/tmp/partial-dependent-sites.json") is True
        assert len(load_calls) == 2
        assert keeper_starts == [True, True]
        assert watch_starts == [True, True]
        assert vpn_shutdowns == [True]
        assert "partial-dependent" not in app_module._watch_threads
    finally:
        release_watch.set()
        watch_thread.join(timeout=2)


def test_retirement_revalidated_at_watch_generation_publication(monkeypatch):
    """A starter past the fast check cannot publish after retirement begins."""
    app_module = importlib.import_module("bulk_downloader.app")
    import bulk_downloader.watch_folder as watch_folder

    arrived = threading.Event()
    proceed = threading.Event()
    real_thread = threading.Thread
    started = []

    class GateLock:
        def __enter__(self):
            arrived.set()
            assert proceed.wait(2)
            return self

        def __exit__(self, *_exc):
            return False

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def is_alive(self):
            return False

        def start(self):
            started.append(self.name)

    app_module.runners.clear()
    app_module._watch_threads.clear()
    app_module._watch_stops.clear()
    app_module.runners["stale"] = object()
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app_module, "_watch_registry_lock", GateLock())
    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(
        watch_folder, "watch_loop_for_site", lambda *_args: None)

    starter = real_thread(target=app_module._start_watch_folder_threads)
    starter.start()
    assert arrived.wait(2)
    app_module._SITE_RUNTIME_RETIRING = True
    proceed.set()
    starter.join(timeout=2)

    assert not starter.is_alive()
    assert started == []
    assert app_module._watch_threads == {}
    assert app_module._watch_stops == {}
