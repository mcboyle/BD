"""tpl95-whoreshub-1 (O1513, harness-work/UIUX-20260928/download-95/B4-B/tpl-whoreshub/events.jsonl#login_done,run_done).

Measured on test2 v3.66.1710 (journal 00:32-00:35Z, evidence manual-takeover-...-20260929T003455Z): the login page
(https://www.whoreshub.com/) has NO login form -- the site's login is a fancybox modal (a#login -> /login/). The generic
username selector ``form input[type='text']`` matched the header SEARCH box (form#search_form, name=q), the username
was typed into it, and the staged-login "continue" click submitted it as a search; then "Couldn't find password field".
POST /login still said ok:true (dl95-vip4k-2 fixes that half). Logged out, every rendition link on a scene page is
``<a href="/login-required/" data-fancybox="ajax">MP4 1080p ...</a>``, and the job ended needs_review "looks like a
modal-trigger button -- set Trigger Selector", which sends the operator to the wrong fix.

Contract after the fix:
  * _try_fill never types into a search box, and says so when that is all it found;
  * a login form next to a search form is still filled (the walk continues past the search box);
  * a download link whose href is the site's login page is reported "not logged in", not as a trigger problem.

Fixtures are verbatim captures (tests/fixtures/tpl95_whoreshub_*.html); pages are served by page.route, no network.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

FIX = Path(__file__).parent / "fixtures"
HOME = (FIX / "tpl95_whoreshub_home_search_form.html").read_text(encoding="utf-8")
LOGIN = (FIX / "tpl95_whoreshub_login_fragment.html").read_text(encoding="utf-8")
GENERIC = ["form input[type='text']"]  # the selector that matched on test2


@contextmanager
def _page(html, url="https://www.whoreshub.com/"):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page()
            pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html)
                     if r.request.url == url else r.fulfill(status=204, body=""))
            pg.goto(url, wait_until="load")
            yield pg
        finally:
            br.close()


def _fill(page):
    from bulk_downloader.login_impl import _common as c

    return c._try_fill(page, GENERIC, "someuser", "username")


def test_search_box_is_not_filled_with_the_username():
    with _page(HOME) as pg:
        assert pg.locator("form#search_form input[name=q]").count() == 1, "fixture is not the measured search form"
        ok, why = _fill(pg)
        typed = pg.locator("input[name=q]").input_value()
    assert typed == "", f"WHORESHUB_USERNAME_TYPED_INTO_SEARCH: {typed!r}"
    assert ok is False and "search box" in why and "name=q" in why, (ok, why)


def test_login_form_after_a_search_form_is_still_filled():
    body = HOME.replace("</body>", LOGIN + "</body>")
    with _page(body) as pg:
        ok, sel = _fill(pg)
        assert ok is True, sel
        assert pg.locator("#login_username").input_value() == "someuser"
        assert pg.locator("input[name=q]").input_value() == ""


def test_login_fragment_alone_fills_as_before():
    """Control: the site's real login form is untouched by the new rule."""
    with _page(LOGIN, "https://www.whoreshub.com/login/") as pg:
        ok, _sel = _fill(pg)
        assert ok is True and pg.locator("#login_username").input_value() == "someuser"


@pytest.mark.parametrize(
    "href,expected",
    [
        ("https://www.whoreshub.com/login-required/", True),
        ("https://www.whoreshub.com/login/", True),
        ("/sign-in?next=/videos/1", True),
        ("https://www.whoreshub.com/videos/738635/caught-cheating/", False),
        ("https://www.whoreshub.com/get_file/1/abc/738000/738635/738635_1080p.mp4/", False),
        ("", False),
        (None, False),
    ],
)
def test_login_wall_href(href, expected):
    from bulk_downloader.runner_transport import _is_login_wall_href

    assert _is_login_wall_href(href) is expected


# ── the needs_review text on the real _do_download no-event path ──────────────


class _Page:
    url = "https://www.whoreshub.com/videos/738635/caught-cheating/"

    def evaluate(self, script):
        return None

    @contextmanager
    def expect_download(self, *, timeout):
        from playwright.sync_api import TimeoutError as PWTimeout

        yield None
        raise PWTimeout("no download event")

    def title(self):
        return "Caught Cheating"


class _Link:
    """<a href="/login-required/" data-fancybox="ajax" class="btn">MP4 1080p, 412.51 Mb</a>: the click opens a modal."""

    def __init__(self, href):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):
        return None


def _review(tmp_path, monkeypatch, href):
    from bulk_downloader import runner_transport as transport

    class _Runner(transport.TransportMixin):
        def __init__(self):
            self.site_id = "wh"
            self.config = {"name": "whoreshub", "use_http_dl": True}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self.jobs = {}
            self.log = logging.getLogger("wh")
            self.status = []

        def _screenshot(self, *_a, **_k):
            return ""

        def _update_job(self, _url, state, message="", **_k):
            self.status.append((state, message))

        def _handle_failure(self, url, message):
            self.status.append(("failed", message))

        def _probe_for_higher_tier(self, url, **_k):
            return url

        def _build_mirror_urls(self, _url):
            return []

    monkeypatch.setattr(transport, "db_log", lambda *_a, **_k: None)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    runner = _Runner()
    page = _Page()
    runner._do_download(page, object(), page.url,
                        {"locator": _Link(href), "score": 0, "size": 0, "text": "MP4 1080p, 412.51 Mb",
                         "_all_candidates": []}, Path(tmp_path), "1080p")
    return [m for s, m in runner.status if s == "needs_review"]


def test_login_required_link_is_reported_not_logged_in(tmp_path, monkeypatch):
    review = _review(tmp_path, monkeypatch, "https://www.whoreshub.com/login-required/")
    assert len(review) == 1, review
    assert "not logged in" in review[0] and "Trigger Selector" not in review[0], (
        f"WHORESHUB_LOGIN_WALL_REPORTED_AS_TRIGGER: {review[0]!r}")


def test_dead_button_without_a_login_href_keeps_the_trigger_hint(tmp_path, monkeypatch):
    """Control: a real modal trigger (no login href) is still sent to 'set Trigger Selector'."""
    review = _review(tmp_path, monkeypatch, None)
    assert len(review) == 1 and "modal-trigger button" in review[0], review
