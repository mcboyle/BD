"""dsw-fix (bughunt deep-spa-writes-queue-library F1, F2, F6): write endpoints that
answered success for objects that do not exist.

F1  /api/tags/add tagged history ids with no history row (orphan tag rows a later
    row could inherit); the SPA said "Tag applied".
F2  /api/stream/token/<hid> minted a signed read token for a row that does not exist.
F6  /api/tags/rename of a tag no row carries answered ok:true -> SPA "Tag renamed".
Each fix keeps the real-object path (controls below).
"""
from __future__ import annotations

import os

import pytest
from flask import Flask

from bulk_downloader import app_stream, app_tags, db, tags

BD_GATE_SCOPE = "module"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(str(tmp_path), "dsw.db"))
    monkeypatch.setattr(tags, "_tags_table_ready", False)
    db.db_init()
    monkeypatch.setattr(app_tags, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(app_stream, "_check_csrf", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(app_tags.tags_bp)
    app.register_blueprint(app_stream.stream_bp)
    return app.test_client()


def _history(n=1):
    with db.db_conn() as cx:
        return [cx.execute("INSERT INTO history(status, filename) VALUES ('done', ?)",
                           (f"f{i}.mp4",)).lastrowid for i in range(n)]


def _tag_rows(tag):
    with db.db_conn() as cx:
        return sorted(r[0] for r in cx.execute(
            "SELECT history_id FROM history_tags WHERE tag = ?", (tag,)))


def test_f1_unknown_history_ids_are_not_tagged(client):
    r = client.post("/api/tags/add", json={"history_ids": [999991, 999992], "tag": "ghost"})
    body = r.get_json()
    assert _tag_rows("ghost") == [], "dsw-fix F1: orphan tag rows stored for unknown ids"
    assert body["ok"] is False and body["added"] == 0 and body["unknown"] == 2, body
    assert "no such history row" in body["error"]


def test_f1_mixed_ids_tag_only_real_rows(client):
    real = _history(2)
    body = client.post("/api/tags/add", json={"history_ids": real + [999993], "tag": "mix"}).get_json()
    assert body["ok"] is True and body["added"] == 2 and body["unknown"] == 1, body
    assert _tag_rows("mix") == sorted(real)


def test_f1_control_real_rows_and_already_had(client):
    real = _history(2)
    assert client.post("/api/tags/add", json={"history_ids": real, "tag": "keep"}).get_json()["added"] == 2
    again = client.post("/api/tags/add", json={"history_ids": real, "tag": "keep"}).get_json()
    assert again["ok"] is True and again["added"] == 0 and again["already_had"] == 2, again
    assert client.post("/api/tags/add", json={"history_ids": [], "tag": "keep"}).status_code == 400


def test_f2_no_token_for_missing_history_row(client):
    r = client.post("/api/stream/token/999999", json={})
    body = r.get_json()
    assert r.status_code == 404, f"dsw-fix F2: token minted for a missing row: {body}"
    assert body["ok"] is False and "token" not in body


def test_f2_control_token_for_real_row(client):
    hid = _history(1)[0]
    body = client.post(f"/api/stream/token/{hid}", json={}).get_json()
    assert body["ok"] is True and body["history_id"] == hid
    assert body["token"].startswith(f"v1.{hid}.")


def test_f6_rename_of_unknown_tag_is_not_a_success(client):
    body = client.post("/api/tags/rename", json={"old": "nosuchtag", "new": "other"}).get_json()
    assert body["ok"] is False, f"dsw-fix F6: rename of an absent tag reported ok: {body}"
    assert "nosuchtag" in body["error"]


def test_f6_control_rename_and_merge_still_succeed(client):
    a, b = _history(2)
    client.post("/api/tags/add", json={"history_ids": [a, b], "tag": "old"})
    client.post("/api/tags/add", json={"history_ids": [b], "tag": "new"})
    body = client.post("/api/tags/rename", json={"old": "old", "new": "new"}).get_json()
    assert body == {"ok": True, "renamed": 1, "merged": 1}, body
    assert _tag_rows("new") == sorted([a, b]) and _tag_rows("old") == []
    bad = client.post("/api/tags/rename", json={"old": "x", "new": ""}).get_json()
    assert bad["ok"] is False and bad["error"] == "invalid tag(s)"
