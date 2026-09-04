"""Derivation-boundary redaction for capture-DERIVED artifacts (F2 / wave 166).

``capture_redact`` scrubs a RAW capture (credential headers, bodies, signed-URL
query strings) at capture time and post-hoc. This module is the complementary
*derivation-boundary* scrubber: it runs over the structured artifacts we
DERIVE from a real, authenticated capture — a template draft, a normalized
review candidate, an offline-analysis summary — right before any of them can
become durable (a saved draft, a committed fixture, a KB doc, a test).

It is **value-content driven, never key-name driven**. A template legitimately
uses keys like ``email`` / ``password`` / ``key`` for SELECTOR SHAPES
(``input#email``, ``input[type="password"]``), and those must survive intact.
So a string is redacted only when its *content* looks like a secret:

  * an email address,
  * a JWT (``eyJ…`` header.payload[.signature]),
  * a signed / credentialed URL query string (reusing the SoT ``redact_query``),
  * URL-authority or bare-authority userinfo (``scheme://user:pass@host`` /
    ``user:pass@host``),
  * a ``key=secret`` pair embedded in a free string (cookies, form bodies,
    query fragments), or
  * a long opaque token (e.g. a Cloudflare Turnstile / session blob).

Structure-only evidence is preserved verbatim: hostnames, counts, status
codes, content types, endpoint/media templates (``…/AVC_{resolution}.mp4``),
selector shapes, and the capture-file SHA-256 (a content hash, not a secret).

Two entry points:
  * :func:`redact_artifact` — return a deep-redacted copy of a derived object.
  * :func:`scan_artifact_secrets` — return a list of ``(path, kind)`` residual
    findings. A correctly-redacted artifact scans clean (``[]``); that is how
    callers and tests *prove* no sensitive value is persisted.

Posture: redaction only — it removes secrets; it never reconstructs, replays,
or evades. It operates purely on already-derived data structures and does NOT
touch ``extraction-core``, ``dom_recorder``, ``dom_capture``, or any runtime
capture path.
"""
from __future__ import annotations

import re
from typing import Any, List, Tuple

from .capture_redact import (PLACEHOLDER, SENSITIVE_QS_KEY, apply_url_mode,
                             redact_path_signing, redact_query)
from .redaction_profile import KEEP_FULL, KEEP_STRUCTURE, current_profile

# ── Value detectors (content-based) ──────────────────────────────────────────

# ── ReDoS hardening (v3.66.291) ──────────────────────────────────────────────
# Every detector below puts an UPPER BOUND on each greedy run ({m,n}, not {m,}).
# Without it, a long contiguous [A-Za-z0-9_-] string (JWPlayer framework class
# chains, concatenated ids, base64/data blobs in a DOM excerpt) that LACKS the
# trailing delimiter ('@', ':', '=', '.') drove these patterns into catastrophic
# (_KV_SECRET) or quadratic (email/userinfo) backtracking, pinning the WACZ
# export at 100% CPU indefinitely. The bounds are far larger than any real secret
# (RFC local-part 64 / domain 255, etc.), so matching is byte-for-byte unchanged
# — only the worst case is now linear. See test_v3_66_291_redaction_redos.

# An email address. The alpha TLD of >=2 means retina "@2x" and package
# specifiers like "pkg@1.2.3" do NOT match — only real addresses do.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}")

# A JSON Web Token. The "eyJ" prefix is the base64url of '{"' that begins
# virtually every JWT header; require at least a header.payload.
_JWT_RE = re.compile(
    r"eyJ[A-Za-z0-9_\-]{6,8192}\.[A-Za-z0-9_\-]{6,8192}"
    r"(?:\.[A-Za-z0-9_\-]{1,8192})?")

# URL-authority userinfo:  scheme://<userinfo>@host  ->  scheme://host
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]{0,32}://)[^/@\s]{1,512}@")

# Bare-authority userinfo (no scheme):  user:pass@host  ->  host. The colon is
# required so this never eats an email (which has no ':' before the '@').
_BARE_USERINFO_RE = re.compile(
    r"[A-Za-z0-9._%+\-]{0,256}:[^/@\s:]{1,256}@(?=[A-Za-z0-9.\-]+)")

