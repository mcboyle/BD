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
import logging
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_log = logging.getLogger(__name__)

# O1876-4: the key every default install used before the per-install key file.
# Public, so it is only ever a DECRYPT fallback for entries written under it.
LEGACY_SEED_KEY: bytes = hashlib.sha256(b"bd-vault-cluster-shared-seed").digest()
# Per-install key, cwd-relative like config/.vault.key; state/ is gitignored.
INSTALL_KEY_FILE: Path = Path("state") / "vault.key"
# Legacy backups expire after 72 h (operator O1895).
LEGACY_BACKUP_TTL_SECONDS: int = 259200
# O1876-4: one atomic compare-and-set. KEYS[1] entry, KEYS[2] backup, ARGV[1]
# the legacy token read, ARGV[2] its re-encryption, ARGV[3] the backup TTL.
# Backup first; the entry keeps its remaining TTL. An entry no longer holding
# ARGV[1] is left alone.
_MIGRATE_LEGACY_LUA: str = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end "
    "local ttl = redis.call('PTTL', KEYS[1]) "
    "if ttl ~= -1 and ttl <= 0 then return 0 end "
    "redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3]) "
    "if ttl == -1 then redis.call('SET', KEYS[1], ARGV[2]) "
    "else redis.call('SET', KEYS[1], ARGV[2], 'PX', ttl) end "
    "return 1"
)


class VaultCryptoError(ValueError):
    """Raised when cryptographic verification, replay check, or host fingerprint check fails."""


class VaultKeyFileError(RuntimeError):
    """The per-install vault key file is unusable (loose permissions or wrong length)."""


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
        return self.decrypt_envelope(
            token, expected_fingerprint, validate_origin, current_host, enforce_ttl)["payload"]

    def decrypt_envelope(
        self,
        token: str,
        expected_fingerprint: str | None = None,
        validate_origin: bool = True,
        current_host: str | None = None,
        enforce_ttl: bool = True,
    ) -> dict[str, Any]:
        """decrypt(), returning the whole verified envelope (payload, target, ts, ttl)."""
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
        return envelope


