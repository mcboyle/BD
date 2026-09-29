"""Watch-folder action reports failures through HTTP, preserving successful scans."""

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"


@pytest.fixture
def scan_client(monkeypatch):
    from bulk_downloader import app_sites_lifecycle as lifecycle
    from bulk_downloader import watch_folder

    runner = object()
    configs = {"site": {"watch_folder": "fixture-folder"}}
    monkeypatch.setattr(lifecycle, "_app_runners", lambda: {"site": runner})
    monkeypatch.setattr(lifecycle, "_app_s_cfg", lambda: configs)
    app = Flask(__name__)
    app.add_url_rule(
        "/scan/<sid>", view_func=lifecycle.api_watch_scan_now, methods=["POST"]
    )
    return app.test_client(), configs, watch_folder, runner


def test_missing_folder_is_client_error(scan_client):
    client, configs, _, _ = scan_client
    configs["site"]["watch_folder"] = "  "
    response = client.post("/scan/site")
    assert response.status_code == 400, "WATCH_SCAN_MISSING_FOLDER_MUST_FAIL_HTTP"
    assert response.json == {"ok": False, "error": "watch_folder not configured"}


def test_scan_exception_is_server_error(scan_client, monkeypatch):
    client, _, watcher, _ = scan_client

    def fail(_folder):
        raise OSError("scan fixture denied")

    monkeypatch.setattr(watcher, "scan_once", fail)
    response = client.post("/scan/site")
    assert response.status_code == 500, "WATCH_SCAN_EXCEPTION_MUST_FAIL_HTTP"
    assert response.json == {"ok": False, "error": "OSError: scan fixture denied"}


def test_success_keeps_summary(scan_client, monkeypatch, tmp_path):
    client, _, watcher, runner = scan_client
    source = tmp_path / "urls.txt"
    calls = []
    monkeypatch.setattr(watcher, "scan_once", lambda folder: [source])

    def process(path, actual_runner, priority):
        calls.append((path, actual_runner, priority))
        return {"ok": True, "urls_imported": 2, "errors": [], "moved_to": "processed"}

    monkeypatch.setattr(watcher, "process_file", process)
    response = client.post("/scan/site")
    assert response.status_code == 200
    assert response.json == {
        "ok": True,
        "scanned": 1,
        "results": [
            {
                "file": "urls.txt",
                "ok": True,
                "urls_imported": 2,
                "errors": [],
                "moved_to": "processed",
            }
        ],
    }
    assert calls == [(source, runner, "normal")]


def test_unknown_site_remains_not_found(scan_client):
    client, _, _, _ = scan_client
    response = client.post("/scan/unknown")
    assert response.status_code == 404
    assert response.json == {"error": "Not found"}
