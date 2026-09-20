"""Row 957: STOCHASTIC-SPATIAL-COORDINATE-PERTURBATION-FOR-HIT-TEST-COVERAGE

Tests for bulk_downloader/spatial_hit_test_fuzzer.py.
Verifies:
(1) Gaussian coordinate dispersion within bounding geometry
(2) Boundary clamp enforcement
(3) Zero coordinate generation outside target client rectangles

Client rectangles are HALF-OPEN boxes [x, x+width) x [y, y+height), matching
DOM hit testing (document.elementFromPoint): a point at exactly x+width or
y+height belongs to the neighbour, not the target. Every bounds assertion
below uses that half-open predicate (px < x+width), never <=. Chromium
additionally hit-tests the whole 1x1 CSS-pixel cell enclosing the point
(hit_cell_contained), so the instrument checks that too on whole-pixel rects.

All statistical tests drive SpatialHitTestFuzzer with a seeded random.Random
so they are deterministic; the unseeded module default is only exercised by
the RED test and the delegation test.
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    import bulk_downloader.spatial_hit_test_fuzzer as _mod
    from bulk_downloader.spatial_hit_test_fuzzer import (
        BoundingRect,
        SpatialHitTestFuzzer,
        contains,
        hit_cell_contained,
        perturb_coordinate,
    )
except ImportError:
    _mod = None
    BoundingRect = None
    SpatialHitTestFuzzer = None
    contains = None
    hit_cell_contained = None
    perturb_coordinate = None

BD_GATE_SCOPE = "module"

_SEED = 957

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# Instrument: the one bounds check every test goes through
# ---------------------------------------------------------------------------

def _classify(points: list[Point], r) -> dict:
    """Exact counts of each way a point can leave the half-open box, plus the
    count of points whose enclosing CSS-pixel cell leaks past the box."""
    counts = {"below_x": 0, "far_x": 0, "below_y": 0, "far_y": 0, "outside": 0, "cell_leak": 0}
    for px, py in points:
        if px < r.x:
            counts["below_x"] += 1
        if px >= r.x + r.width:
            counts["far_x"] += 1
        if py < r.y:
            counts["below_y"] += 1
        if py >= r.y + r.height:
            counts["far_y"] += 1
        if not contains(r, px, py):
            counts["outside"] += 1
        if not hit_cell_contained(r, px, py):
            counts["cell_leak"] += 1
    return counts


def _assert_all_inside(points: list[Point], r, *, cell: bool = True) -> dict:
    """Instrument: raise AssertionError naming the exact count if any point is
    outside the half-open box [x, x+width) x [y, y+height), or (cell=True)
    if any point's hit-test cell is not wholly inside the box."""
    assert len(points) > 0, "fixture produced no points"
    counts = _classify(points, r)
    box = f"[{r.x}, {r.x + r.width}) x [{r.y}, {r.y + r.height})"
    if counts["outside"] != 0:
        raise AssertionError(
            f"{counts['outside']}/{len(points)} coordinates outside half-open rect {box}: {counts}"
        )
    if cell and counts["cell_leak"] != 0:
        raise AssertionError(
            f"{counts['cell_leak']}/{len(points)} coordinates whose hit-test cell leaks "
            f"past rect {box}: {counts}"
        )
    return counts


def _sample(fuzzer, r, n: int) -> list[Point]:
    points = [fuzzer.perturb(r) for _ in range(n)]
    assert len(points) == n
    return points


_CLEAN = {"below_x": 0, "far_x": 0, "below_y": 0, "far_y": 0, "outside": 0, "cell_leak": 0}

# Per-axis dispersion band, in units of the axis extent. The fixed module at
# sigma_factor=0.25 measures pstdev 0.2393w / 0.2384h on rect(100,200,80,40)
# (seed 957, n=100000): the clamp trims the Gaussian's 0.25 tails slightly.
# Swapping width/height in the sigma computation measures 0.1250w / 0.3532h,
# outside the band on BOTH axes.
_DISP_LO = 0.15
_DISP_HI = 0.30


