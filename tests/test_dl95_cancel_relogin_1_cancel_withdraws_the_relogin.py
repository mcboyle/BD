"""dl95-cancel-relogin-1 (HIGH): Cancel during "Session expired -- re-logging in" must withdraw the login and the
job must not come back.

Measured on .82 build 2c0c48c0 (harness-work/DOT95-LANE/live-dot95-brazzers1-clickmiss-exempt/journal-1034.txt):
  10:34:45 pending "re-logging in (try 1/2)" -> 10:34:47 POST /api/queue/v2/cancel -> "stopped: Cancelled by user"
  -> the login flow ran on (fill, submit) -> "attempt 1 settled FAILED" -> "pending: Session refreshed -- will retry"
  -> "running: Claimed by worker" -> "try 2/2" -> a second live login. Two live logins in 76 s on one site.

GREEN: _handle_auth_required registers the job as a requester of the re-login it starts; do_login asks
login_abort_check's predicate before any site contact and before each submit path, so a login every requester has
cancelled stops before it submits; and a job cancelled while the re-login ran is neither re-armed "pending" nor put
back on the queue. A login started by the UI/keeper has no requesters, so a job cancel never withdraws it.

Hermetic: real SiteRunner and _update_job, do_login faked at the runner seam; the do_login checks run in real
Chromium against a loopback login form. No site, no credentials, no network beyond 127.0.0.1.
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

from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.login_impl import submit as submit_impl  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial

SCENE = "https://site-ma.example.com/scene/11524413/a-rosie-day"


def _abort_reason():
    """The predicate as do_login sees it ("" on a build without the hook, so controls run on BASE too)."""
    return getattr(submit_impl, "_login_abort_reason", lambda: "")()


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
        "max_retries": 2,
    }
    r = SiteRunner("cancelReloginSite", cfg)
    # The live state: an older jar is present, so "cookies exist" alone reads as a refreshed session.
    r.cookies = [{"name": "sid", "value": "old", "expires": time.time() + 86400}]
    r._cookies_updated_at = time.time() - 3600
    r._update_job(SCENE, "running", "Claimed by worker")
    # Hermetic: the templated-failure fallback must never open a real browser from a test.
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")
    return r


def _queued(r):
    return [u for u in list(r._url_queue.queue) if u == SCENE]


def _status(r):
    j = r.jobs.get(SCENE) or {}
    return j.get("status"), j.get("message")


def test_cancel_during_relogin_withdraws_it_and_the_job_stays_cancelled():
    r = _runner()
    seen = []

    def _do_login(config, allow_manual_takeover=False, site_id=None):
        # The operator's Cancel lands while the login is in flight (the same call /api/queue/v2/cancel makes).
        r._update_job(SCENE, "stopped", "Cancelled by user")
        seen.append(_abort_reason())
        return False, "settled FAILED", []

    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_do_login):
        r._handle_auth_required(SCENE)
    assert seen and seen[0], (
        "DL95_CANCEL_RELOGIN_NOT_WITHDRAWN: the login in flight could not see that its only requester was "
        f"cancelled (abort reason {seen!r}); it would go on to submit")
    assert _status(r) == ("stopped", "Cancelled by user"), (
        f"DL95_CANCEL_RELOGIN_RESURRECTED: the cancelled job was re-armed: {_status(r)!r}")
    assert _queued(r) == [], "DL95_CANCEL_RELOGIN_REQUEUED: the cancelled job went back on the queue"


def test_a_withdrawn_relogin_never_opens_the_manual_takeover():
    """Lens F1 (bd-worker-B13-B): the withdrawn verdict must not fall into the templated-failure manual fallback."""
    r = _runner()                       # templated: learned login selectors present
    opened = []
    r.start_manual_login = lambda *a, **k: (opened.append(1), (True, "opened"))[1]

    def _do_login(config, allow_manual_takeover=False, site_id=None):
        r._update_job(SCENE, "stopped", "Cancelled by user")
        why = _abort_reason()
        if why:                         # what the candidate's do_login returns once withdrawn
            return False, f"{getattr(submit_impl, 'LOGIN_CANCELLED_PREFIX', '')}{why}", []
        return False, "settled FAILED", []

    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_do_login):
        r._handle_auth_required(SCENE)
    status = str(getattr(r, "_login_status", ""))
    assert opened == [], (
        f"DL95_CANCEL_RELOGIN_OPENED_TAKEOVER: a withdrawn re-login opened the manual login window; status={status!r}")
    assert status.startswith("✗ " + getattr(submit_impl, "LOGIN_CANCELLED_PREFIX", "\0")), status
    assert _status(r) == ("stopped", "Cancelled by user") and _queued(r) == [], _status(r)


def test_an_uncancelled_templated_failure_still_opens_the_takeover():
    """Control: the Phase-B fallback is untouched for a real templated failure."""
    r = _runner()
    opened = []
    r.start_manual_login = lambda *a, **k: (opened.append(1), (True, "opened"))[1]
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(False, "settled FAILED", [])):
        r.login_async()
        r._login_thread.join(15)
    assert opened == [1], opened


def test_an_uncancelled_relogin_still_retries_the_job():
    """Control: nothing cancelled -> the predicate stays quiet and the job is retried as before."""
    r = _runner()
    seen = []

    def _do_login(config, allow_manual_takeover=False, site_id=None):
        seen.append(_abort_reason())
        return True, "ok", [{"name": "sid", "value": "new", "expires": time.time() + 86400}]

    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_do_login):
        r._handle_auth_required(SCENE)
    assert seen == [""], seen
    assert _status(r)[0] == "pending", _status(r)
    assert _queued(r) == [SCENE]


def test_a_ui_login_in_flight_is_never_withdrawn_by_a_job_cancel():
    """A login the job did not start (UI Login / keeper) has no requesters: cancelling the job leaves it alone."""
    r = _runner()
    entered, release = threading.Event(), threading.Event()
    seen = []

    def _do_login(config, allow_manual_takeover=False, site_id=None):
        entered.set()
        release.wait(10)
        seen.append(_abort_reason())
        return False, "settled FAILED", []

    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_do_login):
        r.login_async()                       # the UI's Login button
        assert entered.wait(10)
        t = threading.Thread(target=r._handle_auth_required, args=(SCENE,), daemon=True)
        t.start()
        time.sleep(0.3)
        r._update_job(SCENE, "stopped", "Cancelled by user")
        release.set()
        t.join(15)
    assert seen == [""], f"a job cancel withdrew a login the job did not start: {seen!r}"
    assert _queued(r) == [] and _status(r)[0] == "stopped", _status(r)


# ---- do_login honours the predicate (real Chromium, loopback form) ----

_FORM = b"""<!doctype html><html><body>
<form action="/members" method="post">
  <input id="username" name="u" type="text"><input id="password" name="p" type="password">
  <button id="go" type="submit">Log in</button>
