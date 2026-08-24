#!/usr/bin/env python3
"""
gui_parity_inventory.py — GUI Parity Program, Phase 1 (inventory only).

DISCOVERY/REPORTING ONLY. Reads existing artifacts (tools/*.py docstrings, the live
Flask url_map when importable else ENDPOINT_CATALOG.md, reference docs) and emits a
classified inventory of operator-facing functionality: CLI tools, cockpit pages,
cockpit APIs, and operator workflows. It does NOT add routes, GUI controls, or any
runtime behavior.

For every item: name, source_file, command_or_endpoint, purpose, category,
gui_support (full|partial|none), dependencies, difficulty (low|med|high),
runtime_risk (none|low|medium|high), recommended_gui_location,
recommendation (cli-only|gui-visible|gui-actionable).

Outputs: reports/gui_parity_inventory.json and reports/gui_parity_inventory.md.
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
sys.path.insert(0, str(_REPO))
try:
    import report_core as _RC  # type: ignore
except Exception:  # noqa: BLE001
    _RC = None

# ── classification heuristics ──────────────────────────────────────
# category by filename keyword (first match wins)
_CATEGORY = [
    ("capture", "capture"), ("template", "template"), ("kb", "knowledge base"),
    ("queue", "diagnostics"), ("release", "release"), ("changelog", "release"),
    ("verify", "release"), ("version", "release"), ("openapi", "api/schema"),
    ("endpoint", "api/schema"), ("schema", "api/schema"), ("doc", "documentation"),
    ("reference", "documentation"), ("validate", "configuration"),
    ("config", "configuration"), ("environment", "diagnostics"),
    ("install", "diagnostics"), ("dependency", "diagnostics"),
    ("technical_debt", "diagnostics"), ("compat", "diagnostics"),
    ("offline", "capture"), ("test", "testing"), ("function", "diagnostics"),
    ("module", "diagnostics"), ("search", "api/schema"), ("report", "reporting"),
    ("analytics", "reporting"), ("inventory", "reporting"), ("statistics", "reporting"),
    ("health", "reporting"), ("audit", "reporting"), ("drift", "template"),
    ("score", "template"), ("warning", "template"), ("core", "shared core"),
]
# high-risk-if-actioned keywords (acquisition/auth/promotion WRITE actions only).
# NB: bare "capture" was too broad — it wrongly flagged read-only capture *reporting*
# tools (capture_analytics/inventory/statistics/quality_report). Only the tools that
# actually perform captures or mutate templates are high-risk.
_HIGH_RISK = ("promote", "swap", "capture_session", "capture_batch", "login",
              "session", "normalize", "build_template", "merge")
_MED_RISK = ("validate", "drift", "install")
# tools that already back an existing cockpit surface (gui_support hint)
_BACKED = {
    "template_analytics": ("partial", "/cockpit/template-manager"),
    "template_inventory": ("partial", "/cockpit/template-manager"),
    "template_drift_report": ("partial", "/cockpit/template-manager"),
    "queue_intelligence": ("partial", "/cockpit/monitoring"),
    "capture_analytics": ("partial", "/cockpit/monitoring"),
    "cockpit_console": ("full", "/cockpit"),
    "framework_dashboard": ("full", "/framework"),
    "framework_fleet": ("full", "/fleet"),
}
_LOC_BY_CAT = {
    "capture": "Captures", "template": "Templates", "knowledge base": "Reports → KB",
    "diagnostics": "Monitoring → Diagnostics", "release": "Dev/Release",
    "api/schema": "Dev/Release → API", "documentation": "Reports → Docs",
    "configuration": "Settings (future)", "reporting": "Reports",
    "testing": "Dev/Release → Tests", "shared core": "(internal — no GUI)",
}


def _first_docline(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        doc = ast.get_docstring(tree) or ""
        for line in doc.splitlines():
            s = line.strip()
            if s:
                return s[:160]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _category(name):
    for kw, cat in _CATEGORY:
        if kw in name:
            return cat
    return "other"


def _risk(name, cat):
    n = name.lower()
    if any(k in n for k in _HIGH_RISK):
        return "high"
    if cat in ("shared core", "reporting", "documentation", "api/schema", "testing"):
        return "none"
    if any(k in n for k in _MED_RISK):
        return "low"
    return "low"


def _difficulty(cat, risk):
    if cat == "shared core":
        return "low"
    if risk == "high":
        return "high"
    if cat in ("reporting", "documentation", "diagnostics", "api/schema", "testing"):
        return "low"
    return "medium"


def _recommendation(name, cat, risk):
    if cat == "shared core":
        return "cli-only"  # internal library, not operator-facing
    if risk == "high":
        return "cli-only"  # acquisition/promotion/auth — keep gated until explicit GUI design
    if cat in ("reporting", "documentation", "diagnostics", "api/schema", "testing"):
        return "gui-visible"
    if cat in ("configuration", "release"):
        return "gui-actionable"
    return "gui-visible"


# ── risk-aware GUI classification (Phase 2.5) ──────────────────────
# Sensitive surfaces that must NOT be blanket GUI-actionable (from the Max audit):
# secrets lifecycle, auth/login, manual-login, account rotation, bulk delete,
# circuit control, rebalance, acquisition/promotion.
_SENSITIVE = ("secret", "token", "promote", "swap", "allowlist", "login", "auth",
              "rotate", "delete", "purge", "wipe", "reset", "circuit", "rebalance",
              "manual_login", "bulk_delete", "lock", "unlock", "change_password",
              "migrate", "import", "shell", "exec", "pair", "revoke", "credential",
              "password", "key",
              # Phase 3 (GUI-parity final-audit finding #2): config / maintenance /
              # lifecycle state mutations were landing gui-safe. These stems gate them
              # — and because routes are only gated when *mutating* AND sensitive, a
              # read-only GET (e.g. a config view) is unaffected.
              "config", "configure", "reload", "restore", "snapshot", "feature_flag",
              "maintenance", "backup", "supervisor", "plugin", "prune", "scan",
              "tunnel", "captcha", "start", "stop")


def _gui_class(name, mutating=False, kind="cli_tool", risk="low"):
    """Risk-aware class: gui-safe | gui-gated | read-only | cli-only."""
    n = name.lower()
    sensitive = any(s in n for s in _SENSITIVE)
    if kind in ("cli_tool", "blueprint_module", "shell_entrypoint"):
        if kind == "shell_entrypoint":
            return "cli-only"
        if risk == "high":
            return "cli-only"            # acquisition/promotion/auth tooling
        return "read-only"               # reporting/diagnostic CLI → surface read-only
    if kind in ("cockpit_page", "gui_page", "gui_surface"):
        return "gui-gated" if (kind == "gui_surface" and sensitive) else "read-only"
    # routes / apis
    if not mutating:
        return "read-only"
    return "gui-gated" if sensitive else "gui-safe"


def _import_deps(path, known):
    """Direct tool/module imports of `path`, restricted to `known` names."""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except Exception:  # noqa: BLE001
        return []
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name.split(".")[-1])
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[-1])
    out = {m for m in out if m in known}
    out.discard(path.stem)
    return sorted(out)


_ASSETS = ("/static/", "/screenshots/", "/apple-touch-icon", "/icon.svg",
           "/manifest.json", "/sw.js")


def _classify_route(rule, methods):
    """Return (kind, is_api, mutating) or None for assets."""
    if any(a in rule for a in _ASSETS):
        return None
    mutating = bool(set(methods) & {"POST", "PUT", "DELETE", "PATCH"})
    is_api = ("/api/" in rule) or rule == "/metrics" or rule.startswith("/stream")
    under_cockpit = rule.startswith("/cockpit")
    if is_api or mutating:
        kind = "cockpit_api" if (under_cockpit or rule.startswith("/api/")) else "gui_api"
        return (kind, True, mutating)
    # GET, non-api → a page
    kind = "cockpit_page" if under_cockpit else "gui_page"
    return (kind, False, False)


def _tool_item(path, known=()):
    name = path.stem
    cat = _category(name)
    risk = _risk(name, cat)
    gui, loc = _BACKED.get(name, ("none", _LOC_BY_CAT.get(cat, "(tbd)")))
    is_bp = False
    try:
        _t = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        for _n in _t.body:
            if (isinstance(_n, ast.FunctionDef) and _n.name == "register_routes"
                    and _n.args.args and _n.args.args[0].arg == "app"):
                is_bp = True
                break
    except Exception:  # noqa: BLE001
        is_bp = False
    kind = "blueprint_module" if is_bp else "cli_tool"
    return {
        "name": name,
        "source_file": f"tools/{path.name}",
        "command_or_endpoint": f"python3 tools/{path.name}",
        "purpose": _first_docline(path),
        "category": cat,
        "gui_support": gui,
        "dependencies": _import_deps(path, known),
        "difficulty": _difficulty(cat, risk),
        "runtime_risk": risk,
        "recommended_gui_location": loc,
        "recommendation": _recommendation(name, cat, risk),
        "gui_class": _gui_class(name, kind=kind, risk=risk),
        "kind": kind,
    }


def _routes_from_app():
    """Prefer the live url_map (authoritative). Returns list or None."""
    try:
        os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
        import bulk_downloader.app as A  # type: ignore
        out = []
        for r in A.app.url_map.iter_rules():
            methods = sorted(r.methods - {"HEAD", "OPTIONS"})
            out.append({"rule": str(r.rule), "endpoint": r.endpoint,
                        "methods": methods})
        return out
    except Exception:  # noqa: BLE001
        return None


def _route_item(r):
    rule, methods = r["rule"], r["methods"]
    cls = _classify_route(rule, methods)
    if cls is None:
        return None  # asset — skip
    kind, is_api, mutating = cls
    bp = r["endpoint"].split(".")[0] if "." in r["endpoint"] else ""
    gui_class = _gui_class(r["endpoint"], mutating=mutating, kind=kind)
    sensitive = gui_class == "gui-gated"
    risk = "low"
    if mutating:
        risk = "high" if sensitive else "medium"
    cat = {"cockpit_page": "cockpit page", "gui_page": "operator GUI page",
           "cockpit_api": "cockpit api", "gui_api": "operator GUI api"}[kind]
    # legacy recommendation kept for continuity; gui_class is the corrected field
    rec = ("read-only" if not mutating else
           ("gui-gated" if sensitive else "gui-safe"))
    return {
        "name": r["endpoint"],
        "source_file": "bulk_downloader/app.py (+ blueprints)",
        "command_or_endpoint": f"{'|'.join(methods)} {rule}",
        "purpose": "existing operator surface",
        "category": cat,
        "gui_support": "full",
        "dependencies": ([bp] if bp else []),
        "difficulty": "low",
        "runtime_risk": risk,
        "recommended_gui_location": "(already in GUI)",
        "recommendation": rec,
        "gui_class": gui_class,
        "kind": kind,
    }


# Curated operator workflows (qualitative; the cross-tool journeys operators run).
_WORKFLOWS = [
    ("Capture a site session", "capture_session.py / cockpit Captures",
     "noVNC/sentinel capture → finish/discard", "capture", "partial", "high",
     "Captures", "gui-actionable",
     "interactive capture runs in noVNC/sentinel; cockpit can start/finish but not sandbox-testable"),
    ("Build template from capture", "build_template_from_wacz.py + normalize",
     "WACZ → rich draft → review candidate", "template", "partial", "high",
     "Templates → Onboarding", "gui-actionable",
     "cockpit 'build template' exists; scrubs signing material; nothing enabled"),
    ("Review candidate → gold", "promote_template.py", "candidate → reviewed gold",
     "template", "partial", "high", "Templates → Review", "cli-only",
     "promotion is operator-gated; DEFERRED for GUI action until gold-backup automation"),
    ("Promote/disable runtime template", "/api/template_manager",
     "enable/disable a template at runtime", "template", "full", "high",
     "Templates", "gui-actionable", "existing operator-gated API"),
    ("Inspect template health/drift", "template_health_report / drift",
     "scores, warnings, draft⇄gold drift", "template", "partial", "none",
     "Templates → Health", "gui-visible", "new template-manager page covers most"),
    ("Monitor queue + captures", "queue_intelligence / capture_analytics",
     "queue states, stuck URLs, artifact yield", "diagnostics", "partial", "none",
     "Monitoring", "gui-visible", "new monitoring page covers summary"),
    ("Run release gate", "verify_release.py", "version+docs+templates+tests gate",
     "release", "none", "low", "Dev/Release", "gui-actionable",
     "CLI-only today; safe to surface as a read-only run+report"),
    ("Browse generated reports", "report_center / reports/*", "report index",
     "reporting", "full", "none", "Reports", "gui-visible",
     "new report center page links the data layer"),
    ("Edit global/site config", "global_config / sites_config",
     "operator configuration", "configuration", "partial", "medium",
     "Settings (future)", "gui-actionable",
     "see Phase 2 config surface inventory; no settings center built"),
    # Phase 3 (GUI-parity final-audit finding #1): the operator journeys the audit
    # named but that were previously un-inventoried. Qualitative/curated, like the
    # rest of this list. Inventory only — no GUI control or route is added here.
    ("Manage VPN / tunnel", "vpn_config / vpn_tunnel_start|stop / vpn_leak_tests",
     "select region, start/stop tunnel, verify no leak", "configuration", "partial",
     "high", "Settings → VPN (future)", "gui-gated",
     "tunnel start/stop are state mutations (now gui-gated); leak test is read-only"),
    ("Account / credential rotation", "rotate / change_password / revoke",
     "rotate site credentials and tokens on a schedule", "configuration", "partial",
     "high", "Settings → Accounts (future)", "gui-gated",
     "credential lifecycle; gated. No rotation center built"),
    ("Backup / restore state", "api_backup_restore / config snapshot|restore",
     "snapshot operator state and restore it", "maintenance", "partial", "high",
     "Settings → Maintenance (future)", "gui-gated",
     "restore overwrites live state — gated; backup/snapshot are state ops"),
    ("Library scan + tagging", "api_library_scan_start / tagging",
     "scan the library, organize/tag downloaded items", "library", "partial",
     "medium", "Library (future)", "gui-gated",
     "scan start is a state/job mutation (now gui-gated); browsing is read-only"),
    ("Schedule / recurring jobs", "scheduler / quiet_hours / wakeup_*",
     "schedule captures/scans, quiet-hours windows", "scheduling", "partial",
     "medium", "Settings → Schedule (future)", "gui-actionable",
     "config-backed; quiet_hours/wakeup keys are statically visible (see Phase 2)"),
    ("Secrets lifecycle", "secrets / credential / key store",
     "add/rotate/revoke secrets used by sites", "configuration", "partial", "high",
     "Settings → Secrets (future)", "gui-gated",
     "secret/key/credential ops are gated; never surfaced unredacted"),
    ("Import / export config", "import / export config + templates",
     "move config/templates between hosts", "configuration", "partial", "high",
     "Settings → Import/Export (future)", "gui-gated",
     "import mutates config/templates — gated; export is read-only"),
    ("Live recording session", "live_recordings / capture (live)",
     "operator-driven live recording lifecycle", "capture", "partial", "high",
     "Captures → Live (future)", "gui-gated",
     "live recording is operator-driven; not sandbox-testable; gated"),
    ("Manual-login handoff", "manual_login handoff (noVNC) / challenge clear",
     "operator clears a challenge / logs in by hand in the noVNC pane", "capture",
     "partial", "high", "Captures (noVNC)", "gui-gated",
     "the human-in-the-loop step; tool detects/logs challenges, never automates past"),
]


def _extra_surfaces(root):
    """Operator surfaces beyond tools/ + routes (audit findings)."""
    out = []
    # CLI dashboard living in the package (missed by tools/ scan)
    cd = Path(root) / "bulk_downloader" / "cli_dashboard.py"
    if cd.is_file():
        out.append({
            "name": "cli_dashboard", "source_file": "bulk_downloader/cli_dashboard.py",
            "command_or_endpoint": "python3 -m bulk_downloader.cli_dashboard",
            "purpose": _first_docline(cd) or "terminal operator dashboard",
            "category": "diagnostics", "gui_support": "none", "dependencies": [],
            "difficulty": "low", "runtime_risk": "low",
            "recommended_gui_location": "Monitoring", "recommendation": "gui-visible",
            "gui_class": "read-only", "kind": "cli_tool"})
    # shell entrypoints
    shells = [("bd", "operator wrapper: run any cmd with full env + services", "low"),
              ("bd-status", "kit/service status (20/20)", "none"),
              ("bd-install", "land kits + extract source", "low"),
              ("setup.sh", "bootstrap sandbox / install", "low"),
              ("bdenv.sh", "environment export shim", "none"),
              ("capture.sh", "operator capture launcher", "medium")]
    for nm, purpose, risk in shells:
        p = Path(root) / nm
        out.append({
            "name": nm, "source_file": nm if p.exists() else f"{nm} (host)",
            "command_or_endpoint": f"./{nm}", "purpose": purpose,
            "category": "shell", "gui_support": "none", "dependencies": [],
            "difficulty": "medium", "runtime_risk": risk,
            "recommended_gui_location": "(shell — not GUI)",
            "recommendation": "cli-only", "gui_class": "cli-only",
            "kind": "shell_entrypoint"})
    # GUI operator surfaces with no single route (interactive)
    out.append({
        "name": "browser extension", "source_file": "extension/",
        "command_or_endpoint": "(browser extension)",
        "purpose": "operator browser extension (secret pairing / capture assist)",
        "category": "browser", "gui_support": "full", "dependencies": [],
        "difficulty": "high", "runtime_risk": "high",
        "recommended_gui_location": "(separate surface)",
        "recommendation": "gui-gated", "gui_class": "gui-gated", "kind": "gui_surface"})
    out.append({
        "name": "noVNC capture surface", "source_file": "capture_session / noVNC",
        "command_or_endpoint": "(noVNC interactive)",
        "purpose": "interactive operator capture (manual takeover / finish)",
        "category": "capture", "gui_support": "partial", "dependencies": [],
        "difficulty": "high", "runtime_risk": "high",
        "recommended_gui_location": "Captures", "recommendation": "gui-gated",
        "gui_class": "gui-gated", "kind": "gui_surface"})
    return out


# Any /api/... literal, allowing whole ${...} template blocks (which may contain
# spaces, '?', parens) and ordinary path chars. Catches quoted + backtick literals
# and positional args; ${...} is matched as a unit so it isn't truncated at '?'.
_SPA_API_RE = re.compile(r"/api/(?:\$\{[^}]*\}|[A-Za-z0-9_./<>{}:%-])+")

# Method-qualified harvest. The path-only set above stays the fallback; this adds
# (METHOD, path) pairs so a shared-path method pair (e.g. POST + GET on
# /api/vpn/tunnels) is no longer mutually credited by the method-blind matcher.
# Verb token, an optional <TypeParam>, optional newlines, then the literal — this
# is how the real SPA writes calls (apiGet<QueueV2>("/api/..."), multi-line
# backtick templates), so detection survives the typed/wrapped forms.
_API_PATH = r"(/api/(?:\$\{[^}]*\}|[A-Za-z0-9_./<>{}:%-])+)"
_API_VERB_RE = re.compile(
    r"\bapi(Get|Post|Put|Delete|Patch)(?:Form)?\s*(?:<[^>]*>)?\s*\(\s*"
    r"[`'\"]" + _API_PATH, re.S)
# fetch() defaults to GET but may declare method:"PUT"/"DELETE" in its options
# object (the SPA deletes a site this way) — read it, don't assume GET.
_API_FETCH_RE = re.compile(r"\bfetch\s*\(\s*[`'\"]" + _API_PATH, re.S)
_API_SSE_RE = re.compile(r"\bnew\s+EventSource\s*\(\s*[`'\"]" + _API_PATH, re.S)
_FETCH_METHOD_RE = re.compile(r"\bmethod\s*:\s*[`'\"]([A-Za-z]+)", re.S)
_VERB_METHOD = {"Get": "GET", "Post": "POST", "Put": "PUT",
                "Delete": "DELETE", "Patch": "PATCH"}

# operator-facing vs dev/internal endpoint families (the latter stay CLI/dev-only,
# not GUI-parity targets).
_INTERNAL_EP_RE = re.compile(
    r"^/api/(dev|bitrot|circuit|crash_recovery|pair|debug|test|_)\b")


def strip_ts_comments(text):
    """Remove // and /* */ comments from TS/TSX, preserving string and template-literal
    contents verbatim.

    v3.66.754b. WHY A STATE MACHINE AND NOT A REGEX -- this is the whole lesson.

    A naive ``re.sub(r"/\\*.*?\\*/", "", s)`` treats a ``/*`` appearing INSIDE a ``//``
    comment as a block opener. Dedup.tsx contains a line comment reading

        //   * status - scan - scan/cancel - find - ...

    and the ``/*`` in it opened a "block" that ran 6,448 chars forward to the next ``*/``
    (a JSX ``{/* ... */}``), deleting 40% of the file -- live ``apiPost`` call sites
    included. That hack reported 22 "phantom" endpoints, of which dedup/scan, batch/delete
    and friends are REAL, CALLED endpoints. The instrument could not parse its own subject
    (KB_JUDGMENT (g)). So: ``/*`` inside a ``//`` comment is just text, and a ``//`` inside
    a string is just text.

    NOT HANDLED, and said out loud rather than silently assumed: regex literals
    (``/foo/.test(x)``) are not tracked. A regex containing an unbalanced quote or ``//``
    could confuse the machine. None exists in frontend/src today; if one lands, the
    round-trip assertions in the 754b tests fail LOUDLY rather than mis-stripping quietly.

    Newlines are preserved so line numbers survive for any caller that anchors on them.
    """
    out = []
    i, n = 0, len(text)
    quote = None          # ' " or `  -> inside a string / template literal
    in_line = False       # inside //...
    in_block = False      # inside /* ... */
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_line:
            if c == "\n":
                in_line = False
                out.append(c)
            i += 1
            continue

        if in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            if c == "\n":
                out.append(c)
            i += 1
            continue

        if quote:
            out.append(c)
            if c == "\\":                      # escape: copy the escaped char too
                if i + 1 < n:
                    out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue

        # --- not in a string or a comment ---
        if c in ("'", '"', "`"):
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _norm_ep(path):
    """Normalise an endpoint path for SPA<->inventory matching: drop a leading
    METHOD token, collapse path params (<sid>, {id}, ${expr}, :id) to '*', strip
    query, drop trailing slash/punctuation.

    v3.66.754b -- ORDER IS LOad-BEARING. This used to strip the query BEFORE
    collapsing ``${...}``, so a JS nullish operator inside a template expression was
    eaten as a query delimiter:

        /api/widgets/${encodeURIComponent(siteId ?? "_global")}
          -> '/api/widgets/${encodeURIComponent(siteId '   (truncated)

    ...which then failed the ``"$" in e`` guard in _spa_wiring and was DISCARDED. The
    harvest was never the problem; the normaliser was. Three REAL, OPERABLE controls
    (widgets GET/PUT/DELETE) were invisible to the scanner, and only a COMMENT was
    crediting them. Collapse the template expression first, then strip the query -- a
    '?' inside ${...} belongs to the expression, not to a query string.
    """
    p = (path or "").strip()
    if " " in p and p.split(" ", 1)[0].replace("|", "").isupper():
        p = p.split(" ", 1)[1].strip()          # drop "GET|POST " prefix
    p = re.sub(r"\$\{[^}]*\}", "*", p)           # JS template ${expr} -- BEFORE the query split
    p = p.split("?", 1)[0]                       # now a '?' can only be a real query
    p = re.sub(r"<[^>]+>", "*", p)               # Flask <sid>
    p = re.sub(r"\{[^}]+\}", "*", p)             # doc/path {id}
    p = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "*", p)  # react-router :id
    p = p.rstrip("/.,;:)")                       # trailing slash/prose punctuation
    return p


