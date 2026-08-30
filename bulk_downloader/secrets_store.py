"""v3.43.14: Secure password storage.

Stores site login passwords using whichever backend is available and
chosen, with graceful fallback. Three backends:

  WindowsCredentialBackend  - Windows Credential Manager via keyring.
                              DPAPI-encrypted by your Windows login.
                              Best UX: no master password to remember.

  MasterPasswordBackend     - AES-GCM encrypted blob in secrets.json,
                              with key derived from a master password
                              via PBKDF2 (200k iterations, sha256).
                              Portable across machines, works on Linux
                              and headless deploys.

  PlaintextBackend          - Stores in sites_config.json directly,
                              like v3.43.13 and earlier. The "do
                              nothing" baseline so migration is opt-in
                              and the app never gets locked out.

In sites_config.json, passwords are stored as references when an
encrypted backend is active:

    "password": "@cred:bulkdl-<site_id>"

The "@cred:" prefix tells the resolver to look up the actual password
via the configured backend at use time. Plaintext (legacy) passwords
have no prefix.

Public API:

    backend = get_backend()                  # auto-detect or read config
    backend.set(key, password)               # store
    pw = backend.get(key)                    # retrieve or None
    backend.delete(key)                      # remove
    backend.list_keys()                      # enumerate (for migration UI)

    resolve_password(value)                  # accepts plaintext OR @cred:
                                              # ref, returns actual password

Threat model:

  - Protects against someone obtaining sites_config.json without other
    OS-level access (e.g. an unencrypted backup, a misconfigured
    file share, an accidental git commit).

  - Does NOT protect against the user being compromised: an attacker
    with your Windows login (DPAPI backend) or your master password
    can decrypt. Keychain-level encryption is no stronger than the
    keychain itself.

  - The takeover browser receives plaintext passwords because it
    pastes them into login forms. The encryption is at-rest only.
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as _stdlib_secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Files used by the master-password backend. Stored next to
# sites_config.json so backups capture them together.
_DEFAULT_SECRETS_NAME = "secrets.json"
_DEFAULT_META_NAME = "secrets_meta.json"


def _resolve_vault_paths() -> tuple[Path, Path]:
    """Return (secrets_file, meta_file), honouring the capture-vault override.

    capture.sh stops the service and starts a fresh process, and the master key
    is in-memory only -- so the vault is necessarily LOCKED when the seeder runs
    and an operator unlocking beforehand cannot survive the restart. The capture
    therefore points BD at its OWN vault, holding only the fixture's published
    test credential, and never opens the operator's.

    TWO KEYS, DELIBERATELY. Every other BD_* path override in the tree takes a
    single variable; this one does not, because none of the others can silently
    produce a working-looking empty vault. The first unlock of a fresh vault
    commits its password to a durable verifier, so a stray BD_SECRETS_FILE that
    redirected the vault would not error -- it would hand back a newly
    initialized empty credential store instead of the operator's real one.
    Requiring an explicit BD_CAPTURE_VAULT=1 alongside the path means a single
    misplaced variable is inert.

    The password itself is never read here and never defaulted anywhere in the
    tree: capture.sh supplies it at runtime via /api/secrets/unlock. A constant
    would ship every install a known unlock.
    """
    default = (Path(_DEFAULT_SECRETS_NAME), Path(_DEFAULT_META_NAME))
    override = os.environ.get("BD_SECRETS_FILE", "").strip()
    opted_in = os.environ.get("BD_CAPTURE_VAULT", "") == "1"
    if not override:
        return default
    if not opted_in:
        # Loud, because the alternative is a silent near-miss: the operator
        # believes the capture is isolated and it is running on the real vault.
        sys.stderr.write(
            "  secrets: BD_SECRETS_FILE is set but BD_CAPTURE_VAULT is not '1' "
            "-- ignoring the override and using the operator vault\n")
        return default
    target = Path(override)
    if target == Path(_DEFAULT_SECRETS_NAME):
        # Aliasing the real vault would unlock the operator's credentials with
        # a throwaway password -- the exact outcome this design prevents.
        sys.stderr.write(
            "  secrets: BD_SECRETS_FILE names the operator vault; refusing to "
            "treat it as a capture vault\n")
        return default
    sys.stderr.write(
        f"  secrets: CAPTURE VAULT active -> {target} "
        f"(the operator vault is not opened)\n")
    return target, target.with_name(f"{target.stem}_meta.json")


# Bound at import, the way app.py:33 binds BD_SITES_CONFIG_PATH -- the capture
# sets the environment before the service starts, so import time is the right
# moment. It must stay an assigned module ATTRIBUTE rather than a call-time
# getter: seven test files monkeypatch ss.SECRETS_FILE, and a getter would leave
# every one of those patches silently inert.
SECRETS_FILE, SECRETS_META_FILE = _resolve_vault_paths()

# Prefix that marks an encrypted-reference in sites_config.json.
CRED_PREFIX = "@cred:"

# Service name used for Windows Credential Manager entries. All
# bulk-downloader passwords land under this service, with per-site
# username keys so they're enumerable and revokable.
KEYRING_SERVICE = "BulkDownloader"


# ─── Optional imports (degraded mode if unavailable) ─────────────────

_KEYRING_AVAILABLE = False
try:
    import keyring  # type: ignore
    _KEYRING_AVAILABLE = True
except Exception:
    keyring = None  # type: ignore

_CRYPTO_AVAILABLE = False
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    _CRYPTO_AVAILABLE = True
except Exception:
    AESGCM = None  # type: ignore


# ─── Rotation metadata (last-rotated AGE; NEVER secret values) ───────
# A per-key "last set/rotated" timestamp lives alongside the keyring index in
# secrets_meta.json under a "rotated_at" map: {key_name: epoch_seconds}. It
# stores ONLY the key NAME and a timestamp -- never a secret value. The stamp
# is best-effort: a metadata failure must never affect the stored secret (the
# backend has already persisted the value safely), so the writers below never
# raise into a credential operation. Surfaced AGE-ONLY via /api/secrets/usage.

def _read_meta() -> dict:
    """Read secrets_meta.json as a dict (or {} on any problem)."""
    try:
        d = json.loads(SECRETS_META_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_meta(meta: dict) -> bool:
    """Atomically persist the meta dict, owner-only. Returns success."""
    tmp = SECRETS_META_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True),
                       encoding="utf-8")
        try: tmp.chmod(0o600)
        except Exception: pass
        tmp.replace(SECRETS_META_FILE)
        return True
    except Exception:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass
        return False


def _stamp_rotation(key: str) -> None:
    """Best-effort: record NOW as the last-rotated epoch for ``key`` in
    secrets_meta.json. Records ONLY the key name + a timestamp, never the secret
    value. Read-merge-write so the keyring index and other keys' timestamps are
    preserved. NEVER raises -- a metadata failure must not affect the stored
    secret, which the backend has already persisted."""
    try:
        meta = _read_meta()
        rot = meta.get("rotated_at")
        if not isinstance(rot, dict):
            rot = {}
        rot[key] = round(time.time(), 3)
        meta["rotated_at"] = rot
        _write_meta(meta)
    except Exception:
        pass


def _unstamp_rotation(key: str) -> None:
    """Best-effort: drop a key's rotation timestamp (on delete). Never raises."""
    try:
        meta = _read_meta()
        rot = meta.get("rotated_at")
        if isinstance(rot, dict) and key in rot:
            rot.pop(key, None)
            meta["rotated_at"] = rot
            _write_meta(meta)
    except Exception:
        pass


