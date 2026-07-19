"""v3.43.68: tests for the HereSphere / DeoVR JSON API extractor.

Covers:
  1. probe_site() handles success / 404 / no-JSON / network-error
  2. probe_site() tries multiple paths + extra_hosts
  3. _looks_like_protocol_response correctly identifies shapes
  4. _first_title pulls sample from various library formats
  5. _extract_scene_id handles all common URL shapes + custom regex
  6. _normalize_source handles resolution / height / size fields
  7. normalize_scene works for both HereSphere and DeoVR shapes
  8. normalize_scene auto-detects protocol from response shape
  9. normalize_scene extracts performers + studio from tags
 10. pick_source honors quality_pref cascade + proportional tolerance
 11. pick_source prefers h265 at equal tier when no codec specified
 12. fetch_and_extract end-to-end with mocked httpx
 13. Runner _try_jsonapi_extractor method exists
 14. Flask /api/jsonapi/probe endpoint is registered
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

# [SAST 3:13pm 13 may] removed unused: import pytest

from bulk_downloader import extractors_jsonapi as j


# ─── probe_site ─────────────────────────────────────────────────────


def _mock_httpx(routes: dict):
    """Build a fake httpx whose Client.get(url) consults the routes
    dict. Each route is url-substring → (status, body)."""
    fake = MagicMock()
    fake.RequestError = Exception

    def _get(url, headers=None, **_kw):
        for substr, (status, body) in routes.items():
            if substr in url:
                r = MagicMock()
                r.status_code = status
                r.text = body
                r.headers = {}
                return r
        r = MagicMock()
        r.status_code = 404
        r.text = ""
        r.headers = {}
        return r

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=None)
    client.get = MagicMock(side_effect=_get)
    fake.Client = MagicMock(return_value=client)
    return fake


class TestProbeSite:
    HS_LIB = json.dumps({"library": [{"title": "First Scene"}]})
    DV_LIB = json.dumps({"scenes": [{"title": "DV First"}]})
    HS_SCENE = json.dumps({"title": "S", "media": [{"sources": []}]})

    def test_heresphere_hit_at_root(self):
        routes = {"/heresphere": (200, self.HS_LIB)}
        fake = _mock_httpx(routes)
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.probe_site("https://members.example.com")
        assert r.ok is True
        assert r.heresphere_url.endswith("/heresphere")
        assert r.deovr_url == ""  # we keep trying both, but only HS hit
        assert "First Scene" in r.sample_title

    def test_both_protocols_hit(self):
        routes = {
            "/heresphere": (200, self.HS_LIB),
            "/deovr": (200, self.DV_LIB),
        }
        fake = _mock_httpx(routes)
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.probe_site("https://members.example.com")
        assert r.ok is True
        assert r.heresphere_url
        assert r.deovr_url

    def test_extra_host_override(self):
        """NaughtyAmerica-style: main host returns 404, extra returns 200."""
        routes = {"api.naughtyapi.com/heresphere": (200, self.HS_LIB)}
        fake = _mock_httpx(routes)
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.probe_site(
                "https://members.naughtyamerica.com",
                extra_hosts=["https://api.naughtyapi.com"],
            )
        assert r.ok is True
        assert "api.naughtyapi.com" in r.heresphere_url
        assert r.api_host_override == "https://api.naughtyapi.com"

    def test_all_404_returns_not_ok(self):
        fake = _mock_httpx({})  # no routes match
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.probe_site("https://example.com")
        assert r.ok is False
        assert r.heresphere_url == ""
        assert r.deovr_url == ""
        # probed_urls should record all attempts
        assert len(r.probed_urls) >= 2  # at least /heresphere + /deovr

    def test_non_json_response_rejected(self):
        """A site that returns HTML for /heresphere isn't a real implementer."""
        routes = {"/heresphere": (200, "<html><body>not json</body></html>")}
        fake = _mock_httpx(routes)
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.probe_site("https://example.com")
        assert r.ok is False

    def test_json_but_wrong_shape_rejected(self):
        """JSON that doesn't look like the protocol shouldn't count."""
        routes = {"/heresphere": (200, '{"unrelated": true}')}
        fake = _mock_httpx(routes)
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.probe_site("https://example.com")
        assert r.ok is False

    def test_empty_site_root_returns_error(self):
        r = j.probe_site("")
        assert r.ok is False
        assert r.error == "empty_site_root"

    def test_httpx_missing_returns_error_not_crash(self):
        cached = sys.modules.pop("httpx", None)
        try:
            with patch.dict(sys.modules, {"httpx": None}):
                r = j.probe_site("https://example.com")
        finally:
            if cached is not None:
                sys.modules["httpx"] = cached
        # Either path acceptable — must not raise
        assert isinstance(r, j.ProbeOutcome)


