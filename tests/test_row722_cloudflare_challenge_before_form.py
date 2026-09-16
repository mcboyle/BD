"""Row 722 live (adulttime, 2026-09-15 06:51Z): every login URL (freetour /
www / members x /login, /en/login) answered 307->403 with a Cloudflare
managed challenge page -- title "Just a moment...", ``#challenge-running``,
a Turnstile CHECKBOX "Verify you are human" inside a
challenges.cloudflare.com iframe.  The walker never saw a form:

  login: Couldn't find username field ... tried 25 selectors

-> manual takeover.  G11 clicks the box only inside the FORM's captcha
wait (after the username field was found) -- too late for a challenge page.

Operator decision (07:2xZ): the Turnstile checkbox may be clicked, never a
puzzle.  ``clear_cloudflare_challenge`` runs in ``do_login`` BEFORE the
page-gate walk and the username search.

Both origins are served by ``page.route`` in a local headless chromium.
NO LIVE SITE IS TOUCHED and no login is started anywhere; a missing
browser SKIPS.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722-cf.test"
LOGIN_URL = ORIGIN + "/login"
CLEARED_URL = ORIGIN + "/login?cleared=1"
STUCK_URL = ORIGIN + "/stuck/login"
HC_URL = ORIGIN + "/hc/login"
PLAIN_URL = ORIGIN + "/plain/login"
CF_ORIGIN = "https://challenges.cloudflare.com"
CF_FRAME_URL = CF_ORIGIN + "/fixture/turnstile"
HC_ORIGIN = "https://newassets.hcaptcha.com"
HC_FRAME_URL = HC_ORIGIN + "/fixture/hcaptcha"

FRAME_HTML = """<!doctype html><html><body style="margin:0">
<label style="display:flex;align-items:center;height:65px;width:300px">
  <input type="checkbox" id="cb"
         onclick="parent.postMessage({fixtureChallenge:'%s'},'*')">
  <span>Verify you are human</span>
</label></body></html>"""


def _challenge_html(iframe_src, on_verified_js):
    return f"""<!doctype html><html><head><title>Just a moment...</title></head>
<body>
<h1>www.adulttime.test</h1>
<div id="challenge-running">Verifying you are human. This may take a few seconds.</div>
<form id="challenge-form" action="/login?__cf_chl_f_tk=x" method="POST">
  <div class="cf-turnstile">
    <iframe src="{iframe_src}" style="width:300px;height:65px;border:0"></iframe>
  </div>
</form>
<script>
addEventListener('message', ev => {{
  const d = ev.data || {{}};
  if (!d.fixtureChallenge) return;
  window.__challengeClicked = d.fixtureChallenge;
  {on_verified_js}
}});
</script></body></html>"""


CHALLENGE_HTML = _challenge_html(
    CF_FRAME_URL, "location.replace('/login?cleared=1');")
STUCK_HTML = _challenge_html(CF_FRAME_URL, "/* never navigates */")
HCAPTCHA_HTML = _challenge_html(HC_FRAME_URL, "location.replace('/login?cleared=1');")
# stepsiblingscaught live (11:3xZ): /turnstile/challenge?r=/login is a
# site-drawn challenge with ONLY an "I am human" button (no widget at all).
HUMAN_URL = ORIGIN + "/turnstile/challenge"
HUMAN_HTML = """<!doctype html><html><head><title>Security Check</title></head><body>
<div id="turnstileCheckboxWrapper"><p>Security check</p>
<div class="turnstile-checkbox-wrapper" role="button" aria-label="Verify you are human" id="turnstileCheckboxWrapper2"
     onclick="location.replace('/login?cleared=1')"><span class="turnstile-checkmark">&#10003;</span> <span class="turnstile-label">I am human</span></div>
