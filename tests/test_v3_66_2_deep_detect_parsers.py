"""v3.66.2 — tests for the deep_detect items 1-7:
HLS master parsing, DASH MPD parsing, state-blob extraction,
provider embed extraction, player config extraction, login scoring,
and the honeypot finder.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ════════════════════════════════════════════════════════════════════
# Item 1 — HLS master playlist parsing
# ════════════════════════════════════════════════════════════════════


_HLS_MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",LANGUAGE="en",DEFAULT=YES,URI="aud-en.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="Espanol",LANGUAGE="es",DEFAULT=NO,URI="aud-es.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",DEFAULT=YES,URI="subs-en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,CODECS="avc1.640028",FRAME-RATE=60,AUDIO="aac"
v1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=18000000,RESOLUTION=3840x2160,CODECS="hvc1.2.4.L150.B0",FRAME-RATE=60,AUDIO="aac"
v2160.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=85000000,RESOLUTION=7680x4320,CODECS="hvc1.2.4.L180.B0",FRAME-RATE=60,AUDIO="aac"
v4320.m3u8
"""


def test_hls_master_detects_kind():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER)
    assert r["kind"] == "hls_master"


def test_hls_master_extracts_three_variants():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER)
    assert len(r["variants"]) == 3


def test_hls_master_ranks_8k_above_4k_above_1080p():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER)
    assert r["variants"][0]["resolution"]["label"] == "8k"
    assert r["variants"][1]["resolution"]["label"] == "4k"
    assert r["variants"][2]["resolution"]["label"] == "1080p"


def test_hls_master_extracts_dimensions_and_bandwidth():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER)
    top = r["variants"][0]
    assert top["width"] == 7680
    assert top["height"] == 4320
    assert top["bandwidth"] == 85_000_000
    assert top["frame_rate"] == 60.0


def test_hls_master_resolves_relative_variant_urls():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER,
                         base_url="https://cdn.example.com/v/")
    assert r["variants"][0]["url"] == \
        "https://cdn.example.com/v/v4320.m3u8"


def test_hls_master_extracts_alt_audio_tracks():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER,
                         base_url="https://cdn.example.com/v/")
    assert len(r["audio"]) == 2
    langs = sorted(a["lang"] for a in r["audio"])
    assert langs == ["en", "es"]
    en = next(a for a in r["audio"] if a["lang"] == "en")
    assert en["default"] is True
    assert en["url"] == "https://cdn.example.com/v/aud-en.m3u8"


def test_hls_master_extracts_subtitles():
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master(_HLS_MASTER)
    assert len(r["subtitles"]) == 1
    assert r["subtitles"][0]["lang"] == "en"


def test_hls_master_flags_session_key_encryption():
    from bulk_downloader.deep_detect import parse_hls_master
    enc = ("#EXTM3U\n"
           '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://x"\n'
           "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n"
           "v.m3u8\n")
    r = parse_hls_master(enc)
    assert r["drm_or_encryption_detected"] is True


def test_hls_master_flags_low_latency():
    from bulk_downloader.deep_detect import parse_hls_master
    ll = ("#EXTM3U\n"
          "#EXT-X-PART-INF:PART-TARGET=0.33\n"
          "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n"
          "v.m3u8\n")
    r = parse_hls_master(ll)
    assert r["low_latency"] is True


def test_hls_media_playlist_is_recognized_but_no_variants():
    from bulk_downloader.deep_detect import parse_hls_master
    media = ("#EXTM3U\n"
             "#EXT-X-TARGETDURATION:6\n"
             "#EXTINF:6.0,\n"
             "seg-000.ts\n"
             "#EXTINF:6.0,\n"
             "seg-001.ts\n")
    r = parse_hls_master(media)
    assert r["kind"] == "hls_media"
    assert r["variants"] == []


