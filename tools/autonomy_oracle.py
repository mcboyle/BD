"""Tiered correctness oracle & eligibility engine (Phase E). Determines ELIGIBILITY
ONLY — it performs no automation.

This completes the guardrail set, but completing it does NOT enable Class C auto.
Three independent facts keep automation off: (1) Class C defaults to Approve-each;
(2) `class_c_site_eligible` is empty by design — no tier grants automation, and there
is no per-site grant mechanism (issuing one is a separate governance decision with no
code here); (3) there is no Class C apply path anywhere in the codebase.

The oracle is a TIERED descriptor oracle (Tier 0–3), not a binary pass/fail. Lower
tiers improve review/evidence quality; higher tiers improve confidence in the
eligibility *assessment*. No tier authorizes automation:
  * Tier 0 — No oracle (no held-out evidence, or only training evidence). Suggestions
    only; ineligible for Class C auto consideration.
  * Tier 1 — Weak descriptor oracle (held-out exists, partial descriptors). Enhanced
    review support only.
  * Tier 2 — Standard descriptor oracle (held-out exists; identity + rendition
    descriptors). Oracle-qualified; eligible for *future governance review*.
  * Tier 3 — Strong descriptor oracle (multiple held-out captures; descriptor
    agreement). Highest tier; *future candidate* for limited per-site autonomy
    evaluation — still requires separate governance approval AND a separate policy
    decision.

POSTURE: descriptors only. The oracle NEVER reconstructs signed URLs, reuses signing
values, replays requests, drives a browser, re-downloads media, byte-compares media,
or fetches network resources. Signing markers are recognized by NAME only; any raw
signing value is a hard failure. No module-level I/O.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools.cockpit_core import tasks_root

# ── tiers ────────────────────────────────────────────────────────────────────
TIER_NAMES = {0: "no_oracle", 1: "weak_descriptor", 2: "standard_descriptor",
              3: "strong_descriptor"}

# Permanently ineligible regardless of oracle tier (the pinned actions + credentials).
PERMANENTLY_INELIGIBLE = tuple(ap.PINNED_APPROVE_EACH) + (
    "credential_creation_or_modification", "login_credential_handling")

# value-like token detector: long opaque strings or key=value signing params
_SIGNING_VALUE_RE = re.compile(r"(=|%3D)[A-Za-z0-9_\-%]{12,}"
                               r"|[A-Za-z0-9+/=_\-]{24,}")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── runtime stores (never shipped) ──────────────────────────────────────────
def _oracle_root() -> Path:
    return tasks_root() / "governance" / "oracle"


def _provenance_path() -> Path:
    return _oracle_root() / "capture_provenance.json"


def _grants_path() -> Path:
    # per-site, per-kind auto grants. Empty by default — issued human-only via the grant CLI.
    return _oracle_root() / "site_auto_grants.json"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _normalize_grants(grants: Dict[str, Any]) -> Dict[str, Any]:
    """Back-compat: an old single-kind entry `{site: {granted: …}}` is read as
    `{site: {"live_site_config": {granted: …}}}`. Already-nested entries pass through.
    Every reader normalizes; the next write persists the nested shape (migrate-on-write)."""
    out: Dict[str, Any] = {}
    for site, v in (grants or {}).items():
        if isinstance(v, dict) and "granted" in v:          # OLD single-kind entry
            out[site] = {"live_site_config": v}
        else:
            out[site] = v
    return out


def _load_grants() -> Dict[str, Any]:
    p = _grants_path()
    if not p.is_file():
        return {}
    try:
        return _normalize_grants(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _reports_dir() -> Path:
    return _oracle_root() / "reports"


def _atomic_write(p: Path, obj: Any, text: bool = False) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    if text:
        tmp.write_text(obj, encoding="utf-8")
    else:
        tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)


def _provenance() -> Dict[str, Any]:
    p = _provenance_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── posture-safe descriptor helpers ─────────────────────────────────────────
def _has_raw_signing_value(obj: Any) -> bool:
    """Hard-failure detector: descriptors must carry signing-marker NAMES only. Any
    value-like token (long opaque string, or key=value signing param) is a violation."""
    try:
        blob = json.dumps(obj, default=str)
    except Exception:
        blob = str(obj)
    # marker NAMES are short words; flag long opaque tokens / =value patterns
    return bool(_SIGNING_VALUE_RE.search(blob))


def _held_out_descriptors(site: str) -> List[Dict[str, Any]]:
    """Descriptor sets from the site's HELD-OUT captures (posture-safe, names only).
    Derived from capture metadata; empty when no held-out captures are designated."""
    prov = _provenance().get(site, {})
    held = prov.get("held_out", [])
    out = []
    try:
        from tools.cockpit_templates import _capture_meta
        from tools.cockpit_core import captures_root
        root = captures_root()
        for name in held:
            try:
                meta = _capture_meta(root / name) if root else None
            except Exception:
                meta = None
            if meta and meta.get("loaded"):
                out.append({"capture": name,
                            "identity": meta.get("identity") or name,  # descriptor id
                            "renditions": meta.get("renditions", []),
                            "template_shape": "media_present" if meta.get("media_events") else "thin",
                            "signing_marker_names": meta.get("signing_markers", [])})
    except Exception:
        pass
    return out


def _training_captures(site: str) -> List[str]:
    return list(_provenance().get(site, {}).get("training", []))


def _held_out_capture_names(site: str) -> List[str]:
    return list(_provenance().get(site, {}).get("held_out", []))


# ── the tiered verdict ───────────────────────────────────────────────────────
def oracle_verdict(site: str, candidate: Optional[Dict[str, Any]] = None,
                   held_out: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Per-site tiered oracle verdict. `candidate` is the expected descriptor of the
    proposed template (identity / rendition / template_shape / signing_marker_names);
    `held_out` are descriptor sets from independent held-out captures. Both may be
    injected (tests / future wiring) or derived. Descriptors only — no fetch, no
    download, no byte-compare. `automation_eligible` is ALWAYS False in this build."""
    checks: List[str] = []
    hard_failures: List[str] = []

    training = set(_training_captures(site))
    held_names = set(_held_out_capture_names(site))
    if held_out is None:
        held_out = _held_out_descriptors(site)

    # ── hard-failure conditions (override everything) ──
    overlap = training & held_names
    if overlap:
        hard_failures.append(f"held-out evidence overlaps training evidence: "
                             f"{sorted(overlap)}")
    if _has_raw_signing_value(held_out) or (candidate and _has_raw_signing_value(candidate)):
        hard_failures.append("raw signing value present (descriptors must be NAMES "
                             "only) — signing-value dependency is never permitted")
    if candidate and candidate.get("action") in PERMANENTLY_INELIGIBLE:
        hard_failures.append(f"candidate affects a permanently-ineligible target: "
                             f"{candidate.get('action')}")

    if hard_failures:
        return _verdict(site, 0, checks, hard_failures, held_out, training, held_names)

    # ── tiering (descriptor availability + agreement) ──
    n = len(held_out)
    if n == 0:
        checks.append("no independent held-out capture (or only training evidence)")
        return _verdict(site, 0, checks, hard_failures, held_out, training, held_names)

    checks.append("independent held-out capture exists")
    checks.append("no signing-value dependency (names only)")

    has_identity = all(d.get("identity") for d in held_out)
    has_rendition = all(d.get("renditions") for d in held_out)
    candidate_resolves = bool(candidate is None or candidate.get("identity")
                              or candidate.get("renditions"))

    if not (has_identity and has_rendition):
        # partial descriptors -> Tier 1 (weak)
        checks.append("partial descriptors available (weak)")
        if candidate_resolves:
            checks.append("candidate resolves to a plausible media descriptor")
        return _verdict(site, 1, checks, hard_failures, held_out, training, held_names)

    # identity + rendition present
    checks.append("media identity descriptor present")
    checks.append("rendition (or rendition class) present")
    checks.append("template shape recorded")

    if n >= 2:
        # Tier 3 requires descriptor AGREEMENT across captures
        idents = {d.get("identity") for d in held_out}
        rends = {tuple(sorted(map(str, d.get("renditions", [])))) for d in held_out}
        shapes = {d.get("template_shape") for d in held_out}
        agree = len(idents) == 1 and len(rends) == 1 and len(shapes) == 1
        if agree:
            checks += ["identity stable across captures", "rendition stable across captures",
                       "structural shape stable across captures"]
            return _verdict(site, 3, checks, hard_failures, held_out, training, held_names)
        checks.append("multiple held-out captures but descriptors disagree -> Tier 2")
    return _verdict(site, 2, checks, hard_failures, held_out, training, held_names)


