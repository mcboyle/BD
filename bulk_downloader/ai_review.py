"""Phase 9.9 -- optional AI-assisted screenshot/UI review.

Advisory visual-QA pass over the deterministic render output (9.8). OFF by default
and non-gating: it is invoked only behind `--ai-review`, it never fails a release on
its own, it is never treated as proof, and it requires a VISION-capable model
(enforced via the 9.1 registry). The schema admits only visible UI findings -- no
backend/runtime fields -- so the model cannot invent backend issues.

The vision model is stash-only in the sandbox, so this is exercised mock-first; the
deterministic path (9.8) is unaffected whether or not AI review runs.
"""

import json
import os
from typing import Any, Dict, List, Optional

from . import llm_schemas, model_registry
from .llm_exec import LLMCallSpec, execute

ADVISORY_NOTICE = ("ADVISORY ONLY -- AI visual-QA notes. Not proof, not a test "
                   "result, not a release gate. Human review required.")


def review_screenshots(images: List[str], *, enabled: bool = False,
                       model: Optional[str] = None, context: str = "",
                       _call=None) -> Dict[str, Any]:
    """Run an advisory AI review over `images` (base64 strings or paths-as-context).

    Returns {ran, review, error, advisory, model}. Never raises for normal failure
    modes; a missing/offline/invalid model just yields ran/review accordingly while
    the deterministic screenshots remain valid."""
    if not enabled:
        return {"ran": False, "review": None, "error": "", "advisory": True,
                "reason": "disabled", "model": model}

    # Vision capability is enforced here (9.1) -- a text-only model is rejected and
    # the model is never called.
    ok, why = model_registry.can_use(model, "vision")
    if not ok:
        return {"ran": False, "review": None, "error": why, "advisory": True,
                "reason": "text-only-model-rejected", "model": model}

    image_b64 = images[0] if images else ""
    spec = LLMCallSpec(
        task_id="ui_screenshot_review", prompt_id="ui_screenshot_review",
        prompt_version="1", input=f"Advisory visual QA. Context: {context}",
        schema=llm_schemas.UI_SCREENSHOT_REVIEW, schema_version="1",
        model=model, capability="vision", image_b64=image_b64, image_allowed=True,
        review_required=True, timeout=60,
        fallback=lambda: {"findings": [], "severity": "none"},
    )
    res = execute(spec, _call=_call)
    # ran=True iff the model path actually produced/attempted output; offline/invalid
    # collapse to a non-fatal advisory with the deterministic fallback value.
    ran = res.via == "model"
    return {
        "ran": ran,
        "review": res.value if res.status == "success" else None,
        "error": "" if res.status == "success" else res.status,
        "advisory": True,
        "model": res.model,
        "fallback_value": res.value,
    }


def save_review(review: Dict[str, Any], out_dir: str) -> Dict[str, str]:
    """Write ai_review.md + ai_review.json under out_dir. Advisory framing first."""
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "ai_review.md")
    json_path = os.path.join(out_dir, "ai_review.json")
    body = review.get("review") or {}
    findings = body.get("findings", []) if isinstance(body, dict) else []
    lines = ["# AI screenshot review", "", f"> {ADVISORY_NOTICE}", "",
             f"- model: {review.get('model')}",
             f"- ran: {review.get('ran')}",
             f"- severity: {body.get('severity', 'n/a') if isinstance(body, dict) else 'n/a'}",
             "", "## Findings (advisory)", ""]
    if findings:
        lines += [f"- {f}" for f in findings]
    else:
        lines += ["- (no findings or review unavailable)"]
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(json_path, "w") as fh:
        json.dump({"advisory": True, "notice": ADVISORY_NOTICE, **review}, fh, indent=2)
    return {"md": md_path, "json": json_path}
