"""Tests for v3.43.15 session keep-alive.

Coverage:
  - session_history DB helpers (record, recent, lifetime observations)
  - SessionKeeper class state machine
  - predict_next_expiry with empty + populated history
  - Jitter is bounded
  - Module API: start_keeper / stop_keeper / get_status / force_check
  - API endpoints: /api/session_status, /api/session_history,
    /api/sites/<sid>/reconnect, /api/sites/<sid>/keep_alive_toggle

Tests do NOT exercise the actual Chromium browser path — that requires
Playwright with a real network, which we don't have here. The keeper's
heartbeat uses httpx; we mock the httpx Client to test the verification
logic.
"""
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def _isolated_cwd():
    """Switch to a fresh tmpdir, restore on exit.

    v3.62.1: on Windows the app/db opens log files inside the tmpdir;
    Windows refuses to delete a file that is still open, which crashed
    teardown with WinError 32. Close logging handlers before teardown
    and use ignore_cleanup_errors so a stuck file degrades to a
    harmless leftover instead of failing the test."""
    import logging
    from bulk_downloader import db as db_mod
    from bulk_downloader import session_keeper as sk
    orig = os.getcwd()
    orig_db_path = db_mod.DB_PATH
    try:
        td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except TypeError:
        td_ctx = tempfile.TemporaryDirectory()  # Python < 3.10
    with td_ctx as td:
        # run_tests.py gives each file a BD_HOME, but these tests need a
        # fresh database per test.  Pin DB_PATH explicitly because keeper
        # threads may otherwise keep using the file-level database after the
        # cwd has moved on to the next test.
        db_mod.DB_PATH = str(Path(td) / "downloader_history.db")
        os.chdir(td)
        try:
            yield Path(td)
        finally:
            # A keeper owns a browser thread.  Join it before restoring cwd or
            # deleting its profile directory so it cannot write into the next
            # test's database/profile while teardown is running.
            sk.stop_all()
            os.chdir(orig)
            db_mod.DB_PATH = orig_db_path
            for name in [""] + [n for n in
                                list(logging.Logger.manager.loggerDict)
                                if n == "bulk_downloader"
                                or n.startswith("bulk_downloader.")]:
                lg = logging.getLogger(name)
                for h in list(getattr(lg, "handlers", [])):
                    try:
                        h.close()
                    except Exception:
                        pass
                    try:
                        lg.removeHandler(h)
                    except Exception:
                        pass


def _reset_module_state():
    """Clear keepers + takeover locks between tests."""
    from bulk_downloader import session_keeper as sk
    sk.stop_all()
    with sk._state_lock:
        sk._takeover_locks.clear()


def _reset_app_runtime_state(app_module):
    """Model a process restart for API tests that change their working dir."""
    from bulk_downloader import account_pool

    with app_module._BOOT_LOCK:
        site_ids = set(app_module.s_cfg) | set(app_module.runners)
        for runner in list(app_module.runners.values()):
            try:
                runner.stop_scheduler()
            finally:
                runner.stop()
        for site_id in site_ids:
            account_pool.remove_pool(site_id)
        app_module.runners.clear()
        app_module.s_meta.clear()
        app_module.s_cfg.clear()
        app_module._BOOTED_PATHS.clear()
        app_module._SITE_RUNTIME_PATH = None
        app_module._SITE_RUNTIME_READY = False
        app_module._SITE_RUNTIME_ROLLBACK_PENDING = False


def _keeper_config():
    """Config for lifecycle/API tests that must not launch a real browser."""
    return {"password": "p", "keep_alive_enabled": False}


# Measured on test5 at dcd8201 over 100 public-API lifecycles: maximum start
# latency 0.001312s and maximum stop latency 0.014766s.
# ceil(100 * max(0.001312s, 0.014766s)) = 2s.
_KEEPER_THREAD_BOUND_SECONDS = 2


def _worker_thread_population(keeper) -> int:
    return sum(thread is keeper._thread for thread in threading.enumerate())


def _assert_keeper_worker_started(keeper) -> None:
    population = _worker_thread_population(keeper)
    assert keeper._thread.ident is not None, (
        "keeper start returned without starting its worker thread"
    )
    assert keeper._thread.is_alive(), "keeper worker exited before stop was tested"
    assert population == 1, (
        "expected exactly one live keeper worker thread, found "
        f"{population}"
    )


