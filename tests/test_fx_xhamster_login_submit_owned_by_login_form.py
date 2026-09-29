"""fx-xhamster-login-submit (O1568d, spare12 10.0.70.183, 21:32Z; results/spare12/xhamster.md):
xhamster's header search is a <form class="search-container"> whose icon button is type=submit and precedes the login
modal's <form action="/login"> (DOM read on the VM, modal opened, nothing submitted). Live:

  login submit: click submit selector -> click [button[type=submit]]; waiting...
  login: post-submit page still anonymous ... (https://xhamster.com/search)

The first visible `button[type=submit]` was the SEARCH button; /search is the same origin, so the sweep counted it as
the submit, re-login was exhausted and the job went dead_letter. Fix: the selector click skips a match that sits in a
form without the password field when the password field sits in a form of its own.

Local headless chromium, fixture served by page.route; no live site, no credentials sent anywhere.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-xhamster.test"
HOME_URL = ORIGIN + "/"

PAGE_HTML = """<!doctype html><html><body>
<header>
  <form class="search-container positioned" action="/search"><input name="q" placeholder="Search for videos">
    <button type="submit" id="search"><svg width="16" height="16"></svg></button></form>
</header>
<div role="dialog" style="position:fixed;top:80px;left:300px;z-index:10;background:#fff;padding:20px">
  <form class="loginForm-d69c7" action="/login" method="post" onsubmit="event.preventDefault(); window.__login=(window.__login||0)+1; history.pushState({}, '', '/logged-in');">
    <input autocomplete="username" name="username" value="fixture-user">
    <input type="password" name="password" value="fixture-pass">
    <button type="submit" id="login">Login</button>
  </form>
</div>
</body></html>"""
SEARCH_HTML = "<!doctype html><html><body><h1>Porn Video Search</h1></body></html>"


def _serve(route, request):
    if request.url.split("?", 1)[0].startswith(ORIGIN + "/search"):
        route.fulfill(status=200, content_type="text/html", body=SEARCH_HTML)
    else:
        route.fulfill(status=200, content_type="text/html", body=PAGE_HTML)


@contextmanager
def _page(html=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=20000,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:
            pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE (T5): {e}")
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", _serve)
            page.goto(HOME_URL, wait_until="load")
            if html is not None:
                page.set_content(html)
            yield page
        finally:
            browser.close()


def test_the_login_forms_own_submit_is_clicked_not_the_search_button():
    """THE ROW, with the shipped selector lists (the live sweep's first hit was button[type=submit])."""
    from bulk_downloader.login_impl import submit
    with _page() as page:
        ok, info = submit._submit_login(page, list(submit.SUBMIT_FALLBACKS),
                                        list(submit.PASS_FIELD_FALLBACKS))
        assert not page.url.startswith(ORIGIN + "/search"), (
            f"FX_XHAMSTER_SEARCH_SUBMITTED: the sweep clicked the header SEARCH button as the login "
            f"submit ({page.url}; sweep={ok!r} {info!r})")
        assert page.evaluate("() => window.__login || 0") == 1, (ok, info, page.url)
        assert ok is True and "click submit selector" in info, (ok, info)


def test_accept_predicate_vets_each_match():
    from bulk_downloader.login_impl import submit
    js = submit._SUBMIT_OWNED_BY_LOGIN_FORM_JS
    pf = list(submit.PASS_FIELD_FALLBACKS)
    with _page() as page:
        assert page.locator("#search").evaluate(js, pf) is False
        assert page.locator("#login").evaluate(js, pf) is True


@pytest.mark.parametrize("html,expect", [
    # formless button: not another form's
    ("<form><input name=q></form><input type=password><button id=b>Go</button>", True),
    # two-step login: no password field yet -> the username form's Continue is fine
    ("<form><input name=q><button id=b type=submit>Continue</button></form>", True),
    # password field outside any form: no form owns the login -> keep the old click
    ("<form><input name=q><button id=b type=submit>Go</button></form><input type=password>", True),
    # hidden password field in its own form does not count as the login form
    ("<form><input name=q><button id=b type=submit>Go</button></form>"
     "<form style='display:none'><input type=password></form>", True),
])
def test_control_other_shapes_keep_the_old_click(html, expect):
    from bulk_downloader.login_impl import submit
    with _page("<!doctype html><html><body>" + html + "</body></html>") as page:
        got = page.locator("#b").evaluate(submit._SUBMIT_OWNED_BY_LOGIN_FORM_JS,
                                          list(submit.PASS_FIELD_FALLBACKS))
        assert got is expect, html
