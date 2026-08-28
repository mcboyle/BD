"""Recognizer corpus loadfile shard B (row 333)."""

from test_recognizer_corpus import _check_one


BD_GATE_SCOPE = "repo-wide"


def test_erome(): _check_one("erome")
def test_adult(): _check_one("adult")
def test_news(): _check_one("news")
def test_banb(): _check_one("banb")
# DASH-primary fixture (vidstack/DASH) -- the only primary_protocol=dash +
# site_type=dash_manifest coverage in the corpus.
def test_vdash(): _check_one("vdash")
# CORPUS-EXP (v3.66.520): these cases add net-new class-matrix combinations;
# mxchrome and hlsjs add the media_chrome and hlsjs player families.
def test_wowza(): _check_one("wowza")
def test_mxchrome(): _check_one("mxchrome")
def test_hlsjs(): _check_one("hlsjs")
def test_nubiles(): _check_one("nubiles")
# P7 synthetic player-library and hosted-provider coverage.
def test_react_player(): _check_one("react_player")
def test_kaltura(): _check_one("kaltura")
