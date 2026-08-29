#!/usr/bin/env python3
"""
config_surface_inventory.py — GUI Parity Program, Phase 2 (inventory only).

DISCOVERY/REPORTING ONLY. Scans existing sources for configuration surfaces and
classifies their GUI exposure. Reads, never writes: BD_* environment variables
(with discovered defaults), per-site config keys + validation ranges from
site_editor.py, template schema fields, and the global_config store API. It does
NOT add a settings center, routes, GUI controls, or any runtime behavior, and it
never modifies a config.

For every setting: key, source_file, category, default, gui_exposure (full|partial|
none), read_write, validation_rules, description, risk (none|low|medium|high),
recommended_gui_section, related.

Outputs: reports/config_surface_inventory.json and .md.
"""
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
import spa_population  # noqa: E402  (needs the sys.path insert above)

try:
    import report_core as _RC  # type: ignore
except Exception:  # noqa: BLE001
    _RC = None

_ENV_RE = re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"'](BD_[A-Z0-9_]+)[\"']\s*(?:,\s*([^)]+?))?\)")
_ENV_IDX = re.compile(r"os\.environ\[\s*[\"'](BD_[A-Z0-9_]+)[\"']\s*\]")
# A bare ``env.get("BD_...")`` where ``env`` is an os.environ alias (e.g.
# capture_session._hud_enabled: ``env = os.environ if env is None else env``).
# This is a genuine read site, so attribute source_file to it (drives danger
# classification) — not to a mere literal/comment mention elsewhere.
_ENV_ALIAS_RE = re.compile(r"\benv\.get\(\s*[\"'](BD_[A-Z0-9_]+)[\"']\s*(?:,\s*([^)]+?))?\)")

# category + risk + section by key-token heuristics
_CAT_RULES = [
    (("AUTH", "THROTTLE", "TOKEN", "LOGIN", "RELOGIN"), "auth/session", "high", "Auth & Sessions"),
    (("SESSION", "KEEPALIVE", "WARMUP", "PRELOGIN"), "auth/session", "high", "Auth & Sessions"),
    (("BROWSER", "CLOAK", "PLAYWRIGHT", "PROFILE", "NOVNC"), "browser", "medium", "Browser"),
    (("CAPTURE", "WACZ", "RRWEB", "SNAPDOM", "DOM", "SENTINEL"), "capture", "medium", "Capture"),
    (("DOWNLOAD", "CHUNK", "PARALLEL", "RESOLUTION", "MIN_SIZE", "DISK"), "download", "medium", "Downloads"),
    (("CAPTCHA", "CHALLENGE", "HONEYPOT"), "challenge", "high", "Challenge handling"),
    (("VPN", "PROXY", "BANDWIDTH"), "network", "medium", "Network"),
    (("COCKPIT", "SHELL", "TASKS", "PORT"), "cockpit", "medium", "Cockpit"),
    (("PUSH", "NOTIFY", "APPRISE"), "notifications", "low", "Notifications"),
    (("REPORT", "MONITOR", "ANALYTICS", "DRIFT", "INDEX"), "monitoring/reporting", "low", "Monitoring/Reports"),
    (("YOUTUBE", "CIPHER", "API"), "acquisition", "high", "Acquisition (advanced)"),
    (("INSTALL", "DIR", "ROOT", "PATH", "HOME"), "paths", "low", "Paths"),
    (("AUTONOMY", "ENABLED", "DISABLE", "FLAG"), "feature flag", "medium", "Feature flags"),
]


def _classify(key):
    k = key.upper()
    for tokens, cat, risk, section in _CAT_RULES:
        if any(t in k for t in tokens):
            return cat, risk, section
    return "other", "low", "Advanced"


def _is_flag(key, default):
    k = key.upper()
    return (k.endswith("_ENABLED") or "DISABLE" in k or "_FLAG" in k
            or (default or "").strip().strip('"\'').lower() in ("0", "1", "true", "false", ""))


def _scan_env(root):
    found = {}  # key -> {"default", "files":set}
    for base in ("bulk_downloader", "tools"):
        d = Path(root) / base
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(p, root)
            for m in _ENV_RE.finditer(text):
                key, dflt = m.group(1), (m.group(2) or "").strip()
                e = found.setdefault(key, {"default": None, "files": set()})
                e["files"].add(rel)
                if e["default"] is None and dflt:
                    e["default"] = dflt[:60]
            for m in _ENV_ALIAS_RE.finditer(text):
                key, dflt = m.group(1), (m.group(2) or "").strip()
                e = found.setdefault(key, {"default": None, "files": set()})
                e["files"].add(rel)
                if e["default"] is None and dflt:
                    e["default"] = dflt[:60]
            for m in _ENV_IDX.finditer(text):
                key = m.group(1)
                e = found.setdefault(key, {"default": None, "files": set()})
                e["files"].add(rel)
                e["default"] = e["default"] or "(required, no default)"
    items = []
    # coverage pass: ensure EVERY distinct BD_* literal in code is represented,
    # even if not read via the os.environ.get pattern (e.g. indirect helpers).
    all_keys = {}
    for base in ("bulk_downloader", "tools"):
        d = Path(root) / base
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(p, root)
            # v3.66.319 (4.3c): match BD_* only at a token boundary (real literals
            # in quotes / env-key tuples). The negative-lookbehind excludes the
            # `BD_...` substring inside a leading-underscore MODULE CONSTANT
            # (`_BD_ENV_VARS`, `_BD_TO_APPRISE_EVENT`, ...) — those are not env vars
            # and were scanner false-positives.
            for k in re.findall(r"(?<![A-Za-z0-9_])BD_[A-Z0-9_]+", text):
                all_keys.setdefault(k, set()).add(rel)
    for key in sorted(set(found) | set(all_keys)):
        cat, risk, section = _classify(key)
        e = found.get(key, {"default": None, "files": set()})
        # source_file must be where the var is actually READ (os.environ.get /
        # environ[]) so danger classification keys off the real backing file —
        # NOT a mere literal/comment mention (e.g. the global_config.py schema
        # comment naming the env var). Prefer a real read site; fall back to a
        # mention only when there is no os.environ read at all. all_sources stays
        # comprehensive (reads ∪ mentions).
        read_files = sorted(e["files"])
        files = sorted(e["files"] | all_keys.get(key, set()))
        src = read_files[0] if read_files else (files[0] if files else "")
        items.append({
            "key": key, "source_file": src,
            "all_sources": files,
            "category": "feature flag" if _is_flag(key, e["default"]) else cat,
            "default": e["default"], "gui_exposure": "none",
            "read_write": "read (env at startup)",
            "validation_rules": "string env var; parsed per-call",
            "description": f"environment override ({cat})",
            "risk": risk, "recommended_gui_section": section, "related": [],
            "kind": "env_var",
        })
    return items


