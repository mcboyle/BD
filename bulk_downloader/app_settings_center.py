"""app_settings_center.py — additive Settings Center (GUI Phase 3, Slices 1-5).

A Flask blueprint that surfaces the per-site configuration surface for review and gui-safe
editing. No direct writes: reads + presence-only secrets + a dry-run `validate` gate; the
actual write is delegated to the existing audited `PUT /api/sites/<sid>`. Slice 4 adds
read/write polish — per-field current/default/source/type/range, runtime-categorized
grouping, display-never secrets, and sticky (username/login_url) preserve-on-blank marking.

What it exposes (all read-only):
  * /cockpit/settings                  — page shell (category overview + API links)
  * /api/settings/schema               — per-site field schema from app.py CFG_FIELDS
                                          (authoritative 225 unique), categorized, secret-flagged
  * /api/settings/site/<sid>/effective — per-site effective values (secrets => presence only)
  * /api/settings/global/effective     — global_config effective (read-only)
  * /api/settings/vpn/summary          — vpn tunnels + global_settings (no @cred values)
  * /api/settings/env/effective        — selected env effective value + default (read-only)

Secrets are NEVER returned by value — only presence/health, mirroring the existing
export-strip. CFG_FIELDS is read by AST (no app import => no circular import). All data
reads are fail-open: a missing store yields ok:true with empty data + a note, never a crash
or a mutation. Auth piggybacks on app.py's global before-request hooks.

Headless render was exercised in sandbox; live deployed noVNC/operator click-through
remains required.

Wiring (additive, fail-open in app.py):
    from .app_settings_center import register_routes as _reg_settings
    _reg_settings(app)
"""
from __future__ import annotations

import ast
import html
import math
import os
import re
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

settings_center_bp = Blueprint("settings_center", __name__)
_REPO_ROOT = Path(__file__).resolve().parent.parent

def _is_secret(k) -> bool:
    """Secret iff the field name is a config credential per the shared SoT
    (``site_editor.is_secret_config_key``), OR is exactly ``cookie_file``.

    REDACT-SOT unification: the old local ``password|token|api_key|secret`` regex
    drifted and leaked private_key / cookies / cookies_b64 / passphrase /
    preshared_key. Routed through the single config-domain SoT so it can't drift.
    Bare ``cookie`` (e.g. ``cookie_max_age_hours``, a duration) is still NOT a
    secret -- the SoT floor matches ``cookies`` (plural), not bare ``cookie`` --
    and the explicit ``cookie_file`` case is preserved. Lazy import avoids a
    static import edge."""
    from .site_editor import is_secret_config_key
    return is_secret_config_key(str(k)) or str(k) == "cookie_file"


# Sticky non-secret fields: NOT secrets (they round-trip to the UI) but preserved when
# submitted blank by the audited writer, mirroring app.py's PRESERVE_IF_BLANK. This is a
# documented mirror for display/marking only — PUT /api/sites/<sid> remains the authority.
_STICKY_NONSECRET = frozenset({"username", "login_url"})


def _preserve_on_blank(k) -> bool:
    """True iff the audited writer preserves this field when submitted blank
    (PRESERVE_IF_BLANK): every secret, plus the sticky non-secrets username/login_url."""
    return _is_secret(k) or str(k) in _STICKY_NONSECRET


# ── schema (authoritative, AST-derived; no app import) ─────────────
def _cfg_fields():
    """Read CFG_FIELDS via AST (read-only). Source moved app.py -> app_kernel.py in
    DECOMP-R2a; scan the kernel first and fall back to app.py for older trees. No app
    import => no circular import. Returns unique-ordered list."""
    pkg = _REPO_ROOT / "bulk_downloader"
    for fname in ("app_kernel.py", "app.py"):
        p = pkg / fname
        if not p.is_file():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "CFG_FIELDS" for t in node.targets
            ):
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    vals = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
                    return list(dict.fromkeys(vals))  # unique, order-preserving
    return []


def _categorize(k: str) -> str:
    if k in ("login_url", "username", "password", "user_field", "pass_field", "submit_btn",
             "success_url", "cookie_file", "auto_relogin_enabled", "auto_relogin_interval_hours",
             "prelogin_minutes", "ai_login_assist_enabled", "auto_preemptive_relogin",
             "accounts", "accounts_mode", "accounts_rotate_every",
             "account_cooldown_seconds"):
        # NB: cookie_max_age_hours is a benign cache/session duration, NOT an auth
        # credential or login-flow field — it falls through to "general" (gui-safe /
        # editable). Secret status is decided by _is_secret(), not by category.
        return "auth/login"
    if "selector" in k or k in ("dl_selector", "trigger_selector", "dismiss_selectors",
                                "search_result_selector"):
        return "selector"
    if k.startswith("sched") or "watch" in k or k.startswith("window") or k == "quiet_hours":
        return "scheduling"
    if k.startswith("notify") or k.startswith("webhook") or k.startswith("push_"):
        return "notification"
    if k.startswith(("stash", "plex", "jellyfin", "ha_", "jd_", "qb_", "tpdb", "jsonapi")):
        return "integration"
    if k.startswith("dedup"):
        return "dedup"
    if k.startswith(("use_", "proxy", "warmup", "captcha", "flaresolverr", "scrapling",
                     "multi_conn", "curl")):
        return "anti-detection/network"
    if k.startswith(("thumbnail", "embed", "metadata", "subtitle")):
        return "media/metadata"
    if k.startswith(("storage_tier", "spillover", "site_quota", "disk")):
        return "storage"
    if (k.startswith(("max_", "min_", "chunk", "parallel", "bandwidth", "supervisor",
                      "cluster_rate", "tier_probe", "speculative", "cross_site"))
            or k in ("wait", "delay", "headless", "verify_integrity", "verify_hash")):
        return "download/perf"
    return "general"


