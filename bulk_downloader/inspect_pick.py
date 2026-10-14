"""inspect_pick — observational element-inspector derivations (Track F, Wave A).

PURE, browser-free core for the capture HUD's element inspector + action
recorder. The live page side (``dom_overlay.picker_script``) reads a clicked
element into a JSON **descriptor** and hands it back through the
``__bd_inspect_pick`` binding; ``tools/capture_session`` resolves it here and
correlates it with the network log. Everything in this module is a pure
function of its inputs, so the selector/XPath/role/redaction/correlation logic
is exercisable on fixtures with no browser.

Posture (mirrors :mod:`bulk_downloader.dom_overlay` / ``capture_redactor``):

* What we KEEP is **structure** — the CSS selector and XPath of an element the
  operator pointed at. Selectors are what a reviewed template legitimately
  stores; they carry no per-session secret.
* What we REDACT is **values** — every value-bearing attribute (``href``,
  ``src``, ``data-*`` ids, ``value`` …) is scrubbed via
  :func:`capture_redact.redact_query` / ``PLACEHOLDER`` before it can be shown
  or persisted. A logged-in element's ``href`` may be a signed media URL; it
  never becomes a stored pattern.
* The recorded effect of a click is reported as request **kinds + counts**
  (via the shipped classifier), never the URLs — same seam as the media panel.

This module does not drive the page, replay anything, or emit a signed URL.
"""

from __future__ import annotations

import bisect
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .capture_redact import redact_query, PLACEHOLDER
from .netlog_classify import (
    classify_network_log,
    KIND_HLS_MANIFEST,
    KIND_DASH_MANIFEST,
    KIND_HLS_SEGMENT,
    KIND_DIRECT,
)

# Attribute values that ARE the value (URLs, ids, tokens, free text) — scrubbed
# in any persisted/displayed excerpt. Anything not listed here is treated as a
# value too (deny-by-default); only the structural allowlist below is kept.
_URLISH_ATTRS = ("href", "src", "srcset", "action", "formaction", "poster", "data-src")
# Structural attributes safe to keep verbatim: they carry no per-session secret
# and are exactly what selector/role detection needs.
_STRUCTURAL_ATTRS = ("class", "id", "type", "role", "rel", "target", "name",
                     "aria-label", "aria-role", "alt", "title", "for")

# A class token that looks machine-generated / per-build hashed — unstable, so
# excluded from selectors (e.g. ``css-1a2b3c``, ``sc-bdVaJa``, ``x9f3e2d1``).
# ReDoS: every run is BOUNDED ({m,128}, not {m,}). The unanchored
# ``[a-f0-9]{6,}$`` alternative backtracked O(n²) on an oversized class token (a
# long hex-range run that never reaches '$'); real hashed class names are a
# handful of chars, so 128 leaves matching unchanged and only the worst case
# linear. See test_capture_path_redos.
_HASHED_CLASS = re.compile(
    r"(?:[a-z]{1,64}[-_])?[a-f0-9]{6,128}$"               # hex tail: css-1a2b3c
    r"|^[a-f0-9]{8,128}$"                                  # all-hex token
    r"|^(?:css|sc|jss|emotion|styled|jsx|mui)[-_][A-Za-z0-9]{4,128}$",  # CSS-in-JS minted
    re.I)
# An id that looks auto-generated (uuid-ish / mostly digits / react-ish) —
# present but unstable, so not preferred as a sole selector.
_UNSTABLE_ID = re.compile(
    r"^[0-9]+$"                       # all digits
    r"|[0-9a-f]{8}-[0-9a-f]{4}"       # uuid
    r"|^(?:react|ember|radix|mui|headlessui|:r)[-:]?",  # framework-minted
    re.I,
)
# \Z, not $: ``$`` also matches before a trailing newline.
_CSS_IDENT = re.compile(r"^[A-Za-z_][\w-]*\Z")

