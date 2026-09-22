"""Row 1041: Adaptive Streaming Manifest Parser (HLS/DASH).

Validates:
(1) StreamingManifestParser for HLS master and media playlists.
(2) StreamingManifestParser for MPEG-DASH MPD manifests (AdaptationSets, Representations, DRM).
(3) StreamingManifest data model, segment extraction, and variant resolution.
(4) Integration with bulk_downloader.dev_suite.capture_diag.manifest_probe.

RED on baseline: fails with explicit semantic AssertionError (capability missing), not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import streaming_manifest
except ImportError:
    streaming_manifest = None


def test_positive_control_manifest_probe_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline manifest diagnostic."""
    from bulk_downloader.dev_suite.capture_diag import manifest_probe

    sample_hls = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:9.009,\n"
        "segment1.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    res = manifest_probe(text=sample_hls)
    assert res["ok"] is True
    assert res["format"] == "hls"
    assert res["is_vod"] is True


def test_streaming_manifest_parser_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    assert streaming_manifest is not None, (
        "Row 1041 capability missing: Adaptive Streaming Manifest Parser (HLS/DASH) "
        "not implemented in bulk_downloader.streaming_manifest"
    )
    assert hasattr(streaming_manifest, "StreamingManifestParser"), (
        "Row 1041 component missing: bulk_downloader.streaming_manifest.StreamingManifestParser"
    )
    assert hasattr(streaming_manifest, "StreamingManifest"), (
        "Row 1041 component missing: bulk_downloader.streaming_manifest.StreamingManifest"
    )
    assert hasattr(streaming_manifest, "parse_streaming_manifest"), (
        "Row 1041 component missing: bulk_downloader.streaming_manifest.parse_streaming_manifest"
    )


def test_hls_master_playlist_parsing():
    """Verify HLS master playlist extraction of variants, resolution, codecs, and bandwidth."""
    assert streaming_manifest is not None, "streaming_manifest capability missing"
    from bulk_downloader.streaming_manifest import StreamingManifestParser

    master_content = """#EXTM3U
#EXT-X-VERSION:4
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio-aac",NAME="English",DEFAULT=YES,AUTOSELECT=YES,URI="audio/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2",FRAME-RATE=29.970,AUDIO="audio-aac"
360p/prog_index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",FRAME-RATE=29.970,AUDIO="audio-aac"
720p/prog_index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",FRAME-RATE=60.000,AUDIO="audio-aac"
1080p/prog_index.m3u8
"""
    parser = StreamingManifestParser()
    manifest = parser.parse(master_content, base_url="https://cdn.example.com/stream/master.m3u8")

    assert manifest.format == "hls"
    assert manifest.is_master is True
    assert len(manifest.variants) == 3

    # Ordered descending by bandwidth
    top = manifest.variants[0]
    assert top.bandwidth == 5000000
    assert top.resolution == "1920x1080"
    assert top.width == 1920
    assert top.height == 1080
    assert top.frame_rate == 60.0
    assert top.uri == "https://cdn.example.com/stream/1080p/prog_index.m3u8"

    # Best variant selection
    best_720 = manifest.get_best_variant(prefer_resolution="1280x720")
    assert best_720 is not None
    assert best_720.resolution == "1280x720"

    best_capped = manifest.get_best_variant(max_bandwidth=3000000)
    assert best_capped is not None
    assert best_capped.bandwidth == 2500000


def test_hls_media_playlist_segments_and_drm():
    """Verify HLS media playlist extraction of segment URLs, duration, and encryption keys."""
    assert streaming_manifest is not None, "streaming_manifest capability missing"
    from bulk_downloader.streaming_manifest import StreamingManifestParser

    media_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:100
