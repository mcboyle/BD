"""dev_suite.capture_diag -- capture/download diagnostics

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
    _resolve_all_site_configs, _resolve_site_config)



# ── 36. cookie-jar inspector / age report / auth-heuristic (U17) ───
#
# Three read-only views over each site's live cookie jar
# (SiteRunner.cookies). runner.cookie_info() already gives a one-line
# per-site summary; these go per-cookie and fleet-wide:
#   • cookie_jar_inspect (D-21) — every cookie's name/domain/path/
#     flags, with the value LENGTH only — cookie values are session
#     secrets and are never emitted.
#   • cookie_age_report (D-22) — per-cookie expiry: session vs expired
#     vs expiring-within-24h, jar refresh age, soonest to expire.
#   • auth_cookie_test (D-29) — runs login._looks_authenticated on the
#     jar and shows which cookies drove the verdict.

def _iter_site_jars(runners, site_id):
    """Yield (sid, cookies_list, saved_at) for each live runner — or
    just the one when site_id is given."""
    runners = runners or {}
    targets = ([site_id] if site_id is not None else sorted(runners))
    for sid in targets:
        rn = runners.get(sid)
        if rn is None:
            continue
        yield (sid, list(getattr(rn, "cookies", []) or []),
               getattr(rn, "cookie_saved_at", 0.0) or 0.0)



def _cookie_expiry_label(c, now):
    """Return (label, seconds_left_or_None) for one cookie — label is
    one of session / expired / expiring_soon / ok."""
    exp = c.get("expires", 0) or c.get("expirationDate", 0)
    try:
        exp = float(exp)
    except (TypeError, ValueError):
        exp = 0
    if not exp or exp < 0:        # 0 / -1 / missing == session cookie
        return "session", None
    left = exp - now
    if left <= 0:
        return "expired", left
    if left < 86400:
        return "expiring_soon", left
    return "ok", left



def cookie_jar_inspect(runners=None, site_id=None):
    """D-21 — per-cookie structure of each site's live cookie jar:
    name, domain, path, httpOnly/secure/sameSite, and the value
    LENGTH (never the value). Read-only."""
    sites = []
    for sid, cookies, _saved in _iter_site_jars(runners, site_id):
        rows, domains = [], set()
        for c in cookies:
            dom = str(c.get("domain", ""))
            if dom:
                domains.add(dom)
            rows.append({
                "name": str(c.get("name", "")),
                "domain": dom,
                "path": str(c.get("path", "/")),
                "http_only": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
                "same_site": c.get("sameSite") or "",
                "value_length": len(str(c.get("value", ""))),
            })
        sites.append({"site_id": sid, "cookie_count": len(rows),
                      "distinct_domains": sorted(domains),
                      "cookies": rows})
    if site_id is not None and not sites:
        return {"tool": "cookie_jar_inspect", "ok": False,
                "error": f"site '{site_id}' has no live runner"}
    return {"tool": "cookie_jar_inspect", "ok": True,
            "sites_with_jars": len(sites),
            "total_cookies": sum(s["cookie_count"] for s in sites),
            "sites": sites}



def cookie_age_report(runners=None, site_id=None):
    """D-22 — cookie expiry per site: counts of session / expired /
    expiring-within-24h cookies, the jar's refresh age, and the
    soonest cookie to expire. Read-only."""
    import time as _t
    try:
        from bulk_downloader.cookies import cookie_age_str
    except Exception:
        cookie_age_str = None
    now = _t.time()
    sites = []
    for sid, cookies, saved_at in _iter_site_jars(runners, site_id):
        per = {"session": 0, "expired": 0, "expiring_soon": 0, "ok": 0}
        soonest = None
        for c in cookies:
            label, left = _cookie_expiry_label(c, now)
            per[label] += 1
            if left is not None and left > 0:
                if soonest is None or left < soonest[1]:
                    soonest = (str(c.get("name", "")), left)
        sites.append({
            "site_id": sid,
            "cookie_count": len(cookies),
            "session_cookies": per["session"],
            "expired_cookies": per["expired"],
            "expiring_within_24h": per["expiring_soon"],
            "jar_age": (cookie_age_str(saved_at)
                        if cookie_age_str else ""),
            "soonest_to_expire": (
                {"name": soonest[0],
                 "hours_left": round(soonest[1] / 3600.0, 1)}
                if soonest else None),
        })
    if site_id is not None and not sites:
        return {"tool": "cookie_age_report", "ok": False,
                "error": f"site '{site_id}' has no live runner"}
    return {"tool": "cookie_age_report", "ok": True,
            "sites_with_jars": len(sites),
            "sites_with_expired_cookies":
                sum(1 for s in sites if s["expired_cookies"]),
            "sites": sites}



def auth_cookie_test(runners=None, site_id=None):
    """D-29 — run login._looks_authenticated against each site's live
    cookie jar and show which cookies drove the verdict: auth-named
    hits and substantial-cookie count. Makes the v3.62.4 cookie-based
    login-success heuristic inspectable. Read-only — cookie values are
    classified, never emitted."""
    try:
        from bulk_downloader import login as _login
    except Exception as e:
        return {"tool": "auth_cookie_test", "ok": False,
                "error": f"login module unavailable: {e}"}
    auth_hints = tuple(getattr(_login, "_AUTH_COOKIE_HINTS", ()))
    not_hints = tuple(getattr(_login, "_NOT_AUTH_COOKIE_HINTS", ()))
    sites = []
    for sid, cookies, _saved in _iter_site_jars(runners, site_id):
        try:
            ok, reason = _login._looks_authenticated(cookies)
        except Exception as e:
            sites.append({"site_id": sid, "error": str(e)[:160]})
            continue
        non_empty = [c for c in cookies if c.get("value")]
        auth_named = []
        for c in non_empty:
            nm = str(c.get("name", "")).lower()
            if any(b in nm for b in not_hints):
                continue
            if any(h in nm for h in auth_hints):
                auth_named.append(str(c.get("name", "")))
        substantial = sum(1 for c in non_empty
                          if len(str(c.get("value", ""))) > 8)
        sites.append({
            "site_id": sid,
            "looks_authenticated": bool(ok),
            "reason": reason,
            "cookie_count": len(cookies),
            "auth_named_cookies": auth_named,
            "substantial_cookies": substantial,
        })
    if site_id is not None and not sites:
        return {"tool": "auth_cookie_test", "ok": False,
                "error": f"site '{site_id}' has no live runner"}
    return {"tool": "auth_cookie_test", "ok": True,
            "sites_tested": len(sites),
            "sites_authenticated":
                sum(1 for s in sites if s.get("looks_authenticated")),
            "sites": sites}



# ── 37. login-template dry-run / credential resolver (U18) ─────────
#
# D-24 + D-26 — the login/credential pair.
#   • login_template_dry_run (D-24) — simulate applying a login
#     template to a site: runs the REAL learn.merge_learned on a deep
#     copy of the config and reports the before/after learned.login.
#     The site config is never mutated, nothing is persisted.
#   • credential_resolver (D-26) — per-site credential-reference
#     report: each password field's kind (cred_reference / plaintext
#     / empty), the vault label, and whether a @cred reference
#     resolves. Resolution is an existence check against the vault key
#     index — no secret is decrypted, no value is ever emitted.

def login_template_dry_run(login_template_id, site_id=None, runners=None):
    """D-24 — dry-run applying a login template to a site. Shows what
    learned.login would become, via the same learn.merge_learned the
    real apply uses, on a throwaway deep copy. Read-only."""
    if not login_template_id:
        return {"tool": "login_template_dry_run", "ok": False,
                "error": "login_template_id is required"}
    if not site_id:
        return {"tool": "login_template_dry_run", "ok": False,
                "error": "site_id is required — a dry-run targets a site"}
    try:
        from bulk_downloader import login_templates_data as _lt
        from bulk_downloader.learn import merge_learned
    except Exception as e:
        return {"tool": "login_template_dry_run", "ok": False,
                "error": f"login-template modules unavailable: {e}"}
    tpl = _lt.get_login_template(login_template_id)
    if not tpl:
        return {"tool": "login_template_dry_run", "ok": False,
                "error": f"unknown login template: {login_template_id}"}
    cfg, cfg_source = _resolve_site_config(site_id, runners)
    if cfg is None:
        return {"tool": "login_template_dry_run", "ok": False,
                "error": f"site '{site_id}' not found"}
    tpl_login = tpl.get("login") or {}
    has_selectors = any(tpl_login.get(k) for k in
                        ("user_field", "pass_field", "submit_btn"))

    def _learned_login(d):
        learned = d.get("learned")
        return learned.get("login") if isinstance(learned, dict) else None

    before = _learned_login(cfg)
    import copy as _copy
    sim = _copy.deepcopy(cfg)
    if has_selectors:
        try:
            merge_learned(sim, tpl_login, kind="login")
        except Exception as e:
            return {"tool": "login_template_dry_run", "ok": False,
                    "error": f"merge_learned raised: {e}"}
    after = _learned_login(sim)
    return {
        "tool": "login_template_dry_run",
        "ok": True,
        "site_id": site_id,
        "site_config_source": cfg_source,
        "login_template_id": login_template_id,
        "login_template_name": tpl.get("name", login_template_id),
        "template_selectors": {k: tpl_login.get(k) for k in
                               ("user_field", "pass_field", "submit_btn")
                               if tpl_login.get(k)},
        "template_has_selectors": has_selectors,
        "learned_login_before": before,
        "learned_login_after": after,
        "would_change": before != after,
        "would_set_applied_login_template": login_template_id,
        "note": ("no-op — this template carries no login selectors"
                 if not has_selectors else
                 "applying would merge these selectors into "
                 "learned.login and set applied_login_template; "
                 "auto-login still falls back to manual capture on "
                 "runtime failure"),
    }



def _classify_credential(value):
    """Return (kind, label) for a credential value. kind is one of
    cred_reference / plaintext / empty; label is set only for refs."""
    from bulk_downloader import secrets_store as _ss
    prefix = getattr(_ss, "CRED_PREFIX", "@cred:")
    if not isinstance(value, str) or not value.strip():
        return "empty", None
    if value.startswith(prefix):
        return "cred_reference", value[len(prefix):]
    return "plaintext", None



def credential_resolver(runners=None, site_id=None):
    """D-26 — per-site credential-reference report: each password
    field's kind, vault label, and whether a @cred reference resolves
    (existence check against the vault key index — nothing decrypted,
    no value emitted). Read-only."""
    try:
        from bulk_downloader import secrets_store as _ss
        backend_name = _ss.get_backend_name()
        vault_labels = set(_ss.get_backend().list_keys() or [])
        vault_ok = True
    except Exception as e:
        backend_name = f"unknown ({str(e)[:80]})"
        vault_labels, vault_ok = set(), False
    if site_id is not None:
        cfg, source = _resolve_site_config(site_id, runners)
        if cfg is None:
            return {"tool": "credential_resolver", "ok": False,
                    "error": f"site '{site_id}' not found"}
        configs = {site_id: cfg}
    else:
        configs, source = _resolve_all_site_configs(runners)
    sites, n_unresolved = [], 0
    for sid in sorted(configs):
        cfg = configs[sid] or {}
        fields = [("password", cfg.get("password"))]
        for i, acc in enumerate(cfg.get("accounts") or []):
            if isinstance(acc, dict):
                fields.append((f"accounts[{i}].password",
                               acc.get("password")))
        creds = []
        for field, val in fields:
            kind, label = _classify_credential(val)
            resolves = True
            if kind == "cred_reference":
                resolves = vault_ok and label in vault_labels
                if not resolves:
                    n_unresolved += 1
            rec = {"field": field, "kind": kind, "resolves": resolves}
            if label is not None:
                rec["vault_label"] = label
            creds.append(rec)
        sites.append({"site_id": sid, "credentials": creds})
    return {
        "tool": "credential_resolver",
        "ok": True,
        "config_source": source,
        "vault_backend": backend_name,
        "vault_readable": vault_ok,
        "sites_total": len(sites),
        "unresolved_references": n_unresolved,
        "sites": sites,
        "verdict": ("all credential references resolve"
                    if n_unresolved == 0 else
                    f"{n_unresolved} @cred reference(s) point at a "
                    "label not in the vault"),
    }



# ── 38. extractor capability matrix / fast-path sim (U19) ──────────
#
# D-31 + D-39 — both read extractors._REGISTRY (the EAF-library/host
# map). extractors.installed_libraries() already gives a bare
# site->installed bool; these scope around it:
#   • extractor_matrix (D-31) — the full matrix: per site_id the EAF
#     library, host pattern, adapter, and installed state.
#   • extractor_fastpath_sim (D-39) — for one URL, whether the
#     library-extractor fast path would fire, and if not, why.

def extractor_matrix():
    """D-31 — the library-extractor capability matrix: every
    registered site_id with its EAF library, host pattern, adapter,
    and whether that library is installed. Read-only."""
    try:
        from bulk_downloader import extractors as _ex
    except Exception as e:
        return {"tool": "extractor_matrix", "ok": False,
                "error": f"extractors module unavailable: {e}"}
    rows = []
    for sid in sorted(getattr(_ex, "_REGISTRY", {})):
        try:
            libname, host_re, adapter = _ex._REGISTRY[sid]
            installed = bool(_ex.is_available(sid))
        except Exception as e:
            rows.append({"site_id": sid, "error": str(e)[:120]})
            continue
        rows.append({
            "site_id": sid,
            "library": libname,
            "host_pattern": getattr(host_re, "pattern", str(host_re)),
            "adapter": adapter,
            "library_installed": installed,
        })
    installed_n = sum(1 for r in rows if r.get("library_installed"))
    return {
        "tool": "extractor_matrix",
        "ok": True,
        "registered_extractors": len(rows),
        "libraries_installed": installed_n,
        "libraries_missing": len(rows) - installed_n,
        "extractors": rows,
        "note": ("a missing library is not an error — "
                 "use_library_extractor fails open and the URL falls "
                 "through to the teach path"),
    }



def extractor_fastpath_sim(url, site_id=None, runners=None):
    """D-39 — for one URL, simulate the library-extractor fast-path
    decision: is a registered extractor's host pattern matched, and
    is its library installed? With an optional site_id it also reads
    that site's use_library_extractor flag for a definitive verdict.
    Downloads nothing, mutates nothing. Read-only."""
    if not isinstance(url, str) or not url.strip():
        return {"tool": "extractor_fastpath_sim", "ok": False,
                "error": "url is required"}
    url = url.strip()
    try:
        from bulk_downloader import extractors as _ex
    except Exception as e:
        return {"tool": "extractor_fastpath_sim", "ok": False,
                "error": f"extractors module unavailable: {e}"}
    try:
        matched_sid = _ex.is_supported_url(url)
    except Exception as e:
        return {"tool": "extractor_fastpath_sim", "ok": False,
                "error": f"is_supported_url raised: {e}"}

    cfg_enabled = None
    if site_id:
        cfg, _src = _resolve_site_config(site_id, runners)
        if cfg is not None:
            cfg_enabled = bool(cfg.get("use_library_extractor", False))

    if not matched_sid:
        return {
            "tool": "extractor_fastpath_sim", "ok": True, "url": url,
            "matched": False, "extractor_ready": False,
            "config_use_library_extractor": cfg_enabled,
            "fast_path_would_fire": False,
            "verdict": ("no registered extractor matches this host — "
                        "the URL takes the Playwright teach path"),
        }
    libname, host_re, adapter = _ex._REGISTRY[matched_sid]
    installed = bool(_ex.is_available(matched_sid))
    extractor_ready = installed
    would_fire = (extractor_ready and cfg_enabled
                  if cfg_enabled is not None else None)
    if not installed:
        verdict = (f"extractor {matched_sid}/{libname} matches but the "
                   "library is not installed — extract() returns "
                   "library_not_installed and the URL falls through "
                   "to the teach path")
    elif cfg_enabled is False:
        verdict = (f"extractor {matched_sid}/{libname} is installed, "
                   "but this site has use_library_extractor off — the "
                   "fast path is skipped")
    elif cfg_enabled:
        verdict = (f"the fast path WOULD fire: {matched_sid}/{libname} "
                   "matches, the library is installed, and the site "
                   "has use_library_extractor on")
    else:
        verdict = (f"extractor {matched_sid}/{libname} matches and the "
                   "library is installed — the fast path fires IF the "
                   "site config has use_library_extractor enabled")
    return {
        "tool": "extractor_fastpath_sim", "ok": True, "url": url,
        "matched": True, "matched_site_id": matched_sid,
        "library": libname, "adapter": adapter,
        "library_installed": installed,
        "extractor_ready": extractor_ready,
        "config_use_library_extractor": cfg_enabled,
        "fast_path_would_fire": would_fire,
        "verdict": verdict,
    }



# ── 39. ffmpeg command preview / resolution-scoring test (U20) ─────
#
# D-33 + D-34 — the pipeline-preview pair, both preview a pipeline
# step without executing it:
#   • ffmpeg_command_preview (D-33) — the exact ffmpeg argv
#     hls_downloader would build for a streaming URL, via the real
#     _build_ffmpeg_cmd. Nothing is run.
#   • resolution_scoring_test (D-34) — exercises BOTH resolution code
#     paths (INV-005: detect.res_score/res_label and heuristic_
#     scoring.detect_resolution_tier are two separate hand-maintained
#     tables) and shows their output side by side so a desync is
#     visible.

def ffmpeg_command_preview(url, output_name="preview.mp4",
                           user_agent="", referer=""):
    """D-33 — show the ffmpeg argv hls_downloader would build for a
    streaming URL, without running it. Uses the real _build_ffmpeg_cmd
    so the preview is exactly what download() would assemble.
    Read-only."""
    if not isinstance(url, str) or not url.strip():
        return {"tool": "ffmpeg_command_preview", "ok": False,
                "error": "url is required"}
    url = url.strip()
    try:
        from bulk_downloader import hls_downloader as _hls
    except Exception as e:
        return {"tool": "ffmpeg_command_preview", "ok": False,
                "error": f"hls_downloader unavailable: {e}"}
    found_path = _hls._find_ffmpeg()
    ffmpeg_path = found_path or "ffmpeg"
    try:
        argv = _hls._build_ffmpeg_cmd(
            ffmpeg_path, url, output_name or "preview.mp4",
            user_agent=user_agent, referer=referer)
    except Exception as e:
        return {"tool": "ffmpeg_command_preview", "ok": False,
                "error": f"_build_ffmpeg_cmd raised: {e}"}
    try:
        is_hls = bool(_hls.is_hls_url(url))
        is_dash = bool(_hls.is_dash_url(url))
    except Exception:
        is_hls = is_dash = False
    import shlex as _shlex
    return {
        "tool": "ffmpeg_command_preview",
        "ok": True,
        "url": url,
        "ffmpeg_on_path": found_path is not None,
        "looks_like_hls": is_hls,
        "looks_like_dash": is_dash,
        "argv": argv,
        "command": _shlex.join(argv),
        "note": ("input options precede -i, output options follow it, "
                 "the output path is last (the ffmpeg argv-ordering "
                 "invariant). A preview only — nothing was run."
                 + ("" if found_path else
                    " ffmpeg is not on PATH here; the argv is still "
                    "structurally valid.")),
    }



def resolution_scoring_test(text=None):
    """D-34 — grade the AI and detection resolution-label readers.

    The heuristic fields remain in each row for the existing INV-005
    inspection surface.  The verdict, however, is now about the product paths
    named by the gate: ``aiassist.normalize_resolution`` and
    ``detect.res_score/res_label``.  A missing reader measurement is UNKNOWN,
    never an empty value laundered into OK.  Read-only; the fixed probes all
    use AI's deterministic path and cannot contact a model.
    """
    try:
        from bulk_downloader import detect as _d
        from bulk_downloader import aiassist as _ai
        from bulk_downloader import heuristic_scoring as _hs
    except Exception as e:
        return {"tool": "resolution_scoring_test", "ok": False,
                "verdict": "UNKNOWN",
                "diagnostic": f"UNKNOWN: resolution readers unavailable: {e}",
                "error": f"resolution modules unavailable: {e}"}

    def _probe(s):
        detect_error = None
        try:
            score = _d.res_score(s)
        except Exception as exc:
            score = -1
            detect_error = str(exc) or type(exc).__name__
        detect_found = (isinstance(score, int) and not isinstance(score, bool)
                        and score > 0)
        detect_label = ""
        if detect_found:
            try:
                detect_label = _d.res_label(score)
            except Exception as exc:
                detect_label = ""
                detect_error = str(exc) or type(exc).__name__
        if not detect_label and detect_error is None:
            detect_error = "no measured label"

        ai_error = None
        ai_result = {}
        try:
            ai_result = _ai.normalize_resolution(s, allow_model=False)
        except Exception as exc:
            ai_error = str(exc) or type(exc).__name__
        ai_label = ai_result.get("label") if isinstance(ai_result, dict) else None
        ai_found = bool(
            isinstance(ai_result, dict) and ai_result.get("ok") is True
            and isinstance(ai_label, str) and ai_label
        )
        if not ai_found and ai_error is None:
            ai_error = str(ai_result.get("error") or "no measured label") \
                if isinstance(ai_result, dict) else "invalid reader result"

        try:
            tier, hs_label = _hs.detect_resolution_tier(s)
        except Exception:
            tier, hs_label = 0, ""
        hs_found = isinstance(tier, int) and tier > 0
        return {
            "input": s,
            "detect_res_score": score,
            "detect_res_label": detect_label,
            "detect_error": detect_error,
            "ai_resolution": (ai_result.get("resolution")
                              if isinstance(ai_result, dict) else None),
            "ai_label": ai_label,
            "ai_via": (ai_result.get("via")
                       if isinstance(ai_result, dict) else None),
            "ai_error": ai_error,
            "both_readers_measured": detect_found and bool(detect_label) and ai_found,
            "labels_agree": ((detect_label == ai_label)
                             if detect_found and detect_label and ai_found else None),
            "heuristic_tier": tier,
            "heuristic_label": hs_label,
            "both_paths_detect": detect_found and hs_found,
            "only_one_path_detects": detect_found != hs_found,
        }

    canonical_labels = [
        "8K", "6K", "5K", "4K", "1440p", "1200p", "1080p",
        "900p", "720p", "540p", "480p", "360p",
        "353p (preview)", "240p",
    ]
    # The first occurrence of each tier spans the detector's complete public
    # label vocabulary.  The remaining aliases preserve D-34's existing
    # named/dimension/CDN-path coverage instead of trading breadth for parity.
    probes = ["8K", "6K", "5K", "4K UHD", "2160p", "1440p", "1200p",
              "1080p", "900p", "Full HD", "720p", "HD", "540p", "480p",
              "SD", "1920x1080", "/mp4_2160/", "360p medium", "353p",
              "240p mobile"]
    rows = [_probe(s) for s in probes]
    ad_hoc = _probe(text) if text else None
    verdict_rows = rows + ([ad_hoc] if ad_hoc is not None else [])
    divergent = [r["input"] for r in rows if r["only_one_path_detects"]]
    measured = [r for r in verdict_rows if r["both_readers_measured"]]
    unmeasured_rows = [r for r in verdict_rows if not r["both_readers_measured"]]
    unmeasured_inputs = []
    for row in unmeasured_rows:
        if row["ai_error"]:
            unmeasured_inputs.append({
                "input": row["input"], "reader": "ai",
                "error": row["ai_error"],
            })
        if row["detect_error"]:
            unmeasured_inputs.append({
                "input": row["input"], "reader": "detection",
                "error": row["detect_error"],
            })
    mismatches = [
        {"input": row["input"], "ai_label": row["ai_label"],
         "detect_label": row["detect_res_label"]}
        for row in measured if not row["labels_agree"]
    ]
    canonical_labels_measured = [
        label for label in canonical_labels
        if any(row["both_readers_measured"] and row["labels_agree"]
               and row["detect_res_label"] == label for row in rows)
    ]
    unexpected_canonical_labels = sorted({
        row["detect_res_label"] for row in rows
        if row["both_readers_measured"] and row["labels_agree"]
        and row["detect_res_label"] not in canonical_labels
    })
    missing_canonical_labels = [
        label for label in canonical_labels
        if label not in canonical_labels_measured
    ]
    canonical_coverage_complete = (
        not missing_canonical_labels and not unexpected_canonical_labels
    )

    if mismatches:
        verdict = "FAIL"
        details = "; ".join(
            f"{row['input']} (AI={row['ai_label']!r}, "
            f"detection={row['detect_label']!r})"
            for row in mismatches
        )
        diagnostic = (
            f"FAIL: resolution label disagreement on {len(mismatches)} of "
            f"{len(measured)} measured probes: {details}"
        )
    elif unmeasured_rows:
        verdict = "UNKNOWN"
        details = "; ".join(
            f"{row['input']} ({row['reader']}: {row['error']})"
            for row in unmeasured_inputs
        )
        diagnostic = (
            f"UNKNOWN: {len(unmeasured_rows)} of {len(verdict_rows)} resolution "
            f"probes could not measure both readers: {details}"
        )
    elif not canonical_coverage_complete:
        verdict = "UNKNOWN"
        diagnostic = (
            "UNKNOWN: canonical resolution-label coverage incomplete: "
            f"missing={missing_canonical_labels!r}; "
            f"unexpected={unexpected_canonical_labels!r}"
        )
    else:
        verdict = "OK"
        diagnostic = (
            f"OK: both resolution readers agreed on all {len(measured)} "
            f"measured probes covering all {len(canonical_labels_measured)} "
            "canonical labels"
        )

    return {
        "tool": "resolution_scoring_test",
        "ok": verdict == "OK",
        "verdict": verdict,
        "diagnostic": diagnostic,
        "fixed_probe_count": len(rows),
        "probe_count": len(verdict_rows),
        "measured_probe_count": len(measured),
        "unmeasured_probe_count": len(unmeasured_rows),
        "unmeasured_inputs": unmeasured_inputs,
        "label_mismatch_count": len(mismatches),
        "label_mismatches": mismatches,
        "expected_canonical_label_count": len(canonical_labels),
        "canonical_label_count": len(canonical_labels_measured),
        "canonical_labels_measured": canonical_labels_measured,
        "missing_canonical_labels": missing_canonical_labels,
        "unexpected_canonical_labels": unexpected_canonical_labels,
        "canonical_label_coverage_complete": canonical_coverage_complete,
        "probes_both_paths_detect":
            sum(1 for r in rows if r["both_paths_detect"]),
        "probes_one_path_only": len(divergent),
        "divergent_inputs": divergent,
        "probes": rows,
        "ad_hoc": ad_hoc,
        "note": ("Row 366: AI normalization and deterministic detection "
                 "publish one label vocabulary. Heuristic fields remain "
                 "side-by-side for INV-005 inspection; missing measurements "
                 "produce UNKNOWN."),
    }



# ── 40. HLS/DASH manifest probe (U21: D-32) ────────────────────────
#
# D-32 — fetch (or accept inline) an HLS .m3u8 or DASH .mpd manifest
# and report its structure: variant streams with resolution/bitrate,
# segment counts, VOD-vs-live. BD delegates manifest parsing to ffmpeg,
# so this is a standalone lightweight probe. Read-only — an HTTP GET
# only; downloads no media. DASH is read with targeted regex, not an
# XML parser, so there is no entity-expansion attack surface.

_MANIFEST_FETCH_CAP = 2_000_000   # 2 MB — a manifest is small



def _fetch_manifest_text(url):
    """HTTP GET a manifest URL, size-capped. Returns (ok, text|error)."""
    import urllib.request as _u
    if not url.lower().startswith(("http://", "https://")):
        return False, "url must be http(s)"
    # F-CBD01-01: validate that the URL's host resolves to a public unicast IP
    # BEFORE the outbound GET, so a request-supplied manifest URL cannot be used
    # as an SSRF read primitive against internal targets -- cloud metadata
    # (169.254.169.254), loopback, RFC1918/RFC6598, ULA. Uses the canonical SSRF
    # classifier so this stays consistent with the transport-layer guard.
    from urllib.parse import urlparse as _urlparse
    from ..provider_resolve_impl._common import _is_safe_public_host
    _ok_host, _why = _is_safe_public_host(_urlparse(url).hostname or "")
    if not _ok_host:
        return False, f"host not permitted: {_why}"
    try:
        req = _u.Request(url, headers={
            "User-Agent": "BulkDownloader-dev-probe/1.0"})
        with _u.urlopen(req, timeout=15) as r:
            raw = r.read(_MANIFEST_FETCH_CAP + 1)
        if len(raw) > _MANIFEST_FETCH_CAP:
            return False, f"manifest exceeds {_MANIFEST_FETCH_CAP} bytes"
        return True, raw.decode("utf-8", "replace")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"



def _parse_hls_attrs(attr_text):
    """Parse an HLS attribute list — KEY=VALUE, comma-separated, with
    VALUE optionally a double-quoted string that may contain commas."""
    import re as _re
    attrs = {}
    for m in _re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', attr_text):
        val = m.group(2)
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        attrs[m.group(1)] = val
    return attrs



def _probe_hls(text):
    import re as _re
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    is_master = any(ln.startswith("#EXT-X-STREAM-INF:") for ln in lines)
    out = {"format": "hls",
           "playlist_kind": "master" if is_master else "media"}
    if is_master:
        variants = []
        for i, ln in enumerate(lines):
            if not ln.startswith("#EXT-X-STREAM-INF:"):
                continue
            attrs = _parse_hls_attrs(ln[len("#EXT-X-STREAM-INF:"):])
            uri = next((n for n in lines[i + 1:]
                        if not n.startswith("#")), "")
            try:
                bw = int(attrs.get("BANDWIDTH", 0) or 0)
            except ValueError:
                bw = 0
            variants.append({
                "resolution": attrs.get("RESOLUTION", ""),
                "bandwidth_bps": bw,
                "codecs": attrs.get("CODECS", ""),
                "frame_rate": attrs.get("FRAME-RATE", ""),
                "uri": uri,
            })
        variants.sort(key=lambda v: v["bandwidth_bps"], reverse=True)
        out["variant_count"] = len(variants)
        out["variants"] = variants
        out["alternate_renditions"] = sum(
            1 for ln in lines if ln.startswith("#EXT-X-MEDIA:"))
    else:
        segs = [ln for ln in lines if ln.startswith("#EXTINF:")]
        total = 0.0
        for ln in segs:
            m = _re.match(r"#EXTINF:([\d.]+)", ln)
            if m:
                try:
                    total += float(m.group(1))
                except ValueError:
                    pass
        out["segment_count"] = len(segs)
        out["total_duration_seconds"] = round(total, 1)
        td = [ln for ln in lines
              if ln.startswith("#EXT-X-TARGETDURATION:")]
        out["target_duration"] = td[0].split(":", 1)[1] if td else ""
        out["is_vod"] = any(ln == "#EXT-X-ENDLIST" for ln in lines)
        out["is_live"] = not out["is_vod"]
    return out



def _probe_dash(text):
    import re as _re

    def _attrs(tag_text):
        return {m.group(1): m.group(2) for m in
                _re.finditer(r'([A-Za-z:]+)\s*=\s*"([^"]*)"', tag_text)}

    mpd_m = _re.search(r"<MPD\b[^>]*>", text)
    mpd_attrs = _attrs(mpd_m.group(0)) if mpd_m else {}
    reps = []
    for m in _re.finditer(r"<Representation\b[^>]*>", text):
        a = _attrs(m.group(0))

        def _int(key):
            try:
                return int(a.get(key, 0) or 0)
            except ValueError:
                return 0

        reps.append({
            "id": a.get("id", ""),
            "width": _int("width"),
            "height": _int("height"),
            "bandwidth_bps": _int("bandwidth"),
            "codecs": a.get("codecs", ""),
            "mime_type": a.get("mimeType", ""),
        })
    reps.sort(key=lambda r: r["bandwidth_bps"], reverse=True)
    dash_type = mpd_attrs.get("type", "static")
    return {
        "format": "dash",
        "mpd_type": dash_type,
        "is_vod": dash_type != "dynamic",
        "is_live": dash_type == "dynamic",
        "media_presentation_duration":
            mpd_attrs.get("mediaPresentationDuration", ""),
        "adaptation_set_count": len(_re.findall(r"<AdaptationSet\b", text)),
        "representation_count": len(reps),
        "representations": reps,
    }



def manifest_probe(url=None, text=None):
    """D-32 — probe an HLS (.m3u8) or DASH (.mpd) manifest and report
    its structure: variant streams with resolution/bitrate, segment
    counts, VOD-vs-live. Pass `url` to fetch (an HTTP GET), or `text`
    to parse inline. Read-only — fetches, never downloads media."""
    src = "inline"
    if url:
        if not isinstance(url, str) or not url.strip():
            return {"tool": "manifest_probe", "ok": False,
                    "error": "url is empty"}
        ok, payload = _fetch_manifest_text(url.strip())
        if not ok:
            return {"tool": "manifest_probe", "ok": False,
                    "error": f"fetch failed: {payload}"}
        text, src = payload, url.strip()
    if not isinstance(text, str) or not text.strip():
        return {"tool": "manifest_probe", "ok": False,
                "error": "give a url= to fetch, or text= to parse"}
    head = text.lstrip()
    try:
        if head.startswith("#EXTM3U"):
            report = _probe_hls(text)
        elif "<MPD" in text[:2000]:
            report = _probe_dash(text)
        else:
            return {"tool": "manifest_probe", "ok": False, "source": src,
                    "error": ("not recognised as HLS (#EXTM3U) or DASH "
                              "(<MPD>) — first bytes: " + head[:60])}
    except Exception as e:
        return {"tool": "manifest_probe", "ok": False,
                "error": f"parse failed: {type(e).__name__}: {e}"}
    report["tool"] = "manifest_probe"
    report["ok"] = True
    report["source"] = src
    return report



# ── 60. magic-bytes checker + MP4 metadata inspector (T10) ─────────
#
# D-35 — magic-bytes checker. Reads the first ~64 bytes of a file
# and identifies the format via a magic-byte signature table.
# Critically, it flags CONTAINER-VS-FILENAME MISMATCH — e.g. a .mp4
# extension on bytes that are actually WebM (a real download-pipeline
# failure mode where a CDN returns the wrong format). Read-only.
#
# D-36 — MP4 metadata inspector. Pure byte-level walk of the top-
# level MP4 atoms (ftyp / moov / mdat order + sizes, ftyp brand).
# Distinct from integrity.py / verify_media_integrity, which is the
# ffprobe full-container decode. This is a fast structural inspect
# that runs without ffprobe and catches the two common partial-
# download symptoms: "no moov atom" (incomplete download — already
# a recognised pattern in friendly_error.py) and "mdat before moov"
# (web-unfriendly, must be remuxed).
#
# SECURITY: both tools route the caller-supplied path through
# app._validate_path so the path_allowlist is honoured. A magic-byte
# / atom inspector that reads any arbitrary path would be a file-
# disclosure surface — the existing allowlist already defends the
# rest of the app, and these tools must too.

# Signature table — first match wins. Most entries match a fixed
# byte sequence at a fixed offset; 'mp4_family' matches the "ftyp"
# token at offset 4 (the box-name pattern, not a fixed signature).
_MAGIC_SIGNATURES = [
    # (label, extension, offset, signature-bytes-or-None)
    ("PNG image",       "png",  0, b"\x89PNG\r\n\x1a\n"),
    ("JPEG image",      "jpg",  0, b"\xff\xd8\xff"),
    ("GIF87a image",    "gif",  0, b"GIF87a"),
    ("GIF89a image",    "gif",  0, b"GIF89a"),
    ("WebP / RIFF",     "webp", 0, b"RIFF"),       # confirmed by WEBP at 8
    ("WebM / Matroska", "webm", 0, b"\x1a\x45\xdf\xa3"),
    ("ZIP / OOXML",     "zip",  0, b"PK\x03\x04"),
    ("PDF document",    "pdf",  0, b"%PDF-"),
    ("HTML (likely "
     "soft-blocked)",   "html", 0, b"<!DOCTYPE"),
    ("HTML (likely "
     "soft-blocked)",   "html", 0, b"<html"),
    ("Flash Video",     "flv",  0, b"FLV\x01"),
    ("MPEG-TS",         "ts",   0, b"\x47"),
    ("OGG container",   "ogg",  0, b"OggS"),
    # MP4 family — recognised by an 'ftyp' box at offset 4
    ("MP4 / ISO-BMFF",  "mp4",  4, b"ftyp"),
]


# Filename extensions that "match" a given detected label. Both lists
# are lowercase. Used for the container-vs-filename-mismatch check.
_LABEL_TO_EXTS = {
    "MP4 / ISO-BMFF":      ("mp4", "m4v", "m4a", "mov"),
    "WebM / Matroska":     ("webm", "mkv"),
    "Flash Video":         ("flv",),
    "MPEG-TS":             ("ts", "m2ts", "mts"),
    "OGG container":       ("ogg", "ogv", "oga"),
    "WebP / RIFF":         ("webp", "wav", "avi"),  # RIFF family
    "PNG image":           ("png",),
    "JPEG image":          ("jpg", "jpeg"),
    "GIF87a image":        ("gif",),
    "GIF89a image":        ("gif",),
    "PDF document":        ("pdf",),
    "ZIP / OOXML":         ("zip", "docx", "xlsx", "pptx", "epub"),
    "HTML (likely soft-blocked)": ("html", "htm"),
}



def _detect_magic(head):
    """Return the (label, extension) for the first signature that
    matches `head` (a bytes prefix), or (None, None) if none match."""
    for label, ext, offset, sig in _MAGIC_SIGNATURES:
        if sig is None:
            continue
        end = offset + len(sig)
        if len(head) < end:
            continue
        if head[offset:end] == sig:
            # WebP needs a follow-up to distinguish from bulk_downloader.wav/.avi
            if label == "WebP / RIFF" and len(head) >= 12:
                if head[8:12] == b"WEBP":
                    return ("WebP image", "webp")
                if head[8:12] == b"WAVE":
                    return ("WAV audio", "wav")
                if head[8:12] == b"AVI ":
                    return ("AVI video", "avi")
            return (label, ext)
    return (None, None)



def _resolve_path_against_allowlist(path):
    """Validate a caller-supplied path through app._validate_path so
    the path_allowlist is honoured. Returns (ok, resolved_or_msg)."""
    if not path:
        return False, "path required"
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return False, f"could not load app: {str(e)[:120]}"
    validator = getattr(_app, "_validate_path", None)
    if validator is None:
        return False, "_validate_path unavailable"
    return validator(str(path), "path")



def magic_bytes_check(path):
    """D-35 — identify a file by its first bytes and flag any
    container-vs-filename mismatch. `path` MUST resolve under the
    configured path_allowlist (read-only).
    """
    ok, resolved = _resolve_path_against_allowlist(path)
    if not ok:
        return {"tool": "magic_bytes_check", "ok": False,
                "error": resolved}
    p = Path(resolved)
    if not p.exists():
        return {"tool": "magic_bytes_check", "ok": False,
                "error": f"path not found: {resolved}"}
    if not p.is_file():
        return {"tool": "magic_bytes_check", "ok": False,
                "error": f"not a regular file: {resolved}"}
    try:
        with open(p, "rb") as fh:
            head = fh.read(64)
        size = p.stat().st_size
    except OSError as e:
        return {"tool": "magic_bytes_check", "ok": False,
                "error": f"read failed: {str(e)[:140]}"}

    label, ext = _detect_magic(head)
    filename = p.name
    fn_ext = (filename.rsplit(".", 1)[-1].lower()
              if "." in filename else "")
    expected_exts = _LABEL_TO_EXTS.get(label, ())
    mismatch = (label is not None
                and fn_ext
                and expected_exts
                and fn_ext not in expected_exts)

    out = {
        "tool": "magic_bytes_check",
        "ok": True,
        "path": str(p),
        "filename": filename,
        "filename_ext": fn_ext,
        "file_size_bytes": size,
        "head_hex": head[:16].hex(),
        "head_bytes_read": len(head),
        "detected_label": label,
        "detected_ext": ext,
        "expected_exts_for_label": list(expected_exts),
        "container_vs_filename_mismatch": bool(mismatch),
    }
    if label is None:
        out["verdict"] = (
            "unknown format — first bytes match no known signature "
            "(may be a soft-block HTML page, an empty file, or an "
            "unsupported container)")
    elif mismatch:
        out["verdict"] = (
            f"MISMATCH: file is {label} but filename has "
            f"'.{fn_ext}' extension — likely wrong-format response "
            "from the CDN")
    else:
        out["verdict"] = f"{label} (signature matches filename)"
    return out



def _walk_mp4_atoms(fh, file_size, max_atoms=64):
    """Walk the top-level MP4 atoms. Each atom: 4-byte big-endian
    size, 4-byte ASCII name, then size-8 bytes payload. Size 1 means
    'large size' — the real 64-bit size follows the name. Size 0 is
    'rest of file'. Returns a list of atom records and any parse
    error string (or empty). Read-only — never seeks past EOF."""
    import struct
    atoms = []
    pos = 0
    while pos < file_size and len(atoms) < max_atoms:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return atoms, f"truncated atom header at offset {pos}"
        size = struct.unpack(">I", header[:4])[0]
        name_bytes = header[4:8]
        try:
            name = name_bytes.decode("ascii")
        except UnicodeDecodeError:
            return atoms, (f"non-ASCII atom name at offset {pos}: "
                           f"{name_bytes.hex()}")
        if not all(32 <= b < 127 for b in name_bytes):
            return atoms, (f"non-printable atom name at offset {pos}: "
                           f"{name_bytes.hex()}")
        large = False
        if size == 1:
            ext = fh.read(8)
            if len(ext) < 8:
                return atoms, "truncated large-size atom header"
            size = struct.unpack(">Q", ext)[0]
            large = True
        elif size == 0:
            size = file_size - pos
        atoms.append({"name": name, "size": size,
                      "offset": pos, "large_size": large})
        if size < 8:
            return atoms, (f"atom '{name}' at {pos} has implausible "
                           f"size {size}")
        pos += size
    return atoms, ""



def mp4_metadata_inspect(path):
    """D-36 — walk the top-level MP4 atoms (ftyp/moov/mdat order +
    sizes, ftyp brand). Pure byte-level inspection — no ffprobe.
    Flags 'no moov' (incomplete download) and 'mdat before moov'
    (web-unfriendly: requires remux to stream-start). `path` MUST
    resolve under the configured path_allowlist (read-only).
    """
    ok, resolved = _resolve_path_against_allowlist(path)
    if not ok:
        return {"tool": "mp4_metadata_inspect", "ok": False,
                "error": resolved}
    p = Path(resolved)
    if not p.exists():
        return {"tool": "mp4_metadata_inspect", "ok": False,
                "error": f"path not found: {resolved}"}
    if not p.is_file():
        return {"tool": "mp4_metadata_inspect", "ok": False,
                "error": f"not a regular file: {resolved}"}
    size = p.stat().st_size
    if size < 16:
        return {"tool": "mp4_metadata_inspect", "ok": False,
                "error": f"file too small to be an MP4 ({size} bytes)"}
    try:
        with open(p, "rb") as fh:
            head = fh.read(12)
            # confirm this is the MP4 family before walking
            if head[4:8] != b"ftyp":
                return {"tool": "mp4_metadata_inspect", "ok": False,
                        "error": "not an MP4 / ISO-BMFF file "
                                 "(no ftyp at offset 4)"}
            major_brand = head[8:12].decode("ascii", "replace")
            atoms, parse_error = _walk_mp4_atoms(fh, size)
    except OSError as e:
        return {"tool": "mp4_metadata_inspect", "ok": False,
                "error": f"read failed: {str(e)[:140]}"}

    names = [a["name"] for a in atoms]
    has_moov = "moov" in names
    has_mdat = "mdat" in names
    moov_idx = names.index("moov") if has_moov else -1
    mdat_idx = names.index("mdat") if has_mdat else -1
    mdat_before_moov = (has_moov and has_mdat and mdat_idx < moov_idx)

    out = {
        "tool": "mp4_metadata_inspect",
        "ok": True,
        "path": str(p),
        "file_size_bytes": size,
        "major_brand": major_brand,
        "atom_count": len(atoms),
        "atom_names": names,
        "atoms": atoms,
        "has_ftyp": "ftyp" in names,
        "has_moov": has_moov,
        "has_mdat": has_mdat,
        "mdat_before_moov": mdat_before_moov,
        "parse_error": parse_error,
    }
    findings = []
    if not has_moov:
        findings.append(
            "NO MOOV ATOM — file is incomplete (download "
            "interrupted before the index was written)")
    if mdat_before_moov:
        findings.append(
            "mdat precedes moov — web-unfriendly; requires remux "
            "for streaming (ffmpeg -movflags +faststart)")
    if parse_error:
        findings.append(f"parse stopped: {parse_error}")
    out["findings"] = findings
    out["verdict"] = (
        f"MP4 ok: brand={major_brand!r}, {len(atoms)} atoms"
        if not findings else
        f"MP4 issues: {'; '.join(findings)}")
    return out



# ── 61. dedup-hash explorer + partial-download finder (T11) ────────
#
# D-37 — dedup-hash explorer. Read-only view of the perceptual-hash
# registry (bulk_downloader.dedup.HashRegistry, stored at the path
# configured in app_cfg['dedup_db_path']). Surfaces total entries,
# duplicate clusters (same hash, multiple paths), and a per-cluster
# breakdown — exactly the data the perceptual-dedup feature accumulates
# but never exposes as a dev view. Uses the existing registry; never
# computes a new hash.
#
# D-38 — partial-download finder. Walks every configured site's
# download_dir for .part files (the resumable-download tempfiles
# created by runner.py around line 9032) and reports each with size,
# age, and whether the .part.meta sidecar is present. The pair of
# .part + .part.meta is resumable; a lone .part is orphaned. Paths
# are validated against the path_allowlist before walking.

def dedup_hash_explore(top=20):
    """D-37 — read-only view of the perceptual-hash registry. Counts
    total entries and groups paths that share a hash (exact-match
    clusters — `top` caps how many clusters are returned, largest
    first). Does NOT compute new hashes or run videohash.
    """
    try:
        top = max(1, min(int(top), 200))
    except Exception:
        top = 20
    out = {"tool": "dedup_hash_explore", "ok": True}

    try:
        from bulk_downloader import dedup as _dedup
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "dedup_hash_explore", "ok": False,
                "error": f"could not load dedup module: {str(e)[:140]}"}

    cfg = getattr(_app, "_app_cfg", {}) or {}
    db_path = cfg.get("dedup_db_path") or "video_hashes.db"
    out["db_path"] = db_path
    out["videohash_available"] = bool(_dedup.is_available())

    if not Path(db_path).exists():
        out["registry_present"] = False
        out["total_hashes"] = 0
        out["distinct_hashes"] = 0
        out["duplicate_clusters"] = 0
        out["clusters"] = []
        out["verdict"] = (f"no dedup registry at {db_path} — "
                          "feature not in use yet")
        return out
    out["registry_present"] = True

    import sqlite3 as _sql
    try:
        c = _sql.connect(db_path, timeout=5.0)
        try:
            c.row_factory = _sql.Row
            total = c.execute(
                "SELECT COUNT(*) FROM video_hashes").fetchone()[0]
            distinct = c.execute(
                "SELECT COUNT(DISTINCT hash_hex) "
                "FROM video_hashes").fetchone()[0]
            # exact-hash clusters: same hash_hex shared by >1 path
            cluster_rows = c.execute(
                "SELECT hash_hex, COUNT(*) AS n "
                "FROM video_hashes GROUP BY hash_hex "
                "HAVING n > 1 ORDER BY n DESC LIMIT ?",
                (top,)).fetchall()
            clusters = []
            for cr in cluster_rows:
                paths = c.execute(
                    "SELECT path, file_size_bytes, duration_sec, "
                    "ffprobe_codec, computed_at FROM video_hashes "
                    "WHERE hash_hex = ? ORDER BY file_size_bytes DESC",
                    (cr["hash_hex"],)).fetchall()
                clusters.append({
                    "hash_hex": cr["hash_hex"],
                    "count": cr["n"],
                    "paths": [{
                        "path": p["path"],
                        "size_bytes": p["file_size_bytes"],
                        "duration_sec": p["duration_sec"],
                        "codec": p["ffprobe_codec"],
                        "computed_at": p["computed_at"],
                    } for p in paths],
                })
            cluster_count = c.execute(
                "SELECT COUNT(*) FROM (SELECT hash_hex FROM video_hashes "
                "GROUP BY hash_hex HAVING COUNT(*) > 1)").fetchone()[0]
        finally:
            c.close()
    except Exception as e:
        return {"tool": "dedup_hash_explore", "ok": False,
                "error": f"could not read registry: "
                         f"{type(e).__name__}: {str(e)[:140]}"}

    out["total_hashes"] = total
    out["distinct_hashes"] = distinct
    out["duplicate_clusters"] = cluster_count
    out["clusters"] = clusters
    out["dupes_shown"] = len(clusters)
    out["verdict"] = (
        f"{total} hashed file(s), {cluster_count} duplicate cluster(s)"
        + (f"; showing top {len(clusters)}" if clusters else ""))
    return out



def partial_download_finder(site_configs=None, max_files=500):
    """D-38 — walk every configured site's download_dir for .part
    files (resumable-download tempfiles) and report each with size,
    age, and whether the .part.meta sidecar is present (read-only).
    `site_configs` is app.s_cfg, passed in to avoid a circular import.
    Paths are validated against the path_allowlist before walking.
    """
    import time as _time
    try:
        max_files = max(1, min(int(max_files), 5000))
    except Exception:
        max_files = 500
    out = {"tool": "partial_download_finder", "ok": True,
           "dirs_scanned": [], "dirs_skipped": [], "partials": []}

    # collect unique configured download_dirs
    dirs = []
    seen = set()
    for sid, cfg in (site_configs or {}).items():
        dd = ((cfg or {}).get("download_dir") or "").strip()
        if dd and dd not in seen:
            seen.add(dd)
            dirs.append((sid, dd))

    if not dirs:
        out["verdict"] = "no configured download_dirs to scan"
        return out

    try:
        from bulk_downloader import app as _app
        validator = getattr(_app, "_validate_path", None)
    except Exception:
        validator = None

    truncated = False
    for sid, dd in dirs:
        # path-allowlist enforcement — same defense the rest of the
        # app uses (lesson from T10). Skip a dir, never crash.
        if validator is not None:
            ok, msg = validator(dd, f"site {sid} download_dir")
            if not ok:
                out["dirs_skipped"].append({"site_id": sid, "dir": dd,
                                              "reason": msg[:140]})
                continue
        d = Path(dd)
        if not d.exists():
            out["dirs_skipped"].append({"site_id": sid, "dir": dd,
                                          "reason": "does not exist"})
            continue
        if not d.is_dir():
            out["dirs_skipped"].append({"site_id": sid, "dir": dd,
                                          "reason": "not a directory"})
            continue
        out["dirs_scanned"].append({"site_id": sid, "dir": str(d)})
        try:
            for p in d.rglob("*.part"):
                if not p.is_file():
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                meta = p.with_suffix(p.suffix + ".meta")
                out["partials"].append({
                    "site_id": sid,
                    "path": str(p),
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                    "age_hours": round(
                        (_time.time() - st.st_mtime) / 3600, 1),
                    "has_meta_sidecar": meta.exists(),
                })
                if len(out["partials"]) >= max_files:
                    truncated = True
                    break
        except OSError as e:
            out["dirs_skipped"].append({"site_id": sid, "dir": dd,
                                          "reason": f"walk failed: "
                                                    f"{str(e)[:120]}"})
            continue
        if truncated:
            break

    # classify: a .part with .part.meta is resumable; without, orphaned
    resumable = sum(1 for p in out["partials"] if p["has_meta_sidecar"])
    orphaned = len(out["partials"]) - resumable
    out["resumable_count"] = resumable
    out["orphaned_count"] = orphaned
    out["total_bytes"] = sum(p["size_bytes"] for p in out["partials"])
    out["truncated"] = truncated
    out["verdict"] = (
        f"{len(out['partials'])} .part file(s) across "
        f"{len(out['dirs_scanned'])} dir(s); "
        f"{resumable} resumable, {orphaned} orphaned"
        + (f"; truncated at {max_files}" if truncated else ""))
    return out




# ── 62. filename-template previewer (T12: D-40) ─────────────────────
#
# Distinct from template_audit, which validates LOGIN templates. D-40
# is the filename-template surface: given a template string + an
# optional override context, render it through the real
# fname.resolve_filename_template engine and report exactly what the
# operator will see on disk. Surfaces placeholders, which got filled,
# which got stripped (unknown), and which would be empty in the
# rendered output. Read-only — never writes a file.

def filename_template_preview(template=None, context=None):
    """D-40 — preview a filename template (read-only). With no
    template, renders every KNOWN_VARIABLES sample so the operator
    can see what each placeholder looks like. With a template, runs
    it through resolve_filename_template using the supplied context
    merged into the canonical sample values.
    """
    import re as _re
    try:
        from bulk_downloader import fname as _f
    except Exception as e:
        return {"tool": "filename_template_preview", "ok": False,
                "error": f"fname import failed: {str(e)[:140]}"}

    # canonical sample context — taken from KNOWN_VARIABLES so the
    # preview never drifts from the documented variable catalog
    samples: dict = {}
    catalog = []
    for entry in _f.KNOWN_VARIABLES:
        name, desc, example, since = entry
        samples[name] = example
        catalog.append({"name": name, "description": desc,
                         "example": example, "since": since})

    out = {"tool": "filename_template_preview", "ok": True,
           "known_variables": catalog,
           "known_variable_count": len(catalog)}

    # apply caller-supplied overrides (only string-coercible values)
    ctx = dict(samples)
    if isinstance(context, dict):
        for k, v in context.items():
            ctx[str(k)] = "" if v is None else str(v)

    if not template:
        # no template supplied — render a per-variable demo so the
        # operator sees each placeholder isolated. cheap, useful.
        demos = []
        for name in (e["name"] for e in catalog):
            t = "{" + name + "}"
            try:
                rendered = _f.resolve_filename_template(t, ctx)
            except Exception as e:
                rendered = f"<error: {str(e)[:80]}>"
            demos.append({"placeholder": name,
                            "template": t,
                            "rendered": rendered})
        out["mode"] = "catalog"
        out["per_variable_demo"] = demos
        out["verdict"] = (f"no template supplied — showing "
                          f"{len(catalog)} known variables")
        return out

    template = str(template)[:500]
    out["mode"] = "preview"
    out["template"] = template

    # find placeholders the template references
    refs = sorted(set(_re.findall(r"\{(\w+)\}", template)))
    out["placeholders_used"] = refs
    out["unknown_placeholders"] = [p for p in refs
                                     if p not in samples]
    out["empty_placeholders"] = [
        p for p in refs
        if p in samples and not str(ctx.get(p) or "").strip()]
    out["context_used"] = {p: ctx.get(p, "") for p in refs}

    try:
        rendered = _f.resolve_filename_template(template, ctx)
    except Exception as e:
        return {"tool": "filename_template_preview", "ok": False,
                "template": template,
                "error": f"render failed: "
                         f"{type(e).__name__}: {str(e)[:140]}"}
    out["rendered"] = rendered
    out["rendered_length"] = len(rendered)

    # show whether sanitization or scrubbing changed the output —
    # i.e. the rendered filename differs from a naive str.format with
    # the same context (which would NOT sanitize illegal characters,
    # NOT strip unknown placeholders, NOT trim empty []/() groups)
    naive = template
    for p, v in ctx.items():
        naive = naive.replace("{" + p + "}", str(v))
    out["differs_from_naive_substitution"] = (rendered != naive)
    out["verdict"] = (
        f"rendered to {rendered!r}; "
        f"{len(refs)} placeholder(s) used"
        + (f"; {len(out['unknown_placeholders'])} unknown stripped"
           if out["unknown_placeholders"] else "")
        + (f"; {len(out['empty_placeholders'])} empty"
           if out["empty_placeholders"] else ""))
    return out





# ── 65. FlareSolverr health + captcha-relay status (T15: D-45+D-48) ─
#
# D-45 FlareSolverr health: probe the configured endpoint via the
# existing `flaresolverr_client.ping` (which already fails-open with
# {ok:False, error:...}) and pair it with the long-running counters
# from `stats()` (solves_attempted / succeeded / failed / last_error).
# No new sampler — reuse the in-process stats accumulated by the
# runner.
#
# D-48 captcha-relay status: enumerate `captcha_relay.list_pending`
# (with resolved included), count per challenge_type, count per
# status, and report whether the takeover starter/ender callbacks
# are wired (the runner registers them at startup; if absent the
# operator's "Solve" button can't actually start a manual takeover).

def flaresolverr_health():
    """D-45 — read-only FlareSolverr health: endpoint config, live
    ping (fail-open), and the in-process solver stats. Distinct from
    /api/flaresolverr/status which only reports the active session
    count — this surfaces the full health/usage picture.
    """
    out = {"tool": "flaresolverr_health", "ok": True}
    try:
        from bulk_downloader import flaresolverr_client as _fs
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "flaresolverr_health", "ok": False,
                "error": f"flaresolverr module unavailable: "
                         f"{str(e)[:140]}"}
    cfg = getattr(_app, "_app_cfg", {}) or {}
    endpoint = (cfg.get("flaresolverr_endpoint") or "").strip()
    use_flag = bool(cfg.get("use_flaresolverr"))
    out["endpoint"] = endpoint
    out["use_flaresolverr_setting"] = use_flag
    out["is_configured"] = bool(
        getattr(_fs, "is_configured", lambda x: bool(x))(endpoint))

    # cumulative stats — never raises
    try:
        out["stats"] = _fs.stats()
    except Exception as e:
        out["stats"] = {"error": f"stats raised: {str(e)[:120]}"}

    # live probe (only if configured — ping returns ok=False with a
    # clear error otherwise, but we surface the skip explicitly)
    if not out["is_configured"]:
        out["ping"] = {"ok": False, "skipped": True,
                       "reason": "endpoint_not_configured"}
    else:
        try:
            timeout_s = float(cfg.get(
                "flaresolverr_timeout_s", 5.0) or 5.0)
        except (TypeError, ValueError):
            timeout_s = 5.0
        try:
            out["ping"] = _fs.ping(endpoint, timeout_s=timeout_s)
        except Exception as e:
            out["ping"] = {"ok": False,
                           "error": f"ping raised: {str(e)[:120]}"}

    p = out.get("ping") or {}
    if not use_flag:
        verdict = "FlareSolverr disabled in config"
    elif p.get("skipped"):
        verdict = "FlareSolverr enabled but endpoint not configured"
    elif p.get("ok"):
        ver = p.get("version") or "?"
        verdict = f"FlareSolverr reachable (version {ver})"
    else:
        verdict = (f"FlareSolverr unreachable: "
                   f"{p.get('error', 'unknown')}")
    out["verdict"] = verdict
    return out



def captcha_relay_status():
    """D-48 — read-only captcha-relay state: pending+resolved queue
    grouped by challenge type and resolution state, plus whether the
    runner's takeover starter/ender callbacks are wired up.
    """
    out = {"tool": "captcha_relay_status", "ok": True}
    try:
        from bulk_downloader import captcha_relay as _cr
    except Exception as e:
        return {"tool": "captcha_relay_status", "ok": False,
                "error": f"captcha_relay module unavailable: "
                         f"{str(e)[:140]}"}

    out["challenge_types"] = list(
        getattr(_cr, "CHALLENGE_TYPES", ()))
    # callback registration — module-level globals
    out["takeover_starter_registered"] = (
        getattr(_cr, "_takeover_starter", None) is not None)
    out["takeover_ender_registered"] = (
        getattr(_cr, "_takeover_ender", None) is not None)

    try:
        items = _cr.list_pending(include_resolved=True)
    except Exception as e:
        return {"tool": "captcha_relay_status", "ok": False,
                "error": f"list_pending failed: {str(e)[:140]}"}

    out["total_known"] = len(items)
    by_status: dict = {}
    by_type: dict = {}
    for it in items:
        st = (it or {}).get("status") or "?"
        ty = (it or {}).get("captcha_type") or "?"
        by_status[st] = by_status.get(st, 0) + 1
        by_type[ty] = by_type.get(ty, 0) + 1
    out["by_status"] = by_status
    out["by_type"] = by_type
    out["pending"] = [p for p in items
                       if p.get("status") not in (
                           "resolved", "dismissed")]

    pending_n = len(out["pending"])
    can_takeover = (out["takeover_starter_registered"]
                    and out["takeover_ender_registered"])
    if not can_takeover:
        verdict = ("captcha-relay takeover NOT wired (runner did not "
                   "register starter/ender); 'Solve' will fail")
    elif pending_n:
        verdict = (f"{pending_n} pending captcha(s) awaiting "
                   "manual solve")
    else:
        verdict = (f"captcha-relay wired; no pending challenges "
                   f"(seen {len(items)} total)")
    out["verdict"] = verdict
    return out



# ── 66. UA / stealth audit (T16: D-50) ─────────────────────────────
#
# Audits the anti-detection surface: per-site User-Agent strings,
# stealth-related flags (use_real_chrome / use_stealth /
# use_stealth_library / use_persistent_profile / headless), the
# availability + version of the optional playwright-stealth library
# via the existing stealth.get_library_status() probe, and the
# built-in STEALTH_JS init script's presence. Flags misconfiguration
# patterns the operator should fix:
#   • use_stealth_library=True but the library is not importable
#     -> worker silently runs without it
#   • headless=False (operator probably forgot to re-enable headless)
#   • blank user_agent on a site that uses the per-site UA path
#     (the runner has a fallback Chrome UA, but a blank one is worth
#     calling out so the operator notices)
# Read-only — never touches a page or starts a browser.

def stealth_audit(site_configs=None):
    """D-50 — audit per-site UA + stealth configuration and the
    health of the stealth subsystem (read-only).
    """
    out = {"tool": "stealth_audit", "ok": True}
    # Library status from the existing probe — never raises
    try:
        from bulk_downloader import stealth as _stealth
        out["stealth_library"] = _stealth.get_library_status()
    except Exception as e:
        out["stealth_library"] = {"available": False,
                                   "import_error": str(e)[:140],
                                   "version": ""}

    # Built-in STEALTH_JS — presence + length, never the content
    # (it's well-known anti-fingerprint JS; not a secret, but no need
    # to dump several KB into a dev-tool response)
    try:
        from bulk_downloader.constants import STEALTH_JS as _js
        out["builtin_stealth_js"] = {
            "present": bool(_js),
            "length_bytes": len(_js or ""),
        }
    except Exception as e:
        out["builtin_stealth_js"] = {"present": False,
                                      "error": str(e)[:140]}

    # Per-site flags
    lib_avail = bool(out["stealth_library"].get("available"))
    sites = []
    misconfig = []
    for sid, cfg in (site_configs or {}).items():
        c = cfg or {}
        entry = {
            "site_id": sid,
            "user_agent": (c.get("user_agent") or "").strip(),
            "use_real_chrome": bool(c.get("use_real_chrome", True)),
            "use_stealth": bool(c.get("use_stealth", True)),
            "use_stealth_library": bool(
                c.get("use_stealth_library", False)),
            "use_persistent_profile": bool(
                c.get("use_persistent_profile", True)),
            "headless": bool(c.get("headless", True)),
        }
        # Misconfig detection
        if entry["use_stealth_library"] and not lib_avail:
            misconfig.append({
                "site_id": sid,
                "issue": "use_stealth_library=True but "
                         "playwright-stealth not importable",
                "fix": "pip install playwright-stealth (it's "
                       "optional; the built-in STEALTH_JS still "
                       "runs)",
            })
        if not entry["headless"]:
            misconfig.append({
                "site_id": sid,
                "issue": "headless=False — workers open visible "
                         "Chrome windows for this site",
                "fix": "set headless=True unless this is "
                       "intentional debug",
            })
        if not entry["use_stealth"]:
            misconfig.append({
                "site_id": sid,
                "issue": "use_stealth=False — built-in "
                         "anti-fingerprint JS disabled",
                "fix": "set use_stealth=True (default) unless "
                       "this is intentional",
            })
        sites.append(entry)

    out["site_count"] = len(sites)
    out["sites"] = sites
    out["misconfigurations"] = misconfig
    # UA summary — distinct UA strings across sites
    distinct_ua = sorted(set(
        s["user_agent"] for s in sites if s["user_agent"]))
    out["distinct_user_agents"] = distinct_ua
    out["distinct_ua_count"] = len(distinct_ua)
    out["sites_using_runner_default_ua"] = sum(
        1 for s in sites if not s["user_agent"])

    if misconfig:
        out["verdict"] = (
            f"{len(sites)} site(s); "
            f"{len(misconfig)} misconfig finding(s); "
            f"playwright-stealth "
            f"{'available' if lib_avail else 'NOT available'}")
    else:
        out["verdict"] = (
            f"{len(sites)} site(s); no misconfig; "
            f"playwright-stealth "
            f"{'available' if lib_avail else 'NOT available'}")
    return out


# ── T44 / D-46 — request replay (inspector) ────────────────────────

def request_replay_list(limit=50):
    """T44 / D-46 — list recently captured /api/* requests.
    Read-only — never replays. Replay is via the separate POST route.

    Returns {tool, ok, enabled, buffered, capacity, total_recorded,
    items[], verdict}.
    """
    out = {
        "tool": "request_replay_list",
        "ok": True,
        "enabled": False,
        "buffered": 0,
        "capacity": 0,
        "total_recorded": 0,
        "items": [],
        "verdict": "",
    }
    try:
        from bulk_downloader import request_replay as _rr
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"request_replay import failed: {e}"
        return out
    stats = _rr.stats()
    out["enabled"] = stats.get("enabled", False)
    out["buffered"] = stats.get("buffered", 0)
    out["capacity"] = stats.get("capacity", 0)
    out["total_recorded"] = stats.get("total_recorded", 0)
    out["items"] = _rr.list_recent(limit=limit)
    if not out["enabled"]:
        out["verdict"] = (
            "request capture is OFF — enable the 'request_capture' "
            "feature flag to start recording")
    else:
        out["verdict"] = (
            f"capture ON; {out['buffered']}/{out['capacity']} "
            f"slot(s) used, {out['total_recorded']} request(s) "
            f"recorded since boot")
    return out



# ── T45 / D-27 — login-flow recorder (inspector) ───────────────────

def login_flows_status(*, site_id=None):
    """T45 / D-27 — list saved login flows. Read-only.

    Returns {tool, ok, site_id_filter, flows[], total, verdict}.
    """
    out = {
        "tool": "login_flows_status",
        "ok": True,
        "site_id_filter": site_id,
        "flows": [],
        "total": 0,
        "verdict": "",
    }
    try:
        from bulk_downloader import login_flow_recorder as _lfr
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"login_flow_recorder import failed: {e}"
        return out
    flows = _lfr.list_login_flows(site_id=site_id)
    out["flows"] = flows
    out["total"] = len(flows)
    # Build a per-site summary
    by_site = {}
    for f in flows:
        sid = f.get("site_id", "")
        by_site[sid] = by_site.get(sid, 0) + 1
    summary = ", ".join(f"{s}={n}" for s, n in sorted(by_site.items()))
    out["verdict"] = (
        f"{out['total']} login flow(s) saved"
        + (f": {summary}" if summary else "")
        + (f" (filtered to site_id={site_id})" if site_id else ""))
    return out
