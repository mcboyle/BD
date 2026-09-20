"""render_box_evaluator -- CSS render tree layout box culling.

Row 959 (v3.66.1585): inspect CSS visual formatting model properties
(display, visibility, opacity) and bounding box dimensions to cull
non-rendered tree elements from structural semantic extraction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class BoxCullResult:
    is_rendered: bool
    reason: str


class RenderBoxEvaluator:
    def evaluate(self, node: Any) -> BoxCullResult:
        if node is None:
            return BoxCullResult(False, "null_node")

        style = _get_style(node)
        if style:
            display = str(style.get("display", "")).strip().lower()
            if display == "none":
                return BoxCullResult(False, "display_none")

            vis = str(style.get("visibility", "")).strip().lower()
            if vis in ("hidden", "collapse"):
                return BoxCullResult(False, "visibility_hidden")

            op_raw = style.get("opacity")
            if op_raw is not None:
                try:
                    if float(op_raw) <= 0.001:
                        return BoxCullResult(False, "zero_opacity")
                except (ValueError, TypeError):
                    pass

        rect = _get_rect(node)
        if rect is not None:
            w, h = rect
            if w <= 0 or h <= 0:
                return BoxCullResult(False, "zero_dimension")

        return BoxCullResult(True, "rendered")

    def cull(self, nodes: Iterable[T]) -> List[T]:
        return [n for n in nodes if self.evaluate(n).is_rendered]


def _get_style(node: Any) -> Optional[Dict[str, Any]]:
    if isinstance(node, dict):
        s = node.get("style")
        if isinstance(s, dict):
            return s
    return None


def _get_rect(node: Any) -> Optional[Tuple[float, float]]:
    if isinstance(node, dict):
        r = node.get("rect")
        if isinstance(r, dict):
            w, h = r.get("width"), r.get("height")
            if w is not None and h is not None:
                try:
                    return float(w), float(h)
                except (ValueError, TypeError):
                    pass
    return None


_DEFAULT = RenderBoxEvaluator()


def evaluate_render_box(node: Any) -> BoxCullResult:
    return _DEFAULT.evaluate(node)


def cull_non_rendered(nodes: Iterable[T]) -> List[T]:
    return _DEFAULT.cull(nodes)