def _is_internal_ep(path):
    """True if the endpoint is a dev/maintenance/internal surface (not a
    GUI-parity target). path is a raw or normalised route path."""
    return bool(_INTERNAL_EP_RE.search(path or ""))


# Endpoints that are operator-facing but NOT served by the React SPA, so they are
# not SPA-wireable parity gaps: cockpit-blueprint routes (their own cockpit UI)
# and the two browser-extension *data* routes the extension calls directly.
# (secrets/extension/pair_issue + /revoke are operator MANAGEMENT actions and stay
# counted as genuine SPA gaps — only the data-plane pair/fetch_one are excluded.)
_EXTENSION_DATA_EPS = {
    "/api/secrets/extension/pair", "/api/secrets/extension/fetch_one"}

# v3.66.770: the WebRTC leak-probe endpoints are a WORKER/browser-context data
# plane, not operator UI. /api/vpn/webrtc_js serves a JS snippet that workers eval
# inside their Playwright contexts, and .../webrtc_result is where those contexts
# POST the probe result back. The operator SPA never calls either -- wiring a fetch
# would be a dead control. They are non-SPA surfaces (like the extension data plane),
# so they must not count as SPA-wireable operator gaps.
_WORKER_DATA_EP_SUFFIXES = ("/webrtc_result",)
_WORKER_DATA_EPS = {"/api/vpn/webrtc_js"}


