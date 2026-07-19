"""provider_resolve_impl.jwplayer -- verbatim jwplayer resolver from provider_resolve.py."""

from __future__ import annotations
import json
from typing import List, Optional, Tuple
from urllib.parse import quote as _urlquote, urlparse as _urlparse

from ._common import _coerce_int


_JWPLAYER_MEDIA_URL_TMPL = "https://cdn.jwplayer.com/v2/media/{media_id}"


_JWPLAYER_FEED_URL_TMPL = "https://cdn.jwplayer.com/v2/playlists/{feed_id}"


_JWP_HLS_MIMES  = ("application/vnd.apple.mpegurl",
                   "application/x-mpegurl",
                   "application/vnd.apple.mpegURL")


_JWP_DASH_MIMES = ("application/dash+xml",)


_JWP_MP4_MIMES  = ("video/mp4",)


_JWPLAYER_ALLOWED_SCHEMES = ("http", "https")


_JWPLAYER_DRM_MARKER_KEYS = (
    "widevine", "fairplay", "playready", "clearkey",
)


_JWPLAYER_SIGNED_URL_MARKER_KEYS = (
    "signed", "signedurl", "signed_url", "signedurls", "signed_urls",
    "signingkey", "signing_key", "requiresignedurls",
)


def _jwplayer_emit_sources(
    sources: list,
    *,
    embed_url: Optional[str],
    label_prefix: str,
    extra_fields: Optional[dict] = None,
) -> List[dict]:
    """Classify a JWPlayer ``sources`` array into deep_detect
    candidate dicts. Shared between the cloud-media path and the
    feeds path: both produce sources in the same shape (file / type /
    width / height / label / bitrate / filesize).

    ``label_prefix`` is used to differentiate reasons strings between
    the two paths (``"JWPlayer cdn"`` vs ``"JWPlayer feed"``).
    ``extra_fields`` is merged into every emitted candidate — used by
    the feeds path to carry ``playlist_index`` / ``feed_id``.
    """
    extra = extra_fields or {}
    out: List[dict] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        stream_url = s.get("file")
        if not isinstance(stream_url, str) or not stream_url:
            continue

        stype = s.get("type") or ""
        stype_l = stype.lower()
        url_lower = stream_url.lower().split("?", 1)[0]

        is_hls  = (stype in _JWP_HLS_MIMES) or ("mpegurl" in stype_l) \
            or url_lower.endswith(".m3u8")
        is_dash = (stype in _JWP_DASH_MIMES) or ("dash+xml" in stype_l) \
            or url_lower.endswith(".mpd")
        is_mp4  = (stype in _JWP_MP4_MIMES) or (stype_l == "video/mp4") \
            or url_lower.endswith(".mp4")

        if not (is_hls or is_dash or is_mp4):
            continue

        height = _coerce_int(s.get("height"))
        width = _coerce_int(s.get("width"))
        bitrate = _coerce_int(s.get("bitrate"))
        size_bytes = _coerce_int(s.get("filesize") or s.get("fileSize"))

        if is_hls:
            cand = {
                "url": stream_url,
                "source_type": "jwplayer_resolved_hls",
                "score": 90,
                "resolution": None,
                "codec": None,
                "fps": None,
                "size_bytes": None,
                "found_in": "provider_resolved:jwplayer",
                "resolved_from": embed_url,
                "provider_resolved": True,
                "reasons": [
                    f"{label_prefix} HLS master (type={stype or 'inferred'})"
                ],
                "warnings": [],
                "requires_click": False,
            }
            cand.update(extra)
            out.append(cand)
            continue

        if is_dash:
            cand = {
                "url": stream_url,
                "source_type": "jwplayer_resolved_dash",
                "score": 90,
                "resolution": None,
                "codec": None,
                "fps": None,
                "size_bytes": None,
                "found_in": "provider_resolved:jwplayer",
                "resolved_from": embed_url,
                "provider_resolved": True,
                "reasons": [
                    f"{label_prefix} DASH manifest "
                    f"(type={stype or 'inferred'})"
                ],
                "warnings": [],
                "requires_click": False,
            }
            cand.update(extra)
            out.append(cand)
            continue

        # Progressive MP4 -----------------------------------------------
        label = s.get("label") or (f"{height}p" if height else None)
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
        cand = {
            "url": stream_url,
            "source_type": "jwplayer_resolved",
            "score": score,
            "resolution": res,
            "codec": stype or None,
            "fps": None,  # not reported by the cdn JSON
            "size_bytes": size_bytes,
            "bitrate": bitrate,
            "found_in": "provider_resolved:jwplayer",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "reasons": [
                f"{label_prefix} progressive {label or 'unknown'}"
            ],
            "warnings": [],
            "requires_click": False,
        }
        cand.update(extra)
        out.append(cand)
    return out