def _assert_thread_stopped(thread: threading.Thread) -> None:
    thread.join(timeout=_KEEPER_THREAD_BOUND_SECONDS)
    assert not thread.is_alive(), (
        "keeper worker did not stop within the measured "
        f"{_KEEPER_THREAD_BOUND_SECONDS}s bound"
    )


# ─── session_history DB ───────────────────────────────────────────

def test_record_and_recent_events():
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        db.session_event_record("wow", 0, "login", "first")
        db.session_event_record("wow", 0, "heartbeat_ok", "still alive")
        db.session_event_record("ultraf", 0, "login", "")
        events = db.session_event_recent()
        assert len(events) == 3
        # Most recent first
        assert events[0]["event_type"] in ("login", "heartbeat_ok")
        # Filter by site_id
        wow_events = db.session_event_recent(site_id="wow")
        assert len(wow_events) == 2


def test_lifetime_observations_basic():
    """A login → heartbeat_ok → heartbeat_fail sequence yields one
    lifetime measurement."""
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        db.session_event_record("wow", 0, "login", "")
        time.sleep(0.02)
        db.session_event_record("wow", 0, "heartbeat_ok", "")
        time.sleep(0.02)
        db.session_event_record("wow", 0, "heartbeat_fail", "")
        lifetimes = db.session_lifetime_observations("wow", 0)
        assert len(lifetimes) == 1
        # Lower bound carries the discrimination (the sleeps between the two
        # records really elapsed). The upper bound only rejects absurdity, so
        # it is wide on purpose: it is a WALL-CLOCK gap between two db writes,
        # and under a loaded parallel lane (the @923 sweeps ran 16-64 pytest
        # processes) a tight bound measures the scheduler, not the subject.
        assert 0.02 < lifetimes[0] < 30.0


def test_lifetime_observations_multiple_cycles():
    """Multiple login→failure cycles yield multiple lifetimes."""
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        for cycle in range(3):
            db.session_event_record("wow", 0, "login", "")
            time.sleep(0.02)
            db.session_event_record("wow", 0, "heartbeat_fail", "")
        lifetimes = db.session_lifetime_observations("wow", 0)
        assert len(lifetimes) == 3, f"expected 3 lifetimes, got {lifetimes!r}"


def test_lifetime_observations_preserve_order_when_timestamps_tie():
    """Insertion order disambiguates a failure and next login at one tick."""
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        for event_type in ("login", "heartbeat_fail", "login",
                           "heartbeat_fail"):
            db.session_event_record("wow", 0, event_type, "")
        with db.db_conn() as cx:
            rows = cx.execute(
                "SELECT id FROM session_history ORDER BY id").fetchall()
            for row, timestamp in zip(rows, (100.0, 200.0, 200.0, 300.0)):
                cx.execute("UPDATE session_history SET ts=? WHERE id=?",
                           (timestamp, row["id"]))

        assert db.session_lifetime_observations(
            "wow", 0, lookback_days=100000) == [100.0, 100.0]


def test_lifetime_observations_ignores_heartbeat_ok_for_close():
    """heartbeat_ok between login and heartbeat_fail does NOT close
    the measurement window."""
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        db.session_event_record("wow", 0, "login", "")
        time.sleep(0.01)
        db.session_event_record("wow", 0, "heartbeat_ok", "")
        time.sleep(0.01)
        db.session_event_record("wow", 0, "heartbeat_ok", "")
        time.sleep(0.01)
        db.session_event_record("wow", 0, "heartbeat_fail", "")
        lifetimes = db.session_lifetime_observations("wow", 0)
        # Only one lifetime — full window from login to fail
        assert len(lifetimes) == 1
        assert lifetimes[0] > 0.025  # at least 3*10ms


def test_lifetime_observations_empty():
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        assert db.session_lifetime_observations("doesnotexist") == []


# ─── SessionKeeper basics ───────────────────────────────────────────

def test_keeper_initial_state_starting():
    from bulk_downloader.session_keeper import SessionKeeper
    k = SessionKeeper("wow", 0, {"password": "p"}, lambda *_: (True, ""))
    assert k.state["state"] == "starting"
    assert k.state["consecutive_failures"] == 0
    # Don't start it; just verify the constructor