# v3.66.323 (Phase 4 gap A2 / GAP4): ai_login_assist_enabled is categorized
# auth/login for grouping/section, but it is a boolean FEATURE TOGGLE ("try AI
# to detect the login form when enumeration fails") -- not a credential,
# selector, or secret. The category gate exists to keep credentials/selectors
# out of the gui-safe editor; a non-secret behavioral toggle is operator-
# settable. Open ONLY this named key; every other auth/login field stays gated,
# and the secret check above still wins (a secret in this set stays gated).
_GUI_SAFE_LOGIN_TOGGLES = {"ai_login_assist_enabled"}


def _gui_class(k: str, secret: bool) -> str:
    if secret:
        return "gui-gated (display-never)"
    if k in _GUI_SAFE_LOGIN_TOGGLES:
        return "gui-safe"
    if _categorize(k) in ("auth/login", "selector"):
        return "gui-gated"
    return "gui-safe"


def _schema():
    fields = []
    cats: dict = {}
    for k in _cfg_fields():
        secret = _is_secret(k)
        cat = _categorize(k)
        cats[cat] = cats.get(cat, 0) + 1
        fields.append({"key": k, "category": cat, "secret": secret,
                       "gui_class": _gui_class(k, secret)})
    return {"ok": True, "authoritative_source": "app.py CFG_FIELDS",
            "unique_fields": len(fields), "by_category": cats,
            "secret_fields": [f["key"] for f in fields if f["secret"]],
            "fields": fields, "read_only": True}


# ── read-only effective-value helpers (fail-open, secrets masked) ──
def _mask_nested(v):
    """Recurse a non-secret value so secrets nested inside dicts/lists (e.g.
    accounts[].password) are still presence-masked. Scalars pass through."""
    if isinstance(v, dict):
        return _mask_secrets(v)
    if isinstance(v, list):
        return [_mask_nested(x) for x in v]
    return v


