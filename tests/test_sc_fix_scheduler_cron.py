"""sc-fix: the scheduler/cron bug-hunt rows (O1789 sweep; F1, F2, F4, F5, F6, F7).

Each test states one finding as an assertion, beside a control that shows the
same probe can answer the other way.
"""
import csv
import io
import os
import time

import pytest

from bulk_downloader import bg_scheduler as bg
from bulk_downloader import capture_schedules as cs
from bulk_downloader import scheduled_exports as se

BD_GATE_SCOPE = "module"


@pytest.fixture
def store(clean_workdir):
    from bulk_downloader import db
    from bulk_downloader.migrations import apply_pending
    db.db_init()
    apply_pending(backup_first=False)
    return db


def _client():
    from bulk_downloader import app as A
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


def _make_due(db, table, ids):
    with db.db_conn() as cx:
        cx.execute(f"UPDATE {table} SET next_run_ts=0 WHERE id IN (%s)" % ",".join("?" * len(ids)), list(ids))


# ── F1: GET /api/bg/status replaced boot's wired tasks with unwired ones ──
@pytest.fixture
def scheduler(store, monkeypatch):
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader import app_bg
    monkeypatch.setattr(bg, "_tasks", {})
    monkeypatch.setattr(bg, "start", lambda: None)             # no coordinator thread
    monkeypatch.setattr(app_bg.api_bg_status, "_started", False, raising=False)
    seen = {"enqueued": [], "evaluated": 0}

    def evaluate(s_cfg=None, **k):
        seen["evaluated"] += 1
        return {"unknown": 0}
    monkeypatch.setattr(ae, "evaluate", evaluate)
    return seen


def _status_client():
    from flask import Flask
    from bulk_downloader.app_bg import bg_bp
    app = Flask("sc-fix")
    app.register_blueprint(bg_bp)
    return app.test_client()


def test_f1_status_get_keeps_the_boot_wiring(store, scheduler):
    bg.register_default_tasks(                                  # app.py start_default_tasks' wiring
        s_cfg_getter=lambda: {"siteA": {"start_url": "https://a.example/"}},
        runners_getter=lambda: {},
        capture_enqueue_fn=lambda site_id, urls: (scheduler["enqueued"].append(site_id) or 1))
    sid = cs.add_schedule(site_id="siteA", cadence_hours=1)
    wired = {n: bg._tasks[n]["fn"] for n in ("capture_schedules.run_due", "alerts_engine.evaluate")}

    def tick():
        _make_due(store, "capture_schedules", [sid])
        for name in wired:
            bg._run_one(name, bg._tasks[name])

    tick()
    assert (len(scheduler["enqueued"]), scheduler["evaluated"]) == (1, 1)      # control: wired tasks work
    r = _status_client().get("/api/bg/status")
    assert r.status_code == 200 and r.get_json()["total"] == len(bg._tasks)
    assert {n: bg._tasks[n]["fn"] for n in wired} == wired      # the same callables, not re-registered
    tick()
    assert (len(scheduler["enqueued"]), scheduler["evaluated"]) == (2, 2)


def test_f1_status_get_still_registers_defaults_when_boot_registered_nothing(store, scheduler):
    assert bg.status()["total"] == 0
    body = _status_client().get("/api/bg/status").get_json()
    assert body["total"] > 10
    assert "capture_schedules.run_due" in {t["name"] for t in body["tasks"]}


# ── F2: run_due held one transaction across every due schedule ────────
def _changes(db):
    with db.db_conn() as cx:
        cs._ensure_change_ledger(cx)
        return {r[0]: r[1] for r in cx.execute(
            "SELECT site_id, COUNT(*) FROM schedule_changes GROUP BY site_id").fetchall()}


def test_f2_three_due_schedules_do_not_wait_on_each_other(store):
    one = cs.add_schedule(site_id="one", cadence_hours=3)
    _make_due(store, "capture_schedules", [one])
    assert cs.run_due(enqueue_fn=lambda s, u: 1)["ran"] == 1    # control: one due schedule
    assert sorted(_changes(store).items()) == [("one", 1)]
    ids = [cs.add_schedule(site_id=s, cadence_hours=3) for s in ("a", "b", "c")]
    _make_due(store, "capture_schedules", ids)
    t0 = time.monotonic()
    res = cs.run_due(enqueue_fn=lambda s, u: 1)
    took = time.monotonic() - t0
    assert res["ran"] == 3 and [r["ok"] for r in res["results"]] == [True, True, True]
    assert took < 5.0, f"run_due took {took:.1f} s for 3 schedules"       # was 2 x busy_timeout (20 s)
    assert sorted(_changes(store).items()) == [("a", 1), ("b", 1), ("c", 1), ("one", 1)]   # was a 1, b 0, c 0
    assert cs.run_due(enqueue_fn=lambda s, u: 1)["ran"] == 0              # next_run guard still holds


# ── F4: an export lost its linked search and exported everything ──────
@pytest.fixture
def linked_export(store):
    from bulk_downloader import saved_searches as ss
    from bulk_downloader.db import db_log
    for i in range(3):
        db_log("siteA", "Alpha Studio", f"https://a.example/scene/{i}", "done", filename=f"alpha-{i}.mp4")
    for i in range(2):
        db_log("siteB", "Beta Studio", f"https://b.example/x/{i}", "done", filename=f"beta-{i}.mp4")
    search = ss.add(name="alpha only", query="alpha")
    dest = os.path.join(os.getcwd(), "exports")
    eid = se.add_schedule(label="alpha", format="csv", destination=dest, cadence_hours=24,
                          source_saved_search=search)
    return {"db": store, "search": search, "dest": dest, "eid": eid, "ss": ss}