def rotation_ages(now: float | None = None) -> dict:
    """Read-only per-key last-rotated AGE. Returns
    ``{key: {rotated_at_epoch, age_seconds, age_days}}``. This reads ONLY the
    key names + timestamps from secrets_meta.json -- it never reads, returns, or
    touches a secret value. Unknown/garbled timestamps yield None ages."""
    if now is None:
        now = time.time()
    rot = _read_meta().get("rotated_at")
    out: dict = {}
    if isinstance(rot, dict):
        for k, ts in rot.items():
            try:
                ts = float(ts)
                age = max(0.0, now - ts)
                out[k] = {"rotated_at_epoch": ts,
                          "age_seconds": int(age),
                          "age_days": round(age / 86400.0, 2)}
            except Exception:
                out[k] = {"rotated_at_epoch": None, "age_seconds": None,
                          "age_days": None}
    return out


# ─── Backend interface ───────────────────────────────────────────────

class _BackendBase:
    """Common interface every backend implements. Backends are NOT
    expected to be thread-safe at the implementation level; the module-
    level _lock guards all access."""
    name: str = "base"

    def set(self, key: str, password: str) -> None:
        raise NotImplementedError

    def get(self, key: str) -> str | None:
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        """Returns True if a value existed and was removed."""
        raise NotImplementedError

    def list_keys(self) -> list[str]:
        """Enumerate stored keys. Used by migration UI to show what's
        already encrypted. May return empty when backend can't
        enumerate (e.g. some keyring backends)."""
        raise NotImplementedError

    def is_unlocked(self) -> bool:
        """Master-password backends start locked until the user supplies
        the password. Windows DPAPI backend is always 'unlocked' as long
        as the user's Windows session is active."""
        return True


# ─── PlaintextBackend ────────────────────────────────────────────────

class PlaintextBackend(_BackendBase):
    """No-op backend. Stores passwords inline in sites_config.json
    (current behavior). Exists so the app keeps working before the
    user migrates and provides a "downgrade" path if they need to
    extract their passwords.

    set/get/delete are no-ops because the password lives directly on
    the site config dict, not in any side-store. The runner already
    knows how to read cfg["password"] — it just doesn't go through
    this backend at all in plaintext mode."""
    name = "plaintext"

    def set(self, key, password): pass
    def get(self, key): return None
    def delete(self, key): return False
    def list_keys(self): return []


# ─── WindowsCredentialBackend ────────────────────────────────────────

class WindowsCredentialBackend(_BackendBase):
    """Windows Credential Manager via keyring. Passwords are DPAPI-
    encrypted by your Windows login, no master password needed.

    Per-key namespacing: every entry is service=BulkDownloader,
    username=<key>. The Credential Manager UI shows them all together,
    so you can audit / delete from there too.

    Enumerability: Windows keyring doesn't expose an enumerate API, so
    list_keys returns whatever the module keeps in an index file. The
    index lives in secrets_meta.json and is updated on every set/delete.
    Slight redundancy but means the migration UI can show what's
    stored."""
    name = "windows_credential"

    def __init__(self):
        if not _KEYRING_AVAILABLE:
            raise RuntimeError("keyring package not installed")
        # Verify the Windows backend is actually active
        backend_name = type(keyring.get_keyring()).__name__
        if "Windows" not in backend_name and sys.platform == "win32":
            # Force-select WinVaultKeyring; the default may be a stub
            try:
                from keyring.backends.Windows import WinVaultKeyring  # type: ignore
                keyring.set_keyring(WinVaultKeyring())
            except Exception:
                pass

    def _load_index(self) -> list[str]:
        if not SECRETS_META_FILE.exists(): return []
        try:
            data = json.loads(SECRETS_META_FILE.read_text(encoding="utf-8"))
            return list(data.get("keys", []))
        except Exception:
            return []

    def _save_index(self, keys: list[str]) -> bool:
        # NEW-6 (v3.66.43): return a real success signal (symmetric to
        # B17 on MasterPasswordBackend) so set/delete can detect a failed
        # index persist instead of leaving the keyring and the index out
        # of sync silently.
        # v3.66.388: read-merge-write so the rotated_at map (and any other
        # meta keys) survive an index rewrite -- an overwrite would drop the
        # rotation timestamps written by _stamp_rotation.
        tmp = SECRETS_META_FILE.with_suffix(".json.tmp")
        try:
            meta = _read_meta()
            meta["keys"] = sorted(set(keys))
            meta["backend"] = self.name
            tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            try: tmp.chmod(0o600)   # AF5: owner-only before publish
            except Exception: pass
            tmp.replace(SECRETS_META_FILE)
            return True
        except Exception as e:
            sys.stderr.write(f"  secrets index save failed: {e}\n")
            try:
                if tmp.exists(): tmp.unlink()
            except Exception:
                pass
            return False

    def set(self, key: str, password: str) -> None:
        keyring.set_password(KEYRING_SERVICE, key, password)
        idx = self._load_index()
        if key not in idx:
            idx.append(key)
            if not self._save_index(idx):
                # Roll the keyring write back so keyring/index/disk stay
                # aligned; surface the failure to the caller.
                try:
                    keyring.delete_password(KEYRING_SERVICE, key)
                except Exception:
                    pass
                raise SecretsPersistError(
                    f"secrets_meta.json index save failed after storing "
                    f"key {key!r} in Credential Manager; keyring rolled "
                    f"back (best-effort). Check disk space / permissions.")
        # reached only on a successful store -> record the rotation time
        # (best-effort, age only; never the value)
        _stamp_rotation(key)

    def get(self, key: str) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, key)
        except Exception:
            return None

    def delete(self, key: str) -> bool:
        existed = self.get(key) is not None
        kr_err = None
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except Exception as e:
            if existed:
                kr_err = e
        idx = self._load_index()
        if key in idx:
            idx = [k for k in idx if k != key]
            if not self._save_index(idx):
                raise SecretsPersistError(
                    f"index save failed during delete({key!r}); index may "
                    f"be out of sync with Credential Manager")
        if kr_err is not None:
            raise SecretsPersistError(
                f"keyring.delete_password({key!r}) failed: {kr_err}; index "
                f"updated but credential may still be in Credential Manager "
                f"— manual cleanup may be required")
        _unstamp_rotation(key)  # best-effort: drop the rotation timestamp
        return existed

    def list_keys(self) -> list[str]:
        return self._load_index()


# ─── MasterPasswordBackend ───────────────────────────────────────────

class SecretsPersistError(RuntimeError):
    """Raised when an encrypted secret could not be persisted to disk.

    B4/B13/B17 (v3.66.38): callers MUST treat a failed save as a hard
    failure and roll back in-memory state, never report success. A
    swallowed save failure means the next process restart loads stale
    data — silently losing every password written this session."""


class SecretsUnlockRequiredError(RuntimeError):
    """Raised when a locked vault mutation would erase its key commitment."""


class SecretsUninitializedError(RuntimeError):
    """Raised when an operation requires an existing password commitment."""


class SecretsIntegrityError(RuntimeError):
    """Raised when independently stored password commitments disagree."""


class SecretsPasswordPolicyError(ValueError):
    """Raised when first-use password setup does not meet local policy."""


