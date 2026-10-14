"""backup API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/backup views moved onto a Flask Blueprint.
Endpoint labels gain a "backup." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from pathlib import Path

backup_bp = Blueprint("backup", __name__)

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
    passphrase = data.get("passphrase") or None
    include_db = data.get("include_db", True)
    # POS-1: operator-selectable encryption scope. "all" (default) whole-wraps;
    # "sensitive" encrypts only secret/session members in-place. Ignored without
    # a passphrase (opt-in). Any other value falls back to "all".
    encrypt_scope = data.get("encrypt_scope") or "all"
    if encrypt_scope not in ("all", "sensitive"):
        encrypt_scope = "all"

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
    passphrase = data.get("passphrase") or None
    include_db = data.get("include_db", True)
    encrypt_scope = data.get("encrypt_scope") or "all"
    if encrypt_scope not in ("all", "sensitive"):
        encrypt_scope = "all"
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
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400
    f = request.files["file"]
    passphrase = request.form.get("passphrase") or None
    dry_run = request.form.get("dry_run") == "1"

    # Save to temp + restore from that path. Streaming directly from
    # the Flask file handle to restore_backup would require teaching
    # restore_backup to accept a file-like; saving to temp is simpler
    # and the zips are bounded (~10-50 MB typical).
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp(prefix="bdrestore_")
    try:
        tmp_path = Path(tmpdir) / "upload.zip"
        f.save(str(tmp_path))
        result = bd_backup.restore_backup(
            tmp_path, target_dir=_data_dir(), passphrase=passphrase, dry_run=dry_run,
        )
        return jsonify(result)
    finally:
        try: shutil.rmtree(tmpdir)
        except Exception: pass

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
