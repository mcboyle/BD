"""bulk_downloader/offscreen_detector.py - Off-screen coordinate and clip-path geometry anomaly detector (Row 942).

Detects off-screen coordinate displacement (e.g. left: -9999px) and CSS clipping
anomalies (e.g. clip-path: inset(50%), clip: rect(0, 0, 0, 0)) to prune invalid or
deceptive off-screen links during crawling, while avoiding false positives on
responsive and vertical-scrolling layouts.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# Default viewport dimensions (width, height)
DEFAULT_VIEWPORT: tuple[float, float] = (1280.0, 800.0)

# Regular expressions for CSS clip and clip-path parsing
_RE_INSET = re.compile(
    r"inset\s*\(\s*([^\)]+)\s*\)", re.IGNORECASE
)
_RE_RECT = re.compile(
    r"rect\s*\(\s*([^,\s\)]+)(?:[\s,]+)([^,\s\)]+)(?:[\s,]+)([^,\s\)]+)(?:[\s,]+)([^,\s\)]+)\s*\)",
    re.IGNORECASE,
)
_RE_POLYGON = re.compile(
    r"polygon\s*\(\s*([^\)]+)\s*\)", re.IGNORECASE
)
_RE_CIRCLE = re.compile(
    r"circle\s*\(\s*([^\)]*)\s*\)", re.IGNORECASE
)


@dataclass(frozen=True)
class GeometryAnomalyResult:
    """Detailed evaluation result of an element's layout geometry."""

    is_anomaly: bool
    anomaly_type: str | None = None
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _parse_css_percentage(val: str) -> float | None:
    """Parse a CSS percentage ("60%") or a bare zero into 0-100; any length unit is None.

    inset() thresholds are percentages of the element's own box; a length (px/em/rem/...)
    cannot be judged without that box, so it is not collapse evidence (correctness REFUTE E2).
    """
    val = val.strip().lower()
    if val.endswith("%"):
        try:
            return float(val[:-1])
        except ValueError:
            return None
    try:
        return 0.0 if float(val) == 0.0 else None
    except ValueError:
        return None


def _parse_css_dimension(val: str) -> float | None:
    """Parse a CSS length or percentage into numeric value (0-100 for %)."""
    val = val.strip().lower()
    if not val or val == "auto":
        return None
    if val.endswith("%"):
        try:
            return float(val[:-1])
        except ValueError:
            return None
    for unit in ("px", "em", "rem", "pt", "vh", "vw"):
        if val.endswith(unit):
            try:
                return float(val[:-len(unit)])
            except ValueError:
                return None
    try:
        number = float(val)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def is_clip_path_anomaly(
    clip_path: str | None,
    clip: str | None = None,
) -> bool:
    """Determine whether CSS clip-path or clip styles collapse an element's visible area.

    Detects:
      - clip-path: inset(...) with >= 50% single margin or total opposing margins >= 100%
        (percentages only; px/em/rem lengths are not collapse evidence without the element box)
      - clip-path: polygon(...) with collapsed/zero area vertices
      - clip-path: circle(0) or circle(0px)
      - clip: rect(0, 0, 0, 0) or identical opposing boundaries (top == bottom or right == left)
    """
    if clip_path:
        cp = clip_path.strip().lower()
        if cp in ("none", "initial", "inherit", "unset"):
            pass
        else:
            # Check inset(...)
            m_inset = _RE_INSET.search(cp)
            if m_inset:
                args = [a for a in m_inset.group(1).split() if a.lower() != "round"]
                # a length (px/em/rem/...) is unknown collapse evidence: it counts as 0, the % sides still count
                vals = [v if v is not None else 0.0 for v in (_parse_css_percentage(a) for a in args)]
                if len(vals) == 1:
                    # e.g. inset(50%) or inset(100%) collapses element
                    if vals[0] >= 50.0:
                        return True
                elif len(vals) == 2:
                    # inset(vertical, horizontal)
                    v, h = vals[0], vals[1]
                    if v >= 50.0 or h >= 50.0 or (v * 2 >= 100.0) or (h * 2 >= 100.0):
                        return True
                elif len(vals) >= 4:
                    top, right, bottom, left = vals[0], vals[1], vals[2], vals[3]
                    if (top + bottom >= 100.0) or (left + right >= 100.0):
                        return True
                    if any(x >= 100.0 for x in (top, right, bottom, left)):
                        return True

            # Check polygon(...)
            m_poly = _RE_POLYGON.search(cp)
            if m_poly:
                raw_pairs = [p.strip() for p in m_poly.group(1).split(",") if p.strip()]
                # If all points are identical (e.g. 0 0, 0 0, 0 0), geometry has collapsed
                if raw_pairs and len(set(raw_pairs)) == 1:
                    return True

            # Check circle(...)
            m_circle = _RE_CIRCLE.search(cp)
            if m_circle:
                raw_arg = m_circle.group(1).strip()
                if not raw_arg or raw_arg in ("0", "0px", "0%", "0em"):
                    return True

    if clip:
        c = clip.strip().lower()
        if c not in ("auto", "none", "initial", "inherit", "unset"):
            m_rect = _RE_RECT.search(c)
            if m_rect:
                top = _parse_css_dimension(m_rect.group(1))
                right = _parse_css_dimension(m_rect.group(2))
                bottom = _parse_css_dimension(m_rect.group(3))
                left = _parse_css_dimension(m_rect.group(4))
                if None not in (top, right, bottom, left):
                    # Zero area rect: top == bottom or right == left
                    if top == bottom or right == left:
                        return True
                    # Classic rect(0, 0, 0, 0) or rect(1px, 1px, 1px, 1px)
                    if top == bottom == right == left:
                        return True
                    if (bottom - top) <= 1.0 and (right - left) <= 1.0:
                        return True

    return False


