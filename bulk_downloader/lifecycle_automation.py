"""lifecycle_automation.py — template-lifecycle automation toggles + state machine.

A5 sub-wave 1: the SUBSTRATE the rest of the lifecycle automation hangs off.
Pure metadata + config reads. **No template is mutated here and no automation
runs here.** This module only:

  1. reads automation toggles (ALL DEFAULT OFF), and
  2. defines the template state machine (usable/quarantined/disabled axis +
     the legal transitions + the orthogonal needs_review advisory flag).

Safety invariant (AUTOMATION_POLICY-aligned):
  * Every automation toggle DEFAULTS OFF. With all toggles off, behaviour is
    byte-identical to today — and since nothing imports this module yet, that
    is trivially true at this sub-wave; it becomes load-bearing once the drift
    wiring (sub-wave 3) and mutators (sub-wave 4) consult `is_enabled` before
    acting.
  * The two DOWNLOAD-AFFECTING mutators (auto_quarantine, auto_repair) are
    DOUBLE-GATED: their toggle AND a keystone-present check. Until the
    backup-before-overwrite keystone exists (sub-wave 2), `is_enabled` returns
    False for them no matter what the toggle says — this enforces the policy's
    "no auto-* until backup exists" ordering structurally, not just by docs.
  * All reads are fail-safe: any error resolving a toggle → OFF.

Nothing in this module enables anything. Flipping a toggle on is a separate,
gated operator decision, and only legal once the mechanism is test-covered.
"""
from __future__ import annotations

from typing import Optional

# ── Automation toggles (global_config keys) — ALL DEFAULT OFF ────────────────
# name -> global_config key. Reading an unset key yields the default (False).
AUTOMATION_TOGGLES = {
    "drift_sweep":     "automation.drift_sweep_enabled",      # L1 scheduled sweep
    "validation_gate": "automation.validation_gate_enabled",  # L2 drift gate
    "auto_flag":       "automation.auto_flag_enabled",        # L3 flag needs_review
    "auto_quarantine": "automation.auto_quarantine_enabled",  # L4 (download-affecting)
    "auto_repair":     "automation.auto_repair_enabled",      # L5 (download-affecting)
    "auto_refresh":    "automation.auto_refresh_enabled",     # L5' (download-affecting)
    "auto_onboard":    "automation.auto_onboard_enabled",      # A4 prep-only (NOT download-affecting; never enables)
    "auto_promote":    "automation.auto_promote_enabled",       # A5 auto-promote clean candidate (download-affecting)
    "auto_queue":      "automation.auto_queue_enabled",          # A8 queue self-mgmt (dedup/prioritize/pause-resume; reversible, NOT keystone-required)
    "auto_recover":    "automation.auto_recover_enabled",         # A7 self-recovery (cookie/profile/retry/health-rollback/requarantine; RESTORES known-good, NOT keystone-required)
    "auto_ci":         "automation.auto_ci_enabled",              # A6 CI loop (snapshot+regression+rollback-artifact; RESTORES, NOT keystone-required; binding gate stays stash capture.sh)
    "controller":      "automation.controller_enabled",           # A9 supervised-autonomy controller (orchestrates A1->A2/A3/A5/A7/A8; NOT keystone-required -- delegated actions stay individually keystone-gated; the MASTER OFF-SWITCH dominates this toggle)
    "auto_disco":      "automation.disco_enabled",                 # A-DISCO level-4 enumerate->triage->auto-queue (queueing is reversible, NOT keystone-required; master off-switch dominates; enable gated on live-reversibility OPVs)
    # Capture-time trigger-MODE selectors for auto_refresh (DEFAULT OFF). They
    # choose WHERE auto_refresh fires when a fresh capture is promoted; the
    # actual mutation still goes through the auto_refresh master toggle above
    # (keystone-gated), so these are not independently download-affecting.
    # The sweep-driven path needs no mode toggle (it is drift_sweep + auto_refresh).
    "auto_refresh_on_capture": "automation.auto_refresh_on_capture_enabled",  # drift-gated swap on promote
    "auto_refresh_confirm":    "automation.auto_refresh_confirm_enabled",      # stage on promote; operator confirms
}

# The mutators that can take a template OUT of service / overwrite it. These
# require the backup-before-overwrite keystone in addition to their toggle.
KEYSTONE_REQUIRED = frozenset({"auto_quarantine", "auto_repair", "auto_refresh", "auto_promote"})


