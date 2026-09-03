"""Recognizer corpus loadfile shard A (row 333)."""

from test_recognizer_corpus import _check_one


BD_GATE_SCOPE = "repo-wide"


def test_scroller(): _check_one("scroller")
def test_tiny(): _check_one("tiny")
def test_theo(): _check_one("theo")
def test_kelly(): _check_one("kelly")
def test_xnxx(): _check_one("xnxx")
# iframe_embed fixture. NOTE: this pins the iframe_embed *site_type label* via
# the clean-path fallback (primary None + no player selector -> family stays
# "unknown"); it is NOT a guard on the iframe_hit -> family=iframe_embed
# detection branch. A real cross-origin third-party-embed capture
# (family=iframe_embed, not_downloadable) would guard detection; none was in
# the supplied set. Remaining corpus gap: a family=iframe_embed fixture.
def test_embed(): _check_one("embed")
def test_media(): _check_one("media")
# P7 (v3.66.681): synthetic clappr coverage and hosted/embed providers that
# previously had no in-tree fixture. Each remains a distinct pinned family.
def test_clappr(): _check_one("clappr")
def test_brightcove(): _check_one("brightcove")
def test_vimeo(): _check_one("vimeo")
# Row 453 (row 120's successor): the corpus' only capture in the true
# signed x jwplayer x akamai intersection. Its pin is the guard that the
# akamai_token signing recognizer keeps classifying a real signed JWPlayer
# site as signed_akamai_token / pick_test_promote rather than auto_template.
def test_yupptv(): _check_one("yupptv")
