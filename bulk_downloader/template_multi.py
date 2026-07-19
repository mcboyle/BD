"""template_multi — build a review-required template draft by comparing several
APPROVED captures of the same site instead of relying on one.

Captures are role-tagged (``login``, ``player``, ``quality_menu``,
``download_menu``, ``download_result``). Across them this aggregates:

  * ``selectors``           — DOM candidates that pass ``candidate_filter``,
                              with how many captures/roles support each
  * ``rejected``            — candidates the filter dropped, with reason + role
  * ``network_patterns``    — reusable request URL templates (media / API) and
                              the roles they appear in
  * ``resolution_priority`` — resolution tiers seen across media URLs, high→low

Recognition-only: no replay, no network, no rule writes. The result is always
``review_required`` so a human approves before it becomes a real template.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from . import candidate_filter as cf
from . import heuristic_scoring as hs

try:  # reuse the canonical media-URL check; fall back if internals move
    from .capture_synth import _looks_like_media as _synth_looks_like_media
except Exception:  # pragma: no cover
    _synth_looks_like_media = None

ROLES = ("login", "player", "quality_menu", "download_menu", "download_result")

_KNOWN_RES = r"144|240|360|480|540|576|720|1080|1440|2160|2880|4320"
_NUM_SEG = re.compile(r"^\d{2,}$")
_HEX_SEG = re.compile(r"^[0-9a-fA-F]{8,}$")
# a resolution segment is a KNOWN height (optionally with a trailing p), or any
# 3-4 digit value that is explicitly suffixed with p — never a bare numeric id.
_RES_SEG = re.compile(rf"^(?:{_KNOWN_RES})p?$|^\d{{3,4}}p$", re.I)
_RES_IN_NAME = re.compile(rf"(?:^|[._-])(?:{_KNOWN_RES})p?(?=[._-]|$)", re.I)
_CANDIDATE_TAGS = ("a", "button", "video", "source")


def _looks_like_media(url: str) -> bool:
    if _synth_looks_like_media is not None:
        try:
            return bool(_synth_looks_like_media(url))
        except Exception:
            pass
    return bool(cf.MEDIA_EXT_RE.search(url or "") or cf.MANIFEST_RE.search(url or ""))


def _res_sub(m: "re.Match") -> str:
    g = m.group(0)
    sep = g[0] if g and g[0] in "._-" else ""
    return sep + "{res}"


def _seg_token(seg: str) -> str:
    if _RES_SEG.match(seg):
        return "{res}"
    if _NUM_SEG.match(seg):
        return "{id}"
    if _HEX_SEG.match(seg):
        return "{hex}"
    # resolution embedded in a filename (1080.mp4, video_720p.mp4) -> {res},
    # so the same media endpoint at different qualities coalesces (the actual
    # resolutions are tracked separately in resolution_priority).
    if _RES_IN_NAME.search(seg):
        return _RES_IN_NAME.sub(_res_sub, seg, count=1)
    return seg


def _norm_url_template(url: str) -> str:
    """Collapse a URL into a reusable template: drop the query and replace
    numeric / hex / resolution path segments with typed placeholders, so the
    same endpoint shape recurring across captures coalesces."""
    s = urlsplit(url or "")
    segs = [_seg_token(seg) for seg in (s.path or "").split("/") if seg]
    path = "/".join(segs)
    return (f"{s.netloc}/{path}" if s.netloc else f"/{path}")


def _iter_network(capture: Dict[str, Any]):
    for e in (capture.get("network_log") or capture.get("requests") or []):
        if not isinstance(e, dict):
            continue
        u = e.get("url") or ""
        if u:
            yield u, (e.get("type") or ""), (e.get("method") or "GET")


# ── DOM candidate extraction from rrweb serialized snapshots (best-effort) ──

def _collect_text(node: Dict[str, Any], depth: int = 3) -> str:
    if depth < 0 or not isinstance(node, dict):
        return ""
    if node.get("textContent"):
        return str(node["textContent"])[:120]
    out: List[str] = []
    for c in (node.get("childNodes") or []):
        if isinstance(c, dict):
            if c.get("textContent"):
                out.append(str(c["textContent"]))
            elif depth > 0:
                out.append(_collect_text(c, depth - 1))
    return " ".join(t for t in out if t).strip()[:120]


def _walk_serialized(node: Any, ancestors=()):
    """Yield candidate dicts for a/button/video/source in an rrweb serialized
    DOM tree (``{tagName, attributes, childNodes, textContent}``)."""
    if not isinstance(node, dict):
        return
    tag = (node.get("tagName") or "").lower()
    attrs = node.get("attributes") or {}
    if tag in _CANDIDATE_TAGS:
        yield {
            "tag": tag,
            "href": attrs.get("href", ""),
            "data_href": attrs.get("data-href", ""),
            "data_url": attrs.get("data-url", ""),
            "data_src": attrs.get("data-src", "") or attrs.get("src", ""),
            "data_download": attrs.get("data-download", ""),
            "classes": attrs.get("class", ""),
            "text": _collect_text(node),
            "ancestor_text": " ".join(a for a in ancestors if a),
        }
    marker = attrs.get("class", "") or attrs.get("id", "")
    child_anc = ancestors + (marker,) if marker else ancestors
    for c in (node.get("childNodes") or []):
        yield from _walk_serialized(c, child_anc)


def _dom_candidates(capture: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Best-effort DOM candidates from a capture: an explicit ``dom_candidates``
    list, or walking rrweb full snapshots in ``dom_log``. Empty when no DOM."""
    explicit = capture.get("dom_candidates")
    if isinstance(explicit, list):
        return [c for c in explicit if isinstance(c, dict)]
    cands: List[Dict[str, Any]] = []
    for ev in (capture.get("dom_log") or []):
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") or {}
        root = data.get("node") if isinstance(data, dict) else None
        if root is None and isinstance(data, dict) and "tagName" in data:
            root = data
        if root is not None:
            cands.extend(_walk_serialized(root))
    return cands


