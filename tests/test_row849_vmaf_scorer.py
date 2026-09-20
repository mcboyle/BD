"""tests/test_row849_vmaf_scorer.py -- Row 849 acceptance tests.

Acceptance criteria:
(1) VMAF score calculation correctly flags low-quality upscales
(2) Non-blocking execution (timeout does not block download completion)
(3) Zero external egress
"""
import socket
import time
from unittest.mock import patch

import pytest

BD_GATE_SCOPE = "module"

# Ensure bulk_downloader can be imported
from bulk_downloader.vmaf_scorer import (
    VmafScoreResult,
    build_vmaf_command,
    is_cuda_available,
    parse_vmaf_output,
    score_video,
    score_video_async,
)


class TestVmafQualityAssessment:
    """Acceptance (1): VMAF score calculation correctly flags low-quality upscales."""

    def test_parse_vmaf_output_formats(self):
        """Test extraction of VMAF scores from ffmpeg log/output formats."""
        # Standard libvmaf output line
        stdout_sample = "[libvmaf @ 0x55d7b2] VMAF score: 48.321456\nframe=100 fps=30"
        score = parse_vmaf_output(stdout_sample)
        assert score == pytest.approx(48.321456)

        # JSON log format
        json_sample = '{"pooled_metrics": {"vmaf": {"mean": 92.45, "harmonic_mean": 91.2}}}'
        score_json = parse_vmaf_output("", log_content=json_sample)
        assert score_json == pytest.approx(92.45)

        # Equals format
        alt_sample = "VMAF score = 65.4"
        assert parse_vmaf_output(alt_sample) == pytest.approx(65.4)

    def test_flags_low_quality_upscale(self, tmp_path):
        """Low VMAF score (e.g. 42.0) on claimed 1080p stream flags is_low_quality and is_upscale."""
        video_file = tmp_path / "upscaled_video.mp4"
        video_file.write_bytes(b"dummy video content")
        metadata = {"resolution": "1920x1080", "claimed_quality": "1080p"}

        mock_output = "[libvmaf] VMAF score: 42.15"
        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", return_value=(0, mock_output, "")):
            result = score_video(
                video_path=video_file,
                quality_threshold=70.0,
                media_metadata=metadata,
            )

        assert result.ok is True
        assert result.vmaf_score == pytest.approx(42.15)
        assert result.is_low_quality is True
        assert result.is_upscale is True
        # Verify stored in media_metadata
        assert "vmaf" in metadata
        assert metadata["vmaf"]["score"] == pytest.approx(42.15)
        assert metadata["vmaf"]["is_low_quality"] is True
        assert metadata["vmaf"]["is_upscale"] is True

    def test_passes_high_quality_video(self, tmp_path):
        """High VMAF score (e.g. 94.5) is not flagged as low quality or upscale."""
        video_file = tmp_path / "high_res_video.mp4"
        video_file.write_bytes(b"dummy video content")
        metadata = {"resolution": "1920x1080"}

        mock_output = "[libvmaf] VMAF score: 94.50"
        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", return_value=(0, mock_output, "")):
            result = score_video(
                video_path=video_file,
                quality_threshold=70.0,
                media_metadata=metadata,
            )

        assert result.ok is True
        assert result.vmaf_score == pytest.approx(94.50)
        assert result.is_low_quality is False
        assert result.is_upscale is False
        assert metadata["vmaf"]["is_low_quality"] is False
        assert metadata["vmaf"]["is_upscale"] is False

    def test_nonzero_ffmpeg_result_is_not_a_quality_score(self, tmp_path):
        video_file = tmp_path / "failed.mp4"
        video_file.write_bytes(b"dummy")
        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", return_value=(1, "VMAF score: 94.5", "failed")):
            result = score_video(video_file)
        assert result.ok is False
        assert result.vmaf_score is None
        assert "exit 1" in result.error

    def test_custom_quality_threshold(self, tmp_path):
        """Custom threshold is respected."""
        video_file = tmp_path / "sample.mp4"
        video_file.write_bytes(b"dummy")

        mock_output = "[libvmaf] VMAF score: 65.0"
        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", return_value=(0, mock_output, "")):
            # With threshold 60, score 65 is NOT low quality
            r1 = score_video(video_file, quality_threshold=60.0)
            assert r1.is_low_quality is False

            # With threshold 70, score 65 IS low quality
            r2 = score_video(video_file, quality_threshold=70.0)
            assert r2.is_low_quality is True


