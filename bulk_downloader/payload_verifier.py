"""Payload Duration & Size Verification.

Validates downloaded files and byte streams against expected byte counts and
media durations, accurately detecting incomplete downloads, truncated streams,
size overflows, and empty payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Any, Dict, Optional, Union


class PayloadVerificationStatus(str, Enum):
    """Status outcomes for payload size and duration verification."""
    VERIFIED = "verified"
    SIZE_MISMATCH = "size_mismatch"
    SIZE_TRUNCATED = "size_truncated"
    SIZE_OVERFLOW = "size_overflow"
    DURATION_MISMATCH = "duration_mismatch"
    DURATION_TRUNCATED = "duration_truncated"
    PAYLOAD_EMPTY = "payload_empty"
    FILE_NOT_FOUND = "file_not_found"
    UNCHECKED = "unchecked"  # a duration was expected but could not be measured
    ERROR = "error"


@dataclass
class PayloadVerificationResult:
    """Outcome of payload size and duration validation."""
    ok: bool = True
    status: PayloadVerificationStatus = PayloadVerificationStatus.VERIFIED
    actual_size_bytes: int = 0
    expected_size_bytes: Optional[int] = None
    actual_duration_seconds: Optional[float] = None
    expected_duration_seconds: Optional[float] = None
    size_delta_bytes: int = 0
    duration_delta_seconds: Optional[float] = None
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status.value if isinstance(self.status, PayloadVerificationStatus) else str(self.status),
            "actual_size_bytes": self.actual_size_bytes,
            "expected_size_bytes": self.expected_size_bytes,
            "actual_duration_seconds": self.actual_duration_seconds,
            "expected_duration_seconds": self.expected_duration_seconds,
            "size_delta_bytes": self.size_delta_bytes,
            "duration_delta_seconds": self.duration_delta_seconds,
            "error": self.error,
            "metadata": self.metadata,
        }


def _mp4_duration_or_reason(file_path: str | Path) -> tuple[float | None, str]:
    """Lightweight MP4 atom parser for mvhd timescale and duration without ffprobe.

    Returns (seconds, "") or (None, why): a header that cannot be read names the
    failed step for the caller's UNCHECKED reason instead of being swallowed."""
    try:
        with open(str(file_path), "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                size, name = struct.unpack(">I4s", header)
                header_size = 8
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]
                    header_size = 16
                elif size == 0:
                    break
                if size < header_size:
                    # never advances the reader: a 64-bit size of 0 re-read the same atom forever
                    return None, (f"mp4 atom {name.decode('latin-1')!r} declares {size} bytes, "
                                  f"under its {header_size}-byte header")
                content_size = size - header_size

                if name == b"moov":
                    continue  # Dive into moov container
                elif name == b"mvhd":
                    mvhd_data = f.read(min(content_size, 32))
                    version = mvhd_data[0]
                    if version == 1:
                        # version/flags(4) creation(8) modification(8) timescale(4) duration(8)
                        if len(mvhd_data) >= 32:
                            timescale, duration = struct.unpack(">IQ", mvhd_data[20:32])
                            if timescale > 0:
                                return float(duration) / float(timescale), ""
                    else:
                        if len(mvhd_data) >= 20:
                            timescale, duration = struct.unpack(">II", mvhd_data[12:20])
                            if timescale > 0:
                                return float(duration) / float(timescale), ""
                    return None, f"mp4 mvhd atom gives no duration (version {version}, {len(mvhd_data)} bytes read)"
                else:
                    f.seek(content_size, os.SEEK_CUR)
    except Exception as exc:  # malformed/unreadable input: named, never a crash of the verify path
        return None, f"mp4 header unreadable ({type(exc).__name__}: {exc})"
    return None, "mp4 header has no mvhd atom"


def _probe_mp4_duration(file_path: Union[str, Path]) -> Optional[float]:
    """mvhd duration in seconds, or None (the reason: _mp4_duration_or_reason)."""
    return _mp4_duration_or_reason(file_path)[0]


def _ffprobe_duration_or_reason(p: Path) -> tuple[float | None, str]:
    """ffprobe's container duration, or (None, the failed step in ffprobe's own words)."""
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return None, "ffprobe not available on system PATH"
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(p),
    ]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=10, check=False)
    except subprocess.TimeoutExpired as exc:
        return None, f"ffprobe duration probe timed out after {exc.timeout}s"
    except Exception as exc:  # exec/decode failure: named, never a crash of the verify path
        return None, f"ffprobe invocation failed ({type(exc).__name__}: {exc})"
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()[-200:] or "no detail"
        return None, f"ffprobe reported error (exit {r.returncode}): {detail}"
    try:
        val = float(out)
    except ValueError:
        return None, f"unparseable ffprobe duration: {out[:40]!r}"
    if not math.isfinite(val):
        return None, f"ffprobe duration is not finite: {out[:40]!r}"
    if val <= 0:
        return None, f"ffprobe duration is not positive: {out[:40]!r}"
    return val, ""


