"""Row 368: a restart-locked vault is not a missing credential.

The master-password key is process memory.  These tests build a real encrypted
vault, prove the exact configured-reference denominator, and then exercise the
same subject in three states: unlocked and resolving, locked after the key is
forgotten, and unlocked with a genuinely absent referenced credential.
"""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import time

from flask import Flask
import pytest

from bulk_downloader import app_health
from bulk_downloader import secrets_store as ss
from bulk_downloader.login_impl import manual
from bulk_downloader.login_impl import submit


BD_GATE_SCOPE = "module"


_MASTER_PASSWORD = "row368-synthetic-master-password"
_KEY_A = "bulkdl-site-row368-a"
_KEY_B = "bulkdl-site-row368-b-account-0"
_MISSING_KEY = "bulkdl-site-row368-genuinely-missing"
_VALUE_A = "row368-synthetic-value-a"
_VALUE_B = "row368-synthetic-value-b"


@pytest.fixture
def vault_subject(monkeypatch, tmp_path):
    """A real two-secret vault and two independently enumerated references."""
    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)

    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert backend.name == "master_password"
    # Keep the production algorithm and real AES-GCM path while making this
    # synthetic fixture cheap enough to construct once per test.
    backend._data["iterations"] = 1_000
    assert backend.unlock(_MASTER_PASSWORD) is True
    backend.set(_KEY_A, _VALUE_A)
    backend.set(_KEY_B, _VALUE_B)

    sites = {
        "row368-a": {
            "name": "Row 368 A",
            "password": f"@cred:{_KEY_A}",
        },
        "row368-b": {
            "name": "Row 368 B",
            "password": "",
            "accounts": [{"password": f"@cred:{_KEY_B}"}],
        },
    }

    # PRECONDITION: this is exactly the nonzero shape the verdict judges.  The
    # second reference is in accounts[], so a top-level-only scan cannot pass.
    configured_refs = [
        sites["row368-a"]["password"],
        sites["row368-b"]["accounts"][0]["password"],
    ]
    assert configured_refs == [f"@cred:{_KEY_A}", f"@cred:{_KEY_B}"]
    assert len(configured_refs) == 2
    assert backend.list_keys() == [_KEY_A, _KEY_B]
    assert len(backend.list_keys()) == 2

    @contextmanager
    def memory_db():
        connection = sqlite3.connect(":memory:")
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(app_health, "db_conn", memory_db)
    monkeypatch.setattr(app_health, "_app_runners", lambda: {})
    monkeypatch.setattr(app_health, "_app_s_cfg", lambda: sites)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health.api_health_v2,
        "_ollama_cache",
        (
            time.time(),
            {"reachable": False, "model": None, "error": "test-disabled"},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        app_health,
        "build_identity",
        lambda _install_dir: {"sha": None, "built_at": None, "source": "unknown"},
    )

    flask_app = Flask("row368-vault-health")
    flask_app.register_blueprint(app_health.health_bp)
    yield backend, sites, flask_app.test_client()


