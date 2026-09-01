"""Thumbnail / contact-sheet generator (Phase 133, Block Q).

Build preview thumbnails for downloaded videos:

  • single_thumb(path, *, at_pct, size) — extract one frame at
    `at_pct` (0-100) of the video's duration. Returns the PNG bytes
    or writes to disk.

  • contact_sheet(path, *, rows, cols, width) — N×M grid of frames
    sampled evenly across the runtime. Classic "video preview" tile.
    Saved as <basename>.thumbs.jpg next to the source.

  • sprite_sheet(path, *, count, width) — produce a single-strip
    sprite + a WebVTT cue file. Video.js / hls.js consume this to
    show seek-bar previews on hover.

All operations are external-process via ffmpeg. Skipped silently if
ffmpeg isn't on PATH.

Generated thumbnails sit next to the source file with deterministic
suffixes so a re-scan can detect "already done."
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional


def is_available() -> bool:
    from . import ffmpeg_bin          # MOD-4: both from the SAME build
    return ffmpeg_bin.both_available()


class FFprobeError(RuntimeError):
    """A duration or image-dimension probe could not be measured."""


def _probe_duration(path: str) -> float:
    """Get video duration in seconds or raise a named probe failure."""
    from . import ffmpeg_bin
    ffprobe = ffmpeg_bin.ffprobe()
    if not ffprobe:
        raise FFprobeError("ffprobe_unavailable")
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.DEVNULL, timeout=15)
        return float(out.strip())
    except subprocess.CalledProcessError as e:
        raise FFprobeError(f"ffprobe_exit_{e.returncode}") from e
    except subprocess.TimeoutExpired as e:
        raise FFprobeError("ffprobe_timeout") from e
    except OSError as e:
        raise FFprobeError(
            f"ffprobe_exec_failed:{type(e).__name__}:{str(e)[:120]}") from e
    except (TypeError, ValueError) as e:
        raise FFprobeError("ffprobe_invalid_duration") from e


def single_thumb(path: str, *, at_pct: float = 50.0,
                size: str = "640x360",
                out_path: Optional[str] = None) -> dict:
    """Extract one frame at `at_pct` (0-100). Returns
    {ok, out_path, size_bytes, error}."""
    if not is_available():
        return {"ok": False, "error": "ffmpeg/ffprobe not on PATH"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "source not found"}
    try:
        duration = _probe_duration(path)
    except FFprobeError as e:
        return {"ok": False, "error": str(e)}
    if duration <= 0:
        return {"ok": False, "error": "could not probe duration"}
    timestamp = max(0.0, duration * (at_pct / 100.0))
    out = out_path or str(Path(path).with_suffix(".thumb.jpg"))
    from . import ffmpeg_bin
    ffmpeg = ffmpeg_bin.ffmpeg()
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg_unavailable"}
    try:
        subprocess.check_call(
            [ffmpeg, "-y", "-loglevel", "error",
             "-ss", f"{timestamp:.2f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={size}",
             "-q:v", "3", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=60)
        return {"ok": True, "out_path": out,
                "size_bytes": os.path.getsize(out),
                "timestamp_seconds": timestamp}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"ffmpeg exit {e.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffmpeg_timeout"}
    except OSError as e:
        return {"ok": False,
                "error": (f"ffmpeg_exec_failed:{type(e).__name__}:"
                          f"{str(e)[:120]}")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def contact_sheet(path: str, *, rows: int = 4, cols: int = 4,
                 tile_width: int = 320,
                 out_path: Optional[str] = None) -> dict:
    """Make a rows×cols grid of evenly-spaced frames. Returns
    {ok, out_path, frame_count, error}."""
    if not is_available():
        return {"ok": False, "error": "ffmpeg not on PATH"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "source not found"}
    try:
        duration = _probe_duration(path)
    except FFprobeError as e:
        return {"ok": False, "error": str(e)}
    if duration <= 0:
        return {"ok": False, "error": "could not probe duration"}
    n_frames = rows * cols
    # Sample at: 5%, then evenly spaced up to 95%
    out = out_path or str(Path(path).with_suffix(".thumbs.jpg"))
    # ffmpeg's tile filter wants -frames * fps. We pick frames evenly
    # using the select expression — one frame per (duration/n_frames).
    interval = duration / max(1, n_frames)
    select = ",".join(
        f"eq(n\\,round({(0.5 + i) * interval}*fps))" for i in range(n_frames)
    )
    # Actually a simpler approach: use the fps + tile filter chain
    fps = max(0.01, n_frames / max(1, duration))
    from . import ffmpeg_bin
    ffmpeg = ffmpeg_bin.ffmpeg()
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg_unavailable"}
    try:
        subprocess.check_call(
            [ffmpeg, "-y", "-loglevel", "error", "-i", path,
             "-vf",
             f"fps={fps},scale={tile_width}:-1,tile={cols}x{rows}",
             "-frames:v", "1", "-q:v", "3", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=120)
        return {"ok": True, "out_path": out,
                "frame_count": n_frames,
                "size_bytes": os.path.getsize(out),
                "tile_width": tile_width,
                "rows": rows, "cols": cols,
                "duration_seconds": duration}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"ffmpeg exit {e.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffmpeg_timeout"}
    except OSError as e:
        return {"ok": False,
                "error": (f"ffmpeg_exec_failed:{type(e).__name__}:"
                          f"{str(e)[:120]}")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def sprite_sheet(path: str, *, count: int = 100,
                tile_width: int = 200,
                out_dir: Optional[str] = None) -> dict:
    """Generate a single-row sprite strip + a WebVTT cue file.
    Compatible with video.js seekbar-preview plugins."""
    if not is_available():
        return {"ok": False, "error": "ffmpeg not on PATH"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "source not found"}
    try:
        duration = _probe_duration(path)
    except FFprobeError as e:
        return {"ok": False, "error": str(e)}
    if duration <= 0:
        return {"ok": False, "error": "could not probe duration"}
    base = Path(path)
    sprite_path = base.with_suffix(".sprite.jpg")
    vtt_path = base.with_suffix(".sprite.vtt")
    # ffmpeg-tile filter: 1×count strip
    fps = max(0.01, count / duration)
    from . import ffmpeg_bin
    ffmpeg = ffmpeg_bin.ffmpeg()
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg_unavailable"}
    try:
        subprocess.check_call(
            [ffmpeg, "-y", "-loglevel", "error", "-i", path,
             "-vf",
             f"fps={fps},scale={tile_width}:-1,tile=1x{count}",
             "-frames:v", "1", "-q:v", "4", str(sprite_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=180)
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"ffmpeg exit {e.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffmpeg_timeout"}
    except OSError as e:
        return {"ok": False,
                "error": (f"ffmpeg_exec_failed:{type(e).__name__}:"
                          f"{str(e)[:120]}")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

    # We don't know the actual tile height — re-probe
    try:
        tile_h = _probe_tile_height(str(sprite_path), count)
    except FFprobeError as e:
        return {"ok": False, "error": str(e)}

    # Build VTT cues. Each cue references the same image with #xywh
    interval = duration / count
    cues = ["WEBVTT", ""]
    for i in range(count):
        start_t = i * interval
        end_t = (i + 1) * interval
        cues.append(f"{_fmt_vtt_time(start_t)} --> {_fmt_vtt_time(end_t)}")
        # Vertical strip → y offset varies, x is always 0
        y = i * tile_h
        cues.append(f"{sprite_path.name}#xywh=0,{y},{tile_width},{tile_h}")
        cues.append("")
    vtt_path.write_text("\n".join(cues), encoding="utf-8")
    return {
        "ok": True,
        "sprite_path": str(sprite_path),
        "vtt_path": str(vtt_path),
        "count": count,
        "tile_width": tile_width,
        "tile_height": tile_h,
        "duration_seconds": duration,
    }


def _probe_tile_height(sprite_path: str, count: int) -> int:
    """Read the sprite image's height, divide by count."""
    from . import ffmpeg_bin
    ffprobe = ffmpeg_bin.ffprobe()
    if not ffprobe:
        raise FFprobeError("ffprobe_unavailable")
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height",
             "-of", "default=noprint_wrappers=1:nokey=1", sprite_path],
            stderr=subprocess.DEVNULL, timeout=10)
        total_h = int(out.strip())
        return max(1, total_h // count)
    except subprocess.CalledProcessError as e:
        raise FFprobeError(f"ffprobe_exit_{e.returncode}") from e
    except subprocess.TimeoutExpired as e:
        raise FFprobeError("ffprobe_timeout") from e
    except OSError as e:
        raise FFprobeError(
            f"ffprobe_exec_failed:{type(e).__name__}:{str(e)[:120]}") from e
    except (TypeError, ValueError, ZeroDivisionError) as e:
        raise FFprobeError("ffprobe_invalid_height") from e


def _fmt_vtt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for WebVTT."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"
