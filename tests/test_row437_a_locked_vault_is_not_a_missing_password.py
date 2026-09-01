"""Row 437: the extension fetch route conflated a LOCKED vault with a deleted
or never-stored password.

After every service restart the master-password vault is locked, so
``backend.get(entry_key)`` returns None with the password still on disk. The
route saw only that None and audited ``no_password_stored`` -- factually wrong
-- so an operator debugging autofill from ``vault_access.log`` was steered to
"the password was deleted" instead of "unlock the vault".

Worse, ``check_and_record_fetch`` ran FIRST, so the entry's 5s cooldown and a
sliding-window slot were consumed and persisted on a fetch that could never
succeed, and an immediate retry after unlocking was denied on cooldown.

``resolve_password_state`` exists precisely to distinguish locked from missing
and is already used at login_impl/submit.py and login_impl/manual.py.

CLAUDE.md A7: when the vault's state cannot be determined the route reports its
own distinct UNKNOWN reason rather than laundering it into either named state.

Every password in this module is a documented zero-entropy synthetic literal.
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Flask
import pytest

from bulk_downloader import app_secrets
from bulk_downloader import auth_throttle as at
from bulk_downloader import extension_vault as ev
from bulk_downloader import secrets_store as ss


BD_GATE_SCOPE = "module"

# Documented zero-entropy synthetic values.  None of these is a credential.
_MASTER = "row437-synthetic-master-password"
_SID = "row437site"
_VALUE = "row437-synthetic-value"
_TOKEN = "row437-synthetic-vault-token"
_LABEL = "row437-extension"
_ITERATIONS = 1_000
_UNPARSEABLE = b"{row437 not valid json"


def _install(monkeypatch, root: Path):
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", root / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", root / "secrets_meta.json")
    monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", root / "vault_tokens.json")
    monkeypatch.setattr(ev, "VAULT_AUDIT_LOG", root / "vault_access.log")
    at.reset()


def _open_backend(monkeypatch):
    """Reconstruct past the get_backend cache without depending on
    configure_backend's construction behaviour (row 438 makes it idempotent)."""
    backend = ss.MasterPasswordBackend()
    monkeypatch.setattr(ss, "_backend", backend)
    monkeypatch.setattr(ss, "_backend_pref", "master_password")
    monkeypatch.setattr(ss, "_audited_cache", None)
    return backend


def _seed_token(root: Path) -> dict:
    """One redeemed vault token with an empty rate-limit ledger."""
    meta = {"label": _LABEL, "issued_at": 0, "entry_cooldowns": {},
            "recent_fetches": []}
    (root / "vault_tokens.json").write_text(
        json.dumps({"redeemed": {_TOKEN: meta}}), encoding="utf-8"
    )
    return meta


def _ledger(root: Path) -> dict:
    data = json.loads((root / "vault_tokens.json").read_text(encoding="utf-8"))
    return data["redeemed"][_TOKEN]


def _audit_lines(root: Path) -> list[str]:
    path = root / "vault_access.log"
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]


