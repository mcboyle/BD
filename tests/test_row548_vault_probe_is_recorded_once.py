"""Rows 548, 549, 550, 576 and 577: one probe publishes one vault state.

The row-482 first-use re-probe and the row-537 write-identity guard refuse to
overwrite a vault that this backend never read.  Neither refusal records what
it learned, so every later reader continues to publish the stale in-memory
snapshot as a healthy empty store.  The status and unlock endpoints therefore
give opposite answers over the same bytes.

The two sibling existence checks ask the same question at the wrong boundary:
the Windows metadata probe can raise before its load error boundary, and
``Path.exists`` follows a symlink target instead of measuring whether the vault
path itself is occupied.

Every filesystem object in this module lives below pytest's isolated temporary
root.  HOME, TMPDIR, cwd, the vault path and the metadata path are all replaced
before a backend is constructed; the host's operator vault is never named.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from flask import Flask

from bulk_downloader import app_secrets
from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

_MASTER = "row548-isolated-master-password"
_OTHER_MASTER = "row548-isolated-other-master-password"
_KEY = "bulkdl-site-row548"
_VALUE = "row548-isolated-secret-value"


@pytest.fixture
def vault_sandbox(monkeypatch, tmp_path):
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    install = tmp_path / "install"
    for directory in (home, temp, install):
        directory.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.chdir(install)

    vault = install / "secrets.json"
    meta = install / "secrets_meta.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", vault)
    monkeypatch.setattr(ss, "SECRETS_META_FILE", meta)
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    at.reset()

    assert Path.home() == home
    assert Path(tempfile.gettempdir()) == temp
    assert Path.cwd() == install
    assert vault.parent == install and meta.parent == install
    yield tmp_path, vault, meta
    at.reset()


def _build_populated_vault(
    vault: Path,
    meta: Path,
    *,
    password: str,
    key: str,
) -> bytes:
    """Build and verify one real encrypted vault at an isolated path."""
    vault.parent.mkdir(parents=True, exist_ok=True)
    old_vault, old_meta = ss.SECRETS_FILE, ss.SECRETS_META_FILE
    ss.SECRETS_FILE, ss.SECRETS_META_FILE = vault, meta
    try:
        assert not os.path.lexists(vault), vault
        backend = ss.MasterPasswordBackend()
        backend._data["iterations"] = 1_000
        assert backend.unlock(password) is True
        backend.set(key, _VALUE)
        persisted = vault.read_bytes()
    finally:
        ss.SECRETS_FILE, ss.SECRETS_META_FILE = old_vault, old_meta

    document = json.loads(persisted)
    authority = document.get("commitment_authority")
    assert isinstance(authority, str) and authority
    assert set(document["ciphertexts"]) == {authority, key}
    assert document.get("verifier"), "fixture did not persist a verifier"
    assert vault.is_file() and not vault.is_symlink()
    return persisted


def _secrets_client() -> Flask.test_client:
    flask_app = Flask("row548-secrets")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    return flask_app.test_client()


def test_row548_reappeared_vault_probe_is_recorded_for_every_reader(
    vault_sandbox,
):
    root, vault, _meta = vault_sandbox
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = 1_000
    assert not os.path.lexists(vault)
    assert backend._load_error is None
    assert backend.store_state() == "uninitialized"

    donor_vault = root / "donor" / "secrets.json"
    donor_meta = donor_vault.with_name("secrets_meta.json")
    donor_bytes = _build_populated_vault(
        donor_vault,
        donor_meta,
        password=_MASTER,
        key=_KEY,
    )
    vault.write_bytes(donor_bytes)
    assert vault.read_bytes() == donor_bytes
    assert len(json.loads(donor_bytes)["ciphertexts"]) == 2

    with pytest.raises(ss.SecretsUnreadableError, match="appeared"):
        backend.unlock(_OTHER_MASTER)

    assert backend._load_error is not None, (
        "row 548: the reappeared-vault probe refused the write but recorded "
        "no store state"
    )
    assert backend.store_state() == "unreadable"
    assert backend.is_initialized() is True
    with pytest.raises(ss.SecretsUnreadableError):
        backend.list_keys()
    assert vault.read_bytes() == donor_bytes
    assert not vault.with_suffix(".json.tmp").exists()


def test_row549_changed_vault_write_refusal_is_recorded_for_reads(
    vault_sandbox,
):
    root, vault, meta = vault_sandbox
    original_bytes = _build_populated_vault(
        vault,
        meta,
        password=_MASTER,
        key=_KEY,
    )
    backend = ss.MasterPasswordBackend()
    assert backend.unlock(_MASTER) is True
    loaded_identity = backend._loaded_identity
    assert loaded_identity not in (None, (-1, -1, -1))
    assert backend.list_keys() == [_KEY]
    assert backend.get(_KEY) == _VALUE
    assert backend.is_unlocked() is True

    replacement = root / "replacement" / "secrets.json"
    replacement_meta = replacement.with_name("secrets_meta.json")
    replacement_bytes = _build_populated_vault(
        replacement,
        replacement_meta,
        password=_OTHER_MASTER,
        key="bulkdl-site-row549-foreign",
    )
    replacement.replace(vault)
    current_identity = backend._vault_identity()
    assert current_identity not in (None, (-1, -1, -1))
    assert current_identity != loaded_identity
    assert vault.read_bytes() == replacement_bytes
    assert replacement_bytes != original_bytes

    with pytest.raises(ss.SecretsUnreadableError, match="not the file"):
        backend.set("bulkdl-site-row549-new", "must-not-be-written")

    stale_value = backend.get(_KEY)
    assert stale_value is None, (
        "row 549: the changed-vault refusal left the old derived key live and "
        f"returned a stale secret from the vault snapshot: {stale_value!r}"
    )
    assert backend.is_unlocked() is False
    assert backend._load_error is not None, (
        "row 549: the write guard refused a changed vault but every reader "
        "still trusts the stale snapshot"
    )
    assert backend.store_state() == "unreadable"
    assert backend.is_initialized() is True
    with pytest.raises(ss.SecretsUnreadableError):
        backend.list_keys()

    ss._backend = backend
    ss._backend_pref = "master_password"
    resolved, resolution_state = ss.resolve_password_state(
        f"{ss.CRED_PREFIX}{_KEY}"
    )
    assert resolved is None
    assert resolution_state == "unknown", (
        "row 549: resolve_password_state published an unreadable vault as "
        f"merely locked: {resolution_state!r}"
    )
    assert vault.read_bytes() == replacement_bytes
    assert not vault.with_suffix(".json.tmp").exists()


def test_row550_status_and_unlock_report_the_same_recorded_state(
    vault_sandbox, monkeypatch
):
    root, vault, _meta = vault_sandbox
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = 1_000
    assert backend.store_state() == "uninitialized"
    assert not os.path.lexists(vault)

    donor_vault = root / "route-donor" / "secrets.json"
    donor_bytes = _build_populated_vault(
        donor_vault,
        donor_vault.with_name("secrets_meta.json"),
        password=_MASTER,
        key=_KEY,
    )
    vault.write_bytes(donor_bytes)
    assert vault.read_bytes() == donor_bytes
    assert len(json.loads(donor_bytes)["ciphertexts"]) == 2

    ss._backend = backend
    ss._backend_pref = "master_password"
    monkeypatch.setattr(
        app_secrets,
        "_app_s_cfg",
        lambda: {"row548": {"password": f"@cred:{_KEY}"}},
    )
    client = _secrets_client()

    before = client.get("/api/secrets/status")
    before_body = before.get_json()
    assert before.status_code == 200, before_body
    assert before_body["is_initialized"] is False
    assert before_body["stored_keys"] == []

    unlock = client.post("/api/secrets/unlock", json={"password": _MASTER})
    unlock_body = unlock.get_json()
    assert unlock.status_code == 409, unlock_body
    assert unlock_body["state"] == "unreadable"
    assert unlock_body["is_initialized"] is True

    status = client.get("/api/secrets/status")
    status_body = status.get_json()
    assert status.status_code == 409, (
        "row 550: /api/secrets/unlock reported an unreadable initialized "
        f"vault but /api/secrets/status answered {status.status_code}: "
        f"{status_body}"
    )
    assert status_body["state"] == unlock_body["state"] == "unreadable"
    assert status_body["is_initialized"] is unlock_body["is_initialized"]
    assert status_body["stored_keys"] is None
    assert vault.read_bytes() == donor_bytes


def test_row576_windows_index_probe_stays_inside_its_load_boundary(
    vault_sandbox, monkeypatch
):
    _root, _vault, meta = vault_sandbox
    backend = object.__new__(ss.WindowsCredentialBackend)
    assert meta.parent.is_dir()
    assert not meta.exists()

    real_exists = Path.exists
    probe_calls = 0

    def refusing_exists(path: Path) -> bool:
        nonlocal probe_calls
        if path == meta:
            probe_calls += 1
            raise PermissionError("row576 synthetic metadata probe refusal")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", refusing_exists)
    try:
        loaded = backend._load_index()
    except OSError as error:
        pytest.fail(
            "row 576: metadata existence probe escaped its load boundary "
            f"after {probe_calls} call(s): {type(error).__name__}: {error}"
        )

    assert probe_calls == 1, (
        f"metadata existence branch fired {probe_calls} times, expected 1"
    )
    assert loaded == []


def test_windows_index_still_reads_a_valid_metadata_file(vault_sandbox):
    """NEGATIVE CONTROL: the error boundary must not erase valid metadata."""
    _root, _vault, meta = vault_sandbox
    expected = [_KEY, "bulkdl-site-row576-second"]
    meta.write_text(json.dumps({"keys": expected}), encoding="utf-8")
    assert meta.is_file() and json.loads(meta.read_text())["keys"] == expected

    backend = object.__new__(ss.WindowsCredentialBackend)
    assert backend._load_index() == expected


def test_row577_dangling_symlink_occupies_the_vault_path(vault_sandbox):
    root, vault, _meta = vault_sandbox
    missing_target = root / "outside-install" / "missing-vault.json"
    vault.symlink_to(missing_target)
    link_text = os.readlink(vault)
    assert vault.is_symlink()
    assert os.path.lexists(vault)
    assert vault.exists() is False
    assert missing_target.exists() is False

    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = 1_000
    refusal_count = 0
    try:
        backend.unlock(_MASTER)
    except ss.SecretsUnreadableError:
        refusal_count += 1

    assert refusal_count == 1, (
        "row 577: Path.exists treated a dangling symlink as an empty vault "
        "slot and first-use initialization accepted it"
    )
    assert backend._load_error is not None
    assert backend.store_state() == "unreadable"
    assert backend.is_initialized() is True
    assert vault.is_symlink() and os.readlink(vault) == link_text
    assert missing_target.exists() is False
    assert not vault.with_suffix(".json.tmp").exists()


def test_a_genuinely_absent_vault_still_initializes_exactly_once(
    vault_sandbox,
):
    """NEGATIVE CONTROL: the occupancy guard is not a blanket refusal."""
    _root, vault, _meta = vault_sandbox
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = 1_000
    assert not os.path.lexists(vault)
    assert backend._load_error is None

    unlock_count = int(backend.unlock(_MASTER) is True)
    assert unlock_count == 1
    assert vault.is_file() and not vault.is_symlink()
    document = json.loads(vault.read_text(encoding="utf-8"))
    authority = document.get("commitment_authority")
    assert isinstance(authority, str) and authority
    assert set(document["ciphertexts"]) == {authority}
    assert document.get("verifier")

    backend.lock()
    reopened = ss.MasterPasswordBackend()
    assert reopened.store_state() == "locked"
    ss._backend = reopened
    ss._backend_pref = "master_password"
    resolved, resolution_state = ss.resolve_password_state(
        f"{ss.CRED_PREFIX}{_KEY}"
    )
    assert resolved is None
    assert resolution_state == "locked", (
        "the unreadable-state check widened to hide an ordinary locked "
        f"vault: {resolution_state!r}"
    )
    assert reopened.unlock(_MASTER) is True
    assert reopened.list_keys() == []
