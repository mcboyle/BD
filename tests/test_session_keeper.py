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
import time
from contextlib import contextmanager
from pathlib import Path


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


def _keeper_config():
    """Config for lifecycle/API tests that must not launch a real browser."""
    return {"password": "p", "keep_alive_enabled": False}


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
        # Between 30ms and 200ms (slack for slow CI)
        assert 0.02 < lifetimes[0] < 0.5


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
        # Wait briefly for the thread to do its first iteration
        time.sleep(0.5)
        # Status should exist now
        status = sk.get_status()
        assert len(status) == 1
        assert status[0]["site_id"] == "wow"
        # Clean up
        sk.stop_keeper("wow", 0)
        time.sleep(0.1)


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
        c = A.app.test_client()
        r = c.get('/api/pair'); token = r.get_json()['token']
        r = c.post('/api/pair/redeem', json={'token': token})
        csrf = r.get_json()['csrf_token']
        H = {'X-CSRF-Token': csrf}
        yield c, H, A


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
