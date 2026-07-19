"""C-T2 (bounded slice) — opt-in response-body capture with a body redactor.

PURPOSE
-------
C-T1's live validation left ~94 parameters marked ``source_unknown``: their
values end up in a request URL, but the differ couldn't trace where they
first appeared. Most originate in an earlier *response body* (an API call
returns an id/token that a later URL embeds). The capture pipeline currently
reduces every body to a length marker (``capture_redact.body_marker``), so
that provenance is gone before synthesis ever sees it. This module lets the
operator *opt in* to retaining bodies — text/JSON only, redacted at capture
time — so ``capture_synth._trace_source`` can search prior bodies and resolve
those unknowns.

POSTURE (read first)
--------------------
Record-redact-export only. This is provenance labelling, NOT replay and NOT
reassembly:
  * Bodies are retained ONLY when ``BD_CAPTURE_BODIES=1`` (default OFF). With
    the flag unset, behaviour is byte-for-byte the old length-marker path.
  * Only ``text/*``, ``*json*`` and (v3.66.172) HLS/DASH **manifest** content
    types are ever stored; any other type (video, octet-stream, fonts, images)
    stays a length marker. We never store an opaque blob we can't reason about.
  * Redaction happens HERE, at capture/store time — never deferred to export.
    A body that sits unredacted on disk and is only scrubbed on the way out is
    already a reassembly aid; that is the line we do not cross.
  * The redactor reuses the SHIPPED signing detectors
    (``capture_redact.SENSITIVE_QS_KEY`` for keys, ``netlog_classify._is_signed``
    for signed-URL values, ``capture_synth``'s JWT/token shape rules for value
    shapes). It does not invent a parallel notion of "signed".

The hard guarantee, pinned by the regression test: for JSON/text bodies, **no
signing material survives in a stored body** — not a JWT, not a signed
CloudFront/S3 URL, not a query-signed HLS key/end/limit segment URL, regardless
of how the body labels its fields. The .52 lessons (redact_query had to recurse
into nested signed URLs; a fixture shipped with cleartext CloudFront triples
because the scrub keyed on names) are exactly why the value-shape detectors
matter as much as the key-name ones here.

**v3.66.172 posture caveat (DEFERRED-F2, see ``_is_manifest_ct``):** retained
HLS/DASH manifests get the same text scrub, so query-signed segment URLs / JWTs
/ long tokens are masked — but a *path-signed* URL (signature embedded in the
PATH, not a query param — whether a media segment, an #EXT-X-KEY or an
#EXT-X-MAP URI) is neither detector-flagged nor a floor secret and CAN survive. The wholesale
hard guarantee therefore does NOT yet hold for manifest bodies. Closing that is
the STRUCTURE-mode hardening pass (mask every manifest URI line + stamp the
artifact local_only). Until then, any WACZ built with BD_CAPTURE_BODIES=1 over
a streaming site is local-only — never circulate it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from .capture_redact import (PLACEHOLDER, SENSITIVE_QS_KEY, body_marker,
                             redact_query)
from .netlog_classify import _is_signed as _signed_url_markers

# Value-shape rules for body values that are dangerous regardless of their
# key name. Kept aligned with capture_synth._SHAPE_RULES (jwt/token) — the
# point is that a JWT in a field called "data" is still a JWT.
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$")
_LONG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")  # opaque high-entropy-ish

_FLAG = "BD_CAPTURE_BODIES"
_MAX_BODY_DEPTH = 8       # bound recursion on pathological nested JSON
_MAX_TEXT_LEN = 65536     # cap stored text body size (chars)
# Cap stored JSON body size (chars). Deliberately larger than the text cap
# because JSON API payloads are legitimately bigger; the goal is to BOUND
# unbounded growth (a retained JSON body was previously size-unlimited), not to
# match the text byte value. An over-cap JSON body falls back to the length
# marker, exactly like an ineligible content type.
_MAX_JSON_LEN = 1024 * 1024


def bodies_enabled() -> bool:
    """True iff body capture is opted in. Default OFF → length-marker path.

    v3.66.308 (CLI→GUI parity): the global_config store key ``capture_bodies``
    overrides the env seed when set, so a Settings write takes effect on the
    next capture without a restart. Read at call time; global_config is thin
    (no Flask) and imported lazily — on any failure we fall back to env→default.
    """
    env_on = os.environ.get(_FLAG, "").strip() in ("1", "true", "True", "yes")
    try:
        from bulk_downloader import global_config as _gc
        v = _gc.get("capture_bodies", env_on)
    except Exception:
        return env_on
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def should_capture_body(content_type: Optional[str]) -> bool:
    """True iff a response body of this content-type should be FETCHED and
    retained: body capture is opted in AND the type is text/JSON. Used by the
    live capture driver to decide whether to issue a CDP getResponseBody at
    all — so binary bodies (video/font/image, and crucially the signed media
    streams) are never pulled into memory, which is both the perf guard and
    the posture line (we never fetch stream bytes). v3.66.172: HLS/DASH
    manifests (text, not stream bytes) are now eligible too."""
    return bodies_enabled() and (_is_text_or_json(content_type)
                                 or _is_manifest_ct(content_type))


def _is_text_or_json(content_type: Optional[str]) -> bool:
    """Only text/* and JSON content types are eligible for retention."""
    if not content_type or not isinstance(content_type, str):
        return False
    ct = content_type.lower()
    return ct.startswith("text/") or "json" in ct


# v3.66.172: HLS/DASH streaming-manifest content types. These are line/XML
# TEXT (not binary), so retaining them is opt-in-and-redacted like any other
# text body — NOT a fetch of stream bytes. The bodies still flow through the
# capture-time text scrub (_redact_text → _value_is_dangerous), so query-signed
# segment URLs, JWTs and long opaque tokens are masked before storage, and the
# unconditional WACZ floor gate (scan_floor_secrets) still runs on export.
#
# DEFERRED-F2 (tracked, NOT closed here — the "proper F2/posture" pass):
# a *path-signed* segment URL (signature in the PATH, not the query) and an
# #EXT-X-KEY / #EXT-X-MAP key URI are NOT wholesale-masked by the text scrub
# and are not floor secrets, so they can survive in a retained manifest. The
# hardening option is STRUCTURE mode: mask every URI/segment/key line of a
# manifest regardless of detector, keeping only the non-URL structure, and
# stamp a manifest-retaining artifact reduced_redaction/local_only. Until that
# lands, treat any WACZ built with BD_CAPTURE_BODIES=1 over streaming sites as
# local-only and never circulate it.
def _is_manifest_ct(content_type: Optional[str]) -> bool:
    """True for HLS (.m3u8) / DASH (.mpd) streaming-manifest content types."""
    if not content_type or not isinstance(content_type, str):
        return False
    ct = content_type.lower()
    return ("mpegurl" in ct           # application/vnd.apple.mpegurl, x-mpegurl, audio/(x-)mpegurl
            or "dash+xml" in ct)      # application/dash+xml


def _value_is_dangerous(value: str) -> bool:
    """A bare string VALUE that must be masked regardless of its key:
    a signed/short-lived URL, a JWT, or a long opaque token."""
    if not isinstance(value, str) or not value:
        return False
    # Signed-URL detector (shared with netlog/synth) — also catches a signed
    # URL embedded URL-encoded inside the value, and (since v3.66.55) AWS
    # SigV4's hyphenated X-Amz-* params, which the shared regex was widened
    # to recognise. No local SigV4 fallback needed any more.
    if _signed_url_markers(value):
        return True
    if _JWT_RE.match(value):
        return True
    if _LONG_TOKEN_RE.match(value):
        return True
    return False


def _redact_signed_or_mask(value: str) -> str:
    """Redact a dangerous string value, preserving non-secret provenance.

    The wholesale `<scrubbed>` mask used through v3.66.57 destroyed the part
    of a signed media URL that C-T2 actually wants — the host/path/id and a
    benign ``filename=`` — because those live *inside* the signed URL that got
    masked. This keeps them, scrubbing only the signing params, which is
    exactly how a signed URL nested in a query VALUE is already handled
    (capture_redact.redact_query / _redact_nested_value, v3.66.52). The path
    is not the secret; the signature is, and that is still removed.

    Posture guards, applied before keeping anything:
      * a JWT or bare opaque token (not a URL) has no non-secret part → full
        mask;
      * if the signing markers are in the PATH itself (not the query),
        keeping the path would leak signing material → full mask;
      * an HLS segment URL (``.ts``/``.m4s`` or the ``key=`` + ``end=``/
        ``limit=`` triple) is the canonical short-lived signed stream the
        posture never reconstructs → full mask even when its signing is in
        the query.
    Everything else (CloudFront / S3 / generic expiring direct-media URLs)
    keeps host+path and has its signing query params scrubbed.
    """
    if not _signed_url_markers(value):
        return PLACEHOLDER
    base = value.split("?", 1)[0]
    if _signed_url_markers(base):
        return PLACEHOLDER
    low = value.lower()
    if base.lower().endswith((".ts", ".m4s")) or \
            ("key=" in low and ("end=" in low or "limit=" in low)):
        return PLACEHOLDER
    return redact_query(value)


def _redact_json(obj: Any, depth: int = 0) -> Any:
    """Recursively redact a parsed JSON body.

    A value is masked when EITHER its key looks sensitive (key-name detector)
    OR the value itself looks signed/JWT/token (value-shape detector). This
    two-sided rule is the whole point: bodies hide signing material under
    innocent key names, so key-name matching alone (the .52 fixture bug) is
    not enough.
    """
    if depth >= _MAX_BODY_DEPTH:
        return PLACEHOLDER
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_sensitive = isinstance(k, str) and SENSITIVE_QS_KEY.search(k)
            if key_sensitive:
                out[k] = PLACEHOLDER
            elif isinstance(v, (dict, list)):
                out[k] = _redact_json(v, depth + 1)
            elif isinstance(v, str) and _value_is_dangerous(v):
                out[k] = _redact_signed_or_mask(v)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return [_redact_json(item, depth + 1) for item in obj]
    if isinstance(obj, str) and _value_is_dangerous(obj):
        # a bare dangerous string sitting in an array
        return _redact_signed_or_mask(obj)
    return obj


# A conservative line scrub for non-JSON text bodies: redact any whitespace-
# bounded token that is itself signed/JWT/long-token. Free text can still
# embed a signed media URL (e.g. an HLS master playlist, an HTML data-attr),
# so we cannot store it raw.
_TOKENISH = re.compile(r"\S+")

# v3.66.705 (GUARD): a token carried by an ATTRIBUTE was slipping through. _TOKENISH is
# whitespace-bounded, so `data-token="eyJhbGci..."` is ONE token, and the end-only strip
# below leaves the `data-token="` prefix in place -- which means _value_is_dangerous's
# ANCHORED patterns (^eyJ..., ^[A-Za-z0-9_-]{32,}) never matched and the secret was
# stored RAW. That contradicted this scrub's own reason for existing (see the comment
# above: "an HTML data-attr"). So the value BEHIND a `key=` / `key:` prefix is now tested
# too. Only the dangerous VALUE is replaced -- the attribute name and the surrounding
# markup survive, because the capture exists to preserve that structure.
_ATTR_PREFIX = re.compile(r"""^["']?[\w:.\-]+["']?\s*[=:]\s*["']?""")
# A quoted value inside the token: `data-token="eyJ..."` -> eyJ... . Needed because the
# whitespace-bounded token also carries the TRAILING markup (`">x</a>`), which defeats
# _value_is_dangerous's anchored patterns just as the leading prefix did.
_QUOTED = re.compile(r"""["']([^"'\s]+)["']""")
_UNQUOTED_VALUE = re.compile(r"""^[^"'<>]+""")
_TRIM = "\"'<>(),;{}[]"


def _redact_text(text: str) -> str:
    def _maybe(m: "re.Match[str]") -> str:
        tok = m.group(0)
        # Strip common surrounding punctuation/quotes before testing.
        core = tok.strip(_TRIM)
        if _value_is_dangerous(core):
            return PLACEHOLDER
        # ...and the VALUE carried by an attribute/key (the 705 leak): both the quoted
        # form (`data-token="eyJ..."`, `"token":"eyJ..."`) and the bare form
        # (`session=eyJ...`). Only the dangerous value is replaced -- the attribute name
        # and surrounding markup survive, because the capture exists to preserve them.
        cands = list(_QUOTED.findall(tok))
        bare = _UNQUOTED_VALUE.match(_ATTR_PREFIX.sub("", core))
        if bare:
            cands.append(bare.group(0).strip(_TRIM))
        for c in cands:
            if c and c != core and _value_is_dangerous(c):
                return tok.replace(c, PLACEHOLDER, 1)
        return tok
    return _TOKENISH.sub(_maybe, text)


def content_type_of(headers) -> Optional[str]:
    """Pull the Content-Type from response headers, accepting either the CDP
    list-of-{name,value} shape or a flat mapping, matched case-insensitively.
    Returns None when absent. Content-Type is non-sensitive, so it survives
    capture_redact.scrub_headers and is still readable here after redaction."""
    if isinstance(headers, dict):
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == "content-type":
                return None if v is None else str(v)
    elif isinstance(headers, (list, tuple)):
        for item in headers:
            if isinstance(item, dict) and str(item.get("name", "")).lower() == "content-type":
                val = item.get("value")
                return None if val is None else str(val)
    return None


def redact_body(body: Any, content_type: Optional[str] = None) -> Any:
    """Capture-time body handler.

    With body capture DISABLED (default), or for a non-text/JSON content
    type, returns the length-marker form (identical to the prior contract).
    With it ENABLED for a text/JSON body, returns the body with all signing
    material redacted in place — never the raw body.
    """
    if body is None:
        return None
    if not bodies_enabled():
        return body_marker(body)
    if not (_is_text_or_json(content_type) or _is_manifest_ct(content_type)):
        # Not eligible for retention — keep only the shape marker.
        return body_marker(body)

    ct = (content_type or "").lower()

    # JSON path: parse, redact structurally, re-serialize.
    if "json" in ct:
        # Size guard (rec #3): never parse/retain an oversize JSON string —
        # collapse to the length marker, bounding what the text path already
        # bounded for text bodies.
        if isinstance(body, str) and len(body) > _MAX_JSON_LEN:
            return body_marker(body)
        parsed = None
        if isinstance(body, (dict, list)):
            parsed = body
        elif isinstance(body, str):
            try:
                parsed = json.loads(body)
            except (ValueError, TypeError):
                parsed = None
        if parsed is not None:
            redacted = _redact_json(parsed)
            try:
                out = json.dumps(redacted, ensure_ascii=False)
            except (TypeError, ValueError):
                return body_marker(body)
            # A redacted result over the cap (e.g. a large already-parsed
            # dict/list input that never hit the string check above) collapses to
            # the marker rather than being stored unbounded.
            if len(out) > _MAX_JSON_LEN:
                return body_marker(body)
            return out
        # Unparseable "json" → fall through to conservative text scrub.

    # text/* path (or unparseable JSON): conservative token scrub, capped.
    if isinstance(body, str):
        text = body[:_MAX_TEXT_LEN]
        return _redact_text(text)

    # Anything else → shape marker.
    return body_marker(body)
