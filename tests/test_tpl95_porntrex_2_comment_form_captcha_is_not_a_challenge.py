"""tpl95-porntrex-2 (download-95/B4-B/tpl-porntrex/, A8-A fix-tpl95-porntrex-2/app-shot-3347380.png): every logged-in
porntrex scene went needs_review "Captcha challenge detected (recaptcha2)". The page shows no challenge (the app's own
screenshot is the age gate over the player); the reCAPTCHA v2 checkbox is the one in the scene's COMMENT form
(div.block-comments > form > div.block-new-comment, beside textarea#comment_message). A captcha that guards posting a
comment does not gate the page, so it must not stop the download. A captcha that gates the page still does.

Fixture: tests/fixtures/tpl95_porntrex_2/scene_comment_block.html is that comment block cut verbatim from the real scene
page capture (.95 campaign/porntrex/1/page.scrubbed.html), sitekey replaced by "fixture-key". Detection runs on real DOM
in a local headless chromium; every network request (the reCAPTCHA iframe) is served an empty page -- no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

COMMENTS = (Path(__file__).parent / "fixtures" / "tpl95_porntrex_2" / "scene_comment_block.html").read_text()
SCENE = (
    "<html><body><div class='video-holder'><video width='640' height='360'></video>"
    "<a class='js-download' href='/get_file/1/fixture_1080p.mp4'>1080p</a></div>%s</body></html>"
)
# The same reCAPTCHA v2 checkbox as a page gate: the page's only content is the challenge form. Rendered as the real
# widget renders it, with its own textarea[name=g-recaptcha-response] token field inside the form (review N3).
WIDGET = (
    "<div class='g-recaptcha' data-sitekey='fixture-key'><div style='width:304px;height:78px'>"
    "<iframe title='reCAPTCHA' width='304' height='78' src='https://www.google.com/recaptcha/api2/anchor?k=fixture-key'>"
    "</iframe></div><textarea id='g-recaptcha-response' name='g-recaptcha-response' class='g-recaptcha-response'"
    " style='display:none'></textarea></div>"
)
GATE = (
    "<html><body><form method='post' action='/verify'><p>Please verify you are a human</p>"
    + WIDGET + "<button type='submit'>Continue</button></form></body></html>"
)
# Review N1: a page gate on a page whose body/layout class mentions comments is still a gate.
GATE_ON_COMMENTS_PAGE = GATE.replace("<body>", "<body class='video-page comments-enabled'><div id='comments-layout'>")
# Review N2: each clause on its own -- a comment block inside the form (no message box), and a message form with no
# comment naming -- is a comment captcha; a widget outside any form is weighed as a gate.
COMMENT_BLOCK_ONLY = (
    "<html><body><video width='640' height='360'></video><form method='post'><div class='block-new-comment'>"
    + WIDGET + "<input type='button' value='Send'></div></form></body></html>"
)
MESSAGE_FORM_ONLY = (
    "<html><body><video width='640' height='360'></video><form method='post' action='/contact'>"
    "<textarea name='message'></textarea>" + WIDGET + "<button type='submit'>Send</button></form></body></html>"
)
NO_FORM_IN_COMMENTS = (
    "<html><body><div class='block-comments'>" + WIDGET + "</div></body></html>"
)
TURNSTILE_GATE = (
    "<html><body><div id='cf-chl-widget-a1b2c' style='width:300px;height:65px'>"
    "<iframe width='300' height='65' src='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/fixture'>"
    "</iframe></div></body></html>"
)


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(headless=True, timeout=20000, args=["--no-sandbox", "--disable-dev-shm-usage"])
    except PlaywrightError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


class _Runner:
    """The challenge gate as the runner calls it; every escape hatch after detection is recorded, never run."""

    def __init__(self):
        self.calls = []

    def _record_captcha_encounter(self, now=None):
        self.calls.append("encounter")

    def _try_captcha_solve(self, page):
        self.calls.append("solve")
        return False

    def _try_ytdlp_fallback(self, url, why):
        self.calls.append("ytdlp")
        return False, "", "", 0, 0

    def _try_gallerydl_fallback(self, url, why):
        self.calls.append("gallerydl")
        return False, "", "", 0, 0

    def _screenshot(self, page, url):
        return ""

    def _update_job(self, url, status, msg, **kw):
        self.calls.append(status)


def _verdicts(pages):
    from playwright.sync_api import sync_playwright

    from bulk_downloader import captcha_resolver
    from bulk_downloader.runner_challenge import ChallengeMixin

    runner_cls = type("R", (_Runner, ChallengeMixin), {})
    out = {}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
            for name, html in pages.items():
                page.set_content(html)
                page.wait_for_timeout(200)
                r = runner_cls()
                r.config = {"name": "porntrex"}
                r.site_id = "fixture"
                out[name] = {
                    "anchor_iframes": page.locator("iframe[src*='recaptcha/api2/anchor']").count(),
                    "has_captcha": r._has_captcha(page),
                    "type": captcha_resolver.detect_captcha_type(page),
                }
        finally:
            browser.close()
    return out


def test_the_scene_page_comment_form_captcha_is_not_a_challenge():
    """The real porntrex comment block on a scene page: no challenge, no type -- the download goes ahead."""
    v = _verdicts({"scene": SCENE % COMMENTS})["scene"]
    assert v["anchor_iframes"] == 1, f"fixture lost its reCAPTCHA iframe: {v}"
    assert v["has_captcha"] is False, (
        "a reCAPTCHA inside the scene's comment form was treated as a page challenge -- every porntrex scene "
        f"goes needs_review 'Captcha challenge detected (recaptcha2)' (tpl95-porntrex-2): {v}")
    assert v["type"] is None, f"comment-form reCAPTCHA classified as the page's captcha: {v}"


def test_a_captcha_that_gates_the_page_is_still_a_challenge():
    """Positive controls: the checkbox as the page's only form (with its real token textarea), the same gate on a page
    whose body/layout mentions comments, a widget outside any form, a Turnstile widget, and a gate that sits AFTER a
    comment captcha in the DOM (every match is weighed, not only the first)."""
    v = _verdicts({
        "gate": GATE,
        "gate_on_comments_page": GATE_ON_COMMENTS_PAGE,
        "no_form": NO_FORM_IN_COMMENTS,
        "turnstile": TURNSTILE_GATE,
        "comment_then_gate": (SCENE % COMMENTS).replace("</body>", GATE[12:-14] + "</body>"),
    })
    for name in ("gate", "gate_on_comments_page", "no_form"):
        assert v[name]["has_captcha"] is True and v[name]["type"] == "recaptcha2", (name, v[name])
    assert v["turnstile"]["has_captcha"] is True and v["turnstile"]["type"] == "turnstile", v["turnstile"]
    assert v["comment_then_gate"]["anchor_iframes"] == 2, v["comment_then_gate"]
    assert v["comment_then_gate"]["has_captcha"] is True, (
        f"a page gate after a comment-form captcha was missed: {v['comment_then_gate']}")
    assert v["comment_then_gate"]["type"] == "recaptcha2", v["comment_then_gate"]


def test_each_comment_clause_alone_marks_a_comment_captcha():
    """Review N2: the comment block inside the widget's form, and a message textarea in its form, each suffice."""
    v = _verdicts({"comment_block": COMMENT_BLOCK_ONLY, "message_form": MESSAGE_FORM_ONLY})
    for name, got in v.items():
        assert got["anchor_iframes"] == 1 and got["has_captcha"] is False and got["type"] is None, (name, got)


