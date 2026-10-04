"""O1826 BRIEF-20 (M065, M072, M073): download-classify honeypot + integrity.

M065 -- ``honeypot_score._path_has_pixel_token`` matched PIXEL_PATH_TOKENS as
bare substrings, so ``/collections/clip.mp4`` ("/collect") and
``/tracks/song.mp4`` ("/track") scored 0.85 (>= the 0.8 drop) and real media
was dropped. Tokens must match whole path segments.

M073 -- ``integrity._verify_with_ffprobe`` required a video stream for every
file, so an audio file without cover art (.mp3/.m4a) always failed integrity
and was quarantined. Audio extensions need an audio stream; video extensions
still need a video stream.

M072 -- a satellite-offload failure in ``verify_media_integrity`` was
swallowed by ``_ = str(_e)`` with no log. It must be logged before the local
ffprobe fallback.

Fixtures only: ffprobe is faked through the ``_FFPROBE`` seam and
``subprocess.run``; nothing live is touched.
"""
import json
import logging
import subprocess

BD_GATE_SCOPE = "module"

from bulk_downloader import honeypot_score, integrity, satellite_video
from bulk_downloader.honeypot_score import classify_score, score_candidate


# ── M065: pixel tokens are whole path segments ─────────────────────────────

def test_media_under_collections_and_tracks_is_kept():
    """RED on the base: "/collect" and "/track" matched inside "/collections/"
    and "/tracks/" -> pixel_path 0.85 -> classify_score == "drop"."""
    for url in ("https://cdn.example.com/collections/clip.mp4",
                "https://cdn.example.com/tracks/song.mp4"):
        s, r = score_candidate({"url": url})
        assert "pixel_path" not in r, (
            f"{url}: pixel_path fired on a substring of a real path segment "
            f"(score={s}, reasons={r!r}) -- M065 bare-substring match")
        assert classify_score(s) == "keep", (url, s, r)


def test_whole_segment_pixel_routes_still_drop():
    """Negative control: real pixel routes still score 0.85 and drop."""
    for url in ("https://cdn.example.com/collect?x=1",
                "https://cdn.example.com/g/collect",
                "https://cdn.example.com/pixel/track.gif",
                "https://cdn.example.com/track",
                "https://cdn.example.com/serve/imp.gif?x=1",
                "https://cdn.example.com/p.gif",
                "https://cdn.example.com/Beacon.GIF"):
        s, r = score_candidate({"url": url})
        assert "pixel_path" in r, (url, s, r)
        assert s == 0.85 and classify_score(s) == "drop", (url, s, r)


def test_pixel_token_helper_exact_segment_cases():
    f = honeypot_score._path_has_pixel_token
    assert f("/collect") and f("/a/collect/") and f("/pixel.gif")
    assert not f("/collections/a.mp4")
    assert not f("/tracks/a.mp4")
    assert not f("/audio/soundtrack.mp4")
    assert not f("/x/b.gifs/a.mp4")
    assert not f("")


# ── M073: audio needs an audio stream, video a video stream ────────────────

def _fake_ffprobe(monkeypatch, rc=0, streams=None, stderr=b""):
    out = json.dumps({"streams": streams or []}).encode()
    monkeypatch.setattr(integrity, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(integrity.subprocess, "run", lambda cmd, *a, **kw:
                        subprocess.CompletedProcess(cmd, rc, stdout=out,
                                                    stderr=stderr))


def _no_offload(monkeypatch):
    monkeypatch.setattr(satellite_video, "is_eligible_for_offload",
                        lambda path: False)


def test_artless_mp3_and_m4a_pass_integrity(tmp_path, monkeypatch):
    """RED on the base: (False, "no video stream") -> quarantined."""
    _no_offload(monkeypatch)
    _fake_ffprobe(monkeypatch, streams=[{"codec_type": "audio"}])
    for name in ("song.mp3", "song.m4a"):
        p = tmp_path / name
        p.write_bytes(b"\xff\xfb" + bytes(4096))
        ok, reason = integrity.verify_media_integrity(p)
        assert ok, (
            f"{name} with one audio stream and no cover art failed integrity "
            f"({reason!r}) -- M073: audio must not require a video stream")


def test_audio_extension_without_audio_stream_fails(tmp_path, monkeypatch):
    _no_offload(monkeypatch)
    _fake_ffprobe(monkeypatch, streams=[{"codec_type": "video"}])
    p = tmp_path / "song.mp3"
    p.write_bytes(bytes(4096))
    ok, reason = integrity.verify_media_integrity(p)
    assert not ok and reason == "no audio stream", reason


def test_video_still_requires_video_stream(tmp_path, monkeypatch):
    """Negative control: an audio-only .mp4 still fails."""
    _no_offload(monkeypatch)
    _fake_ffprobe(monkeypatch, streams=[{"codec_type": "audio"}])
    p = tmp_path / "clip.mp4"
    p.write_bytes(bytes(4096))
    ok, reason = integrity.verify_media_integrity(p)
    assert not ok and reason == "no video stream", reason


def test_truncated_mp4_still_fails(tmp_path, monkeypatch):
    """Negative control: ffprobe rc=1 on a truncated mp4 still fails."""
    _no_offload(monkeypatch)
    _fake_ffprobe(monkeypatch, rc=1, stderr=b"moov atom not found")
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypisom")
    ok, reason = integrity.verify_media_integrity(p)
    assert not ok and reason.startswith("ffprobe rc=1"), reason


# ── M072: satellite-offload failure is logged, then falls back ─────────────

def test_satellite_offload_failure_is_logged_and_falls_back(
        tmp_path, monkeypatch, caplog):
    def boom(path):
        raise RuntimeError("satellite rpc exploded")
    monkeypatch.setattr(satellite_video, "is_eligible_for_offload",
                        lambda path: True)
    monkeypatch.setattr(satellite_video, "validate_video_rpc", boom)
    _fake_ffprobe(monkeypatch, streams=[{"codec_type": "video"}])
    p = tmp_path / "clip.mp4"
    p.write_bytes(bytes(4096))
    with caplog.at_level(logging.WARNING, logger=integrity.__name__):
        ok, reason = integrity.verify_media_integrity(p)
    assert ok, reason
    hits = [r for r in caplog.records
            if r.name == integrity.__name__
            and "satellite rpc exploded" in r.getMessage()]
    assert len(hits) == 1, (
        "satellite-offload failure was swallowed without a log record -- M072; "
        f"records={[r.getMessage() for r in caplog.records]!r}")
    assert hits[0].levelno == logging.WARNING
