"""hubdb-nfs-guard r1: auto_recover_sqlite must never quarantine a database
that sits on a network filesystem, or one opened from a host that does not
own it.

The measured mechanism (landing/hubdb-corruption-source/r1/DONE.md): a VM
opened the hub's live history over NFS. SQLite's WAL index (-shm) is an mmap
that is not coherent across hosts, so that reader saw a database the hub reads
as clean (3 of 5 quarantined files are CLEAN with their WAL applied) -- and the
startup self-test renamed the live file out from under the running service.

The guard sits BEFORE classification: even a read-only open over NFS writes
-shm, and a CORRUPT verdict obtained through an NFS client is the very verdict
that is not trustworthy.
"""
import os
import socket
import sqlite3

import pytest

from bulk_downloader import selftest
from bulk_downloader.selftest import OK, WARN, auto_recover_sqlite

BD_GATE_SCOPE = "module"


def _corrupt_db(path):
    """Bytes that SQLite positively identifies as not-a-database (NOTADB)."""
    path.write_bytes(b"this is not a sqlite database " * 200)
    return path.read_bytes()


def _mounts(tmp_path, lines):
    table = tmp_path.parent / f"{tmp_path.name}-mounts"
    table.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return str(table)


def _use_mounts(monkeypatch, tmp_path, lines):
    monkeypatch.setattr(selftest, "_MOUNTS_FILE",
                        _mounts(tmp_path, lines), raising=False)


def _quarantined(tmp_path, name):
    return sorted(p.name for p in tmp_path.iterdir()
                  if p.name.startswith(f"{name}.corrupt."))


def _real(tmp_path):
    return os.path.realpath(str(tmp_path))


# ── RED: the hub DB seen through an NFS client ───────────────────────────────

def test_corrupt_looking_db_on_nfs_is_never_renamed(tmp_path, monkeypatch):
    db = tmp_path / "downloader_history.db"
    before = _corrupt_db(db)
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)
    _use_mounts(monkeypatch, tmp_path, [
        "/dev/sda1 / xfs rw 0 0",
        f"hub:/export {_real(tmp_path)} nfs4 rw,relatime 0 0",
    ])

    result = auto_recover_sqlite(str(db))

    moved = _quarantined(tmp_path, db.name)
    assert db.exists() and not moved, (
        f"BD_HUBDB_NFS_GUARD_RENAMED: a database on nfs4 was quarantined "
        f"(moved to {moved}); an NFS client's verdict renamed the live DB")
    assert db.read_bytes() == before
    assert result["status"] == WARN, result
    assert "network filesystem" in result["message"], result
    assert result["detail"].get("fstype") == "nfs4", result
    assert result["detail"].get("mountpoint") == _real(tmp_path), result


def test_nfs_guard_does_not_even_open_the_db(tmp_path, monkeypatch):
    """Classifying opens the file, and an open over NFS writes -shm: the
    refusal must come before sqlite3 is touched at all."""
    db = tmp_path / "downloader_history.db"
    _corrupt_db(db)
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)
    _use_mounts(monkeypatch, tmp_path,
                [f"hub:/export {_real(tmp_path)} nfs rw 0 0"])
    opened = []

    def _no_connect(*a, **kw):
        opened.append(a)
        raise AssertionError("auto_recover_sqlite opened a DB on NFS")
    monkeypatch.setattr(sqlite3, "connect", _no_connect)

    result = auto_recover_sqlite(str(db))

    assert not opened, (
        "BD_HUBDB_NFS_GUARD_OPENED: the DB on nfs was opened for classification")
    assert result["status"] == WARN, result
    assert db.exists()


def test_symlink_into_an_nfs_mount_is_resolved(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    _corrupt_db(remote / "downloader_history.db")
    link = local / "downloader_history.db"
    link.symlink_to(remote / "downloader_history.db")
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)
    _use_mounts(monkeypatch, tmp_path, [
        f"/dev/sda1 {_real(tmp_path)} xfs rw 0 0",
        f"hub:/export {os.path.realpath(str(remote))} nfs4 rw 0 0",
    ])

    result = auto_recover_sqlite(str(link))

    assert (remote / "downloader_history.db").exists()
    assert not _quarantined(local, link.name)
    assert not _quarantined(remote, "downloader_history.db")
    assert result["detail"].get("fstype") == "nfs4", result


