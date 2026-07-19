#!/usr/bin/env python3
"""selector_learning.py — Phase 2 DOM/selector recognition layer.

Recognition and confidence scoring ONLY. Reads the rrweb-style DOM log that
dom_capture records, identifies candidate action/download/login elements, derives
REVIEWABLE selector candidates, and scores their stability/specificity across
captures. It emits descriptive selector artifacts for human review.

It does NOT, and is structurally unable to, produce an executable session: there
is no code path here that emits a Playwright script, a click sequence, or any
browser-driving flow. The output is data (selectors + scores + reports), never a
program. This is the hard line — selectors describe what looks stable and
trustworthy; they never become a replay.

Boundaries (enforced, not just stated):
  * DOM is read from captured artifacts only — nothing is fetched or driven.
  * Signing values never appear: nodes carrying `_bd_redacted` are already masked
    by dom_capture, storage values are PLACEHOLDER, and a posture scan over the
    output fails closed before writing.
  * Empty/absent DOM logs produce a "blocked / not available" result, not a guess.
  * Selector confidence is reviewable data — it does NOT auto-update live templates,
    learned-selector storage, or the corpus, and cannot retire debt.

Reuses existing project logic where possible:
  * cross_site_selectors._class_token_is_volatile / selector_shape — class-churn
    resistance and specificity shape.
  * selector_drift.status_for — historical live success/zero-match, if available.

Usage:
    python3 tools/selector_learning.py \
        --captures ./captures/bros_run1.wacz ./captures/bros_run2.wacz \
        --site bros \
        --out-dir ./selector_learning/bros \
        [--learned-selectors ./site_templates/bros.selectors.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── reuse project primitives (fall back gracefully if unavailable) ──
try:
    from bulk_downloader.cross_site_selectors import _class_token_is_volatile as _xsite_volatile, selector_shape
except Exception:  # pragma: no cover - defensive
    def _xsite_volatile(tok: str) -> bool:
        return bool(re.search(r"[0-9a-f]{6,}", tok) or re.fullmatch(r"[a-z]{1,3}\d+", tok or ""))
    def selector_shape(sel: str) -> str:
        return sel


# Phase-2 supplements the reused detector: selector stability is stricter about
# hash-like class churn than cross-site matching is. The reused check catches
# pure-hex and camel-hash; we additionally treat short mixed alphanumeric tokens
# with digits (e.g. "x9f3a2b7", "a1b2", utility hashes) as volatile. The shared
# project primitive is left untouched — this only narrows what WE keep.
_LOCAL_HASHY_RE = re.compile(r"^[a-z]?[0-9a-f]{5,}$|^[a-z0-9]*\d[a-z0-9]*$", re.I)
def _class_token_is_volatile(tok: str) -> bool:
    if _xsite_volatile(tok):
        return True
    t = (tok or "").strip()
    if not t:
        return True
    # keep clearly word-like tokens (letters + hyphens, no digit-noise)
    if re.fullmatch(r"[a-z][a-z-]{2,}", t, re.I):
        return False
    # flag short tokens that mix letters and digits or look hashy
    if _LOCAL_HASHY_RE.match(t) and any(ch.isdigit() for ch in t):
        return True
    return False

DOWNLOAD_TEXT = re.compile(r"\b(download|save|dl|get\s+video|export|hd|full)\b", re.I)
RENDITION_TEXT = re.compile(r"\b(\d{3,4}p|4k|2160|1080|720|480|360|uhd|fhd|hd|sd|"
                            r"high|medium|low|best|quality)\b", re.I)
LOGIN_TEXT = re.compile(r"\b(log\s*in|sign\s*in|login|submit|continue|email|password|username)\b", re.I)
ACTION_TAGS = {"a", "button", "input", "select", "form", "video", "source"}


# ── DOM log access + presence gate ──────────────────────────────────
def _iter_nodes(node: Dict[str, Any]):
    """Depth-first walk over a serialized rrweb node tree."""
    if not isinstance(node, dict):
        return
    yield node
    for c in (node.get("childNodes") or []):
        if isinstance(c, dict):
            yield from _iter_nodes(c)


def _snapshot_roots(dom_log: List[Dict[str, Any]]):
    """Yield every serialized node root in the log: full-snapshot nodes and
    mutation 'adds'. Each is a node dict (already redacted at capture time)."""
    for ev in dom_log or []:
        data = ev.get("data") or {}
        if isinstance(data, dict):
            if isinstance(data.get("node"), dict):
                yield data["node"]
            for add in (data.get("adds") or []):
                if isinstance(add, dict) and isinstance(add.get("node"), dict):
                    yield add["node"]


def _dom_presence(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    per = []
    total_nodes = 0
    for m in models:
        raw = m.get("_raw") or {}
        dom = raw.get("dom_log") or []
        n_events = raw.get("dom_log_count")
        if n_events is None:
            n_events = len(dom)
        n_nodes = sum(1 for root in _snapshot_roots(dom) for _ in _iter_nodes(root))
        total_nodes += n_nodes
        per.append({"source": m.get("source_name"),
                    "dom_events": n_events, "serialized_nodes": n_nodes})
    return {"usable_dom_present": total_nodes > 0, "total_serialized_nodes": total_nodes,
            "per_capture": per}


# ── attribute helpers (redaction-aware) ─────────────────────────────
def _attrs(node: Dict[str, Any]) -> Dict[str, Any]:
    return node.get("attributes") or {}


def _tag(node: Dict[str, Any]) -> str:
    return str(node.get("tagName") or node.get("tag") or "").lower()


def _classes(node: Dict[str, Any]) -> List[str]:
    raw = _attrs(node).get("class") or ""
    if isinstance(raw, list):
        return [str(c) for c in raw]
    return str(raw).split()


def _text(node: Dict[str, Any]) -> str:
    """Text content — but NEVER from a redacted node (masked content is '*'s or
    a <blocked> placeholder; we treat redacted nodes as having no usable text)."""
    if node.get("_bd_redacted"):
        return ""
    t = node.get("textContent")
    return str(t) if t else ""


def _is_action_element(node: Dict[str, Any]) -> bool:
    tag = _tag(node)
    if tag in ACTION_TAGS:
        return True
    a = _attrs(node)
    # role/href/download attributes mark interactive elements
    return bool(a.get("href") or a.get("download") is not None or
                str(a.get("role") or "").lower() in
                {"button", "link", "menuitem", "tab"})


# ── candidate selector derivation (data only — no flows) ────────────
def _id_selector(node: Dict[str, Any]) -> Optional[str]:
    i = _attrs(node).get("id")
    return f"#{i}" if i and not _class_token_is_volatile(str(i)) else None


def _attr_selectors(node: Dict[str, Any]) -> List[str]:
    tag = _tag(node) or "*"
    out = []
    a = _attrs(node)
    for name in ("data-testid", "data-test", "data-qa", "name", "type",
                 "aria-label", "role", "download"):
        v = a.get(name)
        if v is None:
            continue
        if v == "" or v is True:
            out.append(f"{tag}[{name}]")
        elif not _class_token_is_volatile(str(v)):
            out.append(f'{tag}[{name}="{v}"]')
    return out


def _class_selector(node: Dict[str, Any]) -> Optional[str]:
    tag = _tag(node) or "*"
    stable = [c for c in _classes(node) if c and not _class_token_is_volatile(c)]
    if not stable:
        return None
    return tag + "".join(f".{c}" for c in stable[:3])


def _href_pattern_selector(node: Dict[str, Any]) -> Optional[str]:
    """href-pattern-assisted: a stable *shape* of the href, signing stripped.
    Never the live href with its query — only the path pattern."""
    href = _attrs(node).get("href")
    if not href or _attrs(node).get("_bd_redacted"):
        return None
    # strip query/fragment — never echo a signed href
    base = str(href).split("?", 1)[0].split("#", 1)[0]
    # collapse trailing numeric id to a wildcard shape (descriptive, not literal)
    shape = re.sub(r"/\d+(/|$)", r"/*\1", base)
    return f'a[href*="{shape.rsplit("/",1)[-1]}"]' if "/" in shape else None


def _text_selector(node: Dict[str, Any]) -> Optional[str]:
    """text-assisted: a reviewable note pairing the tag with stable visible text.
    Expressed as a non-executable descriptor (text=...), not a runnable XPath."""
    txt = _text(node).strip()
    if not txt or len(txt) > 40:
        return None
    return f'{_tag(node) or "*"}:has-text("{txt[:40]}")'


def _role_for(node: Dict[str, Any], text: str) -> str:
    if DOWNLOAD_TEXT.search(text) or _attrs(node).get("download") is not None:
        return "download"
    if LOGIN_TEXT.search(text) or _tag(node) in {"input", "form"}:
        return "login"
    if RENDITION_TEXT.search(text):
        return "rendition"
    return "action"


def _candidates_for(node: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(node)
    cands: List[Dict[str, str]] = []
    idsel = _id_selector(node)
    if idsel:
        cands.append({"kind": "id", "selector": idsel})
    for s in _attr_selectors(node):
        cands.append({"kind": "attribute", "selector": s})
    csel = _class_selector(node)
    if csel:
        cands.append({"kind": "class", "selector": csel})
    hsel = _href_pattern_selector(node)
    if hsel:
        cands.append({"kind": "href_pattern", "selector": hsel})
    tsel = _text_selector(node)
    if tsel:
        cands.append({"kind": "text_assisted", "selector": tsel})
    return {
        "tag": _tag(node),
        "role_hint": _role_for(node, text),
        "near_rendition_signal": bool(RENDITION_TEXT.search(text)),
        "near_download_signal": bool(DOWNLOAD_TEXT.search(text)),
        "redacted": bool(node.get("_bd_redacted")),
        "candidates": cands,
    }


def _extract_inventory(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = model.get("_raw") or {}
    inv: List[Dict[str, Any]] = []
    seen = set()
    for root in _snapshot_roots(raw.get("dom_log") or []):
        for node in _iter_nodes(root):
            if not _is_action_element(node):
                continue
            entry = _candidates_for(node)
            if not entry["candidates"]:
                continue
            key = (entry["tag"], tuple(c["selector"] for c in entry["candidates"]))
            if key in seen:
                continue
            seen.add(key)
            inv.append(entry)
    return inv


# ── scoring ─────────────────────────────────────────────────────────
def _specificity(kind: str) -> int:
    return {"id": 5, "attribute": 4, "href_pattern": 3, "class": 2,
            "text_assisted": 1}.get(kind, 1)


def _score_selectors(inventories: List[List[Dict[str, Any]]],
                     site: str,
                     learned: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate identical selectors across captures and score them. Stability =
    fraction of captures the selector appears in. Specificity from kind. Churn
    resistance is already baked in (volatile tokens were dropped at derivation)."""
    n = len(inventories) or 1
    # selector -> {kinds, count, roles, signals}
    agg: Dict[str, Dict[str, Any]] = {}
    for inv in inventories:
        seen_here = set()
        for entry in inv:
            for c in entry["candidates"]:
                sel = c["selector"]
                rec = agg.setdefault(sel, {"kind": c["kind"], "count": 0,
                                           "roles": set(), "rendition": False,
                                           "download": False})
                if sel not in seen_here:
                    rec["count"] += 1
                    seen_here.add(sel)
                rec["roles"].add(entry["role_hint"])
                rec["rendition"] = rec["rendition"] or entry["near_rendition_signal"]
                rec["download"] = rec["download"] or entry["near_download_signal"]

    # historical live success, if the live drift table has anything for this site
    hist = None
    try:
        from bulk_downloader.selector_drift import status_for
        hist = status_for(site)
    except Exception:
        hist = None

    learned_set = set()
    if isinstance(learned, dict):
        for v in learned.values():
            if isinstance(v, str):
                learned_set.add(v)
            elif isinstance(v, list):
                learned_set.update(x for x in v if isinstance(x, str))

    scored = []
    for sel, rec in agg.items():
        stability = round(rec["count"] / n, 3)
        spec = _specificity(rec["kind"])
        signal_bonus = (1 if rec["download"] else 0) + (1 if rec["rendition"] else 0)
        matches_learned = sel in learned_set
        # composite confidence: stability dominates, specificity + signals refine.
        confidence = round(min(1.0, 0.6 * stability + 0.08 * spec
                               + 0.05 * signal_bonus + (0.1 if matches_learned else 0)), 3)
        scored.append({
            "selector": sel,
            "kind": rec["kind"],
            "shape": selector_shape(sel),
            "stability_across_captures": stability,
            "appeared_in": rec["count"],
            "of_captures": n,
            "specificity": spec,
            "roles": sorted(rec["roles"]),
            "near_download_signal": rec["download"],
            "near_rendition_signal": rec["rendition"],
            "matches_learned_selector": matches_learned,
            "historical_live_status": (hist if hist else "no_live_history"),
            "confidence": confidence,
        })
    scored.sort(key=lambda s: (-s["confidence"], -s["stability_across_captures"],
                               -s["specificity"]))
    return scored


