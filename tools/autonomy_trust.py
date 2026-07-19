"""Per-site trust scoring with automatic DECAY (Phase G / G3).

The load-bearing invariant — the one Phase H is built on — is: **trust may only ever
DECREASE automatically.** Adverse signals (a dropped oracle tier, stale evidence, a high
rollback or review-expiry rate, a freeze, an oracle hard-failure) lower a site's trust;
favourable signals NEVER raise it on their own. The only way trust goes back UP is an
explicit operator action (`reset_trust`, which requires a human identity) — a governance
decision, with no automatic caller in this build. Authority therefore never expands
automatically; it only contracts as risk rises.

Mechanics:
  * `signal_trust(site)` — a pure [0,1] score from the current real signals. No state.
  * `effective_trust(site)` — the stored, ratcheted value (defaults to BASELINE for an
    unseen site). Read-only.
  * `decay_trust(site, by)` — the auto-decay MUTATOR: stored := min(stored, signal). It
    can only LOWER trust; an improved signal leaves the floor untouched. Host-scheduled
    (cron/CLI), never a cockpit button.
  * `reset_trust(site, value, by, reason)` — the ONLY raise. Requires a human `by`;
    logged as a governance action; no automatic caller exists.
  * `trust_eligible(site)` — `effective_trust >= MIN_TRUST`. The eligibility layer (G1)
    consults this: a site whose trust has decayed below the floor is not eligible until a
    human restores it — even if its current evidence looks good.

This module is NOT read-only (decay/reset write the trust store), but it performs no
forbidden mutation: it never writes the corpus, changes policy, applies/reverts a change,
drives a browser, fetches the network, or touches credentials. The store write is atomic
(`.tmp` + replace, UTF-8). No module-level I/O.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools import autonomy_oracle as ao
from tools import autonomy_guardrails as agr
from tools.cockpit_core import tasks_root

# ── thresholds / weights ──────────────────────────────────────────────────────
BASELINE_TRUST = 1.0    # an unseen / never-decayed site starts fully trusted
MIN_TRUST = 0.5         # eligibility floor — below this a site cannot participate
TRUST_FRESH_DAYS = 30   # evidence older than this is a staleness penalty

# signal-penalty weights (sum bounded; score clamped to [0,1])
_W_TIER = 0.6           # full penalty at Tier 0, none at Tier 3
_W_STALE = 0.3
_W_ROLLBACK = 0.5
_W_EXPIRY = 0.3
_W_HARDFAIL = 0.4
_FROZEN_CAP = 0.1       # frozen -> trust capped very low


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _as_dt(value: Any) -> Optional[_dt.datetime]:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        try:
            dt = _dt.datetime.fromisoformat(str(value))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


# ── store ─────────────────────────────────────────────────────────────────────
def _trust_root() -> Path:
    return tasks_root() / "governance" / "trust"


def _trust_path() -> Path:
    return _trust_root() / "trust_state.json"


def _atomic_write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)


def _load() -> Dict[str, Any]:
    p = _trust_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _evidence_ts_for(site: str) -> Optional[str]:
    """When the site's held-out evidence was designated (oracle provenance). Read-only."""
    return ao._provenance().get(site, {}).get("held_out_designated_at")


# ── signal score (pure) ─────────────────────────────────────────────────────────
def signal_trust(site: str, *, held_out: Optional[List[Dict[str, Any]]] = None,
                 evidence_ts: Any = "__derive__", now: Any = None,
                 candidate: Optional[Dict[str, Any]] = None) -> float:
    """Current trust SIGNAL in [0,1] from real signals (no state, no side effects):
    oracle tier, evidence freshness, global rollback + review-expiry rates, oracle
    hard-failures, and freeze state. This is the FLOOR the ratchet may decay toward; it
    is never used to raise stored trust."""
    now_dt = _as_dt(now) or _now_dt()
    v = ao.oracle_verdict(site, candidate=candidate, held_out=held_out)
    tier = v["tier"]
    hard = v.get("hard_failures")

    if evidence_ts == "__derive__":
        evidence_ts = _evidence_ts_for(site)
    ets = _as_dt(evidence_ts)
    fresh = ets is not None and (now_dt - ets).total_seconds() / 86400.0 <= TRUST_FRESH_DAYS

    thr = agr.throttle_metrics()
    rb = min(float(thr.get("rollback_rate") or 0.0), 1.0)
    ex = min(float(thr.get("review_expiry_rate") or 0.0), 1.0)

    score = 1.0
    score -= (3 - min(tier, 3)) / 3.0 * _W_TIER
    if not fresh:
        score -= _W_STALE
    score -= rb * _W_ROLLBACK
    score -= ex * _W_EXPIRY
    if hard:
        score -= _W_HARDFAIL
    score = max(0.0, min(1.0, score))
    if ap.is_frozen():
        score = min(score, _FROZEN_CAP)
    return round(score, 3)


