"""Rows 551, 552, 554 and 555: a secrets mutation reports its REAL outcome.

Four surfaces of one contract -- a delete answers for the store it actually
touched, and a refusal names the condition it actually measured:

* 551  ``get_backend()`` substitutes a ``PlaintextBackend`` whenever the
  encrypted backend could not be constructed, and ``PlaintextBackend.delete``
  returns a flat ``False``.  The delete route publishes that as
  ``ok:true, removed:false`` and then persists the site's ``@cred:`` pointer
  away, orphaning a ciphertext the substituted backend never read.  The
  sibling import route already refuses exactly this substitution.
* 552  ``_save_sites_config()`` returns a bool and swallows its own
  exceptions.  The route discards it and answers ``config_cleaned:true`` over
  a write that never landed.
* 554  ``delete()`` raises three exceptions.  The route arms two; the third,
  ``SecretsPersistError``, escapes as an opaque 500 -- over a delete that
  rolled back cleanly and left the vault byte-identical.
* 555  ``_refuse_if_unreadable_locked`` renders EVERY unavailable measurement
  as "the credential vault file exists but is unreadable ... Repair or
  restore the file".  When existence itself is what could not be measured
  -- an unsearchable or non-directory parent -- that asserts a precondition
  the process never observed and prescribes the remedy for the opposite
  condition.

Every filesystem object in this module lives below pytest's isolated
temporary root.  HOME, TMPDIR, cwd, the vault path, the metadata path and
sites_config.json are all replaced before a backend is constructed; the
host's operator vault is never named and never opened.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest
from flask import Flask

from bulk_downloader import app_secrets
from bulk_downloader import app_state
from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

_MASTER = "row551-isolated-master-password"
_SITE = "row551site"
_OTHER_KEY = "bulkdl-site-row551other"
_VALUE = "row551-isolated-secret-value"
_OTHER_VALUE = "row551-isolated-other-secret-value"

# The exact operator vocabulary row 432 shipped for a vault whose EXISTENCE
# was observed.  Row 555 does not weaken it; it stops it being reused over a
# path whose existence was never measured.
_EXISTS_CLAIM = "the credential vault file exists"
_EXISTS_REMEDY = "Repair or restore the file"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def vault_sandbox(monkeypatch, tmp_path):
    """Isolated HOME/TMPDIR/cwd plus a private vault, meta and sites file."""
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    install = tmp_path / "install"
    for directory in (home, temp, install):
        directory.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setenv("BD_HOME", str(install))
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.chdir(install)

    vault = install / "secrets.json"
    meta = install / "secrets_meta.json"
    sites = install / "sites_config.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", vault)
    monkeypatch.setattr(ss, "SECRETS_META_FILE", meta)
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)

    import bulk_downloader.app as bd_app
    monkeypatch.setattr(bd_app, "SITES_FILE", sites)

    assert Path.home() == home
    assert Path(tempfile.gettempdir()) == temp
    assert Path.cwd() == install
    assert vault.parent == install and meta.parent == install
    assert sites.parent == install
    yield tmp_path, vault, meta, sites


@pytest.fixture
def isolated_s_cfg(monkeypatch):
    """A private site table standing in for the live s_cfg, by reference.

    ``app.s_cfg`` IS ``app_state.s_cfg`` (imported by reference, mutated in
    place), so both the route's ``_app_s_cfg()`` accessor and the real
    ``_save_sites_config`` writer must see the SAME object for the seam to be
    real.  Clearing and restoring the live dict keeps that identity.
    """
    import bulk_downloader.app as bd_app
    live = app_state.s_cfg
    assert bd_app.s_cfg is live, (
        "the writer and the route no longer share one site table; this "
        "fixture would test two different dicts"
    )
    snapshot = dict(live)
    live.clear()
    yield live
    live.clear()
    live.update(snapshot)


def _build_vault(
    vault: Path,
    meta: Path,
    keys: dict[str, str],
    *,
    password: str = _MASTER,
) -> bytes:
    """Build one real encrypted vault at an isolated path and verify it."""
    vault.parent.mkdir(parents=True, exist_ok=True)
    old_vault, old_meta = ss.SECRETS_FILE, ss.SECRETS_META_FILE
    ss.SECRETS_FILE, ss.SECRETS_META_FILE = vault, meta
    try:
        assert not os.path.lexists(vault), vault
        backend = ss.MasterPasswordBackend()
        backend._data["iterations"] = 1_000
        assert backend.unlock(password) is True
        for key, value in keys.items():
            backend.set(key, value)
        persisted = vault.read_bytes()
    finally:
        ss.SECRETS_FILE, ss.SECRETS_META_FILE = old_vault, old_meta

    document = json.loads(persisted)
    authority = document.get("commitment_authority")
    assert isinstance(authority, str) and authority
    assert set(document["ciphertexts"]) == {authority} | set(keys)
    assert vault.is_file() and not vault.is_symlink()
    return persisted


def _unlocked_backend(password: str = _MASTER) -> ss.MasterPasswordBackend:
    backend = ss.MasterPasswordBackend()
    assert backend.unlock(password) is True
    return backend


def _secrets_client():
    flask_app = Flask("row551-secrets")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    return flask_app.test_client()


# ── 551: a substituted plaintext backend may not answer for the vault ──────

def test_row551_plaintext_substitution_cannot_report_a_delete_over_a_vault(
    vault_sandbox, isolated_s_cfg
):
    _root, vault, meta, sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    before_digest = _digest(vault)

    # PRECONDITIONS, measured from the files rather than the fixture.
    assert ss._CRYPTO_AVAILABLE is True, (
        "this host cannot exhibit a SUBSTITUTION -- plaintext would be the "
        "honest default here"
    )
    assert os.path.lexists(vault)
    reopened = ss.MasterPasswordBackend()
    assert reopened._load_error is None
    assert sorted(reopened.list_keys()) == sorted([key, _OTHER_KEY])

    # The substitution get_backend() performs when the encrypted backend
    # cannot be built: _backend is a PlaintextBackend and nothing says so.
    ss._backend = ss.PlaintextBackend()
    backend = ss.get_backend()
    assert backend.name == "plaintext"
    assert backend.delete(key) is False, (
        "precondition: the substituted backend answers a flat False for a "
        "credential that provably IS stored"
    )

    reference = ss.make_password_reference(_SITE)
    isolated_s_cfg[_SITE] = {"password": reference}
    assert isolated_s_cfg[_SITE]["password"] == reference

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": _SITE}
    )
    body = response.get_json() or {}

    assert response.status_code == 409, body
    assert body.get("ok") is False, body
    assert body.get("state") == "plaintext_backend", body
    assert body.get("requires_encrypted_backend") is True, body
    assert "plaintext" in body.get("error", "")
    # The pointer and the ciphertext are both still there.
    assert isolated_s_cfg[_SITE]["password"] == reference, (
        "row 551: the route persisted the @cred: pointer away over a backend "
        "that never read the vault holding the credential"
    )
    assert not sites.exists(), "no sites_config write may follow a refusal"
    assert _digest(vault) == before_digest
    assert sorted(ss.MasterPasswordBackend().list_keys()) == sorted(
        [key, _OTHER_KEY]
    )


def test_row551_negative_control_a_plaintext_host_with_no_vault_still_answers(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: the guard is not a blanket refusal of plaintext.

    With no vault behind it, ``removed:false`` is the honest answer and the
    route must keep giving it -- otherwise a genuinely plaintext host can no
    longer clear anything.
    """
    _root, vault, _meta, sites = vault_sandbox
    assert not os.path.lexists(vault)

    ss._backend = ss.PlaintextBackend()
    assert ss.get_backend().name == "plaintext"

    response = _secrets_client().post(
        "/api/secrets/delete", json={"key": "bulkdl-site-row551-absent"}
    )
    body = response.get_json() or {}

    assert response.status_code == 200, body
    assert body.get("ok") is True, body
    assert body.get("removed") is False, body
    assert body.get("config_cleaned") is False, body
    assert not sites.exists()


