"""Row 932: extensionless media streams are routed by HTTP response headers."""

from __future__ import annotations

import importlib
import inspect

import pytest

from bulk_downloader.runner_transport import TransportMixin

BD_GATE_SCOPE = "module"

PAGE = "https://example.test/members/video/3480"
STREAM = "https://api.example.test/v2/stream/3480"          # no file extension
MP4 = "https://cdn.example.test/files/scene_1080.mp4"


def _router():
    try:
        return importlib.import_module("bulk_downloader.mime_router")
    except ModuleNotFoundError:
        base = TransportMixin._direct_media_route(STREAM, PAGE)
        pytest.fail(
            f"ROW932 no bulk_downloader/mime_router.py: extensionless stream "
            f"{STREAM} is unroutable, transport _direct_media_route -> {base!r}"
        )


# (1) extensionless video stream detection from headers ---------------------

def test_extensionless_video_stream_is_routed_from_content_type():
    assert TransportMixin._direct_media_route(STREAM, PAGE) == (None, None)
    mr = _router()
    url, name = mr.route_by_headers(STREAM, {"Content-Type": "video/mp4"}, PAGE)
    assert url == STREAM
    assert name == "3480.mp4"


def test_octet_stream_with_accept_ranges_is_routed_as_media():
    mr = _router()
    headers = {"content-type": "application/octet-stream",
               "accept-ranges": "bytes",
               "content-length": "5368709120"}
    assert mr.media_verdict(headers) == "media"
    url, name = mr.route_by_headers(STREAM, headers, PAGE)
    assert url == STREAM
    assert name == "3480.mp4"


def test_content_disposition_names_the_file():
    mr = _router()
    headers = {"Content-Type": "video/webm; codecs=vp9",
               "Content-Disposition": 'attachment; filename="Scene Title [1080p].webm"'}
    assert mr.route_by_headers(STREAM, headers, PAGE) == (STREAM, "Scene Title [1080p].webm")


def test_relative_url_is_resolved_against_the_page():
    mr = _router()
    url, name = mr.route_by_headers("/stream/3480", {"Content-Type": "video/mp4"}, PAGE)
    assert url == "https://example.test/stream/3480"
    assert name == "3480.mp4"


def test_header_list_and_mixed_case_names_are_accepted():
    mr = _router()
    pairs = [("CONTENT-TYPE", "Video/MP4"), ("x-cache", "HIT")]
    assert mr.media_verdict(pairs) == "media"
    assert mr.route_by_headers(STREAM, pairs, PAGE) == (STREAM, "3480.mp4")


# (2) rejection of non-media binaries ----------------------------------------

@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/octet-stream"},                      # no Accept-Ranges
    {"Content-Type": "application/octet-stream", "Accept-Ranges": "none"},
    {"Content-Type": "application/zip", "Accept-Ranges": "bytes"},
    {"Content-Type": "application/pdf", "Accept-Ranges": "bytes"},
    {"Content-Type": "image/jpeg", "Accept-Ranges": "bytes"},
    {"Content-Type": "text/html; charset=utf-8", "Accept-Ranges": "bytes"},
    {"Content-Type": "application/json"},
    {},
    {"Accept-Ranges": "bytes"},
])
def test_non_media_binaries_are_rejected(headers):
    mr = _router()
    assert mr.media_verdict(headers) == "non_media", headers
    assert mr.route_by_headers(STREAM, headers, PAGE) == (None, None), headers


def test_streaming_manifest_is_not_handed_to_the_http_worker():
    mr = _router()
    headers = {"Content-Type": "application/vnd.apple.mpegurl"}
    assert mr.media_verdict(headers) == "streaming"
    assert mr.route_by_headers(STREAM, headers, PAGE) == (None, None)


def test_non_http_urls_are_refused_even_with_media_headers():
    mr = _router()
    for bad in ("javascript:void(0)", "data:video/mp4;base64,AAAA", "#", "", None):
        assert mr.route_by_headers(bad, {"Content-Type": "video/mp4"}, PAGE) == (None, None), bad


# (3) download transfer handover ---------------------------------------------

def test_handover_tuple_matches_the_transport_direct_route_contract():
    mr = _router()
    routed = mr.route_by_headers(STREAM, {"Content-Type": "video/mp4"}, PAGE)
    direct = TransportMixin._direct_media_route(MP4, PAGE)
    assert direct == (MP4, "scene_1080.mp4")
    assert tuple(type(x) for x in routed) == tuple(type(x) for x in direct)
    params = list(inspect.signature(TransportMixin._http_download).parameters)
    assert params[:6] == ["self", "page_url", "page", "ctx", "file_url", "final_path"]


def test_routed_content_types_are_media_to_the_transport_gate():
    mr = _router()
    for ctype in ("video/mp4", "video/webm", "audio/mpeg"):
        assert mr.media_verdict({"Content-Type": ctype}) == "media"
        assert TransportMixin._looks_like_media(ctype, b"") is True, ctype
    # The transport gate cannot judge octet-stream by type alone; that is why
    # the router demands Accept-Ranges: bytes beside it.
    assert TransportMixin._looks_like_media("application/octet-stream", b"") is False
    assert mr.media_verdict({"Content-Type": "application/octet-stream"}) == "non_media"


def test_destination_name_extension_follows_the_subtype():
    mr = _router()
    cases = {"video/webm": "3480.webm", "video/x-matroska": "3480.mkv",
             "video/quicktime": "3480.mov", "audio/mpeg": "3480.mp3",
             "video/x-unknown-subtype": "3480.mp4"}
    for ctype, expected in cases.items():
        assert mr.route_by_headers(STREAM, {"Content-Type": ctype}, PAGE)[1] == expected, ctype
