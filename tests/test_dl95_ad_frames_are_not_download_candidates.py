"""dl95-file-examples-7 (O1513, harness-work/DOT95-LANE/live-dl95-file-examples-2/run-0115/driver.log + test2 journal 01:15-01:17Z).

Measured on test2 54cf03cf: five file-examples.com sample-video pages all ended needs_review "Best is 900p (below 1080p)
(no identity proof ...) -- Approve to force. Saw: 900p(?):Advertisement Advertisement ht | ...". Replayed with the real
find_best_download on the page's rendered DOM (fixture): every admitted candidate is a Google ad <iframe aria-label=
"Advertisement" title="Advertisement" tabindex="0" src="https://googleads.g.doubleclick.net/pagead/ads?...&h=280">, reached
by the wide sweep's [tabindex='0'] net, with a tier read out of its own src. Approve-to-force would click an ad.

Contract after the fix: a frame element (<iframe>/<frame>) is never a download candidate; page controls are unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

URL = "https://file-examples.com/index.php/sample-video-files/sample-mp4-files/"
FIXTURE = Path(__file__).parent / "fixtures" / "dl95_file_examples_mp4_page_with_ad_frames.html"


@contextmanager
def _page(html, url=URL):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html)
                     if r.request.url == url else r.fulfill(status=204, body=""))
            pg.goto(url, wait_until="load")
            yield pg
        finally:
            br.close()


def _tags(cands):
    out = []
    for c in cands:
        try:
            out.append(c["locator"].evaluate("e => e.tagName"))
        except Exception as exc:  # noqa: BLE001 -- recorded, asserted below
            out.append(f"?{type(exc).__name__}")
    return out


def test_ad_frames_are_never_candidates():
    from bulk_downloader.detect import find_best_download

    html = FIXTURE.read_text(encoding="utf-8")
    with _page(html) as pg:
        assert pg.locator("iframe[aria-label='Advertisement'][tabindex='0']").count() >= 2, "fixture lost its ad frames"
        best = find_best_download(pg)
        cands = list((best or {}).get("_all_candidates") or [])
        if best and best not in cands:
            cands.insert(0, best)
        tags = _tags(cands)
        texts = [(c.get("text") or "")[:60] for c in cands]
    assert "IFRAME" not in tags, f"DL95_AD_FRAME_ADMITTED: {list(zip(tags, texts))}"
    assert not any("googleads" in t or t.startswith("Advertisement") for t in texts), texts


def test_frame_helper_names_frames_only():
    from bulk_downloader.detect import _is_frame_element

    body = ('<iframe id="ad" title="Advertisement" tabindex="0" src="about:blank"></iframe>'
            '<a id="dl" href="/wp-content/storage/2017/04/file_example_MP4_1920_18MG.mp4" download>Download 1080p</a>'
            '<div id="d" tabindex="0">1080p</div>')
    with _page(f"<html><body>{body}</body></html>") as pg:
        assert _is_frame_element(pg.locator("#ad")) is True
        assert _is_frame_element(pg.locator("#dl")) is False
        assert _is_frame_element(pg.locator("#d")) is False


def test_a_real_download_anchor_beside_an_ad_frame_still_wins():
    """Control: the page's own control is still found and ranked when an ad frame sits next to it."""
    from bulk_downloader.detect import find_best_download

    body = ('<iframe title="Advertisement" aria-label="Advertisement" tabindex="0" '
            'src="https://googleads.g.doubleclick.net/pagead/ads?client=ca-pub-1&amp;output=html&amp;h=280&amp;w=360"></iframe>'
            '<a href="https://cdn.example.invalid/scene_1080p.mp4" download>Download 1080p MP4</a>')
    with _page(f"<html><body>{body}</body></html>", "https://site.example.invalid/video/1/scene/") as pg:
        best = find_best_download(pg)
        assert best, "the real anchor must be found"
        assert best["locator"].evaluate("e => e.tagName") == "A"
        assert best["locator"].get_attribute("href").endswith("scene_1080p.mp4")