def _selector_for(cand: Dict[str, Any]) -> str:
    tag = cand.get("tag", "") or "*"
    cls = (cand.get("classes") or "").split()
    if cls:
        return f"{tag}.{cls[0]}"
    for raw, key in (("data-href", "data_href"), ("data-url", "data_url"),
                     ("data-src", "data_src"), ("data-download", "data_download")):
        if cand.get(key):
            return f"{tag}[{raw}]"
    if cand.get("href"):
        return f"{tag}[href]"
    return tag


def _normalize(captures) -> List[tuple]:
    norm: List[tuple] = []
    for item in captures or []:
        if isinstance(item, dict) and isinstance(item.get("capture"), dict):
            cap = item["capture"]
            role = item.get("role") or cap.get("role") or "unknown"
        elif isinstance(item, dict):
            cap = item
            role = cap.get("role") or "unknown"
        else:
            continue
        norm.append((str(role), cap))
    return norm


def build_multi_capture_draft(captures, *, host: Optional[str] = None) -> Dict[str, Any]:
    """Compare role-tagged approved captures into a review-required draft.

    ``captures``: a list of ``{"role": str, "capture": dict}`` items, or capture
    dicts that each carry a top-level ``"role"``.
    """
    norm = _normalize(captures)
    roles_seen = sorted({r for r, _ in norm})
    if not host:
        for _, cap in norm:
            host = cap.get("host") or (urlsplit(cap.get("url", "")).netloc or None)
            if host:
                break

    # selectors (DOM) -----------------------------------------------------
    sel_support: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    rejected: List[Dict[str, Any]] = []
    for role, cap in norm:
        for cand in _dom_candidates(cap):
            sel = _selector_for(cand)
            v = cf.classify_candidate(cand, page_host=host, selector=sel)
            if v.accepted:
                ent = sel_support.setdefault(
                    sel, {"count": 0, "roles": set(), "signals": set(),
                          "kind": v.kind})
                ent["count"] += 1
                ent["roles"].add(role)
                ent["signals"].update(v.positive_signals)
            else:
                rejected.append({"selector": sel, "role": role,
                                 "url": cf.best_url(cand), "reason": v.reason})

    # network patterns + resolution --------------------------------------
    pat: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    res_tiers: Dict[int, str] = {}
    for role, cap in norm:
        for url, _typ, method in _iter_network(cap):
            is_media = _looks_like_media(url)
            if not (is_media or cf.positive_signals(url)):
                continue                       # skip nav/login/telemetry noise
            tmpl = _norm_url_template(url)
            ent = pat.setdefault(
                tmpl, {"count": 0, "roles": set(), "is_media": is_media,
                       "methods": set(), "example": url[:140]})
            ent["count"] += 1
            ent["roles"].add(role)
            ent["methods"].add(method)
            ent["is_media"] = ent["is_media"] or is_media
            tier, label = hs.detect_resolution_tier(url)
            if tier > 0:
                res_tiers[tier] = label

    selectors = [{"selector": s, "support": d["count"],
                  "roles": sorted(d["roles"]), "kind": d["kind"],
                  "signals": sorted(d["signals"])}
                 for s, d in sel_support.items()]
    selectors.sort(key=lambda x: (-x["support"], x["selector"]))

    network_patterns = [{"template": t, "support": d["count"],
                         "roles": sorted(d["roles"]), "is_media": d["is_media"],
                         "methods": sorted(d["methods"]), "example": d["example"]}
                        for t, d in pat.items()]
    network_patterns.sort(key=lambda x: (-x["support"], not x["is_media"],
                                         x["template"]))

    resolution_priority = [{"tier": t, "label": res_tiers[t]}
                           for t in sorted(res_tiers, reverse=True)]

    return {
        "review_required": True,
        "host": host,
        "roles_seen": roles_seen,
        "capture_count": len(norm),
        "selectors": selectors,
        "rejected": rejected[:50],
        "network_patterns": network_patterns,
        "resolution_priority": resolution_priority,
        "notes": [
            "Recognition-only draft compared across multiple approved captures; "
            "review before use.",
            "selector support = number of approved captures the selector "
            "appeared in.",
        ],
    }
