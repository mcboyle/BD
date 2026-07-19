"""app_sites.collection -- 4 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

Handlers attach to the SHARED sites_bp (imported from .app_sites); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
import os as _os
import json
import os
import re
import sys
import time
import uuid
from flask import Blueprint, Response, jsonify, request
from pathlib import Path
from .constants import SCREENSHOTS_DIR
from .runner import SiteRunner
from .runner import _ts
from datetime import datetime
from .db import db_search
from .db import queue_upsert
from .app_sites import (
    _app_CFG_FIELDS,
    _app_DEFAULTS,
    _app_runners,
    _app_s_cfg,
    _app_s_meta,
    _apply_login_template_by_id,
    _apply_template_by_id,
    _build_meta,
    _create_site,
    _sanitize_display_name,
    _save_sites_config,
    sites_bp,
)


@sites_bp.route("/api/sites/csv_template")
def api_sites_csv_template():
    """Download the CSV template for bulk site import. Data rows are at
    the top (ready to edit); instructions + the valid template ids
    follow as comment lines."""
    from . import csv_bulk, templates as _tpls
    from . import login_templates_data as _ltpls
    body = csv_bulk.build_csv_template(
        _tpls.list_templates(), _ltpls.list_login_templates())
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition":
            'attachment; filename="bulk_add_sites_template.csv"'})


@sites_bp.route("/api/sites/xlsx_template")
def api_sites_xlsx_template():
    """Download the XLSX template for bulk site import — a formatted
    workbook with a fillable 'Sites' sheet (styled header, example row,
    a dropdown on the template column) and an 'Instructions' sheet."""
    from . import csv_bulk, templates as _tpls
    from . import login_templates_data as _ltpls
    data = csv_bulk.build_xlsx_template(
        _tpls.list_templates(), _ltpls.list_login_templates())
    return Response(data, mimetype=(
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"), headers={
        "Content-Disposition":
            'attachment; filename="bulk_add_sites_template.xlsx"'})


@sites_bp.route("/api/sites/bulk_csv", methods=["POST"])
def api_sites_bulk_csv():
    """Bulk-create sites from a CSV or XLSX. The file can arrive as an
    uploaded file (multipart field 'file' — .csv or .xlsx), as JSON
    {'csv': '...'}, or as the raw request body. Returns a per-row
    result so the UI can show which rows were created and which
    failed."""
    s_cfg = _app_s_cfg()
    from . import csv_bulk, templates as _tpls
    from . import login_templates_data as _ltpls
    up = request.files.get("file")
    if up is not None and up.filename:
        raw = up.read()
        if up.filename.lower().endswith(".xlsx"):
            rows, parse_errors = csv_bulk.parse_import(
                xlsx=raw, filename=up.filename)
        else:
            rows, parse_errors = csv_bulk.parse_import(
                text=raw.decode("utf-8", "replace"),
                filename=up.filename)
    else:
        if request.is_json:
            text = str((request.get_json(silent=True) or {}).get("csv", ""))
        else:
            text = request.get_data(as_text=True) or ""
        rows, parse_errors = csv_bulk.parse_import(text=text)
    tpls = _tpls.list_templates()
    login_tpls = _ltpls.list_login_templates()
    results = []
    created = 0
    for lineno, msg in parse_errors:
        results.append({"line": lineno, "status": "error", "error": msg})
    for row in rows:
        line = row.get("line", 0)
        name = csv_bulk.clean_name(row.get("name"),
                                   row.get("login_url", ""))
        tpl_id, tpl_err = csv_bulk.resolve_template(row.get("template"), tpls)
        if tpl_err:
            results.append({"line": line, "name": name,
                            "status": "error", "error": tpl_err})
            continue
        login_tpl_id, login_err = csv_bulk.resolve_template(
            row.get("login_template"), login_tpls)
        if login_err:
            results.append({"line": line, "name": name, "status": "error",
                            "error": login_err.replace("template",
                                                       "login template")})
            continue
        sid, err = _create_site({
            "name": name,
            "login_url": row.get("login_url", ""),
            "username": row.get("username", ""),
            "password": row.get("password", ""),
        }, actor="csv-import")
        if err:
            results.append({"line": line, "name": name,
                            "status": "error", "error": err})
            continue
        created += 1
        entry = {"line": line, "name": name, "id": sid, "status": "created"}
        # v3.65.2: surface auto-picked templates so the CSV import
        # response shows what was matched from the URL. _autopick
        # lives as a transient field on the cfg until next save.
        # Explicit template column entries (handled below) take
        # precedence and OVERWRITE the auto-pick — that's the more-
        # specific user intent.
        autopick = (s_cfg.get(sid) or {}).get("_autopick") or {}
        if autopick.get("download_template_applied"):
            entry["auto_template"] = autopick["download_template_applied"]
        if autopick.get("login_template_applied"):
            entry["auto_login_template"] = autopick["login_template_applied"]
        if tpl_id:
            ok, m = _apply_template_by_id(sid, tpl_id)
            if ok:
                entry["template"] = tpl_id
            else:
                entry["warning"] = f"site created, template not applied: {m}"
        if login_tpl_id:
            ok, m = _apply_login_template_by_id(sid, login_tpl_id)
            if ok:
                entry["login_template"] = login_tpl_id
            else:
                entry["warning"] = (entry.get("warning", "")
                                    + f" login template not applied: {m}"
                                    ).strip()
        results.append(entry)
    return jsonify({
        "created": created,
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    })


@sites_bp.route("/api/sites/import", methods=["POST"])
def api_sites_import():
    """Import a config exported by /export. Body: the export envelope
    (or a bare config). Validates, drops unknown keys, then creates a
    NEW site. Returns {ok, id, errors, warnings} or a 400 on validation
    failure."""
    CFG_FIELDS = _app_CFG_FIELDS()
    DEFAULTS = _app_DEFAULTS()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    from . import site_editor as _se
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False,
                        "errors": ["Request body must be JSON."]}), 400
    result = _se.import_config(payload, known_fields=set(CFG_FIELDS))
    if not result["ok"]:
        return jsonify({"ok": False,
                        "errors": result["errors"],
                        "warnings": result["warnings"]}), 400
    # Create the site via the same path as api_add — but inline here so
    # we don't double-handle the request body.
    cfg_in = result["config"]
    # Normalize the display name (same as api_add does)
    if "name" in cfg_in:
        cfg_in["name"] = _sanitize_display_name(cfg_in["name"])
    sid = uuid.uuid4().hex[:8]
    cfg = {k: cfg_in.get(k, "") for k in CFG_FIELDS}
    cfg.setdefault("name", f"Imported site {len(runners)+1}")
    for k, d in DEFAULTS.items():
        if cfg.get(k) in ("", None):
            cfg[k] = d
    from .constants import make_fingerprint
    cfg["fingerprint"] = make_fingerprint()
    s_cfg[sid] = cfg
    s_meta[sid] = _build_meta(cfg)
    runners[sid] = SiteRunner(sid, cfg)
    if cfg.get("cookie_file") and Path(cfg["cookie_file"]).exists():
        runners[sid].set_cookies_from_file(cfg["cookie_file"])
    _save_sites_config()
    # Audit the import (same pattern as api_add)
    try:
        from . import audit as _audit
        _audit.audit_log(
            source="api", action="create",
            target=f"sites_config:{sid}",
            before=None, after=cfg,
            actor=(request.cookies.get("bd_session", "")[:8]
                   or request.remote_addr or "import"))
    except Exception:
        pass
    return jsonify({"ok": True, "id": sid,
                    "errors": [], "warnings": result["warnings"]})