def _mask_secrets(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        if _is_secret(k):
            out[k] = {"present": bool(v)}
        else:
            # VR-P02: descend into non-secret containers so a secret keyed inside
            # a nested dict or a list of dicts (account-pool creds) cannot be
            # returned raw. Flat top-level secrets still presence-mask above.
            out[k] = _mask_nested(v)
    return out


def _sites_config_path() -> Path:
    p = os.environ.get("BD_SITES_CONFIG_PATH")
    if p:
        return Path(p)
    home = os.environ.get("BD_HOME")
    if home:
        return Path(home) / "sites_config.json"
    return _REPO_ROOT / "sites_config.json"


def _site_effective(sid: str) -> dict:
    import json
    keys = set(_cfg_fields())
    try:
        path = _sites_config_path()
        if not path.exists():
            return {"ok": True, "sid": sid, "fields": {}, "note": "no sites_config present (read-only)"}
        data = json.loads(path.read_text(encoding="utf-8"))
        sites = data.get("sites", data) if isinstance(data, dict) else {}
        site = sites.get(sid, {}) if isinstance(sites, dict) else {}
        fields = {k: v for k, v in site.items() if k in keys}
        return {"ok": True, "sid": sid, "fields": _mask_secrets(fields), "read_only": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "sid": sid, "fields": {}, "note": f"read fail-open: {str(e)[:120]}"}


def _global_effective() -> dict:
    try:
        from . import global_config as GC
        cfg = GC.get_config() if hasattr(GC, "get_config") else {}
        known = ["quiet_hours", "wakeup_threshold", "wakeup_cool_down_seconds"]
        return {"ok": True, "keys": _mask_secrets(cfg if isinstance(cfg, dict) else {}),
                "statically_known_keys": known,
                "note": "dynamic/file-backed store; full live-key set is host-resolved",
                "read_only": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "keys": {}, "statically_known_keys":
                ["quiet_hours", "wakeup_threshold", "wakeup_cool_down_seconds"],
                "note": f"read fail-open: {str(e)[:120]}", "read_only": True}


def _vpn_summary() -> dict:
    try:
        from . import vpn_config as VC
        cfg = VC.get_config() if hasattr(VC, "get_config") else {}
        if not isinstance(cfg, dict):
            cfg = {}
        tunnels = []
        for t in (cfg.get("tunnels") or []):
            if isinstance(t, dict):
                tunnels.append({k: t.get(k) for k in
                                ("tunnel_id", "name", "provider", "backend", "location", "enabled")})
        # Defense-in-depth: the on-disk loader merges arbitrary global_settings keys
        # from the file (not just the schema), so bound the returned set to vpn_config's
        # canonical key set. Sourced dynamically so it tracks the schema (no duplication);
        # fail-closed to {} if the canonical set is unavailable. Read-only; no control.
        gs_raw = cfg.get("global_settings", {}) or {}
        safe_keys = set(getattr(VC, "_DEFAULT_GLOBAL_SETTINGS", {}) or {})
        gs = {k: v for k, v in gs_raw.items() if k in safe_keys} if safe_keys else {}
        return {"ok": True, "global_settings": gs,
                "tunnels": tunnels,
                "note": "global_settings filtered to known non-secret keys; "
                        "tunnel @cred secret refs are never returned", "read_only": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "global_settings": {}, "tunnels": [],
                "note": f"read fail-open: {str(e)[:120]}", "read_only": True}


def _env_effective() -> dict:
    # v3.66.312 (Phase 4.6 read panel): the deploy/path + import-time-bound env set
    # surfaced READ-ONLY (effective value + how-to-override). These are set before the
    # process starts (deploy) or bound once at import (HLS/Live constants), so a GUI
    # *write* is meaningless -> display-only, not a control. No secrets in this set.
    deploy = [
        "BD_HOME", "BD_INSTALL_DIR", "BD_REPO", "BD_ROOT", "BD_KB_DIR", "BD_LOG_FILE",
        "BD_SITES_CONFIG_PATH", "BD_VPN_CONFIG_PATH", "BD_WIDGETS_CONFIG_PATH",
        "BD_CAPTURES_ROOT", "BD_DEV_MODE_DISABLE", "BD_DISABLE_KEEPALIVE",
    ]
    import_time = [
        # v3.66.503 (Bucket 1): the 9 HLS / Live / Captcha tunables formerly listed
        # here were promoted to full editable controls (call-time getters backed by
        # the global_config store) and now render under Settings -> Advanced /
        # Challenge handling, not in this read-only env-lock panel.
    ]
    rows = []
    for n in deploy:
        rows.append({"name": n, "effective": os.environ.get(n), "default": "",
                     "kind": "deploy-only",
                     "override": "set in the service unit / shell env, then restart"})
    for n in import_time:
        rows.append({"name": n, "effective": os.environ.get(n), "default": "",
                     "kind": "import-time",
                     "override": "bound at import; set the env var then restart"})
    return {"ok": True, "env": rows, "count": len(rows),
            "note": "Read-only effective values for deploy/path + import-time-bound env vars "
                    "(display-only: a GUI write cannot take effect without a restart).",
            "read_only": True}


# ── routes (all GET-only) ──────────────────────────────────────────
@settings_center_bp.route("/api/settings/schema", methods=["GET"])
def api_settings_schema():
    try:
        return jsonify(_schema())
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@settings_center_bp.route("/api/settings/site/<sid>/effective", methods=["GET"])
def api_settings_site_effective(sid):
    return jsonify(_site_effective(sid))


@settings_center_bp.route("/api/settings/global/effective", methods=["GET"])
def api_settings_global_effective():
    return jsonify(_global_effective())


@settings_center_bp.route("/api/settings/vpn/summary", methods=["GET"])
def api_settings_vpn_summary():
    return jsonify(_vpn_summary())


@settings_center_bp.route("/api/settings/env/effective", methods=["GET"])
def api_settings_env_effective():
    return jsonify(_env_effective())


# ── page (read-only shell) ─────────────────────────────────────────
def _card(title, body):
    return (f"<div style='background:#141414;border:1px solid #262626;border-radius:8px;"
            f"padding:14px 16px;margin:0 0 14px'><h3 style='margin:0 0 8px;color:#e5e5e5'>"
            f"{html.escape(title)}</h3>{body}</div>")


@settings_center_bp.route("/cockpit/settings", methods=["GET"])
def page_settings():
    s = _schema()
    cat_rows = "".join(
        f"<tr><td style='padding:3px 12px 3px 0'>{html.escape(c)}</td>"
        f"<td style='padding:3px 0;color:#9ca3af'>{n}</td></tr>"
        for c, n in sorted(s["by_category"].items(), key=lambda x: -x[1]))
    body = (
        f"<p style='color:#9ca3af'>Read-only configuration overview. "
        f"<b>{s['unique_fields']}</b> per-site fields (authoritative CFG_FIELDS), "
        f"{len(s['secret_fields'])} secrets (presence only).</p>"
        f"<table style='border-collapse:collapse'>{cat_rows}</table>"
        f"<p style='color:#9ca3af;margin-top:12px'>Pages: "
        f"<a href='/cockpit/settings/secrets' style='color:#6cf'>secrets (presence only)</a> &middot; "
        f"per-site editor at <a href='/cockpit/settings/site/default' style='color:#6cf'>"
        f"/cockpit/settings/site/&lt;sid&gt;</a></p>"
        f"<p style='color:#9ca3af;margin-top:12px'>APIs: "
        f"<code>/api/settings/schema</code>, "
        f"<code>/api/settings/site/&lt;sid&gt;/effective</code>, "
        f"<code>/api/settings/global/effective</code>, "
        f"<code>/api/settings/vpn/summary</code>, "
        f"<code>/api/settings/env/effective</code></p>")
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Settings Center (read-only)</title></head>"
        "<body style='background:#0a0a0a;color:#e5e5e5;font-family:system-ui,sans-serif;"
        "margin:0;padding:24px'>"
        "<div style='font-size:12px;margin:0 0 6px'><a href='/cockpit/home' "
        "style='color:#6cf;text-decoration:none'>&larr; Cockpit Home</a></div>"
        "<h2 style='margin:0 0 4px'>Settings Center</h2>"
        "<div style='color:#f59e0b;margin:0 0 16px'>READ-ONLY (Phase 3 Slice 1) — "
        "no edit controls; editing arrives in a later, gated slice.</div>"
        + _card("Per-site config surface", body)
        + "</body></html>")
    return page


