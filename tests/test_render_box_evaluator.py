"""Row 959: CSS-RENDER-TREE-LAYOUT-BOX-CULLING-AND-VISIBILITY-EVALUATOR

Tests for bulk_downloader/render_box_evaluator.py.
Verifies:
(1) Exclusion of non-rendered CSS boxes
(2) Preservation of visible layout nodes
(3) Sub-5ms processing per 100 elements
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.render_box_evaluator import (
        BoxCullResult,
        RenderBoxEvaluator,
        cull_non_rendered,
        evaluate_render_box,
    )
except ImportError:
    BoxCullResult = None
    RenderBoxEvaluator = None
    cull_non_rendered = None
    evaluate_render_box = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_non_rendered_nodes_distort_extraction():
    """Behavioral RED: without the evaluator, non-rendered nodes
    (display:none, zero-dimension rects) pass through to semantic extraction."""
    tree_nodes = [
        {"tag": "div", "style": {"display": "none"}, "rect": {"width": 100, "height": 50}},
        {"tag": "div", "style": {"display": "block"}, "rect": {"width": 200, "height": 100}},
        {"tag": "span", "style": {"display": "block"}, "rect": {"width": 0, "height": 0}},
    ]
    assert len(tree_nodes) == 3

    if cull_non_rendered is None:
        kept = tree_nodes
        assert len(kept) == 1, (
            f"BEHAVIORAL RED: non-rendered nodes pass through; "
            f"{len(kept)} of 3 kept instead of 1 (assert 3 == 1)"
        )

    result = cull_non_rendered(tree_nodes)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# (1) Exclusion of non-rendered CSS boxes
# ---------------------------------------------------------------------------

def test_exclude_display_none():
    """display:none nodes are culled."""
    assert evaluate_render_box is not None
    node = {"tag": "div", "style": {"display": "none"}, "rect": {"width": 100, "height": 50}}
    r = evaluate_render_box(node)
    assert r.is_rendered is False
    assert r.reason == "display_none"


def test_exclude_visibility_hidden():
    """visibility:hidden nodes are culled."""
    assert evaluate_render_box is not None
    node = {"tag": "p", "style": {"visibility": "hidden"}, "rect": {"width": 80, "height": 20}}
    r = evaluate_render_box(node)
    assert r.is_rendered is False
    assert r.reason == "visibility_hidden"


def test_exclude_zero_width():
    """Zero-width bounding box is culled."""
    assert evaluate_render_box is not None
    node = {"tag": "div", "style": {"display": "block"}, "rect": {"width": 0, "height": 50}}
    r = evaluate_render_box(node)
    assert r.is_rendered is False
    assert r.reason == "zero_dimension"


def test_exclude_zero_height():
    """Zero-height bounding box is culled."""
    assert evaluate_render_box is not None
    node = {"tag": "div", "style": {"display": "block"}, "rect": {"width": 100, "height": 0}}
    r = evaluate_render_box(node)
    assert r.is_rendered is False
    assert r.reason == "zero_dimension"


def test_exclude_negative_dimensions():
    """Negative dimensions are culled."""
    assert evaluate_render_box is not None
    node = {"tag": "div", "style": {"display": "block"}, "rect": {"width": -1, "height": 50}}
    r = evaluate_render_box(node)
    assert r.is_rendered is False
    assert r.reason == "zero_dimension"


def test_exclude_opacity_zero():
    """opacity:0 nodes are culled."""
    assert evaluate_render_box is not None
    node = {"tag": "div", "style": {"opacity": "0"}, "rect": {"width": 50, "height": 50}}
    r = evaluate_render_box(node)
    assert r.is_rendered is False
    assert r.reason == "zero_opacity"


# ---------------------------------------------------------------------------
# (2) Preservation of visible layout nodes
# ---------------------------------------------------------------------------

def test_preserve_visible_block():
    """Normal visible block elements are preserved."""
    assert evaluate_render_box is not None
    node = {"tag": "div", "style": {"display": "block", "visibility": "visible"}, "rect": {"width": 200, "height": 100}}
    r = evaluate_render_box(node)
    assert r.is_rendered is True
    assert r.reason == "rendered"


def test_preserve_inline_element():
    """Inline elements with positive dimensions are preserved."""
    assert evaluate_render_box is not None
    node = {"tag": "span", "style": {"display": "inline"}, "rect": {"width": 80, "height": 16}}
    r = evaluate_render_box(node)
    assert r.is_rendered is True


def test_cull_preserves_all_visible():
    """cull_non_rendered keeps all visible nodes."""
    assert cull_non_rendered is not None
    nodes = [
        {"tag": "a", "href": f"/page{i}", "style": {"display": "block"}, "rect": {"width": 100, "height": 25}}
        for i in range(20)
    ]
    result = cull_non_rendered(nodes)
    assert len(result) == 20


def test_cull_mixed_list():
    """cull_non_rendered removes only non-rendered nodes from mixed list."""
    assert cull_non_rendered is not None
    nodes = [
        {"tag": "a", "href": "/visible", "style": {"display": "block"}, "rect": {"width": 100, "height": 25}},
        {"tag": "div", "style": {"display": "none"}, "rect": {"width": 100, "height": 25}},
        {"tag": "p", "style": {"display": "block"}, "rect": {"width": 200, "height": 40}},
        {"tag": "span", "style": {"visibility": "hidden"}, "rect": {"width": 50, "height": 12}},
    ]
    result = cull_non_rendered(nodes)
    assert len(result) == 2
    assert result[0]["href"] == "/visible"
    assert result[1]["tag"] == "p"


# ---------------------------------------------------------------------------
# (3) Sub-5ms processing per 100 elements
# ---------------------------------------------------------------------------

def test_sub_5ms_per_100_elements():
    """Processing 100 elements takes less than 5ms on average."""
    assert cull_non_rendered is not None
    batch = []
    for i in range(50):
        batch.append({"tag": "div", "style": {"display": "block"}, "rect": {"width": 100, "height": 30}})
        batch.append({"tag": "div", "style": {"display": "none"}, "rect": {"width": 0, "height": 0}})
    assert len(batch) == 100

    _ = cull_non_rendered(batch)  # warmup

    iters = 50
    t0 = time.perf_counter()
    for _ in range(iters):
        res = cull_non_rendered(batch)
    t1 = time.perf_counter()
    avg_ms = ((t1 - t0) / iters) * 1000.0
    assert len(res) == 50
    assert avg_ms < 5.0, f"Expected < 5.0ms per 100 elements, took {avg_ms:.4f}ms"


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def test_negative_control_cull_exact_counts():
    """Negative control: prove culling produces both rendered and non-rendered
    outcomes with exact counts and nonzero fixture shape."""
    assert evaluate_render_box is not None
    assert cull_non_rendered is not None

    non_rendered = [
        {"tag": "div", "style": {"display": "none"}, "rect": {"width": 50, "height": 20}},
        {"tag": "p", "style": {"visibility": "hidden"}, "rect": {"width": 50, "height": 20}},
        {"tag": "span", "style": {"opacity": "0"}, "rect": {"width": 50, "height": 20}},
        {"tag": "div", "style": {"display": "block"}, "rect": {"width": 0, "height": 0}},
        {"tag": "div", "style": {"display": "block"}, "rect": {"width": -5, "height": 20}},
    ]
    rendered = [
        {"tag": "a", "href": "/ok1", "style": {"display": "block"}, "rect": {"width": 100, "height": 25}},
        {"tag": "a", "href": "/ok2", "style": {"display": "inline"}, "rect": {"width": 80, "height": 16}},
        {"tag": "p", "style": {"display": "block", "opacity": "0.5"}, "rect": {"width": 200, "height": 40}},
    ]

    assert len(non_rendered) == 5
    assert len(rendered) == 3

    culled_count = 0
    for node in non_rendered:
        r = evaluate_render_box(node)
        assert r.is_rendered is False
        culled_count += 1
    assert culled_count == 5

    kept_count = 0
    for node in rendered:
        r = evaluate_render_box(node)
        assert r.is_rendered is True
        kept_count += 1
    assert kept_count == 3
