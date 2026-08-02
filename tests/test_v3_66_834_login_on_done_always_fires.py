"""v3.66.834 -- ``AuthMixin.login_async`` must resolve ``on_done`` on EVERY path.

``login_async(on_done=cb)`` publishes a callback contract: the caller hands in
``cb`` and blocks until it fires.  The one real consumer is
``AuthMixin._check_cookies_or_relogin`` (``bulk_downloader/runner_auth.py:1016-1018``),
which builds a ``threading.Event``, passes ``_od`` as ``on_done`` and then does
``ev.wait(timeout=60)``.

Three return paths inside ``login_async`` never call it:

  * ``runner_auth.py:177`` -- ``if self._login_thread and ... is_alive(): return``
  * ``runner_auth.py:187`` -- the ``_manual_login_handle`` no-op guard
  * anywhere in the ``_run`` thread body (``:222``-``:303``) that raises -- the
    thread dies and no ``on_done`` call is ever made.

The guards themselves are correct and MUST stay (Phase 19.fix anti-orphan rule,
stated in-source at ``:178-182``): a second login thread and a second browser
window are exactly what they prevent.  Only the NOTIFICATION is defective.  The
consumer therefore burns the full 60 s and then reports "Auto re-login failed"
-- a false verdict delivered slowly.

These tests assert the OBSERVABLE contract only -- "the callback fires, exactly
once, with a bool" and "the consumer does not stall" -- never a particular
internal mechanism, so a fix is free to restructure ``login_async``.

RED cost note: ``test_consumer_does_not_stall_on_the_manual_handle_guard``
takes ~60 s while the defect is present, because 60 s IS the defect.  It drops
to well under a second once ``on_done`` is resolved.
"""
from __future__ import annotations

import ast
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
from bulk_downloader.cookies import cookies_expiry_info
from bulk_downloader.db import db_init
from bulk_downloader.runner import SiteRunner

REPO = Path(__file__).resolve().parents[1]

_LOGIN_BLOCK = {
    "user_field": ["#username"],
    "pass_field": ["#password"],
    "submit_btn": ["#go"],
}


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    """CLAUDE.md section 5: BD_HOME does NOT govern the database -- BD_INSTALL_DIR
    does.  ``clean_workdir`` sets both BD_INSTALL_DIR and the cwd to a tmpdir, so
    ``downloader_history.db`` cannot land in the repo and rows cannot accumulate
    across runs."""
    yield clean_workdir


class _FakeManualSession:
    """Stand-in for a ManualLoginSession handle.  ``_poll_manual_cookies`` (the
    daemon started by ``start_manual_login``) calls ``snapshot_cookies``."""

    def snapshot_cookies(self, timeout=10):
        return []


def _runner(**over):
    db_init()  # SiteRunner reads the queue table on construction; idempotent.
    cfg = {
        "login_url": "https://example.com/login",
        "username": "u",
        "password": "p",
        # A learned template is present, so login_async never diverts through
        # the auto-teach branch at :189-201 (which DOES fire on_done already).
        "auto_teach_first_run": False,
        "learned": {"login": dict(_LOGIN_BLOCK)},
        "manual_use_persistent_profile": False,
    }
    cfg.update(over)
    return SiteRunner("onDoneSite", cfg)


def _fresh_cookie():
    return [{"name": "sid", "value": "1", "expires": time.time() + 86400}]


class _Collector:
    """Records on_done invocations and lets a test wait for the first one."""

    def __init__(self):
        self.calls = []
        self.fired = threading.Event()

    def __call__(self, ok):
        self.calls.append(ok)
        self.fired.set()


@contextmanager
def _captured_thread_exceptions():
    """Swallow (and record) unhandled exceptions raised on daemon threads so a
    crashed login thread shows up as data instead of as pytest's own
    PytestUnhandledThreadExceptionWarning, which would obscure which assertion
    actually failed."""
    seen = []
    previous = threading.excepthook
    threading.excepthook = seen.append
    try:
        yield seen
    finally:
        threading.excepthook = previous


# ── canaries: without these the rules below are assertions about nothing ─────

def test_canary_a_consumer_really_passes_on_done():
    """Canary (CLAUDE.md section 0): if NOTHING passes ``on_done`` to
    ``login_async``, every assertion in this file is about a dead parameter and
    is vacuously satisfiable.  Instrument = AST over every ``.py`` under
    ``bulk_downloader/``; predicate = ``Call`` whose ``func.attr`` is
    ``login_async`` carrying an ``on_done`` keyword."""
    sources = sorted((REPO / "bulk_downloader").rglob("*.py"))
    assert sources, "denominator empty: no bulk_downloader sources found"
    sites = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "login_async"):
                continue
            if any(kw.arg == "on_done" for kw in node.keywords):
                sites.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert sites, (
        "no call site passes on_done to login_async -- the callback contract "
        "this file tests would not exist")


