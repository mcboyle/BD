"""Packs D-I per spec. SYNTHETIC public markers only. Covers family detection,
alias normalization, DRM escalation, live non-override, hosted no-introspection,
CMS wrapper + adult-shell HINTS (never families, never selectors, F2-clean).
"""
import os, sys, json
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402


def fam(html, script_srcs=None, iframe_hosts=None, network=None):
    return pr.detect(html, script_srcs=script_srcs or [], iframe_hosts=iframe_hosts or [], network=network or [])


# ── Pack D: modern/open web players ─────────────────────────────────────────
def test_vidstack_not_media_chrome():
    r = fam('<media-player src="x"><media-provider></media-provider></media-player>')
    assert r["player_family"] == "vidstack"

def test_radiant_media_player():
    assert fam('<div class="rmp-container"></div>', script_srcs=["x/radiantmediaplayer.min.js"])["player_family"] == "radiant_media_player"

def test_cloudinary_not_native():
    r = fam('<div class="cld-video-player"><video></video></div>', script_srcs=["x/cloudinary-video-player.js"])
    assert r["player_family"] == "cloudinary_video_player"

def test_playerjs():
    assert fam('<div id="player"><div class="pjsdiv"></div></div>', script_srcs=["x/playerjs.js"])["player_family"] == "playerjs"

def test_jplayer():
    assert fam('<div class="jp-jplayer"></div>', script_srcs=["x/jquery.jplayer.min.js"])["player_family"] == "jplayer"

def test_able_player():
    assert fam('<video data-able-player class="able-player"></video>')["player_family"] == "able_player"

def test_paella_player():
    assert fam('<div id="paellaPlayer" class="paella"></div>')["player_family"] == "paella_player"

def test_fv_player_not_wordpress_mejs():
    assert fam('<div class="fvplayer fv-player"></div>', script_srcs=["x/fvplayer.js"])["player_family"] == "fv_player"

def test_presto_player_not_wordpress_mejs():
    assert fam('<div class="presto-player"></div>', script_srcs=["x/presto-player.js"])["player_family"] == "presto_player"

def test_brid_tv():
    assert fam('<div class="brid_tv"></div>', script_srcs=["x/brid.min.js"])["player_family"] == "brid_tv"


# ── Pack E: commercial SDKs + alias normalization + DRM escalation ──────────
def test_akamai_amp():
    assert fam('<video class="azuremediaplayer amp-default-skin"></video>')["player_family"] == "akamai_amp"

def test_nexplayer():
    assert fam('<div class="nexplayer"></div>', script_srcs=["x/nexplayer.js"])["player_family"] == "nexplayer_html5"

def test_vdocipher_is_review_only():
    r = fam('<div class="vdo-player" data-vdo="x"></div>', script_srcs=["x/vdocipher.js"])
    assert r["player_family"] == "vdocipher"
    assert r["policy"] in ("review_only", "drm_never")

def test_castlabs_drm_escalates():
    r = fam('<div class="clpp-container"></div>', script_srcs=["x/prestoplay.js"],
            network=[{"url": "https://wv.service.expressplay.com/widevine/"}])
    assert r["player_family"] == "castlabs_prestoplay"
    assert r["flags"]["drm"] is True and r["policy"] == "drm_never"

def test_theo_dolby_optiview_alias_normalizes_to_theoplayer():
    assert fam('<div class="theo-skin optiview"></div>', script_srcs=["x/optiview.js"])["player_family"] == "theoplayer"

def test_bitmovin_v8_alias_normalizes_to_bitmovin():
    assert fam('<div class="bitmovinplayer-container bmpui-ui-uicontainer"></div>')["player_family"] == "bitmovin"

def test_brightcove_videojs_alias_not_outrank_brightcove():
    # brightcove signal present -> brightcove, never a separate alias family
    assert fam('<video-js data-account="9" class="video-js"></video-js>', script_srcs=["players.brightcove.net/x.js"])["player_family"] == "brightcove"

def test_kaltura_playkit_alias_not_shaka_or_dashjs():
    r = fam('<div id="kaltura_player" class="playkit-player"></div>', script_srcs=["x/playkit.js"])
    assert r["player_family"] == "kaltura"


# ── Pack F: live/RTC platforms (review-only, non-override) ──────────────────
def test_livekit_live_review_only():
    r = fam('<div id="app"></div>', script_srcs=["x/livekit-client.umd.js"])
    assert r["player_family"] == "livekit"
    assert r["policy"] == "review_only"
    assert not r["selectors"].get("download")

def test_janus_webrtc():
    assert fam('<div id="videos"></div>', script_srcs=["x/janus.js"])["player_family"] == "janus_webrtc"

def test_jitsi_meet():
    assert fam('<div id="meet"></div>', script_srcs=["x/lib-jitsi-meet.min.js"])["player_family"] == "jitsi_meet"

def test_wowza_review_only():
    assert fam('<div class="wowza"></div>', script_srcs=["x/wowzaplayer.js"])["policy"] == "review_only"

def test_live_does_not_overwrite_inline_vod():
    # videojs VOD page that also loads an RTC SDK -> videojs stays primary
    r = fam('<div class="video-js"><button class="vjs-big-play-button"></button></div>',
            script_srcs=["x/livekit-client.umd.js"])
    assert r["player_family"] == "videojs"


