"""spatial_hit_test_fuzzer -- 2D Gaussian coordinate perturbation for hit-test coverage.

Row 957 (v3.66.1585): distribute client input coordinates across element
bounding box surface geometries using 2D Gaussian perturbation, clamped to
the target rectangle boundary. Replaces centroid-only event coordinates
for boundary hit-test coverage in automated testing.

Half-open box semantics
-----------------------
DOM hit testing (``document.elementFromPoint``, DOMRect containment) treats
a client rectangle as the HALF-OPEN box ``[x, x + width) x [y, y + height)``:
a point at exactly ``x + width`` or ``y + height`` belongs to the neighbouring
element (or the body), not to the target. ``contains()`` is that predicate.

Chromium goes one step further (measured on headless Chromium via Playwright):
the point is converted to float32 / 1/64-px LayoutUnit, the 1x1 CSS-pixel
cell enclosing it (``[floor(p), ceil(p + 1))``) is intersected with the layout
boxes, and the topmost intersecting element wins. Hence ``179.5`` inside a
``[100, 180)`` box hits the RIGHT neighbour, and ``nextafter(180, 100)``
collapses to ``180.0`` in float32 and misses. ``hit_cell_contained()`` is that
predicate.

``perturb()`` therefore clamps each axis into the closed span
``[ceil(x), floor(x + width) - 1]`` -- the coordinates whose whole hit-test
cell lies inside the client rectangle -- so every returned point satisfies
both ``contains()`` and ``hit_cell_contained()`` and ``elementFromPoint``
resolves to the target regardless of neighbours. Only when the rectangle does
not span one whole CSS pixel (e.g. width < 1, or a sub-pixel rect straddling
a pixel edge) does it fall back to the half-open clamp
``[x, nextafter(x + width, x)]``; ``contains()`` still holds for every draw.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass


def _clamp_span(edge: float, size: float) -> tuple[float, float]:
    """Inclusive [lo, hi] clamp span for one axis of the half-open extent
    ``[edge, edge + size)``: the whole-pixel-cell-safe span when the extent
    covers at least one full CSS pixel, else the half-open extent itself.
    ``hi < edge + size`` is guaranteed even when float rounding at extreme
    magnitudes (>= 2**53) would collapse ``floor(edge + size) - 1`` onto the
    far edge."""
    far = edge + size
    lo = float(math.ceil(edge))
    hi = float(math.floor(far) - 1)
    if hi < lo:
        lo, hi = edge, far
    if hi >= far:
        hi = math.nextafter(far, edge)
    return lo, hi


@dataclass(frozen=True)
class BoundingRect:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        for name in ("x", "y", "width", "height"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite, got {getattr(self, name)}")
        if self.width <= 0:
            raise ValueError(f"width must be positive, got {self.width}")
        if self.height <= 0:
            raise ValueError(f"height must be positive, got {self.height}")
        # the half-open extent must be non-empty in float arithmetic, otherwise
        # no coordinate can satisfy contains() (e.g. width=1e-300 at x=50)
        if not (self.x + self.width > self.x and self.y + self.height > self.y):
            raise ValueError(
                f"degenerate rect: x+width={self.x + self.width!r} vs x={self.x!r}, "
                f"y+height={self.y + self.height!r} vs y={self.y!r}"
            )

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2

    @property
    def x_span(self) -> tuple[float, float]:
        """Inclusive [x_min, x_max] that perturb() clamps into; x_max < x + width."""
        return _clamp_span(self.x, self.width)

    @property
    def y_span(self) -> tuple[float, float]:
        """Inclusive [y_min, y_max] that perturb() clamps into; y_max < y + height."""
        return _clamp_span(self.y, self.height)

    @property
    def x_max(self) -> float:
        """Largest x perturb() can return (strictly below x + width)."""
        return self.x_span[1]

    @property
    def y_max(self) -> float:
        """Largest y perturb() can return (strictly below y + height)."""
        return self.y_span[1]


def contains(rect: BoundingRect, px: float, py: float) -> bool:
    """Half-open DOMRect containment matching DOM hit testing:
    ``x <= px < x + width`` and ``y <= py < y + height``. The far edge is OUTSIDE."""
    return (rect.x <= px < rect.x + rect.width) and (rect.y <= py < rect.y + rect.height)


def hit_cell_contained(rect: BoundingRect, px: float, py: float) -> bool:
    """Chromium hit-test model: the 1x1 CSS-pixel cell enclosing (px, py),
    ``[floor(px), ceil(px + 1)) x [floor(py), ceil(py + 1))``, lies wholly
    inside the half-open client rectangle, so no neighbour can win the hit."""
    return (
        math.floor(px) >= rect.x and math.ceil(px + 1) <= rect.x + rect.width
        and math.floor(py) >= rect.y and math.ceil(py + 1) <= rect.y + rect.height
    )


class SpatialHitTestFuzzer:
    def __init__(self, *, sigma_factor: float = 0.25, rng: random.Random | None = None) -> None:
        self._sigma_factor = sigma_factor
        self._rng = rng or random.Random()

    def perturb(self, rect: BoundingRect) -> tuple[float, float]:
        """Draw one 2D Gaussian point centred on the rect centroid and clamp it
        into the half-open client box ``[x, x + width) x [y, y + height)``.

        Every returned point satisfies ``contains(rect, px, py)``; in particular
        ``px < rect.x + rect.width`` and ``py < rect.y + rect.height`` always
        hold. When the rect spans whole CSS pixels the point additionally
        satisfies ``hit_cell_contained()`` so ``elementFromPoint(px, py)``
        resolves to the target element rather than its neighbour.
        """
        sigma_x = rect.width * self._sigma_factor
        sigma_y = rect.height * self._sigma_factor

        px = self._rng.gauss(rect.cx, sigma_x)
        py = self._rng.gauss(rect.cy, sigma_y)

        x_min, x_max = rect.x_span
        y_min, y_max = rect.y_span
        px = max(x_min, min(px, x_max))
        py = max(y_min, min(py, y_max))

        return px, py


_DEFAULT_FUZZER = SpatialHitTestFuzzer()


def perturb_coordinate(rect: BoundingRect) -> tuple[float, float]:
    return _DEFAULT_FUZZER.perturb(rect)
