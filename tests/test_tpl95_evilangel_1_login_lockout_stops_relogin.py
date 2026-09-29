"""tpl95-evilangel-1 (evilangel, test2, 2026-09-29 03:21Z): GUI Login landed on
https://www.evilangel.com/login-abused, the site's login-abuse lockout
("Your IP was blocked! Please try later or call the customer support ...",
login_evidence/manual-takeover-page-gate-refused-exit-here-...-032225Z).
The app reported "Page gate refused 'EXIT HERE' ... Couldn't find username
field" -- an age gate and a missing form -- and nothing stopped the next
automatic login, which deepens the lockout.

Contract: a login page with no form whose content is a lockout is reported
as a lockout, filed durably, and every AUTOMATIC credential login for that
site is refused while the hold stands. The operator's manual login is not
held. HERMETIC ONLY (PM LOGIN-LOCKOUT amendment): a local http server and a
headless chromium; no live site is touched. A missing browser SKIPS.
"""
from __future__ import annotations

import contextlib
import http.server
import importlib
import threading

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial

# Body text of the kept evidence page, verbatim (scripts/images dropped).
LOCKOUT_HTML = b"""<!doctype html><html><head><title>Evil Angel</title></head>
<body><header><a href="/en/login">Login</a> <a href="/en/join">Become a member</a></header>
<main><p class="error">Your IP was blocked! Please try later or call the customer
support at 1-877-711-7334 (Toll Free US &amp; Canada) OR 1-514-334-1887</p></main>
<footer>Home Videos Movies Pornstars Photos Live Cams Apparel Login Partners</footer>
</body></html>"""
# Negative control: no form and no lockout wording -> the existing hand-off.
NO_FORM_HTML = b"""<!doctype html><html><body><h1>Access Denied</h1>
<p>Welcome. Our login page is being updated.</p></body></html>"""

SITE = "tpl95-evilangel-fixture"


def _handler(html):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args):
            pass
    return Handler


@contextlib.contextmanager
def _serving(html):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(html))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fresh_db(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    sk = importlib.import_module("bulk_downloader.session_keeper")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "evilangel.db"))
    db.db_init()
    return sk


def _do_login(monkeypatch, base_url, *, allow_manual):
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

    monkeypatch.setattr(cloak, "launch_browser", launch_for_test)
    monkeypatch.setattr(submit_impl, "USER_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "PASS_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "SUBMIT_FALLBACKS", [])
    monkeypatch.setattr(submit_impl.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit_impl, "write_login_evidence",
                        lambda *a, **k: None)
    config = {
        "login_url": base_url + "/en/login",
        "username": "u", "password": "p",
        "user_field": "#username", "pass_field": "#password",
        "submit_btn": "#submit", "success_url": "/en/members",
        "wait": 0, "use_real_chrome": False, "use_stealth": False,
        "use_stealth_library": False,
    }
    result = submit_impl.do_login(config, allow_manual_takeover=allow_manual,
                                  site_id=SITE)
    if result and result[0] == "MANUAL_PENDING":
        pw, browser, _ctx = result[2]
        browser.close()
        pw.stop()
    return result


@pytest.mark.parametrize("allow_manual", [False, True])
def test_lockout_page_is_reported_as_a_lockout_and_opens_the_hold(
        monkeypatch, tmp_path, allow_manual):
    sk = _fresh_db(monkeypatch, tmp_path)
    with _serving(LOCKOUT_HTML) as base:
        result = _do_login(monkeypatch, base, allow_manual=allow_manual)
    assert result[0] is False and "Login lockout" in str(result[1]), (
        f"TPL95-EVILANGEL-1: lockout page reported as {result[:2]!r}, "
        "expected a 'Login lockout' failure (not a hand-off / missing form)")
    assert "Your IP was blocked" in str(result[1])
    assert isinstance(result[1], sk.LoginLockout)
    held = sk.reserve_login_attempt(SITE, "runner_auth.login_async", 3)
    assert held["status"] == "LOCKOUT" and held["granted"] is False, (
        f"TPL95-EVILANGEL-1: automatic login after a lockout was {held!r}; "
        "it must be refused while the hold stands")


def test_negative_control_no_form_without_lockout_wording_is_unchanged(
        monkeypatch, tmp_path):
    sk = _fresh_db(monkeypatch, tmp_path)
    with _serving(NO_FORM_HTML) as base:
        result = _do_login(monkeypatch, base, allow_manual=False)
    assert result[0] is False and "Login lockout" not in str(result[1])
    assert "username" in str(result[1]).lower(), result[1]
    granted = sk.reserve_login_attempt(SITE, "runner_auth.login_async", 3)
    assert granted["status"] == "OK" and granted["granted"] is True


def test_hold_refuses_automatic_sources_writes_nothing_and_spares_manual(
        monkeypatch, tmp_path):
    sk = _fresh_db(monkeypatch, tmp_path)
    sk.record_login_lockout(SITE, "'Your IP was blocked' at /login-abused")
    for source in ("runner_auth.login_async", "app._do_login_for_keeper"):
        r = sk.reserve_login_attempt(SITE, source, 3)
        assert r["status"] == "LOCKOUT" and not r["granted"], (source, r)
        assert "login lockout hold" in r["reason"]
    assert sk.login_attempts_for_day(SITE)["count"] == 0, (
        "a lockout refusal wrote an attempt row")
    with pytest.raises(RuntimeError, match="login lockout hold"):
        sk.record_login_attempt(SITE, "keeper.direct")
    manual = sk.reserve_login_attempt(SITE, "runner_auth.start_manual_login", 3)
    assert manual["status"] == "OK" and manual["granted"] is True
    other = sk.reserve_login_attempt("another-site", "runner_auth.login_async", 3)
    assert other["status"] == "OK" and other["granted"] is True


def test_hold_expires(monkeypatch, tmp_path):
    sk = _fresh_db(monkeypatch, tmp_path)
    sk.record_login_lockout(SITE, "old lockout")
    real = sk.time.time
    monkeypatch.setattr(sk.time, "time",
                        lambda: real() + sk.LOGIN_LOCKOUT_HOLD_S + 60)
    r = sk.reserve_login_attempt(SITE, "runner_auth.login_async", 3)
    assert r["status"] == "OK" and r["granted"] is True, r


@pytest.mark.parametrize("text,expected", [
    ("Your IP was blocked! Please try later", True),
    ("Too many failed login attempts. Try again in 30 minutes.", True),
    ("Your account has been locked.", True),
    ("Access Denied", False),
    ("ACCESS DENIED You must be a member to watch this video", False),
])
def test_lockout_language_is_the_lockout_subset_only(text, expected):
    from bulk_downloader.interstitial import LOGIN_LOCKOUT_LANGUAGE
    assert bool(LOGIN_LOCKOUT_LANGUAGE.search(text)) is expected, text
