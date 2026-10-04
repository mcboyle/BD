"""O1826 C01 (findings M150, M148): the secrets store fails closed.

M150: when ``configure_backend`` cannot construct the selected backend it
swallows the exception, and ``get_backend`` used to hand back a fresh,
uncached ``PlaintextBackend`` whose ``set`` is a no-op.  A caller that stored a
credential through it got a silent success and the secret was discarded.

M148: ``WindowsCredentialBackend._load_index`` turns any metadata read error
into ``[]``.  The next ``set`` then saved an index holding only the new key, so
every other key in an unreadable or corrupt ``secrets_meta.json`` was dropped.
The read-merge-write rotation stamp had the same shape through ``_read_meta``.

r2 (cx1 REFUTE, O1848): ``vpn_config.store_secrets`` caught either refusal and
kept the submitted value, so POST /api/vpn/tunnels wrote the password into
plaintext tunnels.json and answered 200. The refusal now propagates and the
route answers 503 with nothing written. PM ruling O1876 widened this to any
failed write on a real backend, including a locked master-password vault.

Every path in this module lives below pytest's temporary root; the vault and
metadata attributes are patched before any backend is constructed.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

_MASTER = "o1826-c01-isolated-master-password"
_KEYS = ["bulkdl-site-a", "bulkdl-site-b", "bulkdl-site-c"]
_NEW = "bulkdl-site-new"
_VALUE = "o1826-c01-isolated-secret-value"


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    install = tmp_path / "install"
    for directory in (home, temp, install):
        directory.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.chdir(install)
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)

    vault = install / "secrets.json"
    meta = install / "secrets_meta.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", vault)
    monkeypatch.setattr(ss, "SECRETS_META_FILE", meta)
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    yield vault, meta


class _KeyringDouble:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set_password(self, _service, key, value):
        self.values[key] = value

    def get_password(self, _service, key):
        return self.values.get(key)

    def delete_password(self, _service, key):
        self.values.pop(key, None)


def _windows_backend(monkeypatch):
    keyring = _KeyringDouble()
    monkeypatch.setattr(ss, "keyring", keyring, raising=False)
    return object.__new__(ss.WindowsCredentialBackend), keyring


def _break_master_password_backend(monkeypatch):
    def refusing_init(self):
        raise RuntimeError("o1826-c01 synthetic backend construction failure")

    monkeypatch.setattr(ss, "_detect_default_backend_name",
                        lambda: "master_password")
    monkeypatch.setattr(ss.MasterPasswordBackend, "__init__", refusing_init)


# ── M150 ────────────────────────────────────────────────────────────────

def test_m150_failed_configure_refuses_set_instead_of_plaintext_noop(
    sandbox, monkeypatch, capsys
):
    _break_master_password_backend(monkeypatch)
    be = ss.get_backend()
    assert "backend 'master_password' unavailable" in capsys.readouterr().err

    try:
        be.set(_NEW, _VALUE)
    except Exception as error:
        assert "secrets backend unavailable" in str(error), error
    else:
        pytest.fail(
            f"M150: configure_backend failed but get_backend() returned a "
            f"{getattr(be, 'name', '?')!r} backend whose set() returned "
            f"silently; the secret was discarded")

    with pytest.raises(ss.SecretsBackendUnavailableError,
                       match="secrets backend unavailable"):
        be.get(_NEW)
    with pytest.raises(ss.SecretsBackendUnavailableError):
        be.delete(_NEW)
    with pytest.raises(ss.SecretsBackendUnavailableError):
        be.list_keys()
    assert be.name != "plaintext"
    assert be.is_unlocked() is False
    assert ss._backend is None, "a refusing backend must not be cached"
    assert ss.resolve_password_state(
        ss.make_password_reference("a")) == (None, "unknown")


def test_m150_failed_configure_is_retried_on_the_next_call(
    sandbox, monkeypatch
):
    _break_master_password_backend(monkeypatch)
    assert ss.get_backend().name == "unavailable"
    monkeypatch.setattr(ss, "_detect_default_backend_name", lambda: "plaintext")
    assert ss.get_backend().name == "plaintext"
    assert ss._backend is ss.get_backend()


def test_m150_negative_control_healthy_backend_round_trips(sandbox):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    vault, _meta = sandbox
    assert ss.configure_backend("master_password") is True
    be = ss.get_backend()
    assert be.name == "master_password"
    assert be is ss.get_backend()
    be._data["iterations"] = 1_000
    assert be.unlock(_MASTER) is True
    be.set(_NEW, _VALUE)
    assert be.get(_NEW) == _VALUE
    assert vault.is_file()


# ── M148 ────────────────────────────────────────────────────────────────

_CORRUPT = {
    "truncated-json": '{"keys": ["bulkdl-site-a", "bulkdl-site-b", "bulkdl-site-c"',
    "not-a-dict": json.dumps(_KEYS),
    "keys-not-a-list": json.dumps({"keys": "bulkdl-site-a,b,c"}),
}


@pytest.mark.parametrize("label", sorted(_CORRUPT))
def test_m148_corrupt_meta_set_refuses_and_meta_is_byte_identical(
    sandbox, monkeypatch, label
):
    _vault, meta = sandbox
    meta.write_text(_CORRUPT[label], encoding="utf-8")
    before = meta.read_bytes()
    backend, keyring = _windows_backend(monkeypatch)

    try:
        backend.set(_NEW, _VALUE)
    except ss.SecretsIntegrityError as error:
        assert "secrets_meta.json" in str(error), error
    else:
        pytest.fail(
            f"M148 [{label}]: set() over a corrupt index returned silently; "
            f"meta is now {meta.read_text(encoding='utf-8')!r}")
    assert meta.read_bytes() == before
    assert _NEW not in keyring.values, "keyring written before the refusal"


def test_m148_unreadable_meta_set_refuses_and_meta_is_byte_identical(
    sandbox, monkeypatch
):
    _vault, meta = sandbox
    meta.write_text(json.dumps({"keys": _KEYS}), encoding="utf-8")
    before = meta.read_bytes()
    backend, keyring = _windows_backend(monkeypatch)

    real_read_text = Path.read_text

    def refusing_read_text(path, *args, **kwargs):
        if path == meta:
            raise PermissionError("o1826-c01 synthetic metadata read refusal")
        return real_read_text(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", refusing_read_text)
        with pytest.raises(ss.SecretsIntegrityError, match="secrets_meta.json"):
            backend.set(_NEW, _VALUE)
    assert meta.read_bytes() == before
    assert _NEW not in keyring.values


@pytest.mark.parametrize("label", ["not-a-dict", "truncated-json"])
def test_m148_rotation_stamp_never_rewrites_a_corrupt_meta(sandbox, label):
    _vault, meta = sandbox
    meta.write_text(_CORRUPT[label], encoding="utf-8")
    before = meta.read_bytes()
    ss._stamp_rotation(_NEW)
    ss._unstamp_rotation(_NEW)
    assert meta.read_bytes() == before, (
        f"M148 [{label}]: rotation stamp replaced a corrupt meta with "
        f"{meta.read_text(encoding='utf-8')!r}")


def test_m148_negative_control_valid_meta_keeps_every_key(
    sandbox, monkeypatch
):
    _vault, meta = sandbox
    meta.write_text(json.dumps({"keys": _KEYS, "rotated_at": {"bulkdl-site-a": 1.0}}),
                    encoding="utf-8")
    backend, keyring = _windows_backend(monkeypatch)
    backend.set(_NEW, _VALUE)
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["keys"] == sorted(_KEYS + [_NEW])
    assert data["rotated_at"]["bulkdl-site-a"] == 1.0
    assert _NEW in data["rotated_at"]
    assert keyring.values[_NEW] == _VALUE
    assert ss.rotation_ages()["bulkdl-site-a"]["rotated_at_epoch"] == 1.0


def test_m148_negative_control_missing_meta_is_an_empty_index(
    sandbox, monkeypatch
):
    _vault, meta = sandbox
    assert not meta.exists()
    backend, keyring = _windows_backend(monkeypatch)
    assert backend.list_keys() == []
    backend.set(_NEW, _VALUE)
    assert json.loads(meta.read_text(encoding="utf-8"))["keys"] == [_NEW]
    assert keyring.values[_NEW] == _VALUE


# ── r2 (cx1 REFUTE, O1848): POST /api/vpn/tunnels laundered the refusal ──
#
# vpn_config.store_secrets caught the refusal and kept the submitted value, so
# the create route wrote it into plaintext tunnels.json and answered 200.

_TID = "o1826c01vpn"
_VPN_SECRET = "o1826-c01-SYNTHETIC-vpn-password-not-a-credential"


@pytest.fixture
def vpn_client(sandbox, monkeypatch, tmp_path):
    from flask import Flask

    from bulk_downloader import app_vpn_api, vpn, vpn_config

    tunnels = tmp_path / "tunnels.json"
    monkeypatch.setattr(vpn_config, "_config_path", lambda: tunnels)
    monkeypatch.setattr(vpn_config, "_state", {
        "schema_version": vpn_config.SCHEMA_VERSION,
        "global_settings": {}, "tunnels": []})
    monkeypatch.setattr(vpn_config, "_quarantined", [])
    monkeypatch.setattr(vpn, "_tunnels", {})
    app = Flask(__name__)
    app.register_blueprint(app_vpn_api.vpn_bp)
    return app.test_client(), tunnels, vpn


def _post(client, password=_VPN_SECRET):
    return client.post("/api/vpn/tunnels", json={
        "tunnel_id": _TID, "name": "o1826c01 isolated", "provider": "generic",
        "backend": "openvpn", "config": {"password": password}})


def _windows_store(monkeypatch):
    backend, keyring = _windows_backend(monkeypatch)
    monkeypatch.setattr(ss, "_backend", backend)
    return keyring


@pytest.mark.parametrize("refusal", ["unavailable", "corrupt-meta"])
def test_r2_vpn_create_refused_store_writes_nothing(
    vpn_client, sandbox, monkeypatch, capsys, refusal
):
    client, tunnels, vpn = vpn_client
    _vault, meta = sandbox
    keyring = None
    if refusal == "unavailable":
        _break_master_password_backend(monkeypatch)
        with pytest.raises(ss.SecretsBackendUnavailableError):
            ss.get_backend().set("o1826c01-probe", "o1826c01-probe")
    else:
        meta.write_text(_CORRUPT["truncated-json"], encoding="utf-8")
        keyring = _windows_store(monkeypatch)
    meta_before = meta.read_bytes() if meta.exists() else None

    response = _post(client)

    raw = tunnels.read_text(encoding="utf-8") if tunnels.exists() else ""
    body = response.get_json()
    assert _VPN_SECRET not in raw, (
        f"O1848 [{refusal}]: refused secret written in plaintext; "
        f"HTTP {response.status_code} {body}")
    assert response.status_code == 503 and body["ok"] is False, (
        f"O1848 [{refusal}]: refusal reported as HTTP "
        f"{response.status_code} {body}")
    assert vpn.get_tunnel(_TID) is None, "registration not rolled back"
    assert _VPN_SECRET not in capsys.readouterr().err
    assert (meta.read_bytes() if meta.exists() else None) == meta_before
    if keyring is not None:
        assert keyring.values == {}


def test_r2_vpn_create_locked_vault_refuses_and_says_so(vpn_client, capsys):
    """PM ruling O1876 (option b): a configured-but-locked master-password
    vault raised "backend is locked" from set(); store_secrets kept the value
    and the route wrote it to plaintext tunnels.json with HTTP 200."""
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    client, tunnels, vpn = vpn_client
    assert ss.configure_backend("master_password") is True
    assert ss.get_backend().is_unlocked() is False

    response = _post(client)

    raw = tunnels.read_text(encoding="utf-8") if tunnels.exists() else ""
    body = response.get_json()
    assert _VPN_SECRET not in raw, (
        f"O1876 [locked]: locked-vault secret written in plaintext; "
        f"HTTP {response.status_code} {body}")
    assert response.status_code == 503 and body["ok"] is False, body
    assert body["error"] == "vault locked: unlock to save credentials"
    assert vpn.get_tunnel(_TID) is None, "registration not rolled back"
    assert _VPN_SECRET not in capsys.readouterr().err


def test_r2_vpn_create_negative_control_healthy_backend_stores_a_ref(
    vpn_client
):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    client, tunnels, vpn = vpn_client
    assert ss.configure_backend("master_password") is True
    be = ss.get_backend()
    be._data["iterations"] = 1_000
    assert be.unlock(_MASTER) is True

    response = _post(client)

    raw = tunnels.read_text(encoding="utf-8")
    assert response.status_code == 200, response.get_json()
    assert f"@cred:{_TID}:password" in raw
    assert _VPN_SECRET not in raw
    assert ss.get_backend().get(f"{_TID}:password") == _VPN_SECRET
    assert vpn.get_tunnel(_TID) is not None


def test_r2_vpn_update_corrupt_meta_writes_no_plaintext(
    vpn_client, sandbox, monkeypatch
):
    client, tunnels, _vpn = vpn_client
    _vault, meta = sandbox
    keyring = _windows_store(monkeypatch)
    assert _post(client, "o1826-c01-SYNTHETIC-first").status_code == 200
    meta.write_text(_CORRUPT["truncated-json"], encoding="utf-8")
    saved_before = tunnels.read_bytes()

    response = client.put(f"/api/vpn/tunnels/{_TID}",
                          json={"config": {"password": _VPN_SECRET}})

    assert _VPN_SECRET not in tunnels.read_text(encoding="utf-8")
    assert tunnels.read_bytes() == saved_before
    assert response.status_code >= 500, response.get_json()
    assert response.get_json()["ok"] is False
    assert keyring.values[f"{_TID}:password"] == "o1826-c01-SYNTHETIC-first"


# ── r3 (cx9 REFUTE): an occupied but unreadable path read as absent ─────
#
# read_text and exists() follow links, so a dangling secrets_meta.json symlink
# raised FileNotFoundError and the strict reader returned {}: set() stored the
# credential and the index save replaced the link with a fresh one-key index.

def _dangle(path):
    target = path.parent / "outside-install" / f"missing-{path.name}"
    path.symlink_to(target)
    assert os.path.lexists(path) and not path.exists()
    return os.readlink(path)


def _assert_link_untouched(path, link):
    assert path.is_symlink(), f"{path.name}: the dangling link was replaced"
    assert os.readlink(path) == link
    assert not path.exists(), f"{path.name}: the link target was created"


def test_r3_dangling_meta_link_set_refuses_and_index_untouched(
    sandbox, monkeypatch
):
    _vault, meta = sandbox
    link = _dangle(meta)
    backend, keyring = _windows_backend(monkeypatch)

    try:
        backend.set(_NEW, _VALUE)
    except ss.SecretsUnreadableError as error:
        assert "secrets_meta.json" in str(error), error
    else:
        pytest.fail(
            "O1826 C01 r3 [dangling-meta]: set() treated an occupied "
            "secrets_meta.json symlink as absent and stored the credential")
    assert keyring.values == {}, "credential written before the refusal"
    _assert_link_untouched(meta, link)


def test_r3_dangling_meta_link_every_reader_and_writer_refuses(
    sandbox, monkeypatch
):
    _vault, meta = sandbox
    link = _dangle(meta)
    backend, keyring = _windows_backend(monkeypatch)
    keyring.values[_KEYS[0]] = _VALUE

    with pytest.raises(ss.SecretsUnreadableError, match="secrets_meta.json"):
        backend.list_keys()
    with pytest.raises(ss.SecretsUnreadableError, match="secrets_meta.json"):
        backend.delete(_KEYS[0])
    with pytest.raises(ss.SecretsUnreadableError, match="secrets_meta.json"):
        ss.rotation_ages()
    ss._stamp_rotation(_NEW)
    ss._unstamp_rotation(_KEYS[0])

    assert keyring.values == {_KEYS[0]: _VALUE}, "delete() reached the keyring"
    _assert_link_untouched(meta, link)


def test_r3_dangling_meta_link_vpn_create_answers_503_and_writes_nothing(
    vpn_client, sandbox, monkeypatch
):
    client, tunnels, vpn = vpn_client
    _vault, meta = sandbox
    link = _dangle(meta)
    keyring = _windows_store(monkeypatch)

    response = _post(client)

    body = response.get_json()
    raw = tunnels.read_text(encoding="utf-8") if tunnels.exists() else ""
    assert response.status_code == 503 and body["ok"] is False, (
        f"O1826 C01 r3 [dangling-meta]: HTTP {response.status_code} {body}")
    assert _VPN_SECRET not in raw
    assert keyring.values == {}
    assert vpn.get_tunnel(_TID) is None, "registration not rolled back"
    _assert_link_untouched(meta, link)


def test_r3_dangling_vault_link_set_refuses_and_index_untouched(sandbox):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    vault, meta = sandbox
    meta.write_text(json.dumps({"keys": _KEYS}), encoding="utf-8")
    before = meta.read_bytes()
    link = _dangle(vault)
    assert ss.configure_backend("master_password") is True

    with pytest.raises(ss.SecretsUnreadableError):
        ss.get_backend().set(_NEW, _VALUE)
    assert meta.read_bytes() == before
    _assert_link_untouched(vault, link)


def test_r3_negative_control_absent_meta_still_first_run(
    sandbox, monkeypatch
):
    _vault, meta = sandbox
    assert not os.path.lexists(meta)
    backend, keyring = _windows_backend(monkeypatch)
    assert ss.rotation_ages() == {}
    backend.set(_NEW, _VALUE)
    assert backend.list_keys() == [_NEW]
    assert backend.delete(_NEW) is True
    assert backend.list_keys() == [] and keyring.values == {}


# ── r4 (cx14 REFUTE F1): an unreadable index parent listed as zero keys ──
#
# _load_index returned [] on any probe error, so with the metadata parent at
# mode 000 list_keys() reported an empty store while set/delete refused.

def test_r4_unreadable_meta_parent_list_keys_refuses(sandbox, monkeypatch):
    _vault, meta = sandbox
    parent = meta.parent / "locked-meta"
    parent.mkdir(mode=0o700)
    locked = parent / meta.name
    locked.write_text(json.dumps({"keys": _KEYS[:2]}), encoding="utf-8")
    before = locked.read_bytes()
    monkeypatch.setattr(ss, "SECRETS_META_FILE", locked)
    backend, keyring = _windows_backend(monkeypatch)

    parent.chmod(0o000)
    try:
        try:
            os.lstat(locked)
        except PermissionError:
            pass
        else:
            pytest.skip("mode 000 does not refuse this uid; needs non-root")
        try:
            listed = backend.list_keys()
        except ss.SecretsUnreadableError as error:
            assert "secrets_meta.json" in str(error), error
        else:
            pytest.fail(
                "O1826 C01 r4 [parent-EACCES]: list_keys() reported "
                f"{listed!r} for an index it could not read")
    finally:
        parent.chmod(0o700)

    assert locked.read_bytes() == before
    assert keyring.values == {}
    assert backend.list_keys() == _KEYS[:2], "restored index must list again"
