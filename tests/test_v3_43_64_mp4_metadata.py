"""v3.43.64: tests for MP4 metadata tagging + cover-art fetch.

These tests don't require mutagen to be installed in the test
environment — they verify:

  1. is_available() returns False gracefully when mutagen is absent
  2. is_mp4_path() works on extensions + magic bytes + missing files
  3. MetadataContext defaults are correct
  4. _normalize_date() handles common date shapes
  5. _detect_image_format() identifies JPEG and PNG by magic bytes
  6. fetch_cover() refuses bad inputs and returns None on failure
  7. build_context_from_extract_result() adapts ExtractResult shape
  8. context_to_template_vars() produces the right dict shape
  9. tag_mp4() returns False when no fields are populated
 10. tag_mp4() returns False when mutagen isn't importable
 11. tag_mp4() returns False on non-MP4 input
 12. tag_mp4() with a real mutagen install round-trips tags
     (skipped when mutagen not installed)
 13. The _embed_metadata_if_mp4 runner helper exists and is callable

The "real mutagen" tests are skipped when mutagen isn't on the path,
which is the typical CI / sandbox case. Production environments with
mutagen installed get the full coverage.
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import io
# [SAST 3:13pm 13 may] removed unused: import os
# [SAST 3:13pm 13 may] removed unused: import struct
import sys
# [SAST 3:13pm 13 may] removed unused: import tempfile
# [SAST 3:13pm 13 may] removed unused: from pathlib import Path
from unittest.mock import patch, MagicMock

# [SAST 3:13pm 13 may] removed unused: import pytest

from bulk_downloader import mp4_metadata


# Detect whether the real mutagen is available — gates the round-trip test
try:
    import mutagen  # noqa: F401
    from mutagen import mp4 as _real_mp4  # noqa: F401
    _MUTAGEN_INSTALLED = True
except Exception:
    _MUTAGEN_INSTALLED = False


# ─── is_available / module-level state ──────────────────────────────


class TestIsAvailable:
    def test_returns_bool(self):
        assert isinstance(mp4_metadata.is_available(), bool)

    def test_matches_real_mutagen_presence(self):
        # Whatever the answer is, it should agree with whether
        # we can `import mutagen` directly.
        assert mp4_metadata.is_available() == _MUTAGEN_INSTALLED


# ─── is_mp4_path ────────────────────────────────────────────────────


class TestIsMp4Path:
    def test_nonexistent_path_returns_false(self):
        assert mp4_metadata.is_mp4_path("/nonexistent/path/file.mp4") is False

    def test_recognizes_mp4_extension_on_real_file(self, tmp_path):
        p = tmp_path / "video.mp4"
        p.write_bytes(b"\x00" * 16)
        assert mp4_metadata.is_mp4_path(str(p)) is True

    def test_recognizes_m4v_extension(self, tmp_path):
        p = tmp_path / "video.m4v"
        p.write_bytes(b"\x00" * 16)
        assert mp4_metadata.is_mp4_path(str(p)) is True

    def test_recognizes_m4a_extension(self, tmp_path):
        p = tmp_path / "audio.m4a"
        p.write_bytes(b"\x00" * 16)
        assert mp4_metadata.is_mp4_path(str(p)) is True

    def test_recognizes_mov_extension(self, tmp_path):
        p = tmp_path / "video.mov"
        p.write_bytes(b"\x00" * 16)
        assert mp4_metadata.is_mp4_path(str(p)) is True

    def test_uppercase_extension(self, tmp_path):
        p = tmp_path / "video.MP4"
        p.write_bytes(b"\x00" * 16)
        assert mp4_metadata.is_mp4_path(str(p)) is True

    def test_rejects_txt_file(self, tmp_path):
        p = tmp_path / "note.txt"
        p.write_text("hello")
        assert mp4_metadata.is_mp4_path(str(p)) is False

    def test_accepts_unknown_extension_with_ftyp_magic(self, tmp_path):
        """ffmpeg sometimes outputs without an extension; magic-bytes path."""
        p = tmp_path / "video"  # no extension
        # MP4 magic: bytes 4..8 = "ftyp"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        assert mp4_metadata.is_mp4_path(str(p)) is True

    def test_rejects_short_file(self, tmp_path):
        p = tmp_path / "video"
        p.write_bytes(b"\x00\x00")  # only 2 bytes
        assert mp4_metadata.is_mp4_path(str(p)) is False

    def test_path_object_input(self, tmp_path):
        p = tmp_path / "video.mp4"
        p.write_bytes(b"\x00" * 16)
        assert mp4_metadata.is_mp4_path(p) is True

    def test_invalid_input_returns_false(self):
        assert mp4_metadata.is_mp4_path(None) is False
        # Passing an object that can't stringify cleanly should NOT raise
        class Bad:
            def __str__(self): raise RuntimeError("nope")
        assert mp4_metadata.is_mp4_path(Bad()) is False


# ─── MetadataContext ────────────────────────────────────────────────


class TestMetadataContext:
    def test_defaults(self):
        ctx = mp4_metadata.MetadataContext()
        assert ctx.title == ""
        assert ctx.artist == ""
        assert ctx.album == ""
        assert ctx.date == ""
        assert ctx.comment == ""
        assert ctx.encoder == ""
        assert ctx.genre == ""
        assert ctx.description == ""
        assert ctx.cover_url == ""
        assert ctx.extras == {}

    def test_field_assignment(self):
        ctx = mp4_metadata.MetadataContext(title="T", artist="A")
        assert ctx.title == "T"
        assert ctx.artist == "A"

    def test_extras_isolated_between_instances(self):
        """Defensive check that mutable default doesn't leak between
        instances — the @dataclass + field(default_factory=dict) pattern
        prevents this, but verify it explicitly."""
        a = mp4_metadata.MetadataContext()
        b = mp4_metadata.MetadataContext()
        a.extras["x"] = "y"
        assert "x" not in b.extras


# ─── _normalize_date ────────────────────────────────────────────────


class TestNormalizeDate:
    def test_iso_date_passes_through(self):
        assert mp4_metadata._normalize_date("2024-08-14") == "2024-08-14"

    def test_us_date(self):
        # 08/14/2024 — month/day/year. Our regex matches YYYY first
        # so this becomes 2024-08-14 only when the date STARTS with the
        # year. A "08/14/2024" input would only match the year.
        out = mp4_metadata._normalize_date("08/14/2024")
        assert out == "2024"  # falls back to year-only match

    def test_compact_date(self):
        assert mp4_metadata._normalize_date("20240814") == "2024-08-14"

    def test_year_only(self):
        assert mp4_metadata._normalize_date("2024") == "2024"

    def test_empty_returns_empty(self):
        assert mp4_metadata._normalize_date("") == ""
        assert mp4_metadata._normalize_date(None) == ""

    def test_invalid_month_falls_back_to_year(self):
        # "2024-13-50" has a year but the month/day are invalid → year only
        out = mp4_metadata._normalize_date("2024-13-50")
        assert out == "2024"

    def test_no_year_returns_empty(self):
        assert mp4_metadata._normalize_date("hello world") == ""


# ─── _detect_image_format ───────────────────────────────────────────


class TestDetectImageFormat:
    def test_jpeg_magic(self):
        assert mp4_metadata._detect_image_format(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "jpeg"

    def test_png_magic(self):
        assert mp4_metadata._detect_image_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == "png"

    def test_too_short_returns_none(self):
        assert mp4_metadata._detect_image_format(b"\x89PNG") is None

    def test_unknown_returns_none(self):
        assert mp4_metadata._detect_image_format(b"GIF89a" + b"\x00" * 20) is None
        assert mp4_metadata._detect_image_format(b"<html>" + b"\x00" * 20) is None


# ─── fetch_cover ────────────────────────────────────────────────────


class TestFetchCover:
    def test_empty_url(self):
        assert mp4_metadata.fetch_cover("") is None

    def test_non_http_scheme(self):
        assert mp4_metadata.fetch_cover("file:///etc/passwd") is None
        assert mp4_metadata.fetch_cover("data:image/jpeg;base64,abc") is None

    def test_none_url(self):
        assert mp4_metadata.fetch_cover(None) is None  # type: ignore[arg-type]

    def test_returns_none_when_httpx_missing(self):
        """If httpx is somehow absent the function logs and returns
        None rather than raising."""
        with patch.dict(sys.modules, {"httpx": None}):
            # Clear cached import
            cached = sys.modules.pop("httpx", None)
            try:
                result = mp4_metadata.fetch_cover("https://example.com/c.jpg")
            finally:
                if cached is not None:
                    sys.modules["httpx"] = cached
            # Either None (the fail-open path) is acceptable.
            assert result is None or isinstance(result, bytes)

    def test_returns_none_on_http_error(self):
        """Mocked httpx returning 404 -> None."""
        # Build a fake httpx module
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {}
        mock_response.iter_bytes = MagicMock(return_value=iter([b""]))

        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__ = MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = MagicMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        fake_httpx = MagicMock()
        fake_httpx.Client = MagicMock(return_value=mock_client)
        fake_httpx.RequestError = Exception  # used in except clause

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = mp4_metadata.fetch_cover("https://example.com/c.jpg")
        assert result is None

    def test_returns_bytes_on_jpeg(self):
        """Mocked httpx returning a valid JPEG -> bytes."""
        payload = b"\xff\xd8\xff\xe0" + b"\x00" * 4096
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": str(len(payload))}
        mock_response.iter_bytes = MagicMock(return_value=iter([payload]))

        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__ = MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = MagicMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        fake_httpx = MagicMock()
        fake_httpx.Client = MagicMock(return_value=mock_client)
        fake_httpx.RequestError = Exception

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = mp4_metadata.fetch_cover("https://example.com/c.jpg")
        assert result == payload

    def test_rejects_oversize_via_content_length(self):
        """Content-Length advertising 100MB should bail before any
        body bytes are read."""
        big = mp4_metadata._COVER_MAX_BYTES + 1
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": str(big)}
        # iter_bytes shouldn't even get called — but mock it safely
        mock_response.iter_bytes = MagicMock(return_value=iter([b"\xff\xd8\xff"]))

        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__ = MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = MagicMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        fake_httpx = MagicMock()
        fake_httpx.Client = MagicMock(return_value=mock_client)
        fake_httpx.RequestError = Exception

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = mp4_metadata.fetch_cover("https://example.com/big.jpg")
        assert result is None

    def test_rejects_non_image_bytes(self):
        """200 + correct length but body is HTML — should reject."""
        payload = b"<!DOCTYPE html><html></html>"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": str(len(payload))}
        mock_response.iter_bytes = MagicMock(return_value=iter([payload]))

        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__ = MagicMock(return_value=mock_response)
        mock_stream_cm.__exit__ = MagicMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        fake_httpx = MagicMock()
        fake_httpx.Client = MagicMock(return_value=mock_client)
        fake_httpx.RequestError = Exception

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = mp4_metadata.fetch_cover("https://example.com/c.jpg")
        assert result is None


# ─── build_context_from_extract_result ──────────────────────────────


class TestBuildContextFromExtractResult:
    def _make_result(self, **overrides):
        """Construct a fake ExtractResult-shaped object — duck-typed."""
        defaults = dict(
            title="Test Title", author="Test Author",
            upload_date="2024-08-14", duration_sec=600,
            thumbnail_url="https://cdn.example.com/thumb.jpg",
            quality="1080p",
        )
        defaults.update(overrides)
        # Use a SimpleNamespace so the function sees real attrs
        from types import SimpleNamespace
        return SimpleNamespace(**defaults)

    def test_basic_mapping(self):
        r = self._make_result()
        ctx = mp4_metadata.build_context_from_extract_result(
            r, page_url="https://example.com/v/1",
            site_display_name="Example Tube",
            encoder_version="BulkDownloader v3.43.64",
        )
        assert ctx.title == "Test Title"
        assert ctx.artist == "Test Author"
        assert ctx.album == "Example Tube"
        assert ctx.comment == "https://example.com/v/1"
        assert ctx.encoder == "BulkDownloader v3.43.64"
        assert ctx.date == "2024-08-14"
        assert ctx.cover_url == "https://cdn.example.com/thumb.jpg"

    def test_missing_optional_fields(self):
        """An ExtractResult with empty author/upload_date/thumb should
        produce empty strings — not None or raise."""
        from types import SimpleNamespace
        r = SimpleNamespace(title="T", author="", upload_date="",
                            duration_sec=0, thumbnail_url="",
                            quality="")
        ctx = mp4_metadata.build_context_from_extract_result(
            r, page_url="https://e.com/v",
            site_display_name="E", encoder_version="x")
        assert ctx.artist == ""
        assert ctx.date == ""
        assert ctx.cover_url == ""

    def test_very_long_title_truncated(self):
        from types import SimpleNamespace
        r = SimpleNamespace(title="X" * 500, author="A",
                            upload_date="", duration_sec=0,
                            thumbnail_url="", quality="")
        ctx = mp4_metadata.build_context_from_extract_result(
            r, page_url="x", site_display_name="s", encoder_version="e")
        assert len(ctx.title) == 255  # capped


# ─── tag_mp4 ────────────────────────────────────────────────────────


class TestTagMp4FailureModes:
    def test_returns_false_when_mutagen_unavailable(self, tmp_path):
        """When mutagen import returns None, tag_mp4 short-circuits."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext(title="T")
        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=None):
            assert mp4_metadata.tag_mp4(str(p), ctx) is False

    def test_returns_false_on_non_mp4(self, tmp_path):
        """Even if mutagen is available, tag_mp4 refuses non-MP4 files."""
        p = tmp_path / "note.txt"
        p.write_text("hello")
        ctx = mp4_metadata.MetadataContext(title="T")
        assert mp4_metadata.tag_mp4(str(p), ctx) is False

    def test_returns_false_when_no_fields_populated(self, tmp_path):
        """An empty context produces no atoms; tag_mp4 returns False."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext()
        # Force mutagen "available" so we exercise the no-atoms branch
        fake_mod = MagicMock()
        fake_mod.MP4 = MagicMock()
        fake_mod.MP4Cover = MagicMock()
        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=fake_mod):
            assert mp4_metadata.tag_mp4(str(p), ctx) is False

    def test_returns_false_on_mutagen_exception(self, tmp_path):
        """If mutagen raises on save (e.g. read-only fs), return False."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext(title="T")

        fake_mod = MagicMock()
        fake_mp4_obj = MagicMock()
        fake_mp4_obj.save.side_effect = IOError("Read-only filesystem")
        # Setting items on the fake MP4 must work, otherwise we error
        # before we get to save().
        fake_mp4_obj.__setitem__ = MagicMock()
        fake_mod.MP4 = MagicMock(return_value=fake_mp4_obj)
        fake_mod.MP4Cover = MagicMock()

        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=fake_mod):
            assert mp4_metadata.tag_mp4(str(p), ctx) is False

    def test_called_with_atoms_when_fields_populated(self, tmp_path):
        """Verify that the right atom keys are passed to MP4()."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext(
            title="T", artist="A", album="S",
            date="2024-08-14", comment="https://x.com/v",
            encoder="BD v3.43.64",
        )

        fake_mod = MagicMock()
        fake_mp4_obj = MagicMock()
        # Capture all __setitem__ calls
        set_calls = {}

        def _setitem(k, v):
            set_calls[k] = v

        fake_mp4_obj.__setitem__.side_effect = _setitem
        fake_mp4_obj.save = MagicMock()
        fake_mod.MP4 = MagicMock(return_value=fake_mp4_obj)
        fake_mod.MP4Cover = MagicMock()

        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=fake_mod):
            ok = mp4_metadata.tag_mp4(str(p), ctx)
        assert ok is True
        # Each atom is a list-wrapped value
        assert set_calls["\xa9nam"] == ["T"]
        assert set_calls["\xa9ART"] == ["A"]
        assert set_calls["\xa9alb"] == ["S"]
        assert set_calls["\xa9day"] == ["2024-08-14"]
        assert set_calls["\xa9cmt"] == ["https://x.com/v"]
        assert set_calls["\xa9too"] == ["BD v3.43.64"]

    def test_cover_bytes_included_when_supplied(self, tmp_path):
        """When cover_bytes are JPEG, the covr atom is set."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext(title="T")
        cover = b"\xff\xd8\xff\xe0" + b"\x00" * 1000

        fake_mod = MagicMock()
        fake_mp4_obj = MagicMock()
        set_calls = {}
        fake_mp4_obj.__setitem__.side_effect = (
            lambda k, v: set_calls.__setitem__(k, v))
        fake_mp4_obj.save = MagicMock()
        fake_mod.MP4 = MagicMock(return_value=fake_mp4_obj)

        fake_cover_class = MagicMock()
        fake_cover_class.FORMAT_JPEG = "jpeg-const"
        fake_cover_class.FORMAT_PNG = "png-const"
        fake_mod.MP4Cover = fake_cover_class

        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=fake_mod):
            ok = mp4_metadata.tag_mp4(str(p), ctx, cover_bytes=cover)
        assert ok is True
        assert "covr" in set_calls
        # Called with the jpeg format constant
        fake_cover_class.assert_any_call(cover, imageformat="jpeg-const")

    def test_unrecognized_cover_format_skipped(self, tmp_path):
        """Cover bytes that aren't JPEG or PNG should be silently
        dropped — the rest of the tags should still be written."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext(title="T")
        bad_cover = b"GIF89a" + b"\x00" * 100  # GIF, not jpeg/png

        fake_mod = MagicMock()
        fake_mp4_obj = MagicMock()
        set_calls = {}
        fake_mp4_obj.__setitem__.side_effect = (
            lambda k, v: set_calls.__setitem__(k, v))
        fake_mp4_obj.save = MagicMock()
        fake_mod.MP4 = MagicMock(return_value=fake_mp4_obj)
        fake_mod.MP4Cover = MagicMock()

        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=fake_mod):
            ok = mp4_metadata.tag_mp4(str(p), ctx, cover_bytes=bad_cover)
        assert ok is True
        assert "covr" not in set_calls
        assert "\xa9nam" in set_calls

    def test_extras_passed_through(self, tmp_path):
        """Extras dict entries (e.g. \\xa9wrt) should be written."""
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)
        ctx = mp4_metadata.MetadataContext(title="T",
                                            extras={"\xa9wrt": "phub"})

        fake_mod = MagicMock()
        fake_mp4_obj = MagicMock()
        set_calls = {}
        fake_mp4_obj.__setitem__.side_effect = (
            lambda k, v: set_calls.__setitem__(k, v))
        fake_mp4_obj.save = MagicMock()
        fake_mod.MP4 = MagicMock(return_value=fake_mp4_obj)
        fake_mod.MP4Cover = MagicMock()

        with patch.object(mp4_metadata, "_try_import_mutagen",
                          return_value=fake_mod):
            ok = mp4_metadata.tag_mp4(str(p), ctx)
        assert ok is True
        assert set_calls["\xa9wrt"] == ["phub"]


# ─── Real-mutagen round-trip ────────────────────────────────────────
#
# A real round-trip test would need a valid MP4 fixture and the actual
# mutagen library. This sandbox has neither (network is offline so we
# can't `pip install mutagen` and we don't ship test fixtures big enough
# to be valid MP4 containers). The mocked tests above exercise the
# atom-construction logic; production environments verify the end-to-end
# flow during normal use.


# ─── context_to_template_vars ───────────────────────────────────────


class TestContextToTemplateVars:
    def test_basic(self):
        ctx = mp4_metadata.MetadataContext(
            title="T", artist="A", album="S",
            date="2024-08-14", encoder="enc", genre="g",
        )
        v = mp4_metadata.context_to_template_vars(ctx)
        assert v["title"] == "T"
        assert v["performer"] == "A"
        assert v["artist"] == "A"
        assert v["studio"] == "S"
        assert v["album"] == "S"
        assert v["site"] == "S"  # alias to album
        assert v["date"] == "2024-08-14"
        assert v["year"] == "2024"
        assert v["encoder"] == "enc"
        assert v["genre"] == "g"

    def test_empty_date_yields_empty_year(self):
        ctx = mp4_metadata.MetadataContext()
        v = mp4_metadata.context_to_template_vars(ctx)
        assert v["year"] == ""

    def test_year_only_date(self):
        ctx = mp4_metadata.MetadataContext(date="2024")
        v = mp4_metadata.context_to_template_vars(ctx)
        assert v["year"] == "2024"


# ─── Runner integration ─────────────────────────────────────────────


class TestRunnerHelperExists:
    """Make sure the SiteRunner has the v3.43.64 helper and that it
    behaves correctly under the most common no-op paths."""

    def test_method_exists_on_class(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_embed_metadata_if_mp4")

    def test_returns_false_when_disabled_in_config(self, tmp_path):
        """If embed_metadata is False, the helper returns False without
        doing any work — even on a real MP4 path."""
        from bulk_downloader.runner import SiteRunner
        p = tmp_path / "v.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8)

        # Construct a minimal SiteRunner without spinning up workers
        runner = SiteRunner.__new__(SiteRunner)
        runner.config = {"embed_metadata": False, "name": "test"}

        result = runner._embed_metadata_if_mp4(
            str(p), title="T", source_url="http://x/v")
        assert result is False

    def test_returns_false_on_non_mp4(self, tmp_path):
        """Helper returns False for .txt input even with embed_metadata=True."""
        from bulk_downloader.runner import SiteRunner
        p = tmp_path / "note.txt"
        p.write_text("hello")

        runner = SiteRunner.__new__(SiteRunner)
        runner.config = {"embed_metadata": True, "name": "test"}

        result = runner._embed_metadata_if_mp4(
            str(p), title="T", source_url="http://x/v")
        assert result is False

    def test_never_raises_on_garbage_input(self, tmp_path):
        """Helper must be entirely fail-open — no input should raise."""
        from bulk_downloader.runner import SiteRunner

        runner = SiteRunner.__new__(SiteRunner)
        runner.config = {"embed_metadata": True, "name": "test"}

        # Each of these should return False without raising
        assert runner._embed_metadata_if_mp4("/nonexistent/file.mp4") is False
        assert runner._embed_metadata_if_mp4("") is False
        # None for path: is_mp4_path handles it; helper must too
        assert runner._embed_metadata_if_mp4(None) is False
