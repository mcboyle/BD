"""dev_suite.audit_security -- security & config audit

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re

from ._common import (
    _MANIFEST_EXCLUDE_DIRS, _iter_route_sources, _pkg_dir, _repo_root,
    _resolve_site_config)



# ── 22. route source enumeration + auth-surface tools (D-81, D-83) ──

_STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}



def _before_request_hook_names(app) -> list:
    return [getattr(f, "__name__", "?")
            for f in app.before_request_funcs.get(None, [])]



def csrf_coverage(app) -> dict:
    """Confirm CSRF is enforced for state-changing /api/ requests.

    CSRF in this app is enforced GLOBALLY by the _check_csrf
    @before_request hook — it runs ahead of every request — so it is
    NOT a per-route concern. The real coverage question is whether
    that hook is registered; its absence would be the security hole.
    The hook's own path-level exemptions are surfaced for audit. Some
    view functions also call _check_csrf() inline — that is a harmless
    redundant belt-and-suspenders call, not the coverage mechanism."""
    import inspect
    import re
    hooks = app.before_request_funcs.get(None, [])
    hook = next((f for f in hooks
                 if getattr(f, "__name__", "") == "_check_csrf"), None)
    registered = hook is not None
    exemptions = []
    if hook is not None:
        try:
            src = inspect.getsource(hook)
            exemptions = sorted({m for m in re.findall(
                r"""["'](/api/[^"']+)["']""", src) if m != "/api/"})
        except (OSError, TypeError):
            exemptions = []
    state_routes, inline = 0, 0
    for rule, methods, endpoint, rsrc in _iter_route_sources(app):
        if rule.startswith("/api/") and (_STATE_METHODS & set(methods)):
            state_routes += 1
            if "_check_csrf(" in rsrc:
                inline += 1
    return {
        "global_hook_registered": registered,
        "before_request_hooks": _before_request_hook_names(app),
        "state_changing_api_routes": state_routes,
        "covered": state_routes if registered else 0,
        "routes_with_redundant_inline_call": inline,
        "hook_path_exemptions": exemptions,
        "verdict": (
            "CSRF enforced globally via the _check_csrf "
            "before_request hook"
            if registered else
            "CRITICAL: no _check_csrf before_request hook — "
            "state-changing /api routes are UNPROTECTED"),
    }



def auth_surface(app) -> dict:
    """Map every route to the auth controls that apply to it.

    Two @before_request hooks apply to ALL routes: _check_token
    (session / same-origin auth) and _check_csrf (CSRF on
    state-changing /api/ requests). Views additionally apply inline
    gates — the dev-mode guard and the vault-token guard. The one
    genuinely per-route requirement is the dev-mode gate: every
    /api/dev route except /api/dev/enabled must call _dev_mode_guard()
    in its own body, so a missing one is a real anomaly."""
    hook_names = _before_request_hook_names(app)
    csrf_hook = "_check_csrf" in hook_names
    routes, anomalies = [], []
    for rule, methods, endpoint, src in _iter_route_sources(app):
        state = bool(_STATE_METHODS & set(methods))
        controls = ["global:session-auth"]
        if csrf_hook and rule.startswith("/api/") and state:
            controls.append("global:csrf")
        if "_dev_mode_guard(" in src:
            controls.append("dev-mode")
        if "_require_vault_token(" in src:
            controls.append("vault-token")
        if "_check_csrf(" in src:
            controls.append("inline-csrf(redundant)")
        routes.append({"rule": rule, "methods": methods,
                       "endpoint": endpoint, "controls": controls})
        if (rule.startswith("/api/dev/") and "dev-mode" not in controls
                and rule != "/api/dev/enabled"):
            anomalies.append({"rule": rule,
                              "issue": "/api/dev route whose view does "
                                       "not call _dev_mode_guard()"})
    routes.sort(key=lambda r: r["rule"])
    return {
        "route_count": len(routes),
        "global_hooks": hook_names,
        "routes": routes,
        "anomalies": anomalies,
        "verdict": ("no auth-surface anomalies — every /api/dev route "
                    "is dev-gated, and the global hooks cover the rest"
                    if not anomalies
                    else f"{len(anomalies)} anomaly(ies) flagged"),
    }



# ── 33. dispatch-chain tracer / dry-run (U5: D-11 + D-20) ──────────
#
# These two tools make runner.py::_process_one's dispatch chain
# OBSERVABLE without touching it. _process_one is a load-bearing
# 655-line function — its branch order is invariant INV-002 and must
# never be reordered. These tools only READ it and re-evaluate its
# side-effect-free *eligibility* predicates; they never call
# _process_one and never call the work helpers (_try_library_extractor
# / _try_qb_download / _try_jd_download / _try_jsonapi_extractor /
# auto-teach / relogin / stash-dedup) that download or mutate state.
#
# _DISPATCH_CHAIN is a hand-maintained mirror of that chain. A mirror
# can drift, so dispatch_chain() re-reads _process_one and verifies
# every branch marker still appears in order (chain_verified), and
# tests/test_dispatch_chain.py pins the same sequence — a reorder of
# _process_one becomes a loud test failure instead of silent rot.

_DISPATCH_CHAIN = [  # INV-002
    {"step": 1, "branch": "retry_backoff", "kind": "runtime_gate",
     "marker": '"Waiting for retry"',
     "condition": "job.retry_after is set and still in the future",
     "outcome": "defers the URL back to pending"},
    {"step": 2, "branch": "auto_teach", "kind": "runtime_gate",
     "marker": "_handle_auto_teach_check(",
     "condition": "_handle_auto_teach_check(url, job) returns True",
     "outcome": "first-download auto-teach handled the URL"},
    {"step": 3, "branch": "cluster_rate", "kind": "config_gate",
     "marker": '"use_cluster_rate"', "config_key": "use_cluster_rate",
     "condition": "config.use_cluster_rate AND the cluster lease cap is hit",
     "outcome": "defers the URL (fail-open: cluster errors fall through)"},
    {"step": 4, "branch": "cookie_relogin", "kind": "runtime_gate",
     "marker": "_check_cookies_or_relogin(",
     "condition": "_check_cookies_or_relogin(url) returns False",
     "outcome": "cookies expired and re-login failed — URL deferred"},
    {"step": 5, "branch": "stash_dedup", "kind": "runtime_gate",
     "marker": "_stash_dedup_check(",
     "condition": "Stash integration on AND the URL is already in Stash",
     "outcome": "skips the download, marks the job done"},
    {"step": 6, "branch": "library_extractor", "kind": "config_branch",
     "marker": '"use_library_extractor"',
     "config_key": "use_library_extractor",
     "condition": ("config.use_library_extractor AND a maintained "
                   "extractor library matches the URL host"),
     "outcome": "in-process direct/HLS download (fail-open: falls through)"},
    {"step": 7, "branch": "plugin_extractor", "kind": "runtime_gate",
     "marker": "_try_plugin_extractor(",
     "condition": ("a plugin registered a BD @extractor for this site "
                   "(plugins.get_extractor(site_id) is not None)"),
     "outcome": ("plugin @extractor direct download "
                 "(fail-open: falls through; HLS deferred)")},
    {"step": 8, "branch": "jsonapi_extractor", "kind": "config_branch",
     "marker": '"use_jsonapi"', "config_key": "use_jsonapi",
     "condition": "config.use_jsonapi AND config.jsonapi_url is populated",
     "outcome": ("HereSphere/DeoVR JSON-API download "
                 "(fail-open: falls through)")},
    {"step": 9, "branch": "qbittorrent", "kind": "config_branch",
     "marker": "if _use_qb:", "config_key": "backend",
     "condition": ("backend == 'qbittorrent' OR the URL is a "
                   "magnet/.torrent link"),
     "outcome": ("qB download; torrent-URL failure -> needs_review, "
                 "explicit-backend failure -> falls through")},
    {"step": 10, "branch": "jdownloader", "kind": "config_branch",
     "marker": "if _is_jd:", "config_key": "backend",
     "condition": "backend == 'jd'",
     "outcome": "JDownloader Remote-API download (failure -> falls through)"},
    {"step": 11, "branch": "playwright_teach", "kind": "fallthrough",
     "marker": "persistent_ctx is not None",
     "condition": "nothing above handled the URL",
     "outcome": "the default Playwright teach/scrape path (_do_download)"},
]



def _read_process_one_source():
    """Read runner.py::_process_one's source text, read-only.
    Returns '' if the function can't be located."""
    import re as _re
    try:
        lines = ((_pkg_dir() / "runner.py")
                 .read_text(encoding="utf-8").splitlines())
    except Exception:
        return ""
    start = None
    for i, ln in enumerate(lines):
        if _re.match(r"\s*def _process_one\b", ln):
            start = i
            break
    if start is None:
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if (ln.strip() and (len(ln) - len(ln.lstrip())) <= indent
                and _re.match(r"\s*def ", ln)):
            end = j
            break
    return "\n".join(lines[start:end])



