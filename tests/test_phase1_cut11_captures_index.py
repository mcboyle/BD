"""Phase 1 Cut 1.1 (v3.66.612): persistent `captures` index table + writer.

Pre-cut state (611): capture enumeration is dom_analyzer.scan_captures() — an
os.walk rebuilt into an in-memory, restart-ephemeral _SCAN_CACHE in app_captures,
only (re)populated by POST /api/captures/scan. Nothing survives a restart.

This cut adds a durable `captures` table to db.db_init() plus db-layer helpers
(db_captures_upsert / db_captures_all / db_captures_prune_missing) so the scan
result becomes persistent. The FS-walk stays as the reconcile PRODUCER; the table
becomes the durable store.

RED (these fail on pristine 611 — table + helpers do not exist yet).
"""
import os
import tempfile


def _isolated_db():
    """Point the DB at a fresh temp dir (chdir pattern used across the suite)."""
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import db
    db.db_init()
    return db


def test_captures_table_exists_after_db_init():
    """db_init() must create a `captures` table (additive CREATE TABLE IF NOT
    EXISTS, matching the history/queue/session_history pattern)."""
    db = _isolated_db()
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='captures'"
        ).fetchall()
    assert rows, "db_init() did not create a `captures` table"


def test_captures_upsert_then_all_roundtrips():
    """db_captures_upsert(rows) persists a capture row; db_captures_all()
    returns it. The row shape mirrors dom_analyzer.scan_captures output."""
    db = _isolated_db()
    row = {
        "rel_path": "captures/example.com/capture_1.wacz",
        "name": "capture_1.wacz",
        "dir": "captures",
        "host": "example.com",
        "captured_at": 1700000000.0,
        "size": 4096,
        "kind": "wacz",
        "redacted": False,
    }
    assert hasattr(db, "db_captures_upsert"), "db.db_captures_upsert missing"
    assert hasattr(db, "db_captures_all"), "db.db_captures_all missing"
    db.db_captures_upsert([row])
    out = db.db_captures_all()
    got = [r for r in out if r["rel_path"] == row["rel_path"]]
    assert len(got) == 1, f"expected exactly 1 row, got {len(got)}"
    assert got[0]["host"] == "example.com"
    assert got[0]["kind"] == "wacz"
    assert int(got[0]["size"]) == 4096


def test_captures_upsert_is_idempotent_on_rel_path():
    """Re-upserting the same rel_path UPDATES in place (rel_path is the PK) —
    no duplicate row, and the new metadata wins."""
    db = _isolated_db()
    base = {
        "rel_path": "captures/site/capture_2.wacz",
        "name": "capture_2.wacz", "dir": "captures", "host": "site",
        "captured_at": 1.0, "size": 100, "kind": "wacz", "redacted": False,
    }
    db.db_captures_upsert([base])
    updated = dict(base, size=200, redacted=True)
    db.db_captures_upsert([updated])
    out = [r for r in db.db_captures_all() if r["rel_path"] == base["rel_path"]]
    assert len(out) == 1, f"upsert duplicated the row (got {len(out)})"
    assert int(out[0]["size"]) == 200, "upsert did not update size in place"
    assert bool(out[0]["redacted"]) is True, "upsert did not update redacted"


def test_captures_all_filters_by_host_and_kind():
    """db_captures_all supports host/kind filters (the picker's facets)."""
    db = _isolated_db()
    rows = [
        {"rel_path": "captures/a/c1.wacz", "name": "c1.wacz", "dir": "captures",
         "host": "a", "captured_at": 3.0, "size": 1, "kind": "wacz", "redacted": False},
        {"rel_path": "captures/b/c2.wacz", "name": "c2.wacz", "dir": "captures",
         "host": "b", "captured_at": 2.0, "size": 1, "kind": "wacz", "redacted": False},
        {"rel_path": "captures/a/capture_3.json", "name": "capture_3.json", "dir": "captures",
         "host": "a", "captured_at": 1.0, "size": 1, "kind": "json", "redacted": False},
    ]
    db.db_captures_upsert(rows)
    a_only = db.db_captures_all(host="a")
    assert {r["rel_path"] for r in a_only} == {
        "captures/a/c1.wacz", "captures/a/capture_3.json"}, "host filter wrong"
    wacz_only = db.db_captures_all(kind="wacz")
    assert all(r["kind"] == "wacz" for r in wacz_only), "kind filter wrong"
    assert len(wacz_only) == 2


def test_captures_all_sorted_newest_first():
    """Default order is captured_at DESC (newest first), matching scan_captures."""
    db = _isolated_db()
    rows = [
        {"rel_path": f"captures/h/c{i}.wacz", "name": f"c{i}.wacz", "dir": "captures",
         "host": "h", "captured_at": float(i), "size": 1, "kind": "wacz", "redacted": False}
        for i in (1, 5, 3)
    ]
    db.db_captures_upsert(rows)
    got = [r["captured_at"] for r in db.db_captures_all()]
    assert got == sorted(got, reverse=True), f"not newest-first: {got}"


def test_captures_prune_missing_drops_absent_rows():
    """db_captures_prune_missing(seen) removes rows whose rel_path is NOT in the
    seen set — this is how a reconcile scan drops deleted captures."""
    db = _isolated_db()
    rows = [
        {"rel_path": "captures/x/keep.wacz", "name": "keep.wacz", "dir": "captures",
         "host": "x", "captured_at": 2.0, "size": 1, "kind": "wacz", "redacted": False},
        {"rel_path": "captures/x/gone.wacz", "name": "gone.wacz", "dir": "captures",
         "host": "x", "captured_at": 1.0, "size": 1, "kind": "wacz", "redacted": False},
    ]
    db.db_captures_upsert(rows)
    assert hasattr(db, "db_captures_prune_missing"), "db.db_captures_prune_missing missing"
    removed = db.db_captures_prune_missing({"captures/x/keep.wacz"})
    remaining = {r["rel_path"] for r in db.db_captures_all()}
    assert remaining == {"captures/x/keep.wacz"}, f"prune wrong; remaining={remaining}"
    assert removed == 1, f"prune should report 1 removed, got {removed}"
