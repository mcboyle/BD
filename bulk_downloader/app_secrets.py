"""secrets API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/secrets views moved onto a Flask Blueprint.
Endpoint labels gain a "secrets." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import json
from flask import Blueprint, jsonify, request

secrets_bp = Blueprint("secrets", __name__)

def _reject_if_vault_token(*_a, **_k):
    """Delegate to app._reject_if_vault_token at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_reject_if_vault_token")(*_a, **_k)

def _require_vault_token(*_a, **_k):
    """Delegate to app._require_vault_token at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_require_vault_token")(*_a, **_k)

def _save_sites_config(*_a, **_k):
    """Delegate to app._save_sites_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_sites_config")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@secrets_bp.route("/api/secrets/status", methods=["GET"])
def api_secrets_status():
    """Report the active backend, its lock status, and whether there
    are any plaintext passwords still in sites_config.json. The
    settings UI uses this to decide whether to show the migration
    banner and the "unlock" form."""
    s_cfg = _app_s_cfg()
    from . import secrets_store as ss
    backend = ss.get_backend()
    plaintext = ss.find_plaintext_passwords(s_cfg)
    status = {
        "backend": backend.name,
        "is_unlocked": (
            backend.is_unlocked()
            if hasattr(backend, "is_unlocked") else True
        ),
        "is_initialized": getattr(
            backend, "is_initialized", lambda: True
        )(),
        "plaintext_count": len(plaintext),
        "plaintext_sites": [sid for sid, _ in plaintext],
        "keyring_available": ss._KEYRING_AVAILABLE,
        "crypto_available": ss._CRYPTO_AVAILABLE,
    }
    try:
        stored_keys = backend.list_keys()
    except ss.SecretsUnreadableError as error:
        # Row 432: distinct from ordinary integrity damage. The file could not
        # be read at all, so the settings UI must not offer first-use setup.
        return jsonify({
            **status,
            "ok": False,
            "state": "unreadable",
            "stored_keys": None,
            "error": str(error)[:240],
        }), 409
    except ss.SecretsIntegrityError as error:
        # The settings surface must retain a machine-readable diagnosis when
        # inventory cannot be trusted. An HTML 500 hides the damaged-vault
        # state and an empty list would launder unavailable evidence as zero.
        return jsonify({
            **status,
            "ok": False,
            "state": "integrity_error",
            "stored_keys": None,
            "error": str(error)[:240],
        }), 409
    return jsonify({
        **status,
        "ok": True,
        "stored_keys": stored_keys,
    })
@secrets_bp.route("/api/secrets/usage", methods=["GET"])
def api_secrets_usage():
    """Cut 7: read-only secret USAGE map — which stored secret keys exist and
    which sites reference them, by NAME / reference only. It never returns any
    secret VALUE (no token, password, or key material). Fail-open: a backend
    hiccup yields empty lists, not an error."""
    s_cfg = _app_s_cfg()
    from . import secrets_store as ss
    try:
        backend = ss.get_backend()
        stored = list(backend.list_keys() or [])
    except Exception:
        stored = []
    # Which sites reference a stored secret (by key name). We only read the
    # reference, never the value.
    usage = {}
    try:
        for key in stored:
            refs = []
            for sid, scfg in (s_cfg or {}).items():
                try:
                    blob = json.dumps(scfg)
                except Exception:
                    blob = ""
                if key and key in blob:
                    refs.append(sid)
            usage[key] = refs
    except Exception:
        usage = {k: [] for k in stored}
    unreferenced = [k for k in stored if not usage.get(k)]
    # last-rotated AGE per stored key (age only -- never a secret value).
    # Fail-open: a metadata hiccup yields an empty map, not an error.
    try:
        rotation = ss.rotation_ages()
        if stored:
            rotation = {k: v for k, v in rotation.items() if k in stored}
    except Exception:
        rotation = {}
    return jsonify({
        "ok": True,
        "stored_keys": stored,        # names only
        "usage": usage,               # key name -> [site ids]
        "unreferenced": unreferenced,
        "rotation": rotation,         # key name -> {rotated_at_epoch, age_seconds, age_days}; NO values
    })
