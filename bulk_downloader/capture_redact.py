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
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
    r"policy|credential|x-amz-|apikey|password|pwd|jwt|hdnea|hdnts|__gda__|"
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


_OPAQUE_PATH_SEGMENT = re.compile(
    r"(?:^eyJ[A-Za-z0-9._~-]{12,}$|"
    r"^(?=.{32,}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._~-]+$)"
)
_SENSITIVE_PATH_LABEL = re.compile(
    r"^(?:token|auth|authorization|session|signature|sig|secret|credential)$",
    re.I,
)

# ── D1: signing packed into the PATH as name=value assignments ───────────────
# A CDN may sign in the PATH rather than the query, packing several assignments
# into ONE segment separated by ',' (often with spaces) or '&':
#
#     https://<host>/key=<sig>,end=<epoch>,ip=<client ip>/.../<file>.mp4
#
# That segment's own name is not a sensitive LABEL, and the mixed '='/','
# punctuation stops it looking OPAQUE, so neither whole-segment rule above
# fires and the signature, the expiry and the OPERATOR'S PUBLIC IP survived
# into operator-visible media evidence. Redact the VALUE of each credential-,
# expiry- or client-identifying assignment and keep everything else: the host,
# the file name, the resolution rung and the non-signing assignments
# (``download2=<file>``, ``speed=``, ``buffer=``) are the evidence an operator
# needs, so a whole-segment mask would destroy the URL's identity.
#
# The vocabulary mirrors ``capture_workbench_impl._common._PATH_SIGN_TYPE``,
# the tree's existing classification of path-signing names (token / expiry /
# ip-binding). It is restated rather than imported because this module is a
# leaf primitive and must not grow an edge into the workbench package; the
# redaction gate asserts every key of that map is covered, so drift is caught
# by a test rather than by prose.
_PATH_ASSIGN_SENSITIVE = re.compile(
    r"^(?:key|keypair|keypairid|credential|token|secret|"
    r"s|sig|signature|hmac|hash|md5|policy|"
    r"exp|expire|expires|end|st|start|limit|"
    r"ip|cip|clientip|client_ip|ipaddr|remote_addr|"
    r"cui|uh)$",
    re.I,
)
# One assignment inside a path run. Every quantifier is upper-bounded (the
# v3.66.291 ReDoS rule); the value stops at a path, query, fragment, list or
# whitespace delimiter so ``ip=2001:db8::10`` and base64 '=' padding survive
# intact while ``download2=x.mp4`` is left for the name check to spare.
_PATH_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9_\-])(?P<k>[A-Za-z0-9_\-]{1,64})=(?P<v>[^/?#,&\s]{1,512})")
# A run of assignments that begins a PATH segment. The lookbehind pins the run
# to a '/' boundary, which is what keeps the QUERY (introduced by '?') under
# the existing ``redact_query`` pass instead of this one.
_PATH_SIGNING_RUN_RE = re.compile(
    r"(?<=/)[A-Za-z0-9_\-]{1,64}=[^/?#]{0,512}")


def _assignment_name_is_sensitive(name: str) -> bool:
    """True when a path assignment's NAME marks its value as a credential, an
    expiry, or a client identifier. Anchored full-name matching for the path
    vocabulary (so ``speed``/``buffer``/``download2`` can never collide), plus
    the module's own query source of truth for the shared credential names."""
    return bool(_PATH_ASSIGN_SENSITIVE.match(name or "")
                or SENSITIVE_QS_KEY.search(name or ""))


# Delimiters that split a URL into the parts each pass owns. Decoding must
# never manufacture one where the caller's split did not see it.
_STRUCTURAL_DELIMITERS = "/?#"


def _substitute_assignments(text: str) -> str:
    """The substitution itself: sensitive assignment values become the
    placeholder, benign assignments are returned verbatim."""
    return _PATH_ASSIGNMENT_RE.sub(
        lambda m: (f"{m.group('k')}={PLACEHOLDER}"
                   if _assignment_name_is_sensitive(m.group("k"))
                   else m.group(0)),
        text)


def _redact_path_assignments(text: str) -> str:
    """Redact the VALUE of every sensitive ``name=value`` assignment in one
    path segment or assignment run; benign assignments pass through verbatim.

    This is the single implementation shared by :func:`redact_media_url` (which
    calls it per already-split path segment) and, via
    :func:`redact_path_signing`, by ``capture_artifact_redact.redact_value``
    (which has only a free string). One rule and one vocabulary, applied at
    each caller's natural granularity.

    PERCENT-ENCODING IS THE SHAPE A LIVE CAPTURE ACTUALLY PRODUCES. The
    production entry is ``redact_media_url(str(response.url))``, and Chromium
    returns an encoded URL: the CDN's ``", "`` separator arrives as ``",%20"``,
    so ``%20end=`` parses as a name ``20end`` that no vocabulary matches and
    the second and later assignments -- the expiry and the client IP -- were
    invisible to a raw-text pass. So the decoded text is tried too, and its
    result is kept ONLY when decoding actually revealed a redaction; a benign
    segment therefore stays byte-identical rather than being silently decoded.
    """
    if not isinstance(text, str) or "=" not in text:
        return text
    decoded = unquote(text)
    if decoded != text and not any(
            c in decoded and c not in text for c in _STRUCTURAL_DELIMITERS):
        revealed = _substitute_assignments(decoded)
        if revealed != decoded:
            return revealed
    return _substitute_assignments(text)


def redact_path_signing(text: str) -> str:
    """Apply the path-assignment rule to every path-segment assignment run in a
    free string, leaving query strings to :func:`redact_query`."""
    if not isinstance(text, str) or "=" not in text:
        return text
    return _PATH_SIGNING_RUN_RE.sub(
        lambda m: _redact_path_assignments(m.group(0)), text)


def redact_media_url(url: str) -> str:
    """Redact query, userinfo, and credential-like media path segments.

    Media evidence is operator-visible, unlike most internal capture records.
    Signed services also place short-lived credentials in URL paths, so query
    redaction alone is not an adequate display boundary.
    """
    if not isinstance(url, str) or not url:
        return url
    safe = redact_query(url)
    try:
        parsed = urlsplit(safe)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if host and port is not None else host

        segments = parsed.path.split("/")
        history_download = False
        lowered = [unquote(segment).lower() for segment in segments]
        for index in range(max(0, len(lowered) - 3)):
            if (
                lowered[index:index + 2] == ["user", "history"]
                and lowered[index + 2] in {"download", "streaming"}
            ):
                history_download = True
                break
        previous = ""
        for index, segment in enumerate(segments):
            decoded = unquote(segment)
            if (
                decoded
                and (
                    _SENSITIVE_PATH_LABEL.match(previous)
                    or _OPAQUE_PATH_SEGMENT.match(decoded)
                    or (history_download and index == len(segments) - 1)
                )
            ):
                segments[index] = PLACEHOLDER
            elif "=" in segment:
                # D1: signing packed into the segment as name=value assignments.
                segments[index] = _redact_path_assignments(segment)
            previous = decoded
        return urlunsplit(parsed._replace(
            netloc=netloc,
            path="/".join(segments),
        ))
    except Exception:
        # Query redaction already completed; never fall back to the raw input.
        return safe


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
