"""v3.66.835 -- the Phase B manual-takeover fallback must be able to OPEN.

``login_async``'s Phase B branch (bulk_downloader/runner_auth.py:266-301) exists
so that a templated site whose auto-login fails is not left dead: it logs
``login_template_fallback``, persists the event, and calls
``self.start_manual_login()`` at ``:295`` to hand the browser to the operator.

That call runs on the ``_run`` daemon thread -- i.e. on ``self._login_thread``
itself.  ``start_manual_login``'s own guard at ``:331``::

    if self._login_thread and self._login_thread.is_alive():
        return False, "An auto-login is already running"

therefore refuses its own caller, and the takeover window is NEVER opened.  The
operator sees ``"manual fallback also failed: An auto-login is already
running"`` and the site stays dead -- the exact outcome Phase B was written to
prevent.

This is a SEPARATE defect from the on_done contract (v3.66.834): this path DOES
call ``on_done(False)`` at ``:300``.  Conflating the two is the trap.

Why no existing test sees it: ``tests/test_v3_62_2_login_fallback.py`` mocks
``start_manual_login`` at :61/:75/:101/:116/:129, so its denominator
structurally excludes the guard under test (CLAUDE.md section 0).  These tests
deliberately leave ``start_manual_login`` REAL and mock one level deeper, at
``open_manual_login_browser`` -- the last thing before a real Chromium launch.
``tests/test_u36_login_live_tests.py:180-186`` already records the behaviour in
a comment.
"""
from __future__ import annotations

import os
import threading
import time
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
from bulk_downloader.db import db_conn, db_init
from bulk_downloader.runner import SiteRunner

_LOGIN_BLOCK = {
    "user_field": ["#username"],
    "pass_field": ["#password"],
    "submit_btn": ["#go"],
}

LOGIN_URL = "https://example.com/login"


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    """CLAUDE.md section 5: BD_INSTALL_DIR (not BD_HOME) governs the sqlite
    path.  ``clean_workdir`` sets BD_INSTALL_DIR and chdirs to a tmpdir, so
    ``downloader_history.db`` cannot be written into the repo."""
    yield clean_workdir


class _FakeManualSession:
    """Stand-in for the ManualLoginSession returned by
    ``open_manual_login_browser``.  ``start_manual_login`` spawns
    ``_poll_manual_cookies`` against it (runner_auth.py:364-368), which calls
    ``snapshot_cookies``."""

    def __init__(self):
        self.snapshots = 0

    def snapshot_cookies(self, timeout=10):
        self.snapshots += 1
        return []


def _runner():
    db_init()  # SiteRunner reads the queue table on construction; idempotent.
    return SiteRunner("takeoverSite", {
        "login_url": LOGIN_URL,
        "username": "u",
        "password": "p",
        # A learned template is present: this is exactly the "first-run manual
        # teach was SKIPPED" state that Phase B is the recovery path for.
        "auto_teach_first_run": True,
        "learned": {"login": dict(_LOGIN_BLOCK)},
        # Keep the real start_manual_login off _manual_profile_dir().
        "manual_use_persistent_profile": False,
    })


def _stop_poller(runner):
    stop = getattr(runner, "_manual_snapshot_stop", None)
    if stop is not None:
        stop.set()


def _fallback_events(site_id):
    with db_conn() as cx:
        return [dict(row) for row in cx.execute(
            "SELECT event_type, detail FROM session_history "
            "WHERE site_id = ? AND event_type = 'login_template_fallback'",
            (site_id,)).fetchall()]


