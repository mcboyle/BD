"""dl95-newsensations-1: a content page the site says does not exist is not a missing download button.

MEASURED on test2 2026-09-29T02:11:43Z (download-95/A8-A/p1/newsensations/RESULT.md, app-page-shot-11370.png): logged in,
members/gallery.php?id=11370&type=vids rendered "Content Id does not exist: 11370." and "Click here to go back." beside
the member nav, and the job ended "[page_shape] No download button found" -- a broken-control verdict, retried, and
counted toward paused_no_button. Fix: in the no-candidate branch of _process_one, a short rendered line that IS the
site's not-found statement fails the job with that line ("permanent", no retry ladder) and resets the streak.
Pages are served by route interception on .example hosts into real headless Chromium; no byte leaves the test.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from contextlib import contextmanager

import pytest

from bulk_downloader import runner as runner_module
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

PAGE_URL = "https://www.tube-members.example/members/gallery.php?id=11370&type=vids"
NAV = "".join(
    f'<li><a href="/members/{n.lower()}.php">{n}</a></li>'
    for n in (
        "HOME",
        "VIDEOS",
        "MODELS",
        "SERIES",
        "CATEGORIES",
        "FAVORITES",
        "DVDS",
        "HELP",
    )
)
DEAD_PAGE = f"""<!doctype html><html><body>
<header><a href="/members/">Site logo</a><input placeholder="Search models, videos, categories..."></header>
<aside><label>DARK THEME</label><ul>{NAV}</ul></aside>
<main><center><b>Content Id does not exist: 11370.</b><br><br>
<a href="javascript:history.back()">Click here to go back.</a></center></main>
<footer>&copy; 2026 Site <a href="/terms">Terms</a> <a href="/privacy">Privacy</a></footer>
</body></html>"""
# A live scene page that merely MENTIONS the words: a comment, and a hidden player-error template.
LIVE_PAGE = f"""<!doctype html><html><body>
<aside><ul>{NAV}</ul></aside>
<main><h1>Requested scene title</h1><p>video not found in 4k?</p>
<p>This video was not found in our 4K collection, sorry</p>
<div style="display:none">Video not found</div></main>
</body></html>"""


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route(
                "**/*",
                lambda route: (
                    route.fulfill(status=200, content_type="text/html", body=html)
                    if route.request.url == PAGE_URL
                    else route.abort()
                ),
            )
            page.goto(PAGE_URL, wait_until="domcontentloaded")
            yield page
        finally:
            browser.close()


class _Runner:
    def __init__(self, streak):
        self._consec_no_btn = streak
        self.failures = []

    def _handle_failure(self, url, message, screenshot=""):
        self.failures.append((url, message, screenshot))


def test_the_dead_content_page_fails_with_the_sites_own_verdict():
    runner = _Runner(streak=4)
    with _page(DEAD_PAGE) as page:
        handled = runner_module._handle_content_not_found_page(
            runner, page, PAGE_URL, "ss.png"
        )
    assert handled is True, (
        "DL95_NS1_DEAD_PAGE_NOT_NAMED: the not-found page fell through to 'No download button found'"
    )
    [(url, message, ss)] = runner.failures
    assert (url, ss) == (PAGE_URL, "ss.png")
    assert '"Content Id does not exist: 11370."' in message, message
    assert "No download button found" not in message
    # the real classifier: terminal, never the page_shape retry ladder
    assert SiteRunner._classify_error(None, message) == "permanent", message
    assert runner._consec_no_btn == 0, (
        "a page naming no such content is not a broken-control miss"
    )


def test_control_a_live_page_that_mentions_the_words_is_not_claimed():
    runner = _Runner(streak=2)
    with _page(LIVE_PAGE) as page:
        handled = runner_module._handle_content_not_found_page(
            runner, page, PAGE_URL, ""
        )
    assert handled is False and runner.failures == []
    assert runner._consec_no_btn == 2


def test_control_an_unreadable_page_claims_nothing():
    class _Broken:
        def evaluate(self, *_a):
            raise RuntimeError("Execution context was destroyed")

    runner = _Runner(streak=3)
    assert (
        runner_module._handle_content_not_found_page(runner, _Broken(), PAGE_URL, "")
        is False
    )
    assert runner.failures == [] and runner._consec_no_btn == 3


@pytest.mark.parametrize(
    ("line", "claimed"),
    [
        ("Content Id does not exist: 11370.", True),
        ("Video not found", True),
        ("Page Not Found", True),
        ("Sorry, this video has been removed.", True),
        ("The video you are looking for does not exist", True),
        ("This scene is no longer available.", True),
        ("Scene 48213 not found", False),  # the id is not after the statement
        ("video not found in 4k?", False),
        ("Video not found here", False),
        ("Click here to go back.", False),
        ("Download this video now", False),
        ("Related: Video not found", False),
    ],
)
def test_not_found_line_rule(line, claimed):
    class _Text:
        def evaluate(self, *_a):
            return f"HOME\nVIDEOS\n{line}\nTerms"

    assert (runner_module._content_not_found_statement(_Text()) == line) is claimed


def test_wired_before_the_streak_and_the_page_shape_failure():
    src = textwrap.dedent(inspect.getsource(SiteRunner._process_one))
    calls = [
        (node.lineno, getattr(node.func, "id", getattr(node.func, "attr", "")))
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
    ]
    wired = min(
        (ln for ln, name in calls if name == "_handle_content_not_found_page"),
        default=None,
    )
    assert wired, "DL95_NS1_NOT_WIRED: _process_one never consults the not-found page"
    streak = min(
        n.lineno
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.AugAssign)
        and getattr(n.target, "attr", "") == "_consec_no_btn"
    )
    no_button = min(
        n.lineno
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant) and n.value == "No download button found"
    )
    assert wired < streak < no_button, (wired, streak, no_button)
