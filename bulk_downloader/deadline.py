"""bulk_downloader.deadline -- row 1006: one run budget, propagated into the
socket tiers that can outlive it.

WHY THIS MODULE EXISTS. `run_budget.RunBudget.wall_s` is read from the site
config key `run_wall_budget_s` and, before this row, had no consumer: the only
entry point anything called was `is_over_mem_budget`. Every `httpx.Timeout` in
the tree is a fixed literal, so a run told it had five seconds would still wait
sixty for a single chunk read. The budget was configured, displayed, and inert.

WHAT IT OWNS, and deliberately nothing more: the arithmetic that turns a wall
budget into a bound on individual waits. It performs no I/O, reads no
environment, and adds no configuration surface -- `run_wall_budget_s` already
exists and is already a site-config key, so the config-parity ratchet and the
env inventory are untouched.

THE TWO WAYS THIS IS EASY TO GET WRONG, both of which have controls in
tests/test_row1006_tiered_deadline_propagating_socket_timeouts.py:

  * 0 MEANS UNCAPPED on every RunBudget field. Read naively as "a deadline of
    zero seconds" it would expire every run instantly, turning an unset option
    into a total outage. `from_wall_budget` returns None for a falsy budget and
    `clamp_tiers` passes its tiers through untouched when handed None.
  * CLAMPING EVERYONE to a small constant satisfies the capped case and breaks
    the uncapped path, which is most runs. `clamp_tiers` only ever LOWERS a
    tier, and only against a real remaining budget, so a budget wider than the
    caller's literals leaves them exactly as they were.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping, Optional

# A wait is never clamped below this. A tier of zero is not "hurry up", it is
# "fail before the connection is attempted", which would report a budget breach
# as a connect error and lose the reason. The floor is deliberately small
# enough to be a real failure signal and large enough to complete a local
# handshake.
MIN_TIER_S = 0.1


class DeadlineExceeded(RuntimeError):
    """The run's wall budget was already spent before this wait began."""


@dataclass(frozen=True)
class Deadline:
    """An absolute point on the monotonic clock, past which work should stop.

    Monotonic, not wall-clock: a deadline anchored to `time.time()` moves when
    the host clock is stepped, which is exactly the situation a long transfer
    is most likely to be running through.
    """

    expires_at: float

    @classmethod
    def from_wall_budget(cls, wall_s, *, now: Optional[float] = None):
        """A deadline `wall_s` seconds from now, or None when uncapped.

        Returns None for 0, None, a negative value or anything non-numeric --
        the same "0 means uncapped" contract RunBudget states for every field.
        """
        try:
            budget = float(wall_s)
        except (TypeError, ValueError):
            return None
        if budget <= 0:
            return None
        anchor = time.monotonic() if now is None else now
        return cls(expires_at=anchor + budget)

    def remaining(self, *, now: Optional[float] = None) -> float:
        """Seconds left, never negative."""
        current = time.monotonic() if now is None else now
        return max(0.0, self.expires_at - current)

    def expired(self, *, now: Optional[float] = None) -> bool:
        return self.remaining(now=now) <= 0.0


def clamp_tiers(tiers: Mapping[str, float], deadline: Optional[Deadline], *,
                now: Optional[float] = None,
                floor: float = MIN_TIER_S) -> dict:
    """Lower each tier to the deadline's remaining budget. Never raise it.

    `deadline=None` (an uncapped run) returns the tiers unchanged, which is the
    path most runs take and the one an over-eager fix breaks first.
    """
    out = {key: float(value) for key, value in tiers.items()}
    if deadline is None:
        return out
    left = deadline.remaining(now=now)
    if left <= 0.0:
        raise DeadlineExceeded(
            "the run wall budget was spent before this request began")
    bound = max(floor, left)
    return {key: min(value, bound) for key, value in out.items()}


def tiers_for_config(cfg, tiers: Mapping[str, float], *,
                     deadline: Optional[Deadline] = None,
                     now: Optional[float] = None) -> dict:
    """`clamp_tiers` against the wall budget on a site config dict.

    The caller may pass an already-anchored `deadline` when it has one; the
    default anchors at the moment of the call, which bounds any single wait by
    the whole budget even where no run start is in scope. That is weaker than a
    true run-start anchor and is the honest limit of this seam: it cannot make
    a wait outlive the budget, but it does not know how much of the budget an
    earlier request already spent.
    """
    if deadline is None:
        from . import run_budget as _run_budget
        deadline = Deadline.from_wall_budget(
            _run_budget.from_config(cfg).wall_s, now=now)
    return clamp_tiers(tiers, deadline, now=now)
