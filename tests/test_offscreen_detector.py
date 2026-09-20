"""tests/test_offscreen_detector.py - Row 942: Off-screen coordinate and clip-path geometry anomaly detector.

Verifies:
(1) Detection of off-screen positioning (e.g. left: -9999px) and clipped styling (e.g. clip-path: inset(50%))
(2) Viewport boundary enforcement (pruning elements positioned outside valid bounds)
(3) Zero false positives on responsive layouts (preserving valid content, including below-the-fold elements)
(4) Negative control and exact-count assertions proving detector selectivity
"""
from __future__ import annotations

import importlib
from typing import Any

try:
    offscreen_detector = importlib.import_module("bulk_downloader.offscreen_detector")
except ImportError:
    offscreen_detector = None

BD_GATE_SCOPE = "module"


def test_offscreen_detector_module_implemented():
    """Verify that offscreen_detector is importable and exposes the expected API."""
    assert offscreen_detector is not None, (
        "bulk_downloader.offscreen_detector module not implemented; "
        "geometry anomaly validator must be provided"
    )
    for expected_attr in (
        "detect_geometry_anomaly",
        "is_offscreen_or_clipped",
        "is_clip_path_anomaly",
        "is_position_anomaly",
        "prune_offscreen_links",
    ):
        assert hasattr(offscreen_detector, expected_attr), (
            f"offscreen_detector missing required API: {expected_attr}"
        )


def test_detect_offscreen_positioning():
    """Verify detection of extreme coordinates and off-screen displacement (Acceptance 1)."""
    assert offscreen_detector is not None, "offscreen_detector not implemented"

    # Extreme negative X (classic left: -9999px displacement)
    box_left_offscreen = {"x": -9999.0, "y": 100.0, "width": 120.0, "height": 30.0}
    res_left = offscreen_detector.detect_geometry_anomaly(box_left_offscreen)
    assert res_left.is_anomaly is True
    assert res_left.anomaly_type == "offscreen_position"

    # Extreme negative Y (top: -9999px displacement)
    box_top_offscreen = {"x": 100.0, "y": -9999.0, "width": 120.0, "height": 30.0}
    res_top = offscreen_detector.detect_geometry_anomaly(box_top_offscreen)
    assert res_top.is_anomaly is True
    assert res_top.anomaly_type == "offscreen_position"

    # Fully off-screen to the right of the viewport
    box_right_offscreen = {"x": 2500.0, "y": 100.0, "width": 200.0, "height": 50.0}
    res_right = offscreen_detector.detect_geometry_anomaly(
        box_right_offscreen, viewport=(1280.0, 800.0)
    )
    assert res_right.is_anomaly is True

    # Zero-dimension collapsed elements
    box_zero = {"x": 50.0, "y": 50.0, "width": 0.0, "height": 0.0}
    res_zero = offscreen_detector.detect_geometry_anomaly(box_zero)
    assert res_zero.is_anomaly is True
    assert res_zero.anomaly_type == "zero_area"


def test_detect_clipped_styling():
    """Verify detection of CSS clip-path and clip anomaly patterns (Acceptance 1)."""
    assert offscreen_detector is not None, "offscreen_detector not implemented"

    normal_box = {"x": 100.0, "y": 100.0, "width": 200.0, "height": 50.0}

    # clip-path: inset(50%) or inset(100%) collapsed geometries
    styles_inset50 = {"clip-path": "inset(50%)"}
    assert offscreen_detector.is_clip_path_anomaly(styles_inset50["clip-path"]) is True
    res_inset = offscreen_detector.detect_geometry_anomaly(normal_box, styles=styles_inset50)
    assert res_inset.is_anomaly is True
    assert res_inset.anomaly_type == "clipped_styling"

    styles_inset_full = {"clip-path": "inset(100% 0 0 0)"}
    assert offscreen_detector.is_clip_path_anomaly(styles_inset_full["clip-path"]) is True

    # clip: rect(0, 0, 0, 0) / rect(0 0 0 0) classic screen-reader hide pattern
    styles_clip_rect = {"clip": "rect(0, 0, 0, 0)"}
    assert offscreen_detector.is_clip_path_anomaly(None, clip=styles_clip_rect["clip"]) is True
    res_rect = offscreen_detector.detect_geometry_anomaly(normal_box, styles=styles_clip_rect)
    assert res_rect.is_anomaly is True
    assert res_rect.anomaly_type == "clipped_styling"

    # collapsed polygon
    styles_poly = {"clip-path": "polygon(0 0, 0 0, 0 0)"}
    assert offscreen_detector.is_clip_path_anomaly(styles_poly["clip-path"]) is True

    # hidden display / visibility styles
    styles_none = {"display": "none"}
    res_none = offscreen_detector.detect_geometry_anomaly(normal_box, styles=styles_none)
    assert res_none.is_anomaly is True

    styles_hidden = {"visibility": "hidden"}
    res_hidden = offscreen_detector.detect_geometry_anomaly(normal_box, styles=styles_hidden)
    assert res_hidden.is_anomaly is True


