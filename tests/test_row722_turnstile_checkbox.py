"""Row 722 live (vip4k.com/en/login, 2026-09-15 06:18Z): ``form#login-form``
carries a Cloudflare Turnstile widget in CHECKBOX mode ("Verify you are
human", iframe from challenges.cloudflare.com).  The box was never clicked,
so ``cf-turnstile-response`` stayed empty; the journal then LIED --

  login: cf-turnstile-response populated after 30.0s

(waited == deadline means it never populated) and every submit method was
answered with "Wrong Captcha" before the manual takeover.

Operator decision: the Turnstile checkbox may be clicked (it is a browser
fingerprint check, not a puzzle).  hCaptcha / reCAPTCHA are NEVER clicked
because they can open image puzzles.

The page and the cross-origin challenge iframe are both served from inline
fixtures by ``page.route`` in a local headless chromium.  NO LIVE SITE IS
TOUCHED and no login is started anywhere; a missing browser SKIPS.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722-ts.test"
LOGIN_URL = ORIGIN + "/en/login"
CF_ORIGIN = "https://challenges.cloudflare.com"
CF_FRAME_URL = CF_ORIGIN + "/fixture/turnstile"

# The checkbox lives INSIDE the challenge iframe (a different origin); a
# click there postMessage()s the parent, which fills the hidden token input
# exactly as the real widget does through its callback.
FRAME_HTML = """<!doctype html><html><body style="margin:0">
<label style="display:flex;align-items:center;height:65px;width:300px">
  <input type="checkbox" id="cb"
         onclick="parent.postMessage({fixtureCaptcha:'%s',token:'tok-%s'},'*')">
  <span>Verify you are human</span>
</label></body></html>"""


def _page_html(token_name, iframe_src):
    return f"""<!doctype html><html><body>
<form id="login-form" method="post" action="/en/login">
  <input type="text" name="login"><input type="password" name="password">
  <input type="hidden" name="{token_name}" value="">
  <iframe src="{iframe_src}" style="width:300px;height:65px;border:0"></iframe>
  <button type="submit">Log in</button>
</form>
<script>
addEventListener('message', ev => {{
  const d = ev.data || {{}};
  if (!d.fixtureCaptcha) return;
  const i = document.querySelector('input[name="' + d.fixtureCaptcha + '"]');
  if (i) i.value = d.token;
}});
</script></body></html>"""


TURNSTILE_HTML = _page_html("cf-turnstile-response", CF_FRAME_URL)
HC_ORIGIN = "https://newassets.hcaptcha.com"
HC_FRAME_URL = HC_ORIGIN + "/fixture/hcaptcha"
HCAPTCHA_URL = ORIGIN + "/hc/login"
HCAPTCHA_HTML = _page_html("h-captcha-response", HC_FRAME_URL)
PLAIN_URL = ORIGIN + "/plain/login"
# vip4k live (10:3xZ): the Turnstile iframe sits in a CLOSED shadow root under
# div.g-recaptcha[data-sitekey]; no selector reaches it. The container itself
# takes the click (the widget's left-edge box) and the token appears.
SHADOW_URL = ORIGIN + "/shadow/login"
SHADOW_HTML = """<!doctype html><html><body>
<form id="login-form" method="post" action="/shadow/login">
  <input type="text" name="login"><input type="password" name="password">
  <input type="hidden" name="cf-turnstile-response" value="">
  <div class="g-recaptcha" data-sitekey="0xFIXTURE"
       style="width:300px;height:65px;display:block"></div>
  <button type="submit">Log in</button>