def test_keeper_set_state_records():
    from bulk_downloader.session_keeper import SessionKeeper
    k = SessionKeeper("wow", 0, {"password": "p"}, lambda *_: (True, ""))
    k._set_state("connected", "test")
    assert k.state["state"] == "connected"
    assert k.state["last_detail"] == "test"


def test_keeper_jitter_bounded():
    from bulk_downloader.session_keeper import _jitter
    base = 100.0
    # Run many samples and verify all within ±20%
    for _ in range(100):
        v = _jitter(base)
        assert 80.0 <= v <= 120.0


# ─── Module-level API ────────────────────────────────────────────────

def test_start_and_stop_keeper():
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        # The lifecycle test deliberately disables heartbeats: browser/login
        # behavior has separate coverage and would make teardown depend on a
        # real Chromium process.
        called = []
        def cb(sid, idx, cfg):
            called.append((sid, idx))
            return True, "test login ok"
        keeper = sk.start_keeper("wow", 0, _keeper_config(), cb)
        # Establish the nonzero subject before making any lifecycle verdict.
        _assert_keeper_worker_started(keeper)
        # Status should exist now
        status = sk.get_status()
        assert len(status) == 1
        assert status[0]["site_id"] == "wow"
        # stop_keeper is deliberately non-blocking.  Join under the measured
        # lifecycle bound, then prove both the registry and worker are gone.
        sk.stop_keeper("wow", 0)
        assert keeper._stop.is_set(), "stop_keeper did not signal its worker"
        _assert_thread_stopped(keeper._thread)
        assert _worker_thread_population(keeper) == 0
        assert sk.get_status() == []


def test_start_stop_precondition_rejects_zero_worker_threads(monkeypatch):
    """Control: the lifecycle assertion must fail if start creates no worker."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        monkeypatch.setattr(sk.SessionKeeper, "start", lambda self: None)
        keeper = sk.start_keeper("wow", 0, _keeper_config(), lambda *_: (True, ""))
        assert ("wow", 0) in sk._keepers, (
            "control did not reach the same registered-keeper precondition"
        )
        with pytest.raises(
            AssertionError,
            match="keeper start returned without starting its worker thread",
        ):
            _assert_keeper_worker_started(keeper)
        assert _worker_thread_population(keeper) == 0


def test_start_failure_is_not_published_and_a_retry_starts_the_worker(monkeypatch):
    """A failed Thread.start cannot become a reusable dead keeper."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        real_start = sk.SessionKeeper.start
        calls = 0

        def fail_once(keeper):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("planted keeper start failure")
            return real_start(keeper)

        monkeypatch.setattr(sk.SessionKeeper, "start", fail_once)
        with pytest.raises(RuntimeError, match="planted keeper start failure"):
            sk.start_keeper(
                "wow", 0, _keeper_config(), lambda *_: (True, ""))
        assert ("wow", 0) not in sk._keepers

        keeper = sk.start_keeper(
            "wow", 0, _keeper_config(), lambda *_: (True, ""))
        _assert_keeper_worker_started(keeper)
        assert sk._keepers[("wow", 0)] is keeper
        sk.stop_keeper("wow", 0)
        _assert_thread_stopped(keeper._thread)


