"""hub-db-corruption-rootcause r1 (O3292, bd-worker-D76-D): G1, the root-cause guard.

The 10-16 hub DB corruption came from a second writer. On .51, pid 545325 ran downloader_ui.py from a checkout that was a
symlink to /mnt/bd-repo, the NFS export of the hub checkout. It wrote the hub's downloader_history.db while the hub
service wrote the same file locally (FINDING-r1.md H2, RULING-pm-B-r1.md H3). SQLite locks do not hold across hosts,
so the two writers never saw each other. db._open_history_conn is the single history-DB connection point. It must
refuse a write open when the file is on a network mount, or when BD_DB_OWNER_HOST names a different host. Read-only
opens are still allowed.

The mount table is a fixture file in /proc/self/mounts format, so the real parser decides which mount holds the path.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3

import pytest

import bulk_downloader.db as db


BD_GATE_SCOPE = "module"

# Resolved with a fallback so that at base the tests fail on the missing refusal (DID NOT RAISE), not on the missing name.
REFUSED = getattr(db, "HistoryDBWriteRefused", sqlite3.OperationalError)


def _mounts(tmp_path, fstype):
    """Return a mounts table: / local, tmp_path/share mounted as `fstype` from 10.0.70.164."""
    share = tmp_path / "share"
    share.mkdir()
    table = tmp_path / "mounts"
    real = os.path.realpath(share).replace(" ", "\\040")
    table.write_text(
        "/dev/sda1 / ext4 rw,relatime 0 0\n"
        "10.0.70.164:/home/mboyle/BulkDownloader %s %s rw,local_lock=none 0 0\n" % (real, fstype))
    return str(share / "downloader_history.db"), str(table)


@pytest.fixture()
def history_at(monkeypatch):
    saved = db.DB_PATH
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)

    def _bind(path, mounts):
        monkeypatch.setattr(db, "_PROC_MOUNTS", mounts, raising=False)
        db.close_all_pooled_connections()
        db.DB_PATH = path
        return path

    try:
        yield _bind
    finally:
        db.close_all_pooled_connections()
        db.DB_PATH = saved


def _write_one_row():
    with db.db_conn() as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS probe(id INTEGER PRIMARY KEY, v TEXT)")
        cx.execute("INSERT INTO probe(v) VALUES ('w')")


def test_local_mount_write_open_allowed(tmp_path, history_at):
    path = history_at(*_mounts(tmp_path, "ext4"))
    _write_one_row()
    assert sqlite3.connect(path).execute("SELECT v FROM probe").fetchall() == [("w",)]


def test_nfs_mount_write_open_refused_naming_the_mount(tmp_path, history_at):
    path, mounts = _mounts(tmp_path, "nfs4")
    history_at(path, mounts)
    with pytest.raises(REFUSED) as ei:
        _write_one_row()
    assert type(ei.value).__name__ == "HistoryDBWriteRefused", type(ei.value)
    msg = str(ei.value)
    assert msg.startswith("history DB write refused: "), msg
    assert "network mount %s (nfs4 from 10.0.70.164:/home/mboyle/BulkDownloader)" % os.path.realpath(
        os.path.dirname(path)) in msg, msg
    assert not os.path.exists(path), "the refusal must come before sqlite3.connect creates the file"


def test_owner_host_mismatch_refused_and_match_allowed(tmp_path, history_at, monkeypatch):
    history_at(*_mounts(tmp_path, "ext4"))
    monkeypatch.setenv("BD_DB_OWNER_HOST", "hub-mesh01-not-" + socket.gethostname())
    with pytest.raises(REFUSED, match=r"BD_DB_OWNER_HOST=hub-mesh01-not-.* but this host is "):
        _write_one_row()
    monkeypatch.setenv("BD_DB_OWNER_HOST", socket.gethostname())
    _write_one_row()


def test_read_only_open_allowed_on_nfs_mount(tmp_path, history_at):
    path, mounts = _mounts(tmp_path, "nfs4")
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, v TEXT)")
    seed.execute("INSERT INTO probe(v) VALUES ('seeded')")
    seed.commit()
    seed.close()
    history_at(path, mounts)

    async def _read():
        engine = db.async_engine_read_only()
        try:
            async with engine.connect() as cx:
                return [tuple(r) for r in await cx.exec_driver_sql("SELECT v FROM probe")]
        finally:
            await engine.dispose()

    assert asyncio.run(_read()) == [("seeded",)]
    with pytest.raises(REFUSED):
        _write_one_row()


# r2 (RULING-pm-B-r1.md 03:08Z, ORDER-r2.md): the bypasses the r1 lenses reproduced. Each test is named for the probe it mirrors:
# cx90 F1/F2 (.review/cx90-evidence/probe.py) and B91 S1/S2 (.review/VERDICT-security-B91-B-hub-db-rootcause-r1.md).


async def _async_write():
    engine = db.async_engine()
    try:
        async with engine.begin() as cx:
            await cx.exec_driver_sql("CREATE TABLE IF NOT EXISTS probe(id INTEGER PRIMARY KEY, v TEXT)")
            await cx.exec_driver_sql("INSERT INTO probe(v) VALUES ('async')")
    finally:
        await engine.dispose()


def test_file_symlink_nfs_write_open_refused(tmp_path, history_at):
    # cx90 F1 / B91 S2: a local downloader_history.db that is a symlink to a file on the NFS share.
    target, mounts = _mounts(tmp_path, "nfs4")
    sqlite3.connect(target).close()
    local = tmp_path / "local"
    local.mkdir()
    link = local / "downloader_history.db"
    link.symlink_to(target)
    history_at(str(link), mounts)
    with pytest.raises(REFUSED, match=r"network mount .*\(nfs4 from 10\.0\.70\.164:") as ei:
        _write_one_row()
    assert os.path.realpath(target) in str(ei.value), "the diagnostic must name the resolved file"
    assert sqlite3.connect("file:%s?mode=ro" % target, uri=True).execute(
        "SELECT name FROM sqlite_master").fetchall() == []


def test_dangling_file_symlink_nfs_refused_before_create(tmp_path, history_at):
    # Missing-file handling kept: the link's target does not exist yet; sqlite3.connect would create it on the share.
    target, mounts = _mounts(tmp_path, "nfs4")
    local = tmp_path / "local"
    local.mkdir()
    link = local / "downloader_history.db"
    link.symlink_to(target)
    history_at(str(link), mounts)
    with pytest.raises(REFUSED):
        _write_one_row()
    assert not os.path.exists(target), "the refusal must come before sqlite3.connect creates the target"


def test_file_uri_name_on_nfs_refused(tmp_path, history_at):
    # B91 S1: SQLite built with USE_URI treats a "file:" name as a URI even with uri=False.
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match=r"network mount "):
        db._open_history_conn("file:" + target)
    with pytest.raises(REFUSED, match=r"network mount "):
        db._open_history_conn("file://localhost" + target.replace("downloader_history", "downloader%5Fhistory"))
    assert not os.path.exists(target), "the refusal must come before sqlite3.connect creates the file"


def test_file_uri_name_local_allowed(tmp_path, history_at):
    path, mounts = _mounts(tmp_path, "ext4")
    history_at(path, mounts)
    cx = db._open_history_conn("file:" + path)
    cx.close()


def test_async_nfs_write_engine_refused(tmp_path, history_at):
    # cx90 F2: db.async_engine() opened its own aiosqlite connection and never consulted the guard.
    path, mounts = _mounts(tmp_path, "nfs4")
    history_at(path, mounts)
    with pytest.raises(REFUSED, match=r"network mount "):
        asyncio.run(_async_write())
    assert not os.path.exists(path)


def test_async_owner_mismatch_write_engine_refused_and_local_allowed(tmp_path, history_at, monkeypatch):
    path = history_at(*_mounts(tmp_path, "ext4"))
    monkeypatch.setenv("BD_DB_OWNER_HOST", "hub-mesh01-not-" + socket.gethostname())
    with pytest.raises(REFUSED, match=r"BD_DB_OWNER_HOST=hub-mesh01-not-"):
        asyncio.run(_async_write())
    assert not os.path.exists(path)
    monkeypatch.delenv("BD_DB_OWNER_HOST")
    asyncio.run(_async_write())
    assert sqlite3.connect(path).execute("SELECT v FROM probe").fetchall() == [("async",)]


def test_owner_host_compared_short_name_case_insensitive(tmp_path, history_at, monkeypatch):
    # B91 S3 (AMEND 03:2xZ): BD_DB_OWNER_HOST naming THIS host by FQDN or in another case must not refuse every open on
    # the owning host (it would brick hub boot); a different host is still refused, however it is spelled.
    history_at(*_mounts(tmp_path, "ext4"))
    short = socket.gethostname().split(".", 1)[0]
    for same in (short.upper(), short + ".lan", short.upper() + ".Example.COM"):
        monkeypatch.setenv("BD_DB_OWNER_HOST", same)
        db.close_all_pooled_connections()  # each spelling must pass a fresh open, not reuse a pooled one
        _write_one_row()
    for other in ("other-" + short, ("OTHER-" + short).upper() + ".lan", short + "x.lan"):
        monkeypatch.setenv("BD_DB_OWNER_HOST", other)
        db.close_all_pooled_connections()
        with pytest.raises(REFUSED, match=r"BD_DB_OWNER_HOST=.* but this host is "):
            _write_one_row()


# r3 (RULING-pm-B-r1.md 03:50Z, ORDER-r3.md): the guard must classify the file the opener will actually open.


def test_async_uri_and_memory_names_on_nfs_cwd_refused(tmp_path, history_at, monkeypatch):
    # cx97 F1: async_engine() read "file:...?mode=ro", "file:...?mode=memory" and ":memory:" as non-writable, but
    # orm_engine._sqlite_url resolves each against cwd into a plain read-write file -- on an NFS cwd, a write on the share.
    target, mounts = _mounts(tmp_path, "nfs4")
    share = os.path.dirname(target)
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.chdir(share)
    for name in ("file:downloader_history.db?mode=ro", "file:downloader_history.db?mode=memory", ":memory:"):
        history_at(name, mounts)
        with pytest.raises(REFUSED, match=r"network mount .*\(nfs4 from 10\.0\.70\.164:"):
            asyncio.run(_async_write())
        assert os.listdir(share) == [], (name, os.listdir(share))


def test_true_memory_db_on_nfs_cwd_still_opens(tmp_path, history_at, monkeypatch):
    # The control for the test above: a name SQLite really opens in memory is not refused and writes nothing to the share.
    target, mounts = _mounts(tmp_path, "nfs4")
    share = os.path.dirname(target)
    history_at(target, mounts)
    monkeypatch.chdir(share)
    for name in (":memory:", "file:downloader_history.db?mode=memory"):
        cx = db._open_history_conn(name)
        try:
            cx.execute("CREATE TABLE probe(v TEXT)")
            cx.execute("INSERT INTO probe(v) VALUES ('mem')")
            assert [tuple(r) for r in cx.execute("SELECT v FROM probe")] == [("mem",)]
        finally:
            cx.close()
    assert os.listdir(share) == []


def test_percent_encoded_uri_option_name_classified_as_sqlite_reads_it(tmp_path, history_at):
    # D98-D C1: SQLite percent-decodes option NAMES too, and a later mode=rwc overrides mode=memory.
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match=r"network mount "):
        db._open_history_conn("file:%s?mode=memory&m%%6Fde=rwc" % target)
    assert not os.path.exists(target), "the refusal must come before sqlite3.connect creates the file"
    assert db._sqlite_uri_file_path("file:%s?m%%6Fde=r%%6F" % target) is None
    assert db._sqlite_uri_file_path("file:%s?mode=memory&m%%6Fde=rwc" % target) == target
    assert db._sqlite_uri_file_path("file:%s?MODE=ro" % target) == target  # SQLite matches "mode" case-sensitively


# r4 (D110-D S1, ORDER-r4.md AMEND, PM 04:46Z): SQLite ends an option name at %00 and skips to '=', so mode%00=rwc
# reads as mode=rwc and the r3 URI parser let it write on nfs4. The authority is now a post-open check on the real
# connection: PRAGMA database_list names the file SQLite opened, and a connection that can write it is closed and refused.
# Each test below disables the pre-open (URI-parsing) check, so the post-open check alone must hold every prior probe.

_PROBES = (
    "file:{t}?mode=memory&mode%00=rwc",     # D110 S1: wrote f.db on nfs4
    "file:{t}?mode=memory&mode%00junk=rwc",  # D110 S1: wrote g.db on nfs4
    "file:{t}?mode=memory&m%6Fde=rwc",      # D98 C1
    "file:{t}?mode=memory&mode=rwc",        # duplicate mode, last wins
    "file:{t}%00junk",                      # D110 PROBE-pathnul: SQLite opens {t}
    "file:{t}",                             # B91 S1
    "{t}",                                  # plain path
)


def _refusal_in(exc):
    """The HistoryDBWriteRefused in ``exc``'s chain (SQLAlchemy wraps a DBAPI error raised in a connect listener)."""
    seen = exc
    while seen is not None and not isinstance(seen, REFUSED):
        seen = getattr(seen, "orig", None) or seen.__cause__
    return seen