class MasterPasswordBackend(_BackendBase):
    """AES-GCM encrypted blob, keyed off a PBKDF2-derived master key.

    File layout (secrets.json):

      {
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": 600000,
        "salt": "<base64>",
        "verifier": {"nonce": "<b64>", "ct": "<b64>"},
        "commitment_authority": "<internal ciphertext key>",
        "ciphertexts": {
            "bulkdl-wow":   {"nonce": "<b64>", "ct": "<b64>"},
            "bulkdl-ultraf": {"nonce": "<b64>", "ct": "<b64>"},
            ...
        }
      }

    Each ciphertext is independently AES-GCM encrypted with a fresh
    12-byte nonce + the same KEK (derived from master password + salt).
    A single corrupted entry doesn't affect the others.

    The verifier is the same AES-GCM envelope around a fixed public sentinel.
    It contains no secret data; it commits the master password even while the
    vault holds zero credentials, so an empty initialized vault can still
    reject a wrong password after lock or restart.

    State:
        - uninitialized: no verifier and no ciphertexts; no password committed
        - locked: _key is None, all get() calls return None
        - unlocked: _key is set, get/set/delete work normally

    The master password isn't stored anywhere — only the salt is on
    disk. The derived key is process-local by design, so this backend requires
    a human unlock after every restart, crash, deploy, or reboot. If the user
    forgets the password there's no recovery path.
    """
    name = "master_password"

    def __init__(self):
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography package not installed")
        # B18 (v3.66.38): serialize every mutation (set/delete/
        # change_password) and the in-memory reads they race with, so a
        # concurrent caller can never observe a half-rebuilt vault during
        # the change_password decrypt-rotate-swap. RLock is re-entrant so
        # change_password -> unlock -> ... nests safely. is_unlocked()
        # stays lock-free by design (cheap bool, called on hot paths).
        self._lock = threading.RLock()
        self._key: bytes | None = None
        self._data: dict[str, Any] = self._load_or_init()

    def _load_or_init(self) -> dict[str, Any]:
        if SECRETS_FILE.exists():
            try:
                return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                # AF2 (v3.66.41): never silently discard a vault we couldn't
                # parse — a transient/partial read must not become permanent
                # loss when the next set() overwrites the file. Move the
                # unparseable file aside, then reinit fresh.
                try:
                    bak = SECRETS_FILE.with_name(
                        SECRETS_FILE.name
                        + f".corrupt-{_stdlib_secrets.token_hex(4)}")
                    SECRETS_FILE.replace(bak)
                    sys.stderr.write(
                        f"  secrets.json malformed: {e}; backed up to "
                        f"{bak} and reinit\n")
                except Exception as e2:
                    sys.stderr.write(
                        f"  secrets.json malformed: {e}; backup failed: "
                        f"{e2}; reinit\n")
        # Fresh init: random salt, no ciphertexts
        # AUDIT v3.43.47: 600,000 iterations follows OWASP 2023 guidance
        # for PBKDF2-HMAC-SHA256. Previously 200,000 (the 2018 baseline).
        # Existing vaults keep their stamped iteration count via the
        # `iterations` field on disk — only NEW vaults pick up this
        # higher default. On a 2024-era CPU this adds ~150ms to each
        # unlock; unnoticeable for the user, meaningful for an attacker
        # who has the file.
        return {
            "version": 1,
            "kdf": "pbkdf2-sha256",
            "iterations": 600000,
            "salt": base64.b64encode(_stdlib_secrets.token_bytes(16)).decode(),
            "ciphertexts": {},
        }

    def _save(self) -> bool:
        """Atomically persist the vault to disk. Returns True on success,
        False on failure (B4/B17, v3.66.38). Callers MUST check the result
        and roll back in-memory state on False — a swallowed save failure
        means the next restart loads stale data (silent password loss)."""
        tmp = SECRETS_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(self._data, indent=2),
                encoding="utf-8",
            )
            # AF5 (v3.66.42): 0600 on the tmp BEFORE the atomic replace, so
            # the published secrets blob is owner-only from the instant it
            # exists — never world-readable on a multi-user host. Best-effort
            # (POSIX; no-op on Windows).
            try: tmp.chmod(0o600)
            except Exception: pass
            tmp.replace(SECRETS_FILE)
            return True
        except Exception as e:
            sys.stderr.write(f"  secrets save failed: {e}\n")
            # Don't leave a partial .tmp behind.
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            return False

    def _derive_key_with_salt(self, salt_b64: str, password: str) -> bytes:
        salt = base64.b64decode(salt_b64)
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(self._data["iterations"]),
            32,
        )

    def _derive_key(self, password: str) -> bytes:
        return self._derive_key_with_salt(self._data["salt"], password)

    _VERIFIER_PLAINTEXT = b"bd-vault-verifier-v1"
    # A top-level ``verifier`` is invisible to releases at or below 44c8701c.
    # Keep a public, non-secret commitment inside ``ciphertexts`` as well so a
    # rollback still authenticates the password instead of treating an empty
    # initialized vault as first use.  Candidate APIs hide and protect this
    # reserved entry; its position is deliberate because base44 authenticates
    # only the first ciphertext.
    _ROLLBACK_COMMITMENT_KEY = "__bd_vault_password_commitment_v1__"
    _ROLLBACK_COMMITMENT_PLAINTEXT = b"bd-vault-rollback-commitment-v1"
    _COMMITMENT_AUTHORITY_FIELD = "commitment_authority"
    # Historical vaults use 200k or 600k.  Bound persisted input so malformed
    # metadata cannot overflow the C API or turn health-green unlock into an
    # effectively unbounded CPU job.
    _MAX_KDF_ITERATIONS = 10_000_000

    def _verify_with(self, entry: Any, key: bytes) -> bool:
        """Return whether *entry* is a valid envelope for this key."""
        try:
            nonce = base64.b64decode(entry["nonce"])
            ciphertext = base64.b64decode(entry["ct"])
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
            return plaintext == self._VERIFIER_PLAINTEXT
        except Exception:
            return False

    @staticmethod
    def _envelope_is_well_formed(entry: Any) -> bool:
        """Whether an entry has the exact AES-GCM envelope wire shape."""
        try:
            if not isinstance(entry, dict) or set(entry) != {"nonce", "ct"}:
                return False
            nonce = base64.b64decode(entry["nonce"], validate=True)
            ciphertext = base64.b64decode(entry["ct"], validate=True)
            return len(nonce) == 12 and len(ciphertext) >= 16
        except Exception:
            return False

    def _verify_rollback_commitment_with(self, entry: Any, key: bytes) -> bool:
        try:
            nonce = base64.b64decode(entry["nonce"])
            ciphertext = base64.b64decode(entry["ct"])
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
            return plaintext == self._ROLLBACK_COMMITMENT_PLAINTEXT
        except Exception:
            return False

    def _verify_ciphertext_with(self, entry: Any, key: bytes) -> bool:
        """Legacy verifier: any intact stored ciphertext authenticates *key*."""
        try:
            nonce = base64.b64decode(entry["nonce"])
            ciphertext = base64.b64decode(entry["ct"])
            AESGCM(key).decrypt(nonce, ciphertext, None)
            return True
        except Exception:
            return False

    def _seal_verifier(self, key: bytes) -> dict[str, str]:
        nonce = _stdlib_secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(
            nonce, self._VERIFIER_PLAINTEXT, None
        )
        return {
            "nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ciphertext).decode(),
        }

    def _seal_rollback_commitment(self, key: bytes) -> dict[str, str]:
        nonce = _stdlib_secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(
            nonce, self._ROLLBACK_COMMITMENT_PLAINTEXT, None
        )
        return {
            "nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ciphertext).decode(),
        }

    def _commitment_authority_key_locked(
        self, ciphertexts: dict[str, Any]
    ) -> str | None:
        """Return the marked internal entry, if its persisted link is valid."""
        authority = self._data.get(self._COMMITMENT_AUTHORITY_FIELD)
        if (
            isinstance(authority, str)
            and self._is_rollback_commitment_name(authority)
            and authority in ciphertexts
        ):
            return authority
        return None

    def _unused_rollback_commitment_key(
        self, ciphertexts: dict[str, Any]
    ) -> str:
        """Choose an internal name without overwriting a legacy user key."""
        candidate = self._ROLLBACK_COMMITMENT_KEY
        suffix = 1
        while candidate in ciphertexts:
            candidate = f"{self._ROLLBACK_COMMITMENT_KEY}.{suffix}"
            suffix += 1
        return candidate

    def _is_rollback_commitment_name(self, name: str) -> bool:
        """Whether *name* is one this release can allocate internally."""
        if name == self._ROLLBACK_COMMITMENT_KEY:
            return True
        prefix = f"{self._ROLLBACK_COMMITMENT_KEY}."
        if not name.startswith(prefix):
            return False
        suffix = name[len(prefix):]
        return (
            bool(suffix)
            and suffix[0] != "0"
            and all("0" <= character <= "9" for character in suffix)
        )

    def _is_initialized_locked(self) -> bool:
        ciphertexts_present = "ciphertexts" in self._data
        ciphertexts = self._data.get("ciphertexts")
        # Every genuine fresh vault has an empty dict.  A missing or non-dict
        # container is damaged persisted state, not permission to choose a new
        # password, and must therefore classify as initialized/fail-closed.
        malformed_ciphertexts = (
            not ciphertexts_present or not isinstance(ciphertexts, dict)
        )
        return (
            malformed_ciphertexts
            or self._COMMITMENT_AUTHORITY_FIELD in self._data
            or "verifier" in self._data
            or bool(ciphertexts)
        )

    def _validate_kdf_metadata_locked(self) -> None:
        """Reject salt/iteration metadata that cannot derive a vault key."""
        try:
            salt_text = self._data["salt"]
            if not isinstance(salt_text, str):
                raise TypeError("salt is not text")
            salt = base64.b64decode(salt_text, validate=True)
            if not salt:
                raise ValueError("salt is empty")
            raw_iterations = self._data["iterations"]
            if isinstance(raw_iterations, bool):
                raise TypeError("iterations is boolean")
            iterations = int(raw_iterations)
            if not 0 < iterations <= self._MAX_KDF_ITERATIONS:
                raise ValueError("iterations is outside the supported range")
        except Exception as error:
            raise SecretsIntegrityError(
                "vault KDF metadata is missing or malformed"
            ) from error

    def _validate_commitment_structure_locked(
        self, ciphertexts: dict[str, Any]
    ) -> None:
        """Reject commitment shapes that cannot authenticate any password.

        This validation needs no derived key, so locked inventory/health can
        distinguish structurally damaged state from an ordinary healthy
        zero-reference vault.  A malformed verifier is allowed only when the
        explicit authority marker and a well-formed reserved commitment make
        authenticated rollback repair possible after unlock.
        """
        authority_present = self._COMMITMENT_AUTHORITY_FIELD in self._data
        authority_key = self._commitment_authority_key_locked(ciphertexts)
        verifier_present = "verifier" in self._data
        verifier = self._data.get("verifier")

        if authority_present:
            if authority_key is None:
                raise SecretsIntegrityError(
                    "authoritative vault password commitment marker is invalid"
                )
            if not self._envelope_is_well_formed(ciphertexts[authority_key]):
                raise SecretsIntegrityError(
                    "authoritative rollback password commitment is missing "
                    "or malformed"
                )
            return

        if (
            verifier_present
            and not self._envelope_is_well_formed(verifier)
        ):
            raise SecretsIntegrityError(
                "vault verifier is malformed; password commitment integrity "
                "cannot be established"
            )
        # With no authority marker every name remains a possible pre-row402
        # user key.  In particular, the preferred internal name was legal in
        # older releases and must not be hidden or rejected by name alone.
        user_entries = list(ciphertexts.values())
        if (
            user_entries
            and not any(
                self._envelope_is_well_formed(entry)
                for entry in user_entries
            )
        ):
            raise SecretsIntegrityError(
                "stored credential ciphertexts are malformed; vault "
                "integrity cannot be established"
            )

    def _persist_missing_commitments(
        self,
        key: bytes,
        *,
        add_verifier: bool,
        add_rollback_commitment: bool,
        commitment_key: str | None = None,
        action: str,
    ) -> None:
        """Atomically add authenticated compatibility material.

        Existing entries are never replaced here.  In particular, a present
        verifier that disagrees with ciphertext is an integrity error, not an
        invitation to silently bind it to whichever password was tried last.
        """
        had_verifier = "verifier" in self._data
        old_verifier = self._data.get("verifier")
        had_ciphertexts = "ciphertexts" in self._data
        old_ciphertexts = self._data.get("ciphertexts")
        had_authority = self._COMMITMENT_AUTHORITY_FIELD in self._data
        old_authority = self._data.get(self._COMMITMENT_AUTHORITY_FIELD)

        # Seal and assemble everything before publishing any in-memory field.
        # RNG/AES failures are exceptions (not a False save result) and must
        # therefore leave first-use and legacy-upgrade state byte-exact too.
        new_verifier = self._seal_verifier(key) if add_verifier else None
        new_ciphertexts = None
        published_commitment_key = commitment_key
        if add_rollback_commitment:
            rollback_commitment = self._seal_rollback_commitment(key)
            current = self._data.get("ciphertexts")
            current = current if isinstance(current, dict) else {}
            published_commitment_key = self._unused_rollback_commitment_key(
                current
            )
            # Reserved entry first: base44 verifies only next(iter(cts)).
            new_ciphertexts = {
                published_commitment_key: rollback_commitment,
                **current,
            }
        if published_commitment_key is None:
            raise SecretsIntegrityError(
                "vault password commitment has no authoritative entry"
            )

        if add_verifier:
            self._data["verifier"] = new_verifier
        if add_rollback_commitment:
            self._data["ciphertexts"] = new_ciphertexts
        self._data[
            self._COMMITMENT_AUTHORITY_FIELD
        ] = published_commitment_key
        if self._save():
            return

        if had_verifier:
            self._data["verifier"] = old_verifier
        else:
            self._data.pop("verifier", None)
        if had_ciphertexts:
            self._data["ciphertexts"] = old_ciphertexts
        else:
            self._data.pop("ciphertexts", None)
        if had_authority:
            self._data[self._COMMITMENT_AUTHORITY_FIELD] = old_authority
        else:
            self._data.pop(self._COMMITMENT_AUTHORITY_FIELD, None)
        self._key = None
        raise SecretsPersistError(
            f"{action} failed; existing vault was not changed"
        )

    def _repair_verifier_from_authoritative_commitment(
        self, key: bytes
    ) -> None:
        """Repair only under the explicit rollback-compatibility contract.

        Base44 preserves unknown top-level fields but rotates every ciphertext.
        Therefore a base44 password change updates the reserved commitment and
        leaves ``verifier`` stale.  The authority marker plus successful AEAD
        authentication of the fixed reserved plaintext is the exact rule that
        permits rebinding the verifier; ordinary user ciphertext never does.
        """
        had_verifier = "verifier" in self._data
        old_verifier = self._data.get("verifier")
        self._data["verifier"] = self._seal_verifier(key)
        if self._save():
            sys.stderr.write(
                "  secrets: repaired verifier from authenticated rollback "
                "password commitment\n"
            )
            return
        if had_verifier:
            self._data["verifier"] = old_verifier
        else:
            self._data.pop("verifier", None)
        self._key = None
        raise SecretsPersistError(
            "vault verifier compatibility repair failed; existing vault was "
            "not changed"
        )

    def _unlock_locked(
        self,
        password: str,
        *,
        persist_compatibility: bool = True,
    ) -> bool:
        """Authenticate while ``self._lock`` is held.

        Ordinary unlocks durably add missing compatibility commitments before
        exposing the key. Password rotation suppresses that intermediate
        publish and commits the upgraded shape with the rotation in one save.
        """
        # Every path proves the supplied password again.  Clear any prior key
        # before validation/derivation/sealing so an unexpected exception can
        # never leave a failed unlock with old access still enabled.
        self._key = None
        raw_cts = self._data.get("ciphertexts")
        if "ciphertexts" not in self._data or not isinstance(raw_cts, dict):
            self._key = None
            raise SecretsIntegrityError(
                "vault ciphertexts container is missing or malformed"
            )
        cts = raw_cts
        try:
            self._validate_kdf_metadata_locked()
            self._validate_commitment_structure_locked(cts)
        except SecretsIntegrityError:
            self._key = None
            raise
        verifier_present = "verifier" in self._data
        verifier = self._data.get("verifier")
        authority_present = self._COMMITMENT_AUTHORITY_FIELD in self._data
        commitment_key = self._commitment_authority_key_locked(cts)
        commitment_present = commitment_key is not None
        commitment = cts.get(commitment_key) if commitment_key else None
        user_cts = {
            name: entry
            for name, entry in cts.items()
            if name != commitment_key
        }
        authority_valid = authority_present and commitment_key is not None

        key = self._derive_key(password)

        if (
            not authority_present
            and not verifier_present
            and not cts
        ):
            self._persist_missing_commitments(
                key,
                add_verifier=True,
                add_rollback_commitment=True,
                action="vault initialization",
            )
            self._key = key
            return True

        verifier_well_formed = (
            verifier_present and self._envelope_is_well_formed(verifier)
        )
        commitment_well_formed = (
            commitment_present
            and self._envelope_is_well_formed(commitment)
        )
        verifier_ok = (
            verifier_well_formed and self._verify_with(verifier, key)
        )
        commitment_ok = (
            commitment_well_formed
            and self._verify_rollback_commitment_with(commitment, key)
        )
        legacy_ok = any(
            self._verify_ciphertext_with(entry, key)
            for entry in user_cts.values()
        )
        # A stripped authority marker must not turn a candidate commitment
        # into ordinary "any ciphertext" legacy authentication.  Candidate
        # commitments always use the preferred name or its numeric collision
        # suffixes.  Keep every unmarked entry as user data (all of those names
        # were legal before row402), but if any candidate-family name survives,
        # every well-formed envelope must authenticate under the same key.
        # Wrong passwords still return False when no entry authenticates; sole
        # and all-same-key legacy collisions remain backward compatible.
        unmarked_commitment_name_present = (
            not authority_present
            and any(
                self._is_rollback_commitment_name(name)
                for name in user_cts
            )
        )
        well_formed_user_entries = [
            entry
            for entry in user_cts.values()
            if self._envelope_is_well_formed(entry)
        ]
        all_well_formed_user_entries_match = all(
            self._verify_ciphertext_with(entry, key)
            for entry in well_formed_user_entries
        )
        if (
            unmarked_commitment_name_present
            and legacy_ok
            and not all_well_formed_user_entries_match
        ):
            self._key = None
            raise SecretsIntegrityError(
                "rollback password commitment and stored ciphertexts disagree"
            )

        if authority_valid:
            if commitment_ok:
                if not all_well_formed_user_entries_match:
                    self._key = None
                    raise SecretsIntegrityError(
                        "authoritative rollback password commitment and "
                        "stored ciphertexts disagree"
                    )
                if not verifier_ok and persist_compatibility:
                    self._repair_verifier_from_authoritative_commitment(key)
                self._key = key
                return True
            if verifier_ok or legacy_ok:
                self._key = None
                raise SecretsIntegrityError(
                    "authoritative rollback password commitment disagrees "
                    "with other vault material"
                )
            self._key = None
            return False

        if verifier_present and commitment_present:
            if verifier_ok != commitment_ok:
                self._key = None
                raise SecretsIntegrityError(
                    "vault verifier and rollback password commitment disagree"
                )
            if verifier_ok:
                if not all_well_formed_user_entries_match:
                    self._key = None
                    raise SecretsIntegrityError(
                        "vault verifier, rollback password commitment, and "
                        "stored ciphertexts disagree"
                    )
                if persist_compatibility:
                    self._persist_missing_commitments(
                        key,
                        add_verifier=False,
                        add_rollback_commitment=False,
                        commitment_key=commitment_key,
                        action="vault commitment-authority upgrade",
                    )
                self._key = key
                return True
            if legacy_ok:
                self._key = None
                raise SecretsIntegrityError(
                    "vault verifier and stored ciphertexts disagree"
                )
            self._key = None
            return False

        if verifier_present:
            if verifier_ok:
                if not all_well_formed_user_entries_match:
                    self._key = None
                    raise SecretsIntegrityError(
                        "vault verifier and stored ciphertexts disagree"
                    )
                if persist_compatibility:
                    self._persist_missing_commitments(
                        key,
                        add_verifier=False,
                        add_rollback_commitment=True,
                        commitment_key=commitment_key,
                        action="vault rollback-commitment upgrade",
                    )
                self._key = key
                return True
            if legacy_ok:
                self._key = None
                raise SecretsIntegrityError(
                    "vault verifier and stored ciphertexts disagree"
                )
            self._key = None
            return False

        if commitment_present:
            if commitment_ok:
                if not all_well_formed_user_entries_match:
                    self._key = None
                    raise SecretsIntegrityError(
                        "rollback password commitment and stored ciphertexts "
                        "disagree"
                    )
                if persist_compatibility:
                    self._persist_missing_commitments(
                        key,
                        add_verifier=True,
                        add_rollback_commitment=False,
                        commitment_key=commitment_key,
                        action="vault verifier upgrade",
                    )
                self._key = key
                return True
            if legacy_ok:
                self._key = None
                raise SecretsIntegrityError(
                    "rollback password commitment and stored ciphertexts "
                    "disagree"
                )
            self._key = None
            return False

        # Pre-row402 legacy vault: one intact user ciphertext is its password
        # commitment.  Once authenticated, add both new forms in one publish.
        if not legacy_ok:
            self._key = None
            return False
        if persist_compatibility:
            self._persist_missing_commitments(
                key,
                add_verifier=True,
                add_rollback_commitment=True,
                action="vault verifier upgrade",
            )
        self._key = key
        return True

    def unlock(self, password: str) -> bool:
        """Unlock an existing vault or durably initialize a fresh one.

        A fresh vault commits the first password by atomically persisting an
        AES-GCM verifier before exposing the derived key.  Persistence failure
        therefore leaves no in-memory or on-disk commitment and is distinct
        from an incorrect password.  Vaults written before the verifier field
        existed remain compatible by authenticating against one ciphertext.
        """
        with self._lock:
            return self._unlock_locked(password)

    def unlock_with_status(
        self,
        password: str,
        *,
        minimum_initial_length: int = 0,
    ) -> dict[str, bool]:
        """Unlock and report initialization from one serialized state view."""
        with self._lock:
            was_initialized = self._is_initialized_locked()
            if (
                not was_initialized
                and minimum_initial_length
                and len(password) < minimum_initial_length
            ):
                raise SecretsPasswordPolicyError(
                    "first-use master password must be at least "
                    f"{minimum_initial_length} characters"
                )
            try:
                unlocked = self._unlock_locked(password)
            except (SecretsPersistError, SecretsIntegrityError) as error:
                # Capture the state while still under the same lock.  The HTTP
                # layer must not re-read after another first-use request has
                # initialized the vault and misreport this request's outcome.
                error.vault_status = {
                    "is_initialized": self._is_initialized_locked(),
                    "is_unlocked": self._key is not None,
                }
                raise
            now_initialized = self._is_initialized_locked()
            return {
                "unlocked": unlocked,
                "initialized_now": (
                    unlocked and not was_initialized and now_initialized
                ),
                "is_initialized": now_initialized,
                "is_unlocked": self._key is not None,
            }

    def lock(self) -> None:
        """Forget the derived key. get/set will fail until unlock again."""
        with self._lock:
            self._key = None

    def is_unlocked(self) -> bool:
        return self._key is not None

    def _change_password_locked(
        self, old_password: str, new_password: str
    ) -> str:
        """Return changed/incorrect_password/corrupt while holding the lock."""
        if not self._is_initialized_locked():
            raise SecretsUninitializedError(
                "initialize the vault through /api/secrets/unlock before "
                "changing its password"
            )
        precall_key = self._key
        if not self._unlock_locked(
            old_password,
            persist_compatibility=False,
        ):
            self._key = precall_key
            return "incorrect_password"
        old_key = self._key
        cts = self._data.get("ciphertexts") or {}
        commitment_key = self._commitment_authority_key_locked(cts)
        # All-or-nothing decrypt: a single failure aborts the whole rotation
        # before any compatibility upgrade or rotated state is published. The
        # old code skipped undecryptable entries to stderr and silently
        # dropped them.
        plaintexts = {}
        for k, entry in cts.items():
            if k == commitment_key:
                # _unlock_locked already authenticated the fixed reserved
                # plaintext. Rotation reseals it directly below rather than
                # treating internal compatibility material as a user secret.
                continue
            try:
                nonce = base64.b64decode(entry["nonce"])
                ct = base64.b64decode(entry["ct"])
                plaintexts[k] = AESGCM(old_key).decrypt(
                    nonce, ct, None).decode("utf-8")
            except Exception as e:
                sys.stderr.write(
                    f"  change_password aborted: cannot decrypt {k!r}: {e}\n")
                self._key = precall_key
                return "corrupt"
        # Derive and seal the complete new representation without touching
        # self._data. Any failure therefore restores the exact entry key state
        # and leaves both memory and disk byte-for-byte at the pre-call shape.
        try:
            new_salt = base64.b64encode(
                _stdlib_secrets.token_bytes(16)
            ).decode()
            new_key = self._derive_key_with_salt(new_salt, new_password)
            new_verifier = self._seal_verifier(new_key)
            new_commitment_key = (
                commitment_key
                if commitment_key is not None
                else self._unused_rollback_commitment_key(plaintexts)
            )
            new_cts = {
                new_commitment_key:
                    self._seal_rollback_commitment(new_key),
            }
            for k, pt in plaintexts.items():
                nonce = _stdlib_secrets.token_bytes(12)
                ct = AESGCM(new_key).encrypt(
                    nonce, pt.encode("utf-8"), None
                )
                new_cts[k] = {
                    "nonce": base64.b64encode(nonce).decode(),
                    "ct": base64.b64encode(ct).decode(),
                }
        except Exception:
            self._key = precall_key
            raise
        # Snapshot, swap once, persist once. This combines any legacy
        # compatibility upgrade with rotation so a later failure cannot leave
        # a successful intermediate upgrade behind.
        old_salt = self._data["salt"]
        old_cts = self._data.get("ciphertexts")
        had_verifier = "verifier" in self._data
        old_verifier = self._data.get("verifier")
        had_authority = self._COMMITMENT_AUTHORITY_FIELD in self._data
        old_authority = self._data.get(self._COMMITMENT_AUTHORITY_FIELD)

        def restore_precall_state() -> None:
            self._data["salt"] = old_salt
            self._data["ciphertexts"] = old_cts
            if had_verifier:
                self._data["verifier"] = old_verifier
            else:
                self._data.pop("verifier", None)
            if had_authority:
                self._data[self._COMMITMENT_AUTHORITY_FIELD] = old_authority
            else:
                self._data.pop(self._COMMITMENT_AUTHORITY_FIELD, None)
            self._key = precall_key

        self._data["salt"] = new_salt
        self._data["ciphertexts"] = {
            new_commitment_key: new_cts[new_commitment_key],
            **{
                name: entry
                for name, entry in new_cts.items()
                if name != new_commitment_key
            },
        }
        self._data["verifier"] = new_verifier
        self._data[
            self._COMMITMENT_AUTHORITY_FIELD
        ] = new_commitment_key
        self._key = new_key
        try:
            saved = self._save()
        except Exception:
            restore_precall_state()
            raise
        if not saved:
            restore_precall_state()
            raise SecretsPersistError(
                "change_password: could not persist rotated vault; "
                "rolled back, old password still in effect")
        return "changed"

    def change_password_with_status(
        self, old_password: str, new_password: str
    ) -> dict[str, bool | str]:
        """Atomically classify authentication and rotate without a preflight."""
        with self._lock:
            reason = self._change_password_locked(old_password, new_password)
            return {"changed": reason == "changed", "reason": reason}

    def change_password(self, old_password: str, new_password: str) -> bool:
        """Re-encrypt every stored secret with a new master password.

        Returns False, without mutation, for an incorrect old password or an
        undecryptable ciphertext. Persistence errors raise after full in-memory
        rollback. The initialized check, password proof, and rotation share one
        lock so this operation can never become first-use initialization.
        """
        with self._lock:
            return (
                self._change_password_locked(old_password, new_password)
                == "changed"
            )

    def set(self, key: str, password: str) -> None:
        if self._key is None:
            raise RuntimeError("backend is locked; call unlock() first")
        with self._lock:
            cts = self._data.setdefault("ciphertexts", {})
            nonce = _stdlib_secrets.token_bytes(12)
            ct = AESGCM(self._key).encrypt(nonce, password.encode("utf-8"), None)
            new_entry = {
                "nonce": base64.b64encode(nonce).decode(),
                "ct": base64.b64encode(ct).decode(),
            }
            authority_key = self._commitment_authority_key_locked(cts)
            if key == authority_key:
                # The internal name is not part of the public key namespace.
                # If a caller legitimately uses it, relocate the commitment
                # and publish both changes atomically instead of rejecting or
                # overwriting either entry.
                user_entries = {
                    name: entry
                    for name, entry in cts.items()
                    if name != authority_key
                }
                user_entries[key] = new_entry
                new_authority = self._unused_rollback_commitment_key(
                    user_entries
                )
                new_ciphertexts = {
                    new_authority: self._seal_rollback_commitment(self._key),
                    **user_entries,
                }
                old_ciphertexts = cts
                old_authority = self._data.get(
                    self._COMMITMENT_AUTHORITY_FIELD
                )
                self._data["ciphertexts"] = new_ciphertexts
                self._data[
                    self._COMMITMENT_AUTHORITY_FIELD
                ] = new_authority
                try:
                    saved = self._save()
                except Exception:
                    self._data["ciphertexts"] = old_ciphertexts
                    self._data[
                        self._COMMITMENT_AUTHORITY_FIELD
                    ] = old_authority
                    raise
                if not saved:
                    self._data["ciphertexts"] = old_ciphertexts
                    self._data[
                        self._COMMITMENT_AUTHORITY_FIELD
                    ] = old_authority
                    raise SecretsPersistError(
                        f"failed to persist secret {key!r}"
                    )
            else:
                had = key in cts
                prev = cts.get(key)
                cts[key] = new_entry
                try:
                    saved = self._save()
                except Exception:
                    if had:
                        cts[key] = prev
                    else:
                        cts.pop(key, None)
                    raise
                if not saved:
                    # B4/B13 (v3.66.38): roll the in-memory mutation back so
                    # a reader / restart sees a consistent store, and RAISE so
                    # callers cannot mistake failed persistence for success.
                    if had:
                        cts[key] = prev
                    else:
                        cts.pop(key, None)
                    raise SecretsPersistError(
                        f"failed to persist secret {key!r}"
                    )
        # reached only after a successful persist (else raised above). Stamp the
        # rotation time OUTSIDE the vault lock -- it is independent best-effort
        # metadata (age only, never the value) and must not affect the vault.
        _stamp_rotation(key)

    def get(self, key: str) -> str | None:
        # B18 (v3.66.38): read under the lock so a get() can't observe a
        # half-swapped vault mid change_password (new ciphertexts vs old key).
        with self._lock:
            cts = self._data.get("ciphertexts") or {}
            if key == self._commitment_authority_key_locked(cts):
                return None
            if self._key is None: return None
            entry = cts.get(key)
            if not entry: return None
            try:
                nonce = base64.b64decode(entry["nonce"])
                ct = base64.b64decode(entry["ct"])
                return AESGCM(self._key).decrypt(nonce, ct, None).decode("utf-8")
            except Exception:
                return None

    def delete(self, key: str) -> bool:
        with self._lock:
            cts = self._data.get("ciphertexts") or {}
            authority_key = self._commitment_authority_key_locked(cts)
            if key == authority_key:
                return False
            if key not in cts: return False
            user_keys = [
                name for name in cts
                if name != authority_key
            ]
            other_user_entries = [
                entry
                for name, entry in cts.items()
                if name not in {authority_key, key}
            ]
            removes_last_well_formed_user = (
                self._envelope_is_well_formed(cts[key])
                and not any(
                    self._envelope_is_well_formed(entry)
                    for entry in other_user_entries
                )
            )
            if (
                self._key is None
                and (
                    len(user_keys) == 1
                    or removes_last_well_formed_user
                )
            ):
                # Removing the final raw user ciphertext -- or the final one
                # whose envelope could still authenticate a legacy vault -- is
                # the destructive edge. Require a successful unlock regardless
                # of verifier presence or shape so an unauthenticated delete
                # cannot leave only known-malformed password evidence.
                raise SecretsUnlockRequiredError(
                    "unlock this vault before deleting its final usable "
                    "credential"
                )
            removed = cts.pop(key)
            if not self._save():
                cts[key] = removed  # B4: roll back, never report a phantom delete
                raise SecretsPersistError(
                    f"failed to persist deletion of {key!r}")
        _unstamp_rotation(key)  # best-effort: drop the rotation timestamp
        return True

    def list_keys(self) -> list[str]:
        with self._lock:
            ciphertexts = self._data.get("ciphertexts")
            if (
                "ciphertexts" not in self._data
                or not isinstance(ciphertexts, dict)
                or any(not isinstance(name, str) for name in ciphertexts)
            ):
                # Inventory powers the health endpoint even while locked.
                # Treat a damaged container as unavailable evidence; iterating
                # a list (or normalising None to {}) can otherwise launder
                # corruption into a healthy zero-reference vault.
                raise SecretsIntegrityError(
                    "vault ciphertexts container is missing or malformed"
                )
            self._validate_kdf_metadata_locked()
            self._validate_commitment_structure_locked(ciphertexts)
            authority_key = self._commitment_authority_key_locked(ciphertexts)
            return sorted(
                name
                for name in ciphertexts
                if name != authority_key
            )

    def is_initialized(self) -> bool:
        """Whether durable material commits a master password.

        Ciphertexts cover legacy vaults; verifier presence covers initialized
        empty vaults.  Presence is intentional: a malformed verifier is a
        damaged initialized vault, not permission to choose a new password.
        """
        with self._lock:
            return self._is_initialized_locked()


