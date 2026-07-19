from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import json
import re

from ._common import (PROGRESSIVE_MEDIA_EXTENSIONS, PROVIDERS, STREAM_MANIFEST_EXTENSIONS)
from .resolution import (detect_resolution_from_text, parse_codec)
from .urls import (classify_url, decode_url)


PLAYER_LIBRARIES = (
    ("videojs",
     ("video-js", "videojs", "vjs-tech", "data-setup")),
    ("jwplayer",
     ("jwplayer", "jwplatform")),
    ("plyr",
     ("plyr", "data-plyr-provider", "data-plyr-embed-id")),
    ("flowplayer",
     ("flowplayer",)),
    ("clappr",
     ("clappr",)),
    ("mediaelement",
     ("mediaelementplayer", "MediaElement.js")),
    ("react_player",
     ("ReactPlayer", "react-player")),
    ("hlsjs",
     ("hls.js", " Hls(", "HlsManifest")),
    ("dashjs",
     ("dash.js", "dashjs", "MediaPlayer().create")),
    ("shaka",
     ("shaka.Player", "shaka-player")),
    ("bitmovin",
     ("bitmovin", "bitmovinplayer")),
    ("theoplayer",
     ("THEOplayer",)),
)


STATE_BLOB_SCRIPT_IDS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__APOLLO_STATE__",
    "__RELAY_STORE__",
    "__INITIAL_STATE__",
    "__PRELOADED_STATE__",
    "__REDUX_STATE__",
    "__SERVER_DATA__",
    "__ROUTE_DATA__",
    "__remixContext",
    "__sveltekit_data__",
)


MEDIA_JSON_KEYS = (
    "url", "src", "href", "file",
    "source", "sources", "media", "assets",
    "video", "audio", "stream", "streams",
    "download", "downloadUrl", "download_url",
    "mediaUrl", "media_url",
    "videoUrl", "video_url",
    "audioUrl", "audio_url",
    "streamUrl", "stream_url",
    "playbackUrl", "playback_url",
    "signedPlaybackUrl",
    "assetUrl", "asset_url",
    "signedUrl", "signed_url",
    "hls", "hlsUrl", "hls_url",
    "dash", "dashUrl", "dash_url",
    "manifest", "manifestUrl",
    "mp4", "webm", "m3u8", "mpd",
)


SOURCE_TYPES = (
    "direct_file",
    "extensionless_file",
    "header_attachment",
    "hls_manifest",
    "low_latency_hls",
    "dash_manifest",
    "smooth_streaming_manifest",
    "adobe_hds_manifest",
    "stream_segment",
    "legacy_stream",
    "webrtc_live",
    "videojs_source",
    "jwplayer_config",
    "plyr_source",
    "react_player_source",
    "shaka_source",
    "bitmovin_source",
    "theoplayer_source",
    "kaltura_embed",
    "brightcove_embed",
    "wistia_embed",
    "vimeo_embed",
    "youtube_embed",
    "jwplayer_embed",
    "mux_stream",
    "cloudflare_stream",
    "bunny_stream",
    "panopto_session",
    "vidyard_embed",
    "json_state_blob",
    "json_ld_media",
    "graphql_endpoint",
    "rest_api_endpoint",
    "trpc_endpoint",
    "rss_enclosure",
    "two_step_post_reveal",
    "redirect_reveal",
    "async_export",
    "signed_url_workflow",
    "resolution_download_card",
    "subtitle_track",
    "thumbnail",
    "poster",
    "checksum_sidecar",
    "signature_sidecar",
    "multi_part_archive",
    "blob_transient",
    "honeypot",
    "trap_link",
    "drm_protected",
    "bot_defense",
    "unknown",
)


_URLISH_RE = re.compile(
    r"https?://[^\s\"'<>]+|//[a-z0-9][a-z0-9.-]*/[^\s\"'<>]*"
    r"|blob:[^\s\"'<>]+",
    re.I,
)


_MAX_JSON_DEPTH = 200


def _walk_json_for_media(obj, *, base_url: str = "",
                        out: Optional[list] = None,
                        seen_keys_path: Optional[list] = None,
                        _depth: int = 0) -> list:
    """Recursively walk a parsed-JSON structure collecting (url, key
    path, key name) for every value that either lives under a known
    media key OR looks URL-shaped with a media extension.

    Returns a list of dicts; dedup happens at the caller."""
    if out is None:
        out = []
    if seen_keys_path is None:
        seen_keys_path = []
    if _depth > _MAX_JSON_DEPTH:
        return out          # deep-JSON guard: media lives near the top
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_lower = str(k).lower()
            is_media_key = key_lower in (
                m.lower() for m in MEDIA_JSON_KEYS)
            # Strings that look like URLs get scraped regardless of key
            if isinstance(v, str):
                if is_media_key:
                    if v.strip():
                        out.append({
                            "url": decode_url(v, base_url=base_url),
                            "key": k,
                            "path": list(seen_keys_path) + [k],
                            "via": "media_key",
                        })
                else:
                    # Look for URL-shaped substrings anywhere
                    for m in _URLISH_RE.findall(v):
                        # Strip both #fragment AND ?query before the
                        # extension check. Pre-fix the function only
                        # stripped ?query, so URLs like
                        # `video.mp4#t=10` would not match `.mp4` and
                        # got skipped.
                        bare = m.lower().rstrip("/").split("?")[0]
                        bare = bare.split("#")[0]
                        if any(bare.endswith(ext)
                               for ext in (PROGRESSIVE_MEDIA_EXTENSIONS
                                           + STREAM_MANIFEST_EXTENSIONS)):
                            out.append({
                                "url": decode_url(m, base_url=base_url),
                                "key": k,
                                "path": list(seen_keys_path) + [k],
                                "via": "url_shaped_string",
                            })
            else:
                _walk_json_for_media(
                    v, base_url=base_url, out=out,
                    seen_keys_path=list(seen_keys_path) + [k], _depth=_depth + 1)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_json_for_media(
                item, base_url=base_url, out=out,
                seen_keys_path=list(seen_keys_path) + [i], _depth=_depth + 1)
    return out