def _expected_health(*, state, ok, resolved, missing, unavailable):
    return {
        "backend": "master_password",
        "is_initialized": True,
        "is_unlocked": state != "locked",
        "missing_count": missing,
        "ok": ok,
        "reference_count": 2,
        "resolved_count": resolved,
        "state": state,
        "stored_count": 2,
        "unavailable_count": unavailable,
    }


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_unlocked_health_proves_two_references_actually_resolve(vault_subject, path):
    backend, sites, client = vault_subject
    refs = [
        sites["row368-a"]["password"],
        sites["row368-b"]["accounts"][0]["password"],
    ]

    # PRECONDITION: unlocked is not inferred from a flag alone; both real
    # ciphertexts are decrypted before the health verdict is asserted.
    assert backend.is_unlocked() is True
    assert [ss.resolve_password(ref) for ref in refs] == [_VALUE_A, _VALUE_B]
    assert len([ref for ref in refs if ss.resolve_password(ref) is not None]) == 2

    response = client.get(path)
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["credentials"] == _expected_health(
        state="unlocked", ok=True, resolved=2, missing=0, unavailable=0
    )


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_initialized_locked_vault_is_a_distinct_failing_health_state(
    vault_subject, path
):
    backend, sites, client = vault_subject
    backend.lock()
    refs = [
        sites["row368-a"]["password"],
        sites["row368-b"]["accounts"][0]["password"],
    ]

    # PRECONDITION: two ciphertexts and two references still exist after the
    # process key is forgotten.  This is a lock, not credential deletion.
    assert backend.is_unlocked() is False
    assert backend.list_keys() == [_KEY_A, _KEY_B]
    assert len(refs) == 2
    assert [ss.resolve_password(ref) for ref in refs] == [None, None]

    response = client.get(path)
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_vault_locked"
    assert body["credentials"] == _expected_health(
        state="locked", ok=False, resolved=0, missing=0, unavailable=2
    )


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_unlocked_missing_reference_is_the_negative_control(vault_subject, path):
    backend, sites, client = vault_subject
    sites["row368-b"]["accounts"][0]["password"] = f"@cred:{_MISSING_KEY}"

    # NEGATIVE CONTROL PRECONDITION: the vault is unlocked and one of the two
    # configured references resolves.  Only the other label is genuinely absent.
    assert backend.is_unlocked() is True
    assert backend.list_keys() == [_KEY_A, _KEY_B]
    assert len(backend.list_keys()) == 2
    assert ss.resolve_password(sites["row368-a"]["password"]) == _VALUE_A
    assert ss.resolve_password(f"@cred:{_MISSING_KEY}") is None

    response = client.get(path)
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_missing"
    assert body["credentials"] == _expected_health(
        state="missing_credentials", ok=False, resolved=1, missing=1, unavailable=0
    )


def test_unavailable_vault_measurement_is_unknown_never_ok(
    vault_subject, monkeypatch
):
    backend, sites, client = vault_subject
    real_list_keys = backend.list_keys
    list_attempts = 0

    # PRECONDITION: both configured references resolve before the measurement
    # seam is made unavailable.  The fixture is healthy; only observability is
    # removed for this verdict.
    refs = [
        sites["row368-a"]["password"],
        sites["row368-b"]["accounts"][0]["password"],
    ]
    assert real_list_keys() == [_KEY_A, _KEY_B]
    assert [ss.resolve_password(ref) for ref in refs] == [_VALUE_A, _VALUE_B]
    assert len(refs) == 2

    def unavailable_list_keys():
        nonlocal list_attempts
        list_attempts += 1
        raise OSError("synthetic vault enumeration unavailable")

    monkeypatch.setattr(backend, "list_keys", unavailable_list_keys)

    response = client.get("/api/health")
    body = response.get_json()
    assert response.status_code == 503, body
    assert body["degraded"] == "credential_state_unknown"
    assert body["credentials"] == {
        "backend": "master_password",
        "is_initialized": True,
        "is_unlocked": True,
        "missing_count": None,
        "ok": False,
        "reference_count": 2,
        "resolved_count": None,
        "state": "unknown",
        "stored_count": None,
        "unavailable_count": None,
    }
    assert list_attempts == 1


