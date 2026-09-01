"""Asset enrichment — chapter detection + audio fingerprint + quality
grading (Phase 107, Block N).

Three operator-facing capabilities, all opt-in, all read-only on the
file (no transcoding):

1. detect_chapters(path) — find black-frame transitions to suggest
   chapter boundaries. Useful for compilation scenes / multi-part
   videos where the operator wants Plex-compatible chapter NFOs.

2. audio_fingerprint(path) — compute a Chromaprint-style hash over
   the audio track. Two files with identical audio but different
   video re-encodings will share a fingerprint. Useful for
   identifying re-uploads of the same source.

3. quality_grade(path) — objective grade derived from probed metadata:
   bitrate, resolution, codec generation, container. Returns a 0-100
   score the recommendation engine can use as a tiebreaker.

All require ffmpeg/ffprobe on PATH. Fail-open when missing: each
function returns None and logs a one-time warning. The post-download
pipeline calls these after the main download completes; failures
must not block the queue.

ffprobe gives us most of what we need without invoking ffmpeg's full
encoder path — it's cheap (<1 sec for a 2 GB MP4).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


_FFPROBE = None
_FFMPEG = None
_TOOLS_WARNED = False


def _resolve_tools():
    """Cache ffmpeg/ffprobe paths. Returns (ffmpeg, ffprobe) or (None, None)
    if either is missing. Emits one warning per process when missing."""
    global _FFPROBE, _FFMPEG, _TOOLS_WARNED
    if _FFPROBE is not None or _FFMPEG is not None:
        return (_FFMPEG, _FFPROBE)
    from . import ffmpeg_bin          # MOD-4: pinned build, resolved together
    _FFMPEG = ffmpeg_bin.ffmpeg()
    _FFPROBE = ffmpeg_bin.ffprobe()
    if (not _FFMPEG or not _FFPROBE) and not _TOOLS_WARNED:
        sys.stderr.write("[enrichment] ffmpeg/ffprobe not on PATH; "
                         "asset enrichment skipped\n")
        _TOOLS_WARNED = True
    return (_FFMPEG, _FFPROBE)


def is_available() -> bool:
    """True when both ffmpeg and ffprobe are reachable."""
    ff, fp = _resolve_tools()
    return bool(ff and fp)


# ─── Probe ────────────────────────────────────────────────────────────

def probe(path: str) -> Optional[dict]:
    """Run ffprobe -show_format -show_streams and parse the JSON.
    Returns the parsed dict or None on error."""
    _, fp = _resolve_tools()
    if not fp:
        return None
    if not Path(path).exists():
        return None
    cmd = [fp, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError,
            json.JSONDecodeError):
        return None


# ─── Chapter detection ────────────────────────────────────────────────

def detect_chapters(
    path: str,
    *,
    min_silence_seconds: float = 1.0,
    black_threshold: float = 0.10,
    pix_threshold: float = 0.10,
) -> list:
    """Find black-frame transitions in `path`. Returns list of dicts:
        [{"start": float seconds, "end": float seconds, "duration": float}]
    Empty list when no chapters detected or ffmpeg unavailable.

    Method: ffmpeg's blackdetect filter — well-tested, fast.
    `min_silence_seconds` is the floor; transitions shorter than this
    are ignored (typical scene cuts have ≥1 sec of black). The two
    thresholds are ffmpeg's pixel-blackness and frame-blackness
    fractions (0.0-1.0); defaults are conservative.
    """
    ff, _ = _resolve_tools()
    if not ff:
        return []
    if not Path(path).exists():
        return []
    cmd = [
        ff, "-hide_banner", "-nostats", "-i", path,
        "-vf", f"blackdetect=d={min_silence_seconds}:"
               f"pic_th={pix_threshold}:pix_th={black_threshold}",
        "-an", "-f", "null", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        sys.stderr.write(f"[enrichment] chapter detect failed: {e}\n")
        return []
    chapters = []
    # ffmpeg writes black_start/black_end lines to stderr. Parse them.
    for line in (r.stderr or "").splitlines():
        if "black_start:" not in line:
            continue
        # Format example:
        #  [blackdetect @ 0x...] black_start:12.345 black_end:13.567 black_duration:1.222
        try:
            parts = line.split("black_start:")[1]
            start_s = float(parts.split()[0])
            end_part = parts.split("black_end:")[1] if "black_end:" in parts else ""
            end_s = float(end_part.split()[0]) if end_part else start_s
            chapters.append({
                "start": round(start_s, 3),
                "end": round(end_s, 3),
                "duration": round(end_s - start_s, 3),
            })
        except (IndexError, ValueError):
            continue
    return chapters


# ─── Audio fingerprint ────────────────────────────────────────────────

def audio_fingerprint(path: str, *, duration_seconds: int = 120) -> Optional[str]:
    """Compute an audio fingerprint by hashing the decoded PCM of the
    first `duration_seconds` of audio. Cheap proxy for Chromaprint
    when chromaprint isn't installed — files with identical audio
    streams produce identical fingerprints regardless of video codec.

    Limitations:
      • Does NOT survive resampling or volume normalization
      • Does NOT match across different audio codecs that re-encode
    For drift-resistant fingerprinting, install chromaprint and the
    pyacoustid package separately; this is the always-available
    fallback.

    Returns a 16-char hex string (truncated SHA-256), or None on error."""
    ff, _ = _resolve_tools()
    if not ff:
        return None
    if not Path(path).exists():
        return None
    cmd = [
        ff, "-hide_banner", "-nostats", "-loglevel", "error",
        "-i", path,
        "-vn",                         # drop video
        "-ac", "1",                    # mono
        "-ar", "8000",                 # 8 kHz — coarse enough for matching
        "-t", str(int(duration_seconds)),
        "-f", "s16le",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode != 0 or not r.stdout:
            return None
        h = hashlib.sha256(r.stdout).hexdigest()
        return h[:16]
    except (subprocess.TimeoutExpired, OSError) as e:
        sys.stderr.write(f"[enrichment] audio fingerprint failed: {e}\n")
        return None


# ─── Quality grade ────────────────────────────────────────────────────

# Codec generation scoring. Newer codecs score higher per bitrate.
_CODEC_SCORE = {
    "av1": 25,
    "hevc": 20, "h265": 20,
    "vp9": 18,
    "h264": 15, "avc": 15, "avc1": 15,
    "vp8": 8,
    "mpeg4": 5,
    "wmv3": 2, "vc1": 2,
}


def quality_grade(path: str) -> Optional[dict]:
    """Compute an objective quality grade for the video at `path`.
    Returns:
      {grade: 0-100, tier: 'sd'|'hd'|'fhd'|'qhd'|'4k'|'8k',
       width, height, bitrate_kbps, codec, container, duration_seconds}

    Heuristic: 40 pts from resolution tier + 30 pts from bitrate vs
    tier-appropriate bitrate + 30 pts from codec generation."""
    p = probe(path)
    if not p:
        return None
    streams = p.get("streams", []) or []
    fmt = p.get("format", {}) or {}
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not vstream:
        return None
    try:
        width = int(vstream.get("width") or 0)
        height = int(vstream.get("height") or 0)
    except (TypeError, ValueError):
        return None
    codec = (vstream.get("codec_name") or "").lower()
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        pass
    bit_rate = 0
    try:
        bit_rate = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        pass
    bitrate_kbps = bit_rate // 1000 if bit_rate else 0
    # Resolution tier
    if height >= 4320:
        tier, res_pts, ref_kbps = "8k", 40, 60000
    elif height >= 2160:
        tier, res_pts, ref_kbps = "4k", 36, 20000
    elif height >= 1440:
        tier, res_pts, ref_kbps = "qhd", 32, 10000
    elif height >= 1080:
        tier, res_pts, ref_kbps = "fhd", 28, 6000
    elif height >= 720:
        tier, res_pts, ref_kbps = "hd", 22, 3000
    elif height >= 480:
        tier, res_pts, ref_kbps = "sd", 14, 1500
    else:
        tier, res_pts, ref_kbps = "lo", 6, 500
    # Bitrate points: 30 at reference, 0 at half-reference, 30 at 2x-reference
    if bitrate_kbps <= 0 or ref_kbps <= 0:
        br_pts = 15
    else:
        ratio = bitrate_kbps / ref_kbps
        if ratio < 0.5:
            br_pts = max(0, int(30 * (ratio / 0.5)))
        elif ratio <= 1.5:
            br_pts = 30
        else:
            # Diminishing returns above reference, capped at 30
            br_pts = 30
    codec_pts = _CODEC_SCORE.get(codec, 10)
    # Container quality is implicit in codec_pts; no separate score
    grade = max(0, min(100, res_pts + br_pts + codec_pts))
    return {
        "grade": grade,
        "tier": tier,
        "width": width,
        "height": height,
        "bitrate_kbps": bitrate_kbps,
        "codec": codec,
        "container": fmt.get("format_name", "").split(",")[0],
        "duration_seconds": round(duration, 1),
    }


def enrich(path: str, *, do_chapters: bool = False,
          do_fingerprint: bool = True, do_quality: bool = True) -> dict:
    """One-call wrapper for the post-download pipeline. Each flag
    can be disabled independently; defaults reflect cheapest useful
    set (fingerprint + quality grade; chapters are expensive on long
    files since they require a full scan)."""
    out: dict = {"path": path}
    if do_quality:
        out["quality"] = quality_grade(path)
    if do_fingerprint:
        out["audio_fingerprint"] = audio_fingerprint(path)
    if do_chapters:
        out["chapters"] = detect_chapters(path)
    return out