def _is_non_spa_surface(path):
    """True if the endpoint is operator-facing but served by a non-SPA surface
    (cockpit blueprint UI, the browser extension data-plane, or a worker/browser-
    context data plane), so it must not be counted as an SPA-wireable operator gap.
    """
    if not path:
        return False
    if path.startswith("/cockpit/"):
        return True
    if path in _EXTENSION_DATA_EPS or path in _WORKER_DATA_EPS:
        return True
    return path.endswith(_WORKER_DATA_EP_SUFFIXES)


def _endpoint_path(it):
    """Return the route path of an inventory item, or '' if it is not an
    HTTP endpoint (CLI tools, workflows, extension/noVNC surfaces)."""
    ce = it.get("command_or_endpoint", "")
    m = re.match(r"^[A-Z|]+\s+(/\S+)$", ce)
    return m.group(1) if m else ""


def _endpoint_methods(it):
    """HTTP methods declared on an inventory endpoint, e.g. 'PATCH|PUT /api/x'
    -> {'PATCH','PUT'}. Empty set if not an endpoint item."""
    ce = it.get("command_or_endpoint", "")
    m = re.match(r"^([A-Z|]+)\s+/\S+$", ce)
    return set(m.group(1).split("|")) if m else set()


def _dispatcher_method(txt, start):
    """Best-effort verb for a dispatcher literal at byte offset ``start``: the
    nearest ``api<Verb>(`` opening just before it (bounded lookback). Returns a
    METHOD string or None when no verb is adjacent (then it stays path-only)."""
    window = txt[max(0, start - 60):start]
    m = None
    for m in re.finditer(r"\bapi(Get|Post|Put|Delete|Patch)(?:Form)?\s*(?:<[^>]*>)?\s*\($", window):
        pass  # keep the last (closest to the literal)
    return _VERB_METHOD.get(m.group(1)) if m else None


