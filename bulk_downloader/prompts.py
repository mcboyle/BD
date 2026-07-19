"""Phase 9.2 -- Prompt registry and prompt versioning.

Every LLM feature references a NAMED prompt by id + version (e.g.
`failure_triage` v1) instead of an inline string scattered through the codebase.
The registry below is the reviewable source of truth: prompt text changes bump the
prompt version; the associated structured-output schema version is tracked
separately so cache/validation can invalidate on either.

A prompt record is:
    {"version", "schema_version", "template", "schema", "review_required",
     "description"}

`render(id, version, **vars)` fills the template with `str.format`. Missing prompt
or version raises (a call MUST reference a known prompt).
"""

from typing import Any, Dict, List, Optional


# ── prompt definitions (reviewable source) ───────────────────────────────
# Each id maps to {version: record}. Add a new version key when the text changes;
# never edit a shipped version's text in place (that would silently break caches).
_PROMPTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "failure_triage": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Classify a download failure into a reason bucket.",
            "template": (
                "Classify this download failure. Respond ONLY with JSON: "
                '{{"category": "...", "reason_code": "...", "retryable": true/false, '
                '"suggested_action": "..."}}.\n'
                "category must be one of: auth, permanent, transient, rate_limited, unknown.\n"
                "Failure message: {message}\n"
                "HTTP status code: {status_code}\n"
            ),
        },
    },
    "template_review_summary": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Summarize a candidate site template for human review.",
            "template": (
                "Summarize this candidate download-site template for an operator "
                "reviewing it before enabling. Respond ONLY with JSON: "
                '{{"summary": "...", "risk": "low|medium|high", "issues": ["..."]}}.\n'
                "Template candidate:\n{candidate}\n"
            ),
        },
    },
    "redaction_secret_scan": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Advisory second-pass secret scan over already-redacted text.",
            "template": (
                "You are a second-pass reviewer checking ALREADY-REDACTED text for "
                "any leftover secret-looking tokens. Respond ONLY with JSON: "
                '{{"secrets_found": ["..."], "severity": "none|low|medium|high"}}.\n'
                "Redacted text:\n{redacted}\n"
            ),
        },
    },
    "site_onboard_summary": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Summarize an onboarding page to help reach the enable step.",
            "template": (
                "Summarize what an operator must do to enable downloads for this site. "
                "Respond ONLY with JSON: "
                '{{"summary": "...", "steps": ["..."], "blockers": ["..."]}}.\n'
                "Page context:\n{context}\n"
            ),
        },
    },
    "opv_evidence_summary": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Summarize operator-provided verification evidence (advisory).",
            "template": (
                "Summarize the operator's verification evidence. Do NOT issue a verdict. "
                "Respond ONLY with JSON: "
                '{{"summary": "...", "evidence_items": ["..."], "gaps": ["..."]}}.\n'
                "Evidence:\n{evidence}\n"
            ),
        },
    },
    "ui_screenshot_review": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Advisory UI/screenshot review notes.",
            "template": (
                "Review this UI screenshot for obvious visual problems. Respond ONLY "
                "with JSON: "
                '{{"findings": ["..."], "severity": "none|low|medium|high"}}.\n'
                "Context: {context}\n"
            ),
        },
    },
    "route_quality_review": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Advisory review of a route render manifest.",
            "template": (
                "Review the rendered route for quality issues. Respond ONLY with JSON: "
                '{{"findings": ["..."], "severity": "none|low|medium|high"}}.\n'
                "Route: {route}\n"
            ),
        },
    },
    "doc_drift_scan": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Advisory doc/runbook drift detection.",
            "template": (
                "Compare the doc text against the described current behavior and flag "
                "drift. Respond ONLY with JSON: "
                '{{"drift": true/false, "items": ["..."]}}.\n'
                "Doc:\n{doc}\nBehavior:\n{behavior}\n"
            ),
        },
    },
    "nl_saved_search": {
        "1": {
            "schema_version": "1",
            "review_required": True,
            "description": "Translate a natural-language query into a saved-search filter (preview).",
            "template": (
                "Translate this natural-language request into a saved-search filter. "
                "Respond ONLY with JSON describing the filter fields.\n"
                "Request: {query}\n"
            ),
        },
    },
    "filename_metadata_parse": {
        "1": {
            "schema_version": "1",
            "review_required": False,
            "description": "Parse structured metadata from a media filename (advisory fallback).",
            "template": (
                "Extract structured metadata from this media filename. Respond ONLY "
                "with JSON: "
                '{{"title": "...", "season": null, "episode": null, '
                '"resolution": "...", "codec": "..."}}.\n'
                "Filename: {filename}\n"
            ),
        },
    },
}


def _versions(prompt_id: str) -> Dict[str, Dict[str, Any]]:
    return _PROMPTS.get(prompt_id) or {}


def latest_version(prompt_id: str) -> Optional[str]:
    vs = _versions(prompt_id)
    if not vs:
        return None
    # versions are simple integer-ish strings; pick the numerically largest
    try:
        return max(vs, key=lambda v: int(v))
    except ValueError:
        return sorted(vs)[-1]


def get(prompt_id: str, version: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return a flattened prompt record (incl. its version), or None if unknown."""
    vs = _versions(prompt_id)
    if not vs:
        return None
    ver = version or latest_version(prompt_id)
    rec = vs.get(ver)
    if rec is None:
        return None
    out = dict(rec)
    out["id"] = prompt_id
    out["version"] = ver
    return out


def exists(prompt_id: str, version: Optional[str] = None) -> bool:
    return get(prompt_id, version) is not None


def render(prompt_id: str, version: Optional[str], **variables: Any) -> str:
    """Render a registered prompt. Raises KeyError for an unknown prompt/version."""
    rec = get(prompt_id, version)
    if rec is None:
        raise KeyError(f"unknown prompt {prompt_id!r} version {version!r}")
    template = rec.get("template", "")
    try:
        return template.format(**variables)
    except KeyError as e:
        raise KeyError(f"prompt {prompt_id} v{rec['version']} missing variable {e}") from None


def schema_version_for(prompt_id: str, version: Optional[str] = None) -> str:
    rec = get(prompt_id, version)
    return (rec or {}).get("schema_version", "") if rec else ""


def schema_for(prompt_id: str, version: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rec = get(prompt_id, version)
    if not rec:
        return None
    if rec.get("schema") is not None:
        return rec["schema"]
    # fall back to the named reference-schema registry (9.3): "<id>.v<schema_version>"
    try:
        from . import llm_schemas
        return llm_schemas.get_schema(f"{prompt_id}.v{rec.get('schema_version', '')}")
    except Exception:
        return None


def review_required_for(prompt_id: str, version: Optional[str] = None) -> bool:
    rec = get(prompt_id, version)
    return bool((rec or {}).get("review_required", True)) if rec else True


def list_prompts() -> List[Dict[str, Any]]:
    """Reviewable catalogue: one row per (id, version)."""
    rows: List[Dict[str, Any]] = []
    for pid, vs in _PROMPTS.items():
        for ver, rec in vs.items():
            rows.append({
                "id": pid,
                "version": ver,
                "schema_version": rec.get("schema_version", ""),
                "review_required": bool(rec.get("review_required", True)),
                "description": rec.get("description", ""),
            })
    return rows