def _client(monkeypatch, sites):
    monkeypatch.setattr(app_secrets, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(
        app_secrets, "_require_vault_token",
        lambda *a, **k: (_TOKEN, {"label": _LABEL}, None),
    )
    flask_app = Flask("row437-secrets")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    return flask_app.test_client()


def _fetch(client, entry_id):
    return client.post("/api/secrets/extension/fetch_one",
                       json={"id": entry_id, "origin": "https://example.test/"})


def _stored_vault(monkeypatch, root: Path) -> tuple[str, dict]:
    """A real vault holding exactly 1 credential for a site that references it."""
    _install(monkeypatch, root)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    key = ss.site_password_key(_SID)
    backend.set(key, _VALUE)
    sites = {_SID: {"username": "u", "password": f"@cred:{key}"}}
    _seed_token(root)
    return key, sites


# ── the locked vault ────────────────────────────────────────────────


def test_a_locked_vault_never_audits_no_password_stored(monkeypatch, tmp_path):
    """Row 437 RED: the stored password audits as if it had been deleted."""
    key, sites = _stored_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)          # relock: fresh instance

    # Preconditions, from the files and the production classifier.
    assert key in backend.list_keys(), "precondition: the backend row exists"
    assert backend.is_unlocked() is False
    assert backend.is_initialized() is True
    value, state = ss.resolve_password_state(f"@cred:{key}")
    assert value is None and state == "locked", (
        "precondition: resolve_password_state reports locked, not missing"
    )
    assert _ledger(tmp_path)["entry_cooldowns"] == {}
    assert _ledger(tmp_path)["recent_fetches"] == []

    client = _client(monkeypatch, sites)
    response = _fetch(client, f"site:{_SID}")
    body = response.get_json()

    # The audit must name the real state.
    lines = _audit_lines(tmp_path)
    assert len(lines) == 1, "exactly one audit line for exactly one fetch"
    assert "no_password_stored" not in lines[0], (
        "a locked vault is not a deleted password"
    )
    assert "vault_locked" in lines[0]

    # The response distinguishes the two states.
    assert body["ok"] is False
    assert body.get("state") == "locked"
    assert response.status_code == 409

    # A locked-vault refusal consumes ZERO cooldown and ZERO window slots.
    ledger = _ledger(tmp_path)
    assert ledger["entry_cooldowns"] == {}, "no cooldown spent on a locked vault"
    assert ledger["recent_fetches"] == [], "no window slot spent on a locked vault"


def test_the_retry_immediately_after_unlocking_succeeds(monkeypatch, tmp_path):
    """The consumed cooldown used to deny the very retry the operator was
    steered toward."""
    key, sites = _stored_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.is_unlocked() is False
    client = _client(monkeypatch, sites)

    denied = _fetch(client, f"site:{_SID}")
    assert denied.status_code == 409, "precondition: the locked refusal fired"

    assert backend.unlock(_MASTER) is True
    granted = _fetch(client, f"site:{_SID}")

    assert granted.status_code == 200, granted.get_json()
    assert granted.get_json()["password"] == _VALUE