def _nothing_written(path):
    """The refused open may leave the empty file connect created; it must hold no page, journal or WAL content."""
    for p in (path, path + "-journal", path + "-wal"):
        assert not os.path.exists(p) or os.path.getsize(p) == 0, (p, os.path.getsize(p))


@pytest.fixture()
def post_open_only(monkeypatch):
    monkeypatch.setattr(db, "_pre_open_refusal", lambda name: None, raising=False)  # raising=False: RED at r3 is the missing check


@pytest.mark.parametrize("probe", _PROBES)
def test_post_open_check_refuses_writable_nfs_open(tmp_path, history_at, post_open_only, probe):
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match=r"post-open: SQLite opened .*downloader_history\.db writable; .*network mount "):
        db._open_history_conn(probe.format(t=target))
    _nothing_written(target)


@pytest.mark.parametrize("probe", _PROBES[:2] + _PROBES[4:5])
def test_nul_probes_refused_with_pre_open_check_in_place(tmp_path, history_at, probe):
    # The shipped path, both checks on: the probes the r3 parser missed (DID NOT RAISE / ValueError) are refused.
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match=r"post-open: .*network mount "):
        db._open_history_conn(probe.format(t=target))
    _nothing_written(target)


def test_post_open_check_refuses_symlink_to_nfs(tmp_path, history_at, post_open_only):
    target, mounts = _mounts(tmp_path, "nfs4")
    link = tmp_path / "local.db"
    link.symlink_to(target)
    history_at(str(link), mounts)
    with pytest.raises(REFUSED, match=r"post-open: .*network mount "):
        _write_one_row()
    _nothing_written(target)