# ─── Module state + auto-detect ──────────────────────────────────────

_lock = threading.RLock()
_backend: _BackendBase | None = None
_backend_pref: str | None = None  # "windows_credential", "master_password", "plaintext"
# NEW-8: cache one audit-wrapper per real backend instance, for identity
# stability across get_backend() calls while auditing is enabled. Only
# populated when BD_SECRETS_AUDIT is on; off-path leaves it None.
_audited_cache: tuple | None = None  # (real_backend, _AuditedBackend)


def _detect_default_backend_name() -> str:
    """Pick the right default for this OS/install. Windows -> keychain
    if available, else master-password if cryptography is available,
    else plaintext."""
    if sys.platform == "win32" and _KEYRING_AVAILABLE:
        return "windows_credential"
    if _CRYPTO_AVAILABLE:
        return "master_password"
    return "plaintext"


def configure_backend(name: str) -> bool:
    """Switch the active backend. Returns True on success.

    Doesn't migrate existing data — that's a separate explicit step
    (migrate_from_plaintext)."""
    global _backend, _backend_pref
    with _lock:
        try:
            if name == "windows_credential":
                _backend = WindowsCredentialBackend()
            elif name == "master_password":
                _backend = MasterPasswordBackend()
            elif name == "plaintext":
                _backend = PlaintextBackend()
            else:
                return False
            _backend_pref = name
            return True
        except Exception as e:
            sys.stderr.write(f"  backend {name!r} unavailable: {e}\n")
            return False