def _verify_chain_against_source():
    """Re-read _process_one and confirm every _DISPATCH_CHAIN marker
    still appears, in dispatch order. Returns (ok, detail, positions)."""
    src = _read_process_one_source()
    if not src:
        return False, "could not read runner.py::_process_one", {}
    positions, missing = {}, []
    for entry in _DISPATCH_CHAIN:
        idx = src.find(entry["marker"])
        positions[entry["branch"]] = idx
        if idx < 0:
            missing.append(entry["branch"])
    if missing:
        return (False,
                f"markers absent from _process_one: {', '.join(missing)}",
                positions)
    ordered = [positions[e["branch"]] for e in _DISPATCH_CHAIN]
    bad = [_DISPATCH_CHAIN[i]["branch"] for i in range(1, len(ordered))
           if ordered[i] <= ordered[i - 1]]
    if bad:
        return (False,
                f"branches out of documented order: {', '.join(bad)}",
                positions)
    return True, "chain matches _process_one source order", positions



def _branch_enabled(branch, cfg):
    """Is a config-driven branch turned on for this site's config?"""
    if branch == "cluster_rate":
        return bool(cfg.get("use_cluster_rate", False))
    if branch == "library_extractor":
        return bool(cfg.get("use_library_extractor", False))
    if branch == "jsonapi_extractor":
        return bool(cfg.get("use_jsonapi", False) and cfg.get("jsonapi_url"))
    if branch == "qbittorrent":
        try:
            from bulk_downloader import qb_bridge as _qb
            return bool(_qb.is_qb_backend(cfg))
        except Exception:
            return False
    if branch == "jdownloader":
        try:
            from bulk_downloader import jd_bridge as _jd
            return bool(_jd.is_jd_backend(cfg))
        except Exception:
            return False
    return None



