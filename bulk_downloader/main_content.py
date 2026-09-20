"""Isolate the primary media container of a page from layout noise.

Templates that harvest links from a whole document pick up sidebar, header
and footer menus. This module finds the largest primary media element that
is not inside a noise region, then climbs its ancestors only while the
enclosing element stays media-dense: few links, low link-text density. The
element where climbing stops is the player boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NOISE_TAGS = frozenset({
    "nav", "aside", "header", "footer", "form", "script", "style",
    "noscript", "template", "svg",
})
NOISE_HINT = re.compile(
    r"(?:^|[\s_-])(?:sidebar|side-?bar|nav|menu|footer|header|related|"
    r"recommend|comments?|share-?bar|breadcrumb|ad(?:vert)?s?|banner|promo)"
    r"(?:$|[\s_-])",
    re.IGNORECASE,
)
MEDIA_TAGS = frozenset({"video", "audio", "iframe", "embed", "object"})
MIN_IMAGE_AREA = 200 * 150
MAX_CONTAINER_LINKS = 2
MAX_LINK_DENSITY = 0.25
MIN_TEXT_FOR_DENSITY = 40
_DIM = re.compile(r"(?<![\w-])(width|height)\s*:\s*(\d+(?:\.\d+)?)px", re.IGNORECASE)
# a candidate that is not rendered, or is a tracker's frame, is never the primary
_HIDDEN_STYLE = re.compile(r"(?<![\w-])(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
_TRACKER_HINT = re.compile(
    r"hjsafecontext|hotjar|gtm|googletagmanager|doubleclick|adsystem|pixel|beacon|track(?:er|ing)|analytics",
    re.IGNORECASE)
_PLAYERISH_SRC = re.compile(
    r"player|embed|youtube|youtu\.be|vimeo|dailymotion|jwp|theo|video|stream|\.m3u8|\.mp4|/e/", re.IGNORECASE)
# rank first, declared area second: a <video> is the page's media before an
# iframe that only LOOKS like a player, and both come before an image; a
# player sized by CSS class declares no area at all
_TAG_RANK = {"video": 4, "audio": 4, "embed": 2, "object": 2, "iframe": 1, "img": 1}


@dataclass(frozen=True)
class Metrics:
    text_chars: int = 0
    link_chars: int = 0
    link_count: int = 0
    media_count: int = 0
    area: int = 0
    depth: int = 0

    @property
    def link_density(self) -> float:
        return self.link_chars / self.text_chars if self.text_chars else 0.0


@dataclass(frozen=True)
class Result:
    found: bool
    tag: str = ""
    element_id: str = ""
    classes: tuple[str, ...] = ()
    selector: str = ""
    metrics: Metrics = field(default_factory=Metrics)
    hrefs: tuple[str, ...] = ()


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _dimension(el, name: str) -> float:
    raw = el.get(name)
    if raw is not None:
        m = re.match(r"\s*(\d+(?:\.\d+)?)", str(raw))
        if m:
            return float(m.group(1))
    for key, value in _DIM.findall(el.get("style") or ""):
        if key.lower() == name:
            return float(value)
    return 0.0


def _own_area(el) -> int:
    return int(_dimension(el, "width") * _dimension(el, "height"))


def _is_noise(el) -> bool:
    if el.name in NOISE_TAGS:
        return True
    hints = " ".join([el.get("id") or "", *(el.get("class") or []), el.get("role") or ""])
    return bool(hints) and bool(NOISE_HINT.search(hints))


def _in_noise(el) -> bool:
    return any(_is_noise(p) for p in el.parents if p.name)


def _is_media(el) -> bool:
    if el.name in MEDIA_TAGS:
        return True
    return el.name == "img" and _own_area(el) >= MIN_IMAGE_AREA


def _is_rendered_media(el) -> bool:
    """A media element that can be the page's primary: not display:none /
    visibility:hidden / the `hidden` attribute, not declared 1px or smaller,
    not an about:blank frame, not a tracker's frame (Hotjar's
    _hjSafeContext_* is a 1x1 display:none iframe under <body>)."""
    if not _is_media(el):
        return False
    if el.has_attr("hidden") or _HIDDEN_STYLE.search(el.get("style") or ""):
        return False
    w, h = _dimension(el, "width"), _dimension(el, "height")
    if (0 < w <= 1) or (0 < h <= 1):
        return False
    src = (el.get("src") or "").strip().lower()
    if el.name == "iframe" and (src == "about:blank"):
        return False
    hints = " ".join([el.get("id") or "", *(el.get("class") or []), el.get("name") or ""])
    return not _TRACKER_HINT.search(hints)


def _rank(el) -> int:
    rank = _TAG_RANK.get(el.name, 0)
    if el.name == "iframe" and _PLAYERISH_SRC.search(el.get("src") or ""):
        rank = 3
    return rank


def _depth(el) -> int:
    return sum(1 for p in el.parents if p.name)


class _Index:
    """Per-document metrics, computed once per element (memoised bottom-up):
    a parent's metrics are its own direct text plus its children's, so the
    whole tree costs O(n) however many ancestors are climbed."""

    def __init__(self):
        self._memo: dict = {}

    def metrics(self, el, in_link: bool | None = None) -> tuple:
        """(text_chars, link_chars, link_count, media_count, area) of el's subtree."""
        key = id(el)
        got = self._memo.get(key)
        if got is not None:
            return got
        if in_link is None:
            in_link = any(p.name == "a" for p in el.parents)
        in_link = in_link or el.name == "a"
        text_chars = link_chars = link_count = media_count = 0
        area = _own_area(el) if _is_media(el) else 0
        skip_text = el.name in NOISE_TAGS
        for child in el.contents:
            name = child.name
            if name is None:
                if skip_text:
                    continue
                n = len(child.strip())
                text_chars += n
                if in_link:
                    link_chars += n
                continue
            t, l, lc, mc, a = self.metrics(child, in_link)
            text_chars += t
            link_chars += l
            link_count += lc
            media_count += mc
            if a > area:
                area = a
            if name == "a" and child.get("href"):
                link_count += 1
            if name in MEDIA_TAGS or (name == "img" and _is_media(child)):
                media_count += 1
        got = (text_chars, link_chars, link_count, media_count, area)
        self._memo[key] = got
        return got

    def metrics_at(self, el) -> Metrics:
        t, l, lc, mc, a = self.metrics(el)
        # the container's own declared size counts even when it is not media
        return Metrics(t, l, lc, mc, max(a, _own_area(el)), _depth(el))


