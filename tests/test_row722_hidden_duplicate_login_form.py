"""Row 722 live (kink.com/login, 2026-09-15 05:23Z): the page carries TWO
login forms.  The first in DOM order is a hidden modal (``form#loginPopup``,
``display:none``, AJAX submit handler); the second is the visible page form.
The fill walked past the hidden inputs (row 770), so the VISIBLE form held
the credentials -- and then every submit method resolved ``.first`` /
``document.querySelector`` and acted on the HIDDEN form:

  login submit: click submit selector -> skip (could not click submit button; tried 59 selectors)
  login submit: JS requestSubmit -> form.requestSubmit(); waiting...
  ...
  login: handing off for manual takeover -- Couldn't submit form: no submit method produced navigation

The fix makes every method act on the form a human sees: the click walk
skips matches that never become visible, and the JS-scoped methods anchor
on the visible password field rather than the first one in the DOM.

The page is rendered in a local headless chromium from an inline fixture
served by ``page.route``.  NO LIVE SITE IS TOUCHED and no login is started
anywhere; a missing browser SKIPS (this proves selection logic, not browser
availability), mirroring tests/test_element_pick_selector._launch.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722.test"
LOGIN_URL = ORIGIN + "/login"
MEMBERS_URL = ORIGIN + "/members"

# Two links before the forms, as on the live page: a Tab from <body> lands on
# a link, so Tab+Enter is not a submit either (it was not on the live page).
# Row 722s: the visible form posts. A GET login form is refused by the JS
# fallbacks (the credentials would enter the URL), which is a separate,
# measured contract -- this fixture is about WHICH form, not the method.
LOGIN_HTML = """<!doctype html><html><body>
<a tabindex="0" id="nav-a">VR</a> <a tabindex="0" id="nav-b">Men</a>
<form id="loginPopup" method="post" action="/login" style="display:none"
      onsubmit="return false">
  <input type="text" name="username" autocomplete="username">
  <input type="password" name="password">
  <button type="submit">Log in</button>
</form>
<form id="pageForm" method="post" action="/members">
  <input type="text" name="username" autocomplete="username">
  <input type="password" name="password">
  <button type="submit">LOG IN</button>
</form>
</body></html>"""

MEMBERS_HTML = "<!doctype html><html><body><h1>Members</h1></body></html>"

# The negative control: the same page with ONLY the hidden modal form.
LONE_URL = ORIGIN + "/login-lone"
LONE_HTML = (LOGIN_HTML[:LOGIN_HTML.index('<form id="pageForm"')]
             .replace('action="/login"', 'action="/login-lone"')
             + "</body></html>")

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


def _serve(route, request):
    url = request.url.split("?", 1)[0]
    if url == MEMBERS_URL:
        route.fulfill(status=200, content_type="text/html", body=MEMBERS_HTML)
    elif url == LONE_URL:
        route.fulfill(status=200, content_type="text/html", body=LONE_HTML)
    else:
        # GET or POST: the login page answers with itself, as the live site
        # does for a rejected POST, so a hidden-form submit "goes nowhere".
        route.fulfill(status=200, content_type="text/html", body=LOGIN_HTML)


@contextmanager
def _login_page(url=LOGIN_URL):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route(ORIGIN + "/**", _serve)
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


@pytest.fixture
def fast_clock(monkeypatch):
    """Keep the 8s post-method poll to one pass; sleeping is free."""
    from bulk_downloader.login_impl import submit

    class _Clock:
        now = 1_000.0

        def time(self):
            self.now += 5.0
            return self.now

        def sleep(self, _s):
            return None

    monkeypatch.setattr(submit, "time", _Clock())


def _values(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('input[name=username]')]"
        ".map(i => [i.form.id, i.value])")


def test_precondition_the_hidden_form_is_first_and_the_visible_form_is_second():
    with _login_page() as page:
        forms = page.evaluate(
            "() => [...document.querySelectorAll('form')]"
            ".map(f => [f.id, f.offsetParent !== null])")
        assert forms == [["loginPopup", False], ["pageForm", True]]
        assert page.locator("button[type=submit]").count() == 2
        assert not page.locator("button[type=submit]").first.is_visible()


def test_the_fill_lands_in_the_visible_form():
    """Row 770's walk already does this; it is the premise of the row."""
    from bulk_downloader.login_impl._common import _try_fill
    with _login_page() as page:
        ok, used = _try_fill(page, ["input[autocomplete='username']"],
                             "fixture-user", "username")
        assert ok, used
        assert _values(page) == [["loginPopup", ""],
                                 ["pageForm", "fixture-user"]]


def test_submit_acts_on_the_visible_form_not_the_hidden_duplicate(fast_clock):
    """THE ROW: the sweep must submit the form that holds the credentials."""
    from bulk_downloader.login_impl import submit
    with _login_page() as page:
        ok, info = submit._submit_login(
            page, ["button[type=submit]"], ["input[type=password]"])
        assert ok is True, (
            "the submit sweep acted on the hidden #loginPopup form and never "
            "reached the visible one: %r" % (info,))
        assert page.url.startswith(MEMBERS_URL), page.url
        assert info == "click submit selector", info


def test_negative_control_a_lone_hidden_form_still_produces_no_navigation(
        fast_clock):
    """With no visible form the sweep must still say so (nothing to click)."""
    from bulk_downloader.login_impl import submit
    with _login_page(LONE_URL) as page:
        assert page.locator("form").count() == 1
        ok, info = submit._submit_login(
            page, ["button[type=submit]"], ["input[type=password]"])
        assert ok is False
        assert info == "no submit method produced navigation"
        assert page.url == LONE_URL


def test_js_request_submit_scopes_to_the_visible_form(fast_clock):
    """With no click candidate the JS methods run first; they must anchor on
    the visible password field, not the first one in the DOM."""
    from bulk_downloader.login_impl import submit
    with _login_page() as page:
        ok, info = submit._submit_login(page, [], ["input[type=password]"])
        assert ok is True, info
        assert info == "JS requestSubmit", info
        assert page.url.startswith(MEMBERS_URL), page.url
