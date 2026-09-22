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

# Route actions are an open class: templates may add site-specific actions via
# ``listing_route_words``.  They are deliberately independent of the route
# root, because a new ``/clips/`` or ``/movies/`` root must not reopen listing
# fan-out.
_LISTING_ROUTE_WORDS = ("sort", "page", "browse", "sites", "gallery", "shorts")


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
    "/film/", "/films/",
    "/clip/", "/clips/",
    "/play/", "view_video.php",
    "/content/item/",         # members-area item pages (ultrafilms)
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
    through to generic hints. ``listing_route_words`` extends the default
    open-class listing actions and ``scene_url_hints`` the default scene
    hints, neither changing ``url_patterns`` precedence.
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
    listing_words = set(_LISTING_ROUTE_WORDS)
    if template is not None:
        listing_words.update(str(word).lower().strip("/")
                             for word in (template.get("listing_route_words") or [])
                             if isinstance(word, str))
    try:
        parsed = urlparse(url)
        path_low = parsed.path.lower()
        segments = [segment for segment in path_low.split("/")
                    if segment]
        # Locale prefixes are not a route component (``/en/videos/sort``).
        if segments and len(segments[0]) == 2 and segments[0].isalpha():
            segments = segments[1:]
        if len(segments) >= 2 and segments[1] in listing_words:
            return False
    except Exception:
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
    # Heuristic: any scene-hint substring, defaults plus the template's own
    scene_hints = list(_SCENE_URL_HINTS)
    if template is not None:
        scene_hints.extend(str(hint).lower().strip()
                           for hint in (template.get("scene_url_hints") or [])
                           if isinstance(hint, str) and hint.strip())
    for h in scene_hints:
        # Redirect/query values are not the page's route.  Query-bearing
        # hints (such as /watch?v=) must identify the actual path and query.
        hint_path, separator, hint_query = h.partition("?")
        if separator:
            if hint_path in path_low and parsed.query.lower().startswith(hint_query):
                return True
        elif h in path_low:
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
    truncated: bool = False


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
    truncated = False
    pagination_error = ""

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
            truncated = True
            pagination_error = f"page_load_failed:{type(e).__name__}"
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
        ok=(len(final) > 0 and not truncated),
        urls=final,
        titles={url: all_titles[url] for url in final if url in all_titles},
        page_count=page_count,
        base_url=listing_url,
        error=pagination_error,
        truncated=truncated,
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


# ─── Row 906: DOM-topology single-entity-vs-aggregate classifier ────
#
# `is_likely_listing_url` above reads the URL string. It says nothing when a
# collection page has no listing keyword in its path -- a common shape for
# site-generated model/gallery pages. This section reads the PAGE ITSELF: a
# collection is a repeated grid of similarly-shaped media "cards", a single
# item is not. `card_count` is the size of the largest group of DOM
# siblings under one parent that share the same tag + CSS class SET (order
# invariant) AND look like a media card: a link that wraps or contains an
# image/video, outside navigation/header/footer/pagination chrome. Bare
# navigation anchors, menus and pagers are never cards, so a single-video
# page with a three-item nav bar stays a single item.


