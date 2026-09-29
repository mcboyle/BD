"""dl95-evilangel-1: a site that was running when the service restarted resumes
its restored jobs on its own; a site the operator stopped or paused does not.

O1513 on test2: every lane deploy restarted the service; the jobs came back
"Restored after restart -- was mid-download at last shutdown" but the sites sat
idle with pending jobs until someone pressed Start (9 of A5-A's sites at
23:46:28Z). Nothing durable recorded that a site was running: stop() rewrites
job status in memory only, so a leftover queue row in "running" cannot tell
"running at shutdown" from "operator stopped it, then the service restarted".

Drives the real restart path twice over one private DB: generation 1 is a real
SiteRunner whose start()/stop()/pause() are the public lifecycle calls;
generation 2 is a fresh SiteRunner built by app._activate_configured_runtime_once
(the boot activation), as after a service restart. Only the worker launch
(_start_serialized) is replaced by a recorder, so no browser runs.
"""

import importlib
import threading

import pytest

BD_GATE_SCOPE = "module"

URL = "https://members.example.test/scenes/42/mid-download"


@pytest.fixture
def harness(monkeypatch):
    from bulk_downloader import db
    from bulk_downloader.runner import SiteRunner

    app = importlib.import_module("bulk_downloader.app")
    db.db_init()
    starts = []
    started = threading.Event()

    def _recording_start(self, _teardown_generation=None):
        starts.append(self)
        self._state = "running"
        started.set()

    monkeypatch.setattr(SiteRunner, "_start_serialized", _recording_start)
    live = []

    def new_runner(sid):
        r = SiteRunner(sid, {"name": sid})
        live.append(r)
        return r

    def restart(sid):
        """Boot activation as after a service restart: fresh runner set."""
        for r in live:
            r._sched_stop.set()
        starts.clear()
        started.clear()
        app.s_cfg.clear()
        app.s_meta.clear()
        app.runners.clear()

        def load():
            app.s_cfg[sid] = {"name": sid}
            app.s_meta[sid] = {"name": sid}
            app.runners[sid] = new_runner(sid)

        monkeypatch.setattr(app, "_SITE_RUNTIME_PATH", None)
        monkeypatch.setattr(app, "_SITE_RUNTIME_READY", False)
        monkeypatch.setattr(app, "_load_sites_config", load)
        monkeypatch.setattr(app, "_init_vpn_runtime", lambda: {"ok": True})
        monkeypatch.setattr(app, "_start_session_keepers", lambda: None)
        monkeypatch.setattr(app, "_start_watch_folder_threads", lambda: None)
        assert (
            app._activate_configured_runtime_once("/tmp/evilangel-1-sites.json") is True
        )
        resumer = getattr(app, "_restart_resume_thread", None)
        if resumer is not None:
            resumer.join(timeout=10)
        runner = app.runners[sid]
        return runner, [s for s in starts if s is runner]

    saved = (dict(app.s_cfg), dict(app.s_meta), dict(app.runners))
    yield db, new_runner, restart, started
    for r in live:
        r._sched_stop.set()
    app.s_cfg.clear()
    app.s_meta.clear()
    app.runners.clear()
    app.s_cfg.update(saved[0])
    app.s_meta.update(saved[1])
    app.runners.update(saved[2])


def _mid_download(db, sid):
    # the worker had claimed the job when the process died
    db.queue_upsert(sid, URL, status="running", message="Downloading 40%")


def test_running_site_resumes_after_restart(harness):
    db, new_runner, restart, _ = harness
    sid = "ea1run"
    r1 = new_runner(sid)
    r1.load_urls([URL])
    r1.start()
    _mid_download(db, sid)

    r2, starts = restart(sid)
    assert r2.jobs[URL]["status"] == "pending"
    assert r2.jobs[URL].get("_recovered_from_crash") is True
    assert len(starts) == 1, (
        "DL95_EVILANGEL_1_NOT_RESUMED: site was running at shutdown; after the "
        f"restart it has {sum(j['status'] == 'pending' for j in r2.jobs.values())} "
        f"pending job(s) and start() ran {len(starts)} time(s)"
    )


@pytest.mark.parametrize("operator_action", ["stop", "pause"])
def test_operator_stopped_or_paused_site_stays_idle(harness, operator_action):
    db, new_runner, restart, _ = harness
    sid = f"ea1{operator_action}"
    r1 = new_runner(sid)
    r1.load_urls([URL])
    r1.start()
    getattr(r1, operator_action)()
    _mid_download(db, sid)  # a stale "running" row must not override the operator

    _, starts = restart(sid)
    assert starts == [], (
        f"DL95_EVILANGEL_1_OVERRODE_{operator_action.upper()}: the operator "
        f"called {operator_action}() before the restart and boot started the site"
    )


def test_never_started_site_stays_idle(harness):
    db, new_runner, restart, _ = harness
    sid = "ea1never"
    new_runner(sid).load_urls([URL])
    _mid_download(db, sid)
    _, starts = restart(sid)
    assert starts == [], "DL95_EVILANGEL_1_STARTED_UNASKED"


def test_running_site_with_nothing_pending_is_not_started(harness):
    db, new_runner, restart, _ = harness
    sid = "ea1drained"
    r1 = new_runner(sid)
    r1.load_urls([URL])
    r1.start()
    db.queue_upsert(sid, URL, status="done", message="Saved")
    _, starts = restart(sid)
    assert starts == [], "DL95_EVILANGEL_1_STARTED_EMPTY"


def test_intent_survives_a_second_restart(harness):
    # The boot resume is itself a start(): the intent stays durable, so a
    # restart that lands mid-resume resumes again.
    db, new_runner, restart, _ = harness
    sid = "ea1twice"
    r1 = new_runner(sid)
    r1.load_urls([URL])
    r1.start()
    _mid_download(db, sid)
    restart(sid)
    _mid_download(db, sid)
    _, starts = restart(sid)
    assert len(starts) == 1, "DL95_EVILANGEL_1_NOT_RESUMED_TWICE"


def test_unreadable_intent_is_not_permission(harness, monkeypatch):
    db, new_runner, _, _ = harness
    sid = "ea1unknown"
    r1 = new_runner(sid)
    r1.load_urls([URL])
    r1.start()
    _mid_download(db, sid)
    real_conn = db.db_conn

    def broken_conn(*a, **k):
        raise db.sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "db_conn", broken_conn)
    try:
        from bulk_downloader.db import run_intent_is_running
    except ImportError:
        pytest.fail("DL95_EVILANGEL_1_NO_INTENT_STORE")
    assert run_intent_is_running(sid) is False
    monkeypatch.setattr(db, "db_conn", real_conn)


def test_resumed_after_pause_resumes_after_restart(harness):
    db, new_runner, restart, _ = harness
    sid = "ea1resumed"
    r1 = new_runner(sid)
    r1.load_urls([URL])
    r1.start()
    r1.pause()
    r1.resume()
    _mid_download(db, sid)
    _, starts = restart(sid)
    assert len(starts) == 1, "DL95_EVILANGEL_1_RESUME_NOT_RECORDED"
