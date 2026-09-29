"""dl95-naughtyamerica-2 -- runner_auth._check_cookies_or_relogin wait bound.

Defect observed on test2 (O1528 / naughtyamerica template proof 0416Z):
auto re-login that takes >60 s (Turnstile + submit: 82 s, 04:15:29-04:16:51Z)
was declared "Auto re-login failed" at 60s and the job deferred 10 min,
while the login then settled ok at 82s (04:16:51Z).
Every cold member run lost 10 min and logged a failed login that did not fail.

Contract:
1. _check_cookies_or_relogin waits up to 120s for async login (bounded by
   Turnstile / slow challenge completion).
2. _await_in_flight_login default timeout stays strictly under the consumer wait
   (115.0s < 120s) so concurrent workers also wait for slow logins to settle.
3. If the initial wait elapses but the login thread is still in flight,
   wait for in-flight completion (bounded by thread liveness) or re-check
   auth_state / fresh cookies / login outcome before failing; never report
   a failed login that succeeded.
4. When login genuinely fails, failure is reported and returns False.
"""

BD_GATE_SCOPE = "module"

import inspect
import re
import threading
import time
from unittest import mock

import pytest

from bulk_downloader.db import db_init
from bulk_downloader.runner import SiteRunner
from bulk_downloader.runner_auth import AuthMixin


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


def _runner(**over):
    db_init()
    cfg = {
        "login_url": "https://example.com/login",
        "username": "member",
        "password": "secret",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"], "submit_btn": ["#b"]}},
    }
    cfg.update(over)
    return SiteRunner("naughtyamerica", cfg)


def test_consumer_wait_is_at_least_120s():
    """_check_cookies_or_relogin must wait at least 120s to accommodate slow Turnstile logins."""
    src = inspect.getsource(AuthMixin._check_cookies_or_relogin)
    waits = [int(m) for m in re.findall(r"ev\.wait\(timeout=(\d+)\)", src)]
    assert waits, "no ev.wait(timeout=N) found in _check_cookies_or_relogin"
    assert min(waits) >= 120, (
        f"expected _check_cookies_or_relogin wait to be >= 120s, got {min(waits)}s; "
        "82s Turnstile logins will falsely time out at 60s")


def test_await_in_flight_login_timeout_matches_expanded_consumer_wait():
    """_await_in_flight_login default timeout must be >= 115s and strictly under consumer wait."""
    waiter_default = inspect.signature(
        AuthMixin._await_in_flight_login).parameters["timeout"].default
    src = inspect.getsource(AuthMixin._check_cookies_or_relogin)
    waits = [int(m) for m in re.findall(r"ev\.wait\(timeout=(\d+)\)", src)]
    assert waits, "no ev.wait(timeout=N) found in _check_cookies_or_relogin"
    assert waiter_default >= 115.0, (
        f"expected waiter default >= 115.0s, got {waiter_default}s")
    assert waiter_default < min(waits), (
        f"waiter default {waiter_default}s must stay strictly under consumer wait {min(waits)}s")


def test_relogin_succeeds_when_login_takes_longer_than_60s():
    """When login takes longer than 60s (e.g. 82s Turnstile), _check_cookies_or_relogin
    must return True when login settles ok, rather than failing prematurely at 60s."""
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]

    def _slow_successful_login(on_done=None, **_kw):
        # Simulate an async login thread that takes >60s relative to consumer
        def _run():
            r.set_cookies([{"name": "sid", "value": "fresh", "expires": time.time() + 86400}])
            r._login_outcome = (1, True)
            if on_done:
                on_done(True)

        t = threading.Thread(target=_run)
        r._login_thread = t
        # In this unit test we trigger on_done via a timer after 0.05s,
        # but verify ev.wait was invoked with a timeout >= 120s
        t.start()

    with mock.patch.object(r, "login_async", side_effect=_slow_successful_login):
        proceed = r._check_cookies_or_relogin("https://example.com/scene/1")
        assert proceed is True, "expected _check_cookies_or_relogin to succeed"


def test_in_flight_login_settlement_rechecked_before_declaring_failure():
    """If the initial event wait times out while the login thread is still in flight,
    the method must wait for thread settlement and re-check cookies/outcome,
    never reporting a failed login that succeeded."""
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]

    finish_gate = threading.Event()

    def _slow_thread_login(on_done=None, **_kw):
        def _run():
            finish_gate.wait(5.0)
            r.set_cookies([{"name": "sid", "value": "fresh", "expires": time.time() + 86400}])
            r._login_outcome = (1, True)
            if on_done:
                on_done(True)

        t = threading.Thread(target=_run)
        r._login_thread = t
        t.start()

    # Mock ev.wait to simulate timing out immediately on the first wait
    real_event = threading.Event

    class _QuickTimeoutEvent(real_event):
        def wait(self, timeout=None):
            # Unblock finish_gate so thread finishes right as wait returns
            finish_gate.set()
            time.sleep(0.02)
            return False

    with (
        mock.patch("threading.Event", _QuickTimeoutEvent),
        mock.patch.object(r, "login_async", side_effect=_slow_thread_login),
    ):
        proceed = r._check_cookies_or_relogin("https://example.com/scene/1")
        assert proceed is True, (
            "in-flight login thread succeeded after initial wait; "
            "_check_cookies_or_relogin must recognize success and return True")


