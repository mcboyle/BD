"""BD-SEC-4 (bughunt backup-replication F1/F2/F3/F5): backup.py create/restore.

F1  the one-click backup carries the app's DB (constants.DB_PATH), as one
    consistent snapshot -- rows still in the WAL included, no -wal/-shm members.
F2  restore writes only backup members; code / unknown members are refused,
    listed, and never written (repro P3).
F3  a restored DB is not overridden by the old DB's stale WAL, and an open
    handle on the live DB sees the restored rows (repro P4).
F5  zip directory entries are skipped, not restored as files (repro P2).
NEG a legit backup still restores byte-equal.
All paths are pytest tmp_path scratch dirs.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

from bulk_downloader import backup as bk
from bulk_downloader import constants

BD_GATE_SCOPE = "module"

DB = Path(constants.DB_PATH).name


def _make_db(path: Path, n: int, tag: str) -> sqlite3.Connection:
    """WAL-mode DB with n rows left in the WAL (no checkpoint); conn stays open."""
    cx = sqlite3.connect(str(path))
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
    cx.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    cx.executemany("INSERT INTO t (v) VALUES (?)",
                   [(f"{tag}-{i}",) for i in range(n)])
    cx.commit()
    return cx


def _rows(path: Path):
    cx = sqlite3.connect(str(path))
    try:
        return cx.execute("SELECT COUNT(*), MIN(v) FROM t").fetchone()
    finally:
        cx.close()


def _zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_manifest.json", json.dumps({"format_version": 1}))
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_f1_backup_targets_name_the_app_db():
    arcs = [arc for _src, arc in bk.BACKUP_TARGETS]
    assert DB in arcs, f"BD-SEC-4 F1: {DB} not in BACKUP_TARGETS {arcs}"
    assert not [a for a in arcs if a.endswith(("-wal", "-shm"))]


def test_f1_backup_contains_consistent_db_snapshot(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "sites_config.json").write_text("{}")
    cx = _make_db(base / DB, 40, "LIVE")
    try:
        assert (base / (DB + "-wal")).stat().st_size > 0  # rows sit in the WAL
        res = bk.create_backup(tmp_path / "b.zip", base_dir=base, include_db=True)
    finally:
        cx.close()
    assert res["ok"], res
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        names = zf.namelist()
        assert DB in names, f"BD-SEC-4 F1: history DB missing from backup: {names}"
        assert not [n for n in names if n.endswith(("-wal", "-shm"))], names
        (tmp_path / "x").mkdir()
        snap = Path(zf.extract(DB, tmp_path / "x"))
    assert _rows(snap) == (40, "LIVE-0")


def test_f1_include_db_false_leaves_db_out(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "sites_config.json").write_text("{}")
    _make_db(base / DB, 3, "LIVE").close()
    res = bk.create_backup(tmp_path / "b.zip", base_dir=base, include_db=False)
    assert res["ok"], res
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert DB not in zf.namelist()


def test_f1_db_over_restore_cap_is_named_in_skipped(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    (base / "sites_config.json").write_text("{}")
    _make_db(base / DB, 200, "LIVE").close()
    monkeypatch.setattr(bk, "_RESTORE_MEMBER_CAP", 1024)
    res = bk.create_backup(tmp_path / "b.zip", base_dir=base)
    assert res["ok"], res
    assert [s for s in res["skipped"] if s.startswith(f"{DB}: ") and "restore cap 1024" in s], res
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert DB not in zf.namelist()


def test_f2_restore_refuses_code_and_unknown_members(tmp_path):
    target = tmp_path / "install"
    (target / "bulk_downloader").mkdir(parents=True)
    app_py = target / "bulk_downloader" / "app.py"
    app_py.write_bytes(b"ORIGINAL = 1\n")
    bad = {
        "bulk_downloader/app.py": "PWNED = 1\n",
        "sitecustomize.py": "import os\n",
        "learned/sitecustomize.json": "{}",
        "cookies/evil.py": "x = 1\n",
        "state/run.sh": "#!/bin/sh\n",
        "notes.txt": "hi",
        "sites_config.json.bak": "{}",
    }
    zp = _zip(tmp_path / "evil.zip", {**bad, "sites_config.json": '{"ok": 1}'})
    res = bk.restore_backup(zp, target_dir=target)
    assert res["ok"], res
    assert app_py.read_bytes() == b"ORIGINAL = 1\n", "BD-SEC-4 F2: code member overwrote app.py"
    for name in bad:
        if name != "bulk_downloader/app.py":
            assert not (target / name).exists(), f"BD-SEC-4 F2: wrote {name}"
    assert sorted(res.get("refused", [])) == sorted(bad), res
    assert res["files_restored"] == 1
    assert (target / "sites_config.json").read_text() == '{"ok": 1}'
    assert any("refused non-backup member: sitecustomize.py" == w
               for w in res["warnings"]), res["warnings"]


def test_f2_dry_run_lists_refusals_and_writes_nothing(tmp_path):
    target = tmp_path / "t"
    zp = _zip(tmp_path / "e.zip", {"sitecustomize.py": "x", "cookies/a.json": "{}"})
    res = bk.restore_backup(zp, target_dir=target, dry_run=True)
    assert res["files_restored"] == 1, f"BD-SEC-4 F2: dry run would write {res}"
    assert res.get("refused") == ["sitecustomize.py"]
    assert not target.exists()


def test_f2_allowlist_keeps_data_members():
    for name in ("sites_config.json", "app_config.json", "secrets.json",
                 "secrets_meta.json", "user_templates.json", "vault_tokens.json",
                 "cookies/site.json", "learned/a/b.json", "state/heartbeat.json",
                 DB, "queue.db"):
        assert bk._restore_allowed(name), name
    for name in ("x.py", "cookies/", "cookies", "state/x.pth", "learned/a.PYC",
                 "queue.db-wal", f"{DB}-shm", "profiles/p/Cookies", "app.py"):
        assert not bk._restore_allowed(name), name


def test_f5_directory_entries_are_skipped(tmp_path):
    target = tmp_path / "t"
    zp = tmp_path / "d.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("sites_config.json", "{}")
        zf.writestr(zipfile.ZipInfo("cookies/"), b"")
        zf.writestr("cookies/a.json", '{"a": 1}')
        zf.writestr("cookies/b.json", '{"b": 1}')
    res = bk.restore_backup(zp, target_dir=target)
    assert res["ok"], res
    assert (target / "cookies").is_dir(), "BD-SEC-4 F5: cookies restored as a FILE"
    assert res["files_restored"] == 3, res
    assert res["files_skipped"] == 0, res
    assert (target / "cookies" / "b.json").read_text() == '{"b": 1}'


def _backup_with_db(tmp_path: Path, n: int, tag: str, arc: str = DB) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    _make_db(src / "made.db", n, tag).close()  # last close checkpoints the WAL
    raw = (src / "made.db").read_bytes()
    assert raw[18:20] == b"\x02\x02"  # WAL-mode header, as a raw-copied DB has
    return _zip(tmp_path / "db.zip", {arc: raw})


def _stale_target(tmp_path: Path, arc: str = DB) -> Path:
    """P4: a target DB file plus an un-checkpointed WAL holding 500 LIVE rows."""
    live = tmp_path / "live"
    live.mkdir()
    cx = _make_db(live / "l.db", 500, "LIVE")
    target = tmp_path / "t"
    target.mkdir()
    shutil.copyfile(live / "l.db", target / arc)
    shutil.copyfile(live / "l.db-wal", target / (arc + "-wal"))
    cx.close()
    # Prove on a COPY (opening the target itself would checkpoint the WAL away).
    chk = tmp_path / "chk"
    shutil.copytree(target, chk)
    assert (target / (arc + "-wal")).stat().st_size > 0
    assert _rows(chk / arc) == (500, "LIVE-0")  # the WAL is what holds them
    return target


def test_f3_restored_db_is_not_overridden_by_stale_wal(tmp_path):
    target = _stale_target(tmp_path)
    zp = _backup_with_db(tmp_path, 3, "BACKUP")
    res = bk.restore_backup(zp, target_dir=target)
    assert res["ok"] and res["files_restored"] == 1, res
    assert _rows(target / DB) == (3, "BACKUP-0"), "BD-SEC-4 F3: stale WAL replayed over restore"


def test_f3_legacy_queue_db_member_same_rule(tmp_path):
    target = _stale_target(tmp_path, "queue.db")
    zp = _backup_with_db(tmp_path, 3, "BACKUP", "queue.db")
    bk.restore_backup(zp, target_dir=target)
    assert _rows(target / "queue.db") == (3, "BACKUP-0")


def test_f3_orphan_wal_is_removed_before_swap(tmp_path):
    """DB file gone, its old -wal left behind: the swap must not inherit it."""
    target = _stale_target(tmp_path)
    (target / DB).unlink()
    assert (target / (DB + "-wal")).exists()
    zp = _backup_with_db(tmp_path, 3, "BACKUP")
    res = bk.restore_backup(zp, target_dir=target)
    assert res["ok"], res
    assert _rows(target / DB) == (3, "BACKUP-0"), "BD-SEC-4 F3: orphan WAL replayed over restore"


def test_f3_open_app_handle_sees_restored_rows(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    app = _make_db(target / DB, 500, "LIVE")  # the running app's handle
    try:
        zp = _backup_with_db(tmp_path, 3, "BACKUP")
        res = bk.restore_backup(zp, target_dir=target)
        assert res["ok"], res
        got = app.execute("SELECT COUNT(*), MIN(v) FROM t").fetchone()
        assert got == (3, "BACKUP-0"), f"BD-SEC-4 F3: open handle still sees {got}"
    finally:
        app.close()
    assert _rows(target / DB) == (3, "BACKUP-0")


def test_f3_non_db_dest_is_swapped_and_sidecars_removed(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / DB).write_bytes(b"not a database")
    (target / (DB + "-wal")).write_bytes(b"stale")
    (target / (DB + "-shm")).write_bytes(b"stale")
    zp = _backup_with_db(tmp_path, 3, "BACKUP")
    res = bk.restore_backup(zp, target_dir=target)
    assert res["ok"], res
    assert not (target / (DB + "-wal")).exists()
    assert not (target / (DB + "-shm")).exists()
    assert _rows(target / DB) == (3, "BACKUP-0")
    assert any("in-place DB restore failed" in w for w in res["warnings"])


def test_neg_control_legit_backup_restores_byte_equal(tmp_path):
    """Passes before and after the fix: a config-only backup made by the app."""
    base = tmp_path / "base"
    for d in ("cookies", "learned/site", "state"):
        (base / d).mkdir(parents=True)
    files = {
        "sites_config.json": b'{"sites": {"a": 1}}',
        "app_config.json": b'{"max": 2}',
        "user_templates.json": b"[]",
        "vault_tokens.json": b"{}",
        "cookies/a.json": b'[{"name": "sid"}]',
        "learned/site/sel.json": b'{"x": "#y"}',
        "state/heartbeat.json": b'{"t": 1}',
    }
    for rel, data in files.items():
        (base / rel).write_bytes(data)
    res = bk.create_backup(tmp_path / "b.zip", base_dir=base, include_db=False)
    assert res["ok"], res
    target = tmp_path / "restored"
    out = bk.restore_backup(tmp_path / "b.zip", target_dir=target)
    assert out["ok"] and out["integrity_ok"] and out["files_skipped"] == 0, out
    assert out["files_restored"] == len(files)
    for rel, data in files.items():
        assert (target / rel).read_bytes() == data, rel


def test_f1_db_round_trip_byte_equal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "sites_config.json").write_bytes(b"{}")
    _make_db(base / DB, 7, "ROW").close()
    assert bk.create_backup(tmp_path / "b.zip", base_dir=base)["ok"]
    target = tmp_path / "restored"
    out = bk.restore_backup(tmp_path / "b.zip", target_dir=target)
    assert out["ok"] and out["integrity_ok"], out
    assert (target / DB).exists(), f"BD-SEC-4 F1: no DB restored: {out}"
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert (target / DB).read_bytes() == zf.read(DB)
    assert _rows(target / DB) == (7, "ROW-0")