def test_row551_negative_control_the_encrypted_delete_path_is_untouched(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: a real master-password delete still succeeds."""
    _root, vault, meta, sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})

    backend = _unlocked_backend()
    ss._backend = backend
    assert ss.get_backend().name == "master_password"
    reference = ss.make_password_reference(_SITE)
    isolated_s_cfg[_SITE] = {"password": reference}

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": _SITE}
    )
    body = response.get_json() or {}

    assert response.status_code == 200, body
    assert body.get("ok") is True, body
    assert body.get("removed") is True, body
    assert body.get("config_cleaned") is True, body
    assert isolated_s_cfg[_SITE]["password"] == ""
    assert ss.MasterPasswordBackend().list_keys() == [_OTHER_KEY]
    assert sites.is_file(), "the real writer did not publish sites_config.json"
    assert json.loads(sites.read_text(encoding="utf-8"))[_SITE][
        "password"
    ] == ""


# ── 552: a discarded save bool ─────────────────────────────────────────────

def test_row552_a_failed_sites_config_write_is_not_reported_as_cleaned(
    vault_sandbox, isolated_s_cfg
):
    _root, vault, meta, sites = vault_sandbox
    import bulk_downloader.app as bd_app

    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    backend = _unlocked_backend()
    ss._backend = backend

    reference = ss.make_password_reference(_SITE)
    isolated_s_cfg[_SITE] = {"password": reference}
    sites.write_text(
        json.dumps({_SITE: {"password": reference}}, indent=2),
        encoding="utf-8",
    )
    on_disk_before = sites.read_bytes()

    # The row's own shape: a stale unwritable .tmp at the fixed staging path.
    # A directory, not a chmod, so the trigger survives a root test runner.
    stale_tmp = sites.with_suffix(".json.tmp")
    stale_tmp.mkdir()
    assert stale_tmp.is_dir()

    # PRECONDITION: the writer really does fail, and really does swallow it.
    assert bd_app._save_sites_config() is False, (
        "precondition: the writer was expected to fail over the stale .tmp"
    )
    assert sites.read_bytes() == on_disk_before

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": _SITE}
    )
    body = response.get_json() or {}

    assert response.status_code == 500, body
    assert body.get("ok") is False, body
    assert body.get("state") == "config_write_failed", body
    assert body.get("config_cleaned") is False, (
        "row 552: the route reported a durable cleanup over a write that "
        f"never landed: {body}"
    )
    assert body.get("removed") is True, body
    assert "@cred:" in body.get("error", ""), body
    # The divergence the operator must be told about is real and on disk.
    assert sites.read_bytes() == on_disk_before
    assert json.loads(on_disk_before.decode("utf-8"))[_SITE][
        "password"
    ] == reference
    assert ss.MasterPasswordBackend().list_keys() == [_OTHER_KEY]
    # The route left no in-memory claim that no writer confirmed: process
    # state matches the only store that actually holds the reference.
    assert isolated_s_cfg[_SITE]["password"] == reference, (
        "row 552: the cleared value survived a write that never landed, so "
        "this process asserts a state neither store holds"
    )

    # And because it was unwound, a retry once the obstruction is gone still
    # has something to clean -- it is not silently satisfied by its own
    # earlier half-completion.
    stale_tmp.rmdir()
    retry = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": _SITE}
    )
    retry_body = retry.get_json() or {}
    assert retry.status_code == 200, retry_body
    assert retry_body.get("config_cleaned") is True, retry_body
    assert json.loads(sites.read_text(encoding="utf-8"))[_SITE][
        "password"
    ] == ""


def test_row552_negative_control_a_successful_write_still_reports_cleaned(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: the honest durable cleanup is unchanged."""
    _root, vault, meta, sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    ss._backend = _unlocked_backend()
    isolated_s_cfg[_SITE] = {"password": ss.make_password_reference(_SITE)}
    assert not sites.with_suffix(".json.tmp").exists()

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": _SITE}
    )
    body = response.get_json() or {}

    assert response.status_code == 200, body
    assert body.get("ok") is True, body
    assert body.get("config_cleaned") is True, body
    assert json.loads(sites.read_text(encoding="utf-8"))[_SITE][
        "password"
    ] == ""