def dispatch_chain(site_id=None, runners=None):
    """D-11 — the ordered dispatch chain of runner.py::_process_one.

    Returns every branch in dispatch order with its trigger condition
    and outcome. If site_id resolves to a config, each config-driven
    branch is annotated with whether that site has it enabled. Always
    re-reads _process_one and reports chain_verified — False means the
    documented chain has drifted from the live function.
    """
    verified, verify_detail, _pos = _verify_chain_against_source()
    cfg, cfg_source = _resolve_site_config(site_id, runners)
    chain = []
    for entry in _DISPATCH_CHAIN:
        row = {"step": entry["step"], "branch": entry["branch"],
               "kind": entry["kind"], "condition": entry["condition"],
               "outcome": entry["outcome"]}
        if cfg is not None and entry.get("config_key"):
            row["enabled_for_site"] = _branch_enabled(entry["branch"], cfg)
        chain.append(row)
    return {
        "tool": "dispatch_chain",
        "source": "runner.py::_process_one (read-only)",
        "branch_count": len(chain),
        "chain": chain,
        "chain_verified": verified,
        "verify_detail": verify_detail,
        "site_id": site_id,
        "site_config_source": cfg_source,
        "note": ("_process_one is invariant INV-002 — this tool only "
                 "reads it. chain_verified=False means the documented "
                 "chain no longer matches the function; re-sync "
                 "_DISPATCH_CHAIN and the pin test."),
    }



def dispatch_dry_run(url, site_id=None, runners=None):
    """D-20 — trace which _process_one branch a URL routes to, WITHOUT
    downloading anything.

    Evaluates only the side-effect-free eligibility predicates (config
    reads, qb_bridge.looks_like_torrent_url, extractors.is_supported_url,
    the is_*_backend helpers) in dispatch order, and reports the first
    branch the URL is eligible for. It never calls _process_one and
    never calls the work helpers — nothing is downloaded, no state
    changes.

    Honest scope:
      • Runtime gates (retry-backoff, auto-teach, cookie-relogin,
        stash-dedup) run BEFORE the routed branch in the real chain
        and may defer/handle the URL first; they depend on live state
        and are reported as not evaluated.
      • The real chain is fail-open: if the routed branch's work
        helper fails at runtime, _process_one falls through to the
        next branch. This reports the *intended* route assuming the
        attempted branch succeeds.
    """
    if not isinstance(url, str) or not url.strip():
        return {"tool": "dispatch_dry_run", "ok": False,
                "error": "url is required"}
    url = url.strip()
    cfg, cfg_source = _resolve_site_config(site_id, runners)
    config_resolved = cfg is not None
    cfg = cfg or {}

    lib_site = None
    try:
        from bulk_downloader import extractors as _ex
        lib_site = _ex.is_supported_url(url)
    except Exception:
        lib_site = None
    try:
        from bulk_downloader import qb_bridge as _qb
        is_qb_backend = bool(_qb.is_qb_backend(cfg))
        is_torrent_url = bool(_qb.looks_like_torrent_url(url))
    except Exception:
        is_qb_backend = is_torrent_url = False
    try:
        from bulk_downloader import jd_bridge as _jd
        is_jd_backend = bool(_jd.is_jd_backend(cfg))
    except Exception:
        is_jd_backend = False

    use_lib = bool(cfg.get("use_library_extractor", False))
    use_json = bool(cfg.get("use_jsonapi", False))
    has_json_url = bool(cfg.get("jsonapi_url"))
    use_cluster = bool(cfg.get("use_cluster_rate", False))

    steps, routed = [], None

    def _add(branch, evaluated, eligible, detail):
        steps.append({"branch": branch, "evaluated": evaluated,
                      "eligible": eligible, "detail": detail})

    _add("retry_backoff", False, None,
         "runtime job-retry timer — not evaluated")
    _add("auto_teach", False, None,
         "runtime first-download/teach state — not evaluated")
    _add("cluster_rate", "config_only", None,
         f"use_cluster_rate={'on' if use_cluster else 'off'}; "
         "lease cap is a runtime check — not evaluated")
    _add("cookie_relogin", False, None,
         "live cookie expiry — not evaluated")
    _add("stash_dedup", False, None,
         "live Stash library lookup — not evaluated")

    lib_eligible = bool(use_lib and lib_site)
    _add("library_extractor", True, lib_eligible,
         f"use_library_extractor={'on' if use_lib else 'off'}; "
         f"extractor match={'site:' + lib_site if lib_site else 'none'}")
    if routed is None and lib_eligible:
        routed = "library_extractor"

    # v3.66.692: plugin @extractor branch. Eligibility is a side-effect-free
    # registry read (get_extractor(site_id) is not None) -- no download.
    try:
        from bulk_downloader import plugins as _pl
        plugin_eligible = bool(site_id and _pl.get_extractor(site_id) is not None)
    except Exception:
        plugin_eligible = False
    _add("plugin_extractor", True, plugin_eligible,
         f"registered @extractor for site={'yes' if plugin_eligible else 'no'}")
    if routed is None and plugin_eligible:
        routed = "plugin_extractor"

    json_eligible = bool(use_json and has_json_url)
    _add("jsonapi_extractor", True, json_eligible,
         f"use_jsonapi={'on' if use_json else 'off'}; "
         f"jsonapi_url={'set' if has_json_url else 'empty'}")
    if routed is None and json_eligible:
        routed = "jsonapi_extractor"

    qb_eligible = bool(is_qb_backend or is_torrent_url)
    _add("qbittorrent", True, qb_eligible,
         f"backend qB={'yes' if is_qb_backend else 'no'}; "
         f"torrent-style URL={'yes' if is_torrent_url else 'no'}")
    if routed is None and qb_eligible:
        routed = "qbittorrent"

    _add("jdownloader", True, is_jd_backend,
         f"backend jd={'yes' if is_jd_backend else 'no'}")
    if routed is None and is_jd_backend:
        routed = "jdownloader"

    _add("playwright_teach", True, routed is None,
         "default path when nothing above routes the URL")
    if routed is None:
        routed = "playwright_teach"

    routed_entry = next(e for e in _DISPATCH_CHAIN
                        if e["branch"] == routed)
    return {
        "tool": "dispatch_dry_run",
        "ok": True,
        "url": url,
        "site_id": site_id,
        "site_config_source": cfg_source,
        "config_resolved": config_resolved,
        "routed_to": routed,
        "routed_outcome": routed_entry["outcome"],
        "steps": steps,
        "downloaded": False,
        "caveats": [
            "runtime gates (retry-backoff, auto-teach, cookie-relogin, "
            "stash-dedup) run first in the real chain and may "
            "defer/handle the URL before the routed branch",
            "the real chain is fail-open: if the routed branch fails "
            "at runtime _process_one falls through — this reports the "
            "intended route on success",
        ]
        + ([] if config_resolved else
           ["no site config resolved — config-driven branches "
            "evaluated as off; only URL-based routing is reliable"]),
    }



