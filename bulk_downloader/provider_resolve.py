"""bulk_downloader.provider_resolve -- thin re-export shim over provider_resolve_impl/.

Decomposed @v3.66.454 (DECOMP-LEAF cut 6, final). ADD-only (R1 shim-over-rm). Re-exports
the COMPLETE surface explicitly (no `import *`). _default_http_get is re-exported here and
resolve_provider_embed reads it back off THIS module at call time, so the documented
test monkeypatch `provider_resolve._default_http_get = ...` stays effective."""

from .provider_resolve_impl import (  # noqa: F401
    CacheWrite,
    DEFAULT_CACHE_TTL_SECONDS,
    HttpGet,
    SSRFBlocked,
    _BRIGHTCOVE_PLAYBACK_URL_TMPL,
    _JWPLAYER_ALLOWED_SCHEMES,
    _JWPLAYER_DRM_MARKER_KEYS,
    _JWPLAYER_FEED_URL_TMPL,
    _JWPLAYER_MEDIA_URL_TMPL,
    _JWPLAYER_SIGNED_URL_MARKER_KEYS,
    _JWP_DASH_MIMES,
    _JWP_HLS_MIMES,
    _JWP_MP4_MIMES,
    _RESOLVERS,
    _SSRFGuardedTransport_factory,
    _SSRF_GUARDED_TRANSPORT_CLS,
    _VIMEO_CONFIG_URL_TMPL,
    _WISTIA_MEDIA_URL_TMPL,
    _YOUTUBE_WATCH_URL_TMPL,
    _YT_CIPHER_SUBPROC_TIMEOUT_SECONDS,
    _YT_CIPHER_YTDLP_PATH_CACHE,
    _YT_DECIPHER_FN_RE,
    _YT_DECIPHER_STMT_RE,
    _YT_JS_URL_RE,
    _YT_PLAYER_JS_HOST_SUFFIXES,
    _YT_PLAYER_RESPONSE_RE,
    _YT_TRANSFORM_METHOD_RE,
    _YT_VIDEO_ID_RE,
    _apply_honeypot_filter,
    _apply_yt_decipher_ops,
    _build_yt_decipher_ops,
    _cache_lookup,
    _classify_ip,
    _classify_yt_transform,
    _coerce_int,
    _decipher_signed_formats,
    _decipher_signed_formats_playerjs,
    _decipher_signed_formats_ytdlp,
    _default_http_get,
    _honeypot_drop_threshold,
    _honeypot_score_threshold,
    _honeypot_score_threshold_raw,
    _is_safe_public_host,
    _jwplayer_check_drm_markers,
    _jwplayer_emit_sources,
    _make_default_http_get,
    _now,
    _primary_id,
    _resolve_jwplayer_feed,
    _resolve_jwplayer_media,
    _resolve_jwplayer_selfhosted,
    _slice_balanced_json,
    _yt_cipher_backend,
    _yt_cipher_ytdlp_path,
    build_signing_callback,
    resolve_brightcove,
    resolve_jwplayer,
    resolve_provider_embed,
    resolve_vimeo,
    resolve_wistia,
    resolve_youtube,
)

# Bind the implementation objects to the facade that exported them.  During
# whole-suite collection an implementation module can be imported before this
# facade, leaving its captured reference empty.  A later module-wipe must not
# make a retained exported function adopt a different facade and thereby miss
# the caller's public monkeypatch/injection seams.
import functools as _functools
import sys as _sys
from .provider_resolve_impl import _common as _pr_common_impl
from .provider_resolve_impl import dispatch as _pr_dispatch_impl
from .provider_resolve_impl import youtube as _pr_youtube_impl

_pr_facade = _sys.modules[__name__]


def _bind_pr_facade_if_unowned(implementation):
    if implementation._PR_SHIM_REF is None:
        implementation._PR_SHIM_REF = _pr_facade


def _bind_pr_facade_call(function):
    """Bind one shared implementation function to this facade per invocation."""
    facade = _pr_facade
    context = _pr_common_impl._PR_SHIM_CONTEXT

    @_functools.wraps(function)
    def bound(*args, **kwargs):
        token = context.set(facade)
        try:
            return function(*args, **kwargs)
        finally:
            context.reset(token)

    return bound


def _bind_pr_facade_factory(factory):
    """Bind both a callable factory and every deferred callable it returns."""
    facade = _pr_facade
    context = _pr_common_impl._PR_SHIM_CONTEXT
    wraps = _functools.wraps
    bound_factory = _bind_pr_facade_call(factory)

    def bind_deferred(function):
        @wraps(function)
        def bound(*args, **kwargs):
            token = context.set(facade)
            try:
                return function(*args, **kwargs)
            finally:
                context.reset(token)

        return bound

    @wraps(factory)
    def build(*args, **kwargs):
        return bind_deferred(bound_factory(*args, **kwargs))

    return build


_bind_pr_facade_if_unowned(_pr_common_impl)
_bind_pr_facade_if_unowned(_pr_dispatch_impl)
_bind_pr_facade_if_unowned(_pr_youtube_impl)

# The implementation package is shared when only this facade is evicted from
# ``sys.modules``.  A single module-global owner can preserve an old retained
# function OR serve the newly imported facade, but cannot do both.  Each facade
# therefore exports its own lightweight entry wrappers.  ContextVar makes the
# ownership nested-call-safe, thread-safe, and task-safe while the old global
# remains the fallback for callers that retained an implementation function
# directly.  Bind every facade entry that can reach ``__pr_shim``; the factory
# additionally binds the deferred transport callable it returns.
_cache_lookup = _bind_pr_facade_call(_cache_lookup)
_decipher_signed_formats_ytdlp = _bind_pr_facade_call(
    _decipher_signed_formats_ytdlp)
_decipher_signed_formats = _bind_pr_facade_call(_decipher_signed_formats)
resolve_youtube = _bind_pr_facade_call(resolve_youtube)
resolve_provider_embed = _bind_pr_facade_call(resolve_provider_embed)
_default_http_get = _bind_pr_facade_call(_default_http_get)
_make_default_http_get = _bind_pr_facade_factory(_make_default_http_get)

del _bind_pr_facade_if_unowned
del _bind_pr_facade_call, _bind_pr_facade_factory
del _pr_common_impl, _pr_dispatch_impl, _pr_youtube_impl, _pr_facade
del _functools, _sys

__all__ = [
    "HttpGet",
    "CacheWrite",
    "DEFAULT_CACHE_TTL_SECONDS",
    "SSRFBlocked",
    "resolve_provider_embed",
    "resolve_vimeo",
    "resolve_youtube",
    "resolve_brightcove",
    "resolve_wistia",
    "resolve_jwplayer",
    "build_signing_callback",
]
