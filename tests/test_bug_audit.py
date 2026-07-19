"""Bug-audit regression repros (v3.66.195 audit).

Phase 1 = RED: these tests fail against the unmodified v3.66.195 tree and
must pass once the F1 / P6-A fixes land. They are the proof-of-bug + the
regression guard for the v3.66.196 cut.

Conventions: custom run_tests.py harness — zero-arg test functions, no
pytest fixtures, repo root derived from the package import (already on
sys.path under run_tests).
"""

import queue as _queue

from bulk_downloader.runner import SiteRunner
from bulk_downloader import deep_detect as _dd


# ─────────────────────────────────────────────────────────────────────────
# F1 — stop->start mid-queue permanently wedges _watch_done (Chromium leak)
#   runner.py L2411-2413: the start() drain does get_nowait() WITHOUT
#   task_done(), so leftover items never decrement queue.unfinished_tasks.
#   _watch_done's gate (L4913: `if unfinished_tasks == 0: break`) then
#   becomes unreachable after any stop->start cycle -> sentinels never sent
#   -> worker threads + Chromium stay alive indefinitely.
# Fix: extract the drain into SiteRunner._drain_url_queue(), repaying the
#   counter (get_nowait() + task_done(), ValueError-guarded).
# ─────────────────────────────────────────────────────────────────────────

def test_f1_inline_drain_leaves_unfinished_tasks_wedged():
    """Characterization: the CURRENT inline drain pattern (runner.py
    L2411-2413, replicated here verbatim) leaves unfinished_tasks > 0.
    This documents WHY _watch_done wedges; it passes on unmodified code."""
    q = _queue.Queue()
    for i in range(5):
        q.put(i)                       # prior run enqueued 5
    for _ in range(2):
        q.get(); q.task_done()         # worker fully processed 2
    # vvv exactly what start() does today (L2411-2413) vvv
    while not q.empty():
        try:
            q.get_nowait()             # <-- NO task_done(): the bug
        except _queue.Empty:
            break
    # 3 leftover items were drained but their unfinished_tasks never repaid
    assert q.unfinished_tasks == 3, (
        f"expected wedged counter == 3, got {q.unfinished_tasks}")


def test_f1_drain_method_repays_unfinished_tasks():
    """Fix contract (RED until F1 lands): SiteRunner._drain_url_queue()
    must repay unfinished_tasks to 0 so _watch_done's gate is reachable.
    Calls the REAL method bound to a minimal stub (it only touches
    self._url_queue) so the test exercises the fixed code without a
    DB-backed SiteRunner construction."""
    assert hasattr(SiteRunner, "_drain_url_queue"), (
        "F1 fix not present: SiteRunner._drain_url_queue() is missing — "
        "the start() drain (runner.py L2411-2413) still wedges "
        "unfinished_tasks")

    class _Stub:
        def __init__(self):
            self._url_queue = _queue.Queue()

    stub = _Stub()
    q = stub._url_queue
    for i in range(5):
        q.put(i)
    for _ in range(2):
        q.get(); q.task_done()
    assert q.unfinished_tasks == 3, "precondition: 3 leftover mid-queue"
    # Invoke the real method with self=stub.
    SiteRunner._drain_url_queue(stub)
    assert q.unfinished_tasks == 0, (
        f"drain left {q.unfinished_tasks} unfinished -> _watch_done "
        f"gate (runner.py L4913) never reached -> worker/Chromium leak")


# ─────────────────────────────────────────────────────────────────────────
# P6-A — score_download_link substring-matches ad-tracker hosts against the
#   WHOLE url (deep_detect.py L4510-4511: `if bad_host in host or
#   bad_host in lower`). A legit download URL that merely carries a tracker
#   name in a query param (utm_source=...) is penalized -90 and rejected.
# Fix: split AD_TRACKER_HOSTS into bare-host entries (matched against the
#   resolved host only) and path-fragment entries like "facebook.com/tr" /
#   "bing.com/action" (matched against the path/url), mirroring the Referer
#   and login_success substring fixes (v3.43.16 / v3.65.2).
# ─────────────────────────────────────────────────────────────────────────

def test_p6a_tracker_name_in_query_not_rejected():
    """RED until P6-A lands: a real 4K download whose URL carries a tracker
    domain in a utm param must NOT be rejected. (Measured on v3.66.195:
    score -75, rejected=True; with the host penalty removed -> +15, OK.)"""
    el = {"href": "https://cdn.legitsite.com/videos/"
                  "scene_4k.mp4?utm_source=google-analytics.com"}
    out = _dd.score_download_link(el)
    assert out["rejected"] is False, (
        f"legit 4K download wrongly rejected; penalties={out['penalties']}")


def test_p6a_real_tracker_host_still_rejected():
    """No-over-correction guard: when the tracker domain IS the actual host,
    the link must still be rejected. Passes on unmodified code; must stay
    passing after the fix."""
    el = {"href": "https://google-analytics.com/collect?v=1&dl=clip.mp4"}
    out = _dd.score_download_link(el)
    assert out["rejected"] is True, (
        f"real tracker host must stay rejected; out={out['penalties']}")


