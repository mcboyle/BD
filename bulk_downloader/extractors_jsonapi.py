"""v3.43.68: HereSphere / DeoVR JSON API extractor.

# Why this module exists

Two open JSON protocols — HereSphere and DeoVR — are used by adult-VR
player apps as a standardized way to read scene libraries from any
site that implements them. The protocols are documented and the
sites that support them VOLUNTARILY expose the endpoints; this is
not reverse engineering.

**The big win:** when a site supports either protocol, we skip
HTML scraping entirely. The JSON response carries the full title,
duration, thumbnail, AND a structured `media[].sources[]` (or
`encodings[].videoSources[]`) array with every quality tier and a
direct stream URL per tier. We pick the highest-quality entry per
the user's preference cascade and direct-download — no Playwright,
no Cloudflare drama, no flashvars regex.

# Sites known to implement at least one protocol

- NaughtyAmerica (confirmed: api.naughtyapi.com/heresphere)
- VR Bangers, VRHush, Czech VR family (various VR studios)
- KinkVR, WankzVR, StasyQ VR
- Most dedicated VR sites — protocol adoption is high among them

Non-VR sites (Vixen, NewSensations, EvilAngel, etc.) almost never
implement these. The extractor is OFF-by-default per site and gets
turned ON by a successful probe at site-add time (the wizard runs
`probe_site()` and surfaces the result).

# Endpoint shapes

Two cases:

  1. SAME-ORIGIN: `https://members.<site>.com/heresphere` returns
     either a library list or a per-scene record.

  2. SEPARATE-ORIGIN: `https://api.<site>api.com/heresphere/<id>`
     — NaughtyAmerica uses this; their main site doesn't have a
     /heresphere route, the API is on a separate host.

The user can configure either, OR let `probe_site()` discover by
trying both in order.

# Library vs per-scene

`/heresphere` returns a LIBRARY (paginated list of scenes). To get
ONE scene we need a scene ID. Two ways:

  A. The site's per-scene route includes the ID and the JSON has
     a stable URL: `/heresphere/<scene_id>` returns the per-scene
     record. We need to extract the scene_id from the user's URL.

  B. The library list has entries with `link` or `id` fields. We
     walk the library, find the entry matching our URL by either
     direct match or fuzzy title match.

We support (A) — extract scene_id from URL via per-site regex (or
the configured `heresphere_id_path`), use it to construct the
per-scene URL. (B) is a future fan-out feature; not needed for the
single-URL case which is BD's bread and butter.

# Response shape normalization

The two protocols use different field names for the same data:

| Concept           | HereSphere              | DeoVR                       |
|-------------------|-------------------------|-----------------------------|
| Title             | `title`                 | `title`                     |
| Description       | `description`           | `description`               |
| Duration (sec)    | `duration`              | `duration` / `videoLength`  |
| Thumbnail         | `thumbnailImage`        | `thumbnailUrl`              |
| Tier list root    | `media[].sources[]`     | `encodings[].videoSources[]`|
| Source URL        | `url`                   | `url`                       |
| Source resolution | `resolution` / `height` | `resolution`                |

We normalize both into the same dataclass and the caller doesn't
need to know which protocol responded.

# Auth

The session cookies from a normal browser login are what these
endpoints expect. No special header magic — `Accept: application/json`
helps but isn't strictly required.

# Fail-open

Every error path returns a result with `ok=False`. Probe failures
are explicitly fine: most sites don't implement these protocols, and
that's expected.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# Default endpoint paths to probe in order. Most sites either use both
# or just one; we always try /heresphere first since it carries richer
# metadata, then fall back to /deovr.
DEFAULT_PROBE_PATHS = ("/heresphere", "/deovr")


# ─── Probe — does this site implement either protocol? ──────────────


@dataclass
class ProbeOutcome:
    """Result of `probe_site()`. Used at site-add time."""
    ok: bool                                     # Either endpoint responded
    heresphere_url: str = ""                     # Full URL if /heresphere worked
    deovr_url: str = ""                          # Full URL if /deovr worked
    api_host_override: str = ""                  # Set when API is on separate host
    sample_title: str = ""                       # First scene title (sanity)
    probed_urls: list = field(default_factory=list)  # [(url, status), ...]
    error: str = ""


def probe_site(
    site_root: str,
    *,
    cookies: Optional[dict] = None,
    user_agent: str = "",
    timeout_s: float = 8.0,
    extra_hosts: Optional[list] = None,
) -> ProbeOutcome:
    """Try /heresphere and /deovr on `site_root`. Returns ProbeOutcome.

    `site_root` is the scheme+host of the user's site (e.g.
    `https://members.naughtyamerica.com`). We also try `extra_hosts`
    if provided — useful for NaughtyAmerica's split API host:

        probe_site("https://members.naughtyamerica.com",
                   extra_hosts=["https://api.naughtyapi.com"])

    Both `/heresphere` and `/deovr` are checked on every host in
    order: site_root first, then each extra host. First 200-OK
    JSON response wins.

    Never raises. On any error returns ProbeOutcome(ok=False).
    """
    if not site_root:
        return ProbeOutcome(ok=False, error="empty_site_root")
    try:
        import httpx
    except ImportError:
        return ProbeOutcome(ok=False, error="httpx_missing")

    hosts = [site_root.rstrip("/")]
    for h in (extra_hosts or []):
        if h:
            h = h.rstrip("/")
            if h not in hosts:
                hosts.append(h)

    headers = {"Accept": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    cookies = cookies or {}

    outcome = ProbeOutcome(ok=False)
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True,
                          cookies=cookies, headers=headers) as client:
            for host in hosts:
                for path in DEFAULT_PROBE_PATHS:
                    full = host + path
                    try:
                        r = client.get(full)
                        status = r.status_code
                        body = r.text or ""
                    except Exception as e:
                        outcome.probed_urls.append(
                            (full, f"err:{type(e).__name__}"))
                        continue
                    outcome.probed_urls.append((full, status))
                    if status != 200 or not body:
                        continue
                    # Verify it's JSON and has at least ONE field that
                    # looks like our protocols (`title`, `library`,
                    # `scenes`, `media`, or `encodings`).
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if not _looks_like_protocol_response(data):
                        continue
                    # We have a winner
                    if "/heresphere" in path:
                        outcome.heresphere_url = full
                    else:
                        outcome.deovr_url = full
                    if host != hosts[0]:
                        outcome.api_host_override = host
                    outcome.sample_title = _first_title(data)
                    outcome.ok = True
                    # Don't return immediately — try the other path on
                    # the same host so we capture both URLs if both work.
    except Exception as e:
        outcome.error = f"{type(e).__name__}: {str(e)[:200]}"
    return outcome


def _looks_like_protocol_response(data) -> bool:
    """True if the parsed JSON looks like a HereSphere or DeoVR
    response (library list or per-scene record)."""
    if not isinstance(data, dict):
        return False
    # Per-scene response shapes
    if any(k in data for k in ("media", "encodings",
                                "videoSources")):
        return True
    # Library list shapes
    if any(k in data for k in ("library", "scenes")):
        return True
    # Some sites paginate as a list of dicts with `title` + `link`
    if isinstance(data.get("list"), list):
        return True
    return False


def _first_title(data) -> str:
    """Extract a sanity-check title from a probe response."""
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("title"), str):
        return data["title"][:120]
    # Library shapes
    for k in ("library", "scenes", "list"):
        v = data.get(k)
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, dict) and isinstance(first.get("title"), str):
                return first["title"][:120]
            if isinstance(first, dict) and isinstance(first.get("name"), str):
                return first["name"][:120]
    return ""


# ─── Per-scene extraction ───────────────────────────────────────────


@dataclass
class JsonApiSource:
    """One quality variant from a normalized scene record."""
    url: str
    height: int = 0      # Numeric height (the "tier") — 0 if unknown
    width: int = 0
    size_bytes: int = 0
    codec: str = ""      # "h264" / "h265" — DeoVR only
    is_hls: bool = False


@dataclass
class JsonApiResult:
    """End-to-end extraction outcome."""
    ok: bool
    source: Optional[JsonApiSource] = None
    title: str = ""
    description: str = ""
    duration_sec: int = 0
    thumbnail_url: str = ""
    date_released: str = ""           # YYYY-MM-DD if present
    performers: list = field(default_factory=list)
    studio: str = ""
    tags: list = field(default_factory=list)
    available_heights: list = field(default_factory=list)
    protocol: str = ""                # "heresphere" / "deovr"
    error: str = ""
    error_detail: str = ""


def _extract_scene_id(url: str, id_regex: str = "") -> str:
    """Pull a scene ID from `url`. If `id_regex` is provided it's used;
    otherwise fall back to common patterns.

    The default patterns try, in order:
      - last numeric segment in the path
      - URL parameter ?id=N / ?scene=N / ?v=N
      - last path segment if alphanumeric+dash
    """
    if not url:
        return ""
    # Custom user regex takes priority
    if id_regex:
        try:
            m = re.search(id_regex, url)
            if m:
                return m.group(m.lastindex or 0) if m.lastindex else m.group(0)
        except re.error:
            pass
    parsed = urlparse(url)
    # Try ?id= or ?scene= or ?v=
    if parsed.query:
        for key in ("id", "scene", "scene_id", "v", "video"):
            m = re.search(rf"(?:^|&){key}=([^&]+)", parsed.query)
            if m:
                return m.group(1)
    # Try last numeric segment
    segs = [s for s in parsed.path.split("/") if s]
    for seg in reversed(segs):
        if seg.isdigit():
            return seg
    # Try slugified last segment
    if segs:
        last = segs[-1]
        m = re.search(r"^([a-zA-Z0-9_-]+)", last)
        if m:
            return m.group(1)
    return ""


def fetch_scene(
    api_base: str,
    scene_url_or_id: str,
    *,
    protocol: str = "heresphere",
    cookies: Optional[dict] = None,
    user_agent: str = "",
    timeout_s: float = 15.0,
    id_regex: str = "",
) -> Optional[dict]:
    """Fetch a per-scene record from a HereSphere/DeoVR endpoint.

    `api_base` should be the full base URL the probe identified —
    e.g. `https://members.naughtyamerica.com/heresphere` or
    `https://api.naughtyapi.com/heresphere`.

    `scene_url_or_id` is either:
      - A scene ID like "12345" (used directly)
      - A full scene URL — we extract the ID via `_extract_scene_id`

    Returns the parsed JSON dict on success, None on any failure.
    Never raises.
    """
    if not api_base or not scene_url_or_id:
        return None
    try:
        import httpx
    except ImportError:
        return None

    # If it looks like a URL, extract the ID. If it's bare-digit-or-slug
    # use it directly.
    if "://" in scene_url_or_id:
        scene_id = _extract_scene_id(scene_url_or_id, id_regex)
    else:
        scene_id = scene_url_or_id.strip()
    if not scene_id:
        log.debug("jsonapi: couldn't extract scene_id from %r",
                  scene_url_or_id)
        return None

    full_url = api_base.rstrip("/") + "/" + scene_id
    headers = {"Accept": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True,
                          cookies=cookies or {}, headers=headers) as client:
            r = client.get(full_url)
            if r.status_code != 200:
                log.debug("jsonapi: %s returned %d", full_url, r.status_code)
                return None
            try:
                return json.loads(r.text or "")
            except json.JSONDecodeError as e:
                log.debug("jsonapi: %s body wasn't JSON: %s", full_url, e)
                return None
    except Exception as e:
        log.debug("jsonapi: fetch %s raised %s: %s",
                  full_url, type(e).__name__, e)
        return None


# ─── Normalization ──────────────────────────────────────────────────


def _normalize_source(entry: dict) -> Optional[JsonApiSource]:
    """One source entry from media[].sources[] or encodings[].videoSources[]."""
    if not isinstance(entry, dict):
        return None
    url = entry.get("url") or entry.get("URL") or ""
    if not isinstance(url, str) or not url:
        return None
    # Resolution can be `resolution`, `height`, or buried elsewhere
    height = 0
    for k in ("resolution", "height"):
        v = entry.get(k)
        if v is None:
            continue
        try:
            height = int(str(v).rstrip("p"))
            break
        except (ValueError, TypeError):
            continue
    width = 0
    if isinstance(entry.get("width"), int):
        width = entry["width"]
    size = 0
    if isinstance(entry.get("size"), int):
        size = entry["size"]
    return JsonApiSource(
        url=url, height=height, width=width, size_bytes=size,
        is_hls=url.endswith(".m3u8") or "/master.m3u8" in url,
    )


def normalize_scene(scene_json: dict, protocol: str = "") -> JsonApiResult:
    """Convert a raw scene response (HereSphere or DeoVR) into a
    JsonApiResult with all sources extracted.

    `protocol` is a hint; we auto-detect from the response shape if
    not provided.
    """
    if not isinstance(scene_json, dict):
        return JsonApiResult(ok=False, error="invalid_response_type")

    # Auto-detect protocol
    if not protocol:
        if "media" in scene_json or "thumbnailImage" in scene_json or \
           "isFavorite" in scene_json:
            protocol = "heresphere"
        elif "encodings" in scene_json or "thumbnailUrl" in scene_json or \
             "screenType" in scene_json:
            protocol = "deovr"
        else:
            protocol = "heresphere"  # default — its shape is more common

    # Title + description
    title = str(scene_json.get("title") or "")[:255]
    description = str(scene_json.get("description") or "")[:1024]

    # Duration
    duration = 0
    for k in ("duration", "videoLength"):
        v = scene_json.get(k)
        if v is None:
            continue
        try:
            duration = int(v)
            break
        except (ValueError, TypeError):
            continue

    # Thumbnail
    thumb = (scene_json.get("thumbnailImage")
             or scene_json.get("thumbnailUrl")
             or scene_json.get("thumb") or "")
    if not isinstance(thumb, str):
        thumb = ""

    # Date released
    date_released = ""
    for k in ("dateReleased", "released", "releaseDate"):
        v = scene_json.get(k)
        if isinstance(v, str) and v:
            date_released = v[:10]
            break

    # Performers + studio from tags (HereSphere encodes them in tags
    # with `Performer:` and `Studio:` prefixes)
    performers: list = []
    studio = ""
    tag_names: list = []
    tags_obj = scene_json.get("tags") or []
    if isinstance(tags_obj, list):
        for t in tags_obj:
            if isinstance(t, dict):
                name = t.get("name") or t.get("title") or ""
            elif isinstance(t, str):
                name = t
            else:
                continue
            if not isinstance(name, str) or not name:
                continue
            if name.lower().startswith("performer:"):
                performers.append(name[len("Performer:"):].strip())
            elif name.lower().startswith("studio:"):
                if not studio:
                    studio = name[len("Studio:"):].strip()
            else:
                tag_names.append(name)

    # Sources — walk both protocol shapes
    sources: list = []
    # HereSphere: media[].sources[]
    media = scene_json.get("media")
    if isinstance(media, list):
        for m in media:
            if isinstance(m, dict):
                src_list = m.get("sources")
                if isinstance(src_list, list):
                    for s in src_list:
                        norm = _normalize_source(s)
                        if norm is not None:
                            sources.append(norm)
    # DeoVR: encodings[].videoSources[]
    encs = scene_json.get("encodings")
    if isinstance(encs, list):
        for enc in encs:
            if isinstance(enc, dict):
                codec = enc.get("name") or ""
                vsl = enc.get("videoSources")
                if isinstance(vsl, list):
                    for s in vsl:
                        norm = _normalize_source(s)
                        if norm is not None:
                            norm.codec = codec
                            sources.append(norm)
    # Some sites flatten — top-level `videoSources`
    flat_vsl = scene_json.get("videoSources")
    if isinstance(flat_vsl, list):
        for s in flat_vsl:
            norm = _normalize_source(s)
            if norm is not None:
                sources.append(norm)

    if not sources:
        return JsonApiResult(
            ok=False, error="no_sources",
            error_detail=f"scene record had no usable sources (protocol={protocol})",
            title=title, description=description,
            duration_sec=duration, thumbnail_url=thumb,
            performers=performers, studio=studio, protocol=protocol,
        )

    # available_heights: dedup descending
    heights_set: set = set()
    for s in sources:
        if s.height > 0:
            heights_set.add(s.height)
    avail = sorted(heights_set, reverse=True)

    return JsonApiResult(
        ok=True,
        source=None,           # caller picks via pick_source
        title=title,
        description=description,
        duration_sec=duration,
        thumbnail_url=thumb,
        date_released=date_released,
        performers=performers,
        studio=studio,
        tags=tag_names,
        available_heights=avail,
        protocol=protocol,
        # The full source list is intentionally not in JsonApiResult —
        # pass it explicitly via pick_source(). Keeps the dataclass
        # focused.
    )


def pick_source(
    sources: list,
    quality_pref: Optional[list] = None,
    prefer_codec: str = "",
) -> Optional[JsonApiSource]:
    """Pick the best variant per `quality_pref` cascade.

    Uses the same proportional ±tolerance as the runner
    (max(50, target * 0.05)).

    `prefer_codec` is "h264" or "h265" — when set, prefer that codec
    if available at the matched tier; otherwise codec is a tiebreaker
    favoring h265 (smaller files, same quality). DeoVR-only signal;
    HereSphere doesn't expose codec.
    """
    if not sources:
        return None
    # Dedup on URL
    seen_urls: set = set()
    uniq: list = []
    for s in sources:
        if s.url in seen_urls:
            continue
        seen_urls.add(s.url)
        uniq.append(s)
    # Sort: highest height first, then codec preference, then MP4 > HLS
    def _key(s: JsonApiSource):
        codec_rank = 0
        if prefer_codec:
            codec_rank = 1 if s.codec.lower() == prefer_codec.lower() else 0
        else:
            # Default: prefer h265 for size at equal tier
            codec_rank = 1 if s.codec.lower() == "h265" else 0
        return (s.height, codec_rank, 1 if not s.is_hls else 0)
    uniq.sort(key=_key, reverse=True)
    # No pref or "best" → highest
    if not quality_pref or any(
        isinstance(p, str) and p.lower() in ("best", "highest", "max")
        for p in quality_pref
    ):
        return uniq[0]
    # Walk preference cascade
    for pref in quality_pref:
        try:
            target = int(str(pref).rstrip("p"))
        except (ValueError, TypeError):
            continue
        tolerance = max(50, int(target * 0.05))
        matches = [s for s in uniq
                   if abs(s.height - target) <= tolerance]
        if matches:
            return max(matches, key=_key)
    # No pref matched → highest
    return uniq[0]


def fetch_and_extract(
    api_base: str,
    scene_url_or_id: str,
    *,
    quality_pref: Optional[list] = None,
    protocol: str = "",
    cookies: Optional[dict] = None,
    user_agent: str = "",
    timeout_s: float = 15.0,
    id_regex: str = "",
    prefer_codec: str = "",
) -> JsonApiResult:
    """Full pipeline: fetch /heresphere/<id> → normalize → pick best.

    Returns JsonApiResult ready for dispatch. On any failure returns
    ok=False with an error code.
    """
    raw = fetch_scene(api_base, scene_url_or_id, protocol=protocol,
                      cookies=cookies, user_agent=user_agent,
                      timeout_s=timeout_s, id_regex=id_regex)
    if raw is None:
        return JsonApiResult(ok=False, error="fetch_failed",
                             error_detail=f"could not fetch {api_base}")
    norm = normalize_scene(raw, protocol=protocol)
    if not norm.ok:
        return norm
    # Re-extract sources to pass to pick_source. We have to walk the
    # raw again because JsonApiResult.source is a single picked entry.
    sources: list = []
    media = raw.get("media") or []
    if isinstance(media, list):
        for m in media:
            if isinstance(m, dict):
                for s in (m.get("sources") or []):
                    n = _normalize_source(s)
                    if n is not None:
                        sources.append(n)
    encs = raw.get("encodings") or []
    if isinstance(encs, list):
        for e in encs:
            if isinstance(e, dict):
                codec = e.get("name") or ""
                for s in (e.get("videoSources") or []):
                    n = _normalize_source(s)
                    if n is not None:
                        n.codec = codec
                        sources.append(n)
    flat = raw.get("videoSources") or []
    if isinstance(flat, list):
        for s in flat:
            n = _normalize_source(s)
            if n is not None:
                sources.append(n)
    picked = pick_source(sources, quality_pref=quality_pref,
                          prefer_codec=prefer_codec)
    if picked is None:
        return JsonApiResult(ok=False, error="no_source_picked",
                             error_detail="all variants were filtered out",
                             protocol=norm.protocol)
    norm.source = picked
    return norm


__all__ = [
    "DEFAULT_PROBE_PATHS",
    "ProbeOutcome",
    "JsonApiResult",
    "JsonApiSource",
    "probe_site",
    "fetch_scene",
    "fetch_and_extract",
    "normalize_scene",
    "pick_source",
]
