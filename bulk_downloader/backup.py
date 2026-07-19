"""v3.43.25: one-click backup + restore.

Bundles the critical state files into a single timestamped zip so the
user can recover from accidental deletion, disk failure, or a botched
config edit without scattered-file backup procedures.

What's in a backup (in order of importance):
  - sites_config.json       site configs (most critical)
  - secrets.json + meta     encrypted password vault (if present)
  - cookies/*.json          per-site session cookies
  - state/*.json            VAPID push keys, heartbeat
  - app_config.json         global config (max concurrent, etc.)
  - learned/*.json          captured selectors per site
  - user_templates.json     custom templates
  - vault_tokens.json       browser-extension pairings
  - queue.db, queue.db-wal, queue.db-shm   SQLite + WAL

What's NOT in a backup:
  - Downloaded media files (way too big; user manages those)
  - profiles/*               Chromium profile dirs (re-downloaded on demand)
  - screenshots/             debug screenshots (re-creatable)
  - bulk_downloader.log      log files (re-creatable)

The format is a standard zip so the user can inspect / partially
restore by hand if needed.

Optional encryption via a user-supplied passphrase — uses the same
PBKDF2-HMAC + Fernet scheme as secrets_store so vault passwords aren't
duplicated in plaintext if the backup is mailed somewhere.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path


def _is_safe_path(base: Path, candidate: Path) -> bool:
    """Resolve both paths and verify candidate is INSIDE base. Used
    on restore to reject zip entries like '../../etc/passwd'."""
    try:
        base_resolved = base.resolve()
        candidate_resolved = (base / candidate).resolve()
        # candidate must be inside base
        candidate_resolved.relative_to(base_resolved)
        return True
    except (ValueError, OSError):
        return False


# ── Backup ────────────────────────────────────────────────────────────

# Files + dirs included in every backup, in priority order. Each entry
# is (source_path, arcname_in_zip). Missing files are silently skipped —
# a fresh install won't have most of these yet.
BACKUP_TARGETS = [
    # Critical config — without these the app can't reconstruct sites
    ("sites_config.json", "sites_config.json"),
    ("app_config.json", "app_config.json"),
    # Vault — encrypted blob; the user's master password is NOT in here
    ("secrets.json", "secrets.json"),
    ("secrets_meta.json", "secrets_meta.json"),
    # Per-site session cookies — without these every site needs re-login
    ("cookies", "cookies"),
    # Learned selectors — losing these means re-teaching every site
    ("learned", "learned"),
    # Custom templates the user wrote
    ("user_templates.json", "user_templates.json"),
    # Extension pairings
    ("vault_tokens.json", "vault_tokens.json"),
    # State (push subscription keys, heartbeat)
    ("state", "state"),
    # SQLite DB — last because it's the biggest and most rebuildable
    ("queue.db", "queue.db"),
    ("queue.db-wal", "queue.db-wal"),
    ("queue.db-shm", "queue.db-shm"),
]


# POS-1 (v3.66.663): members that carry secrets/session data. In encrypt_scope=
# "sensitive" these are encrypted in-place (ciphertext under the same arcname);
# everything else stays plaintext + directly inspectable.
_SENSITIVE_EXACT = {"secrets.json", "secrets_meta.json", "vault_tokens.json"}
_SENSITIVE_PREFIXES = ("cookies/", "state/")
_KDF_ITERATIONS = 600_000


def _is_sensitive_arc(arcname: str) -> bool:
    return (arcname in _SENSITIVE_EXACT
            or any(arcname.startswith(p) for p in _SENSITIVE_PREFIXES))


def _derive_fernet_key(passphrase: str, salt: bytes,
                       iterations: int = _KDF_ITERATIONS):
    """PBKDF2-HMAC-SHA256 -> urlsafe-b64 Fernet key. Same scheme as the whole-
    archive path and secrets_store; the passphrase is never stored."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _iter_files(target_root: Path, src: Path):
    """Yield (file_path, arcname_relative) for every regular file under
    src. Used to flatten directory targets into individual zip entries."""
    if src.is_file():
        yield src, src.name
        return
    if not src.is_dir():
        return
    for f in src.rglob("*"):
        if f.is_file():
            # Skip rotating profile-sync backups (profiles/<site>/<profile>/
            # .sync_backups/<ts>/...): they are backups-of-backups — stale
            # session copies that bloat the archive and shouldn't ride along
            # in a (potentially shared) backup zip.
            if ".sync_backups" in f.parts:
                continue
            # arcname is relative to the dir's name (so cookies/abc.json
            # stays cookies/abc.json in the zip)
            yield f, str(f.relative_to(target_root))


