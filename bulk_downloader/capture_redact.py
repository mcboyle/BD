"""Capture redaction primitives — single source of truth.

bd-recon captures come from REAL authenticated sessions. Every
credential-bearing field must be replaced with :data:`PLACEHOLDER`
before a capture is persisted or committed as a fixture. These
primitives are used by BOTH:

  * ``tools/scrub_recon.py`` — post-hoc scrub of already-saved captures
    (the original home of this logic).
  * ``bulk_downloader.session_capture`` — capture-TIME redaction (A-T1),
    so credentials never hit disk in the first place.

The placeholder is the exact string ``netlog_classify`` already
recognizes (``_SCRUB_PLACEHOLDER``), so a redacted capture flows through
the media classifier unchanged: a redacted/signed URL is described,
never treated as a downloadable candidate.

Posture: this is capture/redaction only — detect-and-surface-risk. It
removes secrets; it never reconstructs, replays, or evades.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, quote

PLACEHOLDER = "<scrubbed>"

# Max recursion depth when redacting a signed URL nested (URL-encoded)
# inside another URL's query value. 2 is plenty for real captures and
# bounds pathological inputs.
_MAX_NEST = 2

# Header names whose VALUES are credentials — drop the value, keep the
# name so structure ("this request sent a Cookie") stays visible.
SENSITIVE_HEADER = re.compile(
    r"(cookie|authorization|auth-token|x-.*-token|x-.*-key|csrf|"
    r"x-xsrf|session|bearer|api[-_]?key|signature|x-amz-security-token)",
    re.I,
)

# Query-param keys whose values are secrets — redact the value.
SENSITIVE_QS_KEY = re.compile(
    r"(token|key|sig|signature|secret|auth|session|sid|hash|expires|"
    r"policy|credential|x-amz-|apikey|password|pwd|jwt|"
    # P3-T12: challenge-RESPONSE tokens. Cloudflare cf_challenge_response /
    # __cf_chl_tk match via 'challenge' / 'cf_chl'; hCaptcha & reCaptcha
    # *-response tokens match via 'captcha'. These are "challenge passed"
    # tokens whose retention could enable replay, so the floor scrubs the
    # VALUE like any signed token. Substrings chosen to avoid benign keys:
    # 'captcha' does NOT match 'capture'; 'cf_chl' is the CF prefix (not bare
    # 'chl'); 'challenge' has no common-key collision.
    r"challenge|cf_chl|captcha|"
    # F-COREBD17-01 (DP-08): csrf / xsrf / bearer as SUBSTRINGS. These are
    # distinctive tokens with no benign query-key collision (no common key
    # contains them), so -- unlike the short code/k/state keys -- they need
    # not be anchored, and substring form also covers _csrf / x-xsrf /
    # bearer_token. csrf/bearer are atypical as query params (they are header/
    # body tokens) but must be scrubbed when they do appear.
    r"csrf|xsrf|bearer)"
    # T7 (v3.66.210): short moderate-entropy analytics tokens (vixen `code`,
    # bang `k`) that read as secrets only when they ARE the whole key.
    # DEC-1: widen the anchored set with nonce/otp/tk/ak/sk + state (OAuth/CSRF
    # nonce class; state included deliberately, F2-safe). Anchored exact-match
    # so we never catch these by substring (geocode/zipcode/encode, ask/task/
    # make/network, estate/statement, announce/pronounce) nor any key merely
    # containing the letter 'k'.
    r"|^(?:code|k|nonce|otp|tk|ak|sk|state)$",
    re.I,
)


def redact_query(url: str, _depth: int = 0) -> str:
    """Redact token-like query param values, keep the rest of the URL.

    Recurses into a param VALUE that is itself a URL-encoded URL carrying
    its own signing params (e.g. ``mediaResource=https%3A%2F%2F...%26
    Signature%3D...``), which the flat top-level pass would otherwise miss
    — the nested host/path is kept, only its signing params are redacted.
    """
    if not isinstance(url, str) or "?" not in url:
        return url
    base, _, qs = url.partition("?")
    parts = []
    for pair in qs.split("&"):
        if "=" in pair:
            k, _, v = pair.partition("=")
            if SENSITIVE_QS_KEY.search(k):
                parts.append(f"{k}={PLACEHOLDER}")
            else:
                parts.append(f"{k}={_redact_nested_value(v, _depth)}")
        else:
            parts.append(pair)
    return f"{base}?{'&'.join(parts)}"


def _redact_nested_value(value: str, depth: int) -> str:
    """If a param value is (or URL-encodes) a URL with its own query,
    recursively redact that nested URL's sensitive params. Returns the
    original value unchanged when there's nothing nested to redact."""
    if not isinstance(value, str) or depth >= _MAX_NEST:
        return value
    decoded = unquote(value)
    if "?" not in decoded:
        return value
    redacted = redact_query(decoded, depth + 1)
    if redacted == decoded:
        return value  # no inner sensitive params → leave original untouched
    # Re-encode if the original was percent-encoded so the outer URL stays
    # structurally valid; otherwise return the redacted raw form.
    return quote(redacted, safe="") if decoded != value else redacted


