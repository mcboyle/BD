"""provider_resolve_impl._common -- SSRFBlocked + SSRF/transport/cache/honeypot
helpers + _make_default_http_get + the _default_http_get module-level instance.
Sink. Lazy `from . import global_config/honeypot_*` absolutized to `..`."""

from __future__ import annotations
from contextvars import ContextVar
import json
import os
import re
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote as _urlquote, urlparse as _urlparse, parse_qs as _parse_qs


import sys as _sys  # H-07 shim capture
_PR_SHIM_REF = _sys.modules.get("bulk_downloader.provider_resolve")
_PR_SHIM_CONTEXT = ContextVar("provider_resolve_facade", default=None)


def __pr_shim():
    # Return the provider_resolve SHIM object THIS module was loaded with.
    # Captured at import time (when our own shim loaded us) so that if the
    # test suite drops bulk_downloader.* from sys.modules and a fresh copy is
    # imported, the function a test invokes (via its collection-time `pr`)
    # still reads the SAME object that test monkeypatched -- a call-time
    # sys.modules re-fetch would return the reloaded twin and miss the patch.
    contextual = _PR_SHIM_CONTEXT.get()
    if contextual is not None:
        return contextual
    global _PR_SHIM_REF
    if _PR_SHIM_REF is None:
        import bulk_downloader.provider_resolve as _m
        _PR_SHIM_REF = _m
    return _PR_SHIM_REF


HttpGet = Callable[[str], Tuple[int, Dict[str, str], bytes]]


CacheWrite = Callable[[str, str, str, float], None]


def _now() -> float:
    """Module-level seam so tests can monkeypatch time. Imported
    lazily so the offline path doesn't pay an import."""
    import time as _t
    return _t.time()


def _honeypot_score_threshold_raw() -> str:
    """v3.66.313 (CLI->GUI parity): resolve the honeypot score threshold string
    store > env seed > "" at call time (GUI write authoritative; the env var is the
    seed when the global_config key is unset). global_config imported lazily."""
    try:
        from .. import global_config as _gc
        v = _gc.get("honeypot_score_threshold", None)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("BD_HONEYPOT_SCORE_THRESHOLD", "").strip()


def _honeypot_score_threshold() -> Optional[float]:
    """Parsed/validated honeypot threshold (store > env): float in (0, 1] or None.
    Mirrors the validation in _honeypot_drop_threshold (<=0 or >1.0 -> None)."""
    raw = _honeypot_score_threshold_raw()
    if not raw:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    # F-REC03-03: NaN passes both bounds checks (nan<=0 and nan>1 are each
    # False), so it would be returned as the threshold and make every
    # `score < NaN` comparison False -- silently disabling honeypot dropping.
    import math as _math
    if not _math.isfinite(val):
        return None
    if val <= 0 or val > 1.0:
        return None
    return val


def _honeypot_drop_threshold(site_id: Optional[str] = None) -> Optional[float]:
    """Read ``BD_HONEYPOT_SCORE_THRESHOLD`` env var; return parsed
    float or None if unset/empty/invalid.

    Returns
    -------
    float in (0, 1] if the operator opted in; ``None`` otherwise.
    A value of 0 or below is treated as None (off) — operators
    can't accidentally disable all candidates by setting it to 0.

    Read every call (not module-load) so tests can flip it via
    ``monkeypatch.setenv``. The cost is one env lookup on the cold
    path; negligible.

    P5-2b (v3.66.36): when ``BD_HONEYPOT_PER_SITE=1`` AND a ``site_id`` is
    supplied AND the global threshold is active, the global value is
    replaced by the per-site learned threshold (a quantile of the site's
    confirmed-trap scores). With insufficient per-site evidence the
    learner returns the global value unchanged, so this is inert until the
    history column has real data. Per-site has no effect when the global
    feature is off (None) — there's no base behaviour to refine.
    """
    raw = _honeypot_score_threshold_raw()
    if not raw:
        return None
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val <= 0:
        return None
    if val > 1.0:
        # Clamp to 1.0 — values above 1.0 mean "never drop", which
        # is functionally equivalent to off. Treat as off so the
        # downscore zone also doesn't activate.
        return None
    # Per-site refinement (opt-in). Never raises — falls back to `val`.
    if site_id:
        try:
            from .. import honeypot_threshold as _ht
            if _ht.enabled():
                val = _ht.learned_drop_threshold(site_id, default=val)
        except Exception:
            pass
    return val