def import_preflight(path=None, text=None, xlsx_bytes=None,
                     existing_urls=None, known_login_templates=None):
    """D-89 preflight — public entry (redaction shim).

    T10 / PHC-2: a bulk-import preflight echoes back per-row would-be configs
    (``login_url``) plus parser / validation messages. A bulk-import file can
    carry credentials in a ``login_url``'s userinfo (``user:pass@host``) or a
    signed query string, so the *preview response* is swept through the canonical
    capture redactor before it leaves the process. All detection — dedup,
    collision-with-existing, template/config validation — runs on the RAW values
    inside :func:`_import_preflight_impl`; only the returned strings are scrubbed,
    so the preview stays useful (host + path + param NAMES preserved, secret
    VALUES placeholdered) while never surfacing a credential. ``redact_artifact``
    is idempotent and ReDoS-bounded (see ``capture_artifact_redact``); ints /
    bools / counts pass through untouched.
    """
    from bulk_downloader.capture_artifact_redact import redact_artifact
    return redact_artifact(_import_preflight_impl(
        path=path, text=text, xlsx_bytes=xlsx_bytes,
        existing_urls=existing_urls,
        known_login_templates=known_login_templates))



def _import_preflight_impl(path=None, text=None, xlsx_bytes=None,
                     existing_urls=None, known_login_templates=None):
    """D-89 — preflight a bulk-import CSV/XLSX file before importing.

    Parses the file (csv_bulk.parse_import), then for every row builds
    the would-be site config and runs site_editor.validate_config on
    it, derives a name where blank exactly as the importer would, and
    flags in-file duplicate login_urls plus collisions with already-
    configured sites. Imports nothing — read-only.

    Pass either `path` (a .csv/.xlsx file under the BD home dir) or
    raw `text`/`xlsx_bytes`.
    """
    try:
        from bulk_downloader import csv_bulk as _cb
        from bulk_downloader import site_editor as _se
    except Exception as e:
        return {"tool": "import_preflight", "ok": False,
                "error": f"import modules unavailable: {e}"}

    filename = ""
    if path:
        filename = str(path)
        p = Path(path)
        ext = p.suffix.lower()
        if ext not in (".csv", ".xlsx"):
            return {"tool": "import_preflight", "ok": False,
                    "error": f"path must be a .csv or .xlsx file (got {ext})"}
        try:
            resolved = p.resolve()
            cwd = Path.cwd().resolve()
            if cwd not in resolved.parents and resolved != cwd:
                return {"tool": "import_preflight", "ok": False,
                        "error": "path must be inside the BD home dir"}
            if not resolved.is_file():
                return {"tool": "import_preflight", "ok": False,
                        "error": f"file not found: {path}"}
            if ext == ".xlsx":
                xlsx_bytes = resolved.read_bytes()
            else:
                text = resolved.read_text(encoding="utf-8")
        except Exception as e:
            return {"tool": "import_preflight", "ok": False,
                    "error": f"could not read {path}: {e}"}

    if text is None and xlsx_bytes is None:
        return {"tool": "import_preflight", "ok": False,
                "error": "give a path, or text=, or xlsx_bytes="}

    try:
        rows, parse_errors = _cb.parse_import(
            text=text, xlsx=xlsx_bytes, filename=filename)
    except Exception as e:
        return {"tool": "import_preflight", "ok": False,
                "error": f"parse failed: {e}"}

    existing = {str(u).strip().lower()
                for u in (existing_urls or []) if u}
    known_tpl = {str(t).strip().lower()
                 for t in (known_login_templates or []) if t}

    seen_urls = {}
    row_reports = []
    n_row_err = n_dup = n_collision = n_bad_tpl = 0
    for row in rows:
        line = row.get("line", "?")
        url = (row.get("login_url") or "").strip()
        name = (row.get("name") or "").strip() or _cb.derive_name(url)
        cfg = {"name": name, "login_url": url,
               "username": (row.get("username") or "").strip(),
               "password": (row.get("password") or "").strip()}
        try:
            v = _se.validate_config(cfg)
            errs = list(v.get("errors") or [])
            # Drop the "credentials set but the X selector is empty"
            # warnings: an import row structurally cannot carry login
            # selectors — they are resolved from template/login_template
            # downstream — so this warning is a false alarm here and
            # would otherwise fire on every single row, burying the
            # genuinely actionable findings.
            warns = [w for w in (v.get("warnings") or [])
                     if "selector ('" not in w]
        except Exception as e:
            errs, warns = [f"validate_config raised: {e}"], []

        norm = url.lower()
        dup_of = seen_urls.get(norm)
        if norm and dup_of is not None:
            errs.append(f"duplicate login_url — same as row {dup_of}")
            n_dup += 1
        elif norm:
            seen_urls[norm] = line

        collides = bool(norm and norm in existing)
        if collides:
            errs.append("login_url already belongs to a configured site")
            n_collision += 1

        tpl = (row.get("login_template") or "").strip()
        bad_tpl = bool(tpl and known_tpl and tpl.lower() not in known_tpl)
        if bad_tpl:
            warns.append(f"login_template '{tpl}' matches no known template")
            n_bad_tpl += 1

        if errs:
            n_row_err += 1
        row_reports.append({"line": line, "name": name,
                            "login_url": url, "ok": not errs,
                            "errors": errs, "warnings": warns})

    parse_err_list = [{"line": ln, "error": msg}
                      for ln, msg in (parse_errors or [])]
    ok_to_import = not parse_err_list and n_row_err == 0
    return {
        "tool": "import_preflight",
        "ok": True,
        "file": filename or ("inline-xlsx" if xlsx_bytes else "inline-csv"),
        "rows_parsed": len(rows),
        "parse_errors": parse_err_list,
        "rows_with_errors": n_row_err,
        "duplicate_urls_in_file": n_dup,
        "collisions_with_existing_sites": n_collision,
        "unknown_login_templates": n_bad_tpl,
        "safe_to_import": ok_to_import,
        "rows": row_reports,
    }



