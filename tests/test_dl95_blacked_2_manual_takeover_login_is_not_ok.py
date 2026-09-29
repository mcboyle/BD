"""dl95-blacked-2 (DOT95-LANE/live-dl95-blacked-1/LIVE-RESULT-A5-A.md probe 2, BK1__1-login.png).

Measured on test2: blacked's worker login stayed on login.vixen.com and handed off for manual takeover
("post-submit page did not redirect within 30s ... handing off for manual takeover"), yet
POST /api/sites/<sid>/login returned {"ok": true}.

Contract (reporting only; the SSO form itself is not driven any further):
  * the attempt that hands off (do_login -> ("MANUAL_PENDING", reason, handle)) answers ok:false with the
    "Manual login required: <reason>" status (dl95-vip4k-2 settled-outcome wait; pinned here on a real runner);
  * a Login click while that takeover is still pending starts nothing (login_async's anti-orphan guard) and
    must answer ok:false state=manual_pending with the reason -- not {"ok": true}.
The first-run teach path that really opens a manual browser keeps {"ok": true}
(test_login_api_refuses_impossible_manual_start, test_dl95_login_reports_real_outcome pin it).
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

BD_GATE_SCOPE = "module"

REASON = (
    "post-submit page did not redirect within 30s -- Expected URL contains "
    "members.blacked.com, got login.vixen.com/i/blacked/login"
)


class _Handle:
    """Stand-in for the (pw, browser, ctx) takeover handle; never driven."""


@pytest.fixture
def blacked(clean_workdir, monkeypatch):
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app_sites_auth as sa
    from bulk_downloader.app import app
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    runner = SiteRunner(
        "dl95blk2",
        {
            "login_url": "https://login.example.invalid/i/blacked/login",
            "success_url": "members.example.invalid",
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
    monkeypatch.setattr(sa, "_app_runners", lambda: {"dl95blk2": runner})
    monkeypatch.setattr(sa, "_LOGIN_WAIT_S", 20.0, raising=False)
    handle = _Handle()
    do_login = mock.Mock(return_value=("MANUAL_PENDING", REASON, handle))

    def post():
        with mock.patch("bulk_downloader.runner_auth.do_login", do_login):
            with app.test_request_context(
                "/api/sites/dl95blk2/login", method="POST", json={}
            ):
                resp = sa.api_login("dl95blk2")
            thread = getattr(runner, "_login_thread", None)
            if thread is not None:
                thread.join(20)
        code = 200
        if isinstance(resp, tuple):
            resp, code = resp[0], resp[1]
        return code, resp.get_json()

    try:
        yield runner, handle, do_login, post
    finally:
        runner._manual_login_handle = None


def test_handoff_attempt_answers_not_ok_with_the_reason(blacked):
    runner, handle, do_login, post = blacked
    code, body = post()
    assert do_login.call_count == 1
    assert runner._manual_login_handle is handle, "precondition: takeover pending"
    assert body.get("ok") is False and body.get("state") == "failed", (
        f"DL95_BLACKED_HANDOFF_OK_TRUE: {code} {body}"
    )
    assert "Manual login required" in body.get("error", "") and "login.vixen.com" in body["error"], body


def test_login_while_takeover_pending_is_not_ok(blacked):
    runner, handle, do_login, post = blacked
    post()  # the attempt that hands off
    code, body = post()  # operator clicks Login again; the takeover is still open
    assert do_login.call_count == 1, "a pending takeover must not start a second login"
    assert runner._manual_login_handle is handle
    assert body.get("ok") is False, (
        f"DL95_BLACKED_PENDING_TAKEOVER_OK_TRUE: {code} {body}"
    )
    assert body.get("state") == "manual_pending", body
    assert "Manual login required" in body.get("error", "") and "login.vixen.com" in body["error"], body


def test_takeover_finished_then_login_starts_a_new_attempt(blacked):
    """Control: once the takeover is gone (I'm Done / cancel), Login runs a real attempt again."""
    runner, handle, do_login, post = blacked
    post()
    runner._manual_login_handle = None
    do_login.return_value = (True, "Logged in", [])
    code, body = post()
    assert do_login.call_count == 2
    assert body.get("ok") is True and body.get("state") == "logged_in", (code, body)
