from flask import Flask

from bulk_downloader import _envfile, app_envfile_editor

BD_GATE_SCOPE = "module"


def test_envfile_editor_updates_all_existing_key_occurrences(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    monkeypatch.setenv("BD_ENVFILE", str(envfile))
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    client = app.test_client()

    envfile.write_text("BD_HOST=first\nOTHER=keep\n", encoding="utf-8")
    control = client.post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert control.status_code == 200
    assert _envfile.parse_envfile(envfile.read_text())["BD_HOST"] == "new"

    envfile.write_text("BD_HOST=first\nOTHER=keep\nBD_HOST=last\n", encoding="utf-8")
    result = client.post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert result.status_code == 200
    text = envfile.read_text()
    assert _envfile.parse_envfile(text)["BD_HOST"] == "new"
    assert "OTHER=keep" in text
    # Lens: both occurrences rewritten in place, nothing appended.
    assert text.splitlines() == ["BD_HOST=new", "OTHER=keep", "BD_HOST=new"]


def test_undecodable_envfile_is_not_replaced(tmp_path, monkeypatch):
    """Lens: a .env the editor cannot decode must be left byte-identical and
    the request must fail -- it used to be rewritten with only the edited key."""
    envfile = tmp_path / ".env"
    monkeypatch.setenv("BD_ENVFILE", str(envfile))
    original = b"BD_HOST=a\nNOTE=caf\xe9\nOTHER=keep\n"
    envfile.write_bytes(original)
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    result = app.test_client().post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert result.status_code == 500
    assert result.get_json()["ok"] is False
    assert envfile.read_bytes() == original


def test_missing_envfile_is_created(tmp_path, monkeypatch):
    envfile = tmp_path / "sub" / ".env"
    monkeypatch.setenv("BD_ENVFILE", str(envfile))
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    result = app.test_client().post("/api/settings/envfile", json={"updates": {"BD_HOST": "new"}})
    assert result.status_code == 200
    assert envfile.read_text() == "BD_HOST=new\n"
