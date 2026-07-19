"""D++ §9 — recognizer regression corpus.

The durability backbone of the D++ program (cuts 1-4). Runs the live
build_template over a curated set of DISTILLED redacted real captures (one+ per
framework × protocol × protection class) and asserts the PINNED verdict, so a
later change to any A-E recognizer that silently alters a real-site
classification fails here.

Fixtures (tests/corpus/recognizer/*.cap.wacz) are distilled name/shape captures
(full DOM snapshot + recognizer-relevant mutations + network url/headers +
manifest bodies; heavy media/segment/mutation bodies dropped). Pins
(expected_verdicts.json) carry NAMES/COUNTS/TAGS only -- never a value (F2).

Regenerate after an intentional recognizer change:
    python3 tools/build_recognizer_corpus.py --regen-pins --out tests/corpus/recognizer
"""
import os
import sys
import json
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import build_template_from_wacz as btw  # noqa: E402
import build_recognizer_corpus as brc  # noqa: E402
sys.path.insert(0, _REPO)
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402

_CORPUS = Path(_REPO) / "tests" / "corpus" / "recognizer"
_PINS = json.loads((_CORPUS / "expected_verdicts.json").read_text(encoding="utf-8"))


def _pin(draft):
    rec = draft.get("recognition") or {}
    v = draft.get("verdict") or {}
    p = rec.get("protection") or {}
    tr = rec.get("tracks") or {}
    return {
        "site_type": v.get("site_type"),
        "recommended_path": v.get("recommended_path"),
        "downloadable": v.get("downloadable"),
        "requires_runtime_capture": v.get("requires_runtime_capture"),
        "primary_protocol": rec.get("primary_protocol"),
        "rendition_count": len(rec.get("renditions") or []),
        "player_family": rec.get("player_family"),
        "signing_schemes": sorted((p.get("signing") or {}).get("schemes") or []),
        "anti_bot": sorted(p.get("anti_bot") or []),
        "drm": bool(p.get("drm")),
        "caption_count": len(tr.get("captions") or []),
        "ssai": bool(tr.get("ssai")),
    }


def test_corpus_present():
    fixtures = sorted(_CORPUS.glob("*.cap.json"))
    assert fixtures, "no corpus fixtures found"
    assert len(_PINS) == len(fixtures), "pin/fixture count mismatch"
    assert len(fixtures) >= 24, "corpus too thin to cover the class matrix"


def test_corpus_covers_the_class_matrix():
    sts = {v["site_type"] for v in _PINS.values()}
    paths = {v["recommended_path"] for v in _PINS.values()}
    fams = {v["player_family"] for v in _PINS.values()}
    schemes = set()
    for v in _PINS.values():
        schemes.update(v["signing_schemes"])
    protos = {v["primary_protocol"] for v in _PINS.values()}
    # every verdict class + signing scheme + the DRM path must be represented.
    # dash_manifest + iframe_embed added once a real fixture for each landed
    # (vdash = vidstack/DASH; embed = clean-path iframe_embed -- see notes below).
    assert {"signed_generic_token", "signed_cloudfront", "signed_aws_sigv4",
            "drm_protected", "hls_manifest", "direct_progressive",
            "dash_manifest", "iframe_embed"} <= sts
    # the DASH manifest path must have a real pinned protocol, not just a label
    assert "dash" in protos, "no fixture exercises primary_protocol=dash"
    assert {"auto_template", "pick_test_promote", "not_downloadable"} <= paths
    assert {"cloudfront", "aws_sigv4", "generic_token"} <= schemes
    # breadth of player frameworks incl. the expanded set (CORPUS-EXP added the
    # media_chrome web-component player and a real hls.js site).
    assert {"jwplayer", "videojs", "bitmovin", "wistia", "native_custom",
            "theoplayer", "shaka", "mediaelement", "media_chrome", "hlsjs"} <= fams
    # DRM diversity: at least two distinct DRM stacks (bitmovin + shaka)
    drm_fams = {v["player_family"] for v in _PINS.values() if v["drm"]}
    assert len(drm_fams) >= 2, f"need >=2 DRM stacks, got {drm_fams}"
    assert any(v["anti_bot"] for v in _PINS.values())
    # caption-rich site exercises recognize_aux
    assert max(v["caption_count"] for v in _PINS.values()) >= 5


def _check_one(name):
    fx = _CORPUS / f"{name}.cap.json"
    draft = brc.build_from_fixture(fx)
    got = _pin(draft)
    assert got == _PINS[name], f"{name}: verdict drift\n got={got}\n pin={_PINS[name]}"
    # F2: no secret survives into the built draft
    assert scan_artifact_secrets(draft) == [], f"{name}: secret leak in draft"


