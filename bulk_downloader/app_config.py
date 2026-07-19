"""config API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/config views moved onto a Flask Blueprint.
Endpoint labels gain a "config." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (CFG_FIELDS, DEFAULTS, runners, s_cfg, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import json
import uuid
from flask import Blueprint, Response, jsonify, request
from pathlib import Path
from .runner import SiteRunner

config_bp = Blueprint("config", __name__)

def _build_meta(*_a, **_k):
    """Delegate to app._build_meta at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_build_meta")(*_a, **_k)

def _app_CFG_FIELDS():
    """The live shared CFG_FIELDS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "CFG_FIELDS")

def _app_DEFAULTS():
    """The live shared DEFAULTS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "DEFAULTS")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")

def _app_s_meta():
    """The live shared s_meta from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_meta")


@config_bp.route("/api/config/export")
def api_config_export():
    """Export full site configs as JSON. Passwords are stripped by default
    (set ?include_passwords=1 to include them — only do this for offline backup)."""
    s_cfg = _app_s_cfg()
    include_pw=(request.args.get("include_passwords","0")=="1")
    if not include_pw:
        # Redact the FULL secret-field set (not just 'password'), using the
        # authoritative SoT so a default export can never leak plex_token /
        # *_api_key / auth_token etc. Same SoT the marketplace export uses.
        from .site_editor import SECRET_FIELDS  # lazy: no static import edge
    payload=[]
    for sid,cfg in s_cfg.items():
        c=dict(cfg)
        if not include_pw:
            for _sk in SECRET_FIELDS:
                if _sk in c: c[_sk]=""
        c["_id"]=sid  # for round-trip merging
        payload.append(c)
    body=json.dumps({"version":"2.1.1","sites":payload},indent=2)
    fn="bulk_downloader_config.json"
    return Response(body,mimetype="application/json",
                    headers={"Content-Disposition":f"attachment;filename={fn}"})

@config_bp.route("/api/config/import",methods=["POST"])
def api_config_import():
    """Import site configs. mode='merge' (default) keeps existing sites and
    adds new ones (matched by name); mode='replace' wipes existing sites
    first. Passwords from the imported file are used; if blank, the existing
    password for a matching site is preserved."""
    CFG_FIELDS = _app_CFG_FIELDS()
    DEFAULTS = _app_DEFAULTS()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    s_meta = _app_s_meta()
    if "file" in request.files:
        try: data=json.loads(request.files["file"].read().decode("utf-8"))
        except Exception as e: return jsonify({"error":f"bad JSON: {e}"}),400
    else:
        data=request.json or {}
    mode=request.args.get("mode") or (data.get("mode") if isinstance(data,dict) else None) or "merge"
    sites=data.get("sites",[]) if isinstance(data,dict) else (data if isinstance(data,list) else [])
    if not sites: return jsonify({"error":"no sites in payload"}),400
    if mode=="replace":
        for sid in list(runners.keys()):
            runners[sid].stop(); del runners[sid]; del s_cfg[sid]; del s_meta[sid]
    # name → existing sid (for password preservation in merge mode)
    # Phase 41 preflight: defensive coercion against non-string names that
    # may exist in s_cfg from before the input validator was added
    def _name_of(c):
        v = c.get("name") if isinstance(c, dict) else None
        return (v if isinstance(v, str) else str(v) if v is not None else "").strip().lower()
    name_to_sid={_name_of(c): sid for sid, c in s_cfg.items()}
    imported=updated=0
    for raw in sites:
        if not isinstance(raw,dict): continue
        # Phase 41: defensive coercion against non-string names in imported data
        _n = raw.get("name")
        name = (_n if isinstance(_n, str) else str(_n) if _n is not None else "").strip()
        cfg={k:raw.get(k,"") for k in CFG_FIELDS}
        cfg["name"]=name or f"Imported {imported+updated+1}"
        for k,d in DEFAULTS.items():
            if cfg.get(k) in ("",None): cfg[k]=d
        existing_sid=name_to_sid.get(cfg["name"].lower()) if mode=="merge" else None
        # Phase 41 preflight: guard against orphan s_cfg entries (no matching
        # runner) — could happen if sites_config.json was hand-edited
        if existing_sid and existing_sid not in runners:
            existing_sid = None  # treat as new
        if existing_sid:
            # Preserve existing secrets when the imported value is blank -- e.g.
            # a default (redacted) export. Covers every SECRET_FIELD (not just
            # password), so a redacted export round-trips without wiping keys
            # like *_api_key / plex_token / auth_token.
            from .site_editor import SECRET_FIELDS  # lazy: no static import edge
            _ex = s_cfg[existing_sid]
            for _sk in SECRET_FIELDS:
                if not cfg.get(_sk) and _ex.get(_sk): cfg[_sk]=_ex[_sk]
            s_cfg[existing_sid]=cfg
            s_meta[existing_sid]=_build_meta(cfg)
            runners[existing_sid].update_config(cfg)
            updated+=1
        else:
            sid=uuid.uuid4().hex[:8]
            s_cfg[sid]=cfg
            s_meta[sid]=_build_meta(cfg)
            runners[sid]=SiteRunner(sid,cfg)
            if cfg.get("cookie_file") and Path(cfg["cookie_file"]).exists():
                runners[sid].set_cookies_from_file(cfg["cookie_file"])
            imported+=1
    return jsonify({"ok":True,"imported":imported,"updated":updated,"mode":mode})

def register_routes(app) -> int:
    app.register_blueprint(config_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("config."))

