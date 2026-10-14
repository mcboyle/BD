"""plaintext-secret-class: every site-password write path uses the vault.

S15 (session-auth-capture) and its class: the site config keeps only a
``@cred:`` reference and the secrets vault holds the password.
- deep-secrets-export F4: POST /api/sites/import of an include_secrets export.
- deep-queue-templates-io F7: POST /api/sites/bulk_csv. Already fixed on main,
  so here it is a control that passes on BASE.
- session-auth-capture S15: the manual-login learn path (finish_manual_login).
- Same class, same owned file: POST /api/config/import.
- deep-secrets-export F7: DELETE /api/sites/<sid> and config import
  mode=replace leave the site's vault ciphertext behind. They now reap it. A
  replace keeps any key a staged replacement still references, so an
  include_passwords export round-trip still resolves.

Driven through the real routes and the real vault helpers, with a stand-in
vault backend. A secret VALUE never appears in an assertion message: the
diagnostics name the route and field only.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import json
from pathlib import Path

import pytest

SENT = "pscS3ntinel-0d9f"  # the secret; never formatted into a message


class _Vault:
    name = "master_password"

    def __init__(self, unlocked=True):
        self.stored = {}
        self.unlocked = unlocked

    def is_unlocked(self):
        return self.unlocked

    def set(self, key, value):
        self.stored[key] = value

    def get(self, key):
        return self.stored.get(key)

    def delete(self, key):
        return self.stored.pop(key, None) is not None

    def list_keys(self):
        return list(self.stored)

    def keys(self):
        """Key names only: an assert on this never renders a stored value."""
        return sorted(self.stored)


@pytest.fixture(autouse=True)
def _sites_file_pin_is_returned_as_found():
    from bulk_downloader import app as a

    saved = a.SITES_FILE
    saved_latch = a._SITES_FILE_LAST_AUTO_OBJECT
    try:
        yield
    finally:
        a.SITES_FILE = saved
        a._SITES_FILE_LAST_AUTO_OBJECT = saved_latch


@pytest.fixture
def rig(fresh_app, monkeypatch):
    from bulk_downloader import secrets_store as ss

    vault = _Vault()
    monkeypatch.setattr(ss, "get_backend", lambda: vault)
    return fresh_app, vault


def _app():
    from bulk_downloader import app as a

    return a


def _disk():
    p = Path(_app().SITES_FILE)
    assert p.is_file(), "rig: sites_config.json was never written"
    return p.read_bytes()


def _leaks(blob):
    """True when the secret is in ``blob``. Asserts only ever see this bool,
    so a failure never renders the value (pytest prints compared operands
    and the arguments of every frame)."""
    return isinstance(blob, (str, bytes)) and (
        SENT.encode() in blob if isinstance(blob, bytes) else SENT in blob
    )


def _assert_vaulted(sid, label):
    """The config (memory and disk) holds the reference, the vault the value."""
    from bulk_downloader import secrets_store as ss

    disk = _disk()
    # Positive control: the probe can see this site in the file it reads.
    assert sid.encode() in disk, f"rig: site {sid} not in sites_config.json"
    on_disk = _leaks(disk)
    assert not on_disk, (
        f"{label}-PLAINTEXT: sites_config.json holds the password value for site {sid}"
    )
    is_ref = _app().s_cfg[sid].get("password") == ss.make_password_reference(sid)
    assert is_ref, f"{label}-NO-REF: s_cfg[{sid}].password is not its @cred: reference"
    in_vault = ss.get_backend().get(ss.site_password_key(sid)) == SENT
    assert in_vault, f"{label}-NOT-IN-VAULT: vault has no password for {sid}"


def _new_site(client, name, secret=None):
    """``secret``: None, "SENT" (the sentinel) or a non-secret literal."""
    body = {"name": name, "login_url": f"https://{name.lower()}.example/login"}
    if secret is not None:
        body["password"] = SENT if secret == "SENT" else secret
    r = client.post("/api/sites", json=body)
    assert r.status_code == 200, f"rig: POST /api/sites {r.status_code}"
    return r.get_json()["id"]


# ── F4: POST /api/sites/import ───────────────────────────────────────────


def test_f4_site_import_vaults_password(rig):
    client, _vault = rig
    r = client.post(
        "/api/sites/import",
        json={
            "config": {
                "name": "PscF4",
                "login_url": "https://pscf4.example/login",
                "username": "u4",
                "password": SENT,
            }
        },
    )
    assert r.status_code == 200, f"rig: import {r.status_code}"
    body = r.get_json()
    sid = body["id"]
    _assert_vaulted(sid, "F4-IMPORT")
    assert body.get("cred_stored") is True, "F4-IMPORT-NOT-REPORTED: no cred_stored"


def test_f4_locked_vault_stores_no_password_anywhere(rig):
    client, vault = rig
    vault.unlocked = False
    r = client.post(
        "/api/sites/import",
        json={
            "config": {
                "name": "PscF4L",
                "login_url": "https://pscf4l.example/login",
                "password": SENT,
            }
        },
    )
    assert r.status_code == 200, f"rig: import {r.status_code}"
    body = r.get_json()
    disk = _disk()
    assert body["id"].encode() in disk, "rig: imported site not on disk"
    on_disk = _leaks(disk)
    assert not on_disk, (
        "F4-LOCKED-PLAINTEXT: a locked vault let the password into sites_config.json"
    )
    in_vault = any(_leaks(v) for v in vault.stored.values())
    assert not in_vault, "F4-LOCKED-STORED: locked vault was written"
    assert body.get("cred_stored") is False and body.get("secrets_locked") is True, (
        f"F4-LOCKED-NOT-REPORTED: reply flags {sorted(k for k in body if k != 'id')}"
    )


# ── deep-queue-templates-io F7: POST /api/sites/bulk_csv (control) ──────


def test_queue_f7_bulk_csv_vaults_password_control(rig):
    client, _vault = rig
    csv = (
        "name,login_url,username,password\n"
        f"PscQ7,https://pscq7.example/login,u7,{SENT}\n"
    )
    r = client.post("/api/sites/bulk_csv", json={"csv": csv})
    assert r.status_code == 200, f"rig: bulk_csv {r.status_code}"
    rows = [x for x in r.get_json().get("results", []) if x.get("status") == "created"]
    assert len(rows) == 1, "rig: bulk_csv created no site"
    _assert_vaulted(rows[0]["id"], "QF7-CSV")


# ── same class: POST /api/config/import ─────────────────────────────────


def test_config_import_vaults_password(rig):
    client, _vault = rig
    r = client.post(
        "/api/config/import?mode=merge",
        json={
            "sites": [
                {
                    "name": "PscCI",
                    "login_url": "https://pscci.example/login",
                    "password": SENT,
                }
            ]
        },
    )
    assert r.status_code == 200, f"rig: config import {r.status_code}"
    sid = next(s for s, c in _app().s_cfg.items() if c.get("name") == "PscCI")
    _assert_vaulted(sid, "CFG-IMPORT")
    assert (
        r.get_json().get("credentials", {}).get(sid, {}).get("cred_stored") is True
    ), "CFG-IMPORT-NOT-REPORTED: no credentials[sid].cred_stored"


def test_config_import_locked_vault_stores_no_password_anywhere(rig):
    client, vault = rig
    vault.unlocked = False
    r = client.post(
        "/api/config/import?mode=merge",
        json={
            "sites": [
                {
                    "name": "PscCL",
                    "login_url": "https://psccl.example/login",
                    "password": SENT,
                }
            ]
        },
    )
    assert r.status_code == 200, f"rig: config import {r.status_code}"
    sid = next(s for s, c in _app().s_cfg.items() if c.get("name") == "PscCL")
    disk = _disk()
    assert sid.encode() in disk, "rig: imported site not on disk"
    on_disk = _leaks(disk)
    assert not on_disk, (
        "CFG-LOCKED-PLAINTEXT: a locked vault let the imported password into "
        "sites_config.json"
    )
    entry = r.get_json().get("credentials", {}).get(sid, {})
    assert entry.get("cred_stored") is False and entry.get("secrets_locked") is True, (
        f"CFG-LOCKED-NOT-REPORTED: flags {sorted(entry)}"
    )


def test_config_import_merge_refused_keeps_old_reference(rig):
    client, vault = rig
    sid = _new_site(client, "PscCM", secret="old-pw-cm")
    ref = _app().s_cfg[sid]["password"]
    vault.unlocked = False
    r = client.post(
        "/api/config/import?mode=merge",
        json={
            "sites": [
                {
                    "name": "PscCM",
                    "login_url": "https://pscm.example/login",
                    "password": SENT,
                }
            ]
        },
    )
    assert r.status_code == 200, f"rig: config import {r.status_code}"
    on_disk = _leaks(_disk())
    assert not on_disk, (
        "CFG-MERGE-LOCKED-PLAINTEXT: merge import wrote the password value"
    )
    kept = _app().s_cfg[sid]["password"] == ref
    assert kept, (
        "CFG-MERGE-LOCKED-LOST-REF: refused merge dropped the old @cred: reference"
    )
    entry = r.get_json().get("credentials", {}).get(sid, {})
    assert entry.get("cred_stored") is False and entry.get("secrets_locked") is True, (
        f"CFG-MERGE-LOCKED-NOT-REPORTED: flags {sorted(entry)}"
    )


# ── S15: manual-login learn path ────────────────────────────────────────


def _learn(monkeypatch, sid):
    from bulk_downloader import learn, login

    runner = _app().runners[sid]
    monkeypatch.setattr(
        login,
        "finalize_manual_login",
        lambda _h: (False, "stub", [], {"clicks": [], "inputs": []}),
    )
    monkeypatch.setattr(
        learn,
        "classify_login",
        lambda _harvest, login_url="": {
            "password_value": SENT,
            "username_value": "learner",
        },
    )
    runner._manual_login_handle = object()
    return runner.finish_manual_login()


def test_s15_learn_vaults_captured_password(rig, monkeypatch):
    client, _vault = rig
    sid = _new_site(client, "PscS15")
    _ok, msg = _learn(monkeypatch, sid)
    assert _app().s_cfg[sid].get("username") == "learner", "rig: learn did not persist"
    _assert_vaulted(sid, "S15-LEARN")
    said = "password" in msg and "not stored" not in msg
    assert said, "S15-LEARN-NOT-REPORTED: message does not say the password was learned"


def test_s15_locked_vault_stores_nothing_and_says_so(rig, monkeypatch):
    client, vault = rig
    vault.unlocked = False
    sid = _new_site(client, "PscS15L")
    _ok, msg = _learn(monkeypatch, sid)
    assert _app().s_cfg[sid].get("username") == "learner", "rig: learn did not persist"
    on_disk = _leaks(_disk())
    assert not on_disk, (
        "S15-LOCKED-PLAINTEXT: learn wrote the password value to sites_config.json"
    )
    on_cfg = _leaks(_app().s_cfg[sid].get("password"))
    assert not on_cfg, (
        "S15-LOCKED-PLAINTEXT: learn put the password value on the live config"
    )
    said = "password not stored" in msg
    assert said, "S15-LOCKED-SILENT: refused learn does not tell the user"


# ── F7: DELETE /api/sites/<sid> and config replace reap the vault ───────


def test_f7_delete_reaps_site_vault_entries(rig):
    client, vault = rig
    from bulk_downloader import secrets_store as ss

    sid = _new_site(client, "PscF7", secret="SENT")
    keep = _new_site(client, "PscF7K", secret="other-pw")
    vault.stored[ss.account_password_key(sid, 0)] = "acct-pw"
    assert ss.site_password_key(sid) in vault.keys(), "rig: password not vaulted"
    r = client.delete(f"/api/sites/{sid}")
    assert r.status_code == 200, f"rig: delete {r.status_code}"
    left = sorted(k for k in vault.stored if k.startswith(ss.site_password_key(sid)))
    assert not left, f"F7-DELETE-LEAVES-CIPHERTEXT: vault still holds {left}"
    assert ss.site_password_key(keep) in vault.keys(), (
        "F7-DELETE-OVERREACH: another site's vault entry was removed"
    )
    assert sorted(r.get_json().get("vault_removed", [])) == sorted(
        [ss.site_password_key(sid), ss.account_password_key(sid, 0)]
    ), "F7-DELETE-NOT-REPORTED: vault_removed does not list the reaped keys"


def test_f7_delete_with_locked_inventory_reports_retained(rig, monkeypatch):
    client, vault = rig
    from bulk_downloader import secrets_store as ss

    sid = _new_site(client, "PscF7R", secret="SENT")

    def _boom():
        raise ss.SecretsIntegrityError("damaged")

    monkeypatch.setattr(vault, "list_keys", _boom)
    r = client.delete(f"/api/sites/{sid}")
    assert r.status_code == 200, f"rig: delete {r.status_code}"
    kept = [e["key"] for e in r.get_json().get("vault_retained", [])]
    assert kept == [ss.site_password_key(sid)], (
        f"F7-RETAINED-SILENT: vault_retained {kept}"
    )
    assert ss.site_password_key(sid) in vault.keys()


def test_f7_replace_reaps_old_entries_and_round_trip_still_resolves(rig):
    client, vault = rig
    from bulk_downloader import secrets_store as ss

    a = _new_site(client, "PscRA", secret="SENT")
    b = _new_site(client, "PscRB", secret="pw-b")
    exp = client.get("/api/config/export?include_passwords=1")
    assert exp.status_code == 200, f"rig: export {exp.status_code}"
    data = json.loads(exp.data)
    sites = data["sites"] if isinstance(data, dict) else data
    sites = [s for s in sites if s.get("name") == "PscRA"]
    assert len(sites) == 1, "rig: export lacks site A"
    r = client.post("/api/config/import?mode=replace", json={"sites": sites})
    assert r.status_code == 200, f"rig: replace {r.status_code}"
    assert ss.site_password_key(b) not in vault.keys(), (
        "F7-REPLACE-LEAVES-CIPHERTEXT: replaced site B's vault entry survived"
    )
    new = [s for s, c in _app().s_cfg.items() if c.get("name") == "PscRA"]
    assert len(new) == 1 and new[0] != a, "rig: replace did not recreate site A"
    ref = _app().s_cfg[new[0]].get("password", "")
    assert ref.startswith(ss.CRED_PREFIX), "rig: round-trip lost the @cred: reference"
    resolves = vault.get(ref[len(ss.CRED_PREFIX) :]) == SENT
    assert resolves, (
        "F7-REPLACE-BROKE-ROUNDTRIP: the replacement's @cred: reference no "
        "longer resolves"
    )
