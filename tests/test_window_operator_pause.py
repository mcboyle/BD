"""Bug-hunt window-operator-pause r1 (deep-dl-runner-state; R200-D advisory A3
on ddr-fix r3, PM A-pm-B-lensrouter-1058Z item 2).

An operator Pause survives download-window entry in BOTH entry paths -- the
app.py scheduler callback (_on_transition) and SiteRunner.window_enter -- with
or without pending work; only a window-caused pause resumes. Resume verbs work
from either paused state.

Real SiteRunner objects over the isolated per-test DB, modelled on the ddr-fix
r3 held_run fixture: one live worker mid-transfer, _worker_loop stubbed, so no
browser runs. The app path drives the real _on_transition captured from
app._start_window_scheduler() with a stub scheduler.
"""
import inspect
import threading

import pytest
from bulk_downloader import db, download_window
from bulk_downloader import runner as runner_mod
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

U = "https://ex.test/scene/1"
TAG = "WINDOW_OPERATOR_PAUSE"


@pytest.fixture
def held_run(monkeypatch):
    """A running site whose one worker is mid-transfer on U (nothing pending)."""
    db.db_init()
    monkeypatch.setattr(SiteRunner, "_worker_loop", lambda self, *a, **k: None)
    monkeypatch.setattr(download_window, "site_in_window", lambda *a, **k: True)
    r = SiteRunner("wop1", {"name": "wop1", "auto_teach_first_run": False})
    r.load_urls([U])
    r._state = "running"
    r._pause.set()
    r.jobs[U]["status"] = "running"
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    r._worker_threads = [worker]
    yield r
    release.set()
    r._sched_stop.set()


class _RunnerPath:
    name = "runner"

    def leave(self, r):
        r.window_leave("paused")

    def enter(self, r):
        r.window_enter()


class _AppPath:
    """The scheduler callback app.py registers, driven directly."""
    name = "app"

    def __init__(self, monkeypatch):
        from bulk_downloader import app as A
        got = []

        class _Sched:
            def set_transition_callback(self, cb):
                got.append(cb)

            def start(self):
                pass

        monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
        monkeypatch.setattr(download_window, "get_scheduler", lambda: _Sched())
        A._start_window_scheduler()
        assert len(got) == 1, f"{TAG}: app did not register a transition callback"
        self._cb, self._app, self._mp = got[0], A, monkeypatch

    def _bind(self, r):
        self._mp.setitem(self._app.runners, r.site_id, r)
        self._mp.setitem(self._app.s_cfg, r.site_id,
                         {"window_action_outside": "paused"})

    def leave(self, r):
        self._bind(r)
        self._cb(r.site_id, False)

    def enter(self, r):
        self._bind(r)
        self._cb(r.site_id, True)


@pytest.fixture(params=["app", "runner"])
def window(request, monkeypatch):
    return _AppPath(monkeypatch) if request.param == "app" else _RunnerPath()


def _transfer(r):
    done = threading.Event()
    threading.Thread(target=lambda: (r._transfer_gate_open(None), done.set()),
                     daemon=True).start()
    return done


def _assert_held(r, done, why):
    assert not done.wait(0.3), (
        f"{TAG}: {why}: transfer released (state={r._state})")
    assert r._state != "running" and not r._pause.is_set(), (
        f"{TAG}: {why}: site resumed (state={r._state})")
    assert not db.run_intent_is_running(r.site_id), (
        f"{TAG}: {why}: run intent re-recorded")


def _assert_resumed(r, done, why):
    assert done.wait(5), f"{TAG}: {why}: transfer still held (state={r._state})"
    assert r._state == "running" and r._pause.is_set(), (
        f"{TAG}: {why}: not running (state={r._state})")


@pytest.mark.parametrize("pending", [False, True], ids=["idle-queue", "pending"])
def test_operator_pause_survives_window_entry(held_run, window, pending):
    r = held_run
    if pending:
        r.load_urls([U + "/next"])
    r.pause()  # operator Pause inside the window
    done = _transfer(r)
    for cycle in (1, 2):
        window.leave(r)
        window.enter(r)
        _assert_held(r, done, f"{window.name} entry {cycle} overrode an "
                              f"operator Pause (pending={pending})")
    r.resume()
    _assert_resumed(r, done, "resume after window entry")


@pytest.mark.parametrize("pending", [False, True], ids=["idle-queue", "pending"])
def test_operator_pause_while_window_paused_survives_entry(held_run, window, pending):
    r = held_run
    if pending:
        r.load_urls([U + "/next"])
    window.leave(r)  # the window pauses the running site
    r.pause()  # operator Pause while the window is closed
    done = _transfer(r)
    for cycle in (1, 2):
        window.enter(r)
        _assert_held(r, done, f"{window.name} entry {cycle} overrode an "
                              f"operator Pause of a window-paused site "
                              f"(pending={pending})")
        window.leave(r)
    window.enter(r)
    r.resume()
    _assert_resumed(r, done, "resume of an operator-held window-paused site")


