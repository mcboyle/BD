"""Tests for Row 949: Async Metadata Hydration Watcher for React and Next.js.

Acceptance criteria:
1. Parsing of Next.js (__NEXT_DATA__) and Nuxt (window.__NUXT__ / __NUXT_DATA__) state payloads.
2. Extraction of titles and stream URLs in <2ms without scraping DOM nodes.
3. Schema validation on extracted media metadata.
4. Robustness against malformed or non-JSON payloads.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import json
import time
import pytest

# Target module under test
try:
    from bulk_downloader.hydration_extractor import (
        HydratedMediaMetadata,
        HydrationExtractor,
        StreamSource,
        validate_metadata_schema,
    )
except ImportError:
    # Allow behavioral tests on base before module creation
    HydratedMediaMetadata = None
    HydrationExtractor = None
    StreamSource = None
    validate_metadata_schema = None


SAMPLE_NEXTJS_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Fallback DOM Title</title>
</head>
<body>
  <div id="__next">
    <div class="empty-loading-shell">Loading player...</div>
  </div>
  <script id="__NEXT_DATA__" type="application/json">
  {
    "props": {
      "pageProps": {
        "title": "Cosmic Voyage: Episode 4K Ultra HD",
        "description": "High-framerate interstellar documentary stream.",
        "duration": 3720.5,
        "thumbnail": "https://cdn.example.com/thumbnails/cosmic_4k.jpg",
        "media": {
          "streams": [
            {
              "url": "https://stream.example.com/hls/master.m3u8",
              "quality": "1080p",
              "format": "hls",
              "bitrate": 5500000
            },
            {
              "url": "https://stream.example.com/mp4/direct_4k.mp4",
              "quality": "2160p",
              "format": "mp4",
              "bitrate": 18000000
            }
          ]
        }
      }
    },
    "page": "/watch/[id]",
    "query": {"id": "cosmic-ep4"}
  }
  </script>
</body>
</html>
"""

SAMPLE_NUXT_JSON_HTML = """
<!DOCTYPE html>
<html>
<head><title>Nuxt Stream Host</title></head>
<body>
  <div id="__nuxt"></div>
  <script id="__NUXT_DATA__" type="application/json">
  {
    "data": {
      "scene_meta": {
        "title": "Oceanic Depths: The Abyss",
        "description": "Submersible 8K video capture.",
        "duration": 1840.0,
        "posterUrl": "https://cdn.example.com/poster/abyss.png",
        "videoSources": [
          {
            "src": "https://cdn.example.com/video/abyss_1080p.mp4",
            "resolution": "1080p",
            "format": "mp4"
          }
        ]
      }
    }
  }
  </script>
</body>
</html>
"""

SAMPLE_NUXT_WINDOW_HTML = """
<!DOCTYPE html>
<html>
<head><title>Nuxt Window State</title></head>
<body>
  <div id="__nuxt"></div>
  <script>
    window.__NUXT__ = {
      state: {
        currentRelease: {
          title: "Alpine Ridge Drone Flight",
          duration: 420.0,
          downloadOptions: [
            {
              "download_url": "https://media.example.com/drone_alpine.mp4",
              "quality": "4K",
              "format": "mp4"
            }
          ]
        }
      }
    };
  </script>
</body>
</html>
"""