# A key=value pair whose KEY marks the value as a secret — cookies, form bodies,
# or a query fragment in a free string. v3.66.291: the key is a single bounded
# run (no embedded keyword alternation — that was the exponential-backtracking
# source); secrecy is decided by a post-match keyword check (_kv_key_is_secret).
# The leading boundary lookbehind stops the engine re-attempting at every offset
# inside one long token. The keyword set mirrors the SoT ``SENSITIVE_QS_KEY``.
_KV_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9_\-])(?P<k>[A-Za-z0-9_\-]{1,128})"
    r"=(?P<v>[^&;,\s\"']{1,4096})")
# Signing-METADATA markers: keys that appear in a signed URL but whose VALUE is
# not itself a credential -- an expiry timestamp, a CloudFront policy doc, a
# hash, and the non-secret AWS SigV4 fields (X-Amz-Date / X-Amz-Algorithm /
# X-Amz-SignedHeaders / X-Amz-Expires). The gated signed-query pass strips these
# on the default surface, but the ALWAYS-ON kv floor must NOT, because a
# keep_full network surface deliberately RETAINS them (test_v3_66_245: Expires
# must survive keep_full). Subtracting these from the SoT match before the
# credential re-check resolves the AWS SigV4 overlap correctly:
# X-Amz-Signature -> 'signature' survives the strip and still matches (scrub);
# X-Amz-Expires   -> nothing matches after the strip and is kept.
_SIGNING_META_ONLY = re.compile(r"(?:expires|policy|hash|x-amz-)", re.I)
# Credential markers that the kv floor has always scrubbed but which live in the
# HEADER source-of-truth (``SENSITIVE_HEADER``: bearer / x-csrf / x-xsrf), not the
# query ``SENSITIVE_QS_KEY``. They can appear as bare kv keys in a cookie/body, so
# the floor matches them explicitly -- preserving the original tuple's coverage
# without widening the query SoT (which would change the gated signed-query pass).
_KV_CRED_EXTRA = re.compile(r"(?:csrf|xsrf|bearer)", re.I)
def _kv_key_is_secret(k: str) -> bool:
    """True if a key=value key marks its value as a CREDENTIAL -- the always-on
    floor scrubs it even under keep_full.

    VR-P03: the query-secret portion of the credential class is *derived from* the
    single source of truth ``SENSITIVE_QS_KEY`` (imported from capture_redact)
    rather than a hand-maintained keyword copy that had drifted. The old
    ``_KV_SECRET_KEYWORDS`` tuple was missing the anchored OAuth-fragment keys
    ``code``/``state`` plus ``apikey``/``challenge``/``captcha``/``nonce``/
    ``otp``, so those exchangeable secrets survived the kv floor (an auth
    ``#code=...&state=...`` landing in capture.json). But the SoT *also* flags
    signing-METADATA keys (``expires``/``policy``/``hash``/``x-amz-*``) which the
    gated signed-query pass strips on the default surface yet a keep_full surface
    legitimately RETAINS; a wholesale delegation over-strips a kept signed URL (it
    scrubbed ``Expires``, regressing test_v3_66_245). So we take the SoT match and
    subtract the signing-metadata markers: if a credential marker still matches the
    residue, the value is secret-bearing. That portion is therefore a strict SUBSET
    of the query SoT (it can never scrub a query key the SoT would not flag), while
    the OAuth-fragment leak is closed and the AWS SigV4 overlap resolves correctly
    (``X-Amz-Signature`` scrubs, ``X-Amz-Expires`` is kept). The three header-origin
    credential markers ``csrf``/``xsrf``/``bearer`` -- which the original floor
    scrubbed but the query SoT never carried -- are matched explicitly via
    ``_KV_CRED_EXTRA`` so prior coverage is preserved. The SoT's deliberate
    anchoring is kept (``code``/``state`` match only as a whole key, never inside
    ``barcode``/``estate``)."""
    k = k or ""
    if _KV_CRED_EXTRA.search(k):
        return True
    if not SENSITIVE_QS_KEY.search(k):
        return False
    return bool(SENSITIVE_QS_KEY.search(_SIGNING_META_ONLY.sub("", k)))

