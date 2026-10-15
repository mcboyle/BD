"""tpl95-site-ma-brazzers-2 r3: a login on a session-ok site must not throw the session away.

Measured on test2 (build 1e334559, 2026-10-14T20:54Z, RESULT-tpl95-site-ma-brazzers-2-2055Z.md): site-ma-brazzers
had auth_state "ok"; ONE POST /api/sites/1167e615/login ran login_async -> do_login unconditionally, the cold form
post landed site-ma.brazzers.com/access/failure -> 302 www.brazzers.com/badlogin, the attempt settled FAILED and
_m2_auth_state flipped the still-valid jar to "expired" (dl95-vip4k-2: a failed attempt newer than the jar outranks
cookie expiry).

Contract after the fix: an explicit login while the jar is the one this runner's own login settled ok with (and
nothing has refused it since) settles ok without a credential submit and auth stays "ok" -- the 19:41Z ok login ->
20:54Z second login shape. Any other jar still submits and a /badlogin landing still reports failed: an in-date jar
set directly (restart/import), an expired jar, a login-walled or server-refused jar, a jar with a failed attempt
since, a replaced jar, worker re-logins (allow_manual=False) and the pre-emptive refresh.

Every control first ARMS the gate (an ok login) and then applies its one condition, so it exercises the gate's
negative branch rather than an unarmed runner.
"""

from __future__ import annotations

import os
import threading
import time
from unittest import mock

import pytest

BD_GATE_SCOPE = "module"

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BADLOGIN = "Rejected login landing: https://www.brazzers.com/badlogin"


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


def _runner(**over):
    db_init()
    cfg = {
        "login_url": "https://site-ma.brazzers.com/login",
        "username": "u",
        "password": "p",
        # No learned template and no auto-teach: a failed auto-login settles FAILED (as on test2) instead of
        # opening the template-fallback manual window.
        "auto_teach_first_run": False,
        "manual_use_persistent_profile": False,
    }
    cfg.update(over)
    return SiteRunner("brazzersR3", cfg)


def _live_jar():
    return [{"name": "access_token_ma", "value": "live", "domain": "site-ma.brazzers.com", "path": "/",
             "expires": time.time() + 86400}]


def _dead_jar():
    return [{"name": "access_token_ma", "value": "dead", "domain": "site-ma.brazzers.com", "path": "/",
             "expires": time.time() - 3600}]


def _auth_state(r):
    from bulk_downloader.app import _m2_auth_state
    return _m2_auth_state(r, r.config)


def _login(r, result=(False, BADLOGIN, []), **kw):
    """login_async with do_login answering `result` (default: as test2 did at 20:54Z). Returns (calls, on_done)."""
    fired, done = [], threading.Event()

    def _od(ok):
        fired.append(ok)
        done.set()

    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=result) as dl:
        r.login_async(on_done=_od, **kw)
        t = r._login_thread
        if t is not None:
            t.join(30)
        assert done.wait(10), "login_async never resolved on_done"
    return dl.call_count, fired


def _armed(**over):
    """A runner whose jar came from its own settled-ok login (test2 19:41Z: attempt 1 settled ok -> auth ok)."""
    r = _runner(**over)
    calls, fired = _login(r, result=(True, "Logged in", _live_jar()))
    assert calls == 1 and fired == [True] and _auth_state(r) == "ok", (
        f"fixture premise: the arming login must submit once and leave auth ok ({calls}, {fired}, {_auth_state(r)})")
    return r


def _api_login(r, monkeypatch):
    from bulk_downloader import app_sites_auth as sa
    from bulk_downloader.app import app
    monkeypatch.setattr(sa, "_app_runners", lambda: {"s1": r})
    monkeypatch.setattr(sa, "_LOGIN_WAIT_S", 30.0, raising=False)
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(False, BADLOGIN, [])) as dl:
        with app.test_request_context("/api/sites/s1/login", method="POST", json={}):
            resp = sa.api_login("s1")
    if isinstance(resp, tuple):
        resp = resp[0]
    return dl.call_count, resp.get_json()


