"""deep-scheduler-cron: the scheduler/cron deep-hunt rows (O1789; D1-D7, D9, D10).

Each test states one finding as an assertion, beside a control that shows the
same probe can answer the other way. D8 (retention 0 / -1) was already fixed
by sc-f9 (_retention_count) and has no test here.
"""
import os
import pathlib
import threading
import time
import types

import pytest

from bulk_downloader import bg_scheduler as bg
from bulk_downloader import capture_schedules as cs
from bulk_downloader import scheduled_exports as se

BD_GATE_SCOPE = "module"

BODY = b"id,url\n" + b"1,https://x.example/a\n" * 50


@pytest.fixture
def store(clean_workdir):
    from bulk_downloader import db
    from bulk_downloader.migrations import apply_pending
    db.db_init()
    apply_pending(backup_first=False)
    return db


@pytest.fixture
def csv_body(monkeypatch):
    from bulk_downloader import exports as ex
    monkeypatch.setattr(ex, "to_csv", lambda f, rows=None: BODY)


def _export_dir(name):
    return os.path.join(os.getcwd(), "exports", name)


# ── D1: a backward wall-clock step froze every bg task ────────────────
class _Clock:
    def __init__(self):
        self.wall, self.mono = 1.9e9, 5.0

    def step(self, wall, mono):
        self.wall += wall
        self.mono += mono


@pytest.fixture
def one_poll(monkeypatch):
    clk = _Clock()
    monkeypatch.setattr(bg, "time", types.SimpleNamespace(
        time=lambda: clk.wall, monotonic=lambda: clk.mono, sleep=time.sleep))
    monkeypatch.setattr(bg, "_tasks", {})
    monkeypatch.setattr(bg, "_stop_event", threading.Event())
    monkeypatch.setattr(bg, "_wait_next", lambda interval: bg._stop_event.set() or False)

    def poll():
        bg._stop_event.clear()
        bg._loop()
    return clk, poll


def test_d1_a_backward_clock_step_does_not_freeze_the_tasks(one_poll):
    clk, poll = one_poll
    fired = {"fast": 0, "daily": 0}
    bg.register("fast", lambda: fired.__setitem__("fast", fired["fast"] + 1), interval_seconds=60)
    bg.register("daily", lambda: fired.__setitem__("daily", fired["daily"] + 1), interval_seconds=86400)
    poll()
    assert fired == {"fast": 1, "daily": 1}          # never run -> due now, even 5 s after boot
    for _ in range(2):
        clk.step(60, 60)
        poll()
    assert fired == {"fast": 3, "daily": 1}          # control: 60 s apart fires every poll
    clk.step(-3600, 0)                                # NTP steps the wall clock back 1 h
    for _ in range(3):
        clk.step(60, 60)
        poll()
    assert fired == {"fast": 6, "daily": 1}, f"after a 1 h backward step: {fired}"   # was fast 3


# ── D3: stop()+start() during a long task left two coordinators ───────
def _coordinators():
    return [t for t in threading.enumerate() if t.name == "bd-bg-scheduler" and t.is_alive()]


def test_d3_stop_then_start_leaves_one_coordinator(monkeypatch):
    monkeypatch.setattr(bg, "_tasks", {})
    monkeypatch.setattr(bg, "_started", False)
    monkeypatch.setattr(bg, "_thread", None)
    entered, release = threading.Event(), threading.Event()

    def long_task():
        entered.set()
        release.wait(30)
    ticks = []
    bg.register("long", long_task, interval_seconds=3600)
    bg.register("tick", lambda: ticks.append(threading.current_thread()), interval_seconds=0)
    try:
        bg.start()
        assert entered.wait(10), "long task never started"
        old = bg._thread
        bg.stop()                                     # join(timeout=5) gives up: the task is still running
        assert old.is_alive()                         # control: the old coordinator is mid-task
        bg.start()
        new = bg._thread
        assert new is not old and new.is_alive()
        release.set()
        old.join(10)
        assert not old.is_alive(), "the stopped coordinator resumed polling beside the new one"
        assert _coordinators() == [new]
        n = sum(1 for t in ticks if t is old)
        time.sleep(0.3)
        assert sum(1 for t in ticks if t is old) == n  # the old thread fires nothing more
    finally:
        release.set()
        bg.stop()


# ── D2: a bg tick and run_now overlapped and exported one schedule twice
def test_d2_tick_and_run_now_do_not_export_one_schedule_twice(store, csv_body, monkeypatch):
    d = _export_dir("d2")
    ids = [se.add_schedule(label=f"o{i}", format="csv", destination=d, cadence_hours=24) for i in range(2)]
    assert None not in ids
    real, ran = se._run_one, []
    started = threading.Event()

    def slow(s, **k):
        ran.append(s["id"])
        if s["id"] == ids[0]:
            started.set()
            time.sleep(1.5)
        return real(s, **k)
    monkeypatch.setattr(se, "_run_one", slow)
    se.run_due_exports()
    se.run_due_exports()
    assert [ran.count(i) for i in ids] == [1, 1]      # control: two sequential ticks export each once
    for force in (False, True):                        # a second tick, then POST run_now
        ran.clear()
        with store.db_conn() as cx:
            cx.execute("UPDATE scheduled_exports SET next_run_ts = 0")
        started.clear()
        tick = threading.Thread(target=se.run_due_exports)
        tick.start()
        assert started.wait(10)
        se.run_due_exports(force=force)
        tick.join(30)
        assert [ran.count(i) for i in ids] == [1, 1], f"force={force}: runs per schedule {ran}"


