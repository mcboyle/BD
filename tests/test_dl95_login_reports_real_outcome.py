"""dl95-vip4k-2 (O1513, harness-work/UIUX-20260928/download-95/A5-A/RESULT-login.md L2; also A1-A FINDINGS A4 / dl95-txxx-1).

Measured on test2: POST /api/sites/<sid>/login returned 200 {"ok": true} for vip4k and txxx while the login FAILED (txxx journal:
"GET form -- stopping the method sweep ... handing off for manual takeover"); Verify straight after showed the failure, and
/api/sites/v2 kept auth_state "ok". Two causes:
  * api_login returned ok:true as soon as login_async STARTED (the UI toasts "Done");
  * _m2_auth_state reads cookie expiry only, so an anonymous/session cookie jar reads "ok" after a failed login.

Contract after the fix:
  * POST /login waits (bounded) for the attempt's own settled outcome (login_async's exactly-once on_done) and returns it:
    ok:false + the login status on failure, 202 state=pending if it has not settled in time; {"async": true} keeps the old
    fire-and-return behaviour.
  * auth_state is "expired" while the latest settled login attempt FAILED and the jar has not been replaced since.
"""

from __future__ import annotations

import threading
import time

import pytest

BD_GATE_SCOPE = "module"

SESSION_JAR = [{"name": "PHPSESSID", "value": "x", "domain": "vip4k.com", "path": "/"}]


class _Runner:
    """login_async double honouring the real stamping contract: a started attempt bumps _login_attempt_seq,
    runs on _login_thread and settles _login_outcome = (seq, ok). outcome=None never settles (until released)."""

    def __init__(
        self,
        outcome=None,
        refusal=None,
        delay=0.05,
        status="✗ login failed: still on the login page",
    ):
        self.outcome, self.refusal, self.delay, self.status = (
            outcome,
            refusal,
            delay,
            status,
        )
        self._login_status = "Logging in..."
        self._login_attempt_seq = 7
        self._login_outcome = (
            7,
            True,
        )  # a stale earlier success must not be read as this attempt's result
        self._login_thread = None
        self.calls = []
        self.release = threading.Event()

    def login_async(self, on_done=None, allow_manual=True):
        self.calls.append(on_done)
        if self.refusal:
            return self.refusal
        self._login_attempt_seq += 1
        seq = self._login_attempt_seq

        def _run():
            if self.outcome is None:
                self.release.wait(10)
                return
            time.sleep(self.delay)
            self._login_status = self.status
            self._login_outcome = (seq, self.outcome)

        self._login_thread = threading.Thread(target=_run, daemon=True)
        self._login_thread.start()
        return None


class _ManualRunner(_Runner):
    """First-run teach path: opens the manual browser, starts no attempt and no thread."""

    def login_async(self, on_done=None, allow_manual=True):
        self.calls.append(on_done)
        self._login_status = "⏳ Manual login browser opened"


@pytest.fixture
def login(monkeypatch):
    from bulk_downloader import app_sites_auth as sa
    from bulk_downloader.app import app

    runners = {}
    monkeypatch.setattr(sa, "_app_runners", lambda: runners)
    monkeypatch.setattr(sa, "_LOGIN_WAIT_S", 2.0, raising=False)

    def call(runner, body=None):
        runners["s1"] = runner
        with app.test_request_context(
            "/api/sites/s1/login", method="POST", json=body or {}
        ):
            resp = sa.api_login("s1")
        code = 200
        if isinstance(resp, tuple):
            resp, code = resp[0], resp[1]
        return code, resp.get_json()

    return call


def test_failed_login_returns_ok_false_with_its_status(login):
    code, body = login(_Runner(outcome=False))
    assert body.get("ok") is False, f"DL95_LOGIN_OK_ON_FAILURE: {code} {body}"
    assert body.get("state") == "failed" and "still on the login page" in body.get(
        "error", ""
    ), body


def test_successful_login_returns_ok_true(login):
    code, body = login(_Runner(outcome=True, status="✓ Logged in"))
    assert (
        code == 200 and body.get("ok") is True and body.get("state") == "logged_in"
    ), body