#EXT-X-KEY:METHOD=AES-128,URI="https://auth.example.com/key.bin",IV=0x00000000000000000000000000000064
#EXTINF:9.009,
segment100.ts
#EXTINF:8.500,
segment101.ts
#EXT-X-BYTERANGE:1024@0
#EXTINF:4.250,
segment102.ts
#EXT-X-ENDLIST
"""
    parser = StreamingManifestParser()
    manifest = parser.parse(media_content, base_url="https://cdn.example.com/hls/playlist.m3u8")

    assert manifest.format == "hls"
    assert manifest.is_master is False
    assert manifest.is_vod is True
    assert manifest.is_live is False
    assert manifest.target_duration == 10.0
    assert manifest.total_duration == pytest.approx(21.759, rel=1e-3)
    assert len(manifest.segments) == 3

    s1 = manifest.segments[0]
    assert s1.uri == "https://cdn.example.com/hls/segment100.ts"
    assert s1.duration == 9.009
    assert s1.sequence_number == 100
    assert s1.key_method == "AES-128"
    assert s1.key_uri == "https://auth.example.com/key.bin"

    s3 = manifest.segments[2]
    assert s3.byte_range == "1024@0"


def test_dash_mpd_manifest_parsing():
    """Verify MPEG-DASH MPD manifest parsing for AdaptationSets, representations, and DRM."""
    assert streaming_manifest is not None, "streaming_manifest capability missing"
    from bulk_downloader.streaming_manifest import StreamingManifestParser

    dash_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT0H5M30.000S" minBufferTime="PT2S">
  <Period id="1" start="PT0S">
    <AdaptationSet id="0" contentType="video" mimeType="video/mp4" segmentAlignment="true">
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed">
        <cenc:pssh xmlns:cenc="urn:mpeg:cenc:2013">AAAAPnBzc2gAAAAA7e+LqXnWSs6jyCfc1R0h7QAAAA4KBgICAAA=</cenc:pssh>
      </ContentProtection>
      <SegmentTemplate initialization="video/$RepresentationID$/init.mp4" media="video/$RepresentationID$/seg-$Number$.m4s" timescale="1000" startNumber="1"/>
      <Representation id="video-720p" bandwidth="2000000" width="1280" height="720" frameRate="30"/>
      <Representation id="video-1080p" bandwidth="4500000" width="1920" height="1080" frameRate="60"/>
    </AdaptationSet>
    <AdaptationSet id="1" contentType="audio" mimeType="audio/mp4" lang="en">
      <SegmentTemplate initialization="audio/init.mp4" media="audio/seg-$Number$.m4s" timescale="1000" startNumber="1"/>
      <Representation id="audio-128k" bandwidth="128000" audioSamplingRate="48000"/>
    </AdaptationSet>
  </Period>
</MPD>
"""
    parser = StreamingManifestParser()
    manifest = parser.parse(dash_xml, base_url="https://video.example.com/dash/manifest.mpd")

    assert manifest.format == "dash"
    assert manifest.is_master is True
    assert manifest.is_vod is True
    assert manifest.is_live is False
    assert manifest.total_duration == pytest.approx(330.0, rel=1e-3)

    # DRM scheme Widevine detected
    assert any("edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" in d for d in manifest.drm_schemes)

    # 2 video representations as variants
    assert len(manifest.variants) == 2
    top = manifest.variants[0]
    assert top.stream_id == "video-1080p"
    assert top.bandwidth == 4500000
    assert top.width == 1920
    assert top.height == 1080


def test_hls_drm_schemes_multi_key_methods():
    """F1/F2: drm_schemes must report ALL key methods seen, not just the last one."""
    assert streaming_manifest is not None, "streaming_manifest capability missing"
    from bulk_downloader.streaming_manifest import StreamingManifestParser

    media_content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"https://key.example.com/k1.bin\"\n"
        "#EXTINF:10.0,\n"
        "seg0.ts\n"
        "#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"https://key.example.com/k2.bin\"\n"
        "#EXTINF:10.0,\n"
        "seg1.ts\n"
        "#EXT-X-KEY:METHOD=NONE\n"
        "#EXTINF:10.0,\n"
        "seg2.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    parser = StreamingManifestParser()
    manifest = parser.parse(media_content)

    assert sorted(manifest.drm_schemes) == ["AES-128", "NONE", "SAMPLE-AES"]


def test_dash_fallback_no_adaptation_excludes_audio():
    """F3: DASH fallback path (no AdaptationSet) must not include audio-only Representations as video variants."""
    assert streaming_manifest is not None, "streaming_manifest capability missing"
    from bulk_downloader.streaming_manifest import StreamingManifestParser

    dash_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT1M">\n'
        '  <Period id="1">\n'
        '    <Representation id="v1" bandwidth="2000000" width="1280" height="720" '
        'mimeType="video/mp4" codecs="avc1.4d401f"/>\n'
        '    <Representation id="a1" bandwidth="128000" '
        'mimeType="audio/mp4" codecs="mp4a.40.2" audioSamplingRate="48000"/>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    parser = StreamingManifestParser()
    manifest = parser.parse(dash_xml)

    assert len(manifest.variants) == 1
    assert manifest.variants[0].stream_id == "v1"


def test_manifest_probe_product_caller_integration():
    """Verify bulk_downloader.dev_suite.capture_diag.manifest_probe utilizes the streaming manifest parser."""
    assert streaming_manifest is not None, "streaming_manifest capability missing"
    from bulk_downloader.dev_suite.capture_diag import manifest_probe

    hls_master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=854x480
480p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
720p/index.m3u8
"""
    res = manifest_probe(text=hls_master)
    assert res["ok"] is True
    assert res["format"] == "hls"
    assert res["playlist_kind"] == "master"
    assert res["variant_count"] == 2
    assert "streaming_manifest" in res
    assert res["streaming_manifest"]["format"] == "hls"