_TOKEN_CHARSET_RE = re.compile(r"^[A-Za-z0-9._=\-]+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_DEC_RE = re.compile(r"^[0-9]+$")
# Below this length a blob is not treated as an opaque credential token.
_OPAQUE_MIN = 40

# ── Recursion-depth guard (deep-DOM export crash) ─────────────────────────────
# The redaction walkers and the floor scanner recurse once per dict/list level;
# a captured DOM nested thousands of elements deep (a hostile/anti-capture page
# or pathological generated content) drove them past Python's recursion limit,
# raising RecursionError out of build_wacz_bytes -> write_wacz and losing the
# WHOLE capture (export aborts around DOM depth ~500). A subtree deeper than this
# cap collapses to PLACEHOLDER, keeping the redacted copy shallow for every
# consumer (redact / scan / json.dumps). The cap counts dict/list levels (~2 per
# DOM nesting level), so 500 ~= DOM depth 250 — far beyond any real DOM (~150),
# leaving real captures byte-identical while a pathological tree is truncated
# gracefully instead of crashing. See test_capture_redact_deep_dom.
_MAX_WALK_DEPTH = 500


def _looks_like_opaque_token(s: str) -> bool:
    """True for a long, structure-free, high-entropy credential blob.

    Conservative on purpose so structure-only evidence survives:
      * must be >= ``_OPAQUE_MIN`` chars, contain no whitespace, and contain no
        ``{``/``}`` (those are endpoint/media templates, never secrets);
      * must not begin like a URL / path / selector / regex anchor;
      * must be pure token charset; and
      * a pure-hex (e.g. a SHA-256 capture hash) or pure-decimal (an id) blob
        is spared — those are content hashes / ids, not credentials. A token is
        only flagged when it carries a base64url/separator char or a letter
        outside the hex range (Turnstile/session blobs do).
    """
    if len(s) < _OPAQUE_MIN or " " in s or "{" in s or "}" in s:
        return False
    if s[:1] in ("/", "#", "[", "^", "<", ".") or s.startswith(("http://", "https://")):
        return False
    if not _TOKEN_CHARSET_RE.match(s):
        return False
    if _HEX_RE.match(s) or _DEC_RE.match(s):
        return False
    # A dotted / underscored lowercase identifier path — a schema name or a
    # dotted module path, e.g. "bulk_downloader.template.review_candidate.v1" —
    # is structure, not a credential. Real token blobs (JWT/Turnstile/session)
    # carry mixed case, base64url separators, or numeric-leading segments and so
    # do not fullmatch this; they remain flagged.
    if re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9][a-z0-9_]*)*", s):
        return False
    # A CSS class-chain selector shape (``tag.class.class…``, hyphenated class
    # names allowed) is STRUCTURE, not a credential — long framework class
    # chains (JW Player et al.) routinely exceed 40 chars and would otherwise be
    # mis-scrubbed, destroying the recorded selector. A real opaque blob is a
    # single contiguous run; a dotted chain whose every segment is a CSS
    # identifier (letter-led, no base64 ``=`` padding) is a selector. JWTs,
    # userinfo, and kv-secrets are already handled by the earlier passes, so this
    # only NARROWS the whole-string catch-all (never forgives a hard credential).
    if "=" not in s and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)+", s):
        return False
    return True


