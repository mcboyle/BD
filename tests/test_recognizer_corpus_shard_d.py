"""Recognizer corpus loadfile shard D (row 333)."""

from test_recognizer_corpus import _check_one


BD_GATE_SCOPE = "repo-wide"


def test_dfx(): _check_one("dfx")
def test_wow(): _check_one("wow")
def test_bit(): _check_one("bit")
def test_peg(): _check_one("peg")
def test_brazzers(): _check_one("brazzers")
def test_shaka(): _check_one("shaka")
def test_vixen(): _check_one("vixen")
def test_art(): _check_one("art")
# P7 (v3.66.681): a second shaka fixture -- a clear DASH stream and the
# structural counterpart to the DRM+SSAI shaka demo. This guards the shaka
# tell in a non-DRM context too.
def test_shaka_clear(): _check_one("shaka_clear")
# P7 hosted/embed provider families that had no in-tree fixture.
def test_dailymotion(): _check_one("dailymotion")
def test_cloudflare_stream(): _check_one("cloudflare_stream")
def test_sproutvideo(): _check_one("sproutvideo")
