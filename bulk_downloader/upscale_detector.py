"""bulk_downloader.upscale_detector -- Spectral upscale detection for resolution verification.

Analyzes 2-D FFT frequency spectra of sampled video frames to determine whether
an ostensibly high-resolution video is an upscaled lower-resolution source.
Upscaled content exhibits a sharp radial spectral power cutoff below the Nyquist
frequency of the stored container resolution.

Acceptance criteria (Row 849):
(1) radial spectral energy profile; cutoff radius where energy drops below fixed fraction of low band;
    upscaled video has cutoff << Nyquist (ratio = native_height_estimate / stored_height)
(2) Laplacian variance as sharpness
(3) is_upscale when cutoff ratio <= 0.6; native_height_estimate within 25% of truth
(4) Fail-soft, bounded (<=10 s, process-group kill), distinct ffmpeg_not_installed error
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np


@dataclass
class UpscaleVerdict:
    """Outcome of spectral upscale analysis."""

    ok: bool = True
    is_upscale: bool = False
    cutoff_ratio: float = 1.0
    native_height_estimate: int = 0
    stored_height: int = 0
    stored_width: int = 0
    sharpness: float = 0.0
    elapsed: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_FFMPEG_AVAILABLE: Optional[bool] = None


def is_ffmpeg_available() -> bool:
    """Return True if ffmpeg and ffprobe both resolve (honours the ffmpeg_path pin)."""
    global _FFMPEG_AVAILABLE
    if _FFMPEG_AVAILABLE is None:
        from . import ffmpeg_bin          # MOD-4: one resolver, honours the pin
        _FFMPEG_AVAILABLE = ffmpeg_bin.both_available()
    return _FFMPEG_AVAILABLE


def is_upscale_detector_available() -> bool:
    """Return True if upscale detector dependencies (ffmpeg, ffprobe, numpy) are present."""
    return is_ffmpeg_available()


def probe_video_metadata(path: str, timeout: float = 5.0) -> Tuple[int, int, float, int]:
    """Probe width, height, duration, and frame count via ffprobe."""
    from . import ffmpeg_bin          # MOD-4: the executed binary is the pinned one
    ffprobe = ffmpeg_bin.ffprobe() if is_ffmpeg_available() else None
    if not ffprobe:
        raise FileNotFoundError("ffprobe not found")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration,nb_frames",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            p.communicate(timeout=1.0)
        except Exception:
            pass
        return 0, 0, 0.0, 0

    if p.returncode != 0:
        return 0, 0, 0.0, 0

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        streams = data.get("streams", [])
        if not streams:
            return 0, 0, 0.0, 0
        s0 = streams[0]
        width = int(s0.get("width") or 0)
        height = int(s0.get("height") or 0)
        dur_str = s0.get("duration") or data.get("format", {}).get("duration") or "0"
        duration = float(dur_str)
        nb_frames_str = s0.get("nb_frames") or "0"
        nb_frames = int(nb_frames_str)
        return width, height, duration, nb_frames
    except Exception:
        return 0, 0, 0.0, 0


def extract_sampled_frames(
    path: str,
    n_frames: int = 5,
    size: int = 512,
    timeout: float = 10.0,
    nb_frames: int = 0,
) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Sample N frames from video using ffmpeg select filter, returning raw gray frames of fixed size."""
    from . import ffmpeg_bin          # MOD-4: the executed binary is the pinned one
    ffmpeg = ffmpeg_bin.ffmpeg() if is_ffmpeg_available() else None
    if not ffmpeg:
        return None, "ffmpeg_not_installed"

    # Frame sampling expression
    if nb_frames > n_frames:
        step = max(1, nb_frames // (n_frames + 1))
        select_expr = f"not(mod(n\\,{step}))"
    else:
        select_expr = "not(mod(n\\,1))"

    # Fixed size 512x512 crop (preserving native un-resampled pixel spatial frequencies)
    vf = f"select='{select_expr}',crop={size}:{size}"

    cmd = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        path,
        "-vf",
        vf,
        "-vframes",
        str(n_frames),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            p.communicate(timeout=1.0)
        except Exception:
            pass
        return None, "timeout"

    if p.returncode != 0:
        return None, f"ffmpeg_exit_{p.returncode}"

    expected_frame_bytes = size * size
    if len(stdout) < expected_frame_bytes:
        return None, "insufficient_frame_data"

    total_frames = len(stdout) // expected_frame_bytes
    actual_frames = min(total_frames, n_frames)
    raw = stdout[: actual_frames * expected_frame_bytes]
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(actual_frames, size, size)
    return frames, None


def compute_radial_profile(frames: np.ndarray, r_max: int = 256) -> np.ndarray:
    """Compute the azimuthally-averaged radial spectral energy profile from 2-D FFT of frames."""
    psds = []
    for f in frames:
        F = np.fft.fftshift(np.fft.fft2(f.astype(float)))
        psds.append(np.abs(F) ** 2)
    avg_psd = np.mean(psds, axis=0)

    cy, cx = r_max, r_max
    y, x = np.ogrid[: 2 * r_max, : 2 * r_max]
    r = np.hypot(x - cx, y - cy).astype(int)

    radial = np.zeros(r_max)
    for ri in range(r_max):
        m = r == ri
        if np.any(m):
            radial[ri] = np.sum(avg_psd[m])
    return radial


def compute_laplacian_sharpness(frames: np.ndarray) -> float:
    """Compute Laplacian variance across frames as sharpness metric."""
    sharpnesses = []
    for f in frames:
        flt = f.astype(float)
        lap = flt[:-2, 1:-1] + flt[2:, 1:-1] + flt[1:-1, :-2] + flt[1:-1, 2:] - 4 * flt[1:-1, 1:-1]
        sharpnesses.append(float(np.var(lap)))
    return float(np.mean(sharpnesses)) if sharpnesses else 0.0


def detect_upscale(
    path: str,
    *,
    n_frames: int = 5,
    size: int = 512,
    cutoff_threshold_ratio: float = 0.6,
    energy_fraction: float = 0.15,
    low_band_range: Tuple[int, int] = (25, 45),
    timeout: float = 10.0,
) -> UpscaleVerdict:
    """Run spectral upscale detection on a video file. Fail-soft, bounded execution."""
    t0 = time.time()

    if not is_ffmpeg_available():
        return UpscaleVerdict(
            ok=False,
            error="ffmpeg_not_installed",
            elapsed=round(time.time() - t0, 3),
        )

    if not os.path.isfile(path):
        return UpscaleVerdict(
            ok=False,
            error="file_not_found",
            elapsed=round(time.time() - t0, 3),
        )

    try:
        width, height, duration, nb_frames = probe_video_metadata(path, timeout=min(5.0, timeout))
        if height <= 0:
            height = 720  # fallback assumption for estimation

        frames, err = extract_sampled_frames(
            path, n_frames=n_frames, size=size, timeout=timeout, nb_frames=nb_frames
        )
        if err is not None or frames is None or len(frames) == 0:
            return UpscaleVerdict(
                ok=False,
                error=err or "no_frames_extracted",
                stored_height=height,
                stored_width=width,
                elapsed=round(time.time() - t0, 3),
            )

        # 1. Radial spectral energy profile
        r_max = size // 2
        radial = compute_radial_profile(frames, r_max=r_max)

        # Find cutoff radius where energy drops below fixed fraction of low-band energy
        l_start, l_end = low_band_range
        low_band = float(np.mean(radial[l_start:l_end])) if len(radial) > l_end else float(np.mean(radial[:r_max // 4]))
        thresh = energy_fraction * low_band

        above = np.where(radial >= thresh)[0]
        above = above[above >= l_start]
        cutoff_r = int(np.max(above)) if len(above) > 0 else 0
        cutoff_ratio = round(cutoff_r / float(r_max), 3)

        # 2. Laplacian variance as sharpness
        sharpness = round(compute_laplacian_sharpness(frames), 2)

        # 3. Verdict
        is_up = cutoff_ratio <= cutoff_threshold_ratio
        native_h_est = int(round(cutoff_ratio * height))
        elapsed = round(time.time() - t0, 3)

        return UpscaleVerdict(
            ok=True,
            is_upscale=is_up,
            cutoff_ratio=cutoff_ratio,
            native_height_estimate=native_h_est,
            stored_height=height,
            stored_width=width,
            sharpness=sharpness,
            elapsed=elapsed,
        )
    except subprocess.TimeoutExpired:
        return UpscaleVerdict(
            ok=False,
            error="timeout",
            elapsed=round(time.time() - t0, 3),
        )
    except Exception as exc:
        err_msg = str(exc)
        if "timed out" in err_msg.lower() or "timeout" in type(exc).__name__.lower():
            err_msg = "timeout"
        return UpscaleVerdict(
            ok=False,
            error=err_msg,
            elapsed=round(time.time() - t0, 3),
        )


def detect_upscale_async(
    path: str,
    *,
    media_metadata: Optional[Dict[str, Any]] = None,
    on_complete: Optional[Callable[[UpscaleVerdict], None]] = None,
    **kwargs: Any,
) -> threading.Thread:
    """Non-blocking background upscale detection on a daemon thread. Writes verdict to media_metadata."""
    def _worker() -> None:
        try:
            verdict = detect_upscale(path, **kwargs)
            if media_metadata is not None:
                media_metadata["upscale_detection"] = verdict.to_dict()
                if verdict.ok:
                    media_metadata["is_upscale"] = verdict.is_upscale
                    media_metadata["native_height_estimate"] = verdict.native_height_estimate
                    media_metadata["upscale_sharpness"] = verdict.sharpness
                    media_metadata["upscale_cutoff_ratio"] = verdict.cutoff_ratio
                else:
                    media_metadata["upscale_error"] = verdict.error
            if on_complete is not None:
                try:
                    on_complete(verdict)
                except Exception:
                    pass
        except Exception:
            pass

    th = threading.Thread(target=_worker, name="UpscaleDetectorWorker", daemon=True)
    th.start()
    return th
