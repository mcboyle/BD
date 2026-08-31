"""Rows 433 + 451 -- the durable download hold is a BARRIER, and it reports
EFFECTS.

Two defects on one surface (``bulk_downloader/app_download_hold.py``), and the
second is why the first matters.

ROW 433 (the race). ``POST /api/download_hold`` writes the durable record and
then walks the live runners calling ``runner.pause()``. ``pause()`` acts only
when ``_state == "running"``. ``start()`` reads the hold at runner.py:1304 and
only reaches ``_state = "running"`` at runner.py:1480, after the window,
maintenance, admission, disk, quota and sort work. A hold POST that lands in
that window sees a runner that is not yet "running", no-ops on it, and start()
then arms the pool anyway -- against a durable hold that answered ``ok: true``.
The operator's one restart-surviving instrument reports success while stopping
nothing.

ROW 451 (the count). The same loop counted every runner it TOUCHED, because a
no-op ``pause()`` returns ``None`` without raising. Six idle runners and zero
running reported ``paused_runners: 6``. A count of attempts presented as a count
of effects -- and it is exactly what hides row 433's mid-start runner.

The race here is driven through a REAL ``SiteRunner.start()`` with a
deterministic seam: ``disk_free_gb`` is a real call site between the hold check
and the running transition, so the test blocks start() there and lands the hold
POST inside the window. No sleeps. The positive control proves the same harness
DOES arm a pool and pick URLs up when no hold is placed, so "zero pickups" after
the fix cannot be manufactured by a harness that never worked.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_URLS = ["https://example.invalid/a.mp4",
         "https://example.invalid/b.mp4",
         "https://example.invalid/c.mp4"]

_RACE_ITERATIONS = 40


# ── helpers ────────────────────────────────────────────────────────────────

class _CountingQueue(queue.Queue):
    """The runner's URL queue, counting real (non-sentinel) enqueues.

    ``start()`` puts every pending URL on this queue inside the same locked
    block that sets ``_state = "running"``, and does it BEFORE spawning any
    worker thread -- so once start() has returned, this count is a settled,
    monotonic fact about whether the pool was armed. ``_worker_threads`` is
    not: ``_watch_done`` clears it as soon as the queue drains, so reading it
    after the race is a coin flip between "never armed" and "armed, ran, and
    tidied up".
    """

    def __init__(self):
        super().__init__()
        self.real_puts = 0

    def put(self, item, *a, **k):
        if item is not None:
            self.real_puts += 1
        return super().put(item, *a, **k)


def _make_runner(site_id, tmp_path, **extra):
    from bulk_downloader.runner import SiteRunner
    cfg = {
        "name": site_id,
        # Without this start() diverts to the auto-teach branch and returns
        # "idle" before ever reaching the running transition -- the race
        # window would never open and a green result would be a precondition
        # artefact rather than evidence.
        "auto_teach_first_run": False,
        "download_dir": str(tmp_path),
        "disk_threshold_gb": 0.0,
        # The per-site quota walk is the seam the race is driven through: it
        # runs strictly BETWEEN the hold check and the running transition. The
        # cap is enormous so the quota itself never refuses the start.
        "site_quota_gb": 1_000_000.0,
        "max_concurrent": 1,
    }
    cfg.update(extra)
    return SiteRunner(site_id, cfg)


def _kill(runner):
    try:
        runner.stop()
    except Exception:
        pass
    try:
        runner._stop_auto_retry()
    except Exception:
        pass


def _install_fake_workers(runner, picked, all_picked):
    """Replace the playwright worker loop with one that records URL pickups.

    A real worker blocks on ``self._pause`` before transferring anything, so
    this fake does the same: an armed pool (``_pause`` set) yields pickups, a
    paused one yields none. ``all_picked`` fires when every URL has been taken,
    which is what the caller waits on -- a sleep would decide the race by
    schedule instead of measuring it.
    """
    def fake_worker_loop(worker_idx, run_generation):
        while True:
            try:
                item = runner._url_queue.get(timeout=10.0)
            except queue.Empty:
                return
            try:
                if item is None:
                    return
                if not runner._pause.wait(timeout=10.0):
                    return
                if runner._stop.is_set():
                    return
                picked.append(item[1])
                if len(picked) >= len(_URLS):
                    all_picked.set()
            finally:
                try:
                    runner._url_queue.task_done()
                except Exception:
                    pass

    runner._worker_loop = fake_worker_loop


def _run_race(tmp_path, site_id, place_hold, *, monkeypatch):
    """Drive one start()/hold interleaving and return everything observed.

    ``place_hold`` False is the POSITIVE CONTROL: identical harness, identical
    seam, no hold -- the pool must arm and pick up every URL.
    """
    from bulk_downloader import app_state, download_hold as dh
    import bulk_downloader.app as a

    runner = _make_runner(site_id, tmp_path)
    runner._url_queue = _CountingQueue()
    picked: list = []
    all_picked = threading.Event()
    _install_fake_workers(runner, picked, all_picked)
    runner.load_urls(list(_URLS))
    assert len(runner.jobs) == len(_URLS), runner.jobs   # precondition: queued

    mid_start = threading.Event()     # start() has passed the hold check
    release = threading.Event()       # ...and may now proceed to the transition

    # THE SEAM. _compute_site_usage is the quota directory walk at
    # runner.py:1343 -- after the hold check (1304) and before the running
    # transition (1480). Blocking here parks a real start() inside the exact
    # window row 433 describes, with no sleep anywhere: the hold POST decides
    # when start() proceeds.
    #
    # NOT disk_free_gb: admission.admission_hold() imports that same symbol
    # from this module and calls it BEFORE the hold check, so a patch there
    # parks start() on the wrong side of the gate and the runner then reads
    # the hold normally -- a green result that never entered the window.
    seam_calls = []

    def seam_compute_site_usage(path):
        seam_calls.append(path)
        mid_start.set()
        assert release.wait(20.0), "race seam never released"
        return 0

    runner._compute_site_usage = seam_compute_site_usage

    # Observe every pause() the hold's walk actually performs on this runner,
    # with the state it saw and the hold state on disk at that instant. A
    # mid-start runner is NOT running, so a correct walk pauses nothing here --
    # which is exactly why the barrier, not the walk, has to be what stops it.
    observed: list = []
    real_pause = runner.pause

    def spy_pause():
        observed.append({"state": runner._state,
                         "hold": dh.hold_state()["state"]})
        return real_pause()

    runner.pause = spy_pause

    # Exactly one runner in the fleet, so the response's per-runner counts are
    # an exact denominator for this race rather than a total over leftovers.
    app_state.runners.clear()
    app_state.runners[site_id] = runner
    outcome = {}

    def _start():
        try:
            runner.start()
        except Exception as e:               # pragma: no cover - harness guard
            outcome["error"] = repr(e)

    t = threading.Thread(target=_start, name=f"race-start-{site_id}")
    t.start()
    try:
        assert mid_start.wait(20.0), "start() never reached the race seam"
        state_at_gate = runner._state
        response = None
        if place_hold:
            response = a.app.test_client().post(
                "/api/download_hold",
                json={"reason": "row433-race", "by": "test"})
        release.set()
        t.join(30.0)
        assert not t.is_alive(), "start() thread did not finish"
        # SNAPSHOT BEFORE TEARDOWN. stop() rewrites _state to "stopped" and
        # makes every worker exit before recording, so reading these after
        # cleanup would report teardown rather than the race.
        enqueued = runner._url_queue.real_puts
        if enqueued:
            # The pool WAS armed: wait for the pickups it is doing rather than
            # guessing at a sleep. Zero pickups then means the workers really
            # could not take work, not that the test looked too early.
            all_picked.wait(20.0)
        snap = {
            "enqueued": enqueued,
            "seam_calls": len(seam_calls),
            "final_state": runner._state,
            # Diagnostic only, never a verdict: _pause is SET at construction
            # (runner.py:692), so it cannot tell "never armed" from "armed".
            "armed": runner._pause.is_set(),
            "picked": list(picked),
            "hold_events": [e for e in list(getattr(runner, "_event_log", []))
                            if isinstance(e, dict)
                            and e.get("kind") == "download_hold"],
        }
    finally:
        release.set()
        t.join(30.0)
        app_state.runners.pop(site_id, None)
        _kill(runner)

    return {
        "runner": runner,
        "observed": observed,
        "state_at_gate": state_at_gate,
        "response": (response.get_json() if response is not None else None),
        "status": (response.status_code if response is not None else None),
        "outcome": outcome,
        **snap,
    }


# ── row 433: the race ──────────────────────────────────────────────────────

def test_race_harness_arms_the_pool_when_no_hold_is_placed(clean_workdir,
                                                           monkeypatch):
    """POSITIVE CONTROL for the race harness.

    Same seam, same fake workers, no hold: the pool must arm and pick up every
    URL. Without this, "zero pickups" under a hold proves nothing about the
    hold and everything about a harness that never ran.
    """
    from bulk_downloader import download_hold as dh
    from bulk_downloader.db import db_init
    db_init()
    assert dh.hold_state()["state"] == dh.CLEAR      # precondition: no hold

    r = _run_race(clean_workdir, "row433_control", False, monkeypatch=monkeypatch)
    assert r["outcome"] == {}, r["outcome"]
    assert r["seam_calls"] == 1, r["seam_calls"]    # the window really opened
    assert r["enqueued"] == 3, r["enqueued"]        # start() reached 1480
    assert sorted(r["picked"]) == sorted(_URLS), r["picked"]
    assert len(r["picked"]) == 3, r["picked"]
    assert r["hold_events"] == []
    # No final-state assertion: once the workers drain the queue _watch_done
    # legitimately moves the runner off "running", so the state at this instant
    # is a race with the harness rather than a fact about the hold. The
    # enqueue count and the pickups are the settled evidence.
    assert r["final_state"] not in (dh.STATE_HELD, dh.STATE_UNKNOWN), r


def test_a_hold_placed_mid_start_stops_the_pool(clean_workdir, monkeypatch):
    """Row 433 RED: the hold lands while start() is in flight.

    On the defective parent start() arms the pool anyway and the workers pick
    up all three URLs while hold_state() reads HELD. After the fix start()
    refuses at the running transition: zero pickups, exactly one download_hold
    event, and the runner publishes the hold token.
    """
    from bulk_downloader import download_hold as dh
    from bulk_downloader.db import db_init
    db_init()
    assert dh.hold_state()["state"] == dh.CLEAR      # precondition: no hold

    r = _run_race(clean_workdir, "row433_race", True, monkeypatch=monkeypatch)
    assert r["outcome"] == {}, r["outcome"]

    # PRECONDITIONS -- prove the interleaving happened, and that the runtime
    # pause walk could NOT have been what stopped this pool. Both hold on the
    # defective parent too, so the verdict below fails for the DEFECT and not
    # for a response field the parent never had.
    assert r["seam_calls"] == 1, r["seam_calls"]      # start parked in the gap
    assert r["state_at_gate"] != "running", r["state_at_gate"]
    assert r["status"] == 200, (r["status"], r["response"])
    assert r["response"]["state"] == dh.HELD, r["response"]   # record written
    assert dh.hold_state()["state"] == dh.HELD
    # Whatever the walk did to this runner, it saw a NON-running one with the
    # record already on disk -- so pause() was a no-op and cannot be what
    # stops the pool. (On the parent that is one observed call; with the fix
    # the walk does not call pause() on a non-running runner at all.)
    assert all(o["state"] != "running" for o in r["observed"]), r["observed"]
    assert all(o["hold"] == dh.HELD for o in r["observed"]), r["observed"]

    # THE DEFECT: the pool must not have downloaded anything.
    assert r["picked"] == [], r["picked"]
    assert r["enqueued"] == 0, r["enqueued"]    # no work was ever handed out
    assert r["final_state"] == dh.STATE_HELD, r["final_state"]
    assert len(r["hold_events"]) == 1, r["hold_events"]

    # ...and the response reports effects: one runner, seen not-running,
    # nothing claimed as paused.
    body = r["response"]
    assert body["runners_total"] == 1, body            # exact denominator
    assert body["runners_already_not_running"] == 1, body
    assert body["paused_runners"] == 0, body
    assert body["runners_pause_unknown"] == 0, body
    assert body["ok"] is True, body
    assert r["observed"] == [], r["observed"]


def test_the_hold_barrier_holds_across_repeated_races(clean_workdir,
                                                      monkeypatch):
    """The window is closed by a barrier, not by schedule luck.

    The seam is deterministic, so a single sample would already be meaningful;
    repeating it 40 times makes a surviving interleaving expensive to miss.
    """
    from bulk_downloader import download_hold as dh
    from bulk_downloader.db import db_init
    db_init()

    armed = []
    for i in range(_RACE_ITERATIONS):
        Path("app_config.json").unlink(missing_ok=True)
        assert dh.hold_state()["state"] == dh.CLEAR
        r = _run_race(clean_workdir, f"row433_loop_{i}", True,
                      monkeypatch=monkeypatch)
        assert r["seam_calls"] == 1, r
        assert r["state_at_gate"] != "running", r
        if (r["picked"] or r["enqueued"]
                or r["final_state"] != dh.STATE_HELD):
            armed.append({"i": i, "state": r["final_state"],
                          "picked": r["picked"],
                          "enqueued": r["enqueued"]})
    assert armed == [], armed


def test_a_lift_lets_the_next_start_proceed(clean_workdir, monkeypatch):
    """NEGATIVE CONTROL for the barrier: it refuses a HELD hold, not starts.

    A gate that refused unconditionally would pass every assertion above. Place
    a hold, watch a start refuse, lift it, and start again -- the second start
    must reach the transition and hand out work.
    """
    from bulk_downloader import app_state, download_hold as dh
    from bulk_downloader.db import db_init
    db_init()

    runner = _make_runner("row433_lift", clean_workdir)
    runner._url_queue = _CountingQueue()
    picked: list = []
    all_picked = threading.Event()
    _install_fake_workers(runner, picked, all_picked)
    runner.load_urls(list(_URLS))
    runner._compute_site_usage = lambda path: 0
    app_state.runners.clear()
    app_state.runners["row433_lift"] = runner
    try:
        assert dh.hold("row433-lift") is True
        assert dh.hold_state()["state"] == dh.HELD     # precondition: HELD
        runner.start()
        assert runner._state == dh.STATE_HELD, runner._state
        assert runner._url_queue.real_puts == 0, runner._url_queue.real_puts

        assert dh.lift("done") is True
        assert dh.hold_state()["state"] == dh.CLEAR    # precondition: lifted
        runner.start()
        assert runner._url_queue.real_puts == 3, runner._url_queue.real_puts
        assert all_picked.wait(20.0), picked
        assert sorted(picked) == sorted(_URLS), picked
    finally:
        app_state.runners.pop("row433_lift", None)
        _kill(runner)


# ── row 451: the count reports effects, not attempts ───────────────────────

def _states_of(runners):
    return sorted(r._state for r in runners.values())


@pytest.fixture
def hold_client(clean_workdir):
    from bulk_downloader.db import db_init
    from bulk_downloader import app_state
    import bulk_downloader.app as a
    db_init()
    app_state.runners.clear()
    try:
        yield a.app.test_client()
    finally:
        for r in list(app_state.runners.values()):
            _kill(r)
        app_state.runners.clear()


def _mixed_fleet(tmp_path, running, not_running_states):
    from bulk_downloader import app_state
    made = []
    for i, st in enumerate(not_running_states):
        r = _make_runner(f"idle_{i}", tmp_path)
        r._state = st
        app_state.runners[f"idle_{i}"] = r
        made.append(r)
    for i in range(running):
        r = _make_runner(f"run_{i}", tmp_path)
        r._state = "running"
        r._pause.set()
        app_state.runners[f"run_{i}"] = r
        made.append(r)
    return made


def test_the_count_is_effects_not_attempts(hold_client, clean_workdir):
    """Row 451 RED: four not-running + two running reported 6, not 2."""
    from bulk_downloader import app_state, download_hold as dh
    _mixed_fleet(clean_workdir, 2,
                 ["idle", "window_paused", "cookies_expired", "paused"])

    # PRECONDITION: the exact state mix, asserted before the call.
    assert len(app_state.runners) == 6
    assert _states_of(app_state.runners) == sorted(
        ["idle", "window_paused", "cookies_expired", "paused",
         "running", "running"])

    res = hold_client.post("/api/download_hold", json={"reason": "row451"})
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True, body
    assert body["paused_runners"] == 2, body
    assert body["runners_total"] == 6, body
    assert body["runners_already_not_running"] == 4, body
    assert body["runners_pause_unknown"] == 0, body
    assert body["state"] == dh.HELD, body
    # The effect really happened: exactly the two running runners moved.
    assert _states_of(app_state.runners) == sorted(
        ["idle", "window_paused", "cookies_expired", "paused",
         "paused", "paused"])


def test_zero_running_reports_zero_paused_and_still_sets_the_hold(
        hold_client, clean_workdir):
    """NEGATIVE CONTROL: six runners, none running."""
    from bulk_downloader import app_state, download_hold as dh
    _mixed_fleet(clean_workdir, 0,
                 ["idle", "idle", "window_paused", "window_paused",
                  "cookies_expired", "paused"])
    assert len(app_state.runners) == 6
    assert "running" not in _states_of(app_state.runners)

    res = hold_client.post("/api/download_hold", json={"reason": "row451-zero"})
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True, body
    assert body["paused_runners"] == 0, body
    assert body["runners_already_not_running"] == 6, body
    assert body["runners_pause_unknown"] == 0, body
    # ...and the hold IS set, durably.
    assert body["state"] == dh.HELD, body
    raw = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert raw["download_hold"]["held"] is True, raw
    assert hold_client.get("/api/download_hold").get_json()["state"] == dh.HELD


def test_exactly_two_running_reports_exactly_two(hold_client, clean_workdir):
    """POSITIVE CONTROL: two running and nothing else."""
    from bulk_downloader import app_state
    _mixed_fleet(clean_workdir, 2, [])
    assert _states_of(app_state.runners) == ["running", "running"]

    body = hold_client.post("/api/download_hold",
                            json={"reason": "row451-two"}).get_json()
    assert body["ok"] is True, body
    assert body["paused_runners"] == 2, body
    assert body["runners_total"] == 2, body
    assert body["runners_already_not_running"] == 0, body
    assert body["runners_pause_unknown"] == 0, body
    assert _states_of(app_state.runners) == ["paused", "paused"]


def test_a_pause_that_cannot_be_established_reads_unknown(hold_client,
                                                          clean_workdir):
    """CLAUDE.md A7: an unmeasurable effect is UNKNOWN, never counted as done.

    Two seams: a pause() that raises, and a pause() that returns cleanly while
    leaving the runner running (the fail-open shape the old counter could not
    tell apart from success).
    """
    from bulk_downloader import app_state
    made = _mixed_fleet(clean_workdir, 1, ["idle"])

    boom = _make_runner("boom", clean_workdir)
    boom._state = "running"

    def _raise():
        raise RuntimeError("pause exploded")
    boom.pause = _raise
    app_state.runners["boom"] = boom

    liar = _make_runner("liar", clean_workdir)
    liar._state = "running"
    liar.pause = lambda: None            # returns cleanly, changes nothing
    app_state.runners["liar"] = liar

    assert len(app_state.runners) == 4
    assert sorted(r._state for r in app_state.runners.values()) == sorted(
        ["idle", "running", "running", "running"])

    res = hold_client.post("/api/download_hold", json={"reason": "row451-unk"})
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is False, body            # unmeasurable is not OK
    assert body["error"] == "runner_pause_unknown", body
    assert body["paused_runners"] == 1, body    # only the real one
    assert body["runners_pause_unknown"] == 2, body
    assert body["runners_already_not_running"] == 1, body
    assert body["runners_total"] == 4, body
    # ...and the hold is still durably recorded.
    raw = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert raw["download_hold"]["held"] is True, raw
    assert made and made[0] is not None


def test_an_unenumerable_fleet_is_unknown_not_zero(hold_client, monkeypatch,
                                                   clean_workdir):
    """A7 again: if the runners cannot be listed at all, the hold's runtime
    application is UNKNOWN. The old code returned 0 and reported ok: true."""
    from bulk_downloader import app_download_hold as adh

    def _explode():
        raise RuntimeError("no runners dict")
    monkeypatch.setattr(adh, "_runners", _explode)

    body = hold_client.post("/api/download_hold",
                            json={"reason": "row451-enum"}).get_json()
    assert body["ok"] is False, body
    assert body["error"] == "runner_pause_unknown", body
    assert body["runners_enumerated"] is False, body
    assert body["paused_runners"] == 0, body
    raw = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert raw["download_hold"]["held"] is True, raw
