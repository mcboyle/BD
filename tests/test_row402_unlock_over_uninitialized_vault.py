"""Row 402: a password accepted against an uninitialised vault must not report
an incoherent state.

MEASURED on test7 at v3.66.1349: POST /api/secrets/unlock on a fresh vault
returned HTTP 200 and left ``is_unlocked=True`` while ``is_initialized=False``
over zero stored secrets -- there is nothing to unlock, no key was derived
against any stored material, and no caller can tell this from a usable vault by
reading ``is_unlocked`` alone (CLAUDE.md A7: a surface reporting OK over a
measurement it never took).

DECISION -- the acceptance offered "refuse with a distinct state" OR "initialise
explicitly and say so". We INITIALISE AND SAY SO, and the reason is structural:
``MasterPasswordBackend.set()`` requires ``self._key``; ``unlock()`` is the ONLY
path that derives and holds it; both ``migrate_from_plaintext`` and
``import_apply`` gate the first ``set()`` on ``is_unlocked()``. Refusing unlock on
an empty vault would kill the ONLY first-use setup path (and
test_v3_66_43_pwmgr_remainder pins ``unlock("anything") is True`` on a fresh
vault). So a fresh unlock IS the in-process initialisation: ``is_initialized()``
reports True the instant a key is held -- which enforces the invariant
``is_unlocked ==> is_initialized`` -- and the surfaces say so: the unlock
endpoint flags ``initialized_empty_vault`` and the health surface names a
distinct ``unlocked_zero_resolved`` state.

Four states are proven reachable and distinguishable here:
    uninitialised / locked / unlocked / unlocked-but-zero-resolved.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

import pytest
from flask import Flask

from bulk_downloader import app_health
from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

_MASTER = "row402-synthetic-master-password"
_KEY = "bulkdl-site-row402"
_REF = f"@cred:{_KEY}"
_VALUE = "row402-synthetic-value"

# The four distinguishable health states this row requires.
_S_UNINIT = "uninitialized"
_S_LOCKED = "locked"
_S_UNLOCKED = "unlocked"
_S_ZERO = "unlocked_zero_resolved"


def _isolate_backend(monkeypatch, root):
    """A fresh, isolated master-password backend rooted at ``root``.

    Real AES-GCM path; only the KDF iteration count is lowered so the synthetic
    fixture is cheap to build. Audit + throttle are cleared so neither ambient
    state can decide an outcome.
    """
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    at.reset()
    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.name == "master_password"
    backend._data["iterations"] = 1_000
    return backend


# ── the invariant: is_unlocked can never be True while is_initialized False ──

def test_fresh_unlock_can_never_report_unlocked_over_uninitialized(
    monkeypatch, tmp_path
):
    backend = _isolate_backend(monkeypatch, tmp_path)

    # PRECONDITION: a genuinely uninitialised, locked vault holding zero secrets.
    assert backend.list_keys() == []
    assert len(backend.list_keys()) == 0
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is False

    # First-use is preserved: any password is accepted on an empty vault, and
    # the derived key is really held (assert the seam, not just the return).
    assert backend.unlock(_MASTER) is True
    assert backend.is_unlocked() is True
    assert backend._key is not None

    # THE DEFECT (row 402): is_unlocked True with is_initialized False is an
    # incoherent pair. Holding a key IS being initialised.
    assert backend.is_initialized() is True, (
        "is_unlocked True must imply is_initialized True; a held key can never "
        "coexist with is_initialized False"
    )
    # The invariant stated directly and generally.
    assert not (backend.is_unlocked() and not backend.is_initialized())


def test_wrong_password_on_initialised_vault_is_the_negative_control(
    monkeypatch, tmp_path
):
    """NEGATIVE CONTROL -- must fail for the intended reason.

    A wrong password on a REAL initialised vault is refused and leaves the vault
    locked. This is the safety ordering the row wants restored: an initialised
    vault is at least as strict as -- not stricter than -- an empty one.
    """
    backend = _isolate_backend(monkeypatch, tmp_path)
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)  # commit the master password (stamps the verifier)
    backend.lock()

    # PRECONDITION: a real initialised vault, one stored secret, locked.
    assert backend.list_keys() == [_KEY]
    assert len(backend.list_keys()) == 1
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is True

    assert backend.unlock("row402-wrong-password") is False
    assert backend.is_unlocked() is False
    # Invariant holds in the failing direction too: initialised stays True.
    assert backend.is_initialized() is True


def test_correct_password_on_initialised_vault_still_unlocks(monkeypatch, tmp_path):
    """The row-mandated positive control: the fix must not over-refuse."""
    backend = _isolate_backend(monkeypatch, tmp_path)
    assert backend.unlock(_MASTER) is True
    backend.set(_KEY, _VALUE)
    backend.lock()
    assert backend.is_unlocked() is False

    assert backend.unlock(_MASTER) is True
    assert backend.is_unlocked() is True
    assert backend.is_initialized() is True
    assert backend.get(_KEY) == _VALUE


# ── the four health states, each reachable and exactly shaped ────────────────

def test_health_state_uninitialised_is_reachable(monkeypatch, tmp_path):
    backend = _isolate_backend(monkeypatch, tmp_path)
    sites: dict = {}  # a bare vault with nothing configured against it

    # PRECONDITION
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert ss.password_reference_keys(sites) == []

    h = app_health.credential_health(sites)
    assert h["state"] == _S_UNINIT
    assert h["ok"] is True
    assert h["is_initialized"] is False
    assert h["is_unlocked"] is False
    assert h["reference_count"] == 0
    assert h["stored_count"] == 0
    assert h["resolved_count"] == 0
    assert h["missing_count"] == 0
    assert h["unavailable_count"] == 0
    assert not (h["is_unlocked"] and not h["is_initialized"])


def test_health_state_locked_is_reachable(monkeypatch, tmp_path):
    backend = _isolate_backend(monkeypatch, tmp_path)
    backend.unlock(_MASTER)
    backend.set(_KEY, _VALUE)
    backend.lock()
    sites = {"row402": {"name": "Row 402", "password": _REF}}

    # PRECONDITION: one stored secret, one reference, key forgotten.
    assert backend.list_keys() == [_KEY]
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is False
    assert ss.password_reference_keys(sites) == [_KEY]

    h = app_health.credential_health(sites)
    assert h["state"] == _S_LOCKED
    assert h["ok"] is False
    assert h["is_initialized"] is True
    assert h["is_unlocked"] is False
    assert h["reference_count"] == 1
    assert h["stored_count"] == 1
    assert h["unavailable_count"] == 1
    assert h["missing_count"] == 0
    assert h["resolved_count"] == 0
    assert not (h["is_unlocked"] and not h["is_initialized"])


def test_health_state_unlocked_is_reachable(monkeypatch, tmp_path):
    backend = _isolate_backend(monkeypatch, tmp_path)
    backend.unlock(_MASTER)
    backend.set(_KEY, _VALUE)
    sites = {"row402": {"name": "Row 402", "password": _REF}}

    # PRECONDITION: unlocked, and the reference actually decrypts.
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True
    assert ss.password_reference_keys(sites) == [_KEY]
    assert ss.resolve_password(_REF) == _VALUE

    h = app_health.credential_health(sites)
    assert h["state"] == _S_UNLOCKED
    assert h["ok"] is True
    assert h["is_initialized"] is True
    assert h["is_unlocked"] is True
    assert h["reference_count"] == 1
    assert h["stored_count"] == 1
    assert h["resolved_count"] == 1
    assert h["missing_count"] == 0
    assert h["unavailable_count"] == 0
    assert not (h["is_unlocked"] and not h["is_initialized"])


def test_health_state_unlocked_but_zero_resolved_is_reachable(monkeypatch, tmp_path):
    """The row's exact scenario: unlocked over an empty vault, zero resolved."""
    backend = _isolate_backend(monkeypatch, tmp_path)
    assert backend.unlock(_MASTER) is True
    sites = {"row402": {"name": "Row 402", "password": _REF}}

    # PRECONDITION: unlocked, initialised (a key is held), yet zero stored and
    # the one reference cannot resolve.
    assert backend.is_unlocked() is True
    assert backend.is_initialized() is True
    assert backend.list_keys() == []
    assert len(backend.list_keys()) == 0
    assert ss.password_reference_keys(sites) == [_KEY]
    assert ss.resolve_password(_REF) is None

    h = app_health.credential_health(sites)
    assert h["state"] == _S_ZERO
    assert h["ok"] is False
    assert h["is_initialized"] is True
    assert h["is_unlocked"] is True
    assert h["reference_count"] == 1
    assert h["stored_count"] == 0
    assert h["resolved_count"] == 0
    assert h["missing_count"] == 1
    assert h["unavailable_count"] == 0
    assert not (h["is_unlocked"] and not h["is_initialized"])


