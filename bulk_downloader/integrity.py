"""ffprobe-based file integrity check (silent skip if not installed)."""
import json, shutil, subprocess

from .streaming_hash import (
    HashVerificationResult,
    StreamingHashEngine,
    StreamingHashReader,
    StreamingHashWriter,
    verify_file_streaming,
    verify_stream_inline,
)


# ─── INTEGRITY VERIFICATION ──────────────────────────────────────────────────
# v3.66.703 (MOD-4): resolve through the ONE resolver, and do it LAZILY. This used
# to be `shutil.which("ffprobe")` evaluated at module IMPORT -- so it took whatever
# was on PATH at startup, and an ffmpeg_path pin set afterwards could never reach
# it. Same fail-open contract: no ffprobe -> integrity is a no-op (we would rather
# download than refuse to run).
_UNSET = object()
_FFPROBE = _UNSET      # module-level seam: tests set this directly; None = "absent"


def _ffprobe():
    """Resolve ffprobe ONCE, lazily, through the one resolver (so an ffmpeg_path pin
    reaches it -- the old code resolved at module IMPORT and a pin set afterwards
    could never have applied). ``_FFPROBE`` stays a module attribute because it is a
    long-standing test seam: setting it to None must still mean "ffprobe absent"."""
    global _FFPROBE
    if _FFPROBE is _UNSET:
        try:
            from . import ffmpeg_bin
            _FFPROBE = ffmpeg_bin.ffprobe()
        except Exception:
            _FFPROBE = shutil.which("ffprobe")
    return _FFPROBE

# Row 722 (ultrafilms, 2026-09-15): a 9.9 MB photo set saved as *.zip was marked
# "failed integrity check (ffprobe rc=1)" and quarantined. ffprobe cannot validate
# a zip or a still image; the check must be format-aware. Video/audio (and any
# unknown extension) keep the ffprobe path UNCHANGED; zips get zipfile.testzip();
# images pass on a non-empty file whose magic header matches the extension.
_FFPROBE_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".m4v",
                 ".mp3", ".m4a", ".ts", ".flv"}
_IMAGE_MAGIC = {
    ".jpg":  (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png":  (b"\x89PNG\r\n\x1a\n",),
    ".gif":  (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),          # RIFF....WEBP -- second check below
}


def _verify_zip(path):
    """zip integrity: open + testzip() is None (first bad member otherwise)."""
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as zf:
            bad = zf.testzip()
    except Exception as e:
        return False, f"zip unreadable: {e}"
    if bad is not None:
        return False, f"zip member corrupt: {bad}"
    return True, ""


def _verify_image(path, ext):
    """image integrity: non-empty file with a magic header matching the extension."""
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(16)
    except Exception as e:
        return False, f"image unreadable: {e}"
    if not head:
        return False, "image is empty"
    if not any(head.startswith(m) for m in _IMAGE_MAGIC[ext]):
        return False, f"image magic header does not match {ext}"
    if ext == ".webp" and head[8:12] != b"WEBP":
        return False, "image magic header does not match .webp"
    return True, ""


def verify_media_integrity(path):
    """Format-aware integrity check over the saved file. Returns (ok, reason).

    Dispatches on extension: zips -> zipfile test, images -> magic header,
    eligible video -> satellite video offload with fail-soft local fallback,
    everything else (audio and unknown) -> the ffprobe check below."""
    import os
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ".zip":
        return _verify_zip(path)
    if ext in _IMAGE_MAGIC:
        return _verify_image(path, ext)
    # Row 862: Route eligible video validation to approved LAN hardware acceleration
    try:
        from . import satellite_video
        if satellite_video.is_eligible_for_offload(path):
            return satellite_video.validate_video_rpc(path)
    except Exception as _e:
        _ = str(_e)
        return _verify_with_ffprobe(path)
    return _verify_with_ffprobe(path)


def _verify_with_ffprobe(path):
    """Run ffprobe over the saved file. Returns (ok, reason).

    The check is light: we ask for a JSON dump of the streams; failure to
    parse that means the container or codec stream is broken. Only a
    minute timeout — large files don't actually require fully decoding.
    Skipped (returns (True, 'ffprobe not installed')) if ffprobe isn't
    available, so users without ffmpeg installed aren't penalised."""
    _fp = _ffprobe()
    if not _fp: return True,"ffprobe not installed"
    try:
        r=subprocess.run([_fp,"-v","error","-show_streams","-of","json",str(path)],
                         stdin=subprocess.DEVNULL, capture_output=True,timeout=60)
        if r.returncode!=0:
            err=(r.stderr or b"").decode("utf-8","ignore")[:120].strip()
            return False,f"ffprobe rc={r.returncode}: {err or 'no detail'}"
        try: data=json.loads(r.stdout or b"{}")
        except Exception as e: return False,f"unparseable ffprobe output: {e}"
        streams=data.get("streams") or []
        if not streams: return False,"no streams found"
        if not any(s.get("codec_type")=="video" for s in streams):
            return False,"no video stream"
        return True,""
    except subprocess.TimeoutExpired: return False,"ffprobe timeout"
    except Exception as e: return False,f"ffprobe error: {e}"


def verify_stream_hash(
    stream,
    expected_hash: str,
    algorithm: str = "sha256",
    chunk_size: int = 65536,
) -> HashVerificationResult:
    """Inline cryptographic stream verification."""
    return verify_stream_inline(stream, expected_hash, algorithm=algorithm, chunk_size=chunk_size)


def verify_file_hash_streaming(
    file_path,
    expected_hash: str,
    algorithm: str = "sha256",
    chunk_size: int = 65536,
) -> HashVerificationResult:
    """Inline cryptographic file verification using streaming reads."""
    return verify_file_streaming(file_path, expected_hash, algorithm=algorithm, chunk_size=chunk_size)


def verify_payload_size_and_duration(path, expected_bytes=None, expected_duration=None, **kwargs):
    """Format and dimension verification over payload size and media duration."""
    from bulk_downloader.payload_verifier import verify_payload_duration_and_size
    return verify_payload_duration_and_size(
        path,
        expected_bytes=expected_bytes,
        expected_duration=expected_duration,
        **kwargs,
    )
