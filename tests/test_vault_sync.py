"""tests/test_vault_sync.py -- Verification for Row 893:
SESSION-STATE-ENCRYPTION-AND-CLUSTER-WIDE-SYNCHRONIZATION-VAULTSYNC.

OWNS: bulk_downloader/vault_sync.py, tests/test_vault_sync.py

Acceptance criteria:
(1) symmetric encryption/decryption round-trip across multi-worker environments,
(2) automatic TTL expiration,
(3) zero plaintext token exposure.
"""
from __future__ import annotations

import base64
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Optional

import pytest
from cryptography.exceptions import InvalidTag

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader.vault_sync import (
        SessionCrypto,
        VaultCryptoError,
        VaultSync,
        _redis_command,
        get_vault_sync,
    )
except ImportError:
    # Fallback probe simulating legacy unencrypted session storage on unmodified base
    class InvalidTag(Exception):  # type: ignore[no-redef]
        pass

    class VaultCryptoError(ValueError):  # type: ignore[no-redef]
        pass

    class SessionCrypto:  # type: ignore[no-redef]
        """Base probe simulating legacy unencrypted JSON serialization."""
        def __init__(self, key: bytes | None = None) -> None:
            self.key = key or b"legacy_unencrypted_test_key_0000"

        def encrypt(self, payload: dict, origin_host: str | None = None, target_host: str | None = None, ttl_seconds: float = 3600.0, host_fingerprint: str | None = None) -> str:
            import json
            return json.dumps(payload)

        def decrypt(self, token: str, expected_fingerprint: str | None = None, validate_origin: bool = True, current_host: str | None = None, enforce_ttl: bool = True) -> dict:
            import json
            return json.loads(token)

        def get_host_fingerprint(self, hostname: str | None = None) -> str:
            return "legacy-unauthenticated-host"

    class VaultSync:  # type: ignore[no-redef]
        """Base probe without cryptographic sync or TTL guarantees."""
        def __init__(self, secret_key: bytes, host: str = "127.0.0.1", port: int = 6379, fallback_local: bool = False) -> None:
            self.crypto = SessionCrypto(secret_key)
            self._store: dict[str, str] = {}
            self.fallback_local = fallback_local

        def set_session(self, site_id: str, account_id: str, session_data: dict, ttl_seconds: int = 3600, target_host: str | None = None) -> bool:
            token = self.crypto.encrypt(session_data)
            self._store[f"{site_id}:{account_id}"] = token
            return True

        def get_session(self, site_id: str, account_id: str, validate_fingerprint: bool = False, current_host: str | None = None) -> dict | None:
            raw = self._store.get(f"{site_id}:{account_id}")
            if raw is None:
                return None
            return self.crypto.decrypt(raw)

        def get_ttl(self, site_id: str, account_id: str) -> int:
            return -1

        def invalidate_session(self, site_id: str, account_id: str) -> bool:
            return self._store.pop(f"{site_id}:{account_id}", None) is not None

    def _redis_command(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None

    def get_vault_sync(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None


class MockRespServer:
    """In-process hermetic Redis RESP socket server fixture on dynamic port 0."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(128)
        self.host, self.port = self.sock.getsockname()[:2]
        self._running = True
        self._data: dict[bytes, tuple[Optional[float], bytes]] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                client, _ = self.sock.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
            t.start()

    def _handle_client(self, client: socket.socket) -> None:
        client.settimeout(2.0)
        f = client.makefile("rb")
        try:
            while self._running:
                line = f.readline()
                if not line:
                    break
                if line[0:1] != b"*":
                    continue
                num_args = int(line[1:].strip())
                args: list[bytes] = []
                for _ in range(num_args):
                    header = f.readline()
                    if not header:
                        break
                    length = int(header[1:].strip())
                    arg = f.read(length)
                    f.read(2)  # consume \r\n
                    args.append(arg)
                if len(args) != num_args:
                    break

                cmd = args[0].upper()
                with self._lock:
                    now = time.time()
                    if cmd == b"SET":
                        key = args[1]
                        val = args[2]
                        exp = None
                        if len(args) >= 5 and args[3].upper() == b"EX":
                            exp = now + float(args[4])
                        self._data[key] = (exp, val)
                        client.sendall(b"+OK\r\n")
                    elif cmd == b"GET":
                        key = args[1]
                        if key in self._data:
                            exp, val = self._data[key]
                            if exp is not None and now > exp:
                                del self._data[key]
                                client.sendall(b"$-1\r\n")
                            else:
                                client.sendall(f"${len(val)}\r\n".encode("ascii") + val + b"\r\n")
                        else:
                            client.sendall(b"$-1\r\n")
                    elif cmd == b"TTL":
                        key = args[1]
                        if key in self._data:
                            exp, _ = self._data[key]
                            if exp is not None:
                                rem = exp - now
                                if rem <= 0:
                                    del self._data[key]
                                    client.sendall(b":-2\r\n")
                                else:
                                    client.sendall(f":{max(0, int(rem))}\r\n".encode("ascii"))
                            else:
                                client.sendall(b":-1\r\n")
                        else:
                            client.sendall(b":-2\r\n")
                    elif cmd == b"DEL":
                        key = args[1]
                        if key in self._data:
                            del self._data[key]
                            client.sendall(b":1\r\n")
                        else:
                            client.sendall(b":0\r\n")
                    elif cmd == b"PING":
                        client.sendall(b"+PONG\r\n")
                    else:
                        client.sendall(b"-ERR unknown command\r\n")
        except Exception:
            pass
        finally:
            try:
                f.close()
                client.close()
            except Exception:
                pass

    def close(self) -> None:
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def mock_redis():
    """Hermetic Redis server fixture binding to an ephemeral port on 127.0.0.1."""
    server = MockRespServer()
    yield server
    server.close()


def test_symmetric_encryption_roundtrip():
    """Verify AES-256-GCM symmetric encryption round-trip and payload fidelity."""
    key = os.urandom(32)
    crypto = SessionCrypto(key=key)

    payload = {
        "site_id": "service_omega",
        "account_id": "operator_77",
        "cookies": {"sessionid": "sec_tok_9918234", "secure": "1"},
        "headers": {"Authorization": "Bearer jwt-cluster-token-xyz"},
        "created_at": time.time(),
    }

    # Encrypt
    token = crypto.encrypt(payload)
    assert isinstance(token, str)
    assert len(token) > 0

    # Decrypt
    decrypted = crypto.decrypt(token)
    assert decrypted == payload


def test_zero_plaintext_token_exposure():
    """Verify sensitive cookies and tokens are never exposed in plaintext ciphertext."""
    key = os.urandom(32)
    crypto = SessionCrypto(key=key)

    secret_marker = "sensitive_plaintext_session_secret_998811"
    payload = {
        "site_id": "vault_site",
        "account_id": "worker_node_4",
        "cookies": {"session": secret_marker},
        "token": secret_marker,
    }

    token = crypto.encrypt(payload)
    # Ciphertext / serialized token must NOT expose plaintext token in base64 or decoded form
    assert secret_marker not in token, (
        f"Security violation: plaintext token {secret_marker} exposed in serialized ciphertext"
    )
    raw_payload_bytes = base64.urlsafe_b64decode(token.encode("ascii"))
    assert secret_marker.encode("utf-8") not in raw_payload_bytes, (
        f"Security violation: plaintext token {secret_marker} exposed in raw payload bytes"
    )
    assert not raw_payload_bytes[12:].lstrip().startswith(b"{"), (
        "Security violation: payload ciphertext begins with unencrypted JSON structure"
    )


def test_tamper_detection_invalid_tag():
    """Negative control: ciphertext bit-flip or corrupted authentication tag fails with InvalidTag."""
    key = os.urandom(32)
    crypto = SessionCrypto(key=key)

    payload = {"account": "admin", "role": "root"}
    token = crypto.encrypt(payload)

    # Corrupt a byte in the token payload
    token_bytes = bytearray(token.encode("ascii"))
    token_bytes[15] = (token_bytes[15] ^ 0xFF)
    corrupted_token = token_bytes.decode("ascii", errors="ignore")

    with pytest.raises(InvalidTag):
        crypto.decrypt(corrupted_token)


def test_wrong_key_decryption_refusal():
    """Negative control: decryption with unauthorized secret key fails with InvalidTag."""
    key_alpha = os.urandom(32)
    key_bravo = os.urandom(32)
    crypto_alpha = SessionCrypto(key=key_alpha)
    crypto_bravo = SessionCrypto(key=key_bravo)

    payload = {"data": "confidential_auth_cookie"}
    token = crypto_alpha.encrypt(payload)

    with pytest.raises(InvalidTag):
        crypto_bravo.decrypt(token)


def test_replay_window_expiration():
    """Negative control: token decrypted outside its replay window raises VaultCryptoError."""
    key = os.urandom(32)
    crypto = SessionCrypto(key=key)

    payload = {"session": "short_lived_token"}
    token = crypto.encrypt(payload, ttl_seconds=0.1)

    time.sleep(0.2)
    with pytest.raises(VaultCryptoError):
        crypto.decrypt(token, enforce_ttl=True)


def test_host_fingerprint_origin_and_target_validation():
    """Verify cryptographic host fingerprint verification and target constraints."""
    key = os.urandom(32)
    crypto = SessionCrypto(key=key)

    fp = crypto.get_host_fingerprint("worker-a")
    assert isinstance(fp, str)
    assert len(fp) == 32

    payload = {"account": "worker_user"}

    # 1. Successful decryption across simulated hosts with valid origin signature
    token = crypto.encrypt(payload, origin_host="worker-a")
    decrypted = crypto.decrypt(token, validate_origin=True)
    assert decrypted == payload

    # 2. Explicit expected fingerprint mismatch raises ValueError
    token_explicit = crypto.encrypt(payload, host_fingerprint="auth-fp-123456789012345678901234")
    with pytest.raises(ValueError, match="host fingerprint mismatch"):
        crypto.decrypt(token_explicit, expected_fingerprint="unauth-fp-999999999999999999999999")

    # 3. Target host constraint
    token_targeted = crypto.encrypt(payload, target_host="worker-node-target")
    with pytest.raises(VaultCryptoError, match="token targeted to host"):
        crypto.decrypt(token_targeted, current_host="worker-node-wrong")

    decrypted_target = crypto.decrypt(token_targeted, current_host="worker-node-target")
    assert decrypted_target == payload


def _worker_producer(
    site_id: str,
    account_id: str,
    secret_key: bytes,
    session_data: dict,
    host: str,
    port: int,
    res_q: multiprocessing.Queue,
) -> None:
    """Simulate cluster producer node synchronizing session state to Redis."""
    try:
        sync = VaultSync(secret_key=secret_key, host=host, port=port)
        ok = sync.set_session(site_id, account_id, session_data, ttl_seconds=30)
        res_q.put(("producer", ok, sync.crypto.get_host_fingerprint()))
    except (OSError, RuntimeError, ValueError) as exc:
        res_q.put(("producer_err", str(exc), ""))


def _worker_consumer(
    site_id: str,
    account_id: str,
    secret_key: bytes,
    host: str,
    port: int,
    res_q: multiprocessing.Queue,
) -> None:
    """Simulate cluster consumer worker retrieving session state from Redis."""
    try:
        sync = VaultSync(secret_key=secret_key, host=host, port=port)
        data = sync.get_session(site_id, account_id)
        res_q.put(("consumer", data, sync.crypto.get_host_fingerprint()))
    except (OSError, RuntimeError, ValueError) as exc:
        res_q.put(("consumer_err", str(exc), ""))


def test_multiprocess_cluster_sync_across_workers(mock_redis):
    """Verify multi-process session synchronization and handover across simulated worker nodes."""
    secret_key = os.urandom(32)
    site_id = f"site_cluster_{int(time.time() * 1000)}"
    account_id = "worker_peer_893"
    session_data = {
        "cookies": {"session": "cluster_auth_cookie_token_456"},
        "csrf": "csrf_guard_789",
        "peer_id": "peer_a",
    }

    q: multiprocessing.Queue = multiprocessing.Queue()

    # Worker A produces session
    p1 = multiprocessing.Process(
        target=_worker_producer,
        args=(site_id, account_id, secret_key, session_data, mock_redis.host, mock_redis.port, q),
    )
    p1.start()
    p1.join(timeout=5)
    assert not p1.is_alive(), "Producer process did not terminate within timeout"

    status, ok, prod_fp = q.get(timeout=2)
    assert status == "producer"
    assert ok is True
    assert len(prod_fp) == 32

    # Worker B consumes session
    p2 = multiprocessing.Process(
        target=_worker_consumer,
        args=(site_id, account_id, secret_key, mock_redis.host, mock_redis.port, q),
    )
    p2.start()
    p2.join(timeout=5)
    assert not p2.is_alive(), "Consumer process did not terminate within timeout"

    status, retrieved, cons_fp = q.get(timeout=2)
    assert status == "consumer"
    assert retrieved == session_data
    assert len(cons_fp) == 32

    # Cleanup
    sync = VaultSync(secret_key=secret_key, host=mock_redis.host, port=mock_redis.port)
    assert sync.invalidate_session(site_id, account_id) is True


def test_automatic_ttl_expiration(mock_redis):
    """Verify automatic TTL expiration evicts stale session states."""
    secret_key = os.urandom(32)
    sync = VaultSync(secret_key=secret_key, host=mock_redis.host, port=mock_redis.port)

    site_id = f"site_ttl_{int(time.time() * 1000)}"
    account_id = "ttl_user_01"
    session_data = {"auth_token": "ephemeral_secret"}

    # Set 1-second TTL
    ok = sync.set_session(site_id, account_id, session_data, ttl_seconds=1)
    assert ok is True

    # Immediate query succeeds and TTL is active
    cached = sync.get_session(site_id, account_id)
    assert cached == session_data

    ttl = sync.get_ttl(site_id, account_id)
    assert 0 <= ttl <= 2

    # Wait for TTL expiration
    time.sleep(1.4)

    # Retrieval after expiration returns None
    expired = sync.get_session(site_id, account_id)
    assert expired is None, f"Expected session to expire after TTL, got {expired}"
    assert sync.get_ttl(site_id, account_id) == -2


def test_offline_redis_fallback_and_fail_soft_correction():
    """Verify offline Redis handling: returns False when fallback_local=False, caches when True."""
    secret_key = os.urandom(32)
    # Closed port
    sync_strict = VaultSync(secret_key=secret_key, host="127.0.0.1", port=59882, fallback_local=False)
    site_id = "site_offline"
    account_id = "offline_user"
    session_data = {"fallback": "active"}

    # With fallback_local=False, set_session must fail honestly (return False)
    assert sync_strict.set_session(site_id, account_id, session_data, ttl_seconds=10) is False
    assert sync_strict.get_session(site_id, account_id) is None
    # Invalidate non-existent key returns False
    assert sync_strict.invalidate_session(site_id, account_id) is False

    # With fallback_local=True, set_session falls back to in-memory store
    sync_fallback = VaultSync(secret_key=secret_key, host="127.0.0.1", port=59882, fallback_local=True)
    ok = sync_fallback.set_session(site_id, account_id, session_data, ttl_seconds=10)
    assert ok is True
    retrieved = sync_fallback.get_session(site_id, account_id)
    assert retrieved == session_data
    ttl = sync_fallback.get_ttl(site_id, account_id)
    assert 0 <= ttl <= 10
    assert sync_fallback.invalidate_session(site_id, account_id) is True
    assert sync_fallback.invalidate_session(site_id, account_id) is False


def test_production_wiring_session_keeper_and_cookies(tmp_path, monkeypatch, mock_redis):
    """Verify production callers in session_keeper and cookies interact with VaultSync."""
    from bulk_downloader.cookies import load_cookies_from_file, save_cookies_to_file
    from bulk_downloader.session_keeper import SessionKeeper

    # Point VaultSync default host/port to mock_redis
    monkeypatch.setenv("BD_REDIS_HOST", mock_redis.host)
    monkeypatch.setenv("BD_REDIS_PORT", str(mock_redis.port))
    monkeypatch.setenv("BD_VAULT_KEY", "test-cluster-vault-key-32bytes!")

    # Reset global singleton to pick up env
    import bulk_downloader.vault_sync as vs_mod
    vs_mod._VAULT_SYNC_INSTANCE = None

    site_id = f"test_prod_{int(time.time() * 1000)}"
    cookie_file = tmp_path / f"{site_id}.json"
    test_cookies = [
        {"name": "sid", "value": "secret_session_token_123", "domain": ".example.com", "path": "/"}
    ]

    # 1. save_cookies_to_file syncs to VaultSync
    saved_path = save_cookies_to_file(cookie_file, test_cookies, validate=True)
    assert saved_path.exists()

    vs = get_vault_sync()
    assert vs is not None
    vault_data = vs.get_session(site_id, "0")
    assert vault_data is not None
    assert vault_data.get("cookies") == test_cookies

    # 2. load_cookies_from_file falls back to VaultSync when file is deleted
    cookie_file.unlink()
    assert not cookie_file.exists()
    loaded_cookies = load_cookies_from_file(cookie_file)
    assert len(loaded_cookies) == 1
    assert loaded_cookies[0]["name"] == "sid"
    assert loaded_cookies[0]["value"] == "secret_session_token_123"

    # 3. session_keeper._persist_cookies and _load_cookies_as_playwright
    keeper = SessionKeeper(
        site_id=site_id,
        account_idx=0,
        site_config={"login_url": "https://example.com/login"},
        do_login_callback=lambda *a: None,
    )
    
    # Simulate Playwright context
    class DummyContext:
        def cookies(self):
            return [{"name": "keeper_token", "value": "refreshed_val", "domain": ".example.com", "path": "/"}]

    keeper._ctx = DummyContext()
    # Monkeypatch cookies path to tmp_path
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cookies").mkdir(exist_ok=True)

    keeper._persist_cookies()
    refreshed_vault = vs.get_session(site_id, "0")
    assert refreshed_vault is not None
    assert refreshed_vault["cookies"][0]["name"] == "keeper_token"

    # Profile seeding via _load_cookies_as_playwright loads directly from VaultSync
    # Even if disk file is removed:
    (tmp_path / "cookies" / f"{site_id}.json").unlink()
    seeded = keeper._load_cookies_as_playwright()
    assert len(seeded) == 1
    assert seeded[0]["name"] == "keeper_token"
