"""Cut 0.6 (Phase-0 close): the ffmpeg health check must probe CAPABILITY -- does
the binary actually run, and does it support the mpegts muxer + https protocol BD
needs for HLS/TS remuxing -- not just presence on PATH. A build that's present but
crashes on invocation or lacks mpegts/https still fails real downloads (the
static-ffmpeg HLS+https segfault class the operator hit)."""
import shutil
from bulk_downloader import healthcheck


def test_ffmpeg_capability_flags_broken_binary():
    cap = healthcheck._ffmpeg_capability("/nonexistent/ffmpeg")
    assert cap.get("error"), "a missing/broken ffmpeg binary must report an error"


def test_ffmpeg_capability_real_binary_has_mpegts_https():
    ff = shutil.which("ffmpeg")
    if not ff:
        return  # no ffmpeg here; the presence branch covers that case
    cap = healthcheck._ffmpeg_capability(ff)
    assert not cap.get("error"), f"real ffmpeg should run: {cap}"
    assert cap.get("missing") == [], f"real ffmpeg should support mpegts+https: {cap}"


def test_check_ffmpeg_reflects_capability_not_just_presence():
    if not shutil.which("ffmpeg"):
        return
    r = healthcheck._check_ffmpeg()
    # the healthy path now names the capability it verified (was presence-only)
    assert "mpegts" in r["message"], f"check should reflect capability, not just presence: {r}"
