"""BR-F4: the restore rehearsal must find only real backups.

THE DEFECT (bughunt backup-replication F4, repro P5, base b0ea77a6):
backup_verify._latest_backup globbed BD_HOME/*.db, so the app's LIVE history DB
was "the newest backup": with no backup on disk, rehearse() returned ok=True and
path=.../downloader_history.db. It also never looked for .bdbk, the name the app
gives a passphrase-wrapped backup, so a disk holding only that read "no backup found".

Every test runs rehearse() with no path, as the scheduled digest does, against the
BD_HOME the autouse fixture points at a fresh tmp dir.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import json
import os
import sqlite3
import zipfile
from pathlib import Path

from bulk_downloader import backup_verify as BV


def _home() -> Path:
    return Path(os.environ["BD_HOME"])


def _live_db() -> Path:
    """Create the app's own store the way the app does, and return its path."""
    from bulk_downloader import db

    db.db_init()
    live = Path(db._resolve_db_path()).resolve()  # may be relative to the cwd
    assert live.is_file(), live
    # The finding's layout: the live store sits at the BD_HOME root.
    assert live.parent.resolve() == _home().resolve(), (live, _home())
    return live


def _zip_backup(where: Path) -> Path:
    where.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(where, "w") as z:
        z.writestr("sites_config.json", json.dumps({"demo": {"name": "demo"}}))
    return where


def _bdbk_backup(where: Path) -> Path:
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_bytes(b"BDBK" + os.urandom(16) + b"ciphertext-not-a-zip")
    return where


def test_live_db_alone_is_not_a_backup():
    live = _live_db()
    r = BV.rehearse()
    assert r["path"] != str(live) and not r["ok"], (
        f"BR-F4: rehearsal took the LIVE DB as its backup and reported {r}"
    )
    assert r["error"] == "no backup found", r


def test_control_real_zip_beside_live_db_is_rehearsed_ok():
    _live_db()
    zp = _zip_backup(_home() / "backups" / "bd_backup_2026-10-13_1400.zip")
    os.utime(zp, (1, 1))  # older than the live DB: still the only backup
    r = BV.rehearse()
    assert r["ok"] and r["path"] == str(zp), r


def test_control_db_dump_in_backups_dir_still_counts():
    _live_db()
    dump = _home() / "backups" / "history-dump.db"
    dump.parent.mkdir()
    with sqlite3.connect(dump) as cx:
        cx.execute("CREATE TABLE history (id INTEGER PRIMARY KEY)")
        cx.execute("INSERT INTO history VALUES (1)")
    os.utime(dump, (1, 1))
    r = BV.rehearse()
    assert r["ok"] and r["path"] == str(dump), r


def test_encrypted_backup_is_seen_and_not_called_restorable():
    bk = _bdbk_backup(_home() / "bd_backup_2026-10-13_1400.bdbk")
    r = BV.rehearse()
    assert r["path"] == str(bk), f"BR-F4: the .bdbk backup on disk was not found: {r}"
    assert not r["ok"] and "passphrase" in r["error"], (
        f"BR-F4: encrypted backup not named as unrehearsable: {r['error']!r}"
    )


def test_newest_real_backup_wins_across_zip_and_bdbk():
    zp = _zip_backup(_home() / "bd_backup_2026-10-12_1400.zip")
    bk = _bdbk_backup(_home() / "backups" / "bd_backup_2026-10-13_1400.bdbk")
    os.utime(zp, (1000, 1000))
    os.utime(bk, (2000, 2000))
    assert BV.rehearse()["path"] == str(bk)
    os.utime(zp, (3000, 3000))
    r = BV.rehearse()
    assert r["ok"] and r["path"] == str(zp), r


def test_negative_control_empty_home_is_not_ok():
    r = BV.rehearse()
    assert not r["ok"] and r["error"] == "no backup found", r


# ── r2 (cx147 REFUTE): an alias of the live DB in backups/ is still the live DB ──


def _independent_copy(live: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(live)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def _alias_case(make_alias):
    live = _live_db()
    alias = _home() / "backups" / "history.db"
    alias.parent.mkdir()
    make_alias(live, alias)
    assert os.path.samefile(alias, live)
    r = BV.rehearse()
    assert not r["ok"] and r["error"] == "no backup found", (
        f"BR-F4 r2: an alias of the LIVE DB was rehearsed as a backup: {r}"
    )


def test_symlink_to_live_db_is_not_a_backup():
    _alias_case(lambda live, alias: os.symlink(live, alias))


def test_hardlink_to_live_db_is_not_a_backup():
    _alias_case(lambda live, alias: os.link(live, alias))


def test_control_independent_copy_of_live_db_counts():
    live = _live_db()
    copy = _independent_copy(live, _home() / "backups" / "history.db")
    assert not os.path.samefile(copy, live)
    r = BV.rehearse()
    assert r["ok"] and r["path"] == str(copy), r


def test_control_symlink_to_independent_copy_counts():
    live = _live_db()
    real = _independent_copy(live, _home() / "elsewhere" / "copy.db")
    alias = _home() / "backups" / "history.db"
    alias.parent.mkdir()
    os.symlink(real, alias)
    r = BV.rehearse()
    assert r["ok"] and r["path"] == str(alias), r


def test_alias_of_live_db_outside_bd_home_is_not_a_backup(monkeypatch):
    """The live DB need not sit in BD_HOME: db._resolve_db_path() decides."""
    from bulk_downloader import db

    live = _home() / "data" / "downloader_history.db"
    live.parent.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(live))
    db.db_init()
    assert Path(db._resolve_db_path()) == live and live.is_file()
    alias = _home() / "backups" / "history.db"
    alias.parent.mkdir()
    os.symlink(live, alias)
    r = BV.rehearse()
    assert not r["ok"] and r["error"] == "no backup found", (
        f"BR-F4 r2: an alias of the LIVE DB at {live} was rehearsed as a backup: {r}"
    )