def test_row552_negative_control_a_key_only_delete_writes_no_config(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: no site_id means no config write to judge."""
    _root, vault, meta, sites = vault_sandbox
    _build_vault(vault, meta, {_OTHER_KEY: _OTHER_VALUE, "bulkdl-x": "v"})
    ss._backend = _unlocked_backend()
    stale_tmp = sites.with_suffix(".json.tmp")
    stale_tmp.mkdir()

    response = _secrets_client().post(
        "/api/secrets/delete", json={"key": _OTHER_KEY}
    )
    body = response.get_json() or {}

    assert response.status_code == 200, body
    assert body.get("ok") is True, body
    assert body.get("removed") is True, body
    assert body.get("config_cleaned") is False, body
    assert not sites.exists()


# ── 554: the third exception delete() raises ───────────────────────────────

def test_row554_a_persist_failure_is_named_rather_than_an_opaque_500(
    vault_sandbox, isolated_s_cfg
):
    _root, vault, meta, sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    backend = _unlocked_backend()
    ss._backend = backend
    before_digest = _digest(vault)
    reference = ss.make_password_reference(_SITE)
    isolated_s_cfg[_SITE] = {"password": reference}

    stale_tmp = vault.with_suffix(".json.tmp")
    stale_tmp.mkdir()
    assert stale_tmp.is_dir()

    # PRECONDITIONS: the vault write really fails, the backend is unlocked and
    # readable, and the credential really is there to be removed.
    assert backend._save() is False
    assert backend.is_unlocked() is True
    assert backend._load_error is None
    assert sorted(backend.list_keys()) == sorted([key, _OTHER_KEY])

    response = _secrets_client().post(
        "/api/secrets/delete", json={"site_id": _SITE}
    )
    body = response.get_json() or {}

    assert response.status_code == 500, (response.status_code, body)
    assert body.get("ok") is False, body
    assert body.get("state") == "persist_failed", body
    assert body.get("removed") is False, body
    assert body.get("config_cleaned") is False, body
    assert "secrets.json" in body.get("error", ""), body
    # The rollback delete() performed is REAL, not merely claimed.
    assert sorted(backend.list_keys()) == sorted([key, _OTHER_KEY])
    assert _digest(vault) == before_digest
    assert sorted(ss.MasterPasswordBackend().list_keys()) == sorted(
        [key, _OTHER_KEY]
    )
    assert isolated_s_cfg[_SITE]["password"] == reference
    assert not sites.exists()


def test_row554_the_real_app_stops_answering_internal_server_error(
    vault_sandbox, isolated_s_cfg
):
    """The row's literal consequence, measured through app.py's 500 handler.

    The blueprint test client has no error handler, so it can only show the
    unhandled exception.  ``app.errorhandler(500)`` is what an operator
    actually receives, and it renders every unhandled route exception as the
    same opaque ``internal server error``.
    """
    import bulk_downloader.app as bd_app

    _root, vault, meta, _sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    backend = _unlocked_backend()
    ss._backend = backend
    vault.with_suffix(".json.tmp").mkdir()
    assert backend._save() is False

    handler = bd_app.app.error_handler_spec[None][500]
    assert handler, "precondition: app.py registers no 500 handler"

    client = bd_app.app.test_client()
    token = client.get("/api/pair").get_json()["token"]
    csrf = client.post(
        "/api/pair/redeem", json={"token": token}
    ).get_json()["csrf_token"]

    response = client.post(
        "/api/secrets/delete",
        json={"key": key},
        headers={"X-CSRF-Token": csrf},
    )
    body = response.get_json() or {}

    assert body.get("error") != "internal server error", (
        "row 554: a rolled-back persist failure is still indistinguishable "
        f"from a crash: {response.status_code} {body}"
    )
    assert body.get("state") == "persist_failed", body
    assert sorted(backend.list_keys()) == sorted([key, _OTHER_KEY])


def test_row554_negative_control_the_two_older_arms_still_fire(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: the new arm did not swallow the 409 refusals.

    An unreadable vault and a locked last credential are DIFFERENT conditions
    with different remedies; a persist arm that caught either of them would
    convert both into "check disk space".
    """
    _root, vault, meta, _sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})

    # (a) locked vault, last usable credential -> 409 locked, unchanged.
    single = vault.parent / "single"
    single.mkdir()
    _build_vault(single / "secrets.json", single / "secrets_meta.json",
                 {key: _VALUE})
    (single / "secrets.json").replace(vault)
    ss._backend = None
    assert ss.configure_backend("master_password") is True
    assert ss.get_backend().is_unlocked() is False
    locked = _secrets_client().post(
        "/api/secrets/delete", json={"key": key}
    )
    locked_body = locked.get_json() or {}
    assert locked.status_code == 409, locked_body
    assert locked_body.get("state") == "locked", locked_body

    # (b) unreadable vault -> 409 unreadable, unchanged.
    vault.write_text("{ not json", encoding="utf-8")
    ss._backend = None
    assert ss.configure_backend("master_password") is True
    unreadable = _secrets_client().post(
        "/api/secrets/delete", json={"key": key}
    )
    unreadable_body = unreadable.get_json() or {}
    assert unreadable.status_code == 409, unreadable_body
    assert unreadable_body.get("state") == "unreadable", unreadable_body