def _assert_per_axis_dispersion(points: list[Point], r) -> tuple[float, float]:
    """Instrument for acceptance (1): the population stdev of each axis, as a
    fraction of that axis's extent, lies in (_DISP_LO, _DISP_HI). Raises
    AssertionError naming the offending axis and measured fraction."""
    assert len(points) > 0, "fixture produced no points"
    fx = statistics.pstdev(p[0] for p in points) / r.width
    fy = statistics.pstdev(p[1] for p in points) / r.height
    if not (_DISP_LO < fx < _DISP_HI):
        raise AssertionError(f"x dispersion {fx:.4f}w outside ({_DISP_LO}, {_DISP_HI})")
    if not (_DISP_LO < fy < _DISP_HI):
        raise AssertionError(f"y dispersion {fy:.4f}h outside ({_DISP_LO}, {_DISP_HI})")
    return fx, fy


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_centroid_only_coordinates():
    """Behavioral RED: without the fuzzer, event coordinates are always
    at the element centroid, missing boundary hit-test coverage."""
    rect = {"x": 100, "y": 200, "width": 80, "height": 40}
    centroid_x = rect["x"] + rect["width"] / 2
    centroid_y = rect["y"] + rect["height"] / 2
    n_samples = 50

    if perturb_coordinate is None:
        coords = [(centroid_x, centroid_y)] * n_samples
        unique = set(coords)
        assert len(unique) > 1, (
            f"BEHAVIORAL RED: centroid-only coordinates produce {len(unique)} "
            f"unique point(s) from {n_samples} samples (assert 1 > 1)"
        )

    r = BoundingRect(x=100, y=200, width=80, height=40)
    coords = [perturb_coordinate(r) for _ in range(n_samples)]
    unique = set(coords)
    assert len(unique) > 1


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def test_contains_is_half_open():
    """contains() accepts the near edge, rejects the far edge exactly, and
    accepts the largest float below the far edge."""
    assert contains is not None
    r = BoundingRect(x=100, y=200, width=80, height=40)
    assert contains(r, 100, 200) is True
    assert contains(r, r.cx, r.cy) is True
    assert contains(r, 180, 202.1) is False          # far x edge -> neighbour
    assert contains(r, 110, 240) is False            # far y edge -> neighbour
    assert contains(r, 180, 240) is False
    assert contains(r, 99.999, 220) is False
    assert contains(r, 110, 199.999) is False
    assert contains(r, math.nextafter(180, 100), 220) is True
    assert contains(r, 110, math.nextafter(240, 200)) is True
    assert r.x_max < 180 and r.y_max < 240
    assert contains(r, r.x_max, r.y_max) is True


def test_hit_cell_contained_models_chromium_pixel_cells():
    """The enclosing 1x1 CSS-pixel cell must lie inside the box: integer
    x+width-1 is safe, anything fractional above it leaks to the neighbour,
    and nextafter(x+width) leaks too (it is 180.0 in float32)."""
    assert hit_cell_contained is not None
    r = BoundingRect(x=100, y=200, width=80, height=40)
    assert hit_cell_contained(r, 100, 200) is True
    assert hit_cell_contained(r, 179, 239) is True
    assert hit_cell_contained(r, 178.5, 238.5) is True
    assert hit_cell_contained(r, 179.01, 220) is False
    assert hit_cell_contained(r, 179.5, 220) is False
    assert hit_cell_contained(r, 140, 239.5) is False
    assert hit_cell_contained(r, math.nextafter(180, 100), 220) is False
    assert hit_cell_contained(r, 99.99, 220) is False
    assert hit_cell_contained(r, 140, 199.99) is False
    assert hit_cell_contained(r, 180, 220) is False
    assert r.x_span == (100.0, 179.0) and r.y_span == (200.0, 239.0)
    # sub-pixel-aligned rect: the safe span shrinks to whole cells
    s = BoundingRect(x=100.5, y=200, width=80, height=40)
    assert s.x_span == (101.0, 179.0)
    assert hit_cell_contained(s, 100.5, 220) is False
    assert hit_cell_contained(s, 101, 220) is True
    # rect narrower than one cell: no cell-safe span exists, half-open fallback
    n = BoundingRect(x=50, y=50, width=0.5, height=0.5)
    assert n.x_span == (50, math.nextafter(50.5, 50)) and n.x_max < 50.5
    assert contains(n, n.x_max, n.y_max) is True


