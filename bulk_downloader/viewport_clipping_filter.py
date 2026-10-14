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

_INSET_RE = re.compile(r"inset\(([^()]*)\)", re.IGNORECASE)
_CLIP_RECT_RE = re.compile(r"rect\(([^()]*)\)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))(px|%)?", re.IGNORECASE)


def _numbers(tokens: List[str], unit: str) -> Optional[List[float]]:
    """Each token as a number in ``unit`` (a bare 0 has no unit), or None when
    any token is something else (``auto``, ``em``, the other unit...): an
    argument this filter cannot measure is never a reason to drop a node."""
    out: List[float] = []
    for tok in tokens:
        m = _NUMBER_RE.fullmatch(tok)
        if m is None:
            return None
        value, tok_unit = float(m.group(1)), (m.group(2) or "").lower()
        if tok_unit != unit and not (tok_unit == "" and value == 0):
            return None
        out.append(value)
    return out


def _clip_rect_is_empty(clip: str) -> bool:
    """``clip: rect(top, right, bottom, left)`` (commas or the CSS2 space form)
    with no area left: right <= left or bottom <= top. Covers rect(0,0,0,0) and
    the visually-hidden idiom rect(1px, 1px, 1px, 1px)."""
    m = _CLIP_RECT_RE.search(clip)
    if m is None:
        return False
    vals = _numbers(m.group(1).replace(",", " ").split(), "px")
    if vals is None or len(vals) != 4:
        return False
    top, right, bottom, left = vals
    return right <= left or bottom <= top


# CSS 2.1 11.1.2: the legacy ``clip`` property applies ONLY to absolutely
# positioned elements (position absolute or fixed). On a static, relative or
# sticky element it does nothing, however empty its rect() is.
_CLIP_POSITIONS = ("absolute", "fixed")


def _legacy_clip_applies(style: Dict[str, Any]) -> bool:
    """Does ``clip`` act on this node? Yes for position absolute/fixed. A style
    that records no position at all is judged as before (the clip is taken at
    its word); a recorded position that is anything else -- static, relative,
    sticky, or a value this filter does not know -- means the clip cannot be a
    reason to drop the node."""
    if "position" not in style:
        return True
    pos = style.get("position")
    return isinstance(pos, str) and pos.strip().lower() in _CLIP_POSITIONS


def _inset_is_empty(clip_path: str) -> bool:
    """``clip-path: inset(...)`` in percentages (1-4 values, CSS shorthand
    order) that leaves no area: top + bottom >= 100% or left + right >= 100%.
    Covers inset(100%), inset(50%) and inset(100% 0 0 0)."""
    m = _INSET_RE.search(clip_path)
    if m is None:
        return False
    args = re.split(r"\bround\b", m.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
    vals = _numbers(args.split(), "%")
    if vals is None or not 1 <= len(vals) <= 4:
        return False
    top = vals[0]
    right = vals[1] if len(vals) > 1 else top
    bottom = vals[2] if len(vals) > 2 else top
    left = vals[3] if len(vals) > 3 else right
    return top + bottom >= 100 or left + right >= 100


@dataclass(frozen=True)
class ViewportBounds:
    width: int
    height: int

    def __post_init__(self) -> None:
        # A 0x0 or negative viewport would put every node off canvas, and a bool
        # is not a size: refuse both rather than silently drop the whole page.
        for name in ("width", "height"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise ValueError(f"Invalid viewport {name}: {v!r}")


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
    if isinstance(clip_path, str) and _inset_is_empty(clip_path):
        return True

    clip = style.get("clip", "")
    if (isinstance(clip, str) and _clip_rect_is_empty(clip)
            and _legacy_clip_applies(style)):
        return True

    return False


def _get_rect(node: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(node, dict):
        return None
    r = node.get("rect")
    if not isinstance(r, dict):
        return None
    # A bool coordinate is not a measurement (False would read as 0): unmeasurable,
    # so never a reason to drop the node.
    if any(isinstance(r.get(k), bool) for k in ("x", "y", "width", "height")):
        return None
    try:
        return float(r["x"]), float(r["y"]), float(r["width"]), float(r["height"])
    except (KeyError, ValueError, TypeError):
        return None


def is_off_canvas(node: Any, viewport: ViewportBounds) -> bool:
    return ViewportClippingFilter(viewport).is_off_canvas(node)


def filter_off_canvas(nodes: Iterable[T], viewport: ViewportBounds) -> List[T]:
    return ViewportClippingFilter(viewport).filter(nodes)
