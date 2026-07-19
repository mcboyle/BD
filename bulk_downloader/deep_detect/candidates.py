from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

from ._common import (PROGRESSIVE_MEDIA_EXTENSIONS, STREAM_MANIFEST_EXTENSIONS, URL_BEARING_ATTRS, _SIGNED_URL_SHORT_TTL_THRESHOLD, _count_by_type, _score_to_confidence, _url_host, _url_path)
from .resolution import (classify_resolution, detect_resolution_from_text, parse_codec)
from .urls import (canonicalize_url, classify_url, decode_url, detect_signed_url)


DOWNLOAD_TERMS = (
    "download", "dl", "direct", "mirror", "asset",
    "release", "latest", "stable", "installer",
    "setup", "offline", "standalone", "portable",
    "export", "backup", "save", "attachment",
    "media", "source", "binary", "artifact",
)


BAD_DOWNLOAD_TERMS = (
    "login", "signin", "register", "checkout",
    "cart", "subscribe", "pricing", "premium",
    "ads", "adserver", "doubleclick", "analytics",
    "tracking", "utm_", "share", "embed",
    "thumbnail", "preview", "sample", "decoy",
)


AD_TRACKER_HOSTS = (
    "doubleclick.net", "googlesyndication.com",
    "googleadservices.com", "adservice.google",
    "ads.youtube.com", "adsystem.com",
    "amazon-adsystem.com", "criteo.com",
    "adnxs.com", "adsrvr.org",
    "outbrain.com", "taboola.com",
    "scorecardresearch.com", "googletagmanager.com",
    "googletagservices.com", "google-analytics.com",
    "analytics.tiktok.com", "pixel.facebook.com",
    "connect.facebook.net", "hotjar.com",
    "mixpanel.com", "segment.io", "amplitude.com",
    "matomo.cloud", "track.adform.net",
)


AD_TRACKER_PATH_FRAGMENTS = (
    "facebook.com/tr",
    "bing.com/action",
)


TRACKER_PATH_PATTERNS = (
    "/out?", "/go?", "/click?", "/clk?",
    "/redirect?", "/redir?",
    "/track?", "/tracker", "/pixel?",
    "/affiliate", "/sponsor", "/promo?",
    "/popup", "/interstitial",
    "utm_source=", "utm_medium=",
    "&aff=", "?aff=",
    "/r/?", "/r?",
)


LINK_VISIBILITY_TRAPS = (
    "display:none",
    "visibility:hidden",
    "opacity:0",
    "pointer-events:none",
    "left:-9999", "left:-10000",
    "top:-9999", "top:-10000",
    "height:0", "width:0",
    "height:1px", "width:1px",
)


