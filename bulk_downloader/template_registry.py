from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TEMPLATE_DIRS = [
    PROJECT_ROOT / "templates" / "reviewed",
    PROJECT_ROOT / "templates" / "enabled",
]


def _host_matches(template_host: str, url_host: str) -> bool:
    template_host = (template_host or "").lower().strip()
    url_host = (url_host or "").lower().strip()

    if not template_host or not url_host:
        return False

    return url_host == template_host or url_host.endswith("." + template_host)


def load_templates(template_dirs=None):
    template_dirs = template_dirs or DEFAULT_TEMPLATE_DIRS
    out = []

    for d in template_dirs:
        d = Path(d)
        if not d.exists():
            continue

        for fp in sorted(d.glob("*.template.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue

            if data.get("status") != "enabled":
                continue

            data["_template_file"] = str(fp.resolve())
            out.append(data)

    return out


def find_template_for_url(url: str, template_dirs=None, *, html=None):
    # CAP-3: when a page's HTML is supplied AND the host has more than one
    # matching template variant, pick the variant whose selectors actually fit
    # this page. html=None (every existing caller) keeps the original host-
    # specificity behavior exactly -> backward-compatible.
    if html:
        variants = find_template_variants_for_url(url, template_dirs)
        if len(variants) > 1:
            return select_best_variant(url, html, template_dirs=template_dirs)

    try:
        host = urlparse(url).netloc
    except Exception:
        return None

    # T1: among all templates whose host matches, return the MOST SPECIFIC one
    # rather than the first encountered — an exact host beats a parent-domain
    # suffix match, and among suffix matches the longest template host wins, so
    # a site-specific template can't be shadowed by a generic parent-domain one.
    best = None
    best_key = None
    for template in load_templates(template_dirs):
        thost = (template.get("host", "") or "").lower().strip()
        if _host_matches(thost, host):
            key = (host.lower().strip() == thost, len(thost))
            if best_key is None or key > best_key:
                best, best_key = template, key

    return best


# ── CAP-3: runtime multi-variant template selection ──────────────────
def find_template_variants_for_url(url: str, template_dirs=None):
    """Every host-matching template for `url` (the variants), most-host-specific
    first. This is the candidate pool find_template_for_url chooses one from."""
    try:
        host = urlparse(url).netloc
    except Exception:
        return []
    matches = []
    for template in load_templates(template_dirs):
        thost = (template.get("host", "") or "").lower().strip()
        if _host_matches(thost, host):
            spec = (host.lower().strip() == thost, len(thost))
            matches.append((spec, template))
    matches.sort(key=lambda m: m[0], reverse=True)
    return [t for _, t in matches]


def _leaf_selectors(template):
    """Flatten a template's nested selectors dict to a list of CSS strings.
    selectors is {role: css | {sub: css | [...]}} -- we collect every string leaf."""
    out = []

    def _walk(v):
        if isinstance(v, str):
            if v.strip():
                out.append(v.strip())
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _walk(x)

    _walk((template or {}).get("selectors") or {})
    return out


def score_template_against_html(template, html) -> float:
    """Variant fitness in [0,1]: the fraction of a template's EVALUABLE leaf CSS
    selectors that match >=1 element in `html`. Playwright-only selectors
    (:has-text, :text-matches, and anything BS4 can't parse) are excluded from
    the denominator rather than counted as misses. 0.0 on no html / no evaluable
    selectors. Never raises."""
    if not template or not html:
        return 0.0
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return 0.0
    evaluable = 0
    hit = 0
    for sel in _leaf_selectors(template):
        probe = sel.replace("{resolution}", "1080")
        try:
            n = len(soup.select(probe))
        except Exception:
            continue  # non-CSS / playwright-only -> not evaluable, skip
        evaluable += 1
        if n > 0:
            hit += 1
    if evaluable == 0:
        return 0.0
    return hit / float(evaluable)


def select_best_variant(url: str, html, template_dirs=None):
    """Among the host's template variants, the one whose selectors best fit
    `html`. Ties (incl. all-zero) fall back to host specificity (variant order).
    With <=1 variant or no html, defers to find_template_for_url."""
    variants = find_template_variants_for_url(url, template_dirs)
    if not variants:
        return None
    if len(variants) == 1 or not html:
        return variants[0]
    best = None
    best_score = -1.0
    for v in variants:  # already sorted most-specific first -> stable tie-break
        s = score_template_against_html(v, html)
        if s > best_score:
            best, best_score = v, s
    return best


def describe_template(template):
    if not template:
        return "no template"

    return {
        "host": template.get("host"),
        "status": template.get("status"),
        "file": template.get("_template_file"),
        "selectors": sorted((template.get("selectors") or {}).keys()),
        "patterns": template.get("network_patterns", []),
        "resolutions": template.get("resolutions", []),
    }
