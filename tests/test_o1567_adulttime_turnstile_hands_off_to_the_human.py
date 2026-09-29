"""o1567 fx-adulttime-turnstile (harness-work/ISP-SPLIT-O1564/results/test3/adulttime.md, FAIL).

LIVE on test3 (.80, 21:23Z): freetour.adulttime.com/en/login answers with a Cloudflare managed challenge. The app
clicked the Turnstile box twice, and Cloudflare re-issued it in place both times: the URL stayed the same, the box
never stayed checked and no puzzle appeared (shots/adulttime-fxlive-contact.png, 8 frames). The log then read "challenge
NOT cleared within 15s". Next, do_login spent 66 s trying 25 username selectors on the challenge page. It handed the
window over as "Couldn't find username field: could not fill username; tried 25 selectors", which names the wrong
blocker to the human who has to act on it.

Contract: when the capped clicks leave the Cloudflare challenge up, do_login stops there. With a headed takeover, it
hands the window to the human at once, and the reason names the challenge and what to do. Without a takeover, it
fails with that reason. No username or password probe runs against a challenge page, and no click beyond the two
existing ones is made. A challenge that does clear still goes on to the form, unchanged.

Drives the real do_login and the real clear_cloudflare_challenge on a real headless chromium page. page.route serves
both origins, so no live site is touched. Only the browser launch is replaced; the budget per click is cut to 1.5 s.
"""
from __future__ import annotations

import functools
import os
import time

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-o1567-at.test"
STUCK_URL = ORIGIN + "/en/login"
CLEARING_URL = ORIGIN + "/clears/en/login"
CLEARED_URL = ORIGIN + "/en/login?cleared=1"
CF_ORIGIN = "https://challenges.cloudflare.com"
CF_FRAME_URL = CF_ORIGIN + "/fixture/turnstile"

FRAME_HTML = """<!doctype html><html><body style="margin:0">
<label style="display:flex;align-items:center;height:65px;width:300px">
  <input type="checkbox" id="cb" onclick="parent.postMessage({fixtureChallenge:1},'*')">
  <span>Verify you are human</span></label></body></html>"""


def _challenge_html(on_click_js):
    # the live shape: "Just a moment..." / freetour.adulttime.com / Performing security verification
    return f"""<!doctype html><html><head><title>Just a moment...</title></head><body>
<h1>freetour.adulttime.com</h1><h2>Performing security verification</h2>
<div id="challenge-running">This website uses a security service to protect against malicious bots.</div>
<form id="challenge-form" action="/en/login?__cf_chl_f_tk=x" method="POST">
  <div class="cf-turnstile"><iframe src="{CF_FRAME_URL}" style="width:300px;height:65px;border:0"></iframe></div>
</form>
<script>addEventListener('message', ev => {{
  if (!(ev.data || {{}}).fixtureChallenge) return;
  window.__clicks = (window.__clicks || 0) + 1;
  {on_click_js}
}});</script></body></html>"""


PAGES = {
    STUCK_URL: _challenge_html("/* live: re-issued in place, the URL never moves */"),
    CLEARING_URL: _challenge_html("location.replace('/en/login?cleared=1');"),
    CLEARED_URL: "<!doctype html><html><head><title>Log in</title></head><body><p>members login</p></body></html>",
    CF_FRAME_URL: FRAME_HTML,
}


def _serve(route, request):
    url = request.url
    html = PAGES.get(url) or PAGES.get(url.split("?", 1)[0])
    if html is None:
        route.fulfill(status=404, content_type="text/plain", body="not fixture")
    else:
        route.fulfill(status=200, content_type="text/html", body=html)


class _RoutedBrowser:
    """The real chromium browser; every context do_login opens serves the fixture origins."""

    def __init__(self, browser):
        self._b = browser
        self.closed = 0

    def new_context(self, **kw):
        ctx = self._b.new_context(**kw)
        for origin in (ORIGIN, CF_ORIGIN):
            ctx.route(origin + "/**", _serve)
        return ctx

    def close(self):
        self.closed += 1
        self._b.close()

    def __getattr__(self, name):
        return getattr(self._b, name)