def _run_export(ctx, **kw):
    _make_due(ctx["db"], "scheduled_exports", [ctx["eid"]])
    res = se.run_due_exports(**kw)
    return [r for r in res["results"] if r.get("id") == ctx["eid"]][0]


def _sites(path):
    rows = list(csv.DictReader(io.StringIO(open(path, "rb").read().decode("utf-8", "replace"))))
    return len(rows), sorted({r.get("site_id") or r.get("site") or "?" for r in rows})


def test_f4_a_deleted_linked_search_fails_the_run_instead_of_exporting_everything(linked_export):
    ctx = linked_export
    first = _run_export(ctx)
    assert first["ok"] is True and _sites(first["file"]) == (3, ["siteA"])      # control: link present
    before = sorted(os.listdir(ctx["dest"]))
    assert ctx["ss"].remove(search_id=ctx["search"])
    gone = _run_export(ctx)
    assert gone["ok"] is False and "linked saved search" in gone["error"]
    assert sorted(os.listdir(ctx["dest"])) == before                           # nothing written
    row = [s for s in se.list_schedules() if s["id"] == ctx["eid"]][0]
    assert row["last_run_ok"] == 0 and "linked saved search" in row["last_run_message"]


def test_f4_the_frozen_filter_fallback_and_unlinked_exports_are_unchanged(linked_export):
    ctx = linked_export
    with_filter = {"id": 901, "format": "csv", "destination": ctx["dest"], "source_saved_search": 123456,
                   "filter_json": '{"site_id": "siteA"}'}
    assert se._run_one(with_filter)["ok"] is True          # a stored filter is still a fallback
    unlinked = {"id": 902, "format": "csv", "destination": ctx["dest"], "source_saved_search": None,
                "filter_json": "{}"}
    res = se._run_one(unlinked)
    assert res["ok"] is True and _sites(res["file"])[0] == 5                   # no link: whole history, as before


# ── F5: a string of urls became one "url" per character ───────────────
def test_f5_a_url_string_is_one_url(store):
    sid = cs.add_schedule(site_id="siteC", cadence_hours=2, urls="https://c.example/v/1")
    stored = {s["id"]: s for s in cs.list_schedules()}[sid]["urls"]
    assert stored == ["https://c.example/v/1"]
    got = []
    cs.run_one(sid, enqueue_fn=lambda site, urls: (got.extend(urls or []) or len(urls or [])))
    assert got == ["https://c.example/v/1"]
    listed = cs.add_schedule(site_id="siteD", cadence_hours=2, urls=["https://d.example/v/1", " "])
    assert {s["id"]: s for s in cs.list_schedules()}[listed]["urls"] == ["https://d.example/v/1"]   # control: a list
    assert cs.add_schedule(site_id="siteE", cadence_hours=2, urls={"u": 1}) is None
    assert cs.add_schedule(site_id="siteF", cadence_hours=2, urls=5) is None
    assert cs.add_schedule(site_id="siteI", cadence_hours=2, urls=["https://i.example/v/1", 7]) is None


def test_f5_api_post_with_a_url_string(store):
    c, h = _client()
    r = c.post("/api/schedules", headers=h, json={"site_id": "siteG", "cadence_hours": 2, "urls": "https://g.example/v/1"})
    assert r.status_code == 200
    assert {s["id"]: s for s in cs.list_schedules()}[r.get_json()["id"]]["urls"] == ["https://g.example/v/1"]
    r = c.post("/api/schedules", headers=h, json={"site_id": "siteH", "cadence_hours": 2, "urls": {"u": 1}})
    assert r.status_code == 400


# ── F6: "run now" ran only what was already due ───────────────────────
def test_f6_run_now_runs_an_export_that_is_not_due(linked_export):
    ctx = linked_export
    c, h = _client()
    assert _run_export(ctx)["ok"] is True
    idle = se.run_due_exports()
    assert (idle["checked"], idle["ran"]) == (0, 0)             # control: the scheduler path skips a not-due export
    for _ in range(2):
        body = c.post("/api/scheduled_exports/run_now", headers=h).get_json()
        assert (body["checked"], body["ran"], body["errors"]) == (1, 1, 0)
    row = [s for s in se.list_schedules() if s["id"] == ctx["eid"]][0]
    assert row["next_run_ts"] > time.time() + 23 * 3600          # cadence restarts from the forced run


def test_f6_force_leaves_a_disabled_export_alone(linked_export):
    ctx = linked_export
    with ctx["db"].db_conn() as cx:
        cx.execute("UPDATE scheduled_exports SET enabled=0 WHERE id=?", (ctx["eid"],))
    assert se.run_due_exports(force=True)["checked"] == 0


# ── F7: ICS folding cut multibyte characters in half and dropped them ─
def _summary(label):
    ics = cs.schedules_to_ics([{"id": 1, "site_id": "s", "label": label, "cadence_hours": 6, "next_run_ts": 1.0}], now=1.0)
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n")), "a folded line exceeds 75 octets"
    line = [ln for ln in ics.replace("\r\n ", "").split("\r\n") if ln.startswith("SUMMARY:")][0]
    return line[len("SUMMARY:"):]


@pytest.mark.parametrize("char", ["é", "漢", "😀"])
@pytest.mark.parametrize("offset", range(4))
def test_f7_folding_keeps_every_multibyte_character(char, offset):
    label = "x" * offset + char * 60
    assert _summary(label).endswith(label)


def test_f7_control_ascii_and_short_lines_fold_as_before():
    label = "Cafe " * 20 + "end"
    assert _summary(label).endswith(label)
    assert cs._ics_fold("short") == "short"
    assert cs._ics_fold("a" * 160) == "a" * 75 + "\r\n " + "a" * 74 + "\r\n " + "a" * 11
