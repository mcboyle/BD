"""Bug-hunt ddr-fix r2 (deep-dl-runner-state sweep, findings F1-F7, F9, F12).

Each test is the finding's own scenario from FINDINGS.md (S-number in the
name); each ``*_ctl`` test is the control that scenario names, and passes on the
base as well. Real SiteRunner objects over the isolated per-test DB; where the
lifecycle would launch workers, ``_start_serialized`` is replaced by a recorder
(as test_dl95_evilangel_1 does), so no browser runs.

F4 follows the adjudicator-B ruling (PERMISSION) and operator DECISION 0438Z
item 1: when a download window opens, resume only sites the window itself
parked; manually stopped or never-started sites stay put. The window verbs are
SiteRunner.window_leave / window_enter; the app.py call site that still calls
start() is OWED (ddr-fix/r2/OWED-app-py.diff).
"""
import sqlite3
import threading

import pytest

import bulk_downloader.runner_queue as RQ
from bulk_downloader import content_rights, db, download_window
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

U = "https://ex.test/scene/1"


@pytest.fixture
def mk():
    db.db_init()
    live = []
    n = [0]

    def make(sid=None, **cfg):
        n[0] += 1
        sid = sid or f"ddr{n[0]}"
        r = SiteRunner(sid, dict({"name": sid}, **cfg))
        live.append(r)
        return r

    yield make
    for r in live:
        r._sched_stop.set()


@pytest.fixture
def armed(monkeypatch):
    """Record arming instead of launching workers."""
    starts = []

    def _recording_start(self, _teardown_generation=None, _restart_resume=False):
        starts.append(self.site_id)
        self._state = "running"

    monkeypatch.setattr(SiteRunner, "_start_serialized", _recording_start)
    return starts


def rows(sid):
    return {x["url"]: x for x in db.queue_load(sid)}


def _sql(sql, args):
    cx = sqlite3.connect(db._resolve_db_path())
    try:
        cx.execute(sql, args)
        cx.commit()
    finally:
        cx.close()


def _disk_full(*_a, **_k):
    raise sqlite3.OperationalError("database or disk is full")


class _LogRec:
    def __init__(self, inner):
        self.inner = inner
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args)
        self.inner.warning(msg, *args)

    def __getattr__(self, name):
        return getattr(self.inner, name)


# ── F1 S28: clear_completed keeps unfinished 'stopped' work ──────────────
def test_f1_s28_clear_completed_keeps_paused_and_stop_parked(mk):
    r = mk()
    done, paused, parked, pend = (U + "/done", U + "/paused", U + "/stoppd", U + "/pend")
    r.load_urls([done, paused, parked, pend])
    r.jobs[done]["status"] = "done"
    db.queue_upsert(r.site_id, done, status="done")
    r.bulk_pause([paused])
    r.jobs[parked].update(status="stopped", message="Stopped")
    db.queue_upsert(r.site_id, parked, status="stopped", message="Stopped")
    r.clear_completed()
    assert sorted(r.jobs) == sorted([paused, parked, pend]), (
        "F1: clear_completed deleted unfinished stopped jobs from memory")
    assert sorted(rows(r.site_id)) == sorted([paused, parked, pend]), (
        "F1: clear_completed deleted unfinished stopped jobs from the queue table")


def test_f1_s28_ctl_done_only_clear_keeps_pending(mk):
    r = mk()
    r.load_urls([U + "/done", U + "/pend"])
    r.jobs[U + "/done"]["status"] = "done"
    db.queue_upsert(r.site_id, U + "/done", status="done")
    r.clear_completed()
    assert list(r.jobs) == [U + "/pend"]
    assert list(rows(r.site_id)) == [U + "/pend"]


# ── F2 S17: a queue persist failure is surfaced, nothing half-queued ─────
def test_f2_s17_persist_failure_raises_and_withdraws(mk, monkeypatch):
    r = mk()
    urls = [U + "/a", U + "/b"]
    monkeypatch.setattr(RQ, "queue_bulk_upsert", _disk_full)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        r.load_urls(urls)
    assert r.jobs == {} and r.urls == [], "F2: unpersisted URLs left queued in memory"
    monkeypatch.undo()
    assert r.load_urls(urls) == (2, 0, 0), "F2: a re-send after the failure counted dupes"
    assert sorted(mk(r.site_id).jobs) == urls


