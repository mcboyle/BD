"""O1789 drb-fix r2 (deep-backup-restore F1 + F10): /api/backup routes.

F10 (S30/S29): POST /api/backup/create with encrypt_scope "sensitive" and no
passphrase returned a PLAIN zip with secrets.json and cookies readable; the
only signal was the X-Backup-Encrypted: 0 header. The caller asked for the
secrets to be encrypted, so the route refuses (400) instead. /preview reads the
body exactly as /create does and refuses the same body.

F1 (S23): POST /api/backup/restore wrote sites_config.json to disk but the
running app kept its in-memory sites, so GET /api/sites did not list the
restored sites and the next config save rewrote the file from memory, undoing
the restore. With no site state loaded the route now reloads the restored
file; with sites already loaded (live runners) it says restart_required.

r3 (lens DRBS-D F-SEC-1): restored content skips the write routes, so a site id
"../../ESCAPE_SID" went live and made a runner dir outside the data root. Only
plain-token site ids with paths the write routes accept are reloaded now.
Advisory A2/A3/A5: blank or non-string passphrase -> 400 (no 500, no temp dir
left); a restored file that is not a JSON object is never reported reloaded.
"""

import io
import json
import os
import zipfile

import pytest
from flask import Flask

from bulk_downloader import app_backup

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

SECRET = b"PLAINTEXT-SECRET-drbr2"
COOKIE = b"PLAINTEXT-COOKIE-drbr2"
REFUSAL = "encrypt_scope 'sensitive' needs a passphrase"


# --- F10 ---------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "cookies").mkdir(parents=True)
    (data / "sites_config.json").write_text(json.dumps({"s1": {"name": "S1"}}))
    (data / "secrets.json").write_bytes(b'{"api_key":"' + SECRET + b'"}')
    (data / "cookies" / "a.json").write_bytes(b'{"c":"' + COOKIE + b'"}')
    monkeypatch.setenv("BD_INSTALL_DIR", str(data))
    monkeypatch.delenv("BD_HOME", raising=False)
    return data


def _bp_client():
    app = Flask(__name__)
    app.register_blueprint(app_backup.backup_bp)
    return app.test_client()


def _post(client, route, body):
    return client.post(route, data=json.dumps(body), content_type="application/json")


def _plaintext_in(data):
    if SECRET in data or COOKIE in data:
        return True
    if data[:2] != b"PK":
        return False
    z = zipfile.ZipFile(io.BytesIO(data))
    return any(SECRET in z.read(n) or COOKIE in z.read(n)
               for n in z.namelist() if n in ("secrets.json", "cookies/a.json"))


@pytest.mark.parametrize("body", [
    {"encrypt_scope": "sensitive"},
    {"encrypt_scope": "sensitive", "passphrase": ""},
    {"encrypt_scope": "sensitive", "passphrase": 0},
    {"encrypt_scope": "sensitive", "passphrase": None},
], ids=["absent", "empty", "zero", "null"])
def test_f10_sensitive_scope_without_passphrase_is_refused(data_dir, body):
    r = _post(_bp_client(), "/api/backup/create", body)
    data = r.get_data()
    r.close()
    assert not _plaintext_in(data), "secrets left the server in plaintext"
    assert r.status_code == 400, (r.status_code, r.headers.get("X-Backup-Encrypted"))
    js = json.loads(data)
    assert js["ok"] is False
    assert REFUSAL in js["error"]


def test_f10_preview_refuses_the_same_body(data_dir):
    r = _post(_bp_client(), "/api/backup/preview", {"encrypt_scope": "sensitive"})
    assert r.status_code == 400, r.get_json()
    assert REFUSAL in r.get_json()["error"]


def test_f10_control_sensitive_with_passphrase_encrypts(data_dir):
    r = _post(_bp_client(), "/api/backup/create",
              {"encrypt_scope": "sensitive", "passphrase": "pw"})
    data = r.get_data()
    r.close()
    assert r.status_code == 200
    assert r.headers["X-Backup-Encrypted"] == "1"
    assert data[:2] == b"PK" and not _plaintext_in(data)