<button type="button" id="other">Contact support</button>
</div></body></html>"""
# The same site-drawn check with ONLY the title signal: no turnstile class,
# no aria-label, not on /turnstile/challenge. Makes the title/path rule in
# _is_cloudflare_challenge_page load-bearing on its own (mutant M10).
TITLE_ONLY_URL = ORIGIN + "/account/verify"
TITLE_ONLY_HTML = """<!doctype html><html><head><title>Security Check</title></head><body>
<p>Security check</p>
<button type="button" id="human" onclick="location.replace('/login?cleared=1')">I am human</button>
</body></html>"""
# login.vixen.com/i/<brand>/login/challenge: an explicit managed-challenge
# widget <div id="cf-chl-widget-xxxx"> whose iframe sits in a CLOSED shadow root.
VIXEN_URL = ORIGIN + "/i/brand/login/challenge"
VIXEN_HTML = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<form id="challenge-form" method="POST" action="/i/brand/login/challenge">
  <input type="hidden" name="cf-turnstile-response" id="cf-chl-widget-udayw_response">
  <div id="cf-chl-widget-udayw" style="width:300px;height:65px;display:block"></div>
</form>
<script>
const host = document.getElementById('cf-chl-widget-udayw');
const root = host.attachShadow({mode: 'closed'});
root.innerHTML = '<iframe src="__CF_FRAME_URL__" style="width:300px;height:65px;border:0"></iframe>';
addEventListener('message', ev => {
  const d = ev.data || {};
  if (d.fixtureChallenge === 'turnstile') location.replace('/login?cleared=1');
});
</script></body></html>""".replace("__CF_FRAME_URL__", CF_FRAME_URL)
# login.vixen.com/i/vixen/login/challenge (13:2xZ): the widget host has a
# RANDOM id and no class; only its position beside the token field says
# what it is. The iframe sits in a closed shadow root inside it.
VIXEN2_URL = ORIGIN + "/i/vixen/login/challenge"
# The widget iframe served through the zone's own /cdn-cgi/challenge-platform
# path instead of challenges.cloudflare.com: neither the frame-tree URL match
# nor TURNSTILE_IFRAME_SEL can name it, so ONLY the DOM host (explicit
# cf-chl-widget id / tagged random-id host) can carry the click. These two
# variants make those fallbacks load-bearing (mutants M5 / M7).
PROXIED_FRAME_URL = ORIGIN + "/cdn-cgi/challenge-platform/h/b/turnstile/fixture"
VIXEN_PROXIED_URL = ORIGIN + "/i/brand-proxied/login/challenge"
VIXEN2_PROXIED_URL = ORIGIN + "/i/vixen-proxied/login/challenge"
# Managed challenge BEFORE the widget has rendered its token input (the
# cf-turnstile-response field is injected by the widget script, not served in
# the HTML): no token field means the random-id tagger returns
# 'no-token-field', so the explicit cf-chl-widget id is the ONLY anchor.
VIXEN_PROXIED_NOTOKEN_URL = ORIGIN + "/i/brand-proxied-notoken/login/challenge"
VIXEN_PROXIED_NOTOKEN_HTML = VIXEN_HTML.replace(CF_FRAME_URL, PROXIED_FRAME_URL).replace(
    '  <input type="hidden" name="cf-turnstile-response" id="cf-chl-widget-udayw_response">\n', "")
assert 'cf-turnstile-response' not in VIXEN_PROXIED_NOTOKEN_HTML
VIXEN2_HTML = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<div class="main-wrapper LDHu0" role="main"><div class="main-content">
<h2 id="jddkS1">login.vixen.test</h2>
<form id="challenge-form" method="POST" action="/i/vixen/login/challenge">
  <div id="lVJB5" style="display:grid;width:300px;height:65px"><div><div>
    <input type="hidden" name="cf-turnstile-response" id="cf-chl-widget-3rv0l_response">
  </div></div></div>