@secrets_bp.route("/api/secrets/configure", methods=["POST"])
def api_secrets_configure():
    """Switch the active backend. Body: {"backend": "windows_credential"|
    "master_password"|"plaintext"}. Does NOT migrate existing data —
    use /api/secrets/migrate for that."""
    from . import secrets_store as ss
    name = (request.json or {}).get("backend", "")
    if ss.configure_backend(name):
        return jsonify({"ok": True, "backend": ss.get_backend_name()})
    return jsonify({"ok": False, "error": f"backend {name!r} unavailable"}), 400
@secrets_bp.route("/api/secrets/unlock", methods=["POST"])
def api_secrets_unlock():
    """Unlock the master-password backend. Body: {"password": "..."}.
    No-op (returns ok:True) for backends that don't need unlocking
    (Windows Credential Manager, plaintext).

    For a fresh master-password backend this is also the first-use setup path:
    the backend durably commits the password before success, and the response
    distinguishes that initialization from opening an existing vault.
    """
    from . import secrets_store as ss
    backend = ss.get_backend()
    if not hasattr(backend, "unlock"):
        return jsonify({"ok": True, "note": "backend doesn't require unlocking"})
    password = (request.json or {}).get("password", "")
    if not password:
        return jsonify({"ok": False, "error": "password required"}), 400
    # NEW-9: shared escalating back-off with change_password (same secret).
    from . import auth_throttle as _at
    allowed, retry = _at.check(_at.LABEL_MASTER_PASSWORD)
    if not allowed:
        resp = jsonify({"ok": False,
                        "error": f"too many attempts; try again in {retry:.0f}s"})
        resp.headers["Retry-After"] = str(int(retry) + 1)
        return resp, 429

    def reject_incoherent_unlock_status():
        lock = getattr(backend, "lock", None)
        if callable(lock):
            try:
                lock()
            except Exception:
                pass
        return jsonify({
            "ok": False,
            "state": "unknown",
            "error": "backend returned an incoherent unlock status",
        }), 500

    try:
        unlock_with_status = getattr(backend, "unlock_with_status", None)
        if callable(unlock_with_status):
            outcome = unlock_with_status(
                password, minimum_initial_length=8
            )
        else:
            # Compatibility for duck-typed test/plugin backends.  The real
            # MasterPasswordBackend uses the serialized method above, so its
            # first-use classification never comes from an outside-lock read.
            # A custom backend cannot claim ``initialized_now`` from this
            # non-atomic path; its post-operation status is used only after
            # the coherence check below.
            unlocked = backend.unlock(password)
            now_initialized = bool(
                getattr(backend, "is_initialized", lambda: True)()
            )
            outcome = {
                "unlocked": unlocked,
                "initialized_now": False,
                "is_initialized": now_initialized,
                "is_unlocked": (
                    backend.is_unlocked()
                    if hasattr(backend, "is_unlocked") else unlocked
                ),
            }
    except ss.SecretsPasswordPolicyError as e:
        return jsonify({"ok": False, "state": "uninitialized",
                        "error": str(e)}), 400
    except ss.SecretsUnreadableError as e:
        # Row 432: this refusal is neither a bad password nor a success. The
        # durable store could not be read, so no password was committed and
        # the operator's file was left untouched. Do not throttle it -- it is
        # a storage failure, not an authentication attempt.
        return jsonify({
            "ok": False,
            "state": "unreadable",
            "is_initialized": True,
            "is_unlocked": bool(
                backend.is_unlocked()
                if hasattr(backend, "is_unlocked") else False
            ),
            "error": str(e),
        }), 409
    except ss.SecretsIntegrityError as e:
        failure_status = getattr(e, "vault_status", {})
        if "is_initialized" in failure_status:
            failed_initialized = bool(failure_status["is_initialized"])
        else:
            failed_initialized = bool(
                getattr(backend, "is_initialized", lambda: True)()
            )
        if "is_unlocked" in failure_status:
            failed_unlocked = bool(failure_status["is_unlocked"])
        else:
            failed_unlocked = (
                bool(backend.is_unlocked())
                if hasattr(backend, "is_unlocked") else False
            )
        return jsonify({
            "ok": False,
            "state": "integrity_error",
            "is_initialized": failed_initialized,
            "is_unlocked": failed_unlocked,
            "error": str(e),
        }), 409
    except ss.SecretsPersistError as e:
        failure_status = getattr(e, "vault_status", {})
        if "is_initialized" in failure_status:
            now_initialized = bool(failure_status["is_initialized"])
        else:
            now_initialized = bool(
                getattr(backend, "is_initialized", lambda: True)()
            )
        if "is_unlocked" in failure_status:
            now_unlocked = bool(failure_status["is_unlocked"])
        else:
            now_unlocked = (
                bool(backend.is_unlocked())
                if hasattr(backend, "is_unlocked") else True
            )
        if now_initialized:
            error = (
                "vault verifier upgrade could not be persisted; the existing "
                "vault remains locked and unchanged"
            )
        else:
            error = (
                "vault initialization could not be persisted; the master "
                "password was not committed"
            )
        return jsonify({
            "ok": False,
            "state": "uninitialized" if not now_initialized else "locked",
            "is_initialized": now_initialized,
            "is_unlocked": now_unlocked,
            "error": error,
        }), 500
    required_status = (
        "unlocked",
        "initialized_now",
        "is_initialized",
        "is_unlocked",
    )
    if (
        not isinstance(outcome, dict)
        or any(type(outcome.get(field)) is not bool for field in required_status)
    ):
        return reject_incoherent_unlock_status()
    unlocked = outcome["unlocked"]
    now_initialized = outcome["is_initialized"]
    now_unlocked = outcome["is_unlocked"]
    initialized_now = outcome["initialized_now"]
    if (
        unlocked != now_unlocked
        or (now_unlocked and not now_initialized)
        or (
            initialized_now
            and not (unlocked and now_initialized and now_unlocked)
        )
    ):
        # Never publish the impossible "unlocked but uninitialized" pair,
        # even for a duck-typed/plugin backend with a broken status contract.
        # Best-effort relock prevents a rejected response from leaving access
        # enabled behind the API's back; UNKNOWN avoids inventing a coherent
        # snapshot when the backend did not provide one.
        return reject_incoherent_unlock_status()
    if unlocked:
        _at.record_success(_at.LABEL_MASTER_PASSWORD)
        return jsonify({
            "ok": True,
            "state": "initialized" if initialized_now else "unlocked",
            "initialized_now": initialized_now,
            "is_initialized": now_initialized,
            "is_unlocked": bool(outcome["is_unlocked"]),
        })
    if not now_initialized:
        # Defensive compatibility for a backend that reports a failed first
        # initialization without raising SecretsPersistError.  This is a
        # storage failure, not an authentication failure, so do not throttle it.
        return jsonify({
            "ok": False,
            "state": "uninitialized",
            "is_initialized": False,
            "is_unlocked": False,
            "error": (
                "vault initialization failed; the master password was not "
                "committed"
            ),
        }), 500
    _at.record_failure(_at.LABEL_MASTER_PASSWORD)
    return jsonify({"ok": False, "state": "locked",
                    "error": "incorrect password"}), 401
