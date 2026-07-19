"""v3.66.152 — harden the flat-draft path against real capA leaks.

The old (7712) builder dumps every captured URL verbatim, so a flat draft can
carry Cloudflare-Stream JWTs (in the path or as ?p=<jwt>&s=<sig>), cachefly
base64 path tokens, the user's email in a getbeamer tracker URL, and non-media
API noise (offers/subscriptions/experiments/banners/event-log/comments/votes/
tags). None may reach a template. Also fixes resolutions being dropped from a
flat draft's top-level ``resolutions`` key.
"""
from __future__ import annotations

from bulk_downloader.pattern_hygiene import scrub_network_patterns
from bulk_downloader.template_normalize import normalize_draft

JWT_PATH = ("https://customer-x.cloudflarestream.com/"
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJuYmYiOjE3ODA1MjkxODN9/manifest/video.m3u8")
JWT_QUERY = ("https://customer-x.cloudflarestream.com/04ed100fd34e83a591c21180119696d2/"
             "video/{id}/seg_1.mp4?p=eyJ0eXBlIjoic2VnbWVudCJ9&s=aA3CocKwOMK4byIxAh7CtRxr")
B64_PATH = ("https://vod1.cachefly.net/"
            "ZGlybWF0Y2g9dHJ1ZTtleHBpcmV0aW1lPTE3ODA1NDUzODMvMWI0YjFiNWI3M2Zj/"
            "vod/teamskeet/dolly_paige/videos/full/VP9_ABR/VP9_1080.mp4")
GETBEAMER = "https://backend.getbeamer.com/numberFeatures?url=app.reptyle.com&email=itdude1865%40pm.me"

NOISE = [
    "https://api2.reptyle.com/api/v1/exclusive-offers/free_account",
    "https://api2.reptyle.com/api/v1/active-subscriptions",
    "https://api2.reptyle.com/api/v1/experiments",
    "https://api2.reptyle.com/api/v1/banners/hero",
    "https://api2.reptyle.com/api/v1/event-log",
    "https://api2.reptyle.com/api/v1/movie/{id}/comments",
    "https://api2.reptyle.com/api/v1/movie/{id}/votes",
    "https://api2.reptyle.com/api/v1/tags",
]
WATCH = "https://api2.reptyle.com/api/v1/movie/{id}/watch"
DL = "https://api2.reptyle.com/api/v1/movie/{id}/download-resolution/{resolution}"
MEDIA_SUFFIX = ".../VP9_{resolution}.mp4"


def _scrub(pats):
    return scrub_network_patterns(pats)


def test_jwt_in_path_rejected():
    r = _scrub([JWT_PATH, WATCH])
    assert JWT_PATH in r["dropped"] and WATCH in r["kept"]


def test_jwt_in_query_value_rejected():
    r = _scrub([JWT_QUERY, WATCH])
    assert JWT_QUERY in r["dropped"]


def test_signed_base64_path_rejected():
    r = _scrub([B64_PATH, WATCH])
    assert B64_PATH in r["dropped"]


def test_getbeamer_and_email_rejected():
    r = _scrub([GETBEAMER, WATCH])
    assert GETBEAMER in r["dropped"]
    assert not any("email" in k for k in r["kept"])


def test_noise_api_paths_dropped():
    r = _scrub(NOISE + [WATCH, DL])
    assert all(n in r["dropped"] for n in NOISE), r["kept"]
    # the real media endpoints survive
    assert WATCH in r["kept"] and DL in r["kept"]


def test_download_resolution_endpoint_survives():
    # the single most important pattern must never be dropped as noise/signed
    assert DL in _scrub([DL])["kept"]


def test_clean_relative_and_media_suffix_kept():
    r = _scrub(["/api/v1/movie/{id}/watch", "/api/v{version}/movie/{id}/download-resolution/{resolution}"])
    assert len(r["kept"]) == 2 and not r["dropped"]


def test_short_hex_id_not_flagged():
    # a 32-char hex id segment is legit (below the 40-char opaque threshold)
    u = "https://cdn.x.com/04ed100fd34e83a591c21180119696d2/video/init.mp4"
    assert u in _scrub([u])["kept"]


def test_flat_draft_capA_shape_is_clean():
    flat = {
        "schema": "bulk_downloader.template.draft.v1",
        "host": "app.reptyle.com",
        "resolutions": [2160, 1080, 720, 540, 480, 360, 240],
        "network_patterns": [JWT_PATH, JWT_QUERY, B64_PATH, GETBEAMER] + NOISE + [WATCH, DL],
        "selectors": {"download": {"button": 'a[href*="download" i]'}},
    }
    c = normalize_draft(flat)
    # nothing signed / PII / noise survives
    np = c["network_patterns"]
    assert WATCH in np and DL in np
    assert not any(("eyJ" in p) or ("getbeamer" in p) or ("cachefly" in p)
                   or ("event-log" in p) or ("/votes" in p) or ("/tags" in p) for p in np)
    # resolutions recovered from the flat top-level key (was the bug)
    assert c["resolutions"] == [2160, 1080, 720, 540, 480, 360, 240]
    # old-draft button mapped to trigger
    assert c["selectors"]["download"].get("trigger") == 'a[href*="download" i]'
