"""dl95-eporner-4 live (test2 6f85714f, eporner, 2026-09-29 01:47-01:50Z): a
re-login for an expired session opened eporner's login_url (the home page),
found no login field, and the LAST-DITCH username fallback

    form input:not([type='hidden']):not([type='submit']):not([type='button'])...

matched the header SEARCH box instead.  The account name was typed into it and
"submitted", and the evidence page was eporner's search results for the
account name:

  login: filled username via [form input:not([type=hidden]):not([type=submit])...]
  login: password field absent; clicked continue [button[type=submit]]
  Couldn't find password field ... tried 15 selectors -> manual takeover -> dead_letter

The fix: a search input (type=search, role=searchbox, inside role=search, in a
form whose action is a /search path, or named q/search/query) is never filled
as a login field, and it does not count as "the username field is already
visible" when a configured login trigger decides whether to open the modal.

The header markup is a REAL capture of https://www.eporner.com/login/
(tests/fixtures/dl95_eporner_4/, 2026-09-29, fetched on test2; no account
data).  Pages are rendered in a local headless chromium via ``page.route``;
NO LIVE SITE IS TOUCHED.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-eporner-4.test"
FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "dl95_eporner_4"
           / "eporner_login_header_search.html")
HEADER = FIXTURE.read_text(encoding="utf-8")
ACCOUNT = "fixture-account-name"

# The live page: the header search form and nothing else a filler can use.
HOME_HTML = f"<!doctype html><html><body>{HEADER}</body></html>"

# Positive control: the same header plus a real, visible login form.
LOGIN_FORM = """
<form id="loginForm" method="post" action="/login/">
  <input type="text" name="username" id="l_user">
  <input type="password" name="password" id="l_pass">
  <button type="submit">Log in</button>
</form>"""
WITH_FORM_HTML = f"<!doctype html><html><body>{HEADER}{LOGIN_FORM}</body></html>"

# A modal login behind a trigger link, as eporner's own
# EP.account.login.openModal('login', 'header') renders it.
MODAL_HTML = f"""<!doctype html><html><body>{HEADER}
<a href="/login/" id="open-login" onclick="document.getElementById('modal').style.display='block';return false;">Log in</a>
<div id="modal" style="display:none">{LOGIN_FORM}</div>
</body></html>"""

PAGES = {"/home": HOME_HTML, "/with-form": WITH_FORM_HTML, "/modal": MODAL_HTML}

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


def _serve(route, request):
    path = "/" + request.url.split("?", 1)[0].split("/", 3)[-1]
    route.fulfill(status=200, content_type="text/html",
                  body=PAGES.get(path, "<!doctype html><html><body>search results</body></html>"))


@contextmanager
def _page(path, html=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            if html is not None:
                page.route(ORIGIN + "/**", lambda route, _req: route.fulfill(
                    status=200, content_type="text/html", body=html))
            else:
                page.route(ORIGIN + "/**", _serve)
            page.goto(ORIGIN + path, wait_until="load")
            yield page
        finally:
            browser.close()


def _fallbacks():
    from bulk_downloader.login_impl.submit import USER_FIELD_FALLBACKS
    return list(USER_FIELD_FALLBACKS)


def _search_value(page):
    return page.locator("#srch").input_value()


def test_precondition_the_real_header_search_is_what_the_generic_fallback_matches():
    """The captured eporner search input is visible and the last-ditch
    fallback selector reaches it (so the RED below is the live mechanism)."""
    with _page("/home") as page:
        assert page.locator("#srch").is_visible()
        generic = _fallbacks()[-1]
        assert generic.startswith("form input:not(")
        assert page.locator(generic).first.get_attribute("id") == "srch"


def test_the_account_name_is_never_typed_into_the_search_box():
    from bulk_downloader.login_impl._common import _try_fill
    with _page("/home") as page:
        ok, info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert _search_value(page) == "", "the account name was typed into the site search box"
        assert ok is False, info


def test_the_failure_names_the_search_field_it_refused():
    """Distinctive diagnostic: 'nothing matched' and 'only a search box
    matched' are different situations and the message says which."""
    from bulk_downloader.login_impl._common import _try_fill
    with _page("/home") as page:
        ok, info = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok is False
        assert "search field" in info and "type=search" in info, info


