"""dl95-dailymotion-3 (O1513, harness-work/UIUX-20260928/download-95/A5-A/RESULT-dailymotion.md D3, shot dm7/DM__scrape.png).

Add URLs > Scrape listing on https://www.dailymotion.com/us returned 0 links (58 KB of HTML, JS-rendered) and the UI could
only toast "No video links found". The fixture is a REAL capture of that page (2026-09-28, 57,979 bytes, 0 <a href>,
21 <script>). /api/scrape_listing must say what it measured and point to the rendered crawl when it finds nothing.

Hermetic: the page is served by an httpx.MockTransport in place of the SSRF-guarded transport; no network.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

BD_GATE_SCOPE = "module"

FIXTURE = Path(__file__).parent / "fixtures" / "dl95_dailymotion_us_listing.html"
URL = "https://www.dailymotion.com/us"


@pytest.fixture
def scrape(monkeypatch):
    import bulk_downloader.app_scrape_listing as sl
    import bulk_downloader.ssrf_transport as st
    from bulk_downloader.app import app

    pages: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=pages[str(request.url)], headers={"content-type": "text/html"}
        )

    monkeypatch.setattr(sl, "_is_url_public", lambda *a, **k: True)
    monkeypatch.setattr(
        st, "guarded_transport", lambda *a, **k: httpx.MockTransport(handler)
    )

    def run(html: str) -> dict:
        pages[URL] = html
        with app.test_request_context(
            "/api/scrape_listing", method="POST", json={"url": URL}
        ):
            resp = sl.api_scrape_listing()
        resp = resp[0] if isinstance(resp, tuple) else resp
        return resp.get_json()

    return run


def test_real_js_rendered_listing_explains_the_empty_result(scrape):
    html = FIXTURE.read_text(encoding="utf-8")
    assert len(html) > 50_000 and "<a " not in html.lower(), (
        "fixture is not the JS-rendered capture"
    )
    body = scrape(html)
    assert body["ok"] is True and body["count"] == 0 and body["found"] == []
    hint = body.get("hint") or ""
    assert hint, f"DL95_SCRAPE_EMPTY_WITHOUT_HINT: {sorted(body)}"
    assert body.get("anchors") == 0
    assert "Discover scenes" in hint and "DOM analyzer" in hint, hint
    assert f"{len(html) // 1024} KB" in hint and "0 links" in hint, hint


def test_static_listing_with_links_has_no_hint(scrape):
    """Positive control: a static listing still returns its video links, with no hint attached."""
    html = '<html><body><a href="/video/x8abc1">one</a><a href="/about">about</a><a href="/v/clip.mp4">two</a></body></html>'
    body = scrape(html)
    assert body["count"] == 2, body
    assert body["found"] == [
        "https://www.dailymotion.com/video/x8abc1",
        "https://www.dailymotion.com/v/clip.mp4",
    ]
    assert "hint" not in body and "anchors" not in body, body


def test_static_page_without_video_links_counts_its_anchors(scrape):
    body = scrape(
        '<html><body><a href="/about">a</a><a href="/help">b</a></body></html>'
    )
    assert body["count"] == 0 and body["anchors"] == 2 and "2 links" in body["hint"], (
        body
    )