# ── 555: a refusal may not assert an existence it never measured ───────────

def test_row555_an_unmeasurable_path_refusal_does_not_assert_a_file(
    vault_sandbox, isolated_s_cfg
):
    _root, _vault, _meta, _sites = vault_sandbox
    blocker = Path.cwd() / "not-a-directory"
    blocker.write_text("this is a regular file", encoding="utf-8")
    unmeasurable = blocker / "secrets.json"

    ss.SECRETS_FILE = unmeasurable
    ss.SECRETS_META_FILE = blocker.parent / "secrets_meta.json"

    # PRECONDITIONS: existence is UNMEASURABLE, and the path provably holds
    # no vault -- so any claim that a vault file is there is false.
    with pytest.raises(OSError) as probe:
        ss._path_entry_exists(unmeasurable)
    assert not isinstance(probe.value, FileNotFoundError)
    assert os.path.lexists(unmeasurable) is False

    backend = ss.MasterPasswordBackend()
    assert backend._load_error is not None
    assert backend.store_state() == "unreadable"

    with pytest.raises(ss.SecretsUnreadableError) as raised:
        backend.delete(ss.site_password_key(_SITE))
    message = str(raised.value)
    assert _EXISTS_CLAIM not in message, (
        "row 555: the refusal asserts a vault file exists at a path whose "
        f"existence is exactly what could not be measured: {message}"
    )
    assert _EXISTS_REMEDY not in message, (
        "row 555: the refusal prescribes repairing a file that may not be "
        f"there, instead of the unsearchable parent: {message}"
    )
    assert "could not be measured" in message, message
    assert "RESTART" in message, message
    assert backend._load_error_kind == "unmeasured", (
        "row 555: the backend latched an unavailable measurement without "
        "recording WHICH one, so every later refusal must guess"
    )

    # The same wrong claim reaches the operator through the route.
    ss._backend = backend
    response = _secrets_client().post(
        "/api/secrets/delete", json={"key": ss.site_password_key(_SITE)}
    )
    body = response.get_json() or {}
    assert response.status_code == 409, body
    assert body.get("state") == "unreadable", body
    assert _EXISTS_CLAIM not in body.get("error", ""), body
    assert "restart" in json.dumps(body).lower(), body


