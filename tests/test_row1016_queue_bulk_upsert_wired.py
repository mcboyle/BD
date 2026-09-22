"""Row 1016 (WIRE, RULING-0145): the product's bulk-enqueue path stages through the pipeline.

Caller under test: POST /api/bulk/enqueue -> SiteRunner.load_urls -> db.queue_bulk_upsert.
RED on BASE 5880aa85 for a behavioural reason: after the enqueue, the thread's leased history
connection carries NO temp staging table (base writes queue rows with a bare executemany), and
the staging telemetry reports 0 rows transferred through the queue path.
The row-722 control (re-added URL is a fresh job, untouched columns survive) is GREEN on both
trees and pins the semantics an OR REPLACE transfer would break.
"""
import pytest

pytestmark = pytest.mark.bd_module_wipe

BD_GATE_SCOPE = "module"


def _setup():
    # The bd_module_wipe marker (conftest) saves, wipes and restores sys.modules
    # around each test; no manual wipe here (v3.66.1034 leaker budget).
    from bulk_downloader import app as a
    from bulk_downloader import db
    c = a.app.test_client()
    r = c.post("/api/sites", json={"name": "Row1016Site"})
    sid = r.get_json()["id"]
    return c, sid, db


def _staging_tables(db):
    with db.db_conn() as cx:
        return [r[0] for r in cx.execute(
            "SELECT name FROM sqlite_temp_master WHERE type='table'")]


def _enqueue(c, sid, urls):
    r = c.post("/api/bulk/enqueue", json={"site_id": sid, "urls": urls})
    body = r.get_json()
    assert r.status_code == 200 and body.get("ok"), body
    return body


def test_bulk_enqueue_reaches_queue_through_the_staging_pipeline():
    c, sid, db = _setup()
    urls = [f"https://example.com/row1016/{i}" for i in range(50)]
    before = _staging_tables(db)
    assert not [n for n in before if "queue" in n], before

    stats_fn = getattr(db, "db_staging_ingest_stats", lambda: {})
    transferred_before = stats_fn().get("total_rows_transferred", 0)

    body = _enqueue(c, sid, urls)
    assert body["added"] == 50, body

    with db.db_conn() as cx:
        n = cx.execute("SELECT count(*) FROM queue WHERE site_id=?", (sid,)).fetchone()[0]
    assert n == 50, "control: the rows must land in queue on any tree"

    after = _staging_tables(db)
    assert [t for t in after if "queue" in t], (
        f"bulk enqueue wrote queue without a temp staging table on the leased connection "
        f"(module {db.__file__}); temp tables present: {after}"
    )
    transferred = stats_fn().get("total_rows_transferred", 0) - transferred_before
    assert transferred == 50, (
        f"staging telemetry saw {transferred} rows through the queue path, expected 50 "
        f"(module {db.__file__})"
    )


def test_readded_url_is_a_fresh_job_and_untouched_columns_survive():
    """Row 722 semantics through the caller. Also pins that the transfer is an
    upsert, not OR REPLACE: `priority` is not in the reset list and must survive."""
    c, sid, db = _setup()
    url = "https://example.com/row1016/readd"
    _enqueue(c, sid, [url])
    with db.db_conn() as cx:
        cx.execute(
            "UPDATE queue SET status='failed', message='boom', retries=2, retry_after=99, "
            "priority='high' WHERE site_id=? AND url=?", (sid, url))
    # The runner de-dupes against its live job map; drop the job so the URL is re-added.
    from bulk_downloader import app as a
    runner = a.runners[sid]
    with runner._lock:
        runner.jobs.pop(url, None)
        if url in runner.urls:
            runner.urls.remove(url)
    body = _enqueue(c, sid, [url])
    assert body["added"] == 1, body
    with db.db_conn() as cx:
        row = cx.execute(
            "SELECT status, message, retries, retry_after, priority FROM queue "
            "WHERE site_id=? AND url=?", (sid, url)).fetchone()
    assert tuple(row) == ("pending", "", 0, 0, "high"), row
