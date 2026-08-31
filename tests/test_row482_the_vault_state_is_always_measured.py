"""Rows 482, 487, 502 and 510: every path into the vault yields one of the four declared states.

``store_state`` documents four mutually exclusive states. Four measured paths
reached none of them, and each landed somewhere worse than an error:

  487  ``SECRETS_FILE.exists()`` sits OUTSIDE the try. CPython's pathlib
       swallows only ENOENT/ENOTDIR/EBADF/ELOOP, so EACCES from a chmod-000
       containing DIRECTORY re-raises out of ``__init__`` before any
       classification can exist. ``configure_backend`` swallows it, ``_backend``
       stays None, and ``get_backend`` hands back a PLAINTEXT backend -- an
       unreadable encrypted vault presenting as a confident empty plaintext
       store.

  510  ``json.loads`` result is bound to ``self._data`` with no isinstance
       check. A vault holding valid JSON that is not an object parses, leaves
       ``_load_error`` None -- the sentinel documented as "the store was
       read" -- and then ``store_state`` raises TypeError or AttributeError out
       of a function whose contract is to return one of four strings.

  482  A vault that does not exist at construction caches a snapshot saying so.
       Nothing re-reads the path. BD itself makes the file appear inside that
       window with no restart: POST /api/backup/restore writes secrets.json
       into the working directory. The next unlock takes the first-use branch
       against the stale snapshot and ``tmp.replace(SECRETS_FILE)`` overwrites
       whatever now occupies the path, under ANY password.

  502  ``SecretsUnreadableError`` subclasses ``SecretsIntegrityError``, not
       ``SecretsUnlockRequiredError``, and the delete route catches only the
       latter -- so the guard's entire operator remedy escapes the route as a
       500 with no remedy in it.

Row 432 is NOT in this cut: its rename half was closed at v3.66.1363, which
preserves the file in place and sets ``_load_error``, and ``_unlock_locked``
calls ``_refuse_if_unreadable_locked`` at :954. Re-derived here rather than
inherited, and a test below pins that so the closure cannot silently regress.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

_PASSWORD = "row482-synthetic-master-password"


@pytest.fixture
def vault_dir(monkeypatch, tmp_path):
    d = tmp_path / "install"
    d.mkdir()
    monkeypatch.setattr(ss, "SECRETS_FILE", d / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", d / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    return d


def _build_real_vault(vault_dir):
    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.name == "master_password"
    backend._data["iterations"] = 1_000
    assert backend.unlock(_PASSWORD) is True
    backend.set("bulkdl-site-row482", "row482-synthetic-value")
    assert ss.SECRETS_FILE.is_file()
    return backend


# ── 487: an unreadable vault must never present as a plaintext store ────────

def test_an_unreadable_vault_directory_never_yields_a_plaintext_backend(vault_dir):
    """RED on the defective parent, and the worst of the four.

    The precondition is asserted rather than assumed: the probe really must
    raise, or this measures nothing.
    """
    _build_real_vault(vault_dir)
    ss._backend = None

    os.chmod(vault_dir, 0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions; EACCES is unreachable")
        with pytest.raises(PermissionError):
            ss.SECRETS_FILE.exists()

        ss.configure_backend("master_password")
        backend = ss.get_backend()
        assert backend.name != "plaintext", (
            "an unreadable ENCRYPTED vault was replaced by a confident empty "
            "PLAINTEXT store; every subsequent set() would write secrets in "
            "clear next to the vault it could not read")
    finally:
        os.chmod(vault_dir, stat.S_IRWXU)


def test_an_unreadable_vault_classifies_rather_than_escaping(vault_dir):
    _build_real_vault(vault_dir)
    ss._backend = None
    os.chmod(vault_dir, 0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        backend = ss.MasterPasswordBackend()
        assert backend._load_error, (
            "construction survived an unreadable vault with _load_error None, "
            "so the instance caches a snapshot asserting the store was read")
        assert backend.store_state() == "unreadable", backend.store_state()
        with pytest.raises(ss.SecretsIntegrityError):
            backend.unlock(_PASSWORD)
    finally:
        os.chmod(vault_dir, stat.S_IRWXU)


# ── 510: a JSON root that is not an object ─────────────────────────────────

@pytest.mark.parametrize("root", ["null", "3", "true", "[]", '"a string"'])
def test_a_non_object_json_root_classifies_and_never_raises(vault_dir, root):
    ss.SECRETS_FILE.write_text(root, encoding="utf-8")
    backend = ss.MasterPasswordBackend()
    # store_state's contract is to RETURN one of four strings. A TypeError or
    # AttributeError out of it is not a fifth state, it is an escape.
    state = backend.store_state()
    assert state == "unreadable", state
    assert backend._load_error, (
        f"a JSON root of {root!r} parsed and left _load_error None -- the "
        "sentinel documented as 'the store was read'")
    with pytest.raises(ss.SecretsIntegrityError):
        backend.unlock(_PASSWORD)


def test_a_well_formed_object_root_is_still_read(vault_dir):
    """POSITIVE CONTROL: the isinstance check must not refuse a real vault."""
    _build_real_vault(vault_dir)
    ss._backend = None
    backend = ss.MasterPasswordBackend()
    assert backend._load_error is None, backend._load_error
    assert backend.store_state() == "locked", backend.store_state()
    assert backend.unlock(_PASSWORD) is True


# ── 482: a vault that appears after construction ───────────────────────────

def test_a_vault_that_appears_after_construction_is_not_overwritten(vault_dir):
    """BD itself makes this happen with no restart: POST /api/backup/restore
    writes secrets.json into the working directory."""
    backend = ss.MasterPasswordBackend()
    assert not ss.SECRETS_FILE.exists()
    assert backend._load_error is None

    # A real vault now occupies the path -- restored from a backup, or copied
    # in by an operator.
    donor_dir = vault_dir.parent / "donor"
    donor_dir.mkdir()
    old_file, old_meta = ss.SECRETS_FILE, ss.SECRETS_META_FILE
    ss.SECRETS_FILE = donor_dir / "secrets.json"
    ss.SECRETS_META_FILE = donor_dir / "secrets_meta.json"
    ss._backend = None
    _build_real_vault(donor_dir)
    donor_bytes = ss.SECRETS_FILE.read_bytes()
    ss.SECRETS_FILE, ss.SECRETS_META_FILE = old_file, old_meta
    ss.SECRETS_FILE.write_bytes(donor_bytes)

    assert json.loads(donor_bytes)["ciphertexts"], "the donor vault is empty"

    with pytest.raises(ss.SecretsIntegrityError):
        backend.unlock("any-password-at-all-8")

    assert ss.SECRETS_FILE.read_bytes() == donor_bytes, (
        "the restored vault was overwritten by an unlock against a stale "
        "construction-time snapshot, under a password nobody had to know")


def test_a_genuinely_fresh_vault_still_initializes(vault_dir):
    """POSITIVE CONTROL: first-use must still work when the path really is
    empty, or the fix has traded a data-loss bug for a lockout."""
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = 1_000
    assert not ss.SECRETS_FILE.exists()
    assert backend.unlock(_PASSWORD) is True
    assert ss.SECRETS_FILE.is_file()


# ── 502: the delete route must carry the refusal ───────────────────────────

def test_the_delete_route_carries_the_unreadable_refusal(vault_dir, monkeypatch):
    from flask import Flask
    from bulk_downloader import app_secrets

    _build_real_vault(vault_dir)
    ss.SECRETS_FILE.write_text("{ not json", encoding="utf-8")
    ss._backend = None
    assert ss.configure_backend("master_password") is True

    app = Flask(__name__)
    app.register_blueprint(app_secrets.secrets_bp)
    app.config["BD_CFG"] = {"sites": {}}
    client = app.test_client()

    resp = client.post("/api/secrets/delete", json={"key": "bulkdl-site-row482"})
    assert resp.status_code != 500, (
        "the unreadable-vault refusal escaped the route as an unhandled "
        "exception, so the operator remedy the guard carries -- repair or "
        "restore the file, then RESTART -- never reached them")
    body = resp.get_json() or {}
    assert body.get("ok") is False, body
    assert "restart" in json.dumps(body).lower(), (
        f"the response does not carry the remedy: {body}")


# ── 432: closed at v3.66.1363. Pinned so the closure cannot regress. ───────

def test_row432_stays_closed_an_unreadable_vault_refuses_every_password(vault_dir):
    _build_real_vault(vault_dir)
    before = ss.SECRETS_FILE.read_bytes()
    ss.SECRETS_FILE.write_text("{ torn write", encoding="utf-8")
    torn = ss.SECRETS_FILE.read_bytes()
    ss._backend = None

    backend = ss.MasterPasswordBackend()
    assert backend._load_error
    with pytest.raises(ss.SecretsIntegrityError):
        backend.unlock("any-password-at-all-8")
    assert ss.SECRETS_FILE.read_bytes() == torn, (
        "the unreadable file was moved or reinitialized; v3.66.1363's "
        "preserve-in-place guarantee regressed")
    assert before != torn