def test_viewport_boundary_enforcement():
    """Verify viewport boundary enforcement when evaluating candidate elements (Acceptance 2)."""
    assert offscreen_detector is not None, "offscreen_detector not implemented"

    viewport = (1920.0, 1080.0)

    # Element partially visible across right edge: e.g. x = 1800, width = 200 (visible portion = 120px)
    partial_visible = {"x": 1800.0, "y": 200.0, "width": 200.0, "height": 50.0}
    res_pv = offscreen_detector.detect_geometry_anomaly(partial_visible, viewport=viewport)
    assert res_pv.is_anomaly is False

    # Element completely outside right edge: x = 1950, width = 100
    outside_right = {"x": 1950.0, "y": 200.0, "width": 100.0, "height": 50.0}
    res_out = offscreen_detector.detect_geometry_anomaly(outside_right, viewport=viewport)
    assert res_out.is_anomaly is True

    # Element displaced above top boundary: y = -100, height = 40 (bottom = -60 < 0)
    outside_top = {"x": 200.0, "y": -100.0, "width": 100.0, "height": 40.0}
    res_top = offscreen_detector.detect_geometry_anomaly(outside_top, viewport=viewport)
    assert res_top.is_anomaly is True


def test_zero_false_positives_on_responsive_layouts():
    """Verify zero false positives across responsive viewport sizes and layouts (Acceptance 3)."""
    assert offscreen_detector is not None, "offscreen_detector not implemented"

    # Mobile viewport (375 x 667), Tablet (768 x 1024), Desktop (1440 x 900)
    responsive_viewports = [
        (375.0, 667.0),
        (768.0, 1024.0),
        (1440.0, 900.0),
    ]

    for vp_w, vp_h in responsive_viewports:
        # 1. Header nav link inside viewport
        header_link = {"x": 20.0, "y": 15.0, "width": vp_w - 40.0, "height": 40.0}
        assert offscreen_detector.is_offscreen_or_clipped(header_link, viewport=(vp_w, vp_h)) is False

        # 2. Main content cards (normal vertical flow)
        card_link = {"x": 20.0, "y": 300.0, "width": vp_w * 0.4, "height": 180.0}
        assert offscreen_detector.is_offscreen_or_clipped(card_link, viewport=(vp_w, vp_h)) is False

        # 3. Below-the-fold content: legitimate scrollable elements (e.g. y = 1500px, 3500px)
        below_fold_link = {"x": 20.0, "y": vp_h + 800.0, "width": vp_w * 0.8, "height": 120.0}
        assert offscreen_detector.is_offscreen_or_clipped(
            below_fold_link, viewport=(vp_w, vp_h), allow_below_fold=True
        ) is False

        # 4. Standard valid CSS clip-path (e.g. decorative rounded corners or subtle non-collapsing polygon)
        valid_clip_styles = {"clip-path": "polygon(0 0, 100% 0, 100% 100%, 0 100%)"}
        assert offscreen_detector.is_offscreen_or_clipped(
            header_link, styles=valid_clip_styles, viewport=(vp_w, vp_h)
        ) is False


