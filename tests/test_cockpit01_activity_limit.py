"""RED-first repro for F-COCKPIT01-02.

The cockpit ``/api/activity`` route casts ``?limit`` with a bare ``int()``, so a
non-numeric limit raises ``ValueError`` and the request 500s. After the fix a
bad limit falls back to the default (never 500), and a valid limit still works.

Pristine-source RED: ``?limit=abc`` returns HTTP 500.
"""
import importlib.util
from pathlib import Path


def _client():
    p = Path(__file__).resolve().parent.parent / "tools" / "cockpit_console.py"
    spec = importlib.util.spec_from_file_location("_cockpit_console_t", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(m.bp)
    return app.test_client()


def test_activity_limit_non_numeric_does_not_500():
    c = _client()
    r = c.get("/cockpit/api/activity?limit=abc")
    assert r.status_code != 500, f"non-numeric limit 500'd: {r.status_code}"
    # a valid limit still returns a normal response
    assert c.get("/cockpit/api/activity?limit=5").status_code == 200
