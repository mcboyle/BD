"""Row 722s live (hustlerunlimited.com/login/, 2026-09-15 14:17Z): the login
form declares no method. The site's own script answers the Login click with
an XHR (no navigation), so the sweep fell through to its JS fallbacks --
requestSubmit(), then form.submit() -- which submitted the form the browser's
default way: a GET. The typed username and password landed in the URL
(/login/?login-id=...&pwd-id=...), and from there in the "Expected URL
contains ..., got <url>" reason, i.e. login_status, journalctl and the
takeover evidence filename.

Two contracts: (1) the JS fallbacks never submit a GET-method form that holds
a password field; (2) a URL quoted in a login diagnostic never carries query
VALUES.
"""
from __future__ import annotations

import inspect
import os
from contextlib import contextmanager

from bulk_downloader.login_impl import submit
from bulk_downloader.login_impl._common import log_url

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722s-get.test"
LOGIN_URL = ORIGIN + "/login/"
PASSWORD = "fixture-not-a-secret-9f2c"

# hustlerunlimited shape: no method attribute (=> GET), a submit input, and a
# click handler that swallows the click (the site logs in over XHR).
GET_FORM_HTML = f"""<!doctype html><html><head><title>Login</title></head><body>
<form action="{LOGIN_URL}" id="lf">
  <input type="email" id="login-id" name="login-id" autocomplete="email">
  <input type="password" id="pwd-id" name="pwd-id">
  <input type="hidden" name="cf-turnstile-response" value="">
  <input type="submit" value="Login">
</form>
<script>
// The click path (m1) is neutralised so the sweep reaches the JS fallbacks;
// the form's own submit is NOT prevented: if a fallback submits this GET
// form the browser navigates to /login/?login-id=...&pwd-id=<password>.
document.querySelector('input[type=submit]').addEventListener('click', ev => ev.preventDefault());
</script></body></html>"""

POST_FORM_HTML = GET_FORM_HTML.replace('<form action=', '<form method="post" action=').replace(
    "</script>",
    "document.getElementById('lf').addEventListener('submit', ev => ev.preventDefault());</script>")
CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        return p.chromium.launch(headless=True, timeout=20000, args=args,
                                 executable_path=CHROME)
    return p.chromium.launch(headless=True, timeout=20000, args=args)


@contextmanager
def _page(html):
    from playwright.sync_api import sync_playwright

    def _serve(route, request):
        url = request.url.split("?", 1)[0]
        if url == LOGIN_URL:
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.fulfill(status=404, content_type="text/plain", body="not fixture")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route(ORIGIN + "/**", _serve)
            page.goto(LOGIN_URL, wait_until="load")
            page.fill("#login-id", "someone@example.test")
            page.fill("#pwd-id", PASSWORD)
            yield page
        finally:
            browser.close()


def test_the_js_fallbacks_refuse_a_get_form_so_the_password_never_enters_the_url():
    with _page(GET_FORM_HTML) as page:
        ok, method = submit._submit_login(
            page, ["input[type=submit]"], ["#pwd-id"])
        assert PASSWORD not in page.url and "pwd-id=" not in page.url, (
            f"credentials went into the URL: the sweep GET-submitted a password "
            f"form (method={method!r}, url={log_url(page.url)})")
        # terminal: no Enter / Tab+Enter / click-sweep method ran after the refusal
        assert ok is False and method == submit.GET_FORM_REFUSED, (ok, method)
        assert page.url.split("?", 1)[0] == LOGIN_URL and "?" not in page.url, log_url(page.url)


def test_negative_control_a_post_form_is_still_submitted_by_the_js_fallbacks():
    with _page(POST_FORM_HTML) as page:
        posted = []
        page.on("request", lambda r: posted.append(r.method) if r.url.startswith(LOGIN_URL) else None)
        ok, method = submit._submit_login(
            page, ["input[type=submit]"], ["#pwd-id"])
        assert ok is True and method in ("JS form.submit", "JS requestSubmit", "form.submit()", "form.requestSubmit()") or "POST" in posted, (
            ok, method, posted)
        assert "pwd-id=" not in page.url


def test_a_diagnostic_url_keeps_keys_and_scrubs_every_value():
    u = "https://h.test/login/?login-id=me%40x.y&pwd-id=hunter2&cf-turnstile-response=#top"
    out = log_url(u)
    assert out == "https://h.test/login/?login-id=<scrubbed>&pwd-id=<scrubbed>&cf-turnstile-response=<scrubbed>", out
    assert "hunter2" not in out and "me%40" not in out
    assert log_url("https://h.test/members/") == "https://h.test/members/"


def test_the_expected_url_reason_is_built_from_the_scrubbed_url():
    src = inspect.getsource(submit.do_login)
    assert "got {log_url(cur)}" in src, (
        "the 'Expected URL contains ..., got <url>' reason quotes the raw "
        "post-submit URL: a GET-submitted form puts the credentials in "
        "login_status, journalctl and the evidence filename")
    assert "got {cur}" not in src