</form>
<p id="QeINV1">Verify you are human by completing the action below.</p>
</div></div>
<script>
const host = document.getElementById('lVJB5');
const root = host.attachShadow({mode: 'closed'});
root.innerHTML = '<iframe src="__CF_FRAME_URL__" style="width:300px;height:65px;border:0"></iframe>';
addEventListener('message', ev => {
  const d = ev.data || {};
  if (d.fixtureChallenge === 'turnstile') location.replace('/login?cleared=1');
});
</script></body></html>""".replace("__CF_FRAME_URL__", CF_FRAME_URL)
FORM_HTML = ("<!doctype html><html><head><title>Log in</title></head><body>"
             "<form id='login-form' method='post' action='/login'>"
             "<input type='text' name='login'>"
             "<input type='password' name='password'>"
             "<button type='submit'>Log in</button></form></body></html>")

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
    url = request.url
    if url == CLEARED_URL:
        html = FORM_HTML
    else:
        html = {
            LOGIN_URL: CHALLENGE_HTML,
            STUCK_URL: STUCK_HTML,
            HC_URL: HCAPTCHA_HTML,
            PLAIN_URL: FORM_HTML,
            HUMAN_URL: HUMAN_HTML,
            TITLE_ONLY_URL: TITLE_ONLY_HTML,
            VIXEN_URL: VIXEN_HTML,
            VIXEN2_URL: VIXEN2_HTML,
            VIXEN_PROXIED_URL: VIXEN_HTML.replace(CF_FRAME_URL, PROXIED_FRAME_URL),
            VIXEN2_PROXIED_URL: VIXEN2_HTML.replace(CF_FRAME_URL, PROXIED_FRAME_URL),
            VIXEN_PROXIED_NOTOKEN_URL: VIXEN_PROXIED_NOTOKEN_HTML,
            PROXIED_FRAME_URL: FRAME_HTML % "turnstile",
            CF_FRAME_URL: FRAME_HTML % "turnstile",
            HC_FRAME_URL: FRAME_HTML % "hcaptcha",
        }.get(url.split("?", 1)[0])
    if html is None:
        route.fulfill(status=404, content_type="text/plain", body="not fixture")
    else:
        route.fulfill(status=200, content_type="text/html", body=html)


@contextmanager
def _page(url, expect_iframe=True):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            for origin in (ORIGIN, CF_ORIGIN, HC_ORIGIN):
                page.route(origin + "/**", _serve)
            page.goto(url, wait_until="load")
            if expect_iframe:
                page.frame_locator("iframe").first.locator("#cb").wait_for(
                    state="visible", timeout=10000)
            yield page
        finally:
            browser.close()


def _log_lines(capsys):
    return [l for l in capsys.readouterr().err.splitlines() if "login:" in l]


def test_precondition_the_challenge_page_hides_the_form_until_the_box_is_clicked():
    with _page(LOGIN_URL) as page:
        assert "Just a moment" in page.title()
        assert page.locator("input[type=password]").count() == 0
        page.frame_locator("iframe").first.locator("#cb").click()
        page.wait_for_url(CLEARED_URL, timeout=10000)
        assert page.locator("input[type=password]").count() == 1


def test_the_row_challenge_is_cleared_and_the_form_is_reached(capsys):
    """RED on base: nothing clicks the box before the username search."""
    from bulk_downloader.login_impl import submit
    with _page(LOGIN_URL) as page:
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"challenge not cleared: url={page.url} title={page.title()!r} "
            "-- the Turnstile checkbox on the challenge page was not clicked")
        assert page.url == CLEARED_URL
        assert page.locator("input[type=password]:visible").count() == 1, (
            "challenge reported cleared but the login form is not visible")
    lines = _log_lines(capsys)
    assert any("cloudflare challenge page — clicked Turnstile checkbox" in l
               for l in lines), lines
    assert any(l.strip() == f"login: challenge cleared -> {CLEARED_URL}"
               for l in lines), lines


def test_do_login_clears_the_challenge_before_the_gate_walk_and_username_search():
    """Source-order proof (as tests/test_row722_pre_submit_evidence.py):
    the pre-form call sits after the goto and before both the page-gate
    walk and the username fill."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    m_call = re.search(r"clear_cloudflare_challenge\(page\)", src)
    assert m_call, "do_login no longer clears a Cloudflare challenge page"
    m_goto = re.search(r"page\.goto\(url,", src)
    m_gates = re.search(r"_pre_form_gate_actions = _dismiss_page_gates\(", src)
    m_user = re.search(r'_try_fill\(page,uf_candidates,username,"username"\)', src)
    assert m_goto and m_gates and m_user
    assert m_goto.start() < m_call.start() < m_gates.start() < m_user.start(), (
        "clear_cloudflare_challenge must run after goto and BEFORE the "
        "page-gate walk and the username search")