_CARD_GRID_DENSITY_JS = """() => {
    const CHROME = 'nav, header, footer, aside, [role="navigation"], '
        + '[role="menubar"], [role="tablist"], [aria-label*="agination" i], '
        + '[class*="pagination" i], [class*="pager" i], [class*="breadcrumb" i], '
        + '[class*="menu" i]';
    const classKey = el => {
        const raw = el.getAttribute('class') || '';
        return raw.trim().split(/\\s+/).filter(Boolean).sort().join('.');
    };
    const isMediaCard = el => {
        if (el.closest(CHROME)) return false;
        const link = el.matches('a[href]') ? el : el.querySelector('a[href]');
        if (!link) return false;
        return !!el.querySelector('img, picture, video');
    };
    const groups = new Map();
    document.querySelectorAll('body *').forEach(el => {
        if (!isMediaCard(el)) return;
        const parent = el.parentElement;
        if (!parent) return;
        const key = el.tagName + '.' + classKey(el);
        let bucket = groups.get(parent);
        if (!bucket) { bucket = new Map(); groups.set(parent, bucket); }
        bucket.set(key, (bucket.get(key) || 0) + 1);
    });
    let max = 0;
    groups.forEach(bucket => {
        bucket.forEach(count => { if (count > max) max = count; });
    });
    // A primary player OUTSIDE every media card marks a single-entity page:
    // the repeated cards beside it are "related" widgets, not the page's
    // subject (row906 fixer E2). A card-wrapped <video> (a hover preview
    // inside a grid tile) does not count as a player.
    const PLAYER = 'video, iframe[src*="embed" i], iframe[src*="player" i], '
        + 'iframe[src*="youtube" i], iframe[src*="vimeo" i], iframe[src*="jwplayer" i]';
    // a LEAF card is a media card with no media card inside it (a grid
    // tile); a container holding many tiles also "looks like" a card but
    // is not one, so only leaf-card ancestors make a video a preview.
    const isLeafCard = el => {
        if (!isMediaCard(el)) return false;
        for (const d of el.querySelectorAll('*')) { if (isMediaCard(d)) return false; }
        return true;
    };
    let player = false;
    document.querySelectorAll(PLAYER).forEach(el => {
        if (player || el.closest(CHROME)) return;
        let node = el.parentElement, inCard = false;
        while (node && node !== document.body) {
            if (isLeafCard(node)) { inCard = true; break; }
            node = node.parentElement;
        }
        if (!inCard) player = true;
    });
    return {card_count: max, player: player};
}"""


@dataclass
class TopologyResult:
    """Outcome of classify_dataset_topology()."""
    ok: bool = False
    is_aggregate: bool = False
    card_count: int = 0
    has_player: bool = False
    base_url: str = ""
    error: str = ""


def classify_dataset_topology(
    page, url: str, *,
    min_cards: int = 3,
    page_load_timeout_ms: int = 30000,
) -> TopologyResult:
    """Classify a page as a single entity or an aggregate/collection by its
    DOM topology (repeated sibling "card" structure), independent of the
    URL text.

    `min_cards`: the smallest repeated-sibling group size that counts as a
    collection grid. Below it, the page is a single item -- this is what
    keeps a couple of "related content" widgets from being misread as a
    collection (acceptance #3, zero single-asset false positives).

    Returns TopologyResult -- never raises (fail-open, same contract as
    extract_playlist_urls above: caller falls back to single-item handling).
    """
    try:
        from bulk_downloader.dataset_structural_classifier import DatasetStructuralClassifier
        res = DatasetStructuralClassifier(min_cards=min_cards).classify_dom_page(
            page, url, page_load_timeout_ms=page_load_timeout_ms
        )
        if not res.ok:
            return TopologyResult(ok=False, base_url=url, error=res.error)
        return TopologyResult(
            ok=True,
            base_url=url,
            card_count=res.card_count,
            has_player=res.has_player,
            is_aggregate=res.is_aggregate,
        )
    except Exception as e:
        return TopologyResult(ok=False, base_url=url, error=f"classifier_failed:{type(e).__name__}")


@dataclass
class RouteResult:
    """Outcome of route_dataset_page(): the single-entity-vs-aggregate
    routing decision, with the aggregate case's children expanded into a
    batch queue (row906 acceptance #2). Expansion is recursive: child
    links that are themselves collections are classified and expanded in
    turn, depth-bounded, with a visited set for cycle protection.
    `collections` lists every aggregate page visited (root first)."""
    ok: bool = False
    is_aggregate: bool = False
    card_count: int = 0
    single_url: str = ""
    child_urls: list = field(default_factory=list)
    collections: list = field(default_factory=list)
    error: str = ""


