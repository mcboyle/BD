"""v3.43.77: Search-and-add by query.

# What this module is

User types a query string (e.g. "blonde", "model name", "anal"). For
each configured site that supports search, BD constructs the search
URL, navigates with Playwright, extracts scene URLs from the results
page, and presents the candidates to the user. User picks which to
queue.

The killer feature for Matt's 91-template deployment: ONE query
expanded across every site that supports search → potentially
hundreds of pre-filtered URLs queued in one click.

# Architecture

Similar to v3.43.75 playlist_extractor — driven by site template
config. Two new template fields:

  - `search_url_pattern`: URL template with `{query}` placeholder.
    Example: `https://wowgirls.com/?s={query}`. BD URL-encodes the
    query before substitution.
  - `search_result_selector`: optional CSS selector for result links.
    When omitted, falls back to the same generic scene-link heuristic
    the playlist extractor uses.

When the template lacks `search_url_pattern`, BD logs the site as
"not searchable" and skips it — no fail. Cross-site aggregation
just returns fewer results.

# Two-phase flow

1. `search_site(site_id, query, ...)` — returns SearchResult with
   URLs + titles + thumbnails. Doesn't queue anything.
2. UI shows the results; user picks which to queue.
3. UI calls existing `/api/route_urls` or `/api/sites/<sid>/urls` to
   queue the selected URLs.

The split lets the user preview before committing. For "search
everything and queue all results" the UI can chain the two phases
itself.

# Fail-open contract

  - Empty query → SearchResult(ok=False, error="empty_query")
  - Site has no search_url_pattern → SearchResult(ok=False,
    error="site_not_searchable")
  - Playwright failure → SearchResult(ok=False, error="navigate_failed:*")
  - Zero results → SearchResult(ok=True, urls=[]) — empty isn't error
  - Bad template URL (invalid {query} substitution etc.) → caught;
    returns clean error

Never raises. Caller always gets a result object.

# Cross-site aggregation

`search_all_sites(query, sites_dict)` iterates every site that's
configured for search. Returns a dict mapping site_id → SearchResult.
Aggregation isn't done here — the UI lays out the per-site results
and the user can dedup/sort.

# Reuses playlist_extractor

Once we navigate to the search URL, extracting the scene URLs IS
the same problem as extracting from a category page. We delegate to
`playlist_extractor.extract_playlist_urls()` with the
search_result_selector as the scene-link selector. Result titles
come from the link's text content if available, otherwise from the
URL.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, quote_plus

log = logging.getLogger(__name__)


# ─── Result types ─────────────────────────────────────────────────


@dataclass
class SearchHit:
    """One result item."""
    url: str = ""
    title: str = ""
    thumbnail_url: str = ""
    duration_s: float = 0.0


@dataclass
class SearchResult:
    """Outcome of search_site() or one site in search_all_sites()."""
    ok: bool = False
    site_id: str = ""
    query: str = ""
    search_url: str = ""               # the URL we navigated to
    hits: list = field(default_factory=list)   # list of SearchHit
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.hits)


# ─── URL construction ────────────────────────────────────────────


_QUERY_PLACEHOLDER_RE = re.compile(r"\{query\}", re.IGNORECASE)


def build_search_url(template: dict, query: str) -> str:
    """Construct the search URL for a site, given its template + query.

    Returns "" when:
      - template has no `search_url_pattern`
      - pattern doesn't contain `{query}` placeholder
      - query is empty

    URL-encodes the query before substitution.
    """
    if not isinstance(template, dict):
        return ""
    if not query or not isinstance(query, str):
        return ""
    pattern = template.get("search_url_pattern", "")
    if not pattern or not isinstance(pattern, str):
        return ""
    if "{query}" not in pattern.lower():
        return ""
    encoded = quote_plus(query.strip())
    # Replace case-insensitively
    return _QUERY_PLACEHOLDER_RE.sub(encoded, pattern)


def is_site_searchable(template: dict) -> bool:
    """True if the template has at least a search_url_pattern."""
    if not isinstance(template, dict):
        return False
    pat = template.get("search_url_pattern", "")
    return bool(pat and isinstance(pat, str) and "{query}" in pat.lower())


# ─── Result extraction ───────────────────────────────────────────


def _extract_link_metadata(page, base_url: str, selector: Optional[str] = None) -> list:
    """Run a JS snippet to extract (url, title, thumb) tuples from
    matching anchor tags. Returns list of dicts.

    `selector` if set is used to pick anchor-or-containing elements.
    Otherwise scans all `a[href]`.

    The JS is defensive — it handles `el.href` (absolute on the
    browser side), reads accessible text labels, and tries common
    thumbnail-image patterns.
    """
    if selector:
        js = """(sel) => {
            const els = document.querySelectorAll(sel);
            return Array.from(els).map(el => {
                let a = el;
                if (a.tagName !== 'A') {
                    a = el.querySelector('a');
                    if (!a) return null;
                }
                const url = a.href || '';
                if (!url) return null;
                // Title: prefer aria-label, then title attr, then text
                let title = a.getAttribute('aria-label')
                         || a.getAttribute('title')
                         || (a.textContent || '').trim();
                title = title.replace(/\\s+/g, ' ').slice(0, 200);
                // Thumb: look for an <img> inside the link or its parent
                let thumb = '';
                const img = a.querySelector('img') ||
                            (a.parentElement ? a.parentElement.querySelector('img') : null);
                if (img) {
                    thumb = img.getAttribute('data-src')
                         || img.getAttribute('data-lazy-src')
                         || img.getAttribute('src') || '';
                }
                return { url, title, thumb };
            }).filter(x => x !== null);
        }"""
        js_args = selector
    else:
        js = """() => {
            const links = document.querySelectorAll('a[href]');
            return Array.from(links).map(a => {
                const url = a.href || '';
                if (!url) return null;
                let title = a.getAttribute('aria-label')
                         || a.getAttribute('title')
                         || (a.textContent || '').trim();
                title = title.replace(/\\s+/g, ' ').slice(0, 200);
                let thumb = '';
                const img = a.querySelector('img');
                if (img) {
                    thumb = img.getAttribute('data-src')
                         || img.getAttribute('data-lazy-src')
                         || img.getAttribute('src') || '';
                }
                return { url, title, thumb };
            }).filter(x => x !== null);
        }"""
        js_args = None
    try:
        if js_args is not None:
            raw = page.evaluate(js, js_args)
        else:
            raw = page.evaluate(js)
    except Exception as e:
        log.debug("search: link extraction raised: %s", e)
        return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _is_same_etld1(url1: str, url2: str) -> bool:
    """Quick same-site check."""
    try:
        h1 = (urlparse(url1).hostname or "").lower()
        h2 = (urlparse(url2).hostname or "").lower()
        if not h1 or not h2:
            return False
        p1 = h1.split(".")[-2:]
        p2 = h2.split(".")[-2:]
        return p1 == p2
    except Exception:
        return False


# Reuse the scene-URL filter from playlist_extractor — same heuristic
# applies here. Import is deferred to avoid hard dependency.
def _looks_like_scene_url(url: str, template: Optional[dict] = None) -> bool:
    try:
        from . import playlist_extractor as _pe
        return _pe._looks_like_scene_url(url, template=template)
    except Exception:
        # Conservative fallback: anything with /video/, /scene/, /watch/
        url_low = (url or "").lower()
        if not url_low:
            return False
        return any(h in url_low for h in (
            "/video/", "/scene/", "/watch", "/episode/", "/movie/",
        ))


# ─── search_site (single site) ───────────────────────────────────


def search_site(
    page,
    site_id: str,
    query: str,
    template: dict,
    *,
    max_results: int = 50,
    page_load_timeout_ms: int = 30000,
) -> SearchResult:
    """Search one site for `query`. Returns SearchResult with hits.

    `page`: a Playwright Page object (caller provides — search_site
    doesn't manage Playwright lifecycle; that's the runner's job).
    """
    import time
    start = time.monotonic()
    if not query or not isinstance(query, str) or not query.strip():
        return SearchResult(ok=False, site_id=site_id, query=query,
                            error="empty_query")
    if not is_site_searchable(template):
        return SearchResult(ok=False, site_id=site_id, query=query,
                            error="site_not_searchable")
    if page is None:
        return SearchResult(ok=False, site_id=site_id, query=query,
                            error="page_is_none")
    search_url = build_search_url(template, query)
    if not search_url:
        return SearchResult(ok=False, site_id=site_id, query=query,
                            error="build_search_url_failed")
    try:
        page.goto(search_url, wait_until="domcontentloaded",
                  timeout=page_load_timeout_ms)
    except Exception as e:
        return SearchResult(
            ok=False, site_id=site_id, query=query,
            search_url=search_url,
            error=f"navigate_failed:{type(e).__name__}",
            elapsed_s=time.monotonic() - start,
        )
    # Extract results
    selector = template.get("search_result_selector", "") or None
    raw = _extract_link_metadata(page, search_url, selector)
    # Filter to scene-looking, in-site URLs and dedupe
    seen: set = set()
    hits: list = []
    for r in raw:
        url = r.get("url", "")
        if not url or url in seen:
            continue
        if not _is_same_etld1(url, search_url):
            continue
        if not _looks_like_scene_url(url, template=template):
            continue
        seen.add(url)
        hits.append(SearchHit(
            url=url,
            title=r.get("title", "")[:200],
            thumbnail_url=r.get("thumb", ""),
        ))
        if len(hits) >= max_results:
            break
    return SearchResult(
        ok=True, site_id=site_id, query=query,
        search_url=search_url, hits=hits,
        elapsed_s=time.monotonic() - start,
    )


# ─── search_all_sites (cross-site) ───────────────────────────────


def search_all_sites(
    page_factory,
    query: str,
    sites: dict,
    *,
    max_results_per_site: int = 20,
    sites_filter: Optional[list] = None,
) -> dict:
    """Search every searchable site in `sites` for `query`.

    `page_factory`: callable returning a fresh Playwright Page object.
    Called once per searchable site. Caller closes the pages.
    `sites`: dict mapping site_id → template config dict.
    `sites_filter`: optional list of site_ids to restrict to. None =
    all searchable sites.

    Returns dict mapping site_id → SearchResult. Sites that aren't
    searchable are still included with ok=False + error=site_not_searchable
    so the UI can show "skipped: 3 sites have no search configured."
    """
    if not query or not isinstance(query, str) or not query.strip():
        return {}
    if not isinstance(sites, dict):
        return {}
    if not callable(page_factory):
        return {}
    results: dict = {}
    for site_id, template in sites.items():
        if sites_filter and site_id not in sites_filter:
            continue
        if not is_site_searchable(template):
            results[site_id] = SearchResult(
                ok=False, site_id=site_id, query=query,
                error="site_not_searchable",
            )
            continue
        try:
            page = page_factory()
        except Exception as e:
            results[site_id] = SearchResult(
                ok=False, site_id=site_id, query=query,
                error=f"page_factory_failed:{type(e).__name__}",
            )
            continue
        try:
            r = search_site(page, site_id, query, template,
                            max_results=max_results_per_site)
        except Exception as e:
            r = SearchResult(
                ok=False, site_id=site_id, query=query,
                error=f"search_raised:{type(e).__name__}:{str(e)[:80]}",
            )
        finally:
            try: page.close()
            except Exception: pass
        results[site_id] = r
    return results


# ─── Stats helper ────────────────────────────────────────────────


def aggregate_stats(results: dict) -> dict:
    """Summarize a search_all_sites result dict for the UI."""
    if not isinstance(results, dict):
        return {"sites_searched": 0, "sites_with_results": 0,
                "total_hits": 0, "sites_skipped": 0, "sites_failed": 0}
    searched = with_results = total = skipped = failed = 0
    for sid, r in results.items():
        if not isinstance(r, SearchResult):
            continue
        if r.ok:
            searched += 1
            if r.count > 0:
                with_results += 1
            total += r.count
        else:
            if r.error == "site_not_searchable":
                skipped += 1
            else:
                failed += 1
    return {
        "sites_searched": searched,
        "sites_with_results": with_results,
        "total_hits": total,
        "sites_skipped": skipped,
        "sites_failed": failed,
    }


__all__ = [
    "SearchHit",
    "SearchResult",
    "build_search_url",
    "is_site_searchable",
    "search_site",
    "search_all_sites",
    "aggregate_stats",
    "_is_same_etld1",
    "_looks_like_scene_url",
]