def test_prune_offscreen_links_and_negative_control():
    """Negative control and exact-count assertion proving detector selectivity."""
    assert offscreen_detector is not None, "offscreen_detector not implemented"

    # Build fixture: 6 legitimate links + 4 anomalous links = 10 total links
    legitimate_links: list[dict[str, Any]] = [
        {"href": "/video/1", "box": {"x": 20.0, "y": 50.0, "width": 200.0, "height": 40.0}},
        {"href": "/video/2", "box": {"x": 240.0, "y": 50.0, "width": 200.0, "height": 40.0}},
        {"href": "/video/3", "box": {"x": 20.0, "y": 200.0, "width": 300.0, "height": 150.0}},
        {"href": "/video/4", "box": {"x": 340.0, "y": 200.0, "width": 300.0, "height": 150.0}},
        # Below fold link
        {"href": "/video/5", "box": {"x": 20.0, "y": 1400.0, "width": 250.0, "height": 80.0}},
        # Footer link
        {"href": "/video/6", "box": {"x": 50.0, "y": 2200.0, "width": 180.0, "height": 30.0}},
    ]
    # Prove fixture built non-zero shape
    assert len(legitimate_links) == 6, "Fixture shape check: must contain exactly 6 legitimate links"

    # Negative control: running pure legitimate corpus through pruner must preserve all 6
    retained_control = offscreen_detector.prune_offscreen_links(legitimate_links, viewport=(1280.0, 800.0))
    assert len(retained_control) == 6, (
        f"Negative control failed: expected 6 retained legitimate links, got {len(retained_control)}"
    )

    anomalous_links: list[dict[str, Any]] = [
        # Anomaly 1: left -9999px
        {"href": "/trap/1", "box": {"x": -9999.0, "y": 100.0, "width": 100.0, "height": 20.0}},
        # Anomaly 2: clip-path inset(50%)
        {
            "href": "/trap/2",
            "box": {"x": 100.0, "y": 100.0, "width": 100.0, "height": 20.0},
            "styles": {"clip-path": "inset(50%)"},
        },
        # Anomaly 3: clip rect(0, 0, 0, 0)
        {
            "href": "/trap/3",
            "box": {"x": 100.0, "y": 100.0, "width": 100.0, "height": 20.0},
            "styles": {"clip": "rect(0, 0, 0, 0)"},
        },
        # Anomaly 4: display none
        {
            "href": "/trap/4",
            "box": {"x": 100.0, "y": 100.0, "width": 100.0, "height": 20.0},
            "styles": {"display": "none"},
        },
    ]
    assert len(anomalous_links) == 4, "Fixture shape check: must contain exactly 4 anomalous links"

    combined_corpus = legitimate_links + anomalous_links
    assert len(combined_corpus) == 10, "Combined fixture must contain exactly 10 links"

    # Exact-count assertion: pruner must eliminate all 4 anomalies and keep all 6 legitimate links
    pruned_result = offscreen_detector.prune_offscreen_links(combined_corpus, viewport=(1280.0, 800.0))
    assert len(pruned_result) == 6, (
        f"Exact count failure: expected 6 pruned links, got {len(pruned_result)}"
    )
    # Verify exact retained hrefs
    retained_hrefs = [link["href"] for link in pruned_result]
    expected_hrefs = [f"/video/{i}" for i in range(1, 7)]
    assert retained_hrefs == expected_hrefs, f"Retained hrefs mismatch: {retained_hrefs}"


def test_refute_false_positives_unknown_geometry_lengths_and_scroll():
    """Correctness REFUTE E1-E3 (2026-09-20): none of these legitimate cases is an anomaly."""
    assert offscreen_detector is not None, "offscreen_detector not implemented"

    # E1: a plain extractor link list with no geometry is kept whole (unknown != hidden)
    boxless = [{"href": "/a"}, {"href": "/b", "text": "Next"}]
    assert offscreen_detector.detect_geometry_anomaly(None).is_anomaly is False
    assert [l["href"] for l in offscreen_detector.prune_offscreen_links(boxless)] == ["/a", "/b"]

    # E2: inset() lengths are not percentages; only % (or 0) values reach the 50/100 thresholds
    for cp in ("inset(60px)", "inset(10px 80px)", "inset(0 0 120px 0)", "inset(2rem)"):
        assert offscreen_detector.is_clip_path_anomaly(cp) is False, cp
    assert offscreen_detector.is_clip_path_anomaly("inset(45%)") is False
    assert offscreen_detector.is_clip_path_anomaly("inset(50%)") is True
    assert offscreen_detector.is_clip_path_anomaly("inset(60% 0 60% 0)") is True
    assert offscreen_detector.is_clip_path_anomaly("inset(10px 80%)") is True  # % sides still judged beside a length

    # E3: viewport-relative rects on a scrolled / horizontally laid-out page
    vp = (1280.0, 800.0)
    scrolled_past = {"x": 20.0, "y": -400.0, "width": 200.0, "height": 40.0}
    assert offscreen_detector.is_offscreen_or_clipped(scrolled_past, viewport=vp, scroll=(0.0, 600.0)) is False
    assert offscreen_detector.is_offscreen_or_clipped(scrolled_past, viewport=vp) is True  # control: no scroll
    carousel_slide = {"x": 1300.0, "y": 200.0, "width": 300.0, "height": 200.0}
    assert offscreen_detector.is_offscreen_or_clipped(carousel_slide, viewport=vp, document_size=(2400.0, 800.0)) is False
    assert offscreen_detector.is_offscreen_or_clipped(carousel_slide, viewport=vp) is True  # control: 1280-wide document
    # per-link scroll travels with the link through the pruner
    kept = offscreen_detector.prune_offscreen_links(
        [{"href": "/scrolled", "box": scrolled_past, "scroll": (0.0, 600.0)}], viewport=vp
    )
    assert [l["href"] for l in kept] == ["/scrolled"]