def test_f10_control_default_scope_without_passphrase_stays_plain(data_dir):
    # Unchanged: no scope + no passphrase is the documented plaintext backup.
    r = _post(_bp_client(), "/api/backup/create", {})
    data = r.get_data()
    r.close()
    assert r.status_code == 200
    assert r.headers["X-Backup-Encrypted"] == "0"
    assert _plaintext_in(data)


# --- F1 ----------------------------------------------------------------------

def _zip(sites):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("sites_config.json",
                   sites if isinstance(sites, str) else json.dumps(sites))
    return buf.getvalue()


def _restore(client, sites, dry_run=False):
    form = {"file": (io.BytesIO(_zip(sites)), "b.zip")}
    if dry_run:
        form["dry_run"] = "1"
    return client.post("/api/backup/restore", data=form,
                       content_type="multipart/form-data")


def _api_site_ids(client):
    r = client.get("/api/sites_list")
    assert r.status_code == 200, r.status_code
    return {s["site_id"] for s in r.get_json()["sites"]}


RESTORED = {"restored_site": {"name": "Restored", "start_url": "https://restored.test/"}}


def _sites_file(clean_workdir, monkeypatch):
    # SITES_FILE is published once per process; pin it to this test's dir
    # (the module attribute is the documented patch point).
    from bulk_downloader import app as A
    f = clean_workdir / "sites_config.json"
    monkeypatch.setattr(A, "SITES_FILE", f)
    assert app_backup._data_dir().resolve() / "sites_config.json" == f
    return f