def create_backup(out_path: str | Path, base_dir: str | Path = ".",
                   include_db: bool = True,
                   passphrase: str | None = None,
                   encrypt_scope: str = "all") -> dict:
    """Bundle all critical state into a zip at `out_path`.

    Args:
      out_path: where to write the zip
      base_dir: app working directory (where sites_config.json lives)
      include_db: include queue.db + WAL files (large, but contains
        history + queue state). Default True. Set False for a quick
        config-only backup.
      passphrase: if provided, encrypt the zip's contents using the
        same scheme as secrets_store (PBKDF2 → Fernet). The encrypted
        format wraps the inner zip; the outer file is a small JSON
        manifest. None = plain zip.

    Returns: {
      "ok": bool, "path": str, "size_bytes": int, "files": int,
      "elapsed_ms": float, "encrypted": bool, "skipped": [str],
      "error": str (only when ok=False)
    }
    """
    t0 = time.time()
    base = Path(base_dir)
    out = Path(out_path)

    # Build the zip in memory first so we can encrypt the whole thing
    # if a passphrase is supplied (zipfile.encryption is weak; we use
    # Fernet around the entire payload instead).
    buf = io.BytesIO()
    files_written = 0
    skipped = []
    targets = list(BACKUP_TARGETS)
    if not include_db:
        targets = [t for t in targets if not t[0].startswith("queue.db")]

    # POS-1: sensitive-scope encrypts only the secret/session members in-place.
    do_sensitive = bool(passphrase) and encrypt_scope == "sensitive"
    enc_salt = None
    enc_key = None
    enc_members: list = []
    if do_sensitive:
        try:
            enc_salt = os.urandom(16)
            enc_key = _derive_fernet_key(passphrase, enc_salt)
        except ImportError:
            return {
                "ok": False,
                "error": "cryptography library required for encrypted "
                          "backup. Install with: pip install cryptography",
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
            }

    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED,
                              compresslevel=6) as zf:
            # CAP-4: build the manifest but write it LAST, so it can carry a
            # per-file sha256 map computed as each file is added to the zip.
            manifest = {
                "format_version": 1,
                "created_at": time.time(),
                "created_at_iso": dt.datetime.utcnow().isoformat() + "Z",
                "bd_version": "3.43.25",
                "base_dir": str(base.resolve()),
                "includes_db": include_db,
            }
            checksums: dict = {}
            for src_rel, arc_dir in targets:
                src = base / src_rel
                if not src.exists():
                    skipped.append(src_rel)
                    continue
                for file_path, arcname in _iter_files(base, src):
                    try:
                        if do_sensitive and _is_sensitive_arc(arcname):
                            # ciphertext under the same arcname; checksum is over
                            # the PLAINTEXT so restore can verify after decrypt.
                            from cryptography.fernet import Fernet
                            with open(file_path, "rb") as _sf:
                                _plain = _sf.read()
                            zf.writestr(arcname, Fernet(enc_key).encrypt(_plain))
                            enc_members.append(arcname)
                            checksums[arcname] = hashlib.sha256(_plain).hexdigest()
                            files_written += 1
                        else:
                            zf.write(file_path, arcname)
                            files_written += 1
                            try:
                                with open(file_path, "rb") as _fh:
                                    checksums[arcname] = hashlib.sha256(
                                        _fh.read()).hexdigest()
                            except OSError:
                                pass
                    except (OSError, PermissionError) as e:
                        skipped.append(f"{arcname}: {type(e).__name__}")
            # CAP-4: manifest last, now carrying the integrity map. Restore reads
            # entries by name, so writing _manifest.json last is fine.
            manifest["checksum_algo"] = "sha256"
            manifest["checksums"] = checksums
            if do_sensitive:
                manifest["encryption"] = {
                    "scope": "sensitive",
                    "algo": "pbkdf2-sha256-fernet",
                    "salt": base64.b64encode(enc_salt).decode("ascii"),
                    "iterations": _KDF_ITERATIONS,
                    "members": enc_members,
                }
            zf.writestr("_manifest.json", json.dumps(manifest, indent=2))
    except Exception as e:
        return {
            "ok": False, "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    payload = buf.getvalue()

    # Optional whole-archive encryption — wrap with Fernet using a PBKDF2-derived
    # key. Skipped for encrypt_scope="sensitive" (already encrypted in-place).
    encrypted = do_sensitive
    if passphrase and not do_sensitive:
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            salt = os.urandom(16)
            # 600k iterations matches OWASP recommendation for PBKDF2-SHA256
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                              salt=salt, iterations=600_000)
            key = base64.urlsafe_b64encode(
                kdf.derive(passphrase.encode("utf-8")))
            f = Fernet(key)
            encrypted_payload = f.encrypt(payload)
            # Outer format: 4-byte magic + 16-byte salt + Fernet blob.
            # Magic distinguishes encrypted from plain so restore can
            # branch without prompting.
            out_payload = b"BDBK" + salt + encrypted_payload
            encrypted = True
        except ImportError:
            return {
                "ok": False,
                "error": "cryptography library required for encrypted "
                          "backup. Install with: pip install cryptography",
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"encryption failed: {type(e).__name__}: {e}",
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
            }
    else:
        out_payload = payload

    # Atomic write — same .tmp + replace pattern as everywhere else
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_bytes(out_payload)
        tmp.replace(out)
    except OSError as e:
        return {
            "ok": False,
            "error": f"write failed: {type(e).__name__}: {e}",
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    return {
        "ok": True,
        "path": str(out),
        "size_bytes": len(out_payload),
        "files": files_written,
        "skipped": skipped,
        "encrypted": encrypted,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


# ── Restore ───────────────────────────────────────────────────────────

def restore_backup(backup_path: str | Path,
                    target_dir: str | Path = ".",
                    passphrase: str | None = None,
                    dry_run: bool = False) -> dict:
    """Restore a backup zip into `target_dir`. Overwrites existing files.

    Args:
      backup_path: path to backup zip (plain or BDBK-encrypted)
      target_dir: where to restore to. Default '.' (current dir).
      passphrase: required when restoring an encrypted backup.
      dry_run: if True, just verify the backup is readable and list
        what would be written; don't touch the filesystem.

    Returns: {
      "ok": bool, "files_restored": int, "files_skipped": int,
      "manifest": {...}, "error": str (when ok=False),
      "elapsed_ms": float, "warnings": [str]
    }

    Safety:
      - Zip entries with absolute paths or '..' traversal are rejected
      - The target dir is created if missing
      - Existing files are overwritten silently (caller can dry_run first
        to preview)
    """
    t0 = time.time()
    target = Path(target_dir)
    bp = Path(backup_path)

    if not bp.exists():
        return {"ok": False, "error": f"backup file not found: {bp}",
                 "elapsed_ms": round((time.time() - t0) * 1000, 1)}

    try:
        raw = bp.read_bytes()
    except OSError as e:
        return {"ok": False, "error": f"read failed: {e}",
                 "elapsed_ms": round((time.time() - t0) * 1000, 1)}

    # Detect encryption by magic header
    if raw.startswith(b"BDBK"):
        if not passphrase:
            return {"ok": False,
                     "error": "backup is encrypted; passphrase required",
                     "encrypted": True,
                     "elapsed_ms": round((time.time() - t0) * 1000, 1)}
        try:
            from cryptography.fernet import Fernet, InvalidToken
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            salt = raw[4:20]
            encrypted_payload = raw[20:]
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                              salt=salt, iterations=600_000)
            key = base64.urlsafe_b64encode(
                kdf.derive(passphrase.encode("utf-8")))
            f = Fernet(key)
            payload = f.decrypt(encrypted_payload)
        except InvalidToken:
            return {"ok": False, "error": "incorrect passphrase",
                     "elapsed_ms": round((time.time() - t0) * 1000, 1)}
        except ImportError:
            return {"ok": False,
                     "error": "cryptography library required to read "
                              "encrypted backup. pip install cryptography",
                     "elapsed_ms": round((time.time() - t0) * 1000, 1)}
        except Exception as e:
            return {"ok": False,
                     "error": f"decrypt failed: {type(e).__name__}: {e}",
                     "elapsed_ms": round((time.time() - t0) * 1000, 1)}
    else:
        payload = raw

    # Parse the inner zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload), "r")
    except zipfile.BadZipFile as e:
        return {"ok": False, "error": f"not a valid backup zip: {e}",
                 "elapsed_ms": round((time.time() - t0) * 1000, 1)}

    warnings = []
    files_restored = 0
    files_skipped = 0
    manifest = None
    # CAP-4: integrity verification against the manifest's per-file sha256 map.
    manifest_checksums: dict = {}
    checksum_failures: list = []
    checksums_verified = 0
    # AUDIT v3.43.48: zip bomb defense. A malicious backup could
    # decompress to terabytes. Cap per-member at 256MB and total
    # restored bytes at 10GB. Real backups are well under either
    # bound (sites_config + cookies + learned selectors compress
    # to a few hundred KB).
    MAX_MEMBER_BYTES = 256 * 1024 * 1024
    MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024
    total_bytes = 0

    try:
        # Read manifest first
        try:
            manifest = json.loads(zf.read("_manifest.json").decode("utf-8"))
        except (KeyError, json.JSONDecodeError):
            warnings.append("_manifest.json missing or malformed; "
                            "proceeding with best-effort restore")
        manifest_checksums = (manifest or {}).get("checksums") or {}

        # POS-1: sensitive-scope backups encrypt certain members in-place; set up
        # the decryption key from the manifest's salt (fail-closed on a missing/
        # wrong passphrase). enc_members names which members are ciphertext.
        _enc_meta = (manifest or {}).get("encryption") or {}
        _enc_members = set()
        _enc_fernet = None
        if _enc_meta.get("scope") == "sensitive":
            if not passphrase:
                return {"ok": False,
                        "error": "backup has encrypted members; passphrase required",
                        "encrypted": True,
                        "elapsed_ms": round((time.time() - t0) * 1000, 1)}
            try:
                from cryptography.fernet import Fernet
                _salt = base64.b64decode(_enc_meta.get("salt", ""))
                _iters = int(_enc_meta.get("iterations", _KDF_ITERATIONS))
                _enc_fernet = Fernet(_derive_fernet_key(passphrase, _salt, _iters))
                _enc_members = set(_enc_meta.get("members") or [])
                # Fail-closed: validate the passphrase by decrypting ONE member
                # up front, so a wrong passphrase aborts BEFORE any file is
                # written (no partial restore). All members share the key.
                for _probe in _enc_members:
                    try:
                        _enc_fernet.decrypt(zf.read(_probe))
                    except KeyError:
                        continue  # listed but absent; try another
                    except Exception:
                        return {"ok": False, "error": "incorrect passphrase",
                                "elapsed_ms": round((time.time() - t0) * 1000, 1)}
                    break
            except ImportError:
                return {"ok": False,
                        "error": "cryptography library required to read this backup",
                        "elapsed_ms": round((time.time() - t0) * 1000, 1)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False,
                        "error": f"decrypt setup failed: {type(e).__name__}",
                        "elapsed_ms": round((time.time() - t0) * 1000, 1)}

        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)

        for name in zf.namelist():
            if name == "_manifest.json":
                continue
            # Reject path traversal and absolute paths
            if name.startswith("/") or ".." in Path(name).parts:
                warnings.append(f"skipped unsafe path: {name}")
                files_skipped += 1
                continue
            # Verify resolved path is inside target
            if not _is_safe_path(target, Path(name)):
                warnings.append(f"skipped path-escape: {name}")
                files_skipped += 1
                continue
            # AUDIT v3.43.48: check declared uncompressed size BEFORE
            # reading. zipfile.ZipInfo.file_size is the uncompressed
            # size — if a malicious zip lies here, the per-member read
            # cap below will still bound memory.
            try:
                info = zf.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES:
                    warnings.append(
                        f"skipped oversized member: {name} "
                        f"({info.file_size} bytes > {MAX_MEMBER_BYTES} cap)")
                    files_skipped += 1
                    continue
                if total_bytes + info.file_size > MAX_TOTAL_BYTES:
                    warnings.append(
                        f"aborting restore: total uncompressed size would "
                        f"exceed {MAX_TOTAL_BYTES} bytes")
                    break
            except KeyError:
                # Member listed in namelist but getinfo fails — skip
                warnings.append(f"skipped (no zipinfo): {name}")
                files_skipped += 1
                continue
            if dry_run:
                files_restored += 1
                total_bytes += info.file_size
                # CAP-4: a dry_run over a checksummed backup is an INTEGRITY
                # CHECK -- read each member, hash, compare, don't touch disk.
                expected = manifest_checksums.get(name)
                if expected:
                    try:
                        with zf.open(name) as _sfp:
                            _data = _sfp.read(MAX_MEMBER_BYTES + 1)
                        if name in _enc_members and _enc_fernet is not None:
                            _data = _enc_fernet.decrypt(_data)
                        if hashlib.sha256(_data).hexdigest() != expected:
                            checksum_failures.append(name)
                        else:
                            checksums_verified += 1
                    except Exception:  # noqa: BLE001
                        checksum_failures.append(name)
                continue
            dest = target / name
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Atomic-write to .tmp then replace. Use bounded read
                # so a zip that lied about file_size still can't OOM us.
                with zf.open(name) as src_fp:
                    data = src_fp.read(MAX_MEMBER_BYTES + 1)
                if name in _enc_members and _enc_fernet is not None:
                    data = _enc_fernet.decrypt(data)  # POS-1: sensitive member
                if len(data) > MAX_MEMBER_BYTES:
                    warnings.append(
                        f"skipped (member exceeded cap during read): {name}")
                    files_skipped += 1
                    continue
                # CAP-4: verify integrity against the manifest checksum. A
                # mismatch is surfaced (integrity_ok=False) but still restored --
                # the operator explicitly asked to restore; the signal is theirs
                # to act on. Run a dry_run first to check without writing.
                expected = manifest_checksums.get(name)
                if expected:
                    if hashlib.sha256(data).hexdigest() != expected:
                        checksum_failures.append(name)
                    else:
                        checksums_verified += 1
                tmp_dest = dest.with_suffix(dest.suffix + ".bdtmp")
                tmp_dest.write_bytes(data)
                tmp_dest.replace(dest)
                files_restored += 1
                total_bytes += len(data)
            except OSError as e:
                warnings.append(f"{name}: {type(e).__name__}: {e}")
                files_skipped += 1
    finally:
        zf.close()

    return {
        "ok": True,
        "files_restored": files_restored,
        "files_skipped": files_skipped,
        "manifest": manifest,
        "warnings": warnings,
        "dry_run": dry_run,
        # CAP-4 integrity signal: True when every checksummed member matched
        # (or the backup predates checksums -- nothing to verify).
        "integrity_ok": len(checksum_failures) == 0,
        "checksum_failures": checksum_failures,
        "checksums_verified": checksums_verified,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


def default_backup_filename() -> str:
    """Suggest a filename like `bd_backup_2026-05-12_0537.zip`. Used
    when the user doesn't specify a path."""
    now = dt.datetime.now()
    return f"bd_backup_{now.strftime('%Y-%m-%d_%H%M')}.zip"
