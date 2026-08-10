"""Four producers put a job into a counted state without a countable date.

THE DEFECT. Four day-window consumers count jobs whose status is done, failed
or needs_review AND whose ``ts_iso`` starts with today's LOCAL ``%Y-%m-%d``
(app.py:3912, app_dashboard.py:66, app_dashboard.py:203, app_queue.py:229).
CUT #31 added ``ts_iso`` and CUT #40 repointed the consumers onto it, but both
cuts fixed the READ side. Four producers still put a job into one of those
statuses without ever writing the field, so those jobs are counted by NONE of
the consumers:

    app_sites_queue.py:542  api_jobs_mark        writes only ``ts`` (HH:MM:SS)
    app_sites_queue.py:586  api_jobs_bulk_mark   writes only ``ts``
    runner_queue.py:303     load_urls, pre_done  writes only ``ts``
    runner_teach.py:351     _handle_auto_teach_check

The fourth is the subtle one and was missing from the register this cut came
from. It sets ``"ts": ""`` directly and then calls ``_update_job(...,
_memory_already_updated=True)``, which is exactly the flag that SKIPS the
central stamp at runner.py:1646-1660 -- so the one writer that would have added
``ts_iso`` is deliberately bypassed. A register said three; the measured answer
is four. CLAUDE.md section 1: re-derive, never inherit.

Effect is an UNDER-COUNT, not always-0. Jobs finished through the normal
transport path do carry ``ts_iso`` (runner.py:1658) and do count. Do not
restate this as "always 0" -- that was #40a's causal clause and it was never
true.

THE SEDUCTIVE WRONG FIX is on the consumer side:
``j.get("ts_iso","") or today_iso`` -- "the key is missing on some paths, so
default it to today". That makes every ts_iso-less job, including ones marked
done weeks ago, count as completed today forever.
test_cut40_dashboard_today_iso.py::test_job_with_no_ts_iso_counts_zero (G4)
pins that boundary and stays green here, because this cut changes PRODUCERS
only. If a later edit makes G4 fail, the fix went in the wrong place.

WHY THE PRODUCER ASSERTIONS ARE DIRECT AND THE CONSUMER ONE IS NARROW.
Three of the four consumers wrap their per-runner loop in a bare ``except``
that silently drops the whole runner (app.py:3906/3919, app_dashboard.py:194/213,
app_queue.py:231). A stand-in missing one attribute therefore raises, is
swallowed, and the counter reads 0 on the FIXED tree too -- a mislabelled RED
that can never go green. So the producer tests assert on the job dict itself,
where nothing can be swallowed, and the single end-to-end test goes through
``app_dashboard.py:61-71``, the ONLY consumer with no guard.

NOT IN SCOPE, and deliberately not asserted here:
  * runner_queue.py:106 stamps ``ts_iso`` from sqlite ``ts_updated``, which
    db.py writes with ``strftime(...,'now')`` = UTC, while every consumer
    compares a LOCAL date. Rehydrated jobs are therefore compared on the wrong
    clock on any non-UTC host. Confirmed real, separately filed; this cut does
    not touch it and adding LOCAL stamps at the producer sites neither helps
    nor worsens it.
  * The consumers do not share a denominator either: app_dashboard.py:203
    counts only done and failed, app_queue.py:229 only done, while app.py:3912
    and app_dashboard.py:66 count all three. ``needs_review`` is invisible to
    two of four regardless of ``ts_iso``. Also separately filed.

run_tests.py conventions: repo root from __file__; no pytest builtins beyond
the shared fixtures this suite already uses.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

_URL = "https://example.invalid/cut41.mp4"

# A full LOCAL ISO stamp, the shape runner.py:1658 already writes.
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _today():
    return time.strftime("%Y-%m-%d")


def _assert_countable(job, where):
    """The property every consumer actually requires, asserted as one thing.

    Not "has a ts_iso key" -- a key holding an HH:MM:SS value would satisfy
    that and still be uncountable, which is the original defect wearing a
    different hat. The subject is date-comparability against today.
    """
    ts_iso = job.get("ts_iso", "")
    assert ts_iso, f"{where}: no ts_iso at all; job={job!r}"
    assert _ISO_RE.fullmatch(ts_iso), (
        f"{where}: ts_iso={ts_iso!r} is not a full LOCAL ISO stamp, so it "
        f"cannot be compared against a %Y-%m-%d prefix")
    assert ts_iso.startswith(_today()), (
        f"{where}: ts_iso={ts_iso!r} does not start with today ({_today()}), "
        f"so no day-window consumer will count this job")


@pytest.fixture
def runner_db(clean_workdir):
    """Isolated BD home WITH the sqlite tables created.

    SiteRunner touches the queue table on construct, so without db_init() the
    test dies on `no such table: queue` -- a failure for the wrong reason that
    proves nothing about the defect.
    """
    from bulk_downloader.db import db_init
    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    return clean_workdir


class _MarkRunner:
    """The exact surface api_jobs_mark / api_jobs_bulk_mark dereference.

    _lock, jobs, log_event. Both endpoints also call queue_upsert /
    queue_bulk_mark, but each is inside its own try/except, so a DB-less run
    exercises the in-memory mutation this cut is about without dying.
    """

    def __init__(self, jobs=None):
        self._lock = threading.Lock()
        self.jobs = jobs if jobs is not None else {}
        self.events = []

    def log_event(self, *a, **k):
        self.events.append((a, k))


def _register(sid, runner):
    from bulk_downloader import app_state as st
    st.s_cfg[sid] = {"name": sid}
    st.runners[sid] = runner

    def _cleanup():
        st.runners.pop(sid, None)
        st.s_cfg.pop(sid, None)
    return _cleanup


def _post(path, payload):
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")                       # warm the bd_session cookie
    tok = (client.get("/api/csrf").get_json() or {}).get("csrf_token", "")
    return client.post(path, json=payload, headers={"X-CSRF-Token": tok})


# ── P1/P2: the two manual-mark endpoints ────────────────────────────────────

@pytest.mark.parametrize("status", ["done", "failed", "needs_review"])
def test_api_jobs_mark_writes_a_countable_stamp(status):
    """RED. api_jobs_mark writes only the display `ts`."""
    r = _MarkRunner({_URL: {"status": "pending", "message": ""}})
    cleanup = _register("cut41_mark", r)
    try:
        resp = _post("/api/sites/cut41_mark/jobs/mark",
                     {"url": _URL, "status": status})
        assert resp.status_code == 200, (resp.status_code, resp.get_data(as_text=True))
        job = r.jobs[_URL]
        assert job["status"] == status, job
        _assert_countable(job, f"api_jobs_mark({status})")
    finally:
        cleanup()


@pytest.mark.parametrize("status", ["done", "failed", "needs_review"])
def test_api_jobs_bulk_mark_writes_a_countable_stamp(status):
    """RED. api_jobs_bulk_mark writes only the display `ts`."""
    urls = [_URL, _URL + "?2"]
    r = _MarkRunner({u: {"status": "pending", "message": ""} for u in urls})
    cleanup = _register("cut41_bulk", r)
    try:
        resp = _post("/api/sites/cut41_bulk/jobs/bulk_mark",
                     {"urls": urls, "status": status})
        assert resp.status_code == 200, (resp.status_code, resp.get_data(as_text=True))
        for u in urls:
            assert r.jobs[u]["status"] == status, r.jobs[u]
            _assert_countable(r.jobs[u], f"api_jobs_bulk_mark({status}) {u}")
    finally:
        cleanup()


# ── P3: the already-on-disk enqueue ─────────────────────────────────────────

def test_load_urls_pre_done_writes_a_countable_stamp(runner_db):
    """RED. runner_queue.py:303 stamps only `ts` on the pre_done arm.

    Drives the REAL pre_done branch: a file already present in download_dir
    makes load_urls enqueue the URL as already-done. Asserting on the pending
    branch instead would exercise a path this cut does not change.
    """
    from bulk_downloader.runner import SiteRunner
    dl = runner_db / "cut41_dl"
    dl.mkdir(exist_ok=True)
    name = "cut41clip"
    (dl / f"{name}.mp4").write_bytes(b"\0" * 2048)

    # The URL's last path segment is compared against the file STEM, and the
    # extension is stripped only for .html/.php/.htm/.aspx (runner_queue.py:285).
    # So a ".mp4" URL can never match "clip.mp4"'s stem "clip" -- the URL must
    # carry no extension for the pre_done arm to fire at all.
    r = SiteRunner("cut41_pre", {"name": "cut41_pre", "download_dir": str(dl)})
    url = f"https://example.invalid/{name}"
    r.load_urls([url], folder_scan=True)

    job = r.jobs.get(url)
    assert job is not None, f"url not enqueued; jobs={list(r.jobs)!r}"
    if job.get("status") != "done":
        pytest.skip(
            "folder_scan did not pre-mark this URL done on this host, so the "
            "pre_done arm was not exercised and the result would be UNKNOWN")
    _assert_countable(job, "load_urls(pre_done)")


# ── P4: the auto-teach path that bypasses the central stamp ─────────────────

def test_auto_teach_needs_review_writes_a_countable_stamp(runner_db):
    """RED. runner_teach.py:351 sets needs_review, then _update_job with
    _memory_already_updated=True -- the flag that skips runner.py:1658.

    Calls the REAL `_handle_auto_teach_check`. An earlier version of this test
    replicated the mutation inline and then asserted on its own copy -- so its
    subject was the test, not the source, and it would have stayed red after a
    correct fix and green after a wrong one. No browser is needed: the method
    short-circuits to the needs_review branch when the site has no learned
    download selectors, which is the first-run condition it exists to serve.
    """
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("cut41_teach", {"name": "cut41_teach",
                                   "auto_teach_first_run": True})
    r.jobs[_URL] = {"status": "pending", "message": ""}

    handled = r._handle_auto_teach_check(_URL, r.jobs[_URL])
    assert handled, (
        "_handle_auto_teach_check declined to handle the URL, so the "
        "needs_review branch never ran and this test measured nothing "
        "(UNKNOWN fails)")

    job = r.jobs[_URL]
    assert job["status"] == "needs_review", job
    _assert_countable(job, "auto_teach needs_review")


# ── E2E: the one consumer with no swallowing guard ──────────────────────────

def test_a_marked_job_is_counted_by_the_dashboard():
    """End-to-end through app_dashboard.py:61-71, the only unguarded consumer.

    app.py:3906, app_dashboard.py:194 and app_queue.py:231 all swallow, so a
    stand-in missing an attribute would read 0 on the FIXED tree there too.
    This consumer raises instead, so a 0 here is the defect and not the fake.
    """
    class _FullRunner(_MarkRunner):
        def state(self):
            return "idle"

        def is_rate_limited(self):
            return False

        def _current_throughput_bps(self):
            return 0.0

        def active_worker_count(self):
            return 0

    r = _FullRunner({_URL: {"status": "pending", "message": ""}})
    r._recent_per_min = 0
    r.cookies = []

    # PIN THE FLEET to exactly this test's runner. /api/dashboard's today.done
    # iterates EVERY registered runner's jobs (app_dashboard.py:55-69), so an
    # absolute `== 1` was really an assertion that no earlier file in this
    # process left a runner holding a done job stamped today. Measured in the
    # v3.66.998 whole-tree -n 4 sweeps: a worker whose app had restored 3077
    # jobs from another file's residue read done == 2 -- on PRISTINE source
    # too, so this predates the lane work. Same class as test_u30's
    # empty-fleet fix: assert over the state the test is ABOUT, not over
    # whatever the process accumulated.
    from bulk_downloader import app_state as st
    saved_runners = dict(st.runners)
    saved_s_cfg = dict(st.s_cfg)
    st.runners.clear()
    st.s_cfg.clear()
    cleanup = _register("cut41_e2e", r)
    try:
        resp = _post("/api/sites/cut41_e2e/jobs/mark",
                     {"url": _URL, "status": "done"})
        assert resp.status_code == 200, resp.get_data(as_text=True)

        from bulk_downloader import app as a
        body = a.app.test_client().get("/api/dashboard").get_json() or {}
        today = body.get("today") or {}
        assert today.get("done") == 1, (
            "a job marked done through the API is not counted by "
            f"GET /api/dashboard: today={today!r} job={r.jobs[_URL]!r}")
    finally:
        cleanup()
        st.runners.clear()
        st.runners.update(saved_runners)
        st.s_cfg.clear()
        st.s_cfg.update(saved_s_cfg)
