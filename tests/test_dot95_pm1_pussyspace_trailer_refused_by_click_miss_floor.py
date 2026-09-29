"""dot95-pm1-pussyspace-clickmiss-floor (PM RULING, harness-work/DOT95-LANE/MERGE-1300.md PM1; as MERGE-0930).

Merging main 27b1e565 into the lane brought dl95-pussyspace-1-rediff's spec: the page-media fallback
at _do_download's click-miss exits never takes a trailer (took False, 0 bytes). On the lane the
fallback reached the kellymadisonmedia-2 trailer-only branch and wrote a needs_review itself
(took True). Ruling: the trailer is refused by the click-miss floor (spa_media_extract.
click_miss_candidates drops trailer/preview-class options), so the fallback answers False and its
caller files the review; the porn00-3 hold and the kmm-2 hold stay for everything else.

Hermetic: the scene HTML is fulfilled in-process in headless Chromium; every other request aborts.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SCENE = "https://www.pussyspace.example/vid-6167743-trailer/"
TRAILER = "https://www.pussyspace.example/trailers/6167743/trailer_1080p.mp4"
LOW = "https://www.pussyspace.example/media/scene-6167743-720p.mp4"


def _html(*media):
    body = "".join(f'<video muted src="{m}"></video>' for m in media)
    return f"<!doctype html><html><body>{body}<a href='/1080p/'>1080p</a></body></html>"


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    def handler(route, request):
        if request.url == SCENE:
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", handler)
            page.goto(SCENE, wait_until="domcontentloaded")
            yield page
        finally:
            browser.close()


def _host(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx
    from bulk_downloader import runner_transport as rt

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Host(rt.TransportMixin, rx.ExtractorsMixin):
        _PAGE_MEDIA_WAIT_S = 0.5
        site_id = "dot95pm1"

        def __init__(self):
            self.config = {"name": "dot95-pm1", "download_dir": str(tmp_path), "min_resolution": 1080}
            self.jobs = {}
            self._lock = threading.Lock()
            self.updates, self.transfers = [], []
            self._spa_api_capture = None

        def _update_job(self, url, status, message="", **extra):
            self.updates.append((status, message))

        def log_event(self, *a, **k):
            pass

        def _screenshot(self, *a, **k):
            return ""

        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            self.transfers.append(file_url)
            Path(output_path).write_bytes(b"\x00" * 16)
            return True

        def _size_on_disk_after_tagging(self, path, fallback):
            return fallback

    return Host()


def test_the_fallback_refuses_a_trailer_only_page_by_the_click_miss_floor(tmp_path, monkeypatch):
    host = _host(tmp_path, monkeypatch)
    with _page(_html(TRAILER)) as page:
        took = host._fallback_to_page_media(page, SCENE, "winner rejected: navigation URL")
    assert took is False and host.transfers == [] and host.updates == [], (
        f"DOT95_PM1_TRAILER_HELD_NOT_REFUSED: took={took} transfers={host.transfers} updates={host.updates}")


def test_control_the_no_candidate_call_still_holds_a_trailer_only_page(tmp_path, monkeypatch):
    """kmm-2 is unchanged off the fallback: Row 722's own call names the trailer in its hold."""
    host = _host(tmp_path, monkeypatch)
    with _page(_html(TRAILER)) as page:
        took = host._try_spa_api_media_extractor(SCENE, page)
    assert took is True and host.transfers == [], (took, host.transfers)
    assert host.updates[-1][0] == "needs_review" and "trailer_1080p.mp4" in host.updates[-1][1], (
        f"DOT95_PM1_KMM2_HOLD_LOST: {host.updates}")


def test_control_the_fallback_still_holds_a_known_height_below_the_floor(tmp_path, monkeypatch):
    """porn00-3 is unchanged on the fallback: beside a trailer, a 720p file under 1080 is the named hold."""
    host = _host(tmp_path, monkeypatch)
    with _page(_html(TRAILER, LOW)) as page:
        took = host._fallback_to_page_media(page, SCENE, "clicked candidate fired no download")
    assert took is True and host.transfers == [], (took, host.transfers)
    assert host.updates[-1][0] == "needs_review" and host.updates[-1][1].startswith(
        "Best is 720p (below 1080p)"), f"DOT95_PM1_PORN00_3_HOLD_LOST: {host.updates}"