def resolve_jwplayer(
    ids: dict,
    *,
    embed: Optional[dict] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """Resolve a JWPlayer embed to playable candidates.

    Three paths supported:

      1. **Cloud-hosted media** (``ids["media_id"]``). Calls
         ``cdn.jwplayer.com/v2/media/<media_id>``. Returns JSON with a
         ``playlist`` array where ``playlist[0].sources`` holds the
         per-quality stream catalog.

      2. **Feeds endpoint** (``ids["feed_id"]``, no ``media_id``).
         New in v3.66.20. Calls
         ``cdn.jwplayer.com/v2/playlists/<feed_id>``. Returns the
         same ``playlist`` shape but with multiple entries (one per
         video in the feed). All entries' sources are flattened into a
         single candidate list, with ``playlist_index`` and
         ``playlist_media_id`` on each candidate so consumers can
         re-group them if they want per-video output. The
         ``reasons`` string prefix becomes ``"JWPlayer feed"``.

      3. **Self-hosted** (``ids["config_url"]``, no media_id /
         feed_id). New in v3.66.23. The config_url points at a
         feed JSON the page author hosts themselves. The shape is the
         same as the cdn feed (a ``playlist[]`` with ``sources``), and
         the same per-source classifier runs. The ``reasons`` string
         prefix becomes ``"JWPlayer self-hosted"``.

         Signing posture: if the JSON contains DRM-marker fields
         (``drm.widevine``, ``drm.fairplay``, etc.) or signed-URL
         markers (``signedUrls: true``, ``signingKey`` present), the
         resolver returns ``([], "<scheme> required")`` without
         attempting to bypass. The caller can opt in to signing by
         passing ``signing_callback`` on the embed dict — see
         ``resolve_provider_embed`` for the contract.

    Each source in any path has:
      * ``file`` — the stream URL
      * ``type`` — ``application/vnd.apple.mpegurl`` for HLS,
        ``video/mp4`` for progressive, ``application/dash+xml`` for
        DASH (with extension-on-file fallback for entries missing
        the type)
      * optional ``width``/``height``/``label``/``bitrate``/``filesize``

    Out-of-scope (deferred): full DRM playback (Widevine / FairPlay
    licence acquisition + CDM integration). For embeds whose feed
    JSON requires DRM, the resolver returns a tagged error so the
    operator can route the embed to needs_review.

    For embeds that arrive with none of ``media_id`` / ``feed_id`` /
    ``config_url``, the resolver returns
    ``([], "missing media_id, feed_id, or config_url")``.

    Asset-type discrimination mirrors Wistia: HLS gets ``score=90``
    (one URL covers all variants), progressive gets ``80 + height//100``,
    DASH is treated like HLS for scoring (90) but tagged separately.
    Anything else is skipped.
    """
    if not isinstance(ids, dict):
        ids = {}

    media_id = ids.get("media_id")
    feed_id = ids.get("feed_id")
    config_url = ids.get("config_url")

    embed_url = (embed or {}).get("url")
    signing_callback = (embed or {}).get("signing_callback")

    if media_id:
        return _resolve_jwplayer_media(
            str(media_id), http_get=http_get, embed_url=embed_url,
        )
    if feed_id:
        return _resolve_jwplayer_feed(
            str(feed_id), http_get=http_get, embed_url=embed_url,
        )
    if config_url:
        return _resolve_jwplayer_selfhosted(
            str(config_url), http_get=http_get, embed_url=embed_url,
            signing_callback=signing_callback,
        )
    return [], "missing media_id, feed_id, or config_url"


def _resolve_jwplayer_media(
    media_id: str, *, http_get: HttpGet, embed_url: Optional[str],
) -> Tuple[List[dict], Optional[str]]:
    """Cloud-hosted ``cdn.jwplayer.com/v2/media/<id>`` resolution."""
    url = _JWPLAYER_MEDIA_URL_TMPL.format(media_id=_urlquote(media_id))
    try:
        status, _headers, body = http_get(url)
    except Exception as ex:
        return [], (
            f"jwplayer cdn request failed: {type(ex).__name__}: {ex}"
        )

    if status == 404:
        return [], (
            "jwplayer cdn returned 404 — media_id may be invalid, "
            "deleted, or self-hosted (not on cdn.jwplayer.com)"
        )
    if status == 403:
        return [], (
            "jwplayer cdn returned 403 — likely a signed-URL-only or "
            "geo-restricted media"
        )
    if status >= 400:
        return [], f"jwplayer cdn returned HTTP {status}"

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as ex:
        return [], f"jwplayer cdn returned non-JSON body: {ex}"

    if not isinstance(data, dict):
        return [], "jwplayer cdn JSON is not an object"

    playlist = data.get("playlist") or []
    if not isinstance(playlist, list) or not playlist:
        return [], "jwplayer cdn JSON has no playlist entries"

    # Cloud-hosted media JSON usually has one entry in playlist[] (the
    # media itself); feeds endpoints return many. We only look at the
    # first entry here — the feeds path uses the multi-entry walker
    # below.
    first = playlist[0] if isinstance(playlist[0], dict) else {}
    sources = first.get("sources") or []
    if not isinstance(sources, list):
        return [], "jwplayer cdn playlist[0].sources is not a list"

    candidates = _jwplayer_emit_sources(
        sources, embed_url=embed_url, label_prefix="JWPlayer cdn",
    )
    if not candidates:
        return [], (
            "jwplayer cdn OK but no progressive / HLS / DASH streams "
            "found in playlist[0].sources"
        )
    return candidates, None


def _resolve_jwplayer_feed(
    feed_id: str, *, http_get: HttpGet, embed_url: Optional[str],
) -> Tuple[List[dict], Optional[str]]:
    """Feeds endpoint ``cdn.jwplayer.com/v2/playlists/<id>`` resolution.

    The response shape mirrors cloud-media but with multiple entries.
    Every entry's sources are flattened into the candidate list, with
    ``playlist_index`` (0-based position in the feed) and
    ``playlist_media_id`` (the entry's mediaid) tagged on each
    candidate so the operator can re-group by video.
    """
    url = _JWPLAYER_FEED_URL_TMPL.format(feed_id=_urlquote(feed_id))
    try:
        status, _headers, body = http_get(url)
    except Exception as ex:
        return [], (
            f"jwplayer feeds request failed: {type(ex).__name__}: {ex}"
        )
    if status == 404:
        return [], (
            "jwplayer feeds returned 404 — feed_id may be invalid, "
            "deleted, or not a cloud-hosted feed"
        )
    if status == 403:
        return [], (
            "jwplayer feeds returned 403 — likely a signed-URL-only or "
            "geo-restricted feed"
        )
    if status >= 400:
        return [], f"jwplayer feeds returned HTTP {status}"

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as ex:
        return [], f"jwplayer feeds returned non-JSON body: {ex}"
    if not isinstance(data, dict):
        return [], "jwplayer feeds JSON is not an object"

    playlist = data.get("playlist") or []
    if not isinstance(playlist, list) or not playlist:
        return [], "jwplayer feeds JSON has no playlist entries"

    all_candidates: List[dict] = []
    for idx, entry in enumerate(playlist):
        if not isinstance(entry, dict):
            continue
        sources = entry.get("sources") or []
        if not isinstance(sources, list):
            continue
        media_id = entry.get("mediaid") or entry.get("mediaId")
        extras = {"playlist_index": idx, "feed_id": feed_id}
        if media_id:
            extras["playlist_media_id"] = media_id
        cands = _jwplayer_emit_sources(
            sources, embed_url=embed_url,
            label_prefix=f"JWPlayer feed[{idx}]",
            extra_fields=extras,
        )
        all_candidates.extend(cands)

    if not all_candidates:
        return [], (
            "jwplayer feeds OK but no progressive / HLS / DASH streams "
            "found in any playlist entry"
        )
    return all_candidates, None


def _jwplayer_check_drm_markers(data: dict) -> Optional[str]:
    """Inspect a parsed JWPlayer feed JSON for DRM / signed-URL
    markers. Returns a scheme tag (one of ``"widevine"`` /
    ``"fairplay"`` / ``"playready"`` / ``"clearkey"`` / ``"signed"``)
    if found, or ``None`` if the feed is unsigned.

    Looks at both top-level config keys and per-playlist-entry
    ``drm`` blocks. JWPlayer feed JSON puts DRM under
    ``playlist[].drm.<scheme>`` per the public docs; some self-hosted
    setups bubble it to the top level too. Signed-URL flags appear
    at the top level. Marker-key matching is case-insensitive on the
    DICT keys so ``signedUrls`` / ``signingKey`` / ``SIGNED_URLS``
    all match the same canonical marker.
    """
    if not isinstance(data, dict):
        return None

    def _has_marker(d: dict, markers: tuple) -> bool:
        # Case-insensitive lookup: marker list is lowercase, dict
        # keys can be any case. We don't normalize the values — only
        # the key match needs to be case-insensitive.
        lower_keys = {k.lower(): v for k, v in d.items() if isinstance(k, str)}
        return any(lower_keys.get(m) for m in markers)

    # Top-level signed-URL markers (truthy values).
    if _has_marker(data, _JWPLAYER_SIGNED_URL_MARKER_KEYS):
        return "signed"

    # v3.66.524 (VR-P16): a top-level drm block (data['drm'].<scheme>) -- the
    # "bubble it to the top level" case the docstring describes -- was never
    # inspected, so a feed with ONLY a top-level drm block (no per-entry drm, no
    # signed markers) mis-pinned as unsigned. Mirror the per-entry detection here.
    top_drm = data.get("drm") or data.get("DRM")
    if isinstance(top_drm, dict):
        top_drm_lower = {k.lower(): v for k, v in top_drm.items()
                         if isinstance(k, str)}
        for scheme in _JWPLAYER_DRM_MARKER_KEYS:
            if scheme in top_drm_lower:
                return scheme

    # Per-entry DRM marker. We only need to detect the first scheme;
    # the operator's choice of how to handle is the same regardless
    # of which entry has it.
    playlist = data.get("playlist") or []
    if isinstance(playlist, list):
        for entry in playlist:
            if not isinstance(entry, dict):
                continue
            drm = entry.get("drm") or entry.get("DRM")
            if isinstance(drm, dict):
                drm_lower = {k.lower(): v for k, v in drm.items()
                             if isinstance(k, str)}
                for scheme in _JWPLAYER_DRM_MARKER_KEYS:
                    if scheme in drm_lower:
                        return scheme
            # Some configs put signed-URL markers per-entry too.
            if _has_marker(entry, _JWPLAYER_SIGNED_URL_MARKER_KEYS):
                return "signed"
    return None


def _resolve_jwplayer_selfhosted(
    config_url: str,
    *,
    http_get: HttpGet,
    embed_url: Optional[str],
    signing_callback: Optional[HttpGet] = None,
) -> Tuple[List[dict], Optional[str]]:
    """Self-hosted JWPlayer feed JSON. The URL can point at any host
    (not just cdn.jwplayer.com); the response shape is the same
    multi-entry ``playlist[]`` used by the cdn feeds endpoint.

    See the module-level comment block on self-hosted JWPlayer for
    signing posture and what is / isn't attempted.

    Returns ``([], err)`` where ``err`` carries a structured prefix
    when the failure is signing-related:
      * ``"jwplayer self-hosted requires signed access (scheme=signed)"``
      * ``"jwplayer self-hosted requires DRM (scheme=widevine)"``
    The caller can string-match on the ``scheme=`` tag to route the
    embed to needs_review with an appropriate reason.
    """
    # Reject non-http(s) schemes defensively. Page HTML might carry
    # data: / javascript: / file: URLs and we should never fetch.
    parsed = _urlparse(config_url)
    if parsed.scheme.lower() not in _JWPLAYER_ALLOWED_SCHEMES:
        return [], (
            f"jwplayer self-hosted: refusing non-http(s) config_url "
            f"(scheme={parsed.scheme!r})"
        )
    if not parsed.netloc:
        return [], "jwplayer self-hosted: config_url has no host"

    # Pick the fetcher. If the caller wired a signing_callback on the
    # embed dict, use that — operator credentials, operator's call
    # what host/auth/cookies to apply. Otherwise fall back to the
    # injected http_get (or _default_http_get inside the dispatcher).
    fetcher: HttpGet
    fetch_label: str
    if callable(signing_callback):
        fetcher = signing_callback
        fetch_label = "signing_callback"
    else:
        fetcher = http_get
        fetch_label = "http_get"

    try:
        status, _headers, body = fetcher(config_url)
    except Exception as ex:
        return [], (
            f"jwplayer self-hosted request failed via {fetch_label}: "
            f"{type(ex).__name__}: {ex}"
        )

    # 401/403 from an unsigned fetch → the feed needs auth. Treat as
    # signing-required so the caller can route appropriately. If the
    # operator already supplied a signing_callback and we STILL got
    # 401/403, that's a credential problem on their end — pass the
    # status through so they can debug.
    if status in (401, 403):
        if fetch_label == "signing_callback":
            return [], (
                f"jwplayer self-hosted: signing_callback returned "
                f"HTTP {status} — credential likely invalid or expired"
            )
        return [], (
            f"jwplayer self-hosted requires signed access "
            f"(scheme=signed): HTTP {status} from unsigned fetch — "
            f"supply signing_callback on the embed to enable"
        )
    if status == 404:
        return [], (
            "jwplayer self-hosted: config_url returned 404 — feed may "
            "have been deleted or the URL is wrong"
        )
    if status >= 400:
        return [], (
            f"jwplayer self-hosted: config_url returned HTTP {status}"
        )

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as ex:
        return [], (
            f"jwplayer self-hosted: config_url returned non-JSON body "
            f"({type(ex).__name__})"
        )
    if not isinstance(data, dict):
        return [], "jwplayer self-hosted: JSON is not an object"

    # DRM / signed-URL detection. If the feed itself indicates signed
    # access is required, surface a structured error.
    scheme = _jwplayer_check_drm_markers(data)
    if scheme is not None:
        if scheme == "signed":
            return [], (
                "jwplayer self-hosted requires signed access "
                f"(scheme=signed): config has signed-URL markers — "
                f"supply signing_callback on the embed to enable"
            )
        return [], (
            f"jwplayer self-hosted requires DRM (scheme={scheme}): "
            f"feed declares DRM-protected playback, which is out of "
            f"scope for this resolver"
        )

    playlist = data.get("playlist") or []
    if not isinstance(playlist, list) or not playlist:
        return [], (
            "jwplayer self-hosted: JSON has no playlist entries"
        )

    # Same multi-entry walk as the feeds path. The only differences
    # are the reasons-prefix and the extra-field set: we tag with
    # config_url instead of feed_id so consumers can re-group by
    # source feed if a single page has multiple self-hosted embeds.
    all_candidates: List[dict] = []
    for idx, entry in enumerate(playlist):
        if not isinstance(entry, dict):
            continue
        sources = entry.get("sources") or []
        if not isinstance(sources, list):
            continue
        media_id = entry.get("mediaid") or entry.get("mediaId")
        extras = {"playlist_index": idx, "config_url": config_url}
        if media_id:
            extras["playlist_media_id"] = media_id
        cands = _jwplayer_emit_sources(
            sources, embed_url=embed_url,
            label_prefix=f"JWPlayer self-hosted[{idx}]",
            extra_fields=extras,
        )
        all_candidates.extend(cands)

    if not all_candidates:
        return [], (
            "jwplayer self-hosted: feed OK but no progressive / HLS / "
            "DASH streams found in any playlist entry"
        )
    return all_candidates, None