def test_negative_a_plain_form_page_is_not_a_challenge_nothing_clicked(capsys):
    from bulk_downloader.login_impl import submit
    with _page(PLAIN_URL, expect_iframe=False) as page:
        assert submit.clear_cloudflare_challenge(page, wait=2) is False
        assert page.url == PLAIN_URL
    assert not any("challenge" in l for l in _log_lines(capsys))


def test_negative_a_challenge_that_never_clears_logs_not_cleared_and_returns(capsys):
    """The click lands (fixture records it) but no navigation follows: the
    helper must give up within its budget, log NOT cleared, and leave the
    username search to report its own failure -- no infinite wait."""
    import time
    from bulk_downloader.login_impl import submit
    with _page(STUCK_URL) as page:
        t0 = time.time()
        assert submit.clear_cloudflare_challenge(page, wait=1.5) is False
        assert time.time() - t0 < 12, "helper did not give up on a stuck challenge"
        assert page.evaluate("window.__challengeClicked") == "turnstile"
        assert page.url == STUCK_URL
        # The username search then fails on its own terms (no form here).
        ok, info = submit._try_fill(page, ["input[name='login']"], "x", "username")
        assert not ok
    lines = _log_lines(capsys)
    assert any("challenge NOT cleared within 1s (still 'Just a moment')" in l
               for l in lines), lines
    assert sum("clicked Turnstile checkbox" in l for l in lines) <= 2, lines


def test_negative_an_hcaptcha_challenge_page_is_never_clicked(capsys):
    from bulk_downloader.login_impl import submit
    with _page(HC_URL) as page:
        assert submit.clear_cloudflare_challenge(page, wait=1) is False
        assert page.evaluate("window.__challengeClicked || null") is None, (
            "hCaptcha checkbox was clicked")
        assert not page.frame_locator("iframe").first.locator("#cb").is_checked()
        assert page.url == HC_URL
    lines = _log_lines(capsys)
    assert not any("clicked Turnstile checkbox" in l for l in lines), lines


