"""Cookie import must remove its temporary validation file on parse errors."""

import tempfile

from flask import Flask

from bulk_downloader import app_sites_auth

BD_GATE_SCOPE = "module"


def test_invalid_cookie_import_removes_validation_file(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(app_sites_auth, "_app_s_cfg", lambda: {"demo": {}})
    app = Flask(__name__)

    with app.test_request_context(
        "/api/sites/demo/cookies/import",
        method="POST",
        json={"text": "not-json credential-sentinel"},
    ):
        response, status = app_sites_auth.api_cookies_import("demo")

    assert status == 400
    assert response.get_json()["error"].startswith("not valid cookie JSON:")
    assert list(tmp_path.glob("*.json")) == []