@pytest.fixture
def login(monkeypatch, tmp_path):
    from playwright.sync_api import sync_playwright

    from bulk_downloader import cloak, learn, stealth
    from bulk_downloader.login_impl import submit

    p = sync_playwright().start()
    chrome = os.environ.get("BD_PW_CHROME", "")
    kw = {"headless": True, "timeout": 20000, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if chrome and os.path.exists(chrome):
        kw["executable_path"] = chrome
    try:
        raw = p.chromium.launch(**kw)
    except Exception as e:  # noqa: BLE001
        p.stop()
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")
    browser = _RoutedBrowser(raw)
    fills = []
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(submit, "clear_cloudflare_challenge",
                        functools.partial(submit.clear_cloudflare_challenge, wait=1.5))

    def fill(page, candidates, value, label):
        fills.append(label)
        return False, "fixture: no field"

    monkeypatch.setattr(submit, "_try_fill", fill)

    def _run(url, allow_manual_takeover):
        # Documented zero-entropy password; no vault reference or real credential.
        config = {"name": "adulttime", "login_url": url, "username": "fixture",
                  "password": "zero-entropy-password", "success_url": "/members", "wait": 0,
                  "login_evidence_dir": str(tmp_path / "login_evidence"), "use_real_chrome": False,
                  "use_stealth": False, "use_stealth_library": False}
        t0 = time.time()
        result = submit.do_login(config, allow_manual_takeover=allow_manual_takeover)
        return result, time.time() - t0

    yield _run, browser, fills
    try:
        if not browser.closed:
            raw.close()
    finally:
        p.stop()


def _clicks(capsys):
    err = capsys.readouterr().err
    return err, err.count("clicked Turnstile checkbox")


def test_a_challenge_the_clicks_do_not_clear_is_handed_to_the_human_naming_it(login, capsys):
    run, _browser, fills = login
    result, took = run(STUCK_URL, allow_manual_takeover=True)
    err, clicks = _clicks(capsys)
    assert result[0] == "MANUAL_PENDING", f"O1567_AT_NO_TAKEOVER: {result[:2]}"
    reason = result[1]
    assert "Cloudflare" in reason and "Verify you are human" in reason and "I'm Done" in reason, (
        f"O1567_AT_WRONG_BLOCKER_NAMED: {reason!r}")
    assert fills == [], f"O1567_AT_USERNAME_PROBED_ON_CHALLENGE: {fills}"
    assert clicks == 2, f"clicks beyond the existing cap: {clicks}\n{err[-800:]}"
    assert "handing off for manual takeover — Cloudflare" in err, err[-800:]
    assert took < 25, f"hand-off took {took:.1f}s"
    _pw, _b, ctx = result[2]
    page = ctx.pages[0]
    assert page.url == STUCK_URL and page.evaluate("window.__clicks") == 2   # the window is left on the challenge


def test_without_a_takeover_the_login_fails_naming_the_challenge(login, capsys):
    run, browser, fills = login
    result, _took = run(STUCK_URL, allow_manual_takeover=False)
    ok, info, cookies = result
    assert ok is False and cookies == [], result
    assert "Cloudflare" in info and "Verify you are human" in info, f"O1567_AT_WRONG_BLOCKER_NAMED: {info!r}"
    assert fills == [], f"O1567_AT_USERNAME_PROBED_ON_CHALLENGE: {fills}"
    assert browser.closed == 1


def test_control_a_challenge_that_clears_goes_on_to_the_form(login, capsys):
    """Positive control: the fixture's click CAN clear, and then the username search runs as before."""
    run, _browser, fills = login
    result, _took = run(CLEARING_URL, allow_manual_takeover=False)
    err, clicks = _clicks(capsys)
    assert "challenge cleared ->" in err and clicks == 1, err[-800:]
    assert fills and fills[0] == "username", fills
    assert "Cloudflare" not in result[1], result[1]


# Lens REFUTE (bd-worker-B7-B): a Turnstile widget EMBEDDED in a username-first login form is not an interstitial.
# The click never navigates it away, so the challenge "stays up"; the stop above must not fire, and the username
# search runs as on BASE.
TWO_STEP_URL = ORIGIN + "/two-step/login"
PAGES[TWO_STEP_URL] = f"""<!doctype html><html><head><title>Sign in</title></head><body>
<form action="/two-step/next" method="POST"><input name="username" type="text">
<div class="cf-turnstile"><iframe src="{CF_FRAME_URL}" style="width:300px;height:65px;border:0"></iframe></div>
<button type="submit">Next</button></form>
<script>addEventListener('message', ev => {{ window.__clicks = (window.__clicks || 0) + 1; }});</script></body></html>"""


def test_an_embedded_turnstile_on_a_username_first_form_still_reaches_the_username_search(login, capsys):
    run, _browser, fills = login
    result, _took = run(TWO_STEP_URL, allow_manual_takeover=False)
    err = capsys.readouterr().err
    assert "cloudflare challenge page" in err, "the fixture did not reach the challenge path:\n" + err[-600:]
    assert fills and fills[0] == "username", f"O1567_AT_EMBEDDED_WIDGET_STOPPED_LOGIN: fills={fills} info={result[1]!r}"
    assert "Cloudflare 'Verify you are human' challenge not cleared" not in result[1], result[1]
