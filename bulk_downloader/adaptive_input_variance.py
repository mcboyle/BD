"""adaptive_input_variance -- Adaptive Input Coordinate Variance for Form Controls.

Row 980 (v3.66.1619): calculate adaptive geometric coordinate variance,
control-specific safe interactive zones, truncated Gaussian dispersion,
trajectory micro-jitter modeling, and dynamic feedback-driven profile
adaptation for enterprise form controls.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum


class FormControlType(str, Enum):
    """Supported form control categories for adaptive variance modeling."""
    BUTTON = "button"
    TEXT_INPUT = "text_input"
    TEXTAREA = "textarea"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SELECT = "select"
    SLIDER = "slider"
    TOGGLE = "toggle"
    GENERIC = "generic"


@dataclass(frozen=True)
class BoundingBox:
    """Immutable 2D bounding rectangle with half-open boundary containment."""
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for attr in ("x", "y", "width", "height"):
            val = getattr(self, attr)
            if not math.isfinite(val):
                raise ValueError(f"{attr} must be finite, got {val}")
        if self.width <= 0:
            raise ValueError(f"width must be positive, got {self.width}")
        if self.height <= 0:
            raise ValueError(f"height must be positive, got {self.height}")

    @property
    def cx(self) -> float:
        return self.x + self.width / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.height / 2.0

    @property
    def left(self) -> float:
        return self.x

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, px: float, py: float) -> bool:
        """Half-open DOM boundary containment: x <= px < right and y <= py < bottom."""
        return (self.x <= px < self.right) and (self.y <= py < self.bottom)


@dataclass
class CoordinateVarianceProfile:
    """Control-specific geometric variance and boundary margin parameters."""
    inner_margin_ratio: float = 0.15
    sigma_factor_x: float = 0.20
    sigma_factor_y: float = 0.20
    bias_x: float = 0.0
    bias_y: float = 0.0
    min_margin_px: float = 1.0
    skew_factor: float = 0.0


@dataclass(frozen=True)
class VarianceSample:
    """Telemetry record for an evaluated adaptive coordinate sample."""
    x: float
    y: float
    control_type: FormControlType
    distance_from_center: float
    normalized_offset: tuple[float, float]
    inside_bounding_box: bool
    inside_inner_zone: bool


class AdaptiveInputVarianceEngine:
    """Core engine calculating adaptive coordinate dispersion across form control geometries."""

    def __init__(
        self,
        profiles: dict[FormControlType, CoordinateVarianceProfile] | None = None,
    ) -> None:
        self._profiles: dict[FormControlType, CoordinateVarianceProfile] = {}
        self._init_default_profiles()
        if profiles:
            self._profiles.update(profiles)

    def _init_default_profiles(self) -> None:
        self._profiles = {
            FormControlType.BUTTON: CoordinateVarianceProfile(
                inner_margin_ratio=0.10,
                sigma_factor_x=0.18,
                sigma_factor_y=0.22,
                min_margin_px=2.0,
            ),
            FormControlType.TEXT_INPUT: CoordinateVarianceProfile(
                inner_margin_ratio=0.15,
                sigma_factor_x=0.25,
                sigma_factor_y=0.15,
                bias_x=-0.12,  # Bias leftward away from clear-button / right adornments
                min_margin_px=2.0,
            ),
            FormControlType.TEXTAREA: CoordinateVarianceProfile(
                inner_margin_ratio=0.12,
                sigma_factor_x=0.25,
                sigma_factor_y=0.20,
                min_margin_px=2.0,
            ),
            FormControlType.CHECKBOX: CoordinateVarianceProfile(
                inner_margin_ratio=0.08,
                sigma_factor_x=0.12,
                sigma_factor_y=0.12,
                min_margin_px=0.5,
            ),
            FormControlType.RADIO: CoordinateVarianceProfile(
                inner_margin_ratio=0.08,
                sigma_factor_x=0.12,
                sigma_factor_y=0.12,
                min_margin_px=0.5,
            ),
            FormControlType.SELECT: CoordinateVarianceProfile(
                inner_margin_ratio=0.12,
                sigma_factor_x=0.20,
                sigma_factor_y=0.18,
                bias_x=-0.18,  # Bias leftward away from dropdown chevron
                min_margin_px=2.0,
            ),
            FormControlType.SLIDER: CoordinateVarianceProfile(
                inner_margin_ratio=0.05,
                sigma_factor_x=0.30,
                sigma_factor_y=0.10,
                min_margin_px=0.5,
            ),
            FormControlType.TOGGLE: CoordinateVarianceProfile(
                inner_margin_ratio=0.10,
                sigma_factor_x=0.15,
                sigma_factor_y=0.15,
                min_margin_px=1.0,
            ),
            FormControlType.GENERIC: CoordinateVarianceProfile(
                inner_margin_ratio=0.15,
                sigma_factor_x=0.20,
                sigma_factor_y=0.20,
                min_margin_px=1.0,
            ),
        }

    def get_profile(self, control_type: FormControlType) -> CoordinateVarianceProfile:
        return self._profiles.get(control_type, self._profiles[FormControlType.GENERIC])

    def set_profile(
        self,
        control_type: FormControlType,
        profile: CoordinateVarianceProfile,
    ) -> None:
        self._profiles[control_type] = profile

    def calculate_inner_target_rect(
        self,
        box: BoundingBox,
        control_type: FormControlType,
    ) -> BoundingBox:
        """Derive safe inner interactive zone avoiding outer boundary margins."""
        profile = self.get_profile(control_type)

        margin_x = max(profile.min_margin_px, box.width * profile.inner_margin_ratio)
        margin_y = max(profile.min_margin_px, box.height * profile.inner_margin_ratio)

        # Ensure margins do not exceed element extent
        if 2.0 * margin_x >= box.width:
            margin_x = box.width * 0.25
        if 2.0 * margin_y >= box.height:
            margin_y = box.height * 0.25

        inner_w = box.width - (2.0 * margin_x)
        inner_h = box.height - (2.0 * margin_y)

        # Apply bias offset if configured
        shift_x = (box.width * profile.bias_x * 0.5) if profile.bias_x else 0.0
        shift_y = (box.height * profile.bias_y * 0.5) if profile.bias_y else 0.0

        inner_x = box.x + margin_x + shift_x
        inner_y = box.y + margin_y + shift_y

        # Keep inner box clamped within parent box bounds
        inner_x = max(box.x, min(inner_x, box.right - inner_w))
        inner_y = max(box.y, min(inner_y, box.bottom - inner_h))

        return BoundingBox(inner_x, inner_y, inner_w, inner_h)

    def generate_coordinate(
        self,
        box: BoundingBox,
        control_type: FormControlType = FormControlType.GENERIC,
        *,
        rng: random.Random | None = None,
    ) -> tuple[float, float]:
        """Compute an adaptive coordinate sample respecting boundary invariants."""
        r = rng or random.Random()
        profile = self.get_profile(control_type)
        inner_box = self.calculate_inner_target_rect(box, control_type)

        sigma_x = max(0.5, inner_box.width * profile.sigma_factor_x)
        sigma_y = max(0.5, inner_box.height * profile.sigma_factor_y)

        raw_x = r.gauss(inner_box.cx, sigma_x)
        raw_y = r.gauss(inner_box.cy, sigma_y)

        # Strictly clamp to half-open box limits [x, right) and [y, bottom)
        safe_right = math.nextafter(box.right, box.x)
        safe_bottom = math.nextafter(box.bottom, box.y)

        px = max(box.x, min(raw_x, safe_right))
        py = max(box.y, min(raw_y, safe_bottom))

        return px, py

    def sample(
        self,
        box: BoundingBox,
        control_type: FormControlType = FormControlType.GENERIC,
        *,
        rng: random.Random | None = None,
    ) -> VarianceSample:
        """Sample coordinate and produce comprehensive telemetry metadata."""
        px, py = self.generate_coordinate(box, control_type, rng=rng)
        inner_box = self.calculate_inner_target_rect(box, control_type)

        dx = px - box.cx
        dy = py - box.cy
        distance = math.hypot(dx, dy)
        norm_x = (dx / (box.width / 2.0)) if box.width > 0 else 0.0
        norm_y = (dy / (box.height / 2.0)) if box.height > 0 else 0.0

        return VarianceSample(
            x=px,
            y=py,
            control_type=control_type,
            distance_from_center=distance,
            normalized_offset=(norm_x, norm_y),
            inside_bounding_box=box.contains(px, py),
            inside_inner_zone=inner_box.contains(px, py),
        )

    def generate_trajectory(
        self,
        start: tuple[float, float],
        box: BoundingBox,
        control_type: FormControlType = FormControlType.GENERIC,
        steps: int = 10,
        *,
        rng: random.Random | None = None,
    ) -> list[tuple[float, float]]:
        """Generate intermediate waypoints with adaptive micro-variance toward target."""
        r = rng or random.Random()
        target_x, target_y = self.generate_coordinate(box, control_type, rng=r)

        trajectory: list[tuple[float, float]] = [start]
        start_x, start_y = start

        for i in range(1, steps):
            t = float(i) / float(steps)
            linear_x = start_x + (target_x - start_x) * t
            linear_y = start_y + (target_y - start_y) * t

            # Micro-jitter envelope decreases as approaching target
            envelope = math.sin(t * math.pi) * 2.0
            jitter_x = r.gauss(0.0, 0.4) * envelope
            jitter_y = r.gauss(0.0, 0.4) * envelope

            trajectory.append((linear_x + jitter_x, linear_y + jitter_y))

        trajectory.append((target_x, target_y))
        return trajectory

    def evaluate_distribution(
        self,
        box: BoundingBox,
        control_type: FormControlType,
        num_samples: int = 1000,
        *,
        rng: random.Random | None = None,
    ) -> dict[str, float]:
        """Compute empirical distribution statistics over multiple draws."""
        r = rng or random.Random()
        samples_x: list[float] = []
        samples_y: list[float] = []
        containment_count = 0

        for _ in range(num_samples):
            px, py = self.generate_coordinate(box, control_type, rng=r)
            samples_x.append(px)
            samples_y.append(py)
            if box.contains(px, py):
                containment_count += 1

        mean_x = sum(samples_x) / num_samples
        mean_y = sum(samples_y) / num_samples
        var_x = sum((val - mean_x) ** 2 for val in samples_x) / num_samples
        var_y = sum((val - mean_y) ** 2 for val in samples_y) / num_samples

        return {
            "sample_count": float(num_samples),
            "mean_x": mean_x,
            "mean_y": mean_y,
            "std_x": math.sqrt(var_x),
            "std_y": math.sqrt(var_y),
            "min_x": min(samples_x),
            "max_x": max(samples_x),
            "min_y": min(samples_y),
            "max_y": max(samples_y),
            "containment_rate": containment_count / float(num_samples),
        }

    def adapt_profile_from_feedback(
        self,
        control_type: FormControlType,
        success_rate: float,
        edge_miss_count: int,
    ) -> CoordinateVarianceProfile:
        """Dynamically tune variance profile parameters based on hit feedback."""
        current = self.get_profile(control_type)

        new_margin = current.inner_margin_ratio
        new_sigma_x = current.sigma_factor_x
        new_sigma_y = current.sigma_factor_y

        if edge_miss_count > 0 or success_rate < 0.90:
            # Tighten dispersion and increase safe inner margin
            new_sigma_x = max(0.05, current.sigma_factor_x * 0.85)
            new_sigma_y = max(0.05, current.sigma_factor_y * 0.85)
            new_margin = min(0.35, current.inner_margin_ratio + 0.05)
        elif success_rate >= 0.99 and edge_miss_count == 0:
            # Relax slightly for better exploratory coverage
            new_sigma_x = min(0.35, current.sigma_factor_x * 1.05)
            new_sigma_y = min(0.35, current.sigma_factor_y * 1.05)

        adapted = CoordinateVarianceProfile(
            inner_margin_ratio=new_margin,
            sigma_factor_x=new_sigma_x,
            sigma_factor_y=new_sigma_y,
            bias_x=current.bias_x,
            bias_y=current.bias_y,
            min_margin_px=current.min_margin_px,
            skew_factor=current.skew_factor,
        )
        self.set_profile(control_type, adapted)
        return adapted

    adapt_profile = adapt_profile_from_feedback


_GLOBAL_ENGINE = AdaptiveInputVarianceEngine()


def get_adaptive_variance_engine() -> AdaptiveInputVarianceEngine:
    """Return the singleton AdaptiveInputVarianceEngine instance."""
    return _GLOBAL_ENGINE


def calculate_adaptive_coordinate(
    box: BoundingBox | tuple[float, float, float, float],
    control_type: str | FormControlType = FormControlType.GENERIC,
    *,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """Calculate adaptive coordinate within bounding box."""
    if isinstance(box, tuple):
        box_obj = BoundingBox(*box)
    else:
        box_obj = box

    if isinstance(control_type, str):
        try:
            ctype_enum = FormControlType(control_type.lower())
        except ValueError:
            ctype_enum = FormControlType.GENERIC
    elif isinstance(control_type, FormControlType):
        ctype_enum = control_type
    else:
        ctype_enum = FormControlType.GENERIC

    return _GLOBAL_ENGINE.generate_coordinate(box_obj, ctype_enum, rng=rng)


class VarianceState(str, Enum):
    """Three-state disposition for adaptive coordinate calculation (O1224)."""
    ADAPTIVE = "adaptive"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


def get_form_control_coordinate(
    box: Any,
    control_type: str | FormControlType = FormControlType.GENERIC,
    *,
    rng: random.Random | None = None,
) -> tuple[float, float, str]:
    """Calculate three-state adaptive form control coordinate.

    Returns:
        (x, y, state) where state is:
        - "adaptive": successfully calculated within safe inner zone with dispersion
        - "fallback": clamped to geometric box center on constraint failure
        - "unavailable": invalid, zero-dimension or detached box (O1224 class)
    """
    if not box:
        return 0.0, 0.0, VarianceState.UNAVAILABLE.value

    bx = by = bw = bh = 0.0
    try:
        if isinstance(box, dict):
            bx = float(box.get("x", 0.0))
            by = float(box.get("y", 0.0))
            bw = float(box.get("width", 0.0))
            bh = float(box.get("height", 0.0))
        elif isinstance(box, (tuple, list)) and len(box) >= 4:
            bx, by, bw, bh = float(box[0]), float(box[1]), float(box[2]), float(box[3])
        elif hasattr(box, "x") and hasattr(box, "width"):
            bx = float(box.x)
            by = float(box.y)
            bw = float(box.width)
            bh = float(box.height)
        else:
            return 0.0, 0.0, VarianceState.UNAVAILABLE.value

        if bw <= 0.0 or bh <= 0.0 or not math.isfinite(bx) or not math.isfinite(by):
            return 0.0, 0.0, VarianceState.UNAVAILABLE.value

        bbox = BoundingBox(bx, by, bw, bh)
        px, py = calculate_adaptive_coordinate(bbox, control_type, rng=rng)
        return px, py, VarianceState.ADAPTIVE.value
    except Exception:
        try:
            return float(bx + bw / 2.0), float(by + bh / 2.0), VarianceState.FALLBACK.value
        except Exception:
            return 0.0, 0.0, VarianceState.UNAVAILABLE.value