def _redis_command(
    host: str,
    port: int,
    cmd_args: list[str | bytes],
    timeout: float = 2.0,
    require_reply: bool = False,
) -> bytes | None:
    """Execute a single Redis command via RESP protocol over a raw socket.

    ``require_reply``: a connection closed with no reply raises instead of
    reading as a nil reply (dl95-hqporner-1: VaultSync.lookup_session)."""
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
            if require_reply:
                raise ConnectionError("Redis closed the connection without a reply")
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
        explicit_endpoint: bool = True,
        legacy_key: bytes | None = None,
    ) -> None:
        self.crypto: SessionCrypto = SessionCrypto(secret_key)
        # O1876-4: decrypt-only fallback for entries written under the old
        # constant-seed key; a hit is backed up and re-encrypted (_migrate_legacy).
        self.legacy_crypto: SessionCrypto | None = (
            SessionCrypto(legacy_key) if legacy_key is not None else None)
        self.host: str = host
        self.port: int = port
        self.fallback_local: bool = fallback_local
        # False only when get_vault_sync fell back to the default endpoint
        # because no Redis host/port was configured (see lookup_session).
        self.explicit_endpoint: bool = explicit_endpoint
        self._local_cache: dict[str, tuple[float, str]] = {}
        # FIX-R-163: every fallback-cache write or eviction holds this lock, so a
        # local migration's compare..store is one step against all of them.
        self._local_lock = threading.Lock()

    def _cache_key(self, site_id: str, account_id: str) -> str:
        return f"bd:vaultsync:{site_id}:{account_id}"

    def _backup_key(self, site_id: str, account_id: str) -> str:
        return f"bd:vaultsync-legacy-backup:{site_id}:{account_id}"

    def _open_token(
        self,
        site_id: str,
        account_id: str,
        token: str,
        validate_fingerprint: bool = False,
        current_host: str | None = None,
        local_exp: float | None = None,
    ) -> dict[str, Any]:
        """Decrypt with the install key; an entry that only the legacy key opens
        is migrated, then returned. Anything else raises as decrypt() does."""
        fp = self.crypto.get_host_fingerprint() if validate_fingerprint else None
        try:
            return self.crypto.decrypt(token, expected_fingerprint=fp, current_host=current_host)
        except InvalidTag:
            if self.legacy_crypto is None:
                _log_key_mismatch(site_id, account_id)
                raise
        legacy = self.legacy_crypto
        legacy_fp = legacy.get_host_fingerprint() if validate_fingerprint else None
        try:
            envelope = legacy.decrypt_envelope(
                token, expected_fingerprint=legacy_fp, current_host=current_host)
        except InvalidTag:
            _log_key_mismatch(site_id, account_id)
            raise
        self._migrate_legacy(site_id, account_id, token, envelope, local_exp)
        return envelope["payload"]

    def _migrate_legacy(
        self,
        site_id: str,
        account_id: str,
        token: str,
        envelope: dict[str, Any],
        local_exp: float | None,
    ) -> None:
        """Back up the legacy token (72 h TTL), then store the entry re-encrypted
        under the install key with its remaining TTL. Only an entry that still holds the
        token read is replaced: a concurrent set_session wins. A failed backup
        leaves the entry as is."""
        key = self._cache_key(site_id, account_id)
        backup = self._backup_key(site_id, account_id)
        remaining = float(envelope.get("ts", time.time())) + float(envelope.get("ttl", 3600.0)) - time.time()
        new_token = self.crypto.encrypt(
            envelope["payload"], target_host=envelope.get("target"), ttl_seconds=max(1.0, remaining))
        if local_exp is not None:
            with self._local_lock:
                if self._local_cache.get(key) != (local_exp, token):
                    return  # replaced or evicted since the read
                self._local_cache[backup] = (time.time() + LEGACY_BACKUP_TTL_SECONDS, token)
                self._local_cache[key] = (local_exp, new_token)
        else:
            try:
                migrated = _redis_command(
                    self.host, self.port,
                    ["EVAL", _MIGRATE_LEGACY_LUA, "2", key, backup, token, new_token,
                     str(LEGACY_BACKUP_TTL_SECONDS)])
            except (OSError, RuntimeError, ValueError):
                return
            if migrated != b"1":
                return  # replaced, expired or deleted since the read
        _log.info("vault_sync: migrated legacy-key entry site=%s account=%s to the install key (backup %s)",
                  site_id, account_id, backup)

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
            with self._local_lock:
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

        try:
            raw = _redis_command(self.host, self.port, ["GET", key])
            if raw is not None:
                decrypted = self._open_token(
                    site_id, account_id,
                    raw.decode("ascii"),
                    validate_fingerprint=validate_fingerprint,
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
                    decrypted = self._open_token(
                        site_id, account_id,
                        token,
                        validate_fingerprint=validate_fingerprint,
                        current_host=current_host,
                        local_exp=exp,
                    )
                    if isinstance(decrypted, dict):
                        return decrypted
                except Exception:
                    pass
                return None
            self._evict_local(key, (exp, token))
        return None

    def lookup_session(
        self, site_id: str, account_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        """dl95-hqporner-1: get_session with the outcome kept explicit.

        ("found", data), ("absent", None) or ("unreadable", None). get_session
        answers None for all three; a caller that must fail closed on an
        unknown needs them apart. A refused connection to the default,
        unconfigured endpoint means no vault service runs on this host (test2
        runs none): the in-process fallback cache is then the whole vault.
        Every other transport, protocol or decrypt failure is unreadable."""
        key = self._cache_key(site_id, account_id)
        try:
            raw = _redis_command(self.host, self.port, ["GET", key], require_reply=True)
        except ConnectionRefusedError:
            no_service = self.fallback_local and not self.explicit_endpoint
            return self._local_lookup(site_id, account_id) or (
                ("absent", None) if no_service else ("unreadable", None))
        except (OSError, RuntimeError):
            return self._local_lookup(site_id, account_id) or ("unreadable", None)
        if raw is None:
            return ("absent", None)
        try:
            data = self._open_token(site_id, account_id, raw.decode("ascii"))
        except Exception:
            return ("unreadable", None)
        return ("found", data) if isinstance(data, dict) else ("unreadable", None)

    def _local_lookup(
        self, site_id: str, account_id: str
    ) -> tuple[str, dict[str, Any] | None] | None:
        """The fallback cache's answer, or None when it holds nothing live."""
        if not self.fallback_local:
            return None
        entry = self._local_cache.get(self._cache_key(site_id, account_id))
        if entry is None or time.time() >= entry[0]:
            return None
        try:
            data = self._open_token(site_id, account_id, entry[1], local_exp=entry[0])
        except Exception:
            return ("unreadable", None)
        return ("found", data) if isinstance(data, dict) else ("unreadable", None)

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
            entry = self._local_cache[key]
            rem = entry[0] - time.time()
            if rem > 0:
                return max(0, int(rem))
            self._evict_local(key, entry)
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
        with self._local_lock:
            del_local = self._local_cache.pop(key, None) is not None
        return del_redis or del_local

    def _evict_local(self, key: str, entry: tuple[float, str]) -> None:
        """Drop an expired fallback entry, unless a writer replaced it since it was read."""
        with self._local_lock:
            if self._local_cache.get(key) == entry:
                del self._local_cache[key]


def _log_key_mismatch(site_id: str, account_id: str) -> None:
    """One line per entry no configured key opens; the entry itself is left as is."""
    _log.warning("vault key mismatch: provision config/.vault.key (site=%s account=%s)",
                 site_id, account_id)


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
    legacy_key: bytes | None = None
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
                # O1876-4: a per-install random key, never a public constant.
                # Hosts sharing one Redis must share one key (BD_VAULT_KEY or
                # the same key file) to read each other's sessions.
                resolved_key = _load_or_create_install_key(INSTALL_KEY_FILE)
                legacy_key = LEGACY_SEED_KEY

    resolved_host = host or os.environ.get("BD_REDIS_HOST", "127.0.0.1")
    resolved_port = int(port or os.environ.get("BD_REDIS_PORT", "6379"))

    _VAULT_SYNC_INSTANCE = VaultSync(
        secret_key=resolved_key,
        host=resolved_host,
        port=resolved_port,
        fallback_local=fallback_local,
        explicit_endpoint=bool(
            host or port
            or os.environ.get("BD_REDIS_HOST") or os.environ.get("BD_REDIS_PORT")),
        legacy_key=legacy_key,
    )
    return _VAULT_SYNC_INSTANCE


_KEY_PUBLISH_ATTEMPTS = 3


def _ensure_owner_only_dir(directory: Path) -> None:
    """Create the key directory 0700 whatever the umask; an existing one keeps
    its mode, with a warning when group/other have any access."""
    try:
        directory.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        mode = directory.stat().st_mode & 0o777
        if mode & 0o077:
            _log.warning("vault_sync: key directory %s has mode %o; owner-only (0700) recommended",
                         directory, mode)
    else:
        os.chmod(directory, 0o700)


def _load_or_create_install_key(path: Path) -> bytes:
    """The install's 32-byte vault key, created owner-only on first use.

    An existing file that grants any group/other permission, or does not hold
    exactly 32 bytes, raises VaultKeyFileError; it is never used or replaced.
    A new key is written to a private 0600 temp file and published with
    link(), which never replaces: a concurrent reader sees no file or all 32
    bytes. A reader that finds no file takes the same path and reads the key
    another process published first."""
    for _attempt in range(_KEY_PUBLISH_ATTEMPTS):
        try:
            st = path.stat()
            break
        except FileNotFoundError:
            pass
        key = os.urandom(32)
        _ensure_owner_only_dir(path.parent)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
                fh.flush()
                os.fsync(fh.fileno())
            os.link(tmp, str(path))
            return key
        except FileExistsError:
            continue  # published first by another process: read it
        finally:
            os.unlink(tmp)
    else:
        # Still exists for link() yet never resolves: a dangling symlink.
        raise VaultKeyFileError(f"vault key file {path} exists but does not resolve")
    mode = st.st_mode & 0o777
    if mode & 0o077:
        raise VaultKeyFileError(
            f"vault key file {path} has mode {mode:o}; it must be owner-only (chmod 600)")
    raw = path.read_bytes()
    if len(raw) != 32:
        raise VaultKeyFileError(f"vault key file {path} holds {len(raw)} bytes, expected 32")
    return raw