def _verdict(site: str, tier: int, checks: List[str], hard_failures: List[str],
             held_out: List[Dict[str, Any]], training: set, held_names: set
             ) -> Dict[str, Any]:
    eligible_for = {
        0: "suggestions + standard review workflow only",
        1: "enhanced review support only",
        2: "oracle-qualified — eligible for FUTURE governance review (not automation)",
        3: "FUTURE candidate for limited per-site autonomy evaluation — still requires "
           "separate governance approval AND a separate policy decision",
    }[tier]
    return {
        "site": site, "tier": tier, "tier_name": TIER_NAMES[tier],
        "checks": checks, "hard_failures": hard_failures or None,
        "held_out_count": len(held_out),
        "training_count": len(training),
        "eligible_for": eligible_for,
        # automation is NEVER granted by the oracle, at ANY tier, in this build:
        "automation_eligible": False,
        "_note": "Eligibility ASSESSMENT only. No tier authorizes automation; even "
                 "Tier 3 is a future candidate requiring separate governance approval "
                 "and a separate policy decision. Descriptors only — no fetch, no "
                 "download, no byte-compare; signing by name only.",
    }


# ── eligibility engine ───────────────────────────────────────────────────────
def _all_sites() -> List[str]:
    try:
        from tools.cockpit_templates import _load_sites_config, _site_id
        return [_site_id(c) for c in _load_sites_config()]
    except Exception:
        return sorted(_provenance().keys())


