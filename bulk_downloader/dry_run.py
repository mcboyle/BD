"""dry_run — offline, no-download diagnostics over the detection + template
stack.

Powers two operator tools, neither of which fetches a page or downloads
anything:

  * #1 Candidate Inspector / Dry-Run Classifier — :func:`inspect_candidates`
    classifies every candidate found in pasted HTML (selector, text, url
    variants, score, size, host, signals, verdict + reason) and reports the
    winner that WOULD be selected.
  * #5 Static Template Test Runner — :func:`template_dry_run` reports whether a
    reviewed template matches a URL, its selector groups / resolutions /
    redacted network patterns, lint warnings, static selector hit-counts
    against optional HTML, the candidate classification, and whether a final
    safe candidate would be selected.

Everything here is read-only and side-effect-free. No cookies/tokens/storage
values are ever read or returned.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from . import candidate_filter as cf
from . import selector_lint as sl


def _host(url: str) -> str:
    try:
        return urlsplit(url or "").netloc
    except Exception:
        return ""


# ── #1 Candidate Inspector ───────────────────────────────────────────────

def _candidate_row(c: Dict[str, Any], page_host: str, *, page_url: str = "",
                   selector: Optional[str] = None) -> Dict[str, Any]:
    sel = (selector
           or (c.get("selector_variants") or [{}])[0].get("selector") or "")
    raw_url = cf.best_url(c)
    resolved_url = urljoin(page_url, raw_url)
    resolved = dict(c)
    resolved["href"] = resolved_url
    v = cf.classify_candidate(resolved, page_host=page_host or None, selector=sel)
    url = resolved_url if v.accepted and v.kind == "download" else raw_url
    return {
        "selector": sel,
        "text": (c.get("text") or "")[:120],
        "url": url,
        "href": c.get("href") or "",
        "data_href": c.get("data_href") or "",
        "data_url": c.get("data_url") or "",
        "data_src": c.get("data_src") or "",
        "score": c.get("score"),
        "size": c.get("estimated_size_bytes") or c.get("size") or 0,
        "host": _host(resolved_url),
        "signals": list(v.positive_signals),
        "kind": v.kind,
        "accepted": v.accepted,
        "reason": v.reason,
    }


def inspect_candidates(html: str, page_url: str = "") -> Dict[str, Any]:
    """#1 — classify every detection candidate found in ``html`` (dry-run).

    Returns every candidate with its selector / text / url variants / score /
    size / host / signals / verdict + rejection reason, plus the ``winner``
    that would be selected (the highest-scored *accepted* candidate — a
    nav/homepage/rejected candidate is never the winner, mirroring the runtime
    gate). Never fetches, never downloads.
    """
    if not isinstance(html, str) or not html.strip():
        return {"ok": False, "error": "no HTML provided",
                "candidates": [], "winner": None}
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False,
                "error": "BeautifulSoup (bs4) not installed",
                "candidates": [], "winner": None}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return {"ok": False,
                "error": f"HTML parse failed: {type(e).__name__}: {e}",
                "candidates": [], "winner": None}

    from .template_extractor import (_walk_for_candidates, _score_all,
                                     _generalize_selectors)
    raw = _walk_for_candidates(soup)
    try:
        _score_all(raw)          # scores in place (score / estimated_size_bytes)
    except Exception:
        pass
    page_host = _host(page_url)
    rows: List[Dict[str, Any]] = []
    for c in raw:
        try:
            sel = (_generalize_selectors(c) or [{}])[0].get("selector") or ""
        except Exception:
            sel = ""
        rows.append(_candidate_row(c, page_host, page_url=page_url, selector=sel))

    accepted = [r for r in rows if r["accepted"]]
    accepted.sort(key=lambda r: ((r["score"] or 0), (r["size"] or 0)),
                  reverse=True)
    rows.sort(key=lambda r: ((r["score"] or 0), (r["size"] or 0)),
              reverse=True)
    winner = accepted[0] if accepted else None
    return {
        "ok": True,
        "page_url": page_url,
        "page_host": page_host,
        "winner": winner,
        "candidates": rows[:40],          # cap for render
        "n_candidates": len(rows),
        "n_accepted": len(accepted),
        "n_rejected": len(rows) - len(accepted),
        "safe_candidate_available": winner is not None,
    }


# ── #5 Static Template Test Runner ────────────────────────────────────────

_TOKEN_RE = re.compile(r"(?i)(token|sig|signature|key|secret|expires?|hash|"
                       r"auth|jwt|bearer)")


def _redact_patterns(template: Optional[Dict[str, Any]]) -> List[str]:
    """Network-pattern hints with any query string / token-looking bits
    stripped. Reviewed patterns are media-only paths, but we redact defensively
    so the dry-run never surfaces a signed value."""
    out: List[str] = []
    for p in ((template or {}).get("network_patterns") or []):
        s = str(p).split("?", 1)[0].split("#", 1)[0]
        # If a token-ish word is in the remaining path, mask the segment.
        parts = []
        for seg in s.split("/"):
            parts.append("<redacted>" if _TOKEN_RE.search(seg) else seg)
        out.append("/".join(parts))
    return out


def _leaf_selectors(template: Dict[str, Any]) -> Dict[str, List[str]]:
    """Collect the concrete selector strings from the download/quality groups,
    keyed by ``group.field`` (for selector hit-counting + display)."""
    sels: Dict[str, List[str]] = {}
    selectors = (template or {}).get("selectors") or {}
    dl = selectors.get("download") or {}
    for field in ("trigger", "button"):
        if isinstance(dl.get(field), str) and dl[field].strip():
            sels[f"download.{field}"] = [dl[field]]
    rows = dl.get("row_selectors")
    if isinstance(rows, (list, tuple)):
        sels["download.row_selectors"] = [str(s) for s in rows if str(s).strip()]
    elif isinstance(rows, str) and rows.strip():
        sels["download.row_selectors"] = [rows]
    quality = selectors.get("quality") or {}
    for field in ("open_menu", "resolution_option"):
        if isinstance(quality.get(field), str) and quality[field].strip():
            sels[f"quality.{field}"] = [quality[field]]
    return sels


def _selector_hit_counts(template: Optional[Dict[str, Any]],
                         html: str) -> Dict[str, Any]:
    """Static hit-counts: how many elements each template selector matches in
    ``html`` via BS4. Playwright-only selectors (``:has-text``,
    ``:text-matches``, scoped pseudos BS4 can't parse) report ``"n/a"`` rather
    than failing — exact live counts need a browser."""
    if not template or not html:
        return {}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return {}
    counts: Dict[str, Any] = {}
    for group, sel_list in _leaf_selectors(template).items():
        per_group = []
        for sel in sel_list:
            # Expand {resolution} placeholders to a literal for the count.
            probe = sel.replace("{resolution}", "1080")
            try:
                n = len(soup.select(probe))
                per_group.append({"selector": sel, "hits": n})
            except Exception:
                per_group.append({"selector": sel,
                                   "hits": "n/a (non-CSS selector)"})
        counts[group] = per_group
    return counts


def template_dry_run(url: str, html: str = "") -> Dict[str, Any]:
    """#5 — static dry-run for a URL (+ optional HTML) against the reviewed
    templates. Reports template match, selector groups, resolutions, redacted
    network patterns, lint warnings, static selector hit-counts, the candidate
    classification, and whether a final safe candidate would be selected.
    Never fetches, never downloads.
    """
    from . import template_registry as tr
    from .template_assist import template_summary

    template = None
    try:
        # CAP-3: when HTML is supplied, let the registry pick the best-fit variant
        # among same-host templates; html-less path is unchanged.
        template = tr.find_template_for_url(
            url, html=(html if isinstance(html, str) and html.strip() else None)
        ) if url else None
    except Exception:
        template = None

    lint_issues = sl.lint_template(template) if template else []
    candidate_view = None
    hit_counts: Dict[str, Any] = {}
    if isinstance(html, str) and html.strip():
        candidate_view = inspect_candidates(html, page_url=url)
        hit_counts = _selector_hit_counts(template, html)

    return {
        "ok": True,
        "url": url,
        "host": _host(url),
        "template_matched": template is not None,
        "template": template_summary(template),
        "network_patterns": _redact_patterns(template),
        "lint_warnings": [i.to_dict() for i in lint_issues],
        "has_blocking_lint": sl.has_blocking_issues(lint_issues),
        "selector_hit_counts": hit_counts,
        "candidate_classification": candidate_view,
        "safe_candidate_selected": bool(candidate_view
                                        and candidate_view.get("winner")),
        "note": "dry-run only — no fetch, no download, no stored values read",
    }