class TestHydrationExtractorAcceptance:
    """Acceptance criteria verification for Row 949."""

    def test_behavioural_red_dom_scraping_fails_to_find_unrendered_stream(self):
        """Negative control: Traditional DOM inspection cannot find stream URLs
        or rich titles inside unrendered React/Next.js shell containers.
        """
        assert "__NEXT_DATA__" in SAMPLE_NEXTJS_HTML
        # In unrendered DOM, no <video>, <source>, or download <a> tags exist:
        assert "<video" not in SAMPLE_NEXTJS_HTML
        assert "<source" not in SAMPLE_NEXTJS_HTML
        assert "direct_4k.mp4" not in SAMPLE_NEXTJS_HTML.split("<script")[0]

    def test_module_must_be_implemented(self):
        """Verify bulk_downloader.hydration_extractor is implemented."""
        assert HydrationExtractor is not None, "bulk_downloader.hydration_extractor is not implemented"
        assert HydratedMediaMetadata is not None
        assert StreamSource is not None

    def test_parse_nextjs_hydration_payload(self):
        """(1a) Verify parsing of Next.js (__NEXT_DATA__) state payload."""
        extractor = HydrationExtractor()
        metadata = extractor.extract(SAMPLE_NEXTJS_HTML)

        assert metadata is not None
        assert metadata.framework == "nextjs"
        assert metadata.title == "Cosmic Voyage: Episode 4K Ultra HD"
        assert metadata.duration == 3720.5
        assert metadata.thumbnail_url == "https://cdn.example.com/thumbnails/cosmic_4k.jpg"
        assert len(metadata.stream_urls) == 2
        assert "https://stream.example.com/hls/master.m3u8" in metadata.stream_urls
        assert "https://stream.example.com/mp4/direct_4k.mp4" in metadata.stream_urls
        assert len(metadata.streams) == 2
        assert metadata.streams[0].quality in ("1080p", "2160p")

    def test_parse_nuxt_hydration_payloads(self):
        """(1b) Verify parsing of Nuxt (__NUXT_DATA__ and window.__NUXT__) payloads."""
        extractor = HydrationExtractor()

        # Nuxt 3 JSON payload
        meta_json = extractor.extract(SAMPLE_NUXT_JSON_HTML)
        assert meta_json is not None
        assert meta_json.framework == "nuxt"
        assert meta_json.title == "Oceanic Depths: The Abyss"
        assert "https://cdn.example.com/video/abyss_1080p.mp4" in meta_json.stream_urls

        # Nuxt 2 window.__NUXT__ assignment
        meta_win = extractor.extract(SAMPLE_NUXT_WINDOW_HTML)
        assert meta_win is not None
        assert meta_win.framework == "nuxt"
        assert meta_win.title == "Alpine Ridge Drone Flight"
        assert "https://media.example.com/drone_alpine.mp4" in meta_win.stream_urls

    def test_extraction_latency_under_two_milliseconds(self):
        """(2) Verify extraction of titles and stream URLs completes in <2ms."""
        extractor = HydrationExtractor()

        # Warm up JIT/bytecode
        extractor.extract(SAMPLE_NEXTJS_HTML)

        # Measure 50 iterations
        iterations = 50
        t0 = time.perf_counter()
        for _ in range(iterations):
            res = extractor.extract(SAMPLE_NEXTJS_HTML)
            assert res is not None
            assert len(res.stream_urls) > 0
        total_time_ms = (time.perf_counter() - t0) * 1000.0
        avg_time_ms = total_time_ms / iterations

        assert avg_time_ms < 2.0, f"Average extraction time {avg_time_ms:.3f}ms exceeded 2.0ms limit"

    def test_schema_validation(self):
        """(3) Verify schema validation logic for extracted media metadata."""
        extractor = HydrationExtractor()
        meta = extractor.extract(SAMPLE_NEXTJS_HTML)
        assert meta is not None

        # Valid metadata passes schema validation
        is_valid, errors = validate_metadata_schema(meta)
        assert is_valid is True
        assert len(errors) == 0

        # Invalid metadata (non-http URL, missing framework) is caught
        invalid_meta = HydratedMediaMetadata(
            framework="",
            title="Valid Title",
            stream_urls=["ftp://invalid-scheme.example.com/video.mp4"],
        )
        is_valid_inv, errors_inv = validate_metadata_schema(invalid_meta)
        assert is_valid_inv is False
        assert any("framework" in e.lower() for e in errors_inv)
        assert any("url" in e.lower() for e in errors_inv)

    def test_robustness_against_malformed_html(self):
        """(4) Gracefully handles malformed or non-state HTML without crashing."""
        extractor = HydrationExtractor()

        # Plain HTML without framework state
        res_empty = extractor.extract("<html><body><p>Hello world</p></body></html>")
        assert res_empty is None

        # Broken JSON inside script tag
        broken_html = '<html><script id="__NEXT_DATA__">{"broken json</script></html>'
        res_broken = extractor.extract(broken_html)
        assert res_broken is None

    def test_nuxt3_array_payload_extraction(self):
        """Verify extraction from Nuxt 3 serialized array structure."""
        extractor = HydrationExtractor()
        nuxt3_html = """
        <html>
        <script id="__NUXT_DATA__" type="application/json">
        [
          {"title": 1, "video_sources": 2},
          "Highland Aurora Night Sky",
          [
            {"url": "https://cdn.example.com/hls/aurora.m3u8", "quality": "1080p"}
          ]
        ]
        </script>
        </html>
        """
        meta = extractor.extract(nuxt3_html)
        assert meta is not None
        assert meta.framework == "nuxt"
        assert meta.title == "Highland Aurora Night Sky"
        assert "https://cdn.example.com/hls/aurora.m3u8" in meta.stream_urls

    def test_zero_login_safety_guarantee(self):
        """Verify extractor operates purely on offline serialized state (Fleet Rule 21)."""
        extractor = HydrationExtractor()
        assert not hasattr(extractor, "login")
        assert not hasattr(extractor, "authenticate")
        assert not hasattr(extractor, "session")

