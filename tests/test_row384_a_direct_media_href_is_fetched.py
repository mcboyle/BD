"""A leaf anchor whose href IS the media file must be fetched, not clicked.

BD_GATE_SCOPE is a module-level ASSIGNMENT below -- the gate classifier parses
the assignment, not a docstring line.

MEASURED on test6 2026-08-29 at v3.66.1342, live page, with BOTH ranking fixes
(rows 380 and 381) deployed. The CHOICE is correct and the download still fails:

  probe, forced wide sweep, we-should-share-your-boyfriends-cum-s51e1:
    WINNER tag=A score=2160 size=5368709120
    href=https://content2a.nubilefilms.com/exclusive/.../nubilefilms_..._3840.mp4
         ?st=lv7YZL_HGs5mufahnkjtpw&e=1788026400&dl=nubilefilms_..._3840.mp4
  the same scene through the runner:
    needs_review: no dl event; scored ok but no download fired

WHY. runner_transport._do_download's direct-URL fast path is gated on
`if best.get("_via_learned") and url_attr:`. `_via_learned` is set at exactly
one place -- detect.py:342, the LEARNED path -- so a WIDE-SWEEP winner can never
reach it, however perfect its href. It falls through to
expect_download(timeout=60000), the browser navigates a signed cross-host .mp4,
no download event fires, and the job burns a full minute to record nothing.

Same shape as the manifest defect (v3.66.819) and fixed the same way: the
routing decision is a PURE FUNCTION over two strings, because _do_download is
~500 lines of browser-coupled code whose transfer point sits far below its
detection point. Manifests keep priority -- _stream_route runs first, and this
function refuses a manifest outright so the ordering cannot silently invert.
"""

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.runner_transport import TransportMixin

route = TransportMixin.__dict__["_direct_media_route"].__func__ \
    if isinstance(TransportMixin.__dict__.get("_direct_media_route"), staticmethod) \
    else getattr(TransportMixin, "_direct_media_route", None)

PAGE = "https://members.nubilefilms.com/video/watch/254257/we-should-share-s51e1"
LIVE = ("https://content2a.nubilefilms.com/exclusive/"
        "we_should_share_your_boyfriends_cum_with_gracey_snow_aubree_blair/videos/"
        "nubilefilms_we_should_share_your_boyfriends_cum_3840.mp4"
        "?st=lv7YZL_HGs5mufahnkjtpw&e=1788026400"
        "&dl=nubilefilms_we_should_share_your_boyfriends_cum_3840.mp4")


def test_precondition_the_routing_function_exists_and_is_pure():
    """It takes two strings and returns two. If this fails the row is not done,
    and every assertion below would be vacuously about None."""
    assert route is not None, (
        "row 384: TransportMixin._direct_media_route is missing -- a wide-sweep "
        "winner with a direct media href still has nowhere to route")
    assert route("", PAGE) == (None, None)
    assert route(None, PAGE) == (None, None)


def test_the_measured_live_href_routes_to_a_direct_fetch():
    url, name = route(LIVE, PAGE)
    assert url == LIVE, "the signed URL must survive verbatim -- st/e/dl are load-bearing"
    assert name == "nubilefilms_we_should_share_your_boyfriends_cum_3840.mp4", (
        f"destination name {name!r}: the dl= parameter names the file the site "
        f"intends, and it is what skip_if_exists compares on the next run")


@pytest.mark.parametrize("href,why", [
    ("https://cdn.example.com/a/b/scene_2160.mp4", "plain .mp4"),
    ("https://cdn.example.com/a/scene.mkv?token=x", "mkv behind a token"),
    ("https://cdn.example.com/a/scene.mov", "mov"),
    ("https://cdn.example.com/a/scene.m4v", "m4v"),
    ("https://cdn.example.com/a/scene.webm", "webm"),
])
def test_direct_media_extensions_route(href, why):
    url, name = route(href, PAGE)
    assert url == href, f"{why}: expected a direct fetch"
    assert name and not name.endswith("/"), f"{why}: needs a usable filename"


@pytest.mark.parametrize("href,why", [
    ("https://cdn.example.com/hls/scene/2.m3u8", "HLS manifest -- _stream_route owns it"),
    ("https://cdn.example.com/dash/scene.mpd", "DASH manifest -- _stream_route owns it"),
    ("https://members.example.com/video/watch/9/title", "a PAGE, not a file"),
    ("https://members.example.com/account", "nav"),
    ("javascript:void(0)", "a click-only affordance"),
    ("#", "an in-page anchor"),
])
def test_negative_control_these_must_still_be_clicked_or_streamed(href, why):
    """The guard must never claim something the click path or the segmented
    downloader owns. A false direct-fetch downloads an HTML page as if it were
    the movie, which is worse than the defect being fixed."""
    assert route(href, PAGE) == (None, None), why


def test_a_relative_media_href_is_resolved_against_the_page():
    """Phase 19.fix's lesson, and _stream_route's: a browser resolves a relative
    href natively on click; httpx receives a string and cannot."""
    url, name = route("/media/scene_2160.mp4", PAGE)
    assert url == "https://members.nubilefilms.com/media/scene_2160.mp4"
    assert name == "scene_2160.mp4"


def test_do_download_actually_calls_it():
    """Seam, not component. The pure function is worthless if the 500-line
    caller never reaches it -- which is precisely the bug: the existing direct
    path exists and is unreachable for a wide-sweep winner."""
    import inspect
    from bulk_downloader import runner_transport as rt
    src = inspect.getsource(rt.TransportMixin._do_download)
    assert "_direct_media_route" in src, (
        "_do_download never calls _direct_media_route, so a direct media href "
        "still reaches expect_download and waits 60s for nothing")
    i_stream = src.index("_stream_route")
    i_direct = src.index("_direct_media_route")
    assert i_stream < i_direct, (
        "the manifest route must be consulted FIRST; otherwise a .m3u8 could be "
        "claimed as a direct fetch and handed to httpx instead of ffmpeg")
