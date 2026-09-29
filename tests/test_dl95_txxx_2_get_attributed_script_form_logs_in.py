"""dl95-txxx-2 (harness-work/UIUX-20260928/download-95/B7-B/p1/txxx/RESULT.md, HIGH).

Measured on test2 (txxx 0ce0eef8, journal 02:09-02:10Z): the sweep clicked the age gate's "Enter" (m1), then both JS
fallbacks refused the login form -- "form method is GET -- refused" -- and the sweep stopped; auth_state stayed
expired. The live form (takeover evidence, values not read) is a Vue form: <form class="form"> with no method, a
username and password input and a "Sign in" button; the page script posts it. Row 722s made the refusal to protect the
credentials from a native GET; the refusal also blocked the site's own script path.

Contract after the fix: (1) for a login form without method=POST, a network-layer guard (page.route) aborts, before it
is sent, any GET whose query carries the password field -- page script cannot bypass it (lens cx-worker-2 REFUTE g1:
a handler that calls form.submit(), an earlier capture listener that stops propagation); (2) the JS fallback dispatches
a synthetic submit event -- the page's handlers run, the browser never submits natively -- and counts it submitted
only if a handler claimed it (preventDefault). No request carrying the password ever reaches the server or page.url.

Hermetic: Playwright route fixtures on a .test origin (as tests/test_row722s_login_form_is_never_submitted_by_get.py),
headless Chromium, a dummy password.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest
from bulk_downloader.login_impl import submit

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-txxx-2.test"
LOGIN_URL = ORIGIN + "/login/"
PASSWORD = "fixture-not-a-secret-7c1d"

FORM = """<!doctype html><html><head><title>Login</title></head><body>
<div class="age-gate"><button onclick="this.parentNode.remove()">Enter</button></div>
<form class="form">
  <input type="text" placeholder="Username or Email" class="input">
  <input type="password" placeholder="Password" name="password" class="input">
  <button class="btn btn-block btn-red">Sign in</button>
