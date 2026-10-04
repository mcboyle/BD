"""O1826 C17 (M009): /api/backup/{create,preview,restore} must use the app's
configured data dir, not whatever directory the process happens to stand in.

app._resolve_sites_file reads sites_config.json from BD_INSTALL_DIR when it is
set. The backup routes passed base_dir="." / target_dir=".", so a backup taken
while the CWD was elsewhere silently left out the live config, and a restore
wrote the files somewhere the app never reads. With no env var set, both sides
still fall back to the CWD (the service runs from its install root).
O1864: BD_INSTALL_DIR, else BD_HOME, else the CWD.
"""

import io
import json
import zipfile

import pytest
from flask import Flask

from bulk_downloader import app_backup, backup

BD_GATE_SCOPE = "module"

SITES = {"marker": "o1826-c17"}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "cookies").mkdir(parents=True)
    (data / "sites_config.json").write_text(json.dumps(SITES))
    (data / "cookies" / "site1.json").write_text("[]")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.delenv("BD_HOME", raising=False)
    return data, elsewhere


def _client():
    app = Flask(__name__)
    app.register_blueprint(app_backup.backup_bp)
    return app.test_client()


def _names(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return set(zf.namelist())


def test_create_reads_install_dir_not_cwd(dirs, monkeypatch):
    data, elsewhere = dirs
    monkeypatch.setenv("BD_INSTALL_DIR", str(data))
    monkeypatch.chdir(elsewhere)

    resp = _client().post("/api/backup/create", json={"include_db": False})
    try:
        assert resp.status_code == 200
        names = _names(resp.data)
    finally:
        resp.close()

    assert "sites_config.json" in names, f"C17: backup ignored BD_INSTALL_DIR, members={sorted(names)}"
    assert "cookies/site1.json" in names, f"C17: backup ignored BD_INSTALL_DIR, members={sorted(names)}"


def test_preview_reads_install_dir_not_cwd(dirs, monkeypatch, tmp_path):
    data, elsewhere = dirs
    expected = backup.create_backup(tmp_path / "ref.zip", base_dir=data, include_db=True)
    assert expected["ok"] and expected["files"] >= 2
    monkeypatch.setenv("BD_INSTALL_DIR", str(data))
    monkeypatch.chdir(elsewhere)

    body = _client().post("/api/backup/preview").get_json()

    assert body["ok"] is True
    assert body["files"] == expected["files"], f"C17: preview counted the CWD, got {body['files']}"


def test_restore_writes_into_install_dir(dirs, monkeypatch, tmp_path):
    data, elsewhere = dirs
    src = tmp_path / "src"
    src.mkdir()
    restored = {"marker": "restored"}
    (src / "sites_config.json").write_text(json.dumps(restored))
    zpath = tmp_path / "in.zip"
    assert backup.create_backup(zpath, base_dir=src, include_db=False)["ok"]
    monkeypatch.setenv("BD_INSTALL_DIR", str(data))
    monkeypatch.chdir(elsewhere)

    resp = _client().post(
        "/api/backup/restore",
        data={"file": (io.BytesIO(zpath.read_bytes()), "in.zip")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert json.loads((data / "sites_config.json").read_text()) == restored, "C17: restore missed BD_INSTALL_DIR"
    assert not (elsewhere / "sites_config.json").exists(), "C17: restore wrote into the CWD"


def test_unset_env_still_uses_cwd(dirs, monkeypatch):
    data, _elsewhere = dirs
    monkeypatch.chdir(data)

    resp = _client().post("/api/backup/create", json={"include_db": False})
    try:
        assert resp.status_code == 200
        names = _names(resp.data)
    finally:
        resp.close()

    assert {"sites_config.json", "cookies/site1.json"} <= names


def test_bd_home_used_when_install_dir_unset(dirs, monkeypatch):
    data, elsewhere = dirs
    monkeypatch.setenv("BD_HOME", str(data))
    monkeypatch.chdir(elsewhere)

    resp = _client().post("/api/backup/create", json={"include_db": False})
    try:
        assert resp.status_code == 200
        names = _names(resp.data)
    finally:
        resp.close()

    assert {"sites_config.json", "cookies/site1.json"} <= names, f"C17: backup ignored BD_HOME, members={sorted(names)}"


def test_install_dir_wins_over_bd_home(dirs, monkeypatch, tmp_path):
    data, elsewhere = dirs
    home = tmp_path / "home"
    home.mkdir()
    (home / "user_templates.json").write_text("{}")
    monkeypatch.setenv("BD_INSTALL_DIR", str(data))
    monkeypatch.setenv("BD_HOME", str(home))
    monkeypatch.chdir(elsewhere)

    resp = _client().post("/api/backup/create", json={"include_db": False})
    try:
        assert resp.status_code == 200
        names = _names(resp.data)
    finally:
        resp.close()

    assert "sites_config.json" in names, f"C17: BD_INSTALL_DIR must win, members={sorted(names)}"
    assert "user_templates.json" not in names, f"C17: BD_HOME won over BD_INSTALL_DIR, members={sorted(names)}"
