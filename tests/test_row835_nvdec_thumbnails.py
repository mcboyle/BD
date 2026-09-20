"""Cut 835: GPU-NVDEC-ACCELERATED-THUMBNAIL-AND-CONTACT-SHEET-EXTRACTION.

SCOPE:
  Add hardware acceleration auto-detection in bulk_downloader/ffmpeg_bin.py
  injecting -hwaccel cuda when available, with automatic fail-soft fallback
  to CPU decode if CUDA driver/memory errors occur; 0 site logins touched.

ACCEPTANCE:
  tests/test_row835_nvdec_thumbnails.py verifying:
  (1) command builder injects -hwaccel cuda when GPU detected,
  (2) automatic fallback to CPU decode on simulated CUDA failure,
  (3) benchmark verifying >10x reduction in CPU decoding time.
"""
from __future__ import annotations

import subprocess
import time
from unittest import mock
import pytest

from bulk_downloader import ffmpeg_bin

BD_GATE_SCOPE = "module"


def test_row835_auto_detection_detects_cuda(monkeypatch):
    """(1) Detection: identifies CUDA capability when driver/hardware is available."""
    ffmpeg_bin.reset()
    # When nvidia-smi / CUDA is present
    monkeypatch.setattr(ffmpeg_bin, "_probe_cuda_support", lambda: True)
    assert ffmpeg_bin.is_cuda_available() is True
    assert ffmpeg_bin.detect_hwaccel() == "cuda"

    ffmpeg_bin.reset()
    # When CUDA is absent
    monkeypatch.setattr(ffmpeg_bin, "_probe_cuda_support", lambda: False)
    assert ffmpeg_bin.is_cuda_available() is False
    assert ffmpeg_bin.detect_hwaccel() is None


def test_row835_command_builder_injects_hwaccel_cuda_when_gpu_detected(monkeypatch):
    """(1) Command builder injects -hwaccel cuda when GPU detected."""
    monkeypatch.setattr(ffmpeg_bin, "is_cuda_available", lambda: True)
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/usr/bin/ffmpeg")

    raw_args = ["-y", "-v", "error", "-ss", "10.5", "-i", "/path/to/video.mp4", "-vframes", "1", "out.jpg"]
    cmd = ffmpeg_bin.build_ffmpeg_command(raw_args)

    assert "-hwaccel" in cmd
    hwaccel_idx = cmd.index("-hwaccel")
    assert cmd[hwaccel_idx + 1] == "cuda"
    i_idx = cmd.index("-i")
    # -hwaccel cuda must appear BEFORE input file (-i)
    assert hwaccel_idx < i_idx
    assert cmd[-1] == "out.jpg"


def test_row835_command_builder_omits_hwaccel_when_gpu_absent(monkeypatch):
    """(1b) Command builder omits -hwaccel when GPU is absent or disabled."""
    monkeypatch.setattr(ffmpeg_bin, "is_cuda_available", lambda: False)
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/usr/bin/ffmpeg")

    raw_args = ["-y", "-v", "error", "-ss", "10.5", "-i", "/path/to/video.mp4", "-vframes", "1", "out.jpg"]
    cmd = ffmpeg_bin.build_ffmpeg_command(raw_args)

    assert "-hwaccel" not in cmd
    assert cmd.count("-i") == 1


def test_row835_automatic_fallback_to_cpu_on_cuda_failure(monkeypatch):
    """(2) Automatic fallback to CPU decode on simulated CUDA failure."""
    monkeypatch.setattr(ffmpeg_bin, "is_cuda_available", lambda: True)
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/usr/bin/ffmpeg")

    calls = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if "-hwaccel" in cmd:
            # Simulate CUDA driver initialization failure
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="CUDA_ERROR_UNKNOWN: OS call failed or Cannot load libcuda.so.1",
            )
        # CPU decode fallback succeeds
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    raw_args = ["-y", "-v", "error", "-ss", "5.0", "-i", "input.mp4", "-vframes", "1", "thumb.jpg"]
    cmd = ffmpeg_bin.build_ffmpeg_command(raw_args)
    assert "-hwaccel" in cmd

    result = ffmpeg_bin.run_ffmpeg(cmd, capture_output=True, text=True)

    assert result.returncode == 0
    # Must have attempted GPU first, then retried CPU
    assert len(calls) == 2
    assert "-hwaccel" in calls[0]
    assert "-hwaccel" not in calls[1]
    assert calls[1][-1] == "thumb.jpg"


# ── FIXER (row835 REFUTE items 1-4) ──────────────────────────────────────
#
# The former "benchmark" test asserted hardcoded arithmetic (15.20/0.28) and
# a synthetic counter; it could not say NO to anything and is replaced. A
# real >10x NVDEC/CPU ratio is a hardware measurement (needs a GPU box and a
# 4K source): UNKNOWN here, never modelled -- see DONE.md for how to measure.


