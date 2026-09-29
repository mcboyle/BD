"""dl95-kellymadisonmedia-2 (harness-work/UIUX-20260928/download-95/A9-A/p1/kellymadisonmedia/RESULT.md#D1, shot
running__0.png; journal harness-work/FIX/dl95-kellymadisonmedia-2-bd-worker-B5-B/journal-d477c086.txt): logged out on a
member site, the page-media arm "chose 1080p from page-media" -- the public trailer 1138_maddie_wren_trailer_1080p_pf.mp4
-- and downloaded it as the scene. The trailer token is "_"-delimited, which a \\b word boundary never splits.

Now a trailer / teaser / preview / sample file is never a page-media candidate, and a page whose only media is such a
file goes to needs_review naming it ("the member file needs a login"), never to a silent done. Local headless chromium,
fixture pages served by page.route; no live site, no login, no credentials.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://www.fixture-km.test"
SCENE_URL = ORIGIN + "/episodes/101518628"
TRAILER = ORIGIN + "/media/1138_maddie_wren_trailer_1080p_pf.mp4"
MEMBER = ORIGIN + "/media/1138_maddie_wren_1080p_pf.mp4"


def _html(srcs):
    videos = "".join(f'<video src="{s}" preload="metadata"></video>' for s in srcs)
    return f"<!doctype html><html><body><h1>Fixture episode</h1>{videos}</body></html>"


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(
            headless=True,
            timeout=20000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except PlaywrightError as e:
        pytest.fail(
            f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}"
        )


@contextmanager
def _page(srcs):
    from playwright.sync_api import sync_playwright

    body = _html(srcs)

    def handler(route, request):
        if request.url == SCENE_URL:
            route.fulfill(status=200, content_type="text/html", body=body)
        elif request.url.endswith(".mp4"):
            route.fulfill(status=200, content_type="video/mp4", body=b"\x00" * 64)
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_timeout(300)
            yield page
        finally:
            browser.close()


class _Stub:
    site_id = "fixturekm"

    def __init__(self, tmp_path):
        self.config = {"name": "fixture", "download_dir": str(tmp_path)}
        self.jobs, self.events, self.transfers = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.jobs.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return "fixture-shot.png"

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        with open(output_path, "wb") as fh:
            fh.write(b"\x00" * 16)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _runner(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    return type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path)


def test_a_trailer_only_page_goes_to_needs_review_naming_it(tmp_path, monkeypatch):
    """THE ROW: the public trailer is not downloaded; the job is needs_review, not done."""
    r = _runner(tmp_path, monkeypatch)
    with _page([TRAILER]) as page:
        handled = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert r.transfers == [], f"the trailer was downloaded as the scene: {r.transfers}"
    assert handled is True, "a trailer-only page must be decided here, not fall through"
    assert r.jobs and r.jobs[-1][0] == "needs_review", r.jobs
    assert "1138_maddie_wren_trailer_1080p_pf.mp4" in r.jobs[-1][1], r.jobs[-1][1]
    assert "needs a login" in r.jobs[-1][1], r.jobs[-1][1]


def test_the_member_file_beside_a_trailer_is_taken(tmp_path, monkeypatch):
    """Negative control: with the member file on the page too, that file is taken and the trailer never is."""
    r = _runner(tmp_path, monkeypatch)
    with _page([TRAILER, MEMBER]) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, r.events
    assert r.transfers == [MEMBER], r.transfers


@pytest.mark.parametrize(
    "url, preview",
    [
        ("https://x.test/m/1138_maddie_wren_trailer_1080p_pf.mp4", True),
        ("https://x.test/previews/scene_1080p.mp4", True),
        ("https://x.test/m/scene-teaser.mp4", True),
        ("https://x.test/m/sample.mp4", True),
        ("https://x.test/m/trailerpark-boys-1080p.mp4", False),
        ("https://x.test/m/1138_maddie_wren_1080p_pf.mp4", False),
        ("https://x.test/get?file=trailer.mp4", False),
    ],
)
def test_preview_tokens_are_delimited_path_segments(url, preview):
    from bulk_downloader import spa_media_extract as spa

    assert spa.is_preview_media(url) is preview, url