def test_exited_keeper_deregisters_and_a_later_start_replaces_it(monkeypatch):
    """A target that exits after Thread.start cannot remain reusable forever."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        first_exited = threading.Event()

        def exit_immediately(_keeper):
            first_exited.set()

        monkeypatch.setattr(sk.SessionKeeper, "_run", exit_immediately)
        first = sk.start_keeper(
            "wow", 0, _keeper_config(), lambda *_: (True, ""))
        assert first_exited.wait(_KEEPER_THREAD_BOUND_SECONDS)
        _assert_thread_stopped(first._thread)
        assert ("wow", 0) not in sk._keepers

        release = threading.Event()
        monkeypatch.setattr(
            sk.SessionKeeper,
            "_run",
            lambda _keeper: release.wait(_KEEPER_THREAD_BOUND_SECONDS),
        )
        second = sk.start_keeper(
            "wow", 0, _keeper_config(), lambda *_: (True, ""))
        try:
            assert second is not first
            _assert_keeper_worker_started(second)
            assert sk._keepers[("wow", 0)] is second
        finally:
            release.set()
            sk.stop_keeper("wow", 0)
            _assert_thread_stopped(second._thread)


def test_restart_refuses_while_stopped_keeper_generation_is_still_live(
        monkeypatch):
    """Stop/start overlap must not publish two keepers for one account."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        entered = threading.Event()
        release = threading.Event()
        generations = []

        def blocked_run(keeper):
            generations.append(keeper)
            entered.set()
            release.wait(_KEEPER_THREAD_BOUND_SECONDS)

        monkeypatch.setattr(sk.SessionKeeper, "_run", blocked_run)
        monkeypatch.setattr(
            sk, "_KEEPER_GENERATION_STOP_TIMEOUT_S", 0.05, raising=False)
        first = sk.start_keeper(
            "wow", 0, _keeper_config(), lambda *_: (True, ""))
        assert entered.wait(_KEEPER_THREAD_BOUND_SECONDS)
        sk.stop_keeper("wow", 0)

        try:
            with pytest.raises(
                RuntimeError, match="previous keeper generation is still live"
            ):
                sk.start_keeper(
                    "wow", 0, _keeper_config(), lambda *_: (True, ""))
            assert generations == [first]
            assert sk._keepers.get(("wow", 0)) is first
        finally:
            release.set()
            _assert_thread_stopped(first._thread)


def test_starter_overlapping_keeper_target_exit_hands_off_generation(
        monkeypatch):
    """An ensure call cannot be lost behind an exiting target finalizer."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        allow_first_return = threading.Event()
        first_body_done = threading.Event()
        second_started = threading.Event()
        release_second = threading.Event()
        generations = []

        def controlled_run(keeper):
            generations.append(keeper)
            if len(generations) == 1:
                allow_first_return.wait(_KEEPER_THREAD_BOUND_SECONDS)
                first_body_done.set()
                return
            second_started.set()
            release_second.wait(_KEEPER_THREAD_BOUND_SECONDS)

        monkeypatch.setattr(sk.SessionKeeper, "_run", controlled_run)
        first = sk.start_keeper(
            "wow", 0, _keeper_config(), lambda *_: (True, ""))
        try:
            # Keep the old target blocked in identity-finally while the
            # overlapping starter sees Thread.is_alive() as true.
            with sk._state_lock:
                allow_first_return.set()
                assert first_body_done.wait(_KEEPER_THREAD_BOUND_SECONDS)
                assert first._thread.is_alive()
                returned = sk.start_keeper(
                    "wow", 0, _keeper_config(), lambda *_: (True, ""))
                assert returned is first

            assert second_started.wait(_KEEPER_THREAD_BOUND_SECONDS)
            second = sk._keepers.get(("wow", 0))
            assert second is not None and second is not first
            assert second._thread.is_alive()
        finally:
            release_second.set()
            sk.stop_all(timeout=_KEEPER_THREAD_BOUND_SECONDS)


def test_keeper_stop_bound_still_fires_for_genuinely_hung_work():
    """Negative bound control: a never-released worker must remain a failure."""
    release = threading.Event()
    thread = threading.Thread(
        target=release.wait,
        name="row337-genuinely-hung-keeper-control",
        daemon=True,
    )
    thread.start()
    try:
        assert thread.is_alive(), "hung-control worker never started"
        with pytest.raises(
            AssertionError,
            match="did not stop within the measured 2s bound",
        ):
            _assert_thread_stopped(thread)
        assert thread.is_alive(), "the bound control was not genuinely hung"
    finally:
        release.set()
        thread.join()


def test_keeper_start_transform_control_imports_without_starting_a_worker():
    """Mutation transform control: importability makes no start verdict."""
    from bulk_downloader import session_keeper as sk
    assert callable(sk.start_keeper)


def test_force_check_returns_true_when_keeper_exists():
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        cb = lambda *_: (True, "")
        sk.start_keeper("wow", 0, _keeper_config(), cb)
        time.sleep(0.1)
        assert sk.force_check("wow", 0) is True
        assert sk.force_check("nosuchsite", 0) is False
        sk.stop_all()


def test_get_takeover_lock_is_reentrant():
    """Same caller can acquire twice without deadlock."""
    from bulk_downloader.session_keeper import get_takeover_lock
    # v3.66.772: use a UNIQUE (site, account) key. get_takeover_lock returns a
    # per-process cached threading.RLock; a keep-alive background thread can hold
    # the "wow"/0 lock (a fixture site) when this test runs under load, timing out
    # the first acquire(0.5). A key nothing else ever touches removes the contention.
    lock = get_takeover_lock("__reentrant_pin__", 999999)
    assert lock.acquire(timeout=0.5)
    try:
        # Reentrant acquire from same thread should succeed
        assert lock.acquire(timeout=0.5)
        lock.release()
    finally:
        lock.release()


def test_get_takeover_lock_returns_same_instance():
    from bulk_downloader.session_keeper import get_takeover_lock
    l1 = get_takeover_lock("wow", 0)
    l2 = get_takeover_lock("wow", 0)
    assert l1 is l2
    # Different (site, account) keys give different locks
    l3 = get_takeover_lock("wow", 1)
    assert l1 is not l3


# ─── predict_next_expiry ────────────────────────────────────────────

def test_predict_returns_zero_when_no_login():
    """If we've never successfully logged in, prediction returns 0."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        # Use a callback that fails so the keeper never sets last_login_ts
        cb = lambda *_: (False, "fake fail")
        sk.start_keeper("wow", 0, _keeper_config(), cb)
        time.sleep(0.3)
        pred = sk.predict_next_expiry("wow", 0)
        assert pred == 0.0
        sk.stop_all()