# ─── NEW-8: opt-in audit wrapper ─────────────────────────────────────
# A transparent proxy that records get/set/delete to secrets_audit. It is
# returned by get_backend() ONLY when BD_SECRETS_AUDIT is enabled; when
# disabled, get_backend() returns the raw backend and this class is never
# instantiated, so the default path is byte-identical and free of overhead.
#
# Every non-audited attribute (unlock, lock, change_password, list_keys,
# is_unlocked, start/stop, name, ...) is delegated straight through, so
# hasattr() and duck-typed callers behave exactly as on the raw backend.
# Nothing in the codebase isinstance/type-checks the backend (verified),
# so the proxy is a safe substitution.

def _audit_safe(action: str, key, *, ok=None, backend=None) -> None:
    """Emit one audit event, fail-open. Never raises into a credential op."""
    try:
        from . import secrets_audit
        secrets_audit.audit(action, key, ok=ok,
                            backend_name=getattr(backend, "name", None))
    except Exception:
        pass


class _AuditedBackend:
    """Transparent audit proxy over a real backend (see NEW-8 above)."""
    __slots__ = ("_be",)

    def __init__(self, backend):
        object.__setattr__(self, "_be", backend)

    def set(self, key, password):
        try:
            result = self._be.set(key, password)
        except Exception:
            _audit_safe("set", key, ok=False, backend=self._be)
            raise
        _audit_safe("set", key, ok=True, backend=self._be)
        return result

    def get(self, key):
        try:
            val = self._be.get(key)
        except Exception:
            _audit_safe("get", key, ok=False, backend=self._be)
            raise
        _audit_safe("get", key, ok=(val is not None), backend=self._be)
        return val

    def delete(self, key):
        # Preserve the real backend's exact return value (bool); do not
        # coerce. ``ok`` defaults to None so a raising delete logs "unknown".
        ok = None
        try:
            result = self._be.delete(key)
            ok = result
            return result
        finally:
            _audit_safe("delete", key, ok=ok, backend=self._be)

    # Transparent delegation for every other attribute / method.
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_be"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_be"), name, value)


