"""dl95-cumlouder-3 (O1513, harness-work/DOT95-LANE/live-dl-f3/url2-integrity.txt + LIVE-RESULT-B6-B.md, journal 23:26-23:36Z).

Measured on test2: cumlouder (min_resolution 1080) -> spa-api "chose 0p from page-media; saw: ?p x5", history id 217
done "spa-api source=page-media tier=0", and the landed file is 640x360. Both scene pages carry ONE
<source ... label='1080p' res='720'> (curl capture 2026-09-29), so no better rendition existed: the defect is that
_try_spa_api_media_extractor applied min_resolution to nothing.

Contract after the fix (design note harness-work/FIX/dl95-cumlouder-3-bd-worker-A2-A/QUESTION.md, option A):
  * a KNOWN height below min_resolution is held needs_review before any transfer, as the button path holds it;
  * an UNKNOWN height passes (as on the button path) and the landed file is measured: its real height is recorded
    and a below-minimum result is flagged in the done message, the history message and a spa_api_below_minimum event;
  * force_download and a job whose URL IS the media file are not held.

The landed files are REAL MP4s made by ffmpeg at test time and measured by the real ffprobe.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

BD_GATE_SCOPE = "module"

SCENE = "https://www.cumlouder.com/porn-video/masturbation-expert-gives-a-lesson-on-live-tv/"
# The measured <source> src shape (captured 2026-09-29; the secure= token redacted).
SOURCE = "https://m4cdnst.cumlouder.com/07a97691a99801434b7f82702b14cbc6/07a97691a99801434b7f82702b14cbc6.mp4?secure=REDACTED"


def _mp4(path, w, h):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required to build the landed-file fixture")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=5",
         "-t", "1", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60)
    return path


class _Page:
    def __init__(self, media, url=SCENE):
        self.media, self.url = media, url

    def evaluate(self, _js):
        return list(self.media)


class _Stub:
    site_id = "dl95cl"

    def __init__(self, tmp_path, landed=None, **config):
        self.config = {"name": "cumlouder", "download_dir": str(tmp_path / "dl"), **config}
        self.jobs = {}
        self.updates, self.events, self.transfers, self.logged = [], [], [], []
        self.landed = landed
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return "shot.png"

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        shutil.copyfile(self.landed, output_path)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def make(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    stubs = []
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    def build(landed=None, **config):
        cls = type("StubRunner", (_Stub, rx.ExtractorsMixin), {})
        r = cls(tmp_path, landed=landed, **config)
        monkeypatch.setattr(rx, "db_log", lambda *a, **k: r.logged.append(a))
        stubs.append(r)
        return r

    return build


def test_unknown_height_that_lands_below_the_minimum_is_flagged(make, tmp_path):
    r = make(landed=_mp4(tmp_path / "360.mp4", 640, 360), min_resolution=1080)
    assert r._try_spa_api_media_extractor(SCENE, _Page([SOURCE])) is True
    assert r.transfers == [SOURCE], "an unknown height is not held before the transfer (button-path parity)"
    status, msg = r.updates[-1]
    assert status == "done" and msg.startswith("API/media 360p"), f"DL95_SPA_LANDED_HEIGHT_NOT_RECORDED: {msg!r}"
    assert "below the 1080p minimum" in msg, f"DL95_SPA_BELOW_MIN_NOT_FLAGGED: {msg!r}"
    assert ("spa_api_below_minimum", "landed 360p; minimum 1080p") in r.events, r.events
    assert "tier=360" in r.logged[-1][6] and "below the 1080p minimum" in r.logged[-1][6], r.logged[-1]


def test_unknown_height_that_lands_at_the_minimum_is_not_flagged(make, tmp_path):
    r = make(landed=_mp4(tmp_path / "1080.mp4", 1920, 1080), min_resolution=1080)
    assert r._try_spa_api_media_extractor(SCENE, _Page([SOURCE])) is True
    status, msg = r.updates[-1]
    assert status == "done" and msg.startswith("API/media 1080p") and "below" not in msg, msg
    assert not [e for e in r.events if e[0] == "spa_api_below_minimum"]


def test_known_height_below_the_minimum_is_held_before_any_transfer(make):
    r = make(min_resolution=1080)
    low = "https://m4cdnst.cumlouder.com/abc/abc_480.mp4?secure=REDACTED"
    assert r._try_spa_api_media_extractor(SCENE, _Page([low])) is True
    assert r.transfers == [], f"DL95_SPA_MIN_RES_NOT_APPLIED: transferred {r.transfers}"
    status, msg = r.updates[-1]
    assert status == "needs_review" and msg.startswith("Best is 480p (below 1080p)"), msg
    assert r.logged[-1][3] == "needs_review", r.logged[-1]


def test_forced_job_is_not_held(make, tmp_path):
    r = make(landed=_mp4(tmp_path / "480.mp4", 854, 480), min_resolution=1080)
    low = "https://m4cdnst.cumlouder.com/abc/abc_480.mp4"
    r.jobs[SCENE] = {"force_download": True}
    assert r._try_spa_api_media_extractor(SCENE, _Page([low])) is True
    assert r.transfers == [low] and r.updates[-1][0] == "done"
    assert "below" not in r.updates[-1][1]


def test_direct_media_job_is_not_held(make, tmp_path):
    """Control (the file-examples lane): the queued URL IS the 480p file -- nothing was chosen, nothing to hold."""
    media = "https://file-examples.com/storage/fe/2017/04/file_example_MP4_480_1_5MG.mp4"
    r = make(landed=_mp4(tmp_path / "480.mp4", 854, 480), min_resolution=1080)
    assert r._try_spa_api_media_extractor(media, _Page([media], url=media)) is True
    assert r.transfers == [media] and r.updates[-1][0] == "done", r.updates


def test_zero_minimum_holds_nothing(make, tmp_path):
    r = make(landed=_mp4(tmp_path / "360.mp4", 640, 360), min_resolution=0)
    assert r._try_spa_api_media_extractor(SCENE, _Page([SOURCE])) is True
    assert r.updates[-1] == ("done", r.updates[-1][1]) and "below" not in r.updates[-1][1]
