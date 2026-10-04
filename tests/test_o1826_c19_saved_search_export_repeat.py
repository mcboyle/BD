"""O1826 C19 (M135 + M139): a failed bookkeeping UPDATE must not repeat work.

saved_searches.run_one advances last_seen_id/last_run_ts, then notifies or
enqueues. scheduled_exports.run_due_exports runs an export, then advances
next_run_ts. Both swallowed a failed UPDATE with a bare ``pass``, so the
cursor never moved and every later run re-notified / re-enqueued / re-exported
the same work with nothing logged.

The failure here is a real SQLite one: a BEFORE UPDATE trigger that RAISEs,
so the module's own UPDATE statement fails exactly as a locked or read-only
database would make it fail. No connection is mocked.
"""
import os

import pytest

from bulk_downloader import db as _db
from bulk_downloader import notify_apprise as _ap
from bulk_downloader import saved_searches as _ss
from bulk_downloader import scheduled_exports as _se

BD_GATE_SCOPE = "module"


@pytest.fixture(autouse=True)
def _isolate_db(clean_workdir):
    return clean_workdir


def _fail_updates_on(table):
    with _db.db_conn() as cx:
        cx.execute(f"CREATE TRIGGER c19_fail_{table} BEFORE UPDATE ON {table} "
                   "BEGIN SELECT RAISE(ABORT, 'c19 forced update failure'); END")


def _saved_search(monkeypatch, *, action):
    monkeypatch.setattr(_db, "db_search_fts",
                        lambda *a, **k: [{"id": 5, "filename": "a.mp4",
                                          "url": "https://c19.invalid/a.mp4"}])
    # run_one calls _ap.send_to_url, which notify_apprise does not define on
    # this base (every real notify dies in run_one's except). Install the seam
    # run_one looks up; the normal-run test below pins that it is called once.
    sent = []
    monkeypatch.setattr(_ap, "send_to_url",
                        lambda url, **k: sent.append(url) or True,
                        raising=False)
    sid = _ss.add(name=f"c19-{action}", query="needle",
                  notify_via="json://localhost/c19", action=action)
    assert sid is not None
    return sid, sent


def _last_seen(sid):
    with _db.db_conn() as cx:
        return cx.execute("SELECT last_seen_id FROM saved_searches WHERE id = ?",
                          (sid,)).fetchone()[0]


def test_failed_cursor_update_does_not_renotify(monkeypatch, capsys):
    sid, sent = _saved_search(monkeypatch, action="notify")
    _fail_updates_on("saved_searches")
    first = _ss.run_one(sid)
    second = _ss.run_one(sid)
    assert len(sent) <= 1, f"notified {len(sent)}x for one match: {first} {second}"
    assert "c19 forced update failure" in capsys.readouterr().err


def test_failed_cursor_update_does_not_reenqueue(monkeypatch, capsys):
    sid, _sent = _saved_search(monkeypatch, action="enqueue")
    enqueued = []
    _fail_updates_on("saved_searches")
    for _ in range(2):
        _ss.run_one(sid, enqueue_fn=lambda urls: enqueued.append(list(urls))
                    or len(urls))
    assert len(enqueued) <= 1, f"enqueued {len(enqueued)}x: {enqueued}"
    assert "c19 forced update failure" in capsys.readouterr().err


def test_normal_run_notifies_once_and_advances(monkeypatch):
    sid, sent = _saved_search(monkeypatch, action="notify")
    first = _ss.run_one(sid)
    second = _ss.run_one(sid)
    assert first["ok"] and first["notified"], first
    assert second["ok"] and second["new_matches"] == 0, second
    assert sent == ["json://localhost/c19"]
    assert _last_seen(sid) == 5


def test_normal_run_enqueues_once(monkeypatch):
    sid, _sent = _saved_search(monkeypatch, action="enqueue")
    enqueued = []
    for _ in range(2):
        _ss.run_one(sid, enqueue_fn=lambda urls: enqueued.append(list(urls))
                    or len(urls))
    assert enqueued == [["https://c19.invalid/a.mp4"]]
    assert _last_seen(sid) == 5


def _export_schedule(tmp_path):
    dest = tmp_path / "exports"
    sid = _se.add_schedule(label="c19", format="json", destination=str(dest),
                           cadence_hours=24)
    assert sid is not None
    return sid, dest


def test_failed_next_run_update_does_not_reexport(tmp_path, capsys):
    _sid, dest = _export_schedule(tmp_path)
    _fail_updates_on("scheduled_exports")
    _se.run_due_exports()
    _se.run_due_exports()
    files = sorted(os.listdir(dest))
    assert len(files) <= 1, f"exported {len(files)}x on a stuck schedule: {files}"
    assert "c19 forced update failure" in capsys.readouterr().err


def test_normal_export_runs_once_and_advances(tmp_path):
    sid, dest = _export_schedule(tmp_path)
    first = _se.run_due_exports()
    second = _se.run_due_exports()
    assert first["ran"] == 1 and second["checked"] == 0, (first, second)
    assert len(os.listdir(dest)) == 1
    with _db.db_conn() as cx:
        row = cx.execute("SELECT next_run_ts, last_run_ok FROM scheduled_exports "
                         "WHERE id = ?", (sid,)).fetchone()
    assert row[1] == 1
    assert row[0] > first["results"][0]["next_run_ts"] + 23 * 3600
