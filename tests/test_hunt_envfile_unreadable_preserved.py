from flask import Flask

from bulk_downloader import app_envfile_editor

BD_GATE_SCOPE = "module"


def test_envfile_editor_preserves_undecodable_existing_file(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    monkeypatch.setenv("BD_ENVFILE", str(envfile))
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    client = app.test_client()

    created = client.post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert created.status_code == 200
    assert envfile.read_text(encoding="utf-8") == "BD_HOST=new\n"

    envfile.write_text("UNRELATED=keep\nBD_HOST=old\n", encoding="utf-8")
    control = client.post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert control.status_code == 200
    assert "UNRELATED=keep" in envfile.read_text(encoding="utf-8")

    original = b"UNRELATED=keep\n# non-UTF8: \xff\nBD_HOST=old\n"
    envfile.write_bytes(original)
    response = client.post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert response.status_code == 500
    assert envfile.read_bytes() == original
