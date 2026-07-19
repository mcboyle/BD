"""Phase O — server-backed cockpit UI prefs route (cockpit_console.py).
Cockpit-only / deploy-excluded; G12 does not gate cockpit routes."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _client():
    os.environ["BD_HOME"] = tempfile.mkdtemp()
    from flask import Flask
    import tools.cockpit_console as ccon
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(ccon.bp)
    return app.test_client()


def test_ui_prefs_get_empty():
    c = _client()
    r = c.get("/cockpit/api/ui_prefs")
    assert r.status_code == 200
    assert r.get_json()["prefs"] == {}


def test_ui_prefs_post_valid_then_merge():
    c = _client()
    r = c.post("/cockpit/api/ui_prefs", json={"layout": "rail", "theme": "ocean"})
    assert r.status_code == 200
    assert r.get_json()["prefs"] == {"layout": "rail", "theme": "ocean"}
    c.post("/cockpit/api/ui_prefs", json={"vtier": "advanced"})
    r2 = c.get("/cockpit/api/ui_prefs")
    assert r2.get_json()["prefs"] == {"layout": "rail", "theme": "ocean", "vtier": "advanced"}


def test_ui_prefs_rejects_invalid_value():
    c = _client()
    assert c.post("/cockpit/api/ui_prefs", json={"layout": "bogus"}).status_code == 400
    assert c.post("/cockpit/api/ui_prefs", json={"theme": "nope"}).status_code == 400
    assert c.post("/cockpit/api/ui_prefs", json={"vtier": "x"}).status_code == 400