# ── Pack G: hosted platforms (third_party_review_only, no introspection) ────
def _hosted_ok(r, fid):
    assert r["player_family"] == fid, r["player_family"]
    assert r["policy"] == "third_party_review_only"
    assert not r["selectors"].get("quality") and not r["selectors"].get("download")

def test_dacast():
    _hosted_ok(fam('<iframe src="https://iframe.dacast.com/x"></iframe>', iframe_hosts=["iframe.dacast.com", "dacast.com"]), "dacast")

def test_sproutvideo():
    _hosted_ok(fam('<iframe src="https://videos.sproutvideo.com/embed/x"></iframe>', iframe_hosts=["sproutvideo.com"]), "sproutvideo")

def test_loom():
    _hosted_ok(fam('<iframe src="https://www.loom.com/embed/abc"></iframe>', iframe_hosts=["www.loom.com"]), "loom")

def test_bunny_stream():
    _hosted_ok(fam('<iframe src="https://iframe.mediadelivery.net/embed/1/2"></iframe>', iframe_hosts=["iframe.mediadelivery.net"]), "bunny_stream")

def test_cloudflare_videodelivery_alias_normalizes():
    r = fam('<iframe src="https://x.videodelivery.net/abc"></iframe>', iframe_hosts=["videodelivery.net"])
    assert r["player_family"] == "cloudflare_stream"

def test_mux_stream_alias_hosted_when_no_element():
    r = fam('<iframe src="https://stream.mux.com/abc.m3u8"></iframe>', iframe_hosts=["stream.mux.com"])
    assert r["player_family"] == "mux_hosted" and r["policy"] == "third_party_review_only"

def test_mux_stream_alias_normalizes_to_mux_with_element():
    assert fam('<mux-player playback-id="x"></mux-player>', iframe_hosts=["stream.mux.com"])["player_family"] == "mux"

def test_jwplatform_hosted_not_outrank_inline_jwplayer():
    # inline jwplayer present alongside a hosted jwplatform iframe -> inline wins
    r = fam('<div class="jwplayer jw-flag-aspect-mode"><div class="jw-controls"></div></div>',
            iframe_hosts=["content.jwplatform.com"])
    assert r["player_family"] == "jwplayer"


# ── Pack H: CMS / LMS wrappers (workflow hints; underlying player primary) ──
def test_wordpress_wrapper_does_not_mask_videojs():
    r = fam('<div class="wp-block-video"><div class="video-js"><button class="vjs-big-play-button"></button></div></div>')
    assert r["player_family"] == "videojs"
    assert any(w["hint"] == "wordpress_block_video" for w in r["workflow_hints"])

def test_membership_wrapper_emits_workflow_note_only():
    r = fam('<div class="mepr-active-account"><video class="video-js"></video></div>')
    assert r["player_family"] == "videojs"
    hints = [w["hint"] for w in r["workflow_hints"]]
    assert "memberpress_protected_video" in hints
    assert any(w.get("kind") == "membership_workflow" for w in r["workflow_hints"])
    # no entitlement/account values persisted — labels only
    assert all(set(w.keys()) <= {"hint", "kind", "note"} for w in r["workflow_hints"])


# ── Pack I: adult/premium platform shells (HINTS ONLY) ──────────────────────
def test_shell_never_outranks_player():
    r = fam('<div class="video-js"><button class="vjs-big-play-button"></button></div>',
            iframe_hosts=["www.brazzers.com"])
    assert r["player_family"] == "videojs"          # player primary
    assert any(s["hint"] == "brazzers_realitykings_shell" for s in r["platform_hints"])

def test_kvs_tube_shell_no_fake_selectors():
    r = fam('<div id="kt_player" class="kt_player"></div>')
    assert any(s["hint"] == "kvs_tube_shell" for s in r["platform_hints"])
    # tube shell must not invent download selectors
    assert not r["selectors"].get("download")

def test_biller_is_workflow_only_not_media():
    r = fam('<a href="https://api.ccbill.com/wap-frontflex/flexforms/x">join</a>')
    shells = {s["hint"]: s for s in r["platform_hints"]}
    assert "ccbill_segbay_epoch_vendo_biller_shell" in shells
    assert shells["ccbill_segbay_epoch_vendo_biller_shell"]["category"] == "biller"

def test_nats_affiliate_structure_only_query_stripped():
    # account/campaign params in query must not survive into any persisted hint
    r = fam('<a href="https://site.com/nats/MC4wLjIuMTAw.5.0.0.0.0.0.0.0.html?nats=ACCOUNT123">x</a>')
    blob = json.dumps(r["platform_hints"])
    assert "nats_affiliate_shell" in blob
    assert "ACCOUNT123" not in blob and "?" not in blob and "nats=" not in blob

def test_shells_persist_no_pii():
    # synthetic capture carrying an email + token in markup -> never echoed in hints
    html = ('<div class="vixen-network" data-host="blacked.com">'
            '<input value="user@example.com"><meta name="csrf" content="TOKEN_SECRET_XYZ"></div>')
    r = fam(html, iframe_hosts=["blacked.com"])
    blob = json.dumps(r["platform_hints"]) + json.dumps(r["workflow_hints"])
    assert "vixen_network_shell" in blob
    assert "user@example.com" not in blob and "TOKEN_SECRET_XYZ" not in blob
