"""dl95-reddit-2: a bot-challenge wall is not a page with no download button.

Live (test2 .95, 2026-09-29 02:32Z, reddit 864f1edb): a public video post
rendered reddit's bot wall -- "Prove your humanity / We're committed to safety
and security. But not for bots. Complete the challenge below..." -- with no
post, no player and no captcha iframe yet. None of CAPTCHA_SELECTORS matched,
so the job failed "[page_shape] No download button found" and fed the
paused_no_button streak. The hub's headless chromium gets a sibling wall
("You've been blocked by network security").

Detection and routing only: the job goes to needs_review for an operator. No
part of this interacts with or answers the challenge.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader import challenge_classify
from bulk_downloader import runner as runner_module
from bulk_downloader.constants import CAPTCHA_SELECTORS


POST_URL = "https://www.reddit.com/r/Unexpected/comments/1wsf2t3/a_pleasant_drive/"

# Visible text reconstructed from the app's own failure screenshot
# (harness-work/UIUX-20260928/download-95/B6-B/p1/reddit/app-shot-fail.png).
_PROVE_HUMANITY = """<!doctype html><html><head><title>Reddit</title></head>
<body><header><a href="/">reddit</a></header>
<main><img alt="" src="data:,"><h1>Prove your humanity</h1>
<p>We&#8217;re committed to safety and security. But not for bots.
Complete the challenge below and let us know you&#8217;re a real person.</p>
<div id="challenge"></div></main>
<footer>Reddit, Inc. &copy; 2026. All rights reserved.
<a href="/user-agreement">User Agreement</a> <a href="/privacy">Privacy Policy</a>
<a href="/content-policy">Content Policy</a> <a href="/help">Help</a></footer>
</body></html>"""

# Measured on the hub's headless chromium against the same post URL: the
# headline is a styled <div>, not a heading element.
_NETWORK_BLOCK = """<!doctype html><html><head><title></title></head>
<body class="theme-beta"><div class="items-center flex flex-col">
<div class="font-bold text-24">You've been blocked by network security.</div>
<div class="text-16"><div class="pt-md">If you think you've been blocked by
mistake, file a ticket below and we'll look into it.
<div class="flex"><a href="/ticket">File a ticket</a></div></div></div>
</div></body></html>"""

# Lens negative (bd-cx-worker-1 REFUTE): a post that DISCUSSES a bot check.
_POST_QUOTES_THE_PHRASE = """<title>Why do websites ask this? - Reddit</title>
<nav>Home Communities Log In</nav><main><article><h1>Why do websites ask
this?</h1><p>Posted by u/example. I keep seeing the question "Are you a robot"
on websites. How does this test work?</p><p>42 comments. Share Save
Report.</p></article></main>"""

# A post whose own headline IS the phrase: content, not a wall.
_POST_TITLED_WITH_THE_PHRASE = """<title>Are you a robot? : r/AskReddit</title>
<main><article><h1>Are you a robot?</h1><p>Posted by u/example.</p>
</article></main>"""

# Negative control: an ordinary post page with nav chrome and no download
# control. It must stay a no-button page.
_ORDINARY_NO_BUTTON = """<!doctype html><html><head><title>A pleasant drive :
r/Unexpected</title></head><body><nav><a href="/login">Log In</a>
<a href="/search">Search</a></nav><main><h1>A pleasant drive</h1>
<p>Posted by u/someone. 1.2k comments. Security tips for new drivers.</p>
</main></body></html>"""


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


class _RunnerStub:
    site_id = "864f1edb"
    config = {"name": "reddit"}

    def __init__(self, consec=4):
        self._consec_no_btn = consec
        self.updates = []

    def _update_job(self, url, status, message, screenshot=None, **extra):
        self.updates.append((url, status, message, screenshot, extra))


def _visible_captcha_selectors(page):
    hits = []
    for sel in CAPTCHA_SELECTORS:
        loc = page.locator(sel).first
        if loc.count() > 0 and loc.is_visible(timeout=500):
            hits.append(sel)
    return hits


def test_precondition_the_live_wall_escapes_every_existing_detector():
    """Why the job reached the no-button branch: no captcha selector and no
    confirmed-no-video shape recognises the wall."""
    runner = _RunnerStub()
    with _page(_PROVE_HUMANITY) as page:
        assert _visible_captcha_selectors(page) == []
        assert runner_module._handle_confirmed_no_video_page(
            runner, page, POST_URL, "wall.png") is False
    assert runner.updates == []


@pytest.mark.parametrize("html, phrase", [
    (_PROVE_HUMANITY, "prove your humanity"),
    (_NETWORK_BLOCK, "blocked by network security"),
], ids=["prove-humanity", "network-block"])
def test_bot_wall_routes_to_needs_review_not_page_shape(monkeypatch, html, phrase):
    history = []
    monkeypatch.setattr(runner_module, "db_log", lambda *a: history.append(a))
    runner = _RunnerStub(consec=4)
    with _page(html) as page:
        handled = runner_module._handle_bot_wall_page(
            runner, page, POST_URL, "wall.png")

    assert handled is True
    assert len(runner.updates) == 1
    url, status, message, shot, extra = runner.updates[0]
    assert (url, status, shot) == (POST_URL, "needs_review", "wall.png")
    assert extra.get("captcha_type") == "bot-wall"
    assert phrase in message.lower()
    assert "no download button" not in message.lower()
    # A bot wall is not a broken-control miss: the streak does not advance.
    assert runner._consec_no_btn == 4
    assert len(history) == 1 and history[0][3] == "needs_review"
    assert phrase in history[0][6].lower()


def test_no_button_branch_consults_the_bot_wall_before_failing():
    """The wiring: _process_one asks the bot-wall check after the screenshot
    and before any no-button streak or 'No download button found' failure."""
    import inspect

    src = inspect.getsource(runner_module.SiteRunner._process_one)
    wall = src.find("_handle_bot_wall_page(self, page, url, ss)")
    shot = src.rfind("ss=self._screenshot(page,url)", 0, wall)
    fail = src.find('"No download button found"', wall)
    streak = src.find("self._consec_no_btn+=1", wall)
    assert src.find('"No download button found"') >= 0  # probe can say yes
    assert wall >= 0, (
        "_process_one no longer calls _handle_bot_wall_page: a bot wall "
        "would fail as '[page_shape] No download button found' again")
    assert 0 <= shot < wall < streak < fail, (
        "bot-wall check must sit after the screenshot and before the "
        "no-button streak and failure")


@pytest.mark.parametrize("html", [
    _ORDINARY_NO_BUTTON, _POST_QUOTES_THE_PHRASE, _POST_TITLED_WITH_THE_PHRASE,
], ids=["ordinary-post", "post-quotes-phrase", "post-titled-with-phrase"])
def test_content_pages_are_left_to_the_page_shape_path(monkeypatch, html):
    """Negative controls: nav 'Log In', body 'Security tips', and a post that
    discusses or is titled with a bot-check phrase are not a wall."""
    monkeypatch.setattr(runner_module, "db_log", lambda *a: None)
    runner = _RunnerStub()
    with _page(html) as page:
        handled = runner_module._handle_bot_wall_page(
            runner, page, POST_URL, "post.png")
    assert handled is False and runner.updates == [], (
        f"POST_MISFILED_AS_BOT_WALL {runner.updates}")


def test_bot_wall_phrase_needs_a_headline_line_on_a_short_page():
    for text in ("Prove your humanity", "You've been blocked by network security."):
        phrase = challenge_classify.bot_wall_phrase("", text)
        assert phrase
        assert not any(w in phrase for w in challenge_classify._FORBIDDEN_OUT)
    assert challenge_classify.bot_wall_phrase("Reddit", "A pleasant drive") is None
    # the phrase inside a sentence is discussion, not a headline
    assert challenge_classify.bot_wall_phrase(
        "", 'I keep seeing the question "Are you a robot" on websites.') is None
    # a long page is content even when a line reads like a wall
    assert challenge_classify.bot_wall_phrase(
        "", "Prove your humanity\n" + "comment text " * 200) is None
