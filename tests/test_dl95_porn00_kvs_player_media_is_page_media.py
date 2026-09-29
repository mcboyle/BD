"""dl95-porn00-1-live-1 (O1515 LIVE FAIL, harness-work/DOT95-LANE/live-dl95-porn00-1/LIVE-RESULT-B6-B.md, half 2).

After a click that fires no download, dl95-pussyspace-1 (live on the lane) hands the page's own media to Row 722's
page-media arm. On porn00 that arm cannot see the scene at all: the KVS player binds <video src=
"https://www.porn00.org/get_file/3/<hash>/42000/42561/42561.mp4/?..."> -- the file name is followed by a SLASH --
and spa_media_extract.page_media_candidates (MEDIA_EXT_RE on the whole URL) requires ".mp4" to be followed by "?" or
the end. Same class as dl95-justporn-1 (_direct_media_route). Measured on the real page (rendered 2026-09-29).

Contract after the fix: a KVS ``.../<name>.mp4/`` URL is page media; ``.jpg/`` screenshots and ``.mp4.html`` pages are not.
Fixture: tests/fixtures/dl95_porn00_player_scene.html (real rendered DOM; the <video> src is the page's own flashvars
video_url, see its header).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

URL = "https://www.porn00.org/video/adriana-chechik-black-duo-destroy-babe-s-cunt/"
FIXTURE = Path(__file__).parent / "fixtures" / "dl95_porn00_player_scene.html"
SCENE_MEDIA = ("https://www.porn00.org/get_file/3/49a1c31bce7b54c8cbbb119197fcdffc/42000/42561/42561.mp4/"
               "?v-acctoken=REDACTED")


@contextmanager
def _page():
    html = FIXTURE.read_text(encoding="utf-8")
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_context(java_script_enabled=False).new_page()
            pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html)
                     if r.request.url == URL else r.fulfill(status=204, body=""))
            pg.goto(URL, wait_until="domcontentloaded")
            yield pg
        finally:
            br.close()


@pytest.mark.parametrize("url,is_media", [
    (SCENE_MEDIA, True),
    ("https://www.porn00.org/get_file/3/h/42000/42561/42561_720p.mp4/", True),
    ("https://ok.xxx/get_file/13/1912258e8e056bd2a2289c760e6b8493/785000/785100/785100_720p.mp4/", True),
    ("https://cdn.example.invalid/a/scene.mp4?t=1", True),
    ("https://www.porn00.org/get_file/3/h/42000/42561/screenshots/1.jpg/", False),
    ("https://www.porn00.org/video/a.mp4.html", False),
    ("https://www.porn00.org/video/adriana-chechik/", False),
])
def test_page_media_rule(url, is_media):
    from bulk_downloader.spa_media_extract import page_media_candidates

    assert bool(page_media_candidates(URL, [url])) is is_media, url


def test_the_players_bound_file_is_a_page_media_candidate():
    from bulk_downloader import spa_media_extract as spa

    with _page() as pg:
        media = pg.evaluate(spa.PAGE_MEDIA_JS) or []
    assert SCENE_MEDIA in media, f"precondition: PAGE_MEDIA_JS reads the <video> src, got {media}"
    got = [c["url"] for c in spa.page_media_candidates(URL, media)]
    assert got == [SCENE_MEDIA], f"DL95_KVS_PLAYER_MEDIA_REFUSED: {got}"


def test_the_page_media_arm_takes_the_kvs_file(tmp_path, monkeypatch):
    """End to end through the real Row 722 arm: the file the player binds is the one transferred."""
    from bulk_downloader import runner_extractors as rx

    class _R(rx.ExtractorsMixin):
        site_id = "p00"

        def __init__(self):
            self.config = {"name": "porn00", "download_dir": str(tmp_path / "dl"), "min_resolution": 0}
            self.jobs, self._lock = {}, threading.RLock()
            self.status, self.transfers, self._spa_api_capture = [], [], None

        def _update_job(self, _url, state, message="", **_k):
            self.status.append((state, message))

        def log_event(self, *_a, **_k):
            return None

        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            self.transfers.append(file_url)
            Path(output_path).write_bytes(b"\0" * 64)
            return True

        def _size_on_disk_after_tagging(self, _path, size):
            return size

    monkeypatch.setattr(rx, "db_log", lambda *_a, **_k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *_a, **_k: {})
    runner = _R()
    with _page() as pg:
        took = runner._try_spa_api_media_extractor(URL, pg)
    assert took is True and runner.transfers == [SCENE_MEDIA], (
        f"DL95_KVS_PLAYER_MEDIA_REFUSED: took={took} transfers={runner.transfers}")
