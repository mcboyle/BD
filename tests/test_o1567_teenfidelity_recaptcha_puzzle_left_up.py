"""fx-teenfidelity-recaptcha (O1567, live bd2 10.0.70.52 21:37Z, cloak 0.5.11): members.kellymadisonmedia.com/login
guards its form with an INVISIBLE reCAPTCHA -- the form's submit listener calls preventDefault() + grecaptcha.execute(),
and only the data-callback posts the form. On the VM the first click opened the image puzzle ("Select all images with
cars", results/bd2/shots/teenfidelity-fx-red-puzzle.png); 8 s later the sweep fired form.submit(), which bypasses the
listener, POSTed with no token and replaced the puzzle with "The g-recaptcha-response field is required." The manual
takeover then got a dead error page instead of the puzzle a human could have solved.

Now: an image challenge on screen ends the sweep -- no further submit fires, the puzzle is left up for a human.
Page and challenge iframe are inline fixtures served by page.route in a local headless chromium. No live site, no
credentials.
"""

from __future__ import annotations

import time

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-o1567-tf.test"
LOGIN_URL = ORIGIN + "/login"

# The live page's wiring, reduced: execute() either opens the puzzle (the challenge iframe becomes visible, as
# reCAPTCHA reveals its pre-rendered hidden bframe) or passes at once and runs the data-callback.
PAGE = """<!doctype html><html><body>
<form class="spaced" method="POST" action="%(origin)s/login">
<input name="username"><input name="password" type="password">
<div class="g-recaptcha" data-sitekey="fixture-key" data-size="invisible" data-callback="_submitForm"></div>
<textarea id="g-recaptcha-response" name="g-recaptcha-response" style="display:none"></textarea>
<button type="submit">Login</button></form>
<div id="bframe-host" style="visibility:hidden;position:absolute;top:-10000px;left:0;right:0;opacity:0">
<iframe title="recaptcha challenge expires in two minutes" width="400" height="580"
 src="%(origin)s/recaptcha/api2/bframe?k=fixture-key"></iframe></div>
<script>
var MODE = "%(mode)s";
var _captchaForm = document.querySelector(".g-recaptcha").closest("form");
window._submitForm = function(){ _captchaForm.submit(); };
window.grecaptcha = {execute: function(){
  if (MODE === "puzzle") {
    var h = document.getElementById("bframe-host");
    h.style.visibility = "visible"; h.style.top = "10px"; h.style.opacity = "1";
  } else {
    document.getElementById("g-recaptcha-response").value = "fixture-token";
    setTimeout(window._submitForm, 200);
  }
}};
_captchaForm.addEventListener("submit", function(e){ e.preventDefault(); grecaptcha.execute(); });
</script></body></html>"""

ERROR_PAGE = "<html><body><p>The g-recaptcha-response field is required.</p></body></html>"
MEMBERS = "<html><body><p>Welcome back, member</p></body></html>"


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(headless=True, timeout=20000, args=["--no-sandbox", "--disable-dev-shm-usage"])
    except PlaywrightError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


def _run(mode):
    """Drive the real _submit_login against the fixture. Returns (ok, info, posts, challenge_visible, secs)."""
    from bulk_downloader.login_impl import submit
    from playwright.sync_api import sync_playwright

    posts = []

    def handler(route, request):
        url = request.url
        if "/recaptcha/api2/bframe" in url:
            return route.fulfill(status=200, content_type="text/html", body="<p>Select all images with cars</p>")
        if request.method == "POST" and url.startswith(LOGIN_URL):
            body = request.post_data or ""
            posts.append(body)
            if "g-recaptcha-response=fixture-token" in body:
                return route.fulfill(status=200, content_type="text/html",
                                     body=f"<script>location.replace('{ORIGIN}/members')</script>")
            return route.fulfill(status=200, content_type="text/html", body=ERROR_PAGE)
        if url.startswith(ORIGIN + "/members"):
            return route.fulfill(status=200, content_type="text/html", body=MEMBERS)
        if url.startswith(LOGIN_URL):
            return route.fulfill(status=200, content_type="text/html", body=PAGE % {"origin": ORIGIN, "mode": mode})
        return route.abort()

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.route("**/*", handler)
            page.goto(LOGIN_URL)
            page.fill("input[name=username]", "fixture-user")
            page.fill("input[name=password]", "fixture-pass")
            t0 = time.time()
            ok, info = submit._submit_login(page, ["button[type=submit]"], ["input[name=password]"], declared_origins=())
            secs = time.time() - t0
            try:
                visible = page.frame_locator("iframe[src*='recaptcha/api2/bframe']").locator("body").is_visible()
                visible = visible and page.evaluate(
                    "getComputedStyle(document.getElementById('bframe-host')).visibility") == "visible"
            except Exception:
                visible = False
            return ok, info, posts, visible, secs
        finally:
            browser.close()


def test_image_puzzle_ends_the_sweep_and_stays_up():
    ok, info, posts, visible, secs = _run("puzzle")
    assert ok is False, (ok, info)
    assert posts == [], f"a tokenless submit went out and destroyed the puzzle: {len(posts)} POST(s); info={info!r}"
    assert visible, "the reCAPTCHA image challenge is no longer on screen for the human"
    assert "image challenge" in info and "human" in info, info
    assert secs < 20, f"sweep kept firing methods for {secs:.1f}s after the puzzle opened"


def test_invisible_recaptcha_that_passes_still_logs_in():
    """Negative control: the same wiring with no puzzle -- the callback posts the token and the sweep reports it."""
    ok, info, posts, visible, _ = _run("pass")
    assert ok is True, (ok, info)
    assert len(posts) == 1 and "g-recaptcha-response=fixture-token" in posts[0]
    assert not visible


def test_prerendered_hidden_bframe_is_not_a_challenge():
    """reCAPTCHA pre-renders the bframe hidden (visibility:hidden, top:-10000px, opacity:0) on every page."""
    from bulk_downloader.login_impl import submit
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.set_content(PAGE % {"origin": ORIGIN, "mode": "puzzle"})
            assert submit._captcha_challenge_visible(page) == ""
            page.evaluate("grecaptcha.execute()")
            assert submit._captcha_challenge_visible(page) == "reCAPTCHA"
        finally:
            browser.close()