@secrets_bp.route("/api/secrets/lock", methods=["POST"])
def api_secrets_lock():
    """Lock the master-password backend (forget the derived key)."""
    from . import secrets_store as ss
    backend = ss.get_backend()
    if hasattr(backend, "lock"): backend.lock()
    return jsonify({"ok": True})
@secrets_bp.route("/api/secrets/change_password", methods=["POST"])
def api_secrets_change_password():
    """Re-encrypt all stored passwords with a new master password.
    Body: {"old_password": "...", "new_password": "..."}.
    Only valid for the master_password backend; other backends 400."""
    from . import secrets_store as ss
    backend = ss.get_backend()
    if not hasattr(backend, "change_password"):
        return jsonify({"ok": False,
                        "error": "current backend doesn't use a master password"}), 400
    data = request.json or {}
    old = data.get("old_password", "")
    new = data.get("new_password", "")
    if not new or len(new) < 8:
        return jsonify({"ok": False,
                        "error": "new password must be at least 8 characters"}), 400
    if not bool(getattr(backend, "is_initialized", lambda: True)()):
        # change_password is rotation, not setup.  In particular it must not
        # call unlock(old) on a fresh vault and accidentally commit either
        # submitted password.
        return jsonify({
            "ok": False,
            "state": "uninitialized",
            "error": (
                "vault is uninitialized; initialize it through the unlock "
                "flow before changing its password"
            ),
        }), 409
    # NEW-9: shared escalating back-off with the unlock route (same secret).
    from . import auth_throttle as _at
    allowed, retry = _at.check(_at.LABEL_MASTER_PASSWORD)
    if not allowed:
        resp = jsonify({"ok": False,
                        "error": f"too many attempts; try again in {retry:.0f}s"})
        resp.headers["Retry-After"] = str(int(retry) + 1)
        return resp, 429
    try:
        change_with_status = getattr(
            backend, "change_password_with_status", None
        )
        if callable(change_with_status):
            # The real backend verifies initialization + old password and
            # rotates under one lock.  A separate unlock preflight could race
            # a failed first-use publish and initialize the vault itself.
            outcome = change_with_status(old, new)
            if not isinstance(outcome, dict):
                return jsonify({
                    "ok": False,
                    "state": "unknown",
                    "error": "backend returned an incoherent rotation status",
                }), 500
            reason = outcome.get("reason")
            changed = outcome.get("changed")
        else:
            # Compatibility path for older duck-typed/plugin backends.  The
            # durable MasterPasswordBackend always uses the atomic path above.
            if not backend.unlock(old):
                reason = "incorrect_password"
                changed = False
            else:
                changed = bool(backend.change_password(old, new))
                reason = "changed" if changed else "corrupt"
        if (
            type(changed) is not bool
            or (changed, reason) not in {
                (True, "changed"),
                (False, "incorrect_password"),
                (False, "corrupt"),
            }
        ):
            return jsonify({
                "ok": False,
                "state": "unknown",
                "error": "backend returned an incoherent rotation status",
            }), 500
        if reason == "incorrect_password":
            _at.record_failure(_at.LABEL_MASTER_PASSWORD)
            return jsonify({"ok": False,
                            "error": "current password is incorrect"}), 401
        _at.record_success(_at.LABEL_MASTER_PASSWORD)
        if changed and reason == "changed":
            return jsonify({"ok": True})
        if reason != "corrupt":
            return jsonify({
                "ok": False,
                "state": "unknown",
                "error": "backend returned an incoherent rotation status",
            }), 500
        return jsonify({
            "ok": False,
            "error": ("rotation aborted because one or more stored "
                      "secrets could not be decrypted (data corruption). "
                      "Your old password is still in effect; no data has "
                      "been changed. Check app logs for which entry "
                      "failed."),
        }), 500
    except ss.SecretsUninitializedError as e:
        return jsonify({"ok": False, "state": "uninitialized",
                        "error": str(e)}), 409
    except ss.SecretsUnreadableError as e:
        # Row 432: the generic SecretsIntegrityError handler below already
        # fails closed on this subclass, but rotation must name the condition
        # -- "integrity_error" reads as damaged CONTENT, and this file could
        # not be read at all. No password was committed and nothing rotated.
        return jsonify({"ok": False, "state": "unreadable",
                        "error": str(e)}), 409
    except ss.SecretsIntegrityError as e:
        return jsonify({"ok": False, "state": "integrity_error",
                        "error": str(e)}), 409
    except ss.SecretsPersistError as e:
        return jsonify({
            "ok": False,
            "error": (f"rotation failed during persist ({e}). Your old "
                      "password is still in effect; check disk space and "
                      "permissions on secrets.json."),
        }), 500
