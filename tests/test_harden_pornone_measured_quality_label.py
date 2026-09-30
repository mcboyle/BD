"""The scene's claimed tier must not become the saved quality label."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bulk_downloader import runner_extractors

BD_GATE_SCOPE = "module"
SCENE = "https://pornone.example/vacation/private-vacation/277555873/"
MEDIA = "https://cdn.pornone.example/media/1080p/277555873.mp4"


def _video(path: Path, height: int) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("ffmpeg is required for the measured quality fixture")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc=size={height * 16 // 9}x{height}:rate=1", "-t", "1",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60,
    )
    return path


class _Page:
    url = SCENE

    def evaluate(self, _js):
        return [MEDIA]


class _Stub:
    site_id = "harden-pornone"

    def __init__(self, tmp_path: Path, landed: Path, minimum: int):
        self.config = {
            "name": "pornone", "download_dir": str(tmp_path / "dl"),
            "filename_template": "{filename}{ext}", "min_resolution": minimum,
        }
        self.jobs = {SCENE: {"force_download": False}}
        self._spa_api_capture = None
        self.landed = landed
        self.updates = []
        self.events = []

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return "shot.png"

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        shutil.copyfile(self.landed, output_path)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _run(tmp_path, monkeypatch, height: int, minimum: int,
         title: str = "Private Vacation 720p"):
    monkeypatch.setattr(runner_extractors, "history_title_kwargs",
                        lambda *a, **k: {"title": title})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    runner = type("Runner", (_Stub, runner_extractors.ExtractorsMixin), {})(
        tmp_path, _video(tmp_path / f"landed-{height}.mp4", height), minimum,
    )
    assert runner._try_spa_api_media_extractor(SCENE, _Page()) is True
    return runner


def test_landed_720p_is_saved_as_720p_when_source_claims_1080p(tmp_path, monkeypatch):
    runner = _run(tmp_path, monkeypatch, 720, 720)
    assert runner.updates[-1][0] == "done", runner.updates
    saved = list((tmp_path / "dl").glob("*.mp4"))
    assert len(saved) == 1, saved
    assert "[720p]" in saved[0].name and "[1080p]" not in saved[0].name
    assert runner_extractors._landed_video_height(str(saved[0])) == 720


def test_below_minimum_hold_names_measured_height_first(tmp_path, monkeypatch):
    runner = _run(tmp_path, monkeypatch, 720, 1080)
    status, message, _ = runner.updates[-1]
    assert status == "needs_review", runner.updates
    assert message.startswith("Measured 720p (below 1080p minimum)"), message
    assert "source advertised 1080p" in message, message
    assert not list((tmp_path / "dl").glob("*.mp4"))


def test_matching_1080p_keeps_1080p_label(tmp_path, monkeypatch):
    runner = _run(tmp_path, monkeypatch, 1080, 1080)
    assert runner.updates[-1][0] == "done", runner.updates
    saved = list((tmp_path / "dl").glob("*.mp4"))
    assert len(saved) == 1, saved
    assert "[1080p]" in saved[0].name
    assert runner_extractors._landed_video_height(str(saved[0])) == 1080


def test_site_suffix_does_not_hide_measured_quality_suffix(tmp_path, monkeypatch):
    runner = _run(tmp_path, monkeypatch, 720, 720,
                  title="Private Vacation 720p — PornOne ex vPorn")
    saved = list((tmp_path / "dl").glob("*.mp4"))
    assert runner.updates[-1][0] == "done", runner.updates
    assert len(saved) == 1, saved
    assert "[720p]" in saved[0].name and "1080p" not in saved[0].name