def _metrics(el) -> Metrics:
    return _Index().metrics_at(el)


def _selector(el) -> str:
    sel = el.name
    if el.get("id"):
        return f"{sel}#{el['id']}"
    classes = el.get("class") or []
    if classes:
        sel += "." + ".".join(classes)
    return sel


def _result(el, metrics: Metrics) -> Result:
    hrefs = tuple(a["href"] for a in el.find_all("a", href=True))
    return Result(
        found=True,
        tag=el.name,
        element_id=el.get("id") or "",
        classes=tuple(el.get("class") or ()),
        selector=_selector(el),
        metrics=metrics,
        hrefs=hrefs,
    )


def isolate(html) -> Result:
    """Return the tightest container around the page's primary media element.

    ``html`` may be a string or an already-parsed BeautifulSoup document
    (parsing a 200 KB capture costs 50-150 ms by itself; the isolation is
    O(n) on top of it -- see tests for the split).
    """
    soup = html if hasattr(html, "find_all") else _soup(html)
    candidates = [el for el in soup.find_all(True) if _is_rendered_media(el) and not _in_noise(el)]
    if not candidates:
        return Result(found=False)
    primary = max(candidates, key=lambda el: (_rank(el), _own_area(el), -_depth(el)))
    index = _Index()
    best = primary
    best_metrics = index.metrics_at(primary)
    for parent in primary.parents:
        if not parent.name or parent.name in ("html", "body", "[document]") or _is_noise(parent):
            break
        metrics = index.metrics_at(parent)
        link_dense = (metrics.text_chars >= MIN_TEXT_FOR_DENSITY
                      and metrics.link_density > MAX_LINK_DENSITY)
        if metrics.link_count > MAX_CONTAINER_LINKS or link_dense:
            break
        best, best_metrics = parent, metrics
    return _result(best, best_metrics)


def links(html) -> list[str]:
    """Hrefs inside the isolated container only; empty when no media is found."""
    return list(isolate(html).hrefs)


def all_links(html: str) -> list[str]:
    return [a["href"] for a in _soup(html).find_all("a", href=True)]
