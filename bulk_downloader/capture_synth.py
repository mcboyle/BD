"""C-T1 capture synthesis — static 2-capture diff → minimal config.

Detect-side only. Given TWO ``session_capture.to_capture_dict()`` recon
dicts of *the same download action*, produce a human-reviewable minimal
config that describes the invariant request flow and the per-request
parameter slots. This is the first ("MV", N=2) tier of capture
synthesis; the N>=3 anti-unification tier is C-T2.

This module is pure/offline: no network, no module-level work, no
import side effects. It is not imported by ``app.py`` and never replays
anything.

OUT OF SCOPE — by posture and/or sandbox (see CAPTURE_SYNTHESIS posture):
  * Live replay of any kind. The PLAN's Stage 5 (header necessity
    probe) and Stage 7 (self-replay validation) replay requests against
    the live site — that is Tier-B replay, which is posture-gated AND
    not runnable in the network-denied sandbox. Not built. Validation
    here is the static structural cross-check only (``cross_check``).
  * Interactive selector synthesis (clicks/inputs) — that is C-T2.
  * Sourcing / templating / reconstructing credential parameters
    (token, sig, expires, cookie, authorization, ...). These are
    redacted at capture time. The synthesizer SURFACES that the flow
    requires them (structure) and stops there. Deriving or assembling a
    signed/short-lived request is exactly what the posture forbids. As
    defense-in-depth, a parameter whose VALUE is itself a signed URL
    (e.g. a CloudFront URL carried URL-encoded inside another request's
    query) is detected via the shared signed-URL marker set, slotted as
    a credential, and never echoed — even if the capture was
    under-scrubbed upstream.
  * Response-body dataflow. Capture redaction keeps only a length
    marker for bodies, so body-sourced parameters resolve to
    ``source-unknown`` rather than being guessed.

The output is its OWN artifact, not a field on a runtime sites_config
entry: ``app._load_sites_config`` keeps only ``CFG_FIELDS`` keys, so a
synthesized flow attached to a site entry would be silently dropped on
load. Folding an approved synthesis into a live runner is a separate,
deliberate step.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, unquote

from .capture_redact import PLACEHOLDER, SENSITIVE_HEADER, SENSITIVE_QS_KEY
# Reuse the single source of truth for signed/short-lived-stream
# detection rather than reinventing the marker set here.
from .netlog_classify import _is_signed as _signed_url_markers

CAPTURE_SYNTH_VERSION = 1

# A value is "redacted" if it is the bare placeholder or a body marker
# (``<scrubbed>(len=N)`` / ``<scrubbed>(json:...)``). Such values are
# credentials that were scrubbed at capture time — never traced.
_REDACTED_RE = re.compile(re.escape(PLACEHOLDER))


def _is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PLACEHOLDER)


def _value_is_signed(value: Any) -> bool:
    """True if a *parameter value* is itself a signed/short-lived URL.

    Signed media URLs are sometimes carried URL-encoded inside another
    request's query value (e.g. ``mediaResource=https%3A%2F%2F...%26
    Signature%3D...``), where the outer query-key redactor never sees
    them. Decode once and test with the shared signed-URL detector. This
    is defense-in-depth: the synthesizer must never echo a signed URL in
    the clear, regardless of how well the capture was scrubbed upstream.
    """
    if not isinstance(value, str) or not value:
        return False
    return _signed_url_markers(unquote(value))


# v3.66.86 (VC-0021) — value-shape corroboration for signing. Some sites sign
# with short or unconventional QUERY param names (nubile: e=<token>&st=<expiry>)
# that the name-based marker list (SENSITIVE_QS_KEY) does not match. Rather than
# enumerate brittle short names, recognize signing by CORROBORATING two facts the
# capture already provides: the value VARIES across same-title captures (signing
# is per-session and short-lived) AND it is shaped like signing material (an
# opaque token, a JWT, or a unix expiry timestamp). The two together catch e/st
# without naming them, and structurally exclude content identities — a per-title
# id is invariant across same-title captures, so it never varies here.
_SIGNING_VALUE_SHAPES = frozenset({"token", "jwt", "unix_ts"})


def _value_shape_is_signing(va: Any, vb: Any) -> bool:
    if not isinstance(va, str) or not isinstance(vb, str):
        return False
    if va == vb:                       # invariant -> identity-like, not signing
        return False
    return bool({classify_value(va), classify_value(vb)} & _SIGNING_VALUE_SHAPES)


# ── parameter shape classification (PLAN Stage 2 table) ────────────
_SHAPE_RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("uuid", re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}$", re.I)),
    ("sha256", re.compile(r"^[0-9a-f]{64}$", re.I)),
    ("md5", re.compile(r"^[0-9a-f]{32}$", re.I)),
    ("iso8601", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                          r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")),
    ("unix_ts", re.compile(r"^\d{10,13}$")),
    ("id", re.compile(r"^\d{1,9}$")),
    ("jwt", re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
                      r"(?:\.[A-Za-z0-9_-]+)?$")),
    ("filename", re.compile(r"^[A-Za-z0-9._-]{1,40}\.[A-Za-z0-9]{1,6}$")),
    ("token", re.compile(r"^[A-Za-z0-9_-]{20,}$")),
]


def classify_value(value: Any) -> str:
    """Infer a parameter's shape. ``redacted`` for scrubbed credentials,
    ``opaque`` when nothing matches, ``empty`` for blank/non-str."""
    if _is_redacted(value):
        return "redacted"
    if not isinstance(value, str) or value == "":
        return "empty"
    for label, rule in _SHAPE_RULES:
        if rule.match(value):
            return label
    return "opaque"


# ── request keying (consistent with session_capture._request_key) ──
def _request_key(entry: Dict[str, Any]) -> str:
    url = entry.get("url") or ""
    try:
        parts = urlsplit(url)
        loc = f"{parts.netloc}{parts.path}"
    except Exception:
        loc = url.split("?", 1)[0]
    return f"{(entry.get('method') or 'GET').upper()} {loc}"


def _query_pairs(url: str) -> List[Tuple[str, str]]:
    """Ordered (key, value) pairs from a URL query, preserving repeats.
    We parse manually (not parse_qs) so a redacted ``token=<scrubbed>``
    value round-trips verbatim and isn't URL-decoded."""
    if not isinstance(url, str) or "?" not in url:
        return []
    qs = url.partition("?")[2]
    out: List[Tuple[str, str]] = []
    for pair in qs.split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        out.append((k, v))
    return out