def test_a_post_submit_challenge_page_is_cleared_before_the_success_url_check():
    """blacked live (11:1xZ): a good submit is answered with a Turnstile
    challenge page before the members redirect. The challenge is cleared
    AFTER the submit and BEFORE `success` is compared against the URL."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    sweep = src.find("ok,method=_submit_login(page,sb_candidates,pf_candidates)")
    post = src.find("login: post-submit cloudflare challenge page")
    clear = src.find("if clear_cloudflare_challenge(page):", post)
    check = src.find("if success and not success_url_reached(success, cur, url):", post)
    assert sweep != -1 and check != -1, "anchors moved; re-anchor this test"
    assert post != -1, "no post-submit challenge step: a challenge answered to the submit is judged as 'Expected URL contains ... got .../login/challenge'"
    assert sweep < post < clear < check, (sweep, post, clear, check)
    # The branch must be LIVE, not merely present in source order: the log
    # line has to sit directly under the real challenge-page predicate. An
    # `if False:` (or any other guard) over the same body is dead code that
    # would still satisfy the ordering assertions above.
    guard = src.rfind("if ", 0, post)
    guard_line = src[guard:src.find("\n", guard)]
    assert guard_line.strip() == "if _is_cloudflare_challenge_page(page):", (
        "the post-submit challenge step is not guarded by the live "
        f"challenge-page predicate; found {guard_line.strip()!r}")


def test_a_site_drawn_i_am_human_button_clears_the_challenge(capsys):
    """stepsiblingscaught live: no widget, only an 'I am human' button."""
    from bulk_downloader.login_impl import submit
    with _page(HUMAN_URL, expect_iframe=False) as page:
        assert page.locator(submit.TURNSTILE_IFRAME_SEL).count() == 0
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"challenge with only an 'I am human' button not cleared: url={page.url}")
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("clicked 'I am human' button" in l for l in lines), lines
    assert not any("Contact support" in l for l in lines)


def test_negative_control_an_unrelated_button_is_never_the_human_button():
    from bulk_downloader.login_impl import submit
    for label in ("Contact support", "Continue", "Verify email", "I am not a robot yet"):
        assert submit.HUMAN_BUTTON_RE.match(label) is None, label
    for label in ("I am human", "I'm human", "Verify you are human", " VERIFY "):
        assert submit.HUMAN_BUTTON_RE.match(label) is not None, label
    # the live glyph-decorated text matches only after stripping decoration
    import re as _re
    assert submit.HUMAN_BUTTON_RE.match("\u2713 I am human") is None
    assert submit.HUMAN_BUTTON_RE.match(_re.sub(r"[^A-Za-z' ]+", " ", "\u2713 I am human").strip())


def test_an_explicit_managed_challenge_widget_in_a_closed_shadow_root_is_clicked(capsys):
    """login.vixen.com/.../login/challenge: <div id=cf-chl-widget-...> host."""
    from bulk_downloader.login_impl import submit
    with _page(VIXEN_URL, expect_iframe=False) as page:
        page.locator("div[id^=cf-chl-widget-]").wait_for(state="visible", timeout=10000)
        assert page.locator(submit.TURNSTILE_IFRAME_SEL).count() == 0, "precondition: iframe unreachable"
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"managed-challenge widget container not clicked: url={page.url} "
            "(the 'no Turnstile checkbox to click' path was taken)")
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("clicked Turnstile checkbox" in l for l in lines), lines


def test_upsell_boxes_are_unchecked_before_the_post_login_interstitial_walk():
    """bangbros live (11:3xZ): /store carries a PRE-CHECKED paid bundle box
    beside CONTINUE TO MEMBERS AREA; the uncheck runs before that walk."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    walk = src.find("_post_gate_actions = _dismiss_interstitials(page, _wall)")
    unch = src.find("_post_upsell=_uncheck_upsell_boxes(page)")
    assert walk != -1, "post-login walk anchor moved; re-anchor"
    assert unch != -1, "no upsell uncheck before the post-login interstitial walk (purchase risk)"
    assert unch < walk
    assert src.find("upsell", unch) < walk


def test_a_widget_host_with_a_random_id_is_found_beside_the_token_field(capsys):
    """login.vixen.com live (attempt 2): no class, no cf-chl-widget id."""
    from bulk_downloader.login_impl import submit
    with _page(VIXEN2_URL, expect_iframe=False) as page:
        page.locator("#lVJB5").wait_for(state="visible", timeout=10000)
        assert page.locator(submit.TURNSTILE_IFRAME_SEL).count() == 0
        for sel in submit.TURNSTILE_CONTAINER_SELS:
            assert page.locator(sel).count() == 0, sel
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"widget host with a random id not clicked: url={page.url} "
            "('no Turnstile checkbox to click' path taken)")
        assert page.url == CLEARED_URL
    assert any("clicked Turnstile checkbox" in l for l in _log_lines(capsys))


def _assert_no_frame_url_path(page, submit):
    """Precondition for the proxied variants: the frame-tree shortcut and the
    iframe selector both see nothing, so the DOM host must carry the click."""
    assert not any("challenges.cloudflare.com" in (fr.url or "") for fr in page.frames), (
        [fr.url for fr in page.frames])
    assert page.locator(submit.TURNSTILE_IFRAME_SEL).count() == 0


def test_the_explicit_widget_container_is_the_click_anchor_when_the_frame_is_proxied(capsys):
    """M5 catcher: with the iframe on the zone's own /cdn-cgi/ path, the
    <div id=cf-chl-widget-...> container selector is the only anchor."""
    from bulk_downloader.login_impl import submit
    with _page(VIXEN_PROXIED_URL, expect_iframe=False) as page:
        page.locator("div[id^=cf-chl-widget-]").wait_for(state="visible", timeout=10000)
        _assert_no_frame_url_path(page, submit)
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"cf-chl-widget container not used as the click anchor: url={page.url}")
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("clicked Turnstile checkbox" in l for l in lines), lines