def test_canary_login_async_accepts_on_done():
    """Canary: the parameter is named ``on_done`` and is optional."""
    import inspect

    sig = inspect.signature(SiteRunner.login_async)
    assert "on_done" in sig.parameters
    assert sig.parameters["on_done"].default is None


# ── the rules ────────────────────────────────────────────────────────────────

def test_on_done_fires_when_a_manual_login_is_already_pending():
    """runner_auth.py:183-187.  A pending manual takeover makes login_async a
    no-op -- correctly, it must not open a second browser -- but the caller's
    callback is dropped, so the caller learns nothing and waits."""
    r = _runner()
    r._manual_login_handle = _FakeManualSession()
    cb = _Collector()

    r.login_async(on_done=cb)

    # Canary for THIS test: prove we exercised the guard path and not the
    # ordinary login path (which already notifies).
    assert r._login_thread is None, (
        "expected the _manual_login_handle guard to short-circuit before any "
        "login thread was started")

    assert cb.fired.wait(5.0), (
        "login_async returned via the _manual_login_handle guard without ever "
        "calling on_done -- the caller is left waiting on a callback that will "
        "never arrive")
    assert len(cb.calls) == 1, f"on_done must fire exactly once, got {cb.calls!r}"
    assert cb.calls[0] is False, (
        "a pending manual takeover is 'not logged in yet', so the resolved "
        f"value must be False, got {cb.calls[0]!r}")


def test_on_done_fires_when_a_login_is_already_in_flight():
    """runner_auth.py:177.  A second login_async while one is running must not
    start a second thread -- and must not silently discard the second caller's
    callback either.  The in-flight login here SUCCEEDS, so the second caller
    is entitled to learn that."""
    r = _runner()
    gate = threading.Event()
    entered = threading.Event()

    def _slow(config, allow_manual_takeover=True):
        entered.set()
        gate.wait(30)
        return (True, "logged in", _fresh_cookie())

    cb = _Collector()
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_slow):
        r.login_async()  # first login, no callback
        assert entered.wait(10), "do_login was never reached on the first call"
        first_thread = r._login_thread
        # Canary for THIS test: the :177 precondition must actually hold.
        assert first_thread is not None and first_thread.is_alive(), (
            "expected a live login thread; without one the second call would "
            "take the ordinary path and this test would prove nothing")

        r.login_async(on_done=cb)  # second call -> the :177 guard

        assert r._login_thread is first_thread, (
            "the guard must leave the in-flight thread in place -- "
            "_handle_auth_required:899 joins exactly this object")
        gate.set()
        first_thread.join(15)

    assert cb.fired.wait(15.0), (
        "login_async returned via the in-flight guard without ever calling "
        "on_done -- the callback was dropped permanently")
    assert len(cb.calls) == 1, f"on_done must fire exactly once, got {cb.calls!r}"
    assert cb.calls[0] is True, (
        "the login that was in flight succeeded, so the waiting caller must be "
        f"told True, got {cb.calls[0]!r}")


def test_on_done_fires_when_the_login_thread_crashes():
    """runner_auth.py:222-303 has no try/except/finally around the body.  Any
    exception (do_login's own escape surface at login_impl/submit.py:511-606 is
    unguarded) kills the daemon thread with the callback unresolved AND leaves
    the UI status pinned at 'Logging in...' forever."""
    r = _runner()
    cb = _Collector()

    with _captured_thread_exceptions():
        with mock.patch("bulk_downloader.runner_auth.do_login",
                        side_effect=RuntimeError("boom")):
            r.login_async(on_done=cb)
            thread = r._login_thread
            # Canary for THIS test: the crash path requires a real thread.
            assert thread is not None, "no login thread was started"
            thread.join(30)

    assert cb.fired.wait(10.0), (
        "the login thread died with on_done unresolved -- the caller waits the "
        "full 60 s and then reports a failure it never actually observed")
    assert len(cb.calls) == 1, f"on_done must fire exactly once, got {cb.calls!r}"
    assert cb.calls[0] is False

    assert not r._login_status.startswith("Logging in"), (
        "a crashed login must not leave the operator-facing status stuck on the "
        f"in-progress placeholder, got {r._login_status!r}")


def test_a_raising_on_done_does_not_kill_the_login_thread():
    """The callback is caller-supplied code.  Invoking it bare means a caller
    bug becomes a login-subsystem crash -- and on the MANUAL_PENDING branch
    (:231) it would also skip the ``return`` and fall through into the Phase B
    fallback."""
    r = _runner()

    def _boom(ok):
        raise ValueError("callback exploded")

    with _captured_thread_exceptions() as seen:
        with mock.patch("bulk_downloader.runner_auth.do_login",
                        return_value=(True, "logged in", _fresh_cookie())):
            r.login_async(on_done=_boom)
            thread = r._login_thread
            assert thread is not None, "no login thread was started"
            thread.join(30)

    # Canary for THIS test: the hook is armed and would have recorded a crash.
    assert threading.excepthook is not None

    assert not seen, (
        "a raising on_done propagated out of login_async's _run thread: "
        f"{[getattr(a, 'exc_value', a) for a in seen]!r} -- the callback must "
        "be invoked defensively")


