"""tests/test_canvas_capture.py — tests for Row 934 canvas and WebRTC frame capture fallback.

Verifies:
1. Frame recording from canvas animation fixtures (HTMLCanvasElement.captureStream).
2. Steady framerate encoding (framerate stability analysis, jitter bounds).
3. Valid output container creation (pure Python EBML / WebM / Matroska parser & validator).
4. WebRTC stream recording fallback (HTMLVideoElement.srcObject / captureStream).
5. Error handling and corrupted container rejection.
6. In-browser injection script synthesis and options.
7. Playwright end-to-end integration test with local animation fixture.
"""

from __future__ import annotations

# BD_GATE_SCOPE marker: strictly 'module' or 'repo-wide' (Fleet Rule O870h / test_v3_66_939)
BD_GATE_SCOPE = "module"

import json
import math
import struct
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Module under test
from bulk_downloader.canvas_capture import (
    CanvasRecorder,
    CanvasRecordingResult,
    ContainerInfo,
    FramerateAnalysis,
    analyze_framerate_stability,
    build_in_browser_recorder_script,
    create_synthetic_webm_container,
    parse_ebml_header,
    validate_container,
)

# FLEET_RULE 46: browser suites fail closed -- a missing Playwright is an
# ImportError at collection, never a skip
from playwright.sync_api import sync_playwright


# =====================================================================
# Unit Tests: EBML / WebM / Matroska Container Validation
# =====================================================================

def test_ebml_header_parser_valid_synthetic_webm():
    """Verify that a synthetic WebM container parses correctly with EBML header."""
    data = create_synthetic_webm_container(frame_count=10, fps=30.0, doc_type="webm")
    info = validate_container(data)
    assert info.is_valid is True
    assert info.container_format == "webm"
    assert info.ebml_version >= 1
    assert info.doc_type == "webm"
    assert info.has_segment is True
    assert info.data_size == len(data)


def test_ebml_header_parser_valid_synthetic_matroska():
    """Verify that a synthetic Matroska container parses with DocType matroska."""
    data = create_synthetic_webm_container(frame_count=5, fps=25.0, doc_type="matroska")
    info = validate_container(data)
    assert info.is_valid is True
    assert info.container_format == "matroska"
    assert info.doc_type == "matroska"
    assert info.has_segment is True


def test_ebml_header_parser_rejects_empty_data():
    """Verify that empty binary data is rejected."""
    info = validate_container(b"")
    assert info.is_valid is False
    assert "empty" in info.error.lower()


def test_ebml_header_parser_rejects_invalid_magic():
    """Verify that non-EBML magic bytes are rejected."""
    corrupt = b"\x00\x00\x00\x00" + b"some arbitrary non-ebml stream content"
    info = validate_container(corrupt)
    assert info.is_valid is False
    assert "magic" in info.error.lower() or "not valid ebml" in info.error.lower()


def test_ebml_header_parser_rejects_truncated_data():
    """Verify that truncated EBML headers fail validation safely without unhandled exceptions."""
    valid = create_synthetic_webm_container(frame_count=5, fps=30.0)
    truncated = valid[:8]  # Cut off mid-header
    info = validate_container(truncated)
    assert info.is_valid is False
    assert info.error != ""


def test_ebml_header_parser_rejects_unknown_doctype():
    """Verify that an EBML file with an unapproved doctype is rejected as webm/matroska."""
    # Build container with doc_type="arbitrary"
    data = create_synthetic_webm_container(frame_count=2, fps=30.0, doc_type="custom_type")
    info = validate_container(data)
    assert info.is_valid is False
    assert "unsupported doctype" in info.error.lower() or info.container_format == "unknown"


# =====================================================================
# Unit Tests: Framerate Stability Analysis
# =====================================================================

def test_framerate_stability_perfect_stream():
    """Verify framerate analysis on a perfectly periodic 30 fps stream."""
    expected_fps = 30.0
    interval_ms = 1000.0 / expected_fps  # ~33.33ms
    timestamps = [i * interval_ms for i in range(30)]

    analysis = analyze_framerate_stability(timestamps, expected_fps=expected_fps, max_jitter_ms=5.0)
    assert analysis.is_steady is True
    assert pytest.approx(analysis.effective_fps, rel=1e-2) == expected_fps
    assert analysis.frame_count == 30
    assert analysis.jitter_ms < 0.1
    assert analysis.dropped_frames == 0


def test_framerate_stability_jitter_within_tolerance():
    """Verify framerate analysis tolerates realistic jitter under threshold."""
    expected_fps = 30.0
    nominal_interval = 1000.0 / expected_fps
    # Add minor alternating jitter +/- 2ms
    timestamps = [0.0]
    for i in range(1, 40):
        jitter = 2.0 if (i % 2 == 0) else -2.0
        timestamps.append(timestamps[-1] + nominal_interval + jitter)

    analysis = analyze_framerate_stability(timestamps, expected_fps=expected_fps, max_jitter_ms=6.0)
    assert analysis.is_steady is True
    assert pytest.approx(analysis.effective_fps, rel=0.05) == expected_fps
    assert analysis.jitter_ms <= 6.0