# Authoritative metadata for per-site STRING fields not in NUMERIC_RANGES/_FIELD_TYPES.
# (category, risk, description, validation, section)
_STRING_FIELD_META = {
    "login_url": ("auth/session", "high", "URL of the site login page", "url", "Site editor → Login"),
    "success_url": ("auth/session", "high", "URL signalling a successful login", "url", "Site editor → Login"),
    "user_field": ("auth/session", "high", "Selector for the username field", "selector", "Site editor → Login"),
    "pass_field": ("auth/session", "high", "Selector for the password field", "selector", "Site editor → Login"),
    "submit_btn": ("auth/session", "high", "Selector for the login submit button", "selector", "Site editor → Login"),
    "login_trigger": ("auth/session", "high", "Selector that reveals the login form", "selector", "Site editor → Login"),
    "username": ("auth/session", "high", "Stored login username for this site", "string", "Site editor → Login"),
    "dl_selector": ("acquisition", "medium", "Selector for the download control", "selector", "Site editor → Selectors"),
    "trigger_selector": ("acquisition", "medium", "Selector that triggers the download", "selector", "Site editor → Selectors"),
    "dismiss_selectors": ("acquisition", "medium", "Selectors to dismiss per-page overlays/popups (cookie/age/consent), tried on every URL", "selector list", "Site editor → Selectors"),
    "dismiss_selectors_login": ("auth/session", "medium", "Selectors to dismiss the post-login interstitial wall, fired once after login", "selector list", "Site editor → Login"),
    "cookie_file": ("paths", "low", "Path to a cookie file for this site", "path", "Site editor → Advanced"),
    "download_dir": ("paths", "low", "Per-site download directory", "path", "Site editor → Downloads"),
    "filename_template": ("download", "low", "Output filename template", "template string", "Site editor → Downloads"),
    "sched_time": ("scheduling", "low", "Scheduled run time", "time", "Site editor → Schedule"),
    "sched_repeat": ("scheduling", "low", "Schedule repeat interval", "enum/interval", "Site editor → Schedule"),
}
_RELATED = {
    "login_url": ["login_trigger", "user_field", "pass_field", "submit_btn", "success_url", "username"],
    "login_trigger": ["login_url", "user_field", "pass_field", "submit_btn"],
    "user_field": ["login_url", "login_trigger", "pass_field", "submit_btn"],
    "pass_field": ["login_url", "login_trigger", "user_field", "submit_btn"],
    "submit_btn": ["login_url", "login_trigger", "user_field", "pass_field"],
    "success_url": ["login_url"],
    "username": ["login_url", "auth_token"],
    "dl_selector": ["trigger_selector", "dismiss_selectors"],
    "trigger_selector": ["dl_selector", "dismiss_selectors"],
    "dismiss_selectors": ["dl_selector", "trigger_selector", "dismiss_selectors_login"],
    "dismiss_selectors_login": ["dismiss_selectors", "login_url", "success_url"],
    "parallel_chunks": ["parallel_min_size_mb", "chunk_size_mb", "max_concurrent"],
    "parallel_min_size_mb": ["parallel_chunks"],
    "chunk_size_mb": ["parallel_chunks", "use_http_dl"],
    "auto_relogin_enabled": ["auto_relogin_interval_hours", "prelogin_minutes"],
    "auto_relogin_interval_hours": ["auto_relogin_enabled"],
    "sched_enabled": ["sched_time", "sched_repeat"],
    "sched_time": ["sched_enabled", "sched_repeat"],
    "sched_repeat": ["sched_enabled", "sched_time"],
}