def test_f1_restore_is_live_and_survives_the_next_save(fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app as A
    client = fresh_app
    sites_file = _sites_file(clean_workdir, monkeypatch)
    assert _api_site_ids(client) == set()
    r = _restore(client, RESTORED)
    js = r.get_json()
    assert r.status_code == 200 and js["ok"] is True, js
    assert "restored_site" in json.loads(sites_file.read_text())
    assert "restored_site" in _api_site_ids(client), (
        f"restored site not loaded: reloaded={js.get('reloaded')!r}")
    assert js.get("reloaded") is True and js.get("restart_required") is False
    r = client.post("/api/sites", json={"name": "AddedAfter", "start_url": "https://added.test/"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)[:200]
    on_disk = json.loads(sites_file.read_text())
    assert "restored_site" in on_disk, f"next save undid the restore: {sorted(on_disk)}"
    assert len(on_disk) == 2
    assert A.runners.get("restored_site") is not None


def test_f1_negative_control_dry_run_does_not_reload(fresh_app, clean_workdir, monkeypatch):
    client = fresh_app
    _sites_file(clean_workdir, monkeypatch)
    js = _restore(client, RESTORED, dry_run=True).get_json()
    assert js["ok"] is True, js
    assert "reloaded" not in js and "restart_required" not in js
    assert _api_site_ids(client) == set()


def test_f1_loaded_sites_are_not_hot_swapped_restart_required(fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app as A
    client = fresh_app
    _sites_file(clean_workdir, monkeypatch)
    r = client.post("/api/sites", json={"name": "Live", "start_url": "https://live.test/"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)[:200]
    live = set(A.runners)
    assert live
    js = _restore(client, RESTORED).get_json()
    assert js["ok"] is True, js
    assert js.get("reloaded") is False and js.get("restart_required") is True, js
    assert set(A.runners) == live


def test_f1_app_reading_another_file_is_not_reloaded(fresh_app, clean_workdir, monkeypatch, tmp_path_factory):
    # BD_SITES_CONFIG_PATH can point the app away from the restore target:
    # loading would read the wrong file, so the caller is told to restart.
    from bulk_downloader import app as A
    client = fresh_app
    monkeypatch.setattr(A, "SITES_FILE", tmp_path_factory.mktemp("other") / "sites_config.json")
    js = _restore(client, RESTORED).get_json()
    assert js["ok"] is True, js
    assert js.get("reloaded") is False and js.get("restart_required") is True, js
    assert _api_site_ids(client) == set()


# --- r3: F-SEC-1 + advisory A2/A3/A5 ---------------------------------------

@pytest.mark.parametrize("route", ["/api/backup/create", "/api/backup/preview"])
@pytest.mark.parametrize("passphrase,why", [
    ("   ", "passphrase must not be blank"),
    (123, "passphrase must be a string"),
    (True, "passphrase must be a string"),
    ([1], "passphrase must be a string"),
    ({"a": 1}, "passphrase must be a string"),
], ids=["blank", "int", "true", "list", "dict"])
def test_a2_a3_bad_passphrase_is_400_and_leaves_no_temp_dir(
        data_dir, tmp_path, monkeypatch, route, passphrase, why):
    import tempfile
    td = tmp_path / "tmp"
    td.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(td))
    r = _post(_bp_client(), route, {"passphrase": passphrase})
    data = r.get_data()
    r.close()
    assert not _plaintext_in(data)
    assert r.status_code == 400, (r.status_code, data[:200])
    assert why in json.loads(data)["error"]
    assert sorted(p.name for p in td.iterdir()) == []


# The lens probe's payload (DRBS-D EV test_zz_secprobe_drbr2.py, trav_sid).
TRAV_SID = {"../../ESCAPE_SID": {"name": "x", "start_url": "https://e.test/",
                                 "download_dir": "../../ESCAPE_DL",
                                 "cookie_file": "/etc/passwd"}}


@pytest.mark.parametrize("sites,why", [
    (TRAV_SID, "is not a plain token"),
    ({"sub/ESCAPE_SID": {"name": "x", "start_url": "https://e.test/"}}, "is not a plain token"),
    ({"..": {"name": "x", "start_url": "https://e.test/"}}, "is not a plain token"),
    ({"ok_site": {"name": "x", "start_url": "https://e.test/",
                  "download_dir": "../../ESCAPE_DL"}}, "download_dir cannot contain '..'"),
    ({"ok_site": {"name": "x", "start_url": "https://e.test/",
                  "accounts": [{"cookie_file": "rel/c.json"}]}},
     "accounts[0].cookie_file must be an absolute path"),
], ids=["trav_sid", "slash_sid", "dotdot_sid", "trav_download_dir", "relative_account_cookie"])
def test_fsec1_unsafe_restored_sites_are_not_loaded(fresh_app, clean_workdir, monkeypatch, sites, why):
    from bulk_downloader import app as A
    client = fresh_app
    sites_file = _sites_file(clean_workdir, monkeypatch)
    before = set(os.listdir(clean_workdir.parent))
    js = _restore(client, sites).get_json()
    assert js["ok"] is True, js
    assert json.loads(sites_file.read_text()) == sites
    assert not A.runners and not A.s_cfg, (
        f"unsafe restored site went live: runners={sorted(A.runners)}")
    assert js.get("reloaded") is False and js.get("restart_required") is True, js
    assert why in js.get("reload_refused", ""), js
    assert set(os.listdir(clean_workdir.parent)) - before == set()


def test_fsec1_control_plain_token_with_absolute_dir_still_loads(fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app as A
    client = fresh_app
    _sites_file(clean_workdir, monkeypatch)
    sites = {"site-1.a": {"name": "C", "start_url": "https://c.test/",
                          "download_dir": str(clean_workdir / "dl")}}
    js = _restore(client, sites).get_json()
    assert js["ok"] is True and js.get("reloaded") is True, js
    assert "reload_refused" not in js
    assert A.runners.get("site-1.a") is not None


@pytest.mark.parametrize("raw", ["{not json", "[1, 2, 3]"], ids=["bad_json", "non_object"])
def test_a5_unloadable_restored_file_is_not_reported_reloaded(fresh_app, clean_workdir, monkeypatch, raw):
    client = fresh_app
    _sites_file(clean_workdir, monkeypatch)
    js = _restore(client, raw).get_json()
    assert js["ok"] is True, js
    assert js.get("reloaded") is False, f"unloadable file reported live: {js}"
    assert js.get("restart_required") is True and js.get("reload_refused"), js