def test_post_open_check_refuses_owner_mismatch(tmp_path, history_at, post_open_only, monkeypatch):
    path, mounts = _mounts(tmp_path, "ext4")
    history_at(path, mounts)
    monkeypatch.setenv("BD_DB_OWNER_HOST", "not-" + socket.gethostname())
    with pytest.raises(REFUSED, match=r"post-open: .*BD_DB_OWNER_HOST="):
        _write_one_row()
    _nothing_written(path)


def _seed(path):
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, v TEXT)")
    seed.execute("INSERT INTO probe(v) VALUES ('seeded')")
    seed.commit()
    seed.close()
    return os.path.getsize(path)


@pytest.mark.parametrize("query", ("mode=ro", "immutable=1"))
def test_post_open_check_keeps_read_only_nfs_open(tmp_path, history_at, post_open_only, query):
    # Control: a connection SQLite opened read-only cannot write the share, so it is kept, and the probe left no journal.
    target, mounts = _mounts(tmp_path, "nfs4")
    size = _seed(target)
    history_at(target, mounts)
    cx = db._open_history_conn("file:%s?%s" % (target, query))
    try:
        assert [tuple(r) for r in cx.execute("SELECT v FROM probe")] == [("seeded",)]
    finally:
        cx.close()
    assert os.path.getsize(target) == size
    assert not os.path.exists(target + "-journal")


def test_post_open_check_keeps_memory_and_local_opens(tmp_path, history_at, post_open_only, monkeypatch):
    target, mounts = _mounts(tmp_path, "nfs4")
    share = os.path.dirname(target)
    history_at(target, mounts)
    monkeypatch.chdir(share)
    for name in (":memory:", "file:downloader_history.db?mode=memory"):
        cx = db._open_history_conn(name)
        cx.execute("CREATE TABLE probe(v TEXT)")
        cx.close()
    assert os.listdir(share) == []
    local = tmp_path / "local"
    local.mkdir()
    cx = db._open_history_conn(str(local / "l.db"))
    cx.execute("CREATE TABLE probe(v TEXT)")
    cx.commit()
    cx.close()
    assert os.path.getsize(local / "l.db") > 0


def test_conn_can_write_reports_read_only(tmp_path):
    path = str(tmp_path / "w.db")
    _seed(path)
    for name, uri, expected in ((path, False, True), ("file:%s?mode=ro" % path, True, False),
                                ("file:%s?immutable=1" % path, True, False)):
        cx = sqlite3.connect(name, uri=uri)
        try:
            assert db._conn_can_write(cx.cursor()) is expected, name
            assert not cx.in_transaction
        finally:
            cx.close()
    empty = str(tmp_path / "empty.db")
    cx = sqlite3.connect(empty)
    try:
        assert db._conn_can_write(cx.cursor()) is True
    finally:
        cx.close()
    assert os.path.getsize(empty) == 0, "the CREATE TABLE probe on an empty file is rolled back"


def test_async_engine_post_open_check_refuses_nfs_and_keeps_read_only(tmp_path, history_at, post_open_only):
    target, mounts = _mounts(tmp_path, "nfs4")
    size = _seed(target)
    history_at(target, mounts)
    with pytest.raises(Exception) as excinfo:
        asyncio.run(_async_write())
    refusal = _refusal_in(excinfo.value)
    assert refusal is not None and "post-open: " in str(refusal), repr(excinfo.value)
    assert os.path.getsize(target) == size

    async def _read():
        engine = db.async_engine_read_only()
        try:
            async with engine.connect() as cx:
                return [tuple(r) for r in await cx.exec_driver_sql("SELECT v FROM probe")]
        finally:
            await engine.dispose()

    assert asyncio.run(_read()) == [("seeded",)]


# ---- r5 (cx135 F1-F3 on r4, PM 05:55Z AMEND): boot refusal choke point, scheduled raw writer, ro opens, bytes paths.

import ast  # noqa: E402
import glob  # noqa: E402
import hashlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOT_REFUSED = r"refused to start: "


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _wal_with_frames(path):
    """A WAL history DB whose frames are not yet in the main file; the returned connection keeps them there."""
    keep = sqlite3.connect(path)
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, v TEXT)")
    keep.execute("INSERT INTO probe(v) VALUES ('framed')")
    keep.commit()
    assert os.path.getsize(path + "-wal") > 0
    return keep


def test_scheduled_wal_checkpoint_refused_on_nfs(tmp_path, history_at):
    # cx135 F1: bg_scheduler -> db_maintenance.run_scheduled_wal_flush -> AsyncWalFlusherPipeline._execute_checkpoint
    # opened sqlite3 raw and checkpointed the NFS history DB (main file changed).
    from bulk_downloader.async_wal_flusher import AsyncWalFlusherPipeline
    target, mounts = _mounts(tmp_path, "nfs4")
    keep = _wal_with_frames(target)
    try:
        history_at(target, mounts)
        before = _sha(target)
        res = AsyncWalFlusherPipeline()._execute_checkpoint(target, "PASSIVE", "r5-f1")
        assert not res.success
        assert "history DB write refused: post-open: " in (res.error or ""), res
        assert _sha(target) == before, "the refused checkpoint changed the NFS main file"
    finally:
        keep.close()


def test_scheduled_wal_checkpoint_local_control(tmp_path, history_at):
    from bulk_downloader.async_wal_flusher import AsyncWalFlusherPipeline
    target, mounts = _mounts(tmp_path, "ext4")
    keep = _wal_with_frames(target)
    try:
        history_at(target, mounts)
        res = AsyncWalFlusherPipeline()._execute_checkpoint(target, "PASSIVE", "r5-f1-ctl")
        assert res.success and res.checkpointed_frames > 0, res
    finally:
        keep.close()


