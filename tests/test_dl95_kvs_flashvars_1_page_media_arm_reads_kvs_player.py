"""dl95-kvs-flashvars-1 (PM ruling E, harness-work/FIX/dl95-porn00-1-bd-worker-B5-B/ASK-scope.md; row evidence
download-95/B6-B/p1/porn00/RESULT.md): a KVS scene page (porn00 and other KVS tubes) declares its files in a
page-global ``flashvars`` -- video_url '/get_file/.../42561.mp4/' ('360p') and video_alt_url
'/get_file/.../42561_720p.mp4/' ('720p') -- and fetches nothing until play. Row 722's page-media/API arm read only
fetched media and API JSON, so with no DOM candidate it found "no download-like options" and the job went to
needs_review with 0 bytes.

The arm now also offers the KVS player's own files, bounded by min_resolution (an option below it, or of unknown
height, is never offered). Fixture pages served by page.route in a local headless chromium; no live site, no
login, no credentials.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://www.fixture-kvs.test"
SCENE_URL = ORIGIN + "/video/fixture-duo-scene/"
F360 = ORIGIN + "/get_file/3/aa/42000/42561/42561.mp4/"
F720 = ORIGIN + "/get_file/3/bb/42000/42561/42561_720p.mp4/"
F1080 = ORIGIN + "/get_file/3/cc/42000/42561/42561_1080p.mp4/"


def _html(flashvars_js):
    return f"""<!doctype html><html><body>
<h1>Fixture Duo Scene</h1><div id="kt_player"></div>
<script>var flashvars = {flashvars_js};</script>
</body></html>"""


PORN00_SHAPE = (
    "{video_id: '42561', video_url: '/get_file/3/aa/42000/42561/42561.mp4/', video_url_text: '360p',"
    " video_alt_url: '/get_file/3/bb/42000/42561/42561_720p.mp4/', video_alt_url_text: '720p',"
    " preview_url: '/contents/videos_screenshots/42000/42561/preview.jpg'}"
)


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
def _page(flashvars_js):
    from playwright.sync_api import sync_playwright

    body = _html(flashvars_js)

    def handler(route, request):
        if request.url == SCENE_URL:
            route.fulfill(status=200, content_type="text/html", body=body)
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_timeout(200)
            yield page
        finally:
            browser.close()


class _Stub:
    site_id = "fixturekvs"

    def __init__(self, tmp_path, min_resolution):
        self.config = {
            "name": "fixture",
            "download_dir": str(tmp_path),
            "min_resolution": min_resolution,
        }
        self.jobs, self.events, self.transfers = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.jobs.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        with open(output_path, "wb") as fh:
            fh.write(b"\x00" * 16)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _runner(tmp_path, monkeypatch, min_resolution):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    return type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path, min_resolution)


def test_page_media_arm_takes_the_kvs_players_720_file(tmp_path, monkeypatch):
    """THE ROW: the porn00-shaped KVS page, site min_resolution 720 -> the player's own 720p file is taken."""
    r = _runner(tmp_path, monkeypatch, 720)
    with _page(PORN00_SHAPE) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, f"KVS flashvars file not taken: events={r.events}"
    assert r.transfers == [F720], r.transfers


def test_min_resolution_bounds_the_kvs_options(tmp_path, monkeypatch):
    """Negative control: at the default 1080 minimum neither 360p nor 720p is taken; no bytes move.
    dl95-porn00-3: the options reach the min_resolution hold, which names them for Approve."""
    r = _runner(tmp_path, monkeypatch, 1080)
    with _page(PORN00_SHAPE) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert r.transfers == [], r.transfers
    assert took is True and r.jobs[-1][0] == "needs_review", r.jobs
    assert r.jobs[-1][1].startswith("Best is 720p (below 1080p) — Approve to force"), r.jobs


def test_highest_kvs_option_wins_and_obfuscated_values_are_skipped(
    tmp_path, monkeypatch
):
    """video_alt_url2 (1080p) beats 720p; a license-obfuscated 'function/0/...' value is never fetched."""
    fv = (
        "{video_url: 'function/0/https://www.fixture-kvs.test/get_file/3/zz/42561_2160p.mp4/',"
        " video_url_text: '2160p',"
        " video_alt_url: '/get_file/3/bb/42000/42561/42561_720p.mp4/', video_alt_url_text: '720p',"
        " video_alt_url2: '/get_file/3/cc/42000/42561/42561_1080p.mp4/', video_alt_url2_text: '1080p'}"
    )
    r = _runner(tmp_path, monkeypatch, 720)
    with _page(fv) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, r.events
    assert r.transfers == [F1080], r.transfers