@secrets_bp.route("/api/secrets/migrate", methods=["POST"])
def api_secrets_migrate():
    """Move all plaintext passwords from sites_config.json to the
    active encrypted backend. Replaces each plaintext value with a
    @cred: reference and persists sites_config.json.

    The backend must be unlocked (for master_password mode). Returns
    {migrated: N, errors: [...]}."""
    s_cfg = _app_s_cfg()
    from . import secrets_store as ss
    count, errors = ss.migrate_from_plaintext(s_cfg)
    if count > 0:
        _save_sites_config()
    return jsonify({
        "ok": True,
        "migrated": count,
        "errors": errors,
        "remaining_plaintext": len(ss.find_plaintext_passwords(s_cfg)),
    })
@secrets_bp.route("/api/secrets/import_file", methods=["POST"])
def api_secrets_import_file():
    """Parse an uploaded password-manager export. Returns the records
    for the UI to display; does NOT save anything yet — that's a
    separate /api/secrets/import_apply call once the user picks which
    entries to keep.

    Body: multipart with a 'file' field, OR JSON with {"content": "..."}.

    AUDIT v3.43.47: cap input at 10MB. The largest realistic
    password-manager export is well under 1MB (Bitwarden exports
    average ~50KB; 1Password .1pif similar). 10MB is generous
    headroom and prevents a 10GB upload from being read into memory.
    """
    from . import password_import as pi
    MAX_IMPORT_BYTES = 10 * 1024 * 1024
    try:
        if request.files and "file" in request.files:
            f = request.files["file"]
            # Read up to MAX+1 to detect overflow without DoS
            data = f.read(MAX_IMPORT_BYTES + 1)
            if len(data) > MAX_IMPORT_BYTES:
                return jsonify({"ok": False,
                                  "error": "file too large (>10MB); paste contents "
                                           "via the 'content' field instead, or split "
                                           "the export"}), 413
        else:
            data = (request.json or {}).get("content", "")
            if isinstance(data, str):
                if len(data) > MAX_IMPORT_BYTES:
                    return jsonify({"ok": False,
                                      "error": "content too large (>10MB)"}), 413
                data = data.encode("utf-8")
        fmt, records = pi.import_passwords(data)
        return jsonify({"ok": True, "format": fmt, "records": records,
                        "count": len(records)})
    except pi.FormatNotRecognized as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
