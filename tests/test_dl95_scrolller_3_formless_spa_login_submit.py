"""dl95-scrolller-3 (scrolller, test2 .82 build 2f161614, 04:38Z, harness-work/DOT95-LANE/live-dl95-scrolller-2/):
the login is a SPA modal with NO <form> -- identifier-input, password-input, a "Forgot Password" type=button and
<button class="...acceptButton">Log in!</button> (DOM read on .82 by opening the modal, nothing submitted). The page's
only <form> is the header search box. Live:

  login submit: click submit selector -> skip (could not click submit button; tried 59 selectors)
  login submit: JS requestSubmit -> form.requestSubmit(); waiting...
  login: ... post-submit page still anonymous  (final_url https://scrolller.com/search/collections)

Two defects: no selector reaches "Log in!" (tier 5 is exact text), and _LOGIN_FORM_JS fell back to the page's FIRST
form when the matched password field sat in no form, so requestSubmit() submitted the SEARCH box and the navigation to
/search counted as the login submit.

Local headless chromium, fixture served by page.route; no live site, no credentials sent anywhere.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-scrolller3.test"
HOME_URL = ORIGIN + "/"

# Shape of the .82 DOM read (class names shortened). The navbar Login and the
# "LOG IN" tab both precede the password field; the header search is the only form;
# the modal overlays the page, as live (the navbar is behind it).
SPA_HTML = """<!doctype html><html><body>
<header>
  <form class="inputWrapper" action="/search/collections"><input placeholder="Search" type="text"></form>
  <button type="button" class="accountMenu" id="nav">Login</button>
</header>
<div id="modal" style="position:fixed;inset:0;z-index:10;background:rgba(0,0,0,.7)">
  <div class="tabContainer"><button class="choice">REGISTER</button>
    <button class="choice choiceActive" id="tab">LOG IN</button></div>
  <div class="inputGroup">
    <label for="identifier-input">Username / Email</label>
    <input id="identifier-input" placeholder="Username / Email" type="text" value="fixture-user">
    <label for="password-input">Password</label>
    <div><input id="password-input" placeholder="password" type="password" value="fixture-pass"><div>Show</div></div>
    <button class="action" type="button" id="forgot">Forgot Password</button>
  </div>
  <button class="acceptButton" id="accept">Log in!</button>
