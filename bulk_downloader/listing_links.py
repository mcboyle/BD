"""Video-link extraction for listing-page scrapes.

One classifier for both callers -- /api/scrape_listing (app_scrape_listing) and the
subscription scanner (SiteRunner._scrape_listing_urls) -- which previously carried
byte-identical copies of the regexes below.

Heuristic only: anchors whose href looks like a video (a media extension, a
/video/-style path segment, or a scene-id segment), minus listing pages.

dl95-pussyspace-2: pussyspace.com's home page yielded only /video/random/,
/video/1-10min/, /video/10-60min/ and /video/movies/ -- sort and duration facets of
the site's own video index -- while its /vid-<id>-<slug> scene links were missed.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

_HREF = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.I)
VIDEO_EXT = re.compile(r"\.(mp4|mkv|webm|avi|mov|m3u8|mpd|ts|flv)(\?|#|$)", re.I)
VIDEO_PATTERNS = re.compile(r"/(video|watch|v|play|movie|episode|stream)/", re.I)
LISTING_PATTERNS = re.compile(
    r"/(category|categories|tag|tags|page|search|browse|list|channel|playlist|feed|sitemap)/", re.I)
# a scene page whose own path segment carries the id: /vid-6167743-some-title
SCENE_ID_SEGMENT = re.compile(r"/(?:vid|video|v|watch|movie|scene)[-_]\d{3,}(?:[-_/.]|$)", re.I)
# the one segment after /video/ that names a sort order or a duration bucket, not a scene
FACET_SEGMENT = re.compile(
    r"^(?:random|movies|popular|newest|new|latest|top|top-rated|best|trending|hd|featured|recent"
    r"|longest|most-viewed|most-popular|all|\d+(?:-\d+)?-?min(?:s|utes)?\+?)$", re.I)


def _is_facet(path: str) -> bool:
    """/video/, /video/random/, /video/1-10min/: the site's own video index or a facet of it."""
    last = None
    for last in VIDEO_PATTERNS.finditer(path):
        pass
    if last is None:
        return False
    rest = path[last.end():].strip("/")
    return not rest or ("/" not in rest and bool(FACET_SEGMENT.match(rest)))


def is_video_link(absolute: str, *, filter_listings: bool = True) -> bool:
    """Whether an absolute URL looks like a single video's page or file."""
    path = urlparse(absolute).path
    by_shape = bool(VIDEO_EXT.search(absolute) or SCENE_ID_SEGMENT.search(path))
    if not (by_shape or VIDEO_PATTERNS.search(absolute)):
        return False
    if not filter_listings:
        return True
    if LISTING_PATTERNS.search(absolute) and not path.rstrip("/").rsplit("/", 1)[-1].isdigit():
        return False  # listing page, not a video page
    return by_shape or not _is_facet(path)


def extract_video_links(html: str, base_url: str, *, max_links: int,
                        filter_listings: bool = True) -> list[str]:
    """The distinct video-looking links of `html`, absolute against `base_url`, in page order."""
    seen, found = set(), []
    for href in _HREF.findall(html):
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith("http") or absolute in seen:
            continue
        if not is_video_link(absolute, filter_listings=filter_listings):
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= max_links:
            break
    return found
