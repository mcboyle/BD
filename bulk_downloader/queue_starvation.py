"""queue_starvation -- Queue Starvation & Priority Inversion detector (row 990).

Pure analysis over one runner's dispatch state.  The runner keeps dispatch
order in ``runner.urls`` (front runs first) and per-job state in
``runner.jobs[url]``; ``set_priority``/``bulk_priority`` keep "high" jobs at
the front, but ``reorder_urls``, ``queue_upsert`` from the templates path and
resume/retry re-appends can leave a "high" pending job behind "normal"
pending jobs.  Nothing on /api/queue/v2 exposes that, nor how long a
pending job has been waiting.

Two findings:

  * inversion -- a pending "high" job with at least one pending non-high
    job ahead of it in dispatch order (running/terminal jobs ahead of it
    do not compete for the dispatcher and are not blockers);
  * starved   -- a pending job whose wait exceeds ``starvation_seconds``.
    Wait age is ``now - last_progress_at``: the creation stamp for a job
    that has never started (runner_queue load_urls), else the last byte
    advance / status change (runner._update_job).  /api/jobs/stuck
    excludes pending jobs on purpose; this covers them.

Reads the runner snapshot only; the caller holds ``runner._lock``.
"""
import time

DEFAULT_STARVATION_SECONDS = 1800

_HIGH = "high"


def _is_high(job):
    return (job.get("priority") or "normal") == _HIGH


def analyze_queue(urls, jobs, *, now=None, starvation_seconds=DEFAULT_STARVATION_SECONDS):
    """Return the starvation / inversion report for one runner.

    ``urls``: dispatch order (list of url).  ``jobs``: url -> job dict.
    """
    if now is None:
        now = time.time()
    pending = {u for u, j in jobs.items() if j.get("status") == "pending"}
    inversions = []
    starved = []
    oldest = None
    normal_ahead = 0        # pending non-high jobs seen so far in dispatch order
    first_normal = None
    seen = set()
    for pos, u in enumerate(urls):
        if u not in pending or u in seen:
            continue
        seen.add(u)
        j = jobs[u]
        if _is_high(j):
            if normal_ahead:
                inversions.append({
                    "url": u, "position": pos,
                    "blockers": normal_ahead,
                    "first_blocker": first_normal,
                })
        else:
            normal_ahead += 1
            if first_normal is None:
                first_normal = u
    for u in pending:
        j = jobs[u]
        stamp = j.get("last_progress_at")
        try:
            wait = max(0.0, now - float(stamp))
        except (TypeError, ValueError):
            continue
        if oldest is None or wait > oldest:
            oldest = wait
        if wait > starvation_seconds:
            starved.append({
                "url": u, "priority": j.get("priority") or "normal",
                "wait_seconds": wait,
                "position": urls.index(u) if u in seen else None,
            })
    starved.sort(key=lambda e: -e["wait_seconds"])
    orphaned = sorted(pending - seen)
    return {
        "pending_count": len(pending),
        "high_count": sum(1 for u in pending if _is_high(jobs[u])),
        "inversions": inversions,
        "inversion_count": len(inversions),
        "starved": starved,
        "starved_count": len(starved),
        "oldest_wait_seconds": oldest,
        "orphaned": orphaned,
        "starvation_seconds": starvation_seconds,
    }
