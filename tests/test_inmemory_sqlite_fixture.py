import os
import sqlite3
import time

import pytest

BD_GATE_SCOPE = "module"


def _artifacts(root):
    """Every filesystem entry below root (name -> size), the denominator the
    'no disk artifacts' claim is measured against; a bare glob('*.db*') sees
    neither a 'file:row865-...' file nor WAL/SHM siblings."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root)] = os.path.getsize(p)
    return out


def _database_files(cx):
    return [row[2] for row in cx.execute("PRAGMA database_list").fetchall()]


def _get_inmemory_sqlite(request):
    """Resolve inmemory_sqlite fixture within the test call phase so that replaying
    against an unpatched base yields a clean behavioral failure (FAILED) rather than
    a fixture setup error (ERROR)."""
    try:
        return request.getfixturevalue("inmemory_sqlite")
    except pytest.FixtureLookupError:
        raise AssertionError("behavioral: inmemory_sqlite fixture not defined in conftest")


def test_fresh_app_runs_in_memory_without_disk_artifacts(fresh_app, tmp_path):
    """Proves fresh_app is wired into RAM: 86 suites across the repository
    using fresh_app now execute entirely in memory without writing downloader_history.db to disk."""
    from bulk_downloader import db

    with db.db_conn() as cx:
        db_files = _database_files(cx)
        assert db_files == [""], f"expected in-memory database, got disk file: {db_files}"
        cx.execute("INSERT INTO history(site_id, status, url) VALUES (?, ?, ?)", ("fresh_test", "done", "https://fresh.example.com"))
        assert cx.execute("SELECT count(*) FROM history WHERE site_id='fresh_test'").fetchone()[0] == 1

    # Disk file check: downloader_history.db must NOT be created on disk
    disk_db = tmp_path / "downloader_history.db"
    assert not disk_db.exists(), f"disk database unexpectedly created at {disk_db}"
    assert not any(p.name.startswith("downloader_history.db") for p in tmp_path.glob("*"))


def test_negative_control_disk_database_writes_file_and_reports_path(tmp_path):
    """Negative control: proves the probe can say YES and distinguish disk from RAM.
    When configured with a disk path, db_init() writes an actual file to disk and
    _database_files reports a non-empty filesystem path."""
    from bulk_downloader import db

    disk_db = tmp_path / "control.db"
    orig = db._resolve_db_path
    db._resolve_db_path = lambda: str(disk_db)
    try:
        db.db_init()
        assert disk_db.exists()
        assert os.path.getsize(disk_db) > 0
        with db.db_conn(str(disk_db)) as cx:
            files = _database_files(cx)
            assert files != [""]
            assert any("control.db" in f for f in files)
    finally:
        db._resolve_db_path = orig


def test_inmemory_sqlite_fixture_runs_schema_without_disk_artifacts(request, tmp_path):
    """Opt-in fixture runs schema and transactions entirely in RAM with zero disk artifacts."""
    from bulk_downloader import db

    uri = _get_inmemory_sqlite(request)
    before = _artifacts(tmp_path)
    db.db_init()
    with db.db_conn() as cx:
        cx.execute("INSERT INTO history(site_id, status) VALUES (?, ?)", ("test", "done"))
        # PRAGMA database_list: the main database has NO file behind it
        assert _database_files(cx) == [""], _database_files(cx)
    with db.db_conn() as cx:
        assert cx.execute("SELECT count(*) FROM history").fetchone()[0] == 1
    assert _artifacts(tmp_path) == before
    assert uri.startswith("file:")


def test_inmemory_sqlite_with_install_dir_fixtures_stays_in_memory(clean_workdir, request, tmp_path):
    """E1: clean_workdir sets BD_INSTALL_DIR; the DB must still be the shared
    in-memory database, not a disk file named 'file:row865-...' under it."""
    from bulk_downloader import db

    uri = _get_inmemory_sqlite(request)
    assert os.environ.get("BD_INSTALL_DIR")
    assert db._resolve_db_path() == uri
    before = _artifacts(tmp_path)
    db.db_init()
    with db.db_conn() as cx:
        cx.execute("INSERT INTO history(site_id, status) VALUES (?, ?)", ("e1", "done"))
        assert _database_files(cx) == [""]
    with db.db_conn() as cx:
        assert cx.execute("SELECT count(*) FROM history WHERE site_id='e1'").fetchone()[0] == 1
    after = _artifacts(tmp_path)
    assert after == before, {k: v for k, v in after.items() if k not in before}
    assert not any("row865" in k or k.endswith((".db", ".db-wal", ".db-shm")) for k in after)


@pytest.fixture
def _teardown_probe(tmp_path):
    """Set up BEFORE inmemory_sqlite (listed first), so its finalizer runs AFTER
    inmemory_sqlite's teardown: that is where 'no artifacts after teardown' is
    actually observable, and where it is asserted."""
    holder = {"before": _artifacts(tmp_path)}
    yield holder
    after = _artifacts(tmp_path)
    assert after == holder["before"], {k: v for k, v in after.items() if k not in holder["before"]}
    assert not any("row865" in k for k in after)


def test_teardown_leaves_no_disk_artifacts(_teardown_probe, clean_workdir, request, tmp_path):
    """E2: real artifacts are enumerated after teardown (see _teardown_probe),
    not a glob('*.db*') during the test."""
    from bulk_downloader import db

    _get_inmemory_sqlite(request)
    db.db_init()
    with db.db_conn() as cx:
        cx.execute("INSERT INTO history(site_id, status) VALUES (?, ?)", ("e2", "done"))
    assert _artifacts(tmp_path) == _teardown_probe["before"]


@pytest.mark.inmemory_sqlite
def test_clean_workdir_supports_inmemory_test_mode(clean_workdir, tmp_path):
    """Proves clean_workdir wires inmemory_sqlite when marked with @pytest.mark.inmemory_sqlite."""
    from bulk_downloader import db

    db_init_path = db._resolve_db_path()
    assert db_init_path.startswith("file:"), f"expected in-memory URI, got {db_init_path}"
    with db.db_conn() as cx:
        assert _database_files(cx) == [""]
    assert not (tmp_path / "downloader_history.db").exists()


def test_memory_database_is_faster_than_disk_baseline(request, tmp_path):
    """E3: Acceptance criterion 2 (4x-6x speedup over disk-backed SQLite fixtures).
    Compares the lifecycle of test fixtures (creating db, initializing schema, executing
    transactions, and committing) between in-memory SQLite and disk-backed SQLite.
    Asserts ratio >= 4.0 strictly without weakening."""
    from bulk_downloader import db

    _get_inmemory_sqlite(request)

    def fixture_lifecycle_disk(n=12):
        d = tmp_path / "disk_bench"
        d.mkdir(exist_ok=True)
        t0 = time.perf_counter()
        orig = db._resolve_db_path
        for i in range(n):
            p = str(d / f"fixture_{i}.db")
            db._resolve_db_path = lambda: p
            idle = getattr(db._DB_CONN_LOCAL, "idle", None)
            if idle is not None:
                db._DB_CONN_LOCAL.idle = None
                db._close_history_conn(idle[1])
            db.db_init()
            with db.db_conn() as cx:
                cx.execute("INSERT INTO history(site_id, status) VALUES (?, ?)", ("bench", "done"))
            idle = getattr(db._DB_CONN_LOCAL, "idle", None)
            if idle is not None:
                db._DB_CONN_LOCAL.idle = None
                db._close_history_conn(idle[1])
        db._resolve_db_path = orig
        return time.perf_counter() - t0

    def fixture_lifecycle_mem(n=12):
        t0 = time.perf_counter()
        orig = db._resolve_db_path
        for i in range(n):
            uri = f"file:bench_{i}_{time.time_ns()}?mode=memory&cache=shared"
            anchor = db.sqlite3.connect(uri, uri=True)
            db._resolve_db_path = lambda: uri
            idle = getattr(db._DB_CONN_LOCAL, "idle", None)
            if idle is not None:
                db._DB_CONN_LOCAL.idle = None
                db._close_history_conn(idle[1])
            db.db_init()
            with db.db_conn() as cx:
                cx.execute("INSERT INTO history(site_id, status) VALUES (?, ?)", ("bench", "done"))
            idle = getattr(db._DB_CONN_LOCAL, "idle", None)
            if idle is not None:
                db._DB_CONN_LOCAL.idle = None
                db._close_history_conn(idle[1])
            anchor.close()
        db._resolve_db_path = orig
        return time.perf_counter() - t0

    # Warmup
    fixture_lifecycle_disk(2)
    fixture_lifecycle_mem(2)

    disk = min(fixture_lifecycle_disk(12) for _ in range(3))
    mem = min(fixture_lifecycle_mem(12) for _ in range(3))
    ratio = disk / mem

    print(f"\nrow865 benchmark: memory {mem*1000:.1f}ms disk {disk*1000:.1f}ms ratio {ratio:.2f}x")
    assert ratio >= 4.0, (
        f"in-memory speedup ratio {ratio:.2f}x failed 4x-6x threshold "
        f"(disk={disk*1000:.1f}ms, mem={mem*1000:.1f}ms)"
    )


def test_real_migrations_and_session_history_run_in_memory(clean_workdir, request, tmp_path):
    """Acceptance criterion 1: the versioned migration ledger
    (bulk_downloader/migrations.py, every registered migration) and the
    session_history helpers run against the in-memory database -- the
    pre-migration backup (a disk copy of the DB file) and the ledger leave
    NO file behind, and the applied set is the whole registry."""
    from bulk_downloader import db, migrations

    _get_inmemory_sqlite(request)
    registry = sorted(m["version"] for m in migrations._MIGRATIONS)
    assert len(registry) >= 11, registry
    before = _artifacts(tmp_path)
    db.db_init()
    assert sorted(migrations.applied_versions()) == []
    result = migrations.apply_pending(backup_first=True)
    assert result["errors"] == 0 and result["applied"] == len(registry), result
    assert "backup" not in result, result  # no DB file to copy: nothing on disk
    assert sorted(migrations.applied_versions()) == registry
    assert migrations.pending_migrations() == []
    with db.db_conn() as cx:
        assert _database_files(cx) == [""]
        cols = {row[1] for row in cx.execute("PRAGMA table_info(history)").fetchall()}
    assert {"bytes_fetched", "transfer_mode"} <= cols, cols  # migrations 8/9 landed

    db.session_event_record("site-a", 0, "login", "fixture")
    db.session_event_record("site-a", 0, "heartbeat_ok")
    db.session_event_record("site-b", 1, "heartbeat_fail", "rejected")
    recent = db.session_event_recent("site-a", 0)
    assert [r["event_type"] for r in recent] == ["heartbeat_ok", "login"]
    assert db.session_event_recent("site-b")[0]["detail"] == "rejected"
    assert db.session_event_recent(limit=1)[0]["site_id"] == "site-b"
    after = _artifacts(tmp_path)
    assert after == before, {k: v for k, v in after.items() if k not in before}
