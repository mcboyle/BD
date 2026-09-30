"""fx-manual-cancel-noop (O1567, test3 adulttime 2026-09-29 23:1xZ).

MEASURED live: POST /api/sites/f5d491e5/login_manual_cancel answered
{"ok":true,"message":"Cancelled"} and the takeover window stayed on the
display; only a service restart closed it (results/test3/adulttime-cookies.md).

Cause: do_login's hand-off returns ("MANUAL_PENDING", reason, (pw, browser, ctx)).
That is sync Playwright, bound to the login thread, and the login thread exits
right after storing it.  Cancel (and I'm Done) then call browser.close() /
ctx.cookies() from a request thread.  Playwright raises "cannot switch to a
different thread", the error is swallowed, and the window stays open with no
cookies read (local control: results/test3/FINDING-takeover-cross-thread.md).

Contract: the login thread closes the hand-off browser itself, while it still
owns it, and opens a thread-owned manual window.  Cancel then really closes the
window, and a cancel that cannot close it says so instead of "Cancelled".
"""
from __future__ import annotations

import os
import threading
from unittest import mock

import pytest

BD_GATE_SCOPE = "module"

REASON = "Cloudflare 'Verify you are human' challenge not cleared by the automated clicks"


class _Owned:
    """A sync-Playwright stand-in: every call from a thread other than the
    one that made it raises like greenlet does, and changes nothing."""

    def __init__(self, log, name):
        self._owner = threading.current_thread()
        self._log = log
        self._name = name
        self.closed = False

    def _check(self, op):
        if threading.current_thread() is not self._owner:
            self._log.append(f"{self._name}.{op} RAISED cross-thread")
            raise RuntimeError("cannot switch to a different thread (which happens to have exited)")
        self._log.append(f"{self._name}.{op}")

    def close(self):
        self._check("close")
        self.closed = True

    def stop(self):
        self._check("stop")
        self.closed = True

    def cookies(self):
        self._check("cookies")
        return []


from bulk_downloader.login_impl.manual import ManualLoginSession


class _Session(ManualLoginSession):
    """Stand-in for a thread-owned ManualLoginSession (no browser)."""

    def __init__(self, closes=True):  # noqa: D401 -- no real launch
        self.closes = closes
        self.cancelled = 0

    def cancel(self, timeout=10):
        self.cancelled += 1
        return self.closes

    def snapshot_cookies(self, timeout=10):
        return []


@pytest.fixture
def takeover(clean_workdir, monkeypatch):
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app_sites_auth as sa
    from bulk_downloader.app import app
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner
    db_init()
    runner = SiteRunner("o1567cn", {
        "login_url": "https://freetour.example.invalid/en/login",
        "success_url": "members.example.invalid",
        "username": "u", "password": "p",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"], "submit_btn": ["#go"]}},
        "manual_use_persistent_profile": False,
    })
    monkeypatch.setattr(sa, "_app_runners", lambda: {"o1567cn": runner})
    monkeypatch.setattr(sa, "_LOGIN_WAIT_S", 20.0, raising=False)
    log, made = [], {}

    def do_login(*a, **k):  # runs ON the login thread, like the real one
        made["pw"], made["browser"], made["ctx"] = (
            _Owned(log, "pw"), _Owned(log, "browser"), _Owned(log, "ctx"))
        return ("MANUAL_PENDING", REASON, (made["pw"], made["browser"], made["ctx"]))

    session = _Session()
    opened = mock.Mock(return_value=session)
    monkeypatch.setattr("bulk_downloader.login.open_manual_login_browser", opened)

    def post():
        with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=do_login):
            with app.test_request_context("/api/sites/o1567cn/login", method="POST", json={}):
                resp = sa.api_login("o1567cn")
            t = getattr(runner, "_login_thread", None)
            if t is not None:
                t.join(20)
        if isinstance(resp, tuple):
            resp = resp[0]
        return resp.get_json()

    try:
        yield runner, post, made, log, session, opened
    finally:
        snap = getattr(runner, "_manual_snapshot_stop", None)
        if snap is not None:
            snap.set()
        runner._manual_login_handle = None


def test_handoff_browser_is_closed_by_its_own_thread_and_a_thread_owned_window_opens(takeover, capsys):
    runner, post, made, log, session, opened = takeover
    body = post()
    err = capsys.readouterr().err
    # Live test3 23:53Z: the extracted window opener lost start_manual_login's
    # session_keeper import ("keeper pause failed (name '_sk' is not defined)").
    assert "keeper pause failed" not in err, (
        "O1567-TAKEOVER-KEEPER-PAUSE: the manual window opened without pausing the site's "
        f"session keepers: {[l for l in err.splitlines() if 'keeper pause' in l]}")
    assert made, "precondition: do_login handed off"
    assert made["browser"].closed and made["pw"].closed, (
        "O1567-TAKEOVER-CROSS-THREAD: the login thread left its own Playwright hand-off open; "
        f"a later cancel/I'm Done from another thread cannot close it. calls={log}")
    assert not any("RAISED" in x for x in log), log
    assert opened.call_count == 1 and runner._manual_login_handle is session, (
        "O1567-TAKEOVER-CROSS-THREAD: no thread-owned manual window replaced the hand-off "
        f"(handle={runner._manual_login_handle!r})")
    # The hand-off contract the UI reads is unchanged (dl95-blacked-2).
    assert body.get("ok") is False and "Manual login required" in body.get("error", ""), body
    assert REASON[:40] in body.get("error", ""), body


def test_cancel_closes_the_window_it_reports(takeover):
    runner, post, made, log, session, opened = takeover
    post()
    ok, msg = runner.cancel_manual_login_pending()
    assert (ok, session.cancelled) == (True, 1), (ok, msg, session.cancelled)
    assert runner._manual_login_handle is None


def test_a_cancel_that_cannot_close_the_window_says_so(takeover):
    runner, post, made, log, session, opened = takeover
    post()
    session.closes = False
    ok, msg = runner.cancel_manual_login_pending()
    assert ok is False and "did not close" in msg, (
        f"O1567-CANCEL-SAYS-CANCELLED: a window that stayed open was reported {ok!r} {msg!r}")
    assert runner._manual_login_handle is session, "keep the handle so I'm Done / cancel can retry"


def test_control_a_non_tuple_handle_is_stored_as_before(takeover, monkeypatch):
    """A handle that is not the (pw, browser, ctx) tuple is left alone."""
    runner, post, made, log, session, opened = takeover
    other = object()
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=("MANUAL_PENDING", REASON, other)):
        runner.login_async()
        runner._login_thread.join(20)
    assert runner._manual_login_handle is other and opened.call_count == 0
