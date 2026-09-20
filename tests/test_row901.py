"""Row 901 -- dual-stream audio/video multiplexing pipeline.

Acceptance:
  1. Synchronized multi-track ingestion (video + audio fetched concurrently,
     both complete before muxing starts).
  2. Zero-transcode container multiplexing (ffmpeg -c copy; the elementary
     bitstreams are never re-encoded).
  3. Container audio/video sync verification (post-mux duration check).
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from bulk_downloader.runner_transport import TransportMixin


# ---------------------------------------------------------------------------
# 1. Synchronized concurrent ingestion
# ---------------------------------------------------------------------------

def test_dual_stream_fetch_runs_both_fetchers_concurrently_not_staggered():
    """Both fetchers must be IN FLIGHT at the same time -- a start_barrier
    proves overlap; a staggered (sequential) implementation would deadlock
    here instead of both sides reaching the barrier together."""
    start_barrier = threading.Barrier(2, timeout=5)

    def video_fetch():
        start_barrier.wait()
        return "/tmp/video.m4v"

    def audio_fetch():
        start_barrier.wait()
        return "/tmp/audio.m4a"

    video_path, audio_path = TransportMixin._dual_stream_fetch_concurrent(
        video_fetch, audio_fetch, timeout=5)
    assert video_path == "/tmp/video.m4v"
    assert audio_path == "/tmp/audio.m4a"


def test_dual_stream_fetch_propagates_a_fetcher_failure():
    def video_fetch():
        raise RuntimeError("video stream 404")

    def audio_fetch():
        return "/tmp/audio.m4a"

    with pytest.raises(RuntimeError, match="video stream 404"):
        TransportMixin._dual_stream_fetch_concurrent(video_fetch, audio_fetch, timeout=5)


# ---------------------------------------------------------------------------
# 2. Zero-transcode multiplexing
# ---------------------------------------------------------------------------

def test_mux_streams_lossless_invokes_ffmpeg_with_stream_copy_not_transcode():
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("bulk_downloader.runner_transport.subprocess.run", side_effect=fake_run):
        result = TransportMixin._mux_streams_lossless("/tmp/v.m4v", "/tmp/a.m4a", "/tmp/out.mp4")

    assert result == {"ok": True, "error": None, "output_path": "/tmp/out.mp4"}
    cmd = captured["cmd"]
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy", (
        "must remux with stream copy, never re-encode the bitstream")
    assert "-i" in cmd
    assert "/tmp/v.m4v" in cmd and "/tmp/a.m4a" in cmd and "/tmp/out.mp4" in cmd


def test_mux_streams_lossless_fails_soft_when_ffmpeg_exits_nonzero():
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Invalid data found")

    with patch("bulk_downloader.runner_transport.subprocess.run", side_effect=fake_run):
        result = TransportMixin._mux_streams_lossless("/tmp/v.m4v", "/tmp/a.m4a", "/tmp/out.mp4")

    assert result["ok"] is False
    assert "Invalid data found" in result["error"]
    assert result["output_path"] is None


def test_mux_streams_lossless_fails_soft_when_ffmpeg_is_unavailable():
    with patch("bulk_downloader.runner_transport.ffmpeg_bin.ffmpeg", return_value=None):
        result = TransportMixin._mux_streams_lossless("/tmp/v.m4v", "/tmp/a.m4a", "/tmp/out.mp4")

    assert result == {"ok": False, "error": "ffmpeg not available", "output_path": None}


# ---------------------------------------------------------------------------
# 3. Container audio/video sync verification
# ---------------------------------------------------------------------------

def test_verify_av_sync_reports_ok_when_durations_match_within_tolerance():
    with patch.object(TransportMixin, "_probe_stream_duration_ms",
                       side_effect=[5000, 5120]):
        result = TransportMixin._verify_av_sync("/tmp/out.mp4", tolerance_ms=250)

    assert result == {"ok": True, "degraded": False, "video_ms": 5000,
                       "audio_ms": 5120, "diff_ms": 120, "error": None}


def test_verify_av_sync_reports_not_ok_when_drift_exceeds_tolerance():
    with patch.object(TransportMixin, "_probe_stream_duration_ms",
                       side_effect=[5000, 6500]):
        result = TransportMixin._verify_av_sync("/tmp/out.mp4", tolerance_ms=250)

    assert result["ok"] is False
    assert result["degraded"] is False
    assert result["diff_ms"] == 1500


def test_verify_av_sync_is_degraded_not_falsely_ok_when_ffprobe_cannot_measure():
    with patch.object(TransportMixin, "_probe_stream_duration_ms",
                       side_effect=[None, 5120]):
        result = TransportMixin._verify_av_sync("/tmp/out.mp4", tolerance_ms=250)

    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["diff_ms"] is None


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def test_dual_stream_mux_end_to_end_with_a_real_local_video(tmp_path):
    """Positive control against REAL ffmpeg/ffprobe: build a 2s synthetic
    video-only file and a 2s synthetic audio-only file with the local
    ffmpeg binary, run the real pipeline (concurrent fetch -> mux -> sync
    verify) with no mocking, and confirm both the mux and sync-check are
    genuinely True -- not merely un-mocked-into-True."""
    from bulk_downloader import ffmpeg_bin
    ffmpeg_exe = ffmpeg_bin.ffmpeg()
    if not ffmpeg_exe:
        pytest.skip("no local ffmpeg binary available to run the real probe")

    video_src = tmp_path / "src_video.mp4"
    audio_src = tmp_path / "src_audio.m4a"
    out = tmp_path / "muxed.mp4"

    subprocess.run([ffmpeg_exe, "-y", "-f", "lavfi", "-i",
                     "testsrc=duration=2:size=64x64:rate=10",
                     "-an", str(video_src)], check=True, capture_output=True)
    subprocess.run([ffmpeg_exe, "-y", "-f", "lavfi", "-i",
                     "sine=frequency=440:duration=2",
                     "-vn", str(audio_src)], check=True, capture_output=True)

    result = TransportMixin.dual_stream_mux(
        video_fetch=lambda: str(video_src),
        audio_fetch=lambda: str(audio_src),
        output_path=str(out),
        tolerance_ms=300,
    )

    assert result["ok"] is True
    assert out.exists() and out.stat().st_size > 0
    assert result["sync"]["ok"] is True, result["sync"]
    assert result["sync"]["degraded"] is False

    # Negative control: a 2s video muxed against a 5s audio track must NOT
    # be reported in sync -- proves the sync check discriminates, rather
    # than always returning True.
    audio_src_long = tmp_path / "src_audio_long.m4a"
    subprocess.run([ffmpeg_exe, "-y", "-f", "lavfi", "-i",
                     "sine=frequency=440:duration=5",
                     "-vn", str(audio_src_long)], check=True, capture_output=True)
    out2 = tmp_path / "muxed_mismatched.mp4"
    mismatched = TransportMixin.dual_stream_mux(
        video_fetch=lambda: str(video_src),
        audio_fetch=lambda: str(audio_src_long),
        output_path=str(out2),
        tolerance_ms=300,
    )
    assert mismatched["ok"] is True  # muxing itself still succeeds (zero-transcode copy)
    assert mismatched["sync"]["ok"] is False, mismatched["sync"]


def test_dual_stream_mux_fails_soft_when_a_fetcher_raises(tmp_path):
    def video_fetch():
        raise RuntimeError("connection reset")

    def audio_fetch():
        return "/tmp/a.m4a"

    result = TransportMixin.dual_stream_mux(
        video_fetch=video_fetch, audio_fetch=audio_fetch,
        output_path=str(tmp_path / "out.mp4"))

    assert result["ok"] is False
    assert "connection reset" in result["error"]
    assert result["sync"] is None