# ── Slice 2: gui-safe field editing (gate + validation; write delegates to PUT) ──
#
# Slice 2 does NOT persist. It enforces the gui-safe field GATE server-side and
# value-validates a proposed change set (dry-run), then the editor delegates the
# actual write to the EXISTING `PUT /api/sites/<sid>` — the canonical, audited,
# atomic write path (it already does path/type validation, secret-preservation-on-
# blank, account merge, audit logging, and `_save_sites_config()`). This avoids a
# divergent writer that would desync with the app's in-memory state.
#
# "gui-safe" = schema fields whose gui_class is exactly "gui-safe": excludes the 10
# secrets, the auth/login group, and the selector group (those are Slice 3, gated).

_BOOL_HINT = re.compile(r"^(use_|.*_enabled$)")
_INT_HINT = re.compile(
    r"(^max_|^min_|_count$|_seconds$|_mb$|_kb$|_hours$|_gb$|_pct$|_ms$|_attempts$|"
    r"_pages$|_rows$|_cols$|_port$|^chunk_size_mb$|^parallel_chunks$|_idx$|_id$)")


# v3.66.710: withheld from the editor descriptor ON PURPOSE -- `accounts` is a
# NESTED per-account credential list, so surfacing it in a field editor would
# put credentials on the wire. Secrets elsewhere are presence-only; this one is
# structured, so it is excluded outright. Stated as an allowlist exclusion, not
# achieved as a side-effect of a truncating regex.
_STRUCTURED_CREDENTIAL_FIELDS = frozenset({"accounts"})


def _editable_field_set():
    """gui-safe per-site fields only (no secrets / auth-login / selector / gated)."""
    return {f["key"] for f in _schema()["fields"] if f["gui_class"] == "gui-safe"}


def _gated_editor_field_set():
    """v3.66.710: per-site keys that are editor-visible but NOT gui-safe -- secrets,
    login, selector, relogin.

    This used to MIRROR config_surface_inventory's non-greedy CFG_FIELDS regex (the
    one that truncates at the first nested ']'), on the reasoning that the truncation
    "deliberately excludes structured fields like `accounts`". But a truncation is not
    an allowlist: to withhold ONE credential-nesting field it also dropped ~180
    unrelated keys, and the inventory then scored 57 of 235 per-site keys -- so every
    parity number was a fraction of the wrong denominator.

    Derive from the schema's own gui_class instead, and withhold the credential-nesting
    field EXPLICITLY. Same protection, stated rather than accidental.
    """
    gated = {f["key"] for f in _schema()["fields"]
             if f["gui_class"].startswith("gui-gated")}
    # Secrets are class "gui-gated (display-never)": they DO get a control (the
    # operator can set them) but the value never round-trips -- gated_meta carries
    # presence only. They belong in the gated set; dropping them silently removed the
    # password/token controls from the descriptor.
    try:
        from . import site_editor as _SE
        gated |= set(getattr(_SE, "SECRET_FIELDS", ()) or ())
    except Exception:  # noqa: BLE001
        pass
    return gated - _STRUCTURED_CREDENTIAL_FIELDS


def _infer_type(k: str) -> str:
    if k in ("headless", "skip_if_exists", "verify_integrity", "verify_hash") or _BOOL_HINT.search(k):
        return "bool"
    if _INT_HINT.search(k):
        return "int"
    return "str"


def _site_editor_meta():
    """Read-only metadata from site_editor.py: (numeric_ranges, field_types, required).
    Fail-open — if the import fails the editor degrades to '—'. site_editor.py is
    CONSUMED here, never modified."""
    try:
        from . import site_editor as SE
        ranges = dict(getattr(SE, "NUMERIC_RANGES", {}) or {})
        ftypes = dict(getattr(SE, "_FIELD_TYPES", {}) or {})
        required = set(getattr(SE, "REQUIRED_FIELDS", ()) or ())
        return ranges, ftypes, required
    except Exception:  # noqa: BLE001
        return {}, {}, set()