def _resolve_dynamic_dispatchers(txt, eps, method_eps=None):
    """Credit endpoints reached through a *generic* dispatcher whose trailing
    path segment is a free variable bound to a STATIC set of literals.

    The v3.66.177 site-actions surface dispatches every per-site action through
    one call, ``apiPost(`/api/sites/${siteId}/${suffix}`)``, where ``suffix`` is
    drawn from a literal table (``{ suffix: "start" }``, ``{ suffix:
    "accounts/rotate" }``, …). The plain literal matcher only sees the dispatcher
    skeleton ``/api/sites/*/*`` and cannot exact-match the concrete route paths,
    so those endpoints were under-credited (the parity-denominator inflation
    noted in the v3.66.177 handoff).

    Resolution is deliberately BOUNDED to avoid over-crediting held endpoints:
      * find dispatchers of the form ``/api/<…>/${<var>}`` (a bare-identifier
        trailing segment), and capture the static prefix + the var name;
      * harvest ONLY string literals assigned to a property named exactly that
        var (``<var>: "literal"`` / ``<var> = "literal"``) within the same file;
      * synthesise ``<prefix>/<literal>`` for each and add the normalised path.
    Because harvested literals are keyed to the dispatcher's own trailing
    variable, a ``suffix:`` table with no matching ``${suffix}`` dispatcher
    contributes nothing, and body-bearing site actions that are NOT in the
    literal table stay correctly unwired.
    """
    # /api/<path-with-one-${param}>/${trailingVar}  — trailing var is an identifier,
    # optionally a MEMBER EXPRESSION (v3.66.754b).
    #
    # It required a BARE ident (`${suffix}`). Vpn.tsx dispatches through `${t.action}` --
    # a member expression -- so this regex never matched, the resolver never ran, and
    # `_norm_ep` collapsed the segment to `*`: `/api/vpn/tunnels/*/*` does not match the
    # route's `/api/vpn/tunnels/*/start`. Three REAL, OPERABLE controls (start/stop/cycle:
    # live dispatcher, real buttons, confirm dialog, per-action toasts) were invisible to
    # the harvest, and a COMMENT was the only thing crediting them. The literal table the
    # resolver needs (`action: "start"` etc.) is in the same file.
    #
    # This widens the SHAPE recognised, not the credit given: the harvest is still keyed
    # to literals bound to the dispatcher's OWN trailing property name, so a table with no
    # matching dispatcher still contributes nothing (pinned by a NEG test).
    disp_re = re.compile(
        r"/api/(?:\$\{[^}]*\}|[A-Za-z0-9_./<>{}:%-])+?/"
        r"\$\{(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\}(?=[`\"'?)])")
    for dm in disp_re.finditer(txt):
        var = dm.group(1)
        meth = _dispatcher_method(txt, dm.start())
        # static prefix = the matched path up to (not incl.) the trailing ${var}
        prefix = _norm_ep(dm.group(0)[: dm.group(0).rfind("${")].rstrip("/"))
        if "$" in prefix or "{" in prefix:
            continue
        lit_re = re.compile(
            rf"\b{re.escape(var)}\b\s*[:=]\s*[\"'`]([A-Za-z0-9_./<>{{}}:%-]+)[\"'`]")
        for lm in lit_re.finditer(txt):
            synth = _norm_ep(prefix + "/" + lm.group(1))
            if "$" not in synth and "{" not in synth:
                eps.add(synth)
                if method_eps is not None and meth:
                    method_eps.add((meth, synth))