def _maybe_wrap_audit(be: "_BackendBase") -> "_BackendBase":
    """Return ``be`` wrapped in the audit proxy iff BD_SECRETS_AUDIT is on,
    else ``be`` unchanged. Caches one wrapper per real backend instance so
    repeated get_backend() calls keep object identity stable."""
    global _audited_cache
    try:
        from . import secrets_audit
        if not secrets_audit.is_enabled():
            return be
    except Exception:
        return be
    if _audited_cache is not None and _audited_cache[0] is be:
        return _audited_cache[1]
    wrapper = _AuditedBackend(be)
    _audited_cache = (be, wrapper)
    return wrapper


def get_backend() -> _BackendBase:
    """Return the active backend, auto-configuring if not yet set.

    When BD_SECRETS_AUDIT is enabled the returned object is a transparent
    audit proxy (NEW-8); otherwise the raw backend is returned unchanged.
    """
    global _backend
    with _lock:
        if _backend is None:
            configure_backend(_detect_default_backend_name())
        be = _backend or PlaintextBackend()
    return _maybe_wrap_audit(be)


def get_backend_name() -> str:
    """Return the active backend's name."""
    return get_backend().name


def site_password_key(site_id: str) -> str:
    """Key used to store a site's login password. Stable across renames
    so you don't lose the password if you change the site's display
    name."""
    return f"bulkdl-site-{site_id}"


