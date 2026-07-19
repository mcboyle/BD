"""provider_resolve_impl.dispatch -- resolve_provider_embed + build_signing_callback +
the _RESOLVERS registry. Imports _common helpers + the 5 provider resolvers."""

from __future__ import annotations
from typing import List, Optional, Tuple

from ._common import _apply_honeypot_filter, _cache_lookup, _honeypot_drop_threshold, _now, _primary_id
from .vimeo import resolve_vimeo
from .wistia import resolve_wistia
from .jwplayer import resolve_jwplayer
from .brightcove import resolve_brightcove
from .youtube import resolve_youtube


import sys as _sys  # H-07 shim capture
_PR_SHIM_REF = _sys.modules.get("bulk_downloader.provider_resolve")


def __pr_shim():
    # Return the provider_resolve SHIM object THIS module was loaded with.
    # Captured at import time (when our own shim loaded us) so that if the
    # test suite drops bulk_downloader.* from sys.modules and a fresh copy is
    # imported, the function a test invokes (via its collection-time `pr`)
    # still reads the SAME object that test monkeypatched -- a call-time
    # sys.modules re-fetch would return the reloaded twin and miss the patch.
    global _PR_SHIM_REF
    if _PR_SHIM_REF is None:
        import bulk_downloader.provider_resolve as _m
        _PR_SHIM_REF = _m
    return _PR_SHIM_REF


DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60


def build_signing_callback(config: Optional[dict]):
    """Build a JWPlayer signing-callback from a per-site config (C2).

    Operators running a self-hosted JWPlayer behind their own signing
    endpoint can wire a fetcher without code changes, via two per-site
    config fields::

        signing_callback_module:   "my_pkg.jw_signer"   # importable path
        signing_callback_function: "fetch_signed"        # attr on the module

    The named function must implement the signing_callback contract
    documented on ``resolve_provider_embed``::

        fn(url) -> (status:int, headers:dict, body:bytes)

    Returns the resolved callable, or ``None`` when either field is
    missing, the import/lookup fails, or the attribute isn't callable.
    Fail-safe by design: a misconfigured callback must never raise into
    the resolution path — it degrades to "no callback", so the embed
    falls back to the default ``http_get`` (which on a signed endpoint
    yields a clean 401/403 error rather than a crash).

    NOTE: this imports an operator-named module by dotted path — an
    arbitrary-import surface. Acceptable here because BulkDownloader is a
    single-operator tool whose config the operator controls; it must NOT
    be fed untrusted config input.
    """
    if not isinstance(config, dict):
        return None
    mod_path = (config.get("signing_callback_module") or "").strip()
    fn_name = (config.get("signing_callback_function") or "").strip()
    if not mod_path or not fn_name:
        return None
    try:
        import importlib
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, fn_name, None)
    except Exception as ex:
        import sys as _sys
        _sys.stderr.write(
            f"  signing_callback: import failed for "
            f"{mod_path}.{fn_name} ({type(ex).__name__}: {str(ex)[:80]}); "
            "JWPlayer embeds will use the default fetcher\n")
        return None
    if not callable(fn):
        import sys as _sys
        _sys.stderr.write(
            f"  signing_callback: {mod_path}.{fn_name} is not callable; "
            "ignoring\n")
        return None
    return fn