@pytest.mark.parametrize("pending", [False, True], ids=["idle-queue", "pending"])
def test_ctl_window_pause_resumes_on_entry(held_run, window, pending):
    r = held_run
    if pending:
        r.load_urls([U + "/next"])
    window.leave(r)
    assert r._state == "window_paused"
    done = _transfer(r)
    assert not done.wait(0.3), f"{TAG}: window exit did not hold the transfer"
    window.enter(r)
    _assert_resumed(r, done, f"{window.name} entry after a window pause")


# start() on an operator Pause is not a resume verb (dl95-justporn-3: a user
# pause stays paused); its window cell is the control.
@pytest.mark.parametrize("held_by,verb", [
    ("operator", "resume"), ("window", "resume"), ("window", "start")])
def test_resume_verbs_from_either_state(held_run, held_by, verb):
    r = held_run
    if held_by == "operator":
        r.pause()
        assert r._state == "paused"
    else:
        r.window_leave("paused")
        assert r._state == "window_paused"
    done = _transfer(r)
    assert not done.wait(0.3)
    getattr(r, verb)()
    _assert_resumed(r, done, f"{verb}() from a {held_by}-held site")
    assert db.run_intent_is_running(r.site_id), (
        f"{TAG}: {verb}() from a {held_by}-held site recorded no run intent")


# r2 (lens REFUTE F1, VERIFY-RACE.md): a window entry that has already taken
# the park, delayed before start() runs, must not override an operator Pause
# that completes in that gap. Sequential Pause-after-entry is the control.
RACE = "WINDOW-ENTER-PAUSE-RACE"


def _delay_start(r, monkeypatch):
    called, release, original = threading.Event(), threading.Event(), r.start

    def delayed(*args, **kwargs):
        called.set()
        assert release.wait(5), f"{RACE}: delayed start never released"
        return original(*args, **kwargs)

    monkeypatch.setattr(r, "start", delayed)
    return called, release


# "outside": the delayed start() finds the site outside its window again and
# would re-park it -- the withdrawn entry must not plant a park either.
@pytest.mark.parametrize("in_window", [True, False], ids=["inside", "outside"])
def test_operator_pause_during_window_entry_is_not_lost(
        held_run, window, monkeypatch, in_window):
    r = held_run
    window.leave(r)
    assert r._state == "window_paused" and db.window_park_is_set(r.site_id)
    called, release = _delay_start(r, monkeypatch)
    errors = []

    def enter():
        try:
            window.enter(r)
        except Exception as exc:  # surfaced below
            errors.append(exc)

    entry = threading.Thread(target=enter, daemon=True)
    entry.start()
    assert called.wait(5), f"{RACE}: {window.name} entry never reached start()"
    r.pause()  # operator Pause completes while the entry is in flight
    assert not db.window_park_is_set(r.site_id)
    assert not db.run_intent_is_running(r.site_id)
    monkeypatch.setattr(download_window, "site_in_window",
                        lambda *a, **k: in_window)
    release.set()
    entry.join(5)
    assert not entry.is_alive() and not errors, f"{RACE}: {errors}"
    assert not db.window_park_is_set(r.site_id), (
        f"{RACE}: {window.name} entry re-parked an operator-held site "
        f"(in_window={in_window})")
    monkeypatch.setattr(download_window, "site_in_window", lambda *a, **k: True)
    done = _transfer(r)
    _assert_held(r, done, f"{RACE}: {window.name} entry overrode a completed "
                          f"operator Pause (in_window={in_window})")
    window.leave(r)
    window.enter(r)
    _assert_held(r, done, f"{RACE}: {window.name} next cycle after the race")
    r.resume()
    _assert_resumed(r, done, "resume after a withdrawn window entry")


def test_ctl_operator_pause_after_completed_entry(held_run, window):
    r = held_run
    window.leave(r)
    window.enter(r)
    assert r._state == "running" and r._pause.is_set()
    assert db.run_intent_is_running(r.site_id)
    r.pause()
    done = _transfer(r)
    assert r._state == "paused"
    _assert_held(r, done, f"{window.name} sequential Pause after entry")

