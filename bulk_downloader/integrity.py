"""ffprobe-based file integrity check (silent skip if not installed)."""
import json, shutil, subprocess

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

def verify_media_integrity(path):
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
                         capture_output=True,timeout=60)
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