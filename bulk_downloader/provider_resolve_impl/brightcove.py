"""provider_resolve_impl.brightcove -- verbatim brightcove resolver from provider_resolve.py."""

from __future__ import annotations
import json
from typing import List, Optional, Tuple
from urllib.parse import quote as _urlquote

from ._common import _coerce_int


_BRIGHTCOVE_PLAYBACK_URL_TMPL = (
    "https://edge.api.brightcove.com/playback/v1/accounts/"
    "{account_id}/videos/{video_id}"
)


def resolve_brightcove(
    ids: dict,
    *,
    embed: Optional[dict] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """Resolve a Brightcove embed to playable candidates.

    Brightcove's Playback API at
    ``edge.api.brightcove.com/playback/v1/accounts/<account>/videos/<video>``
    returns JSON describing the available sources. The response has a
    ``sources`` array where each entry is either:

      * HLS (``src`` is .m3u8, ``type`` is application/x-mpegURL),
      * DASH (``type`` is application/dash+xml),
      * MP4 (``container`` is MP4, optional ``height``/``width``/``avg_bitrate``).

    The Playback API requires a per-account policy key in the
    ``BCOV-Policy`` request header. Without it the endpoint 401s. The
    key is carried on the ``ids`` dict as ``policy_key`` — populated
    by ``_extract_provider_ids`` from inline JS or ``data-policy-key``
    attributes — or, as a fallback, on the embed dict itself
    (``embed["policy_key"]``) so a caller with policy keys cached
    separately in site config can thread them in.

    If the policy key isn't present, the resolver returns
    ``([], "missing policy_key")`` immediately — no network call. This
    is the common failure mode for Brightcove embeds in the wild; the
    operator needs to find the key in the surrounding page or stash it
    in site config.

    Scoring mirrors Wistia / JWPlayer: HLS at 90, DASH at 90, MP4 at
    80 + height//100.
    """
    if not isinstance(ids, dict):
        ids = {}

    account_id = ids.get("account_id")
    video_id = ids.get("video_id")
    if not account_id:
        return [], "missing account_id"
    if not video_id:
        return [], "missing video_id"

    # Policy key: look in ids first (extracted from page), then on the
    # embed dict (caller-supplied from site config / out-of-band).
    policy_key = ids.get("policy_key")
    if not policy_key and isinstance(embed, dict):
        ek = embed.get("policy_key")
        if isinstance(ek, str) and ek:
            policy_key = ek
    if not policy_key:
        return [], (
            "missing policy_key — Brightcove Playback API requires a "
            "BCOV-Policy header (account-scoped). Extract from the page "
            "or pass via embed['policy_key']"
        )

    url = _BRIGHTCOVE_PLAYBACK_URL_TMPL.format(
        account_id=_urlquote(str(account_id)),
        video_id=_urlquote(str(video_id)),
    )

    # The default _default_http_get doesn't know about the BCOV-Policy
    # header. Brightcove resolution is therefore the first resolver
    # that REQUIRES a custom http_get for the production path. Callers
    # that want to use the default httpx-based path must wrap it with
    # the appropriate header. The test path injects via http_get.
    #
    # We document this constraint here rather than silently failing or
    # silently injecting an httpx call ourselves: production wiring is
    # the caller's responsibility (deep_detect's
    # `resolve_provider_embeds` step does this when it calls into us).
    try:
        status, _headers, body = http_get(url)
    except Exception as ex:
        return [], (
            f"brightcove playback request failed: {type(ex).__name__}: {ex}"
        )

    if status == 401:
        return [], (
            "brightcove playback returned 401 — policy_key invalid, "
            "expired, or wrong account"
        )
    if status == 403:
        return [], (
            "brightcove playback returned 403 — likely geo-restricted, "
            "DRM-protected, or domain-restricted"
        )
    if status == 404:
        return [], (
            "brightcove playback returned 404 — video_id may be "
            "invalid or deleted"
        )
    if status >= 400:
        return [], f"brightcove playback returned HTTP {status}"

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as ex:
        return [], f"brightcove playback returned non-JSON body: {ex}"

    if not isinstance(data, dict):
        return [], "brightcove playback JSON is not an object"

    sources = data.get("sources") or []
    if not isinstance(sources, list):
        return [], "brightcove playback `sources` is not a list"

    embed_url = (embed or {}).get("url")
    candidates: List[dict] = []

    # Per-source classification ------------------------------------------
    #
    # Brightcove uses a mix of `type` (MIME), `container`, and `src`
    # extension to identify the source. We accept whichever signal
    # matches first.
    for s in sources:
        if not isinstance(s, dict):
            continue
        stream_url = s.get("src")
        if not isinstance(stream_url, str) or not stream_url:
            continue

        stype = (s.get("type") or "").lower()
        container = (s.get("container") or "").lower()
        url_lower = stream_url.lower().split("?", 1)[0]

        is_hls = ("mpegurl" in stype) or url_lower.endswith(".m3u8")
        is_dash = ("dash+xml" in stype) or url_lower.endswith(".mpd")
        is_mp4 = (container == "mp4") or (stype == "video/mp4") \
            or url_lower.endswith(".mp4")

        if not (is_hls or is_dash or is_mp4):
            continue

        height = _coerce_int(s.get("height"))
        width = _coerce_int(s.get("width"))
        bitrate = _coerce_int(s.get("avg_bitrate") or s.get("bitrate"))
        size_bytes = _coerce_int(s.get("size"))

        if is_hls:
            candidates.append({
                "url": stream_url,
                "source_type": "brightcove_resolved_hls",
                "score": 90,
                "resolution": None,
                "codec": None,
                "fps": None,
                "size_bytes": None,
                "found_in": "provider_resolved:brightcove",
                "resolved_from": embed_url,
                "provider_resolved": True,
                "reasons": [
                    f"Brightcove playback HLS master "
                    f"(type={stype or 'inferred'})"
                ],
                "warnings": [],
                "requires_click": False,
            })
            continue

        if is_dash:
            candidates.append({
                "url": stream_url,
                "source_type": "brightcove_resolved_dash",
                "score": 90,
                "resolution": None,
                "codec": None,
                "fps": None,
                "size_bytes": None,
                "found_in": "provider_resolved:brightcove",
                "resolved_from": embed_url,
                "provider_resolved": True,
                "reasons": [
                    f"Brightcove playback DASH manifest "
                    f"(type={stype or 'inferred'})"
                ],
                "warnings": [],
                "requires_click": False,
            })
            continue

        # Progressive MP4 -----------------------------------------------
        label = (f"{height}p" if height else None)
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
            "source_type": "brightcove_resolved",
            "score": score,
            "resolution": res,
            "codec": s.get("codec") or stype or None,
            "fps": None,
            "size_bytes": size_bytes,
            "bitrate": bitrate,
            "found_in": "provider_resolved:brightcove",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "reasons": [
                f"Brightcove playback progressive {label or 'unknown'}"
            ],
            "warnings": [],
            "requires_click": False,
        })

    if not candidates:
        return [], (
            "brightcove playback OK but no progressive / HLS / DASH "
            "streams found in sources"
        )

    return candidates, None