def test_bytes_stderr_from_capture_output_triggers_the_cpu_retry(monkeypatch):
    """Item 1: subprocess.run(capture_output=True) without text=True gives
    BYTES stderr; a CUDA failure there must still fall back to CPU."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if "-hwaccel" in cmd:
            return subprocess.CompletedProcess(cmd, 1, b"", b"CUDA_ERROR_UNKNOWN: device creation failed")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = ffmpeg_bin.run_ffmpeg(["ffmpeg", "-hwaccel", "cuda", "-i", "in.mp4", "out.jpg"], capture_output=True)
    assert proc.returncode == 0
    assert calls == [["ffmpeg", "-hwaccel", "cuda", "-i", "in.mp4", "out.jpg"],
                     ["ffmpeg", "-i", "in.mp4", "out.jpg"]]


def test_non_cuda_failure_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: calls.append(list(cmd)) or
                        subprocess.CompletedProcess(cmd, 1, b"", b"in.mp4: No such file or directory"))
    proc = ffmpeg_bin.run_ffmpeg(["ffmpeg", "-hwaccel", "cuda", "-i", "in.mp4", "out.jpg"], capture_output=True)
    assert proc.returncode == 1 and len(calls) == 1


@pytest.mark.parametrize("head", ["ffmpeg", "/usr/bin/ffmpeg", "/opt/ff/bin/ffmpeg"])
def test_command_builder_never_doubles_the_binary(monkeypatch, head):
    """Item 2: args[0] naming the binary in any spelling is REPLACED by the
    resolved path, not prepended to."""
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/usr/bin/ffmpeg")
    assert ffmpeg_bin.build_ffmpeg_command([head, "-i", "in.mp4"], use_hwaccel=False) == \
        ["/usr/bin/ffmpeg", "-i", "in.mp4"]
    assert ffmpeg_bin.build_ffmpeg_command(["-i", "in.mp4"], use_hwaccel=False) == \
        ["/usr/bin/ffmpeg", "-i", "in.mp4"]


def test_thumbnail_gen_frame_extraction_is_wired_through_the_accelerated_builder(monkeypatch, tmp_path):
    """Item 3: the real thumbnail_gen call site emits -hwaccel cuda when the
    GPU is detected and retries on CPU when the GPU decode fails."""
    from bulk_downloader import thumbnail_gen

    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 16)
    out = tmp_path / "out.jpg"
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if "-hwaccel" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "Cannot load libcuda.so.1")
        out.write_bytes(b"jpg")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_bin, "is_cuda_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(thumbnail_gen, "_probe_duration", lambda *a, **k: 100.0, raising=False)
    result = thumbnail_gen.generate_single_frame(str(src), str(out))
    assert result.ok, result
    assert len(calls) == 2, calls
    assert calls[0][:1] == ["/usr/bin/ffmpeg"] and "-hwaccel" in calls[0] and calls[0][calls[0].index("-hwaccel") + 1] == "cuda"
    assert calls[0].index("-hwaccel") < calls[0].index("-i")
    assert "-hwaccel" not in calls[1] and calls[1][0] == "/usr/bin/ffmpeg"


def test_thumbnail_sheets_are_wired_with_cpu_retry_at_the_check_call_boundary(monkeypatch, tmp_path):
    """Item 3: thumbnail_sheets keeps its subprocess.check_call boundary (row
    440 pins argv[0] there) but the argv now carries -hwaccel cuda when the
    GPU is detected, and a failed GPU decode is retried once on CPU."""
    from bulk_downloader import thumbnail_sheets

    calls = []

    def fake_check_call(cmd, **kwargs):
        calls.append(list(cmd))
        if "-hwaccel" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return 0

    (tmp_path / "in.mp4").write_bytes(b"\x00" * 16)
    (tmp_path / "p.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_bin, "is_cuda_available", lambda: True)
    monkeypatch.setattr(thumbnail_sheets, "_probe_duration", lambda *a, **k: 100.0)
    monkeypatch.setattr(subprocess, "check_call", fake_check_call)
    res = thumbnail_sheets.single_thumb(str(tmp_path / "in.mp4"), out_path=str(tmp_path / "p.jpg"))
    assert res["ok"] is True, res
    assert len(calls) == 2 and calls[0][0] == "/usr/bin/ffmpeg" and "-hwaccel" in calls[0]
    assert "-hwaccel" not in calls[1] and calls[1][0] == "/usr/bin/ffmpeg"

    calls.clear()
    monkeypatch.setattr(subprocess, "check_call",
                        lambda cmd, **k: calls.append(list(cmd)) or (_ for _ in ()).throw(subprocess.CalledProcessError(1, cmd)))
    res = thumbnail_sheets.single_thumb(str(tmp_path / "in.mp4"), out_path=str(tmp_path / "p.jpg"))
    assert res["ok"] is False and res["error"] == "ffmpeg exit 1" and len(calls) == 2
