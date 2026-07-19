"""Wave 169 pack B (inline players) + pack C (third-party embeds + policy).
Brand recognizers; SYNTHETIC only. Pack C is recognized but NEVER introspected
(no internal selectors) and carries policy=third_party_review_only.
"""
import os, sys
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402


def fam(html, script_srcs=None, iframe_hosts=None, network=None):
    return pr.detect(html, script_srcs=script_srcs or [], iframe_hosts=iframe_hosts or [], network=network or [])


# ── pack B ──────────────────────────────────────────────────────────────────
def test_bitmovin():
    assert fam('<div class="bitmovinplayer-container"><button class="bmpui-ui-settingstogglebutton"></button></div>')["player_family"] == "bitmovin"

def test_brightcove_beats_videojs():
    r = fam('<video-js class="video-js vjs-default" data-account="123" data-video-id="9"></video-js>',
            script_srcs=["https://players.brightcove.net/123/default_default/index.min.js"])
    assert r["player_family"] == "brightcove"

def test_kaltura():
    assert fam('<div id="kaltura_player" class="playkit-player"></div>', script_srcs=["x/mwEmbedLoader.php"])["player_family"] == "kaltura"

def test_mux_beats_media_chrome():
    assert fam('<mux-player playback-id="abc"></mux-player>')["player_family"] == "mux"

def test_media_chrome():
    assert fam('<media-controller><media-play-button></media-play-button></media-controller>')["player_family"] == "media_chrome"

def test_dplayer():
    assert fam('<div class="dplayer"><div class="dplayer-setting"></div></div>')["player_family"] == "dplayer"

def test_artplayer():
    assert fam('<div class="artplayer-app art-video-player"></div>')["player_family"] == "artplayer"

def test_xgplayer():
    r = fam('<div class="xgplayer xgplayer-skin"><div class="xgplayer-definition"></div></div>')
    assert r["player_family"] == "xgplayer"

def test_ovenplayer_not_openplayerjs():
    assert fam('<div id="ovenplayer" class="ovenplayer"></div>', script_srcs=["x/ovenplayer.js"])["player_family"] == "ovenplayer"

def test_openplayerjs_not_ovenplayer():
    assert fam('<div class="op-player op-controls__playpause"></div>', script_srcs=["x/openplayer.min.js"])["player_family"] == "openplayerjs"

def test_fluid_player():
    assert fam('<div id="fluid_video_wrapper_v" class="fluid_video_wrapper"></div>', script_srcs=["x/fluidplayer.min.js"])["player_family"] == "fluid_player"

def test_flvjs_mpegts():
    assert fam('<video></video>', script_srcs=["https://cdn/flv.min.js"])["player_family"] == "flvjs_mpegts"


# ── pack C (embeds + policy) ─────────────────────────────────────────────────
def _embed_ok(r, fid):
    assert r["player_family"] == fid, r["player_family"]
    assert r["policy"] == "third_party_review_only"
    assert not r["selectors"].get("quality") and not r["selectors"].get("settings")

def test_vimeo():
    _embed_ok(fam('<iframe src="https://player.vimeo.com/video/12345"></iframe>', iframe_hosts=["player.vimeo.com"]), "vimeo")

def test_youtube():
    _embed_ok(fam('<iframe src="https://www.youtube.com/embed/abcDEF"></iframe>', iframe_hosts=["www.youtube.com"]), "youtube")

def test_twitch():
    _embed_ok(fam('<iframe src="https://player.twitch.tv/?channel=x"></iframe>', iframe_hosts=["player.twitch.tv"]), "twitch")

def test_dailymotion():
    _embed_ok(fam('<iframe src="https://www.dailymotion.com/embed/video/xyz"></iframe>', iframe_hosts=["www.dailymotion.com"]), "dailymotion")

def test_facebook_video():
    _embed_ok(fam('<iframe src="https://www.facebook.com/plugins/video.php?href=x"></iframe>', iframe_hosts=["www.facebook.com"]), "facebook_video")

def test_cloudflare_stream():
    _embed_ok(fam('<stream src="abc123"></stream><iframe src="https://iframe.cloudflarestream.com/abc"></iframe>', iframe_hosts=["iframe.cloudflarestream.com"]), "cloudflare_stream")

def test_wistia():
    _embed_ok(fam('<div class="wistia_embed wistia_async_abc123def"></div>', script_srcs=["https://fast.wistia.com/embed/medias/abc.jsonp"]), "wistia")

def test_react_player_wrapper():
    assert fam('<div class="react-player"></div>')["player_family"] == "react_player"


# ── no-regression: plain video.js (no brightcove) stays videojs ──────────────
def test_plain_videojs_unaffected():
    assert fam('<div class="video-js vjs-default-skin"><button class="vjs-big-play-button"></button></div>')["player_family"] == "videojs"
