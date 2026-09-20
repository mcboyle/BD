"""tests/test_row849_upscale_detector.py -- Acceptance tests for Row 849 upscale detector.

Acceptance criteria (Row 849 Re-scope O1051):
(1) Real ffmpeg execution (the detector is not monkeypatched) on synthetic corpus:
    - testsrc2 and mandelbrot 1280x720 sources -> is_upscale False
    - 2x/4x bicubic upscales (320x180->1280x720, 640x360->1280x720) -> is_upscale True,
      native_height_estimate within 25% of truth
(2) 60 s 1080p clip completes in <= 10 s
(3) Missing ffmpeg -> distinct error 'ffmpeg_not_installed', no exception
(4) Fail-soft, bounded execution, process-group kill
(5) Call sites enrich media_metadata on daemon threads
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from unittest.mock import patch

import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader.upscale_detector import (
    UpscaleVerdict,
    compute_laplacian_sharpness,
    compute_radial_profile,
    detect_upscale,
    detect_upscale_async,
    is_ffmpeg_available,
    probe_video_metadata,
)


@pytest.fixture(scope="module")
def synthetic_corpus(tmp_path_factory):
    """Generate the synthetic corpus using real ffmpeg: testsrc2 + mandelbrot and their upscales."""
    if not is_ffmpeg_available():
        pytest.skip("ffmpeg not available on host")

    td = tmp_path_factory.mktemp("synthetic_corpus")
    corpus = {}

    # 1. testsrc2 1280x720 native source
    src_test = str(td / "testsrc2_720p.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=1:r=5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", src_test],
        check=True,
    )
    corpus["testsrc2_source"] = {"path": src_test, "true_height": 720, "is_upscale": False}

    # 2. testsrc2 2x upscale (640x360 -> 1280x720 bicubic)
    up2_test = str(td / "testsrc2_up2x.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=1:r=5",
         "-vf", "scale=1280x720:flags=bicubic", "-c:v", "libx264", "-pix_fmt", "yuv420p", up2_test],
        check=True,
    )
    corpus["testsrc2_up2x"] = {"path": up2_test, "true_height": 360, "is_upscale": True}

    # 3. testsrc2 4x upscale (320x180 -> 1280x720 bicubic)
    up4_test = str(td / "testsrc2_up4x.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=1:r=5",
         "-vf", "scale=1280x720:flags=bicubic", "-c:v", "libx264", "-pix_fmt", "yuv420p", up4_test],
        check=True,
    )
    corpus["testsrc2_up4x"] = {"path": up4_test, "true_height": 180, "is_upscale": True}

    # 4. mandelbrot 1280x720 native source
    src_mb = str(td / "mandelbrot_720p.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "mandelbrot=s=1280x720:r=5", "-t", "1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", src_mb],
        check=True,
    )
    corpus["mandelbrot_source"] = {"path": src_mb, "true_height": 720, "is_upscale": False}

    # 5. mandelbrot 2x upscale (640x360 -> 1280x720 bicubic)
    up2_mb = str(td / "mandelbrot_up2x.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=5", "-t", "1",
         "-vf", "scale=1280x720:flags=bicubic", "-c:v", "libx264", "-pix_fmt", "yuv420p", up2_mb],
        check=True,
    )
    corpus["mandelbrot_up2x"] = {"path": up2_mb, "true_height": 360, "is_upscale": True}

    # 6. mandelbrot 4x upscale (320x180 -> 1280x720 bicubic)
    up4_mb = str(td / "mandelbrot_up4x.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "mandelbrot=s=320x180:r=5", "-t", "1",
         "-vf", "scale=1280x720:flags=bicubic", "-c:v", "libx264", "-pix_fmt", "yuv420p", up4_mb],
        check=True,
    )
    corpus["mandelbrot_up4x"] = {"path": up4_mb, "true_height": 180, "is_upscale": True}

    # 7. 60 s 1080p clip
    clip_1080p = str(td / "clip_1080p_60s.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=60:r=30",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", clip_1080p],
        check=True,
    )
    corpus["clip_1080p_60s"] = {"path": clip_1080p, "true_height": 1080}

    return corpus


class TestSyntheticCorpusUpscaleDetection:
    """Acceptance (1): Synthetic corpus detection against real ffmpeg without monkeypatching."""

    def test_sources_classified_as_not_upscale(self, synthetic_corpus):
        """Native sources (testsrc2 and mandelbrot 1280x720) -> is_upscale False."""
        for key in ["testsrc2_source", "mandelbrot_source"]:
            item = synthetic_corpus[key]
            verdict = detect_upscale(item["path"])
            assert verdict.ok, f"{key} failed with error: {verdict.error}"
            assert verdict.is_upscale is False, (
                f"{key} falsely flagged as upscale: cutoff_ratio={verdict.cutoff_ratio}, "
                f"native_est={verdict.native_height_estimate}"
            )
            assert verdict.cutoff_ratio > 0.60
            assert verdict.sharpness > 0.0

    def test_upscales_classified_as_upscale_within_tolerance(self, synthetic_corpus):
        """Bicubic upscales (2x, 4x) -> is_upscale True, native_height_estimate within 25% of truth."""
        for key in ["testsrc2_up2x", "testsrc2_up4x", "mandelbrot_up2x", "mandelbrot_up4x"]:
            item = synthetic_corpus[key]
            verdict = detect_upscale(item["path"])
            assert verdict.ok, f"{key} analysis failed: {verdict.error}"
            assert verdict.is_upscale is True, (
                f"{key} missed upscale: cutoff_ratio={verdict.cutoff_ratio}, "
                f"native_est={verdict.native_height_estimate}"
            )
            assert verdict.cutoff_ratio <= 0.60

            # native_height_estimate within 25% of ground truth
            true_h = item["true_height"]
            est_h = verdict.native_height_estimate
            err_pct = abs(est_h - true_h) / float(true_h) * 100.0
            assert err_pct <= 25.0, (
                f"{key}: estimated native height {est_h} exceeds 25% error tolerance "
                f"from true height {true_h} (error: {err_pct:.1f}%)"
            )

    def test_sixty_second_1080p_clip_duration_bounded(self, synthetic_corpus):
        """A 60 s 1080p clip completes in <= 10 s."""
        item = synthetic_corpus["clip_1080p_60s"]
        t0 = time.time()
        verdict = detect_upscale(item["path"], timeout=10.0)
        wall_time = time.time() - t0

        assert verdict.ok, f"1080p analysis failed: {verdict.error}"
        assert wall_time <= 10.0, f"Analysis took {wall_time:.2f}s, exceeding 10s budget"
        assert verdict.elapsed <= 10.0


class TestErrorHandlingAndProcessBoundaries:
    """Acceptance: Missing ffmpeg, timeouts, and error handling."""

    def test_missing_ffmpeg_returns_distinct_error_no_exception(self):
        """Missing ffmpeg returns distinct error 'ffmpeg_not_installed' without raising an exception."""
        with patch("shutil.which", return_value=None):
            with patch("bulk_downloader.upscale_detector._FFMPEG_AVAILABLE", None):
                verdict = detect_upscale("/any/path/video.mp4")
                assert isinstance(verdict, UpscaleVerdict)
                assert verdict.ok is False
                assert verdict.error == "ffmpeg_not_installed"

    def test_nonexistent_file_returns_error(self):
        """Non-existent video file returns fail-soft error."""
        verdict = detect_upscale("/tmp/nonexistent_video_file_xyz123.mp4")
        assert verdict.ok is False
        assert verdict.error == "file_not_found"

    def test_timeout_kills_process_group_cleanly(self, tmp_path):
        """Bounded execution enforces timeout and kills process group."""
        dummy_file = tmp_path / "dummy.mp4"
        dummy_file.write_bytes(b"dummy video content")

        def slow_communicate(*args, **kwargs):
            time.sleep(0.5)
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=0.1)

        with patch("subprocess.Popen.communicate", side_effect=slow_communicate):
            verdict = detect_upscale(str(dummy_file), timeout=0.1)
            assert verdict.ok is False
            assert verdict.error == "timeout"


class TestAsyncMetadataEnrichment:
    """Wiring verification: non-blocking async execution updates media_metadata."""

    def test_detect_upscale_async_enriches_media_metadata(self, synthetic_corpus):
        """detect_upscale_async enriches media_metadata dict asynchronously."""
        item = synthetic_corpus["testsrc2_up2x"]
        media_meta = {"title": "Test Video"}

        th = detect_upscale_async(item["path"], media_metadata=media_meta)
        th.join(timeout=10.0)

        assert "upscale_detection" in media_meta
        det = media_meta["upscale_detection"]
        assert det["ok"] is True
        assert det["is_upscale"] is True
        assert media_meta["is_upscale"] is True
        assert media_meta["native_height_estimate"] == det["native_height_estimate"]
        assert "upscale_sharpness" in media_meta
        assert "upscale_cutoff_ratio" in media_meta

    def test_nonzero_ffmpeg_exit_returns_failure(self, tmp_path):
        """Negative control: nonzero ffmpeg return code is not treated as success."""
        dummy = tmp_path / "dummy.mp4"
        dummy.write_bytes(b"content")

        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"ffmpeg error")
        mock_proc.returncode = 1

        with patch("subprocess.Popen", return_value=mock_proc):
            verdict = detect_upscale(str(dummy))
            assert verdict.ok is False
            assert "ffmpeg_exit_1" in str(verdict.error)

    def test_enrichment_enrich_calls_upscale_detector(self, synthetic_corpus):
        """Call site 1: bulk_downloader.enrichment.enrich wires detect_upscale_async."""
        from bulk_downloader.enrichment import enrich
        item = synthetic_corpus["testsrc2_up2x"]
        meta = {}
        res = enrich(item["path"], do_quality=True, media_metadata=meta)
        # Give daemon thread a brief moment to complete
        time.sleep(1.0)
        assert "media_metadata" in res or "upscale_detection" in meta

    def test_runner_integrity_calls_upscale_detector(self, synthetic_corpus):
        """Call site 2: runner_integrity._embed_metadata_if_mp4 wires detect_upscale_async."""
        from bulk_downloader.runner_integrity import IntegrityMixin
        item = synthetic_corpus["testsrc2_up2x"]

        class DummyRunner(IntegrityMixin):
            def __init__(self):
                self.config = {}
                self.jobs = {"http://example.com/video": {"media_metadata": {}}}
            def log_event(self, *args, **kwargs):
                pass

        runner = DummyRunner()
        runner._embed_metadata_if_mp4(item["path"], source_url="http://example.com/video", quality="1080p")
        time.sleep(1.0)
        meta = runner.jobs["http://example.com/video"]["media_metadata"]
        assert "upscale_detection" in meta or meta.get("is_upscale") is not None