def extract_state_blob_urls(html: str, *,
                            base_url: str = "") -> List[dict]:
    """Scan an HTML document for known state-blob script tags, parse
    each as JSON, and return every URL-ish value found under known
    media keys (or anywhere in the document if the value itself
    looks like a media URL).

    The detection covers Next.js / Nuxt / Apollo / Relay / generic
    React/Redux / Remix / SvelteKit. Each script tag is tried both as
    raw JSON (Next.js style) AND as a `window.X = {...}` assignment
    (older sites).

    # INV-STATE-BLOB-JSON-ONLY (v3.66.11, audit bugs D / E / F / H):
    # the brace balancer correctly handles strings (including ES6
    # template literals with `${...}` interpolations — see the
    # `interp_stack` logic below) and so produces a balanced
    # candidate blob even when the surrounding code contains regex
    # literals or function bodies with embedded `{` / `}`. HOWEVER
    # the final `json.loads()` step REQUIRES valid JSON — so a
    # `window.X = { foo: function(){...}, ... }` literal
    # (JavaScript object syntax with function values, naked regex
    # literals, trailing commas, or unquoted keys) silently fails to
    # parse and the call returns no candidates. This is by design:
    # parsing arbitrary JavaScript object literals would require a
    # full JS-syntax parser (the balancer alone is not enough), and
    # the vast majority of modern hydration uses pure JSON. Sites
    # that ship JS object literals as their state-blob remain
    # unhandled. Documented here so future maintainers don't try to
    # "fix" the balancer when the limitation is the JSON parser
    # downstream. The balancer never crashes on these inputs — it
    # extracts a syntactically-balanced blob, then JSON parsing
    # cleanly fails and the loop moves on.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    found: List[dict] = []

    for script in soup.find_all("script"):
        sid = script.get("id") or ""
        stype = (script.get("type") or "").lower()
        body = script.string or script.text or ""
        if not body.strip():
            continue

        # By script id (Next.js, Nuxt, Apollo, etc.)
        if sid in STATE_BLOB_SCRIPT_IDS or stype == "application/json":
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            results = _walk_json_for_media(data, base_url=base_url)
            for r in results:
                r["source"] = sid or stype
                found.append(r)
            continue

        # By inline window.__X__ = { … } assignment. Cheap match —
        # find the first JSON-looking blob in scripts that mention a
        # known state-blob variable.
        sample = body[:2048]
        for var in STATE_BLOB_SCRIPT_IDS:
            if var in sample:
                # Find the var occurrence(s); for each, check that an
                # `=` follows within a short window and that an `{`
                # follows the `=` within a short window. Pre-fix the
                # code just did body.find(var) (whole-body scan), which
                # could lock onto a mention in a header comment and
                # then chase a far-away `{`. Bounding both lookups to
                # ~80 chars produces tighter matches.
                pos = 0
                found_assignment = False
                while not found_assignment:
                    pos = body.find(var, pos)
                    if pos == -1:
                        break
                    eq = body.find("=", pos, pos + 80)
                    if eq == -1:
                        pos += len(var)
                        continue
                    start = body.find("{", eq, eq + 80)
                    if start == -1:
                        pos += len(var)
                        continue
                    found_assignment = True
                if not found_assignment:
                    continue
                # Balance braces forward (skip strings — including ES6
                # template literals).
                # Template literals in JS use backticks and may contain
                # ${...} interpolations that themselves contain braces.
                # We track a separate `tpl_depth` for interpolation
                # contexts: while in a template literal, `${` opens an
                # interpolation (we treat its body as code and balance
                # braces there), and a matching `}` closes it.
                depth = 0
                end = -1
                in_str = None       # None | '"' | "'" | "`"
                esc = False
                # Stack of (in_str_when_entered, depth_when_entered)
                # snapshots for each open ${...} we're currently
                # inside, so when the matching `}` is found we return
                # to template-literal state. Without this, a `}` that
                # closes an interpolation would falsely decrement the
                # JSON brace depth.
                interp_stack: List[tuple] = []
                for i in range(start, min(len(body), start + 2_000_000)):
                    ch = body[i]
                    if in_str:
                        if esc:
                            esc = False
                            continue
                        if ch == "\\":
                            esc = True
                            continue
                        # In a backtick string, ${ opens an interp.
                        if in_str == "`" and ch == "$" \
                                and i + 1 < len(body) \
                                and body[i + 1] == "{":
                            # We'll consume the `{` on the next loop
                            # iteration via the normal else branch, but
                            # first push our state so the `}` knows.
                            interp_stack.append((in_str, depth))
                            in_str = None  # exit string mode
                            # The next char is `{` — let the depth+=1
                            # path handle it.
                            continue
                        if ch == in_str:
                            in_str = None
                        continue
                    # Not in a string.
                    if ch in ('"', "'", "`"):
                        in_str = ch
                        continue
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        # If we have an active interpolation context
                        # AND this `}` would bring us below the depth
                        # at which the interp started, pop back into
                        # template-literal state instead of decrementing.
                        if interp_stack \
                                and depth == interp_stack[-1][1] + 1:
                            saved_str, _ = interp_stack.pop()
                            in_str = saved_str
                            depth -= 1
                            continue
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end == -1:
                    continue
                blob = body[start:end]
                try:
                    data = json.loads(blob)
                except json.JSONDecodeError:
                    continue
                for r in _walk_json_for_media(data, base_url=base_url):
                    r["source"] = var
                    found.append(r)
                break

    # JSON-LD <script type="application/ld+json"> — VideoObject etc.
    for script in soup.find_all("script",
                                type="application/ld+json"):
        body = (script.string or script.text or "").strip()
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        # Could be a single object or @graph list
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            jtype = item.get("@type") or ""
            jtypes = [jtype] if isinstance(jtype, str) else list(jtype)
            if not any(t in ("VideoObject", "AudioObject", "MediaObject",
                             "ImageObject")
                       for t in jtypes):
                continue
            for k in ("contentUrl", "embedUrl", "thumbnailUrl",
                     "url"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    found.append({
                        "url": decode_url(v, base_url=base_url),
                        "key": k,
                        "path": ["jsonld", jtype, k],
                        "via": "json_ld",
                        "source": "application/ld+json",
                    })

    # Dedup by URL keeping the first occurrence
    seen, out = set(), []
    for r in found:
        u = r.get("url") or ""
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


_PROVIDER_ID_PATTERNS = {
    "kaltura": [
        # Accept URL query syntax (entry_id=...), path syntax
        # (entry_id/...), and JS object syntax (entry_id: "...").
        ("entry_id",   r"""entry_?id\s*[:/=]\s*["']?([0-9a-zA-Z_]+)"""),
        ("partner_id", r"""partner_?id\s*[:/=]\s*["']?(\d+)"""),
        ("uiconf_id",  r"""uiconf_?id\s*[:/=]\s*["']?(\d+)"""),
        ("ks",         r"""\bks\s*[:=]\s*["']?([A-Za-z0-9_/+\-=.]+)"""),
        # Kaltura uses `wid: "_PARTNER"` as a partner-id shortcut.
        ("partner_id_wid", r"""\bwid\s*[:=]\s*["']?_(\d+)"""),
    ],
    "brightcove": [
        ("account_id",  r"""data-account\s*=\s*["']?(\d+)"""),
        ("player_id",   r"""data-player\s*=\s*["']?([A-Za-z0-9_-]+)"""),
        ("video_id",    r"""data-video-id\s*=\s*["']?(\d+)"""),
        ("account_id_url", r"players\.brightcove\.net/(\d+)"),
        ("video_id_url",   r"videoId=(\d+)"),
        # Policy key: Brightcove's account-level Playback API credential.
        # Always starts with `BCpk`, followed by ~80-200 base64url chars.
        # Often appears in inline JS as `policyKey: "..."` or
        # `"policyKey":"..."`. Carried through `ids` as `policy_key`;
        # `resolve_brightcove` reads it from there.
        ("policy_key",      r"""policyKey['"]?\s*[:=]\s*['"](BCpk[A-Za-z0-9_.\-]{40,300})"""),
        ("policy_key_attr", r"""data-policy-key\s*=\s*['"](BCpk[A-Za-z0-9_.\-]{40,300})"""),
    ],
    "wistia": [
        ("hashed_id",     r"wistia_async_([A-Za-z0-9]+)"),
        ("hashed_id_url", r"wistia\.com/(?:embed/)?medias?/([A-Za-z0-9]+)"),
        ("hashed_id_attr", r"""data-hashed-id\s*=\s*["']?([A-Za-z0-9]+)"""),
    ],
    "vimeo": [
        ("clip_id_player", r"player\.vimeo\.com/video/(\d+)"),
        ("clip_id_url",   r"vimeo\.com/(?:video/)?(\d+)"),
    ],
    "youtube": [
        ("video_id_embed", r"youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]{6,})"),
        ("video_id_short", r"youtu\.be/([A-Za-z0-9_-]{6,})"),
        ("video_id_v",     r"[?&]v=([A-Za-z0-9_-]{6,})"),
    ],
    "mux": [
        ("playback_id_url", r"stream\.mux\.com/([A-Za-z0-9]+)"),
        ("playback_id_attr", r"""playback[_-]?id\s*[:=]\s*["']?([A-Za-z0-9]+)"""),
    ],
    "cloudflare_stream": [
        ("video_id", r"(?:videodelivery\.net|cloudflarestream\.com)/([A-Za-z0-9]+)"),
    ],
    "bunny_stream": [
        ("video_id", r"iframe\.mediadelivery\.net/(?:embed/)?(\d+)/([A-Za-z0-9-]+)"),
        ("library_video", r"vz-([A-Za-z0-9-]+)\.b-cdn\.net/([A-Za-z0-9-]+)"),
    ],
    "panopto": [
        ("session_id",  r"[?&](?:id|SessionId|sessionId)=([A-Za-z0-9-]+)"),
    ],
    "vidyard": [
        ("player_uuid", r"play\.vidyard\.com/([A-Za-z0-9]+)"),
        ("uuid_attr",   r"""data-uuid\s*=\s*["']?([A-Za-z0-9]+)"""),
    ],
    "dailymotion": [
        ("video_id", r"dailymotion\.com/(?:embed/)?video/([A-Za-z0-9]+)"),
        ("video_id_short", r"dai\.ly/([A-Za-z0-9]+)"),
    ],
    "sproutvideo": [
        ("video_id", r"videos\.sproutvideo\.com/(?:embed/)?([A-Za-z0-9]+)"),
    ],
    "jwplayer": [
        # Cloud-hosted media JSON (the canonical per-video URL):
        #   https://cdn.jwplayer.com/v2/media/<media_id>
        ("media_id_url",  r"cdn\.jwplayer\.com/v2/media/([A-Za-z0-9]{6,})"),
        # JWPlayer cloud player iframe — the embed shape most pages use:
        #   https://cdn.jwplayer.com/players/<media_id>-<player_id>.html
        # The first id is the media; the second is the player config.
        ("media_id_player", r"cdn\.jwplayer\.com/players/([A-Za-z0-9]{6,})-[A-Za-z0-9]+\.(?:html|js)"),
        # Feeds endpoint (playlists / multi-media):
        #   https://feeds.jwplayer.com/feeds/<feed_id>.json
        ("feed_id_url",   r"feeds\.jwplayer\.com/feeds/([A-Za-z0-9]{6,})"),
        # jwplayer("x").setup({playlist:"<url>"}) inline configs often
        # name the id explicitly via mediaid: / playlistid: attrs.
        ("media_id_attr", r"""mediaid\s*[:=]\s*["']([A-Za-z0-9]{6,})"""),
        ("feed_id_attr",  r"""playlistid\s*[:=]\s*["']([A-Za-z0-9]{6,})"""),
        # v3.66.23 — self-hosted JWPlayer feed URL. Capture the URL
        # supplied to setup({playlist: "<url>"}) when it's NOT on a
        # JWPlayer cdn host. The (?!...) negative lookahead inside the
        # captured URL excludes cdn.jwplayer.com and feeds.jwplayer.com
        # so the cdn-specific patterns above still win for those.
        # Also excludes content.jwplatform.com (the legacy cdn host).
        # v3.66.23 — self-hosted JWPlayer feed URL. Capture the URL
        # supplied to setup({playlist: "<url>"}) when it's NOT on a
        # JWPlayer cdn host. The negative lookahead excludes
        # cdn.jwplayer.com / feeds.jwplayer.com / content.jwplatform.com
        # so the cdn-specific patterns above still win for those.
        #
        # We deliberately do NOT match `file:` URLs — those are stream
        # URLs (mp4/m3u8/mpd) that extract_player_configs already
        # surfaces as terminal jwplayer_config candidates with full
        # quality metadata. Resolving them as feeds would just fetch
        # the stream URL expecting JSON and bail.
        #
        # We require the URL to look like a feed: either ends in
        # `.json` / `.jsonp` (optionally followed by ?query or #frag),
        # or contains a feed-like path segment (/feed, /playlist,
        # /config — singular or plural). The URL terminator is the
        # closing quote, not $/end-of-string, because the surrounding
        # ["']…["'] capture means we're matching inside a string.
        ("config_url_attr",
         r"""playlist\s*[:=]\s*["']"""
         r"""(https?://(?!(?:[A-Za-z0-9-]+\.)?"""
         r"""(?:cdn\.jwplayer\.com|feeds\.jwplayer\.com|"""
         r"""content\.jwplatform\.com)/)"""
         r"""[^"']*?"""
         r"""(?:\.jsonp?(?=[?#"']|$)"""
         r"""|/(?:feed|playlist|config)s?(?=[/?#"']|$))"""
         r"""[^"']*)["']"""),
    ],
}


