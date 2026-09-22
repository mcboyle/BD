"""config_dryrun.py — the declarative configuration dry-run simulator (row1034).

The `.env` editor already answers "is this submission LEGAL?"
(app_envfile_editor.validate_envfile_updates: accepted / rejected / warnings). It has
never answered "what would this submission CHANGE?" — the editor's POST is the only
way to find out, and by then the write has happened. Backup, rebalance, dedup and
routes all have a plan step (api_rebalance_plan, dedup_preview.plan); configuration
did not.

This module is that missing half, and nothing more:

  plan_envfile_updates(updates, *, saved, effective) -> plan   (PURE: no write, no
      os.environ mutation, no filesystem access — `saved` and `effective` are passed
      in by the caller, which is also what makes it testable without a real .env)
  render_plan_text(plan) -> str                                (the visualizer)

A plan is a per-key ACTION, in the caller's submission order:

    create    the key has no saved value yet
    update    the saved value differs from the submitted one
    noop      the saved value already equals the submitted one
    unset     (reserved; writer persists empty string rather than deleting key)
    rejected  validate_envfile_updates refused it — no value is planned at all

`restart_required` is per key and for the plan as a whole, because the `.env` is read
at boot: a key only needs a restart when the value that would be WRITTEN differs from
the value currently EFFECTIVE in os.environ. A noop on a key whose effective value has
drifted still needs one, which is why `effective` is a separate input from `saved`.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .app_envfile_editor import validate_envfile_updates

_MARK = {"create": "+", "update": "~", "unset": "-", "noop": "=", "rejected": "!"}


def _normalized(updates: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """The value the writer would persist, per key, before validation."""
    out: Dict[str, Optional[str]] = {}
    for k, v in (updates or {}).items():
        out[k] = None if v is None else v
    return out


def plan_envfile_updates(updates: Mapping[str, Any], *,
                         saved: Optional[Mapping[str, str]] = None,
                         effective: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Simulate `updates` against `saved` (.env) and `effective` (os.environ).

    Returns {ok, actions, counts, restart_required, warnings}. Writes NOTHING: the
    two states are inputs, so this function cannot touch the .env or the environment.
    """
    if saved is None:
        try:
            from . import _envfile
            saved = _envfile.parse_envfile(_envfile.resolve_envfile_path().read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    else:
        saved = dict(saved)
    if effective is None:
        import os
        effective = dict(os.environ)
    else:
        effective = dict(effective)
    submitted = _normalized(updates)

    # One validate pass for the whole submission (passing None through as the
    # writer does in app_envfile_editor), so a cross-key finding or rejected
    # empty/None foundation key is the same here as on the real POST.
    verdict = validate_envfile_updates(submitted)
    accepted, rejected = verdict["accepted"], verdict["rejected"]

    actions = []
    counts = {"create": 0, "update": 0, "noop": 0, "unset": 0, "rejected": 0}
    for name, raw in submitted.items():
        prior = saved.get(name)
        live = effective.get(name)
        if name in rejected:
            action, to_value, restart = "rejected", None, False
        else:
            to_value = accepted.get(name, "" if raw is None else str(raw))
            if prior is None:
                action = "create"
            elif prior == to_value:
                action = "noop"
            else:
                action = "update"
            restart = to_value != (live if live is not None else None)
        counts[action] += 1
        actions.append({
            "name": name,
            "action": action,
            "from": prior,
            "to": to_value,
            "effective": live,
            "restart_required": bool(restart),
            "rejected": name in rejected,
            "reason": rejected.get(name, ""),
        })

    return {
        "ok": not rejected,
        "written": False,
        "actions": actions,
        "counts": counts,
        "restart_required": any(a["restart_required"] for a in actions),
        "warnings": list(verdict["warnings"]),
    }


def render_plan_text(plan: Mapping[str, Any]) -> str:
    """Terraform-shaped rendering of a plan. A rejected key never shows its value —
    printing it beside the applied ones is how a reader mistakes it for applied."""
    counts = plan["counts"]
    lines = ["Configuration plan: %d to create, %d to update, %d unchanged, "
             "%d to unset, %d to reject."
             % (counts["create"], counts["update"], counts["noop"],
                counts["unset"], counts["rejected"])]
    for a in plan["actions"]:
        mark = _MARK[a["action"]]
        if a["action"] == "rejected":
            lines.append("  %s %s: REJECTED — %s" % (mark, a["name"], a["reason"]))
        elif a["action"] == "create":
            lines.append("  %s %s: %s" % (mark, a["name"], a["to"]))
        elif a["action"] == "update":
            lines.append("  %s %s: %s -> %s" % (mark, a["name"], a["from"], a["to"]))
        elif a["action"] == "unset":
            lines.append("  %s %s: %s -> (unset)" % (mark, a["name"], a["from"]))
        else:
            lines.append("  %s %s: %s (unchanged)" % (mark, a["name"], a["to"]))
    for w in plan.get("warnings", []):
        lines.append("  warning: %s" % w)
    lines.append("Restart required: %s (the .env is read at boot)."
                 % ("yes" if plan["restart_required"] else "no"))
    return "\n".join(lines)