def score_download_link(el, *, base_url: str = "") -> dict:
    """Penalty-aware scoring for an anchor/button candidate. Returns:

        {
            "url":      str | None,
            "score":    int,           # negative = trap, ≥ 0 = OK
            "penalties":[(reason, delta), ...],
            "bonuses":  [(reason, delta), ...],
            "rejected": bool,          # score < 0
            "reasons":  [str, ...],    # flat for UI
        }

    The signs are intentional: a single trap signal can outweigh many
    weak positives, so a -90 ad/tracker host beats any combination of
    "has download text" + "has file extension"."""
    out = {
        "url": None,
        "score": 0,
        "penalties": [],
        "bonuses": [],
        "rejected": False,
        "reasons": [],
    }
    if el is None:
        return out

    # URL resolution — same logic as the resolution-card extractor.
    href = ""
    for attr in URL_BEARING_ATTRS:
        v = el.get(attr) if hasattr(el, "get") else None
        if v and v.strip():
            href = v.strip()
            break
    raw = href or ""
    if not raw and hasattr(el, "get"):
        onclick = el.get("onclick") or ""
        m = re.search(
            r"""['"](https?://[^'"]+|/[^'"\s]+)['"]""",
            onclick,
        )
        if m:
            raw = m.group(1)
    resolved = ""
    if raw and raw not in ("#", "javascript:;",
                            "javascript:void(0)") \
            and not raw.startswith("javascript:") \
            and not raw.startswith("mailto:"):
        resolved = decode_url(raw, base_url=base_url)
    out["url"] = resolved or None

    # ── Negative signals (traps) ────────────────────────────────────
    if not raw or raw in ("#", "javascript:;", "javascript:void(0)"):
        out["penalties"].append(("empty/anchor href", -100))
    elif raw.startswith("javascript:"):
        out["penalties"].append(("javascript: href", -100))
    elif raw.startswith("mailto:"):
        out["penalties"].append(("mailto: link", -100))

    if resolved:
        host = _url_host(resolved)
        path = _url_path(resolved)
        lower = resolved.lower()
        for bad_host in AD_TRACKER_HOSTS:
            if bad_host in host:   # P6-A: host only — not the whole URL
                out["penalties"].append(
                    (f"ad/tracker host: {bad_host}", -90))
                break
        else:
            for bad_frag in AD_TRACKER_PATH_FRAGMENTS:
                if bad_frag in lower:   # P6-A: host+path endpoints
                    out["penalties"].append(
                        (f"ad/tracker host: {bad_frag}", -90))
                    break
        for pat in TRACKER_PATH_PATTERNS:
            if pat in lower:
                out["penalties"].append(
                    (f"tracker URL pattern: {pat}", -25))
                break
        for term in BAD_DOWNLOAD_TERMS:
            if term in lower and len(term) >= 4:
                out["penalties"].append(
                    (f"bad-download term in URL: {term}", -10))
                break

    # Visibility traps — apply to elements with hidden/off-screen
    # styling or aria-hidden.
    style = ""
    aria_hidden = ""
    tabindex = ""
    if hasattr(el, "get"):
        style = (el.get("style") or "").lower().replace(" ", "")
        aria_hidden = (el.get("aria-hidden") or "").lower()
        tabindex = el.get("tabindex") or ""
    for pat in LINK_VISIBILITY_TRAPS:
        if pat in style:
            out["penalties"].append(
                (f"hidden/off-screen via style: {pat}", -100))
            break
    if aria_hidden == "true":
        out["penalties"].append(('aria-hidden="true"', -40))
    if tabindex == "-1":
        out["penalties"].append(("tabindex=-1", -30))

    # Mismatched text/URL — link says "Download" but URL has no file
    # extension AND points to a sketchy path.
    text = ""
    if hasattr(el, "get_text"):
        text = el.get_text(" ", strip=True).lower()
    text_says_download = any(t in text for t in DOWNLOAD_TERMS)
    has_file_ext = (
        resolved and
        any(_url_path(resolved).endswith(ext)
            for ext in PROGRESSIVE_MEDIA_EXTENSIONS
                       + STREAM_MANIFEST_EXTENSIONS))
    has_dl_attr = (
        hasattr(el, "has_attr") and el.has_attr("download")
    ) if hasattr(el, "has_attr") else False
    if text_says_download and not has_file_ext and not has_dl_attr:
        if resolved and any(p in resolved.lower()
                            for p in TRACKER_PATH_PATTERNS):
            out["penalties"].append(
                ("text says download but URL is a tracker", -25))

    # ── Positive signals ────────────────────────────────────────────
    if has_file_ext:
        out["bonuses"].append(("file extension on URL", +50))
    if has_dl_attr:
        out["bonuses"].append(("HTML download attribute", +30))
    if text_says_download:
        out["bonuses"].append(("download vocabulary in text", +20))
    # Same-host preference — a CDN on the same registered domain as
    # the page is more trustworthy than a third-party host.
    if resolved and base_url:
        try:
            page_host = _url_host(base_url)
            link_host = _url_host(resolved)
            if page_host and link_host and (
                    page_host == link_host
                    or link_host.endswith("." + page_host)
                    or page_host.endswith("." + link_host)):
                out["bonuses"].append(("same-domain link", +15))
        except Exception:
            pass

    # Final tally and rejection.
    out["score"] = sum(p[1] for p in out["penalties"]) + \
        sum(b[1] for b in out["bonuses"])
    out["rejected"] = out["score"] < 0
    out["reasons"] = [r for r, _ in out["penalties"] + out["bonuses"]]
    return out


def _attach_confidence(candidates: list[dict]) -> list[dict]:
    """Decorate each candidate with a `confidence` float derived from
    its `score`. Mutates in place and returns the same list for
    chaining."""
    for c in candidates:
        c["confidence"] = _score_to_confidence(
            int(c.get("score") or 0), c.get("source_type"))
    return candidates