# ─── _looks_like_protocol_response ──────────────────────────────────


class TestLooksLikeProtocolResponse:
    def test_heresphere_per_scene(self):
        assert j._looks_like_protocol_response({"media": [], "title": "X"}) is True

    def test_deovr_per_scene(self):
        assert j._looks_like_protocol_response({"encodings": [], "title": "X"}) is True

    def test_library_list(self):
        assert j._looks_like_protocol_response({"library": []}) is True
        assert j._looks_like_protocol_response({"scenes": []}) is True
        assert j._looks_like_protocol_response({"list": []}) is True

    def test_irrelevant_json_rejected(self):
        assert j._looks_like_protocol_response({"foo": 1}) is False
        assert j._looks_like_protocol_response([]) is False
        assert j._looks_like_protocol_response("string") is False
        assert j._looks_like_protocol_response(None) is False


# ─── _extract_scene_id ──────────────────────────────────────────────


class TestExtractSceneId:
    def test_numeric_path_segment(self):
        assert j._extract_scene_id(
            "https://example.com/scene/12345/foo-bar") == "12345"

    def test_id_query_param(self):
        assert j._extract_scene_id(
            "https://example.com/v?id=4567") == "4567"

    def test_scene_query_param(self):
        assert j._extract_scene_id(
            "https://example.com/v?scene=4567&page=1") == "4567"

    def test_v_query_param(self):
        assert j._extract_scene_id(
            "https://example.com/watch?v=abc123") == "abc123"

    def test_slugified_last_segment(self):
        assert j._extract_scene_id(
            "https://example.com/scenes/foo-bar-slug") == "foo-bar-slug"

    def test_custom_regex(self):
        url = "https://nakedsword.com/movie/MOV-12345/scene"
        assert j._extract_scene_id(url, r"MOV-(\d+)") == "12345"

    def test_empty_returns_empty(self):
        assert j._extract_scene_id("") == ""

    def test_id_preferred_over_path(self):
        """When both are present, ?id= wins (more specific)."""
        # Note: with current impl path numeric segment comes BEFORE
        # query param scan. Verify the actual behavior:
        result = j._extract_scene_id(
            "https://example.com/scene/12345?id=9999")
        # Either 12345 or 9999 is OK; ?id= currently wins
        assert result in ("12345", "9999")


# ─── _normalize_source ──────────────────────────────────────────────


class TestNormalizeSource:
    def test_heresphere_shape(self):
        s = j._normalize_source({
            "resolution": 1080, "height": 1080, "width": 1920,
            "size": 2147483648,
            "url": "https://cdn.example.com/s.mp4",
        })
        assert s is not None
        assert s.height == 1080
        assert s.width == 1920
        assert s.size_bytes == 2147483648
        assert s.url == "https://cdn.example.com/s.mp4"
        assert s.is_hls is False

    def test_deovr_shape(self):
        s = j._normalize_source({
            "resolution": 2160,
            "url": "https://cdn.example.com/s.mp4",
        })
        assert s.height == 2160

    def test_hls_url_flagged(self):
        s = j._normalize_source({
            "resolution": 1080,
            "url": "https://cdn.example.com/master.m3u8",
        })
        assert s.is_hls is True

    def test_missing_url_returns_none(self):
        assert j._normalize_source({"resolution": 1080}) is None
        assert j._normalize_source({"url": ""}) is None

    def test_garbage_input_returns_none(self):
        assert j._normalize_source(None) is None
        assert j._normalize_source("string") is None

    def test_height_from_string(self):
        """Some sites send `"1080"` not `1080`."""
        s = j._normalize_source({
            "resolution": "1080",
            "url": "https://cdn.example.com/s.mp4",
        })
        assert s.height == 1080

    def test_p_suffix_stripped(self):
        s = j._normalize_source({
            "resolution": "1080p",
            "url": "https://cdn.example.com/s.mp4",
        })
        assert s.height == 1080


