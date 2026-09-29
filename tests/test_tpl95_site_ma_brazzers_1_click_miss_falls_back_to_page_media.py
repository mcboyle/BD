"""tpl95-site-ma-brazzers-1 (O1517 B1-B): a scored click that fires no download must still try the page's own media.

Measured on test2 (site 1167e615, template user_b1b_site_ma_brazzers_o1517_1790651264): with the learned Aylo MA
template applied, the trigger opens the rendition menu, the wide sweep scores its entries ("4K h264 - 2160p", ...),
none of them is a media link, and every scene ended needs_review "scored ok but no download fired (also tried 1080p:
no download)" with 0 bytes. Phase 1 (no template) landed 4K on the same site: the scrape found no DOM candidate, so
runner.py fell back to _try_spa_api_media_extractor, which took the page's HLS stream. With a template the menu
entries made `best` truthy and that fallback was never consulted.

GREEN: when no download event fires (and the tier fallback found nothing), _do_download consults the page's own
media via _try_spa_api_media_extractor before filing needs_review, held to the tier floor (lens REFUTE R1,
bd-review-shape-A1-A): a trailer/preview-class URL, a file below min_resolution (unless the job is forced) or a
progressive file of unknown height never stands in for the scored tier; an adaptive manifest does. A page with no
qualifying media still files the review.

Hermetic: page/locator doubles in the teenmegaworld tier-fallback test's shape; no browser, network, ffmpeg or site.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader import hls_downloader
from bulk_downloader import runner_extractors as extractors
from bulk_downloader import runner_transport as transport
from bulk_downloader import spa_media_extract

BD_GATE_SCOPE = "module"

PAGE_URL = "https://site-ma.brazzers.example/scene/11524409/wrong-sauna-right-cocks"
MANIFEST = "https://video.brazzers.example/hls/11524409/master.m3u8?validfrom=1&hash=x"
TRAILER = "https://video.brazzers.example/trailers/11524409/trailer_480p.mp4"
PAYLOAD = b"x" * 4096


class _Page:
    url = PAGE_URL

    def __init__(self, media):
        self.media = list(media)
        self.clicked = None
        self.timeouts = []

    def evaluate(self, script, *_args):
        if script == spa_media_extract.PAGE_MEDIA_JS:
            return list(self.media)
        return None                    # popup-grant arm/read/disarm: nothing captured

    def goto(self, _url, **_kw):
        pass

    @contextmanager
    def expect_download(self, *, timeout):
        self.timeouts.append(timeout)
        yield object()
        raise PWTimeout("no download event")    # a rendition-menu entry never fires one

    def title(self):
        return "Wrong Sauna Right Cocks"


class _MenuEntry:
    """An Aylo MA rendition-menu row: a labelled control with no href."""

    def __init__(self, page, label):
        self.page, self.label, self.clicks = page, label, 0

    def evaluate(self, _js):
        return []

    def get_attribute(self, _name):
        return None

    def click(self):
        self.clicks += 1
        self.page.clicked = self


class _HLSResult:
    ok = True
    error = ""
    bytes_written = len(PAYLOAD)


class _Runner(transport.TransportMixin, extractors.ExtractorsMixin):
    def __init__(self, dl_dir, forced=False):
        self.site_id = "site-ma-brazzers"
        self.config = {"name": "site-ma-brazzers", "use_http_dl": True, "verify_hash": False,
                       "verify_integrity": False, "min_resolution": 720, "download_dir": str(dl_dir)}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {PAGE_URL: {"status": "running", "force_download": forced}}
        self.log = logging.getLogger("site-ma-brazzers")
        self.status, self.events, self.hls, self.direct = [], [], [], []

    def _hls_download_guarded(self, _hls, url, output_path, **_kw):
        Path(output_path).write_bytes(PAYLOAD)
        self.hls.append(url)
        return _HLSResult()

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=None):
        Path(output_path).write_bytes(PAYLOAD)
        self.direct.append(file_url)
        return True

    def log_event(self, kind, message="", **_kw):
        self.events.append((kind, message))

    def _screenshot(self, *_args, **_kwargs):
        return ""

    def _update_job(self, _url, state, message="", **_kwargs):
        self.status.append((state, message))

    def _handle_failure(self, _url, message):
        self.status.append(("failed", message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size


def _drive(tmp_path, monkeypatch, media, forced=False):
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(extractors, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(hls_downloader, "is_available", lambda: True)
    page = _Page(media)
    k4, p1080, p720 = (_MenuEntry(page, t) for t in ("4K h264 - 2160p", "1080p h264 - 1080p", "720p h264 - 720p"))
    cands = [{"locator": loc, "score": score, "size": 0, "text": loc.label, "work": 0}
             for loc, score in ((k4, 2160), (p1080, 1080), (p720, 720))]
    best = dict(cands[0], _all_candidates=cands)
    runner = _Runner(tmp_path / "dl", forced=forced)
    runner._do_download(page, object(), PAGE_URL, best, tmp_path / "dl", transport.res_label(2160))
    return runner, (k4, p1080, p720)


def _reviews(runner):
    return [m for s, m in runner.status if s == "needs_review"]


def test_menu_entries_that_fire_nothing_fall_back_to_the_pages_hls_stream(tmp_path, monkeypatch):
    runner, entries = _drive(tmp_path, monkeypatch, [MANIFEST])
    assert entries[0].clicks == 1, "the scored winner is still clicked first"
    assert runner.hls == [MANIFEST], (
        "tpl95-site-ma-brazzers-1: no download event fired and the page's own HLS stream was never tried; "
        f"status={runner.status}")
    assert _reviews(runner) == [], runner.status
    assert runner.status[-1][0] == "done", runner.status
    assert (tmp_path / "dl").is_dir() and any((tmp_path / "dl").iterdir())


def test_a_page_with_no_media_of_its_own_still_files_the_review(tmp_path, monkeypatch):
    runner, _entries = _drive(tmp_path, monkeypatch, [])
    assert runner.hls == []
    review = _reviews(runner)
    assert len(review) == 1 and "scored ok but no download fired" in review[0], runner.status
    assert "also tried" in review[0], review[0]


def _fetched(runner):
    return runner.hls + runner.direct


def test_a_trailer_video_never_stands_in_for_the_scored_tier(tmp_path, monkeypatch):
    runner, _entries = _drive(tmp_path, monkeypatch, [TRAILER])
    assert _fetched(runner) == [], (
        "tpl95-site-ma-brazzers-1: click-miss fallback took a trailer as the download; "
        f"status={runner.status}")
    assert len(_reviews(runner)) == 1, runner.status


def test_a_named_trailer_manifest_is_refused_too(tmp_path, monkeypatch):
    runner, _entries = _drive(tmp_path, monkeypatch,
                              ["https://video.brazzers.example/preview/11524409/index.m3u8"])
    assert _fetched(runner) == [] and len(_reviews(runner)) == 1, runner.status


def test_a_file_below_min_resolution_is_refused(tmp_path, monkeypatch):
    runner, _entries = _drive(tmp_path, monkeypatch, ["https://cdn.brazzers.example/scene/11524409_480p.mp4"])
    assert _fetched(runner) == [] and len(_reviews(runner)) == 1, runner.status


def test_a_progressive_file_of_unknown_height_is_refused(tmp_path, monkeypatch):
    runner, _entries = _drive(tmp_path, monkeypatch, ["https://cdn.brazzers.example/scene/11524409.mp4"])
    assert _fetched(runner) == [] and len(_reviews(runner)) == 1, runner.status


def test_a_forced_job_may_take_a_file_below_min_resolution(tmp_path, monkeypatch):
    low = "https://cdn.brazzers.example/scene/11524409_480p.mp4"
    runner, _entries = _drive(tmp_path, monkeypatch, [low], forced=True)
    assert runner.direct == [low] and _reviews(runner) == [], runner.status


def test_a_file_at_the_floor_is_taken(tmp_path, monkeypatch):
    full = "https://cdn.brazzers.example/scene/11524409_2160p.mp4"
    runner, _entries = _drive(tmp_path, monkeypatch, [full])
    assert runner.direct == [full] and _reviews(runner) == [], runner.status


def test_the_stream_is_taken_past_a_trailer_on_the_same_page(tmp_path, monkeypatch):
    runner, _entries = _drive(tmp_path, monkeypatch, [TRAILER, MANIFEST])
    assert runner.hls == [MANIFEST] and runner.direct == [], runner.status
