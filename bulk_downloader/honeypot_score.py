"""Phase 5 P5-2: candidate-URL honeypot scoring (deterministic rules).

Pure-function scoring of candidate download URLs before they reach the
download queue. The intent is to catch URLs that look like real content
(right path shape, right filename extension) but resolve to tracking
pixels, paywall stubs, empty bodies, or 200-OK HTML decoy pages.

This is NOT the form-field honeypot detector — that lives in
``deep_detect.py`` (``_input_is_honeypot``). This module operates on
discovered candidate dicts post-resolution.

Design notes
------------

Pure function with one network call surface
    ``score_candidate(candidate, *, probe=None)`` takes a candidate
    dict (the same shape ``provider_resolve`` emits — has ``url`` and
    ``source_type``) and an optional ``probe`` dict (the result of a
    HEAD probe, supplied by the caller — this module never makes
    network calls itself). Returns ``(score, reason)`` where score is
    in [0.0, 1.0] (1.0 = almost certainly a trap) and reason is a
    short string enumerating rules that fired.

Rules combine by max, not sum
    A URL on a tracker host AND with a 200-byte body should not score
    1.6. Each rule contributes a confidence in [0.0, 1.0]; the final
    score is the max. This avoids the "many weak signals add up to a
    false positive" failure mode that plagues additive scorers.

Defaults are conservative
    Every threshold (the tracker host list, the 1KB Content-Length
    floor, etc.) is tuned so that real content does not trigger.
    Better to miss a honeypot than to drop a real download — the
    operator notices a wrong drop immediately; a missed honeypot
    becomes a "this file is suspiciously small" cleanup-helpers
    cleanup later (which already exists in cleanup_helpers.find_tinies).

Caller does the integration
    This module does not import ``provider_resolve``, does not read
    env vars, does not write to event logs. The caller (in
    ``provider_resolve.resolve_provider_embed``) handles the
    threshold check, the drop, and the event log entry. Keeps this
    module a flat pure-function library.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs


# ────────────────────────────────────────────────────────────────────
# Static vocabularies. These live in this module rather than constants.py
# because (a) constants.py has no existing tracker/ad list — the P5-2
# spec assumed one, but the survey turned up that cleanup_helpers.py is
# a library-cleanup module, not an ad-blocking one; (b) the lists are
# only consumed here.
# ────────────────────────────────────────────────────────────────────

# Known ad/tracker hosts. Conservative — only domains that are
# unambiguously not content delivery. CDN hostnames that ALSO carry
# real content (cloudfront, akamaihd, etc.) are NOT in this list.
KNOWN_TRACKER_HOSTS: frozenset = frozenset({
    # Ad tech
    "doubleclick.net",
    "googlesyndication.com",
    "googletagmanager.com",
    "googletagservices.com",
    "google-analytics.com",
    "googleadservices.com",
    "adservice.google.com",
    "adnxs.com",                # AppNexus / Xandr
    "rubiconproject.com",
    "pubmatic.com",
    "openx.net",
    "criteo.com",
    "criteo.net",
    "outbrain.com",
    "taboola.com",
    "mgid.com",
    "revcontent.com",
    "exoclick.com",             # adult ad network
    "trafficjunky.com",         # adult ad network
    "trafficjunky.net",
    "juicyads.com",
    "ero-advertising.com",
    "adskeeper.com",
    # Tracking pixels / analytics
    "scorecardresearch.com",
    "quantserve.com",
    "chartbeat.com",
    "newrelic.com",             # RUM beacons; rarely actually content
    "segment.com",
    "segment.io",
    "amplitude.com",
    "mixpanel.com",
    "hotjar.com",
    "fullstory.com",
    "mouseflow.com",
    "facebook.net",             # FB tracking pixel domain
    "connect.facebook.net",
    "fbcdn.net" + ".tracking",  # never matches — placeholder; fbcdn carries content
})

# URL path substrings that strongly suggest the URL is a beacon/pixel,
# not a content delivery URL. Matched case-insensitively against the
# path portion only (NOT host, NOT query).
PIXEL_PATH_TOKENS: frozenset = frozenset({
    "/pixel",
    "/track",
    "/beacon",
    "/collect",
    "/imp.gif",
    "/impression",
    "/conv.gif",
    "/conversion",
    "/utm.gif",
    "/b.gif",          # common 1x1 pixel name
    "/p.gif",
})

# F2 (R-P5-2 vocab extension): redirect/interstitial path terms. These
# are strong trap signals — a media candidate whose path routes through
# a click/redirect tracker is very rarely the real asset — but they are
# deliberately scored BELOW the drop threshold so a single match
# downscores rather than drops (the operator may legitimately route
# through a redirector). Matched as path substrings, case-insensitive.
TRAP_URL_TERMS: frozenset = frozenset({
    "/click",
    "/clk",
    "/go",
    "/out",
    "/redirect",
    "/tracking",
    "/interstitial",
    "/popup",
})

# F2: brand-name workflow terms. These appear in BOTH traps and
# legitimate download flows (e.g. clicking "Subscribe to get download"
# on a real membership site, or a real "/checkout" gate). Per the F2
# caveat they are wired as WEAK downscore signals only — never a drop,
# never enough on their own to remove a candidate — so a real
# membership/subscribe flow is preserved, just deprioritised. Matched
# as path substrings, case-insensitive.
BRAND_TRAP_TERMS: frozenset = frozenset({
    "affiliate",
    "sponsor",
    "offer",
    "survey",
    "verify",
    "captcha",
    "subscribe",
    "premium",
    "checkout",
})

# File extensions that, when claimed by a URL, mean we expect a
# media/binary response. If a HEAD probe returns text/html for one
# of these, the URL is almost certainly a trap.
MEDIA_EXTENSIONS: frozenset = frozenset({
    ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi",
    ".flv", ".wmv", ".mpg", ".mpeg", ".ts",
    ".m3u8", ".mpd",                          # streaming manifests
    ".jpg", ".jpeg", ".png", ".gif", ".webp", # images (less critical but same logic)
})

# Streaming manifest extensions. These are TEXT files by nature, so
# the "media but text/html" rule must skip them — an .m3u8 with
# application/vnd.apple.mpegurl or text/plain is fine.
MANIFEST_EXTENSIONS: frozenset = frozenset({".m3u8", ".mpd"})

# Minimum plausible body size for a media URL. Anything smaller is
# almost certainly a pixel or a stub. A real .m3u8 master can be
# ~200 bytes, so this is keyed off non-manifest extensions only.
TINY_BODY_THRESHOLD_BYTES = 1024  # 1 KB

# Content-types that count as "this is HTML, not media." A response
# with text/html for a .mp4 URL is overwhelmingly a paywall, login,
# or generic error page.
HTML_CONTENT_TYPES: frozenset = frozenset({
    "text/html",
    "application/xhtml+xml",
})


# ────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────

def score_candidate(
    candidate: dict,
    *,
    probe: Optional[dict] = None,
) -> Tuple[float, str]:
    """Score a single candidate dict for honeypot likelihood.

    Returns ``(score, reason)``:
      * ``score`` in [0.0, 1.0]; 1.0 = almost certainly a trap.
      * ``reason`` is a short comma-separated list of rule names that
        fired. Empty string if no rules fired (score 0.0).

    Parameters
    ----------
    candidate : dict
        A candidate dict from ``provider_resolve`` or
        ``deep_detect``. Required key: ``url``. Optional but used:
        ``source_type``.
    probe : dict or None
        Optional HEAD-probe result the caller may attach. Shape::

            {
                "status": int,                # HTTP status code
                "headers": dict,              # response headers (lowercased keys ok)
                "redirected_to": str | None,  # final URL after redirects
            }

        If ``probe`` is None, only pre-fetch rules fire (URL-shape
        and host-list checks). This is intentional — callers that
        don't want the cost of a HEAD probe can still get some
        scoring signal.

    Rules and their contributions
    -----------------------------

    PRE-FETCH (always evaluated):

      * ``tracker_host``      : 0.95 — URL's host (or a parent
                                domain) is on KNOWN_TRACKER_HOSTS.
      * ``pixel_path``        : 0.85 — URL path contains a
                                PIXEL_PATH_TOKENS substring.
      * ``empty_path_only_qs``: 0.70 — URL has no path component
                                (``/`` or empty) but a non-empty
                                query string. Classic cache-buster
                                tracking pixel shape (``?v=...``,
                                ``?utm_*=...``).
      * ``trap_url_term``     : 0.70 — URL path contains a
                                redirect/interstitial term
                                (TRAP_URL_TERMS: /click, /clk, /go,
                                /out, /redirect, /tracking, ...).
                                Below drop so a lone match downscores.
      * ``brand_trap_term``   : 0.55 — URL path contains a
                                brand-workflow term (BRAND_TRAP_TERMS:
                                subscribe, checkout, verify, ...).
                                WEAK signal — sits in the downscore
                                band only, never drops, so legitimate
                                membership/subscribe flows survive.

    POST-FETCH (require ``probe``):

      * ``media_but_html``    : 0.90 — Probe Content-Type is HTML
                                but the URL's extension is a media
                                extension (excluding manifest
                                extensions, which are text by
                                nature).
      * ``tiny_media_body``   : 0.80 — Probe Content-Length is
                                under TINY_BODY_THRESHOLD_BYTES
                                for a media URL (excluding manifest
                                extensions).
      * ``cookie_on_media``   : 0.40 — Probe sets cookies on what
                                should be a static media URL. Weak
                                signal alone — real CDNs sometimes
                                set CORS-related cookies.
      * ``host_class_redirect``: 0.75 — Probe shows a redirect from
                                a media URL to a different host
                                that is on KNOWN_TRACKER_HOSTS.

    The reason string lists rules in the order they fired.
    """
    if not isinstance(candidate, dict):
        return 0.0, ""
    url = candidate.get("url")
    if not isinstance(url, str) or not url:
        return 0.0, ""

    reasons: list = []
    max_score = 0.0

    # Parse once.
    try:
        parsed = urlparse(url)
    except Exception:
        return 0.0, ""

    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    query = parsed.query or ""

    # PRE-FETCH rules ────────────────────────────────────────────

    if _host_matches_tracker(host):
        reasons.append("tracker_host")
        max_score = max(max_score, 0.95)

    if _path_has_pixel_token(path):
        reasons.append("pixel_path")
        max_score = max(max_score, 0.85)

    if _is_empty_path_only_qs(path, query):
        reasons.append("empty_path_only_qs")
        max_score = max(max_score, 0.70)

    # F2: redirect/interstitial path terms — strong trap signal, scored
    # below the drop threshold so a lone match downscores, not drops.
    if _path_has_trap_term(path):
        reasons.append("trap_url_term")
        max_score = max(max_score, 0.70)

    # F2: brand-workflow terms (subscribe/checkout/verify/...) — WEAK
    # downscore signal only. Lands in the downscore band (>=0.5, <0.8)
    # so it deprioritises but never drops, preserving legitimate
    # membership/subscribe download flows.
    if _path_has_brand_trap_term(path):
        reasons.append("brand_trap_term")
        max_score = max(max_score, 0.55)

    # POST-FETCH rules ───────────────────────────────────────────
    # All require a probe dict.

    if isinstance(probe, dict):
        # Normalize headers to lowercase keys for case-insensitive lookup.
        headers = probe.get("headers") or {}
        if isinstance(headers, dict):
            lc_headers = {
                k.lower(): v for k, v in headers.items()
                if isinstance(k, str)
            }
        else:
            lc_headers = {}

        url_ext = _url_extension(path)

        # media_but_html: media extension, but server says HTML
        if (url_ext in MEDIA_EXTENSIONS
                and url_ext not in MANIFEST_EXTENSIONS):
            ct = (lc_headers.get("content-type") or "").lower()
            # Strip "; charset=..." suffix
            ct_base = ct.split(";", 1)[0].strip()
            if ct_base in HTML_CONTENT_TYPES:
                reasons.append("media_but_html")
                max_score = max(max_score, 0.90)

        # tiny_media_body: media extension, body under 1 KB
        if (url_ext in MEDIA_EXTENSIONS
                and url_ext not in MANIFEST_EXTENSIONS):
            cl = lc_headers.get("content-length")
            if cl is not None:
                try:
                    cl_int = int(cl)
                    if 0 < cl_int < TINY_BODY_THRESHOLD_BYTES:
                        reasons.append("tiny_media_body")
                        max_score = max(max_score, 0.80)
                except (ValueError, TypeError):
                    pass

        # cookie_on_media: Set-Cookie on a media URL (weak signal)
        if url_ext in MEDIA_EXTENSIONS:
            if "set-cookie" in lc_headers and lc_headers.get("set-cookie"):
                reasons.append("cookie_on_media")
                max_score = max(max_score, 0.40)

        # host_class_redirect: media URL redirects to a tracker host
        if url_ext in MEDIA_EXTENSIONS:
            redirected_to = probe.get("redirected_to")
            if isinstance(redirected_to, str) and redirected_to:
                try:
                    rd_host = (urlparse(redirected_to).hostname or "").lower()
                except Exception:
                    rd_host = ""
                if rd_host and rd_host != host:
                    if _host_matches_tracker(rd_host):
                        reasons.append("host_class_redirect")
                        max_score = max(max_score, 0.75)

    reason_str = ",".join(reasons)
    return max_score, reason_str


# ────────────────────────────────────────────────────────────────────
# Threshold helpers (used by the integration in provider_resolve)
# ────────────────────────────────────────────────────────────────────

# Three-zone threshold semantics from the spec (B3):
#   score >= 0.8  → DROP and log
#   0.5 <= score < 0.8 → DOWNSCORE the candidate but keep it
#   score < 0.5   → no action
#
# The DROP and DOWNSCORE thresholds are independently configurable so
# that an operator can move the boundaries (e.g., very aggressive:
# drop at 0.6 and downscore at 0.3). The defaults match the spec.

DEFAULT_DROP_THRESHOLD = 0.8
DEFAULT_DOWNSCORE_THRESHOLD = 0.5


def classify_score(
    score: float,
    *,
    drop_threshold: float = DEFAULT_DROP_THRESHOLD,
    downscore_threshold: float = DEFAULT_DOWNSCORE_THRESHOLD,
) -> str:
    """Map a honeypot score to an action.

    Returns one of ``"drop"``, ``"downscore"``, ``"keep"``.

    The thresholds are inclusive on the drop side, inclusive on the
    downscore lower bound. A score exactly at ``drop_threshold``
    drops; a score exactly at ``downscore_threshold`` downscores.

    If ``drop_threshold <= downscore_threshold`` the drop threshold
    wins for all scores at or above it; the downscore zone shrinks
    or disappears. This is intentional — operators who want to ONLY
    drop (no downscore zone) can set both thresholds equal.
    """
    if score >= drop_threshold:
        return "drop"
    if score >= downscore_threshold:
        return "downscore"
    return "keep"


# ────────────────────────────────────────────────────────────────────
# Internals
# ────────────────────────────────────────────────────────────────────

def _host_matches_tracker(host: str) -> bool:
    """Return True if host equals OR is a subdomain of any known
    tracker host. ``ads.doubleclick.net`` matches ``doubleclick.net``;
    ``mydoubleclick.net`` does not (subdomain boundary required)."""
    if not host:
        return False
    host = host.lower().strip(".")
    if host in KNOWN_TRACKER_HOSTS:
        return True
    for tracker in KNOWN_TRACKER_HOSTS:
        # Subdomain: foo.bar.tracker.com matches tracker.com
        if host.endswith("." + tracker):
            return True
    return False


def _path_has_pixel_token(path: str) -> bool:
    """Return True if the URL path contains a known pixel-route
    substring. Case-insensitive (path is already lowercased by
    score_candidate)."""
    if not path:
        return False
    for tok in PIXEL_PATH_TOKENS:
        if tok in path:
            return True
    return False


def _path_has_trap_term(path: str) -> bool:
    """Return True if the URL path contains a redirect/interstitial
    trap term (F2 TRAP_URL_TERMS). Case-insensitive; path is already
    lowercased by score_candidate."""
    if not path:
        return False
    for tok in TRAP_URL_TERMS:
        if tok in path:
            return True
    return False


def _path_has_brand_trap_term(path: str) -> bool:
    """Return True if the URL path contains a brand-workflow term
    (F2 BRAND_TRAP_TERMS — subscribe/checkout/verify/...). Weak signal:
    these appear in legitimate flows too, so the caller scores it only
    into the downscore band. Case-insensitive."""
    if not path:
        return False
    for tok in BRAND_TRAP_TERMS:
        if tok in path:
            return True
    return False


_QS_CACHE_BUSTER_RE = re.compile(
    r"^(v|t|ts|cb|cachebuster|utm_[a-z_]+|_)=",
    re.IGNORECASE,
)


def _is_empty_path_only_qs(path: str, query: str) -> bool:
    """Return True if the URL has no real path but does have a query
    string. The classic shape is ``https://t.example.com/?v=abc123``
    — a cache-buster-only tracking pixel. We're lenient: only fire
    if the path is exactly '' or '/' AND the query has at least one
    key that's a typical cache-buster or UTM param.

    The check on cache-buster keys (vs firing on ANY query) prevents
    false positives for legitimate "no path, query carries the lookup
    key" patterns like ``https://api.example.com/?id=abc``."""
    if path not in ("", "/"):
        return False
    if not query:
        return False
    # Parse query and check at least one key matches the cache-buster
    # pattern.
    try:
        qs = parse_qs(query, keep_blank_values=True)
    except Exception:
        return False
    for key in qs.keys():
        if _QS_CACHE_BUSTER_RE.match(key + "="):
            return True
    return False


def _url_extension(path: str) -> str:
    """Return the lowercased extension (including dot) of the URL
    path, or empty string if there isn't one. ``/foo/bar.mp4`` →
    ``.mp4``. ``/foo/bar`` → ``''``. Trailing slashes ignored."""
    if not path:
        return ""
    p = path.rstrip("/")
    last_slash = p.rfind("/")
    last_dot = p.rfind(".")
    if last_dot <= last_slash or last_dot == -1:
        return ""
    return p[last_dot:].lower()
