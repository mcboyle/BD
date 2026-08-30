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
    return jsonify({
        "ok": True,
        "backend": backend.name,
        "is_unlocked": backend.is_unlocked() if hasattr(backend, "is_unlocked") else True,
        "is_initialized": getattr(backend, "is_initialized", lambda: True)(),
        "plaintext_count": len(plaintext),
        "plaintext_sites": [sid for sid, _ in plaintext],
        "stored_keys": backend.list_keys(),
        "keyring_available": ss._KEYRING_AVAILABLE,
        "crypto_available": ss._CRYPTO_AVAILABLE,
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
    (Windows Credential Manager, plaintext)."""
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
    if backend.unlock(password):
        _at.record_success(_at.LABEL_MASTER_PASSWORD)
        resp = {"ok": True}
        # Row 402: unlocking a vault that holds no stored secrets is an
        # INITIALISE (any password is accepted first-use, committing this
        # password for the process), not the open of a usable vault. Say so,
        # so a caller can tell the two apart from the response alone --
        # is_unlocked is True in both cases and cannot.
        try:
            if not backend.list_keys():
                resp["initialized_empty_vault"] = True
        except Exception:
            pass
        return jsonify(resp)
    _at.record_failure(_at.LABEL_MASTER_PASSWORD)
    return jsonify({"ok": False, "error": "incorrect password"}), 401
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
    # NEW-9: shared escalating back-off with the unlock route (same secret).
    from . import auth_throttle as _at
    allowed, retry = _at.check(_at.LABEL_MASTER_PASSWORD)
    if not allowed:
        resp = jsonify({"ok": False,
                        "error": f"too many attempts; try again in {retry:.0f}s"})
        resp.headers["Retry-After"] = str(int(retry) + 1)
        return resp, 429
    # B15 (v3.66.43): probe the old password independently. unlock() is
    # read-only and (post-B6) safe as a check. change_password() also
    # returns False on undecryptable ciphertext (data corruption) and
    # raises SecretsPersistError on a failed persist — neither is "wrong
    # password", so map them to distinct status codes instead of telling
    # the operator to retype a password that's actually correct.
    if not backend.unlock(old):
        _at.record_failure(_at.LABEL_MASTER_PASSWORD)
        return jsonify({"ok": False,
                        "error": "current password is incorrect"}), 401
    _at.record_success(_at.LABEL_MASTER_PASSWORD)
    try:
        if backend.change_password(old, new):
            return jsonify({"ok": True})
        return jsonify({
            "ok": False,
            "error": ("rotation aborted because one or more stored "
                      "secrets could not be decrypted (data corruption). "
                      "Your old password is still in effect; no data has "
                      "been changed. Check app logs for which entry "
                      "failed."),
        }), 500
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
    removed = ss.get_backend().delete(key)
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