# Bounds on a picked-element descriptor. The descriptor is written by the PAGE
# (the picker runs in its main world and anything there can call the binding or
# fill ``window.__bd_picks``), and the pump re-resolves every pick each tick, so
# every field is type-checked and length-capped before any derivation runs.
_MAX_PICKS = 300            # same cap as capture_session's _picks
_MAX_TAG = 64
_MAX_ID = 256
_MAX_CLASSES = 32
_MAX_CLASS = 128            # = the _HASHED_CLASS run bound
_MAX_MAP = 32               # data_attrs / attrs entries
_MAX_KEY = 64
_MAX_DATA_VAL = 256
_MAX_ATTR_VAL = 2048
_MAX_TEXT = 200
_MAX_HTML = 4096
_MAX_ANCESTORS = 3


def _capped_str(v: Any, cap: int) -> Optional[str]:
    """``v`` cut to ``cap`` chars ("" for None); None when it is not a string."""
    if v is None:
        return ""
    return v[:cap] if isinstance(v, str) else None


def _clean_map(m: Any, val_cap: int, allow_num: bool) -> Optional[Dict[str, Any]]:
    if m is None:
        return {}
    if not isinstance(m, dict):
        return None
    out: Dict[str, Any] = {}
    for k, v in m.items():
        if len(out) >= _MAX_MAP:
            break
        if not isinstance(k, str) or len(k) > _MAX_KEY:
            continue
        if v is None:
            v = ""
        if isinstance(v, str):
            if len(v) > val_cap:
                continue
        elif not (allow_num and isinstance(v, (int, float)) and not isinstance(v, bool)):
            continue
        out[k] = v
    return out