# ── stored (ratcheted) trust ──────────────────────────────────────────────────
def effective_trust(site: str) -> float:
    """The stored, ratcheted trust for `site` (defaults to BASELINE for an unseen site).
    Read-only — never writes, never raises the value."""
    rec = _load().get(site)
    return float(rec["trust"]) if rec and "trust" in rec else BASELINE_TRUST


def trust_eligible(site: str) -> bool:
    """Whether `site`'s trust is at or above the eligibility floor. The eligibility layer
    consults this."""
    return effective_trust(site) >= MIN_TRUST


def decay_trust(site: str, by: str = "system", **signal_kwargs: Any) -> Dict[str, Any]:
    """AUTO-DECAY: stored trust := min(stored, signal). Can only LOWER trust — an improved
    signal leaves the ratcheted floor untouched, so trust never rises here. Host-scheduled
    (cron/CLI), not a cockpit button. Returns the new value and whether it decreased."""
    sig = signal_trust(site, **signal_kwargs)
    cur = effective_trust(site)
    new = min(cur, sig)          # the ratchet — never raises
    st = _load()
    prior = st.get(site, {})
    st[site] = {"trust": round(new, 3), "signal": sig, "updated_at": _now(), "by": by,
                "raised_by": prior.get("raised_by"), "raised_at": prior.get("raised_at")}
    _atomic_write_json(_trust_path(), st)
    return {"ok": True, "site": site, "trust": round(new, 3), "signal": sig,
            "previous": round(cur, 3), "decreased": new < cur,
            "_note": "Auto-decay only lowers trust. Restoring trust is a human "
                     "governance action (reset_trust)."}


def decay_all_trust(by: str = "system") -> Dict[str, Any]:
    """Decay every configured site once (host-scheduled). Only lowers. Read-derived
    signals; no corpus/policy/credential writes."""
    out = []
    for s in ao._all_sites():
        out.append(decay_trust(s, by=by))
    return {"ok": True, "decayed": len(out),
            "decreased": [r["site"] for r in out if r["decreased"]],
            "_note": "Host-invoked. Trust only ever decreases here."}


def reset_trust(site: str, value: float, by: str, reason: str = "") -> Dict[str, Any]:
    """The ONLY path that RAISES trust — an explicit human governance action. Requires a
    human identity (`by`); there is no automatic caller in this build. Logged."""
    if not by:
        return {"ok": False, "error": "identity (by) required — restoring trust is a "
                                      "human governance action"}
    v = max(0.0, min(1.0, float(value)))
    st = _load()
    prior = st.get(site, {})
    st[site] = {"trust": round(v, 3), "signal": prior.get("signal"),
                "updated_at": _now(), "by": by, "raised_by": by, "raised_at": _now(),
                "reason": reason}
    _atomic_write_json(_trust_path(), st)
    return {"ok": True, "site": site, "trust": round(v, 3), "raised_by": by}


# ── read-only rollups ───────────────────────────────────────────────────────────
def trust_site(site: str, **signal_kwargs: Any) -> Dict[str, Any]:
    """Per-site detail: stored (effective) trust, current signal, eligibility, and the
    last human restore if any. Read-only."""
    rec = _load().get(site, {})
    eff = effective_trust(site)
    sig = signal_trust(site, **signal_kwargs)
    return {"site": site, "trust": eff, "signal": sig,
            "min_trust": MIN_TRUST, "trust_eligible": eff >= MIN_TRUST,
            "would_decay_to": round(min(eff, sig), 3),
            "raised_by": rec.get("raised_by"), "raised_at": rec.get("raised_at"),
            "updated_at": rec.get("updated_at"),
            "_note": "Trust decays automatically toward the signal floor; only a human "
                     "reset raises it. Below min_trust => not eligible."}


def trust_overview(sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Per-site trust rollup. Read-only."""
    sites = sites if sites is not None else ao._all_sites()
    rows = [trust_site(s) for s in sites]
    below = [r["site"] for r in rows if not r["trust_eligible"]]
    return {"sites": rows, "site_count": len(rows), "min_trust": MIN_TRUST,
            "below_min": below, "below_min_count": len(below),
            "frozen": ap.is_frozen(),
            "_note": "Read-only. Trust only decreases automatically; a site below "
                     "min_trust is ineligible until a human restores it."}


def trust_status() -> Dict[str, Any]:
    """Compact status for the cockpit header. Read-only."""
    ov = trust_overview()
    return {"min_trust": MIN_TRUST, "baseline_trust": BASELINE_TRUST,
            "trust_fresh_days": TRUST_FRESH_DAYS,
            "site_count": ov["site_count"], "below_min_count": ov["below_min_count"],
            "frozen": ov["frozen"],
            "_note": "Trust scoring with automatic decay. Trust may only ever DECREASE "
                     "automatically; restoring it is a human governance action. Feeds "
                     "eligibility (below min_trust => ineligible)."}