def _duration_or_reason(file_path: str | Path) -> tuple[float | None, str]:
    """probe_file_duration's measurement, or (None, every failed probe step named)."""
    p = Path(file_path)
    if not p.is_file():
        return None, f"not a regular file: {p}"
    failed: list[str] = []

    # Try lightweight MP4 header parsing first
    if p.suffix.lower() in (".mp4", ".m4v", ".mov"):
        dur, why = _mp4_duration_or_reason(p)
        if dur is not None and dur > 0:
            return dur, ""
        failed.append(why or "mp4 mvhd duration is 0")

    # Fallback to ffprobe if installed
    dur, why = _ffprobe_duration_or_reason(p)
    if dur is not None:
        return dur, ""
    failed.append(why)
    return None, "; ".join(failed)


def probe_file_duration(file_path: Union[str, Path]) -> Optional[float]:
    """Probe audio/video container duration via fast atom parsing with ffprobe fallback."""
    return _duration_or_reason(file_path)[0]


class PayloadVerifier:
    """Verifier for content sizes, chunk completeness, and media stream durations."""

    def __init__(
        self,
        size_tolerance_bytes: int = 0,
        duration_tolerance_seconds: float = 1.0,
        duration_tolerance_pct: float = 0.05,
        min_valid_bytes: int = 1,
    ):
        self.size_tolerance_bytes = max(0, int(size_tolerance_bytes))
        self.duration_tolerance_seconds = max(0.0, float(duration_tolerance_seconds))
        self.duration_tolerance_pct = max(0.0, float(duration_tolerance_pct))
        self.min_valid_bytes = max(0, int(min_valid_bytes))

    def verify_size(
        self,
        payload_or_bytes: Union[str, Path, bytes],
        expected_bytes: Optional[int] = None,
        min_bytes: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> PayloadVerificationResult:
        """Validate payload size against exact expected size or bounds."""
        if isinstance(payload_or_bytes, bytes):
            actual_size = len(payload_or_bytes)
        elif isinstance(payload_or_bytes, (str, Path)):
            p = Path(payload_or_bytes)
            if not p.exists():
                return PayloadVerificationResult(
                    ok=False,
                    status=PayloadVerificationStatus.FILE_NOT_FOUND,
                    error=f"File not found: {p}",
                    metadata={"target": str(p)},
                )
            actual_size = p.stat().st_size
        else:
            return PayloadVerificationResult(
                ok=False,
                status=PayloadVerificationStatus.ERROR,
                error=f"Unsupported payload input type: {type(payload_or_bytes).__name__}",
            )

        # Check empty payload
        if actual_size == 0 and (expected_bytes is None or expected_bytes > 0):
            return PayloadVerificationResult(
                ok=False,
                status=PayloadVerificationStatus.PAYLOAD_EMPTY,
                actual_size_bytes=0,
                expected_size_bytes=expected_bytes,
                size_delta_bytes=-(expected_bytes or 0),
                error="Payload is empty (0 bytes)",
            )

        delta = 0
        if expected_bytes is not None:
            delta = actual_size - expected_bytes
            allowed = self.size_tolerance_bytes
            if abs(delta) > allowed:
                if delta < 0:
                    status = PayloadVerificationStatus.SIZE_TRUNCATED
                    err = f"Payload truncated: expected {expected_bytes} bytes, received {actual_size} (delta: {delta})"
                else:
                    status = PayloadVerificationStatus.SIZE_OVERFLOW
                    err = f"Payload exceeded expected size: expected {expected_bytes} bytes, received {actual_size} (delta: +{delta})"
                return PayloadVerificationResult(
                    ok=False,
                    status=status,
                    actual_size_bytes=actual_size,
                    expected_size_bytes=expected_bytes,
                    size_delta_bytes=delta,
                    error=err,
                )

        if min_bytes is not None and actual_size < min_bytes:
            return PayloadVerificationResult(
                ok=False,
                status=PayloadVerificationStatus.SIZE_TRUNCATED,
                actual_size_bytes=actual_size,
                expected_size_bytes=expected_bytes,
                size_delta_bytes=delta,
                error=f"Payload size {actual_size} below minimum required {min_bytes} bytes",
            )

        if max_bytes is not None and actual_size > max_bytes:
            return PayloadVerificationResult(
                ok=False,
                status=PayloadVerificationStatus.SIZE_OVERFLOW,
                actual_size_bytes=actual_size,
                expected_size_bytes=expected_bytes,
                size_delta_bytes=delta,
                error=f"Payload size {actual_size} exceeds maximum allowable {max_bytes} bytes",
            )

        return PayloadVerificationResult(
            ok=True,
            status=PayloadVerificationStatus.VERIFIED,
            actual_size_bytes=actual_size,
            expected_size_bytes=expected_bytes,
            size_delta_bytes=delta,
        )

    def evaluate_duration(
        self,
        actual_duration: Optional[float],
        expected_duration: Optional[float],
    ) -> PayloadVerificationResult:
        """Compare actual media duration against expected duration with tolerance.

        Three states (O1224): no expected duration -> VERIFIED (nothing was
        claimed); expected but unmeasurable -> UNCHECKED, ok=False, with the
        reason; measured -> VERIFIED or a mismatch.
        """
        if expected_duration is not None and actual_duration is None:
            return PayloadVerificationResult(
                ok=False,
                status=PayloadVerificationStatus.UNCHECKED,
                expected_duration_seconds=expected_duration,
                error=f"Media duration could not be measured (expected {expected_duration:.2f}s)",
            )
        if expected_duration is None or actual_duration is None:
            return PayloadVerificationResult(
                ok=True,
                status=PayloadVerificationStatus.VERIFIED,
                actual_duration_seconds=actual_duration,
                expected_duration_seconds=expected_duration,
            )

        delta = actual_duration - expected_duration
        tol = max(self.duration_tolerance_seconds, expected_duration * self.duration_tolerance_pct)

        if abs(delta) > tol:
            if delta < 0:
                status = PayloadVerificationStatus.DURATION_TRUNCATED
                err = f"Media stream duration truncated: expected {expected_duration:.2f}s, got {actual_duration:.2f}s (delta: {delta:.2f}s)"
            else:
                status = PayloadVerificationStatus.DURATION_MISMATCH
                err = f"Media stream duration mismatch: expected {expected_duration:.2f}s, got {actual_duration:.2f}s (delta: +{delta:.2f}s)"
            return PayloadVerificationResult(
                ok=False,
                status=status,
                actual_duration_seconds=actual_duration,
                expected_duration_seconds=expected_duration,
                duration_delta_seconds=delta,
                error=err,
            )

        return PayloadVerificationResult(
            ok=True,
            status=PayloadVerificationStatus.VERIFIED,
            actual_duration_seconds=actual_duration,
            expected_duration_seconds=expected_duration,
            duration_delta_seconds=delta,
        )

    def verify_duration(
        self,
        actual_duration: Optional[float],
        expected_duration: Optional[float],
    ) -> PayloadVerificationResult:
        """Validate media duration against expected bounds."""
        return self.evaluate_duration(actual_duration, expected_duration)


    def verify_payload(
        self,
        path: Union[str, Path],
        expected_bytes: Optional[int] = None,
        expected_duration: Optional[float] = None,
        content_type: Optional[str] = None,
    ) -> PayloadVerificationResult:
        """Complete verification of payload file size and optional media duration."""
        p = Path(path)
        if not p.exists():
            return PayloadVerificationResult(
                ok=False,
                status=PayloadVerificationStatus.FILE_NOT_FOUND,
                error=f"File not found: {p}",
                metadata={"file_path": str(p)},
            )

        size_res = self.verify_size(p, expected_bytes=expected_bytes)
        if not size_res.ok:
            size_res.metadata["file_path"] = str(p)
            return size_res

        actual_dur: float | None = None
        if expected_duration is not None:
            actual_dur, unmeasured = _duration_or_reason(p)
            dur_res = self.evaluate_duration(actual_dur, expected_duration)
            if not dur_res.ok:
                if dur_res.status == PayloadVerificationStatus.UNCHECKED:
                    dur_res.error = f"{dur_res.error}: {unmeasured}"  # which probe step failed, and how
                dur_res.actual_size_bytes = size_res.actual_size_bytes
                dur_res.expected_size_bytes = expected_bytes
                dur_res.size_delta_bytes = size_res.size_delta_bytes
                dur_res.metadata["file_path"] = str(p)
                return dur_res

        return PayloadVerificationResult(
            ok=True,
            status=PayloadVerificationStatus.VERIFIED,
            actual_size_bytes=size_res.actual_size_bytes,
            expected_size_bytes=expected_bytes,
            actual_duration_seconds=actual_dur,
            expected_duration_seconds=expected_duration,
            size_delta_bytes=size_res.size_delta_bytes,
            metadata={"file_path": str(p)},
        )


def verify_payload_duration_and_size(
    path_or_bytes: Union[str, Path, bytes],
    *,
    expected_bytes: Optional[int] = None,
    expected_duration: Optional[float] = None,
    size_tolerance_bytes: int = 0,
    duration_tolerance_seconds: float = 1.0,
) -> PayloadVerificationResult:
    """Functional convenience interface for payload size and duration verification."""
    verifier = PayloadVerifier(
        size_tolerance_bytes=size_tolerance_bytes,
        duration_tolerance_seconds=duration_tolerance_seconds,
    )
    if isinstance(path_or_bytes, bytes):
        return verifier.verify_size(path_or_bytes, expected_bytes=expected_bytes)
    return verifier.verify_payload(
        path_or_bytes,
        expected_bytes=expected_bytes,
        expected_duration=expected_duration,
    )
