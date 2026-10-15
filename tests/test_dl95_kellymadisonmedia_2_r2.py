"""dl95-kellymadisonmedia-2 r2 (landing/dl95/RESULT-dl95-kellymadisonmedia-2-live-2351Z.md, .95 run 1062): with the r1
fix in, a logged-out add_url still saved the 1080p TRAILER (56.6 s) as the scene, state done, no needs_review:

  spa_api_player_height: player decoded 1080p: https://tour-content-cdn.kellymadisonmedia.com/episode/
      mp4_1080p_video_file/101518628/1138_maddie_wren_trailer_1080p_pf.
  spa_api_candidate: chose 1080p from scene-stream; saw: 1080p:1080p

r1 filtered previews out of the page-media population only. The trailer's CDN path carries the scene id segment
(101518628), so scene_stream_candidates admitted it as THIS scene's stream, cands was non-empty and the trailer-only
needs_review arm never ran. Now a trailer / teaser / preview / sample file is never a scene stream either.

Local headless chromium, fixture pages served by page.route; no live site, no login, no credentials. The browser's
decoded height (a 64-byte fixture body decodes nothing) is supplied through PLAYER_HEIGHTS_JS, as .95's player did.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://www.fixture-kmm.test"
CDN = "https://tour-content-cdn.fixture-kmm.test"
SCENE_URL = ORIGIN + "/episodes/101518628"
# The live URL shape from run 1062, on a fixture host.
TRAILER = CDN + "/episode/mp4_1080p_video_file/101518628/1138_maddie_wren_trailer_1080p_pf.mp4"
MEMBER = CDN + "/episode/mp4_1080p_video_file/101518628/1138_maddie_wren_1080p_pf.mp4"

# Every <video> reports 1080 decoded lines, as the live player did for the trailer.
PLAYER_1080_JS = "() => Array.from(document.querySelectorAll('video')).map(v => [v.src, 1080])"


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
            page.route("**/*", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_timeout(300)
            yield page
        finally:
            browser.close()


class _Stub:
    site_id = "fixturekmm"

    def __init__(self, tmp_path):
        self.config = {"name": "fixture", "download_dir": str(tmp_path)}
        self.jobs, self.updates, self.events, self.transfers = {}, [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message, extra))

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
    from bulk_downloader import spa_media_extract as spa

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(spa, "PLAYER_HEIGHTS_JS", PLAYER_1080_JS)
    return type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path)


def test_the_cdn_trailer_carrying_the_scene_id_is_not_a_scene_stream():
    """The narrow layer: the live trailer URL is not THIS scene's stream; the member file beside it still is."""
    from bulk_downloader import spa_media_extract as spa

    got = [c["url"] for c in spa.scene_stream_candidates(SCENE_URL, [TRAILER, MEMBER])]
    assert TRAILER not in got, f"kmm-2 r2: trailer admitted as a scene-stream: {got}"
    assert got == [MEMBER], got


def test_a_logged_out_trailer_only_scene_goes_to_needs_review(tmp_path, monkeypatch):
    """THE ROW: logged out, the page plays only the trailer -> needs_review naming it, nothing downloaded."""
    r = _runner(tmp_path, monkeypatch)
    with _page([TRAILER]) as page:
        handled = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert r.transfers == [], f"kmm-2 r2: the trailer was downloaded as the scene: {r.transfers} events={r.events}"
    assert handled is True, "a trailer-only page must be decided here, not fall through"
    assert r.updates and r.updates[-1][0] == "needs_review", r.updates
    assert "1138_maddie_wren_trailer_1080p_pf.mp4" in r.updates[-1][1], r.updates[-1][1]
    assert "needs a login" in r.updates[-1][1], r.updates[-1][1]


@pytest.mark.parametrize("srcs", [[MEMBER], [TRAILER, MEMBER]], ids=["member-only", "trailer-and-member"])
def test_a_real_full_scene_still_completes(tmp_path, monkeypatch, srcs):
    """Positive control: the member file on the same CDN path shape is taken as the scene stream."""
    r = _runner(tmp_path, monkeypatch)
    with _page(srcs) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, r.events
    assert r.transfers == [MEMBER], r.transfers
    assert not any(j[0] == "needs_review" for j in r.updates), r.updates