def _cfg_fields(root):
    """Authoritative per-site field list from CFG_FIELDS (hoisted to app_kernel.py
    in DECOMP-R2a; read across app.py + app_*.py so the source location is
    glob-robust regardless of which app_* module holds the kernel).

    v3.66.710: this used to regex `CFG_FIELDS\\s*=\\s*\\[(.*?)\\]` -- NON-GREEDY, so
    the capture stopped at the FIRST `]` it met inside the multi-line list literal
    and returned 39 of 235 keys. The inventory therefore scored 57 per-site keys
    while 235 are GUI-editable, and every parity number computed from it was a
    fraction of the wrong denominator. Parse the literal with AST: a list is a list,
    not a substring.
    """
    pkg = Path(root) / "bulk_downloader"
    appf = pkg / "app.py"
    if not appf.is_file():
        return set()
    for q in [appf] + sorted(pkg.glob("app_*.py")):
        try:
            tree = ast.parse(open(q, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if getattr(tgt, "id", "") != "CFG_FIELDS":
                    continue
                try:
                    val = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(val, (list, tuple, set)):
                    return {v for v in val if isinstance(v, str)}
    return set()


def _editable_site_fields(root):
    """v3.66.710: DERIVE per-site GUI exposure instead of asserting it.

    The old code hardcoded every site key to gui_exposure="partial" and let a
    hand-maintained manifest override it -- i.e. it asserted exposure with ZERO
    knowledge of what the GUI renders. That is how 57 env keys came to claim
    gui_exposure=full while none were writable, and how the per-site layer was
    scored at 57 of 235.

    The site editor is SCHEMA-DRIVEN: SiteSettings.tsx renders whatever
    GET /api/settings/site/<sid>/editable returns, and that descriptor is built from
    app_settings_center._editable_field_set() (gui_class == "gui-safe") plus the
    gated set. So exposure is derivable from source -- ask the thing that builds the
    form, don't guess.

    Returns (editable, gated). Falls back to (set(), set()) if the app cannot be
    imported, in which case the caller keeps the old conservative "partial".
    """
    try:
        from bulk_downloader import app_settings_center as _asc
        editable = set(_asc._editable_field_set())
        gated = set(_asc._gated_editor_field_set())
        # Secrets ARE carried by the descriptor -- as presence-only ({present: bool}),
        # never as values. That is exposure (the operator can set them), so they score
        # "partial", not "none". Verified against the live
        # GET /api/settings/site/<sid>/editable payload.
        try:
            from bulk_downloader import site_editor as _se
            gated |= set(getattr(_se, "SECRET_FIELDS", ()) or ())
        except Exception:  # noqa: BLE001
            pass
        return editable, gated
    except Exception:  # noqa: BLE001 -- inventory must never hard-fail on import
        return set(), set()


def _scan_site_keys(root):
    """Per-site config keys. Primary source = authoritative CFG_FIELDS (app.py),
    enriched with site_editor.py NUMERIC_RANGES / _FIELD_TYPES / SECRET_FIELDS /
    REQUIRED_FIELDS and string-field metadata."""
    p = Path(root) / "bulk_downloader" / "site_editor.py"
    items = []
    src = open(p, encoding="utf-8", errors="replace").read() if p.is_file() else ""
    ranges, field_types, secret, required = {}, {}, set(), set()
    if src:
        try:
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.Assign):
                    continue
                name = getattr(node.targets[0], "id", "")
                try:
                    val = ast.literal_eval(ast.get_source_segment(src, node.value) or "")
                except Exception:  # noqa: BLE001
                    val = None
                if name == "NUMERIC_RANGES" and isinstance(val, dict):
                    for k, v in val.items():
                        if (isinstance(v, (tuple, list)) and len(v) == 2
                                and all(isinstance(x, (int, float)) for x in v)):
                            ranges[k] = (v[0], v[1])
                elif name == "_FIELD_TYPES" and isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, (tuple, list)) and len(v) == 2:
                            field_types[k] = (str(v[0]), str(v[1]))
                elif name == "SECRET_FIELDS" and isinstance(val, (set, frozenset, list, tuple)):
                    secret = set(val)
                elif name == "REQUIRED_FIELDS" and isinstance(val, (set, list, tuple)):
                    required = set(val)
        except Exception:  # noqa: BLE001
            pass
        if not secret:
            m = re.search(r"SECRET_FIELDS\s*=\s*frozenset\(\{([^}]*)\}\)", src)
            if m:
                secret = set(re.findall(r"[\"']([a-z_]+)[\"']", m.group(1)))
    cfg = _cfg_fields(root)
    keys = set(ranges) | set(field_types) | secret | required | cfg
    _editable, _gated = _editable_site_fields(root)
    for key in sorted(keys):
        is_secret = key in secret
        meta = _STRING_FIELD_META.get(key)
        rng = ranges.get(key)
        ftype, fdesc = field_types.get(key, (None, None))
        srcs = ["bulk_downloader/site_editor.py"]
        if key in cfg:
            srcs.append("bulk_downloader/app.py (CFG_FIELDS)")
        if is_secret:
            cat, risk, section = "per-site secret", "high", "Site editor (secret)"
            validation = "secret — stripped on export"
            desc = fdesc or "per-site credential/secret"
        elif meta:
            mcat, mrisk, mdesc, mval, msec = meta
            cat, risk, section = f"per-site/{mcat}", mrisk, msec
            validation = mval + (" (required)" if key in required else "")
            desc = fdesc or mdesc
        else:
            c, r, s = _classify(key.upper())
            cat, risk, section = f"per-site/{c}", r, ("Site editor")
            if rng:
                validation = f"{ftype or 'number'} in [{rng[0]},{rng[1]}]"
            elif ftype:
                validation = ftype + (" (required)" if key in required else "")
            elif key in required:
                validation = "required"
            else:
                validation = "free-form"
            desc = fdesc or f"per-site setting ({c})"
        items.append({
            "key": key, "source_file": srcs[0], "all_sources": srcs,
            "category": cat, "default": None,
            # DERIVED (710), not asserted: full = the editor renders it as a
            # gui-safe control; partial = present but gated (secret/selector/auth);
            # none = the descriptor does not carry it, so no control exists.
            # full = a control exists. gui-gated fields DO render controls (in the
            # gated groups; secrets presence-only), so they are exposed. "none" is
            # reserved for keys the descriptor genuinely does not carry -- today just
            # `accounts` (nested credentials, withheld on purpose).
            "gui_exposure": ("full" if key in (_editable | _gated)
                             else "none" if (_editable or _gated)
                             else "partial"),
            "read_write": "read+write (site editor)",
            "validation_rules": validation, "description": desc,
            "risk": risk, "recommended_gui_section": section,
            "related": _RELATED.get(key, []), "kind": "site_key",
        })
    return items


# Names this module's key regex picks out of the store modules that are NOT
# persisted config keys. The regex is `"<lowercase>"` followed by `:`, which
# matches three things it does not mean to:
#
#   * fields of internal diagnostic records built inside function bodies --
#     vpn_config.load() reports quarantined tunnels as
#     {"index", "tunnel_id", "path", "error"}, which is a report shape, not
#     anything written to tunnels.json;
#   * a quoted string that merely PRECEDES a colon belonging to some other
#     construct, e.g. `if getattr(backend, "name", "") != "plaintext":` --
#     the `:` there ends the `if`, so "plaintext" is not a key at all;
#   * loop/comprehension locals.
#
# This set is deliberately CHECKABLE rather than asserted: every name in it
# must be absent from a freshly saved store document, which
# tests/test_vpn_config_load_quarantine.py pins directly. Widening it without
# that evidence would let a real config key be hidden from the parity ledger --
# the gate-that-cannot-see-its-subject failure this project keeps re-learning.
#
# The durable fix is a predicate that reads real dict keys at module level
# instead of a regex over the whole file. Measured: that predicate is stable
# across diffs and keeps every genuine key, but it also drops `_saved_at`
# (both stores) and `plaintext` (vpn) from the ledger, so it moves the parity
# baselines and belongs in its own cut.
_NOT_STORE_KEYS = {"index", "error", "path"}


def _scan_other_stores(root):
    """vpn_config + widgets_config — additional config stores (audit finding)."""
    items = []
    for fname, store, section, risk in [
            ("vpn_config.py", "vpn", "Network → VPN", "medium"),
            ("widgets_config.py", "widgets", "Dashboard → Widgets", "low")]:
        p = Path(root) / "bulk_downloader" / fname
        if not p.is_file():
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        keys = sorted(set(re.findall(r'["\']([a-z_]{3,})["\']\s*:', src))
                      - _NOT_STORE_KEYS)[:40]
        for k in keys:
            items.append({
                "key": f"{store}.{k}", "source_file": f"bulk_downloader/{fname}",
                "all_sources": [f"bulk_downloader/{fname}"],
                "category": f"{store} config", "default": None,
                "gui_exposure": "none", "read_write": "read+write (config store)",
                "validation_rules": "per-store schema (candidate key)",
                "description": f"{store} configuration key (candidate; heuristic scan)",
                "risk": risk, "recommended_gui_section": section,
                "related": [], "kind": f"{store}_config"})
    return items


_TEMPLATE_FIELDS = [
    ("status", "reviewed/drafts/review_candidates lifecycle", "enum"),
    ("download_trigger", "primary download control selector", "selector"),
    ("row_selectors", "per-row selectors", "selector list"),
    ("api_base", "site-provided API base", "string"),
    ("resolutions", "quality ladder", "int list"),
    ("network_patterns", "observed network patterns", "pattern list"),
    ("selector_groups", "download/login/player/quality groups", "group map"),
    ("blocked_terms", "blocked-term guard (must be empty to promote)", "term list"),
]


def _template_settings():
    items = []
    for key, desc, vtype in _TEMPLATE_FIELDS:
        risk = "high" if key in ("download_trigger", "api_base", "blocked_terms") else "medium"
        items.append({
            "key": f"template.{key}", "source_file": "tools/template_inventory.py (schema)",
            "all_sources": ["tools/template_inventory.py", "docs/SCHEMAS.md"],
            "category": "template", "default": None,
            "gui_exposure": "partial",  # template-manager shows read-only
            "read_write": "read (scanned); write via promotion flow (operator-gated)",
            "validation_rules": vtype,
            "description": desc, "risk": risk,
            "recommended_gui_section": "Templates → editor (future, gated)",
            "related": [], "kind": "template_setting",
        })
    return items