def test_non_owning_host_is_refused(tmp_path, monkeypatch):
    db = tmp_path / "downloader_history.db"
    _corrupt_db(db)
    _use_mounts(monkeypatch, tmp_path,
                [f"/dev/sda1 {_real(tmp_path)} xfs rw 0 0"])
    monkeypatch.setenv("BD_DB_OWNER_HOST", "not-" + socket.gethostname())

    result = auto_recover_sqlite(str(db))

    assert db.exists() and not _quarantined(tmp_path, db.name), (
        "BD_HUBDB_NFS_GUARD_RENAMED: a non-owning host quarantined the DB")
    assert result["status"] == WARN, result
    assert "owning host" in result["message"], result


def test_unreadable_mount_table_refuses(tmp_path, monkeypatch):
    """No filesystem answer is not permission to rename."""
    db = tmp_path / "downloader_history.db"
    _corrupt_db(db)
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)
    monkeypatch.setattr(selftest, "_MOUNTS_FILE",
                        str(tmp_path / "no-such-mounts"), raising=False)

    result = auto_recover_sqlite(str(db))

    assert db.exists() and not _quarantined(tmp_path, db.name)
    assert result["status"] == WARN, result
    assert "could not determine" in result["message"], result


# ── negative controls: a local DB still recovers ─────────────────────────────

@pytest.mark.parametrize("fstype", ["xfs", "ext4", "tmpfs"])
def test_corrupt_db_on_local_fs_is_still_quarantined(tmp_path, monkeypatch,
                                                     fstype):
    db = tmp_path / "downloader_history.db"
    _corrupt_db(db)
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)
    _use_mounts(monkeypatch, tmp_path, [
        "hub:/export / nfs4 rw 0 0",
        f"/dev/sdb1 {_real(tmp_path)} {fstype} rw 0 0",
    ])

    result = auto_recover_sqlite(str(db))

    assert not db.exists()
    assert len(_quarantined(tmp_path, db.name)) == 1
    assert result["status"] == WARN and "moved aside" in result["message"]


def test_owning_host_still_quarantines(tmp_path, monkeypatch):
    db = tmp_path / "downloader_history.db"
    _corrupt_db(db)
    _use_mounts(monkeypatch, tmp_path,
                [f"/dev/sda1 {_real(tmp_path)} xfs rw 0 0"])
    monkeypatch.setenv("BD_DB_OWNER_HOST", socket.gethostname())

    result = auto_recover_sqlite(str(db))

    assert not db.exists()
    assert len(_quarantined(tmp_path, db.name)) == 1
    assert "moved aside" in result["message"]


def test_absent_db_on_nfs_still_reports_ok(tmp_path, monkeypatch):
    """Absent DB stays OK (db_init creates it); the guard only stands between
    a remote file and a rename."""
    monkeypatch.delenv("BD_DB_OWNER_HOST", raising=False)
    _use_mounts(monkeypatch, tmp_path,
                [f"hub:/export {_real(tmp_path)} nfs4 rw 0 0"])
    result = auto_recover_sqlite(str(tmp_path / "absent.db"))
    assert result["status"] == OK, result


# ── the parser ───────────────────────────────────────────────────────────────

def test_longest_prefix_is_component_aware(tmp_path):
    table = _mounts(tmp_path, [
        "/dev/sda1 / xfs rw 0 0",
        "hub:/a /mnt/bd nfs4 rw 0 0",
        "/dev/sdc1 /mnt/bd-repo ext4 rw 0 0",
        "/dev/sdd1 /mnt/with\\040space ext4 rw 0 0",
        "hub:/b /mnt/with\\040space/inner nfs rw 0 0",
        "/dev/sde1 /mnt/over ext4 rw 0 0",
        "hub:/c /mnt/over nfs4 rw 0 0",
    ])
    fs = selftest._mount_fstype
    assert fs("/mnt/bd-repo/x.db", table) == ("/mnt/bd-repo", "ext4")
    assert fs("/mnt/bd/x.db", table) == ("/mnt/bd", "nfs4")
    assert fs("/mnt/bd", table) == ("/mnt/bd", "nfs4")
    # /mnt/bd is a string prefix of /mnt/bdz but not a path prefix of it.
    assert fs("/mnt/bdz/x.db", table) == ("/", "xfs")
    assert fs("/srv/x.db", table) == ("/", "xfs")
    assert fs("/mnt/with space/x.db", table) == ("/mnt/with space", "ext4")
    assert fs("/mnt/with space/inner/x.db", table) == (
        "/mnt/with space/inner", "nfs")
    assert fs("/mnt/over/x.db", table) == ("/mnt/over", "nfs4")


def test_real_mount_table_answers(tmp_path):
    """Positive control for the zero: the live table resolves real paths."""
    mp, fstype = selftest._mount_fstype("/")
    assert mp == "/" and fstype
    mp, fstype = selftest._mount_fstype(str(tmp_path))
    assert mp and fstype
