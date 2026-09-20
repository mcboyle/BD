"""visibility_filter -- computed style and bounding box visibility evaluator.

Row 941 (v3.66.1583): computed style visibility filter checking display:none,
visibility:hidden, opacity:0, and zero-dimension bounding boxes to exclude
non-rendered element nodes during link and form discovery.

Excludes:
1. display: none
2. visibility: hidden / collapse
3. opacity: 0 (or computed opacity below threshold)
4. zero-dimension bounding boxes (width <= 0 or height <= 0)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypeVar, Union

T = TypeVar("T")

VISIBILITY_FILTER_JS = """
(() => {
    function isElementRendered(el, minOpacity = 0.001, minDimension = 0.0) {
        if (!el || !(el instanceof Element)) return false;
        try {
            const style = window.getComputedStyle(el);
            if (!style) return false;
            if (style.display === 'none') return false;
            if (style.visibility === 'hidden' || style.visibility === 'collapse') return false;
            const op = parseFloat(style.opacity);
            if (!isNaN(op) && op <= minOpacity) return false;
            const rect = el.getBoundingClientRect();
            if (!rect || rect.width <= minDimension || rect.height <= minDimension) return false;
            return true;
        } catch (e) {
            return false;
        }
    }
    window.__bd_is_node_rendered = isElementRendered;
    window.__bd_filter_visible = function(elements, minOpacity, minDimension) {
        if (!elements) return [];
        return Array.from(elements).filter(el => isElementRendered(el, minOpacity, minDimension));
    };
    return true;
})();
"""


@dataclass(frozen=True)
class VisibilityResult:
    is_visible: bool
    reason: str


class VisibilityFilter:
    """Evaluator for element computed visibility and geometry dimensions."""

    def __init__(
        self,
        *,
        min_opacity: float = 0.001,
        min_dimension: float = 0.0,
    ) -> None:
        self.min_opacity = float(min_opacity)
        self.min_dimension = float(min_dimension)

    def evaluate(self, element: Any) -> VisibilityResult:
        """Evaluate whether an element descriptor or node is rendered and visible."""
        if element is None:
            return VisibilityResult(False, "invalid_element")

        style = _extract_style(element)
        if style:
            # 1. display check
            display = str(style.get("display", "")).strip().lower()
            if display == "none":
                return VisibilityResult(False, "display_none")

            # 2. visibility check
            vis = str(style.get("visibility", "")).strip().lower()
            if vis in ("hidden", "collapse"):
                return VisibilityResult(False, "visibility_hidden")

            # 3. opacity check
            op_raw = style.get("opacity")
            if op_raw is not None:
                try:
                    op_val = float(op_raw)
                    if op_val <= self.min_opacity:
                        return VisibilityResult(False, "zero_opacity")
                except (ValueError, TypeError):
                    pass

        # 4. bounding box / dimension check
        rect = _extract_rect(element)
        if rect is not None:
            w, h = rect
            if w <= self.min_dimension or h <= self.min_dimension:
                return VisibilityResult(False, "zero_bounding_box")

        return VisibilityResult(True, "visible")

    def is_visible(self, element: Any) -> bool:
        """Return True if element is rendered and visible."""
        return self.evaluate(element).is_visible

    def filter(self, elements: Iterable[T]) -> List[T]:
        """Filter an iterable of elements, returning only rendered visible items."""
        if not elements:
            return []
        return [el for el in elements if self.is_visible(el)]


def _extract_style(element: Any) -> Optional[Dict[str, Any]]:
    """Extract style mapping from element dictionary, attributes, or properties."""
    if isinstance(element, dict):
        if "style" in element and isinstance(element["style"], dict):
            return element["style"]
        if "computed_style" in element and isinstance(element["computed_style"], dict):
            return element["computed_style"]
        # Inlined style string if present
        if "style" in element and isinstance(element["style"], str):
            return _parse_inline_style(element["style"])

    # Object with style attribute
    style_attr = getattr(element, "computed_style", None) or getattr(element, "style", None)
    if isinstance(style_attr, dict):
        return style_attr
    if isinstance(style_attr, str):
        return _parse_inline_style(style_attr)

    return None


def _parse_inline_style(style_str: str) -> Dict[str, str]:
    """Parse CSS declaration string into property map."""
    result = {}
    if not style_str:
        return result
    for declaration in style_str.split(";"):
        declaration = declaration.strip()
        if not declaration or ":" not in declaration:
            continue
        prop, val = declaration.split(":", 1)
        result[prop.strip().lower()] = val.strip().lower()
    return result


def _extract_rect(element: Any) -> Optional[Tuple[float, float]]:
    """Extract (width, height) from element dictionary or object geometry."""
    if isinstance(element, dict):
        # Direct rect or bounding_box subdict
        for key in ("rect", "bounding_box", "bbox", "dimensions", "geometry"):
            sub = element.get(key)
            if isinstance(sub, dict):
                w = sub.get("width")
                h = sub.get("height")
                if w is not None and h is not None:
                    try:
                        return float(w), float(h)
                    except (ValueError, TypeError):
                        pass
            elif isinstance(sub, (list, tuple)) and len(sub) >= 2:
                # (width, height) or (x, y, width, height)
                try:
                    if len(sub) == 2:
                        return float(sub[0]), float(sub[1])
                    return float(sub[2]), float(sub[3])
                except (ValueError, TypeError):
                    pass

        # Direct keys in element
        w = element.get("width")
        h = element.get("height")
        if w is not None and h is not None:
            try:
                return float(w), float(h)
            except (ValueError, TypeError):
                pass

    # Object attributes
    for key in ("rect", "bounding_box", "bbox"):
        sub = getattr(element, key, None)
        if sub is not None:
            w = getattr(sub, "width", None)
            h = getattr(sub, "height", None)
            if w is not None and h is not None:
                try:
                    return float(w), float(h)
                except (ValueError, TypeError):
                    pass

    w = getattr(element, "width", None)
    h = getattr(element, "height", None)
    if w is not None and h is not None:
        try:
            return float(w), float(h)
        except (ValueError, TypeError):
            pass

    return None


_DEFAULT_FILTER = VisibilityFilter()


def evaluate_visibility(
    element: Any,
    *,
    min_opacity: float = 0.001,
    min_dimension: float = 0.0,
) -> VisibilityResult:
    """Evaluate whether an element is visible and rendered."""
    if min_opacity == 0.001 and min_dimension == 0.0:
        return _DEFAULT_FILTER.evaluate(element)
    return VisibilityFilter(min_opacity=min_opacity, min_dimension=min_dimension).evaluate(element)


def is_visible(
    element: Any,
    *,
    min_opacity: float = 0.001,
    min_dimension: float = 0.0,
) -> bool:
    """Return True if element is visible and rendered."""
    return evaluate_visibility(element, min_opacity=min_opacity, min_dimension=min_dimension).is_visible


def filter_visible(
    elements: Iterable[T],
    *,
    min_opacity: float = 0.001,
    min_dimension: float = 0.0,
) -> List[T]:
    """Filter an iterable of elements, returning only rendered visible items."""
    if min_opacity == 0.001 and min_dimension == 0.0:
        return _DEFAULT_FILTER.filter(elements)
    return VisibilityFilter(min_opacity=min_opacity, min_dimension=min_dimension).filter(elements)


filter_visible_elements = filter_visible