# ── 46. security cluster (U27: D-79 + D-80 + D-82 + D-78) ──────────
#
# Four read-only security-introspection tools. They do NOT replace
# the tools/ SAST/DAST pipeline (Bandit, Semgrep, pip-audit, etc.) —
# that stays the heavyweight scanner the operator runs locally
# (lesson E10). These are quick in-app checks + a viewer for that
# pipeline's output.
#   • dependency_audit (D-79) — parse requirements*.txt, report pins.
#   • secret_scan (D-80) — grep the tree for hardcoded-secret shapes.
#   • path_allowlist_test (D-82) — exercise the real _validate_path.
#   • sast_summary (D-78) — surface tools/sast_results/SUMMARY.txt.


_REQ_FILES = ["requirements.txt", "requirements-dev.txt"]


# Secret-shape patterns. Deliberately conservative — these match the
# SHAPE of a leaked credential, not every string. Each finding still
# needs a human read (lesson C3 — regex security heuristics over-flag).
_SECRET_PATTERNS = [
    ("aws_access_key", _sec_re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block",
     _sec_re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_api_key_assign", _sec_re.compile(
        r"(?i)(?:api[_-]?key|secret|passwd|password|token)\s*"
        r"[=:]\s*['\"][A-Za-z0-9/+_\-]{16,}['\"]")),
    ("slack_token", _sec_re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("bearer_literal",
     _sec_re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}")),
]


# Dirs a secret scan must skip. v3.66.749: DERIVED from the manifest
# verifier's "not source" canon (the previous re-typed copy had drifted
# 7 dirs behind -- screenshots/.pytest_cache/results/profiles/
# .mypy_cache/.hypothesis/state -- so on stash the scanner regex-read
# every .json/.txt line of exactly the runtime accretion the canon
# already declared out of scope; see /api/dev/secret_scan on L34's
# advisory slow-list). Scanner-specific extras on top: tests/ carries
# fake creds BY DESIGN; sast_results/dast_results are scanner output.
_SECRET_SKIP_DIRS = frozenset(_MANIFEST_EXCLUDE_DIRS) | {
    "tests", "sast_results", "dast_results"}



def dependency_audit():
    """D-79 — parse requirements*.txt and report each declared
    dependency and whether it is pinned (==), bounded (>=, ~=), or
    unpinned. Unpinned/loosely-bounded deps drift; this surfaces
    them. Read-only. (The CVE scan is pip-audit's job — run the
    tools/ pipeline for that; this is the pin-discipline check.)"""
    import os as _os
    here = str(_pkg_dir())
    repo = str(_repo_root())
    files = {}
    all_unpinned = []
    for rf in _REQ_FILES:
        path = _os.path.join(repo, rf)
        if not _os.path.exists(path):
            files[rf] = {"present": False}
            continue
        pinned, bounded, unpinned, commented = [], [], [], 0
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except Exception as e:
            files[rf] = {"present": True, "error": str(e)[:160]}
            continue
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                commented += 1
                continue
            # strip inline comment + env markers
            spec = line.split("#", 1)[0].split(";", 1)[0].strip()
            if not spec:
                continue
            name = _sec_re.split(r"[<>=~!\[ ]", spec, 1)[0].strip()
            if "==" in spec:
                pinned.append(name)
            elif any(op in spec for op in (">=", "<=", "~=", "!=",
                                           ">", "<")):
                bounded.append(name)
            else:
                unpinned.append(name)
                all_unpinned.append(f"{rf}:{name}")
        files[rf] = {
            "present": True,
            "pinned": sorted(pinned),
            "bounded": sorted(bounded),
            "unpinned": sorted(unpinned),
            "commented_out": commented,
            "total_active": len(pinned) + len(bounded) + len(unpinned),
        }
    return {
        "tool": "dependency_audit",
        "ok": True,
        "files": files,
        "unpinned_across_all": sorted(all_unpinned),
        "verdict": ("every active dependency is pinned or bounded"
                    if not all_unpinned
                    else f"{len(all_unpinned)} fully-unpinned "
                         f"dependency declaration(s)"),
    }



def _redact_secret_context(line):
    """Redacted preview of a line that matched a secret pattern.

    F-COREBD02-01: secret_scan must never echo the credential it flagged.
    We keep only the assignment target / label (the LHS of the first `=` or
    `:`) and mask the value; if there is no clear assignment we emit the
    shape's length + a short hash instead of any raw text.
    """
    s = (line or "").strip()
    for sep in ("=", ":"):
        idx = s.find(sep)
        if 0 < idx <= 40:
            return (s[:idx + 1] + " <redacted>")[:60]
    import hashlib as _hl
    digest = _hl.sha1(s.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:8]
    return f"<redacted secret shape, len={len(s)}, sha1={digest}>"