def test_predict_uses_default_when_no_history():
    """With a last_login_ts but no failure observations, use the
    DEFAULT_SESSION_LIFETIME_SEC."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        cb = lambda *_: (True, "")
        sk.start_keeper("wow", 0, _keeper_config(), cb)
        time.sleep(0.1)
        # Force last_login_ts
        keeper = sk._keepers[("wow", 0)]
        keeper.state["last_login_ts"] = time.time()
        pred = sk.predict_next_expiry("wow", 0)
        assert pred > time.time() + 3600  # at least 1 hour out
        sk.stop_all()


def test_predict_uses_median_of_observed_lifetimes():
    """With 3+ observations, the prediction is the median."""
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import db
    with _isolated_cwd():
        db.db_init()
        _reset_module_state()
        # Seed history with 3 independent cycles. Each cycle starts at
        # a different base time so they don't overlap. Lifetimes are
        # 100s, 200s, 300s — median = 200s.
        base = time.time() - 100000  # well in the past
        for i, life in enumerate((100, 200, 300)):
            t_login = base + i * 1000  # 1000s gap between cycles
            t_fail = t_login + life
            db.session_event_record("wow", 0, "login", "")
            with db.db_conn() as cx:
                cx.execute(
                    "UPDATE session_history SET ts=? WHERE id=(SELECT MAX(id) FROM session_history)",
                    (t_login,))
            db.session_event_record("wow", 0, "heartbeat_fail", "")
            with db.db_conn() as cx:
                cx.execute(
                    "UPDATE session_history SET ts=? WHERE id=(SELECT MAX(id) FROM session_history)",
                    (t_fail,))
        # Verify the lifetimes parse correctly
        lifetimes = db.session_lifetime_observations("wow", 0)
        assert sorted(lifetimes) == [100.0, 200.0, 300.0], \
            f"expected [100, 200, 300], got {sorted(lifetimes)}"
        # Now spin up a keeper with a fail-callback (so it doesn't
        # overwrite our seeded last_login_ts via auto-relogin)
        cb = lambda *_: (False, "test no-op")
        sk.start_keeper("wow", 0, _keeper_config(), cb)
        time.sleep(0.1)
        # Manually set last_login_ts to "now" so the prediction relative
        # to current time is what we expect
        now = time.time()
        sk._keepers[("wow", 0)].state["last_login_ts"] = now
        pred = sk.predict_next_expiry("wow", 0)
        # Median of [100, 200, 300] = 200; prediction = now + 200
        assert abs(pred - (now + 200)) < 5  # 5s slack
        sk.stop_all()


# ─── API endpoints ──────────────────────────────────────────────────

@contextmanager
def _api_client():
    """Test client with a fresh app + isolated tmpdir."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    with _isolated_cwd() as td:
        Path(td, "screenshots").mkdir(exist_ok=True)
        db_init()
        _reset_module_state()
        _reset_app_runtime_state(A)
        try:
            c = A.app.test_client()
            r = c.get('/api/pair'); token = r.get_json()['token']
            r = c.post('/api/pair/redeem', json={'token': token})
            csrf = r.get_json()['csrf_token']
            H = {'X-CSRF-Token': csrf}
            yield c, H, A
        finally:
            _reset_app_runtime_state(A)


