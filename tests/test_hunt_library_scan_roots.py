"""Explicit library scan roots must be a nonempty list of paths."""

from flask import Flask

from bulk_downloader import app_library, library

BD_GATE_SCOPE = "module"


def test_scan_start_requires_explicit_roots_to_be_nonempty_list(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_library, "_app_s_cfg", lambda: {"site": {"download_dir": str(tmp_path)}})
    monkeypatch.setattr(app_library, "_validate_path", lambda path, _label: (True, path))
    monkeypatch.setattr(library, "scan_start", lambda roots: calls.append(roots) or {"ok": True})
    app = Flask(__name__)
    app_library.register_routes(app)
    client = app.test_client()

    default = client.post("/api/library/scan/start", json={})
    assert default.status_code == 200
    assert calls == [[str(tmp_path)]]
    explicit = client.post("/api/library/scan/start", json={"roots": [str(tmp_path)]})
    assert explicit.status_code == 200
    assert calls == [[str(tmp_path)], [str(tmp_path)]]

    for bad_roots in ([], "/", {str(tmp_path): True}):
        response = client.post("/api/library/scan/start", json={"roots": bad_roots})
        assert response.status_code == 400
        assert calls == [[str(tmp_path)], [str(tmp_path)]]
    assert client.post("/api/library/scan/start", json=[]).status_code == 400
    assert calls == [[str(tmp_path)], [str(tmp_path)]]
