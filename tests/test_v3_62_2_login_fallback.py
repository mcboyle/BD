"""v3.62.2 Phase B — templated-login-failure → manual-login fallback.

When a site has a login template applied (learned.login selectors are
present, so the first-run manual teach was skipped), an automatic
login that FAILS must fall back to a manual-login takeover instead of
silently dying. Worker-initiated relogins (allow_manual=False) keep the
old report-only behaviour.

These tests drive SiteRunner.login_async with do_login mocked to fail,
and assert whether start_manual_login is invoked.
"""
from __future__ import annotations

import os
import time
from unittest import mock

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
from bulk_downloader.db import db_init
from bulk_downloader.runner import SiteRunner


def _ensure_db():
    """SiteRunner queries the queue table on construction; make sure
    the schema exists. db_init() is idempotent."""
    db_init()


_LOGIN_BLOCK = {
    "user_field": ["#username"],
    "pass_field": ["#password"],
    "submit_btn": ["#go"],
}


def _runner(with_template):
    """A SiteRunner whose config does/doesn't have a login template."""
    _ensure_db()
    cfg = {
        "login_url": "https://example.com/login",
        "username": "u", "password": "p",
        "auto_teach_first_run": True,
    }
    if with_template:
        cfg["learned"] = {"login": dict(_LOGIN_BLOCK)}
    return SiteRunner("testsite", cfg)


def _wait_for_thread(runner, timeout=5):
    """login_async runs _run() on a daemon thread — wait for it."""
    t = getattr(runner, "_login_thread", None)
    if t is not None:
        t.join(timeout)


def test_failed_templated_login_falls_back_to_manual():
    r = _runner(with_template=True)
    # do_login fails (not MANUAL_PENDING — a hard (False, msg, []))
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=(False, "page load timeout", [])), \
         mock.patch.object(r, "start_manual_login",
                           return_value=(True, "manual window open")
                           ) as m_manual:
        r.login_async(allow_manual=True)
        _wait_for_thread(r)
    # the template-skip is recovered: manual login was opened
    assert m_manual.called
    assert "manually" in r._login_status.lower()


def test_failed_login_without_template_does_not_fall_back():
    # No login template → the site never skipped its teach, so a
    # failure should just report ✗ (no surprise manual window).
    r = _runner(with_template=False)
    # with no learned selectors + auto_teach_first_run, login_async
    # routes to manual capture up front; force the auto path by
    # pretending teach is off so we reach the do_login result handler.
    r.config["auto_teach_first_run"] = False
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=(False, "bad credentials", [])), \
         mock.patch.object(r, "start_manual_login",
                           return_value=(True, "x")) as m_manual:
        r.login_async(allow_manual=True)
        _wait_for_thread(r)
    assert not m_manual.called
    assert r._login_status.startswith("✗")


def test_worker_relogin_failure_does_not_open_manual_window():
    # allow_manual=False is the worker-initiated relogin path. A
    # failure must NOT pop a window — the worker's auth backoff
    # handles it. Status just reports ✗.
    r = _runner(with_template=True)
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=(False, "token refresh failed", [])), \
         mock.patch.object(r, "start_manual_login",
                           return_value=(True, "x")) as m_manual:
        r.login_async(allow_manual=False)
        _wait_for_thread(r)
    assert not m_manual.called
    assert r._login_status.startswith("✗")


def test_successful_templated_login_does_not_fall_back():
    r = _runner(with_template=True)
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=(True, "logged in", [])), \
         mock.patch.object(r, "set_cookies"), \
         mock.patch.object(r, "start_manual_login",
                           return_value=(True, "x")) as m_manual:
        r.login_async(allow_manual=True)
        _wait_for_thread(r)
    assert not m_manual.called
    assert r._login_status.startswith("✓")