def test_operator_pause_during_window_entry_hold_resume_is_not_lost(
        held_run, window, monkeypatch):
    r = held_run
    window.leave(r)
    r._refuse_for_download_hold(
        {"state": "held", "reason": "operator"}, "resume",
        resumable_state="paused")
    assert r._hold_refused_resume_state == "paused"
    assert db.window_park_is_set(r.site_id)
    called, release = threading.Event(), threading.Event()
    original_resume = r.resume
    errors = []

    def delayed_resume(*args, **kwargs):
        called.set()
        assert release.wait(5), f"{RACE}: hold resume never released"
        return original_resume(*args, **kwargs)

    monkeypatch.setattr(r, "resume", delayed_resume)

    def enter():
        try:
            window.enter(r)
        except Exception as exc:
            errors.append(exc)

    entry = threading.Thread(target=enter, daemon=True)
    entry.start()
    assert called.wait(5), f"{RACE}: window entry never reached hold resume"
    r.pause()
    assert not db.run_intent_is_running(r.site_id)
    assert not db.window_park_is_set(r.site_id)
    release.set()
    entry.join(5)
    assert not entry.is_alive() and not errors, f"{RACE}: {errors}"
    done = _transfer(r)
    _assert_held(r, done, f"{RACE}: hold recovery overrode operator Pause")
    r.resume()
    _assert_resumed(r, done, "explicit resume after hold recovery was withdrawn")


# r3 (r2 lens REFUTE F1, ev/test_own_barriers.py): an operator Pause that
# completes inside window entry, after start() lifted the window gate but
# before the final arming barrier (arm), or before the held-run reopen
# barrier (reopen), must withdraw the entry AND leave the transfer gate shut.
ESCAPED = "WINDOW-PAUSE-TRANSFER-ESCAPED"


def _snapshot(r):
    return dict(state=r._state, gate=r._pause.is_set(),
                park=db.window_park_is_set(r.site_id),
                intent=db.run_intent_is_running(r.site_id))


def _pause_at_entry_barrier(r, window, monkeypatch, phase):
    """Drive window entry on a thread, hold it just before the barrier that
    phase names, complete an operator Pause, then let the entry finish."""
    if phase == "arm":
        r.load_urls(["https://ex.test/scene/next"])
    window.leave(r)
    assert (r._state == "window_paused" and not r._pause.is_set()
            and db.window_park_is_set(r.site_id))
    ready, release, errors, entry = (threading.Event(), threading.Event(),
                                     [], [])
    original = runner_mod._download_hold.barrier

    class _Delayed:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            ready.set()
            assert release.wait(5), "TEST-BARRIER-NOT-RELEASED"
            return self.inner.__enter__()

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

    def barrier():
        frame = inspect.currentframe().f_back
        if phase == "reopen":
            wanted = frame.f_code.co_name == "_reopen_window_held_run"
        else:  # the final arming barrier: the only one after pending binds
            wanted = (frame.f_code.co_name == "_start_serialized"
                      and "pending" in frame.f_locals)
        mine = entry and threading.get_ident() == entry[0].ident
        inner = original()
        return _Delayed(inner) if mine and wanted else inner

    monkeypatch.setattr(runner_mod._download_hold, "barrier", barrier)

    def enter():
        try:
            window.enter(r)
        except BaseException as exc:  # surfaced below
            errors.append(repr(exc))

    entry.append(threading.Thread(target=enter, daemon=True))
    entry[0].start()
    try:
        assert ready.wait(5), f"TEST-TARGET-TRANSITION-NOT-REACHED-{phase}"
        r.pause()
        after = _snapshot(r)
        assert not after["intent"] and not after["park"], after
        release.set()
        entry[0].join(5)
        assert not entry[0].is_alive() and not errors, errors
    finally:
        release.set()
        entry[0].join(5)


@pytest.mark.parametrize("phase", ["reopen", "arm"])
def test_withdrawn_entry_cannot_publish_running_after_late_pause(
        held_run, window, monkeypatch, phase):
    r = held_run
    _pause_at_entry_barrier(r, window, monkeypatch, phase)
    assert r._state != "running", f"LATE-PAUSE-POOL-ARMED-{phase}"
    assert not db.window_park_is_set(r.site_id)
    assert not db.run_intent_is_running(r.site_id)
    if phase == "reopen":
        assert not r._pause.is_set(), "WITHDRAWN-REOPEN-OPENED-GATE"


def test_completed_pause_keeps_transfer_gate_closed_during_entry(
        held_run, window, monkeypatch):
    r = held_run
    _pause_at_entry_barrier(r, window, monkeypatch, "arm")
    done = _transfer(r)
    try:
        assert not done.wait(0.4) and not r._pause.is_set(), (
            f"{ESCAPED}: {window.name} completed Pause left an already-open "
            f"gate during window entry ({_snapshot(r)})")
    finally:
        r._stop.set()
        r._pause.set()
    r._stop.clear()
    r.resume()
    assert r._state == "running" and r._pause.is_set(), (
        f"{TAG}: resume after a withdrawn entry: state={r._state}")
