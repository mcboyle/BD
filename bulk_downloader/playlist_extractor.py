"""v3.43.75: Playlist URL fan-out.

# What this module is

The user pastes a "listing" URL — category, model page, series page,
search results — and BD expands it into the individual scene URLs and
queues them all.

Examples:

    https://wowgirls.com/category/blonde/
        → all scene URLs on that category page
    https://www.vixen.com/series/cliche
        → all episode URLs in the series
    https://www.brazzers.com/pornstar/jane-doe
        → all scenes featuring that pornstar
    https://www.aylo.com/search?q=foo
        → all hits on the search page

# Strategy

Two detection paths:

  1. **Site-template-driven** (preferred): the site's template (in
     templates.py or user_templates) declares two fields:
       - `playlist_url_patterns`: regex list — matched URLs are
         treated as listings
       - `playlist_scene_link_selector`: CSS selector matching the
         per-scene links on the listing page
     When both are set, we use them.
  2. **Heuristic fallback**: when no template hints, sniff the URL
     for common keywords (`/category/`, `/playlist/`, `/model/`,
     `/series/`, `/search`, `/tag/`, `/pornstar/`). On match, look
     for common scene-link patterns: any `<a href="...">` whose
     href has the same eTLD+1 AND looks like a scene URL (matches
     a site's `scene_url_patterns` if available, OR contains
     `/video/`, `/scene/`, `/watch/`).

# Pagination

Optional, opt-in per site via `playlist_max_pages` (default 1 — only
first page). When >1, we walk the page-navigation pattern (`?page=N`
or "Next" link). Pagination support is best-effort; sites with
infinite-scroll need a different strategy (out of scope here).

# Output

`extract_playlist_urls(...)` returns a list of scene URLs in DOM
order. Duplicates removed (some listings repeat URLs for "related"
sections). Caller batch-adds them via the existing `_route_urls_internal`
path so they get routed to the right site queue.

# Fail-open

Every step is wrapped:
  - No template match AND no heuristic match → return [] (treat as
    single scene URL)
  - Playwright failure (page load, evaluate, etc.) → return []
  - 0 scene URLs extracted → return [] (caller treats as miss)

The caller never gets an exception. Worst case: the user added what
looked like a listing URL but BD couldn't expand it; we just queue the
literal URL as a single item and the standard worker tries it. If
that ALSO fails, the user gets a normal "needs review" entry — no
worse off than without this feature.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

log = logging.getLogger(__name__)


# ─── Heuristic patterns (when site template lacks playlist config) ──


# URL substrings that suggest "this is a listing page, not a scene"
_LISTING_KEYWORDS = (
    "/category/", "/categories/", "/playlist/", "/playlists/",
    "/model/", "/models/", "/pornstar/", "/pornstars/", "/star/",
    "/stars/", "/series/", "/show/", "/shows/", "/tag/", "/tags/",
    "/search", "/results", "/channel/", "/channels/", "/studio/",
    "/studios/", "/site/", "/network/",
)


# Per-extractor "scene URL looks like THIS" patterns. Used as a
# secondary filter — listing pages often link to navigation as well as
# scenes. If a site is known and we have a scene pattern, prefer it.
# Same shape as templates.py URL patterns: substring or regex.
_SCENE_URL_HINTS = (
    "/video/", "/videos/",     # generic
    "/scene/", "/scenes/",
    "/watch/", "/watch?v=",
    "/episode/", "/episodes/",
    "/movie/", "/movies/",
    "/clip/", "/clips/",
    "/play/", "view_video.php",
)


# Anchors that are definitely NOT scene links
_NON_SCENE_HINTS = (
    "/login", "/signup", "/register", "/forgot",
    "/help", "/faq", "/about", "/contact", "/legal",
    "/terms", "/privacy", "/dmca", "/2257",
    "/account", "/billing", "/membership", "/upgrade",
    "/feedback", "/support",
    "javascript:", "mailto:", "tel:",
    "#",  # in-page anchors
)


def is_likely_listing_url(
    url: str, *,
    template: Optional[dict] = None,
) -> bool:
    """Decide if a URL looks like a listing page.

    `template`: optional dict from templates.py for the site. If it
    declares `playlist_url_patterns`, those win. Otherwise heuristic.

    Returns False on bad input. Conservative: when in doubt, returns
    False so we don't accidentally fan out scene URLs.
    """
    if not url or not isinstance(url, str):
        return False
    # 1. Template-declared patterns
    if template is not None:
        pats = template.get("playlist_url_patterns") or []
        if pats:
            for pat in pats:
                try:
                    if re.search(pat, url, re.IGNORECASE):
                        return True
                except re.error:
                    continue
            # Template specified patterns but none matched → not a listing
            return False
    # 2. Heuristic: any listing keyword
    url_low = url.lower()
    for kw in _LISTING_KEYWORDS:
        if kw in url_low:
            return True
    return False


def _is_same_etld1(url1: str, url2: str) -> bool:
    """Quick check that two URLs share an eTLD+1 (same site).

    v3.66.1013: delegates to bulk_downloader.registrable_domain. The previous
    body compared `hostname.split(".")[-2:]`, which is right for
    members.vip4k.com and WRONG for every multi-part public suffix -- measured
    against the shipped code, it answered True for victim.co.uk vs
    attacker.co.uk and for two unrelated github.io pages. This predicate gates
    whether a URL discovered in a listing gets FOLLOWED, so that was a scope
    escape rather than a cosmetic error. The shared rule also fails closed on
    unparseable input, where "I cannot tell" must not mean yes.
    """
    from .registrable_domain import same_site
    return same_site(url1, url2)


def _looks_like_scene_url(
    url: str, *,
    template: Optional[dict] = None,
) -> bool:
    """Heuristic: does this URL look like a scene page (not nav,
    login, listing, etc.)?

    `template`: when the site declares `url_patterns` (the standard
    scene-URL patterns in templates.py), use them. Otherwise fall
    through to generic hints.
    """
    if not url or not isinstance(url, str):
        return False
    url_low = url.lower()
    # Negative filters first
    for skip in _NON_SCENE_HINTS:
        if url_low.startswith(skip) or skip in url_low:
            return False
    # Don't fan out into more listings
    for kw in _LISTING_KEYWORDS:
        if kw in url_low:
            return False
    # Template-declared scene URL patterns
    if template is not None:
        pats = template.get("url_patterns") or []
        if pats:
            for pat in pats:
                try:
                    if re.search(pat, url, re.IGNORECASE):
                        return True
                except re.error:
                    continue
            return False  # template specified, didn't match
    # Heuristic: any scene-hint substring
    for h in _SCENE_URL_HINTS:
        if h in url_low:
            return True
    return False


# ─── Extraction via Playwright ─────────────────────────────────────


@dataclass
class PlaylistResult:
    """Outcome of extract_playlist_urls()."""
    ok: bool = False
    urls: list = field(default_factory=list)   # scene URLs in DOM order
    titles: dict = field(default_factory=dict)  # scene URL -> listing-card title
    page_count: int = 0
    base_url: str = ""
    error: str = ""


def _normalize_links(
    page, listing_url: str, selector: Optional[str] = None,
    *, include_titles: bool = False,
) -> list:
    """Run document.querySelectorAll, normalize hrefs relative to the
    listing URL, return a list of absolute URLs.

    `selector` if set is used; otherwise we scan all <a href> elements.
    """
    if include_titles and selector:
        js = f"""() => {{
            const els = document.querySelectorAll({selector!r});
            return Array.from(els).map(el => {{
                const a = el.tagName === 'A' ? el : el.querySelector('a');
                if (!a || !a.href) return null;
                const title = a.getAttribute('aria-label')
                           || a.getAttribute('title')
                           || (a.textContent || '').trim();
                return {{url: a.href, title: title.replace(/\\s+/g, ' ').slice(0, 1000)}};
            }}).filter(Boolean);
        }}"""
    elif include_titles:
        js = """() => {
            const links = document.querySelectorAll('a[href]');
            return Array.from(links).map(a => {
                const title = a.getAttribute('aria-label')
                           || a.getAttribute('title')
                           || (a.textContent || '').trim();
                return {url: a.href || '',
                        title: title.replace(/\\s+/g, ' ').slice(0, 1000)};
            }).filter(x => x.url);
        }"""
    elif selector:
        js = f"""() => {{
            const els = document.querySelectorAll({selector!r});
            return Array.from(els).map(el => {{
                if (el.tagName === 'A') return el.href || '';
                // Selector matched something non-anchor; look for inner <a>
                const a = el.querySelector('a');
                return a ? (a.href || '') : '';
            }}).filter(Boolean);
        }}"""
    else:
        js = """() => {
            const links = document.querySelectorAll('a[href]');
            return Array.from(links).map(a => a.href || '').filter(Boolean);
        }"""
    try:
        raw = page.evaluate(js)
    except Exception as e:
        log.debug("playlist: link extraction raised: %s", e)
        return []
    if not isinstance(raw, list):
        return []
    out: list = []
    for item in raw:
        if include_titles and isinstance(item, dict):
            href = item.get("url") or ""
            title = item.get("title") or ""
        else:
            # Compatibility for direct callers and old fixture fakes that
            # return URL strings even when metadata was requested.
            href = item
            title = ""
        if not isinstance(href, str) or not href:
            continue
        try:
            absolute = urljoin(listing_url, href)
            # Strip any fragment so different in-page anchors to the
            # same scene dedupe correctly
            parsed = urlparse(absolute)
            absolute = urlunparse(parsed._replace(fragment=""))
            if include_titles:
                out.append({
                    "url": absolute,
                    "title": " ".join(str(title).split())[:1000],
                })
            else:
                out.append(absolute)
        except Exception:
            continue
    return out


def _dedup_preserving_order(items: list) -> list:
    seen: set = set()
    out: list = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def extract_playlist_urls(
    page,
    listing_url: str,
    *,
    template: Optional[dict] = None,
    max_pages: int = 1,
    page_load_timeout_ms: int = 30000,
) -> PlaylistResult:
    """Walk a listing URL on the given Playwright `page` and return
    the scene URLs found.

    `template`: optional dict (templates.py entry) with optional
    `playlist_scene_link_selector` and `url_patterns` keys.

    `max_pages`: walk this many pages of pagination (using `?page=N`
    or template-declared pattern). 1 = only the first page.

    Returns PlaylistResult — never raises.
    """
    if not listing_url:
        return PlaylistResult(ok=False, error="empty_url")
    if page is None:
        return PlaylistResult(ok=False, error="page_is_none")

    scene_selector = None
    if template is not None:
        sel = template.get("playlist_scene_link_selector")
        if sel and isinstance(sel, str):
            scene_selector = sel

    all_urls: list = []
    all_titles: dict = {}
    page_count = 0
    current_url = listing_url

    for page_idx in range(max(1, int(max_pages))):
        try:
            page.goto(current_url, wait_until="domcontentloaded",
                      timeout=page_load_timeout_ms)
        except Exception as e:
            if page_idx == 0:
                return PlaylistResult(
                    ok=False, base_url=listing_url,
                    error=f"page_load_failed:{type(e).__name__}",
                )
            # On subsequent pages, just stop pagination
            break

        page_count += 1
        # Extract candidate links
        candidates = _normalize_links(
            page, current_url, scene_selector, include_titles=True)
        # Filter to plausible scene URLs (same-site, looks-like-scene)
        for candidate in candidates:
            u = (candidate.get("url", "")
                 if isinstance(candidate, dict) else candidate)
            title = (candidate.get("title", "")
                     if isinstance(candidate, dict) else "")
            if not _is_same_etld1(u, listing_url):
                continue
            if not _looks_like_scene_url(u, template=template):
                continue
            all_urls.append(u)
            if title and u not in all_titles:
                all_titles[u] = title

        # Pagination: bail if we have enough or no more pages
        if page_idx + 1 >= max_pages:
            break
        # Heuristic next-page URL
        next_url = _guess_next_page_url(current_url, page_idx + 2)
        if not next_url or next_url == current_url:
            break
        current_url = next_url

    final = _dedup_preserving_order(all_urls)
    return PlaylistResult(
        ok=(len(final) > 0),
        urls=final,
        titles={url: all_titles[url] for url in final if url in all_titles},
        page_count=page_count,
        base_url=listing_url,
    )


def _guess_next_page_url(current_url: str, page_num: int) -> str:
    """Best-effort: given a listing URL, return the URL for page N.

    Three patterns handled:
      - `?page=N` query param (preferred — most common)
      - `/page/N/` path segment (WordPress style)
      - Existing `?page=N` increments to N+1

    Returns "" if we can't construct a sensible URL.
    """
    if not current_url or page_num < 2:
        return ""
    try:
        parsed = urlparse(current_url)
        # If query already contains page=X, replace it
        q = parsed.query or ""
        if re.search(r"(^|&)page=\d+", q):
            new_q = re.sub(r"(^|&)page=\d+", lambda m: f"{m.group(1)}page={page_num}", q)
            return urlunparse(parsed._replace(query=new_q))
        # If path contains /page/N/
        if re.search(r"/page/\d+/?$", parsed.path):
            new_path = re.sub(r"/page/\d+/?$", f"/page/{page_num}/", parsed.path)
            return urlunparse(parsed._replace(path=new_path))
        # Otherwise, append ?page=N
        new_q = (q + "&" if q else "") + f"page={page_num}"
        return urlunparse(parsed._replace(query=new_q))
    except Exception:
        return ""


__all__ = [
    "is_likely_listing_url",
    "extract_playlist_urls",
    "PlaylistResult",
    "_is_same_etld1",
    "_looks_like_scene_url",
    "_guess_next_page_url",
]
