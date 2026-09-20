"""tests/test_bezier_mouse.py - Row 913: Natural input trajectory simulation.

Verifies:
(1) Non-linear mouse coordinate sequences generated via curved Bezier paths
(2) Realistic timing intervals between mousemove/mousedown/mouseup
(3) Zero test suite overhead (pluggable/instant simulated time)
"""
from __future__ import annotations

import importlib
import math
import time
from typing import Any

cloak = importlib.import_module("bulk_downloader.cloak")

BD_GATE_SCOPE = "module"


class MockMouse:
    """Mock Playwright mouse interface for tracking event sequences and coordinates."""

    def __init__(self, clock_fn: Any = None):
        self.events: list[tuple[str, Any, float]] = []
        self._clock_fn = clock_fn or time.time
        self.current_x = 0.0
        self.current_y = 0.0

    def move(self, x: float, y: float) -> None:
        self.current_x = float(x)
        self.current_y = float(y)
        self.events.append(("move", (self.current_x, self.current_y), self._clock_fn()))

    def down(self, button: str = "left") -> None:
        self.events.append(("down", button, self._clock_fn()))

    def up(self, button: str = "left") -> None:
        self.events.append(("up", button, self._clock_fn()))

    def click(self, x: float, y: float, delay: float = 0.0) -> None:
        self.move(x, y)
        self.down()
        self.up()


class MockLocator:
    """Mock Playwright locator for bounding box measurements."""

    def __init__(self, box: dict[str, float] | None):
        self._box = box

    @property
    def first(self) -> MockLocator:
        return self

    def bounding_box(self) -> dict[str, float] | None:
        return self._box


class MockPage:
    """Mock Playwright page interface."""

    def __init__(self, clock_fn: Any = None):
        self.mouse = MockMouse(clock_fn=clock_fn)
        self._locators: dict[str, MockLocator] = {}

    def set_locator(self, selector: str, box: dict[str, float] | None) -> None:
        self._locators[selector] = MockLocator(box)

    def locator(self, selector: str) -> MockLocator:
        return self._locators.get(selector, MockLocator(None))


class SimulatedClock:
    """Simulated timekeeper to verify realistic timing without actual sleep overhead."""

    def __init__(self, start_time: float = 1000.0):
        self.current_time = start_time
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.current_time

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current_time += seconds


def _perpendicular_distance(
    pt: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    """Calculate distance of pt from line through line_start and line_end."""
    x0, y0 = pt
    x1, y1 = line_start
    x2, y2 = line_end
    line_len = math.hypot(x2 - x1, y2 - y1)
    if line_len < 1e-6:
        return math.hypot(x0 - x1, y0 - y1)
    return abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1) / line_len


def test_generate_bezier_mouse_path_non_linear():
    """Verify that generated mouse path produces non-linear coordinates along a curved trajectory."""
    fn = getattr(cloak, "generate_bezier_mouse_path", None)
    assert callable(fn), "cloak must provide generate_bezier_mouse_path"

    start = (10.0, 20.0)
    target = (300.0, 450.0)
    steps = 25
    path = fn(start=start, target=target, steps=steps, deviation=0.25)

    assert isinstance(path, list)
    assert len(path) >= steps
    # First point should match start and last point match target
    assert math.isclose(path[0][0], start[0], abs_tol=1e-3)
    assert math.isclose(path[0][1], start[1], abs_tol=1e-3)
    assert math.isclose(path[-1][0], target[0], abs_tol=1e-3)
    assert math.isclose(path[-1][1], target[1], abs_tol=1e-3)

    # Calculate maximum perpendicular deviation from the direct straight line
    max_dev = max(_perpendicular_distance(p, start, target) for p in path)
    # Curved path must exhibit measurable non-linear lateral displacement (> 5 px)
    assert max_dev > 5.0, f"Expected non-linear curved path, got max deviation {max_dev}"


def test_bezier_mouse_path_variable_speed_pacing():
    """Verify variable speed pacing (slow start, peak velocity, slow finish)."""
    fn = getattr(cloak, "generate_bezier_mouse_path", None)
    assert callable(fn), "cloak must provide generate_bezier_mouse_path"

    start = (0.0, 0.0)
    target = (600.0, 0.0)
    steps = 30
    path = fn(start=start, target=target, steps=steps, deviation=0.0, wobble=0.0)

    # Compute step increments along the trajectory
    step_lengths = [
        math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        for i in range(1, len(path))
    ]

    # With variable speed pacing, step lengths must not be constant
    std_dev = (sum((x - sum(step_lengths) / len(step_lengths)) ** 2 for x in step_lengths) / len(step_lengths)) ** 0.5
    assert std_dev > 0.5, f"Expected variable speed pacing, got std_dev {std_dev}"

    # Acceleration at start: first step smaller than midpoint step
    mid_idx = len(step_lengths) // 2
    assert step_lengths[0] < step_lengths[mid_idx], "Expected acceleration from start"
    assert step_lengths[-1] < step_lengths[mid_idx], "Expected deceleration near target"


