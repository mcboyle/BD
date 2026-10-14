"""qt-m4 (queue-templates-io sweep, finder bd-worker-S34-D): a dict template id broke the template list permanently.

POST /api/user_templates {"id": {"a": 1}, ...} answered 200 and stored the dict as the id. Afterwards GET /api/templates
and POST /api/user_templates/import/preview answered 500, and DELETE /api/user_templates/<tid> could not target the row.
A non-string id is now refused (400) and never stored; a string id and an absent id (server-generated) still save.
"""

from __future__ import annotations

import pytest
from flask import Flask


BD_GATE_SCOPE = "module"

_LEARNED = {"download": {"row_selectors": ["a.dl"], "url_attribute": "href"}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from bulk_downloader import app_templates, app_user_templates, user_templates

    store = tmp_path / "user_templates.json"
    monkeypatch.setattr(user_templates, "USER_TEMPLATES_FILE", store)
    assert user_templates.list_user_templates() == [], "the isolated store is not empty"
    app = Flask(__name__)
    app.register_blueprint(app_user_templates.user_templates_bp)
    app.register_blueprint(app_templates.templates_bp)
    return app.test_client()


def _post(client, tid, absent=False):
    body = {
        "name": "QT M4",
        "description": "qt-m4",
        "patterns": ["qtm4\\.example"],
        "learned": _LEARNED,
    }
    if not absent:
        body["id"] = tid
    return client.post("/api/user_templates", json=body)


@pytest.mark.parametrize("bad", [{"a": 1}, ["user_x"], 5, True])
def test_a_non_string_id_is_refused_and_the_list_stays_readable(client, bad):
    from bulk_downloader import user_templates

    r = _post(client, bad)
    stored = [t.get("id") for t in user_templates.list_user_templates()]
    listed = client.get("/api/templates")
    assert r.status_code == 400, (
        "QT-M4: POST /api/user_templates with id %r answered %d; stored ids %r; GET /api/templates then %d"
        % (bad, r.status_code, stored, listed.status_code)
    )
    assert "id must be a string" in r.get_json()["error"], r.get_json()
    assert stored == [], stored
    assert listed.status_code == 200, listed.status_code
    preview = client.post("/api/user_templates/import/preview",
                          json={"templates": [{"id": "user_qtm4_p", "name": "P", "description": "p",
                                               "patterns": [], "learned": _LEARNED}]})
    assert preview.status_code == 200, preview.status_code


def test_a_string_id_still_saves_lists_and_deletes(client):
    """Control: a namespaced string id saves (200), lists on /api/templates, and DELETE can target it."""
    r = _post(client, "user_qtm4_ok")
    assert r.status_code == 200, r.get_json()
    listed = client.get("/api/templates")
    assert listed.status_code == 200
    assert "user_qtm4_ok" in [t["id"] for t in listed.get_json()["templates"]]
    d = client.delete("/api/user_templates/user_qtm4_ok")
    assert d.status_code == 200, d.get_json()


def test_an_absent_id_still_gets_a_generated_id(client):
    """Control: the normal create path (server-assigned user_ id) is unchanged; a null id is treated as absent."""
    for kw in ({"absent": True}, {}):
        r = _post(client, None, **kw)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["template"]["id"].startswith("user_")
    assert client.get("/api/templates").status_code == 200