def _extract_provider_ids(provider: str, source_text: str) -> dict:
    """Run all of `provider`'s ID-extraction regexes against
    `source_text` (which can be the URL itself OR a larger document
    blob — the patterns are anchored enough either way) and return
    the captured IDs. Patterns are tried independently; later
    patterns can supply IDs missing from earlier ones."""
    out: Dict[str, str] = {}
    patterns = _PROVIDER_ID_PATTERNS.get(provider, [])
    for key, pat in patterns:
        m = re.search(pat, source_text, re.I)
        if not m:
            continue
        # `_url` and `_attr` suffixes are alternate sources for the
        # same canonical id — normalize them. (e.g. video_id_url
        # and video_id_embed both populate `video_id`.)
        canonical = re.sub(r"_(url|attr|embed|short|player|v)$", "", key)
        if canonical not in out:
            # If multiple groups, the LAST group is the id (after
            # any qualifier).
            captured = (m.group(m.lastindex)
                        if m.lastindex else m.group(0))
            # Pre-fix: an empty-string capture (regex with optional
            # group that didn't actually match content) was still
            # written into the result, producing {"video_id": ""}
            # downstream. Drop empty/whitespace-only captures so
            # callers' `if not ids` checks behave intuitively.
            if captured and captured.strip():
                out[canonical] = captured
    return out