def apply_url_mode(url: str, mode: str = "keep_structure") -> str:
    """Apply a redaction MODE to a single URL (v3.66.171). Mode strings mirror
    ``redaction_profile``; kept here (profile-free) so this stays a primitive:

      * ``keep_structure`` (default) — host/path/name kept, signing params
        scrubbed (``redact_query``). Identical to the v3.66.170 behaviour.
      * ``strip_all`` — host/path kept, the WHOLE query collapsed to one
        placeholder (more aggressive; still leaves enough for recognition/diff).
      * ``keep_full`` — returned unchanged (functional testing only).
    """
    if not isinstance(url, str) or not url:
        return url
    if mode == "keep_full":
        return url
    if mode == "strip_all":
        base, sep, _qs = url.partition("?")
        return base + ("?" + PLACEHOLDER if sep else "")
    return redact_query(url)  # keep_structure (and any unknown mode → safe default)


def _header_value(name, value, extra_headers=()):
    """Value for one header: PLACEHOLDER for credential headers; for a
    non-sensitive header whose value is a URL, redact that URL's query
    (it may carry a signed URL, e.g. Referer/Link); else keep as-is.

    ``extra_headers`` is an ADDITIVE lower-cased name set (v3.66.171): a name in
    it is scrubbed in addition to ``SENSITIVE_HEADER``. It can only widen the
    floor, never shrink it — the ``SENSITIVE_HEADER`` check runs regardless.
    """
    n = str(name)
    if SENSITIVE_HEADER.search(n) or (extra_headers and n.lower() in extra_headers):
        return PLACEHOLDER
    if isinstance(value, str) and value[:5].lower().startswith(("http:", "https")):
        return redact_query(value)
    return value


def scrub_headers(headers, extra_headers=()):
    """Drop values of credential-bearing headers; keep header names so
    structure (e.g. 'this request sent a Cookie') is still visible.
    URL-valued non-sensitive headers have their query redacted too.

    ``extra_headers`` (v3.66.171) is an additive lower-cased name set passed by
    the redactor from the active profile; it widens the credential set only.

    Accepts either a dict (name->value) or a HAR-style list of
    ``{"name", "value"}`` entries; returns the same shape.
    """
    if isinstance(headers, dict):
        return {k: _header_value(k, v, extra_headers) for k, v in headers.items()}
    if isinstance(headers, list):
        out = []
        for h in headers:
            if isinstance(h, dict) and "name" in h:
                out.append({**h, "value": _header_value(h.get("name", ""),
                                                         h.get("value"),
                                                         extra_headers)})
            else:
                out.append(h)
        return out
    return headers


def body_marker(body):
    """Replace a body with a length/shape marker. Bodies can carry
    tokens, signed URLs, PII. Keep only 'there was a body of N chars'.
    """
    if body is None:
        return None
    if isinstance(body, str):
        return f"{PLACEHOLDER}(len={len(body)})"
    return f"{PLACEHOLDER}(json:{type(body).__name__})"


def scrub_globals(obj):
    """Recursively redact token-like string leaves in config blobs.

    URLs get their query redacted; other strings (titles, descriptions)
    are page content and left intact.
    """
    if isinstance(obj, dict):
        return {k: scrub_globals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_globals(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_globals(v) for v in obj)
    if isinstance(obj, (set, frozenset)):
        # THD (v3.66.529): a set/frozenset leaf used to fall through to the
        # passthrough below, so string tokens inside it were never scanned.
        # Scan each member; preserve the (frozen)set type.
        scrubbed = {scrub_globals(v) for v in obj}
        return frozenset(scrubbed) if isinstance(obj, frozenset) else scrubbed
    if isinstance(obj, (bytes, bytearray)):
        # THD (v3.66.529): bytes can carry tokens/credentials but cannot be
        # safely scanned as a string leaf. Fail closed -- drop the content to a
        # shape marker rather than passing the raw bytes through unscanned.
        return f"{PLACEHOLDER}(bytes:len={len(obj)})"
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            return redact_query(obj)
        return obj
    return obj