class TestHardwareAcceleration:
    """CUDA hardware acceleration invocation."""

    def test_build_vmaf_command_cuda_and_cpu(self):
        """Command construction uses cuda flags when requested."""
        cmd_cuda = build_vmaf_command("dist.mp4", reference_path="ref.mp4", use_cuda=True)
        assert "-hwaccel" in cmd_cuda
        assert "cuda" in cmd_cuda

        cmd_cpu = build_vmaf_command("dist.mp4", reference_path="ref.mp4", use_cuda=False)
        assert "-hwaccel" not in cmd_cpu

    def test_cuda_detection_reads_nvidia_smi_and_ffmpeg_hwaccels(self, monkeypatch):
        """E2: detection is decided by nvidia-smi + `ffmpeg -hwaccels`, both
        stubbed here: no nvidia-smi -> False; nvidia-smi failing -> False;
        ffmpeg without cuda -> False; both good -> True."""
        from bulk_downloader import vmaf_scorer as vs
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd[0])
            if cmd[0] == "/fake/nvidia-smi":
                return type("R", (), {"returncode": fake_run.smi_rc, "stdout": "GPU 0"})()
            return type("R", (), {"returncode": 0, "stdout": fake_run.hwaccels})()

        monkeypatch.setattr(vs.subprocess, "run", fake_run)
        monkeypatch.setattr(vs, "_resolve_ffmpeg", lambda: "/fake/ffmpeg")
        monkeypatch.setattr(vs.shutil, "which", lambda name: None)
        assert is_cuda_available() is False and calls == []
        monkeypatch.setattr(vs.shutil, "which", lambda name: "/fake/nvidia-smi")
        fake_run.smi_rc, fake_run.hwaccels = 1, "Hardware acceleration methods:\ncuda\n"
        assert is_cuda_available() is False
        fake_run.smi_rc, fake_run.hwaccels = 0, "Hardware acceleration methods:\nvaapi\n"
        assert is_cuda_available() is False
        fake_run.hwaccels = "Hardware acceleration methods:\ncuda\nvaapi\n"
        assert is_cuda_available() is True

    def test_cuda_failure_falls_back_to_cpu_scoring(self, tmp_path):
        """E1: a failing CUDA run retries on the CPU; the result says which
        path scored it; a CPU failure is still a failure."""
        video_file = tmp_path / "v.mp4"
        video_file.write_bytes(b"dummy")
        seen = []

        def fake_vmaf(distorted_path, reference_path=None, use_cuda=False, timeout=30.0, log_path=None):
            seen.append(use_cuda)
            if use_cuda:
                return 1, "", "Cannot load libcuda.so.1"
            return 0, "[libvmaf] VMAF score: 91.5", ""

        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", side_effect=fake_vmaf):
            res = score_video(video_file, use_cuda=True)
        assert seen == [True, False]
        assert res.ok is True and res.vmaf_score == pytest.approx(91.5)
        assert res.hardware_accelerated is False and res.cuda_fallback is True
        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", return_value=(1, "", "boom")):
            res = score_video(video_file, use_cuda=True)
        assert res.ok is False and "exit 1" in res.error
        with patch("bulk_downloader.vmaf_scorer._run_ffmpeg_vmaf", side_effect=fake_vmaf) as m:
            res = score_video(video_file, use_cuda=False)     # CPU path: one call, no fallback
        assert m.call_count == 1 and res.cuda_fallback is False


