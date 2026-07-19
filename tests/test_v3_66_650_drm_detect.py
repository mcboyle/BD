"""v3.66.650 -- S3.1: DRM / EME protection classifier (DETECTION ONLY).

drm_detect.classify_protection distinguishes DOWNLOADABLE encrypted playback (HLS
AES-128 with a fetchable key -- in scope, yt-dlp handles it) from CDM-DRM (Widevine /
PlayReady / FairPlay -- un-circumventable) and Clear Key, from an EME key-system
string, HLS/DASH manifest text, or a URL marker. netlog_classify carries the coarse
category on each MediaItem. Nothing here decrypts, strips, or defeats DRM.

Per DRM_EME_DETECTION_DECISION.md + TESTING_ETHICS_FRAME: synthetic fixtures only;
no live DRM endpoint is needed or permitted -- classification is byte/string-level.
"""
from __future__ import annotations

import bulk_downloader.drm_detect as dd
import bulk_downloader.netlog_classify as nc


# ---- key-system (EME) ----

def test_key_system_widevine_is_cdm():
    r = dd.classify_protection(key_system="com.widevine.alpha")
    assert r["system"] == "widevine" and r["category"] == dd.CAT_CDM, r


def test_key_system_playready_is_cdm():
    r = dd.classify_protection(key_system="com.microsoft.playready")
    assert r["system"] == "playready" and r["category"] == dd.CAT_CDM, r


def test_key_system_clearkey_is_clearkey_not_cdm():
    r = dd.classify_protection(key_system="org.w3.clearkey")
    assert r["system"] == "clearkey" and r["category"] == dd.CAT_CLEARKEY, r


# ---- HLS ----

def test_hls_aes128_with_fetchable_key_is_downloadable():
    m3u8 = ('#EXTM3U\n'
            '#EXT-X-KEY:METHOD=AES-128,URI="https://k.example/key.bin"\n'
            '#EXTINF:6,\nseg0.ts\n')
    r = dd.classify_protection(hls_text=m3u8)
    assert r["category"] == dd.CAT_AES, r
    assert r["system"] == "aes-128", r


def test_hls_fairplay_keyformat_is_cdm():
    m3u8 = ('#EXTM3U\n'
            '#EXT-X-KEY:METHOD=SAMPLE-AES,'
            'URI="skd://asset",KEYFORMAT="com.apple.streamingkeydelivery"\n')
    r = dd.classify_protection(hls_text=m3u8)
    assert r["category"] == dd.CAT_CDM and r["system"] == "fairplay", r


def test_hls_no_key_is_none():
    m3u8 = '#EXTM3U\n#EXTINF:6,\nseg0.ts\n#EXT-X-ENDLIST\n'
    r = dd.classify_protection(hls_text=m3u8)
    assert r["category"] == dd.CAT_NONE, r


# ---- DASH ----

def test_dash_widevine_contentprotection_is_cdm():
    mpd = ('<MPD><Period><AdaptationSet>'
           '<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>'
           '</AdaptationSet></Period></MPD>')
    r = dd.classify_protection(dash_text=mpd)
    assert r["category"] == dd.CAT_CDM and r["system"] == "widevine", r


def test_dash_cenc_scheme_only_is_encrypted_cdm():
    mpd = ('<MPD><ContentProtection '
           'schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc"/></MPD>')
    r = dd.classify_protection(dash_text=mpd)
    assert r["category"] == dd.CAT_CDM, r


# ---- URL marker ----

def test_url_marker_widevine_is_cdm():
    r = dd.classify_protection(url="https://lic.example/widevine/license")
    assert r["category"] == dd.CAT_CDM, r


def test_plain_media_url_is_none():
    r = dd.classify_protection(url="https://cdn.example/video/movie.mp4")
    assert r["category"] == dd.CAT_NONE, r


# ---- netlog_classify enrichment (existing drm bool unchanged; category added) ----

def test_netlog_carries_drm_category():
    cap = {"host": "ex.com", "network_log": [
        {"url": "https://cdn.ex.com/cenc/manifest.mpd",
         "response_headers": {"content-type": "application/dash+xml"},
         "response_status": 200},
        {"url": "https://cdn.ex.com/video/movie.mp4",
         "response_headers": {"content-type": "video/mp4"},
         "response_status": 200},
    ]}
    rep = nc.classify_network_log(cap)
    by_url = {i.url: i for i in rep.items}
    drm_item = by_url["https://cdn.ex.com/cenc/manifest.mpd"]
    plain = by_url["https://cdn.ex.com/video/movie.mp4"]
    # existing behavior preserved
    assert drm_item.drm is True and plain.drm is False
    # new category
    assert drm_item.drm_category == dd.CAT_CDM, drm_item.as_dict()
    assert plain.drm_category == dd.CAT_NONE, plain.as_dict()
    assert "drm_category" in plain.as_dict()