def _submits_and_fails(r, **kw):
    calls, fired = _login(r, **kw)
    assert calls == 1 and fired == [False], (calls, fired, r._login_status)
    assert BADLOGIN in r._login_status, r._login_status


# -- RED on BASE --------------------------------------------------------------------------------------------------

def test_login_while_auth_ok_sends_no_credentials_and_auth_stays_ok():
    r = _armed()
    calls, fired = _login(r)
    assert calls == 0 and fired == [True] and _auth_state(r) == "ok", (
        "BRAZZERS2_R3_LOGIN_WHILE_OK_SUBMITTED: a login on a session-ok site ran the credential submit "
        f"(do_login calls={calls}, on_done={fired}, status={r._login_status!r}) and left auth_state "
        f"{_auth_state(r)!r} -- test2 20:54Z: /badlogin flipped a live session ok->expired")
    assert "credential submit skipped" in r._login_status, r._login_status


def test_api_login_while_auth_ok_reports_ok_and_keeps_auth_ok(monkeypatch):
    r = _armed()
    calls, body = _api_login(r, monkeypatch)
    assert calls == 0 and body.get("ok") is True and _auth_state(r) == "ok", (
        f"BRAZZERS2_R3_API_LOGIN_WHILE_OK: POST /login on a session-ok site -> {body} "
        f"(do_login calls={calls}), auth_state {_auth_state(r)!r}")


# -- controls: the submit still runs, and a real /badlogin still fails ----------------------------------------------

def test_badlogin_on_an_expired_jar_still_reports_failed(monkeypatch):
    r = _armed()
    r.set_cookies(_dead_jar())
    assert _auth_state(r) == "expired"
    calls, body = _api_login(r, monkeypatch)
    assert calls == 1, f"an expired jar must go to the form login, do_login calls={calls}"
    assert body.get("ok") is False and body.get("state") == "failed" and "badlogin" in body.get("error", ""), body
    assert _auth_state(r) == "expired"


def test_no_jar_still_submits_and_badlogin_fails():
    r = _runner()
    _submits_and_fails(r)
    assert r._login_outcome[1] is False


def test_in_date_jar_set_directly_still_submits():
    """Restart / cookie import: an in-date jar this process's login did not produce is no proof of a session."""
    r = _runner()
    r.set_cookies(_live_jar())
    assert _auth_state(r) == "ok"
    _submits_and_fails(r)


def test_failed_attempt_since_the_jar_still_submits():
    """A genuinely bad credential: once an attempt has failed against this jar, the next login submits again and
    fails again -- the gate never turns a failed login into ok."""
    r = _armed()
    _submits_and_fails(r, allow_manual=False)
    assert _auth_state(r) == "expired"
    _submits_and_fails(r)
    assert _auth_state(r) == "expired"


def test_login_wall_jar_still_submits():
    from bulk_downloader.cookies import jar_fingerprint
    r = _armed()
    r._login_wall_jar = jar_fingerprint(r.cookies)
    _submits_and_fails(r)


def test_server_refused_jar_still_submits():
    r = _armed()
    with mock.patch.object(SiteRunner, "login_async"):
        r._handle_auth_required("https://site-ma.brazzers.com/scene/1", why="Session expired")
    _submits_and_fails(r)


def test_replaced_jar_still_submits():
    r = _armed()
    jar = _live_jar()
    jar[0]["value"] = "other"
    r.set_cookies(jar)
    _submits_and_fails(r)


def test_worker_relogin_is_not_gated():
    r = _armed()
    _submits_and_fails(r, allow_manual=False)


def test_preemptive_relogin_refreshes_a_live_jar():
    r = _armed(auto_preemptive_relogin=True, cookie_max_age_hours=1.0)
    r._cookies_updated_at = time.time() - 2 * 3600
    r._preemptive_login_attempted_at = 0
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(False, BADLOGIN, [])) as dl:
        assert r.maybe_preemptive_relogin() is True
        t = r._login_thread
        if t is not None:
            t.join(30)
    assert dl.call_count == 1, f"pre-emptive refresh of an armed jar must submit, do_login calls={dl.call_count}"