def _apply_honeypot_filter(
    candidates: List[dict],
    provider: str,
    drop_threshold: Optional[float],
) -> Tuple[List[dict], List[dict]]:
    """Score each candidate; drop the ones above ``drop_threshold``,
    downscore the middle zone. Returns ``(kept, dropped)``.

    Score classification follows ``honeypot_score.classify_score``:
      * score >= drop_threshold       → drop
      * downscore_zone <= score < drop_threshold → keep but multiply
        the candidate's ``score`` by 0.5 and append a warning
      * score < downscore_zone        → keep unchanged

    The downscore zone lower bound is fixed at
    ``DEFAULT_DOWNSCORE_THRESHOLD`` (0.5) — operators tune the drop
    boundary, not the downscore boundary, in this first cut.

    If ``drop_threshold`` is None (the default), this function is a
    no-op: returns ``(candidates, [])`` unchanged.
    """
    if drop_threshold is None or not candidates:
        return candidates, []

    # Lazy import: don't pay the cost when feature is off
    from .. import honeypot_score as _hs

    kept: List[dict] = []
    dropped: List[dict] = []

    for c in candidates:
        if not isinstance(c, dict):
            kept.append(c)
            continue
        try:
            score, reason = _hs.score_candidate(c)
        except Exception:
            # Scorer is pure-function and shouldn't raise, but if a
            # future rule mishandles a weird input we must NOT drop a
            # candidate that's actually fine. Fail open: keep it.
            kept.append(c)
            continue

        action = _hs.classify_score(
            score,
            drop_threshold=drop_threshold,
            downscore_threshold=_hs.DEFAULT_DOWNSCORE_THRESHOLD,
        )
        if action == "drop":
            # Annotate with the reason for the operator's event log.
            # The caller stamps this onto the log entry.
            c_with_reason = dict(c)
            c_with_reason["_honeypot_score"] = score
            c_with_reason["_honeypot_reason"] = reason
            dropped.append(c_with_reason)
        elif action == "downscore":
            # Halve the candidate's score so it ranks below cleaner
            # alternatives but isn't dropped entirely.
            c_ds = dict(c)
            orig = c_ds.get("score", 0)
            try:
                c_ds["score"] = int(orig * 0.5) if isinstance(orig, int) else float(orig) * 0.5
            except (TypeError, ValueError):
                pass  # leave score as-is if it's a weird type
            warnings = list(c_ds.get("warnings") or [])
            warnings.append(
                f"honeypot signal (score={score:.2f}, "
                f"reason={reason or 'unknown'}); downscored")
            c_ds["warnings"] = warnings
            c_ds["_honeypot_score"] = score
            c_ds["_honeypot_reason"] = reason
            kept.append(c_ds)
        else:
            kept.append(c)

    return kept, dropped


def _cache_lookup(
    site_memory: Optional[dict],
    provider: str,
    embed_id: Optional[str],
    ttl_seconds: float,
) -> Optional[dict]:
    """Read a cached resolution from a site_memory dict, honoring TTL.

    Returns the cache entry dict (``{id, url, at}``) on hit, None on
    miss. Returns None — never raises — for any malformed shape. The
    "id" stored in the cache is matched against ``embed_id`` so a
    cache for a different video in the same site doesn't false-hit.
    """
    _pr = __pr_shim()  # H-07: the shim instance THIS module was loaded with
    _now = _pr._now
    if not isinstance(site_memory, dict) or not embed_id:
        return None
    pes = site_memory.get("provider_embeds_seen")
    if not isinstance(pes, dict):
        return None
    entry = pes.get(provider)
    if not isinstance(entry, dict):
        return None
    cached = entry.get("last_resolved")
    if not isinstance(cached, dict):
        return None
    if cached.get("id") != embed_id:
        return None
    at = cached.get("at")
    if not isinstance(at, (int, float)):
        return None
    if (_now() - at) > ttl_seconds:
        return None  # expired
    url = cached.get("url")
    if not isinstance(url, str) or not url:
        return None
    return cached