def secret_scan(max_findings=200):
    """D-80 — grep bulk_downloader/*.py + repo-root scripts for the
    SHAPE of a hardcoded secret (AWS keys, private-key blocks, API-key
    assignments, Slack/Bearer tokens). Skips tests/ (fake creds by
    design) and vendored/binary trees. Read-only. Every hit needs a
    human read — this flags shapes, it does not confirm a leak."""
    import os as _os
    here = str(_pkg_dir())
    repo = str(_repo_root())
    try:
        max_findings = max(1, min(int(max_findings), 1000))
    except (TypeError, ValueError):
        max_findings = 200
    findings = []
    scanned = 0
    truncated = False
    for root, dirs, names in _os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SECRET_SKIP_DIRS]
        for fn in names:
            if not fn.endswith((".py", ".sh", ".bat", ".json",
                                ".cfg", ".ini", ".txt", ".env")):
                continue
            full = _os.path.join(root, fn)
            rel = _os.path.relpath(full, repo)
            try:
                with open(full, encoding="utf-8",
                          errors="replace") as fh:
                    content = fh.read()
            except Exception:
                continue
            scanned += 1
            for lineno, line in enumerate(content.splitlines(), 1):
                for label, pat in _SECRET_PATTERNS:
                    if pat.search(line):
                        if len(findings) >= max_findings:
                            truncated = True
                            break
                        findings.append({
                            "file": rel, "line": lineno,
                            "pattern": label,
                            # F-COREBD02-01: redact the value so the scanner's
                            # own output never discloses the credential it flagged
                            "context": _redact_secret_context(line),
                        })
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break
    return {
        "tool": "secret_scan",
        "ok": True,
        "files_scanned": scanned,
        "finding_count": len(findings),
        "findings": findings,
        "truncated": truncated,
        "verdict": ("no hardcoded-secret shapes found"
                    if not findings
                    else f"{len(findings)} secret-shaped string(s) — "
                         f"each needs a manual review (may be false "
                         f"positives)"),
    }



def path_allowlist_test(test_paths=None):
    """D-82 — exercise the real app._validate_path against a battery
    of probe paths, so the path-allowlist behaviour is observable.
    Read-only — _validate_path neither reads nor writes the FS.
    Reports, for each path, whether it is accepted and why."""
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "path_allowlist_test", "ok": False,
                "error": f"app module unavailable: {e}"}
    if not hasattr(_app, "_validate_path"):
        return {"tool": "path_allowlist_test", "ok": False,
                "error": "_validate_path not found in app"}
    default_probes = [
        "/tmp/bd_probe_download",
        "/home/mboyle/BulkDownloader/downloads",
        "../escape/attempt",
        "relative/path",
        "/etc/../etc/passwd",
        "",
    ]
    probes = test_paths if isinstance(test_paths, list) else \
        default_probes
    allowlist = list(_app._app_cfg.get("path_allowlist") or [])
    results = []
    for p in probes:
        try:
            ok, detail = _app._validate_path(p, "probe")
        except Exception as e:
            results.append({"path": p, "error": str(e)[:160]})
            continue
        results.append({"path": p, "accepted": bool(ok),
                         "detail": detail if not ok else "ok"})
    return {
        "tool": "path_allowlist_test",
        "ok": True,
        "allowlist_configured": bool(allowlist),
        "allowlist_roots": allowlist,
        "allowlist_mode": ("enforcing — only paths under a root accepted"
                           if allowlist
                           else "permissive — empty allowlist accepts "
                                "any absolute path (documented design)"),
        "results": results,
    }



