"""Row 413 -- /api/health must not conflate vault state with the download hold.

MEASURED DEFECT (2026-08-30, reproduced here on the defective parent
f5a91265 / v3.66.1362): the health surface answers "may this host download?"
with a single conflated ``ok``/``degraded`` pair.  On a host whose vault is
merely uninitialised, ``GET /api/health`` reports ``ok: false`` with
``degraded: credential_vault_uninitialized`` -- and that is the ONLY named
signal about downloadability the operator gets.  There is no field that says
whether the vault is ready, so an operator reading the payload cannot attribute
a stopped host to a deliberate download hold rather than to the vault, nor the
reverse.  This is the confusion row 408 removed from the deploy path.

OPERATOR RULING 2026-08-30: separate the two states.  ``downloads_allowed``
reflects the DOWNLOAD HOLD ALONE; vault readiness gets its own named field.

WHAT THIS FILE PROVES

  1. ``vault_ready`` exists, is a real bool, and equals ``credentials["ok"]``
     on BOTH /api/health and /api/health/v2, in BOTH the ready branch (where
     _attach_credential_health returns early) and the degraded branch.
  2. ALL FOUR combinations of (hold CLEAR/HELD x vault ready/not-ready) are
     reachable and pairwise distinguishable: the four
     ``(downloads_allowed, vault_ready)`` pairs are four DISTINCT exact pairs.
  3. ``downloads_allowed`` is a function of the HOLD ALONE: holding the hold
     fixed and flipping the vault never changes it (exact equality, both ways).
  4. NEGATIVE CONTROL: a genuinely HELD hold still reports
     ``downloads_allowed is False`` -- including over a vault that is proven
     READY, so the refusal cannot be attributed to the vault.
  5. NEGATIVE CONTROL: a CLEAR hold over a NOT-READY vault still reports
     ``downloads_allowed is True``, so ``vault_ready`` cannot leak into it.

RED-first on the defective parent: every state below raises
``KeyError: 'vault_ready'`` -- the named field does not exist, which is exactly
the defect.  ``downloads_allowed`` was already hold-only in
``download_hold.health_block``; the missing half was the vault's own field.

Preconditions are asserted, never assumed: each vault is proven initialized /
unlocked (or proven neither) BEFORE the request, the hold record is read back
from the durable store before the request, and the reference denominator is
pinned at zero so a stray ``@cred:`` reference cannot silently turn a "ready"
vault into ``missing_credentials``.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from flask import Flask
import pytest

from bulk_downloader import app_health
from bulk_downloader import auth_throttle as at
from bulk_downloader import download_hold as dh
from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

# Zero-entropy documented fixture value (CLAUDE.md A4). It unlocks a vault that
# exists only inside this test's tmp_path and holds nothing.
_MASTER = "row413-synthetic-master-password"
_ITERATIONS = 1_000


# ── isolated health client (row 402's pattern) ──────────────────────────────

@contextmanager
def _memory_db():
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _health_client(monkeypatch, sites=None):
    """A health blueprint over a memory DB, zero runners, zero sites.

    Zero sites pins the credential reference denominator at 0, so "ready"
    means initialized+unlocked and cannot decay into missing_credentials.
    """
    monkeypatch.setattr(app_health, "db_conn", _memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: (sites or {}))
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health, "build_identity",
        lambda _install_dir: {"sha": None, "built_at": None,
                              "source": "unknown"})
    flask_app = Flask("row413-health")
    flask_app.register_blueprint(app_health.health_bp)
    return flask_app.test_client()


# ── the two axes, each built and PROVEN before any request ──────────────────

def _install_vault(monkeypatch, root: Path, *, ready: bool):
    """Install a master-password vault at `root` and prove its exact state."""
    assert ss._CRYPTO_AVAILABLE, (
        "cryptography is required: without it the READY half of this matrix "
        "cannot be built and the four-state proof would be a two-state proof")
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    backend = ss.MasterPasswordBackend()
    backend._data["iterations"] = _ITERATIONS
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    at.reset()

    # PRECONDITION: a brand-new vault is neither initialized nor unlocked.
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    assert backend.list_keys() == []
    if ready:
        # First unlock is the product's first-use setup path (row 402).
        assert backend.unlock(_MASTER) is True
        assert backend.is_initialized() is True
        assert backend.is_unlocked() is True
    # PRECONDITION: the axis really is where this call says it is.
    assert backend.is_initialized() is ready
    assert backend.is_unlocked() is ready
    return backend


def _install_hold(store: Path, *, held: bool):
    """Put the durable hold where `held` says, and read it back from disk."""
    if held:
        assert dh.hold("row413-matrix", note="four-state proof") is True
        raw = json.loads(store.read_text(encoding="utf-8"))
        assert raw["download_hold"]["held"] is True, raw
        expected = dh.HELD
    else:
        assert dh.lift(note="row413 matrix clear") is True
        raw = json.loads(store.read_text(encoding="utf-8"))
        # A lift is a POSITIVE record, never an absence (row 390).
        assert raw["download_hold"]["held"] is False, raw
        expected = dh.CLEAR
    # PRECONDITION: read the state back through the product's own reader.
    assert dh.hold_state()["state"] == expected
    return expected


def _measure(monkeypatch, root: Path, store: Path, *, held: bool, ready: bool,
             path: str = "/api/health"):
    _install_vault(monkeypatch, root, ready=ready)
    hold_state = _install_hold(store, held=held)
    response = _health_client(monkeypatch).get(path)
    body = response.get_json()
    assert body is not None, response.get_data(as_text=True)
    # The hold axis landed where the fixture put it -- assert before verdicts.
    assert body["download_hold"]["state"] == hold_state, body["download_hold"]
    return response, body


# ── 1. the named field exists, is a bool, and is the vault's own ────────────

@pytest.mark.parametrize("ready,expected_state", [
    (True, "unlocked"),
    (False, "uninitialized"),
])
@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_vault_readiness_has_its_own_named_field(
        clean_workdir, monkeypatch, path, ready, expected_state):
    store = clean_workdir / "app_config.json"
    _, body = _measure(monkeypatch, clean_workdir / "vault", store,
                       held=False, ready=ready, path=path)
    assert "vault_ready" in body, sorted(body)
    assert body["vault_ready"] is ready, body["vault_ready"]
    # It is the VAULT's readiness, not a rename of the payload verdict.
    assert body["credentials"]["ok"] is ready, body["credentials"]
    assert body["credentials"]["state"] == expected_state, body["credentials"]
    assert body["credentials"]["reference_count"] == 0, body["credentials"]


# ── 2. all four combinations, pairwise distinct ─────────────────────────────

def test_four_hold_vault_combinations_are_reachable_and_distinct(
        clean_workdir, monkeypatch):
    store = clean_workdir / "app_config.json"
    combos = [(False, True), (True, True), (False, False), (True, False)]
    measured: dict[tuple[bool, bool], dict] = {}
    for i, (held, ready) in enumerate(combos):
        response, body = _measure(
            monkeypatch, clean_workdir / f"vault{i}", store,
            held=held, ready=ready)
        block = body["download_hold"]
        # EXACT values, never truthiness.
        assert block["state"] == (dh.HELD if held else dh.CLEAR), block
        assert block["downloads_allowed"] is (not held), block
        assert body["vault_ready"] is ready, body
        # A deliberate hold is not an unhealthy host; an unready vault is.
        assert body["ok"] is ready, body
        assert response.status_code == (200 if ready else 503), body
        assert body.get("degraded") == (
            None if ready else "credential_vault_uninitialized"), body
        measured[(held, ready)] = {
            "downloads_allowed": block["downloads_allowed"],
            "vault_ready": body["vault_ready"],
        }

    # DENOMINATOR: four DISTINCT inputs asked for, four distinct keys recorded.
    # A duplicated input would silently shrink the matrix to three states.
    assert len(combos) == 4 and len(set(combos)) == 4, combos
    assert len(measured) == 4, measured
    pairs = [(m["downloads_allowed"], m["vault_ready"]) for m in
             measured.values()]
    assert len(pairs) == 4 and len(set(pairs)) == 4, pairs
    assert sorted(pairs) == [(False, False), (False, True),
                             (True, False), (True, True)], pairs
    # DISTINGUISHABLE IN BOTH DIRECTIONS: neither field alone separates the
    # four, which is precisely why both are required.
    assert len({p[0] for p in pairs}) == 2 and len({p[1] for p in pairs}) == 2

    # downloads_allowed is a function of the HOLD ALONE: hold the hold fixed,
    # flip the vault, and it does not move.
    for held in (False, True):
        allowed = {measured[(held, r)]["downloads_allowed"] for r in
                   (True, False)}
        assert allowed == {not held}, (held, allowed)
    # ...and vault_ready is a function of the VAULT alone.
    for ready in (False, True):
        vault = {measured[(h, ready)]["vault_ready"] for h in (True, False)}
        assert vault == {ready}, (ready, vault)


# ── 3. negative controls ────────────────────────────────────────────────────

def test_a_genuine_hold_still_refuses_over_a_proven_ready_vault(
        clean_workdir, monkeypatch):
    """The negative control row 413 requires: HELD stays HELD.

    The vault is proven READY first, so ``downloads_allowed: false`` here can
    only be the hold -- there is no vault degradation left to explain it.
    """
    store = clean_workdir / "app_config.json"
    _, body = _measure(monkeypatch, clean_workdir / "vault", store,
                       held=True, ready=True)
    assert body["vault_ready"] is True, body
    assert body["credentials"]["ok"] is True, body["credentials"]
    assert body["ok"] is True, body
    block = body["download_hold"]
    assert block["state"] == dh.HELD, block
    assert block["downloads_allowed"] is False, block
    assert block["reason"] == "row413-matrix", block
    # The module's own answer agrees with the payload's.
    assert dh.downloads_allowed()[0] is False


def test_an_unmeasurable_vault_is_not_ready(clean_workdir, monkeypatch):
    """A7: the new field must not reproduce the fail-open shape it fixes.

    ``credential_health`` returns an UNKNOWN block when the vault's state
    cannot be measured (here: unlocked while nothing durable commits the
    password -- the incoherent pair row 402 refuses to resolve through).
    UNKNOWN must read as NOT ready. A ``vault_ready: true`` here, or a missing
    key, would be exactly the fail-open this row exists to remove.
    """
    class _IncoherentBackend:
        name = "row413-incoherent"

        def is_initialized(self):
            return False

        def is_unlocked(self):          # unlocked with no durable commitment
            return True

        def list_keys(self):
            return []

    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.setattr(ss, "_backend", _IncoherentBackend())
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    assert _install_hold(clean_workdir / "app_config.json",
                         held=False) == dh.CLEAR

    body = _health_client(monkeypatch).get("/api/health").get_json()
    # PRECONDITION: the measurement really was unavailable, not merely bad.
    assert body["credentials"]["state"] == "unknown", body["credentials"]
    assert body["credentials"]["ok"] is False, body["credentials"]
    # THE VERDICT: unknown is not ready, and the key is present to say so.
    assert "vault_ready" in body, sorted(body)
    assert body["vault_ready"] is False, body
    assert body["ok"] is False, body
    assert body["degraded"] == "credential_state_unknown", body
    # ...and the hold half is untouched by any of it.
    assert body["download_hold"]["state"] == dh.CLEAR, body["download_hold"]
    assert body["download_hold"]["downloads_allowed"] is True, body


def test_an_unready_vault_does_not_make_a_clear_hold_refuse(
        clean_workdir, monkeypatch):
    """The inverse control: vault_ready must not leak into downloads_allowed."""
    store = clean_workdir / "app_config.json"
    _, body = _measure(monkeypatch, clean_workdir / "vault", store,
                       held=False, ready=False)
    assert body["vault_ready"] is False, body
    assert body["ok"] is False, body
    assert body["degraded"] == "credential_vault_uninitialized", body
    block = body["download_hold"]
    assert block["state"] == dh.CLEAR, block
    assert block["downloads_allowed"] is True, block
    assert dh.downloads_allowed()[0] is True
