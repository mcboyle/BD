"""app_envfile_editor.py — Bucket 2 (GUI-config parity): the `.env` editor.

A small, SEPARATE blueprint (kept out of app_settings_center, which is deliberately
read-only: review + dry-run validate, the actual site write delegated to the audited
PUT). The deploy/path/port/host env vars are consumed at boot or by external CLI
tools, so a *live* write is meaningless. This editor persists them to a `.env`
(read at next boot by bulk_downloader._envfile via os.environ.setdefault) and
surfaces saved-vs-live so the UI can show an "applies on restart" chip.

Endpoints (URL kept under /api/settings/ per the plan; served by THIS blueprint):
  * GET  /api/settings/envfile  — {path, env:[{name,kind,applies,saved,effective,
                                   restart_pending,foundation,danger,danger_note}], …}
  * POST /api/settings/envfile  — body {updates:{NAME:value}}; validate first, then
                                   on any rejection 400 + persist NOTHING, else atomic
                                   merge-write + {ok, written, restart_required:true}.

Writable keys are allow-listed to _envfile.EDITOR_KEYS — the endpoint can't be used
to write an arbitrary env var. Atomic write = temp + os.replace, preserving comments
and unrelated lines. No masking (these aren't secrets).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, request

from . import _envfile

envfile_editor_bp = Blueprint("envfile_editor", __name__)


def _keys():
    """Canonical Bucket-2 key metadata (single source: _envfile.EDITOR_KEYS)."""
    return [dict(e) for e in _envfile.EDITOR_KEYS]


def _state() -> dict:
    """GET shape: per key the saved `.env` value, the effective os.environ value,
    and restart_pending (saved present and != effective)."""
    path = _envfile.resolve_envfile_path()
    try:
        saved = _envfile.parse_envfile(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        saved = {}
    rows = []
    for meta in _envfile.EDITOR_KEYS:
        name = meta["name"]
        sv = saved.get(name)
        ev = os.environ.get(name)
        rows.append({
            "name": name, "kind": meta["kind"], "applies": meta["applies"],
            "applies_note": _envfile.APPLIES.get(meta["applies"], ""),
            "foundation": meta["foundation"], "danger": meta["danger"],
            "danger_note": meta["danger_note"],
            "saved": sv, "effective": ev,
            "restart_pending": sv is not None and sv != (ev or ""),
        })
    return {"ok": True, "env": rows, "count": len(rows), "path": str(path),
            "read_only": False,
            "note": "Edit -> persist to .env -> applies on RESTART. The running "
                    "process keeps its current (effective) values until restarted; "
                    "call-time path roots are restart-recommended (split-brain risk)."}


def validate_envfile_updates(updates: dict) -> dict:
    """Dry-run gate. Returns {accepted, rejected, warnings}; performs NO write.
    Foundation paths must be an existing writable dir (a bad one bricks startup);
    other path roots warn (not reject) on a missing parent; ports int 1..65535 with
    a collision warning; bool flags bool-ish; URL/host plain strings."""
    known = {e["name"]: e for e in _envfile.EDITOR_KEYS}
    accepted, rejected, warnings = {}, {}, []
    seen_ports = {}
    for k, v in (updates or {}).items():
        meta = known.get(k)
        if meta is None:
            rejected[k] = "unknown env var (not in the .env editor allow-list)"
            continue
        if isinstance(v, (list, dict)):
            rejected[k] = f"expected scalar, got {type(v).__name__}"
            continue
        sval = "" if v is None else str(v)
        kind = meta["kind"]
        if meta["foundation"]:
            p = Path(sval).expanduser()
            if not sval:
                rejected[k] = "foundation path may not be empty (a bad value bricks startup)"
            elif not p.exists():
                rejected[k] = f"path does not exist: {sval}"
            elif not p.is_dir():
                rejected[k] = f"not a directory: {sval}"
            elif not os.access(p, os.W_OK):
                rejected[k] = f"not writable: {sval}"
            else:
                accepted[k] = str(p)
        elif kind == "port":
            try:
                iv = int(sval)
            except (TypeError, ValueError):
                rejected[k] = "expected an integer port"
                continue
            if not (1 <= iv <= 65535):
                rejected[k] = "port out of range — expected 1..65535"
            else:
                if iv in seen_ports:
                    warnings.append(f"{k} collides with {seen_ports[iv]} on port {iv}")
                seen_ports[iv] = k
                accepted[k] = str(iv)
        elif kind == "bool":
            lv = sval.strip().lower()
            if lv in ("true", "1", "yes", "on"):
                accepted[k] = "1"
            elif lv in ("false", "0", "no", "off", ""):
                accepted[k] = "0"
            else:
                rejected[k] = "expected a boolean (true/false/1/0)"
        elif kind == "path":
            # non-foundation path root: warn (not reject) on a missing parent dir.
            if sval:
                parent = Path(sval).expanduser().parent
                if not parent.exists():
                    warnings.append(f"{k}: parent directory does not exist yet: {parent}")
            accepted[k] = sval
        else:  # host / url / other scalar
            accepted[k] = sval
    return {"accepted": accepted, "rejected": rejected, "warnings": warnings}


def _write_envfile(path: Path, accepted: dict) -> None:
    """Atomically merge `accepted` into the `.env`, preserving comments + unrelated
    lines. Updates a key in place if present, else appends it (temp + os.replace)."""
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        existing = []
    remaining = dict(accepted)
    out = []
    for raw in existing:
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.partition("=")[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(raw)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@envfile_editor_bp.route("/api/settings/envfile", methods=["GET"])
def api_settings_envfile_get():
    try:
        return jsonify(_state())
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@envfile_editor_bp.route("/api/settings/envfile", methods=["POST"])
def api_settings_envfile_post():
    body = request.get_json(silent=True) or {}
    updates = body.get("updates", body)
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "expected an object of {KEY: value}"}), 400
    res = validate_envfile_updates(updates)
    if res["rejected"] or not res["accepted"]:
        return jsonify({"ok": False, "accepted": res["accepted"],
                        "rejected": res["rejected"], "warnings": res["warnings"],
                        "note": "no write performed — fix the rejected values"}), 400
    path = _envfile.resolve_envfile_path()
    try:
        _write_envfile(path, res["accepted"])
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"write failed: {str(e)[:160]}"}), 500
    return jsonify({"ok": True, "written": sorted(res["accepted"]),
                    "warnings": res["warnings"], "path": str(path),
                    "restart_required": True,
                    "note": "persisted to .env — restart the service to apply"})


def register_routes(app):
    """Register the `.env` editor blueprint. Returns route count added."""
    before = len(list(app.url_map.iter_rules()))
    app.register_blueprint(envfile_editor_bp)
    return len(list(app.url_map.iter_rules())) - before
