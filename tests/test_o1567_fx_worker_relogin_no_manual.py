"""O1567 fx-worker-relogin-no-manual: a worker re-login never parks a takeover, and a parked one holds the job.

MEASURED on test7 (wowgirls ddcde6ee, journal 2026-09-30 06:10-06:14Z, T165; same code on T170 da2dda09):
  06:10:59 job "Session expired -- re-logging in (try 1/2)"          (runner_auth._handle_auth_required)
  06:12:38 do_login "handing off for manual takeover -- ..."          (login_async's DEFAULT allow_manual=True)
  06:13:31 job try 2/2 -> "manual login already in progress ... click I'm Done"   (login_async no-op, retry spent)
  06:14:34 job dead_letter "Session expired -- re-login retries exhausted"
Nobody was watching; the unattended worker parked a takeover and then burned its own retry budget on it.

Rule (v3.62.2's own contract, tests/test_v3_62_2_login_fallback.py: "Worker-initiated relogins (allow_manual=False)
keep the old report-only behaviour"):
  * the job-driven re-logins (_handle_auth_required, _check_cookies_or_relogin) pass allow_manual=False, and a
    login_async with allow_manual=False never opens a manual window -- not even the auto-teach first-run route;
  * while a takeover IS parked (UI-started), the job is held pending -- retries unchanged, retried after the
    backoff -- until I'm Done / Cancel, instead of spending a retry per no-op login_async into dead_letter.
The UI/default login_async still hands off (control).

Hermetic: real SiteRunner, login_async and session_keeper accounting on the isolated DB; do_login faked at the
runner seam, start_manual_login stubbed.  No site, no browser.
"""
from __future__ import annotations

import os
import time
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import session_keeper  # noqa: E402
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SITE = "o1567WorkerNoManual"
SCENE = "https://www.wowgirls.example/scene/ddcde6ee/"
REJECTED = (False, "Expected URL contains /members", [])
OK = (True, "Logged in", [{"name": "sid", "value": "v", "domain": ".wowgirls.example", "path": "/"}])


class _Handle:
    """A parked takeover handle (not the thread-bound (pw, browser, ctx) tuple)."""


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


def _runner(**over):
    db_init()
    cfg = {
        "login_url": "https://www.wowgirls.example/login",
        "username": "u",
        "password": "p",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"], "submit_btn": ["#go"]}},
        "manual_use_persistent_profile": False,
        "max_retries": 2,
        session_keeper.LOGIN_CAP_KEY: 20,
    }
    cfg.update(over)
    r = SiteRunner(SITE, cfg)
    r.manual_opened = []
    r.start_manual_login = lambda *a, **k: (r.manual_opened.append(1), (True, "test: manual window"))[1]
    r._update_job(SCENE, "running", "Claimed by worker")
    return r


def _fake_do_login(seen, verdict=REJECTED):
    """do_login that hands off whenever it is ALLOWED to (the wowgirls 06:12:38 hand-off)."""
    def _do_login(cfg, allow_manual_takeover=True, site_id=None):
        seen.append(allow_manual_takeover)
        if allow_manual_takeover:
            return ("MANUAL_PENDING", "Expected URL contains /members", _Handle())
        return verdict
    return _do_login


def _job(r):
    j = r.jobs[SCENE]
    return j.get("status"), j.get("retries", 0), j.get("message", "")


def test_worker_relogin_does_not_park_a_takeover():
    r = _runner()
    seen = []
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login(seen)):
        r._handle_auth_required(SCENE)
    assert seen == [False], (
        f"O1567 WORKER-RELOGIN-MANUAL: _handle_auth_required ran do_login with allow_manual_takeover={seen}; "
        "an unattended worker re-login may hand off to a takeover nobody is watching")
    assert getattr(r, "_manual_login_handle", None) is None, "worker re-login parked a manual takeover"
    assert _job(r)[:2] == ("pending", 1), _job(r)   # report-only failure: one retry spent, as before


def test_expired_cookie_relogin_does_not_park_a_takeover():
    r = _runner()
    r.cookies = [{"name": "sid", "value": "old", "domain": ".wowgirls.example", "path": "/", "expires": 1}]
    r._stored_session_usable = lambda: False
    seen = []
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login(seen)):
        r._check_cookies_or_relogin(SCENE)
    assert seen == [False], (
        f"O1567 WORKER-RELOGIN-MANUAL: _check_cookies_or_relogin ran do_login with allow_manual_takeover={seen}")
    assert getattr(r, "_manual_login_handle", None) is None


def test_worker_relogin_does_not_open_the_auto_teach_manual_window():
    r = _runner(auto_teach_first_run=True, learned={})
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login([])):
        r._handle_auth_required(SCENE)
    assert r.manual_opened == [], (
        "O1567 WORKER-RELOGIN-MANUAL: a worker re-login (allow_manual=False) took the auto-teach route and "
        "opened a manual login window")


def test_parked_takeover_holds_the_job_without_spending_retries():
    r = _runner()
    r._manual_login_handle = _Handle()          # UI-started takeover, parked
    seen = []
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login(seen)):
        for _ in range(4):
            r._handle_auth_required(SCENE)
    status, retries, msg = _job(r)
    assert status == "pending" and retries == 0, (
        f"O1567 WORKER-RELOGIN-MANUAL: a parked takeover drove the job to {status!r} with {retries} retr(ies) "
        "spent -- the 06:14:34Z dead_letter 'Session expired -- re-login retries exhausted'")
    assert "I'm Done" in msg, msg
    assert r.jobs[SCENE].get("retry_after", 0) > time.time() + 30, "held job must back off, not hot-loop"
    assert seen == [], "a parked takeover must not be joined by another login attempt"
    # I'm Done: the takeover clears and the same job's next auth-required logs in and re-queues.
    r._manual_login_handle = None
    r._update_job(SCENE, "running", "Claimed by worker")
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login(seen, OK)):
        r._handle_auth_required(SCENE)
    assert _job(r)[0] == "pending" and "Session refreshed" in _job(r)[2], _job(r)


def test_parked_takeover_holds_the_expired_cookie_path():
    r = _runner()
    r.cookies = [{"name": "sid", "value": "old", "domain": ".wowgirls.example", "path": "/", "expires": 1}]
    r._stored_session_usable = lambda: False
    r._manual_login_handle = _Handle()
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login([])):
        assert r._check_cookies_or_relogin(SCENE) is False
    status, retries, msg = _job(r)
    assert (status, retries) == ("pending", 0) and "I'm Done" in msg, (status, retries, msg)


def test_control_ui_login_still_hands_off_to_a_takeover():
    r = _runner()
    seen = []
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login(seen)):
        r.login_async()
        r._login_thread.join(timeout=30)
    assert seen == [True]
    assert isinstance(getattr(r, "_manual_login_handle", None), _Handle), "UI login must still park the takeover"


def test_control_real_failures_without_a_takeover_still_dead_letter():
    r = _runner()
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_fake_do_login([])):
        for _ in range(3):
            r._update_job(SCENE, "running", "Claimed by worker")
            r._handle_auth_required(SCENE)
    assert _job(r)[0] == "dead_letter", _job(r)