def test_calculate_click_intervals_ranges():
    """Verify realistic timing intervals between mousemove, mousedown, and mouseup."""
    fn = getattr(cloak, "calculate_click_intervals", None)
    assert callable(fn), "cloak must provide calculate_click_intervals"

    intervals = fn()
    assert isinstance(intervals, dict)
    assert "pre_click_ms" in intervals
    assert "hold_ms" in intervals
    assert "post_click_ms" in intervals

    # Realistic human intervals
    assert 15.0 <= intervals["pre_click_ms"] <= 70.0, f"Unrealistic pre_click: {intervals['pre_click_ms']}"
    assert 40.0 <= intervals["hold_ms"] <= 150.0, f"Unrealistic hold: {intervals['hold_ms']}"
    assert 10.0 <= intervals["post_click_ms"] <= 60.0, f"Unrealistic post_click: {intervals['post_click_ms']}"


def test_cloaked_mouse_move_dispatches_events():
    """Verify that cloaked_mouse_move dispatches a cascade of mousemove events."""
    fn = getattr(cloak, "cloaked_mouse_move", None)
    assert callable(fn), "cloak must provide cloaked_mouse_move"

    page = MockPage()
    target = (250.0, 350.0)
    start = (10.0, 10.0)

    path = fn(page=page, target_x=target[0], target_y=target[1], start_pos=start, steps=15, sleep_fn=None)
    assert len(path) >= 15

    move_events = [ev for ev in page.mouse.events if ev[0] == "move"]
    assert len(move_events) == len(path)
    # Final move event must be at target coordinates
    assert move_events[-1][1] == target


def test_cloaked_mouse_click_lifecycle_and_timing():
    """Verify complete click lifecycle with realistic timing intervals using simulated clock."""
    fn = getattr(cloak, "cloaked_mouse_click", None)
    assert callable(fn), "cloak must provide cloaked_mouse_click"

    clock = SimulatedClock()
    page = MockPage(clock_fn=clock.time)

    result = fn(
        page=page,
        target_x=150.0,
        target_y=200.0,
        start_pos=(0.0, 0.0),
        steps=12,
        sleep_fn=clock.sleep,
    )

    assert result["success"] is True
    # Verify event sequencing: moves -> down -> up
    event_names = [ev[0] for ev in page.mouse.events]
    assert "down" in event_names
    assert "up" in event_names
    down_idx = event_names.index("down")
    up_idx = event_names.index("up")
    assert down_idx < up_idx
    # All move events must precede down
    for i in range(down_idx):
        assert event_names[i] == "move"

    # Verify hold interval recorded between down and up
    down_time = page.mouse.events[down_idx][2]
    up_time = page.mouse.events[up_idx][2]
    hold_duration_ms = (up_time - down_time) * 1000.0
    assert 40.0 <= hold_duration_ms <= 150.0, f"Expected realistic hold timing, got {hold_duration_ms}ms"

    # Verify pre-click pause between last move and down
    last_move_time = page.mouse.events[down_idx - 1][2]
    pre_click_ms = (down_time - last_move_time) * 1000.0
    assert 15.0 <= pre_click_ms <= 70.0, f"Expected realistic pre-click timing, got {pre_click_ms}ms"


def test_zero_test_suite_overhead():
    """Verify zero test suite overhead: running operations without sleep completes instantly."""
    move_fn = getattr(cloak, "cloaked_mouse_move", None)
    click_fn = getattr(cloak, "cloaked_mouse_click", None)
    assert callable(move_fn) and callable(click_fn), "cloak must provide cloaked_mouse_move and cloaked_mouse_click"

    page = MockPage()
    t0 = time.perf_counter()
    for _ in range(5):
        move_fn(page=page, target_x=100.0, target_y=100.0, start_pos=(0.0, 0.0), steps=20, sleep_fn=None)
        click_fn(page=page, target_x=200.0, target_y=200.0, start_pos=(100.0, 100.0), steps=20, sleep_fn=None)
    elapsed = time.perf_counter() - t0

    # 5 iterations of 20-step move and 20-step click must execute in < 50ms without sleep
    assert elapsed < 0.05, f"Execution took {elapsed:.4f}s, expected < 0.05s for zero test suite overhead"


def test_cloaked_element_click_with_locator():
    """Verify cloaked_mouse_click supports element selector or locator with bounding box."""
    click_fn = getattr(cloak, "cloaked_mouse_click", None)
    assert callable(click_fn), "cloak must provide cloaked_mouse_click"

    page = MockPage()
    page.set_locator("#submit-btn", {"x": 100.0, "y": 200.0, "width": 80.0, "height": 40.0})

    result = click_fn(page=page, selector="#submit-btn", sleep_fn=None)
    assert result["success"] is True
    # Center should be (100 + 40, 200 + 20) = (140, 220)
    assert math.isclose(result["target"][0], 140.0, abs_tol=1e-1)
    assert math.isclose(result["target"][1], 220.0, abs_tol=1e-1)


def test_edge_case_same_start_and_target():
    """Verify trajectory handles identical start and target without division by zero."""
    fn = getattr(cloak, "generate_bezier_mouse_path", None)
    assert callable(fn), "cloak must provide generate_bezier_mouse_path"

    path = fn(start=(50.0, 50.0), target=(50.0, 50.0), steps=10)
    assert len(path) >= 1
    for pt in path:
        assert math.isclose(pt[0], 50.0, abs_tol=1e-3)
        assert math.isclose(pt[1], 50.0, abs_tol=1e-3)