_CEILINGS = {
    # signal_key:  (ceiling, reason_message)
    "drm":         (0.30, "DRM/encryption gate: bytes unreachable "
                          "without decryption keys"),
    "captcha":     (0.60, "CAPTCHA in flow: requires human action "
                          "before the URL is reachable"),
    "signed_url":  (0.75, "signed URL with limited TTL: confidence "
                          "applies at detection time, not download time"),
    "trap":        (0.20, "URL matches a trap-link signature: page "
                          "showed it as a decoy"),
    "needs_provider": (0.55, "needs provider-API resolution to materialize "
                              "the real media URL"),
    "needs_workflow": (0.55, "needs a multi-step workflow before the "
                              "URL is materialized"),
}


def _detect_ceiling_signals(c: dict) -> list[str]:
    """Inspect a single candidate and return a list of signal keys
    (matching `_CEILINGS`) that should cap its confidence."""
    signals: list[str] = []

    own_warnings_text = " ".join(c.get("warnings") or []).lower()
    if any(t in own_warnings_text
           for t in ("encryption", "encrypted", "drm",
                     "contentprotection")):
        signals.append("drm")
    if "captcha" in own_warnings_text:
        signals.append("captcha")
    if "trap-link" in own_warnings_text:
        signals.append("trap")

    if c.get("signed_url"):
        signals.append("signed_url")
    if c.get("needs_provider_resolution"):
        signals.append("needs_provider")
    if c.get("needs_workflow"):
        signals.append("needs_workflow")

    return signals


def _attach_confidence_ceiling(candidates: list[dict]) -> list[dict]:
    """Decorate each candidate with `confidence_ceiling` (float in
    [0.0, 1.0]) and `ceiling_reasons` (list of message strings
    describing what's capping it).

    Default: ceiling=1.0, reasons=[]. Lowered by any signal in
    `_CEILINGS`. The ceiling is the MINIMUM across all applicable
    signals — multiple caps stack to the strictest one.

    Mutates in place; returns the same list for chaining."""
    for c in candidates:
        signals = _detect_ceiling_signals(c)
        if not signals:
            c["confidence_ceiling"] = 1.0
            c["ceiling_reasons"] = []
            continue
        # Take the strictest applicable ceiling.
        ceiling = min(_CEILINGS[s][0] for s in signals)
        c["confidence_ceiling"] = ceiling
        c["ceiling_reasons"] = [_CEILINGS[s][1] for s in signals]
    return candidates


_DISCLAIMER_RULES = [
    # v3.66.15 (P12): CSP / mixed-content awareness
    ("csp_violation", "csp_violation", "warn"),
    ("mixed_content", "mixed_content", "warn"),
    # Specific DRM / encryption variants (severity depends on certainty)
    ("page-level drm", "drm", "warn"),
    ("hls media playlist is encrypted", "drm", "error"),
    ("hls encryption", "drm", "error"),
    ("contentprotection", "drm", "error"),
    # CAPTCHA
    ("page has captcha", "captcha", "warn"),
    ("captcha", "captcha", "warn"),
    # Trap / honeypot
    ("trap-link", "trap", "warn"),
    ("trap link", "trap", "warn"),
    ("honeypot", "honeypot", "warn"),
    # Signed URL: expired is stronger than the generic warning
    ("expired", "signed_url", "error"),
    ("signed url", "signed_url", "warn"),
    # Parse errors
    ("mpd xml parse", "parse_error", "warn"),
    ("xml parse failed", "parse_error", "warn"),
    # Bot defense
    ("bot defense", "bot_defense", "warn"),
    # Submit / download advisory
    ("do not auto-submit", "do_not_submit", "warn"),
    ("do not download", "do_not_download", "error"),
    # Preference fallback (P14)
    ("prefer_resolution", "prefer_fallback", "info"),
    # Authenticated-view awareness (P13)
    ("cookie", "auth_view", "info"),
    # Generic DRM / encryption catch-alls (must come LAST so the
    # page-level + HLS-specific variants above get first crack)
    ("encryption", "drm", "error"),
    ("encrypted",  "drm", "error"),
    ("drm",        "drm", "error"),
]