</form>
<script>
const host = document.querySelector('div.g-recaptcha');
const root = host.attachShadow({mode: 'closed'});
root.innerHTML = '<iframe src="__CF_FRAME_URL__" style="width:300px;height:65px;border:0"></iframe>';
// The click must reach the widget INSIDE the shadow root: it is the
// iframe's own checkbox that answers (as on the live widget), via postMessage.
addEventListener('message', ev => {
  const d = ev.data || {};
  if (d.fixtureCaptcha === 'cf-turnstile-response')
    document.querySelector('input[name="cf-turnstile-response"]').value = d.token;
});
</script></body></html>""".replace("__CF_FRAME_URL__", CF_FRAME_URL)
# The same closed-shadow-root widget with its iframe served through the
# zone's own /cdn-cgi/challenge-platform path: the frame-tree URL match and
# TURNSTILE_IFRAME_SEL both see nothing, so the CONTAINER selector list is
# the only anchor the click can reach (mutant M3 drops that list).
PROXIED_FRAME_URL = ORIGIN + "/cdn-cgi/challenge-platform/h/b/turnstile/fixture"
SHADOW_PROXIED_URL = ORIGIN + "/shadow-proxied/login"
SHADOW_PROXIED_HTML = SHADOW_HTML.replace(CF_FRAME_URL, PROXIED_FRAME_URL)
PLAIN_HTML = ("<!doctype html><html><body><form id='login-form'>"
              "<input name='login'><input type='password' name='password'>"
              "</form></body></html>")

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
    html = {
        LOGIN_URL: TURNSTILE_HTML,
        HCAPTCHA_URL: HCAPTCHA_HTML,
        PLAIN_URL: PLAIN_HTML,
        SHADOW_URL: SHADOW_HTML,
        SHADOW_PROXIED_URL: SHADOW_PROXIED_HTML,
        CF_FRAME_URL: FRAME_HTML % ("cf-turnstile-response", "turnstile"),
        PROXIED_FRAME_URL: FRAME_HTML % ("cf-turnstile-response", "turnstile"),
        HC_FRAME_URL: FRAME_HTML % ("h-captcha-response", "hcaptcha"),
    }.get(url)
    if html is None:
        route.fulfill(status=404, content_type="text/plain", body="not fixture")
    else:
        route.fulfill(status=200, content_type="text/html", body=html)


@contextmanager
def _login_page(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            for origin in (ORIGIN, CF_ORIGIN, HC_ORIGIN):
                page.route(origin + "/**", _serve)
            page.goto(url, wait_until="load")
            if url not in (SHADOW_URL, SHADOW_PROXIED_URL):  # the shadow fixtures' iframe is unreachable by design
                page.frame_locator("iframe").first.locator("#cb").wait_for(
                    state="visible", timeout=10000)
            else:
                page.locator("div.g-recaptcha").wait_for(state="visible", timeout=10000)
            yield page
        finally:
            browser.close()


def _token(page, name):
    return page.evaluate(
        "n => document.querySelector('input[name=\"' + n + '\"]').value", name)


def _checked(page):
    return page.frame_locator("iframe").first.locator("#cb").is_checked()


def _log_lines(capsys):
    return [l for l in capsys.readouterr().err.splitlines() if "login:" in l]


def test_precondition_the_token_stays_empty_unless_the_iframe_box_is_clicked():
    with _login_page(LOGIN_URL) as page:
        assert _token(page, "cf-turnstile-response") == ""
        page.frame_locator("iframe").first.locator("#cb").click()
        page.wait_for_function(
            "() => document.querySelector('input[name=\"cf-turnstile-response\"]').value")
        assert _token(page, "cf-turnstile-response") == "tok-turnstile"


def test_turnstile_checkbox_is_clicked_and_the_token_populates(capsys):
    """RED on base: nothing clicks the box, so the wait runs to the deadline
    and returns (tok, deadline) -- the 'populated after 30.0s' lie."""
    from bulk_downloader.login_impl import submit
    with _login_page(LOGIN_URL) as page:
        tok, waited = submit._wait_captcha_tokens(
            page, deadline=8, turnstile_click_after=0.5)
        assert tok == "cf-turnstile-response"
        assert waited is not None and waited < 8, (
            f"Turnstile token never populated: waited={waited!r} "
            f"(box checked={_checked(page)}) -- the checkbox was not clicked")
        assert _checked(page), "token populated but the box is not checked"
        assert _token(page, "cf-turnstile-response") == "tok-turnstile"
    lines = _log_lines(capsys)
    assert any("clicked Turnstile checkbox" in l for l in lines), lines


def test_hcaptcha_checkbox_is_never_clicked(capsys):
    """Negative control: an hCaptcha box (which could open a puzzle) is left
    alone, the token stays empty, and the result says NOT populated."""
    from bulk_downloader.login_impl import submit
    with _login_page(HCAPTCHA_URL) as page:
        tok, waited = submit._wait_captcha_tokens(
            page, deadline=2, turnstile_click_after=0.2)
        assert tok == "h-captcha-response"
        assert waited is None, f"hCaptcha wait reported populated: {waited!r}"
        assert not _checked(page), "hCaptcha checkbox was clicked"
        assert _token(page, "h-captcha-response") == ""
    lines = _log_lines(capsys)
    assert not any("clicked Turnstile" in l for l in lines), lines


def test_no_captcha_is_still_none_zero():
    from bulk_downloader.login_impl import submit
    with _plain_page() as page:
        assert submit._wait_captcha_tokens(page, deadline=1) == (None, 0)


@contextmanager
def _plain_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.route(ORIGIN + "/**", _serve)
            page.goto(PLAIN_URL, wait_until="load")
            yield page
        finally:
            browser.close()


class _FakePage:
    """Stand-in for the call-site log test: the token input exists and
    stays empty; input_value() is never non-empty."""
    def __init__(self, names):
        self._names = names

    def locator(self, sel):
        present = any(f"'{n}'" in sel for n in self._names)
        return _FakeLoc(present)

    def frame_locator(self, sel):
        raise RuntimeError("no iframe in fake page")


class _FakeLoc:
    def __init__(self, present):
        self._present = present
        self.first = self

    def count(self):
        return 1 if self._present else 0

    def input_value(self):
        return ""

    def bounding_box(self):
        return None

    def click(self, **kw):
        raise RuntimeError("nothing to click")


def test_wait_returns_none_when_the_turnstile_token_never_populates(monkeypatch):
    """The 'populated after 30.0s' lie: waited == deadline must not be
    reported as populated."""
    from bulk_downloader.login_impl import submit

    class _Clock:
        now = 1_000.0

        def time(self):
            self.now += 5.0
            return self.now

        def sleep(self, _s):
            return None

    monkeypatch.setattr(submit, "time", _Clock())
    tok, waited = submit._wait_captcha_tokens(
        _FakePage(["cf-turnstile-response"]), deadline=30)
    assert tok == "cf-turnstile-response"
    assert waited is None, f"never-populated token reported waited={waited!r}"


def test_call_site_logs_not_populated_distinctly():
    """The journal line for an unfilled token must be unmistakable."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    assert re.search(r"NOT populated within 30s", src), (
        "call site no longer logs the NOT-populated case distinctly")
    assert "waited is None" in src