def extract_provider_embeds(html: str, *,
                            base_url: str = "") -> List[dict]:
    """Find every provider embed in the document. Each returned dict
    has at minimum {provider, source_type, ids, url, found_in}.
    The URL is whatever pointed us at the provider in the first place
    (an iframe src, a script src, an inline JSON URL). Multiple hits
    for the same provider are deduped by ID set."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    findings: List[dict] = []

    # Stage 1: scan every URL-bearing attribute on every element.
    url_attrs = ("src", "href", "data-src", "data-href", "data-url",
                 "data-embed-url", "data-iframe", "data-video",
                 "data-config-url")
    # Provider library / loader scripts that match a provider's host
    # but aren't per-video embeds. These produce useless candidates
    # (no id, no actionable URL for resolution) and crowd out the
    # real embed in the candidate list. Match by URL substring;
    # conservative list of known loaders only.
    _provider_loader_patterns = (
        "/assets/external/E-v1.js",          # Wistia player loader
        "/assets/external/iframe-api.js",    # Wistia legacy loader
        "/api/iframe-api/",                  # YouTube iframe-api wrapper
        "/iframe_api",                       # YouTube iframe API
        "/player/froogaloop",                # Vimeo cross-frame messaging
        "/api/player.js",                    # Vimeo player JS (no id)
        "/jwplayer.js",                      # JWPlayer library
    )
    for el in soup.find_all(True):
        for attr in url_attrs:
            v = el.get(attr)
            if not v or not isinstance(v, str):
                continue
            resolved = decode_url(v, base_url=base_url)
            cls = classify_url(resolved)
            t = cls.get("type", "")
            # provider types end with _embed / _stream / _session
            if not (t.endswith("_embed") or t.endswith("_stream")
                    or t.endswith("_session")):
                continue
            # Skip known provider library/loader scripts: they match a
            # provider's host but aren't per-video embeds, so they
            # pollute the candidate list without giving the resolver
            # anything actionable. Stage 3 will still find the real
            # embed via its element-attrs scan if present.
            if any(pat in resolved for pat in _provider_loader_patterns):
                continue
            prov = (t.replace("_stream", "").replace("_embed", "")
                    .replace("_session", ""))
            # Provider name aliases used in the SOURCE_TYPES → fix
            # back to the PROVIDERS table key.
            if prov == "panopto":
                prov_key = "panopto"
            elif prov == "cloudflare":
                prov_key = "cloudflare_stream"
            elif prov == "bunny":
                prov_key = "bunny_stream"
            elif prov == "mux":
                prov_key = "mux"
            else:
                prov_key = prov
            ids = _extract_provider_ids(prov_key, resolved)
            findings.append({
                "provider": prov_key,
                "source_type": t,
                "ids": ids,
                "url": resolved,
                "found_in": f"<{el.name} {attr}>",
            })

    # Stage 2: scan every <script> body for provider markers (used
    # when the provider config is injected via JS, not iframe).
    for script in soup.find_all("script"):
        body = (script.string or script.text or "")[:200_000]
        if not body:
            continue
        for prov_name, _hosts, markers in PROVIDERS:
            if not any(m in body for m in markers):
                continue
            ids = _extract_provider_ids(prov_name, body)
            # Already recorded with the same id set? skip.
            sig = (prov_name, tuple(sorted(ids.items())))
            if any(
                (f["provider"], tuple(sorted(f["ids"].items()))) == sig
                for f in findings
            ):
                continue
            # Map provider name to a source_type using the same
            # conventions classify_url uses.
            if prov_name in ("mux", "cloudflare_stream", "bunny_stream"):
                src_type = f"{prov_name}" if prov_name.endswith(
                    "_stream") else f"{prov_name}_stream"
            elif prov_name == "panopto":
                src_type = "panopto_session"
            else:
                src_type = f"{prov_name}_embed"
            entry = {
                "provider": prov_name,
                "source_type": src_type,
                "ids": ids,
                "url": None,
                "found_in": "<script> body",
            }
            if not ids:
                # Marker matched but no IDs could be extracted. Still
                # worth surfacing so the UI can show "Vimeo embed
                # detected (ID unknown)" — the operator can investigate.
                # Pre-fix, this branch dropped the embed entirely,
                # hiding the provider from the report.
                entry["warning"] = (
                    f"{prov_name} markers present but no ID could be "
                    "extracted from the script body")
            findings.append(entry)

    # Stage 3: scan element attributes (class, data-*, id) for
    # provider markers. Catches Brightcove's <video-js
    # data-account="..."> and Wistia's <div class="wistia_async_xyz">
    # — both of which carry no URL of their own.
    for el in soup.find_all(True):
        attrs_blob_parts = [el.name or ""]
        for k, v in (el.attrs or {}).items():
            if isinstance(v, list):
                v = " ".join(v)
            attrs_blob_parts.append(f"{k}={v}")
        attrs_blob = " ".join(attrs_blob_parts)[:4000]
        for prov_name, _hosts, markers in PROVIDERS:
            if not any(m in attrs_blob for m in markers):
                continue
            ids = _extract_provider_ids(prov_name, attrs_blob)
            if not ids:
                continue
            sig = (prov_name, tuple(sorted(ids.items())))
            if any(
                (f["provider"], tuple(sorted(f["ids"].items()))) == sig
                for f in findings
            ):
                continue
            if prov_name in ("mux", "cloudflare_stream", "bunny_stream"):
                src_type = (f"{prov_name}"
                            if prov_name.endswith("_stream")
                            else f"{prov_name}_stream")
            elif prov_name == "panopto":
                src_type = "panopto_session"
            else:
                src_type = f"{prov_name}_embed"
            findings.append({
                "provider": prov_name,
                "source_type": src_type,
                "ids": ids,
                "url": None,
                "found_in": f"<{el.name} attrs>",
            })

    # Dedup by (provider, id-set) keeping the first hit (usually the
    # cleanest one — iframe attribute beats heuristic script scrape).
    seen, out = set(), []
    for f in findings:
        sig = (f["provider"], tuple(sorted(f.get("ids", {}).items())))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(f)
    return out


_JWPLAYER_SETUP_RE = re.compile(
    r"""jwplayer\s*\([^)]*\)\.setup\s*\(\s*({[^;]+?})\s*\)""",
    re.S,
)


_VIDEOJS_SOURCES_RE = re.compile(
    r"""sources['"]?\s*:\s*(\[[^\]]+\])""",
    re.S,
)