def _build_state(backend, which):
    if which == "uninitialised":
        return {}
    if which == "locked":
        backend.unlock(_MASTER)
        backend.set(_KEY, _VALUE)
        backend.lock()
        return {"row402": {"password": _REF}}
    if which == "unlocked":
        backend.unlock(_MASTER)
        backend.set(_KEY, _VALUE)
        return {"row402": {"password": _REF}}
    if which == "zero":
        backend.unlock(_MASTER)
        return {"row402": {"password": _REF}}
    raise AssertionError(which)


def test_four_vault_states_are_pairwise_distinguishable(monkeypatch, tmp_path):
    produced = {}
    for i, which in enumerate(["uninitialised", "locked", "unlocked", "zero"]):
        sub = tmp_path / f"vault{i}"
        sub.mkdir()
        backend = _isolate_backend(monkeypatch, sub)
        sites = _build_state(backend, which)
        produced[which] = app_health.credential_health(sites)["state"]

    # Distinguishable: four inputs, four distinct named states.
    assert len(set(produced.values())) == 4
    assert produced == {
        "uninitialised": _S_UNINIT,
        "locked": _S_LOCKED,
        "unlocked": _S_UNLOCKED,
        "zero": _S_ZERO,
    }


# ── HTTP seam: /api/health names the state and returns the right code ────────