</form>
<script>{script}</script></body></html>"""
# txxx shape: the page script claims the submit and posts the fields itself, then navigates to the member area.
SCRIPT_POSTS = """document.querySelector('form.form').addEventListener('submit', ev => {
  ev.preventDefault();
  const f = ev.target;
  fetch('/api/login', {method: 'POST', body: JSON.stringify({u: f[0].value, p: f[1].value})})
    .then(() => { location.href = '/members/'; });
});"""
# Same, but the handler is delegated on document (jQuery-style): it runs after the event left the form.
SCRIPT_DELEGATED = SCRIPT_POSTS.replace("document.querySelector('form.form').addEventListener",
                                        "document.addEventListener")
# A handler that neither cancels nor posts: the site expects the browser's native GET.
SCRIPT_NATIVE = """document.querySelector('form.form').addEventListener('submit', ev => { ev.stopPropagation(); });"""
CHROME = os.environ.get("BD_PW_CHROME", "")


@contextmanager
def _page(script):
    from playwright.sync_api import sync_playwright
    html = FORM.format(script=script)
    seen = []

    def _serve(route, request):
        seen.append((request.method, request.url, request.post_data or ""))
        url = request.url.split("?", 1)[0]
        if url == LOGIN_URL:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif url == ORIGIN + "/api/login":
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        elif url == ORIGIN + "/members/":
            route.fulfill(status=200, content_type="text/html", body="<h1>My account</h1>")
        else:
            route.fulfill(status=404, content_type="text/plain", body="not fixture")

    with sync_playwright() as p:
        args = ["--no-sandbox", "--disable-dev-shm-usage"]
        kw = {"executable_path": CHROME} if CHROME and os.path.exists(CHROME) else {}
        browser = p.chromium.launch(headless=True, timeout=20000, args=args, **kw)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.context.route(ORIGIN + "/**", _serve)   # context: a popup is served (and recorded) too
            page.goto(LOGIN_URL, wait_until="load")
            page.fill("input[type=text]", "someone@example.test")
            page.fill("input[type=password]", PASSWORD)
            yield page, seen
        finally:
            browser.close()


def _no_url_leak(page, seen):
    urls = [u for _, u, _ in seen] + [page.url]
    assert not [u for u in urls if PASSWORD in u or "password=" in u], "DL95_TXXX2_CREDENTIAL_IN_URL"


def _sweep(page):
    # the measured order: the age gate's Enter is the first submit-button candidate
    return submit._submit_login(page, ["button:text-is('Enter')"], ["input[name=password]"])


def test_a_get_attributed_form_posted_by_its_page_script_logs_in():
    with _page(SCRIPT_POSTS) as (page, seen):
        ok, method = _sweep(page)
        _no_url_leak(page, seen)
        assert ok is True, f"DL95_TXXX2_SCRIPT_FORM_REFUSED: {method}"
        assert method == "JS requestSubmit", method
        assert page.url == ORIGIN + "/members/", page.url
        posts = [b for m, u, b in seen if m == "POST" and u == ORIGIN + "/api/login"]
        assert len(posts) == 1 and PASSWORD in posts[0], "the page script must have posted the login once"


def test_a_delegated_page_handler_also_claims_the_submit():
    with _page(SCRIPT_DELEGATED) as (page, seen):
        ok, method = _sweep(page)
        _no_url_leak(page, seen)
        assert ok is True and page.url == ORIGIN + "/members/", (ok, method, page.url)


def test_control_a_handler_that_expects_the_native_get_is_still_refused():
    with _page(SCRIPT_NATIVE) as (page, seen):
        ok, method = _sweep(page)
        _no_url_leak(page, seen)
        assert ok is False and method == submit.GET_FORM_REFUSED, (ok, method)
        assert not [1 for m, u, _ in seen if m == "GET" and u.startswith(LOGIN_URL + "?")], "a native GET was sent"


def test_control_no_page_handler_is_still_refused():
    with _page("") as (page, seen):
        ok, method = _sweep(page)
        _no_url_leak(page, seen)
        assert ok is False and method == submit.GET_FORM_REFUSED, (ok, method)
        assert page.url == LOGIN_URL


def test_a_script_submit_without_navigation_is_named_not_refused():
    """The script took the login but the page stayed put: the reason must not claim a refusal."""
    no_nav = SCRIPT_POSTS.replace("location.href = '/members/';", "")
    with _page(no_nav) as (page, seen):
        ok, method = _sweep(page)
        _no_url_leak(page, seen)
        assert ok is False and method == submit.GET_FORM_SCRIPT_NO_NAV, (ok, method)
        assert [1 for m, u, _ in seen if m == "POST" and u == ORIGIN + "/api/login"]


@pytest.mark.parametrize("script", [
    # lens cx-worker-2: the handler claims the event, then submits natively (a GET with the password)
    "document.querySelector('form').addEventListener('submit', ev => {ev.preventDefault(); ev.target.submit();});",
    # lens cx-worker-2: an earlier capture listener hides the event from every later listener
    "window.addEventListener('submit', ev => {ev.stopImmediatePropagation();}, true);",
    # lens cx-worker-2 (r2b): the same native submit into a target=_blank popup -- a new page's first request
    ("document.querySelector('form').target = '_blank';"
     "document.querySelector('form').addEventListener('submit', ev => {ev.preventDefault(); ev.target.submit();});"),
], ids=["handler-calls-form-submit", "earlier-capture-listener", "popup-target-blank"])
def test_page_script_cannot_route_the_password_into_a_get(script):
    with _page(script) as (page, seen):
        ok, method = _sweep(page)
        _no_url_leak(page, seen)
        for other in page.context.pages:    # a popup is a page of the same context
            assert PASSWORD not in other.url and "password=" not in other.url, f"DL95_TXXX2_CREDENTIAL_IN_URL: {other.url}"
        assert ok is False, (ok, method)
        assert method in (submit.GET_FORM_REFUSED, submit.GET_FORM_SCRIPT_NO_NAV), method


def test_a_native_click_on_the_forms_own_button_is_aborted_too():
    """m1 clicking the real submit button of a GET form with no handler: the native GET is aborted at the network."""
    with _page("") as (page, seen):
        ok, method = submit._submit_login(page, ["button.btn-red"], ["input[name=password]"])
        _no_url_leak(page, seen)
        assert ok is False and page.url == LOGIN_URL, (ok, method, page.url)


@pytest.mark.parametrize("form,installed", [
    ('<form class="form"><input type="password" name="password"></form>', True),
    ('<form method="post"><input type="password" name="password"></form>', False),   # POST: nothing to guard
    ('<form><input type="password"></form>', False),                                # unnamed: never serialized
])
def test_the_network_guard_is_installed_only_for_a_named_password_in_a_get_form(form, installed):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.set_content(f"<!doctype html><html><body>{form}</body></html>")
            assert submit._guard_credential_get(page, ["input[type=password]"]) is installed
        finally:
            browser.close()
