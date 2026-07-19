"""Phase 9.3 -- reference structured-output schemas.

Named, reviewable JSON schemas for the LLM tasks that produce machine-readable
output. Validated by `llm_exec.validate_schema` (a minimal stdlib dialect:
type / required / properties / enum / array-items). Any failure makes the model
output unusable -> the deterministic fallback runs (see 9.0/9.3 hard rule).

Schemas are versioned by name (`<task>.v<schema_version>`); bumping a schema means
adding a new name, never editing a shipped one in place (caches key on it).
"""

from typing import Any, Dict, Optional


FAILURE_TRIAGE: Dict[str, Any] = {
    "type": "object",
    "required": ["category"],
    "properties": {
        "category": {"type": "string",
                     "enum": ["auth", "permanent", "transient", "rate_limited", "unknown"]},
        "reason_code": {"type": "string"},
        "retryable": {"type": "boolean"},
        "suggested_action": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

REDACTION_SECRET_SCAN: Dict[str, Any] = {
    "type": "object",
    "required": ["severity", "secrets_found"],
    "properties": {
        "secrets_found": {"type": "array"},
        "severity": {"type": "string", "enum": ["none", "low", "medium", "high"]},
    },
}

TEMPLATE_REVIEW_SUMMARY: Dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "issues": {"type": "array"},
    },
}

UI_SCREENSHOT_REVIEW: Dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array"},
        "severity": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "accessibility": {"type": "array"},
    },
}


# name -> schema. Name format: "<prompt_id>.v<schema_version>".
REGISTRY: Dict[str, Dict[str, Any]] = {
    "failure_triage.v1": FAILURE_TRIAGE,
    "redaction_secret_scan.v1": REDACTION_SECRET_SCAN,
    "template_review_summary.v1": TEMPLATE_REVIEW_SUMMARY,
    "ui_screenshot_review.v1": UI_SCREENSHOT_REVIEW,
}


def get_schema(name: str) -> Optional[Dict[str, Any]]:
    """Look up a reference schema by `<prompt_id>.v<schema_version>` name."""
    return REGISTRY.get(name)