_TEST_FILE_RE = re.compile(r"\.(test|spec)\.tsx?$")


def _is_spa_source(path) -> bool:
    """True for PRODUCT source, False for a test/spec file.

    THE SCANNER'S POPULATION IS ITS DENOMINATOR. ``_spa_wiring`` is the evidence
    that the SPA actually wires an endpoint, and a *.test.tsx / *.spec.tsx file
    is not the SPA -- it is a description of it. Leaving test files in meant a
    FIXTURE could vouch for a route no product code calls, which is the same
    laundering v3.66.754b closed when it began stripping COMMENTS so a path
    merely NAMED could not count. Comments were one way to name without calling;
    test files are the other, and only the first was closed.

    MEASURED at v3.66.1217 before the change: 457 wired endpoints with test files
    in the population, 443 without -- 14 present ONLY because a test names them,
    every one an obvious fixture (/api/auth/users/bob/role,
    /api/auth/users/carol/password, /api/auth/users/dave,
    /api/sites/alpha/ai_reanalyze, /api/daily_budget/history/ex.com,
    /api/knowledge/notes/7, /api/queue_templates/7/apply/beta,
    /api/cookie_clipboard/save/alpha, /api/auth/users/a%20b%2Fc/role and others).
    """
    return not _TEST_FILE_RE.search(getattr(path, "name", str(path)))


