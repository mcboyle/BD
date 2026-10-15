"""dp13-hls-oserror (NOTE-D2-D-dp13-hls-0140Z.md): dl95-hls-variant-lowest-1 r2 added a pass-only DP-13 handler.

The page-media path reads a landed HLS master body (_try_spa_api_media_extractor, body_kind "manifest") to name its
program.  r2 wrapped that read in `except OSError: pass`, so an unreadable master silently became the unnamed
(first-variant) transfer.  Contract after the fix: the failure is an event naming the path and the error; the
behaviour is unchanged -- program None, the segmented fallback still runs.
"""
from __future__ import annotations

import builtins
import threading
from pathlib import Path

import pytest

from bulk_downloader import runner_extractors

BD_GATE_SCOPE = "module"

SCENE = "https://ok.xxx/video/793009/"
OPTION = "http://127.0.0.1:9/v/scene_720p.mp4"
MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=360000,RESOLUTION=640x360,CODECS="avc1.4d401f"\n360/index.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=480000,RESOLUTION=854x480,CODECS="avc1.4d401f"\n480/index.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=720000,RESOLUTION=1280x720,CODECS="avc1.4d401f"\n720/index.m3u8\n'
).encode()


class _Page:
    def __init__(self, media):
        self.media, self.url = media, SCENE

    def evaluate(self, _js, *a):
        return list(self.media)


class _Refused:
    ok, error, error_detail, bytes_written = False, "stub", "", 0


class _Stub:
    site_id = "dp13hlsread"

    def __init__(self, tmp_path):
        self.config = {"name": "ok", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {SCENE: {"force_download": True}}
        self._stop = threading.Event()
        self.updates, self.events, self.segmented = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        Path(output_path).write_bytes(MASTER)
        return True

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append((manifest_url, kwargs.get("input_format"), kwargs.get("program")))
        return _Refused()

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    return lambda: type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(tmp_path)


def _refuse_second_read(monkeypatch, root):
    """The landed body under *root* opens once (the 512 B _landed_body_kind sniff); the master read is refused."""
    seen = {}

    def guarded(file, mode="r", *a, **k):
        p = str(file)
        if "r" in mode and p.startswith(str(root)):
            seen[p] = seen.get(p, 0) + 1
            if seen[p] > 1:
                raise PermissionError(13, "Permission denied", p)
        return builtins.open(file, mode, *a, **k)

    monkeypatch.setattr(runner_extractors, "open", guarded, raising=False)
    return seen


def test_control_a_readable_master_is_ranked(make):
    """Positive control: the same body, readable, names the 720p program (2) -- the read under test is live."""
    r = make()
    r._try_spa_api_media_extractor(SCENE, _Page([OPTION]))
    assert r.segmented == [(OPTION, "hls", 2)], (r.segmented, r.events)
    assert not [k for k, _ in r.events if k == "spa_api_hls_master_unread"], r.events


def test_unreadable_master_is_said_and_falls_back_unnamed(make, tmp_path, monkeypatch):
    r = make()
    seen = _refuse_second_read(monkeypatch, tmp_path / "dl")
    r._try_spa_api_media_extractor(SCENE, _Page([OPTION]))
    assert list(seen.values()) == [2], f"the master body was not opened twice: {seen}"
    said = [m for k, m in r.events if k == "spa_api_hls_master_unread"]
    assert said, f"DP13_HLS_MASTER_READ_SILENT: master read refused, nothing logged; events={r.events}"
    assert "PermissionError" in said[0] and str(tmp_path / "dl") in said[0], said
    # Behaviour unchanged: still re-fetched segmented, program left unnamed.
    assert "spa_api_manifest_body" in [k for k, _ in r.events], r.events
    assert r.segmented == [(OPTION, "hls", None)], r.segmented


def _dp13_lines(path):
    import json
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([_sys.executable, str(root / "toolchain" / "bin" / "bd-defect-scan"), "--file", str(path),
                          "--json"], capture_output=True, text=True, check=True, cwd=root).stdout
    return {f["line"] for f in json.loads(out) if f["dp"] == "DP-13"}


def test_master_read_adds_no_swallowed_exception(tmp_path):
    """DP-13 ratchet: the try that reads the master body is not pass-only.  Positive control first."""
    import ast

    control = tmp_path / "control.py"
    control.write_text("def f(p):\n    try:\n        p.x()\n    except Exception:\n        pass\n")
    assert _dp13_lines(control) == {4}, "probe cannot see a pass-only handler"
    src_path = Path(__file__).resolve().parents[1] / "bulk_downloader" / "runner_extractors.py"
    src = src_path.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_try_spa_api_media_extractor")
    handlers = {h.lineno for n in ast.walk(fn) if isinstance(n, ast.Try) and n.body
                and isinstance(n.body[0], ast.With)
                and ast.get_source_segment(src, n.body[0].items[0].context_expr) == 'open(output_path, "rb")'
                for h in n.handlers}
    assert handlers, "the master-body read moved"
    swallowed = handlers & _dp13_lines(src_path)
    assert not swallowed, f"DP13_HLS_MASTER_READ_SWALLOWED: lines {sorted(swallowed)}"
