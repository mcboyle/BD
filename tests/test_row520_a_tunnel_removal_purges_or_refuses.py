"""Row 520: a tunnel removal reported success with its secrets unpurged.

``_purge_secrets_for_tunnel`` called ``backend.list_keys()`` inside an inner
bare ``except Exception`` that substituted ``keys = []``, so the
``SecretsUnreadableError`` a damaged vault raises made the delete loop run
exactly 0 times and the function return normally. An outer bare
``except Exception`` was a second fail-open writing one stderr line, so no
purge failure reached a caller by any path.

``remove_tunnel_config`` had already dropped the tunnel from ``_state``
before purging, and the HTTP surface discarded even its boolean, so the
steady state was: the tunnel gone, the saved config no longer carrying the
``@cred:`` references, and every ``tunnel_id:field`` entry still in the vault
with nothing left pointing at it -- orphaned silently, success reported.

The sibling ``rekey_tunnel`` re-raises its secrets failure; this caller now
matches it. CLAUDE.md A7: an inventory that cannot be measured is not an
empty inventory.

Every password in this module is a documented zero-entropy synthetic literal.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bulk_downloader import auth_throttle as at
from bulk_downloader import secrets_store as ss
from bulk_downloader import vpn_config


BD_GATE_SCOPE = "module"

# Documented zero-entropy synthetic values.  None of these is a credential.
_MASTER = "row520-synthetic-master-password"
_PW = "row520-synthetic-password"
_PK = "row520-synthetic-private-key"
_OTHER = "row520-synthetic-other-password"
_ITERATIONS = 1_000
_UNPARSEABLE = b"{row520 not valid json"
# The intact vault bytes, captured by the fixture so a RED can prove the
# entries survived rather than only that the refusal did not fire.
_ORIGINAL: list = []

_T1 = "row520t1"
_OTHER_TUNNEL = "row520other"


def _reconstruct_backend(monkeypatch):
    """Reconstruct the module backend past the get_backend cache.

    Deliberately NOT ``configure_backend`` alone: row 438 makes that call
    idempotent, so it returns the cached instance and the reconstruction the
    RED depends on would silently stop happening.
    """
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_audited_cache", None)
    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    raw = getattr(backend, "_backend", backend)  # unwrap the audit proxy
    return raw


@pytest.fixture
def vault_and_tunnels(monkeypatch, tmp_path):
    """1 tunnel, 3 stored secrets, 2 of them under the t1: prefix.

    Every precondition is asserted by reading the vault back through
    ``list_keys``, not from the fixture's own in-memory view.
    """
    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(tmp_path / "tunnels.json"))
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    at.reset()
    vpn_config._reset_for_tests()

    backend = _reconstruct_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    backend.set(f"{_T1}:password", _PW)
    backend.set(f"{_T1}:private_key", _PK)
    backend.set(f"{_OTHER_TUNNEL}:password", _OTHER)

    vpn_config.load()
    vpn_config.add_tunnel_config({"tunnel_id": _T1, "name": "t1",
                                  "provider": "generic", "backend": "wireguard"})

    # Preconditions, read back through the store itself.
    keys = sorted(ss.get_backend().list_keys())
    assert keys == sorted([f"{_T1}:password", f"{_T1}:private_key",
                           f"{_OTHER_TUNNEL}:password"]), keys
    assert len([k for k in keys if k.startswith(f"{_T1}:")]) == 2
    assert len(vpn_config.load()["tunnels"]) == 1
    _ORIGINAL[:] = [(tmp_path / "secrets.json").read_bytes()]
    try:
        yield tmp_path
    finally:
        vpn_config._reset_for_tests()
        at.reset()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _damage(root: Path, monkeypatch) -> str:
    """Make the vault unreadable and prove the reconstructed backend refuses."""
    path = root / "secrets.json"
    path.write_bytes(_UNPARSEABLE)
    backend = _reconstruct_backend(monkeypatch)
    assert ss.get_backend() is not None
    raw = getattr(ss.get_backend(), "_backend", ss.get_backend())
    assert raw is backend, "precondition: the purge is handed THIS instance"
    with pytest.raises(ss.SecretsUnreadableError):
        raw.list_keys()
    return _digest(path)


# ── the unmeasurable inventory ──────────────────────────────────────


def test_an_unreadable_vault_refuses_the_removal(vault_and_tunnels, monkeypatch):
    """Row 520 RED: removal returned True with all 3 secrets still stored."""
    root = vault_and_tunnels
    before = _damage(root, monkeypatch)

    saved = (root / "secrets.json").read_bytes()

    with pytest.raises(ss.SecretsIntegrityError):
        vpn_config.remove_tunnel_config(_T1)

    # Nothing was dropped and nothing was orphaned.
    assert len(vpn_config.load()["tunnels"]) == 1, "exactly 1 tunnel persisted"
    assert _digest(root / "secrets.json") == before, "the vault was not touched"

    # Repair the file: a backend reconstructed over it must still list all 3
    # keys with exactly 2 under the t1: prefix. Before the fix these same 3
    # keys survived while the removal reported success -- orphaned, because the
    # saved config no longer carried the @cred: references to them.
    (root / "secrets.json").write_bytes(_ORIGINAL[0])
    repaired = _reconstruct_backend(monkeypatch)
    keys = sorted(repaired.list_keys())
    assert len(keys) == 3, keys
    assert len([k for k in keys if k.startswith(f"{_T1}:")]) == 2


def test_a_malformed_container_also_refuses_the_removal(
    vault_and_tunnels, monkeypatch
):
    """The second damaged shape reaches the same contract."""
    root = vault_and_tunnels
    path = root / "secrets.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["ciphertexts"] = ["row520-not-a-mapping"]
    path.write_text(json.dumps(blob), encoding="utf-8")
    raw = _reconstruct_backend(monkeypatch)
    with pytest.raises(ss.SecretsIntegrityError):
        raw.list_keys()

    with pytest.raises(ss.SecretsIntegrityError):
        vpn_config.remove_tunnel_config(_T1)

    assert len(vpn_config.load()["tunnels"]) == 1


def test_the_delete_endpoint_carries_the_named_diagnostic(
    vault_and_tunnels, monkeypatch
):
    """The HTTP surface must be able to name unpurgeable, not answer removed."""
    from flask import Flask
    from bulk_downloader import app_vpn_api, vpn

    root = vault_and_tunnels
    _damage(root, monkeypatch)
    monkeypatch.setattr(vpn, "get_tunnel", lambda tid: {"tunnel_id": tid})
    monkeypatch.setattr(vpn, "stop_tunnel", lambda tid: None)
    if hasattr(vpn, "unregister_tunnel"):
        monkeypatch.setattr(vpn, "unregister_tunnel", lambda tid: None)
    if app_vpn_api.vpn_bp is None:
        pytest.skip("vpn blueprint unavailable")
    flask_app = Flask("row520-vpn")
    flask_app.register_blueprint(app_vpn_api.vpn_bp)
    client = flask_app.test_client()

    response = client.delete(f"/api/vpn/tunnels/{_T1}")
    body = response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert "removed" not in body, "a refusal must not report a removal"
    assert body.get("state") == "secrets_unpurgeable"
    assert len(vpn_config.load()["tunnels"]) == 1


# ── negative controls ───────────────────────────────────────────────


def test_a_readable_vault_still_removes_and_purges(vault_and_tunnels, monkeypatch):
    """Negative control: the fix did not refuse every removal."""
    root = vault_and_tunnels

    assert vpn_config.remove_tunnel_config(_T1) is True

    assert len(vpn_config.load()["tunnels"]) == 0
    keys = sorted(ss.get_backend().list_keys())
    assert [k for k in keys if k.startswith(f"{_T1}:")] == [], "t1: fully purged"
    assert keys == [f"{_OTHER_TUNNEL}:password"], (
        "exactly 1 key survives, and it is the unrelated one"
    )
    assert ss.get_backend().get(f"{_OTHER_TUNNEL}:password") == _OTHER


def test_an_absent_tunnel_never_enters_the_purge(vault_and_tunnels, monkeypatch):
    """Negative control: an absent tunnel_id still returns False having entered
    _purge_secrets_for_tunnel exactly 0 times."""
    calls: list[str] = []
    real = vpn_config._purge_secrets_for_tunnel

    def _counting(tunnel_id):
        calls.append(tunnel_id)
        return real(tunnel_id)

    monkeypatch.setattr(vpn_config, "_purge_secrets_for_tunnel", _counting)

    assert vpn_config.remove_tunnel_config("row520-no-such-tunnel") is False

    assert calls == [], "the purge was entered exactly 0 times"
    assert len(vpn_config.load()["tunnels"]) == 1, "the real tunnel is untouched"
    assert len(ss.get_backend().list_keys()) == 3


def test_a_tunnel_with_no_secrets_still_removes(vault_and_tunnels, monkeypatch):
    """Negative control: an empty purge over a READABLE vault is an honest zero."""
    vpn_config.add_tunnel_config({"tunnel_id": "row520empty", "name": "e",
                                  "provider": "generic", "backend": "wireguard"})
    assert len(vpn_config.load()["tunnels"]) == 2
    assert [k for k in ss.get_backend().list_keys()
            if k.startswith("row520empty:")] == [], "precondition: no secrets"

    assert vpn_config.remove_tunnel_config("row520empty") is True

    assert len(vpn_config.load()["tunnels"]) == 1
    assert len(ss.get_backend().list_keys()) == 3, "no other key was disturbed"


# ── A7 self-audit: the sibling reader had the same shape ─────────────


def test_the_r1_orphan_guard_refuses_over_an_unreadable_vault(
    vault_and_tunnels, monkeypatch
):
    """A7 self-audit finding, same contract, one function over.

    ``tunnel_ids_with_secrets`` swallowed the same named refusal into an EMPTY
    SET, and ``_tunnel_id_guard`` reads an empty set as "no tunnel owns
    secrets, nothing to protect" and returns None -- so the single condition
    under which orphaning is most likely also DISABLED the guard that exists
    to prevent it. An advisory refusal is no refusal.
    """
    from bulk_downloader import app_store_raw_editor as raw

    root = vault_and_tunnels
    # Precondition: with the vault readable the guard SEES the owner and blocks
    # a payload that drops it.
    assert vpn_config.tunnel_ids_with_secrets() == {_T1, _OTHER_TUNNEL}
    blocked = raw._tunnel_id_guard({"tunnels": []})
    assert blocked is not None, "precondition: a readable vault blocks the drop"
    assert _T1 in blocked

    # And it passes a payload that keeps every owner.
    kept = raw._tunnel_id_guard(
        {"tunnels": [{"tunnel_id": _T1}, {"tunnel_id": _OTHER_TUNNEL}]}
    )
    assert kept is None, "precondition: a non-orphaning payload is allowed"

    _damage(root, monkeypatch)
    with pytest.raises(ss.SecretsIntegrityError):
        vpn_config.tunnel_ids_with_secrets()

    refused = raw._tunnel_id_guard({"tunnels": []})

    assert refused is not None, (
        "an unmeasurable inventory must not read as 'nothing to protect'"
    )
    assert "UNKNOWN" in refused
    assert "could not be read" in refused


def test_the_r1_guard_still_allows_a_write_over_a_readable_empty_vault(
    monkeypatch, tmp_path
):
    """Negative control: an honest empty inventory still permits the write."""
    from bulk_downloader import app_store_raw_editor as raw

    if not ss._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not available")
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(tmp_path / "tunnels.json"))
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    at.reset()
    vpn_config._reset_for_tests()
    backend = _reconstruct_backend(monkeypatch)
    backend._data["iterations"] = _ITERATIONS
    assert backend.unlock(_MASTER) is True
    assert backend.list_keys() == [], "precondition: a genuinely empty vault"
    assert vpn_config.tunnel_ids_with_secrets() == set()

    assert raw._tunnel_id_guard({"tunnels": []}) is None
    vpn_config._reset_for_tests()
    at.reset()
