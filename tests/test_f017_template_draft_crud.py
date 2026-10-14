"""F017 -- Template Manager draft create / read / edit / delete.

The /templates row's bar is "list; create TEST template, edit, delete it". Drafts
were list/promote only. These drive the store and the /api/template_manager/draft
routes through that whole loop, and pin the safety edges: a draft is never stored
as enabled, a name cannot leave the drafts dir, an existing draft is not
overwritten, reviewed templates cannot be deleted here, and a secret-looking
value is refused on write and on read.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask

from bulk_downloader import app_template_manager as atm
from bulk_downloader import template_manager as tm

BD_GATE_SCOPE = "module"

URL = "/api/template_manager/draft"
FILE = "test.example.com.template-draft.json"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    rd, dd = tmp_path / "reviewed", tmp_path / "drafts"
    rd.mkdir()
    (rd / "keep.example.com.template.json").write_text(
        json.dumps({"host": "keep.example.com", "status": "enabled"}), "utf-8")
    monkeypatch.setattr(tm, "REVIEWED_DIR", rd)
    monkeypatch.setattr(tm, "DRAFTS_DIR", dd)  # absent until the first create
    return rd, dd


@pytest.fixture
def client(dirs, monkeypatch):
    monkeypatch.setattr(atm, "_check_csrf", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(atm.template_manager_bp)
    return app.test_client()


def _drafts(c):
    return {d["file"]: d for d in c.get("/api/template_manager").get_json()["drafts"]}


def test_create_edit_delete_round_trip(client, dirs):
    _, dd = dirs
    r = client.post(URL, json={"name": "test.example.com", "template": {
        "status": "enabled", "selectors": {"download": {"row_selectors": ["a.dl"]}}}})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"ok": True, "created": FILE}
    listed = _drafts(client)[FILE]
    # never stored as enabled, host defaulted from the name
    assert (listed["host"], listed["status"], listed["enabled"]) == (
        "test.example.com", "draft", False)
    assert listed["selectors"] == ["download"]

    got = client.get(URL, query_string={"file": FILE}).get_json()
    assert got["ok"] is True and got["file"] == FILE
    assert got["template"]["selectors"]["download"]["row_selectors"] == ["a.dl"]

    edited = dict(got["template"], resolutions=[1080], status="enabled")
    r = client.put(URL, json={"file": FILE, "template": edited})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "updated": FILE}
    on_disk = json.loads((dd / FILE).read_text("utf-8"))
    assert on_disk["resolutions"] == [1080] and on_disk["status"] == "draft"
    assert _drafts(client)[FILE]["resolutions"] == [1080]

    r = client.delete(URL, query_string={"file": FILE})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "deleted": FILE}
    assert not (dd / FILE).exists() and FILE not in _drafts(client)
    assert sorted(p.name for p in dd.iterdir()) == []  # no tmp left behind
    r = client.delete(URL, query_string={"file": FILE})
    assert r.status_code == 404


def test_create_refuses_overwrite(client, dirs):
    _, dd = dirs
    assert client.post(URL, json={"name": "test.example.com",
                                  "template": {"v": 1}}).status_code == 200
    r = client.post(URL, json={"name": "test.example.com", "template": {"v": 2}})
    assert r.status_code == 409 and r.get_json()["error"] == "draft already exists"
    assert json.loads((dd / FILE).read_text("utf-8"))["v"] == 1
    assert sorted(p.name for p in dd.iterdir()) == [FILE]


@pytest.mark.parametrize("name", [
    "", "../evil", "a/b", "a\\b", ".hidden", "a..b", "-x", "a b", "é.com", "x" * 121])
def test_create_rejects_bad_names(client, dirs, name):
    _, dd = dirs
    r = client.post(URL, json={"name": name, "template": {}})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid draft name"
    assert not dd.exists() or list(dd.iterdir()) == []


def test_name_with_suffix_and_max_length_accepted(dirs):
    _, dd = dirs
    assert tm.create_draft(FILE)["created"] == FILE
    long = "x" * 120
    assert tm.create_draft(long)["created"] == long + ".template-draft.json"


@pytest.mark.parametrize("body", [[], "x", 3, None])
def test_template_must_be_object(client, dirs, body):
    _, dd = dirs
    tm.create_draft("test.example.com")
    r = client.put(URL, json={"file": FILE, "template": body})
    assert r.status_code == 400
    assert r.get_json()["error"] == "template must be a JSON object"


def test_reviewed_template_cannot_be_deleted_or_edited(client, dirs):
    rd, _ = dirs
    for f in ("keep.example.com.template.json", "../reviewed/keep.example.com.template.json"):
        assert client.delete(URL, query_string={"file": f}).status_code == 400
        assert client.put(URL, json={"file": f, "template": {}}).status_code == 400
    assert (rd / "keep.example.com.template.json").is_file()


def test_edit_missing_draft_is_404(client, dirs):
    for r in (client.put(URL, json={"file": FILE, "template": {}}),
              client.get(URL, query_string={"file": FILE})):
        assert r.status_code == 404
        assert r.get_json() == {"ok": False, "error": "draft not found"}


def test_secret_values_refused_on_write_and_read(client, dirs):
    _, dd = dirs
    # a JWT built at runtime, so no JWT-shaped literal sits in the repo for the secret scanner
    jwt = ".".join(base64.urlsafe_b64encode(part).decode().rstrip("=")
                   for part in (b'{"alg":"HS256"}', b'{"sub":"1234567890"}', b"signaturesignature"))
    r = client.post(URL, json={"name": "test.example.com",
                               "template": {"network_patterns": ["/api?token=" + jwt]}})
    assert r.status_code == 400 and "secret-looking" in r.get_json()["error"]
    assert not dd.exists() or list(dd.iterdir()) == []
    # a secret that reached disk some other way is not served to the edit form
    dd.mkdir(exist_ok=True)
    (dd / FILE).write_text(json.dumps({"cookie": "session=" + jwt}), "utf-8")
    r = client.get(URL, query_string={"file": FILE})
    assert r.status_code == 400 and "secret-looking" in r.get_json()["error"]
    assert jwt not in r.get_data(as_text=True)
    # and an edit cannot write one in
    (dd / FILE).write_text("{}", "utf-8")
    r = client.put(URL, json={"file": FILE, "template": {"auth": "Bearer " + jwt}})
    assert r.status_code == 400
    assert json.loads((dd / FILE).read_text("utf-8")) == {}


def test_too_deep_or_too_large_refused(dirs):
    deep = cur = {}
    for _ in range(tm._DRAFT_MAX_DEPTH):
        cur["n"] = {}
        cur = cur["n"]
    assert tm.create_draft("a.example.com", deep)["error"] == "template is nested too deeply"
    ok = cur = {}
    for _ in range(tm._DRAFT_MAX_DEPTH - 1):
        cur["n"] = {}
        cur = cur["n"]
    assert tm.create_draft("b.example.com", ok)["ok"] is True
    big = {"s": "a b " * (tm._DRAFT_MAX_BYTES // 4)}
    assert tm.create_draft("c.example.com", big)["error"] == "template is too large"


def test_overlapping_edits_both_save(client, dirs, monkeypatch):
    # Two saves of one draft that are both mid-write at the same moment must not
    # share a temp file: each one's rename has to find its own.
    _, dd = dirs
    tm.create_draft("test.example.com")
    gate, real_replace = threading.Barrier(2), os.replace

    def replace_together(src, dst):
        if Path(dst) == dd / FILE:
            gate.wait(timeout=10)
        return real_replace(src, dst)

    monkeypatch.setattr(tm.os, "replace", replace_together)

    def put(res):  # one client per request, as two browsers would be
        r = client.application.test_client().put(URL, json={"file": FILE, "template": {"resolutions": [res]}})
        return r.status_code, r.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        got = list(pool.map(put, [720, 1080]))
    assert got == [(200, {"ok": True, "updated": FILE})] * 2
    assert json.loads((dd / FILE).read_text("utf-8"))["resolutions"] in ([720], [1080])
    assert sorted(p.name for p in dd.iterdir()) == [FILE]  # no temp file left behind


def test_failed_edit_leaves_draft_and_no_temp_file(dirs, monkeypatch):
    _, dd = dirs
    tm.create_draft("test.example.com", {"v": 1})

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(tm.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        tm.update_draft(FILE, {"v": 2})
    assert json.loads((dd / FILE).read_text("utf-8"))["v"] == 1
    assert sorted(p.name for p in dd.iterdir()) == [FILE]


def test_routes_check_csrf_on_writes_only(dirs, monkeypatch):
    seen = []
    monkeypatch.setattr(atm, "_check_csrf", lambda *a, **k: seen.append(1))
    app = Flask(__name__)
    app.register_blueprint(atm.template_manager_bp)
    c = app.test_client()
    c.get(URL, query_string={"file": FILE})
    assert seen == []
    c.post(URL, json={"name": "test.example.com"})
    c.put(URL, json={"file": FILE, "template": {}})
    c.delete(URL, query_string={"file": FILE})
    assert len(seen) == 3