# ---------------------------------------------------------------------------
# (1) Gaussian coordinate dispersion within bounding geometry
# ---------------------------------------------------------------------------

def test_gaussian_dispersion_within_bounds():
    """All perturbed coordinates fall inside the half-open bounding rect."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(rng=random.Random(_SEED))
    r = BoundingRect(x=50, y=50, width=100, height=60)
    points = _sample(fuzzer, r, 500)
    counts = _assert_all_inside(points, r)
    assert counts == _CLEAN


def test_gaussian_distribution_spread():
    """Perturbed coordinates are spread across the bounding box, not clustered."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(rng=random.Random(_SEED))
    r = BoundingRect(x=0, y=0, width=200, height=100)
    points = _sample(fuzzer, r, 200)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    x_range = max(xs) - min(xs)
    y_range = max(ys) - min(ys)
    assert x_range > r.width * 0.3, f"x spread {x_range} too narrow"
    assert y_range > r.height * 0.3, f"y spread {y_range} too narrow"
    assert len(set(points)) == 200


def test_gaussian_centered_near_centroid():
    """Mean of samples is near the centroid."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(rng=random.Random(_SEED))
    r = BoundingRect(x=100, y=200, width=80, height=40)
    cx, cy = r.x + r.width / 2, r.y + r.height / 2
    points = _sample(fuzzer, r, 500)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    assert abs(mean_x - cx) < r.width * 0.15, f"mean x {mean_x} too far from centroid {cx}"
    assert abs(mean_y - cy) < r.height * 0.15, f"mean y {mean_y} too far from centroid {cy}"


def test_gaussian_dispersion_is_per_axis_seeded():
    """Acceptance (1) per axis: sigma_x scales with WIDTH and sigma_y with
    HEIGHT. On a 2:1 rect, 100000 seeded draws give pstdev in (0.15, 0.30) of
    each axis's own extent (fixed module: 0.2393w / 0.2384h). A width/height
    swap in the sigma computation measures 0.1250w / 0.3532h and pins ~33% of
    y draws to an edge, so it fails on both axes and on the edge-pin count."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(rng=random.Random(_SEED))
    r = BoundingRect(x=100, y=200, width=80, height=40)
    total = 100000
    points = _sample(fuzzer, r, total)
    assert _assert_all_inside(points, r) == _CLEAN
    fx, fy = _assert_per_axis_dispersion(points, r)
    assert 0.15 * r.width < statistics.pstdev(p[0] for p in points) < 0.30 * r.width
    assert 0.15 * r.height < statistics.pstdev(p[1] for p in points) < 0.30 * r.height
    # the two axes are dispersed alike relative to their own extent (within 0.02)
    assert abs(fx - fy) < 0.02, (fx, fy)
    # the clamp pins only the ~4.6% two-sided tail beyond +/-2 sigma per axis;
    # never more than 10% of draws on either axis (the swap pins 33% of y)
    pinned_x = sum(1 for px, _ in points if px in (r.x, r.x_max))
    pinned_y = sum(1 for _, py in points if py in (r.y, r.y_max))
    assert 0 < pinned_x < total // 10, pinned_x
    assert 0 < pinned_y < total // 10, pinned_y


# ---------------------------------------------------------------------------
# (2) Boundary clamp enforcement
# ---------------------------------------------------------------------------

