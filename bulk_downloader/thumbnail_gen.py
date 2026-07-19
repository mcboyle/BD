r"""v3.43.76: thumbnail generation via ffmpeg.

# What this module is

Auto-generates a .jpg thumbnail (or contact sheet) per video file
using ffmpeg. Fires on download-done; output is either sidecar
(`scene.mp4` → `scene.jpg`) or parallel directory (`thumbs/scene.jpg`).

Two modes:
  - `single`: one frame at the file's midpoint (default)
  - `sheet`: contact sheet of N frames (default 3×3)

# Why

At 5-7GB per scene, a library of 3,000 files is impossible to browse
visually. A 50KB .jpg per file gives Matt a scannable index without
opening each file.

# Strategy

  - Background daemon-thread queue, populated by the runner's
    download-done hook (parallel to dedup hash worker)
  - Each job: probe duration via ffprobe, pick frame timestamps,
    invoke ffmpeg, save to target path
  - Fail-open everywhere: missing ffmpeg, corrupt file, missing
    parent dir, write permission errors all silently skip with a
    log entry
  - Per-site config: enabled, mode, frame count for sheet mode,
    output dir mode (sidecar or parallel)

# Output naming

  - Sidecar mode: same dir as video, same basename, `.jpg` extension
    `E:\Wow\Brazzers\scene_2024.mp4` → `E:\Wow\Brazzers\scene_2024.jpg`
  - Parallel mode: `thumbs/` subfolder of the download dir, mirroring
    the file's relative path
    `E:\Wow\Brazzers\scene_2024.mp4` → `E:\Wow\thumbs\Brazzers\scene_2024.jpg`

# Implementation notes

  - ffmpeg is invoked via subprocess.run with 60s timeout per job
  - For sheet mode, we use ffmpeg's `-vf "tile=NxM,scale=W:H"` filter
    plus `-frames:v 1` to produce a single composite image
  - For single mode: `-ss <midpoint> -frames:v 1 -q:v 2`
  - The thumb is overwritten if it already exists (mode=regenerate)
    or skipped (mode=skip_existing, the default)
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ─── Defaults ─────────────────────────────────────────────────────


DEFAULT_THUMB_QUALITY = 2          # ffmpeg -q:v 2 = high quality JPEG
DEFAULT_THUMB_WIDTH = 640          # max width in single-frame mode
DEFAULT_SHEET_ROWS = 3
DEFAULT_SHEET_COLS = 3
DEFAULT_SHEET_TILE_WIDTH = 320    # per-tile width in sheet mode
DEFAULT_FFMPEG_TIMEOUT_S = 60.0


# ─── Availability ─────────────────────────────────────────────────


def is_ffmpeg_available() -> bool:
    from . import ffmpeg_bin          # MOD-4
    return ffmpeg_bin.available()


def is_ffprobe_available() -> bool:
    import shutil
    from . import ffmpeg_bin          # MOD-4
    return ffmpeg_bin.ffprobe() is not None


def is_available() -> bool:
    """Both ffmpeg + ffprobe required."""
    return is_ffmpeg_available() and is_ffprobe_available()


# ─── Result types ─────────────────────────────────────────────────


@dataclass
class ThumbnailResult:
    ok: bool = False
    source_path: str = ""
    output_path: str = ""
    duration_s: float = 0.0       # source video duration
    elapsed_s: float = 0.0
    mode: str = ""                # "single" or "sheet"
    error: str = ""


# ─── ffprobe helper ───────────────────────────────────────────────


def _probe_duration(path: str, *, timeout_s: float = 30.0) -> float:
    """Return source video duration in seconds. 0 on any failure."""
    if not is_ffprobe_available():
        return 0.0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             path],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout_s,
        )
        if result.returncode != 0:
            return 0.0
        return float(result.stdout.strip() or 0.0)
    except Exception as e:
        log.debug("thumbnail: ffprobe failed on %s: %s", path[-50:], e)
        return 0.0


# ─── Output path resolution ───────────────────────────────────────


def resolve_output_path(
    source_path: str,
    *,
    mode: str = "sidecar",          # "sidecar" or "parallel"
    download_dir: str = "",
    parallel_subdir: str = "thumbs",
    ext: str = ".jpg",
) -> str:
    """Compute the target thumbnail path for a video file.

    `mode=sidecar`: same directory, same basename, .jpg
    `mode=parallel`: <download_dir>/<parallel_subdir>/<relative-path>.jpg

    Returns "" on bad input.
    """
    if not source_path:
        return ""
    src = os.path.abspath(source_path)
    if mode == "sidecar":
        base, _ = os.path.splitext(src)
        return base + ext
    if mode == "parallel":
        if not download_dir:
            # Fall back to sidecar when no download_dir
            base, _ = os.path.splitext(src)
            return base + ext
        dl = os.path.abspath(download_dir)
        try:
            rel = os.path.relpath(src, dl)
            # If src isn't inside download_dir, relpath returns
            # something starting with .. — fall back to sidecar then
            if rel.startswith(".."):
                base, _ = os.path.splitext(src)
                return base + ext
        except Exception:
            base, _ = os.path.splitext(src)
            return base + ext
        base, _ = os.path.splitext(rel)
        return os.path.join(dl, parallel_subdir, base + ext)
    # Unknown mode → sidecar
    base, _ = os.path.splitext(src)
    return base + ext


# ─── Generation ───────────────────────────────────────────────────


def generate_single_frame(
    source_path: str,
    output_path: str,
    *,
    timestamp_s: Optional[float] = None,
    quality: int = DEFAULT_THUMB_QUALITY,
    width: int = DEFAULT_THUMB_WIDTH,
    timeout_s: float = DEFAULT_FFMPEG_TIMEOUT_S,
) -> ThumbnailResult:
    """Extract a single frame at `timestamp_s` (default = midpoint).
    Writes a .jpg to `output_path`.

    Fail-open: returns ThumbnailResult(ok=False) on every error mode.
    """
    start = time.monotonic()
    if not source_path or not output_path:
        return ThumbnailResult(ok=False, error="empty_paths")
    if not os.path.isfile(source_path):
        return ThumbnailResult(ok=False, source_path=source_path,
                                error="source_not_found")
    if not is_available():
        return ThumbnailResult(ok=False, source_path=source_path,
                                output_path=output_path,
                                error="ffmpeg_or_ffprobe_missing")

    # Pick a midpoint timestamp if not specified
    duration = _probe_duration(source_path)
    if timestamp_s is None:
        # Use 30% point — earlier frames are often title cards, end
        # frames are often credits. Halfway works fine but 30% is
        # a slightly better default for actual content thumbnails.
        timestamp_s = duration * 0.30 if duration > 0 else 5.0
    timestamp_s = max(0.0, timestamp_s)

    # Ensure output parent dir exists
    try:
        parent = os.path.dirname(output_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
    except Exception as e:
        return ThumbnailResult(ok=False, source_path=source_path,
                                output_path=output_path,
                                error=f"mkdir_failed:{e}")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(timestamp_s),
        "-i", source_path,
        "-frames:v", "1",
        "-q:v", str(int(quality)),
        "-vf", f"scale='min({int(width)},iw)':-2",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout_s,
        )
        if result.returncode != 0:
            return ThumbnailResult(
                ok=False, source_path=source_path,
                output_path=output_path, duration_s=duration,
                mode="single",
                elapsed_s=time.monotonic() - start,
                error=f"ffmpeg_rc_{result.returncode}:"
                       f"{result.stderr[:120].strip()}",
            )
    except subprocess.TimeoutExpired:
        return ThumbnailResult(
            ok=False, source_path=source_path,
            output_path=output_path, mode="single",
            elapsed_s=time.monotonic() - start,
            error="ffmpeg_timeout",
        )
    except Exception as e:
        return ThumbnailResult(
            ok=False, source_path=source_path,
            output_path=output_path, mode="single",
            elapsed_s=time.monotonic() - start,
            error=f"{type(e).__name__}:{str(e)[:80]}",
        )

    # Verify the file was written
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        return ThumbnailResult(
            ok=False, source_path=source_path,
            output_path=output_path, mode="single",
            elapsed_s=time.monotonic() - start,
            error="output_missing_or_empty",
        )

    return ThumbnailResult(
        ok=True, source_path=source_path,
        output_path=output_path, duration_s=duration, mode="single",
        elapsed_s=time.monotonic() - start,
    )


def generate_contact_sheet(
    source_path: str,
    output_path: str,
    *,
    rows: int = DEFAULT_SHEET_ROWS,
    cols: int = DEFAULT_SHEET_COLS,
    tile_width: int = DEFAULT_SHEET_TILE_WIDTH,
    quality: int = DEFAULT_THUMB_QUALITY,
    timeout_s: float = DEFAULT_FFMPEG_TIMEOUT_S,
) -> ThumbnailResult:
    """Generate a contact sheet of rows×cols frames into one composite
    JPEG.

    Uses ffmpeg's `-vf select=...,tile=...` filter chain. Frame
    timestamps are evenly distributed across the duration (skipping
    the first/last 5% to avoid title/credits frames).
    """
    start = time.monotonic()
    if not source_path or not output_path:
        return ThumbnailResult(ok=False, error="empty_paths")
    if not os.path.isfile(source_path):
        return ThumbnailResult(ok=False, source_path=source_path,
                                error="source_not_found")
    if not is_available():
        return ThumbnailResult(ok=False, source_path=source_path,
                                error="ffmpeg_or_ffprobe_missing")

    rows = max(1, min(10, int(rows)))
    cols = max(1, min(10, int(cols)))
    n_frames = rows * cols

    duration = _probe_duration(source_path)
    if duration <= 0:
        return ThumbnailResult(
            ok=False, source_path=source_path, mode="sheet",
            error="duration_unknown",
            elapsed_s=time.monotonic() - start,
        )

    # Skip first/last 5%
    start_t = duration * 0.05
    end_t = duration * 0.95
    span = end_t - start_t
    if span <= 0:
        # Very short video
        start_t = 0.0
        end_t = duration
        span = duration

    # Ensure parent dir
    try:
        parent = os.path.dirname(output_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
    except Exception as e:
        return ThumbnailResult(ok=False, source_path=source_path,
                                error=f"mkdir_failed:{e}")

    # Approach: use ffmpeg's `fps=` filter set to extract N frames
    # over the duration, then tile them. `fps=N/duration` extracts
    # ~N frames evenly spaced.
    fps = n_frames / duration if duration > 0 else 1.0
    vf = (f"fps={fps},scale={int(tile_width)}:-2,"
          f"tile={cols}x{rows}")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", source_path,
        "-vf", vf,
        "-frames:v", "1",
        "-q:v", str(int(quality)),
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout_s,
        )
        if result.returncode != 0:
            return ThumbnailResult(
                ok=False, source_path=source_path,
                output_path=output_path, duration_s=duration,
                mode="sheet",
                elapsed_s=time.monotonic() - start,
                error=f"ffmpeg_rc_{result.returncode}:"
                       f"{result.stderr[:120].strip()}",
            )
    except subprocess.TimeoutExpired:
        return ThumbnailResult(
            ok=False, source_path=source_path,
            output_path=output_path, mode="sheet",
            elapsed_s=time.monotonic() - start,
            error="ffmpeg_timeout",
        )
    except Exception as e:
        return ThumbnailResult(
            ok=False, source_path=source_path,
            output_path=output_path, mode="sheet",
            elapsed_s=time.monotonic() - start,
            error=f"{type(e).__name__}:{str(e)[:80]}",
        )

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        return ThumbnailResult(
            ok=False, source_path=source_path,
            output_path=output_path, mode="sheet",
            elapsed_s=time.monotonic() - start,
            error="output_missing_or_empty",
        )

    return ThumbnailResult(
        ok=True, source_path=source_path,
        output_path=output_path, duration_s=duration, mode="sheet",
        elapsed_s=time.monotonic() - start,
    )


# ─── Generate (dispatcher) ────────────────────────────────────────


def generate_thumbnail(
    source_path: str,
    *,
    output_path: str = "",
    mode: str = "single",                # "single" or "sheet"
    output_dir_mode: str = "sidecar",    # "sidecar" or "parallel"
    download_dir: str = "",
    sheet_rows: int = DEFAULT_SHEET_ROWS,
    sheet_cols: int = DEFAULT_SHEET_COLS,
    skip_existing: bool = True,
    timeout_s: float = DEFAULT_FFMPEG_TIMEOUT_S,
) -> ThumbnailResult:
    """Top-level entrypoint. Generates a thumbnail for `source_path`
    according to the given mode.

    If `output_path` is empty, it's computed from `output_dir_mode` +
    `download_dir`.

    If `skip_existing` is True and the output already exists, returns
    ok=True with no work done.
    """
    if not source_path:
        return ThumbnailResult(ok=False, error="empty_source")

    if not output_path:
        output_path = resolve_output_path(
            source_path, mode=output_dir_mode,
            download_dir=download_dir,
        )
    if not output_path:
        return ThumbnailResult(ok=False, source_path=source_path,
                                error="cannot_resolve_output")

    if skip_existing and os.path.isfile(output_path):
        # Compatible result so callers can treat as success
        return ThumbnailResult(
            ok=True, source_path=source_path,
            output_path=output_path,
            mode=mode,
            elapsed_s=0.0,
        )

    if mode == "sheet":
        return generate_contact_sheet(
            source_path, output_path,
            rows=sheet_rows, cols=sheet_cols,
            timeout_s=timeout_s,
        )
    return generate_single_frame(
        source_path, output_path, timeout_s=timeout_s,
    )


# ─── Background worker queue ──────────────────────────────────────


@dataclass
class _ThumbJob:
    source_path: str
    config: dict = field(default_factory=dict)


class ThumbnailWorker:
    """Background daemon thread that processes a queue of thumbnail
    generation jobs.

    Usage:
        worker = ThumbnailWorker()
        worker.start()
        worker.submit(source_path, config={...})
        # ... later ...
        worker.stop()
    """

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stats_lock = threading.Lock()
        self._completed: int = 0
        self._failed: int = 0
        self._total_elapsed_s: float = 0.0
        self._last_error: str = ""

    def submit(self, source_path: str, *, config: Optional[dict] = None) -> None:
        """Add a job to the queue. Fire-and-forget; returns immediately."""
        if not source_path:
            return
        self._q.put(_ThumbJob(source_path=source_path,
                               config=config or {}))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="thumbnail-worker",
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        # Put a sentinel so the queue.get() returns
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)

    def queue_depth(self) -> int:
        return self._q.qsize()

    def stats(self) -> dict:
        with self._stats_lock:
            return {
                "completed": self._completed,
                "failed": self._failed,
                "queue_depth": self.queue_depth(),
                "total_elapsed_s": round(self._total_elapsed_s, 2),
                "running": self._thread is not None and self._thread.is_alive(),
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if job is None:
                # Stop sentinel
                break
            try:
                result = generate_thumbnail(
                    job.source_path,
                    mode=job.config.get("mode", "single"),
                    output_dir_mode=job.config.get(
                        "output_dir_mode", "sidecar"),
                    download_dir=job.config.get("download_dir", ""),
                    sheet_rows=int(job.config.get(
                        "sheet_rows", DEFAULT_SHEET_ROWS)),
                    sheet_cols=int(job.config.get(
                        "sheet_cols", DEFAULT_SHEET_COLS)),
                    skip_existing=job.config.get(
                        "skip_existing", True),
                    timeout_s=float(job.config.get(
                        "timeout_s", DEFAULT_FFMPEG_TIMEOUT_S)),
                )
            except Exception as e:
                with self._stats_lock:
                    self._failed += 1
                    self._last_error = f"{type(e).__name__}:{str(e)[:80]}"
                continue
            with self._stats_lock:
                if result.ok:
                    self._completed += 1
                else:
                    self._failed += 1
                    self._last_error = result.error
                self._total_elapsed_s += result.elapsed_s


# ─── Module-level singleton worker ────────────────────────────────


_default_worker: Optional[ThumbnailWorker] = None
_default_worker_lock = threading.Lock()


def get_default_worker() -> ThumbnailWorker:
    """Get/create the singleton thumbnail worker. Starts it on first
    access."""
    global _default_worker
    if _default_worker is None:
        with _default_worker_lock:
            if _default_worker is None:
                _default_worker = ThumbnailWorker()
                _default_worker.start()
    return _default_worker


def stop_default_worker() -> None:
    """Stop the singleton worker (testing only)."""
    global _default_worker
    with _default_worker_lock:
        if _default_worker is not None:
            _default_worker.stop()
            _default_worker = None


__all__ = [
    "is_ffmpeg_available",
    "is_ffprobe_available",
    "is_available",
    "ThumbnailResult",
    "resolve_output_path",
    "generate_single_frame",
    "generate_contact_sheet",
    "generate_thumbnail",
    "ThumbnailWorker",
    "get_default_worker",
    "stop_default_worker",
]
