"""v3.66.260 — AI-2/AI-7 recognizer precision pass (SYNTHETIC, value-free).

Pins the four review-only changes to player_recognition.detect():
  1. media_chrome + vidstack storage-tell promoters in _STORAGE_CONFIRMS.
  2. weak-brand evidence floor (exclusive element/script/host/storage tell).
  3. capture_quality + confidence flags (zero-media => thin_no_media/low_review;
     storage tell => high).
  4. _streaming_present is network-only + extension-anchored (a .webmanifest or a
     stray .m3u8 substring must not count).
All inputs are crafted markup; no real capture content.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402

# A static video.js shell that scores videojs on markup alone (same shape the
# v3.66.171 test uses: data-vjs-player + video-js + vjs-big-play-button + script).
_VJS_HTML = (
    '<div data-vjs-player><video id="v" class="video-js vjs-default-skin" controls>'
    '<source src="/media/clip.mp4" type="video/mp4"></video>'
    '<button class="vjs-big-play-button"></button>'
    '<div class="vjs-control-bar"><div class="vjs-play-control"></div></div></div>'
)
_VJS_SRCS = ["/assets/video.min.js"]


def test_storage_promoters_media_chrome_and_vidstack():
    assert "media_chrome" in pr._storage_confirmed(["media-chrome-pref-muted"])
    assert "vidstack" in pr._storage_confirmed(["vidstack::settings"])
    # existing tells still confirm
    assert "theoplayer" in pr._storage_confirmed(["theoplayer-session-id"])
    # an unrelated key confirms nothing
    assert pr._storage_confirmed(["site_prefs"]) == set()


def test_weak_brand_tell_helper():
    # vidstack: no tell -> False; custom element / lib script -> True
    assert pr._weak_brand_has_tell("vidstack", "<div class='vds-foo'></div>", [], [], [], set()) is False
    assert pr._weak_brand_has_tell("vidstack", "<media-player></media-player>", [], [], [], set()) is True
    assert pr._weak_brand_has_tell("vidstack", "", ["https://x/vidstack.js"], [], [], set()) is True
    # media_chrome: custom element -> True
    assert pr._weak_brand_has_tell("media_chrome", "<media-controller></media-controller>", [], [], [], set()) is True
    # wowza_player: host tell -> True; bare text-only -> False
    assert pr._weak_brand_has_tell("wowza_player", "wowza wowza", [], ["cdn.wowza.com"], [], set()) is True
    assert pr._weak_brand_has_tell("wowza_player", "wowza wowza", [], [], [], set()) is False
    # a non-weak family is never floored
    assert pr._weak_brand_has_tell("jwplayer", "", [], [], [], set()) is True


def test_streaming_present_network_only_extension_anchored():
    assert pr._streaming_present("", [{"url": "/app.webmanifest"}]) is False
    assert pr._streaming_present("", [{"url": "/seg00001.ts"}]) is True
    assert pr._streaming_present("", [{"url": "https://cdn/x/play.m3u8?token=abc"}]) is True
    assert pr._streaming_present("", [{"url": "https://cdn/x/manifest.mpd"}]) is True
    # a stray .m3u8 substring in page text must NOT count (network is empty)
    assert pr._streaming_present("see playlist.m3u8 in the docs", []) is False


def test_thin_no_media_flags_low_confidence():
    # videojs static shell + NO network activity => player never initialized
    r = pr.detect(_VJS_HTML, script_srcs=_VJS_SRCS, network=[], storage_keys=[])
    assert r["player_family"] == "videojs", r["player_family"]
    assert r["capture_quality"] == "thin_no_media", r["capture_quality"]
    assert r["confidence"] == "low_review", r["confidence"]


def test_storage_tell_yields_high_confidence_and_suppresses_thin():
    # same shell, but a video.js storage tell present => confirmed engine, high conf,
    # and the thin flag is suppressed (a confirmed engine is not a static-shell guess)
    r = pr.detect(_VJS_HTML, script_srcs=_VJS_SRCS, network=[], storage_keys=["vjs-volume"])
    assert r["player_family"] == "videojs", r["player_family"]
    assert r["confidence"] == "high", r["confidence"]
    assert r["capture_quality"] == "ok", r["capture_quality"]
    assert "videojs" in r["storage_confirmed"]


def test_weak_brand_kept_when_exclusive_element_present():
    # vidstack with its custom element is a real match and must be kept (not floored)
    html = ('<media-player title="x"><media-provider></media-provider>'
            '<div class="vds-controls"><div class="vds-button"></div></div></media-player>')
    r = pr.detect(html, script_srcs=["/vidstack.js"], network=[{"url": "/v/clip.mp4", "type": "media"}],
                  storage_keys=[])
    assert r["player_family"] == "vidstack", (r["player_family"], r["candidates"][:3])
