"""v3.43.37: Per-URL retry policy with failure classification.

The v3.20-era auto_retry system uses a fixed schedule (default
[1h, 4h, 24h]) applied uniformly to every failure. This wastes
time on the most common case (transient network blips that resolve
in seconds) AND burns cycles on the rarest (permanent 404s that
won't ever resolve).

This module replaces the uniform schedule with **failure
classification + class-specific backoff**:

  - **transient** — network, 5xx, timeout. Most common. Exponential
    backoff starting at 30s: 30s, 1m, 2m, 4m, 8m, 15m. 6 attempts.
  - **rate_limited** — 429, "too many requests" patterns. Honor
    server-provided `Retry-After` header if present; otherwise 5m
    base with exponential: 5m, 10m, 20m, 40m, 1h, 2h. 10 attempts.
  - **auth** — 401/403, login required. Wait for the next session
    keepalive to relogin; cap at 3 attempts before giving up.
  - **permanent** — 404, 410, "video not found", invalid URL.
    Skip retries entirely; mark as failed forever.

## Why per-class

The current uniform schedule is wrong for every class:

  - 30 seconds is right for a transient blip; 1 hour is wasteful
  - 1 hour is right for rate limits; 30 seconds gets you banned
  - Auth needs a relogin between attempts, not just time
  - Permanent never retry; 24h+24h+24h is wasted scans

## Jitter

All non-zero delays get ±15% jitter. Without it, 3 sites running
on the same schedule (typical for sister sites sharing CDN) retry
at the exact same minute, which compounds the original failure.

## Adaptive max_attempts

Hard cap differs by class — a transient failure stops trying after
6 because if it's STILL failing after 30s+60s+...+15m it's probably
not actually transient. Rate-limit accepts up to 10 because some
sites have rolling daily limits and 10 attempts at 5m-2h spans
the whole day.

## Server Retry-After header

When the server sends `Retry-After`, the runner can pass it
explicitly via `classify_failure(..., retry_after_header=N)`. We
use N seconds as the next delay instead of the class backoff —
respect what the server told us.

## Per-URL state

Each URL's retry state is persisted in the queue row's `retries`
column (existing) plus a new `failure_class` column. We don't add
NEW persistent fields beyond that — the retry_at timestamp comes
from `retry_after` which already exists, and `attempt` is just
`retries`.
"""
from __future__ import annotations

import random
from typing import Optional


# Failure-class definitions. Each class has:
#   base_delay_s: starting backoff
#   max_delay_s:  upper bound to clamp at
#   factor:       exponential multiplier
#   max_attempts: hard cap
#   jitter_pct:   randomization to avoid synchronized retries

CLASSES = {
    "transient": {
        "base_delay_s": 30,
        "max_delay_s": 900,        # 15 min
        "factor": 2.0,
        "max_attempts": 6,
        "jitter_pct": 0.15,
    },
    "rate_limited": {
        "base_delay_s": 300,       # 5 min
        "max_delay_s": 7200,       # 2 h
        "factor": 2.0,
        "max_attempts": 10,
        "jitter_pct": 0.15,
    },
    "auth": {
        # Auth retries wait for the session keeper to do its job.
        # Short delays + few attempts — we don't want to hammer
        # login pages.
        "base_delay_s": 120,       # 2 min
        "max_delay_s": 600,        # 10 min
        "factor": 2.0,
        "max_attempts": 3,
        "jitter_pct": 0.15,
    },
    "permanent": {
        # No retry — set base to 0 + max_attempts to 0 so the
        # scheduler immediately gives up
        "base_delay_s": 0,
        "max_delay_s": 0,
        "factor": 1.0,
        "max_attempts": 0,
        "jitter_pct": 0,
    },
}


# Classification rules. Order matters — first match wins. Patterns
# are case-insensitive substring matches against the failure
# message; status_code matches are exact.

# Permanent: don't retry
_PERMANENT_STATUS = {404, 410}
_PERMANENT_PATTERNS = [
    "404 not found",
    "410 gone",
    "video not found",
    "page not found",
    "video has been removed",
    "removed by uploader",
    "this video is private",
    "no such file",
    "invalid url",
    "unsupported url",
]

# Auth: needs relogin, not just waiting
_AUTH_STATUS = {401, 403}
_AUTH_PATTERNS = [
    "401 unauthorized",
    "403 forbidden",
    "login required",
    "session expired",
    "cookies expired",
    "please log in",
    "must be a member",
    "authentication required",
    "cookie validation failed",
]

# Rate-limited: server explicitly throttling us
_RATE_STATUS = {429}
_RATE_PATTERNS = [
    "429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "slow down",
    "try again later",
    "you are being throttled",
]

# Transient — everything else that looks recoverable: 5xx, timeouts,
# connection resets. This is the default class for unrecognized
# errors (be optimistic: retry on the assumption it's transient).
_TRANSIENT_STATUS = {500, 502, 503, 504, 522, 524}
_TRANSIENT_PATTERNS = [
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "connection aborted",
    "broken pipe",
    "temporary failure",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "ssl",
    "network",
]


