"""dl95-relogin-false-success-1 (MED): a FAILED re-login must not read as "Session refreshed".

Measured on .82 build 2c0c48c0 (harness-work/DOT95-LANE/live-dot95-brazzers1-clickmiss-exempt/journal-1034.txt):
  10:35:30 "login: attempt 1 settled FAILED" -> "pending: Session refreshed -- will retry" -> "running: Claimed by worker"
  -> 10:35:31 "re-logging in (try 2/2)" -> a second live login one second later.
_handle_auth_required judged success as bool(self.cookies) and _cookies_updated_at > 0, so any older jar made the
failed attempt look like a refresh and the job was re-queued at once.

GREEN: success is the awaited attempt's own settled verdict (_login_outcome, stamped by login_async's _settle): a
failed attempt leaves the job pending with the admission backoff and off the queue; a successful one (started here or
already in flight) still re-queues it.

Hermetic: real SiteRunner, _handle_auth_required and login_async; do_login faked at the runner seam. No site.
"""
from __future__ import annotations

import os
import threading
import time
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SCENE = "https://site-ma.example.com/scene/11524413/a-rosie-day"


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
    r = SiteRunner("falseSuccessSite", cfg)
    # The live state: an older jar is present.
    r.cookies = [{"name": "sid", "value": "old", "expires": time.time() + 86400}]
    r._cookies_updated_at = time.time() - 3600
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")   # never a real browser
    r._update_job(SCENE, "running", "Claimed by worker")
    return r


def _queued(r):
    return [u for u in list(r._url_queue.queue) if u == SCENE]


def _job(r):
    return r.jobs.get(SCENE) or {}


def test_a_failed_relogin_with_an_older_jar_is_not_a_refresh():
    r = _runner()
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(False, "settled FAILED", [])):
        r._handle_auth_required(SCENE)
    msg = _job(r).get("message") or ""
    assert "Session refreshed" not in msg, (
        f"DL95_RELOGIN_FALSE_SUCCESS: a FAILED attempt was reported as a refreshed session: {msg!r}")
    assert _queued(r) == [], "DL95_RELOGIN_FALSE_SUCCESS: the job was re-queued at once after a FAILED re-login"
    assert _job(r).get("status") == "pending" and "did not complete" in msg, _job(r)
    assert float(_job(r).get("retry_after") or 0) > time.time(), "the failed re-login must back off"


def test_a_successful_relogin_still_refreshes_and_requeues():
    """Control: the attempt this call started succeeds -> refreshed and re-queued, as before."""
    r = _runner()
    fresh = [{"name": "sid", "value": "new", "expires": time.time() + 86400}]
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(True, "ok", fresh)):
        r._handle_auth_required(SCENE)
    assert "Session refreshed" in (_job(r).get("message") or ""), _job(r)
    assert _queued(r) == [SCENE]


def test_joining_an_in_flight_login_reads_that_attempts_verdict():
    """The worker joins a login already running (UI/keeper); its success, not a jar, decides."""
    for verdict, refreshed in ((True, True), (False, False)):
        r = _runner()
        entered, release = threading.Event(), threading.Event()

        def _do_login(config, allow_manual_takeover=False, site_id=None, _v=verdict):
            entered.set()
            release.wait(10)
            return (_v, "ok" if _v else "settled FAILED",
                    [{"name": "sid", "value": "new", "expires": time.time() + 86400}] if _v else [])

        with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=_do_login):
            r.login_async()
            assert entered.wait(10)
            t = threading.Thread(target=r._handle_auth_required, args=(SCENE,), daemon=True)
            t.start()
            time.sleep(0.3)
            release.set()
            t.join(15)
        got = "Session refreshed" in (_job(r).get("message") or "")
        assert got is refreshed, (verdict, _job(r))
        assert (_queued(r) == [SCENE]) is refreshed, (verdict, _queued(r))


def test_an_earlier_attempts_success_is_not_this_relogins_verdict():
    """A login that never runs now (a manual takeover is already pending) must not borrow an older attempt's OK."""
    r = _runner()
    r._login_attempt_seq = 4
    r._login_outcome = (4, True)          # this morning's login succeeded
    r._manual_login_handle = object()     # login_async returns without starting an attempt
    with mock.patch("bulk_downloader.runner_auth.do_login", side_effect=AssertionError("no attempt expected")):
        r._handle_auth_required(SCENE)
    assert "Session refreshed" not in (_job(r).get("message") or ""), (
        f"DL95_RELOGIN_FALSE_SUCCESS: an earlier attempt's success was read as this re-login's: {_job(r)!r}")
    assert _queued(r) == []