</form></body></html>"""


def _handler():
    class Handler(http.server.BaseHTTPRequestHandler):
        requests: list[str] = []

        def do_GET(self):
            type(self).requests.append(self.path)
            body = b"<html><body>members</body></html>" if self.path.startswith("/members") else _FORM
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.do_GET()

        def log_message(self, *_a):
            pass

    return Handler


@contextlib.contextmanager
def _serving(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def _headless(monkeypatch):
    from playwright.sync_api import sync_playwright

    from bulk_downloader import cloak

    def launch_for_test(*, headless=True, args=None, config=None, **kwargs):
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception:
            pw.stop()   # rule 45: never orphan the started playwright
            raise
        return browser, pw, "playwright-test"

    monkeypatch.setattr(cloak, "launch_browser", launch_for_test)


def _login(monkeypatch, base, predicate):
    _headless(monkeypatch)
    monkeypatch.setattr(submit_impl, "USER_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "PASS_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "SUBMIT_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "_try_check_remember_me", lambda page: False)
    monkeypatch.setattr(submit_impl.time, "sleep", lambda s: None)
    cfg = {"login_url": base + "/login", "username": "u", "password": "p", "user_field": "#username",
           "pass_field": "#password", "submit_btn": "#go", "success_url": "/members", "wait": 0,
           "use_real_chrome": False, "use_stealth": False, "use_stealth_library": False}
    check = getattr(submit_impl, "login_abort_check", None)
    with (check(predicate) if check else contextlib.nullcontext()):
        result = submit_impl.do_login(cfg, allow_manual_takeover=True)
    if result and result[0] == "MANUAL_PENDING":      # rule 45: close a handed-off browser
        pw, browser, _ctx = result[2]
        with contextlib.suppress(Exception):
            browser.close()
        with contextlib.suppress(Exception):
            pw.stop()
    return result


def test_a_login_withdrawn_after_the_form_is_filled_never_submits(monkeypatch):
    handler = _handler()
    withdrawn = {"now": False}
    real_upsell = submit_impl._uncheck_upsell_boxes

    def _upsell_then_cancel(page):          # the last step before the submit sweep
        out = real_upsell(page)
        withdrawn["now"] = True
        return out

    monkeypatch.setattr(submit_impl, "_uncheck_upsell_boxes", _upsell_then_cancel)
    with _serving(handler) as base:
        result = _login(monkeypatch, base, lambda: "job cancelled" if withdrawn["now"] else "")
    assert withdrawn["now"], "fixture drifted: the pre-submit step never ran"
    assert result[0] is False and str(result[1]).startswith(getattr(submit_impl, "LOGIN_CANCELLED_PREFIX", "\0")), \
        result[:2]
    assert not any(p.startswith("/members") for p in handler.requests), (
        f"DL95_CANCEL_RELOGIN_SUBMITTED: the withdrawn login submitted anyway: {handler.requests!r}")


def test_a_login_withdrawn_before_it_starts_never_contacts_the_site(monkeypatch):
    handler = _handler()
    with _serving(handler) as base:
        result = _login(monkeypatch, base, lambda: "job cancelled")
    assert handler.requests == [], (
        f"DL95_CANCEL_RELOGIN_CONTACTED: a login withdrawn before it started reached the site: {handler.requests!r}")
    assert result[0] is False and str(result[1]).startswith(submit_impl.LOGIN_CANCELLED_PREFIX), result[:2]


def test_a_login_nobody_withdrew_submits_as_before(monkeypatch):
    """Control: the predicate quiet -> the loopback form is submitted (the probe can say yes)."""
    handler = _handler()
    with _serving(handler) as base:
        result = _login(monkeypatch, base, lambda: "")
    assert any(p.startswith("/members") for p in handler.requests), (handler.requests, result[:2])


def test_a_login_withdrawn_after_the_page_loads_never_replays_a_saved_flow(monkeypatch):
    """The saved multi-step flow submits on its own; a withdrawn login must not start it."""
    handler = _handler()
    replayed = []
    monkeypatch.setattr(submit_impl, "replay_saved_login_flow",
                        lambda page, config: replayed.append(page.url) or {"ran": False})
    calls = {"n": 0}

    def _withdrawn_once_the_browser_is_up():
        calls["n"] += 1
        return "" if calls["n"] == 1 else "job cancelled"   # quiet at entry, withdrawn after the page load

    with _serving(handler) as base:
        result = _login(monkeypatch, base, _withdrawn_once_the_browser_is_up)
    assert handler.requests == ["/login"], f"fixture drifted: the login page was not loaded once: {handler.requests!r}"
    assert replayed == [], f"DL95_CANCEL_RELOGIN_FLOW_REPLAYED: a withdrawn login replayed the saved flow: {replayed!r}"
    assert result[0] is False and str(result[1]).startswith(getattr(submit_impl, "LOGIN_CANCELLED_PREFIX", "\0")), \
        result[:2]
