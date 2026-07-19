"""D — auto-template robustness for signed JWPlayer behind akamai/cloudflare.

The prior recognizers (R1–R4) miss the signed-direct-media shape: a progressive
``.mp4?token=…`` (no resolution-in-name, no attachment, not ranged, not .ts) or a
JWPlayer playlist/config response. So such a capture yielded a near-empty draft.

R5 recognizes:
  * a media URL whose query carries signing params (token / expiry / signature),
  * media served from a JWPlayer host / playlist path,
emitting a SIGNING-FREE templated pattern (query stripped — no signed value is
ever persisted; F2) plus signed-media / jwplayer signals so the draft is
classified as signed-direct-media (runtime player path), not a broken DL template.

A JWPlayer player-marker helper also yields container + play-button selectors so
the runtime Pick → Test → Promote path has something to drive.
"""
from __future__ import annotations

import json

import tools.build_template_from_wacz as b


SIGNED_MP4 = ("https://cdn.example-media.net/videos/jada5078/content.mp4"
              "?token=eyJABCDEF.signedblob&expires=1750000000")
JW_PLAYLIST = "https://cdn.jwplayer.com/v2/media/AbCd1234.json"
PLAIN_MP4 = "https://static.example.com/promo/teaser.mp4"


# --- signing-query detection -----------------------------------------------

def test_has_signing_query_true():
    assert b._has_signing_query("token=abc&expires=123") is True
    assert b._has_signing_query("Signature=xyz&Policy=p&Key-Pair-Id=k") is True
    assert b._has_signing_query("hdnts=exp=1~hmac=deadbeef") is True
    assert b._has_signing_query("__token__=st=1~hmac=ff") is True


def test_has_signing_query_false():
    assert b._has_signing_query("") is False
    assert b._has_signing_query("page=2&sort=name") is False


# --- JWPlayer target detection ---------------------------------------------

def test_is_jwplayer_target_true():
    assert b._is_jwplayer_target("cdn.jwplayer.com", "/v2/media/AbCd1234.json") is True
    assert b._is_jwplayer_target("content.jwplatform.com", "/v2/playlists/x") is True
    assert b._is_jwplayer_target("media.jwpcdn.com", "/x.mp4") is True


def test_is_jwplayer_target_false():
    assert b._is_jwplayer_target("static.example.com", "/promo/teaser.mp4") is False


# --- R5 in the recognizer ---------------------------------------------------

def _net(url, ct="video/mp4", status="200"):
    return [{"url": url, "response_status": status,
             "response_headers": [["content-type", ct]]}]


def test_recognizes_signed_progressive_mp4():
    out = b._supplemental_media_patterns(_net(SIGNED_MP4))
    assert "signed-media" in out["download_signals"]
    assert ".../{signed}.mp4" in out["media_patterns"]


def test_signed_pattern_persists_no_token_value():
    out = b._supplemental_media_patterns(_net(SIGNED_MP4))
    blob = json.dumps(out)
    assert "signedblob" not in blob          # the signature value
    assert "1750000000" not in blob          # the expiry value
    assert "token=" not in blob


def test_recognizes_jwplayer_playlist():
    out = b._supplemental_media_patterns(
        _net(JW_PLAYLIST, ct="application/json"))
    assert "jwplayer" in out["download_signals"]


def test_no_false_positive_on_plain_unsigned_mp4():
    # plain non-JW mp4, no signing, no resolution-in-name -> R5 must not fire
    out = b._supplemental_media_patterns(_net(PLAIN_MP4))
    assert "signed-media" not in out["download_signals"]
    assert "jwplayer" not in out["download_signals"]


# --- JWPlayer player markers ------------------------------------------------

def test_jwplayer_player_markers_detected():
    html = ('<div id="jwplayer_a1" class="jwplayer jw-flag-aspect-mode">'
            '<div class="jw-icon jw-icon-display" role="button"></div></div>')
    pl = b._jwplayer_player_markers(html)
    assert pl.get("container")
    assert pl.get("play_button")


def test_jwplayer_player_markers_absent():
    assert b._jwplayer_player_markers("<div class='video-js'></div>") == {}
