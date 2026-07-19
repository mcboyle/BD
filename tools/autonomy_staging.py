"""Staged Config Candidate Maintenance (v1) — the FIRST bounded autonomy wire.

This maintains a confined, reversible, per-site STAGED config candidate. It exercises the
full autonomy chain on the lowest-risk write target in the system:

  oracle/trust gates → staged candidate apply → record_change("staging_json", …)
  → register_pending(...) → fail-closed review window → auto-revert if not accepted
  → transition/audit log.

v1 autonomy means MAINTAINING THE STAGED CANDIDATE ONLY. It deliberately does NOT:
  * write production sites_config.json;        * promote to live config;
  * change login / template / live extraction;  * mutate behavioral fields;
  * write corpus entries;                       * retire correction debt;
  * confirm or falsify findings;                * change automation policy;
  * handle credentials;                         * launch captures;
  * interact with third-party sites.

AUTHORITY MODEL. `participation_eligible` means "may auto-apply to LIVE config"; it stays
False and is NOT consulted here. v1 introduces a strictly weaker authority,
`staging_eligible(site)` — "may maintain a reversible STAGED candidate" — sound precisely
because a staged write auto-reverts, is never promoted automatically, and never affects
production behavior.

CREDENTIAL SAFETY. The live site block contains secrets (username/password/cookie paths).
The staged candidate embeds a CREDENTIAL-REDACTED projection of the live block's behavioral
fields — secrets are never embedded, never written to the staging file, never shown in the
cockpit. The loop authors ONLY the `evidence` annotation; the behavioral projection is
copied verbatim, so v1 introduces no behavioral change.

The apply paths (`maintain_staged_candidate`, `maintain_all`) and the fail-closed sweep are
HOST-SCHEDULED (cron/CLI alongside decay_all_trust + scan_and_record). None is a cockpit
button. Human accept/reject reuses the existing audited `/api/review/decide` path.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el
from tools import autonomy_trust as atr
from tools import autonomy_impact as ai

try:  # canonical I0008 secret-keyword floor (F-TOOLSO04-03); tools stay
    # importable without the app present, falling back to the local _SECRET_RE.
    from bulk_downloader.capture_artifact_redact import (
        _kv_key_is_secret as _floor_key_is_secret)
except Exception:  # pragma: no cover
    def _floor_key_is_secret(_k):
        return False
from tools import autonomy_guardrails as agr
from tools import autonomy_apply as aap

SCHEMA = "staged_config/v1"

# Keys that are selectors / paths (NOT secrets) and may be embedded.
_NON_SECRET_KEYS = {"user_field", "pass_field", "submit_btn", "success_url", "output",
                    "url", "domain", "wait", "comment", "learned", "scoring"}
# Account identifiers / PII — redacted.
_PII_KEYS = {"username", "user", "email", "login"}
_SECRET_RE = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|cookie|auth|credential|session)", re.I)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _is_secret_key(k: Any) -> bool:
    kl = str(k).lower()
    if kl in _NON_SECRET_KEYS:
        return False
    if kl in _PII_KEYS:
        return True
    # Delegate the floor to the canonical SoT after the local selector/PII
    # allowlists (which keep winning) -- F-TOOLSO04-03.
    return bool(_SECRET_RE.search(kl)) or _floor_key_is_secret(kl)


def _redact_behavioral(block: Dict[str, Any]) -> Dict[str, Any]:
    """Credential-redacted projection of a live site block. Secrets/PII never embedded."""
    return {k: v for k, v in (block or {}).items() if not _is_secret_key(k)}


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _behavioral_hash(behavioral: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(behavioral).encode("utf-8")).hexdigest()


def _sanitize(site: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(site))[:120] or "site"


def _target_ref(site: str) -> str:
    # Flat staging_json target (no subdir) so the existing reverser round-trips cleanly.
    return f"staged_config__{_sanitize(site)}.json"


def _candidate_file(site: str):
    return agr._staging_dir() / _target_ref(site)


# ── reads (no writes) ────────────────────────────────────────────────────────

def _live_block(site: str) -> Optional[Dict[str, Any]]:
    """The live site dict from sites_config.json, READ-ONLY. None if absent."""
    try:
        from tools.cockpit_templates import _load_sites_config, _site_id
        for cfg in _load_sites_config():
            if _site_id(cfg) == site:
                return cfg
    except Exception:
        return None
    return None


def _evidence_block(site: str) -> Dict[str, Any]:
    ov = ao.oracle_verdict(site)
    elig = el.evaluate_site(site)
    return {"oracle_tier": ov.get("tier"), "tier_name": ov.get("tier_name"),
            "held_out_count": ov.get("held_out_count"),
            "evidence_qualified": elig.get("evidence_qualified"),
            "trust": atr.effective_trust(site), "validated_at": _now()}


def _read_candidate(site: str) -> Dict[str, Any]:
    p = _candidate_file(site)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_candidate(site: str, live: Dict[str, Any],
                     evidence: Dict[str, Any]) -> Dict[str, Any]:
    behavioral = _redact_behavioral(live)
    redacted = sorted(k for k in (live or {}) if _is_secret_key(k))
    return {"schema": SCHEMA, "site": site, "created_ts": _now(), "by": "system",
            "behavioral": behavioral,
            "evidence": evidence,
            "provenance": {"behavioral_hash": _behavioral_hash(behavioral),
                           "redacted_keys": redacted, "source": f"oracle_verdict@{_now()}"},
            "_note": "Behavioral fields are a CREDENTIAL-REDACTED copy of live (secrets "
                     "never embedded); the loop authors only `evidence`. Reversible staged "
                     "candidate; promotion to live is manual; v1 proposes no behavioral "
                     "change."}


# ── gate ─────────────────────────────────────────────────────────────────────

def staging_eligible(site: str) -> Dict[str, Any]:
    """The v1 gate — strictly weaker than participation_eligible, which is NOT consulted.
    Re-evaluated at apply time, never cached."""
    imp = ai.impact_report({"site": site, "target_kind": "staging_json"})
    ok = bool(imp.get("evidence_qualified")
              and imp.get("oracle_tier") == 3
              and imp.get("trust_eligible")
              and imp.get("reversible")
              and imp.get("inflight_ok")
              and not imp.get("family_wide")
              and not imp.get("touches_pinned"))
    return {"ok": ok, "site": site, "reasons": imp.get("concerns", []),
            "participation_eligible": imp.get("participation_eligible", False),
            "_note": "staging_eligible is separate from and weaker than "
                     "participation_eligible (which stays False and gates LIVE apply, not "
                     "this)."}


# ── apply (host-scheduled; never a cockpit button) ────────────────────────────

def _staging_proposer(site: str) -> Optional[Dict[str, Any]]:
    """Build the staged candidate (None if the site isn't in sites_config). Late-binds
    `_live_block` / `_evidence_block` / `_build_candidate` so test monkeypatches apply."""
    live = _live_block(site)
    if live is None:
        return None
    return _build_candidate(site, live, _evidence_block(site))


def _staging_applier(site: str, after: Dict[str, Any]) -> None:
    f = _candidate_file(site)
    f.parent.mkdir(parents=True, exist_ok=True)
    agr._atomic_write_json(f, after)


def _staging_unchanged(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    # idempotent IFF the behavioral block and the two evidence facts match (timestamps and
    # other evidence fields are allowed to differ) — preserves v1's exact idempotency.
    return bool(before.get("behavioral") == after.get("behavioral")
                and before.get("evidence", {}).get("oracle_tier")
                == after.get("evidence", {}).get("oracle_tier")
                and before.get("evidence", {}).get("evidence_qualified")
                == after.get("evidence", {}).get("evidence_qualified"))


# Register the staged-candidate kind with the generic Class-C harness. Its gate is the WEAKER
# `staging_eligible` (impact_report) — it does NOT join the per-(site, kind) grant model and
# does NOT consult `participation_eligible`. Reverser is the existing Phase-C safe one.
aap.register_apply_kind(
    "staging_json",
    gate=lambda s: bool(staging_eligible(s)["ok"]),
    current=lambda s: _read_candidate(s),
    proposer=_staging_proposer,
    applier=_staging_applier,
    reverser=agr._restore_staging_json,
    unchanged=_staging_unchanged,
    target_ref=lambda s: _target_ref(s),
    transition_field="staged_candidate",
)


def maintain_staged_candidate(site: str, *, by: str = "system") -> Dict[str, Any]:
    """Stage/refresh one site's candidate IFF staging_eligible. Reversible; pending; never
    promotes; never writes production config. Thin wrapper over the generic harness;
    behavior identical to the former bespoke flow."""
    return aap.apply_for_kind(site, "staging_json", by=by)


def maintain_all(*, by: str = "system",
                 sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """The autonomous loop: stage candidates for every staging_eligible site, one at a time.
    Empty oracle/trust stores ⇒ nothing qualifies ⇒ no candidates."""
    r = aap.apply_all("staging_json", by=by, sites=sites)
    return {"ok": True, "scanned": r["scanned"], "staged": r["applied"],
            "staged_count": r["applied_count"], "skipped": r["skipped"],
            "_note": "v1 staged-candidate maintenance. No production write, no promotion, "
                     "no behavioral change. Empty stores qualify nothing."}


# ── read-only views (cockpit) ─────────────────────────────────────────────────

def _pending_staged() -> List[Dict[str, Any]]:
    """Pending guardrails changes that are our staged candidates."""
    out = []
    for v in agr.outstanding_unreviewed():
        cid = v.get("change_id")
        rec = agr.change_record(cid) if cid else None
        if not rec or rec.get("target_kind") != "staging_json":
            continue
        ref = rec.get("target_ref", "")
        if not str(ref).startswith("staged_config__"):
            continue
        out.append({"change_id": cid, "site": v.get("site"),
                    "deadline": v.get("deadline"), "applied_ts": v.get("applied_ts"),
                    "record": rec})
    return out


def staged_candidates() -> Dict[str, Any]:
    """List of pending staged candidates with evidence summary + behavioral-unchanged
    check. Read-only."""
    rows = []
    for p in _pending_staged():
        site = p["site"]
        cand = (p["record"] or {}).get("after", {}) or _read_candidate(site or "")
        ev = cand.get("evidence", {})
        live = _live_block(site or "")
        unchanged = (live is not None
                     and cand.get("behavioral") == _redact_behavioral(live))
        rows.append({"site": site, "change_id": p["change_id"], "deadline": p["deadline"],
                     "oracle_tier": ev.get("oracle_tier"), "trust": ev.get("trust"),
                     "behavioral_unchanged": unchanged})
    return {"candidates": rows, "count": len(rows),
            "_note": "Pending staged candidates. Accept/reject via the audited review path "
                     "(reject reverts immediately; silence reverts at the deadline; accept "
                     "blesses without promoting)."}


def staged_candidate(site: str) -> Dict[str, Any]:
    """One site's staged candidate: evidence delta, behavioral-unchanged confirmation,
    metadata-only diff vs live, rollback-preview, deadline. Read-only."""
    cand = _read_candidate(site)
    if not cand:
        return {"site": site, "exists": False,
                "_note": "No staged candidate for this site."}
    live = _live_block(site)
    live_behavioral = _redact_behavioral(live) if live is not None else None
    unchanged = (live_behavioral is not None
                 and cand.get("behavioral") == live_behavioral)
    pend = next((p for p in _pending_staged() if p["site"] == site), None)
    cid = pend["change_id"] if pend else None
    rec = agr.change_record(cid) if cid else None
    rollback_preview = (rec or {}).get("before") if rec else None
    return {"site": site, "exists": True,
            "evidence": cand.get("evidence", {}),
            "behavioral_unchanged": unchanged,
            "behavioral_keys": sorted((cand.get("behavioral") or {}).keys()),
            "redacted_keys": cand.get("provenance", {}).get("redacted_keys", []),
            "change_id": cid, "deadline": pend.get("deadline") if pend else None,
            "rollback_preview": rollback_preview,
            "_note": "Read-only. Behavioral fields are a credential-redacted copy of live "
                     "(no secrets). v1 proposes no behavioral change; promotion is manual; "
                     "accept blesses without promoting; silence/reject reverts."}


def staging_status() -> Dict[str, Any]:
    """Compact header: pending count, next deadline, eligible-site count. Read-only."""
    pend = _pending_staged()
    deadlines = sorted([p["deadline"] for p in pend if p.get("deadline")])
    try:
        eligible = sum(1 for s in ao._all_sites() if staging_eligible(s)["ok"])
    except Exception:
        eligible = 0
    return {"pending_count": len(pend), "next_deadline": deadlines[0] if deadlines else None,
            "eligible_sites": eligible, "review_window_hours": agr.REVIEW_WINDOW_HOURS,
            "_note": "v1 staged-candidate maintenance. Maintains staged candidates only — "
                     "no production write, no promotion, no behavioral change. "
                     "participation_eligible stays False."}
