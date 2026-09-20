"""bulk_downloader/vault_sync.py -- SESSION-STATE-ENCRYPTION-AND-CLUSTER-WIDE-SYNCHRONIZATION-VAULTSYNC.

Row 893 (T2):
Encrypts session cookies and state using AES-256-GCM and synchronizes state via
Redis with cryptographic host fingerprint validation and replay protection.
Enables seamless task handover across multi-worker environments without repeated
authentication. Zero site logins touched (Rule 21).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultCryptoError(ValueError):
    """Raised when cryptographic verification, replay check, or host fingerprint check fails."""


class SessionCrypto:
    """Symmetric session payload encryption and host fingerprint validation using AES-256-GCM."""

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            key = os.urandom(32)
        if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
            raise ValueError("key must be exactly 32 bytes (256 bits)")
        self.key: bytes = bytes(key)
        self._aesgcm: AESGCM = AESGCM(self.key)

    def get_host_fingerprint(self, hostname: str | None = None) -> str:
        """Derive a deterministic 32-character host fingerprint bound to the shared key."""
        host_ident = (hostname or socket.gethostname()).encode("utf-8")
        return hmac.new(self.key, host_ident, hashlib.sha256).hexdigest()[:32]

    def encrypt(
        self,
        payload: dict[str, Any],
        origin_host: str | None = None,
        target_host: str | None = None,
        ttl_seconds: float = 3600.0,
        host_fingerprint: str | None = None,
    ) -> str:
        """Encrypt payload dictionary into an authenticated URL-safe base64 token with envelope metadata."""
        orig = origin_host or socket.gethostname()
        fp = host_fingerprint if host_fingerprint is not None else self.get_host_fingerprint(orig)
        envelope = {
            "payload": payload,
            "origin": orig,
            "fingerprint": fp,
            "target": target_host,
            "ts": time.time(),
            "ttl": float(ttl_seconds),
        }
        envelope_bytes = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)  # 96-bit fresh nonce for AES-GCM
        ciphertext = self._aesgcm.encrypt(nonce, envelope_bytes, None)
        # Token format: base64(nonce [12 bytes] + ciphertext_with_tag [N + 16 bytes])
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(
        self,
        token: str,
        expected_fingerprint: str | None = None,
        validate_origin: bool = True,
        current_host: str | None = None,
        enforce_ttl: bool = True,
    ) -> dict[str, Any]:
        """Decrypt token and verify integrity, authentication tag, host fingerprint, and replay window."""
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
        except Exception as exc:
            raise InvalidTag(f"Malformed token encoding: {exc}") from exc

        if len(raw) < 28:  # 12-byte nonce + minimum 16-byte authentication tag
            raise InvalidTag("Token too short to contain valid nonce and authentication tag")

        nonce = raw[:12]
        ciphertext = raw[12:]

        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        try:
            envelope = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise InvalidTag(f"Malformed envelope payload: {exc}") from exc

        if not isinstance(envelope, dict):
            raise InvalidTag("Decrypted envelope is not a dictionary")

        # 1. Replay window / TTL expiration validation
        if enforce_ttl and "ts" in envelope and "ttl" in envelope:
            now = time.time()
            ts = float(envelope["ts"])
            ttl = float(envelope["ttl"])
            if now > ts + ttl:
                raise VaultCryptoError(
                    f"Session token expired: created at {ts}, ttl {ttl}s, age {now - ts:.1f}s"
                )

        # 2. Host fingerprint validation
        if expected_fingerprint is not None:
            actual_fp = envelope.get("fingerprint")
            if actual_fp != expected_fingerprint:
                raise VaultCryptoError(
                    f"host fingerprint mismatch: expected {expected_fingerprint}, got {actual_fp}"
                )
        elif validate_origin and "origin" in envelope and "fingerprint" in envelope:
            # Cryptographic validation of origin host signature using shared key
            expected_orig_fp = self.get_host_fingerprint(str(envelope["origin"]))
            if envelope["fingerprint"] != expected_orig_fp:
                raise VaultCryptoError(
                    f"host fingerprint mismatch: origin signature invalid for host {envelope['origin']}"
                )

        # 3. Target host constraint verification (if token was restricted to a specific host)
        target = envelope.get("target")
        if target is not None:
            curr = current_host or socket.gethostname()
            if target != curr:
                raise VaultCryptoError(
                    f"host fingerprint mismatch: token targeted to host {target}, received on {curr}"
                )

        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise InvalidTag("Decrypted payload is not a dictionary")
        return payload


def _redis_command(
    host: str,
    port: int,
    cmd_args: list[str | bytes],
    timeout: float = 2.0,
) -> bytes | None:
    """Execute a single Redis command via RESP protocol over a raw socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        buf = [f"*{len(cmd_args)}\r\n".encode("ascii")]
        for arg in cmd_args:
            b = arg.encode("utf-8") if isinstance(arg, str) else bytes(arg)
            buf.append(f"${len(b)}\r\n".encode("ascii"))
            buf.append(b)
            buf.append(b"\r\n")
        s.sendall(b"".join(buf))

        line = bytearray()
        while not line.endswith(b"\r\n"):
            chunk = s.recv(1)
            if not chunk:
                break
            line.extend(chunk)

        if not line:
            return None

        rtype = chr(line[0])
        rest = line[1:-2]

        if rtype in ("+", ":"):
            return bytes(rest)
        elif rtype == "-":
            raise RuntimeError(f"Redis error: {rest.decode('utf-8', errors='replace')}")
        elif rtype == "$":
            length = int(rest)
            if length == -1:
                return None
            body = bytearray()
            while len(body) < length + 2:
                chunk = s.recv(min(4096, (length + 2) - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            return bytes(body[:length])
        else:
            return bytes(rest)
    finally:
        s.close()


class VaultSync:
    """Cluster-wide session state synchronization backed by Redis and AES-256-GCM."""

    def __init__(
        self,
        secret_key: bytes,
        host: str = "127.0.0.1",
        port: int = 6379,
        fallback_local: bool = False,
    ) -> None:
        self.crypto: SessionCrypto = SessionCrypto(secret_key)
        self.host: str = host
        self.port: int = port
        self.fallback_local: bool = fallback_local
        self._local_cache: dict[str, tuple[float, str]] = {}

    def _cache_key(self, site_id: str, account_id: str) -> str:
        return f"bd:vaultsync:{site_id}:{account_id}"

    def set_session(
        self,
        site_id: str,
        account_id: str,
        session_data: dict[str, Any],
        ttl_seconds: int = 3600,
        target_host: str | None = None,
    ) -> bool:
        """Encrypt and store session state in distributed cache with specified TTL."""
        token = self.crypto.encrypt(session_data, target_host=target_host, ttl_seconds=float(ttl_seconds))
        key = self._cache_key(site_id, account_id)

        saved_redis = False
        try:
            res = _redis_command(
                self.host,
                self.port,
                ["SET", key, token, "EX", str(ttl_seconds)],
            )
            saved_redis = res is not None
        except (OSError, RuntimeError):
            if not self.fallback_local:
                return False
            saved_redis = False

        if not saved_redis and self.fallback_local:
            self._local_cache[key] = (time.time() + ttl_seconds, token)
            return True
        return saved_redis

    def get_session(
        self,
        site_id: str,
        account_id: str,
        validate_fingerprint: bool = False,
        current_host: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve and decrypt session state from distributed cache."""
        key = self._cache_key(site_id, account_id)
        fp = self.crypto.get_host_fingerprint() if validate_fingerprint else None

        try:
            raw = _redis_command(self.host, self.port, ["GET", key])
            if raw is not None:
                decrypted = self.crypto.decrypt(
                    raw.decode("ascii"),
                    expected_fingerprint=fp,
                    current_host=current_host,
                )
                if isinstance(decrypted, dict):
                    return decrypted
                return None
        except (OSError, RuntimeError):
            if not self.fallback_local:
                return None

        if key in self._local_cache:
            exp, token = self._local_cache[key]
            if time.time() < exp:
                try:
                    decrypted = self.crypto.decrypt(
                        token,
                        expected_fingerprint=fp,
                        current_host=current_host,
                    )
                    if isinstance(decrypted, dict):
                        return decrypted
                except Exception:
                    pass
                return None
            del self._local_cache[key]
        return None

    def get_ttl(self, site_id: str, account_id: str) -> int:
        """Return remaining TTL seconds for the session key, or -2 if missing/expired."""
        key = self._cache_key(site_id, account_id)
        ttl_val = -2
        try:
            res = _redis_command(self.host, self.port, ["TTL", key])
            if res is not None:
                ttl_val = int(res.decode("ascii"))
        except (OSError, RuntimeError):
            ttl_val = -2

        if ttl_val >= 0:
            return ttl_val

        if key in self._local_cache:
            exp, _ = self._local_cache[key]
            rem = exp - time.time()
            if rem > 0:
                return max(0, int(rem))
            del self._local_cache[key]
            return -2
        return ttl_val

    def invalidate_session(self, site_id: str, account_id: str) -> bool:
        """Evict session data from distributed and local caches."""
        key = self._cache_key(site_id, account_id)
        del_redis = False
        try:
            res = _redis_command(self.host, self.port, ["DEL", key])
            del_redis = res is not None and res != b"0"
        except (OSError, RuntimeError):
            del_redis = False
        del_local = self._local_cache.pop(key, None) is not None
        return del_redis or del_local


_VAULT_SYNC_INSTANCE: VaultSync | None = None


def get_vault_sync(
    secret_key: bytes | None = None,
    host: str | None = None,
    port: int | None = None,
    fallback_local: bool = True,
) -> VaultSync | None:
    """Retrieve or initialize the process-level VaultSync singleton."""
    global _VAULT_SYNC_INSTANCE
    if _VAULT_SYNC_INSTANCE is not None and secret_key is None and host is None and port is None:
        return _VAULT_SYNC_INSTANCE

    resolved_key = secret_key
    if resolved_key is None:
        env_key = os.environ.get("BD_VAULT_KEY")
        if env_key:
            resolved_key = hashlib.sha256(env_key.encode("utf-8")).digest()
        else:
            # Check config/.vault.key or machine identity
            key_file = Path("config") / ".vault.key"
            if key_file.is_file():
                try:
                    raw = key_file.read_bytes().strip()
                    if len(raw) == 32:
                        resolved_key = raw
                    else:
                        resolved_key = hashlib.sha256(raw).digest()
                except OSError:
                    pass
            if resolved_key is None:
                # Deterministic cluster-wide key fallback when no operator key is supplied
                ident = "bd-vault-cluster-shared-seed"
                resolved_key = hashlib.sha256(ident.encode("utf-8")).digest()

    resolved_host = host or os.environ.get("BD_REDIS_HOST", "127.0.0.1")
    resolved_port = int(port or os.environ.get("BD_REDIS_PORT", "6379"))

    _VAULT_SYNC_INSTANCE = VaultSync(
        secret_key=resolved_key,
        host=resolved_host,
        port=resolved_port,
        fallback_local=fallback_local,
    )
    return _VAULT_SYNC_INSTANCE
