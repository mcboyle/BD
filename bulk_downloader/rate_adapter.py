"""Per-domain Retry-After pacing without cross-lane blocking."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from email.utils import parsedate_to_datetime


# Longest wait one Retry-After may impose. A server (or a typo) asking for
# years must not park a domain for good; a day is past any real rate window.
MAX_RETRY_AFTER_S = 86400.0

_DELAY_SECONDS = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")


def parse_retry_after(value: str, *, now: float) -> float:
    """Seconds to wait per RFC 7231 s7.1.3: ``delay-seconds`` or an
    ``HTTP-date``.  Malformed, negative, or non-finite values yield 0 (retry
    at the caller's normal cadence) -- never a permanent block (row951 E3):
    the result is capped at ``MAX_RETRY_AFTER_S``.
    """
    text = str(value).strip()
    if _DELAY_SECONDS.match(text):
        # digits only: float() alone also reads "1_0", "1e300" and "inf"
        delay = float(text)
    else:
        try:
            delay = parsedate_to_datetime(text).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return 0.0
    if not math.isfinite(delay):
        return 0.0
    return min(MAX_RETRY_AFTER_S, max(0.0, delay))


class RateLimitAdapter:
    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    def observe(self, domain: str, status: int, headers: Mapping[str, str], *, now: float) -> float:
        """Record a 429 / 503 Retry-After deadline and return its delay;
        successful responses recover."""
        value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
        if status == 429 or (status == 503 and value is not None):
            delay = parse_retry_after("0" if value is None else value, now=now)
            self._until[domain] = now + delay
            return delay
        if status < 400:
            self._until.pop(domain, None)
        return 0

    def delay_for(self, domain: str, *, now: float) -> float:
        """Return remaining wait for this domain only, and retire elapsed limits."""
        remaining = self._until.get(domain, 0.0) - now
        if remaining <= 0:
            self._until.pop(domain, None)
            return 0
        return remaining
