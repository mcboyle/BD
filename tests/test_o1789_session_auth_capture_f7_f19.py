"""O1789 session-auth-capture F7 F10 F11 F12 F19 (PM RULING-pm-13-blocker-rows-0120Z).

Findings: harness-work/O1789/bughunt/session-auth-capture/FINDINGS.md, re-measured on 42825f50.

F7  two concurrent start_manual_login -> 2 browsers and 2 day-cap reservations (unlocked check-then-set
    of _manual_login_handle). GREEN: the check and the claim are one locked step; the loser opens
    nothing and reserves nothing.
F10 a jar round-trip error after a successful login (cookies.CookieRoundTripError, not an OSError)
    escaped the save guard -> "login crashed", outcome False. GREEN: warned, session kept.
F11 "manual login started for <site>: <login_url>" wrote userinfo / token to stderr raw.
F12 the heartbeat's DEAD "redirected to <final_url>" reason (-> session_history) carried the token raw.
F19 two captcha solve sessions for two URLs in one second got the same session_id.

Hermetic: real SiteRunner / SessionKeeper / AuthMixin; the browser opener is a fake. No site, no network.
"""
from __future__ import annotations

import os
import threading
import time
import types

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import cookies, session_keeper  # noqa: E402
from bulk_downloader import login as _login  # noqa: E402
from bulk_downloader import runner_auth as ra  # noqa: E402
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SITE = "authCaptureSite"
SECRET_URL = "https://op:PW-SECRET@example.com/login?next=%2F&token=TOK-SECRET"
COOKIE = {"name": "sid", "value": "v", "domain": "example.com", "path": "/", "expires": 2**31}


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


class _FakeHandle:
    def __init__(self):
        self.cancelled = 0

    def cancel(self, timeout=None):
        self.cancelled += 1

    def snapshot_cookies(self, *a, **k):
        return None


def _runner(cap=5, **extra):
    db_init()
    cfg = {
        "login_url": "https://example.com/login",
        "username": "u",
        "password": "p",
        "auto_teach_first_run": False,
        "manual_use_persistent_profile": False,
        session_keeper.LOGIN_CAP_KEY: cap,
    }
    cfg.update(extra)
    r = SiteRunner(SITE, cfg)
    r._poll_manual_cookies = lambda handle, stop: None   # no poller against the fake handle
    return r


def _counted():
    got = session_keeper.login_attempts_for_day(SITE)
    assert got["status"] == "OK", got
    return got["count"]


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


# ── F7 ──────────────────────────────────────────────────────────────────────

def test_f7_two_concurrent_manual_starts_open_one_browser_on_one_reservation(monkeypatch):
    r = _runner()
    gate = threading.Event()
    opens = []

    def _open(cfg, manual_profile_dir=None, **k):
        opens.append(threading.current_thread().name)
        gate.wait(10)
        return _FakeHandle()

    monkeypatch.setattr(_login, "open_manual_login_browser", _open)
    results = {}
    t1 = threading.Thread(target=lambda: results.__setitem__("first", r.start_manual_login()), name="start-1")
    t2 = threading.Thread(target=lambda: results.__setitem__("second", r.start_manual_login()), name="start-2")
    try:
        t1.start()
        assert _wait(lambda: len(opens) == 1), "first start never reached the browser open"
        t2.start()
        _wait(lambda: not t2.is_alive() or len(opens) == 2)
    finally:
        gate.set()
        t1.join(15)
        t2.join(15)
    assert len(opens) == 1, (
        f"F7_DOUBLE_START: two concurrent start_manual_login opened {len(opens)} browsers "
        f"({opens}); results={results}")
    assert _counted() == 1, (
        f"F7_DOUBLE_RESERVE: two concurrent start_manual_login spent {_counted()} day-cap slots")
    assert results["first"][0] is True and results["second"][0] is True, results
    assert isinstance(r._manual_login_handle, _FakeHandle)


def test_f7_control_sequential_second_start_reuses_the_open_window(monkeypatch):
    r = _runner()
    opens = []
    monkeypatch.setattr(_login, "open_manual_login_browser",
                        lambda *a, **k: opens.append(1) or _FakeHandle())
    assert r.start_manual_login() == (True, "Manual login window opened")
    assert r.start_manual_login()[0] is True
    assert len(opens) == 1 and _counted() == 1, (opens, _counted())


def test_f7_a_failed_open_does_not_wedge_the_next_start(monkeypatch):
    r = _runner()
    calls = []

    def _open(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("F7 test: first launch fails")
        return _FakeHandle()

    monkeypatch.setattr(_login, "open_manual_login_browser", _open)
    ok, msg = r.start_manual_login()
    assert ok is False and "Couldn't open browser" in msg, msg
    assert r.start_manual_login() == (True, "Manual login window opened"), (
        "F7_CLAIM_LEAK: a failed start left the manual-login claim held")
    assert len(calls) == 2


def test_f7_a_refused_reservation_does_not_wedge_the_next_start(monkeypatch):
    r = _runner(cap=1)
    monkeypatch.setattr(_login, "open_manual_login_browser", lambda *a, **k: _FakeHandle())
    assert r.start_manual_login()[0] is True
    r._manual_login_handle = None            # window closed; the day's one slot is spent
    ok, msg = r.start_manual_login()
    assert ok is False and "daily login attempt cap reached" in msg, msg
    r.config[session_keeper.LOGIN_CAP_KEY] = 2
    assert r.start_manual_login() == (True, "Manual login window opened"), (
        "F7_CLAIM_LEAK: a cap-refused start left the manual-login claim held")


# ── F10 ─────────────────────────────────────────────────────────────────────

def _login_ok(r):
    from unittest import mock
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=(True, "ok", [dict(COOKIE)])):
        r.login_async()
        r._login_thread.join(15)
    assert not r._login_thread.is_alive()


