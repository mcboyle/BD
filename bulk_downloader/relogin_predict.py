"""F1.4 — predictive relogin decision (v3.66.218).

Decide whether to proactively re-login based on the per-site LEARNED session
lifetime, instead of a fixed cookie-age threshold. Pure and fail-safe:

  * With fewer than ``min_observations`` samples it returns ``None`` ("no
    opinion") so the caller keeps its existing fixed-threshold behaviour — a
    data-thin site behaves exactly as before.
  * It never raises on bad input; malformed values yield ``(None, reason)``.

This module is intentionally isolated and is NOT a release-guard file. It
*composes with*, rather than duplicates:

  * ``session_keeper.predict_next_expiry`` — the same median-lifetime idea; the
    keeper schedules its own opportunistic refresh. This module is the decision
    the *runner's* preemptive hook consults; the keeper may adopt it later.
  * ``admission.next_eligible_retry`` — window snapping stays the caller's job.
    A proactive relogin is not a retry backoff, so this module deliberately
    knows nothing about active windows.

The threshold is a *fraction* of the learned median lifetime (default 0.8): we
refresh at ~80% of a session's typical life, leaving a margin before the
reactive heartbeat-fail path would otherwise fire.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

# Don't trust a median formed from fewer than this many observed lifetimes.
MIN_OBSERVATIONS = 3
# Refresh at this fraction of the learned median lifetime.
DEFAULT_FRACTION = 0.8
# Clamp the configurable fraction to a sane band.
_FRACTION_FLOOR = 0.1
_FRACTION_CEIL = 1.0


def _median(values) -> Optional[float]:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) / 2.0


def predictive_relogin_due(
    age_sec,
    observations: Iterable,
    *,
    fraction: float = DEFAULT_FRACTION,
    min_observations: int = MIN_OBSERVATIONS,
) -> Tuple[Optional[bool], str]:
    """Return ``(due, reason)``.

    ``due`` is:
      * ``True``  — the current session age has reached the learned threshold;
                    the caller should relogin (subject to its own throttle).
      * ``False`` — there is enough data and we are NOT yet due; the caller
                    should trust this over any fixed threshold.
      * ``None``  — not enough data (or bad input) to form a prediction; the
                    caller should fall back to its fixed-threshold heuristic.

    ``age_sec`` is the time since the current session was established (seconds).
    ``observations`` is an iterable of past session lifetimes (seconds), e.g.
    ``db.session_lifetime_observations(site_id, account_idx)``.

    Never raises.
    """
    # Sanitize observations: drop None / non-positive / non-numeric.
    obs = []
    for x in (observations or []):
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            obs.append(v)

    need = max(1, int(min_observations) if _is_int_like(min_observations) else MIN_OBSERVATIONS)
    if len(obs) < need:
        return (None, f"insufficient observations ({len(obs)}<{need})")

    med = _median(obs)
    if not med or med <= 0:
        return (None, "no positive median")

    try:
        frac = float(fraction)
    except (TypeError, ValueError):
        frac = DEFAULT_FRACTION
    frac = min(_FRACTION_CEIL, max(_FRACTION_FLOOR, frac))

    try:
        age = float(age_sec)
    except (TypeError, ValueError):
        return (None, "bad age")
    if age < 0:
        return (None, "negative age")

    threshold = frac * med
    due = age >= threshold
    rel = ">=" if due else "<"
    return (
        due,
        f"age={age:.0f}s {rel} {frac:.2f}*median({med:.0f}s)={threshold:.0f}s n={len(obs)}",
    )


def _is_int_like(x) -> bool:
    try:
        int(x)
        return True
    except (TypeError, ValueError):
        return False
