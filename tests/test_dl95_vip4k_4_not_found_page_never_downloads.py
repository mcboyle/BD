"""dl95-vip4k-4 (HIGH; download-95/A9-A/tpl95/vip4k-proof-183/run1-1425-404/RESULT.md + journal-1425-183.txt).

Measured on .183 (2026-09-29T03:42Z): members.vip4k.com/en/videos/1425 does not exist. The worker's page answered HTTP 404,
title "Page not found | Vip4k", body "Looks like you took a wrong turn..." over a "best videos" grid whose cards carry
REAL download links for other scenes. The job logged "admitted without identity proof; no candidate on this page could
be attributed to the scene" -> "bare media leaf '4k.mp4' -> 'Page not found _ Vip4k.mp4'" and 198 MB of an unrelated
scene landed in 3 s. dl95-newsensations-1's not-found reader (this lane base) runs only when NO candidate exists, and
reads the body, where this page says no "not found" line.

Contract after the fix: a winner with no identity proof, on a page that answered 404/410 or states the content is
not found, fails the job as "Content not found on the site" before the min-resolution gate -- nothing is downloaded,
nothing is offered to Approve. A page that answered 200 and says nothing is unchanged (control).
Fixture: tests/fixtures/dl95_vip4k_1425_page_not_found.html (the worker's rendered DOM, see its header).
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

URL = "https://members.vip4k.com/en/videos/1425"
FIXTURE = Path(__file__).parent / "fixtures" / "dl95_vip4k_1425_page_not_found.html"


@contextmanager
def _page(html, status):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1440, "height": 1000})
            pg.route("**/*", lambda r: r.fulfill(status=status, content_type="text/html", body=html)
                     if r.request.url == URL else r.fulfill(status=204, body=""))
            pg.goto(URL, wait_until="load")
            yield pg
        finally:
            br.close()


class _Runner:
    def __init__(self):
        self.failures, self.shots = [], []

    def _handle_failure(self, url, message, screenshot=None, **_k):
        self.failures.append(message)

    def _screenshot(self, _page, url):
        self.shots.append(url)
        return "shot.png"


def test_the_404_pages_winner_is_another_scenes_4k_link_with_no_identity_proof():
    """Precondition: the real selector reproduces the live winner on the real page."""
    from bulk_downloader.detect import find_best_download

    with _page(FIXTURE.read_text(encoding="utf-8"), 404) as pg:
        best = find_best_download(pg)
        href = best["locator"].get_attribute("href") if best else ""
    assert best and best.get("_no_identity_proof") and href.endswith("/4k.mp4?SCRUBBED"), (best, href)


def test_a_not_found_page_refuses_its_unattributed_winner():
    from bulk_downloader import runner as rmod
    from bulk_downloader.detect import find_best_download

    r = _Runner()
    with _page(FIXTURE.read_text(encoding="utf-8"), 404) as pg:
        best = find_best_download(pg)
        handled = rmod._refuse_not_found_winner(r, pg, URL, best)
    assert handled is True and r.failures, f"DL95_NOT_FOUND_PAGE_DOWNLOADED: handled={handled} failures={r.failures}"
    assert r.failures[0].startswith('Content not found on the site: "HTTP 404"'), r.failures


def test_the_same_page_answering_200_is_unchanged():
    """Control: the verdict comes from the page (its status here), not from the winner's shape."""
    from bulk_downloader import runner as rmod

    r = _Runner()
    with _page(FIXTURE.read_text(encoding="utf-8"), 200) as pg:
        assert rmod._page_not_found_reason(pg) == ""
        assert rmod._refuse_not_found_winner(r, pg, URL, {"_no_identity_proof": True, "text": "4K"}) is False
    assert not r.failures


def test_a_soft_404_that_states_it_is_refused_too():
    """A 200 page whose own line says the video is not found (newsensations-1's reader) is the same verdict."""
    from bulk_downloader import runner as rmod

    html = "<html><body><h1>Video not found</h1><a href='/other/4k.mp4' download>4K</a></body></html>"
    r = _Runner()
    with _page(html, 200) as pg:
        assert rmod._refuse_not_found_winner(r, pg, URL, {"_no_identity_proof": True, "text": "4K"}) is True
    assert r.failures[0].startswith('Content not found on the site: "Video not found"'), r.failures


def test_an_attributed_winner_is_not_second_guessed():
    """Scope: only a winner nothing ties to the scene is refused."""
    from bulk_downloader import runner as rmod

    r = _Runner()
    with _page(FIXTURE.read_text(encoding="utf-8"), 404) as pg:
        assert rmod._refuse_not_found_winner(r, pg, URL, {"text": "4K"}) is False


def test_the_gate_runs_before_the_min_resolution_gate():
    """Wiring: _process_one consults it once a winner exists, before min_res (so Approve cannot force it either)."""
    import bulk_downloader.runner as rmod

    tree = ast.parse(Path(rmod.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_process_one")
    calls = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_refuse_not_found_winner"]
    min_res = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "min_res" for t in n.targets)]
    assert len(calls) == 1 and min_res and calls[0] < min(min_res), (calls, min_res)