def _field_descriptor(k, current=None, *, meta=None):
    """Per-field display descriptor (read-only). Secrets => presence-only `current`.
    `default` is '—' (no per-field default source exists); type/range/required come from
    site_editor metadata where available, else a safe fallback / '—'."""
    ranges, ftypes, required = meta if meta is not None else _site_editor_meta()
    secret = _is_secret(k)
    jtype, desc = ftypes.get(k, (None, None))
    rng = ranges.get(k)
    if secret:
        cur = current if isinstance(current, dict) else {"present": bool(current)}
    else:
        cur = current
    src = "CFG_FIELDS (app.py)" + ("; type/range: site_editor.py" if (jtype or rng) else "")
    try:
        from . import site_editor as SE
        enums = getattr(SE, "_FIELD_ENUMS", {}) or {}
    except Exception:  # noqa: BLE001
        enums = {}
    return {
        "key": k,
        "category": _categorize(k),
        "gui_class": _gui_class(k, secret),
        "secret": secret,
        "preserve_on_blank": _preserve_on_blank(k),
        "sticky_nonsecret": (not secret) and str(k) in _STICKY_NONSECRET,
        # v3.66.468: a field with enum choices reports type "enum" to the GUI
        # (so SiteSettings renders a <select>) even though its JSON-Schema type
        # is "string". Derived from enum-presence, not from _FIELD_TYPES.
        "type": ("enum" if (k in enums) else (jtype or ("number" if rng else _infer_type(k)))),
        "enum": list(enums[k]) if k in enums else None,
        "description": desc or "",
        "default": "\u2014",
        "range": list(rng) if rng else None,
        "required": k in required,
        "source": src,
        "current": cur,
    }


def _validate_updates(updates: dict) -> dict:
    """Dry-run gate + value check. Returns {accepted, rejected}; performs NO write."""
    editable = _editable_field_set()
    all_fields = set(_cfg_fields())
    ranges, ftypes, _required = _site_editor_meta()
    accepted, rejected = {}, {}
    for k, v in (updates or {}).items():
        if k not in all_fields:
            rejected[k] = "unknown field (not in CFG_FIELDS)"
            continue
        if k not in editable:
            # explain why it is gated
            secret = _is_secret(k)
            cat = _categorize(k)
            reason = ("secret (display-never)" if secret
                      else f"{cat}: gui-gated — not editable in this slice")
            rejected[k] = reason
            continue
        # Type comes from site_editor metadata where declared (boolean/integer/number);
        # a field with a NUMERIC_RANGE but no declared type is treated as a number; else
        # fall back to the name-heuristic. Ranges are enforced for numeric fields.
        jtype = ftypes.get(k, (None, None))[0]
        rng = ranges.get(k)
        if jtype == "boolean":
            t = "bool"
        elif jtype == "integer":
            t = "int"
        elif jtype == "number" or rng is not None:
            t = "num"
        else:
            t = _infer_type(k)
        if isinstance(v, (list, dict)):
            rejected[k] = f"expected scalar {t}, got {type(v).__name__}"
            continue
        if t == "bool":
            if isinstance(v, bool):
                accepted[k] = v
            elif str(v).lower() in ("true", "1", "yes", "on"):
                accepted[k] = True
            elif str(v).lower() in ("false", "0", "no", "off"):
                accepted[k] = False
            else:
                rejected[k] = "expected boolean (true/false)"
        elif t == "int":
            # v3.66.523 (VR-P12): int(inf) raises OverflowError (echoed as a 500).
            # Guard non-finite floats up front WITHOUT changing int(v) parsing for
            # any other input (NaN-as-float would hit the ValueError path anyway).
            if isinstance(v, float) and not math.isfinite(v):
                rejected[k] = "expected a finite integer"
                continue
            try:
                iv = int(v)
            except (TypeError, ValueError):
                rejected[k] = "expected integer"
                continue
            if rng and not (rng[0] <= iv <= rng[1]):
                rejected[k] = f"out of range — expected integer in [{rng[0]}, {rng[1]}]"
            else:
                accepted[k] = iv
        elif t == "num":
            try:
                fv = float(v)
            except (TypeError, ValueError):
                rejected[k] = "expected number"
                continue
            # v3.66.523 (VR-P12): a range-less num field would echo a raw inf/NaN
            # into `accepted`, which jsonify cannot serialize (-> 500).
            if not math.isfinite(fv):
                rejected[k] = "expected a finite number"
                continue
            if rng and not (rng[0] <= fv <= rng[1]):
                rejected[k] = f"out of range — expected number in [{rng[0]}, {rng[1]}]"
            else:
                accepted[k] = int(fv) if fv.is_integer() else fv
        else:
            accepted[k] = "" if v is None else str(v)
    return {"ok": True, "accepted": accepted, "rejected": rejected,
            "note": "dry-run only; apply via PUT /api/sites/<sid> (the audited write path)"}