@secrets_bp.route("/api/secrets/import_apply", methods=["POST"])
def api_secrets_import_apply():
    """Save selected records into the active backend.

    Body: {"records": [{name, url, username, password, ...}, ...],
           "site_ids": ["wow", "ultraf", ...]}  # parallel arrays;
           # site_id "" or null means "don't link to any site, just
           # store under the entry's name as the key".

    The records' passwords land in the backend keyed by either the
    site_password_key (when site_id is provided) or a derived
    bulkdl-import-<slug> key (when no site is linked).

    Returns {saved: N, skipped: M, errors: [...]}.
    """
    s_cfg = _app_s_cfg()
    from . import secrets_store as ss
    backend = ss.get_backend()
    if backend.name == "plaintext":
        return jsonify({"ok": False, "error":
            "active backend is plaintext; switch to an encrypted backend first"
        }), 400
    if hasattr(backend, "is_unlocked") and not backend.is_unlocked():
        return jsonify({"ok": False, "error": "backend is locked"}), 401

    data = request.json or {}
    records = data.get("records") or []
    site_ids = data.get("site_ids") or []
    if len(site_ids) < len(records):
        site_ids = site_ids + [""] * (len(records) - len(site_ids))

    import re as _re
    saved = 0
    skipped = 0
    errors = []
    # B14 (v3.66.43): track assigned standalone slugs per batch so two
    # records that collide (same name, or same name+username) don't
    # silently overwrite each other in the backend.
    _assigned_slugs: dict[str, int] = {}
    for rec, sid in zip(records, site_ids):
        try:
            password = rec.get("password") or ""
            if not password:
                skipped += 1
                continue
            if sid:
                key = ss.site_password_key(sid)
                # Update the site config to use a @cred: reference
                if sid in s_cfg:
                    s_cfg[sid]["password"] = ss.make_password_reference(sid)
                    if rec.get("username"):
                        s_cfg[sid]["username"] = rec["username"]
            else:
                # Standalone import: slug of name + username, with a
                # per-batch counter for residual collisions.
                name_part = _re.sub(
                    r"[^a-zA-Z0-9]+", "-",
                    rec.get("name") or "untitled").strip("-").lower()[:30] or "untitled"
                user_part = _re.sub(
                    r"[^a-zA-Z0-9]+", "-",
                    rec.get("username") or "").strip("-").lower()[:20]
                base_slug = (f"{name_part}-{user_part}" if user_part else name_part)[:40]
                count = _assigned_slugs.get(base_slug, 0)
                _assigned_slugs[base_slug] = count + 1
                slug = base_slug if count == 0 else f"{base_slug}-{count + 1}"[:40]
                key = f"bulkdl-import-{slug}"
            backend.set(key, password)
            saved += 1
        except Exception as e:
            errors.append(f"{rec.get('name','?')}: {e}")

    if any(sid for sid in site_ids if sid):
        _save_sites_config()
    return jsonify({"ok": True, "saved": saved, "skipped": skipped,
                    "errors": errors})