def _url_template(url: str, query_slot_keys: set,
                  path_slot: Optional[Tuple[int, str]] = None) -> str:
    """Rewrite the URL into a template. Varying query values become
    ``{key}`` slots; if ``path_slot`` is ``(index, name)`` the path
    segment at ``index`` becomes ``{name}``. Non-slot parts stay literal.
    """
    base = url.partition("?")[0]
    if path_slot is not None:
        idx, name = path_slot
        sp = urlsplit(base)
        segs = sp.path.split("/")
        if 0 <= idx < len(segs):
            segs[idx] = "{" + name + "}"
            new_path = "/".join(segs)
            # base = scheme://host + path (no query); swap the path tail.
            base = base[:len(base) - len(sp.path)] + new_path
    pairs = _query_pairs(url)
    if not pairs:
        return base
    rendered = [f"{k}={{{k}}}" if k in query_slot_keys else f"{k}={v}"
                for k, v in pairs]
    return f"{base}?{'&'.join(rendered)}"


def _path_segments(url: str) -> List[str]:
    return urlsplit(url).path.split("/") if isinstance(url, str) else []


def _single_path_seg_diff(url_a: str, url_b: str) -> Optional[int]:
    """If two URLs share host and differ in exactly ONE non-empty path
    segment (same segment count), return that segment index; else None.
    This is the guard against false pairings at N=2 — two genuinely
    different endpoints sharing a prefix won't match (they'd differ in
    >1 segment or in count)."""
    pa, pb = urlsplit(url_a), urlsplit(url_b)
    if pa.netloc != pb.netloc or not pa.netloc:
        return None
    sa, sb = pa.path.split("/"), pb.path.split("/")
    if len(sa) != len(sb):
        return None
    diffs = [i for i in range(len(sa)) if sa[i] != sb[i]]
    if len(diffs) != 1:
        return None
    i = diffs[0]
    if not sa[i] or not sb[i]:
        return None
    return i


