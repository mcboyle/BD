"""Row 941: CSS-VISIBILITY-AND-ZERO-BOUNDING-BOX-ELEMENT-FILTER

Tests for computed style visibility filter in bulk_downloader/visibility_filter.py.
Verifies:
(1) 100% exclusion of non-rendered elements (display:none, visibility:hidden, opacity:0, zero bounding box)
(2) Retention of visible links and form elements
(3) Sub-5ms evaluation per 100 elements
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.visibility_filter import (
        VisibilityFilter,
        VisibilityResult,
        evaluate_visibility,
        filter_visible,
        filter_visible_elements,
        is_visible,
        VISIBILITY_FILTER_JS,
    )
except ImportError:
    VisibilityFilter = None
    VisibilityResult = None
    evaluate_visibility = None
    filter_visible = None
    filter_visible_elements = None
    is_visible = None
    VISIBILITY_FILTER_JS = None

BD_GATE_SCOPE = "module"


def test_behavioral_red_unfiltered_elements_admit_invisible_nodes():
    """Behavioral RED test: demonstrate that without the visibility filter,
    invisible nodes (display:none, zero bounding box, opacity:0) contaminate
    collected elements instead of being 100% excluded."""
    fixture_elements = [
        {"tag": "a", "href": "/hidden1", "style": {"display": "none"}, "rect": {"width": 100, "height": 20}},
        {"tag": "a", "href": "/hidden2", "style": {"visibility": "hidden"}, "rect": {"width": 100, "height": 20}},
        {"tag": "a", "href": "/hidden3", "style": {"opacity": "0"}, "rect": {"width": 100, "height": 20}},
        {"tag": "a", "href": "/zero-dim", "style": {"display": "block"}, "rect": {"width": 0, "height": 0}},
        {"tag": "a", "href": "/visible1", "style": {"display": "block", "visibility": "visible", "opacity": "1.0"}, "rect": {"width": 120, "height": 25}},
    ]
    # Prove fixture built nonzero shape: 5 elements total, 4 non-rendered, 1 visible
    assert len(fixture_elements) == 5, "Fixture must have exactly 5 elements"

    if filter_visible is None:
        # Unmodified baseline: naive collector retains all 5 elements
        naive_collected = fixture_elements
        assert len(naive_collected) == 1, (
            f"BEHAVIORAL RED: naive collector retained {len(naive_collected)} elements including "
            f"non-rendered nodes; expected exactly 1 visible element (assert {len(naive_collected)} == 1)"
        )

    filtered = filter_visible(fixture_elements)
    assert len(filtered) == 1
    assert filtered[0]["href"] == "/visible1"


def test_negative_control_fixture_shape_and_rejection():
    """Negative control: prove that fixture builds the exact non-rendered conditions
    and that evaluate_visibility rejects 100% of them with exact expected reasons."""
    assert evaluate_visibility is not None, "evaluate_visibility required"

    negative_fixture = [
        ({"style": {"display": "none"}, "rect": {"width": 50, "height": 20}}, "display_none"),
        ({"style": {"display": "NONE"}, "rect": {"width": 50, "height": 20}}, "display_none"),
        ({"style": {"visibility": "hidden"}, "rect": {"width": 50, "height": 20}}, "visibility_hidden"),
        ({"style": {"visibility": "collapse"}, "rect": {"width": 50, "height": 20}}, "visibility_hidden"),
        ({"style": {"opacity": "0"}, "rect": {"width": 50, "height": 20}}, "zero_opacity"),
        ({"style": {"opacity": 0.0}, "rect": {"width": 50, "height": 20}}, "zero_opacity"),
        ({"style": {"display": "block"}, "rect": {"width": 0, "height": 30}}, "zero_bounding_box"),
        ({"style": {"display": "block"}, "rect": {"width": 40, "height": 0}}, "zero_bounding_box"),
        ({"style": {"display": "block"}, "rect": {"width": -1, "height": 30}}, "zero_bounding_box"),
    ]

    # Prove nonzero shape before asserting rejection
    assert len(negative_fixture) == 9, "Negative fixture must contain exactly 9 test cases"

    rejected_count = 0
    for element, expected_reason in negative_fixture:
        res = evaluate_visibility(element)
        assert res.is_visible is False, f"Element {element} must not be visible"
        assert res.reason == expected_reason, f"Expected reason {expected_reason}, got {res.reason}"
        rejected_count += 1

    assert rejected_count == 9, "All 9 negative control cases must be rejected"


def test_100_percent_exclusion_of_non_rendered_elements():
    """Acceptance (1): 100% exclusion of non-rendered elements."""
    assert filter_visible is not None, "filter_visible required"

    non_rendered_elements = [
        {"tag": "a", "href": f"/hidden-{i}", "style": {"display": "none"}, "rect": {"width": 100, "height": 20}}
        for i in range(10)
    ] + [
        {"tag": "input", "name": f"invis-{i}", "style": {"visibility": "hidden"}, "rect": {"width": 80, "height": 24}}
        for i in range(10)
    ] + [
        {"tag": "button", "id": f"btn-{i}", "style": {"opacity": "0.0"}, "rect": {"width": 60, "height": 30}}
        for i in range(10)
    ] + [
        {"tag": "form", "action": f"/submit-{i}", "style": {"display": "block"}, "rect": {"width": 0, "height": 0}}
        for i in range(10)
    ]

    assert len(non_rendered_elements) == 40
    result = filter_visible(non_rendered_elements)
    assert len(result) == 0, f"Expected 0 elements after filter, got {len(result)} (100% exclusion failed)"


def test_retention_of_visible_links():
    """Acceptance (2): retention of visible links."""
    assert filter_visible is not None, "filter_visible required"

    visible_links = [
        {
            "tag": "a",
            "href": f"https://example.com/page-{i}",
            "text": f"Link {i}",
            "style": {"display": "inline-block", "visibility": "visible", "opacity": "1.0"},
            "rect": {"x": 10 * i, "y": 20 * i, "width": 100, "height": 25},
        }
        for i in range(25)
    ]

    assert len(visible_links) == 25
    result = filter_visible(visible_links)
    assert len(result) == 25, f"Expected all 25 visible links retained, got {len(result)}"
    for orig, retained in zip(visible_links, result):
        assert orig["href"] == retained["href"]
        assert orig["text"] == retained["text"]


def test_sub_5ms_evaluation_per_100_elements():
    """Acceptance (3): sub-5ms evaluation per 100 elements."""
    assert filter_visible is not None, "filter_visible required"

    # Construct 100 elements (50 visible, 50 invisible)
    test_batch = []
    for i in range(50):
        test_batch.append({
            "tag": "a",
            "href": f"/link-{i}",
            "style": {"display": "block", "visibility": "visible", "opacity": "1"},
            "rect": {"width": 120, "height": 30},
        })
        test_batch.append({
            "tag": "a",
            "href": f"/hidden-{i}",
            "style": {"display": "none"},
            "rect": {"width": 0, "height": 0},
        })

    assert len(test_batch) == 100

    # Warmup
    _ = filter_visible(test_batch)

    # Benchmark over 50 iterations to get reliable timing
    iterations = 50
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = filter_visible(test_batch)
    t1 = time.perf_counter()

    avg_duration_ms = ((t1 - t0) / iterations) * 1000.0
    assert len(res) == 50
    assert avg_duration_ms < 5.0, f"Expected < 5.0ms per 100 elements, took {avg_duration_ms:.4f}ms"


def test_visibility_filter_class_and_js_snippet():
    """Verify VisibilityFilter class methods and client-side JavaScript evaluation snippet."""
    assert VisibilityFilter is not None, "VisibilityFilter required"
    assert VISIBILITY_FILTER_JS is not None, "VISIBILITY_FILTER_JS required"

    filter_instance = VisibilityFilter(min_opacity=0.01, min_dimension=0.1)
    element_ok = {"style": {"display": "block", "visibility": "visible", "opacity": 0.5}, "rect": {"width": 10, "height": 10}}
    element_too_transparent = {"style": {"opacity": 0.005}, "rect": {"width": 10, "height": 10}}

    assert filter_instance.is_visible(element_ok) is True
    assert filter_instance.is_visible(element_too_transparent) is False

    # Check JS snippet presence and keywords
    assert "getComputedStyle" in VISIBILITY_FILTER_JS
    assert "getBoundingClientRect" in VISIBILITY_FILTER_JS
    assert "display" in VISIBILITY_FILTER_JS
    assert "visibility" in VISIBILITY_FILTER_JS