@secrets_bp.route("/api/secrets/delete", methods=["POST"])
def api_secrets_delete():
    """Remove a stored password from the active backend. Body:
    {"key": "bulkdl-site-..."} OR {"site_id": "wow"}. The latter
    derives the canonical key for that site."""
    s_cfg = _app_s_cfg()
    from . import secrets_store as ss
    data = request.json or {}
    if data.get("site_id"):
        key = ss.site_password_key(data["site_id"])
    else:
        key = data.get("key", "")
    if not key:
        return jsonify({"ok": False, "error": "key or site_id required"}), 400
    try:
        removed = ss.get_backend().delete(key)
    except ss.SecretsUnlockRequiredError as e:
        # The final raw user ciphertext -- or final structurally usable legacy
        # ciphertext -- is a destructive boundary regardless of verifier
        # presence or shape.  Do not remove it or clear the matching config
        # reference until this process has successfully unlocked the vault.
        return jsonify({
            "ok": False,
            "state": "locked",
            "requires_unlock": True,
            "error": str(e),
        }), 409
    # B16 (v3.66.43): when deleting by site_id, also clear the matching
    # @cred: reference so the next login sees "no credentials" instead of
    # a dangling reference that resolves to None. Deliberately narrow:
    # only fires when the stored value EXACTLY equals the canonical
    # reference, so a hand-edited plaintext or non-canonical reference is
    # never clobbered.
    config_cleaned = False
    if data.get("site_id"):
        sid = data["site_id"]
        site = s_cfg.get(sid) if isinstance(s_cfg, dict) else None
        if isinstance(site, dict):
            if site.get("password", "") == ss.make_password_reference(sid):
                site["password"] = ""
                config_cleaned = True
        if config_cleaned:
            _save_sites_config()
    return jsonify({"ok": True, "removed": removed, "key": key,
                    "config_cleaned": config_cleaned})