def test_unsettled_login_is_pending_not_ok(login):
    r = _Runner(outcome=None)
    code, body = login(r)
    r.release.set()
    assert code == 202 and body.get("ok") is False and body.get("state") == "pending", (
        code,
        body,
    )


def test_refusal_unchanged(login):
    code, body = login(_Runner(refusal="no login_url configured"))
    assert code == 503 and body == {"ok": False, "error": "no login_url configured"}


def test_async_opt_out_keeps_fire_and_return(login):
    r = _Runner(outcome=None)
    code, body = login(r, {"async": True})
    r.release.set()
    assert code == 200 and body.get("ok") is True and body.get("state") == "started", (
        body
    )
    assert r.calls == [None]


def test_crashed_attempt_without_a_result_is_failed(login):
    r = _Runner(outcome=None)
    r.release.set()  # the thread ends at once without settling
    code, body = login(r)
    assert body.get("ok") is False and body.get("state") == "failed", (code, body)


def test_manual_browser_path_keeps_the_old_answer(login):
    """Control (test_login_api_refuses_impossible_manual_start pins it): no attempt started -> {"ok": true}."""
    code, body = login(_ManualRunner())
    assert (code, body) == (200, {"ok": True})


# ── auth_state ──────────────────────────────────────────────────────────


class _AuthRunner:
    def __init__(self):
        self.cookies = list(SESSION_JAR)
        self._cookies_updated_at = 100.0


def _auth(r):
    from bulk_downloader.app import _m2_auth_state

    return _m2_auth_state(r, {})


def test_failed_login_after_the_jar_reads_expired():
    r = _AuthRunner()
    assert _auth(r) == "ok", "precondition: a session-cookie jar reads ok"
    r._login_attempt_seq, r._login_outcome, r._login_outcome_at = 3, (3, False), 200.0
    assert _auth(r) == "expired", "DL95_AUTH_STATE_OK_AFTER_FAILED_LOGIN"


def test_jar_replaced_after_the_failure_reads_ok_again():
    r = _AuthRunner()
    r._login_attempt_seq, r._login_outcome, r._login_outcome_at = 3, (3, False), 200.0
    r._cookies_updated_at = (
        300.0  # manual login done / cookie import after the failed attempt
    )
    assert _auth(r) == "ok"


def test_successful_latest_login_reads_ok():
    r = _AuthRunner()
    r._login_attempt_seq, r._login_outcome, r._login_outcome_at = 4, (4, True), 200.0
    assert _auth(r) == "ok"


def test_empty_jar_stays_unknown_even_after_failure():
    r = _AuthRunner()
    r.cookies = []
    r._login_attempt_seq, r._login_outcome, r._login_outcome_at = 3, (3, False), 200.0
    assert _auth(r) == "unknown"


def test_real_failed_login_async_makes_auth_state_expired(clean_workdir):
    """End to end on a real SiteRunner: a login_async whose do_login fails settles False, stamps when, and
    the session-cookie jar that read "ok" before now reads "expired" (the test2 txxx/vip4k shape)."""
    import os
    from unittest import mock

    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    r = SiteRunner(
        "dl95vip",
        {
            "login_url": "https://example.invalid/login",
            "username": "u",
            "password": "p",
            "auto_teach_first_run": False,
            "learned": {
                "login": {
                    "user_field": ["#u"],
                    "pass_field": ["#p"],
                    "submit_btn": ["#go"],
                }
            },
            "manual_use_persistent_profile": False,
        },
    )
    r.set_cookies(list(SESSION_JAR))
    assert _auth(r) == "ok", "precondition: the jar reads ok before the failed attempt"
    fired = threading.Event()
    got = []
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=False):
        r.login_async(
            on_done=lambda ok: (got.append(ok), fired.set()), allow_manual=False
        )
        assert fired.wait(30), "on_done never fired"
        r._login_thread.join(30)
    assert got == [False]
    assert (
        r._login_outcome[1] is False
        and getattr(r, "_login_outcome_at", 0) >= r._cookies_updated_at
    )
    assert _auth(r) == "expired", (
        "DL95_AUTH_STATE_OK_AFTER_FAILED_LOGIN (real login_async)"
    )