def test_clamp_enforced_on_small_rect_never_far_edge():
    """sigma_factor=2.0 on a 5x5 rect: the clamp fires on every side (proved
    nonzero) yet the far edge x=15 / y=15 is never produced, nor any point
    whose hit-test cell reaches it."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(sigma_factor=2.0, rng=random.Random(_SEED))
    r = BoundingRect(x=10, y=10, width=5, height=5)
    total = 100000
    points = _sample(fuzzer, r, total)
    counts = _assert_all_inside(points, r)
    assert counts == _CLEAN
    assert sum(1 for px, _ in points if px == 15) == 0
    assert sum(1 for _, py in points if py == 15) == 0
    assert sum(1 for px, _ in points if px >= 15) == 0
    assert sum(1 for _, py in points if py >= 15) == 0
    assert sum(1 for px, _ in points if px > 14) == 0
    assert sum(1 for _, py in points if py > 14) == 0
    # the clamp really fired on both sides: lower edge and x_max/y_max both hit
    assert (r.x_max, r.y_max) == (14.0, 14.0)
    low_x = sum(1 for px, _ in points if px == 10)
    low_y = sum(1 for _, py in points if py == 10)
    top_x = sum(1 for px, _ in points if px == r.x_max)
    top_y = sum(1 for _, py in points if py == r.y_max)
    assert low_x > 0 and low_y > 0 and top_x > 0 and top_y > 0, (low_x, low_y, top_x, top_y)
    assert low_x + top_x > total // 2, "sigma 2.0 on 5px must clamp most x draws"
    assert sum(1 for px, _ in points if 10 < px < 14) > 0, "interior still reached"


def test_clamp_at_exact_boundaries_1x1():
    """1x1 rect always returns coordinates inside [50, 51) x [50, 51); the only
    coordinate whose whole pixel cell is inside is (50, 50)."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(sigma_factor=2.0, rng=random.Random(_SEED))
    r = BoundingRect(x=50, y=50, width=1, height=1)
    total = 100000
    points = _sample(fuzzer, r, total)
    counts = _assert_all_inside(points, r)
    assert counts == _CLEAN
    assert sum(1 for px, py in points if px >= 51 or py >= 51) == 0
    assert sum(1 for px, py in points if px < 50 or py < 50) == 0
    assert all(50 <= px < 51 and 50 <= py < 51 for px, py in points)
    assert set(points) == {(50.0, 50.0)}
    assert sum(1 for p in points if p == (50.0, 50.0)) == total


def test_clamp_subpixel_rect_falls_back_to_half_open():
    """A rect narrower than one CSS pixel has no cell-safe span; the fuzzer
    still never leaves the half-open box."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(sigma_factor=2.0, rng=random.Random(_SEED))
    total = 10000
    for r in (BoundingRect(x=50, y=50, width=0.5, height=0.5),
              BoundingRect(x=100.3, y=10, width=0.5, height=3)):
        points = _sample(fuzzer, r, total)
        counts = _assert_all_inside(points, r, cell=False)
        assert counts["outside"] == 0
        assert counts["far_x"] == 0 and counts["far_y"] == 0
        assert counts["below_x"] == 0 and counts["below_y"] == 0
        assert sum(1 for px, _ in points if px == r.x_max) > 0
        assert sum(1 for px, _ in points if px == r.x) > 0
        assert r.x_max < r.x + r.width


def test_clamp_fractional_origin_rect_generated_points_stay_in_cells():
    """A rect with a fractional origin (100.5, 200.25) shrinks the cell-safe
    span to [101, 179] x [201, 239]; 100000 seeded generated points never
    leave the half-open box nor leak a hit-test cell, and both span ends are
    actually produced (so a lo=edge / hi=edge+size-1 span without ceil/floor
    would be caught by the instrument, not just by a property assertion)."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(rng=random.Random(_SEED))
    r = BoundingRect(x=100.5, y=200.25, width=80, height=40)
    total = 100000
    points = _sample(fuzzer, r, total)
    assert _assert_all_inside(points, r) == _CLEAN
    assert (r.x_span, r.y_span) == ((101.0, 179.0), (201.0, 239.0))
    assert sum(1 for px, _ in points if px == 101.0) > 0
    assert sum(1 for px, _ in points if px == 179.0) > 0
    assert sum(1 for _, py in points if py == 201.0) > 0
    assert sum(1 for _, py in points if py == 239.0) > 0
    assert sum(1 for px, py in points if px < 101.0 or py < 201.0) == 0
    assert sum(1 for px, py in points if px > 179.0 or py > 239.0) == 0
    assert sum(1 for px, py in points if 100.5 <= px < 101.0 or 200.25 <= py < 201.0) == 0