@secrets_bp.route("/api/secrets/extension/pair_issue", methods=["POST"])
def api_secrets_extension_pair_issue():
    """Server-side step: generate a pairing token. The vault settings
    UI calls this and shows the result as a QR code / copyable string.

    Requires the same auth as other settings endpoints (cookie+CSRF
    or BD_AUTH_TOKEN bearer). NOT accessible with a vault token —
    pairing must come from the main app UI."""
    _rej = _reject_if_vault_token()
    if _rej is not None:
        return _rej
    from . import extension_vault as _ev
    token = _ev.issue_pairing_token()
    return jsonify({
        "ok": True,
        "pairing_token": token,
        "expires_in_seconds": _ev.PAIRING_TOKEN_EXPIRY_SECONDS,
    })
@secrets_bp.route("/api/secrets/extension/pair", methods=["POST"])
def api_secrets_extension_pair():
    """Extension-side step: redeem a pairing token for a long-lived
    vault token. No prior auth needed — the pairing token IS the auth.

    Body: {"pairing_token": "...", "label": "Chrome on My Laptop"}
    The label is shown in the vault settings to identify which
    extensions are paired.

    Returns the vault token. Extension stores it in chrome.storage.local
    and sends as Bearer auth on subsequent calls."""
    from . import extension_vault as _ev
    data = request.json or {}
    pairing = data.get("pairing_token", "")
    label = data.get("label", "extension")
    if not pairing:
        return jsonify({"ok": False, "error": "pairing_token required"}), 400
    vault_token = _ev.redeem_pairing_token(pairing, extension_label=label)
    if not vault_token:
        return jsonify({"ok": False,
            "error": "pairing token unknown or expired"}), 401
    return jsonify({"ok": True, "vault_token": vault_token, "label": label})
@secrets_bp.route("/api/secrets/extension/list_paired", methods=["GET"])
def api_secrets_extension_list_paired():
    """Vault settings UI: list paired extensions with metadata.
    Doesn't return the raw vault token, only a short prefix for
    identification and revocation."""
    _rej = _reject_if_vault_token()
    if _rej is not None:
        return _rej
    from . import extension_vault as _ev
    return jsonify({"ok": True, "extensions": _ev.list_vault_tokens()})
@secrets_bp.route("/api/secrets/extension/revoke", methods=["POST"])
def api_secrets_extension_revoke():
    """Vault settings UI: revoke a paired extension by its short ID
    prefix. Body: {"id": "abc12345"}."""
    _rej = _reject_if_vault_token()
    if _rej is not None:
        return _rej
    from . import extension_vault as _ev
    data = request.json or {}
    prefix = data.get("id", "")
    if not prefix:
        return jsonify({"ok": False, "error": "id required"}), 400
    removed = _ev.revoke_by_prefix(prefix)
    return jsonify({"ok": True, "removed": removed})
@secrets_bp.route("/api/secrets/extension/list_for_origin", methods=["GET"])
def api_secrets_extension_list_for_origin():
    """Extension API: return entries matching a URL. Used by the
    content script to decide whether to show an autofill suggestion.

    NEVER returns the password — only enough metadata to render the
    suggestion menu (id, name, username, registrable domain).

    Query: ?origin=<full URL of the page>
    Auth: vault token in Authorization: Bearer header."""
    s_cfg = _app_s_cfg()
    from . import extension_vault as _ev
    from . import secrets_store as ss
    from . import user_templates as ut

    vt, meta, err = _require_vault_token()
    if err: return err

    origin = request.args.get("origin", "")
    if not origin:
        return jsonify({"ok": False, "error": "origin query param required"}), 400

    # Build the entry list from BOTH user templates AND existing site
    # configs (the typical case: user has saved passwords on sites that
    # exist in sites_config.json). Each "entry" carries enough info to
    # autofill: a stable key, a display name, username, and patterns.
    entries = []
    # Site configs: every site with a stored password
    for sid, cfg in s_cfg.items():
        if not isinstance(cfg, dict): continue
        if not cfg.get("password"): continue
        login_url = cfg.get("login_url", "")
        entries.append({
            "id": f"site:{sid}",
            "name": cfg.get("name", sid),
            "username": cfg.get("username", ""),
            # v3.43.17: use full-hostname regex pattern, not eTLD+1.
            # The old `get_registrable_domain(login_url)` returned `co.uk`
            # for `*.co.uk` sites (public-suffix limitation), which would
            # match every UK site as an autofill candidate. The escaped
            # full hostname is strictly safer.
            "patterns": [_ev.hostname_pattern(login_url)] if login_url else [],
        })
    # User templates with linked password keys (from password_import)
    for tpl in ut.list_user_templates():
        if not tpl.get("patterns"): continue
        entries.append({
            "id": f"tpl:{tpl['id']}",
            "name": tpl.get("name", "saved"),
            "username": "",  # templates don't store username separately
            "patterns": tpl.get("patterns", []),
        })

    matched = _ev.entries_matching_origin(origin, entries)
    return jsonify({"ok": True, "origin": origin, "entries": matched})
