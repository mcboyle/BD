"""viewport_clipping_filter -- viewport boundary coordinate filtering.

Row 960 (v3.66.1585): spatial geometry evaluator calculating viewport
intersection bounds and CSS clip polygons to omit off-canvas nodes
from downstream parsing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")

_INSET_100_RE = re.compile(r"inset\(\s*100\s*%\s*\)")
_CLIP_RECT_ZERO_RE = re.compile(r"rect\(\s*0\s*(?:px)?\s*,\s*0\s*(?:px)?\s*,\s*0\s*(?:px)?\s*,\s*0\s*(?:px)?\s*\)")


@dataclass(frozen=True)
class ViewportBounds:
    width: int
    height: int


class ViewportClippingFilter:
    def __init__(self, viewport: ViewportBounds) -> None:
        self._vp = viewport

    def is_off_canvas(self, node: Any) -> bool:
        if _is_css_clipped(node):
            return True

        rect = _get_rect(node)
        if rect is None:
            return False

        x, y, w, h = rect

        if x + w <= 0:
            return True
        if y + h <= 0:
            return True
        if x >= self._vp.width:
            return True
        if y >= self._vp.height:
            return True

        return False

    def filter(self, nodes: Iterable[T]) -> List[T]:
        return [n for n in nodes if not self.is_off_canvas(n)]


def _is_css_clipped(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    style = node.get("style")
    if not isinstance(style, dict):
        return False

    clip_path = style.get("clip-path", "")
    if isinstance(clip_path, str) and _INSET_100_RE.search(clip_path):
        return True

    clip = style.get("clip", "")
    if isinstance(clip, str) and _CLIP_RECT_ZERO_RE.search(clip):
        return True

    return False


def _get_rect(node: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(node, dict):
        return None
    r = node.get("rect")
    if not isinstance(r, dict):
        return None
    try:
        return float(r["x"]), float(r["y"]), float(r["width"]), float(r["height"])
    except (KeyError, ValueError, TypeError):
        return None


def is_off_canvas(node: Any, viewport: ViewportBounds) -> bool:
    return ViewportClippingFilter(viewport).is_off_canvas(node)


def filter_off_canvas(nodes: Iterable[T], viewport: ViewportBounds) -> List[T]:
    return ViewportClippingFilter(viewport).filter(nodes)
