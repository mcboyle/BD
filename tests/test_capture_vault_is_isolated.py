"""The capture may use its own vault, and only when it says so twice.

WHY THIS EXISTS. capture.sh stops the service and starts a fresh process
(capture.sh:206 and :452). The master-password key is in-memory only
(secrets_store.py: self._key, is_unlocked() is `self._key is not None`), with no
persistence and no auto-unlock path anywhere in the tree. So the vault is
NECESSARILY locked when the seeder runs at step 5a, and an operator unlocking
beforehand cannot help -- the capture's own restart discards it. L6/L8 were
therefore unsatisfiable under capture.sh no matter what the operator did.

Confirmed on test4 2026-07-28: identical seeder command, one variable changed.
Inside a capture it printed "REFUSED - the secrets vault is LOCKED"; standalone
with no restart it reached seed_login.

THE SHAPE OF THE FIX. The capture points BD at a SEPARATE vault file holding
exactly one credential -- the fixture's own published test password, which
tools/fixture_site.py prints in its module docstring and which authenticates
against 127.0.0.1 only. The operator's real vault is never opened, never
unlocked, and never at risk. What L6/L8 assert is that BD can store a
credential, drive a login, persist a jar and record auth health; which FILE the
vault lives in is irrelevant to whether that code path works, so this supplies
input to a real path rather than substituting for its output.

THE DANGER THIS FILE CONTAINS. MasterPasswordBackend.unlock() accepts ANY
password when the vault holds no ciphertexts, stamping it as the verifier on the
first set(). So a stray env var that silently redirected the vault would not
error -- it would hand back an empty, trivially-unlockable credential store and
look healthy. That is worse than crashing, and it is why the override needs two
keys rather than one: a path alone does nothing. Every other BD_* path override
in the tree is single-key; this one deliberately is not, and the reason is that
none of the others can silently produce a working-looking empty vault.

The password is NEVER defaulted in source. capture.sh supplies it at runtime.
A hardcoded default would mean every install shipped a known unlock.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bulk_downloader import secrets_store as ss


DEFAULT_NAME = "secrets.json"
DEFAULT_META_NAME = "secrets_meta.json"


def _resolve(monkeypatch, **env):
    """Resolve vault paths under an exact environment.

    Both keys are cleared first so a value inherited from the ambient
    environment cannot decide the outcome -- the denominator is what this test
    sets, not what the host happens to carry.
    """
    monkeypatch.delenv("BD_SECRETS_FILE", raising=False)
    monkeypatch.delenv("BD_CAPTURE_VAULT", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ss._resolve_vault_paths()


# ── the default is untouched ─────────────────────────────────────────────────

def test_with_no_override_the_vault_is_where_it_has_always_been(monkeypatch):
    secrets, meta = _resolve(monkeypatch)
    assert secrets == Path(DEFAULT_NAME), secrets
    assert meta == Path(DEFAULT_META_NAME), meta


def test_the_module_attribute_still_carries_the_resolved_default():
    """The 7 test files that monkeypatch ss.SECRETS_FILE must keep working.

    Resolution happens at import and is ASSIGNED to the module attribute, the
    way app.py:33 binds BD_SITES_CONFIG_PATH. A call-time getter would have
    left every one of those monkeypatches silently inert -- the patch would
    apply to an attribute nothing reads.
    """
    assert isinstance(ss.SECRETS_FILE, Path)
    assert isinstance(ss.SECRETS_META_FILE, Path)


# ── one key is not enough ────────────────────────────────────────────────────

def test_the_path_alone_does_not_move_the_vault(monkeypatch, tmp_path):
    """A stray BD_SECRETS_FILE must be inert.

    This is the test that matters most, and it is also the one most at risk of
    passing for the wrong reason: before the override existed it passed
    vacuously. It is mutation-tested in the cut that introduced it -- honouring
    a single key makes it FAIL.
    """
    stray = tmp_path / "somewhere-else.json"
    secrets, meta = _resolve(monkeypatch, BD_SECRETS_FILE=str(stray))
    assert secrets == Path(DEFAULT_NAME), (
        f"BD_SECRETS_FILE alone redirected the vault to {secrets}. One env var "
        f"must not be able to do this: unlock() accepts any password on a vault "
        f"with no ciphertexts, so a silent redirect yields an empty, trivially "
        f"unlockable credential store that looks healthy."
    )
    assert meta == Path(DEFAULT_META_NAME), meta


def test_the_opt_in_alone_does_not_move_the_vault(monkeypatch):
    """BD_CAPTURE_VAULT with no path has nowhere to go."""
    secrets, meta = _resolve(monkeypatch, BD_CAPTURE_VAULT="1")
    assert secrets == Path(DEFAULT_NAME), secrets
    assert meta == Path(DEFAULT_META_NAME), meta


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "2", " 1"])
def test_only_an_exact_opt_in_counts(monkeypatch, tmp_path, value):
    """Truthiness is not consent. Only "1" opts in."""
    stray = tmp_path / "capture-secrets.json"
    secrets, _meta = _resolve(
        monkeypatch, BD_SECRETS_FILE=str(stray), BD_CAPTURE_VAULT=value)
    assert secrets == Path(DEFAULT_NAME), (
        f"BD_CAPTURE_VAULT={value!r} was treated as opt-in; only '1' may be."
    )


# ── both keys, and the vault moves ───────────────────────────────────────────

def test_both_keys_move_the_vault_and_its_metadata_together(monkeypatch,
                                                            tmp_path):
    """The meta file must follow the vault into the same directory.

    Left behind, the capture would write backend metadata into the operator's
    secrets_meta.json -- so the run would not be isolated after all, and the
    isolation would look complete while leaking through a second file.
    """
    target = tmp_path / "capture-secrets.json"
    secrets, meta = _resolve(
        monkeypatch, BD_SECRETS_FILE=str(target), BD_CAPTURE_VAULT="1")
    assert secrets == target, secrets
    assert meta.parent == target.parent, (
        f"metadata stayed at {meta} while the vault moved to {secrets}; the "
        f"capture would write into the operator's metadata file."
    )
    assert meta != Path(DEFAULT_META_NAME), meta


def test_the_capture_vault_is_never_the_operators_vault(monkeypatch, tmp_path):
    """An override resolving onto the default path is refused.

    Pointing the capture at the real vault would unlock the operator's
    credentials with a throwaway password -- the exact outcome this design
    exists to prevent.
    """
    secrets, _meta = _resolve(
        monkeypatch, BD_SECRETS_FILE=DEFAULT_NAME, BD_CAPTURE_VAULT="1")
    assert secrets == Path(DEFAULT_NAME), secrets


# ── no password may live in the tree ─────────────────────────────────────────

def test_no_capture_password_is_defaulted_in_source():
    """The password is capture.sh's to supply at runtime, never a constant.

    A default would mean every install shipped a known unlock, and unlock()'s
    any-password-on-empty behaviour would make that immediately exploitable on
    a fresh vault.
    """
    src = Path(ss.__file__).read_text(encoding="utf-8")
    for banned in ("12345", "BD_CAPTURE_VAULT_PASSWORD", "capture_password"):
        assert banned not in src, (
            f"{banned!r} appears in secrets_store.py; the capture password "
            f"must never be defaulted in source."
        )