def _classify_disclaimer(message: str) -> dict:
    """Map a free-text warning string onto a structured
    `{type, severity, message}` entry. Falls through to
    `unclassified`/`info` if no rule matches — nothing is lost."""
    text = (message or "").lower()
    for needle, dtype, severity in _DISCLAIMER_RULES:
        if needle in text:
            return {
                "type": dtype,
                "severity": severity,
                "message": message,
            }
    return {
        "type": "unclassified",
        "severity": "info",
        "message": message,
    }


def _build_disclaimers(report: dict) -> list[dict]:
    """Walk the report's `warnings` list and return a structured
    `disclaimers` list. Each warning string becomes one disclaimer
    entry. Order is preserved.

    Idempotent: if `disclaimers` already exists in the report (e.g.
    a future caller populated it explicitly), the function still
    rebuilds from `warnings` — the caller can decide whether to
    merge or override. We never silently drop entries."""
    warnings = ((report.get("buckets") or {}).get("warnings")
                or report.get("warnings") or [])
    return [_classify_disclaimer(w) for w in warnings]


def _flatten_download_candidates(
        *,
        resolution_cards: List[dict],
        hls_master: Optional[dict],
        dash_mpd: Optional[dict],
        state_urls: List[dict],
        provider_embeds: List[dict],
        player_configs: List[dict],
        jsonld_media: List[dict],
        post_reveal: List[dict],
        base_url: str = "",
) -> List[dict]:
    """Merge every download surface into one flat list with a shared
    schema. Each candidate gets:

        {
            "url":         str | None,
            "source_type": one of SOURCE_TYPES,
            "score":       int,
            "resolution":  {label, rank} | None,
            "codec":       str | None,
            "fps":         float | None,
            "size_bytes":  int | None,
            "found_in":    short string describing where we found it,
            "reasons":     [...],
            "warnings":    [...],
        }

    Order in the final list is set by the caller (deep_detect) after
    cross-cutting score adjustments. Here we just normalize."""
    flat: List[dict] = []

    # Resolution cards — already in the right shape from extract_resolution_cards.
    for c in resolution_cards:
        flat.append({
            **c,
            "found_in": "resolution_card",
        })

    # HLS master variants — top variant becomes a high-priority candidate.
    if hls_master and hls_master.get("kind") == "hls_master":
        # Use identity (`is`) to find the "top" variant rather than
        # dict equality — two variants with identical attributes are
        # equal-by-value, which would mis-tag both as `hls_manifest`
        # source_type and could confuse downstream callers.
        top_variant = (hls_master["variants"][0]
                       if hls_master.get("variants") else None)
        for v in hls_master.get("variants") or []:
            res = v.get("resolution") or {}
            score = 90 + ((res.get("rank") or 0) // 100)
            if hls_master.get("drm_or_encryption_detected"):
                score -= 80
            flat.append({
                "url": v.get("url"),
                "source_type": ("hls_manifest" if v is top_variant
                                else "hls_variant"),
                "score": score,
                "resolution": res or None,
                "codec": (parse_codec(v.get("codecs") or "")
                          if v.get("codecs") else None),
                "fps": v.get("frame_rate"),
                "size_bytes": None,
                "bandwidth": v.get("bandwidth"),
                "found_in": "hls_master_playlist",
                "reasons": [
                    f"HLS variant {res.get('label') or '?'} "
                    f"@ {v.get('bandwidth') or 0} bps"],
                "warnings": (
                    ["HLS encryption detected"]
                    if hls_master.get("drm_or_encryption_detected")
                    else []),
                "requires_click": False,
            })

    # DASH representations.
    if dash_mpd and dash_mpd.get("kind") == "dash_mpd":
        for v in dash_mpd.get("video") or []:
            res = v.get("resolution") or {}
            score = 90 + ((res.get("rank") or 0) // 100)
            if dash_mpd.get("drm_or_encryption_detected"):
                score -= 80
            flat.append({
                "url": None,  # DASH representations are referenced by id,
                              # not direct URL (BaseURL + SegmentTemplate)
                "source_type": "dash_manifest",
                "score": score,
                "resolution": res or None,
                "codec": (parse_codec(v.get("codecs") or "")
                          if v.get("codecs") else None),
                "fps": None,
                "size_bytes": None,
                "bandwidth": v.get("bandwidth"),
                "dash_representation_id": v.get("id"),
                "found_in": "dash_mpd",
                "reasons": [
                    f"DASH Representation id={v.get('id')} "
                    f"@ {v.get('bandwidth') or 0} bps"],
                "warnings": (
                    ["DASH ContentProtection detected"]
                    if dash_mpd.get("drm_or_encryption_detected")
                    else []),
                "requires_click": False,
            })

    # State blob URLs (no resolution info from JSON walk alone — the
    # URL itself sometimes carries it; reuse detect_resolution_from_text).
    for s in state_urls:
        url = s.get("url")
        if not url:
            continue
        res = detect_resolution_from_text(url) or \
            detect_resolution_from_text(str(s.get("key") or ""))
        cls = classify_url(url)
        score = 70
        if res:
            score += (res.get("rank") or 0) // 100
        flat.append({
            "url": url,
            "source_type": cls.get("type") or "json_state_blob",
            "score": score,
            "resolution": res,
            "codec": None,
            "fps": None,
            "size_bytes": None,
            "found_in": f"state_blob:{s.get('source')}",
            "reasons": [
                f"URL found in state blob under key {s.get('key')}"],
            "warnings": [],
            "requires_click": False,
        })

    # Provider embeds — present them as candidates with explicit
    # "needs provider resolution" flag.
    for e in provider_embeds:
        flat.append({
            "url": e.get("url"),
            "source_type": e.get("source_type"),
            "score": 60,
            "resolution": None,
            "codec": None,
            "fps": None,
            "size_bytes": None,
            "provider_ids": e.get("ids"),
            "needs_provider_resolution": True,
            "found_in": f"provider:{e.get('provider')}",
            "reasons": [
                f"provider {e.get('provider')} embed; "
                "use provider API to resolve playable URLs"],
            "warnings": [],
            "requires_click": False,
        })

    # Player configs (Video.js / JWPlayer / Plyr / etc.)
    for p in player_configs:
        res = p.get("quality")
        score = 75
        if res:
            score += (res.get("rank") or 0) // 100
        flat.append({
            "url": p.get("url"),
            "source_type": p.get("source_type") or "unknown",
            "score": score,
            "resolution": res,
            "codec": p.get("codec"),
            "fps": None,
            "size_bytes": None,
            "found_in": f"player:{p.get('library')}",
            "reasons": [
                f"{p.get('library')} player config "
                f"({p.get('found_in')})"],
            "warnings": [],
            "requires_click": False,
        })

    # JSON-LD media objects.
    for j in jsonld_media:
        url = j.get("content_url") or j.get("embed_url")
        if not url:
            continue
        score = 65
        if j.get("width") and j.get("height"):
            try:
                cls = classify_resolution(int(j["width"]),
                                          int(j["height"]))
            except (ValueError, TypeError):
                cls = None
            if cls:
                score += cls["rank"] // 100
        flat.append({
            "url": url,
            "source_type": "json_ld_media",
            "score": score,
            # F-REC02-01: reuse the guarded `cls` computed above instead of a
            # second, UNGUARDED int() cast -- a non-integer width/height would
            # raise here and crash the whole flatten on a malformed JSON-LD page.
            "resolution": (cls if j.get("width") and j.get("height") else None),
            "codec": None,
            "fps": None,
            "size_bytes": None,
            "found_in": "json_ld",
            "reasons": [f"JSON-LD {j.get('type')} contentUrl"],
            "warnings": [],
            "requires_click": False,
        })

    # Two-step POST reveal forms — surfaced as workflow_required at
    # the orchestrator level, but also added to the candidate list
    # at a low score so the UI shows them.
    for f in post_reveal:
        flat.append({
            "url": f.get("action") or None,
            "source_type": "two_step_post_reveal",
            "score": f.get("confidence", 40),
            "resolution": None,
            "codec": None,
            "fps": None,
            "size_bytes": None,
            "workflow": {
                "method": "POST",
                "action": f.get("action"),
                "submit_selector": f.get("submit_selector"),
                "safe_fields": f.get("safe_fields"),
                "user_fields": f.get("user_fields"),
                "honeypot_fields": f.get("honeypot_fields"),
                "needs_approval": f.get("needs_approval", False),
                "approval_status": f.get("approval_status", "not_required"),
                "bot_defenses": f.get("bot_defenses") or [],
            },
            "needs_workflow": True,
            "found_in": "two_step_post_form",
            "reasons": f.get("reasons") or [],
            "warnings": (["two-step POST reveal needs operator approval "
                          "(honeypot/challenge markers); awaiting decision"]
                         if f.get("approval_status") == "pending" else []),
            "requires_click": False,
        })

    return flat


def _dedup_candidates(cands: List[dict]) -> List[dict]:
    """Drop duplicate URLs across surfaces while preserving the
    highest-scoring origin. A URL appearing in BOTH an HLS variant
    AND a JWPlayer config is one resource.

    v3.66.10 uses `canonicalize_url` (host case, default ports,
    tracking params, sort order, trailing slash) as the dedup KEY
    rather than the raw URL. So
        https://X.Com:443/file.mp4?utm_source=blog&a=1
        https://x.com/file.mp4?a=1
    are correctly merged, even though their raw forms differ.

    Schema additions:
      • `merged_from`: list of `found_in` strings from dropped duplicates
      • `alternate_urls`: list of raw URLs of dropped duplicates (so the
        UI can show "this URL also appears at X with different tracking
        params" if it wants)

    Mutation: the winner's `url` field is preserved (raw). All other
    fields except merged_from / alternate_urls / reasons / warnings
    stay as-set on the winner.

    # INV-DEDUP-MUTATES (v3.66.11): the winner dict is MUTATED in
    # place (reasons, warnings, merged_from, alternate_urls all
    # append to lists on the live winner). Callers that hold
    # references to candidate dicts pre-dedup must defensive-copy if
    # they need the pre-merge state. This is the existing contract;
    # changing to copy-on-write would break the (~15) downstream
    # callers that walk the list and read merged_from off the
    # winner. Audit bugs X / Y flagged this; the contract is kept.

    # INV-DEDUP-TIEBREAK (v3.66.11): strict `>` comparison on score
    # means same-score duplicates resolve to the FIRST-OCCURRENCE
    # candidate (whichever was inserted in by_canonical first).
    # Order-dependence is deliberate: the flatten step emits
    # candidates in a stable surface order (HLS variants → DASH →
    # state-blob → cards → embeds → ...) and the first-occurrence
    # winner reflects "the more structured source got there first".
    # Audit bug Z flagged this; behaviour is kept and now load-
    # bearing.
    """
    by_canonical: Dict[str, dict] = {}
    no_url: List[dict] = []
    for c in cands:
        u = c.get("url")
        if not u:
            no_url.append(c)
            continue
        key = canonicalize_url(u)
        if not key:
            # Canonicalization failed — fall back to the raw URL as
            # the key, which preserves the old behavior for that
            # candidate.
            key = u
        cur = by_canonical.get(key)
        if cur is None:
            by_canonical[key] = c
            continue
        # Pick the higher-scoring one as the winner.
        if c.get("score", 0) > cur.get("score", 0):
            winner, loser = c, cur
        else:
            winner, loser = cur, c
        # Carry over the loser's evidence so the UI sees the full
        # picture. Reasons/warnings get merged (deduplicated by string
        # identity to avoid stuffing the same text in twice); the
        # loser's `found_in` joins `merged_from`.
        w_reasons = list(winner.get("reasons") or [])
        for r in (loser.get("reasons") or []):
            if r not in w_reasons:
                w_reasons.append(r)
        winner["reasons"] = w_reasons
        w_warnings = list(winner.get("warnings") or [])
        for w in (loser.get("warnings") or []):
            if w not in w_warnings:
                w_warnings.append(w)
        winner["warnings"] = w_warnings
        merged = list(winner.get("merged_from") or [])
        loser_origin = loser.get("found_in")
        if loser_origin and loser_origin not in merged \
                and loser_origin != winner.get("found_in"):
            merged.append(loser_origin)
        # Also fold in any merged_from that the LOSER had already
        # accumulated (in case dedup is called on already-merged data).
        for m in (loser.get("merged_from") or []):
            if m and m not in merged and m != winner.get("found_in"):
                merged.append(m)
        if merged:
            winner["merged_from"] = merged
        # Track the alternate raw URL — only if it differs from the
        # winner's raw URL (which it usually does, since the canonical
        # forms matched but the raw forms differ; that's the whole
        # point of canonicalization).
        loser_url = loser.get("url")
        if loser_url and loser_url != winner.get("url"):
            alt = list(winner.get("alternate_urls") or [])
            if loser_url not in alt:
                alt.append(loser_url)
            # Also fold in loser's alternate_urls if it already had any.
            for au in (loser.get("alternate_urls") or []):
                if au and au not in alt and au != winner.get("url"):
                    alt.append(au)
            winner["alternate_urls"] = alt
        by_canonical[key] = winner
    return list(by_canonical.values()) + no_url


def _apply_signed_url_annotations(
        cands: List[dict],
        rejected_sink: List[dict],
        warnings_sink: List[str],
) -> List[dict]:
    """Annotate each candidate with `signed_url` info if its URL
    matches a known signing scheme. Side effects:
      • Expired candidates are removed from `cands` and pushed into
        `rejected_sink`.
      • Each expired/expiring-soon case is counted; if non-zero,
        a session-level warning is added to `warnings_sink`.

    Returns the filtered candidate list (with expired ones removed).
    Called from both deep_detect()'s static pass AND deep_detect_live()
    after the live-mode anchor active-surfacing adds new candidates —
    those wouldn't otherwise get signed-URL annotations.
    """
    out: List[dict] = []
    expired_count = 0
    expiring_soon_count = 0
    for c in cands:
        u = c.get("url")
        if not u:
            out.append(c)
            continue
        # Idempotency: if we already annotated this candidate (e.g. it
        # passed through deep_detect() first), skip re-detection.
        if "signed_url" in c:
            out.append(c)
            continue
        sig_info = detect_signed_url(u)
        if not sig_info["is_signed"]:
            out.append(c)
            continue
        c["signed_url"] = {
            "provider": sig_info["provider"],
            "expires_at": sig_info["expires_at"],
            "ttl_seconds": sig_info["ttl_seconds"],
        }
        if sig_info["expired"]:
            c.setdefault("warnings", []).append(
                f"signed URL ({sig_info['provider']}) is EXPIRED "
                f"(expires_at={sig_info['expires_at']}); "
                f"fetch will fail")
            c["rejected"] = True
            c["score"] = (c.get("score") or 0) - 500
            rejected_sink.append(c)
            expired_count += 1
            continue
        if sig_info["expiring_soon"]:
            c.setdefault("warnings", []).append(
                f"signed URL ({sig_info['provider']}) expires in "
                f"{sig_info['ttl_seconds']}s — fetch soon")
            expiring_soon_count += 1
        c.setdefault("reasons", []).append(
            f"signed URL: {sig_info['provider']}"
            + (f" (TTL {sig_info['ttl_seconds']}s)"
               if sig_info["ttl_seconds"] is not None else ""))
        out.append(c)
    if expired_count:
        warnings_sink.append(
            f"{expired_count} signed download URL(s) are EXPIRED — "
            "see report['rejected'] for details. The page needs to "
            "be re-scraped to obtain fresh signatures.")
    if expiring_soon_count:
        warnings_sink.append(
            f"{expiring_soon_count} signed download URL(s) expire "
            f"within {_SIGNED_URL_SHORT_TTL_THRESHOLD} seconds — "
            "fetch promptly or re-scrape for fresh signatures.")
    return out


def _build_score_buckets(*, accepted: List[dict], rejected: List[dict],
                         warnings: List[str]) -> dict:
    """F7 (additive): organize already-scored candidates into a
    multi-bucket view without mutating or replacing the flat lists.

    Returns:
        {
          "accepted": [ ...candidates kept, highest score first... ],
          "rejected": [ {"candidate"|"url": ..., "reasons": [...]} ],
          "warnings": [ ...session-level warning strings... ],
          "counts": {"accepted": N, "rejected": M, "warnings": K},
        }

    Each rejected entry surfaces WHY it was rejected: a candidate dict's
    own `warnings`/`reasons` (trap-link, signed-URL-expired, etc.) are
    collected into a `reasons` list so the operator doesn't have to
    cross-reference. Trap-link rejects (which are link dicts, not scored
    candidates) are passed through with whatever reason they carry. This
    function never re-scores, re-orders the accepted list (already sorted
    by the caller), or drops anything.
    """
    rej_view = []
    for r in rejected or []:
        reasons = []
        for key in ("reasons", "warnings"):
            v = r.get(key)
            if isinstance(v, list):
                reasons.extend(str(x) for x in v)
            elif isinstance(v, str) and v:
                reasons.append(v)
        # de-dup while preserving order
        seen, ordered = set(), []
        for x in reasons:
            if x not in seen:
                seen.add(x); ordered.append(x)
        rej_view.append({
            "url": r.get("url"),
            "source_type": r.get("source_type"),
            "score": r.get("score"),
            "reasons": ordered or ["rejected"],
        })
    return {
        "accepted": accepted,
        "rejected": rej_view,
        "warnings": list(warnings or []),
        "counts": {
            "accepted": len(accepted),
            "rejected": len(rej_view),
            "warnings": len(warnings or []),
        },
    }


def _bk(report: dict) -> dict:
    """Return report['buckets'], creating an empty one if absent."""
    b = report.get("buckets")
    if b is None:
        b = {"accepted": [], "rejected": [], "rejected_raw": [],
             "warnings": [], "best": None,
             "counts": {"accepted": 0, "rejected": 0, "warnings": 0}}
        report["buckets"] = b
    b.setdefault("rejected_raw", [])
    return b


def _bk_accepted(report: dict) -> list:
    return _bk(report)["accepted"]


def _bk_warnings(report: dict) -> list:
    return _bk(report)["warnings"]


def _bk_rejected_raw(report: dict) -> list:
    return _bk(report)["rejected_raw"]


def _rejected_view(rejected_raw: list) -> list:
    """Compute the {url, source_type, score, reasons} presentation view
    from the raw reject dicts (same logic as _build_score_buckets)."""
    out = []
    for r in rejected_raw or []:
        reasons = []
        for key in ("reasons", "warnings"):
            v = r.get(key)
            if isinstance(v, list):
                reasons.extend(str(x) for x in v)
            elif isinstance(v, str) and v:
                reasons.append(v)
        seen, ordered = set(), []
        for x in reasons:
            if x not in seen:
                seen.add(x); ordered.append(x)
        out.append({"url": r.get("url"), "source_type": r.get("source_type"),
                    "score": r.get("score"), "reasons": ordered or ["rejected"]})
    return out


def _bk_set_accepted(report: dict, cands: list) -> None:
    """Replace the accepted list and refresh counts + best."""
    b = _bk(report)
    b["accepted"] = cands
    b["best"] = cands[0] if cands else None
    _bk_refresh_counts(report)


def _bk_refresh_counts(report: dict) -> None:
    b = _bk(report)
    b["rejected"] = _rejected_view(b.get("rejected_raw") or [])
    b["counts"] = {
        "accepted": len(b.get("accepted") or []),
        "rejected": len(b["rejected"]),
        "warnings": len(b.get("warnings") or []),
    }


def _bk_best(report: dict):
    """The top accepted candidate (the former `best_download`)."""
    acc = _bk(report).get("accepted") or []
    return acc[0] if acc else None


def _finalize_buckets(out: dict, *, accepted: list, rejected_raw: list = None,
                      source_breakdown: dict = None) -> dict:
    """F7 phase 2: make `buckets` the canonical store and RETIRE the flat
    top-level keys (download_candidates / best_download / rejected /
    warnings). Called at every return point of deep_detect /
    deep_detect_live.

    `accepted` is the final sorted candidate list. `rejected_raw` is the
    raw reject dicts (trap-links + score<0 + signed-expired); defaults to
    whatever `out` accumulated under the old flat key. `warnings` is read
    from `out` (callers append to out['warnings'] during the build).
    `source_breakdown` is kept top-level (unchanged consumer).
    """
    warnings = list(out.get("warnings") or [])
    if rejected_raw is None:
        rejected_raw = out.get("rejected") or []
    b = {
        "accepted": accepted,
        "rejected_raw": list(rejected_raw),
        "rejected": _rejected_view(rejected_raw),
        "warnings": warnings,
    }
    b["counts"] = {
        "accepted": len(accepted),
        "rejected": len(b["rejected"]),
        "warnings": len(warnings),
    }
    out["buckets"] = b
    # `best` and `source_breakdown` move under buckets but are ALSO kept
    # top-level-free; expose best via buckets only.
    b["best"] = accepted[0] if accepted else None
    out["source_breakdown"] = (source_breakdown if source_breakdown is not None
                               else _count_by_type(accepted))
    # Retire the flat keys — buckets is now authoritative.
    for k in ("download_candidates", "best_download", "rejected", "warnings"):
        out.pop(k, None)
    return out
