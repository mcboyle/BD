"""v3.43.13: SQLite contention regression test.

Pre-fix: db.db_conn opened connections with no busy_timeout and no WAL,
so concurrent worker writes triggered "database is locked" errors.
Symptom in user terminal:

    [7ef07d2c] queue persist failed: database is locked
    [7ef07d2c] queue persist failed: database is locked
    [7ef07d2c] queue persist failed: database is locked

WAL mode + busy_timeout=10000 fix the class of error.
"""
import threading


def test_db_conn_uses_wal_mode():
    """Verify the connection actually opens in WAL mode (set via PRAGMA
    in db_conn). If someone removes the PRAGMA, this test fails."""
    from bulk_downloader import db
    with db.db_conn() as cx:
        mode = cx.execute("PRAGMA journal_mode").fetchone()[0]
        # SQLite returns the current mode as a string. WAL is "wal".
        assert mode.lower() == "wal", \
            f"journal_mode is {mode!r}, expected 'wal' (db_conn must " \
            f"set PRAGMA journal_mode=WAL or concurrent writers will " \
            f"hit 'database is locked')"


def test_db_conn_has_busy_timeout():
    """busy_timeout must be >0 so concurrent writers wait rather than
    failing instantly."""
    from bulk_downloader import db
    with db.db_conn() as cx:
        timeout_ms = cx.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout_ms >= 1000, \
            f"busy_timeout is {timeout_ms}ms, expected >=1000ms"


def test_concurrent_writes_dont_lock():
    """5 threads × 20 writes each should all succeed without 'database
    is locked'. This is the real-world scenario: multiple worker threads
    persisting queue updates simultaneously."""
    from bulk_downloader import db
    db.db_init()
    errors = []
    def writer(thread_id):
        try:
            with db.db_conn() as cx:
                for i in range(20):
                    cx.execute(
                        "INSERT INTO history(site_id,site_name,url,status,"
                        "filename,file_size,message,screenshot) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (f"t{thread_id}", "test", f"http://x/{thread_id}/{i}",
                         "done", "f.mp4", 1000, "", ""),
                    )
        except Exception as e:
            errors.append(f"t{thread_id}: {e}")
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors, f"concurrent writes errored: {errors[:3]}"
    # And the rows actually landed
    with db.db_conn() as cx:
        n = cx.execute(
            "SELECT COUNT(*) FROM history WHERE site_id LIKE 't%'"
        ).fetchone()[0]
        assert n == 100, f"expected 100 rows, got {n}"
        # Cleanup
        cx.execute("DELETE FROM history WHERE site_id LIKE 't%'")
