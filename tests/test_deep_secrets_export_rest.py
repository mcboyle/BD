"""deep-secrets-export-rest (O1789 bughunt deep-secrets-export F5, F6, F9, F11).

F5/F6 measured FIXED on BASE (e5bda1004 bd-sec-2-withhold: export_view / site_view
withhold the proxy and accounts values); their tests below are regression pins that
pass on BASE. F9 and F11 were open and are RED on BASE.

F5: POST /api/eol/export wrote templates/<sid>-<date>.bdtpl holding the proxy URL
userinfo password and accounts[].password in plaintext, include_cookies off too:
marketplace._redact is key-based and never descends into lists, and "proxy" is not a
secret key.
F6: the diagnostics bundle (/api/diagnostics_bundle/preview|download and
/api/diagnostics/bundle|download) carried the proxy userinfo password -- the same
key-based redaction lets the "proxy" URL through.
F9: api_secrets_import_apply wrote the site's @cred: reference BEFORE backend.set;
a failing set reported saved 0, but the reference stayed in the live config and was
persisted by the next unrelated save (has_password with no secret behind it).
F11: /api/secrets/import_file answered 500 for content null/list, a body that is not
a JSON object, an empty JSON body (BadRequest) and a body over MAX_CONTENT_LENGTH
(RequestEntityTooLarge) -- all swallowed by the route's catch-all.

The values below are fixed test strings, not credentials.
"""

from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

PROXY_PW = "PXSECRET-dser"
ACCT_PW = "ACCTSECRET-dser"
PROXY_HOST = "proxy.example:3128"


def _cfg():
    return {
        "dser": {
            "name": "dser",
            "base_url": "https://dser.example/",
            "proxy": f"http://pxuser:{PROXY_PW}@{PROXY_HOST}",
            "accounts": [{"username": "acct1", "password": ACCT_PW}],
        }
    }


# ── F5: EOL export ──────────────────────────────────────────────────


def test_f5_eol_export_templates_carry_no_proxy_or_account_password(
        monkeypatch, tmp_path):
    from bulk_downloader import app_eol as AE
    from bulk_downloader import db as DB
    from bulk_downloader import scheduled_exports as SE

    def _no_db():
        raise RuntimeError("dser test: no db")

    live = _cfg()
    before = copy.deepcopy(live)
    dest = tmp_path / "eol-out"
    monkeypatch.setattr(AE, "_app_s_cfg", lambda: live)
    monkeypatch.setattr(AE, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(SE, "resolve_destination", lambda d, pending=False: str(dest))
    monkeypatch.setattr(DB, "db_conn", _no_db)
    app = Flask(__name__)
    app.register_blueprint(AE.eol_bp)
    r = app.test_client().post("/api/eol/export",
                               json={"dest_dir": str(dest)})
    assert r.status_code == 200, r.get_json()
    tpls = sorted((dest / "templates").glob("*.bdtpl"))
    assert tpls, f"no template written: {r.get_json()}"
    blob = b"".join(p.read_bytes() for p in tpls)
    # positive control: the site is exported and its proxy/accounts are marked withheld
    assert b"https://dser.example/" in blob
    assert b'"withheld": true' in blob
    every = b"".join(p.read_bytes() for p in dest.rglob("*") if p.is_file())
    assert PROXY_PW.encode() not in every, "EOL export leaked the proxy password"
    assert ACCT_PW.encode() not in every, "EOL export leaked accounts[].password"
    # the scrub works on a copy: the live config keeps the working proxy/creds
    assert live == before


# ── F6: diagnostics bundle ──────────────────────────────────────────


def _diag_client(monkeypatch, live):
    from bulk_downloader import app_diagnostics as AD
    from bulk_downloader import app_diagnostics_bundle as ADB

    monkeypatch.setattr(AD, "_app_s_cfg", lambda: live)
    monkeypatch.setattr(ADB, "_app_s_cfg", lambda: live)
    app = Flask(__name__)
    app.register_blueprint(AD.diagnostics_bp)
    app.register_blueprint(ADB.diagnostics_bundle_bp)
    return app.test_client()


@pytest.mark.parametrize("path", ["/api/diagnostics_bundle/preview",
                                  "/api/diagnostics/bundle"])
def test_f6_diagnostics_bundle_json_carries_no_proxy_password(monkeypatch, path):
    live = _cfg()
    before = copy.deepcopy(live)
    r = _diag_client(monkeypatch, live).get(path)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    text = r.get_data(as_text=True)
    assert "https://dser.example/" in text  # positive control: the site is there
    assert PROXY_PW not in text, f"{path} leaked the proxy password"
    assert live == before


@pytest.mark.parametrize("path", ["/api/diagnostics_bundle/download",
                                  "/api/diagnostics/download"])
def test_f6_diagnostics_bundle_zip_carries_no_proxy_password(monkeypatch, path):
    live = _cfg()
    r = _diag_client(monkeypatch, live).get(path)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    with zipfile.ZipFile(io.BytesIO(r.get_data())) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist())
    assert b"https://dser.example/" in blob
    assert PROXY_PW.encode() not in blob, f"{path} zip leaked the proxy password"