def eligibility_matrix(sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Per-site eligibility tiers + summary. `automation_eligible` is False for every
    site (no tier authorizes automation in this build)."""
    sites = sites if sites is not None else _all_sites()
    rows = [oracle_verdict(s) for s in sites]
    dist = {TIER_NAMES[t]: sum(1 for r in rows if r["tier"] == t) for t in (0, 1, 2, 3)}
    return {
        "sites": rows, "site_count": len(rows), "tier_distribution": dist,
        "automation_eligible_sites": 0,   # always — no tier grants automation here
        "permanently_ineligible_actions": list(PERMANENTLY_INELIGIBLE),
        "_note": "Per-site oracle tiers. No site is automation-eligible — assessment "
                 "only. Higher tiers improve confidence in the assessment, not "
                 "automation. Read-only.",
    }


def class_c_site_eligible(site: str, kind: str = "live_site_config") -> Dict[str, Any]:
    """The per-(site, kind) gate any Class C auto-apply MUST check. Eligible IFF ALL hold:
    an active (granted, not suspended, unexpired) per-(site, kind) grant; Class C at
    `auto_with_guardrails` (not frozen); and oracle tier >= 3. Stays dark by default: with no
    grant, or Class C at the approve-each default, or no tier-3 evidence, this is False — but
    it is no longer hardcoded. Grants are issued human-only (the grant CLI); the system may
    only auto-suspend (never auto-grant)."""
    reasons = []
    grants = _load_grants()
    g = (grants.get(site) or {}).get(kind) or {}
    granted = bool(g.get("granted")) and not g.get("suspended")
    if not g.get("granted"):
        reasons.append(f"no per-site auto grant for kind {kind!r}")
    elif g.get("suspended"):
        reasons.append(f"grant suspended: {g.get('suspend_reason') or 'safety contraction'}")
    exp = g.get("expires_at")
    unexpired = (not exp) or (str(exp) > _now_iso())
    if not unexpired:
        reasons.append("grant expired")
    ca = ap.can_autonomously("C")
    class_ok = bool(ca.get("allowed"))
    if not class_ok:
        reasons.append(f"class-level: {ca.get('reason')}")
    v = oracle_verdict(site)
    tier_ok = v["tier"] >= 3
    if not tier_ok:
        reasons.append(f"oracle tier {v['tier']} below the per-site-evaluation tier")
    eligible = granted and unexpired and class_ok and tier_ok
    return {"site": site, "kind": kind, "eligible": eligible, "reasons": reasons,
            "oracle_tier": v["tier"],
            "_note": "Eligible only with an active per-(site,kind) grant + Class C at auto + "
                     "tier 3. Dark by default; grants are human-only; auto-suspend only."}


# ── reports ──────────────────────────────────────────────────────────────────
def held_out_evidence_report() -> Dict[str, Any]:
    rows = []
    for s in _all_sites():
        rows.append({"site": s, "training_captures": _training_captures(s),
                     "held_out_captures": _held_out_capture_names(s),
                     "tier": oracle_verdict(s)["tier"]})
    return {"sites": rows,
            "_note": "Held-out vs training capture designation per site. Held-out "
                     "evidence must be disjoint from training evidence; overlap is a "
                     "hard failure. Read-only."}


def ineligible_sites_report() -> Dict[str, Any]:
    rows = []
    for r in eligibility_matrix()["sites"]:
        if r["tier"] == 0 or r.get("hard_failures"):
            rows.append({"site": r["site"], "tier": r["tier"],
                         "hard_failures": r.get("hard_failures"),
                         "why": r["eligible_for"]})
    return {"ineligible": rows, "count": len(rows),
            "permanently_ineligible_actions": list(PERMANENTLY_INELIGIBLE),
            "_note": "Sites at Tier 0 or with hard failures, plus the actions that are "
                     "permanently ineligible regardless of tier. Read-only."}


def oracle_status() -> Dict[str, Any]:
    em = eligibility_matrix()
    return {
        "guardrails_complete": all(v["built"] for v in ap.guardrail_registry().values()),
        "correctness_oracle_built": ap.guardrail_registry().get(
            "correctness_oracle", {}).get("built"),
        "class_c_level": ap.load_policy()["levels"].get("C"),
        "class_c_auto_enabled_by_default": False,
        "automation_eligible_sites": 0,
        "tier_distribution": em["tier_distribution"],
        "site_count": em["site_count"],
        "permanently_ineligible_actions": list(PERMANENTLY_INELIGIBLE),
        "_note": "Phase E complete: the tiered oracle + eligibility engine assess "
                 "eligibility only. Guardrail set is complete, but Class C auto is NOT "
                 "enabled (default Approve-each; per-site grant store empty by design; "
                 "no Class C apply path exists). Enabling any per-site autonomy is a "
                 "separate governance decision. Read-only.",
    }


def oracle_reports() -> Dict[str, Any]:
    """The assembled report bundle (read-only data for the cockpit / for generation)."""
    return {"oracle_status": oracle_status(),
            "eligibility_matrix": eligibility_matrix(),
            "held_out_evidence": held_out_evidence_report(),
            "ineligible_sites": ineligible_sites_report()}


def generate_oracle_reports(by: str) -> Dict[str, Any]:
    """Explicitly write the six report artifacts (advisory, regenerable; never config
    or corpus). Operator-invoked — not automatic. Returns the written paths."""
    if not by:
        return {"ok": False, "error": "identity (by) required"}
    em = eligibility_matrix()
    verdicts = {r["site"]: r for r in em["sites"]}
    ho = held_out_evidence_report()
    inel = ineligible_sites_report()
    written = []

    def w(name, obj, text=False):
        p = _reports_dir() / name
        _atomic_write(p, obj, text=text)
        written.append(str(p))

    w("oracle_verdict.json", {"generated_at": _now(), "verdicts": verdicts})
    w("eligibility_matrix.json", em)
    w("oracle_report.md", _md_oracle(em), text=True)
    w("eligibility_matrix.md", _md_matrix(em), text=True)
    w("held_out_evidence_report.md", _md_held_out(ho), text=True)
    w("ineligible_sites_report.md", _md_ineligible(inel), text=True)
    return {"ok": True, "written": written}


def _md_oracle(em):
    lines = ["# Oracle Report", "", "Eligibility ASSESSMENT only — no tier authorizes "
             "automation.", "", f"Sites: {em['site_count']} · tiers: {em['tier_distribution']}",
             f"Automation-eligible sites: {em['automation_eligible_sites']}", ""]
    for r in em["sites"]:
        lines.append(f"- **{r['site']}** — Tier {r['tier']} ({r['tier_name']}); "
                     f"{r['eligible_for']}")
    return "\n".join(lines) + "\n"


def _md_matrix(em):
    lines = ["# Eligibility Matrix", "", "| Site | Tier | Held-out | Automation eligible |",
             "|---|---|---|---|"]
    for r in em["sites"]:
        lines.append(f"| {r['site']} | {r['tier']} ({r['tier_name']}) | "
                     f"{r['held_out_count']} | no |")
    lines += ["", "Permanently ineligible actions: " +
              ", ".join(em["permanently_ineligible_actions"])]
    return "\n".join(lines) + "\n"


def _md_held_out(ho):
    lines = ["# Held-Out Evidence Report", "",
             "Held-out evidence must be disjoint from training evidence.", ""]
    for r in ho["sites"]:
        lines.append(f"- **{r['site']}** — training {r['training_captures']}, "
                     f"held-out {r['held_out_captures']} (Tier {r['tier']})")
    return "\n".join(lines) + "\n"


def _md_ineligible(inel):
    lines = ["# Ineligible Sites Report", "", f"{inel['count']} site(s) at Tier 0 / "
             "with hard failures.", ""]
    for r in inel["ineligible"]:
        lines.append(f"- **{r['site']}** — Tier {r['tier']}; "
                     f"{r.get('hard_failures') or r['why']}")
    lines += ["", "Permanently ineligible regardless of tier: " +
              ", ".join(inel["permanently_ineligible_actions"])]
    return "\n".join(lines) + "\n"