def _expand_collection(
    page, url: str, *, template, min_cards, max_pages, max_depth,
    visited: set, budget: list, urls: list, collections: list,
) -> str:
    """Expand one already-classified aggregate page into `urls` (leaf
    scenes) and recurse into same-site listing links that classify as
    aggregates themselves. Returns the first extraction error ("" if none).
    `budget` is a one-element list holding the remaining classification
    navigations (shared across the whole traversal)."""
    collections.append(url)
    playlist = extract_playlist_urls(page, url, template=template, max_pages=max_pages)
    urls.extend(playlist.urls)
    error = playlist.error
    if max_depth <= 0:
        return error
    # Candidate sub-collections: same-site links that look like listings
    # but are not scenes and were never visited. `_normalize_links` on the
    # last extraction page is enough for the fake-page contract; a real
    # multi-page listing's children are the scenes, not more listings.
    candidates = _normalize_links(page, url, None, include_titles=True)
    # The URL text is a PRIORITY HINT, never a gate (row906 fixer E1): links
    # with a listing keyword are classified first, every other same-site
    # non-scene link after them, all inside the shared navigation budget --
    # a child collection with no keyword in its path is still inspected.
    ordered: list = []
    for candidate in candidates:
        child = candidate.get("url", "") if isinstance(candidate, dict) else candidate
        if not child or child in visited or child in ordered:
            continue
        if not _is_same_etld1(child, url):
            continue
        if _looks_like_scene_url(child, template=template):
            continue
        ordered.append(child)
    ordered.sort(key=lambda c: 0 if is_likely_listing_url(c, template=template) else 1)
    for child in ordered:
        if child in visited:
            continue
        if budget[0] <= 0:
            error = error or "expansion_budget_exhausted"
            break
        visited.add(child)
        budget[0] -= 1
        topo = classify_dataset_topology(page, child, min_cards=min_cards)
        if not topo.ok or not topo.is_aggregate:
            continue
        sub_error = _expand_collection(
            page, child, template=template, min_cards=min_cards,
            max_pages=max_pages, max_depth=max_depth - 1, visited=visited,
            budget=budget, urls=urls, collections=collections,
        )
        error = error or sub_error
    return error


def route_dataset_page(
    page, url: str, *,
    template: Optional[dict] = None,
    min_cards: int = 3,
    max_pages: int = 1,
    max_depth: int = 3,
    max_collections: int = 64,
) -> RouteResult:
    """Classify `url` and route it: a single item routes to itself, an
    aggregate/collection routes to its expanded child scene queue.

    `max_depth`: how many levels of nested child collections to expand
    below the root (0 = only the root's own scenes). `max_collections`:
    hard cap on child-collection classifications (navigations) across the
    whole traversal. A URL is visited at most once.

    Never raises. A classification failure short-circuits before any
    fan-out call (no second navigation on a page that never loaded)."""
    topo = classify_dataset_topology(page, url, min_cards=min_cards)
    if not topo.ok:
        return RouteResult(ok=False, error=topo.error)
    if not topo.is_aggregate:
        return RouteResult(ok=True, is_aggregate=False,
                            card_count=topo.card_count, single_url=url)
    urls: list = []
    collections: list = []
    error = _expand_collection(
        page, url, template=template, min_cards=min_cards,
        max_pages=max_pages, max_depth=max(0, int(max_depth)),
        visited={url}, budget=[max(0, int(max_collections))],
        urls=urls, collections=collections,
    )
    final = _dedup_preserving_order(urls)
    return RouteResult(
        ok=bool(final) and not error, is_aggregate=True,
        card_count=topo.card_count, child_urls=final,
        collections=collections, error=error,
    )


__all__ = [
    "is_likely_listing_url",
    "extract_playlist_urls",
    "PlaylistResult",
    "classify_dataset_topology",
    "route_dataset_page",
    "TopologyResult",
    "RouteResult",
    "_is_same_etld1",
    "_looks_like_scene_url",
    "_guess_next_page_url",
]
