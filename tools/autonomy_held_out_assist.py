"""Phase K — held-out designation **ASSIST**. The last code phase of the autonomy track.

READ + COMPUTE + PROPOSE only. This module never writes anything. Its job is to make a human's
held-out designation faster and safer by answering, for a candidate capture:
  • is it safe to designate? (independent of training; no raw signing material — the gate's own
    hard-fail rules, applied to the candidate)
  • what tier would the site reach if it were designated? (the REAL `oracle_verdict`, run with the
    candidate appended — never a parallel reimplementation that could drift permissive)
  • is the site's existing held-out evidence going stale?

WHY THIS IS NOT AN APPLY-KIND (load-bearing)
  Held-out designation is the input to the tier-3 gate that authorizes `live_site_config` autonomy.
  If it could be done autonomously, the system could manufacture its own tier-3 and self-expand
  authority — forbidden (*authority may contract automatically but never expand automatically*). So
  the designation write is PERMANENTLY HUMAN (it is a `corpus_writes` action, in
  `PERMANENTLY_INELIGIBLE`). This module is assist-only: not grantable, no autonomous write, no
  reverser. The human acts via `tools/autonomy_designate.py`.

SINGLE SOURCE OF TRUTH
  The tier verdict and the hard-fail rules come from `autonomy_oracle` (`oracle_verdict`,
  `_training_captures`, `_has_raw_signing_value`). This module composes them; it does not restate
  them. Capture access goes through injectable readers so tests need no captures dir.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Dict, List, Optional

from tools import autonomy_oracle as ao


def _stale_days() -> int:
    try:
        from bulk_downloader import global_config as _gc
        _s = _gc.get("held_out_stale_days", None)
        if _s not in (None, ""):
            return max(1, int(_s))
    except Exception:
        pass
    try:
        return max(1, int(os.environ.get("BD_HELD_OUT_STALE_DAYS", "180")))
    except Exception:
        return 180


# ── injectable readers (tests monkeypatch these) ─────────────────────────────
def _descriptor_for(name: str) -> Optional[Dict[str, Any]]:
    """Posture-safe descriptor (names/shapes only) for a capture, or None if not loadable.
    Mirrors ao._held_out_descriptors' per-name derivation via the capture metadata."""
    try:
        from tools.cockpit_templates import _capture_meta
        from tools.cockpit_core import captures_root
        root = captures_root()
        meta = _capture_meta(root / name) if root else None
    except Exception:
        meta = None
    if not (meta and meta.get("loaded")):
        return None
    return {"capture": name,
            "identity": meta.get("identity") or name,
            "renditions": meta.get("renditions", []),
            "template_shape": "media_present" if meta.get("media_events") else "thin",
            "signing_marker_names": meta.get("signing_markers", [])}


def _available_captures() -> List[str]:
    """All capture names present in the captures root (designated or not)."""
    try:
        from tools.cockpit_core import captures_root
        root = captures_root()
        if not root or not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_file())
    except Exception:
        return []


# ── candidate evaluation (pure; reuses the oracle's own checks/verdict) ───────
def evaluate_candidate(site: str, capture_name: str) -> Dict[str, Any]:
    """Integrity verdict + tier-delta for designating `capture_name` as held-out for `site`.
    Never writes. REJECT reasons mirror the gate's hard-fails and cannot be overridden here."""
    out: Dict[str, Any] = {"site": site, "capture": capture_name, "eligible": False,
                           "reasons": [], "current_tier": ao.oracle_verdict(site)["tier"],
                           "projected_tier": None, "advances": False}
    desc = _descriptor_for(capture_name)
    if desc is None:
        out["reasons"].append("capture not loadable / no descriptor")
        out["projected_tier"] = out["current_tier"]
        return out

    # hard-fail rules — the SAME rules as the gate, applied to the candidate
    if capture_name in ao._training_captures(site):
        out["reasons"].append("REJECT: overlaps training evidence (not independent)")
    if ao._has_raw_signing_value(desc):
        out["reasons"].append("REJECT: raw signing value present (descriptors are names only — posture)")
    already = capture_name in ao._held_out_capture_names(site)
    if already:
        out["reasons"].append("already designated held-out")
    rejected = any(r.startswith("REJECT") for r in out["reasons"])

    # tier-delta: the REAL verdict, run with the candidate appended to held-out descriptors.
    projected = ao.oracle_verdict(
        site, held_out=ao._held_out_descriptors(site) + [desc])["tier"]
    out["projected_tier"] = 0 if rejected else projected
    out["advances"] = (not rejected) and (out["projected_tier"] > out["current_tier"])
    if not rejected and not already:
        out["eligible"] = True
        if not (desc.get("identity") and desc.get("renditions")):
            out["reasons"].append("note: partial descriptors — would yield only a weak tier")
    return out


def _decay(designated_at: Any) -> Dict[str, Any]:
    """Surface staleness of a site's held-out designation. Eligibility owns the authoritative
    decay decision; this is an advisory flag for the assist."""
    if not designated_at:
        return {"stale": None, "age_days": None, "reason": "no held_out_designated_at recorded"}
    try:
        ts = _dt.datetime.fromisoformat(str(designated_at))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        age = (_dt.datetime.now(_dt.timezone.utc) - ts).days
        return {"stale": age > _stale_days(), "age_days": age,
                "threshold_days": _stale_days()}
    except Exception:
        return {"stale": None, "age_days": None, "reason": "unparseable designated_at"}


def site_designation(site: str) -> Dict[str, Any]:
    prov = ao._provenance().get(site, {})
    held = ao._held_out_capture_names(site)
    training = ao._training_captures(site)
    cands = [c for c in _available_captures() if c not in held and c not in training]
    evals = [evaluate_candidate(site, c) for c in cands]
    evals.sort(key=lambda e: ((e.get("projected_tier") or 0), bool(e.get("eligible"))), reverse=True)
    return {"site": site,
            "current_tier": ao.oracle_verdict(site)["tier"],
            "held_out": held, "training": training,
            "held_out_designated_at": prov.get("held_out_designated_at"),
            "decay": _decay(prov.get("held_out_designated_at")),
            "candidates": evals,
            "_note": "Read-only assist. Designation is human-only (autonomy_designate); "
                     "this proposes candidates + tier impact, it never writes."}


def designation_report() -> Dict[str, Any]:
    return {"sites": [site_designation(s) for s in ao._all_sites()],
            "_note": "Held-out designation assist (Phase K). Proposes candidates and their tier "
                     "impact using the oracle's own verdict; the designation write is permanently "
                     "human. This module writes nothing."}