# ─── Plaintext-reference resolution ──────────────────────────────────

def password_reference_keys(sites_config: dict | None) -> list[str]:
    """Return every configured ``@cred:`` password key, including accounts.

    The list is occurrence-based rather than deduplicated: health denominators
    describe configured credential consumers, while ``backend.list_keys()``
    separately describes stored entries. Values are names only; this function
    never reads secret material.
    """
    references: list[str] = []
    for cfg in (sites_config or {}).values():
        if not isinstance(cfg, dict):
            continue
        values = [cfg.get("password")]
        accounts = cfg.get("accounts")
        if isinstance(accounts, list):
            values.extend(
                account.get("password")
                for account in accounts
                if isinstance(account, dict)
            )
        for value in values:
            if isinstance(value, str) and value.startswith(CRED_PREFIX):
                references.append(value[len(CRED_PREFIX):])
    return references


def _warn_invalid_password_value(value) -> None:
    try:
        import logging
        logging.getLogger("bulk_downloader.secrets_store").warning(
            "resolve_password: expected str, got %s — treating as None",
            type(value).__name__,
        )
    except Exception:
        pass


def resolve_password_state(value: str | None) -> tuple[str | None, str]:
    """Resolve a password and preserve why no value was returned.

    State is one of ``empty``, ``invalid``, ``plaintext``, ``resolved``,
    ``locked``, ``missing``, ``unavailable``, or ``unknown``. The legacy
    :func:`resolve_password` API intentionally still returns only the value;
    callers that must distinguish a restart-locked vault from deletion use
    this richer result.
    """
    if not value:
        return None, "empty"
    if not isinstance(value, str):
        _warn_invalid_password_value(value)
        return None, "invalid"
    if not value.startswith(CRED_PREFIX):
        return value, "plaintext"

    ref = value[len(CRED_PREFIX):]
    if not ref:
        return None, "missing"

    try:
        backend = get_backend()
        try:
            unlocked = bool(backend.is_unlocked())
        except Exception:
            return None, "unknown"

        if not unlocked:
            try:
                initialized_fn = getattr(backend, "is_initialized", None)
                initialized = (
                    bool(initialized_fn())
                    if callable(initialized_fn)
                    else bool(backend.list_keys())
                )
            except Exception:
                return None, "unknown"
            if initialized:
                # None from a locked backend is not evidence that this
                # particular reference was deleted.
                return None, "locked"

        try:
            resolved = backend.get(ref)
        except Exception:
            return None, "unknown"
        if resolved is not None:
            return resolved, "resolved"

        try:
            stored_keys = backend.list_keys()
        except Exception:
            return None, "unknown"
        # A named entry that cannot be read is not "missing"; it may be
        # corrupted or its backing keyring may be unavailable.
        return None, ("unavailable" if ref in stored_keys else "missing")
    except Exception:
        return None, "unknown"


