"""dl95-relogin-blocked-latch-1 (account safety; PM HOLD-pm-A4-A-dorcelclub-0003Z.md item 3).

MEASURED on .95 (RESULT-dl95-dorcelclub-1-r2-subframe-0001Z.md, 1e334559): a job met the members wall
("O1567-AUTH-EVIDENCE signal=members-only match='ACCESS DENIED'") -> "Session expired -- re-logging in (try 1/2)".
23:55:37Z "login trace: POST https://www.account-dorcel.com/login -> 302 Location .../blocked" -> "attempt 2 settled
FAILED". The job was re-claimed, try 2/2 POSTed the credentials AGAIN at 23:57:59Z ("attempt 3 settled FAILED"), and
both jobs then dead-lettered "re-login retries exhausted". So the app logged in twice into a blocked account.

Contract (ANSWER-dispatch-A-0014Z.md):
  * a submit that lands on /blocked is the site's lockout. It files the existing lockout hold
    (session_keeper.record_login_lockout, tpl95-evilangel-1), which refuses every automatic login source;
  * the worker re-login dead-letters the job "site answered /blocked -- no automatic re-login" -- 1 login, not 2;
  * while the hold stands, Start leaves the jobs Stop parked (dl95-justporn-3 re-queue kept otherwise).
Controls: a /badlogin (wrong password) landing is not a lockout; ACCESS DENIED + a good re-login still recovers once.

HERMETIC ONLY (PM HOLD: no dorcel, no live site): a local http server, a headless chromium, an isolated DB.
"""
from __future__ import annotations

import contextlib
import http.server
import os
import threading
import time
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import session_keeper  # noqa: E402
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial

SITE = "latch1Fixture"
SCENE = "https://www.dorcelclub.example/en/scene/851754/embraced-fantasy"
FORM = (b"<!doctype html><html><body><form method='post' action='/login'>"
        b"<input id='username' name='u'><input id='password' type='password' name='p'>"
        b"<button id='submit' type='submit'>Log in</button></form></body></html>")
# The /blocked body is not known from the live run; a neutral page proves the landing URL decides.
BLOCKED = (b"<!doctype html><html><head><title>Account</title></head><body>"
           b"<h1>Access restricted</h1><p>Please contact customer support.</p></body></html>")
BADLOGIN = b"<!doctype html><html><body><p>Please check your details.</p></body></html>"


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    db_init()
    yield clean_workdir


def _handler(post_to):
    pages = {"/en/login": FORM, "/blocked": BLOCKED, "/badlogin": BADLOGIN}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path.partition("?")[0])
            self.send_response(200 if body else 404)
            body = body or b"not found"
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(302)
            self.send_header("Location", post_to)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass
    return Handler


@contextlib.contextmanager
def _serving(post_to):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(post_to))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _real_login(monkeypatch, base):
    """The production submit path (login_impl.submit.do_login) against the fixture server."""
    from playwright.sync_api import sync_playwright
    from bulk_downloader import cloak
    from bulk_downloader.login_impl import submit as submit_impl

    def launch_for_test(*, headless=True, args=None, config=None, **kwargs):
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            playwright.stop()
            pytest.skip("headless chromium unavailable")
        return browser, playwright, "playwright-test"

    real_sleep = time.sleep
    monkeypatch.setattr(cloak, "launch_browser", launch_for_test)
    for name in ("USER_FIELD_FALLBACKS", "PASS_FIELD_FALLBACKS", "SUBMIT_FALLBACKS"):
        monkeypatch.setattr(submit_impl, name, [])
    monkeypatch.setattr(submit_impl.time, "sleep", lambda s: real_sleep(min(s, 0.05)))
    monkeypatch.setattr(submit_impl, "write_login_evidence", lambda *a, **k: None)
    config = {
        "login_url": base + "/en/login", "username": "u", "password": "p",
        "user_field": "#username", "pass_field": "#password", "submit_btn": "#submit",
        "success_url": "/en/members", "wait": 0, "use_real_chrome": False,
        "use_stealth": False, "use_stealth_library": False,
    }

    def do_login(_cfg, allow_manual_takeover=False, site_id=""):
        return submit_impl.do_login(config, allow_manual_takeover=allow_manual_takeover,
                                    site_id=site_id or SITE)
    return do_login