def test_consumer_does_not_stall_on_the_manual_handle_guard():
    """End-to-end, the reason this matters.  _check_cookies_or_relogin
    (runner_auth.py:1001) is the sole on_done consumer; it waits 60 s at :1018.
    With a manual takeover pending, login_async returns instantly and silently,
    so the worker burns 60 s of wall clock and then calls
    _handle_failure(url, 'Auto re-login failed') -- the same verdict it could
    have delivered immediately, and a misleading one.

    The verdict is NOT what flips here (False is correct either way); the WAIT
    is.  Wall clock is therefore the only available observable."""
    r = _runner()
    r.cookies = [{"name": "a", "value": "1", "expires": time.time() - 3600}]
    r._manual_login_handle = _FakeManualSession()

    # Canary (section 0): if the expiry precondition does not hold,
    # _check_cookies_or_relogin returns True at :1010/:1013 without ever
    # calling login_async, and the timing assertion below measures nothing.
    ei = cookies_expiry_info(r.cookies)
    assert ei["expired"] > 0 and ei["session"] == 0, (
        f"precondition not met, the relogin branch is unreachable: {ei!r}")
    assert r.config.get("username") and r.config.get("password"), (
        "no credentials -> :1022 short-circuits and the relogin branch is "
        "unreachable")

    started = time.monotonic()
    verdict = r._check_cookies_or_relogin("http://example.com/video/1")
    elapsed = time.monotonic() - started

    # Proves the relogin branch really ran (True would mean we short-circuited).
    assert verdict is False, (
        "expected the relogin branch to run and route the URL to failure; "
        "True means the precondition canary above is lying")
    assert elapsed < 15.0, (
        f"_check_cookies_or_relogin blocked {elapsed:.1f}s waiting for an "
        "on_done that login_async never calls; the 60 s ev.wait at "
        "runner_auth.py:1018 expired instead of being resolved")


def test_in_flight_watcher_reports_the_login_result_not_a_shared_timestamp():
    """The in-flight watcher must read the attempt's OWN outcome.

    Inferring success from ``_cookies_updated_at`` is wrong in the dangerous
    direction: any other code path that calls ``set_cookies`` -- an account
    switch, the transport layer, the sites API -- bumps that timestamp, so a
    login that FAILED while such a write raced it reads as success and the
    waiting worker proceeds to download with a dead jar.

    Measured against the timestamp predicate this scenario reported True with
    the jar fully expired; it must report False.
    """
    r = _runner()
    gate = threading.Event()
    entered = threading.Event()

    def _failing(config, allow_manual_takeover=True):
        entered.set()
        gate.wait(30)
        return (False, "bad credentials", [])

    cb = _Collector()
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_failing):
        r.login_async(allow_manual=False)  # first login, will FAIL
        assert entered.wait(10), "do_login was never reached"
        first_thread = r._login_thread
        assert first_thread is not None and first_thread.is_alive()

        r.login_async(on_done=cb, allow_manual=False)  # second caller -> :177

        # A concurrent, unrelated cookie write lands while the login is still
        # in flight -- exactly what an account switch does. The jar is expired.
        r.set_cookies([{"name": "sid", "value": "x", "expires": time.time() - 3600}])
        r._cookies_updated_at = time.time()

        gate.set()
        first_thread.join(15)

    assert cb.fired.wait(15.0), "the second caller's on_done never fired"
    assert len(cb.calls) == 1, f"on_done must fire exactly once, got {cb.calls!r}"
    assert cb.calls[0] is False, (
        "the in-flight login FAILED; a concurrent set_cookies bumping "
        "_cookies_updated_at must not be read as this login succeeding -- "
        f"got {cb.calls[0]!r}, which would send a worker at an expired jar")


def test_watcher_timeout_stays_under_the_consumer_wait():
    """The waiter's default must land before _check_cookies_or_relogin gives up.

    These two numbers are coupled by contract, not by construction -- nothing
    in the code links them, so this is the gate whose denominator contains the
    invariant (CLAUDE.md section 0).
    """
    import inspect
    from bulk_downloader.runner_auth import AuthMixin

    waiter_default = inspect.signature(
        AuthMixin._await_in_flight_login).parameters["timeout"].default
    src = inspect.getsource(AuthMixin._check_cookies_or_relogin)
    waits = [int(m) for m in re.findall(r"ev\.wait\(timeout=(\d+)\)", src)]
    assert waits, (
        "no ev.wait(timeout=N) found in _check_cookies_or_relogin -- the "
        "consumer's wait moved and this pin can no longer see its subject")
    assert waiter_default < min(waits), (
        f"watcher default {waiter_default}s must be strictly under the "
        f"consumer's {min(waits)}s wait, or the callback lands after the "
        "consumer has already given up and reported a false failure")
