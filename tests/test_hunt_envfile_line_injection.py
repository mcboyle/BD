"""A .env editor value must stay on its own line for every consumer."""

from flask import Flask

from bulk_downloader import _envfile, app_envfile_editor

BD_GATE_SCOPE = "module"


def test_envfile_editor_rejects_line_breaks_before_writing(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    envfile.write_text("BD_HOST=old-host\n", encoding="utf-8")
    monkeypatch.setenv("BD_ENVFILE", str(envfile))
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    client = app.test_client()

    valid = client.post("/api/settings/envfile", json={"updates": {"BD_HOST": "new-host"}})
    assert valid.status_code == 200
    assert _envfile.parse_envfile(envfile.read_text())["BD_HOST"] == "new-host"

    original = envfile.read_bytes()
    for separator in ("\n", "\r", "\u2028", "\x00"):
        injected = client.post(
            "/api/settings/envfile",
            json={"updates": {"BD_HOST": "another-host" + separator + "BD_VAULT_KEY=unexpected"}},
        )
        assert injected.status_code == 400
        assert envfile.read_bytes() == original
        assert "BD_VAULT_KEY" not in _envfile.parse_envfile(envfile.read_text())


def test_envfile_editor_rejects_trailing_backslash(tmp_path, monkeypatch):
    # systemd EnvironmentFile (install_service.sh) joins a line ending in a
    # backslash with the next one, swallowing the following assignment.
    envfile = tmp_path / ".env"
    envfile.write_text("BD_HOST=old-host\nBD_PORT=5555\n", encoding="utf-8")
    monkeypatch.setenv("BD_ENVFILE", str(envfile))
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    original = envfile.read_bytes()

    response = app.test_client().post(
        "/api/settings/envfile", json={"updates": {"BD_HOST": "new-host\\"}})
    assert response.status_code == 400
    assert envfile.read_bytes() == original
