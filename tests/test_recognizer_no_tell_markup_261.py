"""v3.66.261 — no-tell weak-markup -> native_custom (SYNTHETIC, value-free).

Generalizes the deferred one-family videojs density-demoter (which whack-a-moled:
demoting the top markup-only brand just promoted the next stacked one). When
EVERY eligible candidate is a markup-class brand resting only on CSS-class markup
-- none carrying a lib-script / exclusive element/id/data-attr / storage tell --
AND the page is fundamentally a native <video> with no adaptive (HLS/DASH) or MSE
delivery, all such matches demote so the generic native_custom fallback wins.

newsensations is the real over-call this fixes: dense vjs- skin markup + a stacked
jwplayer class match, NO video.js/jwplayer script, NO storage tell, native <video>,
progressive delivery. All inputs here are crafted markup; no real capture content.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402

# A native <video> page carrying video.js SKIN markup (vjs- + control-bar) AND a
# stacked jwplayer class match -- but NO lib script, NO storage tell, NO exclusive
# element/id/data-attr, progressive sources only. This is the newsensations shape.
_STACKED_HTML = (
    '<div class="video-js vjs-default-skin">'
    '<video src="/media/clip.mp4" type="video/mp4"></video>'
    '<button class="vjs-big-play-button"></button>'
    '<div class="vjs-control-bar"><div class="vjs-play-control"></div></div>'
    '<div class="jwplayer jw-reset"><div class="jw-icon-settings"></div></div>'
    '</div>'
)


def _fam(html, script_srcs=None, network=None, storage_keys=None):
    return pr.detect(html, script_srcs=script_srcs or [], network=network or [],
                     storage_keys=storage_keys or [])


# ── the whack-a-mole fix (RED on baseline: returns videojs) ──────────────────
def test_stacked_no_tell_markup_demotes_all_to_native_custom():
    r = _fam(_STACKED_HTML)
    assert r["player_family"] == "native_custom", (r["player_family"], r["candidates"][:4])
    # both stacked brands stay VISIBLE in candidates (nothing erased)
    fams = {c["family"] for c in r["candidates"]}
    assert "videojs" in fams and "jwplayer" in fams, fams
    # a note explains the demotion
    assert any("native" in n.lower() and ("markup" in n.lower() or "skin" in n.lower())
               for n in r["notes"]), r["notes"]


def test_single_no_tell_videojs_markup_demotes_to_native():
    html = ('<div class="video-js vjs-default-skin">'
            '<video src="/v/clip.mp4"></video>'
            '<button class="vjs-big-play-button"></button>'
            '<div class="vjs-control-bar"></div></div>')
    r = _fam(html)
    assert r["player_family"] == "native_custom", (r["player_family"], r["candidates"][:3])


def test_brid_tv_no_tell_markup_demotes_to_native():
    html = '<div class="brid_tv"><video src="/v/clip.mp4"></video></div>'
    r = _fam(html)
    assert r["player_family"] == "native_custom", (r["player_family"], r["candidates"][:3])


def test_plyr_no_tell_markup_demotes_to_native():
    html = ('<div class="plyr"><div class="plyr__controls">'
            '<button class="plyr__menu"></button></div>'
            '<video src="/v/clip.mp4"></video></div>')
    r = _fam(html)
    assert r["player_family"] == "native_custom", (r["player_family"], r["candidates"][:3])


# ── tells KEEP the brand (already GREEN on baseline; guard against over-demotion) ──
def test_videojs_kept_with_lib_script_tell():
    html = ('<div class="video-js vjs-default-skin"><video src="/v/clip.mp4"></video>'
            '<button class="vjs-big-play-button"></button></div>')
    r = _fam(html, script_srcs=["https://cdn/video.min.js"])
    assert r["player_family"] == "videojs", (r["player_family"], r["candidates"][:3])


def test_videojs_kept_with_storage_tell():
    html = ('<div class="video-js vjs-default-skin"><video src="/v/clip.mp4"></video>'
            '<button class="vjs-big-play-button"></button></div>')
    r = _fam(html, storage_keys=["vjs-volume"])
    assert r["player_family"] == "videojs", (r["player_family"], r["candidates"][:3])
    assert "videojs" in r["storage_confirmed"]


def test_videojs_kept_with_data_attr_element_tell():
    html = ('<div data-vjs-player class="video-js vjs-default-skin">'
            '<video src="/v/clip.mp4"></video>'
            '<button class="vjs-big-play-button"></button></div>')
    r = _fam(html)
    assert r["player_family"] == "videojs", (r["player_family"], r["candidates"][:3])


def test_videojs_kept_when_delivery_is_adaptive_hls():
    # markup-only videojs, but HLS content-type in network => not a native page
    html = ('<div class="video-js vjs-default-skin"><video src="blob:https://x/a"></video>'
            '<button class="vjs-big-play-button"></button></div>')
    net = [{"url": "x", "response_headers": [
        {"name": "content-type", "value": "application/vnd.apple.mpegurl"}]}]
    r = _fam(html, network=net)
    assert r["player_family"] == "videojs", (r["player_family"], r["delivery"])


def test_videojs_kept_when_no_native_video_present():
    # no <video> => no native fallback target; preserves the families.py contract
    html = ('<div class="video-js vjs-default-skin">'
            '<button class="vjs-big-play-button"></button></div>')
    r = _fam(html)
    assert r["player_family"] == "videojs", (r["player_family"], r["candidates"][:3])


def test_brand_class_kept_when_delivery_unknown_no_media():
    # a media-less page (no observed progressive media) is too thin to override a
    # brand class: positive progressive evidence is required, so a bare video-js
    # <video> with no media stays videojs (e.g. a membership gate that never loaded
    # the player). Guards the v3.66.261 positive-delivery refinement. (The result
    # `delivery` reflects the videojs family hint, not the internal pre-delivery the
    # demotion gate inspects, so we assert on the family, which is the contract.)
    r = _fam('<video class="video-js"></video>')
    assert r["player_family"] == "videojs", (r["player_family"], r["candidates"][:3])


def test_real_tell_backed_brand_wins_over_no_tell_skin():
    # vjs- skin markup-only co-present with a jwplayer that DOES carry its lib
    # script: not all-no-tell => rule must not fire; the tell-backed brand wins.
    html = ('<div class="video-js vjs-default-skin"><video src="/v/clip.mp4"></video>'
            '<button class="vjs-big-play-button"></button>'
            '<div class="jwplayer jw-reset"></div></div>')
    r = _fam(html, script_srcs=["https://cdn/jwplayer.js"])
    assert r["player_family"] == "jwplayer", (r["player_family"], r["candidates"][:3])


# ── the broadened markup-brand set (flowplayer/clappr/mediaelement) ──────────
def test_flowplayer_clappr_mediaelement_no_tell_demote_to_native():
    rf = _fam('<div class="flowplayer"><div class="fp-controls"></div>'
              '<video src="/v/c.mp4"></video></div>')
    assert rf["player_family"] == "native_custom", (rf["player_family"], rf["candidates"][:3])
    rc = _fam('<div class="clappr-style"><div class="media-control"></div>'
              '<video src="/v/c.mp4"></video></div>')
    assert rc["player_family"] == "native_custom", (rc["player_family"], rc["candidates"][:3])
    rm = _fam('<div class="mejs__container"><div class="mejs__controls"></div>'
              '<video src="/v/c.mp4"></video></div>')
    assert rm["player_family"] == "native_custom", (rm["player_family"], rm["candidates"][:3])


def test_markup_brand_has_tell_helper():
    sc = set()
    # storage confirmation, lib script, and exclusive element/id/data-attr are tells
    assert pr._markup_brand_has_tell("videojs", "", [], {"videojs"}) is True
    assert pr._markup_brand_has_tell("videojs", "", ["x/video.min.js"], sc) is True
    assert pr._markup_brand_has_tell("jwplayer", "", ["x/jwplayer.js"], sc) is True
    assert pr._markup_brand_has_tell("brid_tv", "", ["x/brid.min.js"], sc) is True
    assert pr._markup_brand_has_tell("videojs", "<div data-vjs-player></div>", [], sc) is True
    assert pr._markup_brand_has_tell("jwplayer", '<div id="jwplayer-0"></div>', [], sc) is True
    # a bare CSS class is NOT a tell
    assert pr._markup_brand_has_tell("videojs", '<div class="vjs-foo"></div>', [], sc) is False
    assert pr._markup_brand_has_tell("jwplayer", '<div class="jwplayer"></div>', [], sc) is False
    # a non-markup-brand family is never floored by this helper
    assert pr._markup_brand_has_tell("theoplayer", "", [], sc) is True
