"""BR-F7: replication must find what the app itself writes, where it writes it.

db_replication._install_root() returned the package parent unconditionally. The app
writes app_config.json cwd-relative (global_config._CONFIG_FILE, app.APP_CFG_FILE) and
opens the main DB through db._resolve_db_path() (BD_INSTALL_DIR when set). With either
apart from the package parent, ``replication.enabled`` was ignored and no store found.
r1 moved everything to BD_INSTALL_DIR and was refuted (cx149): the config writer does
not write there. These tests drive the REAL writers (global_config.set_config,
db.db_conn) and then read replication_status -- a round trip, not a hand-placed file.

conftest's autouse fixture already chdirs into tmp_path and pops BD_INSTALL_DIR.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import sqlite3

import pytest

from bulk_downloader import db, db_replication as R, global_config


@pytest.fixture
def split_roots(tmp_path, monkeypatch):
    """cwd = app dir (config writer), BD_INSTALL_DIR = data dir (main DB owner)."""
    app_dir, data_dir = tmp_path / "app", tmp_path / "data"
    app_dir.mkdir()
    data_dir.mkdir()
    monkeypatch.chdir(app_dir)
    monkeypatch.setenv("BD_INSTALL_DIR", str(data_dir))
    monkeypatch.setattr(db, "DB_PATH", "downloader_history.db")
    monkeypatch.setattr(global_config, "_cached", None)
    return app_dir, data_dir


def _write_main_db():
    with db.db_conn() as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS br_f7(x)")


def test_status_round_trips_the_apps_own_writers(split_roots):
    app_dir, data_dir = split_roots
    assert global_config.set_config({"replication": {"enabled": True}}) is True
    assert global_config.get_config()["replication"]["enabled"] is True   # the writer saved it
    _write_main_db()
    assert (data_dir / "downloader_history.db").exists()                  # the DB owner wrote here
    sqlite3.connect("video_hashes.db").close()                           # cwd-relative, like dedup's owner

    st = R.replication_status()
    assert st["enabled"] is True, f"BR-F7-CONFIG-MISSED enabled={st['enabled']} (writer cwd={app_dir})"
    assert sorted(st["configured_stores"]) == ["downloader_history.db", "video_hashes.db"], (
        f"BR-F7-STORE-MISSED stores={st['configured_stores']}")
    paths = sorted(str(p) for p in R.replication_stores())
    assert paths == sorted([str((data_dir / "downloader_history.db").resolve()),
                            str((app_dir / "video_hashes.db").resolve())]), paths


def test_replicas_stay_beside_the_config_not_in_the_db_dir(split_roots):
    """No migration: replicas default under the working root, as before for the service."""
    app_dir, _data_dir = split_roots
    st = R.replication_status()
    assert os.path.realpath(st["replica_root"]) == os.path.realpath(app_dir / "replicas"), st["replica_root"]


def test_trailing_space_install_dir_matches_the_db_owner(tmp_path, monkeypatch):
    """cx149 RUNTIME: replication must resolve BD_INSTALL_DIR exactly as db does (no strip)."""
    data_dir = tmp_path / "data "
    data_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BD_INSTALL_DIR", str(data_dir))
    monkeypatch.setattr(db, "DB_PATH", "downloader_history.db")
    _write_main_db()
    assert (data_dir / "downloader_history.db").exists()
    assert R.replication_status()["configured_stores"] == ["downloader_history.db"], (
        "BR-F7-STORE-MISSED under a trailing-space BD_INSTALL_DIR")


def test_explicit_base_dir_still_wins(split_roots, tmp_path):
    other = tmp_path / "explicit"
    other.mkdir()
    (other / "app_config.json").write_text('{"replication": {"enabled": true}}')
    sqlite3.connect(str(other / "downloader_history.db")).close()
    st = R.replication_status(str(other))
    assert st["enabled"] is True and st["configured_stores"] == ["downloader_history.db"], st


def test_service_shape_is_unchanged(monkeypatch):
    """Control: the service cwds into the package parent -- the root is the old value."""
    parent = os.path.dirname(os.path.dirname(os.path.abspath(R.__file__)))
    monkeypatch.chdir(parent)
    assert R._install_root() == parent


def test_disabled_config_stays_disabled(split_roots):
    """Negative control: a saved enabled=false is honoured and start refuses."""
    assert global_config.set_config({"replication": {"enabled": False}}) is True
    _write_main_db()
    st = R.replication_status()
    assert st["enabled"] is False and st["configured_stores"] == ["downloader_history.db"], st
    started = R.start_replication()
    assert not started.get("ok"), started