# one zero-arg test per fixture (the runner injects no params)
def test_reptyle():  _check_one("reptyle")
def test_ultra():    _check_one("ultra")
def test_banb():     _check_one("banb")
def test_news():     _check_one("news")
def test_vip4k():    _check_one("vip4k")
def test_dfx():      _check_one("dfx")
def test_vixen():    _check_one("vixen")
def test_tiny():     _check_one("tiny")
def test_bit():      _check_one("bit")
def test_iframe():   _check_one("iframe")
def test_xnxx():     _check_one("xnxx")
def test_beeg():     _check_one("beeg")
def test_nubiles():  _check_one("nubiles")
def test_scroller(): _check_one("scroller")
def test_peg():      _check_one("peg")
def test_teen():     _check_one("teen")
def test_brazzers(): _check_one("brazzers")
def test_nook():     _check_one("nook")
def test_theo():     _check_one("theo")
def test_shaka():    _check_one("shaka")
# P7 (v3.66.681): a 2nd shaka fixture -- a CLEAR (non-DRM) DASH stream, the
# structural counterpart to the DRM+SSAI shaka demo. NET-NEW combo
# (dash_manifest x auto_template x dash x shaka, downloadable/clear), so the
# recognizer's shaka tell is guarded in a non-DRM context too.
def test_shaka_clear(): _check_one("shaka_clear")
def test_media():    _check_one("media")
def test_dpla():     _check_one("dpla")
def test_art():      _check_one("art")
def test_adult():    _check_one("adult")
def test_redgif():   _check_one("redgif")
def test_wow():      _check_one("wow")
# DASH-primary fixture (vidstack/DASH) -- the only primary_protocol=dash +
# site_type=dash_manifest coverage in the corpus.
def test_vdash():    _check_one("vdash")
# iframe_embed fixture. NOTE: this pins the iframe_embed *site_type label* via
# the clean-path fallback (primary None + no player selector -> family stays
# "unknown"); it is NOT a guard on the iframe_hit -> family=iframe_embed
# detection branch. A real cross-origin third-party-embed capture
# (family=iframe_embed, not_downloadable) would guard detection; none was in
# the supplied set. Remaining corpus gap: a family=iframe_embed fixture.
def test_embed():    _check_one("embed")
# CORPUS-EXP (v3.66.520) -- widen the class matrix from the consolidated corpus.
# Each adds a NET-NEW (site_type x recommended_path x primary_protocol x
# player_family) combo not already pinned. Two new player FAMILIES land here:
#   mxchrome -> media_chrome (Mux <media-chrome> web component, hls_manifest)
#   hlsjs    -> hls.js (operator-confirmed real hls.js site, hls_manifest)
# plus a real videojs progressive site (bang), a signed_generic_token+progressive
# videojs (kelly), a 2nd flowplayer host (wowza), and an unsigned videojs HLS
# manifest (erome).
def test_mxchrome(): _check_one("mxchrome")
def test_hlsjs():    _check_one("hlsjs")
def test_bang():     _check_one("bang")
def test_kelly():    _check_one("kelly")
def test_wowza():    _check_one("wowza")
def test_erome():    _check_one("erome")
# P7 (v3.66.681, "go p7 A on all available families"): synthetic fixtures for the
# three recognizer-detectable PLAYER_LIBRARIES families that had NO in-tree
# coverage -- clappr (HLS), react_player (HLS), dashjs (DASH). Each is a clean,
# downloadable, non-DRM stream on a synthetic .test host; the recognizer emits the
# distinct family, guarding its detection tell against silent rot.
def test_clappr():       _check_one("clappr")
def test_react_player(): _check_one("react_player")
def test_dashjs():       _check_one("dashjs")
# P7 (v3.66.681) cont. -- the hosted/embed PROVIDER families the recognizer emits
# as a distinct player_family (bulk_downloader.deep_detect._common.PROVIDERS) that
# had no in-tree fixture. wistia + jwplayer were already covered; vidyard/panopto/
# youtube collapse to unknown (provider ID-hint only, not a distinct family verdict).
def test_brightcove():        _check_one("brightcove")
def test_kaltura():           _check_one("kaltura")
def test_vimeo():             _check_one("vimeo")
def test_mux():               _check_one("mux")
def test_dailymotion():       _check_one("dailymotion")
def test_bunny_stream():      _check_one("bunny_stream")
def test_cloudflare_stream(): _check_one("cloudflare_stream")
def test_sproutvideo():       _check_one("sproutvideo")
