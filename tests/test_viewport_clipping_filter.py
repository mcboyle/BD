"""Row 960: VIEWPORT-CLIPPING-GEOMETRY-AND-OFF-CANVAS-ELEMENT-FILTER

Tests for bulk_downloader/viewport_clipping_filter.py.
Verifies:
(1) Detection of clipped and off-screen geometry coordinates
(2) Viewport intersection validation
(3) Zero false culls on visible responsive content
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.viewport_clipping_filter import (
        ViewportBounds,
        ViewportClippingFilter,
        filter_off_canvas,
        is_off_canvas,
    )
except ImportError:
    ViewportBounds = None
    ViewportClippingFilter = None
    filter_off_canvas = None
    is_off_canvas = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_off_canvas_nodes_pass_through():
    """Behavioral RED: without the filter, off-canvas nodes (left:-9999px,
    clip-path hidden) pass through to downstream parsing."""
    nodes = [
        {"tag": "nav", "style": {"left": "-9999px"}, "rect": {"x": -9999, "y": 0, "width": 200, "height": 50}},
        {"tag": "div", "style": {}, "rect": {"x": 100, "y": 100, "width": 300, "height": 200}},
        {"tag": "span", "style": {"clip-path": "inset(100%)"}, "rect": {"x": 50, "y": 50, "width": 80, "height": 20}},
    ]
    assert len(nodes) == 3

    if filter_off_canvas is None:
        kept = nodes
        assert len(kept) == 1, (
            f"BEHAVIORAL RED: off-canvas nodes pass through; "
            f"{len(kept)} of 3 kept instead of 1 (assert 3 == 1)"
        )

    viewport = ViewportBounds(width=1920, height=1080)
    result = filter_off_canvas(nodes, viewport)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# (1) Detection of clipped and off-screen geometry
# ---------------------------------------------------------------------------

def test_detect_left_offscreen():
    """Element at left:-9999px is off-canvas."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "nav", "rect": {"x": -9999, "y": 0, "width": 200, "height": 50}}
    assert is_off_canvas(node, viewport) is True


def test_detect_top_offscreen():
    """Element above viewport is off-canvas."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "rect": {"x": 100, "y": -5000, "width": 100, "height": 50}}
    assert is_off_canvas(node, viewport) is True


def test_detect_right_offscreen():
    """Element beyond right edge is off-canvas."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "rect": {"x": 5000, "y": 100, "width": 100, "height": 50}}
    assert is_off_canvas(node, viewport) is True


def test_detect_bottom_offscreen():
    """Element below viewport is off-canvas."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "rect": {"x": 100, "y": 5000, "width": 100, "height": 50}}
    assert is_off_canvas(node, viewport) is True


def test_detect_clip_path_inset_100():
    """clip-path: inset(100%) fully clips the element."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "span", "style": {"clip-path": "inset(100%)"}, "rect": {"x": 50, "y": 50, "width": 80, "height": 20}}
    assert is_off_canvas(node, viewport) is True


def test_detect_clip_rect_zero():
    """clip: rect(0,0,0,0) fully clips the element."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "style": {"clip": "rect(0, 0, 0, 0)"}, "rect": {"x": 100, "y": 100, "width": 50, "height": 50}}
    assert is_off_canvas(node, viewport) is True


# ---------------------------------------------------------------------------
# (2) Viewport intersection validation
# ---------------------------------------------------------------------------

def test_partially_visible_kept():
    """Element partially in viewport is NOT off-canvas."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "rect": {"x": -50, "y": 100, "width": 200, "height": 100}}
    assert is_off_canvas(node, viewport) is False


def test_fully_visible_kept():
    """Element fully inside viewport is NOT off-canvas."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "rect": {"x": 100, "y": 100, "width": 300, "height": 200}}
    assert is_off_canvas(node, viewport) is False


def test_edge_touching_kept():
    """Element at viewport edge (x=0) is visible."""
    assert is_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    node = {"tag": "div", "rect": {"x": 0, "y": 0, "width": 100, "height": 50}}
    assert is_off_canvas(node, viewport) is False


# ---------------------------------------------------------------------------
# (3) Zero false culls on visible responsive content
# ---------------------------------------------------------------------------

def test_filter_preserves_all_visible():
    """filter_off_canvas keeps all visible nodes."""
    assert filter_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    nodes = [
        {"tag": "a", "href": f"/page{i}", "rect": {"x": 50 + i * 100, "y": 50, "width": 80, "height": 30}}
        for i in range(15)
    ]
    result = filter_off_canvas(nodes, viewport)
    assert len(result) == 15


def test_filter_mixed_list():
    """filter_off_canvas removes only off-canvas nodes from mixed list."""
    assert filter_off_canvas is not None
    viewport = ViewportBounds(width=1920, height=1080)
    nodes = [
        {"tag": "a", "href": "/vis", "rect": {"x": 100, "y": 100, "width": 200, "height": 40}},
        {"tag": "nav", "rect": {"x": -9999, "y": 0, "width": 200, "height": 50}},
        {"tag": "p", "rect": {"x": 200, "y": 300, "width": 400, "height": 60}},
        {"tag": "div", "style": {"clip-path": "inset(100%)"}, "rect": {"x": 50, "y": 50, "width": 80, "height": 20}},
    ]
    result = filter_off_canvas(nodes, viewport)
    assert len(result) == 2
    assert result[0]["href"] == "/vis"
    assert result[1]["tag"] == "p"


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def test_negative_control_filter_exact_counts():
    """Negative control: prove filtering produces both kept and removed
    outcomes with exact counts and nonzero fixture shape."""
    assert is_off_canvas is not None
    assert filter_off_canvas is not None

    viewport = ViewportBounds(width=1920, height=1080)

    off_canvas = [
        {"tag": "nav", "rect": {"x": -9999, "y": 0, "width": 200, "height": 50}},
        {"tag": "div", "rect": {"x": 100, "y": -5000, "width": 100, "height": 50}},
        {"tag": "span", "rect": {"x": 5000, "y": 100, "width": 100, "height": 50}},
        {"tag": "div", "rect": {"x": 100, "y": 5000, "width": 100, "height": 50}},
        {"tag": "div", "style": {"clip-path": "inset(100%)"}, "rect": {"x": 50, "y": 50, "width": 80, "height": 20}},
    ]
    visible = [
        {"tag": "a", "href": "/ok1", "rect": {"x": 100, "y": 100, "width": 200, "height": 40}},
        {"tag": "p", "rect": {"x": 200, "y": 300, "width": 400, "height": 60}},
        {"tag": "div", "rect": {"x": 0, "y": 0, "width": 100, "height": 50}},
    ]

    assert len(off_canvas) == 5
    assert len(visible) == 3

    culled_count = 0
    for node in off_canvas:
        assert is_off_canvas(node, viewport) is True
        culled_count += 1
    assert culled_count == 5

    kept_count = 0
    for node in visible:
        assert is_off_canvas(node, viewport) is False
        kept_count += 1
    assert kept_count == 3