def sast_summary():
    """D-78 — surface the tools/ SAST/DAST pipeline's latest SUMMARY
    output (tools/sast_results/SUMMARY.txt, tools/dast_results/
    SUMMARY.txt) if the operator has run it. Read-only — a VIEWER,
    not a scanner; it does not run Bandit/Semgrep itself."""
    import os as _os
    here = str(_pkg_dir())
    repo = str(_repo_root())
    out = {}
    for kind, sub in (("sast", "tools/sast_results"),
                      ("dast", "tools/dast_results")):
        summ = _os.path.join(repo, sub, "SUMMARY.txt")
        if not _os.path.exists(summ):
            out[kind] = {"present": False,
                         "hint": f"run tools/{kind}.sh to generate"}
            continue
        try:
            with open(summ, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            mtime = _os.path.getmtime(summ)
        except Exception as e:
            out[kind] = {"present": True, "error": str(e)[:160]}
            continue
        out[kind] = {
            "present": True,
            "path": _os.path.join(sub, "SUMMARY.txt"),
            "modified_epoch": round(mtime, 1),
            # cap the surfaced text so a huge report can't bloat JSON
            "summary": text[:8000],
            "truncated": len(text) > 8000,
        }
    have = [k for k, v in out.items() if v.get("present")]
    return {
        "tool": "sast_summary",
        "ok": True,
        "reports": out,
        "verdict": (f"{', '.join(have)} report(s) available"
                    if have
                    else "no SAST/DAST reports — run the tools/ "
                         "pipeline locally and re-check"),
    }



# ── 56. manual-takeover log + CSRF-token inspector (T6: D-28 + D-30)─
#
# D-28 — manual-takeover log. There is NO dedicated persisted store
# of manual-login / takeover events — but the structured app log
# already captures them (a _StreamTee routes sys.stderr writes into
# logs/bulk_downloader.log alongside the real logger). So this is a
# purpose-built FILTERED VIEW of that log: it scans for the takeover
# vocabulary and classifies each line into a lifecycle phase. It is
# not a rebuild of the generic log_search (single-substring) tool —
# it knows the takeover keyword set and the phase taxonomy.
#
# D-30 — CSRF-token inspector. NOT csrf_coverage (U2), which audits
# whether every state-changing route ENFORCES CSRF. This inspects
# the token MECHANISM itself: the double-submit HMAC scheme, the
# process-local key state, the header/cookie names, and — given a
# session cookie — the derived token, so the operator can confirm a
# client is sending the right value. Read-only.

# Keyword groups that classify a log line into a takeover lifecycle
# phase. Checked in order; first hit wins. Lower-cased substring match.
_TAKEOVER_PHASES = [
    ("aborted",  ("manual login abort", "abandoned the manual",
                  "cancel_manual_login", "manual login cancel")),
    ("finished", ("finish_manual_login", "manual login done",
                  "manual login finished", "manual login complete")),
    ("error",    ("manual_dl: session thread error",
                  "manual_dl: session never became ready",
                  "manual_dl: session construction raised",
                  "takeover persist failed",
                  "takeover classify failed")),
    ("started",  ("manual login started for",
                  "start_manual_login", "start_manual_download")),
    ("cookies",  ("post-takeover cookie", "manual_dl: harvest",
                  "manual_dl: cookie")),
]

# Any line touching one of these is in scope at all.
_TAKEOVER_KEYWORDS = ("manual login", "manual_login", "manual_dl",
                      "takeover")



def _classify_takeover_line(line_lower):
    """Return the lifecycle phase for a takeover log line, or
    'activity' for an in-scope line that matches no specific phase."""
    for phase, keys in _TAKEOVER_PHASES:
        if any(k in line_lower for k in keys):
            return phase
    return "activity"



def manual_takeover_log(limit=200):
    """D-28 — a classified timeline of manual-login / takeover events,
    extracted from logs/bulk_downloader.log (read-only).

    Newest match first. Reads only the tail of the log (last 4 MB) so
    a large rotated log is never loaded whole — same bound as
    log_search. Each entry carries a lifecycle `phase`.
    """
    import re
    try:
        limit = max(1, min(int(limit), 2000))
    except Exception:
        limit = 200
    log_path = Path("logs") / "bulk_downloader.log"
    if not log_path.exists():
        return {"tool": "manual_takeover_log", "ok": True,
                "path": str(log_path), "exists": False,
                "events": [], "match_count": 0,
                "verdict": "log file not present — no events"}
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            window = min(size, 4 * 1024 * 1024)
            fh.seek(size - window)
            data = fh.read(window)
        lines = data.decode("utf-8", "replace").splitlines()
        if window < size and lines:
            lines = lines[1:]  # drop a partial first line
    except Exception as e:
        return {"tool": "manual_takeover_log", "ok": False,
                "error": str(e)[:200]}

    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+"
                     r"([A-Z]+)\s+([\w.]+):\s?(.*)$")
    events = []
    phase_counts: dict = {}
    for raw in reversed(lines):
        line = raw.rstrip("\n")
        low = line.lower()
        if not any(k in low for k in _TAKEOVER_KEYWORDS):
            continue
        m = pat.match(line)
        ts, lvl, lg, msg = (m.groups() if m
                            else ("", "", "", line))
        phase = _classify_takeover_line(low)
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        events.append({"ts": ts, "level": lvl, "logger": lg,
                        "phase": phase, "message": msg})
        if len(events) >= limit:
            break
    return {
        "tool": "manual_takeover_log",
        "ok": True,
        "path": str(log_path),
        "exists": True,
        "scanned_lines": len(lines),
        "match_count": len(events),
        "phase_counts": phase_counts,
        "events": events,
        "verdict": (f"{len(events)} manual-takeover log line(s); "
                    f"phases: {phase_counts or 'none'}"),
    }



def csrf_token_inspect(app, session_cookie=None):
    """D-30 — inspect the CSRF token MECHANISM (read-only). Reports
    the double-submit HMAC scheme, the process-local key state, the
    header/cookie names, and — if a session cookie is supplied — the
    CSRF token derived from it (so a client value can be confirmed).

    Distinct from csrf_coverage, which audits route enforcement. This
    inspects how the token is built and validated, not which routes
    require it. The signing key itself is never emitted.
    """
    out = {"tool": "csrf_token_inspect", "ok": True}
    app_module = None
    try:
        import sys as _sys
        app_module = _sys.modules.get("bulk_downloader.app")
    except Exception:
        app_module = None

    # the process-local HMAC key — report only its presence + length,
    # never the bytes themselves
    key = getattr(app_module, "_csrf_key", None)
    out["scheme"] = "double-submit HMAC-SHA256 (token derived from " \
                    "the session cookie)"
    out["key_present"] = isinstance(key, (bytes, bytearray))
    out["key_bytes"] = len(key) if isinstance(
        key, (bytes, bytearray)) else None
    out["key_is_process_local"] = True
    out["token_length"] = 32  # _csrf_token_for truncates to 32 hex
    out["session_cookie_name"] = "bd_session"
    out["csrf_header_name"] = "X-CSRF-Token"
    out["csrf_seed_endpoint"] = "/api/csrf"
    out["enforced_for"] = ["POST", "PUT", "PATCH", "DELETE"]
    out["bypasses"] = [
        "GET/HEAD/OPTIONS (read-only / preflight)",
        "Bearer-token requests (CLI — bearer is itself a secret)",
        "requests with no session cookie",
        "/api/pair/redeem (session bootstrap)",
    ]

    # if a session cookie is supplied, derive its CSRF token so the
    # operator can compare against what a client is sending
    if session_cookie:
        derive = getattr(app_module, "_csrf_token_for", None)
        if callable(derive):
            try:
                out["derived_csrf_token"] = derive(str(session_cookie))
                out["derived_for_session"] = True
            except Exception as e:
                out["derive_error"] = str(e)[:160]
        else:
            out["derive_error"] = "_csrf_token_for unavailable"
    else:
        out["derived_for_session"] = False

    out["verdict"] = (
        "CSRF token mechanism healthy: HMAC key present, "
        "double-submit scheme active"
        if out["key_present"] else
        "CSRF token mechanism PROBLEM: no HMAC key found")
    return out



