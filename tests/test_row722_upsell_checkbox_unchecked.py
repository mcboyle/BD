"""Row 722 (operator, 2026-09-15, binding): "verify any upsell check boxes
are unchecked and verified and then continue to the site. This is part of
fixing the row." Adult-site login/gate pages carry pre-checked cross-sale
boxes ("Yes! add <site> for $1", "Get a bonus site", "special offer",
"trial", "newsletter") -- submitting with them checked is a purchase risk.
"Remember me" (Phase 19, ``_try_check_remember_me``) must stay checked.

The page is rendered in a local headless chromium from an inline fixture
served by ``page.route``, mirroring
tests/test_row722_hidden_duplicate_login_form.py. NO LIVE SITE IS TOUCHED
and no login is started anywhere; a missing browser SKIPS.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722b.test"
LOGIN_URL = ORIGIN + "/login"

# (1) Remember me -- checked, must stay checked.
# (2) "YES! Add MILF Hunter for $1 trial" -- checked upsell, must be unchecked.
# (3) "Send me special offers" -- checked upsell, must be unchecked.
# (4) unlabelled checked box -- must be left alone, logged UNKNOWN.
# (5) hidden checked upsell box -- must be left untouched (not visible).
# (6) a remember-me box whose label ALSO carries an upsell trigger word --
#     proves the remember-me exclusion is checked (and wins) before the
#     upsell match, not merely that a plain "Remember me" label happens
#     not to match the upsell vocabulary.
FORM_HTML = """
<label for="remember">Remember me
  <input type="checkbox" id="remember" name="remember" checked>
</label>
<label for="upsell1">YES! Add MILF Hunter for $1 trial
  <input type="checkbox" id="upsell1" name="addsite" checked>
</label>
<label for="upsell2">Send me special offers
  <input type="checkbox" id="upsell2" name="newsletter" checked>
</label>
<input type="checkbox" id="mystery" name="mystery" checked>
<label for="hidden1" style="display:none">Get a bonus site for $1
  <input type="checkbox" id="hidden1" name="hiddenupsell" checked
         style="display:none">
</label>
<label for="remember2">Remember me and get bonus access
  <input type="checkbox" id="remember2" name="remember2" checked>
</label>
"""

LOGIN_HTML = ("<!doctype html><html><body><form id=\"loginForm\" "
              "method=\"post\" action=\"/login\">" + FORM_HTML +
              "</form></body></html>")

# Negative control: only Remember me on the page -- nothing should be acted
# on, and no upsell log line should be emitted.
NEGATIVE_HTML = """<!doctype html><html><body><form id="loginForm">
<label for="remember">Remember me
  <input type="checkbox" id="remember" name="remember" checked>
</label>
</form></body></html>"""

# Row 722: an operator-named chrome only (BD_PW_CHROME). The retired sandbox
# home default that tests/test_element_pick_selector.py still carries is a
# ratcheted population (tests/test_sandbox_home_stays_retired.py) and exists
# on no host this runs on; the fallback below is Playwright's own chromium.
CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip (T5): {e}")


def _serve(html):
    def handler(route, request):
        route.fulfill(status=200, content_type="text/html", body=html)
    return handler


@contextmanager
def _page_with(html, url=LOGIN_URL):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route(ORIGIN + "/**", _serve(html))
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


def _checked(page, elem_id):
    return page.locator(f"#{elem_id}").is_checked()


def test_upsell_boxes_unchecked_and_verified_remember_me_and_unknown_untouched():
    """THE ROW: checked upsell boxes get unchecked & verified; Remember me
    stays checked; the unlabelled box and the hidden box are left alone."""
    from bulk_downloader.login_impl import submit
    with _page_with(LOGIN_HTML) as page:
        acted = submit._uncheck_upsell_boxes(page)

        assert _checked(page, "remember") is True, (
            "Remember me was unchecked -- it must stay checked")
        assert _checked(page, "upsell1") is False, (
            "'$1 trial' upsell box was left checked before submit")
        assert _checked(page, "upsell2") is False, (
            "'special offers' upsell box was left checked before submit")
        assert _checked(page, "mystery") is True, (
            "unlabelled box must be left alone (never clicked blind)"
        )
        assert _checked(page, "hidden1") is True, (
            "hidden upsell box must be left untouched"
        )
        assert _checked(page, "remember2") is True, (
            "a remember-me box must stay checked even when its label also "
            "carries an upsell trigger word -- remember-me exclusion must "
            "be checked before the upsell match"
        )

        assert sorted(acted) == sorted(
            ["YES! Add MILF Hunter for $1 trial", "Send me special offers"]
        ), acted


def test_negative_control_only_remember_me_nothing_acted_on_no_log():
    """A form with only Remember me: nothing acted on, no upsell log line."""
    import io
    import sys as _sys
    from bulk_downloader.login_impl import submit
    with _page_with(NEGATIVE_HTML) as page:
        buf = io.StringIO()
        old_stderr = _sys.stderr
        _sys.stderr = buf
        try:
            acted = submit._uncheck_upsell_boxes(page)
        finally:
            _sys.stderr = old_stderr
        assert acted == [], acted
        assert _checked(page, "remember") is True
        assert "upsell" not in buf.getvalue()
