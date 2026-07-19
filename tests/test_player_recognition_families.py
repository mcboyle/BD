"""Wave 169 (built on 168 scaffold) — family pack A + delivery refinement.

Brand recognizers plug into the registry; delivery is computed orthogonally to
family (from network content-types / media), so a branded player that serves
HLS is labelled by brand AND hls — not collapsed to mse_blob. SYNTHETIC only.
"""
import os, sys
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402


def fam(html, script_srcs=None, network=None):
    return pr.detect(html, script_srcs=script_srcs or [], network=network or [])


def test_videojs():
    r = fam('<div class="video-js vjs-default-skin"><button class="vjs-big-play-button"></button></div>')
    assert r["player_family"] == "videojs"
    assert r["selectors"]["player"]["container"] == ".video-js"


def test_theoplayer():
    r = fam('<div class="theoplayer-skin"><div aria-label="Open the video quality settings menu"></div>'
            '<div aria-label="Set video quality to 1080p"></div></div>')
    assert r["player_family"] == "theoplayer"
    assert "{resolution}" in r["selectors"]["quality"]["resolution_option"]


def test_jwplayer():
    r = fam('<div id="jwplayer-0" class="jwplayer jw-reset"><div class="jw-icon-settings"></div></div>')
    assert r["player_family"] == "jwplayer"


def test_shaka():
    r = fam('<div class="shaka-video-container"><button class="shaka-overflow-menu-button"></button></div>')
    assert r["player_family"] == "shaka"


def test_hlsjs_via_script():
    r = fam('<video></video>', script_srcs=["https://cdn.example.com/hls.min.js"])
    assert r["player_family"] == "hlsjs"
    assert r["delivery"] in ("hls", "hls+progressive", "progressive+hls")


def test_dashjs_via_script():
    r = fam('<video></video>', script_srcs=["https://cdn.example.com/dash.all.min.js"])
    assert r["player_family"] == "dashjs"


def test_plyr():
    r = fam('<div class="plyr"><div class="plyr__controls"><button class="plyr__menu"></button></div></div>')
    assert r["player_family"] == "plyr"


def test_flowplayer():
    r = fam('<div class="flowplayer"><div class="fp-controls"></div></div>')
    assert r["player_family"] == "flowplayer"


def test_clappr():
    r = fam('<div data-player class="clappr-style"><div class="media-control"></div></div>')
    assert r["player_family"] == "clappr"


def test_mediaelement():
    r = fam('<div class="mejs__container"><div class="mejs__controls"></div></div>')
    assert r["player_family"] == "mediaelement"


def test_wordpress_mejs_beats_plain_mediaelement():
    r = fam('<div class="wp-video"><div class="mejs__container mejs__controls"></div></div>')
    assert r["player_family"] == "wordpress_mejs"


def test_delivery_orthogonal_to_family():
    # branded (videojs) serving HLS+DASH+progressive -> brand family, rich delivery, NOT mse_blob
    net = [
        {"url": "x", "response_headers": [{"name": "content-type", "value": "application/vnd.apple.mpegurl"}]},
        {"url": "x", "response_headers": [{"name": "content-type", "value": "video/mp4"}]},
    ]
    r = fam('<div class="video-js"><video src="blob:https://x/abc"></video></div>', network=net)
    assert r["player_family"] == "videojs"          # brand, not mse_blob_custom
    assert "hls" in r["delivery"] and "progressive" in r["delivery"]
    assert "mse_blob" not in r["delivery"]


def test_bare_blob_still_mse_blob_when_no_brand_no_classes():
    r = fam('<video src="blob:https://x/abc"></video>')
    assert r["player_family"] == "mse_blob_custom"
    assert r["delivery"] == "mse_blob"


def test_candidates_reported_for_mixed_signals():
    # vjs class + theoplayer aria -> both score, highest wins, both listed
    r = fam('<div class="video-js"><div aria-label="Set video quality to 720p"></div>'
            '<div class="theoplayer-skin"></div></div>')
    ids = [c["family"] for c in r["candidates"]]
    assert "videojs" in ids and "theoplayer" in ids
