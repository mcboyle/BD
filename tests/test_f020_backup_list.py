"""F020 "list backups": GET /api/backup/list returns every backup BD keeps on disk,
metadata only -- name, size, created, kind -- newest first.

/api/backup/create streams its zip and keeps no copy, but BD does keep backups:
backup archives the rehearsal reads (BD_HOME or CWD, and its backups/), the
<db>.premigration.bak copy migrations write, template gold generations under
templates/.gold_backups/<host>/<ts>/, and profile-sync move-asides under
profiles/<site>/<profile>/.sync_backups/<ts>/. Each store is planted here in a
temp tree; the live DB itself must never be listed as a backup.
"""

import os

import pytest
from flask import Flask

from bulk_downloader import app_backup, db

BD_GATE_SCOPE = "module"

ROW_KEYS = {"name", "size", "created", "kind"}


def _client():
    app = Flask(__name__)
    app.register_blueprint(app_backup.backup_bp)
    return app.test_client()


def _plant(path, data, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


@pytest.fixture
def stores(tmp_path, monkeypatch):
    home, root, cwd = tmp_path / "home", tmp_path / "root", tmp_path / "cwd"
    for d in (home, root, cwd):
        d.mkdir()
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.setenv("BD_HOME", str(home))
    monkeypatch.setenv("BD_ROOT", str(root))
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(db, "DB_PATH", str(home / "bulk.db"))
    return home, root, cwd


def test_list_reports_every_store_metadata_only_newest_first(stores):
    home, root, cwd = stores
    _plant(home / "bulk.db", b"live-db", 1_000)  # the live DB: never a backup
    _plant(home / "backups" / "bd_backup_2026-10-01_0101.zip", b"z" * 10, 2_000)
    _plant(home / "bd_backup_2026-10-02_0202.bdbk", b"e" * 7, 3_000)
    _plant(home / "bulk.db.premigration.bak", b"p" * 5, 4_000)
    gold = root / "templates" / ".gold_backups" / "example.com" / "20261001-010101-000001"
    _plant(gold / "example.com.template.json", b"t" * 3, 5_000)
    _plant(gold / "manifest.json", b"m" * 2, 5_000)
    os.utime(gold, (5_000, 5_000))
    sync = cwd / "profiles" / "site1" / "main" / ".sync_backups" / "20261002-020202"
    _plant(sync / "Cookies", b"c" * 4, 6_000)
    os.utime(sync, (6_000, 6_000))

    res = _client().get("/api/backup/list")
    assert res.status_code == 200, res.data[:200]
    body = res.get_json()
    assert body["ok"] is True and body["errors"] == [], body
    rows = body["backups"]
    for r in rows:
        assert set(r) == ROW_KEYS, f"metadata only, got keys {sorted(r)}"
    got = [(r["kind"], r["name"], r["size"]) for r in rows]
    assert got == [
        ("profile_sync", "site1/main/20261002-020202", 4),
        ("template_gold", "example.com/20261001-010101-000001", 5),
        ("premigration", "bulk.db.premigration.bak", 5),
        ("archive", "bd_backup_2026-10-02_0202.bdbk", 7),
        ("archive", "backups/bd_backup_2026-10-01_0101.zip", 10),
    ], got
    assert rows[-1]["created"] == "1970-01-01T00:33:20Z", rows[-1]
    assert "bulk.db" not in {r["name"] for r in rows}


def test_list_is_explicitly_empty_when_bd_keeps_no_backups(stores):
    home, _root, _cwd = stores
    _plant(home / "bulk.db", b"live-db", 1_000)
    res = _client().get("/api/backup/list")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "backups": [], "errors": []}


needs_perms = pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 dirs; the denial cannot be staged")


@needs_perms
def test_unreadable_store_is_reported_never_an_empty_success(stores):
    home, _root, cwd = stores
    _plant(home / "bd_backup_2026-10-03_0303.zip", b"r" * 6, 2_000)  # readable control
    _plant(home / "backups" / "bd_backup_2026-10-01_0101.zip", b"z" * 10, 1_000)
    _plant(cwd / "profiles" / "siteB" / "main" / ".sync_backups" / "20261002-020202" / "Cookies", b"c" * 4, 3_000)
    _plant(cwd / "profiles" / "siteA" / "main" / ".sync_backups" / "20261001-010101" / "Cookies", b"c", 3_000)
    locked = [home / "backups", cwd / "profiles" / "siteA"]
    for d in locked:
        d.chmod(0)
    try:
        body = _client().get("/api/backup/list").get_json()
    finally:
        for d in locked:
            d.chmod(0o755)
    assert body["ok"] is True, body
    assert {(r["kind"], r["name"]) for r in body["backups"]} == {
        ("archive", "bd_backup_2026-10-03_0303.zip"),
        ("profile_sync", "siteB/main/20261002-020202"),
    }, body
    assert sorted((e["kind"], e["name"], e["error"]) for e in body["errors"]) == [
        ("archive", "backups", "Permission denied"),
        ("profile_sync", "siteA", "Permission denied"),
    ], body["errors"]
    assert str(home) not in str(body["errors"]), "error text must not carry absolute paths"


def test_symlinks_are_neither_listed_nor_followed(stores, tmp_path):
    home, root, _cwd = stores
    outside = tmp_path / "outside"
    _plant(outside / "secret.zip", b"s" * 50, 1_000)
    (home / "backups").mkdir()
    (home / "backups" / "escape.zip").symlink_to(outside / "secret.zip")
    gold = root / "templates" / ".gold_backups" / "example.com" / "20261001-010101-000001"
    _plant(gold / "example.com.template.json", b"t" * 3, 5_000)
    (gold / "big.json").symlink_to(outside / "secret.zip")
    (root / "templates" / ".gold_backups" / "linked.com").symlink_to(outside)
    body = _client().get("/api/backup/list").get_json()
    assert body["errors"] == [], body
    assert [(r["kind"], r["name"], r["size"]) for r in body["backups"]] == [
        ("template_gold", "example.com/20261001-010101-000001", 3),
    ], body


def test_symlinked_store_directories_are_reported_not_entered(stores, tmp_path):
    home, root, cwd = stores
    outside = tmp_path / "outside"
    _plant(outside / "bd_backup_2026-10-09_0909.zip", b"x" * 9, 1_000)
    _plant(outside / "example.com" / "20261009-090909-000001" / "t.json", b"x" * 9, 1_000)
    _plant(outside / "20261009-090909" / "Cookies", b"x" * 9, 1_000)
    _plant(home / "bd_backup_2026-10-03_0303.zip", b"r" * 6, 2_000)  # readable control
    (home / "backups").symlink_to(outside)
    (root / "templates").mkdir()
    (root / "templates" / ".gold_backups").symlink_to(outside)
    (cwd / "profiles" / "site1" / "main").mkdir(parents=True)
    (cwd / "profiles" / "site1" / "main" / ".sync_backups").symlink_to(outside)
    body = _client().get("/api/backup/list").get_json()
    assert [(r["kind"], r["name"]) for r in body["backups"]] == [
        ("archive", "bd_backup_2026-10-03_0303.zip"),
    ], body
    assert sorted((e["kind"], e["name"], e["error"]) for e in body["errors"]) == [
        ("archive", "backups", "symlink not followed"),
        ("profile_sync", "site1/main/.sync_backups", "symlink not followed"),
        ("template_gold", ".gold_backups", "symlink not followed"),
    ], body["errors"]