def _primary_id(provider: str, ids: dict) -> Optional[str]:
    """Pick the canonical id for cache-keying. Mirrors learn.py's
    ``provider_embeds_seen.last_id`` preference order. Returns None
    if no usable id is present."""
    if not isinstance(ids, dict) or not ids:
        return None
    for key in ("video_id", "entry_id", "playback_id", "clip_id",
                "hashed_id", "media_id"):
        v = ids.get(key)
        if isinstance(v, str) and v:
            return v
    # Last-ditch: first non-empty string value
    for v in ids.values():
        if isinstance(v, str) and v:
            return v
    return None


def _coerce_int(v):
    """Best-effort int coercion; returns None on failure or empty."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            # Some Vimeo fields are floats (e.g. fps: 29.97); fall
            # back to int(float(...)) and accept the truncation.
            return int(float(v))
        except (TypeError, ValueError):
            return None


class SSRFBlocked(Exception):
    """Raised when SSRF guard refuses to fetch a URL.

    Distinct exception so resolver call sites can tell a guard
    refusal apart from a network error. Resolvers catch the broad
    ``Exception`` already and surface it in their error string, so
    no resolver code needs to change to take advantage — the
    exception name + message naturally end up in the error.
    """


def _is_safe_public_host(host: str) -> Tuple[bool, str]:
    """Return ``(True, "")`` if ``host`` resolves to a public unicast
    IP, or ``(False, reason)`` if it's private/loopback/link-local/
    reserved/multicast.

    The hostname → IP resolution happens inside this check (we don't
    trust the caller to have pre-resolved). Any DNS failure is
    treated as "refuse" with an informative reason — defensive
    posture: better to fail closed on resolution errors than to
    let them through.

    Note on TOCTOU / DNS rebinding (closed in v3.66.25):
    there's a window between this resolution and httpx's own
    resolution at connect time. An attacker controlling
    authoritative DNS could rebind between the two. That gap is
    closed by ``_SSRFGuardedTransport`` (see below), which is
    installed by ``_make_default_http_get`` and does its own
    resolve+classify at the transport layer, then pins the
    connection to the IP it just vetted. This predicate remains
    as the fail-fast pre-fetch and redirect-target check.
    """
    import socket
    import ipaddress

    if not host:
        return False, "no host"

    # Strip IPv6 brackets if present. urlparse leaves them on for
    # bracketed hosts (e.g. "[::1]").
    bare = host
    if bare.startswith("[") and bare.endswith("]"):
        bare = bare[1:-1]

    # First, try parsing the host as a literal IP. If it parses,
    # there's no DNS lookup to do — check the literal directly.
    try:
        addr = ipaddress.ip_address(bare)
        return _classify_ip(addr, bare)
    except ValueError:
        pass

    # Hostname → resolve via getaddrinfo. Use SOCK_STREAM and a
    # short timeout shouldn't apply here (getaddrinfo doesn't take
    # one), but on failure we fail closed.
    try:
        infos = socket.getaddrinfo(bare, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError) as ex:
        return False, f"DNS resolution failed: {type(ex).__name__}: {ex}"

    if not infos:
        return False, "DNS resolution returned no addresses"

    # Every resolved address must be safe — if ANY is private, refuse.
    # This stops the simple "name resolves to multiple IPs, one
    # public one private" trick.
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family == socket.AF_INET:
            ip_str = sockaddr[0]
        elif family == socket.AF_INET6:
            ip_str = sockaddr[0]
        else:
            continue
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"got non-IP from getaddrinfo: {ip_str!r}"
        ok, reason = _classify_ip(addr, bare)
        if not ok:
            return False, reason
    return True, ""


def _classify_ip(addr, host_repr: str) -> Tuple[bool, str]:
    """Per-IP safety check. Splits out from _is_safe_public_host so
    literal-IP hosts and resolved hostnames share the same predicate.
    """
    # Reject loopback (127/8, ::1), private (10/8, 172.16/12, 192.168/16,
    # fc00::/7), link-local (169.254/16, fe80::/10), and reserved
    # (everything else not in the global unicast space).
    #
    # Specifically: is_private covers RFC1918 + IPv6 ULA but ALSO
    # covers loopback and link-local on Python's stdlib (since they're
    # "not public"). is_loopback and is_link_local are explicit
    # subsets. We list all four for clarity in the rejection reason.
    if addr.is_loopback:
        return False, f"refusing loopback address ({host_repr} → {addr})"
    # v3.66.524 (VR-P15): RFC 6598 CGNAT / shared address space (100.64.0.0/10)
    # is NOT flagged is_private/is_reserved/is_global by the stdlib, so it slipped
    # every check below and the SSRF classifier returned safe. Reject it
    # explicitly (it routes to carrier-internal space). This is the single shared
    # predicate, so _is_safe_public_host inherits the rejection.
    import ipaddress as _ip
    if isinstance(addr, _ip.IPv4Address) and addr in _ip.ip_network("100.64.0.0/10"):
        return False, f"refusing CGNAT/shared address RFC 6598 ({host_repr} → {addr})"
    if addr.is_link_local:
        return False, f"refusing link-local address ({host_repr} → {addr})"
    if addr.is_private:
        return False, f"refusing private address ({host_repr} → {addr})"
    if addr.is_reserved:
        return False, f"refusing reserved address ({host_repr} → {addr})"
    if addr.is_multicast:
        return False, f"refusing multicast address ({host_repr} → {addr})"
    if addr.is_unspecified:
        return False, f"refusing unspecified address ({host_repr} → {addr})"
    return True, ""


_SSRF_GUARDED_TRANSPORT_CLS = None


def _SSRFGuardedTransport_factory():
    """Return the ``_SSRFGuardedTransport`` class, lazy-imported.

    Defined as a factory so the heavy ``httpx`` import only happens
    when a caller actually constructs the default fetcher (matching
    the lazy-import discipline of ``_make_default_http_get``). The
    class is memoized at module level so repeated factory calls
    return the same class object — important for ``isinstance``
    checks in tests.
    """
    global _SSRF_GUARDED_TRANSPORT_CLS
    if _SSRF_GUARDED_TRANSPORT_CLS is not None:
        return _SSRF_GUARDED_TRANSPORT_CLS

    import socket
    import ipaddress
    import httpx

    class _SSRFGuardedTransport(httpx.HTTPTransport):
        """Rebinding-safe HTTPTransport.

        Closes the TOCTOU window between ``_is_safe_public_host``'s
        DNS resolution and httpx/httpcore's own resolution at
        connect time. For each outgoing request:

        1. If the request URL's host is already an IP literal, just
           delegate — there's no DNS step to rebind on. (The
           pre-fetch / redirect event hook already classified it.)
        2. Otherwise, resolve the hostname HERE (in the transport),
           classify every returned IP with ``_classify_ip``. If any
           IP is private/loopback/link-local/etc., raise
           ``SSRFBlocked`` immediately (same fail-closed policy as
           the predicate — one bad IP poisons the resolution set).
        3. Pick one safe IP. Rewrite the request URL to use the IP
           literal so httpcore won't re-resolve. Set
           ``request.extensions["sni_hostname"]`` to the original
           hostname so TLS certificate verification continues to
           work. The ``Host`` header on the ``Request`` was set at
           construction time from the original URL and is NOT
           overwritten by the URL rewrite — virtual-hosting servers
           continue to route correctly.
        4. Delegate to the base transport, which now connects
           directly to the vetted IP.

        Because the IP literal is what httpcore sees, no third
        ``getaddrinfo`` call happens between classification and
        connect. The rebinding window is closed.

        Composition: this is independent of the existing event-hook
        guard. The event hook fires once per outgoing request
        (initial + every redirect target) at the host-name level —
        it's the fail-fast layer. The transport fires once per
        connect, at the IP level. Both must be present for full
        defense in depth.

        When constructed with ``allow_private_hosts=True`` the
        transport is a pass-through (delegates without rewriting).
        That preserves the opt-out semantics of
        ``_make_default_http_get(allow_private_hosts=True)``.
        """

        def __init__(self, *args, allow_private_hosts: bool = False, **kwargs):
            super().__init__(*args, **kwargs)
            self._allow_private_hosts = allow_private_hosts

        def handle_request(self, request: "httpx.Request") -> "httpx.Response":
            self._prepare_request(request)
            return super().handle_request(request)

        def _prepare_request(self, request: "httpx.Request") -> None:
            """Resolve, classify, and IP-pin the request URL in place.

            Split out from ``handle_request`` so tests can exercise
            the SSRF logic without going to the network. Raises
            ``SSRFBlocked`` if the host fails classification.
            Mutates the request: rewrites ``url`` to the IP literal
            and sets ``extensions['sni_hostname']`` to the original
            hostname. No-op (returns None) if the opt-out is set or
            the URL is already an IP literal.
            """
            if self._allow_private_hosts:
                return

            original_host = request.url.host
            if not original_host:
                # No host means this can't be SSRF'd in a meaningful
                # way (likely a unix-domain-socket scheme); let the
                # base transport handle it.
                return

            # Skip rewriting when host is already an IP literal. The
            # event hook already classified it; rewriting to the same
            # literal would be a no-op but the indirection costs an
            # extra getaddrinfo (which on a literal is cheap but
            # noisy). Detect both v4 and v6, with and without
            # IPv6 brackets — though httpx.URL.host strips brackets.
            try:
                ipaddress.ip_address(original_host)
                return
            except ValueError:
                pass

            # Resolve and classify. Use the URL's port if set so the
            # getaddrinfo hint is correct for AF_INET6 scope.
            port_hint = request.url.port or (
                443 if request.url.scheme in ("https", "wss") else 80
            )
            try:
                infos = socket.getaddrinfo(
                    original_host, port_hint, type=socket.SOCK_STREAM,
                )
            except (socket.gaierror, OSError, UnicodeError) as ex:
                raise SSRFBlocked(
                    f"SSRF guard (transport): DNS resolution failed "
                    f"for {original_host!r}: "
                    f"{type(ex).__name__}: {ex}"
                )
            if not infos:
                raise SSRFBlocked(
                    f"SSRF guard (transport): DNS resolution returned "
                    f"no addresses for {original_host!r}"
                )

            # Classify every resolved IP. If ANY is private, refuse —
            # mirrors the policy of _is_safe_public_host. Picking the
            # "first safe one" while a sibling is private would let
            # subsequent retries/connections rebind onto the private
            # IP via httpcore's own retry path.
            chosen = None  # (ip_str, family)
            for family, _stype, _proto, _canon, sockaddr in infos:
                if family not in (socket.AF_INET, socket.AF_INET6):
                    continue
                ip_str = sockaddr[0]
                try:
                    addr = ipaddress.ip_address(ip_str)
                except ValueError:
                    raise SSRFBlocked(
                        f"SSRF guard (transport): non-IP from "
                        f"getaddrinfo for {original_host!r}: "
                        f"{ip_str!r}"
                    )
                ok, reason = _classify_ip(addr, original_host)
                if not ok:
                    raise SSRFBlocked(
                        f"SSRF guard (transport): {reason}"
                    )
                if chosen is None:
                    chosen = (ip_str, family)

            if chosen is None:
                # Defensive: filtered to nothing (all family unknown).
                raise SSRFBlocked(
                    f"SSRF guard (transport): no usable IP returned "
                    f"for {original_host!r}"
                )

            ip_literal = chosen[0]

            # Pin the connection to the IP we just vetted. httpx
            # bracket-handles IPv6 inside copy_with.
            new_url = request.url.copy_with(host=ip_literal)
            # request.url is a settable property; the Host header
            # was populated from the original URL when the Request
            # was constructed and is preserved (httpx does not
            # rewrite headers from a URL mutation).
            request.url = new_url
            # Preserve TLS hostname verification. httpcore reads this
            # extension when calling start_tls.
            request.extensions["sni_hostname"] = original_host

    _SSRF_GUARDED_TRANSPORT_CLS = _SSRFGuardedTransport
    return _SSRFGuardedTransport


def _make_default_http_get(
    *, allow_private_hosts: bool = False,
) -> HttpGet:
    """Factory for the default http_get fetcher.

    The factory pattern lets test code and dev/mock setups construct
    a relaxed variant (``allow_private_hosts=True``) that bypasses
    the SSRF guard, without polluting the production path with an
    env-var that could be flipped unintentionally. The constructor
    flag is the only way to opt out — there is deliberately no env
    var, no module-level switch, no class attribute. If you find
    yourself needing one for an integration test, accept the
    test-scope override of ``_default_http_get`` via monkeypatch
    instead.

    The returned callable:
      * checks the URL's host against ``_is_safe_public_host``
        before the network call (unless ``allow_private_hosts``);
      * installs an httpx event hook that re-checks the host on
        every redirect target, so a public host that 302s to a
        private one is also blocked;
      * uses ``_SSRFGuardedTransport`` as the underlying httpx
        transport, which does a second classification at connect
        time and pins the connection to a vetted IP literal —
        closing the DNS rebinding TOCTOU window between the
        pre-fetch resolution and httpcore's connect-time
        resolution;
      * raises ``SSRFBlocked`` on any guard refusal, which resolver
        call sites already catch via their broad ``except Exception``
        and surface in the error string.
    """
    import ipaddress  # noqa: F401  (re-exported for type clarity)

    def _http_get(url: str) -> Tuple[int, Dict[str, str], bytes]:
        _pr = __pr_shim()  # H-07: the shim instance THIS module was loaded with
        _is_safe_public_host = _pr._is_safe_public_host
        # Lazy imports keep cold start cheap for callers that
        # inject their own http_get.
        import httpx
        from urllib.parse import urlparse

        # Pre-fetch guard. Parse the URL, pull the host, check it.
        if not allow_private_hosts:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            ok, reason = _is_safe_public_host(host)
            if not ok:
                raise SSRFBlocked(f"SSRF guard: {reason}")

        # Redirect-time guard. httpx fires the "request" event hook
        # on EVERY outgoing request, including redirect follow-ups.
        # We re-check each one. If a public host 302s to a private
        # one, the hook raises and httpx propagates the exception.
        def _request_hook(req):
            if allow_private_hosts:
                return
            # req.url is httpx's URL type; .host gives the bare host
            host_redir = req.url.host or ""
            ok2, reason2 = _is_safe_public_host(host_redir)
            if not ok2:
                raise SSRFBlocked(
                    f"SSRF guard (redirect): {reason2}"
                )

        hooks = {"request": [_request_hook]}

        # Build the guarded transport. The factory call is cheap
        # after the first invocation (httpx is already imported).
        transport_cls = _SSRFGuardedTransport_factory()
        transport = transport_cls(
            allow_private_hosts=allow_private_hosts,
        )

        # 8s is enough for the /config endpoint (typically <500ms);
        # we don't want resolution to dominate the overall
        # deep_detect budget on a flaky network. Caller can override
        # the timeout by passing their own http_get.
        with httpx.Client(
            timeout=8.0, follow_redirects=True, event_hooks=hooks,
            transport=transport,
        ) as client:
            r = client.get(url, headers={
                "Accept": "application/json",
                # Vimeo's /config 403s on requests with no Referer in
                # some embed-protected configurations. We don't have
                # the original page URL here, so we send the embed
                # URL — sufficient for the public-embed case which
                # is what this resolver targets.
                "User-Agent": "BulkDownloader/3.66 (provider_resolve)",
            })
        return r.status_code, dict(r.headers), r.content

    return _http_get


_default_http_get: HttpGet = _make_default_http_get()
