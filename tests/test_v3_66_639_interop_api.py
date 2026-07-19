"""Cut v3.66.639 / INTEROP-GOV-1b: the operator API over the interop registry.

GOV-1 (638) landed the keystone (bulk_downloader.interop_registry) + the extension
gate, but the registry could only be populated programmatically. This adds the
operator surface -- a thin blueprint over the keystone so the operator can register
an interop item, record its provenance, acknowledge its risk, and enable it:

  GET  /api/interop/registry     -> {ok, items:[{kind,item_id,...}], kinds:[...]}
  POST /api/interop/register     -> register (chromium_extension: dir hashed server-side)
  POST /api/interop/acknowledge  -> set risk_acknowledged (400 if unregistered)
  POST /api/interop/enable       -> set enabled flag  (400 if unregistered)

Charter unchanged: the registry RECORDS provenance and REQUIRES ack+enable; it is
not an allowlist and BD ships nothing. The three mutating routes are POST-only and
operator-facing (wired in the Maintenance UI so operator_facing_unwired stays 0).

CSRF: _check_csrf skips sessionless requests, so the test client reaches the
handlers directly; real browser sessions go through the same /api/ CSRF gate.

RED on pristine 3.66.638: none of the /api/interop/* routes exist.
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytestmark = pytest.mark.bd_module_wipe

_MUTATORS = ("/api/interop/register", "/api/interop/acknowledge", "/api/interop/enable")


def _client():
    os.environ["BD_HOME"] = tempfile.mkdtemp()   # isolate the registry per test
    from bulk_downloader import app as a
    return a.app.test_client()


def test_routes_registered():
    from bulk_downloader import app as a
    rules = {r.rule: {m for m in r.methods if m not in ("HEAD", "OPTIONS")}
             for r in a.app.url_map.iter_rules()}
    assert "/api/interop/registry" in rules and "GET" in rules["/api/interop/registry"]
    for path in _MUTATORS:
        assert path in rules and "POST" in rules[path], path


def test_mutators_are_post_only():
    c = _client()
    for path in _MUTATORS:
        assert c.get(path).status_code == 405, path


def test_registry_lists_kinds_and_is_initially_empty():
    body = _client().get("/api/interop/registry").get_json()
    assert body["ok"] is True
    assert body["items"] == []
    assert "chromium_extension" in body["kinds"]


def test_register_rejects_unknown_kind():
    r = _client().post("/api/interop/register", json={"kind": "bogus", "item_id": "x"})
    assert r.status_code == 400
    assert "kind" in (r.get_json() or {}).get("error", "").lower()


def test_register_requires_item_id():
    r = _client().post("/api/interop/register", json={"kind": "ytdlp_plugin"})
    assert r.status_code == 400
    assert "item_id" in (r.get_json() or {}).get("error", "")


def test_register_records_off_by_default():
    c = _client()
    r = c.post("/api/interop/register",
               json={"kind": "ytdlp_plugin", "item_id": "repo/plug", "source": "github",
                     "commit": "abc123"})
    rec = r.get_json()["record"]
    assert rec["risk_acknowledged"] is False and rec["enabled"] is False
    assert rec["commit"] == "abc123"
    items = c.get("/api/interop/registry").get_json()["items"]
    assert any(it["item_id"] == "repo/plug" and it["kind"] == "ytdlp_plugin" for it in items)


def test_acknowledge_unregistered_is_400():
    r = _client().post("/api/interop/acknowledge",
                       json={"kind": "ytdlp_plugin", "item_id": "ghost"})
    assert r.status_code == 400


def test_register_then_ack_then_enable_flow():
    c = _client()
    c.post("/api/interop/register", json={"kind": "ytdlp_plugin", "item_id": "p"})
    assert c.post("/api/interop/acknowledge",
                  json={"kind": "ytdlp_plugin", "item_id": "p"}).status_code == 200
    assert c.post("/api/interop/enable",
                  json={"kind": "ytdlp_plugin", "item_id": "p", "enabled": True}).status_code == 200
    it = [x for x in c.get("/api/interop/registry").get_json()["items"]
          if x["item_id"] == "p"][0]
    assert it["risk_acknowledged"] is True and it["enabled"] is True


def test_register_chromium_extension_hashes_dir_serverside():
    """For a chromium extension the server hashes the dir (provenance pin) so the
    operator does not supply a hash."""
    c = _client()
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        fh.write('{"name":"x"}')
    rec = c.post("/api/interop/register",
                 json={"kind": "chromium_extension", "item_id": d}).get_json()["record"]
    assert rec["sha256"] and len(rec["sha256"]) == 64   # computed server-side
