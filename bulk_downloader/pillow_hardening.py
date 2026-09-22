"""Row 1070: Deterministic Pillow & Image Processing Engine Hardening.

Provides secure image processing with decompression bomb prevention, format
whitelisting via magic byte validation, deterministic metadata stripping (EXIF/XMP/ICC),
dimension aspect-ratio clamping, and in-place image file hardening for thumbnail pipelines.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PIL import Image, ImageFile


class PermittedImageFormat(str, Enum):
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"
    GIF = "GIF"


class ImageHardeningError(ValueError):
    """Base exception for image hardening failures."""


class DecompressionBombError(ImageHardeningError):
    """Raised when uncompressed pixel volume exceeds safe thresholds."""


class UnsupportedFormatError(ImageHardeningError):
    """Raised when image format is disallowed or does not match permitted signatures."""


MAGIC_SIGNATURES: list[tuple[bytes, PermittedImageFormat]] = [
    (b"\x89PNG\r\n\x1a\n", PermittedImageFormat.PNG),
    (b"\xff\xd8\xff", PermittedImageFormat.JPEG),
    (b"GIF87a", PermittedImageFormat.GIF),
    (b"GIF89a", PermittedImageFormat.GIF),
]


def detect_image_format_from_magic_bytes(data: bytes) -> PermittedImageFormat | None:
    """Detect image format by examining initial magic byte signatures."""
    if not data or len(data) < 8:
        return None

    for sig, fmt in MAGIC_SIGNATURES:
        if data.startswith(sig):
            return fmt

    # WebP signature: 'RIFF' + 4 bytes size + 'WEBP'
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return PermittedImageFormat.WEBP

    return None


def validate_image_dimensions(
    width: int,
    height: int,
    max_pixels: int = 50_000_000,
) -> bool:
    """Validate that image pixel dimensions are positive and within safe limits."""
    if width <= 0 or height <= 0:
        raise ImageHardeningError(f"Invalid image dimensions: {width}x{height}")

    total_pixels = width * height
    if total_pixels > max_pixels:
        raise DecompressionBombError(
            f"Image dimensions {width}x{height} ({total_pixels} pixels) "
            f"exceed max_pixels limit of {max_pixels}"
        )
    return True


def apply_pillow_hardening(max_pixels: int = 50_000_000) -> None:
    """Apply global environment hardening to Pillow runtime."""
    Image.MAX_IMAGE_PIXELS = max_pixels
    ImageFile.LOAD_TRUNCATED_IMAGES = False


def sanitize_image_bytes(
    data: bytes,
    max_dimension: int = 4096,
    max_pixels: int = 50_000_000,
    target_format: str = "PNG",
) -> bytes:
    """Deterministically sanitize, re-encode, and strip all metadata from an image."""
    fmt = detect_image_format_from_magic_bytes(data)
    if fmt is None:
        raise UnsupportedFormatError("Disallowed or invalid image format signature")

    apply_pillow_hardening(max_pixels=max_pixels)

    try:
        with Image.open(io.BytesIO(data)) as img:
            validate_image_dimensions(img.width, img.height, max_pixels=max_pixels)

            # Determine clean color mode
            is_jpeg = target_format.upper() in ("JPEG", "JPG")
            if is_jpeg:
                mode = "RGB"
            else:
                mode = "RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB"
            converted = img.convert(mode)

            # Aspect-ratio preserving dimension clamping
            w, h = converted.size
            if max(w, h) > max_dimension:
                scale = max_dimension / float(max(w, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resized = converted.resize((new_w, new_h), Image.Resampling.LANCZOS)
            else:
                resized = converted

            # Construct clean canvas without metadata or profiles
            clean_canvas = Image.new(mode, resized.size)
            clean_canvas.paste(resized, (0, 0))

            out = io.BytesIO()
            save_fmt = "JPEG" if is_jpeg else target_format
            clean_canvas.save(out, format=save_fmt, optimize=True)
            return out.getvalue()
    except Exception as exc:
        if isinstance(exc, ImageHardeningError):
            raise
        raise ImageHardeningError(f"Corrupt or unparseable image data: {exc}") from exc


def harden_image_file(
    file_path: str | Path,
    max_dimension: int = 4096,
    max_pixels: int = 50_000_000,
) -> None:
    """Validate, sanitize, and strip metadata from an image file on disk in place."""
    path = Path(file_path)
    if not path.is_file():
        raise ImageHardeningError(f"Image file does not exist: {file_path}")

    raw_bytes = path.read_bytes()
    if not raw_bytes:
        raise ImageHardeningError(f"Image file is empty: {file_path}")

    fmt = detect_image_format_from_magic_bytes(raw_bytes)
    if fmt is None:
        target_fmt = "JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    else:
        target_fmt = fmt.value

    sanitized = sanitize_image_bytes(
        raw_bytes,
        max_dimension=max_dimension,
        max_pixels=max_pixels,
        target_format=target_fmt,
    )
    path.write_bytes(sanitized)


class PillowHardeningEngine:
    """Manages image processing pipeline, validation parameters, and telemetry."""

    def __init__(
        self,
        max_pixels: int = 50_000_000,
        max_dimension: int = 4096,
    ) -> None:
        self.max_pixels = max_pixels
        self.max_dimension = max_dimension
        self._processed_count = 0
        self._rejected_count = 0

    def process_image(self, data: bytes, target_format: str = "PNG") -> bytes:
        try:
            result = sanitize_image_bytes(
                data,
                max_dimension=self.max_dimension,
                max_pixels=self.max_pixels,
                target_format=target_format,
            )
            self._processed_count += 1
            return result
        except ImageHardeningError:
            self._rejected_count += 1
            raise

    def export_telemetry(self) -> dict[str, Any]:
        return {
            "processed_count": self._processed_count,
            "rejected_count": self._rejected_count,
            "max_pixels": self.max_pixels,
            "max_dimension": self.max_dimension,
        }