def test_f2_s17_ctl_healthy_persist_survives_restart(mk):
    r = mk()
    assert r.load_urls([U + "/a", U + "/b"]) == (2, 0, 0)
    assert len(mk(r.site_id).jobs) == 2


# ── F3 S31: window exit "paused" holds the workers ───────────────────────
def test_f3_s31_window_paused_closes_the_worker_gate(mk):
    r = mk()
    r.load_urls([U])
    r._state = "running"
    r._pause.set()
    intent = db.run_intent_is_running(r.site_id)
    r._state = "window_paused"  # what app._on_transition does on exit today
    assert not r._pause.is_set(), (
        "F3: window_paused left _pause set; workers keep claiming")
    assert db.run_intent_is_running(r.site_id) == intent
    r._state = "running"  # the arming transition reopens it
    assert r._pause.is_set()


def test_f3_s31_ctl_operator_pause_closes_the_gate(mk):
    r = mk()
    r.load_urls([U])
    r._state = "running"
    r._pause.set()
    r.pause()
    assert not r._pause.is_set()


# ── F4 S32: window entry resumes only what the window parked ─────────────
@pytest.mark.parametrize("action", ["paused", "stopped"])
def test_f4_s32_window_parked_site_resumes_after_restart(mk, armed, action):
    r = mk()
    r.load_urls([U])
    r.start()
    assert r.window_leave(action) is True
    assert r._state == ("stopped" if action == "stopped" else "window_paused")
    assert db.window_park_is_set(r.site_id)
    r2 = mk(r.site_id)  # service restart between exit and entry
    armed.clear()
    r2.window_enter()
    assert armed == [r.site_id] and r2._state == "running"
    assert db.run_intent_is_running(r.site_id)
    assert not db.window_park_is_set(r.site_id)


def test_f4_s32_operator_stop_stays_stopped(mk, armed):
    r = mk()
    r.load_urls([U])
    r.start()
    r.window_leave("paused")
    r.stop()  # operator Stop while the window is closed
    armed.clear()
    assert r.window_enter() is None
    assert armed == [] and r._state == "stopped", "F4: window entry overrode an operator Stop"
    assert not db.run_intent_is_running(r.site_id), "F4: window entry re-recorded run intent"
    r.start()  # an operator Stop inside the window is not parked either
    r.stop()
    assert r.window_leave("stopped") is False
    assert r.window_enter() is None and armed == [r.site_id]


def test_f4_s32_operator_pause_stays_paused(mk, armed):
    r = mk()
    r.load_urls([U])
    r.start()
    r.pause()
    assert r._state == "paused"
    assert r.window_leave("paused") is False
    armed.clear()
    assert r.window_enter() is None
    assert armed == [] and r._state == "paused", "F4: window entry overrode an operator Pause"
    assert not db.run_intent_is_running(r.site_id)


def test_f4_s32_never_started_site_stays_idle(mk, armed):
    r = mk()
    r.load_urls([U])
    assert r.window_leave("paused") is False
    assert r.window_enter() is None
    assert armed == [] and r._state == "idle", "F4: window entry started a never-started site"
    assert not db.run_intent_is_running(r.site_id)


def test_f4_s32_start_outside_window_is_parked_by_the_window(mk, monkeypatch):
    monkeypatch.setattr(SiteRunner, "_worker_loop", lambda self, *a, **k: None)
    monkeypatch.setattr(download_window, "site_in_window", lambda *a, **k: False)
    r = mk(auto_teach_first_run=False)
    r.load_urls([U])
    r.start()  # operator Start outside the window: the window parks it
    assert r._state == "window_paused"
    assert db.window_park_is_set(r.site_id)
    r.stop()
    assert not db.window_park_is_set(r.site_id)


def test_f4_s32_ctl_operator_start_after_stop_still_starts(mk, armed):
    r = mk()
    r.load_urls([U])
    r.start()
    r.stop()
    armed.clear()
    r.start()
    assert armed == [r.site_id] and r._state == "running"