# ── reports ─────────────────────────────────────────────────────────
def _blocked_artifacts(site: str, presence: Dict[str, Any], out: Path) -> None:
    msg = (f"# Selector learning — {site}: BLOCKED\n\n"
           f"No usable DOM/rrweb log in the supplied captures "
           f"(total serialized nodes: {presence['total_serialized_nodes']}). "
           f"Selector learning is not possible until captures are taken with DOM "
           f"capture active. Per capture:\n\n")
    for pc in presence["per_capture"]:
        msg += f"- {pc['source']}: {pc['dom_events']} DOM events, {pc['serialized_nodes']} nodes\n"
    msg += ("\nLive operation is unaffected — it continues using existing learned-selector "
            "behavior. This is a not-available result, not a failure.\n")
    _write_text(out / "selector_learning_report.md", msg)
    for j in ("selector_inventory.json", "selector_confidence.json",
              "selector_profile_update_candidate.json"):
        _write_json(out / j, {"status": "blocked_no_dom", "site": site})
    _write_text(out / "selector_drift_report.md",
                f"# Selector drift — {site}\n\nNo DOM logs; drift not measurable.\n")


def _learning_report(site: str, scored: List[Dict[str, Any]],
                     presence: Dict[str, Any]) -> str:
    L = [f"# Selector learning report — {site}", ""]
    L += [f"Derived from {presence['total_serialized_nodes']} serialized DOM nodes across "
          f"{len(presence['per_capture'])} capture(s). Selectors below are REVIEWABLE "
          f"CANDIDATES — descriptive data scored by stability and specificity. No "
          f"executable flow, click sequence, or replay is produced; no signing value "
          f"appears (redacted nodes contribute no text/value, hrefs are query-stripped).",
          ""]
    if not scored:
        L.append("No action/download/login candidate elements were found in the DOM logs.")
        return "\n".join(L)
    L.append("## Top selector candidates by confidence")
    for s in scored[:25]:
        roles = ",".join(s["roles"])
        L.append(f"- `{s['selector']}` ({s['kind']}, roles: {roles}) — "
                 f"confidence **{s['confidence']}**, stability "
                 f"{s['stability_across_captures']} ({s['appeared_in']}/{s['of_captures']}), "
                 f"specificity {s['specificity']}"
                 + (", near download" if s["near_download_signal"] else "")
                 + (", near rendition" if s["near_rendition_signal"] else "")
                 + (", matches learned" if s["matches_learned_selector"] else ""))
    L += ["", "## What this enables / what needs approval",
          "These are candidate selectors and confidence scores for a human to review. They "
          "do NOT update live templates, learned-selector storage, or the corpus. A "
          "maintainer decides which (if any) to promote into the live selector set. "
          "Selectors describe what looks stable; they are not executed."]
    return "\n".join(L)