@settings_center_bp.route("/api/settings/site/<sid>/editable", methods=["GET"])
def api_settings_site_editable(sid):
    """gui-safe editable fields + current values (read-only; secrets excluded)."""
    eff = _site_effective(sid)
    editable = _editable_field_set()
    fields = {k: v for k, v in (eff.get("fields") or {}).items() if k in editable}
    # include editable keys absent from the site config (so the editor can show them)
    for k in sorted(editable):
        fields.setdefault(k, None)
    # Slice 4: per-field descriptors (current/default/source/type/range), grouped by the
    # runtime category. Existing keys preserved for back-compat.
    meta = _site_editor_meta()
    descriptors = [_field_descriptor(k, fields.get(k), meta=meta) for k in sorted(editable)]
    groups: dict = {}
    for d in descriptors:
        groups.setdefault(d["category"], []).append(d)
    # v3.66.310 (Phase 4.1): SEPARATE gated block for the per-site keys that are NOT
    # gui-safe — secrets / login / selector / relogin. Kept OUT of field_meta so the
    # slice4 F2 invariant (no secret ever in field_meta, no value leak) is untouched.
    # eff["fields"] already presence-masks secrets (via _site_effective -> _mask_secrets),
    # so a secret's `current` is {"present": bool} and its VALUE never reaches the wire.
    gated = _gated_editor_field_set()
    eff_fields = eff.get("fields") or {}
    def _safe_gated_current(k):
        cur = eff_fields.get(k)
        # defense-in-depth: never emit a structured (list/dict) value as a raw current
        # unless it is already the presence-mask dict. Anything structured -> presence-only.
        if isinstance(cur, dict) and set(cur) == {"present"}:
            return cur
        if isinstance(cur, (list, dict)):
            return {"present": bool(cur)}
        return cur
    gdesc = [_field_descriptor(k, _safe_gated_current(k), meta=meta) for k in sorted(gated)]
    gated_groups: dict = {}
    for d in gdesc:
        gated_groups.setdefault(d["category"], []).append(d)
    return jsonify({"ok": True, "sid": sid, "editable_count": len(editable),
                    "fields": fields,
                    "field_meta": {d["key"]: d for d in descriptors},
                    "groups": groups,
                    "gated_count": len(gated),
                    "gated_meta": {d["key"]: d for d in gdesc},
                    "gated_groups": gated_groups,
                    "write_via": f"PUT /api/sites/{sid}",
                    "validate_via": f"POST /api/settings/site/{sid}/validate",
                    "read_only_endpoint": True})


@settings_center_bp.route("/api/settings/site/<sid>/validate", methods=["POST"])
def api_settings_site_validate(sid):
    """Dry-run validation/gate for a proposed gui-safe change set. NO write."""
    # v3.66.523 (VR-P09): a non-object body ([1,2,3]/null/scalar) made body.get
    # raise AttributeError, which this handler's own except swallowed into a 500.
    # Reject it as a clean 400 before the try (so the global handler isn't needed).
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    updates = body.get("updates", body)
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "'updates' must be a JSON object"}), 400
    try:
        return jsonify(_validate_updates(updates))
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


_LABEL_ACRONYMS = {
    "url": "URL", "api": "API", "ai": "AI", "id": "ID", "ui": "UI", "vpn": "VPN",
    "http": "HTTP", "tls": "TLS", "dl": "download", "db": "DB", "gb": "GB", "mb": "MB",
    "kb": "KB", "ms": "ms", "pct": "%", "qb": "qB", "tpdb": "TPDB", "ha": "HA", "cffi": "cffi",
}

# Short, static, presentation-only captions for a few groups. Unknown -> "".
_CATEGORY_CAPTIONS = {
    "general": "Miscellaneous per-site settings.",
    "download/perf": "Throughput, retries, and download tuning.",
    "anti-detection/network": "Browser/network behavior and anti-detection.",
    "scheduling": "When this site runs.",
    "notification": "Per-site notification routing.",
    "integration": "External service integrations.",
    "media/metadata": "Media handling and metadata.",
    "storage": "Disk and spillover.",
    "dedup": "Duplicate detection.",
}


def _humanize(key: str) -> str:
    """Presentation-only label derived ONLY from the field key (never from a value)."""
    parts = str(key).split("_")
    out = []
    for i, p in enumerate(parts):
        if p in _LABEL_ACRONYMS:
            out.append(_LABEL_ACRONYMS[p])
        elif i == 0:
            out.append(p.capitalize())
        else:
            out.append(p)
    return " ".join(out) if out else str(key)


def _fmt_current(d) -> str:
    """Render a descriptor's current value. Secrets => presence-only badge (never the value)."""
    cur = d.get("current")
    if d.get("secret"):
        present = cur.get("present") if isinstance(cur, dict) else bool(cur)
        if present:
            return ("<span style='background:#14532d;color:#bbf7d0;border-radius:10px;"
                    "padding:1px 8px;font-size:12px'>set</span>")
        return ("<span style='background:#1f2937;color:#9ca3af;border-radius:10px;"
                "padding:1px 8px;font-size:12px'>not set</span>")
    if cur in (None, ""):
        return "<span style='color:#6b7280'>—</span>"
    return f"<span style='color:#e5e7eb'>{html.escape(str(cur))}</span>"


def _fmt_constraints(d) -> str:
    bits = []
    if d.get("range"):
        lo, hi = d["range"]
        bits.append(f"<span style='background:#1f2937;color:#cbd5e1;border-radius:6px;"
                    f"padding:1px 6px;font-size:12px'>{html.escape(str(lo))}\u2013{html.escape(str(hi))}</span>")
    if d.get("required"):
        bits.append("<span style='color:#f59e0b;font-size:12px'>required</span>")
    if d.get("preserve_on_blank"):
        bits.append("<span style='color:#9ca3af;font-size:12px'>preserve-on-blank</span>")
    return " ".join(bits) if bits else "<span style='color:#6b7280'>—</span>"


def _help_text(d) -> str:
    """Help text from site_editor description ONLY; never invented when absent."""
    desc = (d.get("description") or "").strip()
    if not desc:
        return "<span style='color:#4b5563'>—</span>"
    return f"<span style='color:#9ca3af'>{html.escape(desc)}</span>"