def test_an_unwalkable_match_set_is_weighed_as_before():
    """Review R1: a Locator set that cannot be walked per match (no .nth) is weighed as its first node, unfiltered --
    never laxer than the pre-fix check. Test doubles with only .first/.count/.is_visible still see their captcha."""
    from bulk_downloader import captcha_resolver
    from bulk_downloader.runner_challenge import ChallengeMixin

    class _Loc:
        def __init__(self, n):
            self.n = n

        @property
        def first(self):
            return self

        def count(self):
            return self.n

        def is_visible(self, timeout=0):
            return self.n > 0

    class _Page:
        def locator(self, sel):
            return _Loc(1 if sel in ("iframe[src*='hcaptcha.com/captcha']", "iframe[src*='hcaptcha.com']") else 0)

    r = type("R", (_Runner, ChallengeMixin), {})()
    assert r._has_captcha(_Page()) is True
    assert captcha_resolver.detect_captcha_type(_Page()) == "hcaptcha"


def test_the_runner_gate_lets_the_scene_through_and_still_holds_a_gated_page():
    """_handle_captcha_check: the comment-form page continues with no solver/fallback/needs_review; the gated page is
    still held needs_review after its fallbacks."""
    from playwright.sync_api import sync_playwright

    from bulk_downloader.runner_challenge import ChallengeMixin

    runner_cls = type("R", (_Runner, ChallengeMixin), {})
    got = {}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
            for name, html in (("scene", SCENE % COMMENTS), ("gate", GATE)):
                page.set_content(html)
                page.wait_for_timeout(200)
                r = runner_cls()
                r.config = {"name": "porntrex"}
                r.site_id = "fixture"
                from bulk_downloader import runner_challenge
                real_db_log = runner_challenge.db_log
                runner_challenge.db_log = lambda *a, **k: None
                try:
                    cont = r._handle_captcha_check(page, "https://www.porntrex.com/video/1/fixture")
                finally:
                    runner_challenge.db_log = real_db_log
                got[name] = (cont, r.calls)
        finally:
            browser.close()
    assert got["scene"] == (True, []), f"comment-form captcha stopped the scene: {got['scene']}"
    assert got["gate"][0] is False and got["gate"][1][-1] == "needs_review", got["gate"]