# ── static dataflow (PLAN Stage 3, replay-free subset) ─────────────
_MEDIA_EXT = (".mp4", ".m3u8", ".mpd", ".ts", ".m4s", ".webm", ".mov",
              ".mkv", ".m4v", ".m4a", ".mp3", ".key")


def _looks_like_media(url: str) -> bool:
    path = urlsplit(url).path.lower() if isinstance(url, str) else ""
    return path.endswith(_MEDIA_EXT)


# v3.66.83 (VC-0014) — distinguish an HLS/DASH MANIFEST (the recognized download
# entry point) from its SEGMENTS, so goal-selection can prefer the manifest.
_MANIFEST_EXT = (".m3u8", ".mpd", ".ism", ".isml", ".f4m")
_SEGMENT_EXT = (".ts", ".m2ts", ".mts", ".m4s")


def _media_kind(url: str) -> str:
    """manifest | segment | progressive — by path extension (query stripped)."""
    path = urlsplit((url or "").partition("?")[0]).path.lower()
    if path.endswith(_MANIFEST_EXT):
        return "manifest"
    if path.endswith(_SEGMENT_EXT):
        return "segment"
    return "progressive"


def _header_values(entry: Dict[str, Any], which: str) -> List[Tuple[str, str]]:
    hdrs = entry.get(which) or []
    out: List[Tuple[str, str]] = []
    if isinstance(hdrs, list):
        for h in hdrs:
            if isinstance(h, dict):
                out.append((str(h.get("name", "")), str(h.get("value", ""))))
    elif isinstance(hdrs, dict):
        for k, v in hdrs.items():
            out.append((str(k), str(v)))
    return out