def _field_label_cell(d) -> str:
    """Humanized label + raw key (always shown) + source as a small dim line."""
    return (f"<div style='color:#e5e7eb;font-weight:600'>{html.escape(_humanize(d['key']))}</div>"
            f"<code style='color:#6b7280;font-size:12px'>{html.escape(d['key'])}</code>"
            f"<div style='color:#4b5563;font-size:11px'>{html.escape(d['source'])}</div>")


def _editable_section(cat, descriptors) -> str:
    cap = _CATEGORY_CAPTIONS.get(cat, "")
    cap_html = (f"<p style='color:#6b7280;margin:0 0 8px;font-size:13px'>{html.escape(cap)}</p>"
                if cap else "")
    head = ("<tr style='color:#6b7280;text-align:left;border-bottom:1px solid #262626'>"
            "<th style='padding:4px 12px 6px 0'>field</th>"
            "<th style='padding:4px 12px 6px 0'>type</th>"
            "<th style='padding:4px 12px 6px 0'>default</th>"
            "<th style='padding:4px 12px 6px 0'>constraints</th>"
            "<th style='padding:4px 12px 6px 0'>current</th>"
            "<th style='padding:4px 0 6px'>description</th></tr>")
    rows = "".join(
        "<tr style='border-bottom:1px solid #1a1a1a;vertical-align:top'>"
        f"<td style='padding:6px 12px 6px 0'>{_field_label_cell(d)}</td>"
        f"<td style='padding:6px 12px 6px 0;color:#9ca3af'>{html.escape(str(d['type']))}</td>"
        f"<td style='padding:6px 12px 6px 0;color:#6b7280'>{html.escape(str(d['default']))}</td>"
        f"<td style='padding:6px 12px 6px 0'>{_fmt_constraints(d)}</td>"
        f"<td style='padding:6px 12px 6px 0'>{_fmt_current(d)}</td>"
        f"<td style='padding:6px 0;font-size:13px'>{_help_text(d)}</td></tr>"
        for d in descriptors)
    return _card(f"{html.escape(cat)} · {len(descriptors)} field(s)",
                 cap_html + f"<table style='border-collapse:collapse;width:100%'>{head}{rows}</table>")


@settings_center_bp.route("/cockpit/settings/site/<sid>", methods=["GET"])
def page_settings_site(sid):
    meta = _site_editor_meta()
    eff = _site_effective(sid)
    eff_fields = eff.get("fields") or {}
    editable = sorted(_editable_field_set())

    # editable (gui-safe) descriptors, grouped by runtime category
    descriptors = [_field_descriptor(k, eff_fields.get(k), meta=meta) for k in editable]
    groups: dict = {}
    for d in descriptors:
        groups.setdefault(d["category"], []).append(d)
    sections = "".join(_editable_section(c, groups[c]) for c in sorted(groups))

    legend_card = _card(
        "How to read this",
        "<p style='color:#9ca3af;margin:0'>Each editable field shows its humanized label, raw key, "
        "declared <b>type</b>, <b>default</b> (\u2014 when none is declared), <b>constraints</b> "
        "(numeric range, required, preserve-on-blank), the <b>current</b> value, and a <b>description</b> "
        "(shown only where one is defined). Edits are validated, then written via the audited PUT — "
        "see <i>Save flow</i> below.</p>")

    # sticky non-secrets (auth/login): NOT editable here, shown read-only with current value
    sticky_rows = "".join(
        "<tr style='vertical-align:top'>"
        f"<td style='padding:4px 12px 4px 0'>"
        f"<div style='color:#e5e7eb;font-weight:600'>{html.escape(_humanize(k))}</div>"
        f"<code style='color:#6b7280;font-size:12px'>{html.escape(k)}</code></td>"
        f"<td style='padding:4px 12px 4px 0'>{_fmt_current(_field_descriptor(k, eff_fields.get(k), meta=meta))}</td>"
        f"<td style='padding:4px 0;color:#9ca3af;font-size:13px'>{_help_text(_field_descriptor(k, eff_fields.get(k), meta=meta))}</td></tr>"
        for k in sorted(_STICKY_NONSECRET))
    sticky_card = _card(
        "Sticky (auth/login) — preserve-on-blank, not editable in this slice",
        "<p style='color:#9ca3af;margin:0 0 8px'>Non-secret and round-tripped to the UI. "
        "<b>Leave blank to keep current</b> — the audited writer preserves these when submitted blank.</p>"
        f"<table style='border-collapse:collapse'>{sticky_rows}</table>")

    # secrets: display-never (presence only); managed via the gated secrets page
    secret_keys = sorted(_schema()["secret_fields"])
    secret_rows = "".join(
        "<tr><td style='padding:4px 12px 4px 0'>"
        f"<div style='color:#e5e7eb;font-weight:600'>{html.escape(_humanize(k))}</div>"
        f"<code style='color:#6b7280;font-size:12px'>{html.escape(k)}</code></td>"
        f"<td style='padding:4px 0'>{_fmt_current(_field_descriptor(k, eff_fields.get(k), meta=meta))}</td></tr>"
        for k in secret_keys)
    secret_card = _card(
        f"Secrets ({len(secret_keys)}) — display-never (presence only)",
        "<p style='color:#9ca3af;margin:0 0 8px'>Values are never shown or editable here; "
        "the badge reflects presence only. Secret lifecycle is gated — see "
        "<code>/cockpit/settings/secrets</code>.</p>"
        f"<table style='border-collapse:collapse'>{secret_rows}</table>")

    save_card = _card(
        "Save flow",
        "<p style='color:#9ca3af;margin:0'>Edits are first checked by "
        f"<code>POST /api/settings/site/{html.escape(sid)}/validate</code> (dry-run gate; no write), "
        "then applied only through the existing audited "
        "<code>PUT /api/sites/&lt;sid&gt;</code> (path/type validation, secret &amp; sticky "
        "preserve-on-blank, audit logging). The Settings Center never writes directly.</p>")

    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Settings — {html.escape(sid)} (gui-safe edit)</title></head>"
        "<body style='background:#0a0a0a;color:#e5e5e5;font-family:system-ui,sans-serif;"
        "margin:0;padding:24px;line-height:1.45'>"
        f"<h2 style='margin:0 0 4px'>Settings — site <code>{html.escape(sid)}</code></h2>"
        "<div style='color:#f59e0b;margin:0 0 16px'>gui-safe per-site fields (Phase 3 Slice 5 — UI/UX polish). "
        "Secrets are display-never; auth/login &amp; selector fields stay gated. Saving validates, "
        "then writes via the existing audited <code>PUT /api/sites/&lt;sid&gt;</code>. "
        "NEEDS OPERATOR CLICK-THROUGH.</div>"
        f"<p style='color:#9ca3af;margin:0 0 14px'>{len(editable)} gui-safe editable field(s) across "
        f"{len(groups)} group(s).</p>"
        + legend_card + sections + sticky_card + secret_card + save_card
        + "</body></html>")
    return page


