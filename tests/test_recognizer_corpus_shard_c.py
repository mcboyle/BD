"""Recognizer corpus loadfile shard C (row 333)."""

from test_recognizer_corpus import _check_one


BD_GATE_SCOPE = "repo-wide"


def test_ultra(): _check_one("ultra")
def test_beeg(): _check_one("beeg")
def test_bang(): _check_one("bang")
def test_reptyle(): _check_one("reptyle")
def test_redgif(): _check_one("redgif")
def test_nook(): _check_one("nook")
def test_teen(): _check_one("teen")
def test_dpla(): _check_one("dpla")
def test_iframe(): _check_one("iframe")
def test_vip4k(): _check_one("vip4k")
# P7 synthetic PLAYER_LIBRARIES coverage: clean, downloadable, non-DRM DASH
# on a synthetic .test host, guarding the dashjs detection tell.
def test_dashjs(): _check_one("dashjs")
# P7 hosted/embed provider families that had no in-tree fixture.
def test_mux(): _check_one("mux")
def test_bunny_stream(): _check_one("bunny_stream")