def test_the_explicit_widget_container_alone_carries_the_click_without_a_token_field(capsys):
    """M5 catcher: proxied frame AND no cf-turnstile-response field yet, so
    neither the frame URL nor the random-id tagger can name the host; only
    the div[id^=cf-chl-widget-] container selector reaches the checkbox."""
    from bulk_downloader.login_impl import submit
    with _page(VIXEN_PROXIED_NOTOKEN_URL, expect_iframe=False) as page:
        page.locator("div[id^=cf-chl-widget-]").wait_for(state="visible", timeout=10000)
        assert page.locator('input[name="cf-turnstile-response"]').count() == 0
        _assert_no_frame_url_path(page, submit)
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"cf-chl-widget container not used as the click anchor: url={page.url}")
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("turnstile host search: no-token-field" in l for l in lines), lines
    assert any("clicked Turnstile checkbox" in l for l in lines), lines


def test_the_tagged_random_id_host_is_the_click_anchor_when_the_frame_is_proxied(capsys):
    """M7 catcher: no class, no cf-chl-widget id, iframe proxied -- the host
    tagged beside the token field is the only anchor."""
    from bulk_downloader.login_impl import submit
    with _page(VIXEN2_PROXIED_URL, expect_iframe=False) as page:
        page.locator("#lVJB5").wait_for(state="visible", timeout=10000)
        _assert_no_frame_url_path(page, submit)
        for sel in submit.TURNSTILE_CONTAINER_SELS:
            assert page.locator(sel).count() == 0, sel
        cleared = submit.clear_cloudflare_challenge(page, wait=10)
        assert cleared is True, (
            f"tagged random-id host not used as the click anchor: url={page.url}")
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("clicked Turnstile checkbox" in l for l in lines), lines


def test_a_challenge_that_auto_verifies_is_waited_for_not_abandoned(capsys):
    """blacked live (13:1xZ): widget already 'Verifying...' -- nothing to
    click, but the page navigates by itself within seconds."""
    from bulk_downloader.login_impl import submit
    with _page(STUCK_URL) as page:
        # No clickable widget from the product's point of view: hide the
        # iframe/container, then let the page navigate on its own.
        page.evaluate("() => { document.querySelector('.cf-turnstile').remove(); "
                      "setTimeout(() => location.replace('/login?cleared=1'), 1500); }")
        cleared = submit.clear_cloudflare_challenge(page, wait=8)
        assert cleared is True, "auto-verifying challenge abandoned instead of waited for"
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("waiting for auto-verification" in l for l in lines), lines
    assert any("challenge cleared ->" in l for l in lines), lines


def test_a_checkbox_that_appears_during_the_auto_verify_wait_is_clicked(capsys):
    """blacked live (14:3xZ): Cloudflare downgrades from 'Verifying...' to an
    interactive checkbox mid-wait; the waiter must re-look and click it."""
    from bulk_downloader.login_impl import submit
    with _page(STUCK_URL) as page:
        # Start with NO clickable widget, then let the widget appear 1.5s later.
        page.evaluate("""() => {
          const box = document.querySelector('.cf-turnstile');
          const html = box.outerHTML; box.remove();
          setTimeout(() => { document.querySelector('#challenge-form').insertAdjacentHTML('beforeend', html); }, 1500);
          addEventListener('message', ev => { if ((ev.data||{}).fixtureChallenge) location.replace('/login?cleared=1'); });
        }""")
        cleared = submit.clear_cloudflare_challenge(page, wait=6)
        assert cleared is True, "a checkbox that appeared during the wait was never clicked"
        assert page.url == CLEARED_URL
    lines = _log_lines(capsys)
    assert any("checkbox appeared during the wait" in l for l in lines), lines


