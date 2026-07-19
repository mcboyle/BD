"""provider_resolve_impl.wistia -- verbatim wistia resolver from provider_resolve.py."""

from __future__ import annotations
import json
from typing import List, Optional, Tuple
from urllib.parse import quote as _urlquote

from ._common import _coerce_int


_WISTIA_MEDIA_URL_TMPL = "https://fast.wistia.net/embed/medias/{hashed_id}.json"


def resolve_wistia(
    ids: dict,
    *,
    embed: Optional[dict] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """Resolve a Wistia embed to playable candidates.

    Wistia's ``fast.wistia.net/embed/medias/<hashed_id>.json`` endpoint
    returns JSON with a ``media.assets`` array. Each asset has at minimum
    a ``url``, ``type``, ``width``, ``height``, and (for video) ``bitrate``.

    Asset ``type`` values we care about:

      * ``original`` / ``mp4_video`` / ``md_mp4_video`` / ``hd_mp4_video``
        — progressive MP4 renditions (one candidate each)
      * ``hls_playlist`` — HLS master playlist (one candidate, scored
        higher because HLS contains all renditions in one URL)

    Other asset types (``still_image``, ``iphone_video`` thumbnails,
    flash relics, etc.) are skipped. Anything without a recognized
    ``type`` or a ``video/mp4``/``mpegurl`` content_type is ignored.
    """
    hashed_id = ids.get("hashed_id")
    if not hashed_id:
        return [], "missing hashed_id"

    url = _WISTIA_MEDIA_URL_TMPL.format(hashed_id=_urlquote(str(hashed_id)))
    try:
        status, _headers, body = http_get(url)
    except Exception as ex:
        return [], f"wistia medias.json request failed: {type(ex).__name__}: {ex}"

    if status == 404:
        return [], "wistia medias.json returned 404 — hashed_id may be invalid or media deleted"
    if status == 403:
        return [], (
            "wistia medias.json returned 403 — likely a private or "
            "domain-restricted media"
        )
    if status >= 400:
        return [], f"wistia medias.json returned HTTP {status}"

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as ex:
        return [], f"wistia medias.json returned non-JSON body: {ex}"

    if not isinstance(data, dict):
        return [], "wistia medias.json JSON is not an object"

    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    assets = media.get("assets") or []
    if not isinstance(assets, list):
        return [], "wistia medias.json `media.assets` is not a list"

    embed_url = (embed or {}).get("url")
    candidates: List[dict] = []

    # Progressive MP4 + HLS, in one pass ----------------------------------
    #
    # Mirror the Vimeo scoring: HLS at 90 (single URL, all variants),
    # progressive at 80 + height_bucket. Distinguish by `type` with a
    # fallback to `content_type` for older API responses.
    hls_types = ("hls_playlist", "hls_playlists")
    mp4_types = ("original", "mp4_video", "md_mp4_video", "hd_mp4_video",
                 "iphone_video")
    for a in assets:
        if not isinstance(a, dict):
            continue
        stream_url = a.get("url")
        if not isinstance(stream_url, str) or not stream_url:
            continue

        atype = a.get("type") or ""
        content_type = a.get("content_type") or a.get("contentType") or ""

        is_hls = (atype in hls_types) or ("mpegurl" in content_type.lower())
        is_mp4 = (atype in mp4_types) or (content_type == "video/mp4")
        if not (is_hls or is_mp4):
            continue

        height = _coerce_int(a.get("height"))
        width = _coerce_int(a.get("width"))
        bitrate = _coerce_int(a.get("bitrate"))
        size_bytes = _coerce_int(a.get("fileSize") or a.get("size"))

        if is_hls:
            candidates.append({
                "url": stream_url,
                "source_type": "wistia_resolved_hls",
                "score": 90,
                "resolution": None,  # HLS master; variants in playlist
                "codec": None,
                "fps": None,
                "size_bytes": None,
                "found_in": "provider_resolved:wistia",
                "resolved_from": embed_url,
                "provider_resolved": True,
                "reasons": [
                    f"Wistia medias.json HLS master (type={atype or 'inferred'})"
                ],
                "warnings": [],
                "requires_click": False,
            })
            continue

        # Progressive MP4 -------------------------------------------------
        label = a.get("slug") or atype
        if not label and height:
            label = f"{height}p"
        res = None
        if height:
            res = {
                "height": height,
                "label": (f"{height}p" if not label else str(label)),
                "rank": height,
            }
            if width:
                res["width"] = width
        score = 80 + (height // 100 if height else 0)
        candidates.append({
            "url": stream_url,
            "source_type": "wistia_resolved",
            "score": score,
            "resolution": res,
            "codec": content_type or None,
            "fps": None,  # not reported by medias.json
            "size_bytes": size_bytes,
            "bitrate": bitrate,
            "found_in": "provider_resolved:wistia",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "reasons": [
                f"Wistia medias.json progressive {label or 'unknown'}"
            ],
            "warnings": [],
            "requires_click": False,
        })

    if not candidates:
        return [], "wistia medias.json OK but no progressive or hls streams found"

    return candidates, None