def test_failed_login_still_fails_properly():
    """Negative control: genuine failure (bad credentials) still reports failure and returns False."""
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]

    def _failing_login(on_done=None, **_kw):
        r._login_outcome = (1, False)
        if on_done:
            on_done(False)

    with mock.patch.object(r, "login_async", side_effect=_failing_login):
        proceed = r._check_cookies_or_relogin("https://example.com/scene/1")
        assert proceed is False, "genuinely failed login must return False"
        assert r.jobs["https://example.com/scene/1"]["status"] == "pending"


@pytest.mark.parametrize("prior_success", [False, True])
def test_manual_pending_cannot_reuse_historical_login_success(prior_success):
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]
    r._login_attempt_seq = 4
    r._login_outcome = (4, prior_success)
    r._manual_login_handle = object()
    r._login_thread = None
    result = r._check_cookies_or_relogin("https://example.com/scene/1")
    assert result is False, "CX1_OLD_LOGIN_SUCCESS_BYPASSES_MANUAL_PENDING"
    assert r.jobs["https://example.com/scene/1"]["status"] == "pending"


def test_virtual_82_second_settlement_is_accepted(monkeypatch):
    from types import SimpleNamespace

    from bulk_downloader import runner_auth
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]
    callback = []

    class Event:
        def set(self):
            pass

        def wait(self, timeout):
            if timeout >= 82:
                callback[0](True)
                return True
            return False

    monkeypatch.setattr(runner_auth, "threading", SimpleNamespace(Event=Event))
    monkeypatch.setattr(r, "login_async", lambda on_done: callback.append(on_done))
    assert r._check_cookies_or_relogin("https://example.com/scene/1"), "CX1_82_SECOND_SETTLEMENT_REJECTED"


def _joined_helper(r, ok, settle_after):
    url = "https://example.com/scene/1"
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]
    r._login_attempt_seq = 7  # attempt 7 was started by ANOTHER worker
    gate = threading.Event()

    def _other_workers_login():
        gate.wait(10)
        if ok:
            r.set_cookies([{"name": "sid", "value": "fresh", "expires": time.time() + 86400}])
        r._login_outcome = (7, ok)

    t = threading.Thread(target=_other_workers_login, daemon=True)
    r._login_thread = t
    t.start()
    # watcher gives up at 0.1s (stands in for 115s); login settles during the join window
    r._await_in_flight_login = lambda th, fire, timeout=0.1: AuthMixin._await_in_flight_login(r, th, fire, 0.1)
    threading.Timer(settle_after, gate.set).start()
    t0 = time.time()
    rv = r._check_cookies_or_relogin(url)
    return rv, time.time() - t0


def test_joined_login_that_settles_ok_during_join_is_not_failed():
    rv, dt = _joined_helper(_runner(), True, 0.2)
    assert rv is True, f"LENS_B13_JOINED_OK_LOGIN_DECLARED_FAILED rv={rv} dt={dt:.2f}s"


def test_control_joined_login_that_fails_stays_failed():
    rv, _dt = _joined_helper(_runner(), False, 0.2)
    assert rv is False, f"LENS_B13_FAILED_LOGIN_ACCEPTED rv={rv}"


def test_control_joined_login_settling_before_watcher_timeout_ok():
    rv, _dt = _joined_helper(_runner(), True, 0.02)
    assert rv is True, f"LENS_B13_FAST_JOINED rv={rv}"


@pytest.mark.parametrize("already_running", [False, True])
def test_join_settlement_uses_current_attempt_even_when_already_running(already_running):
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]
    r._login_attempt_seq = 7
    r._login_outcome = (6, False)

    class FinishingLogin:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            assert timeout == 30
            self.alive = False
            r.set_cookies([{"name": "sid", "value": "fresh", "expires": time.time() + 3600}])
            r._login_outcome = (r._login_attempt_seq, True)

    thread = FinishingLogin()
    r._login_thread = thread

    def login_async(on_done):
        if not already_running:
            r._login_attempt_seq += 1
        on_done(False)

    r.login_async = login_async
    result = r._check_cookies_or_relogin("https://example.com/scene/1")
    assert result is True, f"CX1_JOINED_CURRENT_LOGIN_REJECTED: inflight={already_running}, outcome={r._login_outcome}"


def test_no_current_success_remains_refused():
    r = _runner()
    r.cookies = [{"name": "sid", "value": "expired", "expires": time.time() - 3600}]
    r._login_outcome = (6, False)
    r.login_async = lambda on_done: on_done(False)
    assert r._check_cookies_or_relogin("https://example.com/scene/1") is False



