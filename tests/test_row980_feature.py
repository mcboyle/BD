"""Row 980: Adaptive Input Coordinate Variance for Form Controls.

Provides adaptive geometric coordinate variance, control-specific safe interactive
zones, truncated Gaussian dispersion, trajectory micro-jitter modeling, dynamic
feedback-driven profile adaptation, and three-state disposition (O1224) for enterprise
form controls.

Wired into real production callers:
- bulk_downloader.cloak.cloaked_mouse_click (with adaptive_variance=True, control_type)
- bulk_downloader.cloak.calculate_adaptive_click_target
- bulk_downloader.login_impl._common._human_move_to
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, List

import pytest

BD_GATE_SCOPE = "module"

try:
    import bulk_downloader.adaptive_input_variance as _adaptive_mod
    from bulk_downloader.adaptive_input_variance import (
        AdaptiveInputVarianceEngine,
        BoundingBox,
        CoordinateVarianceProfile,
        FormControlType,
        VarianceSample,
        VarianceState,
        calculate_adaptive_coordinate,
        get_adaptive_variance_engine,
        get_form_control_coordinate,
    )
except ImportError:
    _adaptive_mod = None
    AdaptiveInputVarianceEngine = None
    BoundingBox = None
    CoordinateVarianceProfile = None
    FormControlType = None
    VarianceSample = None
    VarianceState = None
    calculate_adaptive_coordinate = None
    get_adaptive_variance_engine = None
    get_form_control_coordinate = None

import bulk_downloader.cloak as cloak


class _MockLocator:
    def __init__(self, box: dict[str, float] | None) -> None:
        self._box = box

    def bounding_box(self) -> dict[str, float] | None:
        return self._box


class _MockMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float]] = []
        self.downs: list[str] = []
        self.ups: list[str] = []

    def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))

    def down(self, button: str = "left") -> None:
        self.downs.append(button)

    def up(self, button: str = "left") -> None:
        self.ups.append(button)


class _MockPage:
    def __init__(self, locators: dict[str, dict[str, float]]) -> None:
        self._locators = locators
        self.mouse = _MockMouse()

    def locator(self, selector: str) -> Any:
        class _LocSeq:
            def __init__(self, box: dict[str, float] | None) -> None:
                self.first = _MockLocator(box)
        return _LocSeq(self._locators.get(selector))


def test_positive_control_cloak_mouse_baseline():
    """Positive control: bulk_downloader.cloak baseline mouse click exists and is callable."""
    assert hasattr(cloak, "cloaked_mouse_click")
    assert callable(cloak.cloaked_mouse_click)
    page = _MockPage({"#btn": {"x": 100.0, "y": 200.0, "width": 80.0, "height": 40.0}})
    res = cloak.cloaked_mouse_click(page=page, selector="#btn", sleep_fn=None)
    assert res.get("success") is True
    assert "target" in res


def test_behavioral_red_form_control_adaptive_variance_through_caller():
    """Behavioral RED: cloak.calculate_adaptive_click_target capability exists in cloak.

    On baseline, cloak lacks calculate_adaptive_click_target and does not compute
    adaptive Gaussian variance for form controls.
    """
    assert hasattr(cloak, "calculate_adaptive_click_target"), (
        "BEHAVIORAL RED: bulk_downloader.cloak missing 'calculate_adaptive_click_target' method"
    )

    box = {"x": 100.0, "y": 200.0, "width": 80.0, "height": 30.0}
    targets: List[tuple[float, float]] = []
    for _ in range(30):
        tx, ty, state = cloak.calculate_adaptive_click_target(box, control_type="button")
        assert state == "adaptive"
        assert 100.0 <= tx < 180.0
        assert 200.0 <= ty < 230.0
        targets.append((tx, ty))

    xs = [t[0] for t in targets]
    ys = [t[1] for t in targets]

    # Must have non-zero variance (not all static center clicks)
    std_x = statistics.stdev(xs)
    std_y = statistics.stdev(ys)
    assert std_x > 0.5, f"Expected non-zero horizontal variance, got {std_x}"
    assert std_y > 0.2, f"Expected non-zero vertical variance, got {std_y}"


def test_three_state_variance_disposition_o1224():
    """Verify three-state disposition (adaptive, fallback, unavailable) per O1224.

    Fail-open results must be three-state: zero/missing boxes report UNAVAILABLE,
    not fake ADAPTIVE success.
    """
    if not hasattr(cloak, "calculate_adaptive_click_target"):
        pytest.skip("calculate_adaptive_click_target not yet available")

    # 1. Healthy box -> adaptive
    healthy = {"x": 50.0, "y": 50.0, "width": 100.0, "height": 40.0}
    _, _, state1 = cloak.calculate_adaptive_click_target(healthy, control_type="text_input")
    assert state1 == "adaptive"

    # 2. Invalid zero-dimension box -> unavailable (never fail-open to adaptive)
    zero_box = {"x": 50.0, "y": 50.0, "width": 0.0, "height": 40.0}
    _, _, state2 = cloak.calculate_adaptive_click_target(zero_box, control_type="button")
    assert state2 == "unavailable"

    # 3. None / detached element -> unavailable
    _, _, state3 = cloak.calculate_adaptive_click_target(None, control_type="button")
    assert state3 == "unavailable"


def test_cloaked_mouse_click_adaptive_variance_integration():
    """Verify cloaked_mouse_click executes adaptive variance through mock page."""
    if not hasattr(cloak, "calculate_adaptive_click_target"):
        pytest.skip("calculate_adaptive_click_target not yet available")

    box = {"x": 200.0, "y": 300.0, "width": 120.0, "height": 40.0}
    page = _MockPage({"#login-btn": box})

    res = cloak.cloaked_mouse_click(
        page=page,
        selector="#login-btn",
        control_type="button",
        adaptive_variance=True,
        sleep_fn=None,
    )
    assert res["success"] is True
    assert res.get("variance_state") == "adaptive"
    tx, ty = res["target"]
    assert 200.0 <= tx < 320.0
    assert 300.0 <= ty < 340.0


def test_human_move_to_login_common_integration():
    """Verify login_impl._common._human_move_to executes with adaptive target calculation."""
    from bulk_downloader.login_impl._common import _human_move_to

    box = {"x": 150.0, "y": 250.0, "width": 100.0, "height": 35.0}
    loc = _MockLocator(box)

    class _PageForMove:
        def __init__(self) -> None:
            self.mouse = _MockMouse()

    page = _PageForMove()
    _human_move_to(page, loc)
    assert len(page.mouse.moves) >= 12
    final_x, final_y = page.mouse.moves[-1]
    # Within bounding box margins
    assert 150.0 <= final_x < 250.0
    assert 250.0 <= final_y < 285.0


def test_runtime_metadata():
    """Verify adaptive input variance engine exports and default configuration."""
    if AdaptiveInputVarianceEngine is None:
        pytest.skip("AdaptiveInputVarianceEngine not yet available")

    engine = get_adaptive_variance_engine()
    assert isinstance(engine, AdaptiveInputVarianceEngine)

    for control_name in ("BUTTON", "TEXT_INPUT", "TEXTAREA", "CHECKBOX", "RADIO", "SELECT", "SLIDER", "GENERIC"):
        assert hasattr(FormControlType, control_name)

    for ctype in FormControlType:
        profile = engine.get_profile(ctype)
        assert isinstance(profile, CoordinateVarianceProfile)
        assert 0.0 <= profile.inner_margin_ratio < 0.5
        assert profile.sigma_factor_x > 0.0
        assert profile.sigma_factor_y > 0.0


def test_bounding_box_validation():
    """Verify BoundingBox geometry invariants and error validation."""
    if BoundingBox is None:
        pytest.skip("BoundingBox not yet available")

    box = BoundingBox(10.0, 20.0, 100.0, 40.0)
    assert box.x == 10.0
    assert box.y == 20.0
    assert box.width == 100.0
    assert box.height == 40.0
    assert box.cx == 60.0
    assert box.cy == 40.0
    assert box.contains(60.0, 40.0) is True
    assert box.contains(5.0, 40.0) is False

    with pytest.raises(ValueError, match="width must be positive"):
        BoundingBox(0.0, 0.0, -10.0, 20.0)


def test_feedback_adaptation_loop():
    """Verify dynamic profile adaptation tightening under high edge misses."""
    if AdaptiveInputVarianceEngine is None:
        pytest.skip("AdaptiveInputVarianceEngine not yet available")

    engine = AdaptiveInputVarianceEngine()
    initial = engine.get_profile(FormControlType.BUTTON)
    adapted = engine.adapt_profile(FormControlType.BUTTON, success_rate=0.80, edge_miss_count=5)
    assert adapted.sigma_factor_x < initial.sigma_factor_x
    assert adapted.inner_margin_ratio >= initial.inner_margin_ratio


def test_cloaked_mouse_click_degenerate_box_preserves_base_geometry():
    """Verify cloaked_mouse_click on zero-width or negative-dimension box preserves base geometry.

    O1224: When adaptive variance evaluates to unavailable/fallback on degenerate boxes,
    callers must never teleport/click at viewport origin (0, 0). Click target must match
    element base geometry (box center) while reporting variance_state='unavailable'.
    """
    box_zero_w = {"x": 200.0, "y": 300.0, "width": 0.0, "height": 40.0}
    page = _MockPage({"#zero-w": box_zero_w})
    res = cloak.cloaked_mouse_click(
        page=page,
        selector="#zero-w",
        control_type="button",
        adaptive_variance=True,
        sleep_fn=None,
    )
    assert res["success"] is True
    # Base behavior was element center: (200.0 + 0/2, 300.0 + 40/2) = (200.0, 320.0), NOT (0.0, 0.0)
    assert res["target"] == (200.0, 320.0), f"Expected base center (200.0, 320.0), got {res['target']}"
    assert res.get("variance_state") == "unavailable"

    # Also test negative-height box
    box_neg_h = {"x": 200.0, "y": 300.0, "width": 120.0, "height": -1.0}
    page_neg = _MockPage({"#neg-h": box_neg_h})
    res_neg = cloak.cloaked_mouse_click(
        page=page_neg,
        selector="#neg-h",
        control_type="button",
        adaptive_variance=True,
        sleep_fn=None,
    )
    assert res_neg["success"] is True
    assert res_neg["target"] == (260.0, 299.5), f"Expected base center (260.0, 299.5), got {res_neg['target']}"
    assert res_neg.get("variance_state") == "unavailable"


def test_human_move_to_degenerate_box_preserves_base_geometry():
    """Verify _human_move_to on zero-width or degenerate box preserves base geometry.

    O1224: When adaptive variance returns unavailable, _human_move_to must move to
    element center (+ jitter) per base behaviour, never teleporting to viewport origin (0, 0).
    """
    from bulk_downloader.login_impl._common import _human_move_to

    class _PageForMove:
        def __init__(self) -> None:
            self.mouse = _MockMouse()

    # Zero-width box
    box_zero_w = {"x": 200.0, "y": 300.0, "width": 0.0, "height": 40.0}
    page1 = _PageForMove()
    _human_move_to(page1, _MockLocator(box_zero_w))
    assert len(page1.mouse.moves) > 0, "Expected moves to element center"
    final_x1, final_y1 = page1.mouse.moves[-1]
    # Base behavior target was (200.0, 320.0) + jitter(-3..3, -2..2)
    assert abs(final_x1 - 200.0) < 10.0, f"Expected final_x near 200.0, got {final_x1}"
    assert abs(final_y1 - 320.0) < 10.0, f"Expected final_y near 320.0, got {final_y1}"
    assert (final_x1, final_y1) != (0.0, 0.0), "Mouse must not teleport to (0, 0)"

    # Negative-height box
    box_neg_h = {"x": 200.0, "y": 300.0, "width": 120.0, "height": -1.0}
    page2 = _PageForMove()
    _human_move_to(page2, _MockLocator(box_neg_h))
    assert len(page2.mouse.moves) > 0, "Expected moves to element center"
    final_x2, final_y2 = page2.mouse.moves[-1]
    assert abs(final_x2 - 260.0) < 10.0, f"Expected final_x near 260.0, got {final_x2}"
    assert abs(final_y2 - 299.5) < 10.0, f"Expected final_y near 299.5, got {final_y2}"
    assert (final_x2, final_y2) != (0.0, 0.0), "Mouse must not teleport to (0, 0)"