def _global_config_note(root=None):
    """v3.66.710: ENUMERATE the global_config store per key.

    This used to return ONE row -- `"(global_config store)": "full"` -- with the
    excuse that "keys are dynamic/file-backed, not statically enumerable in source".
    That excuse was false: GLOBAL_CONFIG_SCHEMA declares every key statically. The
    single row hid 90 keys behind one assertion, and because the parity ratchet
    counts ROWS, it read open=0 while automation.master_off_switch -- the emergency
    stop -- was unwritable (fixed at 709). A gate cannot see what its denominator
    does not contain.

    Exposure is DERIVED from the frontend (does any control reference the key?),
    never asserted. The 21 automation keys declared at 709 therefore surface as
    gui_exposure="none" until Cut 3 lands their controls -- which is the point: the
    gap becomes a number instead of a blind spot.
    """
    try:
        from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA as _GCS
    except Exception:  # noqa: BLE001
        return []

    # POPULATION: PRODUCT-ONLY. `exposed` is DERIVED from "does a control
    # reference this key", so anything inside this scan can vouch for a GUI
    # control existing. A Vitest spec naming a key is not a control -- that is
    # the laundering v3.66.1217 closed in tools/gui_parity_inventory.py, and
    # this is the same defect in a second inventory. See tools/spa_population.py.
    fe = ""
    if root:
        fe = spa_population.product_text(Path(root) / "frontend" / "src")

    items = []
    # v3.66.720: global_config keys that are DEPLOY/BOOT-time, not runtime-tunable. A GUI
    # write is meaningless because the value is read ONCE at startup. ffmpeg_path resolves
    # the ffmpeg binary in ffmpeg_bin.py at boot -- same class as the deploy env knobs. It
    # is display-only (show the effective value + how to override), NOT parity debt.
    _GC_BOOT_DISPLAY_ONLY = {"ffmpeg_path"}
    for key, spec in sorted(_GCS.items()):
        if key in _GC_BOOT_DISPLAY_ONLY:
            exposed = "display-only"
        else:
            exposed = "full" if (fe and ('"%s"' % key in fe or "'%s'" % key in fe)) else "none"
        safety = bool(spec.get("safety"))
        items.append({
            "key": key,
            "source_file": "bulk_downloader/global_config.py",
            "all_sources": ["bulk_downloader/global_config.py"],
            "category": "global",
            "default": spec.get("safe_default"),
            "gui_exposure": exposed,
            "read_write": "read+write (POST /api/global_config)",
            "validation_rules": "type=%s%s" % (
                getattr(spec.get("type"), "__name__", spec.get("type")),
                "; safety-bearing (fail-closed)" if safety else ""),
            "description": "global_config key (declared in GLOBAL_CONFIG_SCHEMA)",
            "risk": "high" if safety else "medium",
            "recommended_gui_section": (
                "Settings -> Automation" if key.startswith("automation.")
                else "Settings -> Global"),
            "related": [], "kind": "global_config",
            "runtime_tunable": key not in _GC_BOOT_DISPLAY_ONLY,
            "danger": safety,
            # v3.66.713: app.py's _origins_env_locked does
            # os.environ.get("<prefix>_" + field.upper()) -- so EVERY global_config key
            # has an implicit env override that pins it. Those locks are ATTRIBUTES of a
            # setting already counted, not settings of their own: recording all 90 as
            # rows would double-count every key in the store. A denominator padded with
            # duplicates is no more honest than one that truncates.
            "env_lock": "<prefix>_" + key.upper().replace(".", "_"),
        })
    return items


def _global_config_note_legacy():
    return [{
        "key": "(global_config store)", "source_file": "bulk_downloader/global_config.py",
        "all_sources": ["bulk_downloader/global_config.py"],
        "category": "global", "default": "(file-backed JSON)",
        "gui_exposure": "partial",
        "read_write": "read+write (get/set_config API)",
        "validation_rules": "free-form JSON object; keys assigned at call sites",
        "description": "Global key/value config store (get/set/get_config). Keys are "
                       "dynamic/file-backed, not statically enumerable in source - a "
                       "precise key list requires reading the live config file on stash.",
        "risk": "medium", "recommended_gui_section": "Settings → Global (future)",
        "related": [], "kind": "global_config",
    }]



# ---------------------------------------------------------------------------
# v3.66.713 (A-GUI Cut 4) -- the layers this inventory never knew existed.
# ---------------------------------------------------------------------------

# Deploy/install knobs that live in SHELL scripts. The env-tranche gate scans
# .py only, so the gate that exists to make prefixed literals impossible to miss cannot
# see these at all. (Naming them literally here is impossible: _scan_env scans THIS
# file too, and any BD_-prefixed literal in a comment becomes a phantom env var --
# FG-ENV-TRANCHE-BD-LITERAL, which this cut tripped while writing it.)
# They are DEPLOY-time: a GUI write is meaningless, so they are
# display-only -- tracked, not manufactured debt.
_SHELL_ENV_GLOBS = ("*.sh", "*.bat")

# Runtime env read WITHOUT the project prefix, so the prefix-matching scan misses them.
# CLOAKBROWSER_BINARY_PATH decides which browser BINARY is executed.
# (The irony worth naming: the NETNS_* vars are deliberately NOT project-prefixed
# precisely to dodge FG-ENV-TRANCHE-BD-LITERAL -- the gate's own pressure creates
# config surface the gate cannot see.)
_NON_BD_RUNTIME_ENV = {
    "CLOAKBROWSER_BINARY_PATH": "which cloakbrowser binary to execute",
    "PLAYWRIGHT_BROWSERS_PATH": "where playwright resolves browser builds",
    "DISPLAY": "X display for headed capture",
    "APPDATA": "Windows config root",
    "DECOMP_KIT": "decomp toolkit path (dev)",
}

# Config that lives in SQLITE, not in a file or an env var. Operator-facing and
# GUI-reachable (a mutating route + a control exists), just never inventoried.
_DB_CONFIG_TABLES = {
    "capture_schedules": ("/api/schedules", "capture schedule rows"),
    "scheduled_exports": ("/api/scheduled_exports", "scheduled export rows"),
    "api_auth_tokens": ("/api/auth", "API auth tokens"),
    "share_tokens": ("/api/shares", "share tokens"),
}

_PLUGIN_MANIFEST_FIELDS = (
    "name", "version", "api_version", "author", "description",
    "capabilities", "hooks", "extractor",
)


