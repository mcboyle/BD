"""dl95-kellymadisonmedia-1: a dead login host must be NAMED, not read as auth_state "unknown".

O1513 on test2 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md#A8): the site's login_url pointed at a
host that gives no HTTP answer, and the Sites list reported auth_state "unknown" -- the state of a site nobody
has logged into yet -- so nothing told the operator the configured host was the problem.

  1. do_login: a login page that never loads (navigation timeout, or a net::ERR_* failure) returns a
     "Login page unreachable: <host>" verdict naming the host.
  2. _m2_auth_state: with no usable cookie jar, a last login attempt that reported the host unreachable buckets
     as "unreachable"; any other failure stays "unknown", and a usable jar still wins ("ok").
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout  # noqa: E402

from bulk_downloader.app import _m2_auth_state  # noqa: E402

BD_GATE_SCOPE = "module"

LOGIN_URL = "https://dead-host.example.invalid/"


def _run_login(monkeypatch, tmp_path, goto_exc):
    """Drive the real do_login; replace only the browser boundary. goto raises goto_exc."""
    from bulk_downloader import cloak, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"close": 0, "fill": 0}

    class Page:
        url = "about:blank"

        def goto(self, url, **kwargs):
            raise goto_exc

    def close():
        calls["close"] += 1

    ctx = SimpleNamespace(new_page=lambda: Page(), cookies=lambda: [])
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=close)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)

    def fill(*a, **kw):
        calls["fill"] += 1
        return True, "fixture field"

    monkeypatch.setattr(submit, "_try_fill", fill)
    # Documented zero-entropy password; no vault reference or real credential.
    config = {"login_url": LOGIN_URL, "username": "fixture", "password": "zero-entropy-password",
              "success_url": "/members", "wait": 0,
              "login_evidence_dir": str(tmp_path / "login_evidence"), "use_real_chrome": False,
              "use_stealth": False, "use_stealth_library": False}
    result = submit.do_login(config)
    return result, calls


@pytest.mark.parametrize("goto_exc", [
    PWTimeout("Page.goto: Timeout 25000ms exceeded."),
    PWError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://dead-host.example.invalid/"),
], ids=["timeout", "net_error"])
def test_login_page_that_never_loads_is_reported_unreachable_by_host(monkeypatch, tmp_path, goto_exc):
    (ok, msg, cookies), calls = _run_login(monkeypatch, tmp_path, goto_exc)
    assert ok is False and cookies == []
    assert msg.startswith("Login page unreachable: dead-host.example.invalid"), (
        f"DL95_KMM_UNREACHABLE_NOT_NAMED: {msg!r}")
    assert calls["fill"] == 0          # never reached the form
    assert calls["close"] == 1         # browser torn down


class _Runner:
    def __init__(self, cookies, login_status=""):
        self.cookies = cookies
        self._login_status = login_status


def _session_cookie():
    return {"name": "sessionid", "value": "v", "domain": "example.test", "path": "/"}


def test_empty_jar_after_unreachable_login_host_buckets_unreachable():
    r = _Runner([], "✗ Login page unreachable: dead-host.example.invalid (no response in 25 s)")
    assert _m2_auth_state(r, {}) == "unreachable", "DL95_KMM_AUTH_STATE_HIDES_DEAD_HOST"


@pytest.mark.parametrize("status", ["", "✗ Submit failed: no nav", "✗ Wrong password",
                                    "Login page unreachable"])  # no ✗ verdict prefix: not a login verdict
def test_other_failures_and_no_attempt_stay_unknown(status):
    assert _m2_auth_state(_Runner([], status), {}) == "unknown"


def test_usable_jar_wins_over_a_stale_unreachable_status():
    r = _Runner([_session_cookie()], "✗ Login page unreachable: dead-host.example.invalid")
    assert _m2_auth_state(r, {}) == "ok"


def test_interactive_login_on_a_templated_site_reports_unreachable_not_a_manual_window(clean_workdir):
    """Lens R1 (bd-review-shape-A1-A): Actions > Login is allow_manual=True, and on a templated site the Phase B
    fallback used to open a manual window at the dead host with a "⏳ ... finish login manually" status that
    _m2_auth_state reads as "unknown"."""
    import os
    from unittest import mock
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    r = SiteRunner("deadHostSite", {
        "login_url": LOGIN_URL, "username": "u", "password": "p",
        "auto_teach_first_run": True,
        "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"], "submit_btn": ["#go"]}},
        "manual_use_persistent_profile": False,
    })
    opened = []
    msg = "Login page unreachable: dead-host.example.invalid (no response in 25 s)"
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(False, msg, [])), \
         mock.patch("bulk_downloader.login.open_manual_login_browser",
                    side_effect=lambda *a, **kw: opened.append(1)):
        r.login_async(allow_manual=True)
        assert r._login_thread is not None
        r._login_thread.join(30)
    assert opened == [], "DL95_KMM_MANUAL_WINDOW_AT_DEAD_HOST"
    assert r._login_status == "✗ " + msg, f"DL95_KMM_STATUS: {r._login_status!r}"
    assert _m2_auth_state(r, {}) == "unreachable", "DL95_KMM_AUTH_STATE_HIDES_DEAD_HOST"
