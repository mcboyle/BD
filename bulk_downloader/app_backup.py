"""backup API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/backup views moved onto a Flask Blueprint.
Endpoint labels gain a "backup." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

import json
import re
import threading

from flask import Blueprint, jsonify, request
from pathlib import Path

backup_bp = Blueprint("backup", __name__)

# drb-fix r4 F-SEC-1b: one restore at a time from its write of
# sites_config.json through the refusal check and _load_sites_config. Two
# overlapping restores let the loader re-read a file the other had just
# written after this one's check passed (check-then-reread), so a refused
# site id went live.
_RESTORE_LOCK = threading.Lock()
# drb-fix r4 F4: the app-wide 4 MB body cap (app.py) refused the app's own
# backups past 4 MB. A restore upload may be as large as restore_backup will
# extract (its MAX_TOTAL_BYTES, 10 GiB); its member/total caps still apply.
_RESTORE_UPLOAD_MAX = 10 * 1024 * 1024 * 1024

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


def _confined_path(raw):
    """BK-REST F12/F13: a verify/smoke_restore/drift ``path`` must be a string
    naming a file inside BD_HOME (or BD_INSTALL_DIR; the CWD when neither is
    set). Checked BEFORE any filesystem access and refused with one body
    whether or not the outside path exists, so the routes are no existence
    oracle. Returns (resolved_path, None) or (None, (response, 400))."""
    import os
    from .backup import _is_safe_path
    if not isinstance(raw, str):
        return None, (jsonify({"ok": False, "error": "path must be a string"}), 400)
    roots = [v for v in (os.environ.get(k, "").strip()
                         for k in ("BD_HOME", "BD_INSTALL_DIR")) if v] or ["."]
    for root in roots:
        if _is_safe_path(Path(root), Path(raw)):
            return str((Path(root) / raw).resolve()), None
    return None, (jsonify({"ok": False,
                           "error": "path must be inside BD_HOME"}), 400)


@backup_bp.route("/api/backup/verify", methods=["POST"])
def api_backup_verify():
    _check_csrf()
    body = request.json or {}
    path = body.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    path, err = _confined_path(path)
    if err:
        return err
    try:
        from . import backup_verify as _bv
        if path.endswith((".db", ".sqlite")):
            return jsonify(_bv.verify_db_dump(path))
        return jsonify(_bv.verify_tarball(
            path,
            expected_members=body.get("expected_members"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
@backup_bp.route("/api/backup/smoke_restore", methods=["POST"])
def api_backup_smoke():
    _check_csrf()
    body = request.json or {}
    if not body.get("path"):
        return jsonify({"ok": False, "error": "path required"}), 400
    path, err = _confined_path(body["path"])
    if err:
        return err
    try:
        import shutil
        from . import backup_verify as _bv
        result = _bv.smoke_restore(
            path,
            expected_tables=body.get("expected_tables"),
        )
        # BK-REST F9: the caller is a browser; the sandbox is not its to read.
        sandbox = result.pop("restored_to", None)
        if sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
@backup_bp.route("/api/backup/drift", methods=["POST"])
def api_backup_drift():
    _check_csrf()
    body = request.json or {}
    if not body.get("path"):
        return jsonify({"ok": False, "error": "path required"}), 400
    path, err = _confined_path(body["path"])
    if err:
        return err
    try:
        from . import backup_verify as _bv
        return jsonify(_bv.verify_against_live(path))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
@backup_bp.route("/api/backup/history")
def api_backup_history():
    # BK-REST F13: a non-integer or non-positive limit is the caller's error
    # (400), not a 500 and not an unbounded list.
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        limit = 0
    if limit < 1:
        return jsonify({"error": "limit must be a positive integer"}), 400
    try:
        from . import backup_verify as _bv
        return jsonify({"verifications": _bv.recent_verifications(
            limit=limit)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@backup_bp.route("/api/backup/list")
def api_backup_list():
    """F020: every backup BD keeps on disk -- metadata only (name, size, created, kind)."""
    try:
        from . import backup_verify as _bv
        inv = _bv.backup_inventory()
        return jsonify({"ok": True, "backups": inv["backups"], "errors": inv["errors"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
@backup_bp.route("/api/backup/create", methods=["POST"])
def api_backup_create():
    """Create a backup zip and return as download. Body params:
      passphrase: optional; if present, output is encrypted
      include_db: default true; set false for config-only backup
    Returns the zip directly (not a JSON wrapper) with
    Content-Disposition header so the browser saves it."""
    from bulk_downloader import backup as bd_backup
    data = request.get_json(silent=True) or {}
    passphrase, refusal = _passphrase_from(data)
    if refusal:
        return refusal
    include_db = data.get("include_db", True)
    # POS-1: operator-selectable encryption scope. "all" (default) whole-wraps;
    # "sensitive" encrypts only secret/session members in-place. Any other value
    # falls back to "all". drb-fix F10: "sensitive" without a passphrase is
    # refused -- it used to return a plain zip with secrets.json and cookies.
    encrypt_scope = data.get("encrypt_scope") or "all"
    if encrypt_scope not in ("all", "sensitive"):
        encrypt_scope = "all"
    if encrypt_scope == "sensitive" and not passphrase:
        return _sensitive_needs_passphrase()

    # Build into a temp file then stream — keeps the in-memory zip
    # path consistent with the create_backup atomicity guarantees.
    import shutil
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="bdback_")
    fname = bd_backup.default_backup_filename()
    if passphrase and encrypt_scope == "all":
        fname = fname.replace(".zip", ".bdbk")  # whole-wrap is not a valid zip
    out_path = Path(tmpdir) / fname
    result = bd_backup.create_backup(
        out_path,
        base_dir=_data_dir(),
        include_db=include_db,
        passphrase=passphrase,
        encrypt_scope=encrypt_scope,
    )
    if not result["ok"]:
        # Cleanup temp dir
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass
        return jsonify(result), 500
    # Stream the file. Flask's send_file with attachment_filename handles
    # the Content-Disposition header for us.
    from flask import send_file
    response = send_file(
        out_path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=fname,
    )
    # Attach metadata via headers so the client can read it without
    # having to parse the response body (which is the binary zip).
    response.headers["X-Backup-Size"] = str(result["size_bytes"])
    response.headers["X-Backup-Files"] = str(result["files"])
    response.headers["X-Backup-Encrypted"] = "1" if result["encrypted"] else "0"
    response.headers["X-Backup-Elapsed-Ms"] = str(result["elapsed_ms"])
    # send_file uses direct_passthrough, so Response.call_on_close is bypassed.
    from werkzeug.wsgi import ClosingIterator
    response.response = ClosingIterator(
        response.response, lambda: shutil.rmtree(tmpdir, ignore_errors=True)
    )
    return response
@backup_bp.route("/api/backup/preview", methods=["POST"])
def api_backup_preview():
    """Preview what a backup would contain WITHOUT actually creating it.
    Used by the UI to show size estimate + file count before the user
    commits to creating + downloading."""
    from bulk_downloader import backup as bd_backup
    import tempfile, shutil
    # svam-fix F6: preview the archive the same body would CREATE (include_db,
    # passphrase, encrypt_scope read exactly as api_backup_create reads them).
    data = request.get_json(silent=True) or {}
    passphrase, refusal = _passphrase_from(data)
    if refusal:
        return refusal
    include_db = data.get("include_db", True)
    encrypt_scope = data.get("encrypt_scope") or "all"
    if encrypt_scope not in ("all", "sensitive"):
        encrypt_scope = "all"
    if encrypt_scope == "sensitive" and not passphrase:
        return _sensitive_needs_passphrase()
    tmpdir = tempfile.mkdtemp(prefix="bdback_preview_")
    try:
        out_path = Path(tmpdir) / "preview.zip"
        result = bd_backup.create_backup(
            out_path, base_dir=_data_dir(), include_db=include_db,
            passphrase=passphrase, encrypt_scope=encrypt_scope,
        )
        return jsonify(result)
    finally:
        try: shutil.rmtree(tmpdir)
        except Exception: pass
@backup_bp.route("/api/backup/restore", methods=["POST"])
def api_backup_restore():
    """Restore a backup. Accepts multipart/form-data with `file` (the
    backup zip) and optional `passphrase` form field. The dry_run
    flag previews without writing."""
    from bulk_downloader import backup as bd_backup
    # Before request.files: the body is parsed against this request's cap.
    request.max_content_length = _RESTORE_UPLOAD_MAX
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400
    f = request.files["file"]
    passphrase = request.form.get("passphrase") or None
    dry_run = request.form.get("dry_run") == "1"

    # Save to temp + restore from that path. Streaming directly from
    # the Flask file handle to restore_backup would require teaching
    # restore_backup to accept a file-like; saving to temp is simpler
    # and the zips are bounded (_RESTORE_UPLOAD_MAX).
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp(prefix="bdrestore_")
    try:
        tmp_path = Path(tmpdir) / "upload.zip"
        f.save(str(tmp_path))
        target = _data_dir()
        with _RESTORE_LOCK:
            before = _stat_sig(target / "sites_config.json")
            result = bd_backup.restore_backup(
                tmp_path, target_dir=target, passphrase=passphrase, dry_run=dry_run,
            )
            if result.get("ok") and not dry_run:
                result.update(_apply_restored_sites(target / "sites_config.json", before))
        return jsonify(result)
    finally:
        try: shutil.rmtree(tmpdir)
        except Exception: pass

def _passphrase_from(data):
    """(passphrase or None, 400 response or None) for a create/preview body.
    Absent or falsy is "no passphrase", as before. drb-fix r3: a non-string
    (123, true, [1]) used to reach the encryptor and 500 after the temp dir
    was made; a blank one ("   ") encrypted with whitespace. Both are 400."""
    passphrase = data.get("passphrase") or None
    if passphrase is None:
        return None, None
    if not isinstance(passphrase, str):
        return None, (jsonify({"ok": False,
                               "error": "passphrase must be a string"}), 400)
    if not passphrase.strip():
        return None, (jsonify({"ok": False,
                               "error": "passphrase must not be blank"}), 400)
    return passphrase, None


def _sensitive_needs_passphrase():
    return jsonify({"ok": False, "error": (
        "encrypt_scope 'sensitive' needs a passphrase: without one the "
        "secrets and cookies would be written in plaintext")}), 400


def _stat_sig(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _apply_restored_sites(restored: Path, before) -> dict:
    """drb-fix F1: make a restored sites_config.json the app's live config.

    The route used to write files only; the running app kept its in-memory
    sites, so the next config save rewrote the file and undid the restore.
    _load_sites_config is the boot loader and is only safe on empty site state
    (it publishes new runners without retiring old ones), so it runs only when
    no site is loaded and it reads the file the restore wrote. Otherwise the
    caller is told to restart before saving any config."""
    if _stat_sig(restored) == before:
        return {"reloaded": False, "restart_required": False}
    import importlib
    app_mod = importlib.import_module("bulk_downloader.app")
    try:
        same_file = Path(app_mod.SITES_FILE).resolve() == restored.resolve()
    except OSError:
        same_file = False
    if (not same_file or app_mod._SITE_RUNTIME_RETIRING
            or app_mod.s_cfg or app_mod.s_meta or app_mod.runners):
        return {"reloaded": False, "restart_required": True}
    # drb-fix r3 F-SEC-1: restored content has not been through the write
    # routes (uuid site ids, path checks), so it goes live on demand only if
    # it would pass them; a site id like "../../x" made runner dirs outside
    # the data root.
    refused = _restored_sites_refusal(app_mod, restored)
    if refused:
        return {"reloaded": False, "restart_required": True,
                "reload_refused": refused[:200]}
    try:
        app_mod._load_sites_config()
    except Exception as e:
        # Same all-or-nothing rollback as boot: never leave a partial live set.
        if app_mod._retire_configured_runtime_runners():
            app_mod._clear_configured_runtime_state()
        return {"reloaded": False, "restart_required": True,
                "reload_error": f"{type(e).__name__}: {e}"[:200]}
    # Boot starts these after the load; both skip what is already running.
    for start in (app_mod._start_session_keepers, app_mod._start_watch_folder_threads):
        try:
            start()
        except Exception as e:
            import sys
            sys.stderr.write(f"  backup restore: {start.__name__} failed: {e}\n")
    return {"reloaded": True, "restart_required": False}


_PLAIN_SITE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def _restored_sites_refusal(app_mod, restored: Path) -> str:
    """Why the restored sites_config.json must not be loaded live, or "".

    Site ids must be plain tokens (they name runner dirs); download_dir,
    spillover_dirs and every cookie_file must pass the checks the site write
    routes apply (_validate_config_paths, _validate_cookie_files). As in
    _sites_config_path_gate, the cookie path the app fills in itself
    (<BD_HOME>/cookies/<sid>.json) is accepted."""
    try:
        sites = json.loads(restored.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"sites_config.json is not readable JSON ({type(e).__name__})"
    if not isinstance(sites, dict):
        return "sites_config.json is not a JSON object of sites"
    import os
    cookie_dir = Path(os.environ.get("BD_HOME") or ".").resolve() / "cookies"
    for sid, entry in sites.items():
        if not _PLAIN_SITE_ID.fullmatch(sid):
            return f"site id {sid[:60]!r} is not a plain token"
        if not isinstance(entry, dict):
            continue
        ok, msg = app_mod._validate_config_paths(
            {k: entry.get(k) for k in ("download_dir", "spillover_dirs")})
        if ok:
            default = str(cookie_dir / f"{sid}.json")
            ok, msg = app_mod._validate_cookie_files(
                {"cookie_file": None if entry.get("cookie_file") == default
                 else entry.get("cookie_file"),
                 "accounts": entry.get("accounts")})
        if not ok:
            return f"site {sid}: {msg}"
    return ""


def register_routes(app) -> int:
    app.register_blueprint(backup_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("backup."))


def _data_dir() -> Path:
    """Where backup reads from and restore writes to (O1826 C17, M009; O1864).

    BD_INSTALL_DIR first (app._resolve_sites_file reads sites_config.json from
    it), else BD_HOME (app.py keeps cookies/ there), else the CWD, unchanged
    from before. Read at call time, not import. When both are set and differ,
    no single base covers both; that needs per-target resolution in backup.py."""
    import os
    for var in ("BD_INSTALL_DIR", "BD_HOME"):
        val = os.environ.get(var, "").strip()
        if val:
            return Path(val).resolve()
    return Path(".")