def _build_ctx_sources(cap: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Ordered (label, haystack) page-context sources a parameter value
    may originate from, highest-priority first. Credential-bearing fields
    (cookies, local_storage, session_storage) are deliberately excluded —
    they are redacted and must never be traced into or surfaced.
    """
    out: List[Tuple[str, str]] = []
    out.append(("page_url",
                f"{cap.get('url') or ''} {cap.get('search') or ''}"))
    ref = cap.get("referrer")
    if isinstance(ref, str) and ref:
        out.append(("referrer", ref))
    for m in (cap.get("meta_tags") or []):
        if isinstance(m, dict):
            name = (m.get("property") or m.get("name")
                    or m.get("itemprop") or "meta")
            c = m.get("content")
            if isinstance(c, str) and c:
                out.append((f"meta:{name}", c))
    for pe in (cap.get("player_elements") or []):
        if not isinstance(pe, dict):
            continue
        attrs = pe.get("attributes")
        if isinstance(attrs, dict):
            for ak, av in attrs.items():
                if isinstance(av, str) and av:
                    out.append((f"player_attr:{ak}", av))
        for s in (pe.get("sources") or []):
            if isinstance(s, dict):
                src = s.get("src") or s.get("url")
                if isinstance(src, str) and src:
                    out.append(("player_source", src))
            elif isinstance(s, str) and s:
                out.append(("player_source", s))
    for st in (cap.get("script_tags_of_interest") or []):
        if isinstance(st, dict):
            c = st.get("content")
            if isinstance(c, str) and c:
                out.append(("script_config", c))
    jg = cap.get("js_globals")
    if isinstance(jg, dict) and jg:
        try:
            out.append(("js_global", json.dumps(jg, ensure_ascii=False)))
        except (TypeError, ValueError):
            pass
    return out


def _body_haystack(entry: Dict[str, Any]) -> Optional[str]:
    """Return a prior response body as a searchable string IFF it was
    retained (C-T2 body capture on) and is not a length marker or a fully
    redacted blob. Returns None when the body is absent, a marker, or
    redacted — so tracing only ever resolves a value to a body that
    genuinely still contains it. Signing material in a retained body is
    already <scrubbed> by the capture-time redactor, so a credential can
    never be traced to a body (correct: credentials are surfaced as
    credential params, never given a benign source)."""
    body = entry.get("response_body")
    if not isinstance(body, str) or not body:
        return None
    # A length marker ("<scrubbed>(len=N)") or a bare placeholder is not real
    # content — skip it so we don't false-match on the placeholder text.
    if body.startswith(PLACEHOLDER):
        return None
    return body


def _trace_source(value: str, ctx_sources: List[Tuple[str, str]],
                  prior: List[Dict[str, Any]]) -> str:
    """Static, replay-free provenance for a *non-redacted* value. Returns
    a source label. Values shorter than 4 chars are not substring-traced
    (too noisy).

    Resolution order: page-context sources, then prior request/response
    *header* values, then prior retained response *bodies* (C-T2). Bodies
    are searched only from ``prior`` entries — provenance flows forward in
    time, so a value can only originate in a response that preceded the
    request using it. Body search is a no-op when body capture is off
    (bodies are length markers and skipped), so this stays inert by default.
    """
    if not value or len(value) < 4:
        return "source_unknown"
    for label, hay in ctx_sources:
        if value in hay:
            return label
    # Prior request/response *header* values (non-sensitive only — a
    # sensitive header's value is already <scrubbed>, so a match there is
    # impossible for a real value).
    for e in prior:
        for name, hv in _header_values(e, "request_headers"):
            if value == hv:
                return f"request_header:{name}"
        for name, hv in _header_values(e, "response_headers"):
            if value == hv:
                return f"response_header:{name}"
    # Prior retained response bodies (C-T2). Substring match: the value was
    # emitted somewhere in an earlier response body (e.g. an id/token a later
    # URL embeds). The request key identifies which response, so the
    # provenance is actionable. Walked last — headers/page-context are more
    # specific sources, so they take precedence.
    for e in prior:
        hay = _body_haystack(e)
        if hay and value in hay:
            return f"response_body:{_request_key(e)}"
    return "source_unknown"


def _query_param_slots(url_a: str, url_b: str, key: str,
                       ctx_sources: List[Tuple[str, str]],
                       prior: List[Dict[str, Any]]):
    """Build query parameter slots for one aligned request. Returns
    (params, slot_keys, unresolved, credentials). Shared by the
    common-request pass and the path-recovery pass."""
    pairs_a = dict(_query_pairs(url_a))
    pairs_b = dict(_query_pairs(url_b))
    all_keys = set(pairs_a) | set(pairs_b)
    redacted_keys = {k for k in all_keys
                     if _is_redacted(pairs_a.get(k))
                     or _is_redacted(pairs_b.get(k))}
    signed_keys = {k for k in all_keys
                   if _value_is_signed(pairs_a.get(k))
                   or _value_is_signed(pairs_b.get(k))}
    # A param whose KEY is a signing marker (Signature/Policy/Key-Pair-Id/
    # token/sig/expires...) is a credential even if its cleartext value
    # survived an under-scrubbed capture — mirrors the scrubber's policy.
    sensitive_keys = {k for k in all_keys if SENSITIVE_QS_KEY.search(k)}
    varying_keys = {k for k in all_keys
                    if pairs_a.get(k) != pairs_b.get(k)}
    slot_keys = varying_keys | redacted_keys | signed_keys | sensitive_keys

    params: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    creds: set = set()
    for pk in sorted(slot_keys):
        params.append(_classify_slot(pk, pairs_a.get(pk, ""),
                                     pairs_b.get(pk, ""), key, ctx_sources,
                                     prior, unresolved, creds, in_path=False))
    return params, slot_keys, unresolved, creds


def _classify_slot(name: str, va: str, vb: str, key: str,
                   ctx_sources, prior, unresolved, creds, *, in_path):
    """Build one parameter slot (query or path). Mutates unresolved/creds.
    Credential and signed-URL values are surfaced but never traced or
    echoed."""
    redacted = _is_redacted(va) or _is_redacted(vb)
    signed = (not redacted) and (_value_is_signed(va) or _value_is_signed(vb))
    key_sensitive = (not redacted and not signed
                     and bool(SENSITIVE_QS_KEY.search(name)))
    # v3.66.86 (VC-0021) — value-shape corroboration catches signing whose NAME
    # the marker list misses (short/unconventional). Only consulted after the
    # name- and value-URL checks, so it never reclassifies what they already own.
    value_shape = (not redacted and not signed and not key_sensitive
                   and _value_shape_is_signing(va, vb))
    where = "path" if in_path else "query"
    if redacted or signed or key_sensitive or value_shape:
        creds.add(f"{name} ({where})")
        if redacted:
            ptype, mask, basis = "redacted", None, "redacted"
        elif signed:
            ptype, mask, basis = "signed_url", "<signed_url>", "value_url"
        elif key_sensitive:
            ptype, mask, basis = "credential", "<credential>", "marker_name"
        else:
            # varies across same-title captures + signing-shaped value
            ptype, mask, basis = "signing_value", "<signing_value>", "value_shape"
        return {
            "key": name,
            "type": ptype,
            "credential": True,
            "basis": basis,
            "in_path": in_path,
            "value_a": va if mask is None else mask,
            "value_b": vb if mask is None else mask,
            "source": "redacted_credential",
        }
    src = _trace_source(va, ctx_sources, prior)
    if src == "source_unknown":
        unresolved.append({
            "request": key, "param": name,
            "reason": "source-unknown: not in entry URL, page context, "
                      "prior non-sensitive headers, or retained response "
                      "bodies (enable BD_CAPTURE_BODIES to widen body search)",
        })
    return {
        "key": name,
        "type": classify_value(va or vb),
        "credential": False,
        "in_path": in_path,
        "value_a": va,
        "value_b": vb,
        "source": src,
    }


def _credential_header_names(entry: Dict[str, Any]) -> List[str]:
    names = []
    for name, hv in _header_values(entry, "request_headers"):
        if _is_redacted(hv) or SENSITIVE_HEADER.search(name):
            names.append(name)
    return sorted(set(names))


def synthesize(cap_a: Dict[str, Any], cap_b: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize a minimal config from two recon captures of one action.

    Parameters are dicts as produced by
    :meth:`session_capture.SessionCapture.to_capture_dict` (page context
    flattened to top level + ``network_log``). Returns a self-contained,
    human-reviewable config dict — see module docstring for the scope and
    why it is its own artifact rather than a sites_config field.
    """
    log_a = cap_a.get("network_log") or []
    log_b = cap_b.get("network_log") or []

    # Index by request key, preserving capture order (lowest seq first).
    def index(log):
        idx: Dict[str, List[dict]] = {}
        for e in sorted(log, key=lambda x: x.get("seq", 0)):
            idx.setdefault(_request_key(e), []).append(e)
        return idx

    ia, ib = index(log_a), index(log_b)
    keys_a, keys_b = set(ia), set(ib)
    common = keys_a & keys_b
    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)

    # Skeleton = requests common to both captures, ordered by their seq
    # in capture A. (Replay-based reachability pruning — PLAN Stage 4 —
    # is out of scope; common-to-both is the best static approximation.)
    ordered_keys = [k for k in
                    sorted(common, key=lambda k: ia[k][0].get("seq", 0))]

    requests: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    credentials_required: set = set()

    ctx_sources = _build_ctx_sources(cap_a)

    def _prior(seq_val):
        return [ia[k][0] for k in ordered_keys
                if ia[k][0].get("seq", 0) < seq_val]

    def _make_request(ea, eb, key, *, path_slot=None):
        url_a, url_b = ea.get("url") or "", eb.get("url") or ""
        prior = _prior(ea.get("seq", 0))
        params, slot_keys, unres, creds = _query_param_slots(
            url_a, url_b, key, ctx_sources, prior)
        has_path_slot = False
        if path_slot is not None:
            idx, name = path_slot
            sa, sb = _path_segments(url_a), _path_segments(url_b)
            pv_a = sa[idx] if 0 <= idx < len(sa) else ""
            pv_b = sb[idx] if 0 <= idx < len(sb) else ""
            params = [_classify_slot(name, pv_a, pv_b, key, ctx_sources,
                                     prior, unres, creds, in_path=True)] + params
            has_path_slot = True
        unresolved.extend(unres)
        credentials_required.update(creds)
        cred_hdrs = _credential_header_names(ea)
        for h in cred_hdrs:
            credentials_required.add(f"{h} (header)")
        classification = ("varying" if (slot_keys or has_path_slot)
                          else "invariant")
        return {
            "seq": ea.get("seq", 0),
            "method": (ea.get("method") or "GET").upper(),
            "key": key,
            "url_template": _url_template(url_a, slot_keys, path_slot),
            "classification": classification,
            "type": ea.get("type"),
            "is_media": _looks_like_media(url_a),
            "params": params,
            "credential_headers": cred_hdrs,
            "recovered_path_param": path_slot is not None,
            "goal": False,
        }

    for key in ordered_keys:
        requests.append(_make_request(ia[key][0], ib[key][0], key))

    # Path-recovery pass: a request that embeds its varying id in the
    # PATH (e.g. /videos/10001/master.m3u8) lands in only_in_* because its
    # request key (method+host+path) differs across captures. Re-pair an
    # only-in-A request with an only-in-B request whose path matches modulo
    # exactly one non-empty segment (same method/host/segment-count). N=2,
    # low confidence — _single_path_seg_diff is the guard against pairing
    # two genuinely different endpoints that merely share a prefix.
    recovered_a, recovered_b = set(), set()
    for ka in only_a:
        ea = ia[ka][0]
        ma = (ea.get("method") or "GET").upper()
        for kb in only_b:
            if kb in recovered_b:
                continue
            eb = ib[kb][0]
            if ma != (eb.get("method") or "GET").upper():
                continue
            idx = _single_path_seg_diff(ea.get("url") or "",
                                        eb.get("url") or "")
            if idx is None:
                continue
            requests.append(_make_request(ea, eb, ka, path_slot=(idx,
                                                                  f"path{idx}")))
            recovered_a.add(ka)
            recovered_b.add(kb)
            break

    requests.sort(key=lambda r: r.get("seq", 0))
    only_a = [k for k in only_a if k not in recovered_a]
    only_b = [k for k in only_b if k not in recovered_b]

    # Goal = the media request the user is trying to download. Prefer an HLS/DASH
    # MANIFEST (.m3u8/.mpd) over its SEGMENTS (.ts/.m4s): segments stream
    # continuously so the highest-seq media request is a late segment, but the
    # manifest is the recognized download target (classify_url -> hls_manifest).
    # Recognition-only — preferring the manifest surfaces it as a signed HLS entry
    # point; it is never reassembled. v3.66.83 (VC-0014). Heuristic; flagged for
    # operator review.
    media = [(i, r) for i, r in enumerate(requests) if r["is_media"]]
    manifests = [(i, r) for i, r in media
                 if _media_kind(r["url_template"]) == "manifest"]
    segments = [(i, r) for i, r in media
                if _media_kind(r["url_template"]) == "segment"]
    goal_idx = None
    goal_reason = None
    if manifests:
        goal_idx = max(manifests, key=lambda t: t[1].get("seq", 0))[0]
        goal_reason = "hls_manifest_preferred"
    elif media:
        goal_idx = max(media, key=lambda t: t[1].get("seq", 0))[0]
        goal_reason = "highest_seq_media"
    elif requests:
        goal_idx = len(requests) - 1
        goal_reason = "last_request_fallback"
    if goal_idx is not None:
        requests[goal_idx]["goal"] = True

    def _cand(r):  # query-stripped — never echo signing values
        return {"seq": r.get("seq", 0),
                "url": (r["url_template"] or "").partition("?")[0],
                "kind": _media_kind(r["url_template"])}
    goal_selection = {
        "reason": goal_reason,
        "selected": _cand(requests[goal_idx]) if goal_idx is not None else None,
        "manifest_candidates": [_cand(r) for _, r in manifests],
        "segment_candidates": [_cand(r) for _, r in segments[:5]],
        "n_segment_candidates": len(segments),
        "n_media_alternatives": len(media),
        "note": (
            "HLS/DASH manifest preferred over segment(s) as the recognized "
            "download target; recognition-only — the signed manifest is surfaced, "
            "never reassembled."
            if goal_reason == "hls_manifest_preferred"
            else "highest-seq media request (no manifest present)"),
    }

    notes = [
        "N=2 capture: an 'invariant' may be coincidental — confirm with "
        ">=3 captures (C-T2) before relying on this flow.",
        "Static synthesis only: no live replay, no header necessity probe, "
        "no self-replay validation (posture + sandbox).",
    ]
    if only_a or only_b:
        notes.append(
            f"{len(only_a) + len(only_b)} request(s) appeared in only one "
            "capture (session-specific noise or unpruned reachability) — "
            "excluded from the skeleton; listed under 'session_specific'.")
    n_recovered = sum(1 for r in requests if r.get("recovered_path_param"))
    if n_recovered:
        notes.append(
            f"{n_recovered} request(s) recovered via path-segment matching "
            "(varying id embedded in the URL path). N=2 path pairing is "
            "low-confidence — verify these.")

    return {
        "capture_synth_version": CAPTURE_SYNTH_VERSION,
        "synthesized": True,
        "needs_review": True,
        "confidence": "low",  # N=2 is structurally low-confidence
        "host": cap_a.get("host") or urlsplit(cap_a.get("url") or "").netloc,
        "entry_url": cap_a.get("url"),
        "requests": requests,
        "goal_selection": goal_selection,
        "credentials_required": sorted(credentials_required),
        "unresolved": unresolved,
        "session_specific": {"only_in_a": only_a, "only_in_b": only_b},
        "summary": (
            f"{len(requests)} load-bearing request(s) "
            f"({sum(1 for r in requests if r['classification'] == 'varying')}"
            f" varying), {len(credentials_required)} required credential(s), "
            f"{len(unresolved)} unresolved parameter(s)"),
        "notes": notes,
    }


def cross_check(synth: Dict[str, Any],
                site_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Static (replay-free) structural cross-check of a synthesized config
    against an existing ``sites_config.json`` entry — the sandbox-safe
    stand-in for Stage 7 self-replay validation.

    Confirms the synthesized host matches the known site's host. Returns
    a small report; does NOT execute anything.
    """
    syn_host = (synth.get("host") or "").lower()
    cfg_host = ""
    for field in ("domain", "success_url", "login_url", "url"):
        val = site_cfg.get(field)
        if val:
            cfg_host = urlsplit(val if "//" in val else f"//{val}").netloc.lower() or val.lower()
            if cfg_host:
                break
    host_match = bool(syn_host) and bool(cfg_host) and (
        syn_host == cfg_host or syn_host.endswith("." + cfg_host)
        or cfg_host.endswith("." + syn_host))
    return {
        "synth_host": syn_host,
        "config_host": cfg_host,
        "host_match": host_match,
        "checked": "host_only",
        "note": "structural host check only; live replay validation is "
                "out of scope (posture + sandbox)",
    }