</div>
<script>
// React-style listeners (no onclick attributes), as on the live SPA.
const bump = (k) => () => { window[k] = (window[k] || 0) + 1; };
document.getElementById('nav').addEventListener('click', bump('__navLogin'));
document.getElementById('tab').addEventListener('click', bump('__tab'));
document.getElementById('forgot').addEventListener('click', bump('__forgot'));
document.getElementById('accept').addEventListener('click', () => {
  bump('__login')();
  document.getElementById('modal').remove();
  document.querySelector('header').insertAdjacentHTML('beforeend', '<a href="/logout">Logout</a>');
});
</script>
</body></html>"""
SEARCH_HTML = "<!doctype html><html><body><h1>search results</h1></body></html>"


def _serve(route, request):
    if request.url.split("?", 1)[0].startswith(ORIGIN + "/search"):
        route.fulfill(status=200, content_type="text/html", body=SEARCH_HTML)
    else:
        route.fulfill(status=200, content_type="text/html", body=SPA_HTML)


@contextmanager
def _spa_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=20000,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:
            pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE (T5): {e}")
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route(ORIGIN + "/**", _serve)
            page.goto(HOME_URL, wait_until="load")
            yield page
        finally:
            browser.close()


@pytest.fixture
def fast_clock(monkeypatch):
    from bulk_downloader.login_impl import submit

    class _Clock:
        now = 1_000.0

        def time(self):
            self.now += 5.0
            return self.now

        def sleep(self, _s):
            return None

    monkeypatch.setattr(submit, "time", _Clock())


def _counts(page):
    return page.evaluate("() => ({login: window.__login||0, tab: window.__tab||0, "
                         "nav: window.__navLogin||0, forgot: window.__forgot||0})")


def test_the_formless_login_is_submitted_by_its_own_control_not_the_search_form():
    """THE ROW, with the shipped selector lists (the live sweep's 59 + fallbacks). Real clock:
    the navigation the defect makes must be seen inside the sweep's own 8s window."""
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        ok, info = submit._submit_login(page, list(submit.SUBMIT_FALLBACKS),
                                        list(submit.PASS_FIELD_FALLBACKS))
        counts = _counts(page)
        assert counts["login"] == 1, (
            f"DL95-SCROLLLER-3: 'Log in!' was not clicked (counts={counts!r}, sweep={ok!r} {info!r}, "
            f"url={page.url})")
        assert not page.url.startswith(ORIGIN + "/search"), (
            f"DL95-SCROLLLER-3: the sweep submitted the header SEARCH form as the login ({page.url})")
        assert counts["tab"] == 0 and counts["nav"] == 0 and counts["forgot"] == 0, counts
        assert page.url == HOME_URL
        assert ok is False and "SPA login" in info, (ok, info)


def test_js_form_fallbacks_never_resolve_a_formless_password_field_to_another_form():
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        form = page.evaluate(
            "(s) => { const f = (" + submit._LOGIN_FORM_JS + ")(s); return f ? f.className : null; }",
            ["input[type=password]"])
        assert form is None, f"DL95-SCROLLLER-3: formless password resolved to form {form!r}"


def test_negative_control_no_password_field_at_all_keeps_the_first_form_fallback():
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        form = page.evaluate(
            "(s) => { const f = (" + submit._LOGIN_FORM_JS + ")(s); return f ? f.className : null; }",
            ["input[type=no-such-type]"])
        assert form == "inputWrapper", form


def test_negative_control_the_scoped_submit_ignores_a_form_login_and_preceding_controls():
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        page.evaluate("() => { const f = document.createElement('form');"
                      " f.method = 'post'; f.appendChild(document.getElementById('modal'));"
                      " document.body.appendChild(f); }")
        assert page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"]) is None
    with _spa_page() as page:
        page.evaluate("() => document.querySelector('.acceptButton').remove()")
        got = page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"])
        assert got is None, f"a control BEFORE the password field was tagged: {got!r}"


# Lens R1 (bd-cx-worker-1, VERDICT-correctness on tree 87ca480e): with the login's own control disabled or absent,
# the ancestor climb escaped the login and tagged a FOLLOWING search form's "Go". Ownership, not proximity.
_TRAILING_GO = ("() => document.body.insertAdjacentHTML('beforeend',"
                " '<form action=\"/search/collections\"><input type=search><button id=go>Go</button></form>"
                "<input id=note type=text><button id=cont type=button>Continue</button>')")


@pytest.mark.parametrize("login_control", ["disabled", "absent"])
def test_r1_a_following_unrelated_control_is_never_the_login_submit(login_control):
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        page.evaluate(_TRAILING_GO)
        page.evaluate("() => document.getElementById('modal').style.position = 'static'")
        if login_control == "disabled":
            page.evaluate("() => { document.getElementById('accept').disabled = true; }")
        else:
            page.evaluate("() => document.getElementById('accept').remove()")
        got = page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"])
        assert got is None, f"DL95-SCROLLLER-3-R1: a control outside the login was tagged: {got!r}"


def test_r1_a_formless_continue_after_another_text_field_is_not_the_login_submit():
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        page.evaluate("() => { document.getElementById('accept').remove();"
                      " document.getElementById('modal').insertAdjacentHTML('beforeend',"
                      " '<input id=promo type=text><button id=cont type=button>Continue</button>'); }")
        got = page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"])
        assert got is None, f"DL95-SCROLLLER-3-R1: tagged {got!r} past another text field"


def test_r1_control_the_enabled_login_control_is_still_found_with_trailing_widgets():
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        page.evaluate(_TRAILING_GO)
        assert page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"]) == "Log in!"


# Lens R1 G2 (bd-cx-worker-1, tree 5ae14645): document order is not ownership -- with Log in! absent, a SIBLING
# dialog's "Continue" (no form, no text field) was tagged and clicked. The search is bounded to the login container.
@pytest.mark.parametrize("where", ["sibling", "nested"])
def test_r1g2_another_dialogs_control_is_never_the_login_submit(where):
    from bulk_downloader.login_impl import submit
    other = ("'<section role=dialog aria-label=\"Cookie preferences\"><p>Cookies</p>"
             "<button id=ck type=button>Continue</button></section>'")
    target = "document.body" if where == "sibling" else "document.getElementById('modal')"
    with _spa_page() as page:
        page.evaluate("() => { document.getElementById('accept').remove();"
                      f" {target}.insertAdjacentHTML('beforeend', {other}); }}")
        got = page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"])
        assert got is None, f"DL95-SCROLLLER-3-R1G2: another dialog's control was tagged: {got!r}"


def test_r1g2_no_identifier_field_means_no_ownership():
    from bulk_downloader.login_impl import submit
    with _spa_page() as page:
        page.evaluate("() => document.getElementById('identifier-input').remove()")
        assert page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"]) is None