# ── F5 S13: a crash mid-download is charged to the retry budget ──────────
def test_f5_s13_crash_restarts_count_toward_max_retries(mk):
    sid, u = "ddr-f5", U + "/poison"
    db.queue_bulk_upsert(sid, [u])
    seen = []
    for _ in range(3):
        db.queue_upsert(sid, u, status="running", message="mid")
        r = mk(sid, max_retries=2)
        seen.append((r.jobs[u]["status"], r.jobs[u]["retries"]))
    assert seen == [("pending", 1), ("pending", 2), ("failed", 2)], (
        f"F5: crash restarts never exhaust the retry budget: {seen}")
    assert rows(sid)[u]["status"] == "failed"


def test_f5_s13_ctl_pending_row_restores_unchanged(mk):
    sid, u = "ddr-f5c", U + "/calm"
    db.queue_bulk_upsert(sid, [u])
    db.queue_upsert(sid, u, status="pending", retries=1)
    r = mk(sid, max_retries=2)
    assert (r.jobs[u]["status"], r.jobs[u]["retries"]) == ("pending", 1)


# ── F6 S15/S15b: persisted rows are normalised on restore ────────────────
def test_f6_s15_null_retries_and_unknown_status_normalised(mk):
    sid, a, b = "ddr-f6", U + "/nullret", U + "/weird"
    db.queue_bulk_upsert(sid, [a, b])
    _sql("UPDATE queue SET status='pending', retries=NULL WHERE site_id=? AND url=?", (sid, a))
    _sql("UPDATE queue SET status='weird_state' WHERE site_id=? AND url=?", (sid, b))
    r = mk(sid, max_retries=2)
    assert r.jobs[a]["retries"] == 0, "F6: NULL retries restored as None"
    r.jobs[a]["status"] = "running"
    r._handle_failure(a, "boom: 503 Service Unavailable")
    assert (r.jobs[a]["status"], r.jobs[a]["retries"]) == ("pending", 1)
    assert r.jobs[b]["status"] == "failed", "F6: unknown status restored as-is"
    assert "weird_state" in r.jobs[b]["message"]
    assert rows(sid)[b]["status"] == "failed"


def test_f6_s15_ctl_zero_retries_fails_over_to_retry(mk):
    sid, a = "ddr-f6c", U + "/zero"
    db.queue_bulk_upsert(sid, [a])
    r = mk(sid, max_retries=2)
    r.jobs[a]["status"] = "running"
    r._handle_failure(a, "boom: 503 Service Unavailable")
    assert (r.jobs[a]["status"], r.jobs[a]["retries"]) == ("pending", 1)


# ── F7 S29: bulk verbs do not swallow the persist error ──────────────────
_F7 = {
    "retry_failed": ("failed", lambda r: r.retry_failed()),
    "bulk_pause": ("pending", lambda r: r.bulk_pause([U])),
    "bulk_resume": ("stopped", lambda r: r.bulk_resume([U])),
}


@pytest.mark.parametrize("verb", sorted(_F7))
def test_f7_s29_persist_failure_is_logged(mk, monkeypatch, verb):
    status, call = _F7[verb]
    r = mk()
    r.load_urls([U])
    r.jobs[U]["status"] = status
    db.queue_upsert(r.site_id, U, status=status)
    r.log = rec = _LogRec(r.log)
    monkeypatch.setattr(RQ, "queue_bulk_update", _disk_full)
    call(r)
    assert any(w.startswith(f"{verb}: 1 job(s) changed in memory only") for w in rec.warnings), (
        f"F7: {verb} swallowed the persist error: {rec.warnings}")


@pytest.mark.parametrize("verb", sorted(_F7))
def test_f7_s29_ctl_healthy_persist_is_silent(mk, verb):
    status, call = _F7[verb]
    r = mk()
    r.load_urls([U])
    r.jobs[U]["status"] = status
    db.queue_upsert(r.site_id, U, status=status)
    r.log = rec = _LogRec(r.log)
    call(r)
    assert rows(r.site_id)[U]["status"] == r.jobs[U]["status"] != status
    assert not rec.warnings


# ── F9 S11: a content-rights refusal is not a duplicate ──────────────────
def test_f9_s11_refusal_not_counted_as_duplicate(mk, monkeypatch):
    monkeypatch.setattr(content_rights, "url_is_blocked",
                        lambda u: {"blocked": True, "id": 7, "reason": "dmca"}
                        if u.endswith("/blk") else None)
    r = mk()
    events = []
    orig = r.log_event
    r.log_event = lambda kind, msg, **k: events.append((kind, msg, k)) or orig(kind, msg, **k)
    assert r.load_urls([U + "/blk", U + "/ok"]) == (1, 1, 0)   # slot 1 = not queued
    imp = [(m, k.get("extra")) for kind, m, k in events if kind == "import"]
    assert "0 duplicate(s)" in imp[0][0], f"F9: refusal counted as a dupe: {imp[0][0]!r}"
    assert "1 refused" in imp[0][0]
    assert imp[0][1]["dupes"] == 0 and imp[0][1]["refused"] == 1


