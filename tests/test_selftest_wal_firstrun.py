"""Cut 0.1 (ROB-1): selftest.check_database must not false-positive WARN on a
first-run / not-yet-created DB. db_conn() sets WAL on init; a missing DB is a
first-run condition (like missing cookies/sites_config), not a WAL misconfig."""
import os
import tempfile
from bulk_downloader import selftest


def test_check_database_missing_db_is_not_wal_warn():
    d = tempfile.mkdtemp(prefix="bd_wal_fr_")
    missing = os.path.join(d, "downloader_history.db")
    assert not os.path.exists(missing), "precondition: db must not exist"
    r = selftest.check_database(missing)
    # first-run (db not created yet) must NOT be reported as a WAL WARN
    assert r.get("status") != selftest.WARN, (
        "spurious first-run WARN: %r" % (r.get("message") or r.get("detail")))


def test_check_database_after_init_is_ok_wal():
    # positive control: once initialized, WAL is on and status is OK
    home = tempfile.mkdtemp(prefix="bd_wal_ok_")
    os.environ["BD_HOME"] = home
    from bulk_downloader import db
    db.db_init()
    r = selftest.check_database(db._resolve_db_path())
    assert r.get("status") == selftest.OK, r