# ── Slice 3 (read foundation): secrets presence/health — READ-ONLY, never values ──
#
# The dangerous Slice-3 WRITE surfaces (secret change/unlock/delete/rotate, backup/
# restore, maintenance/feature-flag/config-reload, vpn tunnel start/stop) are NOT
# implemented here — each needs its own approval, server-side gating inspection on
# stash, and (for global_config) a live-key read. This piece only SURFACES which
# secrets are set, as booleans/counts; values are never read or returned.

def _secrets_health():
    import json
    secret_fields = _schema()["secret_fields"]
    out = {"ok": True, "secret_field_names": secret_fields, "per_site": {},
           "global": {}, "read_only": True,
           "note": "presence/health only — secret values are never read or returned"}
    try:
        path = _sites_config_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            sites = data.get("sites", data) if isinstance(data, dict) else {}
            for sid, site in (sites.items() if isinstance(sites, dict) else []):
                if not isinstance(site, dict):
                    continue
                present = {f: bool(site.get(f)) for f in secret_fields if f in site}
                if present:
                    out["per_site"][sid] = {
                        "present": present,
                        "count_set": sum(1 for v in present.values() if v)}
    except Exception as e:  # noqa: BLE001
        out["per_site_note"] = f"read fail-open: {str(e)[:120]}"
    try:
        from . import global_config as GC
        cfg = GC.get_config() if hasattr(GC, "get_config") else {}
        out["global"] = {k: bool(v) for k, v in (cfg or {}).items()
                         if _is_secret(k)}
    except Exception as e:  # noqa: BLE001
        out["global_note"] = f"read fail-open: {str(e)[:120]}"
    return out


@settings_center_bp.route("/api/settings/secrets/health", methods=["GET"])
def api_settings_secrets_health():
    return jsonify(_secrets_health())


@settings_center_bp.route("/cockpit/settings/secrets", methods=["GET"])
def page_settings_secrets():
    h = _secrets_health()
    rows = "".join(
        f"<tr><td style='padding:3px 12px 3px 0;color:#d4d4d4'>{html.escape(sid)}</td>"
        f"<td style='padding:3px 0;color:#6b7280'>{v['count_set']} set</td></tr>"
        for sid, v in sorted(h.get("per_site", {}).items()))
    gcount = len(h.get("global", {}))
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Secrets health (read-only)</title></head>"
        "<body style='background:#0a0a0a;color:#e5e5e5;font-family:system-ui,sans-serif;"
        "margin:0;padding:24px'>"
        "<h2 style='margin:0 0 4px'>Secrets health</h2>"
        "<div style='color:#f59e0b;margin:0 0 16px'>READ-ONLY — presence/health only. "
        "Values are never shown. Secret lifecycle actions (change/unlock/delete/rotate) are "
        "gated to a later, per-endpoint-approved slice.</div>"
        + _card(f"Per-site secrets set ({len(h.get('per_site', {}))} sites with secrets; "
                f"{gcount} global secret keys present)",
                f"<table style='border-collapse:collapse'>{rows or '<tr><td>none</td></tr>'}</table>")
        + "</body></html>")
    return page


def register_routes(app):
    """Register the read-only Settings Center blueprint. Returns route count added."""
    before = len(list(app.url_map.iter_rules()))
    app.register_blueprint(settings_center_bp)
    return len(list(app.url_map.iter_rules())) - before