def test_phase_b_fallback_actually_opens_the_takeover_window():
    """The rule.  With ``start_manual_login`` left REAL, a templated auto-login
    failure must reach ``open_manual_login_browser``."""
    r = _runner()
    opened = []

    def _open(config, manual_profile_dir=None, headless=False):
        opened.append(config.get("login_url"))
        return _FakeManualSession()

    try:
        with mock.patch("bulk_downloader.runner_auth.do_login",
                        return_value=(False, "page load timeout", [])), \
             mock.patch("bulk_downloader.login.open_manual_login_browser",
                        side_effect=_open):
            r.login_async(allow_manual=True)
            thread = r._login_thread
            assert thread is not None, "no login thread was started"
            thread.join(30)

        # Canary (CLAUDE.md section 0): if Phase B were never entered, the
        # `opened` assertion below would be about a branch that did not run --
        # true, and useless.  session_event_record fires at runner_auth.py:285,
        # BEFORE start_manual_login at :295, so this pins reachability
        # independently of the outcome under test.
        events = _fallback_events("takeoverSite")
        assert events, (
            "Phase B was never entered -- no login_template_fallback event was "
            "persisted, so the takeover assertion below would be vacuous")
        assert "page load timeout" in events[0]["detail"]

        assert opened == [LOGIN_URL], (
            "the Phase B manual-takeover fallback never opened a browser: "
            "start_manual_login's alive-guard at runner_auth.py:331 refused its "
            "own caller (login_async's _run thread IS self._login_thread), so "
            f"the site is left dead. status={r._login_status!r}")
        assert r._manual_login_handle is not None, (
            "no manual-login handle was stored, so 'I'm Done' has nothing to "
            "finalize")
    finally:
        _stop_poller(r)


def test_phase_b_fallback_status_is_not_the_self_refusal_message():
    """The operator-facing symptom, asserted directly: the status must not tell
    the user the fallback failed because 'an auto-login is already running' --
    the auto-login in question is the very thread asking for the takeover."""
    r = _runner()

    try:
        with mock.patch("bulk_downloader.runner_auth.do_login",
                        return_value=(False, "page load timeout", [])), \
             mock.patch("bulk_downloader.login.open_manual_login_browser",
                        side_effect=lambda *a, **k: _FakeManualSession()):
            r.login_async(allow_manual=True)
            thread = r._login_thread
            assert thread is not None, "no login thread was started"
            thread.join(30)

        # Canary: same reachability pin as above.
        assert _fallback_events("takeoverSite"), (
            "Phase B was never entered; the status assertion below is vacuous")

        assert "already running" not in r._login_status.lower(), (
            "Phase B reported its own thread as a blocking auto-login: "
            f"{r._login_status!r}")
    finally:
        _stop_poller(r)


def test_the_alive_guard_still_refuses_an_external_caller():
    """Preservation pin, not a RED assertion.  This PASSES on pristine source
    and must keep passing: the fix may only exempt the login thread itself.
    Every other caller (the UI's Manual Login button, app_sites_auth, the
    wizard) must still be refused while an auto-login is in flight, or Phase
    19.fix's orphaned-browser bug comes back."""
    r = _runner()
    gate = threading.Event()
    entered = threading.Event()
    opened = []

    def _slow(config, allow_manual_takeover=True):
        entered.set()
        gate.wait(30)
        return (False, "page load timeout", [])

    try:
        with mock.patch("bulk_downloader.runner_auth.do_login",
                        side_effect=_slow), \
             mock.patch("bulk_downloader.login.open_manual_login_browser",
                        side_effect=lambda *a, **k: opened.append(1)
                        or _FakeManualSession()):
            r.login_async(allow_manual=False)  # allow_manual=False: no Phase B
            assert entered.wait(10), "do_login was never reached"
            # Canary: the guard's precondition must actually hold.
            assert r._login_thread is not None and r._login_thread.is_alive()
            assert threading.current_thread() is not r._login_thread

            ok, msg = r.start_manual_login()   # an EXTERNAL caller

            assert ok is False, (
                "an external caller must not be allowed to open a second "
                "browser while an auto-login is in flight")
            assert "already running" in msg.lower(), msg
            assert not opened, "a second browser was opened behind the guard"

            gate.set()
            r._login_thread.join(15)
    finally:
        gate.set()
        _stop_poller(r)