def test_p6a_path_fragment_tracker_still_rejected():
    """No-over-correction guard: host+path fragment entries (facebook.com/tr,
    bing.com/action) must keep matching against the path after the split."""
    el = {"href": "https://www.facebook.com/tr?ev=Download&u=clip.mp4"}
    out = _dd.score_download_link(el)
    assert out["rejected"] is True, (
        f"facebook.com/tr path-fragment must stay rejected; "
        f"out={out['penalties']}")


# ─────────────────────────────────────────────────────────────────────────
# F3 — db_log canonical history INSERT is unwrapped (db.py L431). On the
#   runner done-path, a raise (lock contention, disk full, corruption)
#   propagates and is treated as a download failure -> a verified-on-disk
#   completion is flipped to 'failed' and re-downloaded.
# Fix: best_effort param + auto-swallow when status == "done" (a completion
#   must never be un-completed by a history hiccup); other statuses still
#   raise.
# ─────────────────────────────────────────────────────────────────────────

def test_f3_done_insert_failure_is_swallowed():
    """RED until F3 lands. Trigger: point db.DB_PATH at an absolute,
    schemaless DB so the canonical INSERT raises 'no such table: history'
    deterministically. 'done' (and explicit best_effort) must swallow;
    other statuses must still raise."""
    import inspect
    import os as _os
    import tempfile as _tf
    import bulk_downloader.db as _db

    assert "best_effort" in inspect.signature(_db.db_log).parameters, (
        "F3 fix not present: db_log has no best_effort param")

    _orig = _db.DB_PATH
    try:
        _db.DB_PATH = _os.path.join(_tf.mkdtemp(), "noschema.db")  # absolute
        # precondition: a non-done insert genuinely raises on a schemaless DB
        _raised = False
        try:
            _db.db_log("s", "S", "http://x", "queued")
        except Exception:
            _raised = True
        assert _raised, "non-done insert should still raise on insert failure"
        # F3: a 'done' insert must be swallowed (no raise) -> stays 'done'
        _db.db_log("s", "S", "http://x", "done", "clip.mp4", 123, "ok")
        # explicit best_effort swallows for any status too
        _db.db_log("s", "S", "http://x", "queued", best_effort=True)
    finally:
        _db.DB_PATH = _orig


# ─────────────────────────────────────────────────────────────────────────
# F4 — _save_sites_config (app.py L1925/L1930) iterates the shared s_cfg
#   module dict with no lock. A concurrent add/delete from another request
#   thread raises "dict changed size during iteration", which the function's
#   outer try/except swallows -> the save silently no-ops and the triggering
#   add is lost. Fix: snapshot via list(s_cfg.items()) (the pattern already
#   used at app.py L2140/2519/2557).
# ─────────────────────────────────────────────────────────────────────────

def test_f4_save_survives_concurrent_mutation():
    """RED until F4 lands. Deterministic injection: a config value whose
    first cookie_file assignment (set by the cookie-fill loop body) adds a
    new site to the parent dict, standing in for a concurrent add arriving
    mid-loop. Unmodified -> RuntimeError -> swallowed -> file not written;
    fixed -> snapshot -> save completes -> file written."""
    import os as _os
    import json as _json
    import tempfile as _tf
    from pathlib import Path as _P
    import bulk_downloader.app as _app

    class _MutatingCfg(dict):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.parent = None
            self.fired = False

        def __setitem__(self, key, val):
            super().__setitem__(key, val)
            if (key == "cookie_file" and not self.fired
                    and self.parent is not None):
                self.fired = True
                dict.__setitem__(
                    self.parent, "injected_site", {"name": "injected"})

    _orig_cfg = _app.s_cfg
    _orig_file = _app.SITES_FILE
    _orig_home = _os.environ.get("BD_HOME")
    try:
        _home = _tf.mkdtemp()
        _os.environ["BD_HOME"] = _home
        _app.SITES_FILE = _P(_home) / "sites_config.json"
        new_cfg = {}
        a = _MutatingCfg({"name": "site-a"}); a.parent = new_cfg
        new_cfg["a"] = a
        new_cfg["b"] = _MutatingCfg({"name": "site-b"})  # 2nd iter trips view
        _app.s_cfg = new_cfg
        _app._save_sites_config()
        assert _app.SITES_FILE.exists(), (
            "save aborted: _save_sites_config did not write the file after a "
            "concurrent mutation (bare s_cfg.items() -> RuntimeError -> "
            "swallowed by the outer try/except)")
        data = _json.loads(_app.SITES_FILE.read_text(encoding="utf-8"))
        assert "injected_site" in data, (
            "snapshot save should still capture the mid-loop add")
    finally:
        _app.s_cfg = _orig_cfg
        _app.SITES_FILE = _orig_file
        if _orig_home is None:
            _os.environ.pop("BD_HOME", None)
        else:
            _os.environ["BD_HOME"] = _orig_home