def test_hls_master_returns_not_hls_for_non_m3u8():
    from bulk_downloader.deep_detect import parse_hls_master
    assert parse_hls_master("<html></html>")["kind"] == "not_hls"
    assert parse_hls_master("")["kind"] == "not_hls"


def test_hls_master_handles_bom():
    """Some CDNs emit a UTF-8 BOM in front of #EXTM3U."""
    from bulk_downloader.deep_detect import parse_hls_master
    r = parse_hls_master("\ufeff" + _HLS_MASTER)
    assert r["kind"] == "hls_master"
    assert len(r["variants"]) == 3


# ════════════════════════════════════════════════════════════════════
# Item 2 — DASH MPD parsing
# ════════════════════════════════════════════════════════════════════


_DASH_MPD = """<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"
     type="static"
     profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
 <Period>
  <AdaptationSet mimeType="video/mp4" contentType="video">
   <Representation id="1080" width="1920" height="1080"
                   bandwidth="8000000" codecs="avc1.640028"/>
   <Representation id="2160" width="3840" height="2160"
                   bandwidth="18000000" codecs="hvc1.2.4.L150.B0"/>
   <Representation id="4320" width="7680" height="4320"
                   bandwidth="85000000" codecs="hvc1.2.4.L180.B0"/>
  </AdaptationSet>
  <AdaptationSet mimeType="audio/mp4" lang="en">
   <Representation id="aud-128" bandwidth="128000" codecs="mp4a.40.2"/>
   <Representation id="aud-256" bandwidth="256000" codecs="mp4a.40.2"/>
  </AdaptationSet>
 </Period>
</MPD>"""


def test_dash_detects_kind():
    from bulk_downloader.deep_detect import parse_dash_mpd
    assert parse_dash_mpd(_DASH_MPD)["kind"] == "dash_mpd"


def test_dash_extracts_three_video_representations():
    from bulk_downloader.deep_detect import parse_dash_mpd
    r = parse_dash_mpd(_DASH_MPD)
    assert len(r["video"]) == 3


def test_dash_ranks_8k_top():
    from bulk_downloader.deep_detect import parse_dash_mpd
    r = parse_dash_mpd(_DASH_MPD)
    assert r["video"][0]["resolution"]["label"] == "8k"
    assert r["video"][0]["width"] == 7680


def test_dash_extracts_audio_sorted_by_bandwidth():
    from bulk_downloader.deep_detect import parse_dash_mpd
    r = parse_dash_mpd(_DASH_MPD)
    assert len(r["audio"]) == 2
    assert r["audio"][0]["bandwidth"] >= r["audio"][1]["bandwidth"]
    assert r["audio"][0]["lang"] == "en"


def test_dash_flags_drm_when_content_protection_present():
    from bulk_downloader.deep_detect import parse_dash_mpd
    drm = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
 <Period>
  <AdaptationSet mimeType="video/mp4">
   <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
   <Representation id="1" width="1920" height="1080" bandwidth="1"/>
  </AdaptationSet>
 </Period>
</MPD>"""
    r = parse_dash_mpd(drm)
    assert r["drm_or_encryption_detected"] is True


def test_dash_flags_low_latency_profile():
    from bulk_downloader.deep_detect import parse_dash_mpd
    ll = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"
     profiles="urn:mpeg:dash:profile:isoff-live:2011 http://example.com/low-latency">
 <Period><AdaptationSet mimeType="video/mp4">
   <Representation id="1" width="1280" height="720" bandwidth="1"/>
 </AdaptationSet></Period></MPD>"""
    r = parse_dash_mpd(ll)
    assert r["low_latency"] is True


def test_dash_returns_not_dash_for_html():
    from bulk_downloader.deep_detect import parse_dash_mpd
    assert parse_dash_mpd("<html></html>")["kind"] == "not_dash"
    assert parse_dash_mpd("")["kind"] == "not_dash"