def test_precondition_the_site_drawn_challenge_carries_no_cloudflare_marker():
    """stepsiblingscaught: none of CF_CHALLENGE_SELS, no 'Just a moment'."""
    from bulk_downloader.login_impl import submit
    with _page(HUMAN_URL, expect_iframe=False) as page:
        assert "just a moment" not in page.title().lower()
        for sel in submit.CF_CHALLENGE_SELS:
            assert page.locator(sel).count() == 0, sel
        assert submit._is_cloudflare_challenge_page(page) is True, (
            "the site-drawn 'Security Check' page is not recognised as a challenge")


def test_the_security_check_title_alone_marks_a_site_drawn_challenge(capsys):
    """M10 catcher: none of CF_CHALLENGE_SELS, none of SITE_CHALLENGE_SELS,
    not on /turnstile/challenge -- only the 'Security Check' title says what
    the page is, and that alone must mark it (then the button clears it)."""
    from bulk_downloader.login_impl import submit
    with _page(TITLE_ONLY_URL, expect_iframe=False) as page:
        assert "security check" in page.title().lower()
        assert "/turnstile/challenge" not in page.url
        for sel in (*submit.CF_CHALLENGE_SELS, *submit.SITE_CHALLENGE_SELS):
            assert page.locator(sel).count() == 0, sel
        assert submit._is_cloudflare_challenge_page(page) is True, (
            "a 'Security Check' page with no widget markers is not recognised "
            "as a challenge: the title/path rule is dead")
        assert submit.clear_cloudflare_challenge(page, wait=10) is True
        assert page.url == CLEARED_URL
    assert any("clicked 'I am human' button" in l for l in _log_lines(capsys))


def test_a_cleared_challenge_that_lands_on_a_dead_page_re_enters_the_site():
    """vixen live (15:4xZ): after the click the page shows "Not found" at the
    challenge URL; the run re-enters the declared success page before judging."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    post = src.find("login: post-submit cloudflare challenge page")
    reenter = src.find("challenge cleared onto a dead page", post)
    judge = src.find("anonymous,anon_why=anonymous_surface_check(page)", post)
    assert post != -1 and judge != -1, "anchors moved; re-anchor"
    assert reenter != -1, "a cleared challenge that lands on a dead page is judged as-is (vixen 'Not found')"
    assert post < reenter < judge
    assert 'if "/challenge" in cur or "not found" in _t:' in src


def test_a_transitional_post_submit_page_is_given_time_to_redirect():
    """blacked live (15:5xZ): the accepted submit shows a bare page whose
    JS redirects to members.blacked.com seconds later; judge AFTER that."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    sweep = src.find("ok,method=_submit_login(page,sb_candidates,pf_candidates)")
    wait = src.find("post-submit page redirected after", sweep)
    judge = src.find("if success and not success_url_reached(success, cur, url):", sweep)
    assert sweep != -1 and judge != -1, "anchors moved; re-anchor"
    assert wait != -1, "no post-submit redirect wait: a JS wait-redirect page is judged as a failed login"
    assert sweep < wait < judge
    assert "while time.time()-_t0 < budget:" in src, "the redirect wait loop is gone"
    assert src.count("_wait_transitional(cur)") >= 3, "the transitional wait must also run after clearance and after re-entry (blacked)"
    assert "script[src*='wait-redirect']" in src, "a pending wait-redirect script must keep the wait alive (blacked shell keeps #password in the DOM)"


def test_after_a_cleared_challenge_an_empty_login_form_is_re_submitted_once():
    """vixen live (16:3xZ): the challenge swallowed the POST; the form is back."""
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    post = src.find("login: post-submit cloudflare challenge page")
    resub = src.find("challenge cleared but the login form is", post)
    judge = src.find("anonymous,anon_why=anonymous_surface_check(page)", post)
    assert resub != -1, "no re-submit after a cleared challenge: the swallowed POST is never retried"
    assert post < resub < judge
    assert src.count("_submit_login(page,sb_candidates,pf_candidates)") == 2, "exactly one re-submit"
    assert "if _form_back:" in src, "the re-submit guard is gone"
    assert "second cloudflare challenge after" in src, "a second challenge after the re-submit must be cleared once more (vixen loop)"