# ── D4: add_schedule deduped on (site_id, cadence) only ───────────────
def test_d4_a_second_schedule_with_other_urls_is_not_dropped(store):
    a = cs.add_schedule(site_id="s", cadence_hours=6, label="one", urls=["https://a.example/1"])
    assert cs.add_schedule(site_id="s", cadence_hours=6, label="one", urls=["https://a.example/1"]) == a   # control
    b = cs.add_schedule(site_id="s", cadence_hours=6, label="two", urls=["https://a.example/2"])
    assert b is not None and b != a, "the second schedule's label and urls were dropped"
    rows = {r["id"]: r for r in cs.list_schedules()}
    assert rows[b]["label"] == "two" and rows[b]["urls"] == ["https://a.example/2"]
    assert rows[a]["urls"] == ["https://a.example/1"]


# ── D5: one huge cadence broke schedules_to_ics ───────────────────────
def test_d5_one_huge_cadence_does_not_break_the_calendar(store):
    good = {"id": 1, "site_id": "s", "label": "ok", "cadence_hours": 6, "next_run_ts": 1.9e9}
    assert cs.schedules_to_ics([good], now=1.9e9).count("BEGIN:VEVENT") == 1   # control
    huge = {"id": 2, "site_id": "h", "label": "huge", "cadence_hours": 2 ** 62, "next_run_ts": 1.66e22}
    odd = {"id": 3, "site_id": "n", "label": "nan", "cadence_hours": 1, "next_run_ts": float("nan")}
    ics = cs.schedules_to_ics([good, huge, odd], now=1.9e9)
    assert ics.count("BEGIN:VEVENT") == 3
    assert cs.add_schedule(site_id="b", cadence_hours=True) is None      # a bool is not 1 h
    assert cs.add_schedule(site_id="f", cadence_hours=1.5) is None       # a fraction is refused, not truncated
    assert cs.add_schedule(site_id="w", cadence_hours=2.0) is not None   # control: a whole float still adds
    assert cs.add_schedule(site_id="u", cadence_hours="\u00b2") is None  # isdigit() but not int(): refused, no raise (lens F2)
    assert cs.add_schedule(site_id="d", cadence_hours="3") is not None   # control: a digit string still adds


# ── D6: a negative cadence_min_h became the interval ──────────────────
def test_d6_a_negative_minimum_is_not_used_as_the_interval():
    assert cs.adaptive_cadence_hours(1, change_count=50, min_hours=1) == 1    # control
    for lo in (-5, 0):
        got = cs.adaptive_cadence_hours(1, change_count=50, min_hours=lo, max_hours=168)
        assert got >= 1, f"min_hours={lo} gave a cadence of {got} h"
    assert cs.adaptive_cadence_hours(1, change_count=50, min_hours=5, max_hours=2) >= 1


# ── D7: an export was written straight to its final name ──────────────
def test_d7_a_failed_write_leaves_no_partial_export(store, csv_body, monkeypatch):
    d = _export_dir("d7")
    eid = se.add_schedule(label="p", format="csv", destination=d, cadence_hours=1)
    real = pathlib.Path.write_bytes

    def half(self, data):
        with open(self, "wb") as fh:
            fh.write(data[: len(data) // 2])
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(pathlib.Path, "write_bytes", half)
    res = [r for r in se.run_due_exports()["results"] if r["id"] == eid][0]
    monkeypatch.setattr(pathlib.Path, "write_bytes", real)
    assert res["ok"] is False
    assert os.listdir(d) == [], f"a failed export left {os.listdir(d)}"
    with store.db_conn() as cx:
        cx.execute("UPDATE scheduled_exports SET next_run_ts = 0 WHERE id = ?", (eid,))
    good = [r for r in se.run_due_exports()["results"] if r["id"] == eid][0]
    assert good["ok"] is True and os.listdir(d) == [os.path.basename(good["file"])]   # control
    assert os.path.getsize(good["file"]) == len(BODY)


# ── D9: a schedule removed during a running sweep still fired ─────────
def test_d9_a_schedule_removed_mid_sweep_does_not_fire(store, csv_body, monkeypatch):
    ids = [cs.add_schedule(site_id=f"cx{i}", cadence_hours=24) for i in range(2)]
    fired = []

    def enq(site, urls):
        fired.append(site)
        if site == "cx0":
            assert cs.remove_schedule(ids[1])
        return 1
    cs.run_due(enqueue_fn=enq)
    assert fired == ["cx0"], f"capture sweep fired {fired}"          # was cx0, cx1

    d = _export_dir("d9")
    eids = [se.add_schedule(label=f"e{i}", format="csv", destination=d, cadence_hours=24) for i in range(2)]
    real, ran = se._run_one, []

    def run(s, **k):
        ran.append(s["id"])
        if s["id"] == eids[0]:
            assert se.remove_schedule(eids[1])
        return real(s, **k)
    monkeypatch.setattr(se, "_run_one", run)
    se.run_due_exports()
    assert ran == [eids[0]], f"export sweep ran {ran}"


# ── D10: /api/bg/enable read "false" as enabled ───────────────────────
def test_d10_bg_enable_parses_the_flag(monkeypatch):
    from flask import Flask
    from bulk_downloader import app_bg
    monkeypatch.setattr(app_bg, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(bg, "_tasks", {})
    bg.register("t", lambda: None, interval_seconds=60)
    app = Flask("dsc")
    app.register_blueprint(app_bg.bg_bp)
    c = app.test_client()

    def post(body):
        r = c.post("/api/bg/enable/t", json=body)
        return r.status_code, bg._tasks["t"]["enabled"]
    assert post({"enabled": False}) == (200, False)    # control
    assert post({"enabled": True}) == (200, True)      # control
    assert post({"enabled": "false"}) == (200, False)
    bg.set_enabled("t", True)
    assert post({"enabled": "0"}) == (200, False)
    assert post({"enabled": "maybe"})[0] == 400
    assert post([1, 2])[0] == 400