def redact_value(s: str, *, redact_signed_query: bool = True,
                 redact_kv: bool = True, redact_emails: bool = True) -> str:
    """Redact secret *content* from a single string, preserving structure.

    Idempotent: a value already reduced to placeholders scans clean and is left
    alone on a second pass. v3.66.171: ``redact_signed_query`` / ``redact_kv`` /
    ``redact_emails`` let a profile-aware caller skip the query-string, key=secret,
    and/or email passes. For a URL-attribute DOM string the URL mode
    (``apply_url_mode``) owns the query, so the floor sweep skips both the query
    and kv passes to avoid re-scrubbing a query that ``keep_full`` deliberately
    kept. All default True → byte-identical to before.
    """
    if not isinstance(s, str) or not s:
        return s
    out = s
    # 1. URL-authority userinfo: scheme://creds@host -> scheme://host
    out = _URL_USERINFO_RE.sub(lambda m: m.group("scheme"), out)
    # 2. bare-authority userinfo: user:pass@host -> host
    out = _BARE_USERINFO_RE.sub("", out)
    # 3. signed / credentialed URL query strings (host + path kept)
    if redact_signed_query and "?" in out and ("://" in out or out.startswith("/")):
        out = redact_query(out)
    # 3b. D1: signing packed into a URL PATH as name=value assignments
    #     (``/key=<sig>,end=<epoch>,ip=<client ip>/``). Always on -- like the
    #     userinfo passes above and unlike the gated query pass -- because the
    #     run carries a live credential AND the operator's public IP, and no
    #     surface legitimately retains those. Scoped to path-segment assignment
    #     runs, so a query string stays the gated pass's business.
    out = redact_path_signing(out)
    # 4. key=secret pairs anywhere in the string (cookies / bodies / fragments).
    #    The pair regex matches ALL key=value runs; only those whose key marks a
    #    secret are redacted — selector/structure pairs (e.g. color=red) pass
    #    through verbatim.
    if redact_kv:
        out = _KV_PAIR_RE.sub(
            lambda m: (f"{m.group('k')}={PLACEHOLDER}"
                       if _kv_key_is_secret(m.group("k")) else m.group(0)),
            out)
    # 5. JWTs
    out = _JWT_RE.sub(PLACEHOLDER, out)
    # 6. emails
    if redact_emails:
        out = _EMAIL_RE.sub(PLACEHOLDER, out)
    # 7. a whole-string opaque token (Turnstile / session blob). Runs last so a
    #    string already carrying a placeholder from steps 4-6 is not re-touched.
    if _looks_like_opaque_token(out):
        out = PLACEHOLDER
    return out