# Every raw SQLite connect / engine call site in the package, by enclosing function. A NEW site fails here until it is
# classified: "seam" (is the guarded opener), "boot" (history DB, only reached in a process past the boot refusal),
# "other" (never the history DB). A history writer outside the seam must call db.guarded_connect instead.
# r6: backup._snapshot_db, database.get_connection, selftest._classify_db/check_database now open through
# db.guarded_connect, so they are no longer raw sites (tests/test_hub_db_rootcause_static_opener_gate.py gates the rest).
# r7 (cx230): backup._restore_db and migrations._restore_db_from_backup open through db.guarded_connect too; the two
# sqlite_cdc reads are mode=ro URIs.
_RAW_SITES = {
    "backup_verify.py:verify_db_dump": "other",  # mode=ro URI on a backup copy
    "cas_ledger.py:_get_conn": "other",         # <db dir>/cas ledger
    "connection_pool.py:_open_raw_conn": "seam",  # db.py binds connector=_open_history_conn; raw branch is other DBs
    "db.py:_open_history_conn": "seam",
    "dedup.py:_conn": "other",                  # video_hashes.db
    "human_challenge.py:cookies_on_disk": "other",  # a copy of a browser cookie DB
    "ledger_reconcile.py:digest_from_path": "other",  # mode=ro URI
    "migrations.py:apply_pending": "other",     # :memory: dry run
    "orm_engine.py:create_async_engine_for": "seam",  # db.async_engine* wrap it in _guard_engine_connections
    "sqlite_cdc.py:_read_table_snapshot": "boot",  # r7: mode=ro URI, path percent-quoted
    "sqlite_cdc.py:_table_columns": "boot",  # r7: mode=ro URI, path percent-quoted
    "telemetry_db.py:_get_connection": "other",  # telemetry DB / :memory:
    "vector_index.py:sqlite_vec_available": "other",  # :memory:
}


def _raw_connect_sites():
    raw, engines = {"sqlite3", "_sqlite3", "aiosqlite"}, {"create_engine", "create_async_engine"}
    pkg = os.path.join(REPO, "bulk_downloader")
    found = set()
    for path in sorted(glob.glob(os.path.join(pkg, "**", "*.py"), recursive=True)):
        rel = os.path.relpath(path, pkg)
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())

        def walk(node, fn):
            for child in ast.iter_child_nodes(node):
                name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(child, ast.Call):
                    f = child.func
                    if ((isinstance(f, ast.Attribute) and f.attr == "connect"
                         and isinstance(f.value, ast.Name) and f.value.id in raw)
                            or (isinstance(f, ast.Name) and f.id in engines)
                            or (isinstance(f, ast.Attribute) and f.attr in engines)):
                        found.add("%s:%s" % (rel, name))
                walk(child, name)
        walk(tree, "<module>")
    return found


def test_every_raw_history_opener_is_classified():
    found = _raw_connect_sites()
    assert found == set(_RAW_SITES), ("unclassified raw sqlite sites (route a history writer through "
                                      "db.guarded_connect): new=%s gone=%s"
                                      % (sorted(found - set(_RAW_SITES)), sorted(set(_RAW_SITES) - found)))


def test_async_wal_flusher_goes_through_the_guarded_connect():
    src = open(os.path.join(REPO, "bulk_downloader", "async_wal_flusher.py"), encoding="utf-8").read()
    assert "guarded_connect(" in src


@pytest.mark.parametrize("query", ("mode=ro", "immutable=1"))
def test_read_only_nfs_open_allowed_with_shipped_pre_open_check(tmp_path, history_at, query):
    # cx135 F2: r4's advisory pre-open refusal denied an existing NFS file opened immutable=1 before the post-open
    # authority could see it was read-only. No post_open_only mask here: the shipped path must keep the read.
    target, mounts = _mounts(tmp_path, "nfs4")
    size = _seed(target)
    history_at(target, mounts)
    cx = db._open_history_conn("file:%s?%s" % (target, query))
    try:
        assert [tuple(r) for r in cx.execute("SELECT v FROM probe")] == [("seeded",)]
    finally:
        cx.close()
    assert os.path.getsize(target) == size


def test_async_engine_read_only_kwarg_on_nfs_reads(tmp_path, history_at):
    # cx135 F2: async_engine(read_only=True) classified the writable target and refused a true read-only engine.
    target, mounts = _mounts(tmp_path, "nfs4")
    size = _seed(target)
    history_at(target, mounts)

    async def _read():
        engine = db.async_engine(read_only=True)
        try:
            async with engine.connect() as cx:
                return [tuple(r) for r in await cx.exec_driver_sql("SELECT v FROM probe")]
        finally:
            await engine.dispose()

    assert asyncio.run(_read()) == [("seeded",)]
    assert os.path.getsize(target) == size


def test_missing_nfs_file_still_refused_before_create(tmp_path, history_at):
    # The relaxed pre-open check still stops connect from creating a new history file on the share.
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match=r"network mount "):
        db._open_history_conn(target)
    assert not os.path.exists(target)


def test_bytes_local_path_keeps_base_behaviour(tmp_path, history_at):
    # cx135 F3: _history_write_refusal called bytes.startswith("file:") -> TypeError before open.
    target, mounts = _mounts(tmp_path, "ext4")
    history_at(target, mounts)
    with db.db_conn(os.fsencode(str(tmp_path / "local.db"))) as cx:
        assert [tuple(r) for r in cx.execute("SELECT 7")] == [(7,)]


def test_bytes_nfs_path_refused(tmp_path, history_at):
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match=r"network mount "):
        db._open_history_conn(os.fsencode(target))


# -- boot refusal: the single choke point (PM 05:55Z (1)). Every entrypoint that can write the history DB refuses to
#    start, before any other boot step, when realpath(history DB) is on a network FS or BD_DB_OWNER_HOST mismatches.

def test_boot_refusal_classifies_nfs_symlink_owner_and_local(tmp_path, history_at, monkeypatch):
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    assert "network mount " in (db.history_boot_refusal() or "")
    link = str(tmp_path / "local-link.db")
    os.symlink(target, link)
    assert "resolves to " in (db.history_boot_refusal(link) or "")
    assert "network mount " in (db.history_boot_refusal(os.fsencode(target)) or "")
    assert "network mount " in (db.history_boot_refusal("file:%s?mode=ro" % target) or ""), \
        "a process configured with a read-only URI still boots as a writer"
    local = str(tmp_path / "local.db")
    assert db.history_boot_refusal(local) is None
    monkeypatch.setenv("BD_DB_OWNER_HOST", "not-" + socket.gethostname())
    assert "BD_DB_OWNER_HOST=not-" in (db.history_boot_refusal(local) or "")
    assert not os.path.exists(target) and not os.path.exists(local), "classification opens nothing"