def test_dash_handles_malformed_xml_gracefully():
    from bulk_downloader.deep_detect import parse_dash_mpd
    r = parse_dash_mpd("<?xml version='1.0'?><MPD><bad")
    # Not_dash because it didn't parse; warning recorded
    assert r["kind"] in ("not_dash", "dash_mpd")
    if r["kind"] == "dash_mpd":
        assert r["warnings"]


def test_dash_multi_period_aggregation():
    from bulk_downloader.deep_detect import parse_dash_mpd
    multi = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
 <Period id="p1">
  <AdaptationSet mimeType="video/mp4">
   <Representation id="a" width="1920" height="1080" bandwidth="5"/>
  </AdaptationSet>
 </Period>
 <Period id="p2">
  <AdaptationSet mimeType="video/mp4">
   <Representation id="b" width="3840" height="2160" bandwidth="10"/>
  </AdaptationSet>
 </Period>
</MPD>"""
    r = parse_dash_mpd(multi)
    assert len(r["video"]) == 2
    # 4K from period 2 should sort above 1080p from period 1
    assert r["video"][0]["width"] == 3840


# ════════════════════════════════════════════════════════════════════
# Item 3 — State-blob extractor
# ════════════════════════════════════════════════════════════════════


def test_nextjs_data_blob_yields_media_urls():
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"video":{
  "playbackUrl":"https://cdn.example.com/v.m3u8",
  "downloadUrl":"https://cdn.example.com/v-2160.mp4",
  "sources":[{"src":"https://cdn.example.com/v-1080.mp4","label":"1080p"}]
}}}}
</script></body></html>"""
    results = extract_state_blob_urls(html, base_url="https://example.com/")
    urls = {r["url"] for r in results}
    assert "https://cdn.example.com/v.m3u8" in urls
    assert "https://cdn.example.com/v-2160.mp4" in urls
    assert "https://cdn.example.com/v-1080.mp4" in urls


def test_nuxt_blob_extraction():
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><body>
<script>
window.__NUXT__ = {"state":{"playback":{"hlsUrl":"https://cdn.x.com/master.m3u8"}}};
</script>
</body></html>"""
    results = extract_state_blob_urls(html, base_url="https://x.com/")
    urls = {r["url"] for r in results}
    assert "https://cdn.x.com/master.m3u8" in urls


def test_jsonld_video_object_extraction():
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><head>
<script type="application/ld+json">
{"@type":"VideoObject",
 "name":"Test",
 "contentUrl":"https://cdn.x.com/v.mp4",
 "thumbnailUrl":"https://cdn.x.com/v-thumb.jpg",
 "embedUrl":"https://embed.x.com/v"}
</script>
</head><body></body></html>"""
    results = extract_state_blob_urls(html)
    urls = {r["url"] for r in results}
    assert "https://cdn.x.com/v.mp4" in urls
    assert "https://cdn.x.com/v-thumb.jpg" in urls


def test_state_blob_dedup_keeps_first_url():
    """The same URL appearing in multiple keys produces one entry."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"src":"https://cdn.x.com/v.mp4","downloadUrl":"https://cdn.x.com/v.mp4"}
</script></body></html>"""
    results = extract_state_blob_urls(html)
    urls = [r["url"] for r in results]
    assert urls == ["https://cdn.x.com/v.mp4"]


def test_state_blob_walks_nested_arrays():
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"qualities":[
  {"src":"https://cdn.x.com/720.mp4","label":"720p"},
  {"src":"https://cdn.x.com/1080.mp4","label":"1080p"},
  {"src":"https://cdn.x.com/2160.mp4","label":"2160p"}
]}
</script></body></html>"""
    results = extract_state_blob_urls(html)
    assert len(results) == 3


def test_state_blob_url_shaped_string_detection():
    """A non-media-key string that LOOKS like a media URL (ends in a
    known extension) is still picked up."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"metadata":"Watch at https://cdn.x.com/v.mp4 for free"}