def test_row555_a_write_time_probe_failure_keeps_its_kind_for_later_readers(
    vault_sandbox, isolated_s_cfg
):
    """The latch is re-rendered by EVERY later reader, so it must be typed.

    ``_record_probe_failure_locked`` stores one string and
    ``_refuse_if_unreadable_locked`` re-wraps it for the rest of the
    process's life.  Fixing only the first raise would leave every
    subsequent inventory and delete asserting the same false existence.
    """
    _root, _vault, _meta, _sites = vault_sandbox
    nest = Path.cwd() / "nest"
    nest.mkdir()
    vault = nest / "secrets.json"
    meta = nest / "secrets_meta.json"
    ss.SECRETS_FILE = vault
    ss.SECRETS_META_FILE = meta
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})

    backend = _unlocked_backend()
    assert backend._loaded_identity not in (None, (-1, -1, -1))
    assert sorted(backend.list_keys()) == sorted([key, _OTHER_KEY])

    # Replace the containing directory with a regular file: stat() now raises
    # NotADirectoryError, which _vault_identity classifies as "I could not
    # look" rather than "there is nothing there".
    vault.unlink()
    meta.unlink()
    nest.rmdir()
    nest.write_text("the parent is no longer a directory", encoding="utf-8")
    assert backend._vault_identity() == (-1, -1, -1)

    with pytest.raises(ss.SecretsUnreadableError) as raised:
        backend.delete(key)
    first = str(raised.value)
    assert _EXISTS_CLAIM not in first, first
    assert _EXISTS_REMEDY not in first, first
    assert backend._load_error_kind == "unmeasured"

    # SECOND SURFACE: a later reader re-renders the same latch.
    with pytest.raises(ss.SecretsUnreadableError) as later:
        backend.list_keys()
    second = str(later.value)
    assert _EXISTS_CLAIM not in second, (
        "row 555: the latched refusal still asserts an unmeasured existence "
        f"to every later reader: {second}"
    )
    assert _EXISTS_REMEDY not in second, second
    assert "could not be measured" in second, second