def test_f9_s11_ctl_real_duplicate_still_counted(mk):
    r = mk()
    assert r.load_urls([U, U]) == (1, 1, 0)


# ── F12 S05: dedupe=False is refused, not ignored ────────────────────────
def test_f12_s05_dedupe_false_is_refused(mk):
    r = mk()
    with pytest.raises(ValueError, match=r"dedupe=False\) is not supported"):
        r.load_urls([U, U], dedupe=False)
    assert r.jobs == {}


def test_f12_s05_ctl_dedupe_true(mk):
    r = mk()
    assert r.load_urls([U, U], dedupe=True) == (1, 1, 0)


# ── r3 (R158-C F1): window entry with nothing to arm reopens the gate ─────
# The window closed the gate on a live run (window_paused clears _pause); at
# entry start() finds nothing pending. Its in-flight transfer must resume.
@pytest.fixture
def held_run(mk, monkeypatch):
    """A running site whose one worker is mid-transfer on U when the window
    closes; returns (runner, transfer_done, release_worker)."""
    monkeypatch.setattr(SiteRunner, "_worker_loop", lambda self, *a, **k: None)
    monkeypatch.setattr(download_window, "site_in_window", lambda *a, **k: True)
    r = mk(auto_teach_first_run=False)
    r.load_urls([U])
    r._state = "running"
    r._pause.set()
    r.jobs[U]["status"] = "running"
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    r._worker_threads = [worker]  # the in-flight worker is alive
    yield r, release
    release.set()


def _transfer(r):
    done = threading.Event()
    threading.Thread(target=lambda: (r._transfer_gate_open(None), done.set()),
                     daemon=True).start()
    return done


@pytest.mark.parametrize("path", ["app", "verbs"])
def test_r3_f1_entry_with_nothing_pending_resumes_the_transfer(held_run, path):
    r, _release = held_run
    if path == "app":
        r._state = "window_paused"  # app._on_transition exit, as shipped
    else:
        assert r.window_leave("paused") is True
    done = _transfer(r)
    assert not done.wait(0.3), "window exit did not hold the transfer"
    generation = r._worker_run_generation
    r.start() if path == "app" else r.window_enter()
    assert done.wait(5), (
        "R158-C F1: window entry with nothing to arm left the gate closed; "
        f"in-flight transfer stuck (state={r._state})")
    assert r._state == "running" and r._pause.is_set()
    assert r._worker_run_generation == generation, (
        "R158-C F1: the live run's generation was replaced")
    r.pause()  # the operator can pause the resumed run again
    assert r._state == "paused" and not r._pause.is_set()


def test_r3_f1_entry_returning_early_still_lifts_the_window_hold(held_run, monkeypatch):
    r, _release = held_run
    r.load_urls([U + "/next"])  # something to arm: start() takes the long path
    r._state = "window_paused"
    done = _transfer(r)
    from bulk_downloader import maintenance
    monkeypatch.setattr(maintenance, "is_action_paused", lambda *a, **k: True)
    r.start()  # window open, maintenance returns before arming
    assert r._state == "maintenance_paused"
    assert done.wait(5), (
        "R158-C F1: start() returned after the window check with the window's "
        "gate still closed")


def test_r3_f1_ctl_entry_with_pending_work_arms(held_run):
    r, _release = held_run
    r.load_urls([U + "/next"])
    r._state = "window_paused"
    done = _transfer(r)
    r.start()
    assert done.wait(5) and r._state == "running" and r._pause.is_set()


def test_r3_f1_ctl_start_outside_window_keeps_the_hold(held_run, monkeypatch):
    r, _release = held_run
    r._state = "window_paused"
    monkeypatch.setattr(download_window, "site_in_window", lambda *a, **k: False)
    done = _transfer(r)
    r.start()
    assert not done.wait(0.3) and r._state == "window_paused"
    assert not r._pause.is_set()
