"""Resource quota budget and file-descriptor utilization gauge (row 991).

`dev_suite.introspection.process_info()` and `tools/stress_probe.py` both report the number of
open descriptors as a bare integer. A bare integer is not actionable: 900 descriptors is idle
under a 1,048,576 soft limit and one allocation from EMFILE under 1,024. This module supplies the
missing denominator -- the RLIMIT_NOFILE budget -- and the three derived numbers an operator
actually reads: how much of the quota is spent, how much headroom is left, and whether that
crosses a threshold worth acting on.

Two rules the arithmetic has to respect, because both are real states of a live process:
  * an UNLIMITED soft limit (RLIM_INFINITY) has no denominator. It is 0% used with no headroom
    figure, never a division by zero and never a false `critical`.
  * a count that could not be taken is `None`, never 0. Zero open descriptors would read as
    perfect health and is impossible for a running process, so an unreadable /proc reports
    `unknown` and lets the caller decide.
  * the SAME rule applies to the denominator, which is where the first cut of this row was
    refuted (correctness lens B8-B, 2026-09-22T09:28Z, finding A). A limit that getrlimit
    refused to hand over is UNKNOWN, and unknown is not the same as unlimited: treating it as
    unlimited made the gauge answer "ok, 0% used" about a budget it had never read -- a check
    that fails open on its own failure path. Three states, kept apart everywhere below:
    a real limit (a denominator), RLIM_INFINITY or a non-positive limit (no denominator), and
    None (not read; status "unknown", unlimited False, pct None, headroom None).

No `BD_` environment key is introduced: the budget is read from the kernel (getrlimit) and the
thresholds are module constants, so the pinned config-parity ratchet and the GUI manifest are
untouched by this row.
"""
from __future__ import annotations

import os
import resource

# Thresholds on percent-of-soft-limit consumed. Chosen so `warn` leaves a quarter of the budget
# to react in and `critical` still leaves a tenth -- enough headroom for the descriptors a
# shutdown path itself needs to open (log files, the sqlite journal).
WARN_PCT = 75.0
CRITICAL_PCT = 90.0

_MEASURE = object()   # sentinel: "take the reading now". Explicit None means "unavailable".


def count_open_fds():
    """Open descriptors held by this process, or None when the count cannot be taken."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return None


def is_unlimited(soft_limit) -> bool:
    """True when the soft limit imposes no budget at all (RLIM_INFINITY, or a non-positive
    limit, which cannot be a real denominator either).

    None is NOT unlimited -- it means the limit was never read. See ``limit_is_unknown``.
    """
    if soft_limit is None:
        return False
    return soft_limit == resource.RLIM_INFINITY or soft_limit <= 0


def limit_is_unknown(soft_limit) -> bool:
    """True when the budget could not be read at all, so no claim about health is available."""
    return soft_limit is None


def utilization_pct(open_fds, soft_limit):
    """Percent of the descriptor budget consumed, or None when the count is unavailable.

    An unlimited (or non-positive) soft limit yields 0.0: nothing is being consumed OUT OF a
    budget, because there is no budget. Reporting 0.0 rather than raising keeps this callable
    from a status route that must not fail. An UNREAD limit yields None, not 0.0 -- the route
    still does not fail, but it is told the gauge has nothing to report.
    """
    if open_fds is None or limit_is_unknown(soft_limit):
        return None
    if is_unlimited(soft_limit):
        return 0.0
    return round(100.0 * float(open_fds) / float(soft_limit), 2)


def quota_status(pct) -> str:
    """'unknown' | 'ok' | 'warn' | 'critical' for a utilization percentage."""
    if pct is None:
        return "unknown"
    if pct >= CRITICAL_PCT:
        return "critical"
    if pct >= WARN_PCT:
        return "warn"
    return "ok"


def headroom(open_fds, soft_limit):
    """Descriptors still allocatable under the soft limit; None when there is no budget or no
    count to subtract from it."""
    if open_fds is None or limit_is_unknown(soft_limit) or is_unlimited(soft_limit):
        return None
    return soft_limit - open_fds


def fd_quota_snapshot(open_fds=_MEASURE, soft_limit=_MEASURE, hard_limit=_MEASURE) -> dict:
    """One reading of the gauge. Never raises: a failed measurement is data.

    Every argument defaults to taking the reading now. Passing None explicitly models the
    UNREADABLE case -- an unreadable counter, or a budget getrlimit would not surrender -- and
    passing a number models a budget other than this process's own. The sentinel is what keeps
    "take it now" distinguishable from "it is not available"; before the B8-B refutation
    ``soft_limit=None`` meant both, which is how an unread limit came to be read as unlimited.
    """
    if open_fds is _MEASURE:
        open_fds = count_open_fds()
    if soft_limit is _MEASURE or hard_limit is _MEASURE:
        try:
            got_soft, got_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        except Exception:
            # The gauge still does not raise -- a status route depends on that -- but it now
            # reports the failure as a failure instead of as health.
            got_soft, got_hard = None, None
        soft_limit = got_soft if soft_limit is _MEASURE else soft_limit
        hard_limit = got_hard if hard_limit is _MEASURE else hard_limit
    pct = utilization_pct(open_fds, soft_limit)
    return {
        "open_fds": open_fds,
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "unlimited": is_unlimited(soft_limit),
        "limit_known": not limit_is_unknown(soft_limit),
        "utilization_pct": pct,
        "headroom": headroom(open_fds, soft_limit),
        "status": quota_status(pct),
        "warn_pct": WARN_PCT,
        "critical_pct": CRITICAL_PCT,
    }
