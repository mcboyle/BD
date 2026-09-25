from types import SimpleNamespace

from flask import Flask

from bulk_downloader import app_import, mass_import

BD_GATE_SCOPE = "module"


def test_mass_import_rejects_non_http_url_schemes(monkeypatch):
    received = []
    runner = SimpleNamespace(load_urls=lambda *args, **kwargs: None)
    monkeypatch.setattr(app_import, "_app_runners", lambda: {"site": runner})
    monkeypatch.setattr(app_import, "_check_csrf", lambda: None)

    def start_import(**kwargs):
        received.extend(kwargs["urls"])
        return {"ok": True, "job_id": "j", "total": len(kwargs["urls"])}

    monkeypatch.setattr(mass_import, "start_import", start_import)
    app = Flask(__name__)
    app.register_blueprint(app_import.import_bp)
    client = app.test_client()
    valid = "https://example.com/video"
    result = client.post("/api/import/start/site", json={"text": valid})
    assert result.status_code == 202
    assert received == [valid]

    received.clear()
    result = client.post("/api/import/start/site", json={"text": "httpx://not-http/video\nhttp://"})
    assert result.status_code == 400
    assert received == []

    result = client.post(
        "/api/import/start/site",
        json={"text": "httpx://not-http/video\nhttps://[broken\n" + valid},
    )
    assert result.status_code == 202
    assert received == [valid]
