"""fx-harden-kellymadisonmedia -- a live remember-me cookie carries the session.

Defect observed on bd2 (SWEEP-T165, 2026-09-30 06:00Z): teenfidelity and
kellymadisonmedia (same members.kellymadisonmedia.com login) failed with
"Auto re-login failed". The stored jar held three cookies: the Laravel
`kmm_session` and `XSRF-TOKEN` (both expired 01:14Z) and
`remember_web_<sha1>` (valid to 2027-11). A plain GET of /videos carrying
only that remember cookie returned the logged-in members page (Logout link,
no password field); the same GET with no cookies landed on /login with the
reCAPTCHA form.

runner_auth._stored_session_usable nonetheless answered False: its predicate
`expired <= 0 or session != 0` treats ANY expired cookie as a dead jar, while
its own docstring ("some unexpired, or session cookies") and
_check_cookies_or_relogin's ("all stored cookies are expired and there are no
session cookies") say otherwise, and app._m2_auth_state already publishes
"ok" for the same jar. The runner therefore forced a login, the login page
escalated to a reCAPTCHA image puzzle, and a working session became a
NEEDS-HUMAN row.

If the server does refuse a partly-expired jar, _handle_auth_required is the
re-login path for that; the pre-flight check must not pre-empt it.
"""

BD_GATE_SCOPE = "module"

import time
from unittest import mock

import pytest

from bulk_downloader.app import _m2_auth_state
from bulk_downloader.db import db_init
from bulk_downloader.runner import SiteRunner


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


SCENE = "https://members.example.test/episodes/1"


def _runner():
    db_init()
    return SiteRunner("kmm-harden", {
        "login_url": "https://members.example.test/login",
        "username": "member",
        "password": "secret",
        "auto_teach_first_run": False,
    })


def _cookie(name, seconds_from_now):
    return {"name": name, "value": "v", "domain": "members.example.test",
            "path": "/", "secure": True, "httpOnly": True,
            "expires": time.time() + seconds_from_now}


def _kmm_jar():
    """The bd2 jar's shape: session + XSRF expired, remember-me live."""
    return [
        _cookie("remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d", 400 * 86400),
        _cookie("XSRF-TOKEN", -5 * 3600),
        _cookie("kmm_session", -5 * 3600),
    ]


def test_live_remember_cookie_beside_expired_session_does_not_relogin():
    r = _runner()
    r.cookies = _kmm_jar()
    with mock.patch.object(r, "login_async") as login:
        proceed = r._check_cookies_or_relogin(SCENE)
    assert login.call_count == 0, (
        "HARDEN-KMM-RELOGIN: _check_cookies_or_relogin started a login on a jar "
        "whose remember-me cookie is still in date (2 of 3 cookies expired). "
        "On kellymadisonmedia that login escalates to a reCAPTCHA puzzle and "
        "a working session becomes NEEDS-HUMAN.")
    assert proceed is True


def test_runner_and_panel_agree_on_a_partly_expired_jar():
    r = _runner()
    r.cookies = _kmm_jar()
    assert _m2_auth_state(r, {}) == "ok", "precondition changed: re-read app._m2_auth_state"
    assert r._stored_session_usable() is True, (
        "HARDEN-KMM-RELOGIN: /api/sites/v2 publishes auth_state=ok for this jar "
        "while the runner treats it as dead and re-logs in.")


def test_negative_control_every_dated_cookie_expired_still_relogins():
    r = _runner()
    r.cookies = [_cookie("remember_web_x", -60), _cookie("kmm_session", -60)]
    assert _m2_auth_state(r, {}) == "expired"

    def _failed_login(on_done=None, **_kw):
        if on_done:
            on_done(False)

    with mock.patch.object(r, "login_async", side_effect=_failed_login) as login, \
            mock.patch.object(r, "_handle_failure") as failure:
        proceed = r._check_cookies_or_relogin(SCENE)
    assert login.call_count == 1
    assert proceed is False
    failure.assert_called_once_with(SCENE, "Auto re-login failed")