# ── T43 / D-47 — TLS / cert checker ────────────────────────────────

def _extract_hosts_from_site_configs(site_configs):
    """Best-effort host extraction from site configs. We look at
    login_url, start_url, home_url, base_url — whichever the site
    actually has. Returns a sorted list of unique hostnames."""
    from urllib.parse import urlparse
    hosts: set = set()
    if not isinstance(site_configs, dict):
        return []
    for sid, cfg in site_configs.items():
        if not isinstance(cfg, dict):
            continue
        for key in ("login_url", "start_url", "home_url", "base_url"):
            url = cfg.get(key)
            if not isinstance(url, str) or not url:
                continue
            try:
                p = urlparse(url)
                if p.scheme == "https" and p.hostname:
                    hosts.add(p.hostname)
            except Exception:
                continue
    return sorted(hosts)



def tls_cert_check(*, hosts=None, site_configs=None, port=443,
                    timeout=5.0):
    """T43 / D-47 — connect to each host, walk the cert chain, report
    expiry / SAN / issuer.

    Hosts to check: explicit `hosts` list wins; otherwise derived from
    site_configs (HTTPS URLs only). The route should pass site_configs.

    OPT-IN by default in the sense that the caller must pass hosts
    OR site_configs — never auto-scans. Per-host timeout is bounded.

    Returns {tool, ok, port, hosts_checked, results[], verdict}.
    """
    import socket as _socket
    import ssl as _ssl
    import time as _time
    from datetime import datetime as _datetime
    out = {
        "tool": "tls_cert_check",
        "ok": True,
        "port": int(port) if isinstance(port, int) else 443,
        "hosts_checked": [],
        "results": [],
        "verdict": "",
    }
    if hosts is not None:
        if not isinstance(hosts, list):
            out["ok"] = False
            out["verdict"] = "hosts must be a list of hostnames"
            return out
        host_list = [str(h) for h in hosts
                     if isinstance(h, str) and h]
    else:
        host_list = _extract_hosts_from_site_configs(site_configs)
    out["hosts_checked"] = host_list
    if not host_list:
        out["verdict"] = (
            "no hosts to check (pass hosts= or "
            "supply site_configs with HTTPS login_url/start_url)")
        return out
    try:
        timeout_f = float(timeout)
        if timeout_f <= 0 or timeout_f > 30:
            timeout_f = 5.0
    except (TypeError, ValueError):
        timeout_f = 5.0
    ctx = _ssl.create_default_context()
    now = _datetime.utcnow()
    expiring_soon = 0
    expired = 0
    failed = 0
    for host in host_list:
        entry = {"host": host, "ok": False}
        sock = None
        try:
            sock = _socket.create_connection(
                (host, out["port"]), timeout=timeout_f)
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            failed += 1
            out["results"].append(entry)
            continue
        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
        # cert is a dict from getpeercert(). Pull expiry, SANs, issuer.
        entry["ok"] = True
        not_after_str = cert.get("notAfter", "")
        not_before_str = cert.get("notBefore", "")
        entry["not_after"] = not_after_str
        entry["not_before"] = not_before_str
        # parse "May 20 12:00:00 2027 GMT"
        try:
            not_after = _datetime.strptime(
                not_after_str, "%b %d %H:%M:%S %Y %Z")
            days_left = (not_after - now).days
            entry["days_until_expiry"] = days_left
            if days_left < 0:
                expired += 1
                entry["status"] = "EXPIRED"
            elif days_left < 30:
                expiring_soon += 1
                entry["status"] = "expiring_soon"
            else:
                entry["status"] = "ok"
        except Exception:
            entry["days_until_expiry"] = None
            entry["status"] = "unknown_expiry"
        # SANs
        sans = []
        for typ, val in cert.get("subjectAltName", ()):
            if typ in ("DNS", "IP Address"):
                sans.append(val)
        entry["sans"] = sans
        # Issuer (it's a tuple of tuples of tuples)
        issuer = {}
        for rdn in cert.get("issuer", ()):
            for k, v in rdn:
                issuer[k] = v
        entry["issuer_common_name"] = issuer.get("commonName", "")
        entry["issuer_organization"] = issuer.get(
            "organizationName", "")
        # SAN-match: hostname against the cert's SAN list. ssl already
        # enforced this at handshake (we'd have failed) — but we report
        # explicitly so the operator can see WHY a wildcard matches.
        entry["san_matches_host"] = any(
            _san_matches(host, san) for san in sans)
        out["results"].append(entry)
    valid = sum(1 for r in out["results"] if r["ok"])
    out["verdict"] = (
        f"{valid}/{len(out['results'])} host(s) OK; "
        f"{failed} fail, {expiring_soon} expiring<30d, "
        f"{expired} expired")
    return out



def _san_matches(host, san):
    """Cheap wildcard-aware match. The real validation already
    happened in ssl.wrap_socket; this is for the report."""
    if not host or not san:
        return False
    host = host.lower()
    san = san.lower()
    if san == host:
        return True
    if san.startswith("*."):
        suffix = san[1:]  # ".example.com"
        # Wildcard matches exactly one label per RFC 6125
        if host.endswith(suffix):
            prefix = host[: -len(suffix)]
            if prefix and "." not in prefix:
                return True
    return False
