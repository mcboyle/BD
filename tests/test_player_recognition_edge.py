"""Finish-set: video_react family + edge transports (webrtc_live, webtorrent) +
guardrail FLAGS (drm_eme_review_only, ad_wrapper) modelled as orthogonal flags
so the real content family is never masked. SYNTHETIC only.
"""
import os, sys
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402


def fam(html, script_srcs=None, iframe_hosts=None, network=None):
    return pr.detect(html, script_srcs=script_srcs or [], iframe_hosts=iframe_hosts or [], network=network or [])


def test_video_react():
    assert fam('<div class="video-react video-react-fluid"><button class="video-react-big-play-button"></button></div>')["player_family"] == "video_react"


def test_webrtc_live():
    r = fam('<video autoplay></video>', script_srcs=["https://cdn/webrtc-adapter.js", "https://x/peerjs.min.js"])
    assert r["player_family"] == "webrtc_live"
    assert r["policy"] == "review_only"
    assert any("webrtc" in n.lower() or "live" in n.lower() for n in r["notes"])


def test_webtorrent():
    r = fam('<video></video>', script_srcs=["https://cdn/webtorrent.min.js"])
    assert r["player_family"] == "webtorrent"
    assert r["policy"] == "review_only"


def test_drm_is_a_flag_not_a_family():
    # a videojs player that is DRM-protected stays videojs, but flags.drm + policy escalate
    net = [{"url": "https://lic.drmtoday.com/license/widevine", "response_headers": []}]
    r = fam('<div class="video-js"><button class="vjs-big-play-button"></button></div>', network=net)
    assert r["player_family"] == "videojs"           # content family preserved
    assert r["flags"]["drm"] is True
    assert r["policy"] == "drm_never"
    assert "drm_eme_review_only" in r["concerns"]
    assert any("drm" in n.lower() and "never" in n.lower() for n in r["notes"])
    # never emit anything that helps bypass
    assert "license" not in str(r["selectors"]).lower()


def test_ad_wrapper_is_a_flag_content_family_kept():
    r = fam('<div class="video-js"><button class="vjs-big-play-button"></button></div>',
            script_srcs=["https://imasdk.googleapis.com/js/sdkloader/ima3.js"],
            network=[{"url": "https://securepubads.g.doubleclick.net/gampad/ads"}])
    assert r["player_family"] == "videojs"           # content kept, not "ad_wrapper"
    assert r["flags"]["ad_overlay"] is True
    assert "ad_wrapper" in r["concerns"]
    assert any("ad" in n.lower() for n in r["notes"])


def test_clean_page_has_no_flags():
    r = fam('<div class="video-js"><button class="vjs-big-play-button"></button></div>')
    assert r["flags"]["drm"] is False and r["flags"]["ad_overlay"] is False
    assert r["concerns"] == []
    assert r["policy"] == "normal"


# ── v3.66.170 edge arbitration (presto/fv vs wp-core mejs; brid hosted) ───────
def test_wp_plugin_supersedes_core_mejs_on_cooccurrence():
    # Presto plugin class only (0.6) co-occurring with WP-core mejs (0.7).
    # Pre-170 the higher mejs score won (wrong family AND wrong delivery); now
    # the plugin wins deterministically and delivery follows it (hls).
    r = fam('<div class="wp-video presto-player"><div class="mejs__container"></div></div>')
    assert r["player_family"] == "presto_player", r["player_family"]
    assert "hls" in r["delivery"], r["delivery"]
    assert any("MediaElement" in n and "fallback" in n for n in r["notes"]), r["notes"]
    assert any(c["family"] == "wordpress_mejs" for c in r["candidates"]), r["candidates"]


def test_fv_plugin_supersedes_core_mejs_on_cooccurrence():
    r = fam('<div class="wp-video fv-player"><div class="mejs__container"></div></div>')
    assert r["player_family"] == "fv_player", r["player_family"]


def test_wordpress_mejs_alone_unaffected():
    # No plugin present -> core mejs still wins (regression guard for the families test).
    r = fam('<div class="wp-video"><div class="mejs__container mejs__controls"></div></div>')
    assert r["player_family"] == "wordpress_mejs", r["player_family"]


def test_brid_hosted_iframe_is_third_party():
    r = fam('<iframe src="https://player.brid.tv/player/abc/123"></iframe>',
            iframe_hosts=["player.brid.tv"])
    assert r["player_family"] == "brid_tv_hosted", r["player_family"]
    assert r["policy"] == "third_party_review_only", r["policy"]
    assert "quality" not in r["selectors"] and "settings" not in r["selectors"]
    assert any("third-party" in n.lower() or "not in the capture" in n.lower()
               for n in r["notes"]), r["notes"]


def test_brid_inline_stays_normal():
    r = fam('<div class="brid_tv"></div>', script_srcs=["x/brid.min.js"])
    assert r["player_family"] == "brid_tv", r["player_family"]
    assert r["policy"] == "normal", r["policy"]