_REACTPLAYER_URL_RE = re.compile(
    r"""ReactPlayer\b[^>]*?\burl\s*=\s*"""      # up to the url prop
    r"""(?:"""
    r"""(?P<dq>"[^"]*")"""                      # url="..."
    r"""|(?P<sq>'[^']*')"""                     # url='...'
    r"""|\{\s*(?P<expr>"[^"]*"|'[^']*'|\[[^\]]*\])\s*\}"""  # url={...}
    r""")""",
    re.S,
)


def _try_parse_loose_json(blob: str) -> Optional[Any]:
    """JSON parsers reject single quotes and unquoted keys, which
    JWPlayer config blobs use. Cheap workaround: try strict first,
    then a guarded single-quote → double-quote swap.

    The swap is intentionally conservative — when we can't be sure
    the input is safe to rewrite, we return None rather than mutate
    a value (a corrupted URL parsed as valid JSON is worse than no
    parse at all):
      • Escaped apostrophes (`\\'`) anywhere → bail (config has
        embedded text with apostrophes).
      • Double-quoted strings containing apostrophes (e.g. `"it's"`)
        → bail (single-quote rewriting would split those values).
      • A safety-net check at the end: if rewriting produced a result
        that re-parsing as JSON STILL can't parse, return None even
        if the regex completed — don't return half-rewritten garbage.
    """
    blob = blob.strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    # Bail early on inputs that the lossy swap would corrupt.
    if "\\'" in blob:
        return None
    # Double-quoted strings containing apostrophes — the single-quote
    # rewriter would treat the apostrophe as a string boundary and
    # split the value.
    if re.search(r'"[^"]*\'[^"]*"', blob):
        return None
    swapped = re.sub(
        r"'([^'\\]*)'",
        lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
        blob,
    )
    # Unquoted keys: key: → "key":
    # The lookbehind anchors us to `{` or `,`, which means we won't
    # mis-fire inside a string VALUE (those start with `"`, `'`, `[`,
    # or a number — never `{` or `,` at the start of the value text).
    # However, if the swap above left us with a string containing
    # `,key:`, the lookbehind could still mis-fire. Skip the unquoted-
    # key step entirely if the swapped result already parses (some
    # configs use single-quoted keys exclusively).
    try:
        return json.loads(swapped)
    except json.JSONDecodeError:
        pass
    swapped = re.sub(r"(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:",
                     r'"\1":', swapped)
    try:
        return json.loads(swapped)
    except json.JSONDecodeError:
        return None


