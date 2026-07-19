"""v3.66.651 -- S3.1 follow-on: manifest-body DRM category classification.

deep_detect.manifests parse_hls_master / parse_dash_mpd now carry drm_category
(none / downloadable-aes / clearkey / cdm-drm) + drm_system alongside the existing
drm_or_encryption_detected bool, by folding in drm_detect.classify_protection at the
(unguarded) manifest-body parse site. Detection only; never circumvents.

Synthetic fixtures only (TESTING_ETHICS_FRAME): classification is string/XML-level.
"""
from __future__ import annotations

from bulk_downloader.deep_detect.manifests import parse_hls_master, parse_dash_mpd
import bulk_downloader.drm_detect as dd


def test_hls_media_aes128_fetchable_is_downloadable():
    m3u8 = ('#EXTM3U\n'
            '#EXT-X-KEY:METHOD=AES-128,URI="https://k.example/key.bin"\n'
            '#EXTINF:6,\nseg0.ts\n#EXT-X-ENDLIST\n')
    out = parse_hls_master(m3u8)
    assert out["kind"] == "hls_media", out
    assert out["drm_or_encryption_detected"] is True
    assert out["drm_category"] == dd.CAT_AES, out
    assert out["drm_system"] == "aes-128", out


def test_hls_master_fairplay_session_key_is_cdm():
    m3u8 = ('#EXTM3U\n'
            '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://a",'
            'KEYFORMAT="com.apple.streamingkeydelivery"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=640x360\n'
            'var.m3u8\n')
    out = parse_hls_master(m3u8)
    assert out["kind"] == "hls_master", out
    assert out["drm_or_encryption_detected"] is True
    assert out["drm_category"] == dd.CAT_CDM, out
    assert out["drm_system"] == "fairplay", out


def test_hls_plain_media_is_none():
    m3u8 = '#EXTM3U\n#EXTINF:6,\nseg0.ts\n#EXT-X-ENDLIST\n'
    out = parse_hls_master(m3u8)
    assert out["drm_or_encryption_detected"] is False
    assert out["drm_category"] == dd.CAT_NONE, out
    assert out["drm_system"] is None, out


def test_dash_widevine_contentprotection_is_cdm():
    mpd = ('<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet>'
           '<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>'
           '<Representation id="1" bandwidth="1000" width="640" height="360"/>'
           '</AdaptationSet></Period></MPD>')
    out = parse_dash_mpd(mpd)
    assert out["kind"] == "dash_mpd", out
    assert out["drm_or_encryption_detected"] is True
    assert out["drm_category"] == dd.CAT_CDM, out
    assert out["drm_system"] == "widevine", out


def test_dash_plain_is_none():
    mpd = ('<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet>'
           '<Representation id="1" bandwidth="1000" width="640" height="360"/>'
           '</AdaptationSet></Period></MPD>')
    out = parse_dash_mpd(mpd)
    assert out["drm_or_encryption_detected"] is False
    assert out["drm_category"] == dd.CAT_NONE, out
