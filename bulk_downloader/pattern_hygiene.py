"""Network-pattern hygiene for reviewed/draft templates.

A capture records every network URL it sees, so a freshly built draft's
``network_patterns`` is polluted with analytics / ad / error-telemetry beacons
(Sentry, GTM, Google Analytics, tsyndicate, …) and occasionally with non-URL
junk (an inlined HTML document, a JS snippet, a JSON blob). None of that
belongs in a template the runtime consults; it is noise at best and, once a
draft is promoted to an *enabled* reviewed template, it ships that noise.

``scrub_network_patterns`` drops:
  * non-URL junk — anything that isn't ``http(s)://…`` / ``//…`` / ``/…``
    (HTML/JS/JSON blobs, bare words, anything with whitespace),
  * patterns whose host matches a known analytics / advertising / telemetry
    provider,
  * patterns carrying signed / credential query material — AWS SigV4
    (``X-Amz-*``), CloudFront (``Policy`` / ``Signature`` / ``Key-Pair-Id`` /
    ``Expires``), and bearer/token/auth/sig/credential query keys. The rich
    builder already strips query strings, but old flat drafts can carry a full
    signed media URL, and such a URL must never land in a template.

It KEEPS real asset/media patterns (CDNs, the site's own host, relative paths).
Signed-URL rejection here drops the pattern entirely; that is distinct from
``dry_run._redact_patterns``, which *masks* query values for display.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

# Single authoritative blocked-term list (shared with the promote gate + the
# inventory diagnostic). Dropping any pattern that contains a blocked term makes
# this scrubber a SUPERSET of the gate: a kept pattern can never later be
# rejected by promote on blocked-term grounds. This is what reconciles
# cdnjs/cloudflare — kept here historically, but blocked by the gate.
from .bad_terms import first_bad_term

# Host substrings (case-insensitive) for analytics / ads / error-telemetry.
# Matched against the pattern's host, so a substring like "sentry.io" catches
# "o851585.ingest.sentry.io". Deliberately conservative — asset/media CDNs and
# support widgets are NOT listed, so legitimate download hints survive.
_TRACKER_HOSTS: Tuple[str, ...] = (
    "sentry.io",
    "google-analytics.com",
    "googletagmanager.com",
    "googlesyndication.com",
    "doubleclick.net",
    "adservice.google",
    "clarity.ms",
    "tsyndicate.com",
    "psmcode.com",
    "scorecardresearch.com",
    "quantserve.com",
    "segment.io",
    "segment.com",
    "mixpanel.com",
    "amplitude.com",
    "hotjar.com",
    "fullstory.com",
    "mouseflow.com",
    "connect.facebook.net",
    "criteo.com",
    "taboola.com",
    "outbrain.com",
    "bat.bing.com",
    "matomo.cloud",
    "plausible.io",
    "heap.io",
    "snowplowanalytics.com",
    "getbeamer.com",
)

_URLISH_RE = re.compile(r"^(https?://|//|/)")

# Query-parameter keys (lowercased) that mark a credentialed / signed URL.
_SIGNED_QUERY_KEYS: Tuple[str, ...] = (
    "policy", "key-pair-id", "keypairid", "signature", "expires",
    "token", "auth", "bearer", "sig", "credential", "credentials",
    "awsaccesskeyid", "x-goog-signature", "x-goog-credential", "email",
)

# A JWT (eyJ… header) or a long opaque base64/base64url token. These show up as
# Cloudflare Stream JWTs in the path, ?p=<jwt>&s=<sig> query blobs, and cachefly
# base64 path segments that decode to "dirmatch=true;expiretime=…". None belong
# in a stored template; the rich builder strips them, this catches flat drafts.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{15,}")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_=+-]{40,}$")

# First-party but non-media endpoints captured alongside the real media calls
# (offers, subscriptions, experiments, banners, telemetry, social). Dropped so a
# review candidate carries media/watch/download patterns only.
_NOISE_PATH: Tuple[str, ...] = (
    "/exclusive-offers", "/active-subscriptions", "/experiments", "/banners",
    "/event-log", "/comments", "/votes", "/tags",
)


def _has_signed_blob(pattern: str) -> bool:
    s = str(pattern)
    if _JWT_RE.search(s):
        return True
    for seg in re.split(r"[/?&;=]", s):
        if len(seg) >= 40 and _OPAQUE_RE.match(seg) and re.search(r"[0-9_=+-]", seg):
            return True  # a single long opaque token segment (signed blob)
    return False


def _is_noise_path(pattern: str) -> bool:
    path = re.sub(r"^https?://[^/]+", "", str(pattern).split("?", 1)[0]).lower()
    return any(n in path for n in _NOISE_PATH)


def _has_signed_query(pattern: str) -> bool:
    """True if the pattern carries signed/credential query material.

    AWS SigV4 ``X-Amz-*`` and CloudFront ``Key-Pair-Id`` markers are matched
    anywhere (they are distinctive). Everything else is matched only as a
    query-string *key*, so legitimate path segments like ``/auth/login`` or
    ``/token/refresh`` are not flagged.
    """
    s = str(pattern)
    low = s.lower()
    if "x-amz-" in low or "key-pair-id" in low or "awsaccesskeyid" in low:
        return True
    if "?" not in s:
        return False
    query = s.split("?", 1)[1]
    for part in re.split(r"[&;]", query):
        key = part.split("=", 1)[0].strip().lower()
        if key in _SIGNED_QUERY_KEYS:
            return True
    return False


def _host_of(pattern: str) -> str:
    """Best-effort host for a URL-ish pattern. Relative ``/path`` → ""."""
    if pattern.startswith("//"):
        pattern = "https:" + pattern
    if pattern.startswith("/"):
        return ""  # same-site relative path — no host to classify
    try:
        return (urlparse(pattern).hostname or "").lower()
    except Exception:
        return ""


def _is_junk(pattern: str) -> bool:
    """True for anything that isn't a clean URL/path pattern."""
    if not pattern:
        return True
    # Real network patterns are single tokens; whitespace/newlines mean an
    # inlined HTML/JS/JSON blob got captured as a "pattern".
    if any(c.isspace() for c in pattern):
        return True
    if pattern[0] in "<{":  # HTML document / JSON object
        return True
    return not _URLISH_RE.match(pattern)


def _is_tracker(host: str) -> bool:
    return any(t in host for t in _TRACKER_HOSTS)


def scrub_network_patterns(patterns: Any) -> Dict[str, List[str]]:
    """Return ``{"kept": [...], "dropped": [...]}`` from a patterns list.

    Order within ``kept`` is preserved. Duplicates are de-duplicated within
    ``kept`` while keeping first occurrence. Non-list input → empty result.
    """
    kept: List[str] = []
    dropped: List[str] = []
    seen = set()
    if not isinstance(patterns, (list, tuple)):
        return {"kept": [], "dropped": []}
    for raw in patterns:
        p = str(raw).strip()
        if (_is_junk(p) or _is_tracker(_host_of(p)) or _has_signed_query(p)
                or _has_signed_blob(p) or _is_noise_path(p)
                or first_bad_term(p)):  # gate parity: drop anything promote rejects
            dropped.append(str(raw))
            continue
        if p in seen:
            continue
        seen.add(p)
        kept.append(p)
    return {"kept": kept, "dropped": dropped}