def test_login_locked_diagnostic_does_not_blame_a_historical_missing_password(
    vault_subject, capsys
):
    backend, sites, _client = vault_subject
    backend.lock()
    cfg = {
        "name": "Row 368 A",
        "login_url": "https://row368.invalid/login",
        "username": "synthetic-user",
        "password": sites["row368-a"]["password"],
    }

    # PRECONDITION: exactly one configured reference names an extant ciphertext,
    # but the process has no derived key after the simulated restart.
    assert cfg["password"] == f"@cred:{_KEY_A}"
    assert len([cfg["password"]]) == 1
    assert backend.list_keys().count(_KEY_A) == 1
    assert backend.is_unlocked() is False

    ok, diagnostic, cookies = submit.do_login(cfg)
    stderr = capsys.readouterr().err
    locked_lines = [line for line in stderr.splitlines() if "vault is LOCKED" in line]
    assert len(locked_lines) == 1, stderr
    assert "Settings -> Secrets" in locked_lines[0]
    assert "pre-v3.43.14" not in stderr
    assert (ok, diagnostic, cookies) == (
        False,
        "Credential vault locked: password",
        [],
    )


def test_login_genuinely_missing_reference_is_not_called_locked(
    vault_subject, capsys
):
    backend, _sites, _client = vault_subject
    cfg = {
        "name": "Row 368 missing control",
        "login_url": "https://row368.invalid/login",
        "username": "synthetic-user",
        "password": f"@cred:{_MISSING_KEY}",
    }

    # NEGATIVE CONTROL PRECONDITION: decryption is available, but exactly one
    # referenced label is absent from an otherwise nonempty vault.
    assert backend.is_unlocked() is True
    assert len(backend.list_keys()) == 2
    assert backend.list_keys().count(_MISSING_KEY) == 0
    assert ss.resolve_password(cfg["password"]) is None

    ok, diagnostic, cookies = submit.do_login(cfg)
    stderr = capsys.readouterr().err
    missing_lines = [
        line for line in stderr.splitlines() if "stored credential is MISSING" in line
    ]
    locked_lines = [line for line in stderr.splitlines() if "vault is LOCKED" in line]
    assert len(missing_lines) == 1, stderr
    assert len(locked_lines) == 0, stderr
    assert (ok, diagnostic, cookies) == (
        False,
        "Stored credential missing: password",
        [],
    )


def test_manual_login_does_not_claim_password_autofill_while_vault_is_locked(
    vault_subject, monkeypatch, capsys
):
    backend, sites, _client = vault_subject
    backend.lock()
    cfg = {
        "name": "Row 368 A",
        "login_url": "https://row368.invalid/login",
        "username": "synthetic-user",
        "password": sites["row368-a"]["password"],
        "use_real_chrome": False,
        "use_stealth": False,
    }

    # PRECONDITION: the password reference names exactly one stored ciphertext,
    # and only the process-local derived key has been discarded.
    assert cfg["password"] == f"@cred:{_KEY_A}"
    assert backend.list_keys().count(_KEY_A) == 1
    assert len(backend.list_keys()) == 2
    assert backend.is_unlocked() is False

    class FakePage:
        def __init__(self):
            self.evaluated = []

        def goto(self, *_args, **_kwargs):
            return None

        def evaluate(self, script):
            self.evaluated.append(script)
            return None

    page = FakePage()

    class FakeContext:
        pages = []

        def add_init_script(self, _script):
            return None

        def new_page(self):
            return page

    context = FakeContext()

    class FakeBrowser:
        def new_context(self, **_kwargs):
            return context

    from bulk_downloader import cloak

    monkeypatch.setattr(
        cloak,
        "launch_browser",
        lambda **_kwargs: (FakeBrowser(), object(), "row368-fake-browser"),
    )
    monkeypatch.setattr(cloak, "log_choice", lambda *_args, **_kwargs: None)

    session = object.__new__(manual.ManualLoginSession)
    session._config = cfg
    session._banner_js = "row368-banner"
    session._manual_profile_dir = None
    session._headless = True
    session._launch()

    stderr = capsys.readouterr().err
    locked_lines = [line for line in stderr.splitlines() if "vault is LOCKED" in line]
    autofilled_lines = [
        line for line in stderr.splitlines() if "autofilled credentials" in line
    ]
    assert len(locked_lines) == 1, stderr
    assert "Settings -> Secrets" in locked_lines[0]
    assert len(autofilled_lines) == 0, stderr
