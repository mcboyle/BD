"""Bug-hunt qti-queue-f6 (deep-queue-templates-io F6).

Per-URL custom headers ("url<TAB>Referer=...") lived only in the running
process: gone after a restart, absent from queue/export, absent from a saved
template. They are now a nullable queue column (JSON object text).

Covers: survive restart / export (+ import) / save_template (+ apply); an
old-schema DB upgrades in place; a build without the column still reads and
writes the upgraded DB. Real SiteRunner, scratch workdir + DB. No network.
"""
from __future__ import annotations

import json
import logging

import pytest

from bulk_downloader import app as app_module
from bulk_downloader import app_queue_templates, app_sites_queue, db
from bulk_downloader import queue_templates as qt
from bulk_downloader.runner import SiteRunner
from bulk_downloader.staging_ingest import StagingIngestPipeline

BD_GATE_SCOPE = "module"

SID = "qf6"
U1 = "https://a.test/1"
U2 = "https://a.test/2"
HDRS = {"Referer": "https://a.test/page?x=1", "Cookie": "s=abc; t=1"}
TAB1 = U1 + "\tReferer=https://a.test/page?x=1\tCookie=s=abc; t=1"
OLD_KEYS = ["force_download", "message", "priority", "status", "url"]

# The queue table as every build before this cut created it.
OLD_QUEUE_DDL = """CREATE TABLE queue(
    site_id TEXT NOT NULL, url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', message TEXT DEFAULT '',
    retries INTEGER DEFAULT 0, retry_after REAL DEFAULT 0,
    screenshot TEXT DEFAULT '', force_download INTEGER DEFAULT 0,
    priority TEXT DEFAULT '', ord INTEGER DEFAULT 0, filename TEXT DEFAULT '',
    listing_title TEXT DEFAULT '', file_size INTEGER DEFAULT 0,
    lane TEXT DEFAULT 'default', depends_on TEXT DEFAULT '',
    ts_added TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
    ts_updated TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),
    PRIMARY KEY(site_id, url))"""
