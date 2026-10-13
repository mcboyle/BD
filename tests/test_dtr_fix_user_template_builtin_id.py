"""dtr-fix F4 (deep-templates-roundtrip sweep, bd-worker-C2-C, S21): a user template could take a hand-written built-in id.

POST /api/user_templates {"id": "video_js", ...} answered 200, and GET /api/templates then listed ONE "video_js" -- the user
entry -- with the built-in gone, while site_templates.get("video_js") still returned the built-in. user_templates.py says ids
are "namespaced with user_ prefix so they can never collide". The seeded O1517 built-ins carry user_ ids and are the one
deliberate override (fx-templates-vm-seed), so they stay writable.
"""

from __future__ import annotations

import pytest
from flask import Flask


BD_GATE_SCOPE = "module"

_LEARNED = {"download": {"row_selectors": ["a.dl"], "url_attribute": "href"}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from bulk_downloader import app_user_templates, user_templates

    store = tmp_path / "user_templates.json"
    monkeypatch.setattr(user_templates, "USER_TEMPLATES_FILE", store)
    assert user_templates.list_user_templates() == [], "the isolated store is not empty"
    app = Flask(__name__)
    app.register_blueprint(app_user_templates.user_templates_bp)
    return app.test_client()


def _post(client, tid):
    body = {
        "name": "DTR SHADOW",
        "description": "dtr-fix",
        "patterns": ["dtr\\.example"],
        "learned": _LEARNED,
    }
    if tid is not None:
        body["id"] = tid
    return client.post("/api/user_templates", json=body)


def _builtin_ids():
    from bulk_downloader.site_templates import SEEDED_BUILTIN_IDS, TEMPLATES

    return {t["id"] for t in TEMPLATES} - SEEDED_BUILTIN_IDS, SEEDED_BUILTIN_IDS


def test_a_builtin_id_is_refused_and_the_builtin_stays_listed(client):
    from bulk_downloader import templates

    hand_written, _ = _builtin_ids()
    assert "video_js" in hand_written, (
        "fixture: video_js is no longer a hand-written built-in"
    )
    r = _post(client, "video_js")
    listed = [t for t in templates.list_templates() if t["id"] == "video_js"]
    assert r.status_code == 400, (
        "DTR-F4: POST /api/user_templates with built-in id video_js answered %d; /api/templates now lists %r"
        % (r.status_code, [t["source"] for t in listed])
    )
    assert [t["source"] for t in listed] == ["builtin"], listed
    assert "built-in" in r.get_json()["error"]


def test_a_user_prefixed_id_still_saves_beside_the_builtins(client):
    """Control: the namespaced id the docstring promises coexists with the built-ins."""
    from bulk_downloader import templates

    r = _post(client, "user_dtr_rt")
    assert r.status_code == 200, r.get_json()
    ids = [t["id"] for t in templates.list_templates()]
    assert "user_dtr_rt" in ids and "video_js" in ids


def test_a_new_template_without_an_id_still_saves(client):
    """Control: the normal create path (server-assigned user_ id) is unchanged."""
    r = _post(client, None)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["template"]["id"].startswith("user_")


def test_a_seeded_builtin_may_still_be_overridden(client):
    """Control: the O1517 seeded built-ins (user_ ids) are the deliberate override (fx-templates-vm-seed)."""
    from bulk_downloader import templates

    _, seeded = _builtin_ids()
    assert seeded, "fixture: no seeded built-ins to override"
    sid = sorted(seeded)[0]
    r = _post(client, sid)
    assert r.status_code == 200, r.get_json()
    assert [t["source"] for t in templates.list_templates() if t["id"] == sid] == [
        "user"
    ]