def test_framerate_stability_detects_unsteady_jitter_and_drops():
    """Verify framerate analysis catches irregular intervals and large drops."""
    expected_fps = 30.0
    # Jitter exceeding max_jitter_ms, plus a stall of 200ms
    timestamps = [0.0, 33.3, 66.6, 266.6, 300.0, 333.3, 500.0]
    analysis = analyze_framerate_stability(timestamps, expected_fps=expected_fps, max_jitter_ms=10.0)
    assert analysis.is_steady is False
    assert analysis.dropped_frames > 0


def test_framerate_stability_insufficient_frames():
    """Verify behavior when fewer than 2 frames are present."""
    analysis = analyze_framerate_stability([100.0], expected_fps=30.0)
    assert analysis.is_steady is False
    assert analysis.frame_count == 1
    assert "insufficient" in analysis.notes.lower()


# =====================================================================
# Unit Tests: Injection Script Generation & Options
# =====================================================================

def test_injection_script_generation():
    """Verify generation of the in-browser recording JavaScript snippet."""
    script = build_in_browser_recorder_script(
        canvas_selector="#render-surface",
        fps=60,
        mime_type="video/webm;codecs=vp9",
        timeslice_ms=50,
    )
    assert "#render-surface" in script
    assert "captureStream" in script
    assert "MediaRecorder" in script
    assert "window.__bd_canvas_recorder" in script
    assert "video/webm;codecs=vp9" in script


def test_injection_script_supports_webrtc_selector():
    """Verify script supports fallback targeting video elements or custom selectors."""
    script = build_in_browser_recorder_script(
        canvas_selector="video#stream-view",
        fps=25,
        mime_type="video/webm",
    )
    assert "video#stream-view" in script
    assert "srcObject" in script
    assert "captureStream" in script


# =====================================================================
# Unit Tests: CanvasRecordingResult
# =====================================================================

def test_canvas_recording_result_save_and_properties(tmp_path: Path):
    """Verify CanvasRecordingResult properties, container inspection, and saving."""
    data = create_synthetic_webm_container(frame_count=8, fps=24.0)
    result = CanvasRecordingResult(
        data=data,
        mime_type="video/webm",
        duration_ms=333.3,
        frame_count=8,
        fps=24.0,
        metadata={"source": "canvas_fixture"},
    )
    assert result.is_valid is True
    assert result.container_format == "webm"
    assert len(result.data) == len(data)

    out_file = tmp_path / "test_record.webm"
    saved_path = result.save(out_file)
    assert saved_path.exists()
    assert saved_path.read_bytes() == data


# =====================================================================
# Integration Test: Playwright Canvas Animation Recording
# =====================================================================