# The two queue writes of the build before this cut, verbatim column lists.
OLD_INSERT = """INSERT OR REPLACE INTO queue
    (site_id,url,status,message,retries,retry_after,screenshot,
     force_download,priority,ord,filename,listing_title,file_size,lane,depends_on)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
OLD_BULK_CLAUSE = (
    "ON CONFLICT(site_id,url) DO UPDATE SET status='pending', message='', "
    "retries=0, retry_after=0, screenshot='', force_download=0, "
    "filename='', file_size=0, ord=excluded.ord, "
    "listing_title=excluded.listing_title, "
    "ts_added=strftime('%Y-%m-%dT%H:%M:%S','now'), "
    "ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now')")


def _new_runner(sid=SID):
    return SiteRunner(sid, {"name": sid})


@pytest.fixture
def runner(clean_workdir, monkeypatch):
    db.db_init()
    r = _new_runner()
    reg = {SID: r}
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: reg)
    monkeypatch.setattr(app_queue_templates, "_app_runners", lambda: reg)
    r._reg = reg
    return r


def _call(view, path, *args, method="POST", body=None):
    with app_module.app.test_request_context(path, method=method, json=body):
        out = view(*args)
    resp, code = out if isinstance(out, tuple) else (out, out.status_code)
    return resp.get_json(), code


def _export(sid=SID):
    return _call(app_sites_queue.api_queue_export, f"/api/sites/{sid}/queue/export",
                 sid, method="GET")[0]["rows"]


def _import(rows, sid=SID, mode="append"):
    return _call(app_sites_queue.api_queue_import, f"/api/sites/{sid}/queue/import",
                 sid, body={"mode": mode, "rows": rows})


def _stored(url, sid=SID):
    with db.db_conn() as cx:
        row = cx.execute("SELECT custom_headers FROM queue WHERE site_id=? AND url=?",
                         (sid, url)).fetchone()
    return row[0] if row else "NO ROW"


# ── survive 1: restart ────────────────────────────────────────────────

def test_headers_survive_a_restart(runner):
    runner.load_urls([TAB1, U2])
    assert runner.jobs[U1]["custom_headers"] == HDRS
    again = _new_runner()                              # a new process reads the DB
    assert again.jobs[U1]["custom_headers"] == HDRS
    assert list(again.jobs[U1]["custom_headers"]) == ["Referer", "Cookie"]   # order as entered
    assert "custom_headers" not in again.jobs[U2]      # control: none stays none
    assert json.loads(_stored(U1)) == HDRS
    assert _stored(U2) is None                         # NULL, the old default


# ── survive 2: queue/export, and back through queue/import ────────────

def test_headers_are_in_the_export_and_only_on_rows_that_have_them(runner):
    runner.load_urls([TAB1, U2])
    rows = {r["url"]: r for r in _export()}
    # queue-headers-redact r1: the names ride along, every value masked
    assert rows[U1]["custom_headers"] == {k: db.QUEUE_HEADER_MASK for k in HDRS}
    assert sorted(rows[U2]) == OLD_KEYS                # a plain row is unchanged
    assert sorted(set(rows[U1]) - {"custom_headers"}) == OLD_KEYS


def test_export_then_import_brings_the_headers_back(runner):
    runner.load_urls([TAB1, U2])
    runner.jobs[U1]["priority"] = "high"
    db.queue_upsert(SID, U1, priority="high")
    exported = _export()
    body, code = _import(exported, mode="replace")
    assert code == 200 and body["added"] == 2
    assert runner.jobs[U1]["custom_headers"] == HDRS
    assert runner.jobs[U1]["priority"] == "high"
    assert "custom_headers" not in runner.jobs[U2]
    assert _new_runner().jobs[U1]["custom_headers"] == HDRS      # and it is on disk


def test_import_row_field_wins_over_headers_written_in_the_url(runner):
    body, code = _import([{"url": U1 + "\tReferer=old\tX-Old=1",
                           "custom_headers": {"Referer": "new"}}])
    assert code == 200 and body["added"] == 1
    assert runner.jobs[U1]["custom_headers"] == {"Referer": "new"}


BAD_HEADERS = ["Referer=x", ["Referer"], 5, True, {"Referer": 5}, {"Referer": None},
               {"": "x"}, {"A=B": "x"}, {" A": "x"}, {"A": "x\ty"}, {"A": "x\ny"},
               {"A\r": "x"}, {"A": "x y"}, {"A": " x"}]


@pytest.mark.parametrize("bad", BAD_HEADERS, ids=repr)
@pytest.mark.parametrize("mode", ["append", "replace"])
def test_import_refuses_malformed_headers_before_changing_anything(runner, bad, mode):
    runner.load_urls([U2])
    body, code = _import([{"url": "https://a.test/ok"},
                          {"url": U1, "custom_headers": bad}], mode=mode)
    assert code == 400 and "custom_headers" in body["error"], body
    assert list(runner.jobs) == [U2]                   # replace did not wipe either
    assert [r["url"] for r in db.queue_load(SID)] == [U2]


@pytest.mark.parametrize("empty", [None, {}])
def test_import_row_with_no_headers_is_a_plain_row(runner, empty):
    assert _import([{"url": U1, "custom_headers": empty}])[1] == 200
    assert "custom_headers" not in runner.jobs[U1] and _stored(U1) is None


# ── survive 3: save_template, and back through apply ──────────────────

def test_headers_survive_save_template_and_apply(runner):
    runner.load_urls([TAB1, U2])
    runner.jobs[U1]["priority"] = "high"
    runner.jobs[U1]["force_download"] = True
    body, code = _call(app_sites_queue.api_queue_save_template,
                       f"/api/sites/{SID}/queue/save_template", SID, body={"name": "t"})
    assert code == 200 and body["saved"] == 2 and body["headers_dropped"] == 0
    t = qt.get(body["id"])
    assert t["urls"] == [TAB1, U2]
    assert t["priority_map"] == {U1: "high", U2: "normal"} and t["force_set"] == [U1]
    other = _new_runner("qf6b")
    runner._reg["qf6b"] = other
    out, code = _call(app_queue_templates.api_queue_template_apply,
                      f"/api/queue_templates/{body['id']}/apply/qf6b", body["id"], "qf6b")
    assert code == 200 and out["added"] == 2
    assert other.jobs[U1]["custom_headers"] == HDRS
    assert other.jobs[U1]["priority"] == "high" and other.jobs[U1]["force_download"] is True
    assert "custom_headers" not in other.jobs[U2]
    assert _new_runner("qf6b").jobs[U1]["custom_headers"] == HDRS


def test_a_header_that_cannot_be_stored_is_reported_not_silently_kept(runner, caplog):
    with caplog.at_level(logging.WARNING):
        runner.load_urls([U1 + "\t=novalue-name", U2 + "\tA=b"])
    assert runner.jobs[U1]["custom_headers"] == {"": "novalue-name"}   # in memory, as before
    assert any("cannot be stored" in r.getMessage() and U1 in r.getMessage()
               for r in caplog.records)
    assert _stored(U1) is None and json.loads(_stored(U2)) == {"A": "b"}
    assert sorted(_export()[0]) == OLD_KEYS
    body, _ = _call(app_sites_queue.api_queue_save_template,
                    f"/api/sites/{SID}/queue/save_template", SID, body={"name": "t"})
    assert body["headers_dropped"] == 1
    assert qt.get(body["id"])["urls"] == [U1, U2 + "\tA=b"]


# ── stale rows, direct writes ─────────────────────────────────────────

def test_a_re_added_url_does_not_inherit_a_stale_rows_headers(runner):
    runner.load_urls([TAB1])
    with runner._lock:                                 # job gone from memory, row left behind
        runner.jobs.clear(); runner.urls.clear()
    runner.load_urls([U1])                             # re-added without headers
    assert "custom_headers" not in runner.jobs[U1]
    assert _stored(U1) is None
    assert "custom_headers" not in _new_runner().jobs[U1]


def test_queue_upsert_writes_the_column_on_update_and_on_insert(runner):
    text = db.queue_headers_encode({"A": "b"})
    db.queue_upsert(SID, U1, status="pending", custom_headers=text)       # insert path
    assert json.loads(_stored(U1)) == {"A": "b"}
    db.queue_upsert(SID, U1, custom_headers=db.queue_headers_encode({"C": "d"}))  # update path
    assert json.loads(_stored(U1)) == {"C": "d"}
    db.queue_upsert(SID, U1, status="done")                               # unrelated update keeps it
    assert json.loads(_stored(U1)) == {"C": "d"}


def test_header_codec():
    assert db.queue_headers_encode({}) is None and db.queue_headers_encode(None) is None
    assert db.queue_headers_encode({"A": "x\ty"}) is None
    assert db.queue_headers_decode(db.queue_headers_encode(HDRS)) == HDRS
    for garbage in [None, "", "not json", "[]", '"x"', "5", '{"A": 5}', '{"": "x"}', 7, b"{}"]:
        assert db.queue_headers_decode(garbage) == {}, garbage
    assert db.queue_headers_entry(U1, HDRS) == TAB1
    assert db.queue_headers_entry(U1, None) == U1 and db.queue_headers_entry(U1, {"": "x"}) == U1
    assert db.queue_headers_error({"A": ""}) == "" and db.queue_headers_error({}) == ""


# ── schema: upgrade in place, and the old build on the new DB ─────────

def _columns():
    with db.db_conn() as cx:
        return [r[1] for r in cx.execute("PRAGMA table_info(queue)")]


def test_old_schema_db_upgrades_in_place(clean_workdir):
    with db.db_conn() as cx:
        cx.execute(OLD_QUEUE_DDL)
        cx.execute("INSERT INTO queue(site_id,url,status,priority,force_download,message)"
                   " VALUES(?,?,?,?,?,?)", (SID, U2, "done", "high", 1, "kept"))
    assert "custom_headers" not in _columns()
    # before the upgrade: a header batch still lands (headers not stored yet)
    db.queue_bulk_upsert(SID, [U1], ord_start=5, custom_headers={U1: HDRS})
    assert sorted(r["url"] for r in db.queue_load(SID)) == [U1, U2]
    db.db_init()                                       # the upgrade
    cols = _columns()
    assert cols.count("custom_headers") == 1
    db.db_init()                                       # idempotent
    assert _columns() == cols
    from bulk_downloader import dev_suite
    drift = dev_suite.queue_table_inspect()            # the schema-drift inspector agrees
    assert (drift["missing_columns"], drift["unexpected_columns"]) == ([], [])
    old = next(r for r in db.queue_load(SID) if r["url"] == U2)
    assert (old["status"], old["priority"], old["force_download"], old["message"],
            old["custom_headers"]) == ("done", "high", 1, "kept", None)
    r = _new_runner()
    assert r.jobs[U2]["status"] == "done" and "custom_headers" not in r.jobs[U2]
    r.load_urls(["https://a.test/3\tReferer=r"])
    assert _new_runner().jobs["https://a.test/3"]["custom_headers"] == {"Referer": "r"}


def test_staging_table_follows_a_column_added_on_the_same_connection(clean_workdir):
    """The staging TEMP table belongs to the connection. One created before
    the ALTER must not make the first header batch after it fail."""
    with db.db_conn() as cx:
        cx.execute(OLD_QUEUE_DDL)
    db.queue_bulk_upsert(SID, [U2])                    # staging table: old columns
    db.db_init()
    db.queue_bulk_upsert(SID, [U1], ord_start=1, custom_headers={U1: HDRS})
    assert json.loads(_stored(U1)) == HDRS and _stored(U2) is None


def test_a_build_without_the_column_reads_and_writes_the_upgraded_db(runner):
    """Downgrade: the previous build's queue statements, run verbatim against a
    DB this build has upgraded and written headers into."""
    runner.load_urls([TAB1, U2])
    with db.db_conn() as cx:
        got = cx.execute("SELECT * FROM queue WHERE site_id=? ORDER BY ord, ts_added",
                         (SID,)).fetchall()            # its queue_load
        assert len(got) == 2
        cx.execute(OLD_INSERT, (SID, "https://a.test/old-insert", "pending", "", 0, 0, "",
                                0, "", 9, "", "", 0, "default", ""))
        cx.execute("UPDATE queue SET status=?, ts_updated=strftime('%Y-%m-%dT%H:%M:%S','now')"
                   " WHERE site_id=? AND url=?", ("done", SID, U1))
        StagingIngestPipeline(cx, owns_connection=False).ingest_batch(
            "queue", [{"site_id": SID, "url": "https://a.test/old-bulk", "status": "pending",
                       "ord": 10, "listing_title": ""}], upsert_clause=OLD_BULK_CLAUSE)
        cx.execute("CREATE TABLE IF NOT EXISTS queue(site_id TEXT)")      # its db_init: no-op
    back = _new_runner()                               # this build again
    assert set(back.jobs) == {U1, U2, "https://a.test/old-insert", "https://a.test/old-bulk"}
    assert back.jobs[U1]["status"] == "done" and back.jobs[U1]["custom_headers"] == HDRS
    assert _stored("https://a.test/old-insert") is None
    assert _stored("https://a.test/old-bulk") is None