def resolve_password(value: str | None) -> str | None:  # INV-006
    """Take whatever's in cfg["password"] and return the real password.

    Three cases:
        - None / empty       -> None (no password set)
        - Starts with @cred: -> look up via backend using the suffix as key
        - Anything else      -> plaintext, return as-is (legacy + plaintext mode)

    Audit 2026-05 (Phase 4 fuzzing): defensively handle non-str inputs.
    The type annotation says str | None, but a corrupted sites_config (e.g.
    hand-edited or imported) could contain an int/list/dict here. Calling
    .startswith() on any of those raises AttributeError. Treat anything
    non-string as "no password" rather than crashing — matches the
    empty-string branch and avoids cascading failures during startup.
    """
    resolved, _state = resolve_password_state(value)
    return resolved


def make_password_reference(site_id: str) -> str:
    """Build the sites_config.json reference string for a site. Stored
    in cfg["password"] when the encrypted backend is active."""
    return CRED_PREFIX + site_password_key(site_id)


# ─── Migration ───────────────────────────────────────────────────────

def find_plaintext_passwords(sites_config: dict) -> list[tuple[str, str]]:
    """Walk sites_config and return [(scope, plaintext_password), ...]
    for every site that still has a plaintext password (not a @cred:
    reference, not None, not empty).

    B11 (v3.66.43): also walks the per-account ``accounts[]`` array used
    by multi-account sites. A top-level password yields a bare site_id
    scope; a per-account password yields a compound scope of the form
    ``"<site_id>::account:<index>"`` so migrate_from_plaintext can route
    it back to the right slot. Before this, a multi-account site with an
    empty top-level password reported zero plaintext passwords while
    every per-account secret sat in sites_config.json in the clear."""
    out = []
    for sid, cfg in (sites_config or {}).items():
        if not isinstance(cfg, dict): continue
        pw = cfg.get("password")
        if pw and isinstance(pw, str) and not pw.startswith(CRED_PREFIX):
            out.append((sid, pw))
        accounts = cfg.get("accounts")
        if isinstance(accounts, list):
            for i, a in enumerate(accounts):
                if not isinstance(a, dict): continue
                apw = a.get("password")
                if apw and isinstance(apw, str) and not apw.startswith(CRED_PREFIX):
                    out.append((f"{sid}::account:{i}", apw))
    return out


def _split_account_scope(scope: str) -> tuple[str, "int | None"]:
    """Split a find_plaintext_passwords scope into (site_id, account_idx).
    A bare site_id returns (site_id, None); a compound
    ``"<sid>::account:<n>"`` returns (sid, n)."""
    sep = "::account:"
    i = scope.find(sep)
    if i == -1:
        return scope, None
    try:
        return scope[:i], int(scope[i + len(sep):])
    except ValueError:
        return scope, None


def account_password_key(site_id: str, account_idx: int) -> str:
    """Backend key for a per-account password (B11, v3.66.43)."""
    return f"bulkdl-site-{site_id}-account-{account_idx}"


def make_account_password_reference(site_id: str, account_idx: int) -> str:
    """sites_config reference string for a per-account password."""
    return CRED_PREFIX + account_password_key(site_id, account_idx)


def migrate_from_plaintext(sites_config: dict) -> tuple[int, list[str]]:
    """Move all plaintext passwords into the active backend and replace
    them with @cred: references. Mutates sites_config in place.

    Returns (count_migrated, errors).

    Caller is expected to persist sites_config after this call. We
    don't do the persist here because app.py owns that path.

    Safety: backend.set() is called BEFORE the plaintext is removed
    from sites_config, so a failure mid-migration leaves the plaintext
    intact. On a roundtrip-verify mismatch the backend write is rolled
    back (B5, v3.66.43) so a password is never left in both the
    encrypted backend and plaintext config."""
    targets = find_plaintext_passwords(sites_config)
    backend = get_backend()
    if backend.name == "plaintext":
        return 0, ["active backend is plaintext; configure encrypted backend first"]
    if hasattr(backend, "is_unlocked") and not backend.is_unlocked():
        return 0, ["backend is locked; unlock first"]
    migrated = 0
    errors = []
    for scope, plaintext in targets:
        sid, acct_idx = _split_account_scope(scope)
        if acct_idx is None:
            key = site_password_key(sid)
        else:
            key = account_password_key(sid, acct_idx)
        try:
            backend.set(key, plaintext)
            # Verify by reading back
            roundtrip = backend.get(key)
            if roundtrip != plaintext:
                # B5 (v3.66.43): roll the backend write back. Without
                # this the same password lives in BOTH the encrypted
                # backend AND plaintext config — migration was supposed
                # to HALVE the attack surface, not double it.
                try:
                    backend.delete(key)
                except Exception as cleanup_e:
                    errors.append(
                        f"{scope}: roundtrip verify failed; backend "
                        f"rollback also failed ({type(cleanup_e).__name__}); "
                        f"password may be in BOTH encrypted backend AND "
                        f"plaintext config — manual cleanup required")
                else:
                    errors.append(
                        f"{scope}: roundtrip verify failed; plaintext "
                        f"kept, backend copy rolled back")
                continue
            # Safe to replace at the right slot
            if acct_idx is None:
                sites_config[sid]["password"] = make_password_reference(sid)
            else:
                sites_config[sid]["accounts"][acct_idx]["password"] = \
                    make_account_password_reference(sid, acct_idx)
            migrated += 1
        except Exception as e:
            errors.append(f"{scope}: {e}")
    return migrated, errors