def _clean_descriptor(desc: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    """A bounded, type-checked copy of a picked-element descriptor, or None when
    a field has the wrong type (the pick is skipped; the other picks are kept).
    Over-long values are cut (text, outer_html) or dropped (a class token, a
    data/attr value, an id); lists and maps are capped in length."""
    if not isinstance(desc, dict):
        return None
    tag = _capped_str(desc.get("tag"), 10 ** 9)
    eid = _capped_str(desc.get("id"), 10 ** 9)
    text = _capped_str(desc.get("text"), _MAX_TEXT)
    html = _capped_str(desc.get("outer_html"), _MAX_HTML)
    if tag is None or eid is None or text is None or html is None:
        return None
    classes = desc.get("classes")
    if classes is None:
        classes = []
    if not isinstance(classes, (list, tuple)):
        return None
    data = _clean_map(desc.get("data_attrs"), _MAX_DATA_VAL, allow_num=True)
    attrs = _clean_map(desc.get("attrs"), _MAX_ATTR_VAL, allow_num=False)
    if data is None or attrs is None:
        return None
    out: Dict[str, Any] = {
        "tag": tag if len(tag) <= _MAX_TAG else "",
        "id": eid if len(eid) <= _MAX_ID else "",
        "classes": [c for c in classes[:_MAX_CLASSES * 2]
                    if isinstance(c, str) and len(c) <= _MAX_CLASS][:_MAX_CLASSES],
        "data_attrs": data,
        "attrs": attrs,
        "text": text,
        "outer_html": html,
    }
    for k in ("nth", "of_type_nth"):
        v = desc.get(k)
        out[k] = v if isinstance(v, int) and not isinstance(v, bool) else None
    if depth == 0:
        anc = desc.get("ancestors")
        if anc is None:
            anc = []
        if not isinstance(anc, (list, tuple)):
            return None
        out["ancestors"] = [a for a in (_clean_descriptor(x, 1) for x in anc[:_MAX_ANCESTORS])
                            if a is not None]
    return out


def clean_pick(pick: Any) -> Optional[Dict[str, Any]]:
    """A recorder pick ``{"descriptor", "ts"[, "origin"]}`` with its descriptor
    bounded by :func:`_clean_descriptor`; None when the pick is malformed."""
    if not isinstance(pick, dict):
        return None
    desc = _clean_descriptor(pick.get("descriptor"))
    if desc is None:
        return None
    out = dict(pick)
    out["descriptor"] = desc
    return out


def origin_of(url: Any) -> str:
    """``scheme://host[:port]`` of an http(s) url, lower-cased; "" otherwise.
    The key the capture driver scopes a page's HUD actions by."""
    if not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    if port == {"http": 80, "https": 443}[scheme]:
        port = None
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


def _stable_classes(classes: Any) -> List[str]:
    out: List[str] = []
    for c in (classes or []):
        c = str(c).strip()
        if not c or not _CSS_IDENT.match(c):
            continue
        if _HASHED_CLASS.search(c):
            continue
        out.append(c)
    return out


def _stable_id(eid: Optional[str]) -> bool:
    return bool(eid) and bool(_CSS_IDENT.match(eid)) and not _UNSTABLE_ID.search(eid)


def _short_enum(v: Any) -> bool:
    """A data-attr value short/enum-like enough to qualify a selector (e.g.
    ``1080``, ``hd``, ``play``) — not a URL/id/token."""
    s = str(v)
    return 0 < len(s) <= 16 and bool(re.match(r"^[\w.+-]+$", s)) and "/" not in s


def build_selector(desc: Dict[str, Any]) -> str:
    """Best-effort STABLE CSS selector for the element described by ``desc``.

    Priority: a stable ``#id`` → ``tag.stable-classes`` (optionally qualified by
    one short ``[data-*]`` enum) → ancestor-contextualised ``:nth-child`` path.
    Deterministic and pure; uniqueness can only be confirmed against the live
    DOM (the caller may verify), so this yields the most specific robust guess.
    """
    tag = str(desc.get("tag") or "*").lower()
    if _stable_id(desc.get("id")):
        return "#" + str(desc["id"])

    own = tag + "".join("." + c for c in _stable_classes(desc.get("classes")))
    if own != tag:  # we have stable classes
        for k, v in (desc.get("data_attrs") or {}).items():
            name = _data_attr_name(k)
            if name and _short_enum(v):
                own += f'[data-{name}="{v}"]'
                break
        return own

    # Nothing distinctive on the element itself — contextualise with ancestors
    # + the element's position among its siblings.
    ctx = _ancestor_prefix(desc.get("ancestors"))
    nth = desc.get("nth")
    node = f"{tag}:nth-child({int(nth)})" if isinstance(nth, int) and nth > 0 else tag
    return f"{ctx} > {node}" if ctx else node


def _data_attr_name(key: Any) -> Optional[str]:
    """The ``data-*`` attribute suffix for a picked ``el.dataset`` key. The DOM
    camelCases the attribute (``data-video-quality`` -> ``videoQuality``), and
    attribute names are case-insensitive, so the key is turned back into kebab
    case. None unless the result is a plain CSS identifier: the key is written
    by the page and lands unquoted in the selector."""
    if not isinstance(key, str) or len(key) > _MAX_KEY:
        return None
    name = re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)
    return name if _CSS_IDENT.match(name) else None


def _ancestor_prefix(ancestors: Any, max_levels: int = 2) -> str:
    """A short, stable parent prefix: the nearest ancestor that carries a stable
    id or class, capped at ``max_levels`` deep. Empty string if none qualifies."""
    parts: List[str] = []
    for anc in (ancestors or [])[:max_levels]:
        if not isinstance(anc, dict):
            continue
        if _stable_id(anc.get("id")):
            return "#" + str(anc["id"])  # an id anchor is enough on its own
        atag = str(anc.get("tag") or "*").lower()
        acls = _stable_classes(anc.get("classes"))
        if acls:
            parts.append(atag + "".join("." + c for c in acls))
    return " ".join(reversed(parts))