class TestNonBlockingExecution:
    """Acceptance (2): Non-blocking execution (timeout does not block download completion)."""

    def test_timeout_stops_hanging_process_without_raising(self, tmp_path):
        """A hanging ffmpeg process is aborted within timeout and returns non-blocking result."""
        video_file = tmp_path / "hanging.mp4"
        video_file.write_bytes(b"dummy")
        metadata = {"download_status": "completed"}

        def slow_run(*args, **kwargs):
            timeout = kwargs.get("timeout", 0.5)
            time.sleep(timeout + 0.1)
            raise TimeoutError("Command timed out")

        with patch("bulk_downloader.vmaf_scorer._execute_vmaf_subprocess", side_effect=slow_run):
            t0 = time.time()
            result = score_video(video_file, timeout=0.2, media_metadata=metadata)
            elapsed = time.time() - t0

        assert elapsed < 1.0  # Enforces rapid timeout abort
        assert result.ok is False
        assert result.timed_out is True
        # Download status remains intact
        assert metadata["download_status"] == "completed"
        assert metadata["vmaf"]["timed_out"] is True

    def test_async_scoring_does_not_block_download_completion(self, tmp_path):
        """score_video_async returns immediately allowing download flow to finish."""
        video_file = tmp_path / "async_test.mp4"
        video_file.write_bytes(b"dummy")
        metadata = {"id": "dl_123", "status": "downloading"}

        def delayed_score(*args, **kwargs):
            time.sleep(0.3)
            return VmafScoreResult(
                ok=True,
                vmaf_score=88.0,
                is_low_quality=False,
                is_upscale=False,
                hardware_accelerated=False,
                media_metadata={"vmaf": {"score": 88.0, "is_low_quality": False}},
            )

        with patch("bulk_downloader.vmaf_scorer.score_video", side_effect=delayed_score):
            t0 = time.time()
            worker_thread = score_video_async(
                video_path=video_file,
                media_metadata=metadata,
            )
            return_time = time.time() - t0

            # Must return virtually instantly (<0.08s)
            assert return_time < 0.08
            # Download completion simulation proceeds immediately
            metadata["status"] = "finished"
            assert metadata["status"] == "finished"

            # Wait for background thread to update
            worker_thread.join(timeout=2.0)
            assert "vmaf" in metadata
            assert metadata["vmaf"]["score"] == 88.0


class TestZeroExternalEgress:
    """Acceptance (3): Zero external egress."""

    def test_zero_network_egress_is_enforced_by_ffmpeg_itself(self, tmp_path):
        """E3: the scorer is a subprocess, so a Python socket patch proves
        nothing about it. Isolation is enforced where the process opens
        inputs: (a) a URL input is refused before any command is built, (b)
        the command carries `-protocol_whitelist file,pipe,crypto,data`, which
        makes ffmpeg's own demuxer refuse http/https/rtmp inputs, (c) that
        whitelist is on the REAL command line the scorer executes."""
        from bulk_downloader import vmaf_scorer as vs
        for url in ("http://cdn.example/x.mp4", "https://cdn.example/x.mp4", "rtmp://x/y"):
            with pytest.raises(ValueError, match="local files only"):
                vs.build_vmaf_command(url)
            with pytest.raises(ValueError, match="local files only"):
                vs.build_vmaf_command(tmp_path / "v.mp4", reference_path=url)
        cmd = vs.build_vmaf_command(tmp_path / "v.mp4")
        i = cmd.index("-protocol_whitelist")
        assert cmd[i + 1] == "file,pipe,crypto,data" and "http" not in cmd[i + 1]
        assert i < cmd.index("-i")                          # applies to every input
        executed = []
        with patch("bulk_downloader.vmaf_scorer._execute_vmaf_subprocess",
                   side_effect=lambda c, timeout: executed.append(c) or (0, "[libvmaf] VMAF score: 85.0", "")):
            res = score_video(tmp_path / "v.mp4", use_cuda=False)
        assert res.ok is True and "-protocol_whitelist" in executed[0]
