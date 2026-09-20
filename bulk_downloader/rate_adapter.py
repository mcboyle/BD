"""Per-domain Retry-After pacing without cross-lane blocking."""
from __future__ import annotations

import math
from collections.abc import Mapping
from email.utils import parsedate_to_datetime


def parse_retry_after(value: str, *, now: float) -> float:
    """Seconds to wait per RFC 7231 s7.1.3: ``delay-seconds`` or an
    ``HTTP-date``.  Malformed, negative, or non-finite values yield 0 (retry
    at the caller's normal cadence) -- never a permanent block (row951 E3).
    """
    text = str(value).strip()
    try:
        delay = float(text)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(text).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return 0.0
    if not math.isfinite(delay):
        return 0.0
    return max(0.0, delay)


class RateLimitAdapter:
    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    def observe(self, domain: str, status: int, headers: Mapping[str, str], *, now: float) -> float:
        """Record a 429 deadline and return its delay; successful responses recover."""
        if status != 429:
            self._until.pop(domain, None)
            return 0
        value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "0")
        delay = parse_retry_after(value, now=now)
        self._until[domain] = now + delay
        return delay

    def delay_for(self, domain: str, *, now: float) -> float:
        """Return remaining wait for this domain only, and retire elapsed limits."""
        remaining = self._until.get(domain, 0.0) - now
        if remaining <= 0:
            self._until.pop(domain, None)
            return 0
        return remaining