def _spa_wiring(root):
    """Scan the React SPA source for the set of /api/* endpoints it references,
    normalised. Uses one robust literal matcher (quoted + backtick template +
    positional), plus a bounded resolver for generic dispatchers whose trailing
    segment is a variable bound to a static literal table (see
    ``_resolve_dynamic_dispatchers``). Empty set if the frontend tree is absent
    — keeps the pass behaviour-preserving.

    v3.66.754b: COMMENTS ARE STRIPPED BEFORE HARVESTING. A path merely NAMED in a comment
    used to count as a call site, so gui_parity could be wired by prose.

    Still cannot resolve fully-dynamic URLs (``apiGet(url)`` where url is computed at
    runtime). Those are NOT silently ignored -- see ``spa_wiring_unresolved``.
    """
    src = Path(root) / "frontend" / "src"
    eps = set()
    method_eps = set()
    if not src.is_dir():
        return eps, method_eps
    for f in src.rglob("*.ts*"):          # .ts and .tsx
        if not _is_spa_source(f):     # a fixture is not wiring; see _is_spa_source
            continue
        try:
            raw_txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        txt = strip_ts_comments(raw_txt)
        for m in _SPA_API_RE.finditer(txt):
            e = _norm_ep(m.group(0))
            if "$" in e or "{" in e:   # partial capture of a template expr — skip
                continue
            eps.add(e)
        # method-qualified harvest (additive; path-only set above stays the fallback)
        for m in _API_VERB_RE.finditer(txt):
            meth = _VERB_METHOD.get(m.group(1))
            e = _norm_ep(m.group(2))
            if meth and "$" not in e and "{" not in e:
                method_eps.add((meth, e))
        for m in _API_SSE_RE.finditer(txt):       # SSE is always GET
            e = _norm_ep(m.group(1))
            if "$" not in e and "{" not in e:
                method_eps.add(("GET", e))
        for m in _API_FETCH_RE.finditer(txt):     # fetch: read method: option, default GET
            e = _norm_ep(m.group(1))
            if "$" in e or "{" in e:
                continue
            mm = _FETCH_METHOD_RE.search(txt, m.end(), m.end() + 200)
            method_eps.add(((mm.group(1).upper() if mm else "GET"), e))
        _resolve_dynamic_dispatchers(txt, eps, method_eps)
    return eps, method_eps


