"""dot95-pm2-dp13-lane-log (PM RULING, harness-work/DOT95-LANE/MERGE-1300.md PM2).

Merging main 27b1e565 into the lane brought dl95-cumlouder-3-gen2delta-dp13's ratchet
(tests/test_dl95_spa_api_min_resolution.py::test_spa_preview_cleanup_adds_no_swallowed_exception):
no ``try: os.remove(output_path)`` in _try_spa_api_media_extractor may be pass-only (DP-13).
Two lane-only handlers were: dl95-ok-3's removal of a body that is not media, and dl95-beeg-2's
removal of a file that landed below its label. Each is narrowed to OSError and says, in a
``spa_api_cleanup`` event naming the file, that the rejected file stayed on disk -- as main's
handler does. The job's outcome is unchanged: the removal failing never changes the verdict.

The landed files are REAL: an HTML body, and an MP4 made by ffmpeg and measured by ffprobe.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

BD_GATE_SCOPE = "module"

SCENE = "https://www.example-ok.test/video/42/"
UNLABELLED = "https://cdn.example-ok.test/0a1b/0a1b.mp4"
LABELLED_1080 = "https://cdn.example-ok.test/0a1b/0a1b_1080.mp4"


def _mp4(path, w, h, seconds=12):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required to build the landed-file fixture")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=1",
         "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60)
    return path


class _Page:
    def __init__(self, media, url=SCENE):
        self.media, self.url = media, url

    def evaluate(self, _js):
        return list(self.media)


class _Stub:
    site_id = "dot95pm2"

    def __init__(self, tmp_path, landed, **config):
        self.config = {"name": "dot95-pm2", "download_dir": str(tmp_path / "dl"), **config}
        self.jobs = {}
        self.updates, self.events = [], []
        self.landed = landed
        self._spa_api_capture = None

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return "shot.png"

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        shutil.copyfile(self.landed, output_path)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def refused_removal(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)

    def refuse(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(rx.os, "remove", refuse)

    def build(landed, **config):
        cls = type("StubRunner", (_Stub, rx.ExtractorsMixin), {})
        return cls(tmp_path, landed, **config)

    return build


def _cleanup(r):
    return [m for k, m in r.events if k == "spa_api_cleanup"]


def test_a_page_body_that_cannot_be_removed_is_said(refused_removal, tmp_path):
    """dl95-ok-3's handler: the .mp4 URL answered with a page; its removal failing is an event."""
    page_body = tmp_path / "login.html"
    page_body.write_bytes(b"<!doctype html><html><body>Please log in</body></html>")
    r = refused_removal(page_body, min_resolution=0)
    assert r._try_spa_api_media_extractor(SCENE, _Page([UNLABELLED])) is False
    assert [k for k, _m in r.events if k == "spa_api_not_media"], r.events
    said = _cleanup(r)
    assert said and "not removed" in said[-1] and "PermissionError" in said[-1], (
        f"DOT95_PM2_OK3_CLEANUP_SILENT: {r.events}")


def test_a_mislabelled_file_that_cannot_be_removed_is_said(refused_removal, tmp_path):
    """dl95-beeg-2's handler: labelled 1080p, landed 480p; its removal failing is an event."""
    r = refused_removal(_mp4(tmp_path / "480.mp4", 854, 480), min_resolution=1080)
    assert r._try_spa_api_media_extractor(SCENE, _Page([LABELLED_1080])) is True
    assert r.updates[-1][0] == "needs_review" and r.updates[-1][1].startswith(
        "Measured 480p (below 1080p minimum); source advertised 1080p"), r.updates
    said = _cleanup(r)
    assert said and "not removed" in said[-1] and "PermissionError" in said[-1], (
        f"DOT95_PM2_BEEG2_CLEANUP_SILENT: {r.events}")
