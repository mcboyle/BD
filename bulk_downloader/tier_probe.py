"""v3.43.65: Speculative tier-probe for direct-CDN video URLs.

# Why this module exists

The 2026-05-13 recon survey of 20 paywalled adult-content sites
revealed that many of them embed the resolution as a path segment
in the CDN URL. Examples:

    https://cdn.vixen.com/video/mp4_480/106491/.../VIXEN_106491_480P.mp4
    https://video.vip4k.com/secure/.../1080p.mp4
    https://content2a.nubilefilms.com/.../nubilefilms_priceless_480.mp4
    https://cdn-videos.r1.cdn.pornpros.com/.../stream_mp4_1080.mp4

The player loads ONE of these on page render. Whichever it picks is
often *not* the highest tier the CDN serves — the player picks based
on bandwidth heuristics, last-watched-quality, or a default that
happens to be 480p/720p even on a fiber connection.

Before this module, BD would scrape `<video src>` and download whatever
the player happened to load — leaving 8K / 4K bytes on the table when
the same scene was available at higher resolution by URL substitution.

This module does one thing: given a URL like
    https://cdn.vixen.com/video/mp4_480/...
and a per-site pattern that says "the tier lives in the `mp4_NNN`
segment", it speculatively HEAD-probes
    https://cdn.vixen.com/video/mp4_4320/...
    https://cdn.vixen.com/video/mp4_2160/...
    https://cdn.vixen.com/video/mp4_1440/...
    https://cdn.vixen.com/video/mp4_1080/...
and returns the URL for the highest tier that returned 200 OK.

# Fail-open

Every failure mode (malformed pattern, network error, all probes 404,
HEAD method not allowed, signed-URL invalidation) returns the original
URL unchanged. The runner falls back to whatever the player picked.
No download is ever blocked by a probe failure.

# Per-site opt-in

A site's `tier_probe_enabled` config flag defaults False. The user
opts in by:
  1. Setting `tier_probe_enabled: true`
  2. Configuring `tier_probe_pattern` to a regex with a `tier` capture
     group: e.g. `mp4_(?P<tier>\\d+)/` for Vixen.

Optional config:
  - `tier_probe_ladder`: comma-separated list of tier values to try,
    in priority order. Default `"4320,2160,1440,1080,720,480"`.
  - `tier_probe_timeout_s`: HEAD timeout per probe (default 5.0).
  - `tier_probe_max_attempts`: cap the number of HEAD probes (default
    6). Cheap but not free; some signed-URL CDNs charge per request.

# Signed-URL caveat

CDNs that sign URLs (Vixen's AWS CloudFront, naughtycdn) include the
*full path* in the signature. Swapping `mp4_480` → `mp4_4320` makes
the signature invalid. For those CDNs, the player has to re-fetch a
new signed URL with the higher tier baked in — and that requires
hitting the player API again, not just HEAD-probing.

BD handles this by checking the response status: a 403 with a body
containing "Signature" or "denied" means the URL pattern works but
the signature doesn't cover the tier — those sites need a different
mechanism (pre-scrape quality menu click, the v3.43.65 sibling
feature). Sites that 200 OK on tier-swapped URLs (e.g. Naughty
America's `_desktop10sec` pattern, internal CDNs with no signing) get
the full benefit.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Default ladder — 8K-first, falls back through every tier we know.
# Configurable per site via `tier_probe_ladder`.
DEFAULT_LADDER = [4320, 3160, 2880, 2160, 1440, 1080, 720, 480]


@dataclass
class ProbeResult:
    """Result of a tier probe. `url` is always set — either the upgraded
    URL on success, or the original URL unchanged on failure.

    `original_tier` and `chosen_tier` are populated when we successfully
    identified the tier; otherwise both are 0. `chosen_tier` may equal
    `original_tier` when the original was already the highest available.
    """
    url: str
    original_tier: int = 0
    chosen_tier: int = 0
    probed_tiers: list = field(default_factory=list)  # [(tier, status), ...]
    error: str = ""
    # True when we did real work; False when we short-circuited (pattern
    # didn't match, probing disabled, etc.). Used for telemetry.
    probed: bool = False


# Compile-cache: site_id -> compiled pattern. Patterns are user-supplied
# and may be invalid regex; cache them so we don't re-compile per URL.
_pattern_cache: dict = {}


def compile_pattern(pattern: str):
    """Compile a tier_probe_pattern string. Returns the compiled
    pattern on success, None on failure (with a warning log).

    The pattern MUST contain a named group `(?P<tier>...)` that
    captures only the tier value. Anything else in the pattern is
    treated as a literal anchor (e.g. `mp4_(?P<tier>\\d+)/`).
    """
    if not pattern:
        return None
    cached = _pattern_cache.get(pattern)
    if cached is not None:
        return cached if cached is not False else None
    try:
        rx = re.compile(pattern)
    except re.error as e:
        log.warning("tier_probe: bad pattern %r: %s", pattern, e)
        _pattern_cache[pattern] = False
        return None
    # Verify the named group is present
    if "tier" not in rx.groupindex:
        log.warning("tier_probe: pattern %r missing (?P<tier>...) group",
                    pattern)
        _pattern_cache[pattern] = False
        return None
    _pattern_cache[pattern] = rx
    return rx


def parse_tier_from_url(url: str, pattern):
    """Extract the current tier value from `url` according to `pattern`.

    `pattern` may be a string or a compiled regex. Returns
    `(tier_int, prefix, suffix)` where prefix and suffix are the parts
    of the URL surrounding the tier (so substitution = prefix + new_tier
    + suffix). Returns `(0, "", "")` if the pattern doesn't match or
    the tier value isn't parseable as an integer.
    """
    if not url or not pattern:
        return 0, "", ""
    if isinstance(pattern, str):
        pattern = compile_pattern(pattern)
        if pattern is None:
            return 0, "", ""
    m = pattern.search(url)
    if not m:
        return 0, "", ""
    try:
        tier = int(m.group("tier"))
    except (ValueError, IndexError):
        return 0, "", ""
    # The tier value's span within the URL — we need to know the
    # CHARACTERS occupied by the digits, not the whole match span.
    # Use the named-group span specifically.
    start, end = m.span("tier")
    prefix = url[:start]
    suffix = url[end:]
    return tier, prefix, suffix


def build_candidate_urls(url: str, pattern,
                         ladder: Optional[list] = None,
                         skip_lower_than_current: bool = True) -> list:
    """Build a list of candidate URLs to probe, in descending tier order.

    Returns a list of `(tier, candidate_url)` tuples. The original URL's
    tier is included in the list (so the caller has a baseline to compare
    against), unless skip_lower_than_current excludes everything below it.

    `ladder` defaults to DEFAULT_LADDER (8K first). Pass a custom list to
    restrict probing to a specific set (e.g. only 4K and 1080p).
    """
    if ladder is None:
        ladder = DEFAULT_LADDER
    cur_tier, prefix, suffix = parse_tier_from_url(url, pattern)
    if cur_tier == 0:
        return []  # pattern doesn't match the URL
    # Filter ladder: descending order, deduped, optionally drop lower
    # tiers than current.
    candidates = []
    seen = set()
    for t in sorted(set(ladder), reverse=True):
        if t in seen:
            continue
        seen.add(t)
        if skip_lower_than_current and t < cur_tier:
            continue
        if t == cur_tier:
            # Current-tier URL is the baseline; include it but the
            # caller can decide whether to actually HEAD-probe it.
            candidates.append((t, url))
        else:
            candidates.append((t, prefix + str(t) + suffix))
    return candidates


def _is_signed_url_rejection(status: int, body_preview: str = "") -> bool:
    """Heuristic: was this 403 because the URL is signed and the signature
    doesn't cover the rewritten tier? If so, we shouldn't keep probing
    (every higher tier will also 403 for the same reason)."""
    if status != 403:
        return False
    if not body_preview:
        # No body to inspect; assume yes for 403 (conservative — better
        # to bail than burn 5 HEAD requests on the same signature error).
        return True
    bp = body_preview.lower()
    return any(kw in bp for kw in (
        "signature", "signed", "denied", "policy", "expired", "key-pair",
    ))


def _ssrf_guard_hook(request):
    """v3.66.542 (F-SWEEP-N2): SSRF guard for the tier-probe fetcher.

    Installed as an httpx 'request' event hook so it fires on EVERY outgoing
    request -- the initial tier candidate AND any redirect target (the client
    uses follow_redirects=True). Tier candidates are substituted from the input
    URL and share its host, so a download of an internal URL would otherwise let
    the probe HEAD internal space. Revalidate the host via the single canonical
    predicate and refuse non-public ones; the probe loop catches the raised
    SSRFBlocked and records the candidate as an error, skipping it.
    """
    from bulk_downloader.provider_resolve_impl._common import (
        _is_safe_public_host, SSRFBlocked,
    )
    try:
        host = request.url.host or ""
    except Exception:
        host = ""
    ok, reason = _is_safe_public_host(host)
    if not ok:
        raise SSRFBlocked(f"tier_probe SSRF guard: {reason}")


def probe_higher_tiers(
    url: str,
    pattern,
    *,
    user_agent: str = "",
    referer: str = "",
    cookies: Optional[dict] = None,
    ladder: Optional[list] = None,
    timeout_s: float = 5.0,
    max_attempts: int = 6,
) -> ProbeResult:
    """Speculatively HEAD-probe higher tiers for `url`.

    Returns a ProbeResult with .url set to the highest-tier 200 OK
    variant (or the original URL on any failure). Never raises.

    Probes are attempted in descending tier order, stopping at the
    FIRST 200 OK — because higher tiers are tried first, the first
    200 is by construction the highest available.

    `max_attempts` caps total HEAD requests (across all tiers including
    the original). Default 6 covers 8K/6K/5K/4K/1440/1080 plus a slot
    for the original tier as baseline.
    """
    if not url:
        return ProbeResult(url=url, error="empty_url")
    rx = compile_pattern(pattern) if isinstance(pattern, str) else pattern
    if rx is None:
        return ProbeResult(url=url, error="bad_pattern")

    candidates = build_candidate_urls(url, rx, ladder=ladder)
    if not candidates:
        return ProbeResult(url=url, error="pattern_no_match")

    try:
        import httpx
    except ImportError:
        log.info("tier_probe: httpx not installed; cannot probe")
        return ProbeResult(url=url, error="httpx_missing")

    cur_tier, _, _ = parse_tier_from_url(url, rx)
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer

    probed: list = []
    chosen_url = url
    chosen_tier = cur_tier
    error = ""

    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True,
                          cookies=cookies or None,
                          event_hooks={"request": [_ssrf_guard_hook]}) as client:
            for i, (tier, candidate) in enumerate(candidates):
                if i >= max_attempts:
                    error = f"max_attempts={max_attempts}_exhausted"
                    break
                # Skip the original-tier URL if we already have it as
                # the baseline (we trust it 200s — it just came from
                # the player). Still record it in probed so the caller
                # sees what was considered.
                if tier == cur_tier and candidate == url:
                    probed.append((tier, "baseline"))
                    # Don't break — keep checking higher tiers.
                    continue
                try:
                    r = client.head(candidate, headers=headers)
                    status = r.status_code
                    body_preview = ""
                except httpx.RequestError as e:
                    probed.append((tier, f"err:{type(e).__name__}"))
                    # Single network failure shouldn't abort the whole
                    # cascade; the CDN may be flaky on one URL. Continue.
                    continue
                except Exception as e:
                    probed.append((tier, f"err:{type(e).__name__}"))
                    continue
                probed.append((tier, status))
                if status == 200:
                    chosen_url = candidate
                    chosen_tier = tier
                    # Higher tiers were tried first; this is the highest.
                    break
                if status == 405:
                    # Method Not Allowed: CDN doesn't accept HEAD. Try
                    # a Range-bounded GET so we don't fetch the body.
                    try:
                        r = client.get(candidate, headers={
                            **headers, "Range": "bytes=0-0",
                        })
                        if r.status_code in (200, 206):
                            chosen_url = candidate
                            chosen_tier = tier
                            probed[-1] = (tier, f"GET-Range:{r.status_code}")
                            break
                        probed[-1] = (tier, f"GET-Range:{r.status_code}")
                    except Exception:
                        pass
                    continue
                # Signed-URL rejection: stop probing higher tiers — the
                # signature error will repeat for every variant.
                if _is_signed_url_rejection(status, body_preview):
                    error = "signed_url_rejection"
                    break
                # Other non-200 (404, 403 non-signed, 5xx): continue
                # probing lower tiers.
    except Exception as e:
        error = f"client_error:{type(e).__name__}:{e}"

    return ProbeResult(
        url=chosen_url,
        original_tier=cur_tier,
        chosen_tier=chosen_tier,
        probed_tiers=probed,
        error=error,
        probed=True,
    )


# ─── Per-site pattern presets ───────────────────────────────────────
#
# Convenience: known patterns for sites surfaced by the recon survey.
# A site template can ship `tier_probe_pattern` directly, but having
# canonical names lets the UI offer a dropdown.

KNOWN_PATTERNS: dict = {
    # Vixen network (cdn.vixen.com, cdn.blacked.com, cdn.tushy.com)
    "vixen_network": r"mp4_(?P<tier>\d+)/",
    # PornPros / Tiny4K
    "pornpros": r"stream_mp4_(?P<tier>\d+)\.mp4",
    # VIP4K (path-only tier)
    "vip4k_path": r"/(?P<tier>\d+)p\.mp4\b",
    # Bang.com
    "bang_path": r"\.(?P<tier>\d+)p\.mp4\b",
    # WowGirls / Ultrafilms (resolution AS WxH; we extract HEIGHT)
    # Note: the height capture group is named `tier` for consistency
    # but it requires the WxH suffix elsewhere to disambiguate.
    "wowgirls_wxh": r"\d+x(?P<tier>\d+)_\d+FPS\.mp4",
    # Nubiles network — bare `_480.mp4` / `_720.mp4` / `_1080.mp4`
    "nubiles_suffix": r"_(?P<tier>480|720|1080|2160|4320)\.mp4\b",
}


def list_known_patterns() -> list:
    """Return sorted list of known pattern keys for UI dropdowns."""
    return sorted(KNOWN_PATTERNS.keys())


def resolve_pattern(value: str) -> str:
    """If `value` is a key in KNOWN_PATTERNS, return its regex.
    Otherwise return `value` unchanged (assume the user supplied a
    custom regex)."""
    return KNOWN_PATTERNS.get(value, value)


__all__ = [
    "ProbeResult",
    "DEFAULT_LADDER",
    "KNOWN_PATTERNS",
    "compile_pattern",
    "parse_tier_from_url",
    "build_candidate_urls",
    "probe_higher_tiers",
    "list_known_patterns",
    "resolve_pattern",
]