_CANVAS_ANIMATION_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Canvas Animation Fixture</title></head>
<body>
<canvas id="anim-canvas" width="320" height="240" style="background:#000;"></canvas>
<script>
  const canvas = document.getElementById('anim-canvas');
  const ctx = canvas.getContext('2d');
  let x = 0;
  let frameIndex = 0;

  function draw() {
    ctx.fillStyle = '#10141a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.fillStyle = '#00ffaa';
    ctx.beginPath();
    ctx.arc(40 + (x % 240), 120, 25, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#ffffff';
    ctx.font = '16px monospace';
    ctx.fillText('Frame: ' + frameIndex, 10, 30);

    x += 5;
    frameIndex++;
    window.__current_frame = frameIndex;
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
</script>
</body>
</html>
"""


def test_canvas_recording_in_browser_with_playwright():
    """Integration acceptance test:
    1. Render canvas animation fixture in headless Chromium.
    2. Inject in-browser recorder via CanvasRecorder using HTMLCanvasElement.captureStream().
    3. Record animated frames to WebM container.
    4. Verify steady framerate.
    5. Verify valid WebM container creation.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            try:
                page = context.new_page()
                page.set_content(_CANVAS_ANIMATION_FIXTURE_HTML)
                page.wait_for_function("window.__current_frame > 5", timeout=3000)
                # positive control for the framerate assertion: the page's own
                # requestAnimationFrame rate (frame timestamps are rAF-paced,
                # ~60 Hz in headless Chromium, whatever the encoder's fps)
                raf_hz = page.evaluate("""() => new Promise(resolve => {
                    const t0 = performance.now(); let n = 0;
                    const tick = t => { n++; if (t - t0 >= 500) resolve(n * 1000 / (t - t0)); else requestAnimationFrame(tick); };
                    requestAnimationFrame(tick);
                })""")
                assert 20.0 <= raf_hz <= 250.0, raf_hz

                recorder = CanvasRecorder(
                    fps=30,
                    mime_type="video/webm",
                    timeslice_ms=100,
                )

                # 1. Start recording canvas
                recorder.start(page, selector="#anim-canvas")
                assert recorder.is_recording(page) is True

                # Let animation run for ~400ms to produce frames
                page.wait_for_timeout(400)

                # 2. Stop recording and collect result
                result = recorder.stop(page)
                assert recorder.is_recording(page) is False

                # 3. Acceptance Verification: Valid output container creation
                assert len(result.data) > 0, "Recorded video data must not be empty"
                assert result.is_valid is True, f"Container validation failed: {result.validation_error}"
                assert result.container_format in ("webm", "matroska")

                # 4. Acceptance Verification: Frame recording & steady framerate
                assert result.frame_count > 0, "Must have recorded at least one frame"
                assert result.duration_ms > 0

                # Analyze framerate stability of the recorded stream
                timestamps = result.metadata["frame_timestamps_ms"]
                assert len(timestamps) > 1 and len(timestamps) == result.frame_count, result.metadata.keys()
                analysis = analyze_framerate_stability(timestamps, expected_fps=raf_hz, max_jitter_ms=25.0)
                assert analysis.frame_count == result.frame_count > 1
                assert analysis.is_steady is True, (analysis, raf_hz)
                assert analysis.dropped_frames == 0 and analysis.jitter_ms <= 25.0
                # negative control: the same timestamps judged against a rate they do not have
                assert analyze_framerate_stability(timestamps, expected_fps=raf_hz * 3, max_jitter_ms=25.0).is_steady is False
            finally:
                context.close()
        finally:
            browser.close()


# =====================================================================
# Integration Test: WebRTC / Video Fallback Recording
# =====================================================================

_WEBRTC_VIDEO_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>WebRTC Video Stream Fixture</title></head>
<body>
<canvas id="src-canvas" width="200" height="150" style="display:none;"></canvas>
<video id="stream-player" autoplay playsinline width="200" height="150"></video>
<script>
  // Simulate WebRTC playback by assigning canvas.captureStream() into video.srcObject
  const canvas = document.getElementById('src-canvas');
  const ctx = canvas.getContext('2d');
  let angle = 0;
  function animate() {
    ctx.fillStyle = '#331144';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#ff0055';
    ctx.fillRect(50 + Math.sin(angle) * 30, 50, 40, 40);
    angle += 0.1;
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);

  const stream = canvas.captureStream(25);
  const video = document.getElementById('stream-player');
  video.srcObject = stream;
  video.play().then(() => {
    window.__video_ready = true;
  });
</script>
</body>
</html>
"""


def test_webrtc_video_stream_recording_in_browser():
    """Verify recording fallback from a WebRTC video element (srcObject MediaStream)."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            try:
                page = context.new_page()
                page.set_content(_WEBRTC_VIDEO_FIXTURE_HTML)
                page.wait_for_function("window.__video_ready === true", timeout=3000)

                recorder = CanvasRecorder(fps=25, mime_type="video/webm")
                recorder.start(page, selector="#stream-player")

                page.wait_for_timeout(350)
                result = recorder.stop(page)

                assert len(result.data) > 0
                assert result.is_valid is True
                assert result.container_format in ("webm", "matroska")
            finally:
                context.close()
        finally:
            browser.close()


def test_stop_exposes_snake_case_frame_timestamps_for_the_stability_analysis():
    """E1 (unit): the in-page record is camelCase; CanvasRecorder.stop maps
    frameTimestampsMs -> metadata["frame_timestamps_ms"] (floats), so the
    Python analysis reads real timestamps -- an empty list would have made
    the framerate assertion vacuous."""
    class Page:
        def evaluate(self, script):
            assert script == "window.__bd_canvas_recorder.stop()"
            return {"dataBase64": "", "mimeType": "video/webm", "durationMs": 100.0, "frameCount": 4,
                    "frameTimestampsMs": [0, 33.4, 66.7, 100.1], "byteLength": 0}
    result = CanvasRecorder(fps=30).stop(Page())
    assert result.metadata["frame_timestamps_ms"] == [0.0, 33.4, 66.7, 100.1]
    assert result.metadata["frame_count"] == result.frame_count == 4
    assert result.metadata["frameTimestampsMs"] == [0, 33.4, 66.7, 100.1]   # the raw record is kept
    analysis = analyze_framerate_stability(result.metadata["frame_timestamps_ms"], expected_fps=30.0, max_jitter_ms=25.0)
    assert analysis.frame_count == 4 and analysis.is_steady is True

    class Bare:
        def evaluate(self, script):
            return {"dataBase64": "", "mimeType": "video/webm", "durationMs": 0, "frameCount": 0}
    assert CanvasRecorder(fps=30).stop(Bare()).metadata["frame_timestamps_ms"] == []
