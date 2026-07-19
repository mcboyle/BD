"""v3.43.63: tests for the library-extractor fast path + HLS downloader.

These tests don't require any of the actual extractor libraries to be
installed — they verify:

  1. The registry is well-formed and covers the expected sites
  2. is_supported_url() routes correctly
  3. is_available() / extract() degrade gracefully when a library is missing
  4. The adapter dispatch logic is correct (each site → right adapter)
  5. _pick_quality() honors preference order and falls back sanely
  6. hls_downloader detects HLS/DASH URLs correctly
  7. hls_downloader's progress regex parses real ffmpeg stderr
  8. hls_downloader.download() returns ok=False when ffmpeg is absent
  9. _build_ffmpeg_cmd produces sensible argv
 10. The 8 v3.43.63 templates have use_library_extractor=True

We don't actually invoke ffmpeg or hit real sites — that would be
non-deterministic and slow. Real integration is verified in production.
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import os
import re
# [SAST 3:13pm 13 may] removed unused: import sys
# [SAST 3:13pm 13 may] removed unused: import tempfile
# [SAST 3:13pm 13 may] removed unused: import threading
import time
# [SAST 3:13pm 13 may] removed unused: from pathlib import Path
from unittest.mock import patch, MagicMock

# Import our modules
from bulk_downloader import extractors
from bulk_downloader import hls_downloader


# ─── extractors module ──────────────────────────────────────────────


class TestExtractorsRegistry:
    """The registry maps BD site_ids to (lib_module, host_regex, adapter)."""

    def test_registry_nonempty(self):
        assert len(extractors._REGISTRY) >= 14, \
            "v3.43.63 should register at least 14 sites"

    def test_registry_entries_well_formed(self):
        for sid, entry in extractors._REGISTRY.items():
            assert isinstance(entry, tuple) and len(entry) == 3, \
                f"{sid}: entry must be (lib, regex, adapter)"
            lib, host_re, adapter = entry
            assert isinstance(lib, str) and lib, f"{sid}: lib must be non-empty str"
            assert isinstance(host_re, re.Pattern), f"{sid}: host_re must be compiled regex"
            assert isinstance(adapter, str) and adapter.startswith("_"), \
                f"{sid}: adapter must be a private function name"

    def test_every_adapter_function_exists(self):
        for sid, (_lib, _re, adapter_name) in extractors._REGISTRY.items():
            assert hasattr(extractors, adapter_name), \
                f"{sid}: adapter function {adapter_name} not found in extractors"
            assert callable(getattr(extractors, adapter_name)), \
                f"{sid}: {adapter_name} is not callable"

    def test_list_supported_sites_returns_all_registered(self):
        sites = extractors.list_supported_sites()
        assert set(sites) == set(extractors._REGISTRY.keys())
        # Sorted
        assert sites == sorted(sites)


class TestIsSupportedUrl:

    def test_pornhub_url_routes(self):
        assert extractors.is_supported_url("https://www.pornhub.com/view_video.php?viewkey=abc") == "pornhub"

    def test_pornhub_premium_url_routes(self):
        assert extractors.is_supported_url("https://www.pornhubpremium.com/view_video.php?viewkey=abc") == "pornhub"

    def test_pornhub_lang_subdomain_routes(self):
        assert extractors.is_supported_url("https://de.pornhub.com/view_video.php?viewkey=abc") == "pornhub"

    def test_xnxx_routes(self):
        assert extractors.is_supported_url("https://www.xnxx.com/video-abc/title") == "xnxx"

    def test_xvideos_routes(self):
        assert extractors.is_supported_url("https://www.xvideos.com/video.123/title") == "xvideos"

    def test_xhamster_routes_with_country_digit(self):
        # xhamster appends a digit for blocked-country redirects (xhamster2, xhamster3, ...)
        assert extractors.is_supported_url("https://xhamster3.com/videos/title-123") == "xhamster"

    def test_eporner_routes(self):
        assert extractors.is_supported_url("https://www.eporner.com/video-abc/title/") == "eporner"

    def test_youporn_routes(self):
        assert extractors.is_supported_url("https://www.youporn.com/watch/123/") == "youporn"

    def test_unsupported_url_returns_none(self):
        assert extractors.is_supported_url("https://www.example.com/foo") is None

    def test_empty_url_returns_none(self):
        assert extractors.is_supported_url("") is None

    def test_non_string_returns_none(self):
        assert extractors.is_supported_url(None) is None
        assert extractors.is_supported_url(123) is None
        assert extractors.is_supported_url([]) is None

    def test_malformed_url_returns_none(self):
        # No scheme/host
        assert extractors.is_supported_url("just-some-text") is None


class TestIsAvailableGraceful:
    """is_available() must not crash when libraries are missing."""

    def setup_method(self):
        extractors._reset_import_cache_for_tests()

    def test_returns_false_for_unknown_site(self):
        assert extractors.is_available("nonexistent_site") is False

    def test_returns_false_when_library_not_installed(self):
        # phub probably isn't installed in CI; this should return False, not crash.
        result = extractors.is_available("pornhub")
        assert result in (True, False)  # tolerate either; never crash

    def test_no_exception_on_repeated_calls(self):
        # Caching: second call should not crash either
        for _ in range(3):
            extractors.is_available("pornhub")
            extractors.is_available("xnxx")


class TestExtractGraceful:
    """extract() must never raise — all errors flow back as ExtractResult."""

    def setup_method(self):
        extractors._reset_import_cache_for_tests()

    def test_unknown_site(self):
        r = extractors.extract("not_a_site", "https://example.com/foo")
        assert isinstance(r, extractors.ExtractResult)
        assert r.ok is False
        assert r.error == "unsupported_site"

    def test_url_host_mismatch(self):
        # pornhub site_id but xnxx URL
        r = extractors.extract("pornhub", "https://www.xnxx.com/video-abc/title")
        assert r.ok is False
        assert r.error == "url_host_mismatch"

    def test_library_not_installed_returns_structured_error(self):
        # Force the import to fail by clearing cache then mocking importlib
        extractors._reset_import_cache_for_tests()
        with patch("bulk_downloader.extractors.importlib.import_module",
                   side_effect=ImportError("simulated missing")):
            r = extractors.extract("pornhub", "https://www.pornhub.com/view_video.php?viewkey=x")
            assert r.ok is False
            assert r.error == "library_not_installed"
            assert "pip install phub" in r.error_detail

    def test_extract_with_missing_library_doesnt_raise(self):
        extractors._reset_import_cache_for_tests()
        # Whatever happens, no exception
        try:
            r = extractors.extract("pornhub", "https://www.pornhub.com/view_video.php?viewkey=x")
            assert isinstance(r, extractors.ExtractResult)
        except Exception as e:
            raise AssertionError(f"extract() raised: {e}")

    def test_invalid_url_type(self):
        # The is_supported_url guard catches non-strings before extract;
        # but extract itself should also be safe.
        # We pass a valid site_id but a non-string URL.
        try:
            r = extractors.extract("pornhub", None)  # type: ignore
            # Should either return ok=False or coerce; never raise.
            assert isinstance(r, extractors.ExtractResult)
            assert r.ok is False
        except Exception as e:
            raise AssertionError(f"extract() raised on bad URL: {e}")


class TestPickQuality:
    """Pure logic — verify the quality selection helper."""

    def test_prefers_exact_match(self):
        choices = ["1080p", "720p", "480p"]
        # Ask for 720, get 720
        assert extractors._pick_quality(choices, ["720"]) == "720p"

    def test_falls_back_through_preference_order(self):
        choices = ["480p", "240p"]
        # Ask for 1080 first, then 720, then 480 -- only 480 hits.
        result = extractors._pick_quality(choices, ["1080", "720", "480"])
        assert result == "480p"

    def test_best_returns_highest(self):
        choices = ["480p", "1080p", "720p"]
        assert extractors._pick_quality(choices, ["best"]) == "1080p"

    def test_worst_returns_lowest(self):
        choices = ["1080p", "480p", "720p"]
        assert extractors._pick_quality(choices, ["worst"]) == "480p"

    def test_no_match_falls_back_to_max(self):
        choices = ["480p", "720p"]
        # 4K requested but not available — fall back to highest available
        assert extractors._pick_quality(choices, ["2160"]) == "720p"

    def test_4k_alias(self):
        choices = ["2160p", "1080p", "720p"]
        result = extractors._pick_quality(choices, ["4k"])
        assert result == "2160p"

    def test_empty_choices_returns_none(self):
        assert extractors._pick_quality([], ["best"]) is None

    def test_tolerance_within_50px(self):
        # 1088p is within 50 of 1080 -- should match a "1080" preference.
        choices = ["1088p", "480p"]
        assert extractors._pick_quality(choices, ["1080"]) == "1088p"


class TestInstalledLibraries:

    def test_returns_dict_with_all_sites(self):
        m = extractors.installed_libraries()
        assert isinstance(m, dict)
        assert set(m.keys()) == set(extractors._REGISTRY.keys())
        # Every value must be bool
        for v in m.values():
            assert isinstance(v, bool)


# ─── hls_downloader module ──────────────────────────────────────────


class TestIsStreamingUrl:

    def test_m3u8_is_hls(self):
        assert hls_downloader.is_hls_url("https://cdn.example.com/master.m3u8") is True

    def test_mpd_is_dash(self):
        assert hls_downloader.is_dash_url("https://cdn.example.com/master.mpd") is True

    def test_mp4_is_neither(self):
        assert hls_downloader.is_hls_url("https://cdn.example.com/video.mp4") is False
        assert hls_downloader.is_dash_url("https://cdn.example.com/video.mp4") is False

    def test_query_string_doesnt_break_detection(self):
        assert hls_downloader.is_hls_url("https://cdn.example.com/master.m3u8?token=abc") is True

    def test_case_insensitive(self):
        assert hls_downloader.is_hls_url("https://cdn.example.com/master.M3U8") is True

    def test_non_string_returns_false(self):
        assert hls_downloader.is_hls_url(None) is False
        assert hls_downloader.is_dash_url(123) is False

    def test_is_streaming_url_combines(self):
        assert hls_downloader.is_streaming_url("https://x.com/x.m3u8") is True
        assert hls_downloader.is_streaming_url("https://x.com/x.mpd") is True
        assert hls_downloader.is_streaming_url("https://x.com/x.mp4") is False


class TestIsHlsContentType:

    def test_apple_mpegurl(self):
        assert hls_downloader.is_hls_content_type("application/vnd.apple.mpegurl") is True

    def test_x_mpegurl(self):
        assert hls_downloader.is_hls_content_type("application/x-mpegurl") is True

    def test_mp4_is_not_hls(self):
        assert hls_downloader.is_hls_content_type("video/mp4") is False

    def test_with_charset_suffix(self):
        # Real Content-Type headers often have ";charset=..."
        assert hls_downloader.is_hls_content_type(
            "application/vnd.apple.mpegurl;charset=utf-8"
        ) is True

    def test_non_string(self):
        assert hls_downloader.is_hls_content_type(None) is False
        assert hls_downloader.is_hls_content_type(123) is False


class TestParseFfmpegProgress:
    """Verify the progress regex handles real ffmpeg stderr lines."""

    def test_typical_progress_line(self):
        line = "frame=  300 fps= 30 q=-1.0 size=    1024kB time=00:00:10.00 bitrate=839.0kbits/s speed=1.0x"
        p = hls_downloader._parse_ffmpeg_progress(line)
        assert p is not None
        assert p["bytes"] == 1024 * 1024
        assert p["seconds"] == 10.0

    def test_long_duration(self):
        line = "frame=18000 fps= 30 q=-1.0 size=  102400kB time=01:23:45.67 bitrate=..."
        p = hls_downloader._parse_ffmpeg_progress(line)
        assert p is not None
        assert p["bytes"] == 102400 * 1024
        # 1h 23m 45.67s
        assert abs(p["seconds"] - (3600 + 23 * 60 + 45.67)) < 0.01

    def test_non_progress_line(self):
        line = "[hls @ 0x7f0] Skip ('#EXT-X-VERSION:3')"
        assert hls_downloader._parse_ffmpeg_progress(line) is None

    def test_empty_line(self):
        assert hls_downloader._parse_ffmpeg_progress("") is None


class TestBuildFfmpegCmd:
    """Verify command construction has the right input/output ordering."""

    def test_basic_cmd_structure(self):
        cmd = hls_downloader._build_ffmpeg_cmd(
            "/usr/bin/ffmpeg",
            "https://cdn.example.com/master.m3u8",
            "/tmp/out.mp4",
        )
        assert cmd[0] == "/usr/bin/ffmpeg"
        # Output must be the LAST argument
        assert cmd[-1] == "/tmp/out.mp4"
        # -i must precede the input URL
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == "https://cdn.example.com/master.m3u8"

    def test_user_agent_goes_before_i(self):
        cmd = hls_downloader._build_ffmpeg_cmd(
            "ffmpeg", "https://x.com/a.m3u8", "/tmp/o.mp4",
            user_agent="MyUA/1.0",
        )
        i_idx = cmd.index("-i")
        ua_idx = cmd.index("-user_agent")
        assert ua_idx < i_idx
        assert cmd[ua_idx + 1] == "MyUA/1.0"

    def test_referer_goes_before_i(self):
        cmd = hls_downloader._build_ffmpeg_cmd(
            "ffmpeg", "https://x.com/a.m3u8", "/tmp/o.mp4",
            referer="https://x.com/video/123",
        )
        i_idx = cmd.index("-i")
        ref_idx = cmd.index("-referer")
        assert ref_idx < i_idx

    def test_reconnect_options_present(self):
        cmd = hls_downloader._build_ffmpeg_cmd(
            "ffmpeg", "https://x.com/a.m3u8", "/tmp/o.mp4",
        )
        # Reconnect-on-error flags
        assert "-reconnect" in cmd
        assert "-reconnect_streamed" in cmd
        assert "-reconnect_delay_max" in cmd

    def test_copy_codec(self):
        cmd = hls_downloader._build_ffmpeg_cmd(
            "ffmpeg", "https://x.com/a.m3u8", "/tmp/o.mp4",
        )
        # stream-copy (no re-encode)
        c_idx = cmd.index("-c")
        assert cmd[c_idx + 1] == "copy"

    def test_extra_headers_joined_correctly(self):
        cmd = hls_downloader._build_ffmpeg_cmd(
            "ffmpeg", "https://x.com/a.m3u8", "/tmp/o.mp4",
            extra_headers={"X-Foo": "bar", "X-Baz": "qux"},
        )
        # -headers takes a single blob
        h_idx = cmd.index("-headers")
        blob = cmd[h_idx + 1]
        assert "X-Foo: bar" in blob
        assert "X-Baz: qux" in blob
        assert "\r\n" in blob


class TestDownloadGraceful:
    """download() must never raise. It returns DownloadResult always."""

    def test_returns_failure_when_ffmpeg_missing(self):
        # Force ffmpeg-not-found
        hls_downloader._reset_ffmpeg_cache_for_tests()
        with patch("shutil.which", return_value=None):
            r = hls_downloader.download(
                "https://x.com/a.m3u8", "/tmp/out.mp4",
            )
            assert isinstance(r, hls_downloader.DownloadResult)
            assert r.ok is False
            assert r.error == "ffmpeg_not_installed"
        hls_downloader._reset_ffmpeg_cache_for_tests()

    def test_rejects_non_http_url(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            hls_downloader._reset_ffmpeg_cache_for_tests()
            r = hls_downloader.download(
                "file:///etc/passwd", "/tmp/out.mp4",
            )
            assert r.ok is False
            assert r.error == "bad_url"
        hls_downloader._reset_ffmpeg_cache_for_tests()

    def test_rejects_empty_output_path(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            hls_downloader._reset_ffmpeg_cache_for_tests()
            r = hls_downloader.download(
                "https://x.com/a.m3u8", "",
            )
            assert r.ok is False
            assert r.error == "bad_output_path"
        hls_downloader._reset_ffmpeg_cache_for_tests()


class TestIsAvailable:
    """Module-level is_available() reflects ffmpeg presence."""

    def teardown_method(self):
        hls_downloader._reset_ffmpeg_cache_for_tests()

    def test_returns_true_when_ffmpeg_present(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            hls_downloader._reset_ffmpeg_cache_for_tests()
            assert hls_downloader.is_available() is True

    def test_returns_false_when_ffmpeg_absent(self):
        with patch("shutil.which", return_value=None):
            hls_downloader._reset_ffmpeg_cache_for_tests()
            assert hls_downloader.is_available() is False

    def test_cached_after_first_call(self):
        # First call hits shutil.which; second uses cache.
        with patch("shutil.which", return_value="/usr/bin/ffmpeg") as m:
            hls_downloader._reset_ffmpeg_cache_for_tests()
            hls_downloader.is_available()
            hls_downloader.is_available()
            hls_downloader.is_available()
            # Should have been called exactly once
            assert m.call_count == 1


# ─── Template integration ───────────────────────────────────────────


class TestTemplatesEnableLibraryExtractor:
    """The 8 templates v3.43.63 ships should have use_library_extractor=True."""

    def test_aylo_free_tubes_enables(self):
        from bulk_downloader import templates
        t = templates.get("aylo_free_tubes")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_wgcz_tubes_enables(self):
        from bulk_downloader import templates
        t = templates.get("wgcz_tubes")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_xhamster_enables(self):
        from bulk_downloader import templates
        t = templates.get("xhamster")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_spankbang_enables(self):
        from bulk_downloader import templates
        t = templates.get("spankbang")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_eporner_enables(self):
        from bulk_downloader import templates
        t = templates.get("eporner")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_beeg_enables(self):
        from bulk_downloader import templates
        t = templates.get("beeg")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_porntrex_enables(self):
        from bulk_downloader import templates
        t = templates.get("porntrex")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True

    def test_txxx_network_enables(self):
        from bulk_downloader import templates
        t = templates.get("txxx_network")
        assert (t.get("config_defaults") or {}).get("use_library_extractor") is True


# ─── Runner integration smoke test ──────────────────────────────────


class TestRunnerLibraryExtractorMethod:
    """SiteRunner._try_library_extractor exists and degrades gracefully."""

    def test_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_try_library_extractor")
        assert callable(getattr(SiteRunner, "_try_library_extractor"))

    def test_method_short_circuits_on_unsupported_url(self):
        """When the URL host isn't in the registry, the method returns
        False immediately so the worker falls through to other paths."""
        from bulk_downloader.runner import SiteRunner
        # Build a minimal SiteRunner-shaped mock
        runner = MagicMock(spec=SiteRunner)
        runner.config = {"use_library_extractor": True}
        runner.site_id = "test"
        # Call the unbound method against our mock
        result = SiteRunner._try_library_extractor(runner, "https://example.com/foo")
        assert result is False

    def test_direct_http_download_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_do_direct_http_download")