def _runner():
    cfg = {
        "login_url": "https://www.account-dorcel.example/login", "username": "u", "password": "p",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#username"], "pass_field": ["#password"], "submit_btn": ["#submit"]}},
        "manual_use_persistent_profile": False, "max_retries": 2,
        session_keeper.LOGIN_CAP_KEY: 10,
    }
    r = SiteRunner(SITE, cfg)
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")   # never a real browser
    r._update_job(SCENE, "running", "Claimed by worker")
    return r


def _counted():
    got = session_keeper.login_attempts_for_day(SITE)
    assert got["status"] == "OK", got
    return got["count"]


def _job(r):
    return r.jobs.get(SCENE) or {}


def test_blocked_landing_is_a_lockout_and_opens_the_hold(monkeypatch):
    with _serving("/blocked") as base:
        result = _real_login(monkeypatch, base)({}, site_id=SITE)
    assert result[0] is False and isinstance(result[1], session_keeper.LoginLockout), (
        f"LATCH1_BLOCKED_LANDING_NOT_A_LOCKOUT: a submit that landed on /blocked settled as {result[:2]!r}")
    held = session_keeper.reserve_login_attempt(SITE, "app._do_login_for_keeper", 3)
    assert held["status"] == "LOCKOUT" and not held["granted"], f"LATCH1_HOLD_NOT_OPENED {held}"


def test_worker_relogin_after_blocked_logs_in_once_and_dead_letters(monkeypatch):
    r = _runner()
    with _serving("/blocked") as base:
        with mock.patch("bulk_downloader.runner_auth.do_login", _real_login(monkeypatch, base)):
            r._handle_auth_required(SCENE)            # try 1/2: the POST lands on /blocked
            first = dict(_job(r))
            r._handle_auth_required(SCENE)            # the job met the wall again (live 23:57:35Z)
    assert _counted() == 1, (
        f"LATCH1_SECOND_AUTOMATIC_LOGIN: {_counted()} login_attempt rows after a /blocked answer, expected 1 "
        f"(job after try 1: {first.get('status')} {first.get('message')!r})")
    assert first.get("status") == "dead_letter" and "site answered /blocked" in (first.get("message") or ""), (
        f"LATCH1_JOB_NOT_DEAD_LETTERED: after the /blocked answer the job was {first.get('status')!r} "
        f"{first.get('message')!r}")
    assert list(r._url_queue.queue) == [], "LATCH1_JOB_REQUEUED"
    for source in ("app._do_login_for_keeper", "runner_auth.login_async"):
        held = session_keeper.reserve_login_attempt(SITE, source, 10)
        assert held["status"] == "LOCKOUT", (source, held)


def test_control_wrong_password_landing_is_not_a_lockout(monkeypatch):
    with _serving("/badlogin") as base:
        result = _real_login(monkeypatch, base)({}, site_id=SITE)
    assert result[0] is False and not isinstance(result[1], session_keeper.LoginLockout), result[:2]
    assert "Rejected login landing" in str(result[1]), result[:2]
    assert session_keeper.reserve_login_attempt(SITE, "runner_auth.login_async", 3)["status"] == "OK"


def test_control_access_denied_with_a_good_relogin_recovers_once():
    r = _runner()
    fresh = [{"name": "sid", "value": "new", "expires": time.time() + 86400}]
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(True, "ok", fresh)):
        r._handle_auth_required(SCENE)
    assert _counted() == 1
    assert _job(r).get("status") == "pending" and "Session refreshed" in (_job(r).get("message") or ""), _job(r)
    assert list(r._url_queue.queue) == [SCENE]


@pytest.mark.parametrize("held", [True, False])
def test_start_leaves_stop_parked_jobs_parked_while_the_hold_stands(held):
    r = _runner()
    r.stop()
    assert _job(r).get("status") == "stopped" and _job(r).get("message") == "Stopped", _job(r)
    if held:
        session_keeper.record_login_lockout(SITE, "'/blocked' at https://www.account-dorcel.example/blocked")
    r._repend_jobs_parked_by_stop()                   # start()'s re-arm step (dl95-justporn-3)
    if held:
        assert _job(r).get("status") == "stopped", (
            f"LATCH1_PARKED_JOB_REARMED_UNDER_HOLD: Start re-queued a parked job while the site's login lockout "
            f"hold stands: {_job(r).get('status')} {_job(r).get('message')!r}")
    else:
        assert _job(r).get("status") == "pending", f"justporn-3 re-queue lost: {_job(r)}"
