"""template_extractor_impl.refine -- verbatim cluster from template_extractor.py."""

from __future__ import annotations

from typing import Any, Dict, List

from ._constants import REFINE_PROMPT


def refine_with_ai(template: Dict[str, Any],
                     candidates: List[Dict[str, Any]],
                     html: str) -> Dict[str, Any]:
    """Send the rule-based draft to the configured AI provider for
    refinement. Returns:
      {ok, refined_template, warnings, ai_confidence, provider}

    Returns ok=False quietly if AI is disabled — caller falls back
    to the rule-based draft."""
    if not isinstance(template, dict):
        return {"ok": False, "error": "invalid template"}
    try:
        from .. import aiassist
    except Exception as e:
        return {"ok": False, "error": f"aiassist unavailable: {e}"}
    if not aiassist.get_config().get("enabled"):
        return {"ok": False, "error": "AI assist not enabled"}
    # Truncate the HTML excerpt to keep token usage reasonable
    excerpt = (html or "")[:8000]
    # Build candidates summary
    summary_lines = []
    for c in (candidates or [])[:6]:
        sels = c.get("selector_variants") or []
        first_sel = sels[0]["selector"] if sels else "(no selector)"
        summary_lines.append(
            f"  score={c.get('score',0)}  text={c.get('text','')[:50]!r}  "
            f"selector={first_sel}")
    prompt = REFINE_PROMPT.format(
        current_template=str({k: v for k, v in template.items()
                                if not k.startswith("_")}),
        candidates_summary="\n".join(summary_lines) or "  (none)",
        html_excerpt=excerpt,
    )
    result = aiassist._call_model(prompt, image_b64=None,
                                     max_tokens=1024, temperature=0.1,
                                     timeout=60)
    if not result.ok:
        return {"ok": False,
                "error": result.error,
                "error_kind": result.error_kind,
                "provider": result.provider}
    parsed = aiassist.extract_json(result.text or "")
    if not isinstance(parsed, dict):
        return {"ok": False,
                "error": "AI returned non-JSON or unparseable response",
                "raw": (result.text or "")[:400],
                "provider": result.provider}
    # Merge into a refined template
    refined = dict(template)
    refined["row_selectors"] = _clean_selector_list(
        parsed.get("refined_row_selectors")) or template.get("row_selectors", [])
    refined["trigger_selectors"] = _clean_selector_list(
        parsed.get("refined_trigger_selectors")) or template.get("trigger_selectors", [])
    if parsed.get("url_attribute"):
        new_attr = str(parsed.get("url_attribute")).strip()
        if new_attr in ("href", "src") or new_attr.startswith("data-"):
            refined["url_attribute"] = new_attr
    ai_warnings = _clean_warning_list(parsed.get("warnings"))
    try:
        confidence = int(parsed.get("confidence", 50))
        confidence = max(0, min(100, confidence))
    except Exception:
        confidence = 50
    return {
        "ok": True,
        "refined_template": refined,
        "warnings": ai_warnings,
        "ai_confidence": confidence,
        "provider": result.provider,
        "model": result.model,
    }


def _clean_selector_list(v) -> List[str]:
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        if isinstance(item, str):
            s = item.strip()
            if s and len(s) <= 300:
                out.append(s)
    return out[:6]


def _clean_warning_list(v) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(w)[:200] for w in v if w][:5]
