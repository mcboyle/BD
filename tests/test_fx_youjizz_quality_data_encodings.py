"""fx-youjizz-quality (O1568d, spare12 10.0.70.183; results/spare12/youjizz.md, row dl95-youjizz-1): a youjizz scene
page declares every rendition in its inline ``var dataEncodings = [...]`` -- progressive mp4s 240/288/360/480/720/1080
on cdne-mobile.youjizz.com, the same heights as _hls/ m3u8, then "Auto" -- and its player loads the 240p stream. Live:

  spa_api_candidate ...free-use-family-41670211.html: chose 240p from page-media; saw: 240p:page-media | 240p:page-media ...

Every youjizz file was 426x240 (index-f1-v1-a1*.mp4) though the page declared up to 1080p. The page-media arm now also
offers the player's declared mp4 renditions. Fixture page served by page.route in a local headless chromium (the
youjizz hostnames are routed, never fetched); no live site, no login, no credentials.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

SCENE_URL = "https://www.youjizz.com/videos/fixture-scene-41670211.html"
CDN = "https://cdne-mobile.youjizz.com/videos/4/2/e/a/6/42ea6029"
HLS = "https://abre-videos.youjizz.com/_hls/videos/4/2/e/a/6/42ea6029"
SIZES = {240: "426-240", 288: "512-288", 360: "640-360", 480: "854-480", 720: "1280-720", 1080: "1920-1080"}


def _mp4(h):
    return f"{CDN}-{SIZES[h]}-h264.mp4?validfrom=1&validto=2&hash=x"


def _encodings(heights, hls=True):
    enc = [{"quality": str(h), "filename": "//" + _mp4(h).split("://", 1)[1], "name": f"{h}p"} for h in heights]
    if hls:
        enc += [{"quality": str(h), "filename": f"//{HLS.split('://', 1)[1]}-{SIZES[h]}-h264.mp4.m3u8",
                 "name": f"{h}p"} for h in heights]
    enc.append({"quality": "Auto", "filename": f"//{HLS.split('://', 1)[1]}-,426-240,.urlset/master.m3u8",
                "name": "Auto"})
    return json.dumps(enc).replace("/", "\\/")


def _html(encodings_js):
    # the player's own 240p source is what page media sees, as live
    return f"""<!doctype html><html><body><h1>Fixture scene</h1>
<video id="yj-player" src="{_mp4(240)}" preload="none"></video>
<script>
    var dataEncodings = {encodings_js};
    var mp4Encodings = dataEncodings.filter(function(encoding) {{ return true; }});
</script></body></html>"""


@contextmanager
def _page(body):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    def handler(route, request):
        if request.url == SCENE_URL:
            route.fulfill(status=200, content_type="text/html", body=body)
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=20000,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
        except PlaywrightError as e:
            pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route("**/*", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_timeout(200)
            yield page
        finally:
            browser.close()


class _Stub:
    site_id = "fixtureyj"

    def __init__(self, tmp_path, min_resolution):
        self.config = {"name": "youjizz", "download_dir": str(tmp_path), "min_resolution": min_resolution}
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


def _runner(tmp_path, monkeypatch, min_resolution=0):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    return type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path, min_resolution)


def test_the_players_declared_1080_mp4_is_taken_not_the_loaded_240(tmp_path, monkeypatch):
    """THE ROW: the nicole-aniston-shaped page (240..1080 declared, 240 loaded)."""
    r = _runner(tmp_path, monkeypatch)
    with _page(_html(_encodings([240, 288, 360, 480, 720, 1080]))) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, f"events={r.events}"
    assert r.transfers == [_mp4(1080)], (
        f"FX_YOUJIZZ_LOWEST_TAKEN: took {r.transfers} though dataEncodings declares 1080p; events={r.events}")


def test_min_resolution_still_bounds_the_declared_renditions(tmp_path, monkeypatch):
    """The free-use-family shape: 480p is the best declared; a 720 floor keeps it from being taken."""
    r = _runner(tmp_path, monkeypatch, 720)
    with _page(_html(_encodings([240, 288, 360, 480]))) as page:
        r._try_spa_api_media_extractor(SCENE_URL, page)
    assert _mp4(480) not in r.transfers and _mp4(1080) not in r.transfers, r.transfers


def test_parser_prefers_mp4_then_hls_and_is_host_gated():
    from bulk_downloader import spa_media_extract as spa

    got = spa.youjizz_encodings_candidates(SCENE_URL, _html(_encodings([240, 1080])))
    assert [(c["height"], c["url"]) for c in got] == [(240, _mp4(240)), (1080, _mp4(1080))]
    assert all(c["source"] == "youjizz-encodings" for c in got)
    only_hls = json.dumps([{"quality": "720", "filename": f"//{HLS.split('://', 1)[1]}-1280-720-h264.mp4.m3u8",
                            "name": "720p"}])
    got = spa.youjizz_encodings_candidates(SCENE_URL, _html(only_hls))
    assert [c["height"] for c in got] == [720] and got[0]["url"].endswith(".m3u8")


@pytest.mark.parametrize("page_url,html", [
    ("https://www.fixture-other.test/videos/x.html", None),                  # another host: never read
    (SCENE_URL, "<script>var dataEncodings = [{\"quality\": </script>"),     # truncated JSON
    (SCENE_URL, "<script>var somethingElse = [1,2];</script>"),              # no assignment
])
def test_control_nothing_offered(page_url, html):
    from bulk_downloader import spa_media_extract as spa

    body = html if html is not None else _html(_encodings([1080]))
    assert spa.youjizz_encodings_candidates(page_url, body) == []
