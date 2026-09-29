"""dl95-hoopladigital-3: the generic age dismisser clicks only an age control.

Live on test2 (build 4953203f, 02:36Z): on a hoopladigital title page with no
age gate at all, ``clear_gates`` logged "age: cleared via a:has-text('Enter')"
-- Playwright's ``:has-text`` is a case-insensitive substring match, so it
clicked the publisher link "Disney Enterprises, Inc." ("Help Center" matches
too), left the scene without re-requesting it, and the job failed
"[page_shape] No download button found".

The label cases run ``interstitial.clear_gates`` against real Chromium pages
served from loopback; the re-request case uses a page whose URL change is
observed only after the tier loop, the race the live journal shows.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bulk_downloader import interstitial

BD_GATE_SCOPE = "module"

_TITLE_PAGE = """<!doctype html><html><body>
<h1>Straight Talk: Dolly Parton</h1><p>Movie, 2019. Borrow to watch.</p>
<footer>
  <a href="/publisher/disney">Disney Enterprises, Inc.</a>
  <a href="/help">Help Center</a>
</footer></body></html>"""

_AGE_WALL = """<!doctype html><html><body>
<div role="dialog" class="age-modal">
  <p>This site contains adult content. You must be 18 or older to enter.</p>
  <a href="https://exit.invalid/">Exit</a>
  <button onclick="this.closest('.age-modal').remove()">ENTER</button>
</div>
<footer><a href="/publisher/disney">Disney Enterprises, Inc.</a></footer>
</body></html>"""

_BARE_ENTER_NO_AGE_TEXT = """<!doctype html><html><body>
<p>Search the catalogue.</p>
<button onclick="location.href='/elsewhere'">Enter</button>
</body></html>"""

_OVER_18 = """<!doctype html><html><body>
<div class="age-modal"><button onclick="this.parentNode.remove()">
I am over 18</button></div></body></html>"""


class _Server:
    def __init__(self, pages):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = pages.get(self.path.split("?")[0],
                                 "<html><body>other page</body></html>")
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    s = _Server({"/movie/title": _TITLE_PAGE, "/age": _AGE_WALL,
                 "/bare": _BARE_ENTER_NO_AGE_TEXT, "/over18": _OVER_18})
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _clear(page, url):
    page.goto(url)
    return interstitial.clear_gates(
        page, url=url, sleep=lambda _s: page.wait_for_timeout(200))


def test_hoopla_publisher_link_containing_enter_is_never_an_age_control(
        site, page):
    url = f"{site.base}/movie/title"
    # Precondition: the substring selector DOES nominate the publisher link.
    page.goto(url)
    assert page.locator("a:has-text('Enter')").first.inner_text() == \
        "Disney Enterprises, Inc."
    result = _clear(page, url)
    assert not [m for m in result if m.startswith("age:")], (
        f"HOOPLA-AGE: generic age tier clicked a non-age control: {result}")
    assert page.url == url


def test_bare_enter_without_age_language_is_not_clicked(site, page):
    url = f"{site.base}/bare"
    result = _clear(page, url)
    assert not [m for m in result if m.startswith("age:")], result
    assert page.url == url


def test_control_a_real_age_wall_enter_is_still_cleared(site, page):
    url = f"{site.base}/age"
    result = _clear(page, url)
    assert "age: cleared via button:has-text('Enter')" in result, result
    assert page.locator(".age-modal").count() == 0
    assert page.url == url


def test_control_a_self_describing_over_18_affirmation_is_cleared(
        site, page):
    url = f"{site.base}/over18"
    result = _clear(page, url)
    assert [m for m in result if m.startswith("age:")], result
    assert page.locator(".age-modal").count() == 0


class _LateNavPage:
    """A dismiss click whose navigation commits after the tier's URL read.

    ``clear_gates`` reads ``page.url`` once right after the click; the
    navigation is only visible from the NEXT read on -- the ordering in the
    live journal (no re-request, then "[page_shape]").
    """

    def __init__(self, url):
        self._url = url
        self._pending = None
        self._reads_since_click = 0
        self.goto_calls = []

    @property
    def url(self):
        if self._pending:
            self._reads_since_click += 1
            if self._reads_since_click >= 2:
                self._url, self._pending = self._pending, None
        return self._url

    def locator(self, selector):
        return _LateLocator(self, selector)

    def inner_text(self, *_args, **_kwargs):
        return "Adults only. You must be 18 or older."

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self._url, self._pending = url, None


class _LateLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
        self.first = self

    def count(self):
        return 1 if self.selector == "button:has-text('Enter')" else 0

    def nth(self, _index):
        return self

    def is_visible(self):
        return True

    def inner_text(self):
        return "Enter"

    def click(self, **_kwargs):
        self.page._pending = self.page._url.rsplit("/", 1)[0] + "/elsewhere"


def test_a_clearance_that_lands_elsewhere_late_is_re_requested():
    url = "https://www.hoopladigital.example/movie/title/13338310"
    page = _LateNavPage(url)
    result = interstitial.clear_gates(page, url=url, sleep=lambda _s: None)
    assert "age: cleared via button:has-text('Enter')" in result, result
    assert page.goto_calls == [url], (
        f"HOOPLA-AGE: the page was left on {page.url} after the age click "
        f"and the scene was not re-requested: {result}")
    assert page.url == url


def test_control_a_clearance_that_stays_on_the_url_is_not_re_requested():
    url = "https://www.hoopladigital.example/movie/title/13338310"
    page = _LateNavPage(url)
    page.locator = lambda selector: _StayLocator(page, selector)
    result = interstitial.clear_gates(page, url=url, sleep=lambda _s: None)
    assert "age: cleared via button:has-text('Enter')" in result, result
    assert page.goto_calls == []


class _StayLocator(_LateLocator):
    def click(self, **_kwargs):
        pass
