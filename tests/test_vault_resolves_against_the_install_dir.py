"""VAULT-RESOLVES-AGAINST-CWD -- the credential vault followed the process cwd.

THE DEFECT, measured by the 2026-09-03 download campaign on test6.
``bulk_downloader/secrets_store.py`` resolved the vault as

    default = (Path("secrets.json"), Path("secrets_meta.json"))

Both are RELATIVE, so every read and every write resolved them against
whatever directory the importing process happened to be standing in. A
campaign harness that imported the package from ``~/campaign`` therefore did
not open the operator's vault at all: the first unlock found no file there,
committed a fresh password to a NEW EMPTY vault, and reported success. The
lane measured a 475-byte vault at ``~/campaign/secrets.json`` sitting beside
the operator's real 4773-byte one.

That is the exact outcome the ``BD_CAPTURE_VAULT`` two-key design exists to
prevent -- ``_resolve_vault_paths``'s own docstring says a single misplaced
variable must not be able to "hand back a newly initialized empty credential
store instead of the operator's real one" -- and it was reachable with NO
environment variable at all. A silent redirect of a credential store is worse
than a loud failure: every downstream reader sees a healthy, empty, wrong
vault.

THE AUTHORITY. ``sites_config.json`` already answers this question, in
``app.py::_resolve_sites_file``: absolute under ``BD_INSTALL_DIR`` when that is
set, and the historical relative path when it is not. The vault is documented
as living "next to sites_config.json so backups capture them together", so it
takes the same ladder rather than a second invented one.

WHY NOT ``constants.INSTALL_DIR``. It is frozen at import of constants.py and
falls back to ``Path.cwd()``, so whether it holds the install root or a tmp dir
depends on whether that import beat the suite's chdir. ``_resolve_sites_file``
rejected it for exactly this reason and keys off the env var directly; so does
this.

WHAT IS DELIBERATELY UNCHANGED, and it is a contract not an oversight. With
``BD_INSTALL_DIR`` unset the relative default stands, byte-identical:
``tests/test_capture_vault_is_isolated.py`` pins it, nineteen test files chdir
into a tmpdir and rely on it for isolation, and the live systemd unit sets
``WorkingDirectory`` rather than ``BD_INSTALL_DIR``. Closing THAT residual is
an operator decision about the unit and the harness, not a test change.

IT MUST REMAIN A MODULE ATTRIBUTE. Seven test files monkeypatch
``ss.SECRETS_FILE``; a bare call-time getter would leave every one of those
patches silently inert. Resolution is therefore call-time and REPUBLISHED to
the attribute, using app.py's identity idiom: an explicitly assigned value is
never overwritten.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

# Zero-entropy fixture value. It is not a credential: it unlocks nothing but
# the throwaway vault each test creates inside its own tmp_path and is written
# here in the clear on purpose (CLAUDE.md A4, security fixtures).
FIXTURE_UNLOCK_VALUE = "bd-fixture-unlock-value"

DEFAULT_NAME = "secrets.json"
DEFAULT_META_NAME = "secrets_meta.json"

_VAULT_ENV = ("BD_SECRETS_FILE", "BD_CAPTURE_VAULT", "BD_INSTALL_DIR")


pytestmark = pytest.mark.skipif(
    not ss._CRYPTO_AVAILABLE,
    reason="the master-password backend is the subject; without cryptography "
           "there is no vault to resolve and this gate would pass vacuously")


def _clear_env(monkeypatch):
    """Remove every variable that can decide the answer.

    CLAUDE.md A7: an environment-changing test REMOVES inherited values rather
    than merely declining to set them, so an ambient BD_INSTALL_DIR on the host
    cannot decide any verdict below.
    """
    for name in _VAULT_ENV:
        monkeypatch.delenv(name, raising=False)


def _reset_backend():
    """Force re-detection; the module caches one backend instance globally."""
    ss._backend = None
    ss._backend_pref = None
    ss._audited_cache = None


@pytest.fixture
def two_dirs(tmp_path, monkeypatch):
    """An install dir and a separate cwd, with the module state reset."""
    install = tmp_path / "install"
    install.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _clear_env(monkeypatch)
    _reset_backend()
    # The tests below make configure_backend() REPUBLISH ss.SECRETS_FILE /
    # SECRETS_META_FILE into this test's tmp_path. Pin the module attributes
    # through monkeypatch so teardown restores the pre-test objects: without
    # this the next test file in the same worker inherits a vault path inside
    # a deleted tmp dir (measured: test_secret_rotation_age failed 4/27 when
    # this file ran first, 27/27 when it ran last).
    monkeypatch.setattr(ss, "SECRETS_FILE", ss.SECRETS_FILE)
    monkeypatch.setattr(ss, "SECRETS_META_FILE", ss.SECRETS_META_FILE)
    # Preconditions, asserted rather than assumed: two distinct real
    # directories, both empty, and neither is the other.
    assert install.is_dir() and elsewhere.is_dir()
    assert install != elsewhere
    assert list(install.iterdir()) == []
    assert list(elsewhere.iterdir()) == []
    try:
        yield install, elsewhere
    finally:
        _reset_backend()


# ── the defect itself ────────────────────────────────────────────────────────

def test_a_declared_install_dir_owns_the_vault_not_the_process_cwd(
        two_dirs, monkeypatch):
    """The campaign's shape: import from elsewhere, write nothing there.

    This drives the production seam -- configure_backend -> get_backend ->
    unlock -- rather than the resolver alone, because the defect was that the
    resolved path was USED to create a vault, not merely returned.
    """
    install, elsewhere = two_dirs
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    monkeypatch.chdir(elsewhere)
    assert Path.cwd() == elsewhere.resolve(), Path.cwd()

    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    # Precondition: the subject really is the vault backend. A plaintext
    # fallback would leave every assertion below vacuously true.
    assert backend.name == "master_password", backend.name
    assert backend.store_state() == "uninitialized", backend.store_state()

    assert backend.unlock(FIXTURE_UNLOCK_VALUE) is True
    assert backend.is_unlocked() is True

    assert (install / DEFAULT_NAME).is_file(), (
        f"the vault was not created in the declared install dir {install}; "
        f"it holds {sorted(p.name for p in install.iterdir())}"
    )
    assert list(elsewhere.iterdir()) == [], (
        f"a credential vault was created in the process cwd {elsewhere}: "
        f"{sorted(p.name for p in elsewhere.iterdir())}. A relative vault path "
        f"follows the cwd, so a process importing BD from another directory "
        f"silently gets a NEW EMPTY vault instead of the operator's -- "
        f"measured on test6 as a 475-byte vault beside the real 4773-byte one."
    )


def test_the_resolver_points_at_the_declared_install_dir(two_dirs, monkeypatch):
    """The path itself, so a failure above can be localized to the resolver."""
    install, elsewhere = two_dirs
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    monkeypatch.chdir(elsewhere)
    secrets, meta = ss._resolve_vault_paths()
    assert secrets == install.resolve() / DEFAULT_NAME, secrets
    assert meta == install.resolve() / DEFAULT_META_NAME, meta
    assert secrets.is_absolute() and meta.is_absolute()


# ── resolution happens at call time, and the attribute still carries it ──────

def test_resolution_is_call_time_not_import_time(two_dirs, monkeypatch):
    """The module is already imported; the environment changes afterwards.

    The campaign process imported BD once and never re-imported, so a value
    frozen at import can never become right. configure_backend republishes.
    """
    install, elsewhere = two_dirs
    monkeypatch.chdir(elsewhere)
    # Precondition: with nothing declared the resolver yields the historical
    # relative default, so the assertion below measures a real change.
    assert ss._resolve_vault_paths()[0] == Path(DEFAULT_NAME)

    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    assert ss.configure_backend("master_password") is True
    assert ss.SECRETS_FILE == install.resolve() / DEFAULT_NAME, ss.SECRETS_FILE
    assert ss.SECRETS_META_FILE == install.resolve() / DEFAULT_META_NAME


def test_an_explicit_module_patch_survives_the_refresh(two_dirs, monkeypatch):
    """Seven test files monkeypatch ss.SECRETS_FILE. All must keep working."""
    install, elsewhere = two_dirs
    pinned = elsewhere / "pinned-secrets.json"
    monkeypatch.setattr(ss, "SECRETS_FILE", pinned)
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    # configure_backend is the seam that republishes the resolved paths.
    assert ss.configure_backend("master_password") is True
    assert ss.SECRETS_FILE == pinned, (
        f"an explicitly assigned SECRETS_FILE was overwritten by the "
        f"call-time refresh (now {ss.SECRETS_FILE}); every monkeypatch of "
        f"this attribute would be silently inert."
    )


# ── negative control: the historical default is untouched ────────────────────

def test_with_no_install_dir_the_vault_is_where_it_has_always_been(
        two_dirs, monkeypatch):
    """The contract test_capture_vault_is_isolated.py pins, re-asserted here.

    Nineteen test files chdir into a tmpdir and rely on this. If this test
    ever fails, the fix has broken suite isolation, not fixed the defect.
    """
    _install, elsewhere = two_dirs
    monkeypatch.chdir(elsewhere)
    secrets, meta = ss._resolve_vault_paths()
    assert secrets == Path(DEFAULT_NAME), secrets
    assert meta == Path(DEFAULT_META_NAME), meta

    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.name == "master_password", backend.name
    assert backend.unlock(FIXTURE_UNLOCK_VALUE) is True
    assert (elsewhere / DEFAULT_NAME).is_file(), sorted(
        p.name for p in elsewhere.iterdir())


# ── the explicit capture override still wins ─────────────────────────────────

def test_the_capture_vault_override_outranks_the_install_dir(
        two_dirs, monkeypatch, tmp_path):
    """capture.sh sets BOTH BD_INSTALL_DIR and the two-key vault override."""
    install, elsewhere = two_dirs
    target = tmp_path / "capture" / "capture-secrets.json"
    target.parent.mkdir()
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    monkeypatch.setenv("BD_SECRETS_FILE", str(target))
    monkeypatch.setenv("BD_CAPTURE_VAULT", "1")
    monkeypatch.chdir(elsewhere)
    secrets, meta = ss._resolve_vault_paths()
    assert secrets == target, secrets
    assert meta.parent == target.parent, meta


def test_a_stray_path_alone_still_cannot_move_the_vault(two_dirs, monkeypatch,
                                                        tmp_path):
    """BD_SECRETS_FILE without the opt-in stays inert, install dir or not."""
    install, elsewhere = two_dirs
    stray = tmp_path / "stray-secrets.json"
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    monkeypatch.setenv("BD_SECRETS_FILE", str(stray))
    monkeypatch.chdir(elsewhere)
    secrets, _meta = ss._resolve_vault_paths()
    assert secrets == install.resolve() / DEFAULT_NAME, secrets
    assert secrets != stray


# ── an unresolvable state dir refuses; it never falls back to the cwd ────────

def test_an_unresolvable_state_dir_refuses_and_creates_nothing(
        two_dirs, monkeypatch):
    """UNKNOWN is the third state (CLAUDE.md A2), and it must be NAMED.

    A declared install dir that does not exist is a configuration error. The
    one thing it may never do is quietly become the cwd, because that is the
    defect this row fixes wearing a different hat.
    """
    install, elsewhere = two_dirs
    missing = install / "does-not-exist"
    assert not missing.exists()
    monkeypatch.setenv("BD_INSTALL_DIR", str(missing))
    monkeypatch.chdir(elsewhere)

    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.name == "master_password", backend.name
    assert backend.store_state() == "unreadable", backend.store_state()

    with pytest.raises(ss.SecretsUnreadableError) as excinfo:
        backend.unlock(FIXTURE_UNLOCK_VALUE)
    message = str(excinfo.value)
    # A refusal that cannot be acted on is barely better than a silent one
    # (CLAUDE.md A7): name the variable, name the directory, and do not reuse
    # the "fix the permissions" remedy, which is for a different failure.
    assert "BD_INSTALL_DIR" in message, message
    assert str(missing) in message, message
    assert "permissions" not in message, (
        f"the unresolvable-state-dir refusal reused the unreadable-vault "
        f"remedy, so the operator is told to fix permissions on a directory "
        f"that does not exist: {message}"
    )

    assert list(elsewhere.iterdir()) == [], (
        f"the refusal still wrote into the cwd {elsewhere}: "
        f"{sorted(p.name for p in elsewhere.iterdir())}"
    )
    assert not missing.exists(), "the refusal created the declared state dir"
    assert list(install.iterdir()) == [], sorted(
        p.name for p in install.iterdir())


def test_the_refusal_is_distinct_from_an_unreadable_vault(two_dirs,
                                                          monkeypatch):
    """Negative control: the two refusals must not collapse into one text."""
    install, elsewhere = two_dirs
    monkeypatch.chdir(elsewhere)
    corrupt = install / DEFAULT_NAME
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))

    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.store_state() == "unreadable", backend.store_state()
    with pytest.raises(ss.SecretsUnreadableError) as excinfo:
        backend.unlock(FIXTURE_UNLOCK_VALUE)
    message = str(excinfo.value)
    assert "BD_INSTALL_DIR" not in message, (
        f"an unreadable vault was reported as an unresolvable state dir: "
        f"{message}")
    assert "unreadable" in message, message
    # The damaged file is preserved byte-identical (row 432).
    assert corrupt.read_text(encoding="utf-8") == "{not json"


def test_a_changed_install_dir_cannot_redirect_an_active_backend(
        two_dirs, monkeypatch, capsys, tmp_path):
    """The shape lens's reproduction: configure over A, write, then change
    BD_INSTALL_DIR to B and configure again. The cached backend reads the
    module attribute at use time, so a republish would redirect the store
    into a NEW vault at B while A stays untouched -- this row's defect,
    reached through the fix's own seam. The change must be refused."""
    install, elsewhere = two_dirs
    other = tmp_path / "other-install"
    other.mkdir()
    monkeypatch.setenv("BD_INSTALL_DIR", str(install))
    monkeypatch.chdir(elsewhere)
    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.unlock(FIXTURE_UNLOCK_VALUE) is True
    backend.set("k", "v")
    assert backend.get("k") == "v"
    assert (install / DEFAULT_NAME).is_file()
    size_a = (install / DEFAULT_NAME).stat().st_size

    monkeypatch.setenv("BD_INSTALL_DIR", str(other))
    capsys.readouterr()
    assert ss.configure_backend("master_password") is True
    assert ss.get_backend() is backend, "the cached instance must survive"
    err = capsys.readouterr().err
    assert "changed while a vault backend is active" in err, err
    assert ss.SECRETS_FILE == install.resolve() / DEFAULT_NAME, ss.SECRETS_FILE

    backend.set("k2", "v2")
    assert backend.get("k2") == "v2"
    assert list(other.iterdir()) == [], (
        f"a write after the changed install dir created a NEW vault at "
        f"{other}: {sorted(p.name for p in other.iterdir())}")
    assert (install / DEFAULT_NAME).stat().st_size >= size_a


# ── transform control ────────────────────────────────────────────────────────

def test_transform_control_imports_without_exercising_vault_resolution():
    """Import-only control for the mutation battery.

    It asserts module identity and nothing about where the vault resolves, so
    every mutant in this cut's specs must ESCAPE it. That an escape is possible
    is what proves the CAUGHT verdicts are assertion failures rather than
    import or syntax breakage.
    """
    assert ss.__name__ == "bulk_downloader.secrets_store"
    assert hasattr(ss, "SECRETS_FILE")
    assert hasattr(ss, "SECRETS_META_FILE")
    assert callable(ss._resolve_vault_paths)
    assert os.path.basename(ss.__file__) == "secrets_store.py"