def redact_artifact(obj: Any, _depth: int = 0) -> Any:
    """Deep-redact a capture-derived object, returning a redacted copy.

    Walks dicts/lists; redacts string leaves via :func:`redact_value`; leaves
    numbers / bools / None untouched (counts, status codes, flags). A subtree
    deeper than ``_MAX_WALK_DEPTH`` collapses to the placeholder (deep-DOM guard).
    """
    if _depth > _MAX_WALK_DEPTH and isinstance(obj, (dict, list, tuple)):
        return PLACEHOLDER
    if isinstance(obj, dict):
        return {k: redact_artifact(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_artifact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return redact_value(obj)
    return obj


# ── Scanner (the proof that nothing sensitive remains) ────────────────────────

def _value_findings(s: str) -> List[str]:
    """Kinds of residual secret detected in one string (empty == clean)."""
    kinds: List[str] = []
    if _EMAIL_RE.search(s):
        kinds.append("email")
    if _JWT_RE.search(s):
        kinds.append("jwt")
    if _URL_USERINFO_RE.search(s) or _BARE_USERINFO_RE.search(s):
        kinds.append("userinfo")
    if "?" in s and ("://" in s or s.startswith("/")):
        _, _, qs = s.partition("?")
        for pair in qs.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                if SENSITIVE_QS_KEY.search(k) and v and v != PLACEHOLDER:
                    kinds.append("signed_url")
                    break
    for m in _KV_PAIR_RE.finditer(s):
        if _kv_key_is_secret(m.group("k")) and m.group("v") != PLACEHOLDER:
            kinds.append("kv_secret")
            break
    if _looks_like_opaque_token(s):
        kinds.append("opaque_token")
    return kinds


def scan_artifact_secrets(obj: Any, _path: str = "$",
                          _depth: int = 0) -> List[Tuple[str, str]]:
    """Return ``[(json_path, kind), …]`` for every residual sensitive value.

    A redacted artifact returns ``[]``. Callers/tests assert emptiness to prove
    no email / token / signed URL / userinfo / credential pair is persisted.
    Stops descending past ``_MAX_WALK_DEPTH`` (the redactor truncates a subtree
    that deep to a placeholder, so there is nothing sensitive below it).
    """
    findings: List[Tuple[str, str]] = []
    if _depth > _MAX_WALK_DEPTH:
        return findings
    if isinstance(obj, dict):
        for k, v in obj.items():
            findings.extend(scan_artifact_secrets(v, f"{_path}.{k}", _depth + 1))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            findings.extend(scan_artifact_secrets(v, f"{_path}[{i}]", _depth + 1))
    elif isinstance(obj, str):
        for kind in _value_findings(obj):
            findings.append((_path, kind))
    return findings


# ── 7.6: LLM redaction safety-net (additive, read-only second pass) ──────────
#
# A locally-hosted model gets a SECOND look at the ALREADY-REDACTED artifact and
# FLAGS values that look secret-shaped but slipped past the deterministic rules
# (a novel token format, a secret in an unexpected field). It is strictly
# additive and read-only: it returns extra suspect findings for human review.
# It NEVER un-redacts, NEVER mutates the artifact, and CANNOT clear a
# deterministic finding — `redact_artifact` / `scan_artifact_secrets` remain the
# sole source of truth. No model -> no suspects (it never guesses).

_LLM_SUSPECT_MIN_LEN = 8


def llm_residual_suspects(redacted_obj: Any, *, llm=None,
                          _path: str = "$", _depth: int = 0):
    """Return ``[(json_path, "llm_suspect"), …]`` for values the injected model
    judges secret-shaped that the deterministic scan did NOT already flag.

    ``llm`` is a ``Callable[[str], bool]`` (production wires the local model;
    tests pass a fake). The walk is a read-only mirror of
    `scan_artifact_secrets` — it copies nothing, writes nothing, and only
    *appends* suspects. Returns ``[]`` when ``llm`` is None, on any model error,
    or when nothing is flagged (no false-positive storm — the model decides).
    """
    suspects = []
    if llm is None or _depth > _MAX_WALK_DEPTH:
        return suspects
    if isinstance(redacted_obj, dict):
        for k, v in redacted_obj.items():
            suspects.extend(
                llm_residual_suspects(v, llm=llm, _path=f"{_path}.{k}",
                                      _depth=_depth + 1))
    elif isinstance(redacted_obj, (list, tuple)):
        for i, v in enumerate(redacted_obj):
            suspects.extend(
                llm_residual_suspects(v, llm=llm, _path=f"{_path}[{i}]",
                                      _depth=_depth + 1))
    elif isinstance(redacted_obj, str):
        s = redacted_obj
        # Skip the placeholder, trivially-short strings, and anything the
        # deterministic pass already caught (that is handled, not a "miss").
        if (s != PLACEHOLDER and len(s) >= _LLM_SUSPECT_MIN_LEN
                and not _value_findings(s)):
            try:
                flagged = bool(llm(s))
            except Exception:
                flagged = False
            if flagged:
                suspects.append((_path, "llm_suspect"))
    return suspects


# ── Profile-aware capture scrub (v3.66.171) ──────────────────────────────────
# Applied at the WACZ export boundary over an ASSEMBLED capture dict. The
# network_log is already redacted at capture time (capture_redactor); this
# closes the DOM-embedded-URL + email gap and applies the credential FLOOR
# everywhere. URL signing in the DOM is governed by the dom_embedded_urls MODE;
# credential blobs (userinfo / JWT / kv-secret / opaque token) are ALWAYS
# scrubbed regardless of mode — keep_full relaxes signed-URL *structure*, never
# raw credentials.

_URL_LIKE_PREFIX = ("http://", "https://")


def _url_like(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    t = s.lstrip()[:8].lower()
    return t.startswith(_URL_LIKE_PREFIX) or "://" in s[:12]


def _dom_floor(s: str, do_email: bool, *, url_attr: bool, url_mode: str) -> str:
    """Credential floor for a DOM string whose URL query is already handled by
    ``apply_url_mode`` (bare URL attribute). For a bare URL under ``keep_full`` we
    skip the query + kv passes (preserve the kept signed URL); otherwise we run
    the kv pass so a query param the signing-key set misses (e.g. ``xref``) but
    the kv-secret detector flags is still scrubbed — keeping the redactor
    consistent with ``scan_floor_secrets``."""
    keep = url_attr and url_mode == KEEP_FULL
    return redact_value(s, redact_signed_query=False, redact_kv=not keep,
                        redact_emails=do_email)


def _walk_dom(o, url_mode, do_email, key=None, _depth=0):
    if _depth > _MAX_WALK_DEPTH and isinstance(o, (dict, list, tuple)):
        return PLACEHOLDER          # deep-DOM guard: collapse the deep subtree
    if isinstance(o, dict):
        return {k: _walk_dom(v, url_mode, do_email, key=k, _depth=_depth + 1)
                for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_walk_dom(v, url_mode, do_email, key=key, _depth=_depth + 1)
                for v in o]
    if isinstance(o, str):
        if key and str(key).lower() == "srcset" and "," in o:
            # srcset = "URL desc, URL desc" — redact each URL, keep descriptors.
            segs = []
            for seg in o.split(","):
                bits = seg.strip().split()
                if bits and _url_like(bits[0]):
                    bits[0] = apply_url_mode(bits[0], url_mode)
                segs.append(" ".join(bits))
            return _dom_floor(", ".join(segs), do_email, url_attr=True, url_mode=url_mode)
        if _url_like(o):
            # bare URL attribute (src/href/poster/currentSrc/...): mode-governed.
            return _dom_floor(apply_url_mode(o, url_mode), do_email,
                              url_attr=True, url_mode=url_mode)
        # Not a bare URL: a URL embedded in CSS / inline style / _cssText / text.
        # redact_value's whole-string query pass scrubs each sensitive param to a
        # single placeholder (idempotent on an already-scrubbed value), even under
        # keep_full — these are CSS/text refs, not the functional media src.
        return redact_value(o, redact_signed_query=True, redact_kv=True,
                            redact_emails=do_email)
    return o


def _floor_walk(o, do_email, redact_signed_query=True, _depth=0):
    if _depth > _MAX_WALK_DEPTH and isinstance(o, (dict, list, tuple)):
        return PLACEHOLDER          # deep-DOM guard: collapse the deep subtree
    if isinstance(o, dict):
        return {k: _floor_walk(v, do_email, redact_signed_query, _depth + 1)
                for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_floor_walk(v, do_email, redact_signed_query, _depth + 1)
                for v in o]
    if isinstance(o, str):
        # network_log is query-redacted at capture time, BUT other non-dom fields
        # (action_timeline effect URLs, route/media summaries, metadata) are NOT,
        # and the kv pass alone leaves non-secret signing params like `Expires`
        # that the floor's signed_url detector still flags. So under a non-keep_full
        # network surface (default/strip) run the full signed-query pass to fully
        # neutralize the query (host+path kept); under keep_full skip it so the
        # intended signing residual is preserved (scan_floor_secrets forgives it
        # on a keep_full surface). v3.66.245: closes the signed_url-survives-default
        # floor failure (WaczRedactionError on capture.json).
        return redact_value(o, redact_signed_query=redact_signed_query, redact_kv=True,
                            redact_emails=do_email)
    return o


def redact_capture(capture, profile=None):
    """Return a profile-aware redacted copy of an assembled capture dict.

    - ``dom_log``: bare URL attributes scrubbed per ``dom_embedded_urls`` mode
      (+ srcset split); URLs embedded in CSS/style/text scrubbed to keep_structure
      via the whole-string query pass; emails per ``emails``; the credential floor
      always.
    - everything else: credential floor + emails (network signing already done at
      capture time; idempotent on already-scrubbed values).
    Idempotent.
    """
    if not isinstance(capture, dict):
        return capture
    p = profile or current_profile()
    url_mode = p.get("dom_embedded_urls", "keep_structure")
    do_email = (p.get("emails") == "redact")
    # v3.66.245: non-dom fields follow the network surface for signed queries --
    # strip when not keep_full (default/strip), keep under keep_full. Symmetric
    # with scan_floor_secrets' keep_full forgiveness; closes the signed_url
    # residual that survived in non-network non-dom fields (e.g. action_timeline).
    net_signed_full = (p.get("network_signed_urls") == KEEP_FULL)
    out = dict(capture)
    if "dom_log" in out:
        out["dom_log"] = _walk_dom(out["dom_log"], url_mode, do_email)
    for k in list(out.keys()):
        if k == "dom_log":
            continue
        out[k] = _floor_walk(out[k], do_email,
                             redact_signed_query=(not net_signed_full))
    return out


def scan_floor_secrets(capture, profile=None):
    """Residual findings that VIOLATE the active profile — the build gate's proof.

    A signed-URL residual is *allowed* on a surface whose mode is ``keep_full``
    (that is the chosen setting, stamped ``reduced_redaction``); an email residual
    is allowed when ``emails`` is ``keep``. Credential blobs (jwt/userinfo/
    kv_secret/opaque) are NEVER allowed — they are the floor. Returns
    ``[(json_path, kind), …]``; empty means the capture honours its profile.
    """
    p = profile or current_profile()
    do_email = (p.get("emails") == "redact")
    dom_full = p.get("dom_embedded_urls") == KEEP_FULL
    net_full = p.get("network_signed_urls") == KEEP_FULL
    findings = []

    def _findings_for(o):
        # srcset / multi-URL string: scan each URL token separately so the flat
        # query parser isn't fooled by a second URL after a comma (a real single
        # URL never contains a raw space or a second "://").
        if isinstance(o, str) and o.count("://") > 1 and "," in o:
            ks = []
            for seg in o.split(","):
                toks = seg.strip().split()
                if toks:
                    ks.extend(_value_findings(toks[0]))
            return ks
        return _value_findings(o)

    def walk(o, path, in_dom, _depth=0):
        if _depth > _MAX_WALK_DEPTH:
            return                  # deep-DOM guard (matches the redactor's cap)
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}", in_dom or k == "dom_log", _depth + 1)
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]", in_dom, _depth + 1)
        elif isinstance(o, str):
            is_url = _url_like(o) or (o.count("://") > 1 and "," in o)
            # The signed_url/kv_secret detector (_value_findings) fires on a query
            # carried by a URL OR a root-relative path ("?" present AND ("://" or a
            # leading "/")), including signed URLs whose "://" sits past char 12 --
            # cases the stricter _url_like misses. keep_full must forgive the SAME
            # shape it can detect, or a kept signed URL like "/media/x?token=..." is
            # detected-but-never-forgiven even under keep_full. Hard credentials
            # (jwt/userinfo/opaque_token -- not in the skip set) and raw, non-URL kv
            # secrets (cookies/bodies, no query shape) remain NEVER forgiven.
            url_query_shape = is_url or (
                "?" in o and ("://" in o or o.startswith("/")))
            surface_full = (in_dom and dom_full) or (not in_dom and net_full)
            for kind in _findings_for(o):
                if kind == "email" and not do_email:
                    continue
                # On a keep_full URL surface, the kept query signing params
                # (reported as signed_url and/or kv_secret) are the intended
                # residual; hard credentials are still never allowed.
                if kind in ("signed_url", "kv_secret") and url_query_shape and surface_full:
                    continue
                findings.append((path, kind))
    walk(capture, "$", False)
    return findings


def _force_scrub_floor(capture, residual):
    """v3.66.470 DEFER-FLOOR-FAILOPEN: blunt-scrub the leaves at the floor-flagged
    paths to ``PLACEHOLDER`` and return a scrubbed COPY (the caller's object is
    untouched). ``residual`` is the ``[(json_path, kind), …]`` list from
    :func:`scan_floor_secrets`. Paths are reconstructed the SAME way the scanner
    builds them (``$`` root, ``.key`` for dicts, ``[i]`` for lists), so the match
    is exact and nothing outside the flagged set is touched. ``PLACEHOLDER`` scans
    clean by construction, so a forced scrub is guaranteed to clear that leaf.

    This is a strictly-MORE-scrubbing safety lever: it only ever replaces a flagged
    sensitive leaf with the placeholder. keep_full signed-URL residuals are never
    flagged by scan_floor_secrets (the surface_full skip), so they never reach this
    helper and a keep_full export stays byte-identical.
    """
    flagged = {p for p, _k in (residual or [])}
    if not flagged:
        return capture

    def rebuild(o, path):
        if isinstance(o, dict):
            return {k: rebuild(v, f"{path}.{k}") for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [rebuild(v, f"{path}[{i}]") for i, v in enumerate(o)]
        if isinstance(o, str) and path in flagged:
            return PLACEHOLDER
        return o

    return rebuild(capture, "$")
