"""queue_policy.py -- Phase 2 Cut 2.1: pure queue decision logic.

The DECISION layer for lanes + priorities + dependencies + dead-letter. Pure
functions over job dicts (no db, no runner, no I/O) so the ordering/gating rules
are unit-testable in isolation; the runner wires these into its dispatch/retry
path (the runtime dequeue itself is exercised on-stash, not in-sandbox).

A "job" here is the queue-row dict shape: at least
``{url, status, lane, priority, ord, depends_on}``. Missing keys default sensibly
so a partial/legacy row (pre-migration, no lane/depends_on) behaves as an
unconstrained default-lane normal-priority job.
"""
from __future__ import annotations

from typing import Dict, List

# Terminal statuses -- a job here is never dispatched again by the ready-set.
_TERMINAL = frozenset({"done", "dead_letter"})
# Statuses from which a dependency can NEVER become satisfied (it won't reach
# 'done'), so a dependent on one of these is permanently blocked.
_DEP_UNSATISFIABLE = frozenset({"dead_letter", "failed"})
# Priority rank: lower sorts first. Unknown priorities sort as 'normal'.
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


def should_dead_letter(retries, max_retries) -> bool:
    """True when a job has used up its retry budget and should move to the
    terminal dead-letter status instead of being retried again."""
    try:
        return int(retries) >= int(max_retries)
    except (TypeError, ValueError):
        return False


def _dep_key(job: dict) -> str:
    return str(job.get("depends_on") or "").strip()


def dependency_satisfied(job: dict, jobs_by_url: Dict[str, dict]) -> bool:
    """True if the job has no dependency, or the job it depends on is 'done'.
    A dependency that is missing or permanently gone (dead_letter/failed) is NOT
    satisfied (see dependency_blocked)."""
    dep = _dep_key(job)
    if not dep:
        return True
    d = jobs_by_url.get(dep)
    if d is None:
        return False
    return (d.get("status") or "") == "done"


def dependency_blocked(job: dict, jobs_by_url: Dict[str, dict]) -> bool:
    """True if the job's dependency can never be satisfied -- it points at an
    unknown job, or one in a permanently-unsatisfiable status (dead_letter/failed).
    A blocked job is surfaced to the operator rather than dispatched or retried."""
    dep = _dep_key(job)
    if not dep:
        return False
    d = jobs_by_url.get(dep)
    if d is None:
        return True
    return (d.get("status") or "") in _DEP_UNSATISFIABLE


def is_ready(job: dict, jobs_by_url: Dict[str, dict]) -> bool:
    """A job is READY to dispatch iff it is pending (non-terminal, not blocked)
    and its dependency (if any) is satisfied."""
    status = job.get("status") or "pending"
    if status in _TERMINAL:
        return False
    if status != "pending":
        return False  # running / stopped / needs_review are not dispatch-ready
    if dependency_blocked(job, jobs_by_url):
        return False
    return dependency_satisfied(job, jobs_by_url)


def _sort_key(job: dict):
    pr = _PRIORITY_RANK.get((job.get("priority") or "normal"), 1)
    try:
        ordv = int(job.get("ord") or 0)
    except (TypeError, ValueError):
        ordv = 0
    return (pr, ordv, str(job.get("url") or ""))


def order_ready_jobs(jobs) -> List[dict]:
    """Return the dispatch-ready jobs across all lanes, ordered by priority
    (high first) then ``ord`` then url. Accepts a dict-of-jobs or an iterable of
    job dicts. Excludes terminal, non-pending, blocked, and dependency-unmet jobs.
    """
    jobs_by_url, seq = _normalize(jobs)
    ready = [j for j in seq if is_ready(j, jobs_by_url)]
    ready.sort(key=_sort_key)
    return ready


def order_ready_jobs_by_lane(jobs) -> Dict[str, List[dict]]:
    """Same as order_ready_jobs but partitioned by lane: ``{lane: [job, ...]}``,
    each lane's list independently priority/ord-ordered. Lanes process
    independently, so this is what a lane-parallel dispatcher iterates."""
    ready = order_ready_jobs(jobs)
    out: Dict[str, List[dict]] = {}
    for j in ready:
        lane = (j.get("lane") or "default")
        out.setdefault(lane, []).append(j)
    return out


def blocked_jobs(jobs) -> List[dict]:
    """Jobs whose dependency can never be satisfied -- for operator surfacing."""
    jobs_by_url, seq = _normalize(jobs)
    return [j for j in seq
            if (j.get("status") or "pending") not in _TERMINAL
            and dependency_blocked(j, jobs_by_url)]


def _normalize(jobs):
    """Accept a dict keyed by url OR an iterable of job dicts; return
    (jobs_by_url, sequence)."""
    if isinstance(jobs, dict):
        seq = list(jobs.values())
    else:
        seq = list(jobs)
    by_url = {}
    for j in seq:
        u = j.get("url")
        if u is not None:
            by_url[u] = j
    return by_url, seq
