"""T3 (Tier 3) — D-6 queue-table inspector + D-5 FTS inspector. Two
read-only DB-inspection tools in Group B. D-6 reads the persisted
`queue` table (counts, retry pressure, stuck pending rows); D-5 reads
the history_fts FTS5 index health (availability, row-count drift,
integrity-check).
"""
import time

from bulk_downloader import db
from bulk_downloader import dev_suite as ds


# ── D-6 queue_table_inspect ────────────────────────────────────────

def test_queue_inspect_empty_db(clean_workdir):
    db.db_init()
    r = ds.queue_table_inspect()
    assert r["ok"] is True
    assert r["tool"] == "queue_table_inspect"
    assert r["total_rows"] == 0
    assert r["by_status"] == {}
    assert r["stuck_pending_rows"] == 0
    # a fresh schema must show no drift
    assert r["missing_columns"] == []
    assert r["unexpected_columns"] == []


def test_queue_inspect_counts_by_status_and_site(clean_workdir):
    db.db_init()
    db.queue_upsert("siteA", "https://a.example/1", status="pending")
    db.queue_upsert("siteA", "https://a.example/2", status="pending")
    db.queue_upsert("siteA", "https://a.example/3", status="done")
    db.queue_upsert("siteB", "https://b.example/1", status="error")
    r = ds.queue_table_inspect()
    assert r["total_rows"] == 4
    assert r["by_status"]["pending"] == 2
    assert r["by_status"]["done"] == 1
    assert r["by_status"]["error"] == 1
    assert r["by_site"]["siteA"] == 3
    assert r["by_site"]["siteB"] == 1


def test_queue_inspect_site_filter(clean_workdir):
    db.db_init()
    db.queue_upsert("siteA", "https://a.example/1", status="pending")
    db.queue_upsert("siteB", "https://b.example/1", status="pending")
    db.queue_upsert("siteB", "https://b.example/2", status="done")
    r = ds.queue_table_inspect(site_id="siteB")
    assert r["site_filter"] == "siteB"
    assert r["total_rows"] == 2
    # by_site is omitted when already filtered to one site
    assert "by_site" not in r


def test_queue_inspect_flags_stuck_pending_rows(clean_workdir):
    db.db_init()
    past = time.time() - 3600          # retry_after an hour in the past
    future = time.time() + 3600        # not yet due — must NOT count
    db.queue_upsert("siteA", "https://a.example/stuck",
                    status="pending", retry_after=past)
    db.queue_upsert("siteA", "https://a.example/waiting",
                    status="pending", retry_after=future)
    # a non-pending row with a past retry_after must not be flagged
    db.queue_upsert("siteA", "https://a.example/done",
                    status="done", retry_after=past)
    r = ds.queue_table_inspect()
    assert r["stuck_pending_rows"] == 1
    sample_urls = [row["url"] for row in r["oldest_pending_sample"]]
    assert "https://a.example/stuck" in sample_urls


def test_queue_inspect_retry_pressure(clean_workdir):
    db.db_init()
    db.queue_upsert("siteA", "https://a.example/1",
                    status="pending", retries=0)
    db.queue_upsert("siteA", "https://a.example/2",
                    status="pending", retries=7)
    r = ds.queue_table_inspect()
    assert r["max_retries"] == 7
    assert r["retry_distribution"]["0"] == 1
    assert r["retry_distribution"]["7"] == 1
    assert "high retry pressure" in r["verdict"]


def test_queue_inspect_sample_is_clamped(clean_workdir):
    db.db_init()
    for i in range(15):
        db.queue_upsert("siteA", f"https://a.example/{i}",
                        status="pending")
    r = ds.queue_table_inspect(sample=5)
    assert len(r["oldest_pending_sample"]) == 5
    # an absurd sample is clamped, never unbounded
    assert len(ds.queue_table_inspect(
        sample=99999)["oldest_pending_sample"]) <= 100