def build_xpath(desc: Dict[str, Any]) -> str:
    """A readable XPath from the ancestor chain + the element's of-type index.
    Prefers a ``@class`` predicate on the nearest classed ancestor."""
    tag = str(desc.get("tag") or "*").lower()
    steps: List[str] = []
    for anc in reversed((desc.get("ancestors") or [])[:2]):
        if not isinstance(anc, dict):
            continue
        atag = str(anc.get("tag") or "*").lower()
        acls = _stable_classes(anc.get("classes"))
        if acls:
            steps.append(f"{atag}[@class='{' '.join(acls)}']")
        else:
            steps.append(atag)
    idx = desc.get("of_type_nth") or desc.get("nth")
    leaf = f"{tag}[{int(idx)}]" if isinstance(idx, int) and idx > 0 else tag
    steps.append(leaf)
    return "//" + "/".join(steps)


# Role heuristics — (role label, regexes over text/aria/class/attrs). First
# match wins; confidence reflects how specific the signal is.
_ROLE_RULES: Tuple[Tuple[str, float, re.Pattern], ...] = (
    ("download link", 0.92, re.compile(r"download|\.mp4|save\s*video", re.I)),
    ("play button",   0.88, re.compile(r"\bplay\b|player|vjs-big-play|ytp-play", re.I)),
    ("quality select",0.84, re.compile(r"quality|resolution|\b\d{3,4}p\b|hd\b", re.I)),
    ("login/submit",  0.80, re.compile(r"log\s*in|sign\s*in|submit|continue", re.I)),
    ("media element", 0.95, re.compile(r"^(video|source|audio)$", re.I)),
)


def role_of(desc: Dict[str, Any]) -> Tuple[str, float]:
    """Guess the operator-meaningful role of the picked element. Pure heuristic
    over tag/text/aria/class — advisory only (it never changes behaviour)."""
    tag = str(desc.get("tag") or "").lower()
    if re.match(r"^(video|source|audio)$", tag):
        return ("media element", 0.95)
    hay = " ".join([
        tag,
        str(desc.get("text") or ""),
        " ".join(_stable_classes(desc.get("classes")) + list(desc.get("classes") or [])),
        " ".join(str(v) for v in (desc.get("attrs") or {}).values()),
        " ".join((desc.get("data_attrs") or {}).keys()),
    ])
    has_download_attr = "download" in (desc.get("attrs") or {})
    if has_download_attr:
        return ("download link", 0.94)
    for label, conf, rx in _ROLE_RULES:
        if rx.search(hay):
            return (label, conf)
    return ("element", 0.4)


# Match an attribute pair in a serialized tag: name="value" / name='value'.
# A value with no closing quote runs to the end of the input: the picker cuts
# outerHTML at 600 chars, so a long value (a token in a hidden input) arrives
# unterminated and must still be redacted, not passed through.
_ATTR_RE = re.compile(r"""(\s)([:\w-]+)\s*=\s*(["'])(.*?)(?:\3|\Z)""", re.S)


def redact_excerpt(html: Optional[str], max_len: int = 400) -> str:
    """Scrub a raw ``outerHTML`` excerpt so only STRUCTURE survives. Attribute
    KEYS, tag names, and class names are kept; value-bearing attribute VALUES
    are redacted (URL-ish via ``redact_query``, everything else to a marker).
    Text nodes are preserved (they are the control's visible label and the
    operator already sees them on screen) but the whole thing is length-capped.
    """
    if not isinstance(html, str) or not html:
        return ""
    html = html[:max_len * 4]  # cap before processing pathological input

    def _sub(m: "re.Match") -> str:
        lead, name, q, val = m.group(1), m.group(2), m.group(3), m.group(4)
        lname = name.lower()
        if lname in _STRUCTURAL_ATTRS:
            return m.group(0)  # keep structural values verbatim
        if lname in _URLISH_ATTRS or lname.startswith("data-") or "://" in val:
            red = redact_query(val) if ("://" in val or "/" in val) else PLACEHOLDER
            return f"{lead}{name}={q}{red}{q}"
        # any other attribute value: deny by default
        return f"{lead}{name}={q}{PLACEHOLDER}{q}"

    out = _ATTR_RE.sub(_sub, html)
    return out[:max_len]


