"""dl95-eporner-5 live (bd3 10.0.70.53, eporner 0c546602, 2026-09-29 20:31Z, O1567
fx-eporner): a re-login for an expired session opened eporner's login_url (the
home page).  dl95-eporner-4 already keeps the header SEARCH box out of the
username fill, but the home page sidebar carries a second visible form: the
video filter with two ``<input type="range">`` duration sliders and an Apply
submit button.  The last-ditch fallback

    form input:not([type='hidden']):not([type='submit']):not([type='button'])...

matches the FIRST slider, so

  * ``_fire_login_trigger_if_needed`` decided "username field is already
    visible" and never clicked the configured trigger
    (a[data-nav-header='login_open'] -> the modal), and
  * ``_try_fill`` "typed the account name" into the slider and the staged-login
    continue click ``button[type=submit]`` pressed the filter's Apply:

  login: filled username via [form input:not([type='hidden'])...]
  login: password field absent; clicked continue [button[type=submit]] -- retrying password fill
  login: handing off for manual takeover -- Couldn't find password field ... -> dead_letter

A slider, checkbox, radio, colour, file or date control is never a username or
password field.  The markup is the REAL sidebar capture
(tests/fixtures/dl95_eporner_5/), rendered in a local headless chromium via
``page.route``; NO LIVE SITE IS TOUCHED.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-eporner-5.test"
FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "dl95_eporner_5"
           / "eporner_home_filter_ranges.html")
BODY = FIXTURE.read_text(encoding="utf-8")
HOME_HTML = f"<!doctype html><html><body>{BODY}</body></html>"
ACCOUNT = "fixture-account-name"
TRIGGER = "a[data-nav-header='login_open'], a[href='/login/']"
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


@contextmanager
def _page(html=HOME_HTML):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", lambda route, _req: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(ORIGIN + "/home", wait_until="load")
            yield page
        finally:
            browser.close()


def _fallbacks():
    from bulk_downloader.login_impl.submit import USER_FIELD_FALLBACKS
    return list(USER_FIELD_FALLBACKS)


def _sliders_untouched(page):
    return (page.locator("#home-video-filter-duration-min").input_value() == "0"
            and page.locator("#home-video-filter-duration-max").input_value() == "61"
            and page.locator("#srch").input_value() == "")


def test_precondition_the_generic_fallback_reaches_the_visible_range_slider():
    """The live mechanism: once the search box is skipped, the last-ditch
    fallback still matches a visible, positive-size range input."""
    with _page() as page:
        first = page.locator("#home-video-filter-duration-min")
        assert first.is_visible()
        generic = _fallbacks()[-1]
        assert generic.startswith("form input:not(")
        ids = [page.locator(generic).nth(i).get_attribute("id")
               for i in range(page.locator(generic).count())]
        assert "home-video-filter-duration-min" in ids, ids


def test_a_range_slider_is_not_a_search_or_credential_field():
    from bulk_downloader.login_impl._common import _is_search_field
    with _page() as page:
        is_bad, why = _is_search_field(page.locator("#home-video-filter-duration-min"))
        assert is_bad and "type=range" in why, why


def test_the_account_name_is_never_typed_into_a_slider():
    from bulk_downloader.login_impl._common import _try_fill
    with _page() as page:
        ok, info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is False, info
        assert _sliders_untouched(page)
        assert "type=range" in info, info  # distinctive diagnostic: names what it refused


def test_a_configured_trigger_fires_although_the_filter_sliders_are_visible():
    """Pre-fix the slider counted as 'username field is already visible', so
    eporner's login trigger never opened the modal."""
    from bulk_downloader.login_impl._common import (
        _fire_login_trigger_if_needed,
        _try_fill,
    )
    from bulk_downloader.login_impl.submit import PASS_FIELD_FALLBACKS
    with _page() as page:
        assert not page.locator("#modal_user").is_visible()
        needed, fired, detail = _fire_login_trigger_if_needed(page, TRIGGER, _fallbacks())
        assert (needed, fired) == (True, True), detail
        assert page.locator("#modal_user").is_visible()
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok, used
        assert page.locator("#modal_user").input_value() == ACCOUNT
        ok, used = _try_fill(page, list(PASS_FIELD_FALLBACKS), "pw-fixture", "password")
        assert ok, used
        assert page.locator("#modal_pass").input_value() == "pw-fixture"
        assert _sliders_untouched(page)


@pytest.mark.parametrize("itype", ["text", "email", "tel", "number", "password", "url"])
def test_negative_control_text_like_inputs_are_still_fillable(itype):
    """Positive control beside the zero: only slider-like controls are refused;
    every text-like credential input type (incl. number/tel for OTP codes) is not."""
    from bulk_downloader.login_impl._common import _is_search_field
    html = f'<!doctype html><html><body><form action="/login/"><input type="{itype}" name="u" id="f"></form></body></html>'
    with _page(html) as page:
        assert _is_search_field(page.locator("#f")) == (False, "")


@pytest.mark.parametrize("itype", ["range", "checkbox", "radio", "color", "file", "date", "image", "reset"])
def test_each_non_text_control_is_refused(itype):
    from bulk_downloader.login_impl._common import _is_search_field
    html = f'<!doctype html><html><body><form action="/login/"><input type="{itype}" name="u" id="f"></form></body></html>'
    with _page(html) as page:
        is_bad, why = _is_search_field(page.locator("#f"))
        assert is_bad and f"type={itype}" in why, why