# ── D-5 fts_index_inspect ──────────────────────────────────────────

def test_fts_inspect_healthy_empty_index(clean_workdir):
    db.db_init()
    r = ds.fts_index_inspect()
    assert r["ok"] is True
    assert r["tool"] == "fts_index_inspect"
    # the sandbox SQLite has FTS5; db_init creates history_fts
    assert r["fts5_available"] is True
    assert r["fts_table_present"] is True
    assert r["history_row_count"] == 0
    assert r["indexed_doc_count"] == 0
    assert r["row_count_drift"] == 0
    assert r["integrity_check"] == "ok"
    assert "healthy" in r["verdict"]


def test_fts_inspect_stays_in_sync_after_history_writes(clean_workdir):
    db.db_init()
    # db_log feeds both history and the FTS mirror — must stay in sync
    db.db_log("siteA", "Site A", "https://a.example/v1", "done",
              filename="beach.mp4", message="ok")
    db.db_log("siteA", "Site A", "https://a.example/v2", "error",
              filename="pool.mp4", message="timeout")
    r = ds.fts_index_inspect()
    assert r["history_row_count"] == 2
    assert r["indexed_doc_count"] == 2
    assert r["row_count_drift"] == 0
    assert r["integrity_check"] == "ok"


def test_fts_inspect_detects_index_drift(clean_workdir):
    # COUNT(*) on a content-less external-content table reads through
    # to `history`, so it can never reveal a desync. The tool counts
    # distinct docs in the inverted index via fts5vocab — that DOES
    # catch a `history` row whose text never reached the FTS mirror.
    db.db_init()
    db.db_log("siteA", "Site A", "https://a.example/v1", "done",
              message="indexed via db_log")
    # a row inserted straight into history bypasses the FTS mirror,
    # so its text is absent from the inverted index
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id,site_name,url,status,message) "
            "VALUES('siteA','Site A','https://a.example/v2','done',"
            "'only in the content table')")
    r = ds.fts_index_inspect()
    assert r["history_row_count"] == 2
    # COUNT(*) is fooled — it reads through to history
    assert r["fts_count_star"] == 2
    # the vocab-based count is not — only 1 doc is genuinely indexed
    assert r["indexed_doc_count"] == 1
    assert r["row_count_drift"] == 1
    assert "drift" in r["verdict"]


def test_fts_inspect_reports_optimize_sentinel_age(clean_workdir):
    db.db_init()
    # no sentinel yet
    assert ds.fts_index_inspect()["last_optimize_age_hours"] is None
    # run the real optimize — it writes .fts_optimize_last
    db.db_fts_optimize(force=True)
    age = ds.fts_index_inspect()["last_optimize_age_hours"]
    assert age is not None
    assert age >= 0.0


# ── dev routes ─────────────────────────────────────────────────────

def test_queue_table_route(fresh_app):
    db.db_init()
    db.queue_upsert("siteA", "https://a.example/1", status="pending")
    j = fresh_app.get("/api/dev/queue_table").get_json()
    assert j["ok"] is True
    assert j["tool"] == "queue_table_inspect"
    assert "by_status" in j


def test_queue_table_route_site_filter(fresh_app):
    db.db_init()
    db.queue_upsert("siteA", "https://a.example/1", status="pending")
    db.queue_upsert("siteB", "https://b.example/1", status="pending")
    j = fresh_app.get("/api/dev/queue_table?site=siteB").get_json()
    assert j["site_filter"] == "siteB"
    assert j["total_rows"] == 1


def test_fts_index_route(fresh_app):
    db.db_init()
    j = fresh_app.get("/api/dev/fts_index").get_json()
    assert j["ok"] is True
    assert j["tool"] == "fts_index_inspect"
    assert "row_count_drift" in j


def test_t3_routes_are_dev_gated():
    import os
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/queue_table", "/api/dev/fts_index"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
