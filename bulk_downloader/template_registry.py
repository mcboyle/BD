from __future__ import annotations

import copy
import ipaddress
import json
from pathlib import Path
import re
import time
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


_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _valid_sibling_domain(canonical_host: str, value) -> str:
    if not isinstance(value, str):
        return ""
    raw_domain = value.strip().lower()
    if raw_domain.endswith(".."):
        return ""
    domain = raw_domain.rstrip(".")
    canonical = (canonical_host or "").strip().lower().rstrip(".")
    if (
        not domain
        or domain == "localhost"
        or domain.endswith(".localhost")
        or "." not in domain
    ):
        return ""
    try:
        ipaddress.ip_address(domain)
        return ""
    except ValueError:
        pass
    labels = domain.split(".")
    if all(label.isdigit() for label in labels):
        return ""
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        return ""
    if canonical != domain and not canonical.endswith("." + domain):
        return ""
    return domain


def _template_host_match_key(template: dict, url_host: str):
    host = (url_host or "").strip().lower()
    canonical = str((template or {}).get("host") or "").strip().lower()
    if not host or not canonical:
        return None
    if host == canonical:
        return (4, len(canonical))

    match = template.get("match")
    if not isinstance(match, dict):
        match = {}
    normalized_host = host.rstrip(".")
    aliases = match.get("hosts")
    if isinstance(aliases, list):
        exact_aliases = {
            value.strip().lower().rstrip(".")
            for value in aliases
            if isinstance(value, str) and value.strip()
        }
        if normalized_host in exact_aliases:
            return (3, len(normalized_host))

    if _host_matches(canonical, host):
        return (2, len(canonical))

    sibling = _valid_sibling_domain(canonical, match.get("sibling_domain"))
    if sibling and (
        normalized_host == sibling or normalized_host.endswith("." + sibling)
    ):
        return (1, len(sibling))
    return None


# M178: parsed templates cached per file on a stat signature, so a lookup
# re-lists the dir instead of re-reading and re-parsing every template JSON.
# path -> [sig, read_ns, parsed data | _PARSE_FAILED, enabled template | None]
_TEMPLATE_CACHE: dict = {}
_PARSE_FAILED = object()
# Racy-stamp window: a file stamped this close to when we read it may have been
# rewritten in the same clock tick without moving its stamp -> re-read it.
_RACY_NS = 2_000_000_000


def _cached_template_entry(fp: Path):
    try:
        st = fp.stat()
        sig = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino, st.st_dev)
    except OSError:
        st = sig = None
    key = str(fp)
    entry = _TEMPLATE_CACHE.get(key)
    if (
        entry is not None
        and sig is not None
        and entry[0] == sig
        and max(st.st_mtime_ns, st.st_ctime_ns) + _RACY_NS < entry[1]
    ):
        return entry
    read_ns = time.time_ns()
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        data = _PARSE_FAILED
    entry = [sig, read_ns, data, None]
    if sig is not None:
        _TEMPLATE_CACHE[key] = entry
    return entry


def _load_templates_shared(template_dirs=None):
    """load_templates() over the parse cache. The dicts are SHARED with the
    cache: internal read-only use only; anything handed to a caller is copied."""
    template_dirs = template_dirs or DEFAULT_TEMPLATE_DIRS
    out = []

    for d in template_dirs:
        d = Path(d)
        if not d.exists():
            continue

        for fp in sorted(d.glob("*.template.json")):
            entry = _cached_template_entry(fp)
            data = entry[2]
            if data is _PARSE_FAILED:
                continue

            if data.get("status") != "enabled":
                continue

            if entry[3] is None:
                template = dict(data)
                template["_template_file"] = str(fp.resolve())
                entry[3] = template
            out.append(entry[3])

    return out


def load_templates(template_dirs=None):
    return [copy.deepcopy(t) for t in _load_templates_shared(template_dirs)]


def find_template_for_url(url: str, template_dirs=None, *, html=None):
    # One template load per lookup (M178): the CAP-3 variant path below reuses it.
    templates = _load_templates_shared(template_dirs)

    # CAP-3: when a page's HTML is supplied AND the host has more than one
    # matching template variant, pick the variant whose selectors actually fit
    # this page. html=None (every existing caller) keeps the original host-
    # specificity behavior exactly -> backward-compatible.
    if html:
        variants = _template_variants(url, templates)
        if len(variants) > 1:
            return copy.deepcopy(_best_variant(url, html, variants))

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
    for template in templates:
        key = _template_host_match_key(template, host)
        if key is not None and (best_key is None or key > best_key):
            best, best_key = template, key

    return copy.deepcopy(best)


# ── CAP-3: runtime multi-variant template selection ──────────────────
def find_template_variants_for_url(url: str, template_dirs=None):
    """Every host-matching template for `url` (the variants), most-host-specific
    first. This is the candidate pool find_template_for_url chooses one from."""
    variants = _template_variants(url, _load_templates_shared(template_dirs))
    return [copy.deepcopy(t) for t in variants]


def _template_variants(url: str, templates):
    try:
        host = urlparse(url).netloc
    except Exception:
        return []
    matches = []
    for template in templates:
        key = _template_host_match_key(template, host)
        if key is not None:
            matches.append((key, template))
    matches.sort(key=lambda match: match[0], reverse=True)
    return [template for _, template in matches]


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
    """Among the most host-specific variants, return the best HTML fit.
    Score ties (including all-zero) keep variant discovery order. With <=1
    variant or no html, return the first discovered variant."""
    variants = _template_variants(url, _load_templates_shared(template_dirs))
    return copy.deepcopy(_best_variant(url, html, variants))


def _best_variant(url: str, html, variants):
    if not variants:
        return None
    if len(variants) == 1 or not html:
        return variants[0]
    host = urlparse(url).netloc
    max_key = _template_host_match_key(variants[0], host)
    variants = [
        variant
        for variant in variants
        if _template_host_match_key(variant, host) == max_key
    ]
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