def is_position_anomaly(
    box: dict[str, float] | None,
    styles: dict[str, str] | None = None,
    viewport: tuple[float, float] = DEFAULT_VIEWPORT,
    *,
    allow_below_fold: bool = True,
    scroll: tuple[float, float] = (0.0, 0.0),
    document_size: tuple[float, float] | None = None,
) -> bool:
    """Determine whether an element's coordinates or display styles constitute an anomaly."""
    res = detect_geometry_anomaly(
        box=box,
        styles=styles,
        viewport=viewport,
        allow_below_fold=allow_below_fold,
        scroll=scroll,
        document_size=document_size,
    )
    return res.is_anomaly


def detect_geometry_anomaly(
    box: dict[str, float] | None,
    styles: dict[str, str] | None = None,
    viewport: tuple[float, float] = DEFAULT_VIEWPORT,
    *,
    allow_below_fold: bool = True,
    scroll: tuple[float, float] = (0.0, 0.0),
    document_size: tuple[float, float] | None = None,
) -> GeometryAnomalyResult:
    """Evaluate element bounding box and computed styles against viewport boundaries.

    `box` is viewport-relative (getBoundingClientRect); `scroll` = (scrollX, scrollY) of the
    page when it was measured and `document_size` = (scrollWidth, scrollHeight) if known.
    Boundary rules are applied in DOCUMENT space so a link the crawler scrolled past, or a
    carousel slide on a horizontally scrollable page, is not an anomaly (REFUTE E3).

    Validates:
      1. Collapsed bounding box (width <= 0 or height <= 0). A MISSING box is unknown
         geometry, not evidence of hiding: never an anomaly (REFUTE E1).
      2. Extreme coordinate displacement (e.g. x <= -50, y <= -50).
      3. Document boundary crossing (fully outside the right edge or above the top).
      4. Hidden CSS styling (display: none, visibility: hidden, opacity: 0).
      5. Clipped styling via clip-path or clip.

    Ensures zero false positives on responsive layouts and below-the-fold content.
    """
    styles = styles or {}

    # Check CSS visibility and display styles
    disp = styles.get("display", "").strip().lower()
    if disp == "none":
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="hidden_styling",
            reason="CSS display: none",
            details={"display": disp},
        )

    vis = styles.get("visibility", "").strip().lower()
    if vis in ("hidden", "collapse"):
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="hidden_styling",
            reason=f"CSS visibility: {vis}",
            details={"visibility": vis},
        )

    op_val = styles.get("opacity", "").strip()
    opacity = _parse_css_dimension(op_val) if op_val else None  # non-numeric opacity is unknown, not hidden
    if opacity is not None and opacity <= 0.0:
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="hidden_styling",
            reason="CSS opacity: 0",
            details={"opacity": op_val},
        )

    # Check text-indent hiding (e.g. text-indent: -9999px)
    indent = styles.get("text-indent", "").strip()
    if indent:
        parsed_indent = _parse_css_dimension(indent)
        if parsed_indent is not None and parsed_indent <= -1000.0:
            return GeometryAnomalyResult(
                is_anomaly=True,
                anomaly_type="offscreen_position",
                reason=f"Extreme negative text-indent: {indent}",
                details={"text-indent": indent},
            )

    # Check CSS clipping
    clip_path = styles.get("clip-path") or styles.get("-webkit-clip-path")
    clip = styles.get("clip")
    if is_clip_path_anomaly(clip_path=clip_path, clip=clip):
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="clipped_styling",
            reason="Collapsed CSS clip-path or clip rect",
            details={"clip-path": clip_path, "clip": clip},
        )

    # Check bounding box: no geometry is UNKNOWN, and unknown is not an anomaly (fail-open)
    if box is None:
        return GeometryAnomalyResult(
            is_anomaly=False,
            reason="No bounding box: geometry unknown, element kept",
        )

    x = float(box.get("x", box.get("left", 0.0)))
    y = float(box.get("y", box.get("top", 0.0)))
    w = float(box.get("width", 0.0))
    h = float(box.get("height", 0.0))
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(w) and math.isfinite(h)):
        return GeometryAnomalyResult(
            is_anomaly=False,
            reason="Non-finite bounding box: geometry unknown, element kept",
            details={"box": box},
        )

    vp_w, vp_h = float(viewport[0]), float(viewport[1])
    scroll_x, scroll_y = float(scroll[0]), float(scroll[1])
    # Document-space position and the reachable document extent (at least the viewport)
    doc_x, doc_y = x + scroll_x, y + scroll_y
    doc_w = max(vp_w, float(document_size[0])) if document_size else vp_w

    # Zero or negative dimensions
    if w <= 0.0 or h <= 0.0:
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="zero_area",
            reason=f"Element has zero or negative area ({w}x{h})",
            details={"box": box},
        )

    # Extreme negative DOCUMENT coordinates (e.g. left: -9999px or top: -9999px)
    if (doc_x + w <= 0.0 and doc_x <= -50.0) or (doc_y + h <= 0.0 and doc_y <= -50.0):
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="offscreen_position",
            reason=f"Extreme off-screen coordinates: x={doc_x}, y={doc_y}",
            details={"box": box, "scroll": scroll},
        )

    # Completely displaced past the right edge of the reachable document
    if doc_x >= doc_w:
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="offscreen_position",
            reason=f"Element horizontally outside document: x={doc_x} >= {doc_w}",
            details={"box": box, "viewport": viewport, "scroll": scroll, "document_size": document_size},
        )

    # Fully above the top of the document (a scrolled-past link has doc_y + h > 0)
    if doc_y + h <= 0.0:
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="offscreen_position",
            reason=f"Element above top of document: y+h={doc_y + h} <= 0",
            details={"box": box, "viewport": viewport, "scroll": scroll},
        )

    # Below-the-fold enforcement: if below-fold is strictly prohibited
    if not allow_below_fold and doc_y >= vp_h:
        return GeometryAnomalyResult(
            is_anomaly=True,
            anomaly_type="offscreen_position",
            reason=f"Element below viewport fold: y={doc_y} >= {vp_h}",
            details={"box": box, "viewport": viewport},
        )

    return GeometryAnomalyResult(
        is_anomaly=False,
        reason="Geometry is on-screen and within layout boundaries",
        details={"box": box, "viewport": viewport},
    )