def test_a_locked_tpl_entry_is_also_locked_not_missing(monkeypatch, tmp_path):
    """The tpl: arm reaches the backend directly and had the same conflation."""
    _install(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    backend.set("bulkdl-tpl-row437", _VALUE)
    _seed_token(tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.is_unlocked() is False
    assert "bulkdl-tpl-row437" in backend.list_keys()

    client = _client(monkeypatch, {})
    response = _fetch(client, "tpl:row437")

    assert response.status_code == 409
    assert response.get_json().get("state") == "locked"
    assert _ledger(tmp_path)["entry_cooldowns"] == {}


def test_an_unmeasurable_vault_reports_its_own_unknown_reason(
    monkeypatch, tmp_path
):
    """A7: an unreadable vault is neither 'locked' nor 'no_password_stored'."""
    key, sites = _stored_vault(monkeypatch, tmp_path)
    (tmp_path / "secrets.json").write_bytes(_UNPARSEABLE)
    backend = _open_backend(monkeypatch)
    assert backend.store_state() == "unreadable", "precondition: unmeasurable"

    client = _client(monkeypatch, sites)
    response = _fetch(client, f"site:{_SID}")
    body = response.get_json()

    assert body["ok"] is False
    assert body.get("state") == "unknown"
    lines = _audit_lines(tmp_path)
    assert len(lines) == 1
    assert "no_password_stored" not in lines[0]
    assert "vault_locked" not in lines[0], (
        "an unmeasurable state must not be laundered into the locked one"
    )
    assert "vault_state_unknown" in lines[0]
    assert _ledger(tmp_path)["entry_cooldowns"] == {}


# ── negative controls ───────────────────────────────────────────────


def test_a_genuinely_absent_password_still_audits_no_password_stored(
    monkeypatch, tmp_path
):
    """Negative control: the guard was not widened into refusing everything.

    An UNLOCKED vault that genuinely holds no entry for this site must still
    audit no_password_stored and must still spend exactly one cooldown entry
    and exactly one window slot.
    """
    _install(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    key = ss.site_password_key(_SID)
    assert key not in backend.list_keys(), "precondition: genuinely absent"
    assert backend.is_unlocked() is True
    _seed_token(tmp_path)
    sites = {_SID: {"username": "u", "password": f"@cred:{key}"}}

    client = _client(monkeypatch, sites)
    response = _fetch(client, f"site:{_SID}")

    assert response.status_code == 403
    assert response.get_json()["error"] == "denied"
    lines = _audit_lines(tmp_path)
    assert len(lines) == 1
    assert "no_password_stored" in lines[0]

    ledger = _ledger(tmp_path)
    assert list(ledger["entry_cooldowns"]) == [key], (
        "exactly one cooldown entry, for exactly this key"
    )
    assert len(ledger["recent_fetches"]) == 1, "exactly one window slot"


def test_a_legacy_plaintext_password_is_served_over_a_locked_vault(
    monkeypatch, tmp_path
):
    """Negative control: resolution that does not depend on the vault must not
    be refused because the vault happens to be locked."""
    _install(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    backend.set("bulkdl-tpl-unrelated", _VALUE)
    _seed_token(tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.is_unlocked() is False, "precondition: the vault IS locked"
    sites = {_SID: {"username": "u", "password": _VALUE}}

    client = _client(monkeypatch, sites)
    response = _fetch(client, f"site:{_SID}")

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["password"] == _VALUE


def test_the_rate_limit_still_denies_a_second_fetch(monkeypatch, tmp_path):
    """Negative control: moving the vault probe ahead of the rate limit did not
    disable the rate limit."""
    key, sites = _stored_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.unlock(_MASTER) is True
    client = _client(monkeypatch, sites)

    first = _fetch(client, f"site:{_SID}")
    assert first.status_code == 200, "precondition: the first fetch is served"
    second = _fetch(client, f"site:{_SID}")

    assert second.status_code == 429
    assert len(_ledger(tmp_path)["recent_fetches"]) == 1


def test_an_unknown_site_is_still_a_generic_403(monkeypatch, tmp_path):
    """Negative control: entry existence is still not disclosed."""
    key, sites = _stored_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.unlock(_MASTER) is True
    client = _client(monkeypatch, sites)

    response = _fetch(client, "site:row437-does-not-exist")

    assert response.status_code == 403
    assert response.get_json() == {"ok": False, "error": "denied"}


# ── A7 self-audit: the fix must not act on state it read earlier ─────


def test_a_vault_that_locks_mid_request_is_not_a_missing_password(
    monkeypatch, tmp_path
):
    """The pre-gate probe is a READING. If the vault locks between that probe
    and resolution, the resulting None is the very lie the probe was added to
    stop -- so the reason is re-measured before it is named.
    """
    key, sites = _stored_vault(monkeypatch, tmp_path)
    backend = _open_backend(monkeypatch)
    assert backend.unlock(_MASTER) is True, "precondition: unlocked at probe time"

    calls: list[str] = []
    real_get = backend.get

    def _locks_between(k):
        calls.append(k)
        backend.lock()          # the race: locked after the probe, before get
        return real_get(k)

    monkeypatch.setattr(backend, "get", _locks_between)

    client = _client(monkeypatch, sites)
    response = _fetch(client, f"site:{_SID}")
    body = response.get_json()

    assert calls == [key], "precondition: the racing get fired exactly once"
    assert backend.is_unlocked() is False, "precondition: the race really landed"
    assert response.status_code == 409
    assert body.get("state") == "locked"
    lines = _audit_lines(tmp_path)
    assert len(lines) == 1
    assert "no_password_stored" not in lines[0]
    assert "vault_locked" in lines[0]