def _drift_report(site: str, scored: List[Dict[str, Any]]) -> str:
    L = [f"# Selector drift report — {site}", ""]
    L += ["Selectors that appeared in only some captures are candidates for drift "
          "(unstable across sessions/titles); those in all captures are stable. "
          "Cosmetic/moderate/breaking is left to the reviewer — a low-stability selector "
          "near the download signal is more concerning than a peripheral one.", ""]
    stable = [s for s in scored if s["stability_across_captures"] >= 0.999]
    partial = [s for s in scored if 0 < s["stability_across_captures"] < 0.999]
    L.append(f"## Stable across all captures ({len(stable)})")
    for s in stable[:15]:
        L.append(f"- `{s['selector']}` (confidence {s['confidence']})")
    L.append(f"\n## Partial / drifting ({len(partial)})")
    for s in partial[:15]:
        L.append(f"- `{s['selector']}` — only {s['appeared_in']}/{s['of_captures']} captures"
                 + (" — NEAR DOWNLOAD SIGNAL" if s["near_download_signal"] else ""))
    return "\n".join(L)


def _profile_update_candidate(site: str, scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A SUGGESTED update — never auto-applied. High-confidence selectors a human
    might promote into the live selector set."""
    promotable = [s for s in scored if s["confidence"] >= 0.7
                  and s["stability_across_captures"] >= 0.999]
    return {
        "_status": "SUGGESTED — reviewable only. Does NOT auto-update live templates, "
                   "learned-selector storage, or the corpus. A maintainer promotes "
                   "selectors manually after review.",
        "site": site,
        "suggested_selectors": [
            {"selector": s["selector"], "kind": s["kind"], "roles": s["roles"],
             "confidence": s["confidence"]}
            for s in promotable
        ],
        "note": "Recognition-only. Selectors are descriptive data, not executable steps.",
    }


# ── io ──────────────────────────────────────────────────────────────
def _write_json(path: Path, obj: Any) -> None:
    import os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=list)
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ── orchestration ───────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 2 DOM/selector recognition layer")
    p.add_argument("--captures", nargs="+", required=True,
                   help="Capture paths (.wacz or recon .json) carrying DOM logs.")
    p.add_argument("--site", required=True, help="Site name (artifact stem).")
    p.add_argument("--learned-selectors", default=None,
                   help="Optional existing learned-selectors JSON, to flag overlap.")
    p.add_argument("--out-dir", default="./selector_learning",
                   help="Output dir (default ./selector_learning).")
    return p


def run(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    from bulk_downloader.capture_ingest import analyze_captures, posture_scan

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    result = analyze_captures(args.captures)
    models = result.get("models", [])

    presence = _dom_presence(models)
    if not presence["usable_dom_present"]:
        _blocked_artifacts(args.site, presence, out)
        print(f"Selector learning BLOCKED for {args.site}: no usable DOM logs. "
              f"Artifacts written to {out}/ (blocked status).")
        return 0  # not-available is a valid, non-error outcome

    inventories = [_extract_inventory(m) for m in models]
    learned = None
    if args.learned_selectors:
        try:
            with open(args.learned_selectors, encoding="utf-8") as f:
                learned = json.load(f)
        except (FileNotFoundError, ValueError):
            learned = None

    scored = _score_selectors(inventories, args.site, learned)

    inv_out = {"site": args.site, "captures": result.get("labels"),
               "per_capture_inventory": inventories}
    lr = _learning_report(args.site, scored, presence)
    dr = _drift_report(args.site, scored)
    upd = _profile_update_candidate(args.site, scored)

    # POSTURE: no signing value may appear in any artifact. Fail closed.
    blob = "\n".join([lr, dr, json.dumps(inv_out, default=list),
                      json.dumps(scored, default=list), json.dumps(upd, default=list)])
    leaks = posture_scan(blob)
    if leaks:
        print(f"POSTURE FAIL: a signing value would appear in selector output ({leaks}); "
              f"refusing to write.", file=sys.stderr)
        return 2

    # POSTURE/REPLAY: assert no artifact is an executable flow. Selector artifacts
    # are data; this guard documents and enforces that nothing script-like ships.
    for art in (inv_out, scored, upd):
        s = json.dumps(art, default=list)
        if re.search(r"page\.(goto|click|fill)|await\s|playwright|new_page\(", s):
            print("POSTURE FAIL: selector artifact looks executable; refusing to write.",
                  file=sys.stderr)
            return 2

    _write_json(out / "selector_inventory.json", inv_out)
    _write_json(out / "selector_confidence.json", scored)
    _write_text(out / "selector_learning_report.md", lr)
    _write_text(out / "selector_drift_report.md", dr)
    _write_json(out / "selector_profile_update_candidate.json", upd)

    print(f"Selector learning artifacts written to {out}/")
    print(f"  captures: {result.get('n_captures')}  nodes: {presence['total_serialized_nodes']}  "
          f"scored selectors: {len(scored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