def test_app_boot_once_refuses_before_any_boot_step(tmp_path, history_at, monkeypatch):
    from bulk_downloader import app as app_mod
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    steps = []
    monkeypatch.setattr(app_mod, "_install_secret_key", lambda: steps.append("secret_key"))
    monkeypatch.setattr(app_mod, "db_init", lambda *a, **k: steps.append("db_init"))
    with pytest.raises(REFUSED, match="app.boot_once " + BOOT_REFUSED + ".*network mount "):
        app_mod.boot_once(force=True)
    assert steps == [] and not os.path.exists(target)


def test_app_boot_once_refuses_owner_mismatch(tmp_path, history_at, monkeypatch):
    from bulk_downloader import app as app_mod
    target, mounts = _mounts(tmp_path, "ext4")
    history_at(target, mounts)
    monkeypatch.setenv("BD_DB_OWNER_HOST", "not-" + socket.gethostname())
    steps = []
    monkeypatch.setattr(app_mod, "_install_secret_key", lambda: steps.append("secret_key"))
    with pytest.raises(REFUSED, match="app.boot_once " + BOOT_REFUSED + "BD_DB_OWNER_HOST=not-"):
        app_mod.boot_once(force=True)
    assert steps == [] and not os.path.exists(target)


def test_bg_scheduler_start_refuses_and_local_control_starts(tmp_path, history_at, monkeypatch):
    from bulk_downloader import bg_scheduler
    assert not bg_scheduler._started
    target, mounts = _mounts(tmp_path, "nfs4")
    history_at(target, mounts)
    with pytest.raises(REFUSED, match="bg_scheduler.start " + BOOT_REFUSED + ".*network mount "):
        bg_scheduler.start()
    assert not bg_scheduler._started
    history_at(str(tmp_path / "local.db"), mounts)
    try:
        bg_scheduler.start()
        assert bg_scheduler._started
    finally:
        bg_scheduler.stop()