def test_session_status_endpoint_empty():
    """With no keepers running, status returns overall='none'."""
    with _api_client() as (c, H, A):
        r = c.get('/api/session_status', headers=H)
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert d["overall"] == "none"
        assert d["keepers"] == []


def test_session_status_with_active_keeper():
    """With a keeper running, status reports it."""
    with _api_client() as (c, H, A):
        from bulk_downloader import session_keeper as sk
        cb = lambda *_: (True, "")
        sk.start_keeper("wow", 0, _keeper_config(), cb)
        time.sleep(0.2)
        r = c.get('/api/session_status', headers=H)
        d = r.get_json()
        assert len(d["keepers"]) == 1
        assert d["keepers"][0]["site_id"] == "wow"
        sk.stop_all()


def test_session_history_endpoint():
    with _api_client() as (c, H, A):
        from bulk_downloader import db
        db.session_event_record("wow", 0, "login", "test")
        db.session_event_record("ultraf", 0, "heartbeat_ok", "")
        r = c.get('/api/session_history', headers=H)
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True
        assert len(d["events"]) == 2
        # Filter by site
        r2 = c.get('/api/session_history?site_id=wow', headers=H)
        d2 = r2.get_json()
        assert len(d2["events"]) == 1


def test_reconnect_unknown_site_returns_404():
    with _api_client() as (c, H, A):
        r = c.post('/api/sites/does-not-exist/reconnect', json={}, headers=H)
        assert r.status_code == 404


def test_reconnect_triggers_check():
    """For a real site, reconnect calls force_check."""
    with _api_client() as (c, H, A):
        # Create a site
        r = c.post('/api/sites',
                   json={'name': 'X', 'password': 'p'}, headers=H)
        sid = r.get_json()['id']
        # Start a keeper for it
        from bulk_downloader import session_keeper as sk
        cb = lambda *_: (True, "")
        sk.start_keeper(sid, 0, _keeper_config(), cb)
        time.sleep(0.1)
        # Hit reconnect
        r2 = c.post(f'/api/sites/{sid}/reconnect', json={}, headers=H)
        assert r2.status_code == 200
        d = r2.get_json()
        assert d["ok"] is True
        assert d["count"] >= 1
        sk.stop_all()


def test_keep_alive_toggle():
    with _api_client() as (c, H, A):
        r = c.post('/api/sites',
                   json={'name': 'X', 'password': 'p'}, headers=H)
        sid = r.get_json()['id']
        # Turn off
        r2 = c.post(f'/api/sites/{sid}/keep_alive_toggle',
                    json={'enabled': False}, headers=H)
        assert r2.status_code == 200
        d = r2.get_json()
        assert d["enabled"] is False
        # Verify persisted
        from bulk_downloader import app as A_mod
        assert A_mod.s_cfg[sid]["keep_alive_enabled"] is False
        # Turn back on
        r3 = c.post(f'/api/sites/{sid}/keep_alive_toggle',
                    json={'enabled': True}, headers=H)
        assert r3.get_json()["enabled"] is True


def test_start_session_keepers_passes_account_credentials(monkeypatch):
    """An account-backed keeper must receive that account's password."""
    from bulk_downloader import app as app_module
    from bulk_downloader import session_keeper as sk

    captured = []
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app_module, "s_cfg", {
        "fixture": {
            "keep_alive_enabled": True,
            "accounts": [{"username": "tester", "password": "fixturepass"}],
        }
    })
    monkeypatch.setattr(
        sk, "start_keeper",
        lambda site_id, account_idx, cfg, callback: captured.append(
            (site_id, account_idx, cfg, callback)),
    )

    app_module._start_session_keepers()

    assert captured[0][2]["username"] == "tester"
    assert captured[0][2]["password"] == "fixturepass"


