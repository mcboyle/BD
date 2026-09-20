"""ingress_flow_controller -- response document classification and lane backpressure.

Row 953 (v3.66.1585): evaluate response MIME types and DOM root descriptors
for blocking state hierarchies (login walls, CAPTCHAs, maintenance pages,
redirect loops, rate limits, unexpected MIME types). Assert lane-level
backpressure pauses and emit structured health metrics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence


class BlockingCategory(Enum):
    PASSABLE = "passable"
    LOGIN_WALL = "login_wall"
    CAPTCHA = "captcha"
    MAINTENANCE = "maintenance"
    REDIRECT_LOOP = "redirect_loop"
    RATE_LIMITED = "rate_limited"
    UNEXPECTED_MIME = "unexpected_mime"


@dataclass(frozen=True)
class IngressDecision:
    is_blocking: bool
    category: BlockingCategory
    reason: str = ""


_LOGIN_PATTERNS = [
    re.compile(r"""<input[^>]+type\s*=\s*['"]?password""", re.I | re.S),
    re.compile(r"""<form[^>]*(?:login|signin|sign-in|auth)""", re.I | re.S),
]

_CAPTCHA_PATTERNS = [
    re.compile(r"""class\s*=\s*['"][^'"]*(?:g-recaptcha|h-captcha|captcha-container|cf-turnstile)""", re.I),
    re.compile(r"""<iframe[^>]+(?:recaptcha|hcaptcha|turnstile)""", re.I),
]

_MAINTENANCE_KEYWORDS = re.compile(
    r"(?:under\s+maintenance|service\s+(?:temporarily\s+)?unavailable|"
    r"maintenance\s+(?:mode|in\s+progress)|we.ll\s+be\s+back)",
    re.I,
)

_EXPECTED_DOCUMENT_MIMES = frozenset({
    "text/html", "application/xhtml+xml", "application/json",
    "text/plain", "text/xml", "application/xml",
})

_REDIRECT_THRESHOLD = 10


def classify_response(
    content_type: str,
    status_code: int,
    body: str,
    *,
    redirect_count: int = 0,
    expected_mime: Optional[str] = None,
) -> IngressDecision:
    if status_code == 429:
        return IngressDecision(True, BlockingCategory.RATE_LIMITED, "HTTP 429")

    if redirect_count >= _REDIRECT_THRESHOLD:
        return IngressDecision(
            True, BlockingCategory.REDIRECT_LOOP,
            f"{redirect_count} redirects",
        )

    if status_code == 503 and _MAINTENANCE_KEYWORDS.search(body or ""):
        return IngressDecision(True, BlockingCategory.MAINTENANCE, "503 + maintenance keywords")

    ct_base = (content_type or "").lower().split(";")[0].strip()

    if expected_mime and ct_base and ct_base != expected_mime.lower():
        if ct_base not in _EXPECTED_DOCUMENT_MIMES:
            return IngressDecision(
                True, BlockingCategory.UNEXPECTED_MIME,
                f"expected {expected_mime}, got {ct_base}",
            )

    if ct_base in ("text/html", "application/xhtml+xml") and body:
        for pat in _CAPTCHA_PATTERNS:
            if pat.search(body):
                return IngressDecision(True, BlockingCategory.CAPTCHA, "CAPTCHA element detected")

        for pat in _LOGIN_PATTERNS:
            if pat.search(body):
                return IngressDecision(True, BlockingCategory.LOGIN_WALL, "login form detected")

    return IngressDecision(False, BlockingCategory.PASSABLE)


@dataclass
class _LaneState:
    blocking_count: int = 0
    paused: bool = False
    categories: Dict[str, int] = field(default_factory=dict)


class IngressFlowController:
    def __init__(self, *, blocking_threshold: int = 5) -> None:
        self._threshold = blocking_threshold
        self._lanes: Dict[str, _LaneState] = {}
        self.on_backpressure: Optional[Callable[[str, dict], None]] = None

    def _lane(self, lane_id: str) -> _LaneState:
        if lane_id not in self._lanes:
            self._lanes[lane_id] = _LaneState()
        return self._lanes[lane_id]

    def record_blocking(self, lane_id: str, category: BlockingCategory) -> None:
        state = self._lane(lane_id)
        state.blocking_count += 1
        cat_val = category.value
        state.categories[cat_val] = state.categories.get(cat_val, 0) + 1

        if not state.paused and state.blocking_count >= self._threshold:
            state.paused = True
            if self.on_backpressure:
                self.on_backpressure(lane_id, self._lane_metrics(state))

    def is_lane_paused(self, lane_id: str) -> bool:
        state = self._lanes.get(lane_id)
        return state.paused if state else False

    def deschedule(
        self, lane_id: str, pending: Sequence[dict],
    ) -> List[dict]:
        if not self.is_lane_paused(lane_id):
            return []
        return list(pending)

    def resume_lane(self, lane_id: str) -> None:
        state = self._lanes.get(lane_id)
        if state:
            state.blocking_count = 0
            state.paused = False
            state.categories.clear()

    def get_health_metrics(self) -> dict:
        return {
            "lanes": {
                lid: self._lane_metrics(st) for lid, st in self._lanes.items()
            },
        }

    @staticmethod
    def _lane_metrics(state: _LaneState) -> dict:
        return {
            "blocking_count": state.blocking_count,
            "paused": state.paused,
            "categories": dict(state.categories),
        }
