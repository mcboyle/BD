"""Row 1070: Deterministic Pillow & Image Processing Engine Hardening.

Validates Pillow security hardening against decompression bombs, format
whitelisting with magic byte validation, deterministic metadata stripping (EXIF/XMP),
safe dimension clamping, and thumbnail_sheets production pipeline integration.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

BD_GATE_SCOPE = "module"


def _create_sample_png(width: int = 100, height: int = 80, color: tuple = (255, 0, 0, 255)) -> bytes:
    """Helper to generate in-memory valid PNG image bytes."""
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_jpeg_with_exif(width: int = 100, height: int = 80) -> bytes:
    """Helper to generate a JPEG containing raw EXIF metadata tags."""
    img = Image.new("RGB", (width, height), (200, 100, 50))
    exif = img.getexif()
    exif[0x9286] = "RawExifProbeTag"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_module_exports():
    """RED assertion 1: bulk_downloader.pillow_hardening exports core hardening tools."""
    from bulk_downloader import pillow_hardening

    assert hasattr(pillow_hardening, "PermittedImageFormat")
    assert hasattr(pillow_hardening, "PillowHardeningEngine")
    assert hasattr(pillow_hardening, "validate_image_dimensions")
    assert hasattr(pillow_hardening, "detect_image_format_from_magic_bytes")
    assert hasattr(pillow_hardening, "sanitize_image_bytes")
    assert hasattr(pillow_hardening, "apply_pillow_hardening")
    assert hasattr(pillow_hardening, "harden_image_file")


def test_format_magic_bytes_detection_and_validation():
    """Verify format detection based strictly on image magic headers, not extensions."""
    from bulk_downloader.pillow_hardening import (
        PermittedImageFormat,
        detect_image_format_from_magic_bytes,
    )

    png_bytes = _create_sample_png(20, 20)
    detected = detect_image_format_from_magic_bytes(png_bytes)
    assert detected == PermittedImageFormat.PNG

    jpeg_bytes = _create_jpeg_with_exif(20, 20)
    detected_jpeg = detect_image_format_from_magic_bytes(jpeg_bytes)
    assert detected_jpeg == PermittedImageFormat.JPEG

    # Fraudulent polyglot header / executable bytes
    assert detect_image_format_from_magic_bytes(b"MZ\x90\x00\x03\x00\x00\x00") is None
    assert detect_image_format_from_magic_bytes(b"<html><body>test</body></html>") is None


def test_decompression_bomb_prevention():
    """Verify rejection of images exceeding maximum allowed pixel limits."""
    from bulk_downloader.pillow_hardening import (
        DecompressionBombError,
        validate_image_dimensions,
    )

    # 10,000 x 10,000 = 100,000,000 pixels exceeds default 50M limit
    try:
        validate_image_dimensions(10_000, 10_000, max_pixels=50_000_000)
        assert False, "Expected DecompressionBombError"
    except DecompressionBombError:
        pass

    # Normal size passes
    assert validate_image_dimensions(1920, 1080, max_pixels=50_000_000) is True


def test_exif_metadata_stripping_and_sanitization():
    """Verify removal of EXIF, GPS, and custom metadata from sanitized output."""
    from bulk_downloader.pillow_hardening import sanitize_image_bytes

    # Create PNG with metadata
    img = Image.new("RGBA", (50, 50), (0, 255, 0, 255))
    buf = io.BytesIO()
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Evil Actor")
    info.add_text("GPSCoordinates", "37.7749,-122.4194")
    img.save(buf, format="PNG", pnginfo=info)
    raw_with_meta = buf.getvalue()

    sanitized = sanitize_image_bytes(raw_with_meta)
    reloaded = Image.open(io.BytesIO(sanitized))

    assert "Author" not in reloaded.info
    assert "GPSCoordinates" not in reloaded.info


def test_aspect_ratio_clamping_and_downscaling():
    """Verify oversized images are deterministically scaled within max_dimension bounds."""
    from bulk_downloader.pillow_hardening import sanitize_image_bytes

    # 800 x 400 image clamped to max_dimension 200 -> should become 200 x 100
    large_img = _create_sample_png(800, 400)
    sanitized = sanitize_image_bytes(large_img, max_dimension=200)

    reloaded = Image.open(io.BytesIO(sanitized))
    assert reloaded.size == (200, 100)


def test_deterministic_reencoding_and_byte_equality():
    """Verify identical inputs produce identical byte-for-byte outputs (reproducibility)."""
    from bulk_downloader.pillow_hardening import sanitize_image_bytes

    sample = _create_sample_png(64, 64)
    run1 = sanitize_image_bytes(sample)
    run2 = sanitize_image_bytes(sample)

    assert run1 == run2


def test_corrupt_or_unsupported_format_rejection():
    """Verify rejection of corrupted payloads or disallowed formats."""
    from bulk_downloader.pillow_hardening import (
        ImageHardeningError,
        sanitize_image_bytes,
    )

    corrupt_bytes = b"\x89PNG\r\n\x1a\nCorruptPayloadTrailingJunk"
    try:
        sanitize_image_bytes(corrupt_bytes)
        assert False, "Expected ImageHardeningError on corrupt payload"
    except ImageHardeningError:
        pass


def test_pillow_hardening_engine_lifecycle():
    """Verify engine configuration, stats, and telemetry export."""
    from bulk_downloader.pillow_hardening import PillowHardeningEngine

    engine = PillowHardeningEngine(max_pixels=10_000_000, max_dimension=1024)
    sample = _create_sample_png(30, 30)
    out = engine.process_image(sample)
    assert len(out) > 0

    stats = engine.export_telemetry()
    assert stats["processed_count"] == 1
    assert stats["rejected_count"] == 0


def test_harden_image_file_in_place(tmp_path):
    """Verify harden_image_file strips metadata and downscales file on disk."""
    from bulk_downloader.pillow_hardening import harden_image_file

    raw_jpeg = _create_jpeg_with_exif(400, 200)
    target_file = tmp_path / "test_thumb.jpg"
    target_file.write_bytes(raw_jpeg)

    # Verify initial file has EXIF
    with Image.open(target_file) as im:
        assert 0x9286 in im.getexif()

    harden_image_file(target_file, max_dimension=100)

    # Verify hardened file has stripped EXIF and clamped dimensions
    with Image.open(target_file) as im:
        assert not im.getexif(), "EXIF metadata must be stripped"
        assert im.size == (100, 50), f"Expected clamped dimensions (100, 50), got {im.size}"


def test_thumbnail_sheets_single_thumb_hardens_output_and_strips_metadata(tmp_path, monkeypatch):
    """Verify production thumbnail_sheets.single_thumb hardens output in place.

    Behavioral RED on base: single_thumb generates unhardened image containing raw EXIF.
    On hardened cut: single_thumb invokes harden_image_file, completely stripping EXIF.
    """
    from bulk_downloader import ffmpeg_bin, thumbnail_sheets

    vid_path = tmp_path / "clip.mp4"
    vid_path.write_bytes(b"\x00" * 128)
    out_img = tmp_path / "clip.thumb.jpg"

    raw_jpeg_with_exif = _create_jpeg_with_exif(320, 180)

    def fake_check_call(cmd, **kwargs):
        # Emulate ffmpeg creating raw output file
        out_target = Path(cmd[-1])
        out_target.write_bytes(raw_jpeg_with_exif)

    monkeypatch.setattr(thumbnail_sheets, "is_available", lambda: True)
    monkeypatch.setattr(thumbnail_sheets, "_probe_duration", lambda p: 15.0)
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_bin, "check_call_ffmpeg", fake_check_call)

    res = thumbnail_sheets.single_thumb(str(vid_path), out_path=str(out_img))
    assert res["ok"] is True
    assert out_img.is_file()

    with Image.open(out_img) as im:
        exif = im.getexif()
        assert not exif, "single_thumb output must have EXIF metadata stripped via pillow_hardening"


def test_thumbnail_sheets_single_thumb_rejects_decompression_bomb(tmp_path, monkeypatch):
    """Verify production thumbnail_sheets.single_thumb rejects corrupt/decompression bomb outputs."""
    from bulk_downloader import ffmpeg_bin, thumbnail_sheets

    vid_path = tmp_path / "clip2.mp4"
    vid_path.write_bytes(b"\x00" * 128)
    out_img = tmp_path / "clip2.thumb.jpg"

    def fake_check_call(cmd, **kwargs):
        # Emulate corrupt/invalid image file produced by ffmpeg failure
        out_target = Path(cmd[-1])
        out_target.write_bytes(b"\xff\xd8\xffCorruptedTruncatedPayload")

    monkeypatch.setattr(thumbnail_sheets, "is_available", lambda: True)
    monkeypatch.setattr(thumbnail_sheets, "_probe_duration", lambda p: 15.0)
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_bin, "check_call_ffmpeg", fake_check_call)

    res = thumbnail_sheets.single_thumb(str(vid_path), out_path=str(out_img))
    assert res["ok"] is False
    assert "Corrupt or unparseable" in res["error"] or "Disallowed" in res["error"]