def test_positive_control_a_real_login_form_beside_the_search_box_is_filled():
    from bulk_downloader.login_impl._common import _try_fill
    from bulk_downloader.login_impl.submit import PASS_FIELD_FALLBACKS
    with _page("/with-form") as page:
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok, used
        assert page.locator("#l_user").input_value() == ACCOUNT
        assert _search_value(page) == ""
        ok, used = _try_fill(page, list(PASS_FIELD_FALLBACKS), "pw-fixture", "password")
        assert ok, used
        assert page.locator("#l_pass").input_value() == "pw-fixture"


def test_a_configured_trigger_fires_when_only_the_search_box_is_visible():
    """Pre-fix the search box counted as 'username field is already visible',
    so a login_trigger on a site like eporner could never open the modal."""
    from bulk_downloader.login_impl._common import _fire_login_trigger_if_needed, _try_fill
    with _page("/modal") as page:
        needed, fired, detail = _fire_login_trigger_if_needed(
            page, "a#open-login", _fallbacks())
        assert (needed, fired) == (True, True), detail
        assert page.locator("#l_user").is_visible()
        ok, used = _try_fill(page, _fallbacks(), ACCOUNT, "username")
        assert ok, used
        assert page.locator("#l_user").input_value() == ACCOUNT
        assert _search_value(page) == ""


def test_a_visible_login_field_still_skips_the_trigger():
    from bulk_downloader.login_impl._common import _fire_login_trigger_if_needed
    with _page("/with-form") as page:
        assert _fire_login_trigger_if_needed(page, "a#nowhere", _fallbacks()) == (
            False, False, "username field is already visible")


@pytest.mark.parametrize("html,reason", [
    ('<form action="/search/"><input type="text" name="term" id="f"></form>', "form action"),
    ('<form action="/find"><input type="text" name="q" id="f"></form>', "name=q"),
    ('<div role="search"><form action="/x"><input type="text" name="k" id="f"></form></div>', "role=search"),
    ('<form action="/x"><input type="text" role="searchbox" name="k" id="f"></form>', "role=searchbox"),
    ('<form action="/x"><input type="search" name="k" id="f"></form>', "type=search"),
])
def test_each_search_spelling_is_refused(html, reason):
    from bulk_downloader.login_impl._common import _search_field_reason
    with _page("/x", html=f"<!doctype html><html><body>{html}</body></html>") as page:
        assert reason in _search_field_reason(page.locator("#f"))


@pytest.mark.parametrize("html", [
    '<form action="/login/"><input type="text" name="login" id="f"></form>',
    '<form action="/members/login?next=/search/"><input type="email" name="email" id="f"></form>',
    '<form action="/signin"><input type="text" name="user" id="f" placeholder="Username or email"></form>',
    '<input type="text" name="username" id="f">',
])
def test_login_fields_are_not_mistaken_for_search(html):
    from bulk_downloader.login_impl._common import _search_field_reason
    with _page("/x", html=f"<!doctype html><html><body>{html}</body></html>") as page:
        assert _search_field_reason(page.locator("#f")) == ""


def test_a_locator_that_cannot_evaluate_fails_open():
    """Fake/unsupported locators (unit-test doubles) are never refused."""
    from bulk_downloader.login_impl._common import _search_field_reason

    class _NoEval:
        def evaluate(self, _js):
            raise RuntimeError("no browser")

    class _NotAString:
        def evaluate(self, _js):
            return object()

    assert _search_field_reason(_NoEval()) == ""
    assert _search_field_reason(_NotAString()) == ""