def test_downloader_ui_main_exits_nonzero_on_owner_mismatch(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update(BD_INSTALL_DIR=str(tmp_path), BD_DB_OWNER_HOST="not-" + socket.gethostname(), BD_HOST="127.0.0.1",
               BD_PORT="1", BD_HEADLESS="1", BD_DISABLE_KEEPALIVE="1", BD_ENVFILE="/dev/null", MOD3_PG_DSN="",
               LC_ALL="C")
    p = subprocess.run([sys.executable, os.path.join(REPO, "downloader_ui.py")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 3, (p.returncode, p.stderr[-2000:])
    assert "downloader_ui " + BOOT_REFUSED.strip() in p.stderr and "BD_DB_OWNER_HOST=not-" in p.stderr, p.stderr[-2000:]
    assert not os.path.exists(tmp_path / "downloader_history.db")


# ---- r5 AMEND (D136-D SEC S1): the r4 writability probe ran SQL on the connection it was about to refuse; on a stale
# -wal or a hot -journal that first read ran WAL recovery / hot-journal rollback and wrote the NFS main file.

_HOT = r'''
import os, sqlite3, sys
path, kind = sys.argv[1], sys.argv[2]
cx = sqlite3.connect(path, isolation_level=None)
if kind == "walhot":
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
cx.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, v TEXT)")
cx.execute("INSERT INTO probe(v) VALUES ('committed')")
if kind == "jhot":
    cx.execute("PRAGMA cache_size=2")  # spill: dirty pages reach the main file, the synced journal holds the originals
    cx.execute("BEGIN")
    for _ in range(400):
        cx.execute("INSERT INTO probe(v) VALUES (?)", ("x" * 2000,))
os._exit(0)  # no close: the WAL is never checkpointed / the transaction never ends
'''
_SIDECAR = {"walhot": "-wal", "jhot": "-journal"}


def _hot(path, kind):
    """A crashed writer's leftovers at ``path``: committed frames only in -wal, or a hot -journal over a spilled main."""
    subprocess.run([sys.executable, "-c", _HOT, path, kind], check=True, timeout=60)
    side = path + _SIDECAR[kind]
    assert os.path.getsize(side) > 0, "precondition: %s left no %s" % (kind, side)
    return side


def _state(path, side):
    st = os.stat(path)
    return _sha(path), st.st_mtime_ns, os.path.exists(side) and _sha(side)


@pytest.mark.parametrize("kind", sorted(_SIDECAR))
def test_s1_positive_control_plain_read_recovers_and_writes_main(tmp_path, kind):
    # Rule 7: the fixture is live -- one SELECT on a plain connection runs recovery, changes main, removes the sidecar.
    path = str(tmp_path / "downloader_history.db")
    side = _hot(path, kind)
    before = _state(path, side)
    cx = sqlite3.connect(path)
    cx.execute("SELECT count(*) FROM probe").fetchone()
    cx.close()
    after = _state(path, side)
    assert after[0] != before[0] and after[2] is False, (kind, before, after)


_S1_NAMES = (
    "file:{t}?mode=memory&mode%00=rwc",          # S1a
    "file:{t}%00junk",                           # S1c
    "file:{t}?mode=memory&mode%00=rwc&nolock=1",  # S1a + nolock
    "file:{t}%00junk?nolock=1",                  # S1c + nolock
    "file:{t}?nolock=1",
    "file:{t}",
    "{t}",
)


@pytest.mark.parametrize("kind", sorted(_SIDECAR))
@pytest.mark.parametrize("name", _S1_NAMES)
def test_s1_refusal_leaves_hot_nfs_db_untouched(tmp_path, history_at, kind, name):
    # Shipped path, both checks on (F2 lets every existing NFS file through to the post-open authority).
    target, mounts = _mounts(tmp_path, "nfs4")
    side = _hot(target, kind)
    history_at(target, mounts)
    before = _state(target, side)
    with pytest.raises(REFUSED, match=r"post-open: SQLite opened .*downloader_history\.db writable; .*network mount "):
        db._open_history_conn(name.format(t=target))
    assert _state(target, side) == before, "the refused open ran recovery: main bytes/mtime or %s changed" % side


@pytest.mark.parametrize("kind", sorted(_SIDECAR))
def test_s1_guarded_connect_and_async_engine_leave_hot_nfs_db_untouched(tmp_path, history_at, kind):
    target, mounts = _mounts(tmp_path, "nfs4")
    side = _hot(target, kind)
    history_at(target, mounts)
    before = _state(target, side)
    for name, kw in ((target, {}), ("file:%s?mode=memory&mode%%00=rwc&nolock=1" % target, {"uri": True})):
        with pytest.raises(REFUSED, match=r"post-open: .*network mount "):
            db.guarded_connect(name, **kw)
        assert _state(target, side) == before, ("guarded_connect", name)
    with pytest.raises(Exception) as excinfo:
        asyncio.run(_async_write())
    refusal = _refusal_in(excinfo.value)
    assert refusal is not None and "post-open: " in str(refusal), repr(excinfo.value)
    assert _state(target, side) == before, "async_engine"


@pytest.mark.parametrize("kind", sorted(_SIDECAR))
@pytest.mark.parametrize("query", ("mode=ro", "immutable=1"))
def test_s1_read_only_open_of_hot_nfs_db_kept_without_writing(tmp_path, history_at, kind, query):
    # Control: the post-open authority keeps a read-only open of the same hot file, and deciding so wrote nothing.
    target, mounts = _mounts(tmp_path, "nfs4")
    side = _hot(target, kind)
    history_at(target, mounts)
    before = _state(target, side)
    db.guarded_connect("file:%s?%s" % (target, query), uri=True).close()
    assert _state(target, side) == before


def test_s1_conn_can_write_runs_nothing_but_database_list(tmp_path):
    # The oracle on a plain read-write handle over a hot journal: says writable, and leaves the journal hot.
    path = str(tmp_path / "w.db")
    side = _hot(path, "jhot")
    before = _state(path, side)
    statements = []
    cx = sqlite3.connect(path)
    cx.set_trace_callback(statements.append)
    try:
        assert db._conn_can_write(cx.cursor()) is True
    finally:
        cx.close()
    assert statements == ["PRAGMA database_list"], statements
    assert _state(path, side) == before


# r6 (PM AMEND 07:3xZ, D159-D SEC S1): boot_once and /api/selftest handed selftest constants.DB_PATH, the bare
# "downloader_history.db" that sqlite resolves against the cwd, not db._resolve_db_path(). The boot refusal judged
# the resolved file; selftest then opened a DIFFERENT one raw. With BD_INSTALL_DIR (or DB_PATH) local and the cwd an
# NFS checkout, the self-test opened the NFS file read-write. Each entrypoint must hand selftest the resolved path,
# and selftest's own opens go through db.guarded_connect.

class _SelftestReached(Exception):
    pass


def _nfs_cwd_local_history(tmp_path, history_at, monkeypatch):
    """History DB bound to a local file; cwd is the NFS share, holding its own downloader_history.db."""
    nfs_db, mounts = _mounts(tmp_path, "nfs4")
    _seed(nfs_db)
    local = tmp_path / "local"
    local.mkdir()
    history_at(str(local / "downloader_history.db"), mounts)
    monkeypatch.chdir(os.path.dirname(nfs_db))
    return nfs_db, db._resolve_db_path()


def _capture_run_all(monkeypatch, module, seen):
    def run_all(**kw):
        seen.append(kw.get("db_path"))
        raise _SelftestReached()
    monkeypatch.setattr(module, "run_all", run_all)


def test_app_boot_once_selftest_gets_the_resolved_db_not_the_cwd_file(tmp_path, history_at, monkeypatch):
    from bulk_downloader import app as app_mod
    nfs_db, resolved = _nfs_cwd_local_history(tmp_path, history_at, monkeypatch)
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app_mod, "_install_secret_key", lambda: None)
    seen = []
    _capture_run_all(monkeypatch, app_mod._selftest, seen)
    with pytest.raises(_SelftestReached):
        app_mod.boot_once(force=True)
    assert seen and os.path.abspath(seen[0]) == os.path.abspath(resolved) != os.path.abspath(nfs_db), (
        "D159 S1: boot_once self-test opens %r, the boot refusal judged %r" % (seen, resolved))


def test_api_selftest_gets_the_resolved_db_not_the_cwd_file(tmp_path, history_at, monkeypatch):
    from flask import Flask
    from bulk_downloader import app_selftest
    nfs_db, resolved = _nfs_cwd_local_history(tmp_path, history_at, monkeypatch)
    monkeypatch.setattr(app_selftest, "_capture_store_root_for_selftest", lambda: str(tmp_path), raising=False)
    seen = []
    _capture_run_all(monkeypatch, app_selftest._selftest, seen)
    flask_app = Flask("r6-selftest")
    flask_app.register_blueprint(app_selftest.selftest_bp)
    flask_app.testing = False
    flask_app.test_client().get("/api/selftest")
    assert seen and os.path.abspath(seen[0]) == os.path.abspath(resolved) != os.path.abspath(nfs_db), (
        "D159 S1: /api/selftest opens %r, not the resolved history DB %r" % (seen, resolved))


def test_selftest_database_checks_refuse_nfs_file_and_never_call_it_corrupt(tmp_path, history_at):
    from bulk_downloader import selftest
    nfs_db, mounts = _mounts(tmp_path, "nfs4")
    _seed(nfs_db)
    history_at(nfs_db, mounts)
    before = _sha(nfs_db)
    res = selftest.check_database(nfs_db)
    assert res["status"] != selftest.OK and "network mount " in res["message"], res
    verdict, why = selftest._classify_db(nfs_db)
    assert verdict == selftest._UNKNOWN and "network mount " in why, (verdict, why)
    assert _sha(nfs_db) == before and not os.path.exists(nfs_db + "-wal") and not os.path.exists(nfs_db + "-journal")


def test_selftest_database_checks_local_control(tmp_path, history_at):
    from bulk_downloader import selftest
    _nfs, mounts = _mounts(tmp_path, "nfs4")
    local = str(tmp_path / "downloader_history.db")
    _seed(local)
    history_at(local, mounts)
    assert "network mount " not in selftest.check_database(local)["message"]
    assert selftest._classify_db(local) == (selftest._CLEAN, "quick_check ok")


def test_database_manager_connection_refused_on_nfs(tmp_path, history_at):
    from bulk_downloader.database import DatabaseManager
    nfs_db, mounts = _mounts(tmp_path, "nfs4")
    history_at(nfs_db, mounts)
    with pytest.raises(REFUSED, match="network mount "):
        DatabaseManager(sqlite_path=nfs_db, dsn="").get_connection()
    assert not os.path.exists(nfs_db)


def test_backup_snapshot_refuses_nfs_source_and_local_control(tmp_path, history_at):
    from bulk_downloader import backup
    nfs_db, mounts = _mounts(tmp_path, "nfs4")
    _seed(nfs_db)
    history_at(nfs_db, mounts)
    before, out = _sha(nfs_db), tmp_path / "snap.db"
    with pytest.raises(REFUSED, match="network mount "):
        backup._snapshot_db(nfs_db, out)
    assert _sha(nfs_db) == before and not out.exists()
    local = tmp_path / "local.db"
    _seed(str(local))
    backup._snapshot_db(local, out)
    assert sqlite3.connect(str(out)).execute("SELECT v FROM probe").fetchall() == [("seeded",)]


def test_baselines_snapshot_has_no_read_write_fallback(tmp_path):
    from tools import baselines_snapshot
    missing = tmp_path / "downloader_history.db"
    with pytest.raises(sqlite3.Error):
        baselines_snapshot.main(["--db", str(missing), "--out", str(tmp_path / "out.json")])
    assert not missing.exists(), "the read-only snapshot fell back to a read-write open and created the DB"


def test_cutover_probe_opens_read_only_through_guarded_connect(monkeypatch):
    import tools.db_cutover_probe as probe
    opened = []
    real = db.guarded_connect

    def spy(database, **kw):
        opened.append((database, kw.get("uri")))
        return real(database, **kw)
    monkeypatch.setattr(db, "guarded_connect", spy, raising=False)
    probe.run_probe(verify_parity=True)
    assert opened and all("mode=ro" in str(d) and uri for d, uri in opened), opened


def test_cutover_probe_refusal_is_not_ready(monkeypatch):
    import tools.db_cutover_probe as probe

    def refuse(database, **kw):
        raise REFUSED("history DB write refused: r6-planted-refusal")
    monkeypatch.setattr(db, "guarded_connect", refuse, raising=False)
    rc, payload = probe.run_probe(verify_parity=True)
    assert rc != 0 and payload["status"] == "HISTORY_DB_REFUSED", payload
    assert "r6-planted-refusal" in payload.get("history_db_refused", ""), payload


# ---------------------------------------------------------------------------------------------------------------
# r7 (cx230 HIGH): backup.restore_backup wrote a downloader_history.db member over the destination with raw
# sqlite3.connect + backup(), so a restore into a network-mounted data dir (or another owner's) rewrote the history
# DB the guard refuses everywhere else. The destination is now judged by the history guard and both in-place opens
# go through db.guarded_connect; a refused member leaves dest and its sidecars byte-identical and ok=False.
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402


def _seed_probe(path, value):
    cx = sqlite3.connect(str(path))
    cx.execute("CREATE TABLE probe(v TEXT)")
    cx.execute("INSERT INTO probe VALUES (?)", (value,))
    cx.commit()
    cx.close()


def _files_of(path):
    d = os.path.dirname(str(path))
    base = os.path.basename(str(path))
    return {n: _sha(os.path.join(d, n)) for n in sorted(os.listdir(d)) if n.startswith(base)}


def _history_archive(tmp_path):
    src = tmp_path / "restore-source.db"
    _seed_probe(src, "RESTORED")
    archive = tmp_path / "restore.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(src, "downloader_history.db")
    return archive


def _rows(path):
    cx = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return cx.execute("SELECT v FROM probe").fetchall()
    finally:
        cx.close()


def test_r7_restore_backup_into_nfs_history_dest_refused_unchanged(tmp_path, history_at):
    from bulk_downloader import backup
    path = history_at(*_mounts(tmp_path, "nfs4"))
    _seed_probe(path, "before")
    before = _files_of(path)
    out = backup.restore_backup(_history_archive(tmp_path), target_dir=os.path.dirname(path))
    assert out["ok"] is False, out
    assert out.get("db_refused") == ["downloader_history.db"], out
    assert "history guard refused to restore: downloader_history.db" == out.get("error"), out
    assert any("network mount" in w for w in out["warnings"]), out
    assert _files_of(path) == before, "the refused restore changed the history DB or left a .bdtmp"
    assert _rows(path) == [("before",)]


def test_r7_restore_backup_missing_nfs_history_dest_not_created(tmp_path, history_at):
    from bulk_downloader import backup
    path = history_at(*_mounts(tmp_path, "nfs4"))
    out = backup.restore_backup(_history_archive(tmp_path), target_dir=os.path.dirname(path))
    assert out["ok"] is False and out.get("db_refused") == ["downloader_history.db"], out
    assert os.listdir(os.path.dirname(path)) == [], "the refused restore created files on the network mount"


def test_r7_restore_backup_owner_mismatch_refused_and_owner_match_restores(tmp_path, history_at, monkeypatch):
    from bulk_downloader import backup
    path = history_at(*_mounts(tmp_path, "ext4"))
    _seed_probe(path, "before")
    before = _files_of(path)
    archive = _history_archive(tmp_path)
    monkeypatch.setenv("BD_DB_OWNER_HOST", "hub-mesh01-not-" + socket.gethostname())
    out = backup.restore_backup(archive, target_dir=os.path.dirname(path))
    assert out["ok"] is False and out.get("db_refused") == ["downloader_history.db"], out
    assert any("BD_DB_OWNER_HOST=hub-mesh01-not-" in w for w in out["warnings"]), out
    assert _files_of(path) == before
    monkeypatch.setenv("BD_DB_OWNER_HOST", socket.gethostname())
    out = backup.restore_backup(archive, target_dir=os.path.dirname(path))
    assert out["ok"] is True and out["db_refused"] == [] and out["files_restored"] == 1, out
    assert _rows(path) == [("RESTORED",)]


def test_r7_restore_backup_local_control_restores_in_place_and_fresh(tmp_path, history_at):
    from bulk_downloader import backup
    path = history_at(*_mounts(tmp_path, "ext4"))
    archive = _history_archive(tmp_path)
    out = backup.restore_backup(archive, target_dir=os.path.dirname(path))  # fresh dest: swap path
    assert out["ok"] is True and out["files_restored"] == 1 and "error" not in out, out
    assert _rows(path) == [("RESTORED",)]
    cx = sqlite3.connect(path)
    cx.execute("UPDATE probe SET v='edited'")
    cx.commit()
    cx.close()
    out = backup.restore_backup(archive, target_dir=os.path.dirname(path))  # existing dest: in-place backup API
    assert out["ok"] is True and not any("in-place DB restore failed" in w for w in out["warnings"]), out
    assert _rows(path) == [("RESTORED",)]
    assert not any(n.endswith(".bdtmp") for n in os.listdir(os.path.dirname(path)))


def test_r7_premigration_restore_refuses_bak_on_nfs_and_restores_local(tmp_path, history_at):
    from bulk_downloader import migrations
    _, mounts = _mounts(tmp_path, "nfs4")
    live = tmp_path / "local" / "downloader_history.db"
    live.parent.mkdir()
    history_at(str(live), mounts)
    _seed_probe(live, "live")
    nfs_bak = tmp_path / "share" / "downloader_history.db.bak"
    _seed_probe(nfs_bak, "bak")
    cx = sqlite3.connect(str(nfs_bak))
    cx.execute("CREATE TABLE only_in_bak(x)")
    cx.commit()
    cx.close()
    before = _files_of(nfs_bak)
    assert migrations._restore_db_from_backup(str(nfs_bak)) is False
    assert _files_of(nfs_bak) == before
    assert _rows(live) == [("live",)]
    local_bak = tmp_path / "local" / "downloader_history.db.bak"
    _seed_probe(local_bak, "bak")
    db.close_all_pooled_connections()
    assert migrations._restore_db_from_backup(str(local_bak)) is True
    db.close_all_pooled_connections()
    assert _rows(live) == [("bak",)]


# r7: guarded_connect opens through _open_history_conn, db.py's one sqlite3.connect. A caller may pass its own module's
# sqlite3 (migrations does, its patch point); the pre-open (missing file) and post-open checks both still run on it.
def test_r7_guarded_connect_sqlite3_module_keeps_both_checks(tmp_path, history_at):
    _, mounts = _mounts(tmp_path, "nfs4")
    live = tmp_path / "local" / "downloader_history.db"
    live.parent.mkdir()
    history_at(str(live), mounts)
    nfs_db = tmp_path / "share" / "downloader_history.db"
    _seed_probe(nfs_db, "nfs")
    before = _files_of(nfs_db)
    opened = []

    class _Spy:
        @staticmethod
        def connect(name, *a, **k):
            opened.append(str(name))
            return sqlite3.connect(str(nfs_db), *a, **k)  # the file SQLite really opens is the NFS one

    missing = tmp_path / "share" / "missing" / "downloader_history.db"
    with pytest.raises(db.HistoryDBWriteRefused, match="history DB write refused"):
        db.guarded_connect(str(missing), sqlite3_module=_Spy)
    assert opened == [] and not missing.exists(), "pre-open refusal must stop the caller's opener before it runs"
    local = tmp_path / "local" / "other.db"
    with pytest.raises(db.HistoryDBWriteRefused, match="post-open"):
        db.guarded_connect(str(local), sqlite3_module=_Spy)
    assert opened == [str(local)], "the caller's opener must be the one used"
    assert _files_of(nfs_db) == before
    cx = db.guarded_connect(str(local), sqlite3_module=type("S", (), {"connect": staticmethod(sqlite3.connect)}))
    try:
        assert cx.execute("PRAGMA database_list").fetchone()[2] == str(local)
    finally:
        cx.close()


# r7 AMEND: sqlite_cdc's two opens only read; they open mode=ro (named choice), so they never create or write the
# tracked DB. Positive control: snapshot + columns still read a local WAL-mode fixture, including frames that are
# only in the -wal, and a path that needs URI quoting.
class _RecordingSink:
    def __init__(self):
        self.calls = []

    def ensure_table(self, table, pk, columns):
        self.calls.append(("ensure", table, pk, list(columns)))
        return True

    def apply_events(self, table, pk, events):
        self.calls.append(("apply", table, pk, events))
        return True


def _wal_fixture(path):
    cx = sqlite3.connect(str(path))
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
    cx.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
    cx.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b')")
    cx.commit()
    return cx  # held open so the -wal is not checkpointed away


def test_r7_cdc_snapshot_and_columns_read_local_wal_fixture(tmp_path):
    from bulk_downloader import sqlite_cdc
    d = tmp_path / "dir with space #1?x"
    d.mkdir()
    path = str(d / "tracked%41.db")
    writer = _wal_fixture(path)
    try:
        assert os.path.getsize(path + "-wal") > 0
        assert sqlite_cdc._table_columns(path, "items") == ["id", "name"]
        snap = sqlite_cdc._read_table_snapshot(path, "items", "id")
        assert sorted(snap) == [1, 2] or sorted(snap) == ["1", "2"], snap
        sink = _RecordingSink()
        stream = sqlite_cdc.SqliteCDCStream(path, [("items", "id")], sink=sink,
                                            state_path=str(tmp_path / "state.json"))
        assert stream.poll()["events"] == 2
        writer.execute("INSERT INTO items VALUES (3, 'c')")
        writer.commit()
        assert stream.poll()["events"] == 1
        assert sink.calls[0] == ("ensure", "items", "id", ["id", "name"]), sink.calls
        assert [c[0] for c in sink.calls] == ["ensure", "apply", "apply"], sink.calls
    finally:
        writer.close()


def test_r7_cdc_reads_never_create_the_tracked_db(tmp_path):
    from bulk_downloader import sqlite_cdc
    missing = str(tmp_path / "downloader_history.db")
    with pytest.raises(sqlite3.Error):
        sqlite_cdc._read_table_snapshot(missing, "items", "id")
    with pytest.raises(sqlite3.Error):
        sqlite_cdc._table_columns(missing, "items")
    assert os.listdir(str(tmp_path)) == [], "a CDC read created the tracked DB"


# r7 AMEND (D231 F1): the tmp.replace(dest) fallback (taken when the in-place backup API fails) is a write onto the
# history path as well. It is judged right before the replace; a destination that turns refused between the first
# check and the swap is left byte-identical, tmp removed.
def test_r7_restore_swap_fallback_rechecks_the_guard(tmp_path, history_at, monkeypatch):
    from bulk_downloader import backup
    path = history_at(*_mounts(tmp_path, "ext4"))
    _seed_probe(path, "before")
    before = _files_of(path)
    tmp = tmp_path / "share" / "downloader_history.db.bdtmp"
    tmp.write_bytes(_NOT_A_DB)  # the in-place backup API fails on it -> the swap fallback
    real_refusal, real_connect = db._history_write_refusal, db.guarded_connect
    opened = []

    def connect_spy(database, **kw):
        cx = real_connect(database, **kw)
        opened.append(str(database))
        return cx

    def refused_once_in_place_began(p):
        if str(path) in opened:
            return "planted: mount changed before the swap"
        return real_refusal(p)
    monkeypatch.setattr(db, "guarded_connect", connect_spy)
    monkeypatch.setattr(db, "_history_write_refusal", refused_once_in_place_began)
    with pytest.raises(REFUSED, match="planted: mount changed before the swap"):
        backup._restore_db(tmp, Path(path))
    assert opened == [str(tmp), str(path)], opened
    assert _files_of(path) == before and not tmp.exists()


_NOT_A_DB = b"r7: not an SQLite database" * 64


def test_r7_restore_swap_fallback_local_control_swaps(tmp_path, history_at):
    from bulk_downloader import backup
    path = history_at(*_mounts(tmp_path, "ext4"))
    _seed_probe(path, "before")
    tmp = tmp_path / "share" / "downloader_history.db.bdtmp"
    tmp.write_bytes(_NOT_A_DB)
    warn = backup._restore_db(tmp, Path(path))
    assert warn and "file swapped" in warn, warn
    assert open(path, "rb").read() == _NOT_A_DB and not tmp.exists()


# r7 AMEND (D231 F2): baselines_snapshot percent-quotes the path into its mode=ro URI. Unquoted, a DB named
# 'h.db?mode=rwc#x' made SQLite open (and create) 'h.db' read-write; quoted it is one literal, missing, ro file.
def test_r7_baselines_snapshot_quotes_the_path(tmp_path):
    from tools import baselines_snapshot
    with pytest.raises(sqlite3.Error):
        baselines_snapshot.main(["--db", str(tmp_path / "h.db?mode=rwc#x"), "--out", str(tmp_path / "o.json")])
    assert sorted(os.listdir(str(tmp_path))) == [], "the ro snapshot created a DB through an unquoted URI"
    d = tmp_path / "d %41#?x"
    d.mkdir()
    real = str(d / "h.db")
    _seed_probe(real, "x")
    assert baselines_snapshot.main(["--db", real, "--out", str(tmp_path / "o.json")]) == 0
    import json
    assert json.load(open(str(tmp_path / "o.json")))["db_path"] == real