def _read_toggle(key: str) -> bool:
    """Read a global_config boolean, fail-safe OFF on any error."""
    try:
        from . import global_config  # lazy: keeps this module import-light
    except Exception:
        return False
    try:
        return bool(global_config.get(key, False))
    except Exception:
        return False


def keystone_available() -> bool:
    """True iff the backup-before-overwrite keystone is present and usable.

    The download-affecting mutators (auto_quarantine, auto_repair) require this
    in addition to their toggle. Fail-safe: any import/probe error → False, so
    a broken keystone forces the mutators OFF rather than letting them run
    without a rollback path.
    """
    try:
        from . import template_keystone
        return bool(template_keystone.keystone_present())
    except Exception:
        return False


def is_enabled(name: str) -> bool:
    """True iff automation `name` is enabled AND (if download-affecting) the
    keystone is available. Unknown name → False. Default for any toggle → OFF.
    """
    key = AUTOMATION_TOGGLES.get(name)
    if key is None:
        return False
    if not _read_toggle(key):
        return False
    if name in KEYSTONE_REQUIRED and not keystone_available():
        return False
    return True


def toggle_status() -> dict:
    """Read-only snapshot of every automation's effective state (for an
    operator surface). Reports the raw toggle AND the effective value (which
    differs for keystone-gated mutators when the keystone is absent)."""
    out = {}
    for name, key in AUTOMATION_TOGGLES.items():
        raw = _read_toggle(key)
        out[name] = {
            "key": key,
            "toggle_on": raw,
            "effective": is_enabled(name),
            "keystone_gated": name in KEYSTONE_REQUIRED,
        }
    return out


# ── Template state machine ───────────────────────────────────────────────────
# The USABILITY axis is the template's `status` field. template_registry only
# matches status == "enabled" (find_template_for_url), so any other status is
# automatically NOT used for downloads — that is why `quarantined` is a status
# (auto-excluded by construction) while `needs_review` is an orthogonal FLAG
# (status stays "enabled", so a flagged template keeps working — advisory only).
STATUS_ENABLED = "enabled"          # usable (matched by find_template_for_url)
STATUS_DISABLED = "disabled"        # manually taken offline (disable_reviewed)
STATUS_QUARANTINED = "quarantined"  # auto/drift offline; behaves as not-enabled
STATUS_REVIEWED = "reviewed"        # promoted, not yet enabled
STATUS_CANDIDATE = "candidate"      # draft awaiting review

USABLE_STATUSES = frozenset({STATUS_ENABLED})   # mirrors template_registry L41

# Advisory flag, orthogonal to status. Set by L3 auto-flag; does NOT change
# usability — a needs_review template is still matched and used.
NEEDS_REVIEW_FLAG = "needs_review"

# Legal status transitions. Recovery from quarantine goes back through review
# (repair produces a reviewed candidate that must pass the gold diff before
# re-enable) — never quarantined -> enabled directly.
_TRANSITIONS = {
    STATUS_CANDIDATE:   frozenset({STATUS_REVIEWED}),
    STATUS_REVIEWED:    frozenset({STATUS_ENABLED, STATUS_DISABLED}),
    STATUS_ENABLED:     frozenset({STATUS_DISABLED, STATUS_QUARANTINED}),
    STATUS_DISABLED:    frozenset({STATUS_ENABLED}),
    STATUS_QUARANTINED: frozenset({STATUS_REVIEWED, STATUS_DISABLED}),
}

ALL_STATUSES = frozenset(_TRANSITIONS)


def is_usable(status: Optional[str]) -> bool:
    """True iff a template with this status is matched/used for downloads."""
    return status in USABLE_STATUSES


def can_transition(frm: Optional[str], to: str) -> bool:
    """True iff `frm -> to` is a legal status transition. A no-op (frm == to)
    is legal. Unknown `frm` is treated as having no legal transitions except
    to itself."""
    if frm == to:
        return True
    return to in _TRANSITIONS.get(frm, frozenset())


def assert_transition(frm: Optional[str], to: str) -> None:
    """Raise ValueError on an illegal transition. The mutators (sub-wave 4)
    call this before writing a new status, so an out-of-band state change
    fails loudly rather than silently corrupting the lifecycle."""
    if not can_transition(frm, to):
        raise ValueError(
            f"illegal template state transition {frm!r} -> {to!r}; "
            f"legal targets from {frm!r}: "
            f"{sorted(_TRANSITIONS.get(frm, ())) or '(none)'}")