def _parse_srcset(srcset: str) -> List[tuple]:
    """Parse an HTML `srcset` value into [(url, descriptor), ...].

    srcset is a comma-separated list of `<url> <descriptor>` candidates,
    where the descriptor is a width (`720w`), a pixel density (`2x`), or
    absent. URLs themselves may contain commas (rare but legal when
    percent-encoded; bare commas in URLs are not valid in srcset), so we
    split on commas then peel the trailing whitespace-delimited
    descriptor off each candidate. Returns the descriptor verbatim (e.g.
    "1080w") so the caller can fold it into quality detection.
    """
    out: List[tuple] = []
    for cand in (srcset or "").split(","):
        cand = cand.strip()
        if not cand:
            continue
        parts = cand.split()
        url = parts[0]
        descriptor = parts[1] if len(parts) > 1 else ""
        if url:
            out.append((url, descriptor))
    return out


def extract_player_configs(html: str, *,
                           base_url: str = "") -> List[dict]:
    """Find player-library configs in inline script bodies and
    return one entry per (library, source URL). Output:

        [
            {
                "library": "videojs|jwplayer|plyr|...",
                "url": "...",
                "source_type": "videojs_source|jwplayer_config|...",
                "quality": {"label":..., "rank":...} | None,
                "codec": str | None,
                "found_in": "<script>" | "<video data-setup>",
            },
            ...
        ]
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []

    # ── Video.js: <video class="video-js" data-setup="{...}"> ──────
    for video in soup.find_all("video"):
        classes = " ".join(video.get("class") or []).lower()
        if "video-js" not in classes and "videojs" not in classes:
            # Not Video.js, but it's still an HTML5 video — emit a
            # generic html5 entry for the tag's own `src` attr (a bare
            # <video src="..."> with no <source> child) and for each
            # <source> child (src, data-src, AND srcset candidates).
            vsrc = video.get("src") or video.get("data-src")
            if vsrc:
                resolved = decode_url(vsrc, base_url=base_url)
                cls = classify_url(resolved)
                out.append({
                    "library": "html5_video",
                    "url": resolved,
                    "source_type": cls.get("type") or "direct_file",
                    "quality": detect_resolution_from_text(vsrc),
                    "codec": None,
                    "found_in": "<video src>",
                })
            for s in video.find_all("source"):
                src = s.get("src") or s.get("data-src")
                if src:
                    resolved = decode_url(src, base_url=base_url)
                    cls = classify_url(resolved, mime=s.get("type") or "")
                    out.append({
                        "library": "html5_video",
                        "url": resolved,
                        "source_type": cls.get("type") or "direct_file",
                        "quality": detect_resolution_from_text(
                            " ".join([src, s.get("label") or "",
                                       s.get("data-quality") or ""])),
                        "codec": parse_codec(s.get("type") or ""),
                        "found_in": "<video><source>",
                    })
                # srcset: responsive/adaptive candidate list. Each
                # candidate is its own downloadable URL; the width/density
                # descriptor feeds quality detection.
                for cand_url, descriptor in _parse_srcset(s.get("srcset") or ""):
                    resolved = decode_url(cand_url, base_url=base_url)
                    cls = classify_url(resolved, mime=s.get("type") or "")
                    quality = detect_resolution_from_text(
                        " ".join([cand_url, descriptor, s.get("type") or ""]))
                    # srcset descriptors are widths (`1920w`) or pixel
                    # densities (`2x`), which detect_resolution_from_text
                    # doesn't parse. If nothing else matched, surface the
                    # descriptor as a width-based hint (rank by width) so
                    # candidates still sort sensibly — without inventing a
                    # height we don't actually know.
                    if quality is None and descriptor.endswith("w"):
                        try:
                            wpx = int(descriptor[:-1])
                            quality = {"label": f"{wpx}w", "rank": wpx,
                                       "width": wpx, "ambiguous": True}
                        except ValueError:
                            pass
                    out.append({
                        "library": "html5_video",
                        "url": resolved,
                        "source_type": cls.get("type") or "direct_file",
                        "quality": quality,
                        "codec": parse_codec(s.get("type") or ""),
                        "found_in": "<video><source srcset>",
                    })
            continue
        data_setup = video.get("data-setup") or ""
        videojs_setup_emitted = False
        if data_setup:
            cfg = _try_parse_loose_json(data_setup)
            if isinstance(cfg, dict):
                for src in (cfg.get("sources") or []):
                    if not isinstance(src, dict):
                        continue
                    url = src.get("src") or src.get("file")
                    if not url:
                        continue
                    resolved = decode_url(url, base_url=base_url)
                    out.append({
                        "library": "videojs",
                        "url": resolved,
                        "source_type": "videojs_source",
                        "quality": detect_resolution_from_text(
                            " ".join([url, src.get("label") or "",
                                       src.get("type") or ""])),
                        "codec": parse_codec(src.get("type") or ""),
                        "found_in": "<video data-setup>",
                    })
                    videojs_setup_emitted = True
        # Inline <source> children — but only if data-setup didn't
        # already provide the sources. Pre-fix, both branches ran
        # together, double-emitting the same URLs (dedup later
        # absorbed them, but the redundant work was avoidable).
        if not videojs_setup_emitted:
            for s in video.find_all("source"):
                src = s.get("src") or s.get("data-src")
                if not src:
                    continue
                resolved = decode_url(src, base_url=base_url)
                out.append({
                    "library": "videojs",
                    "url": resolved,
                    "source_type": "videojs_source",
                    "quality": detect_resolution_from_text(
                        " ".join([src, s.get("label") or "",
                                   s.get("type") or ""])),
                    "codec": parse_codec(s.get("type") or ""),
                    "found_in": "<video class=video-js><source>",
                })

    # ── JWPlayer: jwplayer("x").setup({...}) in <script>. ──────────
    for script in soup.find_all("script"):
        # Cap script bodies — the state-blob walker already caps at
        # 200_000; player-config scanning has no such cap pre-fix.
        # 500_000 chars (~500 KB) is plenty for legitimate configs.
        body = (script.string or script.text or "")[:500_000]
        if not body:
            continue
        for m in _JWPLAYER_SETUP_RE.finditer(body):
            cfg = _try_parse_loose_json(m.group(1))
            if not isinstance(cfg, dict):
                continue
            sources = cfg.get("sources") or cfg.get("playlist") or []
            if isinstance(sources, dict):
                sources = [sources]
            for s in sources:
                if not isinstance(s, dict):
                    continue
                # playlist items can themselves have a `sources` list
                nested = s.get("sources")
                if isinstance(nested, list):
                    items = nested
                else:
                    items = [s]
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    url = it.get("file") or it.get("src")
                    if not url:
                        continue
                    resolved = decode_url(url, base_url=base_url)
                    out.append({
                        "library": "jwplayer",
                        "url": resolved,
                        "source_type": "jwplayer_config",
                        "quality": detect_resolution_from_text(
                            " ".join([url, it.get("label") or "",
                                       it.get("type") or "",
                                       str(it.get("height") or "")])),
                        "codec": parse_codec(it.get("type") or ""),
                        "found_in": "<script> jwplayer().setup",
                    })

    # ── Plyr provider tags: <div data-plyr-provider="youtube" data-plyr-embed-id="...">
    for el in soup.find_all(attrs={"data-plyr-provider": True}):
        provider = (el.get("data-plyr-provider") or "").lower()
        eid = el.get("data-plyr-embed-id") or ""
        if not eid:
            continue
        if provider == "youtube":
            url = f"https://www.youtube.com/embed/{eid}"
            stype = "youtube_embed"
        elif provider == "vimeo":
            url = f"https://player.vimeo.com/video/{eid}"
            stype = "vimeo_embed"
        else:
            url = eid
            stype = "plyr_source"
        out.append({
            "library": "plyr",
            "url": url,
            "source_type": stype,
            "quality": None,
            "codec": None,
            "found_in": "<* data-plyr-provider>",
        })

    # ── Generic inline `sources: [...]` array — catches Shaka,
    # Bitmovin, THEOplayer, generic React players when the config is
    # in window assignment rather than a tagged state blob.
    for script in soup.find_all("script"):
        body = script.string or script.text or ""
        if not body or any(b in body for b in ("jwplayer", "video-js")):
            continue
        for m in _VIDEOJS_SOURCES_RE.finditer(body[:200_000]):
            arr = _try_parse_loose_json(m.group(1))
            if not isinstance(arr, list):
                continue
            for it in arr:
                if not isinstance(it, dict):
                    continue
                url = it.get("src") or it.get("file")
                if not url:
                    continue
                resolved = decode_url(url, base_url=base_url)
                # Pick a library name based on which marker is closest;
                # default to "generic_player".
                lib_window = body[
                    max(0, m.start() - 200):m.start() + 200]
                lib = "generic_player"
                for lname, terms in PLAYER_LIBRARIES:
                    if any(t in lib_window for t in terms):
                        lib = lname
                        break
                src_type_map = {
                    "shaka": "shaka_source",
                    "bitmovin": "bitmovin_source",
                    "theoplayer": "theoplayer_source",
                    "react_player": "react_player_source",
                    "hlsjs": "hls_manifest",
                    "dashjs": "dash_manifest",
                }
                stype = src_type_map.get(lib)
                if not stype:
                    cls = classify_url(resolved)
                    stype = cls.get("type") or "unknown"
                out.append({
                    "library": lib,
                    "url": resolved,
                    "source_type": stype,
                    "quality": detect_resolution_from_text(
                        " ".join([url, it.get("label") or "",
                                   it.get("type") or ""])),
                    "codec": parse_codec(it.get("type") or ""),
                    "found_in": "<script> sources[]",
                })

    # ── React Player: <ReactPlayer url="..." /> ───────────────────
    # Closes the gap the module comment promised but never implemented:
    # ReactPlayer uses a `url` prop, not a sources[] array. Scan the raw
    # HTML (the prop can be in rendered markup OR an inline script's JSX
    # / hydration string), capture the prop value, and emit one entry
    # per URL. Array form yields multiple; {src} objects are unwrapped.
    def _reactplayer_urls(prop_value: str) -> List[str]:
        v = prop_value.strip()
        urls: List[str] = []
        # array form: ["a.mp4", {src:"b.mp4"}, {src:"c.mp4", type:...}]
        if v.startswith("["):
            arr = _try_parse_loose_json(v)
            if isinstance(arr, list):
                for it in arr:
                    if isinstance(it, str):
                        urls.append(it)
                    elif isinstance(it, dict):
                        u = it.get("src") or it.get("file")
                        if isinstance(u, str):
                            urls.append(u)
            return urls
        # plain quoted string: strip one matching quote pair
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1]
        if v:
            urls.append(v)
        return urls

    if "ReactPlayer" in html:
        for m in _REACTPLAYER_URL_RE.finditer(html):
            raw = m.group("dq") or m.group("sq") or m.group("expr") or ""
            for u in _reactplayer_urls(raw):
                if not u or u.startswith(("data:", "blob:")):
                    continue
                resolved = decode_url(u, base_url=base_url)
                cls = classify_url(resolved)
                out.append({
                    "library": "react_player",
                    "url": resolved,
                    "source_type": "react_player_source",
                    "quality": detect_resolution_from_text(u),
                    "codec": None,
                    "found_in": "<ReactPlayer url=>",
                })

    # Dedup by URL keeping the first occurrence.
    seen, dedup = set(), []
    for r in out:
        u = r.get("url") or ""
        if not u or u in seen:
            continue
        seen.add(u)
        dedup.append(r)
    return dedup


JSONLD_MEDIA_TYPES = (
    "VideoObject", "AudioObject", "MediaObject",
    "ImageObject", "DigitalDocument", "SoftwareApplication",
    # v3.66.33: real member-site captures (AdultTime, dfxtra, Vixen)
    # expose video metadata under schema.org Movie / TVEpisode / Clip
    # rather than a bare VideoObject. These types usually carry only
    # metadata (name/description/thumbnail/duration) and NOT a playable
    # contentUrl — the playable source lives in the network log / player
    # config for blob:-based players. We still recognize them so the
    # metadata becomes a labelled candidate; _emit does not fabricate a
    # content_url when none is present.
    "Movie", "TVEpisode", "Clip", "Episode",
)


def extract_jsonld_media(html: str, *,
                         base_url: str = "") -> List[dict]:
    """Find every <script type="application/ld+json"> block, parse
    each, walk @graph if present, and emit one dict per VideoObject /
    AudioObject / MediaObject / ImageObject / DigitalDocument /
    SoftwareApplication found.

    Each entry preserves the structured fields a downstream consumer
    cares about (contentUrl, embedUrl, thumbnailUrl, uploadDate,
    encodingFormat, duration, etc.) — extract_state_blob_urls only
    keeps the URLs.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []

    # Schema.org properties that commonly nest a media object inside
    # another entity. Walking these lets us find a Movie's trailer
    # (Movie.trailer → VideoObject), a Recipe's video (Recipe.video →
    # VideoObject), a BlogPosting's associated media, etc.
    NESTED_MEDIA_PROPERTIES = (
        "hasPart", "associatedMedia", "encoding", "encodings",
        "trailer", "video", "audio", "image",
        "subjectOf", "workExample", "exampleOfWork",
        "mainEntity", "mainEntityOfPage",
        "primaryImageOfPage", "isPartOf",
    )

    def _emit(item: dict, source: str) -> None:
        if not isinstance(item, dict):
            return
        jtype = item.get("@type") or ""
        types = [jtype] if isinstance(jtype, str) else list(jtype)
        if not any(t in JSONLD_MEDIA_TYPES for t in types):
            return
        entry = {
            "type": types[0] if types else None,
            "name": item.get("name"),
            "description": item.get("description"),
            "content_url": (decode_url(item["contentUrl"],
                                       base_url=base_url)
                            if isinstance(item.get("contentUrl"), str)
                            else None),
            "embed_url": (decode_url(item["embedUrl"],
                                      base_url=base_url)
                          if isinstance(item.get("embedUrl"), str)
                          else None),
            "thumbnail_url": item.get("thumbnailUrl"),
            "upload_date": item.get("uploadDate"),
            "duration": item.get("duration"),
            "encoding_format": item.get("encodingFormat"),
            "width": item.get("width"),
            "height": item.get("height"),
            "bitrate": item.get("bitrate"),
            "source": source,
        }
        out.append(entry)

    def _walk(obj, source: str, seen_ids: set, _depth: int = 0) -> None:
        """Recurse through a JSON-LD value collecting media objects.
        Visits @graph children, nested media properties (hasPart,
        associatedMedia, trailer, video, audio, …), and the obj itself
        if it has a media @type. Cycle-safe via id()-set; depth-bounded
        (the id()-set guards cycles, not a deep acyclic chain)."""
        if _depth > _MAX_JSON_DEPTH:
            return          # deep-JSON-LD guard (see test_deep_detect_json_recursion)
        if isinstance(obj, list):
            for item in obj:
                _walk(item, source, seen_ids, _depth + 1)
            return
        if not isinstance(obj, dict):
            return
        # Cycle guard. JSON-LD @id self-references can produce loops
        # when the input is normalized from RDF; protect ourselves.
        oid = id(obj)
        if oid in seen_ids:
            return
        seen_ids.add(oid)
        # Emit if this object itself is media-typed.
        _emit(obj, source)
        # @graph: list of any types, each treated as a fresh root.
        graph = obj.get("@graph")
        if isinstance(graph, list):
            for g in graph:
                _walk(g, source + " @graph", seen_ids, _depth + 1)
        # Nested media properties.
        for prop in NESTED_MEDIA_PROPERTIES:
            val = obj.get(prop)
            if val is None:
                continue
            _walk(val, f"{source} {prop}", seen_ids, _depth + 1)

    for script in soup.find_all("script",
                                type="application/ld+json"):
        body = (script.string or script.text or "").strip()
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        seen_ids: set = set()
        _walk(data, "application/ld+json", seen_ids)
    return out
