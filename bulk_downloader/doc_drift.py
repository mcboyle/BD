"""Phase 9.11 -- doc/runbook drift detection.

Deterministic checks first, optional advisory LLM summary second. The LLM may
summarize / classify severity / suggest wording -- it never rewrites source truth
and never asserts live/deployed state. Deterministic findings are preserved whether
or not the model runs.
"""

import re
from typing import Any, Dict, List, Optional, Set

_VERSION_RE = re.compile(r"\b3\.66\.(\d+)\b")
_ROUTE_RE = re.compile(r"(/api/[A-Za-z0-9_./:-]+)")
_FILE_RE = re.compile(r"\b((?:bulk_downloader|tools|tests)/[A-Za-z0-9_./-]+\.py)\b")
_LIVE_RE = re.compile(r"\b(live|deployed|in production|currently running)\b", re.I)
_PACK_RE = re.compile(r"\bv3_66_(\d+)\b")


def _finding(doc, kind, severity, detail):
    return {"doc": doc, "kind": kind, "severity": severity, "detail": detail}


def scan(docs: Dict[str, str], *, state: Dict[str, Any],
         routes: Optional[Set[str]] = None, files: Optional[Set[str]] = None,
         health_version: Optional[str] = None) -> Dict[str, Any]:
    """Deterministic drift scan over a set of docs. Returns {ok, findings}."""
    routes = routes or set()
    files = files or set()
    live_version = str(state.get("live_version", "") or "")
    live_n = None
    m = _VERSION_RE.search(live_version)
    if m:
        live_n = int(m.group(1))
    findings: List[Dict[str, Any]] = []

    for name, text in docs.items():
        text = text or ""
        # referenced version vs STATE.json
        for vm in _VERSION_RE.finditer(text):
            n = int(vm.group(1))
            if live_n is not None and n < live_n:
                findings.append(_finding(name, "stale_version", "warning",
                                         f"references 3.66.{n}, live is {live_version}"))
                break
        # stale "live"/"deployed" claim not matching health
        if _LIVE_RE.search(text) and health_version:
            for vm in _VERSION_RE.finditer(text):
                if vm.group(0) != health_version and f"3.66.{vm.group(1)}" != health_version:
                    findings.append(_finding(name, "stale_live_claim", "high",
                                             f"claims live at 3.66.{vm.group(1)}, "
                                             f"/api/health reports {health_version}"))
                    break
        # referenced routes that don't exist
        for rm in _ROUTE_RE.finditer(text):
            r = rm.group(1).rstrip(".,);:")
            base = r.split("?")[0]
            if routes and base not in routes and not any(base.startswith(x) for x in routes):
                findings.append(_finding(name, "missing_route", "warning",
                                         f"documents {base}, not in current routes"))
        # referenced files that don't exist
        for fm in _FILE_RE.finditer(text):
            f = fm.group(1)
            if files and f not in files:
                findings.append(_finding(name, "missing_file", "warning",
                                         f"references {f}, not in source tree"))
        # superseded pack references
        for pm in _PACK_RE.finditer(text):
            n = int(pm.group(1))
            if live_n is not None and n < live_n:
                findings.append(_finding(name, "superseded_pack", "info",
                                         f"references pack v3_66_{n}, current is {live_version}"))
                break

    return {"ok": not findings, "findings": findings}


def summarize(findings: List[Dict[str, Any]], *, model: Optional[str] = None,
              _call=None) -> Dict[str, Any]:
    """Optional advisory LLM summary of deterministic findings. The deterministic
    findings are always returned unchanged; the model only adds a prose summary.
    If the model is offline/unavailable, summary is empty but findings persist."""
    from .llm_exec import LLMCallSpec, execute
    if not findings:
        return {"summary": "no drift detected", "findings": findings, "advisory": True}
    desc = "; ".join(f"{f['kind']}:{f['detail']}" for f in findings[:20])
    spec = LLMCallSpec(task_id="doc_drift_scan", prompt_id="doc_drift_scan",
                       prompt_version="1", input=f"Summarize these doc-drift findings: {desc}",
                       schema=None, model=model, review_required=True,
                       fallback=lambda: "")
    res = execute(spec, _call=_call)
    return {"summary": res.value if res.status == "success" else "",
            "findings": findings, "advisory": True}