def test_turnstile_in_a_closed_shadow_root_is_clicked_through_its_container(capsys):
    """vip4k live: no selector reaches the iframe; the container is clicked."""
    from bulk_downloader.login_impl import submit
    with _login_page(SHADOW_URL) as page:
        assert page.locator(submit.TURNSTILE_IFRAME_SEL).count() == 0, (
            "precondition: the iframe must be unreachable (closed shadow root)")
        tok, waited = submit._wait_captcha_tokens(
            page, deadline=8, turnstile_click_after=0.5)
        assert tok == "cf-turnstile-response"
        assert waited is not None and waited < 8, (
            f"Turnstile token never populated (waited={waited!r}): the widget "
            f"container was not clicked when the iframe is in a closed shadow root")
        assert _token(page, "cf-turnstile-response") == "tok-turnstile"
    lines = _log_lines(capsys)
    assert any("clicked Turnstile checkbox" in l for l in lines), lines


def test_the_container_selectors_carry_the_click_when_the_frame_is_proxied(capsys):
    """M3 catcher: the iframe is on the zone's own /cdn-cgi/ path, so neither
    the frame-tree shortcut nor TURNSTILE_IFRAME_SEL can name it; only the
    container selector list (div.g-recaptcha[data-sitekey] here) can."""
    from bulk_downloader.login_impl import submit
    with _login_page(SHADOW_PROXIED_URL) as page:
        assert page.locator(submit.TURNSTILE_IFRAME_SEL).count() == 0
        assert not any("challenges.cloudflare.com" in (fr.url or "") for fr in page.frames), (
            [fr.url for fr in page.frames])
        tok, waited = submit._wait_captcha_tokens(
            page, deadline=8, turnstile_click_after=0.5)
        assert tok == "cf-turnstile-response"
        assert waited is not None and waited < 8, (
            f"Turnstile token never populated (waited={waited!r}): the container "
            f"selector list was not used to deliver the click")
        assert _token(page, "cf-turnstile-response") == "tok-turnstile"
    lines = _log_lines(capsys)
    assert any("clicked Turnstile checkbox" in l for l in lines), lines