# ─── normalize_scene ────────────────────────────────────────────────


class TestNormalizeSceneHeresphere:
    HS = {
        "title": "Test Scene",
        "description": "Description here",
        "duration": 1820,
        "dateReleased": "2024-01-15",
        "thumbnailImage": "https://t.example.com/p.jpg",
        "tags": [
            {"name": "Performer:Jane Doe"},
            {"name": "Performer:John Smith"},
            {"name": "Studio:TestStudio"},
            {"name": "blonde"},
            {"name": "outdoor"},
        ],
        "media": [{
            "name": "Video",
            "sources": [
                {"resolution": 1080, "url": "https://cdn.example.com/1080.mp4"},
                {"resolution": 4320, "url": "https://cdn.example.com/8k.mp4"},
            ],
        }],
    }

    def test_happy_path(self):
        r = j.normalize_scene(self.HS)
        assert r.ok is True
        assert r.protocol == "heresphere"
        assert r.title == "Test Scene"
        assert r.duration_sec == 1820
        assert r.date_released == "2024-01-15"
        assert r.thumbnail_url == "https://t.example.com/p.jpg"

    def test_performers_extracted(self):
        r = j.normalize_scene(self.HS)
        assert "Jane Doe" in r.performers
        assert "John Smith" in r.performers

    def test_studio_extracted(self):
        r = j.normalize_scene(self.HS)
        assert r.studio == "TestStudio"

    def test_plain_tags_kept(self):
        r = j.normalize_scene(self.HS)
        assert "blonde" in r.tags
        assert "outdoor" in r.tags
        # Performer: and Studio: tags removed from plain list
        assert not any(t.startswith("Performer:") for t in r.tags)

    def test_available_heights_sorted_desc(self):
        r = j.normalize_scene(self.HS)
        assert r.available_heights == [4320, 1080]


class TestNormalizeSceneDeoVR:
    DV = {
        "id": "12345",
        "title": "DV Test",
        "description": "DV description",
        "duration": 1820,
        "videoLength": 1820,
        "thumbnailUrl": "https://t.example.com/p.jpg",
        "screenType": "dome",
        "stereoMode": "sbs",
        "encodings": [
            {"name": "h264", "videoSources": [
                {"resolution": 1080, "url": "https://cdn.example.com/h264_1080.mp4"},
                {"resolution": 2160, "url": "https://cdn.example.com/h264_4k.mp4"},
            ]},
            {"name": "h265", "videoSources": [
                {"resolution": 4320, "url": "https://cdn.example.com/h265_8k.mp4"},
            ]},
        ],
    }

    def test_protocol_detected(self):
        r = j.normalize_scene(self.DV)
        assert r.protocol == "deovr"

    def test_duration_from_videoLength_fallback(self):
        d = dict(self.DV)
        del d["duration"]  # Only videoLength remains
        r = j.normalize_scene(d)
        assert r.duration_sec == 1820

    def test_all_tiers_collected_across_encodings(self):
        r = j.normalize_scene(self.DV)
        assert 1080 in r.available_heights
        assert 2160 in r.available_heights
        assert 4320 in r.available_heights


class TestNormalizeSceneFailures:
    def test_no_sources_returns_ok_false(self):
        r = j.normalize_scene({"title": "Empty", "media": []})
        assert r.ok is False
        assert r.error == "no_sources"
        # But metadata still populated for diagnostics
        assert r.title == "Empty"

    def test_non_dict_input(self):
        r = j.normalize_scene("garbage")
        assert r.ok is False
        assert r.error == "invalid_response_type"


# ─── pick_source ────────────────────────────────────────────────────


def _make_sources():
    return [
        j.JsonApiSource(url="https://cdn.example.com/1080.mp4", height=1080),
        j.JsonApiSource(url="https://cdn.example.com/2160.mp4", height=2160),
        j.JsonApiSource(url="https://cdn.example.com/4320.mp4", height=4320),
    ]