# An api<Verb>( call whose first argument is NOT a string/template literal — an
# INDIRECTED call site (apiGet(url), apiPost(IMPORT_EP)). The literal harvest cannot
# see these at all: there is no path to match.
_API_NONLITERAL_RE = re.compile(
    r"\bapi(Get|Post|Put|Delete|Patch)(?:Form)?\s*(?:<[^>]*>)?\s*\(\s*"
    r"(?![`'\"])([A-Za-z_$][\w.$]*)")
# The module that DEFINES the api* helpers forwards its own `path` parameter, which looks
# identical to an indirected call. Detect it by what it DECLARES, not by its filename — a
# hardcoded exclusion is just another hand-kept denominator waiting to drift.
_API_CLIENT_DEF_RE = re.compile(r"export\s+(?:async\s+)?function\s+api(?:Get|Post|Put|Delete|Patch)")


def spa_wiring_unresolved(root):
    """SPA call sites the harvest CANNOT SEE, as an explicit third state.

    v3.66.754b. The scanner reports a set of wired endpoints. It has never reported what
    it could not read -- and ``_spa_wiring`` cannot resolve ``apiGet(url)`` where the path
    is computed at runtime. Reporting only the resolved set makes the unresolvable ones
    indistinguishable from the non-existent ones: unknown gets silently folded into ABSENT,
    which is the shape this whole program exists to kill. (It bit here: the widgets
    GET/PUT/DELETE were dropped by a bare ``continue`` for their entire life, and the only
    reason anyone ever noticed is that a COMMENT happened to be crediting them.)

    Returns [(relative_path, VERB, argument_expression), ...]. A non-empty result is not a
    failure -- it is the honest statement that N controls exist which this scanner is
    structurally unable to adjudicate, so a reader does not mistake "not in the wired set"
    for "not called".
    """
    src = Path(root) / "frontend" / "src"
    out = []
    if not src.is_dir():
        return out
    for f in sorted(src.rglob("*.ts*")):
        if not _is_spa_source(f):     # same population rule as _spa_wiring
            continue
        try:
            txt = strip_ts_comments(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if _API_CLIENT_DEF_RE.search(txt):
            continue                      # this module DEFINES the helpers; not a call site
        for m in _API_NONLITERAL_RE.finditer(txt):
            out.append((str(f.relative_to(src)), _VERB_METHOD[m.group(1)], m.group(2)))
    return out


def build(root="."):
    root = os.path.abspath(root)
    tools_dir = Path(root) / "tools"
    tool_paths = [p for p in sorted(tools_dir.glob("*.py")) if not p.name.startswith("_")]
    known = {p.stem for p in tool_paths} | {
        "template_inventory", "template_drift_report", "check_doc_drift",
        "report_core", "template_core", "kb_core", "capture_analytics",
        "queue_intelligence", "changelog_analyzer", "kb_audit", "app_data_layer"}
    items = [_tool_item(p, known) for p in tool_paths]
    routes = _routes_from_app()
    route_src = "live url_map" if routes is not None else "ENDPOINT_CATALOG.md (fallback)"
    if routes is None:
        routes = _routes_from_catalog(Path(root) / "ENDPOINT_CATALOG.md")
    for r in routes:
        it = _route_item(r)
        if it is not None:  # None == asset, skipped
            items.append(it)
    for (nm, src, purpose, cat, gui, risk, loc, rec, note) in _WORKFLOWS:
        gclass = ("cli-only" if rec == "cli-only" else
                  ("read-only" if rec == "gui-visible" else
                   ("gui-gated" if risk == "high" else "gui-safe")))
        items.append({
            "name": nm, "source_file": src, "command_or_endpoint": src,
            "purpose": purpose, "category": cat, "gui_support": gui,
            "dependencies": [], "difficulty": "high" if risk == "high" else "medium",
            "runtime_risk": risk, "recommended_gui_location": loc,
            "recommendation": rec, "gui_class": gclass, "kind": "workflow", "note": note,
        })
    items.extend(_extra_surfaces(root))
    # SPA-wiring pass (additive): mark which HTTP endpoints the React SPA
    # actually calls, so gui_class (risk tier) can be read against real
    # frontend reachability instead of being mistaken for build status.
    spa_eps, spa_method_eps = _spa_wiring(root)
    for it in items:
        ep = _endpoint_path(it)
        np = _norm_ep(ep) if ep else ""
        # Method-aware match: when the SPA's verb(s) for this exact path are
        # reliably detected, require a method match (breaks the shared-path
        # phantom where a sibling method gets credited). When no verb is
        # detectable for the path (indirected/const literals), fall back to the
        # path-only set — preserving prior behaviour with zero regression.
        if ep:
            methoded = {m for (m, p) in spa_method_eps if p == np}
            if methoded:
                wired = bool(_endpoint_methods(it) & methoded)
            else:
                wired = np in spa_eps
        else:
            wired = False
        it["spa_wired"] = wired
        # endpoints only: True=operator-facing, False=dev/internal, None=not an endpoint
        it["operator_facing"] = (None if not ep else (not _is_internal_ep(ep)))
        # endpoints only: False=served by a non-SPA surface (cockpit/extension),
        # so not an SPA-wireable parity gap; None=not an endpoint
        it["spa_surface"] = (None if not ep else (not _is_non_spa_surface(ep)))
    return {"root": root, "route_source": route_src,
            "spa_endpoints_called": len(spa_eps),
            "counts": _counts(items), "items": items}


def _counts(items):
    by_kind, by_gui, by_rec, by_risk, by_class = {}, {}, {}, {}, {}
    for it in items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
        by_gui[it["gui_support"]] = by_gui.get(it["gui_support"], 0) + 1
        by_rec[it["recommendation"]] = by_rec.get(it["recommendation"], 0) + 1
        by_risk[it["runtime_risk"]] = by_risk.get(it["runtime_risk"], 0) + 1
        by_class[it.get("gui_class", "?")] = by_class.get(it.get("gui_class", "?"), 0) + 1
    spa_wired_total = sum(1 for it in items if it.get("spa_wired"))
    # gui-gated ENDPOINTS split by real SPA reachability — the actionable
    # write-side number (unwired gui-gated endpoints are the remaining work).
    gg = [it for it in items if it.get("gui_class") == "gui-gated"
          and _endpoint_path(it)]
    gg_wired = sum(1 for it in gg if it.get("spa_wired"))
    gg_op = [it for it in gg if it.get("operator_facing")]
    # SPA-wireable operator gaps = operator-facing, unwired, AND served by the SPA
    # (excludes cockpit-native + extension data-plane routes).
    gg_op_unwired_all = [it for it in gg_op if not it.get("spa_wired")]
    gg_op_unwired = sum(1 for it in gg_op_unwired_all if it.get("spa_surface"))
    gg_non_spa_unwired = len(gg_op_unwired_all) - gg_op_unwired
    return {"total": len(items), "by_kind": by_kind, "by_gui_support": by_gui,
            "by_recommendation": by_rec, "by_runtime_risk": by_risk,
            "by_gui_class": by_class,
            "spa_wired_total": spa_wired_total,
            "gui_gated_endpoints": {"total": len(gg), "spa_wired": gg_wired,
                                    "spa_unwired": len(gg) - gg_wired,
                                    "operator_facing": len(gg_op),
                                    "operator_facing_unwired": gg_op_unwired,
                                    "non_spa_surface_unwired": gg_non_spa_unwired,
                                    "dev_internal": len(gg) - len(gg_op)}}


def _routes_from_catalog(path):
    out = []
    if not path.is_file():
        return out
    import re
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.search(r"`(/(?:cockpit|api)[\w/{}.-]*)`", line)
        if m:
            out.append({"rule": m.group(1), "endpoint": m.group(1), "methods": ["GET"]})
    return out


def _md(d):
    c = d["counts"]
    L = ["# GUI Parity Inventory — Phase 1 (Phase 2.5 corrected)", "",
         f"- root: `{d['root']}`",
         f"- route source: {d['route_source']}",
         f"- total items: **{c['total']}** — by kind: {c['by_kind']}",
         f"- GUI support: {c['by_gui_support']}",
         f"- **gui_class (risk-aware): {c.get('by_gui_class', {})}**",
         f"- runtime risk mix: {c['by_runtime_risk']}", "",
         "Discovery/inventory only — no GUI controls implemented. `gui_class` "
         "(gui-safe / gui-gated / read-only / cli-only) is the corrected risk-aware "
         "classification; values are recommendations for later review.", ""]
    order = ["workflow", "gui_surface", "cockpit_page", "gui_page",
             "cockpit_api", "gui_api", "blueprint_module", "cli_tool",
             "shell_entrypoint"]
    titles = {"workflow": "Operator workflows", "gui_surface": "GUI operator surfaces",
              "cockpit_page": "Cockpit pages", "gui_page": "Other operator GUI pages",
              "cockpit_api": "Cockpit APIs", "gui_api": "Other operator GUI APIs",
              "blueprint_module": "Blueprint modules (back routes)",
              "cli_tool": "CLI tools", "shell_entrypoint": "Shell entrypoints"}
    for kind in order:
        rows = [it for it in d["items"] if it["kind"] == kind]
        if not rows:
            continue
        L += [f"## {titles[kind]} ({len(rows)})", "",
              "| name | source | command/endpoint | category | gui_class | risk | deps | location |",
              "|---|---|---|---|---|---|---|---|"]
        for it in sorted(rows, key=lambda x: (x["category"], x["name"])):
            deps = ",".join(it.get("dependencies", []))[:36] or "—"
            L.append("| {name} | `{src}` | `{cmd}` | {cat} | **{gc}** | {risk} | {deps} | {loc} |".format(
                name=it["name"][:46], src=it["source_file"][:34],
                cmd=it["command_or_endpoint"][:44], cat=it["category"],
                gc=it.get("gui_class", "?"), risk=it["runtime_risk"], deps=deps,
                loc=it["recommended_gui_location"]))
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = build(a.root)
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    os.makedirs(a.outdir, exist_ok=True)
    jp = os.path.join(a.outdir, "gui_parity_inventory.json")
    mp = os.path.join(a.outdir, "gui_parity_inventory.md")
    if _RC:
        _RC.write_json(jp, d)
        _RC.write_report(a.outdir, "gui_parity_inventory.md", _md(d))
    else:
        open(jp, "w").write(json.dumps(d, indent=2, default=str))
        open(mp, "w").write(_md(d))
    print(f"wrote {jp} and {mp}: {d['counts']['total']} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