</script></body></html>"""
    results = extract_state_blob_urls(html)
    urls = {r["url"] for r in results}
    assert "https://cdn.x.com/v.mp4" in urls


def test_state_blob_malformed_json_is_skipped():
    """A state-blob script with malformed JSON should not crash; it
    should just yield no results from that blob."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = """<html><body>
<script id="__NEXT_DATA__" type="application/json">
{not valid json at all
</script>
<script id="__APOLLO_STATE__" type="application/json">
{"src":"https://cdn.x.com/good.mp4"}
</script>
</body></html>"""
    results = extract_state_blob_urls(html)
    assert any(r["url"] == "https://cdn.x.com/good.mp4" for r in results)


def test_state_blob_empty_html_returns_empty():
    from bulk_downloader.deep_detect import extract_state_blob_urls
    assert extract_state_blob_urls("") == []
    assert extract_state_blob_urls("<html></html>") == []


# ════════════════════════════════════════════════════════════════════
# Item 4 — Provider embed extractor
# ════════════════════════════════════════════════════════════════════


def test_youtube_embed_iframe():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>'
    embeds = extract_provider_embeds(html)
    assert any(e["provider"] == "youtube"
               and e["ids"].get("video_id") == "dQw4w9WgXcQ"
               for e in embeds)


def test_vimeo_player_iframe():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = '<iframe src="https://player.vimeo.com/video/76979871"></iframe>'
    embeds = extract_provider_embeds(html)
    vimeo = next(e for e in embeds if e["provider"] == "vimeo")
    assert vimeo["ids"]["clip_id"] == "76979871"


def test_brightcove_video_js_attrs():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = """<video-js data-account="6118377982001"
                       data-player="default"
                       data-video-id="6356900250"
                       class="brightcove"></video-js>"""
    embeds = extract_provider_embeds(html)
    bc = next(e for e in embeds if e["provider"] == "brightcove")
    assert bc["ids"]["account_id"] == "6118377982001"
    assert bc["ids"]["video_id"] == "6356900250"


def test_wistia_async_class():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = '<div class="wistia_embed wistia_async_abc123def"></div>'
    embeds = extract_provider_embeds(html)
    w = next(e for e in embeds if e["provider"] == "wistia")
    assert w["ids"]["hashed_id"] == "abc123def"


def test_kaltura_kwidget_in_script():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = """<script>
kWidget.embed({targetId:"v", wid:"_2007281",
               uiconf_id:50058932, entry_id:"1_abc"});
</script>"""
    embeds = extract_provider_embeds(html)
    k = next(e for e in embeds if e["provider"] == "kaltura")
    assert k["ids"]["entry_id"] == "1_abc"
    assert k["ids"]["uiconf_id"] == "50058932"


def test_mux_stream_url():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = '<video src="https://stream.mux.com/abc123XYZ.m3u8"></video>'
    embeds = extract_provider_embeds(html)
    m = next(e for e in embeds if e["provider"] == "mux")
    assert m["ids"]["playback_id"] == "abc123XYZ"


def test_cloudflare_stream_url():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = ('<iframe src="https://videodelivery.net/abc123/'
            'manifest/video.m3u8"></iframe>')
    embeds = extract_provider_embeds(html)
    cf = next(e for e in embeds if e["provider"] == "cloudflare_stream")
    assert cf["ids"]["video_id"] == "abc123"


def test_youtube_short_url():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = '<a href="https://youtu.be/dQw4w9WgXcQ">watch</a>'
    embeds = extract_provider_embeds(html)
    yt = next(e for e in embeds if e["provider"] == "youtube")
    assert yt["ids"]["video_id"] == "dQw4w9WgXcQ"


def test_multiple_providers_on_same_page():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = """<html><body>
<iframe src="https://www.youtube.com/embed/yt1"></iframe>
<iframe src="https://player.vimeo.com/video/123"></iframe>
<video src="https://stream.mux.com/muxabc.m3u8"></video>
</body></html>"""
    providers = {e["provider"] for e in extract_provider_embeds(html)}
    assert providers >= {"youtube", "vimeo", "mux"}


def test_provider_embed_dedup_by_id_set():
    """Same provider referenced multiple ways should produce ONE
    entry — the iframe is the primary source."""
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = """<html><body>
<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
<a href="https://youtu.be/dQw4w9WgXcQ">link</a>
</body></html>"""
    yts = [e for e in extract_provider_embeds(html)
           if e["provider"] == "youtube"]
    assert len(yts) == 1


def test_provider_no_match_returns_empty_list():
    from bulk_downloader.deep_detect import extract_provider_embeds
    html = '<html><body><a href="/page">link</a></body></html>'
    assert extract_provider_embeds(html) == []


# ════════════════════════════════════════════════════════════════════
# Item 5 — Player config extractor
# ════════════════════════════════════════════════════════════════════


def test_videojs_data_setup_sources():
    from bulk_downloader.deep_detect import extract_player_configs
    html = """<video class="video-js"
        data-setup='{"sources":[
            {"src":"v-1080.mp4","type":"video/mp4","label":"1080p"},
            {"src":"v-2160.mp4","type":"video/mp4","label":"4K"}]}'>