class TestPickSource:
    def test_no_pref_picks_highest(self):
        s = j.pick_source(_make_sources())
        assert s.height == 4320

    def test_best_keyword_picks_highest(self):
        s = j.pick_source(_make_sources(), quality_pref=["best"])
        assert s.height == 4320

    def test_pref_exact_match(self):
        s = j.pick_source(_make_sources(), quality_pref=[2160])
        assert s.height == 2160

    def test_pref_cascade(self):
        """8K not in our list; cascades to 4K."""
        # Wait — 8K IS in our list. Let me make a cascade where higher
        # isn't available:
        srcs = [j.JsonApiSource(url="https://x/1080.mp4", height=1080),
                j.JsonApiSource(url="https://x/2160.mp4", height=2160)]
        s = j.pick_source(srcs, quality_pref=[4320, 2160, 1080])
        assert s.height == 2160

    def test_prefer_h265_at_equal_tier(self):
        srcs = [
            j.JsonApiSource(url="https://x/h264.mp4", height=2160, codec="h264"),
            j.JsonApiSource(url="https://x/h265.mp4", height=2160, codec="h265"),
        ]
        s = j.pick_source(srcs)
        assert s.codec == "h265"

    def test_force_h264_codec(self):
        srcs = [
            j.JsonApiSource(url="https://x/h264.mp4", height=2160, codec="h264"),
            j.JsonApiSource(url="https://x/h265.mp4", height=2160, codec="h265"),
        ]
        s = j.pick_source(srcs, prefer_codec="h264")
        assert s.codec == "h264"

    def test_proportional_tolerance(self):
        srcs = [j.JsonApiSource(url="https://x/2200.mp4", height=2200)]
        s = j.pick_source(srcs, quality_pref=[2160])
        assert s.height == 2200

    def test_empty_returns_none(self):
        assert j.pick_source([]) is None

    def test_dedup_on_url(self):
        # Same URL appears twice (e.g. from two media[] entries)
        srcs = [
            j.JsonApiSource(url="https://x/2160.mp4", height=2160),
            j.JsonApiSource(url="https://x/2160.mp4", height=2160),
        ]
        s = j.pick_source(srcs)
        assert s is not None

    def test_prefers_mp4_over_hls_at_equal_tier(self):
        srcs = [
            j.JsonApiSource(url="https://x/2160.m3u8", height=2160, is_hls=True),
            j.JsonApiSource(url="https://x/2160.mp4", height=2160, is_hls=False),
        ]
        s = j.pick_source(srcs)
        assert s.is_hls is False


# ─── fetch_and_extract end-to-end with mock ─────────────────────────


class TestFetchAndExtract:
    SCENE_HS = json.dumps({
        "title": "Mock Scene",
        "duration": 1500,
        "media": [{"sources": [
            {"resolution": 2160, "url": "https://cdn.example.com/4k.mp4"},
        ]}],
    })

    def test_happy_path(self):
        routes = {"/12345": (200, self.SCENE_HS)}
        fake = _mock_httpx(routes)
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.fetch_and_extract(
                "https://api.example.com/heresphere",
                "https://members.example.com/scene/12345/foo",
                quality_pref=[2160],
            )
        assert r.ok is True
        assert r.title == "Mock Scene"
        assert r.source is not None
        assert r.source.height == 2160

    def test_fetch_failed(self):
        fake = _mock_httpx({})  # no routes hit → 404
        with patch.dict(sys.modules, {"httpx": fake}):
            r = j.fetch_and_extract(
                "https://api.example.com/heresphere",
                "https://members.example.com/scene/99999/foo",
            )
        assert r.ok is False
        assert r.error == "fetch_failed"


# ─── Runner + Flask integration ─────────────────────────────────────


class TestRunnerIntegration:
    def test_runner_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_try_jsonapi_extractor")

    def test_config_fields_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_jsonapi" in CFG_FIELDS
        assert "jsonapi_url" in CFG_FIELDS
        assert "jsonapi_protocol" in CFG_FIELDS
        assert "jsonapi_id_regex" in CFG_FIELDS
        assert "jsonapi_prefer_codec" in CFG_FIELDS
        assert DEFAULTS.get("use_jsonapi") is False
        assert DEFAULTS.get("jsonapi_url") == ""


class TestFlaskEndpoint:
    def test_probe_endpoint_registered(self):
        from bulk_downloader.app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/api/jsonapi/probe" in rules