def test_clamp_extreme_magnitude_rect_never_reaches_far_edge():
    """At width=1e17, floor(x+width)-1 rounds back onto x+width in float; the
    span guard must still keep every generated point strictly below the far
    edge (a float-degenerate case outside any real DOM range, kept honest)."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(sigma_factor=2.0, rng=random.Random(_SEED))
    r = BoundingRect(x=0, y=0, width=1e17, height=10)
    total = 20000
    assert float(math.floor(r.x + r.width) - 1) == r.x + r.width  # the rounding hazard is real
    assert r.x_max < r.x + r.width
    assert r.x_max == math.nextafter(1e17, 0)
    points = _sample(fuzzer, r, total)
    counts = _assert_all_inside(points, r)
    assert counts == _CLEAN
    assert sum(1 for px, _ in points if px == r.x_max) > 0, "far clamp must fire"
    assert sum(1 for px, _ in points if px >= 1e17) == 0


# ---------------------------------------------------------------------------
# (3) Zero coordinate generation outside target client rectangles
# ---------------------------------------------------------------------------

def test_zero_outside_coordinates_exact_counts():
    """0 of 100000 seeded samples have px >= x+width or py >= y+height;
    0 have px < x or py < y; 0 have a hit-test cell leaking past the box."""
    assert SpatialHitTestFuzzer is not None
    fuzzer = SpatialHitTestFuzzer(rng=random.Random(_SEED))
    r = BoundingRect(x=300, y=400, width=50, height=30)
    total = 100000
    points = _sample(fuzzer, r, total)
    counts = _classify(points, r)
    assert counts["far_x"] == 0
    assert counts["far_y"] == 0
    assert counts["below_x"] == 0
    assert counts["below_y"] == 0
    assert counts["outside"] == 0
    assert counts["cell_leak"] == 0
    assert sum(1 for px, py in points if px >= r.x + r.width or py >= r.y + r.height) == 0
    assert sum(1 for px, py in points if px < r.x or py < r.y) == 0
    assert sum(1 for px, py in points if contains(r, px, py)) == total
    assert sum(1 for px, py in points if hit_cell_contained(r, px, py)) == total


def test_perturb_coordinate_delegates_to_default_fuzzer(monkeypatch):
    """perturb_coordinate() delegates to the module default fuzzer."""
    assert _mod is not None
    r = BoundingRect(x=100, y=200, width=80, height=40)
    assert isinstance(_mod._DEFAULT_FUZZER, SpatialHitTestFuzzer)
    monkeypatch.setattr(_mod, "_DEFAULT_FUZZER", SpatialHitTestFuzzer(rng=random.Random(_SEED)))
    via_function = [perturb_coordinate(r) for _ in range(50)]
    via_instance = _sample(SpatialHitTestFuzzer(rng=random.Random(_SEED)), r, 50)
    assert via_function == via_instance
    assert _assert_all_inside(via_function, r) == _CLEAN


def test_invalid_rect_raises():
    """Zero or negative dimensions raise ValueError."""
    assert BoundingRect is not None
    with pytest.raises(ValueError, match="width must be positive"):
        BoundingRect(x=0, y=0, width=0, height=10)
    with pytest.raises(ValueError, match="height must be positive"):
        BoundingRect(x=0, y=0, width=10, height=-1)


def test_non_finite_or_float_degenerate_rect_raises():
    """nan/inf fields and rects whose half-open extent is empty in float
    arithmetic (x + width == x) are rejected at construction, so perturb()'s
    contract 'every returned point satisfies contains()' has no unsatisfiable
    inputs; ordinary sub-pixel rects are still accepted."""
    assert BoundingRect is not None
    with pytest.raises(ValueError, match="width must be finite"):
        BoundingRect(x=0, y=0, width=math.nan, height=1)
    with pytest.raises(ValueError, match="width must be finite"):
        BoundingRect(x=0, y=0, width=math.inf, height=1)
    with pytest.raises(ValueError, match="x must be finite"):
        BoundingRect(x=math.inf, y=0, width=1, height=1)
    with pytest.raises(ValueError, match="y must be finite"):
        BoundingRect(x=0, y=math.nan, width=1, height=1)
    with pytest.raises(ValueError, match="degenerate rect"):
        BoundingRect(x=50, y=50, width=1e-300, height=1e-300)
    with pytest.raises(ValueError, match="degenerate rect"):
        BoundingRect(x=50, y=50, width=1, height=1e-300)
    assert BoundingRect(x=50, y=50, width=0.5, height=0.5).x_max < 50.5
    assert BoundingRect(x=0, y=0, width=1e-300, height=1e-300).x_max < 1e-300


# ---------------------------------------------------------------------------
# Negative controls: the instrument must fail, for the intended reason, on
# generators that violate the half-open box or the pixel-cell rule.
# ---------------------------------------------------------------------------

def _mutant_points(r, sigma_factor: float, total: int,
                   clamp: Callable[[float, float], Point]) -> tuple[list[Point], int, int]:
    """Replay the same seeded Gaussian draw sequence as the fuzzer, apply a
    mutant clamp, and return the points plus independently derived counts of
    draws outside the half-open box and draws whose pixel cell leaks."""
    rng = random.Random(_SEED)
    points: list[Point] = []
    expected_outside = 0
    expected_leak = 0
    for _ in range(total):
        gx = rng.gauss(r.cx, r.width * sigma_factor)
        gy = rng.gauss(r.cy, r.height * sigma_factor)
        px, py = clamp(gx, gy)
        points.append((px, py))
        if not (r.x <= px < r.x + r.width and r.y <= py < r.y + r.height):
            expected_outside += 1
        if not (math.floor(px) >= r.x and math.ceil(px + 1) <= r.x + r.width
                and math.floor(py) >= r.y and math.ceil(py + 1) <= r.y + r.height):
            expected_leak += 1
    return points, expected_outside, expected_leak


def test_negative_control_closed_interval_clamp_is_caught():
    """The pre-fix closed-interval clamp max(x, min(px, x+width)) lands draws
    exactly on the far edge; the instrument must reject it with the exact
    far-edge count."""
    assert BoundingRect is not None
    r = BoundingRect(x=100, y=200, width=80, height=40)
    total = 1000

    def closed_clamp(gx: float, gy: float) -> Point:
        return (max(r.x, min(gx, r.x + r.width)), max(r.y, min(gy, r.y + r.height)))

    points, expected, _ = _mutant_points(r, 2.0, total, closed_clamp)
    assert len(points) == total
    assert expected > 0, "fixture shape: closed clamp must pin some draws to the far edge"
    counts = _classify(points, r)
    assert counts["below_x"] == 0 and counts["below_y"] == 0  # only the far edge leaks
    assert counts["outside"] == expected
    assert sum(1 for px, py in points if px == 180 or py == 240) == expected
    with pytest.raises(AssertionError, match=rf"^{expected}/{total} coordinates outside half-open rect"):
        _assert_all_inside(points, r)

    # positive control on the same instrument: the real fuzzer with the same
    # seed and sigma passes.
    real = _sample(SpatialHitTestFuzzer(sigma_factor=2.0, rng=random.Random(_SEED)), r, total)
    assert _assert_all_inside(real, r) == _CLEAN


def test_negative_control_nextafter_clamp_is_caught_by_cell_rule():
    """A clamp to nextafter(x+width, x) satisfies the half-open predicate yet
    every such point's pixel cell reaches the neighbour (Chromium: 180.0 in
    float32). The instrument must reject it with the exact cell-leak count."""
    assert BoundingRect is not None
    r = BoundingRect(x=100, y=200, width=80, height=40)
    total = 1000
    hx, hy = math.nextafter(180, 100), math.nextafter(240, 200)

    def nextafter_clamp(gx: float, gy: float) -> Point:
        return (max(r.x, min(gx, hx)), max(r.y, min(gy, hy)))

    points, expected_outside, expected_leak = _mutant_points(r, 0.25, total, nextafter_clamp)
    assert len(points) == total
    assert expected_outside == 0  # the mutant is honest about the half-open box...
    assert expected_leak > 0, "fixture shape: nextafter clamp must pin some draws next to the far edge"
    counts = _classify(points, r)
    assert counts["outside"] == 0
    assert counts["cell_leak"] == expected_leak  # ...but not about pixel cells
    assert _assert_all_inside(points, r, cell=False)["outside"] == 0
    with pytest.raises(AssertionError, match=rf"^{expected_leak}/{total} coordinates whose hit-test cell leaks"):
        _assert_all_inside(points, r)


def test_negative_control_unclamped_generator_is_caught():
    """A generator with the clamp bypassed leaks on every side; the instrument
    must reject it with the exact outside count."""
    assert BoundingRect is not None
    r = BoundingRect(x=100, y=100, width=50, height=50)
    total = 1000

    points, expected, _ = _mutant_points(r, 1.0, total, lambda gx, gy: (gx, gy))
    assert len(points) == total
    assert expected > 0, "fixture shape: unclamped draws must leave the rect"
    counts = _classify(points, r)
    assert counts["outside"] == expected
    assert counts["below_x"] > 0 and counts["far_x"] > 0
    assert counts["below_y"] > 0 and counts["far_y"] > 0
    with pytest.raises(AssertionError, match=rf"^{expected}/{total} coordinates outside half-open rect"):
        _assert_all_inside(points, r)


def test_negative_control_swapped_sigma_axes_is_caught():
    """A generator whose sigma_x scales with HEIGHT and sigma_y with WIDTH
    (the width/height swap mutant) keeps every point inside the box yet fails
    acceptance (1): the per-axis dispersion instrument must reject it, on the
    x axis first, with the measured fraction, while the real fuzzer passes."""
    assert BoundingRect is not None
    r = BoundingRect(x=100, y=200, width=80, height=40)
    total = 100000
    rng = random.Random(_SEED)
    x_min, x_max = r.x_span
    y_min, y_max = r.y_span
    swapped: list[Point] = []
    for _ in range(total):
        gx = rng.gauss(r.cx, r.height * 0.25)  # swapped
        gy = rng.gauss(r.cy, r.width * 0.25)   # swapped
        swapped.append((max(x_min, min(gx, x_max)), max(y_min, min(gy, y_max))))
    assert len(swapped) == total
    assert _assert_all_inside(swapped, r) == _CLEAN  # bounds alone cannot see the swap
    fx = statistics.pstdev(p[0] for p in swapped) / r.width
    fy = statistics.pstdev(p[1] for p in swapped) / r.height
    assert fx < _DISP_LO and fy > _DISP_HI, (fx, fy)  # fixture shape: both axes out of band
    with pytest.raises(AssertionError, match=rf"^x dispersion {fx:.4f}w outside"):
        _assert_per_axis_dispersion(swapped, r)
    # y-only mutant: x honest, y swapped -> instrument names the y axis
    y_only = [(px, py) for (px, _), (_, py) in zip(
        _sample(SpatialHitTestFuzzer(rng=random.Random(_SEED)), r, total), swapped)]
    fy_only = statistics.pstdev(p[1] for p in y_only) / r.height
    assert fy_only > _DISP_HI
    with pytest.raises(AssertionError, match=rf"^y dispersion {fy_only:.4f}h outside"):
        _assert_per_axis_dispersion(y_only, r)
    # positive control: the real fuzzer on the same seed passes the instrument
    real = _sample(SpatialHitTestFuzzer(rng=random.Random(_SEED)), r, total)
    rfx, rfy = _assert_per_axis_dispersion(real, r)
    assert _DISP_LO < rfx < _DISP_HI and _DISP_LO < rfy < _DISP_HI
