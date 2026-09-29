"""fx-ok-extractor (O1567, from results/spare12/ok.md): an ok.ru video page declares its files in the player's
``data-options`` attribute (entity-escaped JSON -> flashvars.metadata JSON -> ``videos`` [{name, url}], names
mobile/lowest/low/sd/hd/full/quad/ultra) and fetches nothing until play. The page has no download button and the
page-media/API arm saw no download-like options, so the job failed page_shape ("site needs onboarding") after a
10-minute yt-dlp timeout, 0 bytes.

The arm now also offers the players own files -- only the players for THIS video (metadata.movie.id == the id in
the job url), never a recommendation's. Fixture page served by page.route in a local headless chromium; no live
site, no login, no credentials.
"""

from __future__ import annotations

import html as _html
import json
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://ok.ru"  # intercepted by page.route; the extractor is gated to the ok.ru host
VIDEO_ID = "15649682885135"
SCENE_URL = f"{ORIGIN}/video/{VIDEO_ID}"
CDN = "https://vd.fixture-okru.test/"


def _file(name):
    return f"{CDN}?expires=1790802702052&srcIp=1.2.3.4&pr=10&srcAg=CHROME&type={name}&id={VIDEO_ID}"


NAMES = ("mobile", "lowest", "low", "sd", "hd")  # ok.ru's own names: 144/240/360/480/720p


def _options(movie_id=VIDEO_ID, names=NAMES):
    meta = {
        "movie": {"id": movie_id, "title": "Fixture clip", "duration": "43"},
        "videos": [{"name": n, "url": _file(n)} for n in names],
        "hlsManifestUrl": CDN + "video.m3u8?cmd=videoPlayerCdn",
    }
    return {"playerId": "VideoPopup_player_" + movie_id,
            "flashvars": {"metadata": json.dumps(meta), "autoplayEnabled": "true"}}


def _page_html(*options):
    tags = "".join(
        f'<div class="vid-card_cnt" data-options="{_html.escape(json.dumps(o), quote=True)}"></div>'
        for o in options)
    return f"<!doctype html><html><body><h1>Fixture</h1>{tags}</body></html>"


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(headless=True, timeout=20000,
                                 args=["--no-sandbox", "--disable-dev-shm-usage"])
    except PlaywrightError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


@contextmanager
def _page(*options):
    from playwright.sync_api import sync_playwright

    body = _page_html(*options)

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
    site_id = "fixtureokru"

    def __init__(self, tmp_path, min_resolution):
        self.config = {"name": "fixture", "download_dir": str(tmp_path),
                       "min_resolution": min_resolution}
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


def test_takes_the_players_hd_file(tmp_path, monkeypatch):
    """THE ROW: an ok.ru page whose player lists mobile..hd, min_resolution 720 -> the 720p ('hd') file."""
    r = _runner(tmp_path, monkeypatch, 720)
    with _page(_options()) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, f"ok.ru player file not taken: events={r.events} jobs={r.jobs}"
    assert r.transfers == [_file("hd")], r.transfers


def test_min_resolution_bounds_the_options(tmp_path, monkeypatch):
    """Negative control: at the default 1080 minimum nothing moves; the options reach the hold, which names 720p."""
    r = _runner(tmp_path, monkeypatch, 1080)
    with _page(_options()) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert r.transfers == [], r.transfers
    assert took is True and r.jobs[-1][0] == "needs_review", r.jobs
    assert r.jobs[-1][1].startswith("Best is 720p (below 1080p) — Approve to force"), r.jobs


def test_a_different_videos_player_is_never_offered(tmp_path, monkeypatch):
    """Negative control: a player whose metadata.movie.id is another video (a recommendation card) is not this job's."""
    r = _runner(tmp_path, monkeypatch, 720)
    with _page(_options(movie_id="999999999999")) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is False and r.transfers == [], (took, r.transfers, r.jobs)


def test_the_highest_named_option_wins(tmp_path, monkeypatch):
    """'full' (1080p) beats 'hd' (720p); ok.ru's names carry the height, the urls do not."""
    r = _runner(tmp_path, monkeypatch, 720)
    with _page(_options(names=NAMES + ("full",))) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert took is True, r.events
    assert r.transfers == [_file("full")], r.transfers