def test_site_update_cannot_start_keeper_for_concurrently_deleted_other_site(
        monkeypatch):
    """A site-local update must not replay a stale all-site keeper snapshot."""
    from bulk_downloader import account_pool, app as app_module, app_state
    from bulk_downloader import audit, cookie_health, db, session_keeper
    from bulk_downloader import app_sites_id_core as site_core

    site_a = "keeper-update-site-a"
    site_b = "keeper-delete-site-b"
    suffix = 0
    while app_state.site_lifecycle_lock(site_a) is app_state.site_lifecycle_lock(
            site_b):
        suffix += 1
        site_b = f"keeper-delete-site-b-{suffix}"

    class _Runner:
        def update_config(self, _cfg):
            return None

        def retire_scheduler(self, timeout=2.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            return True

    start_a_entered = threading.Event()
    release_start_a = threading.Event()
    delete_done = threading.Event()
    started_sites = []
    responses = {}

    def gated_start(site_id, account_idx, cfg, callback):
        started_sites.append(site_id)
        if site_id == site_a:
            start_a_entered.set()
            assert release_start_a.wait(2), "test never released site-A start"

    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app_module, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app_module, "boot_once", lambda: False)
    monkeypatch.setattr(app_module, "_accepted_tokens", lambda: [])
    monkeypatch.setattr(app_module, "_save_sites_config", lambda: None)
    monkeypatch.setattr(app_module, "_start_watch_folder_threads", lambda: None)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(session_keeper, "start_keeper", gated_start)
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    monkeypatch.setattr(db, "queue_delete_site", lambda _sid: None)
    monkeypatch.setattr(cookie_health, "forget_site", lambda _sid: None)
    monkeypatch.setattr(audit, "audit_log", lambda **_kwargs: None)

    assert site_a not in app_state.s_cfg and site_b not in app_state.s_cfg
    app_state.s_cfg[site_a] = {
        "name": "Site A",
        "password": "secret-a",
        "keep_alive_enabled": True,
    }
    app_state.s_cfg[site_b] = {
        "name": "Site B",
        "password": "secret-b",
        "keep_alive_enabled": True,
    }
    app_state.s_meta[site_a] = {"name": "Site A"}
    app_state.s_meta[site_b] = {"name": "Site B"}
    app_state.runners[site_a] = _Runner()
    app_state.runners[site_b] = _Runner()

    def issue_update():
        with app_module.app.test_client() as client:
            responses["update"] = client.put(
                f"/api/sites/{site_a}", json={"name": "Site A updated"})

    def issue_delete():
        with app_module.app.test_client() as client:
            responses["delete"] = client.delete(f"/api/sites/{site_b}")
        delete_done.set()

    update_thread = threading.Thread(
        target=issue_update, name="site-a-keeper-update", daemon=True)
    delete_thread = threading.Thread(
        target=issue_delete, name="site-b-delete", daemon=True)
    try:
        update_thread.start()
        assert start_a_entered.wait(2), "site-A update never reached keeper start"
        delete_thread.start()
        assert delete_done.wait(2), "site-B delete was blocked by site-A update"
        assert responses["delete"].status_code == 200

        release_start_a.set()
        update_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        assert not update_thread.is_alive() and not delete_thread.is_alive()
        assert responses["update"].status_code == 200
        assert site_b not in app_state.s_cfg
        assert site_b not in started_sites, (
            "site-A update started an orphan keeper from its stale all-site "
            "snapshot after site B was deleted")
    finally:
        release_start_a.set()
        update_thread.join(timeout=2)
        delete_thread.join(timeout=2)
        for site_id in (site_a, site_b):
            app_state.runners.pop(site_id, None)
            app_state.s_meta.pop(site_id, None)
            app_state.s_cfg.pop(site_id, None)


def test_retired_keeper_lifecycle_rejects_stale_starter(monkeypatch):
    """Teardown retirement must fence a starter that arrives afterward."""
    from bulk_downloader import session_keeper as sk

    constructed = []

    class UnexpectedKeeper:
        def __init__(self, *_args, **_kwargs):
            constructed.append(True)

    assert sk.stop_all(timeout=0.1, retire=True) is True
    monkeypatch.setattr(sk, "SessionKeeper", UnexpectedKeeper)

    with pytest.raises(RuntimeError, match="lifecycle is retired"):
        sk.start_keeper("stale", 0, _keeper_config(), lambda *_: None)

    assert constructed == []
    assert sk.open_lifecycle() is True
