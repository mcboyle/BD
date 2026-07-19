"""provider_resolve_impl.vimeo -- verbatim vimeo resolver from provider_resolve.py."""

from __future__ import annotations
import json
from typing import List, Optional, Tuple
from urllib.parse import quote as _urlquote

from ._common import _coerce_int


_VIMEO_CONFIG_URL_TMPL = "https://player.vimeo.com/video/{clip_id}/config"


def resolve_vimeo(
    ids: dict,
    *,
    embed: Optional[dict] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """Resolve a Vimeo embed to playable candidates.

    Vimeo's ``/config`` endpoint returns JSON with two relevant
    sections:

      * ``request.files.progressive`` — list of MP4 renditions
        (height, width, mime, fps, url, quality string)
      * ``request.files.hls.cdns.<cdn>.url`` — HLS master playlists
        (one per CDN; we emit the default CDN only to keep the
        candidate list short)

    Both contribute one candidate per stream. Progressive renditions
    get ``source_type: "vimeo_resolved"`` to distinguish them from
    deep_detect's native ``direct_file`` candidates while keeping
    them mergeable into ``download_candidates``.
    """
    clip_id = ids.get("clip_id")
    if not clip_id:
        return [], "missing clip_id"

    url = _VIMEO_CONFIG_URL_TMPL.format(clip_id=_urlquote(str(clip_id)))
    try:
        status, _headers, body = http_get(url)
    except Exception as ex:
        return [], f"vimeo /config request failed: {type(ex).__name__}: {ex}"

    if status == 403:
        return [], (
            "vimeo /config returned 403 — likely a domain-restricted "
            "or password-protected video"
        )
    if status == 404:
        return [], "vimeo /config returned 404 — clip_id may be invalid"
    if status >= 400:
        return [], f"vimeo /config returned HTTP {status}"

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as ex:
        return [], f"vimeo /config returned non-JSON body: {ex}"

    if not isinstance(data, dict):
        return [], "vimeo /config JSON is not an object"

    embed_url = (embed or {}).get("url")
    candidates: List[dict] = []

    files = (
        (data.get("request") or {}).get("files") or {}
    ) if isinstance(data.get("request"), dict) else {}

    # Progressive MP4 renditions ------------------------------------------
    progressive = files.get("progressive") or []
    if isinstance(progressive, list):
        for p in progressive:
            if not isinstance(p, dict):
                continue
            stream_url = p.get("url")
            if not isinstance(stream_url, str) or not stream_url:
                continue
            height = _coerce_int(p.get("height"))
            width = _coerce_int(p.get("width"))
            fps = _coerce_int(p.get("fps"))
            label = p.get("quality")
            if not label and height:
                label = f"{height}p"
            res = None
            if height:
                res = {
                    "height": height,
                    "label": label or f"{height}p",
                    "rank": height,
                }
                if width:
                    res["width"] = width
            score = 80 + (height // 100 if height else 0)
            candidates.append({
                "url": stream_url,
                "source_type": "vimeo_resolved",
                "score": score,
                "resolution": res,
                "codec": p.get("mime") or None,
                "fps": fps,
                "size_bytes": _coerce_int(p.get("size")),
                "found_in": "provider_resolved:vimeo",
                "resolved_from": embed_url,
                "provider_resolved": True,
                "reasons": [
                    f"Vimeo /config progressive rendition "
                    f"{label or 'unknown'}"
                ],
                "warnings": [],
                "requires_click": False,
            })

    # HLS master playlists ------------------------------------------------
    hls = files.get("hls") or {}
    if isinstance(hls, dict):
        cdns = hls.get("cdns") or {}
        default_cdn = hls.get("default_cdn")
        if isinstance(cdns, dict):
            # Prefer the default CDN; if absent, take the first one.
            cdn_key = default_cdn if default_cdn in cdns else next(
                iter(cdns), None)
            if cdn_key:
                cdn = cdns.get(cdn_key) or {}
                hls_url = cdn.get("url") if isinstance(cdn, dict) else None
                if isinstance(hls_url, str) and hls_url:
                    candidates.append({
                        "url": hls_url,
                        "source_type": "vimeo_resolved_hls",
                        "score": 90,
                        "resolution": None,  # HLS master — variants in playlist
                        "codec": None,
                        "fps": None,
                        "size_bytes": None,
                        "found_in": "provider_resolved:vimeo",
                        "resolved_from": embed_url,
                        "provider_resolved": True,
                        "reasons": [
                            f"Vimeo /config HLS master (cdn={cdn_key})"
                        ],
                        "warnings": [],
                        "requires_click": False,
                    })

    if not candidates:
        # Endpoint OK but no renditions surfaced — could be a live
        # stream, a deleted video, or an unexpected schema. Surface
        # as an error so the operator knows resolution "ran but
        # found nothing", which is different from "no resolver
        # exists".
        return [], "vimeo /config OK but no progressive or hls streams found"

    return candidates, None