# ── F9 / F11: secrets import ────────────────────────────────────────


class _Backend:
    name = "master_password"

    def __init__(self, fail=False):
        self.fail = fail
        self.store = {}

    def set(self, key, value):
        if self.fail:
            raise RuntimeError("dser backend set refused")
        self.store[key] = value
        return True

    def is_initialized(self):
        return True

    def is_unlocked(self):
        return True


def _secrets_client(monkeypatch, live, backend, saves):
    from bulk_downloader import app_secrets as AS
    from bulk_downloader import secrets_store as SS

    monkeypatch.setattr(SS, "get_backend", lambda: backend)
    monkeypatch.setattr(AS, "_app_s_cfg", lambda: live)
    monkeypatch.setattr(AS, "_save_sites_config",
                        lambda *a, **k: saves.append(copy.deepcopy(live)))
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # app.py's cap
    app.register_blueprint(AS.secrets_bp)
    return app.test_client()


def test_f9_failed_backend_set_leaves_site_password_and_username(monkeypatch):
    live = {"dser": {"name": "dser", "password": "", "username": "old"}}
    saves = []
    c = _secrets_client(monkeypatch, live, _Backend(fail=True), saves)
    r = c.post("/api/secrets/import_apply",
               json={"records": [{"name": "x", "username": "new",
                                  "password": "pw-dser"}],
                     "site_ids": ["dser"]})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["saved"] == 0 and len(body["errors"]) == 1, body
    assert live["dser"]["password"] == "", (
        "import_apply wrote the @cred: ref although backend.set failed")
    assert live["dser"]["username"] == "old"
    assert all(s["dser"]["password"] == "" for s in saves)


def test_f9_successful_backend_set_links_the_reference(monkeypatch):
    from bulk_downloader import secrets_store as SS

    live = {"dser": {"name": "dser", "password": "", "username": "old"}}
    backend = _Backend()
    c = _secrets_client(monkeypatch, live, backend, [])
    r = c.post("/api/secrets/import_apply",
               json={"records": [{"name": "x", "username": "new",
                                  "password": "pw-dser"}],
                     "site_ids": ["dser"]})
    assert r.get_json()["saved"] == 1
    assert live["dser"]["password"] == SS.make_password_reference("dser")
    assert live["dser"]["username"] == "new"
    assert backend.store[SS.site_password_key("dser")] == "pw-dser"


@pytest.mark.parametrize("sid", [["dser"], {"x": 1}, 5], ids=["list", "dict", "int"])
def test_f9_non_string_site_id_stores_nothing(monkeypatch, sid):
    # security lens: with the set moved before the link, an unhashable site_id reached
    # backend.set and left the secret under "bulkdl-site-<repr>" (saved 1 AND an error).
    live = {"dser": {"name": "dser", "password": ""}}
    backend, saves = _Backend(), []
    c = _secrets_client(monkeypatch, live, backend, saves)
    r = c.post("/api/secrets/import_apply",
               json={"records": [{"name": "x", "password": "pw-dser"}],
                     "site_ids": [sid]})
    assert r.status_code == 400, r.get_data(as_text=True)[:300]
    assert "pw-dser" not in r.get_data(as_text=True)
    assert backend.store == {}, f"secret stored under {sorted(backend.store)}"
    assert saves == [] and live["dser"]["password"] == ""


@pytest.mark.parametrize("kwargs,code", [
    ({"json": {"content": None}}, 400),
    ({"json": {"content": ["a", "b"]}}, 400),
    ({"json": {"content": 7}}, 400),
    ({"json": ["content"]}, 400),
    ({"data": "", "content_type": "application/json"}, 400),
    ({"data": "{not json", "content_type": "application/json"}, 400),
], ids=["null", "list", "int", "body-list", "empty-body", "bad-json"])
def test_f11_import_file_bad_body_is_a_clean_4xx_json(monkeypatch, kwargs, code):
    c = _secrets_client(monkeypatch, {}, _Backend(), [])
    r = c.post("/api/secrets/import_file", **kwargs)
    assert r.status_code == code, (r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_json(silent=True)
    assert isinstance(body, dict) and body.get("ok") is False, r.get_data(as_text=True)[:300]


def test_f11_import_file_over_max_content_length_is_413_not_swallowed(monkeypatch):
    # RequestEntityTooLarge must propagate to the app's 413 handler (app.py
    # _on_request_too_large answers JSON on /api/); this blueprint-only app has no
    # such handler, so the pin is: 413, and not the catch-all's "<Exc>: <msg>" 500.
    c = _secrets_client(monkeypatch, {}, _Backend(), [])
    r = c.post("/api/secrets/import_file",
               data=json.dumps({"content": "x" * (4 * 1024 * 1024 + 16)}),
               content_type="application/json")
    text = r.get_data(as_text=True)
    assert r.status_code == 413, (r.status_code, text[:300])
    assert "RequestEntityTooLarge:" not in text


def test_f11_import_file_unrecognized_string_is_still_400(monkeypatch):
    c = _secrets_client(monkeypatch, {}, _Backend(), [])
    r = c.post("/api/secrets/import_file", json={"content": "not an export"})
    assert r.status_code == 400, r.get_json()