</video>"""
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert len(cfgs) == 2
    assert all(c["library"] == "videojs" for c in cfgs)
    urls = [c["url"] for c in cfgs]
    assert "https://x.com/v-1080.mp4" in urls
    assert "https://x.com/v-2160.mp4" in urls


def test_videojs_source_elements():
    from bulk_downloader.deep_detect import extract_player_configs
    html = """<video class="video-js">
  <source src="v.mp4" type="video/mp4" label="1080p">
  <source src="v.m3u8" type="application/x-mpegURL">
</video>"""
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert len(cfgs) >= 2


def test_jwplayer_setup_sources():
    from bulk_downloader.deep_detect import extract_player_configs
    html = """<script>
jwplayer("p").setup({sources:[
  {file:"https://cdn.x.com/v.m3u8", label:"Auto"},
  {file:"v-720.mp4", label:"720p"}
]});</script>"""
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    libs = {c["library"] for c in cfgs}
    assert "jwplayer" in libs
    urls = {c["url"] for c in cfgs}
    assert "https://cdn.x.com/v.m3u8" in urls


def test_html5_video_without_videojs_class():
    """A plain <video><source> outside of Video.js should still be
    extracted, tagged as html5_video."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = """<video><source src="v.webm" type="video/webm"></video>"""
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert any(c["library"] == "html5_video" for c in cfgs)


def test_plyr_youtube_provider():
    from bulk_downloader.deep_detect import extract_player_configs
    html = ('<div data-plyr-provider="youtube" '
            'data-plyr-embed-id="dQw4w9WgXcQ"></div>')
    cfgs = extract_player_configs(html)
    assert cfgs[0]["library"] == "plyr"
    assert cfgs[0]["source_type"] == "youtube_embed"
    assert cfgs[0]["url"].endswith("/dQw4w9WgXcQ")


def test_player_config_dedup_by_url():
    """The same URL appearing in two player configs should produce
    one entry."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = """<video class="video-js"
        data-setup='{"sources":[{"src":"v.mp4","type":"video/mp4"}]}'>
  <source src="v.mp4" type="video/mp4">
</video>"""
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert len(cfgs) == 1


def test_videojs_label_drives_quality_detection():
    from bulk_downloader.deep_detect import extract_player_configs
    html = """<video class="video-js"
        data-setup='{"sources":[
          {"src":"v.mp4","type":"video/mp4","label":"4K UHD"}]}'>
</video>"""
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert cfgs[0]["quality"]["label"] == "4k"


def test_player_no_player_returns_empty():
    from bulk_downloader.deep_detect import extract_player_configs
    assert extract_player_configs("<html></html>") == []
    assert extract_player_configs("") == []


# ════════════════════════════════════════════════════════════════════
# Item 6 — Login scorer
# ════════════════════════════════════════════════════════════════════


_CLEAN_LOGIN_HTML = """<html><body>
<form action="/auth/login" method="POST">
  <input type="email" name="email" id="email">
  <input type="password" name="password" id="password">
  <input type="hidden" name="_csrf" value="x">
  <button type="submit" id="signin">Sign in</button>
