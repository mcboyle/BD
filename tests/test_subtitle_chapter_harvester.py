"""RED-first tests for Row 910: multi-track auxiliary text and chapter
annotation ingestion.

OWNS-CORRECTED (RULING-row910-OWNS-bd-pm-F51-A-2026-09-19T23:35:55Z.md):
register named bulk_downloader/app_captures.py (a Flask blueprint,
misfit) and bulk_downloader/file_assembler.py (absent at base). The
measured seams are bulk_downloader/subtitles.py (source-page .vtt/.srt
discovery + sidecar download, additive beside the existing subliminal
path) and bulk_downloader/mp4_metadata.py (chapter-interval embedding
via a new iTunes freeform atom; enrichment.detect_chapters is the
producer of intervals, not duplicated here).

Covers the register's three acceptance points:
  1. caption sidecar download
  2. chapter marker embedding in MP4 container
  3. character encoding preservation
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bulk_downloader import subtitles, mp4_metadata

BD_GATE_SCOPE = "module"


# ─── discover_page_track_urls ───────────────────────────────────────

def test_discovers_vtt_and_srt_urls_and_ignores_everything_else():
    urls = [
        "https://cdn.example.com/video/master.m3u8",
        "https://cdn.example.com/captions/en.vtt",
        "https://cdn.example.com/captions/en.vtt?t=123",
        "https://cdn.example.com/subs/fr.srt#frag",
        "https://cdn.example.com/thumb.jpg",
        "",
        None,
        42,
    ]
    found = subtitles.discover_page_track_urls(urls)
    assert found == [
        {"url": "https://cdn.example.com/captions/en.vtt", "kind": "vtt"},
        {"url": "https://cdn.example.com/captions/en.vtt?t=123", "kind": "vtt"},
        {"url": "https://cdn.example.com/subs/fr.srt#frag", "kind": "srt"},
    ]


def test_discover_page_track_urls_empty_input_is_empty_output():
    assert subtitles.discover_page_track_urls([]) == []
    assert subtitles.discover_page_track_urls(None) == []


# ─── download_track: caption sidecar download ───────────────────────

def _fake_httpx_get(status_code, body_bytes):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.content = body_bytes

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)
    mock_client.get = MagicMock(return_value=mock_response)

    fake_httpx = MagicMock()
    fake_httpx.Client = MagicMock(return_value=mock_client)
    fake_httpx.RequestError = Exception
    return fake_httpx, mock_client


def test_download_track_saves_sidecar_on_200(tmp_path):
    payload = b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world\n"
    fake_httpx, mock_client = _fake_httpx_get(200, payload)
    dest = tmp_path / "video.en.vtt"

    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = subtitles.download_track(
            "https://cdn.example.com/captions/en.vtt", dest)

    assert result["ok"] is True
    assert result["error"] is None
    assert Path(result["path"]) == dest
    assert dest.read_bytes() == payload
    mock_client.get.assert_called_once()


def test_download_track_reports_http_error_and_writes_nothing(tmp_path):
    fake_httpx, _ = _fake_httpx_get(404, b"")
    dest = tmp_path / "video.en.vtt"

    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = subtitles.download_track(
            "https://cdn.example.com/captions/missing.vtt", dest)

    assert result["ok"] is False
    assert "404" in result["error"]
    assert not dest.exists()


def test_download_track_rejects_non_http_scheme():
    result = subtitles.download_track("file:///etc/passwd", "/tmp/x.vtt")
    assert result["ok"] is False
    assert result["error"] == "unsupported scheme"


def test_download_track_rejects_empty_url():
    result = subtitles.download_track("", "/tmp/x.vtt")
    assert result["ok"] is False


# ─── character encoding preservation ────────────────────────────────

def test_download_track_preserves_non_utf8_bytes_exactly(tmp_path):
    # Latin-1 body: a byte sequence that is NOT valid UTF-8 on its own
    # (0xE9 = "é" in Latin-1, invalid as a lone UTF-8 continuation byte).
    payload = "1\n00:00:00,000 --> 00:00:01,000\nCaf\xe9\n".encode("latin-1")
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")

    fake_httpx, _ = _fake_httpx_get(200, payload)
    dest = tmp_path / "video.fr.srt"

    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = subtitles.download_track(
            "https://cdn.example.com/subs/fr.srt", dest)

    assert result["ok"] is True
    # Byte-identical: no decode/re-encode step touched the payload.
    assert dest.read_bytes() == payload


def test_download_track_preserves_utf8_bom(tmp_path):
    payload = b"\xef\xbb\xbfWEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n"
    fake_httpx, _ = _fake_httpx_get(200, payload)
    dest = tmp_path / "video.en.vtt"

    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = subtitles.download_track(
            "https://cdn.example.com/captions/en.vtt", dest)

    assert result["ok"] is True
    assert dest.read_bytes() == payload
    assert dest.read_bytes()[:3] == b"\xef\xbb\xbf"


# ─── embed_chapter_markers: chapter marker embedding in MP4 container ──

def _make_bare_mp4(tmp_path) -> Path:
    """Magic-byte-only MP4 stand-in, matching the fixture shape already
    used by tests/test_v3_43_64_mp4_metadata.py -- is_mp4_path() only
    needs the ftyp magic, and MP4()/save() are mocked below rather than
    driven against a real container (the existing tag_mp4 tests use the
    same approach)."""
    p = tmp_path / "video.mp4"
    p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
    return p


def _fake_mutagen_mod():
    fake_mod = MagicMock()
    fake_mp4_obj = MagicMock()
    set_calls = {}
    fake_mp4_obj.__setitem__.side_effect = (
        lambda k, v: set_calls.__setitem__(k, v))
    fake_mp4_obj.save = MagicMock()
    fake_mod.MP4 = MagicMock(return_value=fake_mp4_obj)
    fake_mod.MP4FreeForm = lambda data: data  # identity: store raw bytes
    return fake_mod, set_calls


def test_embed_chapter_markers_writes_and_reloads_json_atom(tmp_path):
    p = _make_bare_mp4(tmp_path)
    chapters = [
        {"start": 0.0, "end": 12.345, "duration": 12.345},
        {"start": 12.345, "end": 40.0, "duration": 27.655},
    ]
    fake_mod, set_calls = _fake_mutagen_mod()

    with patch.object(mp4_metadata, "_try_import_mutagen",
                       return_value=fake_mod):
        ok = mp4_metadata.embed_chapter_markers(p, chapters=chapters)

    assert ok is True
    import json
    written = set_calls[mp4_metadata.CHAPTERS_ATOM_KEY][0]
    assert json.loads(written.decode("utf-8")) == chapters


def test_embed_chapter_markers_calls_enrichment_when_chapters_omitted(tmp_path):
    p = _make_bare_mp4(tmp_path)
    detected = [{"start": 1.0, "end": 2.0, "duration": 1.0}]
    fake_mod, set_calls = _fake_mutagen_mod()

    with patch.object(mp4_metadata, "_try_import_mutagen",
                       return_value=fake_mod), \
         patch("bulk_downloader.enrichment.detect_chapters",
               return_value=detected) as mock_detect:
        ok = mp4_metadata.embed_chapter_markers(p, video_path=str(p))

    assert ok is True
    mock_detect.assert_called_once_with(str(p))
    import json
    written = set_calls[mp4_metadata.CHAPTERS_ATOM_KEY][0]
    assert json.loads(written.decode("utf-8")) == detected


def test_embed_chapter_markers_false_when_no_chapters_and_none_detected(tmp_path):
    p = _make_bare_mp4(tmp_path)
    with patch("bulk_downloader.enrichment.detect_chapters", return_value=[]):
        ok = mp4_metadata.embed_chapter_markers(p)
    assert ok is False


def test_embed_chapter_markers_false_for_non_mp4_path(tmp_path):
    p = tmp_path / "not_a_video.txt"
    p.write_text("hello")
    ok = mp4_metadata.embed_chapter_markers(
        p, chapters=[{"start": 0.0, "end": 1.0, "duration": 1.0}])
    assert ok is False