def is_offscreen_or_clipped(
    box: dict[str, float] | None,
    styles: dict[str, str] | None = None,
    viewport: tuple[float, float] = DEFAULT_VIEWPORT,
    *,
    allow_below_fold: bool = True,
    scroll: tuple[float, float] = (0.0, 0.0),
    document_size: tuple[float, float] | None = None,
) -> bool:
    """Convenience boolean check for layout geometry anomalies."""
    return detect_geometry_anomaly(
        box=box,
        styles=styles,
        viewport=viewport,
        allow_below_fold=allow_below_fold,
        scroll=scroll,
        document_size=document_size,
    ).is_anomaly


def prune_offscreen_links(
    links: list[dict[str, Any]],
    viewport: tuple[float, float] = DEFAULT_VIEWPORT,
    *,
    allow_below_fold: bool = True,
    scroll: tuple[float, float] = (0.0, 0.0),
    document_size: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """Prune off-screen and clipped layout anomalies from a candidate link list.

    Each link entry in `links` may provide:
      - 'box' or 'rect': dict with x/left, y/top, width, height (viewport-relative)
      - 'styles': optional dict of computed CSS properties
      - 'scroll': optional (scrollX, scrollY) at measurement time (overrides `scroll`)
    A link with no box/rect has unknown geometry and is KEPT.
    """
    valid_links: list[dict[str, Any]] = []
    for link in links:
        box = link.get("box") or link.get("rect")
        styles = link.get("styles")
        link_scroll = link.get("scroll") or scroll
        if not is_offscreen_or_clipped(
            box=box,
            styles=styles,
            viewport=viewport,
            allow_below_fold=allow_below_fold,
            scroll=link_scroll,
            document_size=document_size,
        ):
            valid_links.append(link)
    return valid_links