def test_row555_negative_control_a_present_unreadable_vault_keeps_row432_text(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: the fix separates two kinds, it does not erase one.

    When the directory entry WAS observed and only its contents could not be
    read, row 432's vocabulary is correct and must survive byte-for-byte.
    """
    _root, vault, meta, _sites = vault_sandbox
    _build_vault(vault, meta, {ss.site_password_key(_SITE): _VALUE})
    vault.write_text("{ not json", encoding="utf-8")
    assert os.path.lexists(vault) is True

    backend = ss.MasterPasswordBackend()
    assert backend._load_error is not None

    with pytest.raises(ss.SecretsUnreadableError) as raised:
        backend.delete(ss.site_password_key(_SITE))
    message = str(raised.value)
    assert _EXISTS_CLAIM in message, message
    assert _EXISTS_REMEDY in message, message
    assert "NOT reinitialized" in message, message
    assert "could not be measured" not in message, message
    assert backend._load_error_kind == "unreadable"


def test_row555_negative_control_a_changed_vault_still_names_the_replacement(
    vault_sandbox, isolated_s_cfg
):
    """NEGATIVE CONTROL: the row-537 refusal is a MEASURED existence.

    A vault that was replaced is present and observed, so it keeps the
    existence-asserting vocabulary and its own distinctive diagnostic.
    """
    _root, vault, meta, _sites = vault_sandbox
    key = ss.site_password_key(_SITE)
    _build_vault(vault, meta, {key: _VALUE, _OTHER_KEY: _OTHER_VALUE})
    backend = _unlocked_backend()
    assert backend._loaded_identity not in (None, (-1, -1, -1))

    donor = vault.parent / "donor"
    donor.mkdir()
    _build_vault(donor / "secrets.json", donor / "secrets_meta.json",
                 {key: _VALUE}, password="row551-isolated-other-password")
    (donor / "secrets.json").replace(vault)
    assert backend._vault_identity() not in (None, (-1, -1, -1))

    with pytest.raises(ss.SecretsUnreadableError, match="not the file") as e:
        backend.delete(key)
    assert "could not be measured" not in str(e.value)
    assert backend._load_error_kind == "unreadable"