def _host(url: str) -> str:
    if not isinstance(url, str) or "://" not in url:
        return ""
    return url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0]


def _content_type(entry: Dict[str, Any]) -> str:
    """Content-type from an entry, tolerant of the shapes the capture uses:
    a ``response_headers`` list of ``{name,value}``, a headers dict, or a flat
    ``content_type``/``mime`` field."""
    for key in ("content_type", "contentType", "mime", "mime_type"):
        v = entry.get(key)
        if isinstance(v, str) and v:
            return v.lower()
    rh = entry.get("response_headers") or entry.get("headers")
    if isinstance(rh, dict):
        for k, v in rh.items():
            if str(k).lower() == "content-type":
                return str(v).lower()
    if isinstance(rh, (list, tuple)):
        for h in rh:
            if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type":
                return str(h.get("value", "")).lower()
    return ""


def _is_nav(entry: Dict[str, Any]) -> bool:
    rt = str(entry.get("resource_type") or entry.get("resourceType")
             or entry.get("type") or "").lower()
    if rt in ("document", "navigation"):
        return True
    return _content_type(entry).startswith("text/html")


def _effects_from(window: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify a set of requests (already windowed/attributed) into the
    kinds+counts effect dict. No URLs cross this boundary."""
    rep = classify_network_log({"network_log": window})
    items = rep.items
    return {
        "req_count": len(window),
        "manifest": len(rep.hls_manifests) + len(rep.dash_manifests),
        "segments": len(rep.segments),
        "direct_media": len([i for i in items if i.kind == KIND_DIRECT]),
        "signed": len(rep.signed_items) > 0,
        "nav": any(_is_nav(e) for e in window),
    }


# ── REC-3 autoplay-window attribution (v3.66.302) ──────────────────────────
# A click's effect.direct_media is only a credible DOWNLOAD trigger if the
# media it is credited with was actually initiated BY the click — not the
# player's autoplay/preview stream already in flight, which the 2500 ms
# correlation window otherwise sweeps onto every click. These two booleans give
# the builder the effect-attribution it needs to reject an autoplay-contaminated
# trigger. Pure, from network-log timing; F2 (booleans only — the url shapes are
# compared internally and never emitted).
_MEDIA_EXTS = (".ts", ".m4s", ".mp4", ".m3u8", ".mpd", ".webm", ".mov", ".aac",
               ".m4a", ".m4v")


def _is_media_request(entry: Dict[str, Any]) -> bool:
    rt = str(entry.get("resource_type") or entry.get("resourceType")
             or entry.get("type") or "").lower()
    if rt == "media":
        return True
    ct = _content_type(entry)
    if ct.startswith("video/") or ct.startswith("audio/"):
        return True
    if "mpegurl" in ct or "dash+xml" in ct or "octet-stream" in ct:
        return True
    u = str(entry.get("url") or "").lower().split("?", 1)[0].split("#", 1)[0]
    return any(u.endswith(ext) or ext + "/" in u for ext in _MEDIA_EXTS)


def _media_shape(url: str) -> str:
    """Stream-identity key: scheme://host + path DIRECTORY, query/fragment
    stripped. Directory granularity so an advancing HLS/DASH stream
    (``…/stream/seg0.ts`` → ``seg1.ts`` → ``seg2.ts``) collapses to one identity
    and is NOT mistaken for a fresh download, while a genuinely new resource
    under a different path (``…/files/movie.mp4``) reads as fresh. Internal
    comparison key only (never emitted) — carries no credential material."""
    base = str(url or "").split("?", 1)[0].split("#", 1)[0]
    cut = base.rsplit("/", 1)
    return cut[0] if len(cut) == 2 else base


def media_attribution(network_log: Any, click_ts: Optional[int],
                      window_ms: int = 2500) -> Dict[str, Any]:
    """Attribute media flow around a click. Returns
    ``{"autoplay": bool, "fresh_download": bool}``:

      * ``autoplay``       — media of any kind was already in flight before the
                             click (a preview/autoplay stream the click did not
                             start).
      * ``fresh_download`` — a media request whose url SHAPE was not already
                             streaming appears in ``(click_ts, click_ts+window]``
                             — i.e. the click triggered a NEW download, even if a
                             preview was already playing.

    A download-affordance click that is ``autoplay and not fresh_download`` is
    the autoplay stream carrying over, not a real download trigger.
    """
    if not isinstance(click_ts, int):
        return {"autoplay": False, "fresh_download": False}
    nl = [e for e in (network_log or [])
          if isinstance(e, dict) and isinstance(e.get("timestamp"), int)]
    media = [e for e in nl if _is_media_request(e)]
    before = [e for e in media if e["timestamp"] < click_ts]
    after = [e for e in media
             if click_ts <= e["timestamp"] <= click_ts + window_ms]
    before_shapes = {_media_shape(e.get("url") or "") for e in before}
    fresh = any(_media_shape(e.get("url") or "") not in before_shapes
                for e in after)
    return {"autoplay": bool(before), "fresh_download": bool(fresh)}


def effects_for_click(click_ts: Optional[int],
                      network_log: Any,
                      window_ms: int = 2500) -> Dict[str, Any]:
    """Effect of a SINGLE click (e.g. the manual picker): requests in the
    ``window_ms`` after ``click_ts``, as kinds/counts only. For the auto-record
    timeline use :func:`correlate_timeline`, which attributes each request to
    exactly one click (no double-counting when clicks are close together)."""
    nl = network_log if isinstance(network_log, (list, tuple)) else []
    if not isinstance(click_ts, int):
        window = [e for e in nl if isinstance(e, dict)]
    else:
        lo, hi = click_ts, click_ts + window_ms
        window = [e for e in nl if isinstance(e, dict)
                  and isinstance(e.get("timestamp"), int)
                  and lo < e["timestamp"] <= hi]
    return _effects_from(window)


def _entry_from(desc: Dict[str, Any], click_ts: Optional[int],
                effect: Dict[str, Any]) -> Dict[str, Any]:
    role, conf = role_of(desc)
    return {
        "ts": click_ts,
        "selector": build_selector(desc),
        "xpath": build_xpath(desc),
        "role": role,
        "confidence": round(float(conf), 2),
        "tag": str(desc.get("tag") or "").lower(),
        "excerpt": redact_excerpt(desc.get("outer_html")),
        "effect": effect,
    }


def build_action_entry(desc: Dict[str, Any],
                       network_log: Any,
                       click_ts: Optional[int],
                       window_ms: int = 2500) -> Dict[str, Any]:
    """Resolve ONE picked element (manual single-pick) into an action entry:
    selector + XPath + role (structure) and its effect (kinds/counts), excerpt
    redacted. No values cross this boundary."""
    entry = _entry_from(_clean_descriptor(desc) or {}, click_ts,
                        effects_for_click(click_ts, network_log, window_ms))
    entry["effect"].update(media_attribution(network_log, click_ts, window_ms))
    return entry


def correlate_timeline(picks: Any,
                       network_log: Any,
                       window_ms: int = 2500) -> List[Dict[str, Any]]:
    """Resolve the FULL recorded click sequence into action entries, attributing
    each request to its MOST-RECENT preceding click within ``window_ms`` (so a
    request is counted once, against the click that plausibly caused it — not
    against every click whose window happens to overlap it).

    ``picks`` is the recorder's raw list of ``{"descriptor": {...}, "ts": int}``
    in click order. Returns entries in the same order, each with kinds/counts
    effects. Pure; this is what the capture driver renders and persists.

    Bounded: only the last ``_MAX_PICKS`` well-formed picks are resolved (a
    malformed one is skipped, see :func:`clean_pick`), and the work is one
    sorted pass over the log — O((picks + requests) log) — because the capture
    pump re-runs it every tick.
    """
    return [e for _, e in _correlate(picks, network_log, window_ms)]


def timeline_by_origin(picks: Any,
                       network_log: Any,
                       window_ms: int = 2500
                       ) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """:func:`correlate_timeline`, plus the same entries grouped by the
    ``origin`` each pick was recorded on (picks with no origin are in no group).
    The capture driver shows a page's HUD only its own origin's actions; the
    entries themselves carry no origin."""
    pairs = _correlate(picks, network_log, window_ms)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p, e in pairs:
        o = p.get("origin")
        if isinstance(o, str) and o:
            groups.setdefault(o, []).append(e)
    return [e for _, e in pairs], groups


def _correlate(picks: Any, network_log: Any,
               window_ms: int) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    raw = picks if isinstance(picks, (list, tuple)) else []
    kept: List[Dict[str, Any]] = []
    for p in reversed(raw):                         # newest first, keep the last N
        c = clean_pick(p)
        if c is not None:
            kept.append(c)
            if len(kept) >= _MAX_PICKS:
                break
    kept.reverse()
    seq = sorted(kept, key=lambda p: p.get("ts") if isinstance(p.get("ts"), int) else 0)
    nl = [e for e in (network_log or [])
          if isinstance(e, dict) and isinstance(e.get("timestamp"), int)]

    # Latest preceding click wins: the last click (in sorted order) with
    # ci < ts; it owns the request when ts <= ci + window_ms.
    clicks = [(p["ts"], i) for i, p in enumerate(seq) if isinstance(p.get("ts"), int)]
    click_ts = [c for c, _ in clicks]
    buckets: List[List[Dict[str, Any]]] = [[] for _ in seq]
    for e in nl:
        ts = e["timestamp"]
        j = bisect.bisect_left(click_ts, ts) - 1
        if j >= 0 and ts <= click_ts[j] + window_ms:
            buckets[clicks[j][1]].append(e)

    if not seq:
        return []
    media = _MediaIndex(nl)
    out = []
    for i, p in enumerate(seq):
        entry = _entry_from(p["descriptor"], p.get("ts"), _effects_from(buckets[i]))
        entry["effect"].update(media.attribution(p.get("ts"), window_ms))
        out.append((p, entry))
    return out


class _MediaIndex:
    """:func:`media_attribution` for many clicks over one log. A click's
    ``fresh_download`` holds iff some media url shape is FIRST seen inside
    ``[click_ts, click_ts + window_ms]`` (seen in the window and not before
    it), so the first-seen time per shape, sorted, answers it by bisection."""

    def __init__(self, nl: List[Dict[str, Any]]) -> None:
        first: Dict[str, int] = {}
        for e in nl:
            if _is_media_request(e):
                shape, ts = _media_shape(e.get("url") or ""), e["timestamp"]
                if shape not in first or ts < first[shape]:
                    first[shape] = ts
        self.first_seen = sorted(first.values())

    def attribution(self, click_ts: Any, window_ms: int) -> Dict[str, Any]:
        if not isinstance(click_ts, int):
            return {"autoplay": False, "fresh_download": False}
        fs = self.first_seen
        k = bisect.bisect_left(fs, click_ts)
        return {"autoplay": k > 0,
                "fresh_download": k < len(fs) and fs[k] <= click_ts + window_ms}


def verify_summary(action_timeline: Any,
                   capture: Any,
                   recorded_clicks: Optional[int] = None,
                   report: Any = None) -> Dict[str, Any]:
    """Finish-time confidence readout for the HUD verify bar. ADVISORY ONLY —
    it never blocks finishing a capture (fail-open-into-review).

    Returns ``{tier, checks, warnings, gap_count, trigger_selector}``:
      * ``tier`` — overall readiness (ready/partial/blocked/thin) from the
        capture's own media, mirroring the HUD readiness verdict.
      * ``checks`` — phases that are present (load/auth/play/download …).
      * ``warnings`` — clicks that fired **0 network** (a likely missed or
        already-satisfied action), and a rrweb-vs-resolved click count
        mismatch (the (B) cross-check: rrweb saw N interactions, we resolved M).
      * ``trigger_selector`` — the selector of the click whose effect first
        produced a manifest/segment/direct-media (the resolved trigger).

    ``report`` is ``classify_network_log(capture)`` when the caller already has
    it (several timelines judged against one capture in the same tick).
    """
    tl = action_timeline if isinstance(action_timeline, (list, tuple)) else []
    cap = capture if isinstance(capture, dict) else (
        capture.to_capture_dict() if hasattr(capture, "to_capture_dict") else {})

    # Overall tier from the capture's media (independent of the per-click view).
    rep = report if report is not None else classify_network_log(cap)
    has_manifest = (len(rep.hls_manifests) + len(rep.dash_manifests)) > 0
    has_seg = len(rep.segments) > 0
    has_direct = any(i.kind == KIND_DIRECT for i in rep.items)
    has_media = len(rep.items) > 0
    drm_only = any(i.drm for i in rep.items) and not any(not i.drm for i in rep.items)
    n_req = len(cap.get("network_log") or [])

    if drm_only:
        tier = "blocked"
    elif (has_manifest or has_seg or has_direct):
        tier = "ready"
    elif has_media or n_req > 0:
        tier = "partial"
    else:
        tier = "thin"

    checks: List[str] = []
    warnings: List[str] = []
    trigger_selector: Optional[str] = None
    gap_count = 0

    roles_seen = set()
    for e in tl:
        if not isinstance(e, dict):
            continue
        roles_seen.add(e.get("role"))
        eff = e.get("effect")
        if not isinstance(eff, dict):
            eff = {}
        if trigger_selector is None and (eff.get("manifest") or eff.get("segments")
                                         or eff.get("direct_media")):
            trigger_selector = e.get("selector")
        # A 0-network click is only a *gap* for elements the operator expects to
        # fire network. Focusing/typing a form FIELD (input/textarea/select)
        # legitimately fires 0 network — flagging it produces an advisory that can
        # never clear and makes the verify bar look perpetually unfinished. Skip
        # field tags from both the warning and the gap count.
        _tag = str(e.get("tag") or "").lower()
        _is_field = _tag in ("input", "textarea", "select")
        if (not _is_field) and isinstance(eff.get("req_count"), int) and eff["req_count"] == 0:
            gap_count += 1
            # Once the capture already has media (tier == "ready"), a 0-network
            # click is almost certainly a redundant UI click (a play/pause toggle,
            # a menu open) rather than a missed action — soften the hedge so the
            # advisory does not read as a problem on an otherwise-complete capture.
            # Below "ready", keep the missed-click hedge: a missed click may be
            # exactly why no media was captured yet.
            _suffix = ("(already satisfied; capture is ready)" if tier == "ready"
                       else "(missed click, or already satisfied?)")
            warnings.append(
                f"{e.get('selector', 'an element')} fired 0 network {_suffix}")

    if any(r == "login/submit" for r in roles_seen):
        checks.append("auth action recorded")
    if any(r == "play button" for r in roles_seen) or has_manifest or has_seg:
        checks.append("play captured")
    if any(r == "download link" for r in roles_seen) or has_direct:
        checks.append("download captured")
    if not checks:
        checks.append("media observed" if has_media else "no media yet")

    # (B) cross-check: rrweb interaction count vs resolved entries.
    if isinstance(recorded_clicks, int) and recorded_clicks > len(tl):
        warnings.append(
            f"rrweb saw {recorded_clicks} clicks, {len(tl)} resolved "
            f"({recorded_clicks - len(tl)} unresolved)")

    return {
        "tier": tier,
        "checks": checks,
        "warnings": warnings,
        "gap_count": gap_count,
        "trigger_selector": trigger_selector,
        "trigger_resolved": trigger_selector is not None,
        "action_count": len(tl),
    }