def _scan_shell_env(root):
    """BD_* literals in install/deploy shell scripts -- invisible to the .py gate."""
    import glob as _glob
    hits = {}
    for pat in _SHELL_ENV_GLOBS:
        for q in _glob.glob(os.path.join(root, pat)) + _glob.glob(
                os.path.join(root, "scripts", pat)):
            try:
                src = open(q, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for k in re.findall(r"\b(BD_[A-Z0-9_]+)\b", src):
                hits.setdefault(k, os.path.relpath(q, root))
    items = []
    for k in sorted(hits):
        items.append({
            "key": k, "source_file": hits[k], "all_sources": [hits[k]],
            "category": "deploy", "default": None,
            # deploy-time: the process is already running by the time a GUI could
            # write it, so a control is meaningless. display-only, NOT open debt.
            "gui_exposure": "display-only",
            "read_write": "read (install/deploy script)",
            "validation_rules": "shell scalar",
            "description": "install/deploy knob (shell script; the .py env gate cannot see it)",
            "risk": "medium", "recommended_gui_section": "Environment (read-only)",
            "related": [], "kind": "env_var", "runtime_tunable": False,
            "danger": False, "danger_note": "",
        })
    return items


def _scan_non_bd_env(root):
    """Runtime env read without the BD_ prefix -- missed by a prefix-matching scan."""
    items = []
    for k, desc in sorted(_NON_BD_RUNTIME_ENV.items()):
        items.append({
            "key": k, "source_file": "bulk_downloader/", "all_sources": ["bulk_downloader/"],
            "category": "bootstrap", "default": None,
            "gui_exposure": "display-only",
            "read_write": "read (process env)",
            "validation_rules": "path/string",
            "description": desc + " (read WITHOUT the BD_ prefix, so the env gate misses it)",
            "risk": "high" if "BINARY" in k else "medium",
            "recommended_gui_section": "Environment (read-only)",
            "related": [], "kind": "env_var", "runtime_tunable": False,
            "danger": "BINARY" in k,
            "danger_note": ("selects the executable that runs during capture"
                            if "BINARY" in k else ""),
        })
    return items


def _scan_db_config(root):
    """Config persisted in SQLite. A GUI control exists for each; nothing counted it."""
    items = []
    for tbl, (route, desc) in sorted(_DB_CONFIG_TABLES.items()):
        items.append({
            "key": tbl, "source_file": "bulk_downloader/db/", "all_sources": ["bulk_downloader/db/"],
            "category": "db config", "default": None,
            "gui_exposure": "full",
            "read_write": "read+write (%s)" % route,
            "validation_rules": "row-level (see the endpoint)",
            "description": desc + " -- config that lives in SQLite, not a file or an env var",
            "risk": "medium", "recommended_gui_section": route,
            "related": [], "kind": "db_config", "runtime_tunable": True,
            "danger": "token" in tbl, "danger_note": "bearer credentials" if "token" in tbl else "",
        })
    return items


def _scan_plugin_manifest(root):
    """Plugin v3 manifest fields -- 8 declared, 0 previously scored."""
    items = []
    for f in _PLUGIN_MANIFEST_FIELDS:
        items.append({
            "key": "plugin.%s" % f, "source_file": "bulk_downloader/plugin_loader.py",
            "all_sources": ["bulk_downloader/plugin_loader.py"],
            "category": "plugin manifest", "default": None,
            "gui_exposure": "full",
            "read_write": "read+write (/api/plugins/config)",
            "validation_rules": "manifest schema",
            "description": "plugin manifest field",
            "risk": "high" if f in ("capabilities", "hooks") else "low",
            "recommended_gui_section": "Plugins",
            "related": [], "kind": "plugin_manifest", "runtime_tunable": True,
            "danger": f in ("capabilities", "hooks"),
            "danger_note": "grants plugin capability" if f in ("capabilities", "hooks") else "",
        })
    return items


def _scan_stores(root):
    """The JSON files BD actually persists config/state into. Recorded as STORES, not
    as parity rows -- app_config.json BACKS global_config, so counting it as a setting
    would double-count every key in it. _scan_other_stores was a hand-list of two."""
    pkg = os.path.join(root, "bulk_downloader")
    found = {}
    wr = re.compile(r"json\.dump|write_text|save_json|atomic_write|\.write\(")
    for dp, _dn, fns in os.walk(pkg):
        for fn in sorted(fns):
            if not fn.endswith(".py"):
                continue
            q = os.path.join(dp, fn)
            try:
                src = open(q, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(r"[\"']([a-z0-9_]+\.json)[\"']", src):
                seg = src[max(0, m.start() - 300):m.start() + 300]
                if wr.search(seg):
                    found.setdefault(m.group(1), os.path.relpath(q, root))
    return [{"file": f, "written_by": found[f]} for f in sorted(found)]


def build(root="."):
    root = os.path.abspath(root)
    items = (_scan_env(root) + _scan_site_keys(root) + _scan_other_stores(root)
             + _template_settings() + _global_config_note(root)
             # v3.66.713 -- the layers nothing scanned before
             + _scan_shell_env(root) + _scan_non_bd_env(root)
             + _scan_db_config(root) + _scan_plugin_manifest(root))
    # De-dup on (KIND, key), never on key alone: a per-site key and a global_config
    # key can share a NAME while being different settings in different scopes
    # (deduping on name alone silently dropped a global_config key -- 90 -> 89).
    seen, deduped = set(), []
    for it in items:
        sig = (it.get("kind"), it["key"])
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(it)
    items = _apply_manifest(deduped, _load_manifest(root))
    return {"root": root, "counts": _counts(items), "items": items,
            "stores": _scan_stores(root)}


# ── GUI-parity ratchet (Phase 4+) ────────────────────────────────────────────
# Deploy/path/bootstrap env vars are set BEFORE the process starts (service unit /
# sandbox), so a GUI *write* is meaningless — parity for these = a read-only
# "effective value + how to override" panel (gui_exposure == "display-only").
# Everything else is runtime-tunable and must reach gui_exposure == "full".
_DEPLOY_ONLY = {
    "BD_HOME", "BD_INSTALL_DIR", "BD_REPO", "BD_ROOT", "BD_KB_DIR", "BD_WORK",
    "BD_LOG_FILE", "BD_SITES_CONFIG_PATH", "BD_VPN_CONFIG_PATH",
    "BD_WIDGETS_CONFIG_PATH", "BD_CAPTURES_ROOT", "BD_DEV_MODE_DISABLE",
    "BD_DISABLE_KEEPALIVE", "DISPLAY", "PLAYWRIGHT_BROWSERS_PATH", "APPDATA",
    "FLASK_DEBUG", "BULK_DOWNLOADER_DEBUG",
    # MOD-1 C-5/C-7: optional pin for the vnc-takeover browser BINARY, read at
    # import in takeover_vnc.py. Set before the process launches the browser --
    # a GUI write cannot take effect (matches PLAYWRIGHT_BROWSERS_PATH /
    # CLOAKBROWSER_BINARY_PATH) -> display-only, not undelivered parity.
    "BD_VNC_CHROME",
    # v3.66.315 (Phase 4.2): cockpit/fleet/framework BIND ports are set before the
    # process starts -> a GUI write is meaningless; target display-only.
    "BD_COCKPIT_PORT", "BD_FLEET_PORT", "BD_FRAMEWORK_PORT",
    # v3.66.319 (Phase 4.3a): deploy seed only — read by edge_deploy.py k8s
    # manifest generation, NOT by the running service. The LIVE download-dir knob
    # is the `download_dir` config key (already gui_exposure=full, site_key), so a
    # second GUI control over BD_DOWNLOAD_DIR would be redundant -> display-only.
    "BD_DOWNLOAD_DIR",
    # v3.66.319 (Phase 4.3d): final env_var closures — all set OUTSIDE the running
    # service so a GUI write is meaningless -> display-only (effective-value panel).
    # BD_HOST/BD_PORT: Flask bind host/port, bound before any handler runs (matches
    # the @315 BD_*_PORT bind-port precedent). BD_RELEASE_ARCHIVE: read only by the
    # rollback.py CLI ops tool, not the service. BD_URL: the URL external clients
    # use to reach the server; no service reader (operator: display-only for now).
    "BD_HOST", "BD_PORT", "BD_RELEASE_ARCHIVE", "BD_URL",
    # v3.66.505 (Bucket 2 .env editor): the bootstrap pointer that LOCATES the
    # `.env` (bulk_downloader/_envfile.resolve_envfile_path). It is read at import
    # to find the file, so it cannot itself live in the `.env` (chicken-and-egg)
    # and a GUI write is meaningless -> host/bootstrap-managed, display-only. NOT
    # in the .env editor's editable set for the same reason.
    "BD_ENVFILE",
}
# v3.66.319 (Phase 4.3a): legacy back-compat ALIASES of an env var whose canonical
# control is ALREADY gui_exposure=full. resolve_backend()'s _ENV_KEYS triple is
# (BD_BROWSER_BACKEND, BD_USE_CLOAK, BD_SESSION_KEEPER_USE_CLOAKBROWSER); the first
# two are already full, so the third is controlled by-proxy.
# v3.66.506 (Bucket 3a): BD_SESSION_KEEPER_USE_CLOAKBROWSER was promoted to a
# first-class full key (declared in GLOBAL_CONFIG_SCHEMA + flipped full in the
# manifest), so it is no longer an alias-of-full -> dropped from this set, leaving
# it empty. (It remains a redundant alias of browser_backend at the resolution
# level; that redundancy is now surfaced as GUI label copy, not a parity exclusion.)
_ALIAS_OF_FULL = set()
# Env vars read into MODULE-LEVEL constants at import (e.g. hls_downloader.py /
# live_recorder.py: `INPUT_TIMEOUT_US = int(os.environ.get(...))`). They are bound
# once at process start, so a GUI *write* could not take effect without a restart.
# Treated like deploy-only for parity: target display-only (effective-value panel),
# NOT a live write control — promoting them "full" would wire a control to a frozen
# value (v3.66.309). If any is ever refactored to a call-time getter, drop it here
# and promote it full.
_IMPORT_TIME = {
    # v3.66.503 (Bucket 1): the HLS / Live-recorder / Captcha-relay tunables were
    # promoted out of this set — their readers now use call-time getters
    # (runtime_flags.num, store > env seed > default) so they reach gui_exposure
    # full. Only the genuinely import/boot-bound VPN vars remain display-only:
    # BD_DISABLE_VPN_RUNTIME gates a one-time init() (a live write can't retro-
    # actively start/stop the runtime), and BD_VPN_LEAK_INTERVAL_S has no live
    # reader (the substantive knob is the store key vpn.leak_test_interval_s,
    # already a full Vpn.tsx control) -> a separate control would wire to nothing.
    "BD_DISABLE_VPN_RUNTIME", "BD_VPN_LEAK_INTERVAL_S",
    # v3.66.818: the capture-vault pair. secrets_store.py binds
    # `SECRETS_FILE, SECRETS_META_FILE = _resolve_vault_paths()` at MODULE level,
    # so both are read exactly once, before any request handler exists. A live
    # GUI write could not move an already-opened vault, and a control that
    # appeared to would be worse than none. capture.sh sets them via a systemd
    # drop-in and the service restarts to pick them up -- which is the whole
    # mechanism, not an incidental detail.
    "BD_SECRETS_FILE", "BD_CAPTURE_VAULT",
}
# v3.66.312 (Phase 4.3): widgets STORE-METADATA keys — written by the widgets_config
# store on save (timestamp / schema version), never a user-set control. The dashboard
# governs the substantive widget config (global/per_site/size + per-site entries) via
# /api/widgets/all + /api/widgets/<scope>; these two are byproducts -> display-only
# (effective-value), not a live write control.
_WIDGETS_METADATA = {"widgets._saved_at"}
# v3.66.319 (Phase 4.5): VPN store/system-managed keys — written by the config
# store (timestamp / schema version) or assigned by the system on tunnel create
# (tunnel_id), never an operator-set knob. The substantive VPN config (tunnels +
# global settings + secrets) is GUI-managed via Vpn.tsx; these are byproducts ->
# display-only (effective-value), same pattern as _WIDGETS_METADATA.
# v3.66.507 (Bucket 3b): vpn.schema_version + vpn.tunnel_id promoted out of the
# metadata set -> full, raw-editable via the /api/settings/store-raw editor
# (tunnel_id changes are R1-guarded; rename via the rekey action). vpn._saved_at
# stays here (display-only) because every save() auto-stamps it -> a manual value
# is transient. Mirrors _WIDGETS_METADATA = {"widgets._saved_at"}.
_VPN_METADATA = {"vpn._saved_at"}
_MANIFEST_REL = "reports/config_gui_manifest.json"
_BASELINE_REL = "reports/config_parity_baseline.json"

# v3.66.468: GUI-parity ratchet master switch. PARKED (False) by operator
# directive until the project is feature-complete -- while False, _check() is
# inert (always passes) and the matching suite gate (test_config_parity_ratchet)
# stands down, so config-key/env changes stop tripping the ratchet mid-build.
# The inventory, manifest, and classification logic all keep working (accurate
# data for the read panels + a clean reactivation). To REACTIVATE: set True and
# re-pin the baseline via --update-baseline.
_RATCHET_ACTIVE = False

# Settings whose modification can have IRRECOVERABLE effects — the GUI control
# must surface danger_note as a disclaimer. Nothing is off-limits to GUI exposure
# (guard-backed capture core, auth tokens, VPN/leak protection, path roots are all
# configurable); the disclaimer is the safeguard, not exclusion.
_GUARD_FILES = {
    "bulk_downloader/extraction_core.py", "bulk_downloader/session_capture.py",
    "tools/capture_session.py", "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py", "bulk_downloader/capture_bodies.py",
    "tools/build_release.py",
}
_AUTH_ENV = {"BD_AUTH_TOKEN", "BD_TOKEN"}
_VPN_ENV = {"BD_DISABLE_VPN_RUNTIME", "BD_VPN_LEAK_INTERVAL_S"}
_PATH_EXTRA = {"BD_CAPTURES_ROOT", "BD_DOWNLOAD_DIR",
               # v3.66.317: promoted full path roots (value-honored, no jail).
               "BD_COCKPIT_TASKS", "BD_FRAMEWORK_REPORTS"}
# v3.66.317: controls that ENABLE a privileged/code-execution surface — the in-GUI
# dev/test-runner (spawns Python subprocesses) and the cockpit PTY shell (runs
# arbitrary commands). Promoted to full live controls per operator directive; the
# GUI control must surface the surface-expansion disclaimer.
_PRIV_ENABLE = {"BD_DEV_MODE", "BD_COCKPIT_SHELL"}
# v3.66.319 (Phase 4.3b): the autonomy final-apply switch. Distinct from
# _PRIV_ENABLE (which enables a surface the operator then drives) — arming this
# lets the system take autonomous Class-B state-changing actions on its own. The
# GUI control must surface the autonomous-action disclaimer.
_AUTONOMY_ENABLE = {"BD_AUTONOMY_ENABLED"}
# Redaction-weakening controls: enabling them retains secrets/signing in the
# capture. A capture made under a weakened profile cannot be un-circulated —
# IRRECOVERABLE — so the GUI control must surface a disclaimer (v3.66.308).
_REDACTION_WEAKEN = {"BD_CAPTURE_RAW", "BD_REDACT_DOM_URLS", "BD_REDACT_NETWORK_URLS"}


def _danger_for(it):
    """Irrecoverable-effects classification + disclaimer text -> (bool, note)."""
    key = it.get("key", "")
    src = it.get("source_file", "")
    kind = it.get("kind")
    if src in _GUARD_FILES:
        return True, ("Backs the SHA-pinned capture-integrity core. A wrong value can "
                      "silently weaken or corrupt capture/redaction, and affected "
                      "captures cannot be re-derived. IRRECOVERABLE — change only with a "
                      "known-good value, then re-run the release-guard verification.")
    if key in _AUTH_ENV or (kind == "site_key" and any(
            t in key for t in ("token", "password", "api_key", "secret"))):
        return True, ("Auth credential. Changing or clearing it can lock you or your "
                      "integrations out of the running instance and may not be "
                      "recoverable from the GUI if you lose access. Rotate deliberately.")
    if key in _VPN_ENV or kind == "vpn_config":
        return True, ("VPN / leak protection. Disabling or misconfiguring it can expose "
                      "your real IP during capture; a leaked request cannot be un-sent. "
                      "IRRECOVERABLE.")
    if key in _REDACTION_WEAKEN:
        return True, ("Weakens capture redaction — secrets / signing / signed URLs are "
                      "RETAINED in the capture. A capture made with this enabled cannot be "
                      "un-circulated once shared. IRRECOVERABLE — keep raw captures local.")
    if key in _PRIV_ENABLE:
        return True, ("Enables a privileged surface — the in-GUI dev/test runner spawns "
                      "Python subprocesses; the cockpit shell runs arbitrary commands. "
                      "Enabling it widens the code-execution surface of the running "
                      "instance; do so only on a trusted, single-operator network.")
    if key in _AUTONOMY_ENABLE:
        return True, ("Arms the autonomy final-apply switch — the system can then take "
                      "autonomous Class-B state-changing actions on its own (still gated "
                      "by the kill switch + Class-B policy level). Applied autonomous "
                      "changes cannot be un-made automatically. Arm only deliberately, on "
                      "a trusted single-operator network, with the kill switch understood.")
    if (kind == "env_var" and key in _DEPLOY_ONLY) or key in _PATH_EXTRA:
        return True, ("Path / storage root. Repointing it at runtime can orphan or "
                      "overwrite existing data and the process may not relocate cleanly. "
                      "Prefer changing it at deploy time, then restart.")
    return False, ""


def _load_manifest(root):
    """Keys with a CONFIRMED GUI control, written as each Phase-4 cut lands one:
    {key: "full"|"display-only"}. Absent => parity not yet delivered."""
    p = os.path.join(root, _MANIFEST_REL)
    try:
        with open(p, encoding="utf-8") as fh:
            m = json.load(fh)
        return m.get("exposed", m) if isinstance(m, dict) else {}
    except Exception:
        return {}


# v3.66.710: per-site keys that will NEVER get a GUI control, by design -- so they
# are not "parity not yet delivered" (open debt), they are a decided exclusion.
# `accounts` is a NESTED per-account credential list; a field editor would put
# credentials on the wire. Withheld explicitly (app_settings_center.
# _STRUCTURED_CREDENTIAL_FIELDS) rather than, as before, by the accident of a
# truncating regex that also hid 178 unrelated keys.
_NEVER_EXPOSE = {"accounts"}


def _is_runtime_tunable(it):
    """CAN take effect at runtime (so a GUI control is meaningful) — everything
    except the deploy/path/bootstrap env vars, import-time-bound constants, and
    store-metadata keys (widgets timestamp/schema version)."""
    if it.get("kind") == "env_var" and it["key"] in (_DEPLOY_ONLY | _IMPORT_TIME | _ALIAS_OF_FULL):
        return False
    # v3.66.713: the scanners added at Cut 4 carry their own verdict. Shell/deploy
    # knobs (<prefix>_DEPLOY_DIR, <prefix>_RESTART_CMD...) and bootstrap env (PLAYWRIGHT_BROWSERS_PATH,
    # DISPLAY...) are set BEFORE the process starts -- a GUI write cannot take effect, so
    # they are display-only, not "parity not yet delivered". Honour the flag the scanner
    # set rather than recomputing it from a hardcoded list this function has never heard
    # of; otherwise scanning a new surface MANUFACTURES debt that no control could ever
    # close.
    if it.get("runtime_tunable") is False:
        return False
    if it["key"] in (_WIDGETS_METADATA | _VPN_METADATA):
        return False
    if it.get("kind") == "site_key" and it["key"] in _NEVER_EXPOSE:
        return False
    return True


def _apply_manifest(items, manifest):
    """Stamp runtime_tunable + parity_target per setting and override gui_exposure
    from the manifest. Idempotent."""
    for it in items:
        it["runtime_tunable"] = _is_runtime_tunable(it)
        it["parity_target"] = "full" if it["runtime_tunable"] else "display-only"
        danger, note = _danger_for(it)
        it["danger"] = danger
        it["danger_note"] = note
        lvl = manifest.get(it["key"])
        if lvl in ("full", "display-only"):
            it["gui_exposure"] = lvl
    return items


def _open_settings(items):
    """Runtime-tunable settings not yet at gui_exposure == 'full'."""
    return [it["key"] for it in items
            if it.get("runtime_tunable") and it.get("gui_exposure") != "full"]


def _display_open(items):
    """Deploy/path-only settings not yet surfaced as display-only.

    v3.66.711: _NEVER_EXPOSE keys are excluded here too. `accounts` is not
    "deploy-only work still to do" -- it is a DECIDED exclusion (nested per-account
    credentials; a field editor would put them on the wire). Marking it not
    runtime-tunable took it out of the open count but dropped it straight into this
    one, which reported it as pending display-only work. It is neither. A decided
    exclusion belongs in no debt bucket.
    """
    return [it["key"] for it in items
            if not it.get("runtime_tunable")
            and it.get("gui_exposure") != "display-only"
            and it["key"] not in _NEVER_EXPOSE]


def _write_baseline(root, d):
    base = {"open_count": d["counts"]["open_runtime_tunable"],
            "open": sorted(_open_settings(d["items"])),
            "display_open": sorted(_display_open(d["items"])),
            "note": "GUI-parity ratchet baseline: open = runtime-tunable settings not "
                    "yet gui_exposure=full. Shrinks as Phase-4 cuts land controls (add "
                    "the key to reports/config_gui_manifest.json, then re-pin). "
                    "display_open = deploy/path-only settings not yet display-only."}
    p = os.path.join(root, _BASELINE_REL)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(base, fh, indent=2)
    print(f"wrote {p}: open_count={base['open_count']}")
    return 0


def _check(root, d):
    # v3.66.468: the GUI-parity ratchet is PARKED by operator directive until the
    # project is feature-complete. While parked, --check always passes (it neither
    # blocks cuts nor enforces the open-count baseline). Reactivate by flipping
    # _RATCHET_ACTIVE back to True and re-pinning the baseline (--update-baseline);
    # the manifest data stays accurate in the meantime so reactivation is a single
    # flip, not a re-derivation.
    if not _RATCHET_ACTIVE:
        cur = d["counts"].get("open_runtime_tunable", 0)
        print(f"GUI-PARITY RATCHET: PARKED (inert by operator directive; "
              f"open={cur} not enforced). Flip _RATCHET_ACTIVE=True to reactivate.")
        return 0
    p = os.path.join(root, _BASELINE_REL)
    try:
        with open(p, encoding="utf-8") as fh:
            base = json.load(fh)
    except Exception as e:
        sys.stderr.write(f"GUI-PARITY: no baseline at {p} ({e}) — run --update-baseline once\n")
        return 1
    cur = d["counts"]["open_runtime_tunable"]
    baseline = int(base.get("open_count", 0))
    if cur > baseline:
        regressed = sorted(set(_open_settings(d["items"])) - set(base.get("open", [])))
        sys.stderr.write(
            f"GUI-PARITY RATCHET: FAIL — open={cur} EXCEEDS baseline={baseline}. "
            f"New un-exposed runtime-tunable settings: {regressed}\n")
        return 1
    rel = "<" if cur < baseline else "=="
    print(f"GUI-PARITY RATCHET: OK (open={cur} {rel} baseline={baseline})"
          + ("; re-pin with --update-baseline to lock the gain" if cur < baseline else ""))
    return 0


def _counts(items):
    by_kind, by_expo, by_risk, by_cat = {}, {}, {}, {}
    for it in items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
        by_expo[it["gui_exposure"]] = by_expo.get(it["gui_exposure"], 0) + 1
        by_risk[it["risk"]] = by_risk.get(it["risk"], 0) + 1
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    return {"total": len(items), "by_kind": by_kind, "by_gui_exposure": by_expo,
            "by_risk": by_risk, "by_category": by_cat,
            "open_runtime_tunable": len(_open_settings(items)),
            "display_open": len(_display_open(items)),
            "danger_count": sum(1 for it in items if it.get("danger"))}


def _md(d):
    c = d["counts"]
    L = ["# Config Surface Inventory — Phase 2 (Phase 2.5 corrected)", "",
         f"- root: `{d['root']}`",
         f"- total settings: **{c['total']}** — by kind: {c['by_kind']}",
         f"- GUI exposure: {c['by_gui_exposure']}",
         f"- risk mix: {c['by_risk']}", "",
         "Discovery/inventory only — no settings center, no config changes. Per-site keys "
         "now sourced from authoritative `CFG_FIELDS` (app.py) enriched by site_editor "
         "metadata; vpn/widgets stores added. The global_config store is file-backed with "
         "dynamic keys — a precise live-key list requires reading the config on stash "
         "(DEFERRED).", ""]
    titles = {"env_var": "Environment variables (BD_*)", "site_key": "Per-site config keys",
              "vpn_config": "VPN config keys", "widgets_config": "Widgets config keys",
              "template_setting": "Template settings", "global_config": "Global config store"}
    for kind in ("env_var", "site_key", "vpn_config", "widgets_config",
                 "template_setting", "global_config"):
        rows = [it for it in d["items"] if it["kind"] == kind]
        if not rows:
            continue
        L += [f"## {titles[kind]} ({len(rows)})", "",
              "| key | source | category | exposure | r/w | validation | risk | related | section |",
              "|---|---|---|---|---|---|---|---|---|"]
        for it in sorted(rows, key=lambda x: (x["category"], x["key"])):
            rel = ",".join(it.get("related", []))[:28] or "—"
            L.append("| {k} | `{src}` | {cat} | {ex} | {rw} | {val} | {risk} | {rel} | {sec} |".format(
                k=it["key"][:40], src=it["source_file"][:30], cat=it["category"],
                ex=it["gui_exposure"], rw=it["read_write"][:22],
                val=str(it["validation_rules"])[:26], risk=it["risk"], rel=rel,
                sec=it["recommended_gui_section"]))
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="GUI-parity ratchet: exit 1 if open count exceeds the pinned baseline")
    ap.add_argument("--update-baseline", action="store_true",
                    help="re-pin the parity baseline to the current open set")
    a = ap.parse_args(argv)
    d = build(a.root)
    if a.check:
        return _check(a.root, d)
    if a.update_baseline:
        return _write_baseline(a.root, d)
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    os.makedirs(a.outdir, exist_ok=True)
    jp = os.path.join(a.outdir, "config_surface_inventory.json")
    if _RC:
        _RC.write_json(jp, d)
        _RC.write_report(a.outdir, "config_surface_inventory.md", _md(d))
    else:
        open(jp, "w").write(json.dumps(d, indent=2, default=str))
        open(os.path.join(a.outdir, "config_surface_inventory.md"), "w").write(_md(d))
    print(f"wrote {jp} and .md: {d['counts']['total']} settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