def classify_failure(message: str = "",
                       status_code: Optional[int] = None) -> str:
    """Categorize a failure into one of four classes.

    Args:
      message: human-readable error string (case-insensitive match)
      status_code: HTTP status if known

    Returns: "transient" | "rate_limited" | "auth" | "permanent"

    Precedence:
      1. Status code (most reliable signal)
      2. Pattern match against message
      3. Default: transient (be optimistic about retries)

    The default of "transient" is deliberate — an unrecognized error
    is more likely to be a transient network/CDN issue than a
    permanent problem, and a wrong "transient" classification just
    means we retry up to 6 times, which is cheap. A wrong "permanent"
    classification means we never retry, which silently loses URLs.
    """
    # Defensive: callers might pass non-string (None, a dict from a
    # JSON error response, etc.). Coerce to empty string rather than
    # crashing — the caller's hot path can't afford exceptions.
    if not isinstance(message, str):
        msg = ""
    else:
        msg = message.lower()
    # Defensive: status_code might be a string from a JSON-
    # deserialized error response. Coerce to int or None.
    sc = status_code
    if sc is not None and not isinstance(sc, int):
        try:
            sc = int(sc)
        except (TypeError, ValueError):
            sc = None

    # Status code takes precedence — explicit signal from the server
    if sc is not None:
        if sc in _PERMANENT_STATUS:
            return "permanent"
        if sc in _AUTH_STATUS:
            return "auth"
        if sc in _RATE_STATUS:
            return "rate_limited"
        if sc in _TRANSIENT_STATUS:
            return "transient"
        # Other 4xx codes that aren't explicitly auth/rate-limit are
        # probably permanent (bad request, gone, etc.) — don't waste
        # retries
        if 400 <= sc < 500:
            return "permanent"
        # 5xx not in the explicit list — be cautious, treat as transient
        if 500 <= sc < 600:
            return "transient"

    # No status code — pattern-match the message
    for p in _PERMANENT_PATTERNS:
        if p in msg:
            return "permanent"
    for p in _AUTH_PATTERNS:
        if p in msg:
            return "auth"
    for p in _RATE_PATTERNS:
        if p in msg:
            return "rate_limited"
    for p in _TRANSIENT_PATTERNS:
        if p in msg:
            return "transient"
    # Default: transient. Better to retry an unknown error a few
    # times than to silently lose URLs.
    return "transient"


def compute_next_delay(failure_class: str,
                         attempt: int,
                         retry_after_header: Optional[int] = None,
                         apply_jitter: bool = True) -> int:
    """How many seconds to wait before the next retry attempt.

    Args:
      failure_class: from classify_failure()
      attempt: 0 for first retry, 1 for second, etc. (so the FIRST
                attempt after the original failure is attempt=0)
      retry_after_header: server's Retry-After value in seconds.
        When present AND the class is rate_limited, this overrides
        the class backoff.
      apply_jitter: True in production; False for unit tests so
        results are deterministic

    Returns: delay in seconds. 0 means "don't retry".
    """
    cfg = CLASSES.get(failure_class) or CLASSES["transient"]

    # Defensive: attempt might be None or a string from a corrupted
    # job dict. Coerce to a non-negative int.
    try:
        attempt = max(0, int(attempt))
    except (TypeError, ValueError):
        attempt = 0

    # Permanent never retries
    if cfg["max_attempts"] == 0:
        return 0
    # Past the attempt cap → no more retries
    if attempt >= cfg["max_attempts"]:
        return 0

    # Special case: rate-limited with server-provided Retry-After.
    # The server knows when its window resets; trust it.
    if (failure_class == "rate_limited"
            and retry_after_header is not None):
        # Defensive: coerce to int. A string from a header parse
        # is the realistic case.
        try:
            ra = int(retry_after_header)
        except (TypeError, ValueError):
            ra = 0
        if ra > 0:
            delay = min(ra, cfg["max_delay_s"])
        else:
            # Negative or zero — fall through to class backoff
            delay = int(cfg["base_delay_s"] * (cfg["factor"] ** attempt))
            delay = min(delay, cfg["max_delay_s"])
            delay = max(delay, cfg["base_delay_s"])
    else:
        # Exponential backoff: base * (factor ^ attempt), clamped
        # to max_delay_s
        delay = int(cfg["base_delay_s"] * (cfg["factor"] ** attempt))
        delay = min(delay, cfg["max_delay_s"])
        delay = max(delay, cfg["base_delay_s"])

    # Jitter — avoids synchronized retries across sites
    if apply_jitter and cfg["jitter_pct"] > 0:
        jit = delay * cfg["jitter_pct"]
        delay = int(delay + random.uniform(-jit, jit))
        # Floor at 1s so we never produce 0 (which means "don't retry")
        delay = max(1, delay)

    return delay


def should_retry(failure_class: str, attempt: int) -> bool:
    """Cheap check: are we within attempt budget for this class?
    Lets the runner skip the next_delay computation entirely when
    we know the answer is "give up"."""
    cfg = CLASSES.get(failure_class) or CLASSES["transient"]
    return attempt < cfg["max_attempts"]


def parse_retry_after(header_value) -> Optional[int]:
    """Parse the value of an HTTP Retry-After response header.
    The spec allows two forms:
      - Integer seconds: "120"
      - HTTP-date: "Wed, 21 Oct 2025 07:28:00 GMT"

    Returns: seconds to wait, or None if the header is missing or
    malformed.

    For HTTP-date form, we compute (header_date - now). Clamped to
    sane bounds [0, 86400] — a server claiming to send Retry-After
    7 days from now is broken; treat as missing."""
    if header_value is None:
        return None
    s = str(header_value).strip()
    if not s:
        return None
    # Integer form
    try:
        n = int(s)
        if 0 <= n <= 86400:
            return n
        return None
    except (TypeError, ValueError):
        pass
    # HTTP-date form
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        if 0 <= delta <= 86400:
            return int(delta)
    except Exception:
        pass
    return None


def get_class_config(failure_class: str) -> dict:
    """Public accessor for class config — used by the UI to display
    the policy and by tests to assert configuration."""
    return dict(CLASSES.get(failure_class) or CLASSES["transient"])


def get_all_classes() -> list[str]:
    """All known failure class names."""
    return list(CLASSES.keys())