@contextmanager
def _memory_db():
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _health_client(monkeypatch, sites):
    monkeypatch.setattr(app_health, "db_conn", _memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health.api_health_v2,
        "_ollama_cache",
        (time.time(), {"reachable": False, "model": None, "error": "test-disabled"}),
        raising=False,
    )
    monkeypatch.setattr(
        app_health,
        "build_identity",
        lambda _install_dir: {"sha": None, "built_at": None, "source": "unknown"},
    )
    flask_app = Flask("row402-health")
    flask_app.register_blueprint(app_health.health_bp)
    return flask_app.test_client()


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_health_endpoint_flags_unlocked_zero_resolved(monkeypatch, tmp_path, path):
    backend = _isolate_backend(monkeypatch, tmp_path)
    assert backend.unlock(_MASTER) is True
    sites = {"row402": {"name": "Row 402", "password": _REF}}

    # PRECONDITION: unlocked over an empty vault with one unresolved reference.
    assert backend.is_unlocked() is True
    assert backend.is_initialized() is True
    assert backend.list_keys() == []

    client = _health_client(monkeypatch, sites)
    resp = client.get(path)
    body = resp.get_json()
    assert resp.status_code == 503, body
    assert body["degraded"] == "credential_unlocked_zero_resolved"
    creds = body["credentials"]
    assert creds["state"] == _S_ZERO
    assert creds["is_initialized"] is True
    assert creds["is_unlocked"] is True
    assert creds["stored_count"] == 0
    assert creds["resolved_count"] == 0
    assert creds["reference_count"] == 1
    assert not (creds["is_unlocked"] and not creds["is_initialized"])


# ── the unlock endpoint SAYS SO when it initialises an empty vault ───────────

def _secrets_client(monkeypatch, tmp_path):
    """A bare Flask app carrying only the secrets blueprint.

    Deliberately not the full ``bulk_downloader.app``: the unlock view needs no
    session, CSRF pairing, or ``/api/pair`` -- and avoiding the app keeps this
    off ``_lan_ip_guess``'s UDP route lookup, so the test touches no network at
    all (A6).
    """
    from bulk_downloader.app_secrets import secrets_bp

    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    backend = _isolate_backend(monkeypatch, tmp_path)

    flask_app = Flask("row402-secrets")
    flask_app.register_blueprint(secrets_bp)
    return flask_app.test_client(), backend


def test_unlock_endpoint_says_so_when_it_initialises_an_empty_vault(
    monkeypatch, tmp_path
):
    client, backend = _secrets_client(monkeypatch, tmp_path)

    # PRECONDITION: a fresh, empty, locked master-password vault.
    assert backend.name == "master_password"
    assert backend.list_keys() == []
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is False

    # Unlocking an EMPTY vault initialises it -- and SAYS SO.
    r = client.post("/api/secrets/unlock", json={"password": _MASTER})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["ok"] is True
    assert body.get("initialized_empty_vault") is True
    # The invariant now holds through the endpoint's own effect.
    assert backend.is_unlocked() is True
    assert backend.is_initialized() is True

    # Populate the vault, then relock.
    backend.set(_KEY, _VALUE)
    backend.lock()
    assert backend.list_keys() == [_KEY]

    # Unlocking a POPULATED vault is a plain unlock -- the flag is ABSENT.
    # That absence is what makes "says so" distinctive, not decorative.
    r2 = client.post("/api/secrets/unlock", json={"password": _MASTER})
    body2 = r2.get_json()
    assert r2.status_code == 200, body2
    assert body2["ok"] is True
    assert "initialized_empty_vault" not in body2

    # NEGATIVE CONTROL: a wrong password on the populated vault is refused with
    # the distinctive "incorrect password" diagnostic (401), and the vault stays
    # locked -- distinct from the empty-vault accept-anything path.
    backend.lock()
    at.reset()
    r3 = client.post("/api/secrets/unlock", json={"password": "row402-wrong"})
    body3 = r3.get_json()
    assert r3.status_code == 401, body3
    assert body3["ok"] is False
    assert body3["error"] == "incorrect password"
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is True
