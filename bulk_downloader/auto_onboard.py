"""bulk_downloader.auto_onboard -- A4: auto-onboard prep on site create/update.

On a site create/update, classify the template-onboarding state (via the
existing, pure ``tools/onboard_site_template.plan_site`` -- no capture, no disk
write) and STAGE a draft intent. PREP ONLY:

  * never enables a template, and
  * never launches the live capture here.

The live capture + the first-time enable stay the operator/stash action (the
one manual atom in the policy's hard floor). Gated by the ``auto_onboard``
toggle -- default OFF -> a complete no-op (cfg untouched), so wiring the hook
into the create path is behaviour-neutral by default.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from . import lifecycle_automation as la


def auto_onboard_on_site_change(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Classify onboarding for ``cfg`` and stage a prep intent in place. Returns
    a summary. No-op (cfg untouched) unless the ``auto_onboard`` toggle is on."""
    if not isinstance(cfg, dict):
        return {"ok": False, "error": "cfg must be a dict"}
    if not la.is_enabled("auto_onboard"):
        return {"ok": True, "skipped": "auto_onboard disabled"}
    try:
        from tools.onboard_site_template import plan_site
    except Exception as e:
        return {"ok": False, "error": f"plan unavailable: {e}"[:120]}

    plan = plan_site(cfg)
    if not plan:
        return {"ok": True, "staged": False, "reason": "no usable URL"}

    cfg.update(plan)                       # template_onboarding + detect mode
    cfg["auto_teach_first_run"] = False    # never pop the first-run teach window

    staged = False
    onboarding = plan.get("template_onboarding")
    if onboarding == "capture_required":
        # Prep-only: record a pending-capture intent for the operator / stash to
        # action. We do NOT launch capture and do NOT enable anything here.
        cfg["onboard_pending"] = {
            "state": "capture_required",
            "at": int(time.time()),
            "auto": True,
        }
        staged = True

    # Hard-floor invariant: the prep hook never enables a template. If a plan
    # ever tried to, force it back -- first-time enable is the one manual atom.
    if cfg.get("template_auto_detect_mode") == "enabled":
        cfg["template_auto_detect_mode"] = "capture_then_review"

    return {"ok": True, "onboarding": onboarding, "staged": staged,
            "enabled": False}
