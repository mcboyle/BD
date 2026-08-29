"""template_manager — file-level management for reviewed + draft templates.

Backs the Template Manager API/page (#10): list ``templates/reviewed/*.template.json``
and ``templates/drafts/*.template-draft.json`` with status / host / selector
groups / resolutions / redacted network patterns / lint warnings; promote a
draft to a reviewed template; disable a reviewed template.

Safety invariants:
  * Drafts are NEVER auto-enabled — promotion is always an explicit call, and a
    draft carrying unsafe (blocking-lint) selectors is refused.
  * Filenames are validated (no path separators / ``..`` / wrong suffix) so the
    API can only touch files inside the two template dirs.
  * Read paths never expose cookie/token/storage values; network patterns are
    run through the dry-run redactor.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import template_registry as tr
from . import selector_lint as sl
from . import dry_run as dr

REVIEWED_DIR = tr.PROJECT_ROOT / "templates" / "reviewed"
DRAFTS_DIR = tr.PROJECT_ROOT / "templates" / "drafts"

_REVIEWED_SUFFIX = ".template.json"
_DRAFT_SUFFIX = ".template-draft.json"


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as pretty JSON to ``path`` atomically: serialize to a
    sibling ``*.tmp`` (same directory → same filesystem → ``os.replace`` is
    atomic) and swap it into place. A crash or write failure mid-serialize
    truncates only the throwaway tmp; the live file is never partially written.
    Mirrors ``template_keystone.commit_swap``'s stage→os.replace pattern. (F2.)
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), "utf-8")
    os.replace(tmp, path)  # atomic


def _safe_name(name: str, suffix: str) -> Optional[str]:
    name = (name or "").strip()
    if (not name or "/" in name or "\\" in name or ".." in name
            or not name.endswith(suffix)):
        return None
    return name


def _describe(fp: Path) -> Dict[str, Any]:
    try:
        t = json.loads(fp.read_text("utf-8"))
    except Exception as e:
        return {"file": fp.name, "ok": False,
                "error": f"{type(e).__name__}: {e}"[:120]}
    issues = sl.lint_template(t)
    # A6-1: surface the review-only api_candidate (secret-free: host + parametrised
    # relative paths) so the SPA can offer the accept-API affordance only when a
    # candidate exists. base + endpoint names only — never tokens/ids/queries.
    cand = t.get("api_candidate")
    api_candidate = None
    if isinstance(cand, dict) and cand.get("base"):
        api_candidate = {
            "base": str(cand.get("base")),
            "endpoints": sorted(k for k in cand.keys() if k != "base"),
        }
    return {
        "file": fp.name,
        "host": t.get("host"),
        "status": t.get("status"),
        "enabled": t.get("status") == "enabled",
        "selectors": sorted((t.get("selectors") or {}).keys()),
        "resolutions": t.get("resolutions", []),
        "network_patterns": dr._redact_patterns(t),
        "lint_warnings": [i.to_dict() for i in issues],
        "has_blocking_lint": sl.has_blocking_issues(issues),
        "api_candidate": api_candidate,
    }


def list_templates(*, reviewed_dir=None, drafts_dir=None) -> Dict[str, Any]:
    """List reviewed + draft templates with redacted detail + lint warnings."""
    rd = Path(reviewed_dir or REVIEWED_DIR)
    dd = Path(drafts_dir or DRAFTS_DIR)
    reviewed = ([_describe(fp) for fp in sorted(rd.glob("*" + _REVIEWED_SUFFIX))]
                if rd.is_dir() else [])
    drafts = ([_describe(fp) for fp in sorted(dd.glob("*" + _DRAFT_SUFFIX))]
              if dd.is_dir() else [])
    return {"ok": True, "reviewed": reviewed, "drafts": drafts,
            "reviewed_dir": str(rd), "drafts_dir": str(dd)}


def _materialize_api_block(t: Dict[str, Any]) -> Dict[str, Any]:
    """A6-1: validate the draft's review-only ``api_candidate`` and, if it
    passes, return the concrete runtime ``api`` block ({base, <name>: /rel/path}).

    The runtime ``api`` block is what ungates ``template_assist.build_api_url``
    (gated v3.66.155 / v3.66.157). It is materialized ONLY at an explicit
    operator ``accept_api`` and ONLY when the candidate base's host was actually
    observed serving the API (``observed_api_hosts``) — never an operator-typed
    or guessed host. A candidate that fails this is REFUSED, never silently
    dropped, so the operator cannot believe they accepted an API that the gate
    rejected.

    Returns ``{"ok": True, "api": {...}}`` or ``{"ok": False, "error": "..."}``.
    """
    cand = t.get("api_candidate")
    if not isinstance(cand, dict):
        return {"ok": False,
                "error": "accept_api requested but draft has no api_candidate"}
    base = str(cand.get("base") or "").strip()
    if not base.startswith(("http://", "https://")):
        return {"ok": False, "error": "api_candidate base is not an absolute URL"}
    from urllib.parse import urlparse
    host = (urlparse(base).netloc or "").lower()
    if not host:
        return {"ok": False, "error": "api_candidate base has no host"}
    # Gate preservation: the base host MUST be a host that was actually observed
    # serving the API. observed_api_hosts is the review-only evidence from the
    # capture; api_base_candidate is the normalizer's single-host suggestion.
    observed = {str(h).lower() for h in (t.get("observed_api_hosts") or []) if h}
    abc = str(t.get("api_base_candidate") or "").strip().lower()
    abc_host = (urlparse(abc).netloc or "").lower() if abc else ""
    if host not in observed and host != abc_host:
        return {"ok": False,
                "error": ("api_candidate base host %r was not observed serving "
                          "the API; refusing to promote an unverified host"
                          % host)}
    # The named-endpoint entries (everything but "base") are the relative paths
    # build_api_url expands. Require at least one, and keep only str values.
    api = {"base": base}
    for k, v in cand.items():
        if k == "base":
            continue
        if isinstance(v, str) and v:
            api[k] = v
    if len(api) < 2:
        return {"ok": False,
                "error": "api_candidate has no named endpoint paths"}
    return {"ok": True, "api": api}


# Reusable patterns must look media/API-relevant (loose, site-agnostic) — the
# same list ``tools/promote_template.py`` enforces.
_MEANINGFUL_PATTERN_TOKENS = ["/api/", "download", "watch", "resolution", "media",
                              ".m3u8", ".mpd", ".mp4", "/movie/", "/video/"]


def promote_gate_errors(t: Dict[str, Any]) -> List[str]:
    """Readiness/safety gate for a runtime-shape review candidate — the same set
    of checks the CLI (``tools/promote_template.py``) enforces, so the Workbench
    promote path cannot ship a template the CLI would reject. Returns a list of
    human-readable errors (``[]`` == passes). Does NOT mutate ``t``.

    The selector-lint *blocking* refusal is handled separately by the caller
    (it already returns a structured ``lint_warnings`` payload); this gate covers
    normalize-shape, resolutions, a media/API-relevant pattern, the download
    selector shape, and the BAD_TERMS denylist on reusable URL/API material.
    """
    from .bad_terms import BAD_TERMS  # single source of truth (shared w/ scrubber)
    errors: List[str] = []

    # A normalized candidate carries a flat ``network_patterns`` list and no raw
    # discovery block; refuse a raw builder draft (run normalize first).
    schema = str(t.get("schema_version") or t.get("schema") or "")
    if ("network_discovery" in t or "template_draft" in schema
            or not isinstance(t.get("network_patterns"), list)):
        errors.append("input looks like a raw builder draft; normalize before promoting")
        return errors

    patterns = t.get("network_patterns") or []
    selector_only_dom_proven = bool(
        schema == "row363.learned-template.v1"
        and isinstance(t.get("learning_evidence"), dict)
        and t["learning_evidence"].get("dom_options_proven") is True
    )
    if selector_only_dom_proven:
        from .affordance_learning import learned_template_gate_errors
        learned_errors = learned_template_gate_errors(t)
        errors.extend(learned_errors)
        selector_only_dom_proven = not learned_errors
    if not patterns and not selector_only_dom_proven:
        errors.append("network_patterns must be a non-empty list")

    api_values = ([str(v) for v in (t.get("api") or {}).values()]
                  if isinstance(t.get("api"), dict) else [])
    lint_text = "\n".join([str(p) for p in patterns] + api_values).lower()
    for bad in BAD_TERMS:
        if bad.lower() in lint_text:
            errors.append(f"reusable URL/API material contains blocked term: {bad}")

    if patterns and not any(
            any(tok in str(p).lower() for tok in _MEANINGFUL_PATTERN_TOKENS)
            for p in patterns):
        errors.append("no media/API-relevant network pattern found")

    dl = (t.get("selectors") or {}).get("download") or {}
    if not (dl.get("trigger") or dl.get("row_selectors") or dl.get("button")):
        errors.append("selectors.download must have a trigger or row_selectors")

    if not (t.get("resolutions") or []):
        errors.append("resolutions list is empty")

    return errors


def promote_gate_warnings(t: Dict[str, Any], *,
                          trigger_match_count: Any = None) -> List[str]:
    """2c-guard soft (NON-blocking) pre-enable warnings. Distinct from
    ``promote_gate_errors`` (which hard-blocks): these only advise.

    ``trigger_match_count`` is the number of elements the template's download
    trigger matched on a LIVE fetch of the site (produced upstream by
    ``/api/template/sandbox`` / ``/api/playground/test``). When it is exactly 0
    the template is stale against today's markup and would drive a dead run, so
    we warn -- but never block: a live fetch can transiently fail (offline,
    Cloudflare), and a hard block on that would be brittle (the reviewed-set must
    stay enable-able). ``None`` means "no live check was run" -> silent.

    Fail-open: a non-int / unexpected count never raises and never warns. Does
    NOT mutate ``t``.
    """
    warnings: List[str] = []
    try:
        if trigger_match_count is None:
            return warnings
        if isinstance(trigger_match_count, bool):
            return warnings
        n = int(trigger_match_count)
    except (TypeError, ValueError):
        return warnings
    if n == 0:
        warnings.append(
            "download trigger matches 0 elements on the live page -- the "
            "template may be stale; enable anyway?")
    return warnings


def promote_draft(filename: str, *, enable: bool = True, accept_api: bool = False,
                  reviewed_dir=None, drafts_dir=None) -> Dict[str, Any]:
    """Promote ``templates/drafts/<filename>`` to a reviewed template.

    Explicit operator action only. Refuses a draft with blocking-lint
    (unsafe generic/nav) selectors. The draft file is kept (audit trail); a new
    ``<host>.template.json`` is written with ``status`` = enabled/disabled per
    ``enable`` (default enabled — promotion IS the deliberate enable step).

    ``accept_api`` (default False) is the A6-1 reviewer affordance: when True
    and the draft carries a valid review-only ``api_candidate``, the concrete
    ``api`` block is materialized onto the reviewed template (ungating
    ``build_api_url`` / relative-pattern resolution — gated v3.66.155/157). The
    default (False) is byte-for-byte the prior behaviour: NO ``api`` block, gate
    stays closed. An invalid/unverified candidate under ``accept_api`` REFUSES
    the whole promote rather than promoting without the API.
    """
    rd = Path(reviewed_dir or REVIEWED_DIR)
    dd = Path(drafts_dir or DRAFTS_DIR)
    safe = _safe_name(filename, _DRAFT_SUFFIX)
    if not safe:
        return {"ok": False, "error": "invalid draft filename"}
    src = dd / safe
    if not src.is_file():
        return {"ok": False, "error": "draft not found"}
    try:
        t = json.loads(src.read_text("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"draft parse failed: {e}"[:120]}
    # NEXT-1: normalize a RAW builder draft into the runtime review-candidate
    # shape FIRST (the CLI runs normalize_template_draft.py before promote). The
    # Workbench reads RAW drafts from templates/drafts/, so without this the path
    # shipped raw-shape, resolution-less drafts straight into reviewed/ as ENABLED.
    # Normalize is applied ONLY when the input is actually raw: an already-normalized
    # candidate is passed through untouched, because re-normalizing one would
    # recompute (and so CLOBBER) its review-only api metadata — observed_api_hosts
    # / api_base_candidate are derived from network_discovery, which a re-fed
    # candidate no longer carries, breaking the accept_api host check below.
    # normalize itself preserves ``api_candidate``, so the materialize path is safe.
    from .template_normalize import normalize_draft
    _schema = str(t.get("schema_version") or t.get("schema") or "")
    if ("network_discovery" in t or "template_draft" in _schema
            or not isinstance(t.get("network_patterns"), list)):
        t = normalize_draft(t)
    issues = sl.lint_template(t)
    if sl.has_blocking_issues(issues):
        return {"ok": False,
                "error": "draft has unsafe selectors; fix before promoting",
                "lint_warnings": [i.to_dict() for i in issues]}
    # NEXT-1: enforce the SAME readiness gate the CLI (tools/promote_template.py)
    # enforces — resolutions, a media/API-relevant pattern, a download trigger/row,
    # and the BAD_TERMS denylist — so the GUI cannot promote a template the CLI
    # would refuse (and never enables a half-derived draft).
    gate = promote_gate_errors(t)
    if gate:
        return {"ok": False, "error": gate[0], "gate_errors": gate}
    # A6-1: materialize the runtime ``api`` block ONLY on an explicit accept,
    # and ONLY if the candidate validates against the observed API host. A failed
    # validation refuses the whole promote (no silent API-less success).
    # A6-1 gate integrity: the runtime ``api`` block may ONLY come from the
    # validated accept path below. Strip any pre-existing ``api`` key first
    # (a hand-authored / tampered draft could smuggle one — normalize_draft
    # never emits ``api``, only the review-only ``api_candidate``). Without this,
    # a smuggled block would sail through with accept_api=False and falsely ungate
    # build_api_url for an unobserved host.
    t.pop("api", None)
    accepted_api = None
    if accept_api:
        mres = _materialize_api_block(t)
        if not mres.get("ok"):
            return {"ok": False, "error": mres.get("error")}
        accepted_api = mres["api"]
    # Hygiene: a captured draft carries analytics/ad/telemetry beacons (and the
    # occasional inlined HTML/JS blob) in network_patterns. Strip them so the
    # ENABLED reviewed template ships only real asset/media hints.
    from .pattern_hygiene import scrub_network_patterns
    scrub = scrub_network_patterns(t.get("network_patterns"))
    if "network_patterns" in t:
        t["network_patterns"] = scrub["kept"]
    # Accurate, F2-safe drop accounting: normalize already scrubbed (its drops land
    # in ``rejected_patterns``), and this second pass catches a hand-fed candidate
    # that skipped normalize. Report the COUNT only — the dropped values can be
    # trackers/signed material and must never ride out in an API response.
    dropped_count = len(t.get("rejected_patterns") or []) + len(scrub["dropped"])
    if accepted_api is not None:
        t["api"] = accepted_api          # the gate-ungating runtime block
    t["status"] = "enabled" if enable else "disabled"
    t["promoted_at"] = int(time.time())
    reviewed_name = safe[:-len(_DRAFT_SUFFIX)] + _REVIEWED_SUFFIX
    rd.mkdir(parents=True, exist_ok=True)
    # A5 auto-refresh hook (default-OFF; byte-identical when no capture-time mode
    # is toggled on). When promoting to ENABLED over a host whose live template is
    # already enabled, an active mode routes the overwrite through the keystone
    # (gold snapshot -> drift-gated swap, or stage-for-operator-confirm) instead
    # of the raw write below. on_fresh_capture returns handled=False in the
    # default/all-off case and on a first promote (no enabled live template).
    if enable:
        try:
            from . import lifecycle_drift as _ld
            _ar = _ld.on_fresh_capture(t.get("host") or safe[:-len(_DRAFT_SUFFIX)],
                                       t, reviewed_dir=rd)
        except Exception as _e:  # the hook must never break a manual promote
            _ar = {"handled": False, "reason": f"hook error: {_e}"[:120]}
        if _ar.get("handled"):
            return {"ok": bool(_ar.get("ok", True)), "promoted": reviewed_name,
                    "from": safe, "auto_refresh": _ar,
                    "api_accepted": accepted_api is not None,
                    "dropped_patterns": dropped_count}
    # Golden protection (floor): when the opt-in lifecycle keystone did NOT
    # handle this write (default config, or a disable/first promote), never
    # clobber an existing reviewed/gold template without a recoverable copy. The
    # .bak suffix is outside the ``*.template.json`` glob, so it is never loaded
    # as a template. Best-effort: a backup failure must not block the promote.
    _gold_path = rd / reviewed_name
    if _gold_path.exists():
        try:
            import shutil as _shutil
            _shutil.copy2(_gold_path, _gold_path.with_name(_gold_path.name + ".bak"))
        except OSError:
            pass
    _atomic_write_json(rd / reviewed_name, t)
    # E1: plugin event surface. A successful promote makes the draft a REVIEWED
    # template; an enabled promote additionally takes it LIVE. Fired through the
    # canonical isolated emit seam -- a throwing consumer never breaks a promote.
    try:
        from . import plugins as _pl
        _host = t.get("host") or safe[:-len(_DRAFT_SUFFIX)]
        _enabled = t.get("status") == "enabled"
        _ts = int(time.time())
        _pl.emit("template.reviewed",
                 {"host": _host, "filename": reviewed_name,
                  "enabled": _enabled, "ts": _ts})
        if _enabled:
            _pl.emit("template.promoted",
                     {"host": _host, "filename": reviewed_name, "ts": _ts})
    except Exception:  # the event surface must never break a manual promote
        pass
    return {"ok": True, "promoted": reviewed_name,
            "enabled": t["status"] == "enabled", "from": safe,
            "api_accepted": accepted_api is not None,
            "dropped_patterns": dropped_count}


def disable_reviewed(filename: str, *, reviewed_dir=None) -> Dict[str, Any]:
    """Disable a reviewed template (``status`` -> ``disabled``) so
    ``find_template_for_url`` no longer matches it. The file is kept."""
    rd = Path(reviewed_dir or REVIEWED_DIR)
    safe = _safe_name(filename, _REVIEWED_SUFFIX)
    if not safe:
        return {"ok": False, "error": "invalid template filename"}
    fp = rd / safe
    if not fp.is_file():
        return {"ok": False, "error": "template not found"}
    try:
        t = json.loads(fp.read_text("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"parse failed: {e}"[:120]}
    t["status"] = "disabled"
    t["disabled_at"] = int(time.time())
    _atomic_write_json(fp, t)
    return {"ok": True, "disabled": safe, "enabled": False}
