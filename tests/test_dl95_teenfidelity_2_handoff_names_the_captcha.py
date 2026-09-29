"""dl95-teenfidelity-2 (harness-work/DOT95-LANE/live-dl95-teenfidelity-1/LIVE-RESULT-A5-A.md, shot TFL__1-login.png):
the teenfidelity login page carries an invisible reCAPTCHA (g-recaptcha + data-sitekey), yet a failed login handed
off with only "Couldn't submit form: no submit method produced navigation". The manual-takeover message now names a
reCAPTCHA / hCaptcha / Turnstile mount when the page has one.

Detection runs against real DOM in a local headless chromium (page.set_content; no network); the wiring runs the
real do_login through the row-708 fixture driver with the browser boundary stubbed. No credentials.
"""

from __future__ import annotations

import pytest
from test_row708_no_nav_login_is_not_success import _drive, _jar

BD_GATE_SCOPE = "module"

LOGIN_FORM = (
    '<form action="/login" method="post"><input name="username"><input name="password" type="password">'
    "%s<button type=submit>Login</button></form>"
)
MOUNTS = {
    "invisible reCAPTCHA": '<div class="g-recaptcha" data-sitekey="fixture-key" data-size="invisible"'
    ' data-callback="onSubmit"></div>',
    "reCAPTCHA": '<div class="g-recaptcha" data-sitekey="fixture-key"></div>',
    "hCaptcha": '<div class="h-captcha" data-sitekey="fixture-key"></div>',
    "Cloudflare Turnstile": '<div class="cf-turnstile" data-sitekey="fixture-key"></div>',
    "": "",
}


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(
            headless=True,
            timeout=20000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except PlaywrightError as e:
        pytest.fail(
            f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}"
        )


def test_the_captcha_mount_is_named_on_a_real_page():
    """Each mount is named -- a g-recaptcha with data-sitekey is never called Turnstile; a plain form names none."""
    from bulk_downloader.login_impl import submit
    from playwright.sync_api import sync_playwright

    seen = {}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            for want, mount in MOUNTS.items():
                page.set_content(LOGIN_FORM % mount)
                seen[want] = submit._captcha_mount_name(page)
        finally:
            browser.close()
    assert seen == {k: k for k in MOUNTS}, seen


def test_the_manual_takeover_message_names_the_recaptcha(monkeypatch, tmp_path):
    """THE ROW: the unconvincing no-nav submit hands off naming the reCAPTCHA."""
    from bulk_downloader.login_impl import submit

    monkeypatch.setattr(
        submit, "_captcha_mount_name", lambda page: "invisible reCAPTCHA"
    )
    before = _jar(["pref_a"])
    result, _ = _drive(
        monkeypatch,
        tmp_path,
        branch="ajax",
        before=before,
        after=before,
        allow_manual=True,
    )
    assert result[0] == "MANUAL_PENDING", repr(result[0])
    assert result[1] == (
        "Couldn't submit form: fixture submit -- invisible reCAPTCHA on the login page; "
        "a human (or a captcha relay) must solve it"
    ), result[1]


def test_negative_control_no_mount_keeps_the_plain_message(monkeypatch, tmp_path):
    """No captcha mount -> the message is exactly the old one."""
    from bulk_downloader.login_impl import submit

    monkeypatch.setattr(submit, "_captcha_mount_name", lambda page: "")
    before = _jar(["pref_a"])
    result, _ = _drive(
        monkeypatch,
        tmp_path,
        branch="ajax",
        before=before,
        after=before,
        allow_manual=True,
    )
    assert result[0] == "MANUAL_PENDING", repr(result[0])
    assert result[1] == "Couldn't submit form: fixture submit", result[1]