def test_f10_jar_round_trip_error_after_login_keeps_the_session(monkeypatch, tmp_path):
    r = _runner(cookie_file=str(tmp_path / "jar.json"))

    def _boom(path, expected_count):
        raise cookies.CookieRoundTripError(
            "count_mismatch", f"cookie file {path}: wrote {expected_count} cookies but read back 0")

    # the real save_cookies_to_file runs; only its post-write read-back reports the mismatch
    monkeypatch.setattr(cookies, "_verify_cookie_roundtrip", _boom)
    _login_ok(r)
    assert r._login_outcome[1] is True, (
        f"F10_ROUNDTRIP_CRASH: a jar round-trip error after set_cookies failed the login: "
        f"status={r._login_status!r}")
    assert "login crashed" not in r._login_status, r._login_status
    assert r._login_status.startswith("✓"), r._login_status
    assert r.cookies, "the in-memory session must survive a failed jar save"


def test_f10_control_oserror_on_save_already_keeps_the_session(monkeypatch, tmp_path):
    r = _runner(cookie_file=str(tmp_path / "jar.json"))

    def _boom(*a, **k):
        raise OSError("F10 control: disk full")

    monkeypatch.setattr(cookies, "save_cookies_to_file", _boom)
    _login_ok(r)
    assert r._login_outcome[1] is True, r._login_status
    assert r._login_status.startswith("✓"), r._login_status


# ── F11 ─────────────────────────────────────────────────────────────────────

def test_f11_manual_login_started_line_masks_the_login_url(monkeypatch, capsys):
    r = _runner(login_url=SECRET_URL)
    monkeypatch.setattr(_login, "open_manual_login_browser", lambda *a, **k: _FakeHandle())
    assert r.start_manual_login() == (True, "Manual login window opened")
    err = capsys.readouterr().err
    line = [ln for ln in err.splitlines() if "manual login started for" in ln]
    assert line, f"the 'manual login started for' line was not written: {err!r}"
    assert "PW-SECRET" not in err and "TOK-SECRET" not in err, (
        f"F11_RAW_LOGIN_URL: stderr carries the login_url's credentials: {line}")
    assert "<REDACTED>" in line[0] and "example.com/login" in line[0], line


# ── F12 ─────────────────────────────────────────────────────────────────────

class _Resp:
    status = 200


class _Page:
    def __init__(self, final_url):
        self.url = final_url

    def goto(self, url, **k):
        return _Resp()


@pytest.mark.parametrize("final_url, secret", [
    ("https://example.com/login?next=%2F&token=TOK-SECRET", "TOK-SECRET"),
    ("https://op:PW-SECRET@example.com/signin", "PW-SECRET"),
    ("https://example.com/login?" + "x=1&" * 10 + "token=TOK-SECRET-LONGER-THAN-THE-CUT", "TOK-SECRET"),
])
def test_f12_heartbeat_redirect_reason_masks_the_final_url(final_url, secret):
    k = session_keeper.SessionKeeper(SITE, 0, {"login_url": "https://example.com/login"},
                                     lambda *a, **kw: None)
    k._page = _Page(final_url)
    k._derive_check_url = lambda: "https://example.com/account"
    verdict, why = k._heartbeat_navigate()
    assert verdict is session_keeper.DEAD, (verdict, why)
    assert why.startswith("redirected to "), why
    assert secret[:7] not in why, f"F12_RAW_REDIRECT_URL: the DEAD reason carries the secret: {why!r}"
    assert "example.com" in why, why


def test_f12_control_plain_redirect_url_is_kept():
    k = session_keeper.SessionKeeper(SITE, 0, {}, lambda *a, **kw: None)
    k._page = _Page("https://example.com/login?next=%2Faccount")
    k._derive_check_url = lambda: "https://example.com/account"
    assert k._heartbeat_navigate() == (
        session_keeper.DEAD, "redirected to https://example.com/login?next=%2Faccount")


# ── F19 ─────────────────────────────────────────────────────────────────────

class _CaptchaRunner:
    site_id = "siteA"

    def __init__(self):
        self.config = {"manual_use_persistent_profile": False}
        self._captcha_solve_sessions = {}

    def _manual_profile_dir(self):
        return None

    def log_event(self, *a, **k):
        pass

    start_captcha_solve_session = ra.AuthMixin.start_captcha_solve_session


def test_f19_two_captcha_urls_in_one_second_get_distinct_session_ids(monkeypatch):
    monkeypatch.setattr(_login, "open_manual_login_browser", lambda *a, **k: _FakeHandle())
    frozen = types.SimpleNamespace(**{n: getattr(time, n) for n in dir(time) if not n.startswith("_")})
    frozen.time = lambda: 1800000000.0
    monkeypatch.setattr(ra, "time", frozen)
    r = _CaptchaRunner()
    a = r.start_captcha_solve_session("https://example.com/challenge/a")
    b = r.start_captcha_solve_session("https://example.com/challenge/b")
    assert a["ok"] and b["ok"], (a, b)
    assert a["session_id"] != b["session_id"], (
        f"F19_SESSION_ID_COLLISION: two URLs in one second share {a['session_id']!r}")
    assert a["session_id"].startswith("captcha-siteA-1800000000"), a
    assert b["session_id"].startswith("captcha-siteA-1800000000"), b


def test_f19_control_same_url_reuses_its_session():
    r = _CaptchaRunner()
    r._captcha_solve_sessions["https://example.com/c"] = {"session_id": "captcha-siteA-1"}
    got = r.start_captcha_solve_session("https://example.com/c")
    assert got == {"session_id": "captcha-siteA-1", "ok": True,
                   "url": "https://example.com/c", "reused": True}