</form></body></html>"""


def test_clean_login_form_high_confidence():
    from bulk_downloader.deep_detect import score_login_page
    r = score_login_page(_CLEAN_LOGIN_HTML)
    assert r["best"]["confidence"] >= 80
    assert "form_password" in r["best"]["login_types"]


def test_clean_login_captures_csrf_in_safe_fields():
    from bulk_downloader.deep_detect import score_login_page
    r = score_login_page(_CLEAN_LOGIN_HTML)
    safe = r["best"]["safe_fields"]
    assert "_csrf" in safe
    assert safe["_csrf"] == "token"


def test_clean_login_submit_selector():
    from bulk_downloader.deep_detect import score_login_page
    r = score_login_page(_CLEAN_LOGIN_HTML)
    assert r["best"]["submit_selector"] == "#signin"


def test_login_honeypot_excluded_from_safe_fields():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/login" method="POST">
  <input type="text" name="website" style="display:none">
  <input type="email" name="email">
  <input type="password" name="password">
  <button type="submit">Login</button>
</form></body></html>"""
    r = score_login_page(html)
    assert "website" in r["best"]["honeypot_fields"]
    assert "website" not in r["best"]["safe_fields"]


def test_login_recaptcha_flags_do_not_auto_submit():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/login" method="POST">
  <input type="email" name="email">
  <input type="password" name="password">
  <div class="g-recaptcha" data-sitekey="abc"></div>
  <button type="submit">Login</button>
</form>
<script>grecaptcha.ready(function(){});</script>
</body></html>"""
    r = score_login_page(html)
    assert r["best"]["do_not_auto_submit"] is True
    assert any("recaptcha" in d.lower()
               for d in r["best"]["bot_defenses"])


def test_login_turnstile_flagged():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/login" method="POST">
  <input type="email" name="email">
  <input type="password" name="password">
  <div class="cf-turnstile" data-sitekey="abc"></div>
  <button type="submit">Login</button>
</form></body></html>"""
    r = score_login_page(html)
    assert r["best"]["do_not_auto_submit"] is True


def test_login_passwordless_classification():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/api/magic-link" method="POST">
  <h1>Sign in</h1>
  <p>Enter your email and we'll send you a magic link.</p>
  <input type="email" name="email">
  <button type="submit">Email me a link</button>
</form></body></html>"""
    r = score_login_page(html)
    assert "passwordless" in r["best"]["login_types"]


def test_login_sso_classification():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<a href="/oauth/authorize?client_id=x&redirect_uri=y&response_type=code">
  Sign in with Google
</a></body></html>"""
    r = score_login_page(html)
    # No form, so candidate is synthesized at page level.
    assert r["best"] is not None
    assert "sso_oauth" in r["best"]["login_types"]


def test_login_saml_classification():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/sso/saml/login" method="POST">
  <input type="hidden" name="SAMLRequest" value="...">
  <input type="hidden" name="RelayState" value="...">
</form></body></html>"""
    r = score_login_page(html)
    assert "saml" in r["best"]["login_types"]


def test_login_webauthn_classification():
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<button onclick="navigator.credentials.get({publicKey:{}})">
  Use your passkey
</button>
<script>
if (window.PublicKeyCredential) { /* webauthn flow */ }
</script></body></html>"""
    r = score_login_page(html)
    assert r["best"] is not None
    assert "webauthn" in r["best"]["login_types"]


def test_login_search_form_does_not_score_high():
    """A search form looks superficially like a login (text input +
    button) but has no password and no login terms. Should NOT score
    as a login candidate."""
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/search" method="GET">
  <input type="text" name="q" placeholder="Search">
  <button type="submit">Search</button>
