"""mass_import in-memory job map must stay bounded.

Memory/performance audit: finished mass-import jobs used to accumulate
in mass_import._jobs for the entire life of the process — a slow
unbounded-growth leak. _prune_jobs() now caps the retained finished
jobs; the full history still lives in the mass_imports DB table, so
list_recent() is unaffected. Running jobs are never evicted.
"""
from bulk_downloader import mass_import as _mi


def _reset_jobs():
    with _mi._jobs_lock:
        _mi._jobs.clear()


def test_prune_caps_finished_jobs_and_spares_running():
    _reset_jobs()
    try:
        cap = _mi._MAX_RETAINED_JOBS
        # far more finished jobs than the cap, plus two running ones
        for i in range(cap + 40):
            _mi._jobs[f"fin{i}"] = {"state": "done",
                                    "finished_ts": float(i)}
        _mi._jobs["run_a"] = {"state": "running"}
        _mi._jobs["run_b"] = {"state": "running"}
        with _mi._jobs_lock:
            _mi._prune_jobs()
        finished = [j for j in _mi._jobs.values()
                    if j.get("state") != "running"]
        running = [j for j in _mi._jobs.values()
                   if j.get("state") == "running"]
        assert len(finished) == cap, \
            f"finished jobs not capped: {len(finished)} != {cap}"
        assert len(running) == 2, "running jobs must never be evicted"
        # newest finished jobs survive; oldest are dropped
        assert f"fin{cap + 39}" in _mi._jobs, "newest finished evicted"
        assert "fin0" not in _mi._jobs, "oldest finished not evicted"
    finally:
        _reset_jobs()


def test_prune_is_a_noop_below_the_cap():
    _reset_jobs()
    try:
        for i in range(5):
            _mi._jobs[f"j{i}"] = {"state": "done",
                                  "finished_ts": float(i)}
        with _mi._jobs_lock:
            _mi._prune_jobs()
        assert len(_mi._jobs) == 5, "prune dropped jobs below the cap"
    finally:
        _reset_jobs()
