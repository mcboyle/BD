"""O1567 fx-login-async-double-start: two workers that hit auth at the same moment start TWO live logins.

MEASURED on test4 (10.0.70.85) 2026-09-29, site-ma-brazzers 1167e615: session_history rows 4868/4869 are two
``login_attempt`` reservations (source runner_auth.login_async) 1 ms apart (21:20:52.267/.268Z) and the journal
shows every login step twice ("browser profile = cloak default", "cleared consent gate", "filled username",
"login submit") -- two browsers submitting the same account at once; the site then answered badlogin.

Cause: login_async reads ``self._login_thread.is_alive()`` at the top and publishes/starts the new thread at the
bottom with nothing holding the two together, so both workers' re-login calls pass the in-flight check before
either publishes (the publish helper overwrites ``_login_thread`` without looking at it).

Rule: the in-flight check, the attempt stamp and the thread publish are one step; a caller that loses the race
awaits the in-flight attempt (its on_done fires with THAT attempt's outcome) and spends no login.

Hermetic: real SiteRunner.login_async + real session_keeper accounting on the isolated DB; do_login is a stub.
"""
from __future__ import annotations

import os
import sys
import threading
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import session_keeper  # noqa: E402
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SITE = "o1567DoubleLogin"


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


def _runner():
    db_init()
    cfg = {
        "login_url": "https://example.com/login",
        "username": "u",
        "password": "p",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#username"], "pass_field": ["#password"], "submit_btn": ["#go"]}},
        "manual_use_persistent_profile": False,
        session_keeper.LOGIN_CAP_KEY: 10,
    }
    r = SiteRunner(SITE, cfg)
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")   # never a real browser
    return r


class _StubLogin:
    """do_login stand-in: counts calls and holds each one until released."""

    def __init__(self):
        self.calls = 0
        self.release = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, *a, **k):
        with self._lock:
            self.calls += 1
        self.release.wait(10)
        return (False, "test: rejected", [])


def _counted():
    got = session_keeper.login_attempts_for_day(SITE)
    assert got["status"] == "OK", got
    return got["count"]


def _race_two_callers(r, stub):
    """Both callers pass the top-of-function in-flight check before either publishes a thread: the
    barrier sits on the 'Logging in...' status write that runs between the check and the publish."""
    barrier = threading.Barrier(2, timeout=3)
    real_status = r._set_login_status

    def _status(s):
        if s == "Logging in...":
            try:
                barrier.wait()
            except threading.BrokenBarrierError as barrier_exc:  # O805 DP-13: named; a broken barrier is the test's own timeout
                sys.stderr.write(f"  test: login barrier broken ({barrier_exc})\n")
        real_status(s)

    r._set_login_status = _status
    fired = []
    with mock.patch("bulk_downloader.runner_auth.do_login", stub):
        callers = [threading.Thread(target=r.login_async,
                                    kwargs={"on_done": fired.append, "allow_manual": False})
                   for _ in range(2)]
        for t in callers:
            t.start()
        for t in callers:
            t.join(8)
        threading.Event().wait(0.3)   # let a second login thread (the defect) reach do_login
        stub.release.set()
        th = r._login_thread
        if th is not None:
            th.join(10)
        deadline = threading.Event()
        for _ in range(50):
            if len(fired) == 2:
                break
            deadline.wait(0.1)
    return fired


def test_two_workers_hitting_auth_together_start_one_live_login():
    r = _runner()
    stub = _StubLogin()
    fired = _race_two_callers(r, stub)
    assert stub.calls == 1, (
        f"O1567 DOUBLE LOGIN: two concurrent login_async callers ran do_login {stub.calls} times "
        "(two browsers submitting one account)")
    assert _counted() == 1, f"O1567 DOUBLE LOGIN: {_counted()} login attempts spent for one re-login"
    assert fired == [False, False], f"both callers must get the in-flight attempt's outcome, got {fired}"


def test_control_sequential_logins_still_each_run():
    r = _runner()
    stub = _StubLogin()
    stub.release.set()
    with mock.patch("bulk_downloader.runner_auth.do_login", stub):
        for _ in range(2):
            r.login_async(allow_manual=False)
            r._login_thread.join(10)
            assert not r._login_thread.is_alive()
    assert stub.calls == 2, f"a finished login must not block the next one (calls={stub.calls})"
    assert _counted() == 2