@secrets_bp.route("/api/secrets/extension/fetch_one", methods=["POST"])
def api_secrets_extension_fetch_one():
    """Extension API: fetch ONE password for a chosen entry.

    Body: {"id": "site:wow"}
    Auth: vault token.

    Returns: {"ok": true, "password": "<plaintext>", "username": "..."}

    Rate-limited (see extension_vault.check_and_record_fetch). Failures
    don't reveal whether the entry exists — same generic 'denied'
    response for invalid entry, cooldown, and rate limit, to avoid
    helping an attacker enumerate stored entries.

    Every call (success OR deny) is logged to vault_access.log."""
    s_cfg = _app_s_cfg()
    from . import extension_vault as _ev
    from . import secrets_store as ss

    vt, meta, err = _require_vault_token()
    if err: return err

    data = request.json or {}
    entry_id = data.get("id", "")
    origin = data.get("origin", "")  # for audit log only
    if not entry_id:
        return jsonify({"ok": False, "error": "id required"}), 400

    # Parse the entry ID into (kind, key)
    if entry_id.startswith("site:"):
        sid = entry_id[5:]
        cfg = s_cfg.get(sid)
        if not cfg or not isinstance(cfg, dict):
            _ev.audit_fetch(meta, entry_id, origin, False, "unknown_site")
            return jsonify({"ok": False, "error": "denied"}), 403
        entry_key = ss.site_password_key(sid)
        username = cfg.get("username", "")
        raw_pw = cfg.get("password", "")
    elif entry_id.startswith("tpl:"):
        # User templates don't store their own password; the user
        # imported one separately and the password is keyed by template id.
        tid = entry_id[4:]
        entry_key = f"bulkdl-tpl-{tid}"
        username = ""
        raw_pw = None  # forces backend lookup, not legacy plaintext
    else:
        _ev.audit_fetch(meta, entry_id, origin, False, "bad_id")
        return jsonify({"ok": False, "error": "denied"}), 403

    allowed, reason = _ev.check_and_record_fetch(vt, entry_key)
    if not allowed:
        _ev.audit_fetch(meta, entry_id, origin, False, reason)
        return jsonify({"ok": False, "error": "denied",
                        "retry_after": "rate limit"}), 429

    # Resolve the password
    password = ss.resolve_password(raw_pw) if raw_pw else ss.get_backend().get(entry_key)
    if not password:
        _ev.audit_fetch(meta, entry_id, origin, False, "no_password_stored")
        return jsonify({"ok": False, "error": "denied"}), 403

    _ev.audit_fetch(meta, entry_id, origin, True)
    return jsonify({"ok": True, "username": username,
                    "password": password, "entry_id": entry_id})
@secrets_bp.route("/api/secrets/extension/ping", methods=["GET"])
def api_secrets_extension_ping():
    """Liveness check for the extension. Validates the token and
    returns the label so the popup can show 'Paired as: <label>'."""
    vt, meta, err = _require_vault_token()
    if err: return err
    return jsonify({
        "ok": True,
        "label": meta.get("label", "extension"),
        "issued_at": meta.get("issued_at", 0),
    })

def register_routes(app) -> int:
    app.register_blueprint(secrets_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("secrets."))
