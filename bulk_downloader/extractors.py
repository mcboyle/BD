"""v3.43.63: Library-extractor fast path for sites with maintained PyPI
extractor libraries.

# Why this module exists

Many adult-content sites have actively-maintained PyPI extractor libraries
(EchterAlsFake's `phub`, `xnxx_api`, `xvideos_api`, `eporner_api`,
`youporn_api`, `hqporner_api`, `xhamster_api`, `spankbang_api`,
`missav_api`, `xfreehd_api`, `beeg_api`, `porntrex_api`, `porngo_api`).
Each one solves the "given a URL, get the highest-quality video file URL"
problem in a way that:

  - Tracks the upstream site's changes (semantic version bumps when sites
    rework their player)
  - Handles HLS variant selection automatically
  - Returns proper title/author/upload_date metadata
  - Is one `pip install` away

Before this module, BD's only options were:
  - Write speculative CSS/regex selectors and hope they don't break
  - Fall back to yt-dlp (which works but is slow and opaque)

Now there's a third option that's faster than yt-dlp, more reliable than
CSS scraping, and gives us actual metadata for tagging.

# Architecture

This module is a thin shim:

  1. `is_available(site_id)` -- True if the relevant library is installed
  2. `extract(site_id, url, quality_preference)` -> ExtractResult
     - dispatches to the right library by site_id
     - normalizes its return type into our ExtractResult dataclass
     - never raises -- all failures are returned as ExtractResult(ok=False)
  3. `download_via_library(url, output_path, ...)` -- convenience that
     wraps extract() + an HTTP download (delegating segmented HLS to
     the library when supported, otherwise to ffmpeg/streamlink)

# Fail-open

If a library isn't installed, is_available() returns False and the
worker falls back to its normal CSS/extension/yt-dlp paths. No errors,
no crashes -- the user just doesn't get the speed-up unless they
opt into installing the optional deps.

# Per-site opt-in

Each template can set `use_library_extractor: True` in config_defaults
(or via the UI per-site toggle). When True AND the library is installed
AND the URL matches the library's domain pattern, the worker takes the
fast path. When False (default), behavior is unchanged from v3.43.62.
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
# INV-003: this module is one of the documented fail-open sinks.
# `except Exception: pass` clauses in extractor dispatch are intentional —
# a library throwing must not crash the worker. See DANGER_MAP §INV-003.
from __future__ import annotations

import importlib
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─── Site registry ──────────────────────────────────────────────────
#
# Map: BD site_id  →  (library_module_name, url_host_regex,
#                      adapter_function_name)
#
# adapter_function_name is a callable in this module that knows how to
# drive that particular library's API surface (each library has a
# slightly different API). All adapters share the same signature:
#
#     def adapter(url: str, quality_preference: list[str],
#                 cookies: Optional[dict] = None) -> ExtractResult
#
# Adding a new library = add an entry here + write the adapter function.


@dataclass
class ExtractResult:
    """Normalized extraction outcome."""
    ok: bool
    # Set on success:
    file_url: str = ""             # Direct CDN URL OR m3u8 master playlist URL
    is_hls: bool = False           # True if file_url is an .m3u8 manifest
    title: str = ""                # Video title (used for filename)
    author: str = ""               # Channel / model / studio / uploader
    upload_date: str = ""          # YYYY-MM-DD if known
    duration_sec: int = 0          # Length in seconds (for progress)
    thumbnail_url: str = ""        # Cover-image URL (for metadata tagging)
    quality: str = ""              # The picked quality label (e.g. "1080p")
    available_qualities: list[str] = field(default_factory=list)
    # Set on failure:
    error: str = ""                # Short machine-readable code
    error_detail: str = ""         # Human-readable, may include traceback
    # Always set:
    extractor: str = ""            # Which library was used (e.g. "phub")
    via_fallback: bool = False     # True if we tried this AFTER another path failed


# Site config: (lib_module, host_regex, adapter_name)
#
# host_regex is matched against urlparse(url).netloc (lowercased, no port).
# Anchored implicitly with ^/$ via re.fullmatch in `_match_host()`.
_REGISTRY: dict[str, tuple[str, re.Pattern, str]] = {
    "pornhub": ("phub",
                re.compile(r"(?:www\.|m\.)?(?:[a-z]{2}\.)?pornhub(?:premium)?\.com", re.I),
                "_phub_adapter"),
    "redtube": ("eaf_base_api",  # NOTE: redtube extraction provided by eaf_base_api family
                re.compile(r"(?:www\.|m\.)?redtube\.com", re.I),
                "_redtube_adapter"),
    "youporn": ("youporn_api",
                re.compile(r"(?:www\.|m\.)?youporn(?:gay)?\.com", re.I),
                "_youporn_adapter"),
    "xnxx":    ("xnxx_api",
                re.compile(r"(?:www\.|m\.)?xnxx\.(?:com|es|tv)", re.I),
                "_xnxx_adapter"),
    "xvideos": ("xvideos_api",
                re.compile(r"(?:www\.|m\.)?xvideos\.(?:com|red)", re.I),
                "_xvideos_adapter"),
    "xhamster": ("xhamster_api",
                 re.compile(r"(?:www\.|m\.)?xhamster\d*\.(?:com|desi)", re.I),
                 "_xhamster_adapter"),
    "spankbang": ("spankbang_api",
                  re.compile(r"(?:www\.|m\.|la\.)?spankbang\.com", re.I),
                  "_spankbang_adapter"),
    "eporner": ("eporner_api",
                re.compile(r"(?:www\.|m\.)?eporner\.com", re.I),
                "_eporner_adapter"),
    "hqporner": ("hqporner_api",
                 re.compile(r"(?:www\.|m\.)?hqporner\.com", re.I),
                 "_hqporner_adapter"),
    "beeg":    ("beeg_api",
                re.compile(r"(?:www\.|m\.)?beeg\.com", re.I),
                "_beeg_adapter"),
    "porntrex": ("porntrex_api",
                 re.compile(r"(?:www\.|m\.)?porntrex\.com", re.I),
                 "_porntrex_adapter"),
    "missav":  ("missav_api",
                re.compile(r"(?:www\.|m\.)?missav\.(?:ws|com|to|net|ai)", re.I),
                "_missav_adapter"),
    "xfreehd": ("xfreehd_api",
                re.compile(r"(?:www\.|m\.)?xfreehd\.com", re.I),
                "_xfreehd_adapter"),
    "porngo":  ("porngo_api",
                re.compile(r"(?:www\.|m\.)?porngo\.com", re.I),
                "_porngo_adapter"),
}


# ─── Module-cache for import results ────────────────────────────────
#
# Importing dozens of optional libs is expensive (each one pulls in
# httpx + bs4 + lxml under the hood). Cache the result of every import
# attempt; "missing" caches as None so we don't retry on every URL.

_import_cache: dict[str, Any] = {}
_import_lock = threading.Lock()


def _try_import(module_name: str) -> Any:
    """Try to import an optional library. Returns the module on success,
    None on failure. Cached -- the import only runs once per process."""
    with _import_lock:
        if module_name in _import_cache:
            return _import_cache[module_name]
        try:
            mod = importlib.import_module(module_name)
            _import_cache[module_name] = mod
            log.info("extractors: loaded library %r", module_name)
            return mod
        except ImportError as e:
            _import_cache[module_name] = None
            # Log once at INFO level (not WARNING) -- absence is normal.
            log.info("extractors: library %r not installed (%s); "
                     "fast-path disabled for that site", module_name, e)
            return None
        except Exception as e:
            # Library imported but raised. Cache None to avoid retrying.
            _import_cache[module_name] = None
            log.warning("extractors: library %r import raised: %s", module_name, e)
            return None


def _reset_import_cache_for_tests() -> None:
    """Test hook -- forces re-import on next call."""
    with _import_lock:
        _import_cache.clear()


# ─── Public surface ─────────────────────────────────────────────────


def list_supported_sites() -> list[str]:
    """All BD site_ids that have a registered library adapter,
    regardless of whether the library is installed.

    UI uses this to decide whether to show the "use library extractor"
    toggle per site."""
    return sorted(_REGISTRY.keys())


def is_supported_url(url: str) -> Optional[str]:
    """Returns the site_id whose host pattern matches `url`, or None.

    Cheaper than running the full extract -- used for routing decisions."""
    if not isinstance(url, str) or not url:
        return None
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return None
    if not host:
        return None
    for site_id, (_libname, host_re, _adapter) in _REGISTRY.items():
        if host_re.fullmatch(host):
            return site_id
    return None


def is_available(site_id: str) -> bool:
    """True if the library backing `site_id` is installed AND imports
    cleanly. Cheap after first call (cached)."""
    entry = _REGISTRY.get(site_id)
    if not entry:
        return False
    libname, _host_re, _adapter = entry
    return _try_import(libname) is not None


def installed_libraries() -> dict[str, bool]:
    """Diagnostics map: site_id -> installed? for every site in the
    registry. UI surfaces this in a settings panel so the user can see
    which fast-paths are active."""
    return {sid: is_available(sid) for sid in _REGISTRY}


def extract(site_id: str, url: str,
            quality_preference: Optional[list[str]] = None,
            cookies: Optional[dict] = None) -> ExtractResult:
    """Run the library-backed extractor for `site_id` on `url`.

    `quality_preference` is a list of resolution strings preferred in
    order, e.g. ["1080", "720", "best"]. The adapter does its best to
    honor it; falls back to library default ("best") on no match.

    `cookies` is an optional Playwright-format cookies dict (
    {name: value} flat map -- domain is implicit from the URL). Most
    libraries don't need auth for public videos; passed through for
    the ones that benefit (xhamster, missav).

    Never raises. On any failure returns ExtractResult(ok=False, error=...).
    """
    if quality_preference is None:
        quality_preference = ["best"]
    entry = _REGISTRY.get(site_id)
    if not entry:
        return ExtractResult(ok=False, error="unsupported_site",
                             error_detail=f"no registry entry for {site_id!r}")
    libname, host_re, adapter_name = entry
    # URL must match the host pattern -- guard against caller mismatch.
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        host = ""
    if not host_re.fullmatch(host):
        return ExtractResult(
            ok=False, error="url_host_mismatch",
            error_detail=f"url host {host!r} does not match {site_id} pattern",
            extractor=libname,
        )
    if not is_available(site_id):
        return ExtractResult(
            ok=False, error="library_not_installed",
            error_detail=(f"pip install {libname} to enable the {site_id} "
                          f"fast-path"),
            extractor=libname,
        )
    adapter = globals().get(adapter_name)
    if not callable(adapter):
        return ExtractResult(
            ok=False, error="adapter_missing",
            error_detail=f"internal: adapter {adapter_name!r} not found",
            extractor=libname,
        )
    try:
        result = adapter(url, quality_preference, cookies)
        if not isinstance(result, ExtractResult):
            return ExtractResult(
                ok=False, error="adapter_bad_return",
                error_detail=f"{adapter_name} returned {type(result).__name__}",
                extractor=libname,
            )
        if not result.extractor:
            result.extractor = libname
        return result
    except Exception as e:
        # Hostile/changed pages can throw in unpredictable places inside
        # any of these libraries. Capture and downgrade to ok=False.
        import traceback
        return ExtractResult(
            ok=False, error="adapter_raised",
            error_detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            extractor=libname,
        )


# ─── Quality picking helper (shared across adapters) ────────────────


def _pick_quality(available: list, preference: list[str],
                  parse: Callable[[Any], Optional[int]] = None) -> Any:
    """Given a list of `available` quality items (in library-specific
    format) and a `preference` list of resolution strings, return the
    best match.

    `parse` is a function that converts one element of `available` into
    an integer pixel-height, or None if the element doesn't have one.
    Default extracts the leading int from `str(item)` (works for many
    libraries that return ['1080p', '720p', ...]).

    Strategy:
      1. For each preference in order:
         - 'best' or 'highest' -> max(available) by pixel-height
         - 'worst' or 'lowest' -> min(available)
         - int-like (e.g. '1080', '1080p', '4k') -> exact match,
           else within ±50px
      2. First preference that produces a hit wins.
      3. If nothing matches, fall back to max(available).
    """
    if not available:
        return None
    if parse is None:
        def parse(item):
            m = re.search(r"(\d{3,4})", str(item))
            return int(m.group(1)) if m else None
    # Pre-parse to (item, height) tuples, drop unparseable.
    parsed = []
    for it in available:
        h = parse(it)
        if h is not None:
            parsed.append((it, h))
    if not parsed:
        # Fallback: first item.
        return available[0]
    for pref in preference:
        pref_lower = pref.strip().lower()
        if pref_lower in ("best", "highest", "max"):
            return max(parsed, key=lambda t: t[1])[0]
        if pref_lower in ("worst", "lowest", "min"):
            return min(parsed, key=lambda t: t[1])[0]
        # Numeric: handle "4k"/"8k" aliases and trailing p
        if pref_lower.endswith("k"):
            try:
                kval = int(pref_lower[:-1])
                target = kval * 1000 if kval < 10 else kval * 100
                # 4k -> 2160, 8k -> 4320 approximate (round nearest)
                target = {4: 2160, 5: 2880, 6: 3160, 8: 4320}.get(kval, target)
            except ValueError:
                continue
        else:
            try:
                target = int(re.sub(r"[^\d]", "", pref_lower))
            except ValueError:
                continue
            if target <= 0:
                continue
        # Find exact or within tolerance
        TOLERANCE = 50
        within = [t for t in parsed if abs(t[1] - target) <= TOLERANCE]
        if within:
            return max(within, key=lambda t: t[1])[0]
    # No preference matched -> max available.
    return max(parsed, key=lambda t: t[1])[0]


# ─── Adapters (one per library) ─────────────────────────────────────
#
# Each adapter is defensive: the library APIs vary across versions, so
# we use hasattr / getattr / except Exception liberally. The result of
# each adapter is normalized into ExtractResult.
#
# These adapters target the API surface of the EchterAlsFake libraries
# as of late 2024 -- if a library version bumps and breaks the surface,
# the adapter returns ok=False and the worker falls back to its normal
# path. We never crash.


def _phub_adapter(url: str, quality_preference: list[str],
                  cookies: Optional[dict]) -> ExtractResult:
    """PornHub via phub. phub.Video(url).get_direct_url(quality=...)."""
    phub = _try_import("phub")
    if phub is None:
        return ExtractResult(ok=False, error="library_not_installed")
    try:
        Client = getattr(phub, "Client", None)
        Video = getattr(phub, "Video", None)
        if Client and Video:
            client = Client()
            video = client.get(url)
        elif Video:
            video = Video(url)
        else:
            return ExtractResult(ok=False, error="phub_api_unknown",
                                 error_detail="neither Client nor Video found")
        # Quality enum: phub.Quality.BEST / .HIGH / .LOW
        Quality = getattr(phub, "Quality", None)
        if Quality and quality_preference:
            pref = quality_preference[0].lower()
            qmap = {"best": "BEST", "highest": "BEST", "max": "BEST",
                    "1080": "BEST", "high": "HIGH",
                    "720": "HIGH", "480": "MIDDLE", "360": "LOW",
                    "worst": "LOW", "low": "LOW", "min": "LOW"}
            qkey = qmap.get(pref, "BEST")
            qval = getattr(Quality, qkey, getattr(Quality, "BEST", None))
        else:
            qval = None
        # phub.Video has .get_direct_url(quality=Quality.BEST) OR .direct
        direct = None
        if hasattr(video, "get_direct_url"):
            try:
                direct = video.get_direct_url(quality=qval) if qval else video.get_direct_url()
            except Exception:
                pass
        if not direct and hasattr(video, "direct"):
            direct = video.direct
        if not direct:
            return ExtractResult(ok=False, error="phub_no_url",
                                 error_detail="video object had no direct URL")
        title = getattr(video, "title", "") or ""
        author = ""
        try:
            a = getattr(video, "author", None)
            if a:
                author = getattr(a, "name", str(a))
        except Exception:
            pass
        thumb = ""
        try:
            thumb = getattr(video, "image", "") or getattr(video, "thumbnail", "")
            if hasattr(thumb, "url"):
                thumb = thumb.url
        except Exception:
            pass
        duration = 0
        try:
            d = getattr(video, "duration", None)
            if d is not None:
                duration = int(getattr(d, "seconds", d))
        except Exception:
            pass
        return ExtractResult(
            ok=True, file_url=direct,
            is_hls=direct.endswith(".m3u8") or "/hls/" in direct,
            title=title, author=author, thumbnail_url=thumb,
            duration_sec=duration, quality=quality_preference[0] if quality_preference else "best",
            extractor="phub",
        )
    except Exception as e:
        return ExtractResult(ok=False, error="phub_raised",
                             error_detail=f"{type(e).__name__}: {e}")


def _generic_eaf_adapter(libname: str, url: str,
                         quality_preference: list[str],
                         cookies: Optional[dict]) -> ExtractResult:
    """Shared implementation for the EchterAlsFake-family libraries that
    share a common API shape: `<lib>.Client().get_video(url)` returns an
    object with `.get_m3u8_by_quality(quality)` or `.direct_download_url`,
    plus `.title`, `.author`, `.thumbnail`, `.length` attributes.

    Used by: xnxx_api, xvideos_api, eporner_api, youporn_api, hqporner_api,
    xhamster_api, spankbang_api, missav_api, xfreehd_api, beeg_api,
    porntrex_api, porngo_api. Each thin adapter just calls this with
    its own libname.
    """
    lib = _try_import(libname)
    if lib is None:
        return ExtractResult(ok=False, error="library_not_installed")
    try:
        # All EAF libs follow the Client().get_video() pattern.
        Client = getattr(lib, "Client", None)
        if Client is None:
            return ExtractResult(ok=False, error="eaf_no_client",
                                 error_detail=f"{libname} has no Client class")
        client = Client()
        # The get_video / get method varies slightly by library
        get_video = (getattr(client, "get_video", None)
                     or getattr(client, "get", None)
                     or getattr(client, "video", None))
        if get_video is None:
            return ExtractResult(ok=False, error="eaf_no_get",
                                 error_detail=f"{libname} client has no get/get_video")
        video = get_video(url)
        # Quality enum
        Quality = getattr(lib, "Quality", None)
        qval = None
        if Quality and quality_preference:
            pref = quality_preference[0].lower()
            qmap = {"best": "BEST", "highest": "BEST",
                    "worst": "WORST", "lowest": "WORST",
                    "high": "HALF", "low": "WORST"}
            qkey = qmap.get(pref, "BEST")
            qval = getattr(Quality, qkey, getattr(Quality, "BEST", None))
        # Find the URL via one of the common methods.
        file_url = ""
        is_hls = False
        # Try m3u8 first (most EAF libs default to this)
        for attr in ("get_m3u8_by_quality", "get_m3u8"):
            fn = getattr(video, attr, None)
            if callable(fn):
                try:
                    file_url = fn(qval) if qval else fn("best")
                    if file_url:
                        is_hls = True
                        break
                except Exception:
                    pass
        # Fall back to direct download URL
        if not file_url:
            for attr in ("direct_download_url", "direct_url", "url"):
                v = getattr(video, attr, None)
                if v and isinstance(v, str):
                    file_url = v
                    is_hls = file_url.endswith(".m3u8")
                    break
        if not file_url:
            return ExtractResult(ok=False, error="eaf_no_url",
                                 error_detail=f"{libname} video had no URL")
        # Metadata
        title = getattr(video, "title", "") or ""
        author = ""
        for attr in ("author", "uploader", "channel"):
            v = getattr(video, attr, None)
            if v:
                author = getattr(v, "name", str(v))
                break
        thumb = getattr(video, "thumbnail", "") or getattr(video, "image", "") or ""
        if hasattr(thumb, "url"):
            thumb = thumb.url
        duration = 0
        for attr in ("length", "duration", "seconds"):
            v = getattr(video, attr, None)
            if v:
                try:
                    duration = int(getattr(v, "seconds", v))
                    break
                except Exception:
                    pass
        return ExtractResult(
            ok=True, file_url=file_url, is_hls=is_hls,
            title=title, author=author, thumbnail_url=str(thumb) if thumb else "",
            duration_sec=duration,
            quality=quality_preference[0] if quality_preference else "best",
            extractor=libname,
        )
    except Exception as e:
        return ExtractResult(ok=False, error=f"{libname}_raised",
                             error_detail=f"{type(e).__name__}: {e}")


# Generated thin wrappers for the EAF family.
def _redtube_adapter(url, qp, cookies):  return _generic_eaf_adapter("eaf_base_api", url, qp, cookies)
def _youporn_adapter(url, qp, cookies):  return _generic_eaf_adapter("youporn_api",  url, qp, cookies)
def _xnxx_adapter(url, qp, cookies):     return _generic_eaf_adapter("xnxx_api",     url, qp, cookies)
def _xvideos_adapter(url, qp, cookies):  return _generic_eaf_adapter("xvideos_api",  url, qp, cookies)
def _xhamster_adapter(url, qp, cookies): return _generic_eaf_adapter("xhamster_api", url, qp, cookies)
def _spankbang_adapter(url, qp, cookies):return _generic_eaf_adapter("spankbang_api",url, qp, cookies)
def _eporner_adapter(url, qp, cookies):  return _generic_eaf_adapter("eporner_api",  url, qp, cookies)
def _hqporner_adapter(url, qp, cookies): return _generic_eaf_adapter("hqporner_api", url, qp, cookies)
def _beeg_adapter(url, qp, cookies):     return _generic_eaf_adapter("beeg_api",     url, qp, cookies)
def _porntrex_adapter(url, qp, cookies): return _generic_eaf_adapter("porntrex_api", url, qp, cookies)
def _missav_adapter(url, qp, cookies):   return _generic_eaf_adapter("missav_api",   url, qp, cookies)
def _xfreehd_adapter(url, qp, cookies):  return _generic_eaf_adapter("xfreehd_api",  url, qp, cookies)
def _porngo_adapter(url, qp, cookies):   return _generic_eaf_adapter("porngo_api",   url, qp, cookies)


__all__ = [
    "ExtractResult",
    "extract",
    "is_available",
    "is_supported_url",
    "list_supported_sites",
    "installed_libraries",
]