</form></body></html>"""
    r = score_login_page(html)
    if r["best"]:
        assert r["best"]["confidence"] < 40


def test_login_method_post_scored_higher_than_get():
    from bulk_downloader.deep_detect import score_login_page
    get_form = _CLEAN_LOGIN_HTML.replace('method="POST"', 'method="GET"')
    r_post = score_login_page(_CLEAN_LOGIN_HTML)
    r_get = score_login_page(get_form)
    # Both clamp to 100, but raw_score preserves the distinction.
    assert r_post["best"]["raw_score"] > r_get["best"]["raw_score"]


def test_login_no_form_returns_no_best_candidate():
    from bulk_downloader.deep_detect import score_login_page
    html = "<html><body><p>just a page</p></body></html>"
    r = score_login_page(html)
    assert r["best"] is None


def test_login_multiple_forms_picks_highest_confidence():
    """A page with a newsletter signup AND a login form should
    return the login as best."""
    from bulk_downloader.deep_detect import score_login_page
    html = """<html><body>
<form action="/newsletter" method="POST">
  <input type="email" name="newsletter_email">
  <button type="submit">Subscribe</button>
</form>
<form action="/auth/login" method="POST">
  <input type="email" name="email">
  <input type="password" name="password">
  <button type="submit">Sign in</button>
</form>
</body></html>"""
    r = score_login_page(html)
    assert r["best"]["action"].endswith("/auth/login")
    assert len(r["candidates"]) == 2


def test_login_empty_html_returns_no_candidates():
    from bulk_downloader.deep_detect import score_login_page
    r = score_login_page("")
    assert r["best"] is None
    assert r["candidates"] == []


# ════════════════════════════════════════════════════════════════════
# Item 7 — Honeypot finder
# ════════════════════════════════════════════════════════════════════


def test_honeypot_hidden_input_by_name():
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form><input type="text" name="website" style="display:none">
    <input type="email" name="email"><input type="password" name="password">
    </form>"""
    h = find_honeypots(html)
    assert any(hp["name"] == "website"
               for hp in h["honeypot_inputs"])


def test_honeypot_tabindex_neg_one():
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form>
    <input type="text" name="email" tabindex="-1" style="display:none">
    </form>"""
    h = find_honeypots(html)
    assert h["honeypot_inputs"]


def test_honeypot_duplicate_field_with_hidden_copy():
    """Two inputs named 'email' where one is hidden = the hidden one
    is a honeypot."""
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form>
    <input type="text" name="email" style="display:none">
    <input type="email" name="email">
    </form>"""
    h = find_honeypots(html)
    assert any(d["name"] == "email" for d in h["duplicate_fields"])


def test_honeypot_fake_submit_button():
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form>
    <button type="submit" style="pointer-events:none">Hidden</button>
    </form>"""
    h = find_honeypots(html)
    assert h["fake_submit_buttons"]


def test_honeypot_bot_defenses_detected():
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form>
    <input type="text" name="email">
    <div class="g-recaptcha"></div>
    </form>
    <script>grecaptcha.ready();</script>"""
    h = find_honeypots(html)
    assert any("recaptcha" in d.lower() for d in h["bot_defenses"])


def test_honeypot_time_traps_detected():
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form><input type="email" name="email"></form>
    <script>
    var form_loaded_at = Date.now();
    function check() {
      if (Date.now() - form_loaded_at < minimum_time) {
        submitted_too_fast();
      }
    }
    </script>"""
    h = find_honeypots(html)
    assert h["time_traps"]


def test_honeypot_no_honeypots_on_clean_form():
    from bulk_downloader.deep_detect import find_honeypots
    html = """<form>
    <input type="email" name="email">
    <input type="password" name="password">
    <button type="submit">Login</button>
    </form>"""
    h = find_honeypots(html)
    assert h["honeypot_inputs"] == []
    assert h["duplicate_fields"] == []
    assert h["fake_submit_buttons"] == []