def resolve_provider_embed(
    embed: dict,
    *,
    http_get: Optional[HttpGet] = None,
    site_memory: Optional[dict] = None,
    cache_write: Optional[CacheWrite] = None,
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    site_id: Optional[str] = None,
) -> Tuple[List[dict], Optional[str]]:
    """Resolve a single provider embed dict to playable candidates.

    ``embed`` is one entry from ``extract_provider_embeds`` output:
    ``{"provider": "vimeo", "source_type": "vimeo_embed",
       "ids": {"clip_id": "123"}, "url": "...", "found_in": "..."}``

    Returns ``(candidates, error)``. On success: ``candidates`` is a
    list of new candidate dicts; ``error`` is None. On failure:
    ``candidates`` is ``[]`` and ``error`` is a human-readable string.

    Returns ``([], None)`` (success, no candidates) for providers
    we don't have a resolver for yet. Use the absence of an error
    to distinguish "we tried" from "we won't try".

    Persistent cache (v3.66.17+)
    ----------------------------
    If ``site_memory`` carries a ``provider_embeds_seen[provider]
    .last_resolved = {id, url, at}`` entry for the embed's id, and
    that entry is younger than ``cache_ttl_seconds``, the cached URL
    is returned directly as a single ``<provider>_resolved_cached``
    candidate — no network call. Cache hits surface
    ``found_in: "provider_resolved_cache:<provider>"`` so downstream
    consumers can distinguish them.

    On a successful resolution (network path), if ``cache_write`` is
    provided, it is called with ``(provider, embed_id, best_url, ts)``
    where ``best_url`` is the highest-scored candidate's URL. The
    dispatcher does not write into ``site_memory`` directly — the
    caller is responsible for persisting via ``learn.py``.
    """
    _pr = __pr_shim()  # H-07: the shim instance THIS module was loaded with
    _now = _pr._now
    _default_http_get = _pr._default_http_get
    if not isinstance(embed, dict):
        return [], "embed is not a dict"
    provider = embed.get("provider")
    if not provider:
        return [], "embed has no provider"

    resolver = _RESOLVERS.get(provider)
    if resolver is None:
        # Not an error — just no resolver yet. The original
        # needs_provider_resolution candidate stays as-is.
        return [], None

    ids = embed.get("ids") or {}
    embed_id = _primary_id(provider, ids)

    # Cache lookup --------------------------------------------------------
    cached = _cache_lookup(site_memory, provider, embed_id, cache_ttl_seconds)
    if cached is not None:
        embed_url = embed.get("url")
        return [{
            "url": cached["url"],
            "source_type": f"{provider}_resolved_cached",
            "score": 85,  # below network HLS (90), above progressive (80)
            "resolution": None,
            "codec": None,
            "fps": None,
            "size_bytes": None,
            "found_in": f"provider_resolved_cache:{provider}",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "cached": True,
            "reasons": [
                f"{provider} resolution cache hit (age "
                f"{int(_now() - cached['at'])}s, ttl {int(cache_ttl_seconds)}s)"
            ],
            "warnings": [],
            "requires_click": False,
        }], None

    if http_get is None:
        http_get = _default_http_get

    try:
        candidates, error = resolver(ids, embed=embed, http_get=http_get)
    except Exception as ex:  # never propagate
        return [], f"{provider} resolver crashed: {type(ex).__name__}: {ex}"

    # Honeypot filter (P5-2 v3.66.27) -------------------------------------
    # Default: env var unset → no-op. When opted in, scores each
    # candidate and either drops or downscores per the thresholds.
    # Runs BEFORE cache_write so we don't persist a URL we just dropped.
    drop_threshold = _honeypot_drop_threshold(site_id=site_id)
    if drop_threshold is not None and candidates:
        candidates, dropped = _apply_honeypot_filter(
            candidates, provider, drop_threshold)
        if dropped:
            # Emit the drop info via stderr so the operator can see it
            # in the runner log. Event-log integration is a separate
            # follow-up (B4) — for now stderr keeps the wiring simple
            # and consistent with the rest of provider_resolve.
            import sys as _sys
            for d in dropped:
                _sys.stderr.write(
                    f"  honeypot_drop: provider={provider} "
                    f"url={d.get('url', '')!r} "
                    f"score={d.get('_honeypot_score', 0):.2f} "
                    f"reason={d.get('_honeypot_reason', '')}\n")
        if not candidates and not error:
            # Every candidate was dropped — surface as an error so
            # the caller knows resolution found candidates that were
            # then filtered out, distinct from "resolver found
            # nothing."
            error = (
                f"all {provider} candidates filtered out by honeypot "
                f"score (threshold={drop_threshold:.2f})")

    # Cache write ---------------------------------------------------------
    if cache_write is not None and candidates and embed_id:
        # Persist the highest-scored candidate's URL. On score ties,
        # prefer HLS variants — a single playlist URL covers all
        # renditions, so a stale signed token doesn't trap callers
        # at one specific quality. We detect HLS by source_type
        # suffix (the resolvers emit `*_resolved_hls`).
        def _cache_pref(c):
            score = c.get("score", 0)
            is_hls = (c.get("source_type") or "").endswith("_hls")
            return (score, 1 if is_hls else 0)
        best = max(candidates, key=_cache_pref)
        best_url = best.get("url")
        if isinstance(best_url, str) and best_url:
            try:
                cache_write(provider, embed_id, best_url, _now())
            except Exception:
                # Cache write failures are non-fatal — the resolution
                # still succeeded; the caller just won't get a cache
                # hit next time. Silent by design.
                pass

    return candidates, error


_RESOLVERS = {
    "vimeo":      resolve_vimeo,
    "youtube":    resolve_youtube,
    "brightcove": resolve_brightcove,
    "wistia":     resolve_wistia,
    "jwplayer":   resolve_jwplayer,
}
