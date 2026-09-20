"""bulk_downloader.breadcrumb_extractor -- resilient breadcrumb and category path extractor.

Row 950 (O888): extracts structured breadcrumb navigation from schema.org
BreadcrumbList JSON-LD, HTML microdata/nav markup, or URL path fallbacks,
converting breadcrumb hierarchies into sanitized filesystem category directory paths.
0 site logins touched (Rule 21).
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional
from urllib.parse import unquote, urlparse

# Characters forbidden in directory or filenames on common filesystems (Windows, POSIX).
_FORBIDDEN_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_MULTIPLE_SPACES = re.compile(r"\s+")
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})
_ROOT_NAMES = frozenset({"home", "main", "start", "index", "catalog", "root"})
_NOISE_URL_SEGMENTS = frozenset({
    "watch", "video", "videos", "view", "media", "item", "content",
    "p", "post", "posts", "clip", "clips", "detail", "details",
})


def sanitize_path_segment(value: Any, max_length: int = 180) -> str:
    """Sanitize a single breadcrumb or category name for filesystem directory use.

    Replaces illegal filesystem characters, removes traversal components,
    escapes Windows reserved names, and trims trailing dots and whitespace.
    """
    s = str(value or "").strip()
    if not s:
        return "untitled"

    # Replace forbidden chars with underscore
    s = _FORBIDDEN_CHARS.sub("_", s)
    s = s.replace("{", "(").replace("}", ")")
    s = _MULTIPLE_SPACES.sub(" ", s).strip()

    # Traversal neutralization
    if s in (".", "..") or s.startswith(".."):
        s = s.replace(".", "_")

    # Strip trailing dots and spaces (problematic on Windows/SMB)
    s = s.rstrip(". ").lstrip(" ")

    # If empty after stripping, provide fallback
    if not s:
        s = "untitled"

    # Windows reserved device names (e.g. CON, PRN, AUX, NUL)
    upper_base = s.split(".")[0].upper()
    if upper_base in _WINDOWS_RESERVED:
        s = "_" + s

    return s[:max_length]


def breadcrumbs_to_path(
    breadcrumbs: List[str],
    max_depth: Optional[int] = None,
    skip_root: bool = True,
) -> str:
    """Convert an ordered list of breadcrumbs into a sanitized relative directory path."""
    if not breadcrumbs:
        return ""

    crumbs = [c.strip() for c in breadcrumbs if c and c.strip()]
    if not crumbs:
        return ""

    # Optionally drop root navigation crumb (e.g. "Home") if multiple crumbs exist
    if skip_root and len(crumbs) > 1:
        if crumbs[0].lower() in _ROOT_NAMES:
            crumbs = crumbs[1:]

    if max_depth is not None and max_depth > 0:
        crumbs = crumbs[:max_depth]

    sanitized = [sanitize_path_segment(c) for c in crumbs if c]
    return "/".join(sanitized)


def _find_breadcrumblists(obj: Any) -> List[dict]:
    """Recursively discover schema.org BreadcrumbList nodes in JSON-LD structures."""
    found = []
    if isinstance(obj, dict):
        obj_type = obj.get("@type")
        if obj_type == "BreadcrumbList" or (isinstance(obj_type, list) and "BreadcrumbList" in obj_type):
            found.append(obj)
        for val in obj.values():
            found.extend(_find_breadcrumblists(val))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_find_breadcrumblists(item))
    return found


def extract_jsonld_breadcrumbs(html_or_data: Any) -> List[str]:
    """Extract ordered breadcrumb names from schema.org BreadcrumbList JSON-LD."""
    if not html_or_data:
        return []

    data_objects = []
    if isinstance(html_or_data, (dict, list)):
        data_objects.append(html_or_data)
    elif isinstance(html_or_data, str):
        # Scan for <script type="application/ld+json"> blocks
        scripts = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html_or_data,
            re.DOTALL | re.IGNORECASE,
        )
        for content in scripts:
            content = content.strip()
            if not content:
                continue
            try:
                parsed = json.loads(content)
                data_objects.append(parsed)
            except Exception:
                continue

    for root in data_objects:
        lists = _find_breadcrumblists(root)
        for bl in lists:
            items = bl.get("itemListElement")
            if not isinstance(items, list) or not items:
                continue

            extracted = []
            for idx, el in enumerate(items):
                if not isinstance(el, dict):
                    continue

                # Position can be int or string
                pos_raw = el.get("position", idx + 1)
                try:
                    pos = int(pos_raw)
                except (ValueError, TypeError):
                    pos = idx + 1

                # Extract name
                name = el.get("name")
                if not name and isinstance(el.get("item"), dict):
                    name = el["item"].get("name")
                elif not name and isinstance(el.get("item"), str):
                    # Sometimes item is a title or slug
                    name = el.get("name")

                if name and str(name).strip():
                    extracted.append((pos, str(name).strip()))

            if extracted:
                # Sort by position
                extracted.sort(key=lambda x: x[0])
                return [name for _, name in extracted]

    return []


def extract_html_breadcrumbs(html: str) -> List[str]:
    """Extract breadcrumbs from semantic HTML nav, microdata, or common breadcrumb CSS classes."""
    if not html or not isinstance(html, str):
        return []

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    # 1. Microdata check: itemprop="itemListElement"
    microdata_items = soup.find_all(attrs={"itemprop": "itemListElement"})
    if microdata_items:
        crumbs = []
        for item in microdata_items:
            name_el = item.find(attrs={"itemprop": "name"})
            text = name_el.get_text() if name_el else item.get_text()
            clean = _clean_crumb_text(text)
            if clean:
                crumbs.append(clean)
        if crumbs:
            return crumbs

    # 2. Semantic nav element with aria-label="breadcrumb" or class
    nav = soup.find(lambda tag: tag.name == "nav" and (
        "breadcrumb" in tag.get("aria-label", "").lower()
        or "breadcrumb" in " ".join(tag.get("class", [])).lower()
    ))
    if not nav:
        nav = soup.find(attrs={"class": re.compile(r"\bbreadcrumbs?\b", re.I)})

    if nav:
        items = nav.find_all(["li", "a", "span"])
        # If li elements present, prefer li text to avoid duplicate spans/links
        li_items = nav.find_all("li")
        target_items = li_items if li_items else items

        crumbs = []
        for it in target_items:
            # Skip if this element is inside another target item to avoid duplicates
            if target_items == items and it.find_parent(["a", "span"]):
                continue
            text = _clean_crumb_text(it.get_text())
            if text and text not in (">", "/", "»", "›", "→"):
                crumbs.append(text)
        if crumbs:
            return crumbs

    return []


def _clean_crumb_text(text: str) -> str:
    """Normalize text extracted from breadcrumb HTML elements."""
    if not text:
        return ""
    # Strip common breadcrumb separators
    t = text.strip()
    t = re.sub(r"^[\s>/»›→]+|[\s>/»›→]+$", "", t).strip()
    t = _MULTIPLE_SPACES.sub(" ", t)
    return t


def extract_url_breadcrumbs(url: str) -> List[str]:
    """Fallback: extract category path hierarchy from URL path components."""
    if not url:
        return []

    try:
        parsed = urlparse(url)
        path = unquote(parsed.path or "").strip()
    except Exception:
        return []

    if not path or path == "/":
        return []

    segments = [s.strip() for s in path.split("/") if s.strip()]
    if not segments:
        return []

    # Strip file extensions from trailing segment (e.g. video.mp4, page.html)
    if segments and "." in segments[-1]:
        segments[-1] = segments[-1].rsplit(".", 1)[0]

    # Filter out noise prefixes like watch, video, view, clips
    cleaned = []
    for s in segments:
        s_lower = s.lower()
        if s_lower in _NOISE_URL_SEGMENTS:
            continue
        cleaned.append(s)

    # If the last segment looks like a leaf item/ID and we have parent directories,
    # keep only the parent categories
    if len(cleaned) > 1:
        last = cleaned[-1]
        if (
            re.match(r"^v\d+$", last, re.I)
            or re.search(r"-\d+$|_[a-z0-9]+$", last, re.I)
            or re.match(r"^(video|clip|item|media|watch)-", last, re.I)
            or (last.isdigit() and len(cleaned) > 2)
        ):
            cleaned = cleaned[:-1]

    return cleaned


def extract_breadcrumbs(html_or_json: Any, url: Optional[str] = None) -> List[str]:
    """Extract hierarchical breadcrumb list from JSON-LD, HTML, or URL fallback."""
    if html_or_json:
        # 1. Try JSON-LD schema.org
        crumbs = extract_jsonld_breadcrumbs(html_or_json)
        if crumbs:
            return crumbs

        # 2. Try HTML markup
        if isinstance(html_or_json, str):
            crumbs = extract_html_breadcrumbs(html_or_json)
            if crumbs:
                return crumbs

    # 3. Try URL path fallback
    if url:
        crumbs = extract_url_breadcrumbs(url)
        if crumbs:
            return crumbs

    return []


def extract_category_path(
    html: Optional[str] = None,
    url: Optional[str] = None,
    default: str = "",
    skip_root: bool = True,
    max_depth: Optional[int] = None,
) -> str:
    """Extract hierarchical breadcrumbs and return a sanitized relative category directory path."""
    crumbs = extract_breadcrumbs(html, url=url)
    if not crumbs:
        return default

    path = breadcrumbs_to_path(crumbs, max_depth=max_depth, skip_root=skip_root)
    return path if path else default


class BreadcrumbExtractor:
    """Configurable breadcrumb and category path extractor."""

    def __init__(
        self,
        skip_root: bool = True,
        max_depth: Optional[int] = None,
        default: str = "",
    ):
        self.skip_root = skip_root
        self.max_depth = max_depth
        self.default = default

    def extract(self, html: Optional[str] = None, url: Optional[str] = None) -> List[str]:
        return extract_breadcrumbs(html, url=url)

    def extract_path(self, html: Optional[str] = None, url: Optional[str] = None) -> str:
        return extract_category_path(
            html=html,
            url=url,
            default=self.default,
            skip_root=self.skip_root,
            max_depth=self.max_depth,
        )
