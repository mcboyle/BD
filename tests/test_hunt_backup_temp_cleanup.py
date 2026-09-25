"""A downloaded backup must not remain in a temporary directory."""

import tempfile
from pathlib import Path

from flask import Flask

from bulk_downloader import app_backup, backup

BD_GATE_SCOPE = "module"


def test_backup_temp_removed_after_response_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(backup, "default_backup_filename", lambda: "sample.zip")

    def create_backup(out_path, **_kwargs):
        Path(out_path).write_bytes(b"backup bytes")
        return {
            "ok": True,
            "size_bytes": 12,
            "files": 1,
            "encrypted": False,
            "elapsed_ms": 1,
        }

    monkeypatch.setattr(backup, "create_backup", create_backup)
    app = Flask(__name__)
    app.register_blueprint(app_backup.backup_bp)

    response = app.test_client().post("/api/backup/create", json={"include_db": False})
    assert response.status_code == 200
    assert response.data == b"backup bytes"
    working = list(tmp_path.glob("bdback_*"))
    assert len(working) == 1
    assert (working[0] / "sample.zip").is_file()

    response.close()

    assert not list(tmp_path.glob("bdback_*"))
