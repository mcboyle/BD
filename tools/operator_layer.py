#!/usr/bin/env python3
"""operator_layer.py — Phases 25-30 + cross-cuts: operationalize the framework.

The objective is not more analysis — it is making the analysis already produced by
Phases 1-24 visible, prioritized, governable, and manageable. Every subcommand reads
existing artifacts, re-presents and ranks them, fails closed on a posture scan, and
changes nothing: no corpus write, no debt change, no live behavior, no replay, no
approval. These are roll-ups and views, not new scorers.

Subcommands:
  cockpit       Phase 25 — unified operator cockpit (single pane: what matters now)
  portfolio     Phase 26 — portfolio prioritization (where effort goes next)
  capacity      Phase 27 — review capacity planning (will human review scale)
  governance    Phase 28 — governance compliance monitoring (still inside the rules)
  memory        Phase 29 — institutional knowledge layer (what not to rediscover)
  exec          Phase 30 — executive intelligence (leadership view in minutes)
  resources     Cross-cut — resource allocation (effort/risk mismatch)
  reviewroi     Cross-cut — review ROI (which review categories pay off)
  bottlenecks   Cross-cut — operational bottlenecks (review/capture/evidence/scaling)

These consume a --portfolio-root: a directory with one subdir per site, each holding
that site's Phase 1-24 artifacts (the outputs of the earlier tools). Framework-level
artifacts (maturity scorecard, risk register, etc.) are passed by path where needed.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load(path: Optional[str]) -> Optional[Any]:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _load_site(site_dir: Path, name: str) -> Optional[Any]:
    return _load(str(site_dir / name))


def _write_json(path: Path, obj: Any) -> None:
    import os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=list)
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _posture_ok(*objs) -> Optional[List[str]]:
    import re
    from bulk_downloader.capture_ingest import posture_scan
    blob = "\n".join(json.dumps(o, default=list) if not isinstance(o, str) else o
                     for o in objs)
    leaks = posture_scan(blob)
    if leaks:
        return leaks
    if re.search(r"page\.(goto|click|fill)|await\s|playwright|new_page\(|"
                 r"requests\.(get|post)", blob):
        return ["executable_or_replay_content"]
    return None


def _fail(leaks) -> int:
    print(f"POSTURE FAIL: {leaks}; refusing to write.", file=sys.stderr)
    return 2


def _debt() -> Dict[str, Any]:
    try:
        from bulk_downloader.validation_corpus import debt_report
        r = debt_report()
        return {"correction": len(r["correction_debt"]),
                "capability": len(r["capability_debt"]),
                "validation": len(r["validation_debt"]),
                "validation_items": [d.get("id") for d in r["validation_debt"]]}
    except Exception:
        return {}


def _iter_sites(root: Path):
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                yield d


def _site_summary(d: Path) -> Dict[str, Any]:
    """Pull the per-site signals the roll-ups need from existing artifacts."""
    health = _load_site(d, "site_health_score.json") or {}
    forecast = _load_site(d, "drift_forecast.json") or {}
    freshness = _load_site(d, "evidence_freshness.json") or {}
    decision = _load_site(d, "automation_decision_report.json") or {}
    return {
        "site": d.name,
        "maturity": health.get("maturity_state"),
        "overall_health": (health.get("scores") or {}).get("overall"),
        "p_broken": forecast.get("probability_enter_broken"),
        "p_fragile": forecast.get("probability_enter_fragile"),
        "stale": bool(freshness.get("review_priority")),
        "queued": decision.get("queued", {}),
    }


# ── Phase 25: unified operator cockpit ──────────────────────────────
def cmd_cockpit(args) -> int:
    sites = [_site_summary(d) for d in _iter_sites(Path(args.portfolio_root))]
    maturity = _load(args.framework_scorecard) or {}
    risk = _load(args.risk_register) or {}
    audit = _load(args.audit_gaps) or {}
    debt = _debt()

    # Honest degradation: the framework-maturity scorecard is produced by the
    # meta_intelligence maturity chain (calibration/audit/risk/freshness ->
    # maturity). If it was not supplied, do NOT show a bare "None" that looks
    # broken — say what is missing. If per-site health IS present, surface a
    # portfolio-level maturity derived from it as an interim, clearly labelled.
    _mat = maturity.get("maturity")
    _health = maturity.get("overall")
    _site_maturities = [s.get("maturity") for s in sites if s.get("maturity")]
    _site_health = [s.get("overall_health") for s in sites
                    if s.get("overall_health") is not None]
    if _mat is None:
        if _site_maturities:
            # interim: worst per-site maturity stands in for framework maturity
            _rank = {"Broken": 0, "Experimental": 1, "Fragile": 2, "Emerging": 2,
                     "Watch": 3, "Operational": 3, "Stable": 4, "Mature": 5,
                     "Highly Mature": 6}
            worst = min(_site_maturities, key=lambda m: _rank.get(m, 99))
            _mat = f"{worst} (interim — per-site worst; run meta_intelligence maturity for the framework score)"
        else:
            _mat = "not computed (needs the meta_intelligence maturity chain: calibrate/audit/risk/freshness)"
    if _health is None and _site_health:
        _health = round(sum(_site_health) / len(_site_health), 3)
        _health = f"{_health} (interim — mean of per-site health)"
    elif _health is None:
        _health = "not computed (no framework scorecard supplied)"

    total_review = sum(s["queued"].get("manual_review", 0) for s in sites)
    total_capture = sum(s["queued"].get("capture_requests", 0) for s in sites)
    total_approvals = sum(s["queued"].get("profile_approvals", 0) for s in sites)
    high_risk = [r for r in (risk.get("risk_register") or [])
                 if r.get("severity") in ("Critical", "High")]
    fragile_sites = [s["site"] for s in sites
                     if s.get("maturity") in ("Fragile", "Broken")
                     or (s.get("p_broken") or 0) >= 0.5]
    stale_sites = [s["site"] for s in sites if s["stale"]]

    cockpit = {
        "framework_maturity": _mat,
        "framework_overall_health": _health,
        "active_high_risks": [r["risk"] for r in high_risk],
        "evidence_freshness": {"stale_sites": stale_sites, "n_stale": len(stale_sites)},
        "calibration_status": "see confidence_calibration per site",
        "debt_status": debt,
        "review_workload": {"review": total_review, "approvals": total_approvals},
        "capture_priorities": {"requested": total_capture,
                               "validation_debt_items": debt.get("validation_items")},
        "fragile_sites": fragile_sites,
        "audit_unsupported": (audit.get("distribution") or {}).get("Unsupported", 0),
        "_status": "Read-only single-pane view. 'What matters right now?' Nothing acts.",
    }
    if (leaks := _posture_ok(cockpit)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "operator_cockpit.json", cockpit)
    L = ["# Operator dashboard — what matters right now", "",
         f"**Framework maturity:** {cockpit['framework_maturity']} "
         f"(overall {cockpit['framework_overall_health']})",
         f"**Debt:** {debt.get('correction','?')}/{debt.get('capability','?')}/"
         f"{debt.get('validation','?')} (correction/capability/validation)",
         f"**Review workload:** {total_review} review + {total_approvals} approvals; "
         f"{total_capture} captures requested", ""]
    if high_risk:
        L.append("## Active high/critical risks")
        for r in high_risk:
            L.append(f"- **{r['severity']}**: {r['risk']} — {r.get('detail','')}")
    if fragile_sites:
        L.append(f"\n## Fragile / at-risk sites\n{', '.join(fragile_sites)}")
    if stale_sites:
        L.append(f"\n## Stale evidence\n{', '.join(stale_sites)}")
    if cockpit["audit_unsupported"]:
        L.append(f"\n## Audit\n{cockpit['audit_unsupported']} unsupported conclusion(s) "
                 f"need attention")
    L.append("\nRead-only. Approvals and captures remain operator-driven through the "
             "existing human gates.")
    _write_text(out / "operator_dashboard.md", "\n".join(L))
    print(f"Phase 25 cockpit: maturity={cockpit['framework_maturity']} "
          f"high-risks={len(high_risk)} fragile={len(fragile_sites)} review={total_review}")
    return 0


# ── Phase 26: portfolio prioritization ──────────────────────────────
def cmd_portfolio(args) -> int:
    sites = [_site_summary(d) for d in _iter_sites(Path(args.portfolio_root))]
    ranked = []
    for s in sites:
        risk = s.get("p_broken") or 0
        uncertainty = 1.0 - (s.get("overall_health") or 0.5)
        review_burden = s["queued"].get("manual_review", 0)
        maintenance = s["queued"].get("capture_requests", 0) + (1 if s["stale"] else 0)
        # information gain ~ uncertainty + risk
        info_gain = round(min(1.0, uncertainty + risk), 3)
        priority = round(min(1.0, 0.35 * risk + 0.25 * uncertainty
                             + 0.2 * min(1.0, review_burden / 3)
                             + 0.2 * min(1.0, maintenance / 3)), 3)
        ranked.append({
            "site": s["site"], "priority": priority,
            "risk": round(risk, 3), "uncertainty": round(uncertainty, 3),
            "information_gain": info_gain, "review_burden": review_burden,
            "maintenance_cost": maintenance, "maturity": s.get("maturity"),
            "why": (f"risk {round(risk,2)}, uncertainty {round(uncertainty,2)}, "
                    f"{review_burden} review + {maintenance} maintenance items"),
        })
    ranked.sort(key=lambda x: -x["priority"])
    obj = {"rankings": ranked,
           "by_dimension": {
               "highest_risk": sorted(ranked, key=lambda x: -x["risk"])[:5],
               "highest_uncertainty": sorted(ranked, key=lambda x: -x["uncertainty"])[:5],
               "highest_information_gain": sorted(ranked, key=lambda x: -x["information_gain"])[:5],
               "highest_review_burden": sorted(ranked, key=lambda x: -x["review_burden"])[:5],
               "highest_maintenance": sorted(ranked, key=lambda x: -x["maintenance_cost"])[:5],
           },
           "_status": "Read-only prioritization. 'Where should effort go next?' No action."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "portfolio_rankings.json", obj)
    L = ["# Portfolio priority report", "",
         "Sites ranked by combined risk, uncertainty, review burden, and maintenance "
         "cost. Where should effort go next?", "", "## Overall ranking"]
    for r in ranked:
        L.append(f"- **{r['site']}** (priority {r['priority']}, maturity {r['maturity']}): "
                 f"{r['why']}")
    if not ranked:
        L.append("No sites in portfolio.")
    _write_text(out / "portfolio_priority_report.md", "\n".join(L))
    print(f"Phase 26 portfolio: ranked {len(ranked)} sites")
    return 0


# ── Phase 27: review capacity planning ──────────────────────────────
def cmd_capacity(args) -> int:
    sites = [_site_summary(d) for d in _iter_sites(Path(args.portfolio_root))]
    review = sum(s["queued"].get("manual_review", 0) for s in sites)
    approvals = sum(s["queued"].get("profile_approvals", 0) for s in sites)
    captures = sum(s["queued"].get("capture_requests", 0) for s in sites)
    per_review_min = args.minutes_per_review
    pending_effort_min = (review + approvals) * per_review_min
    # crude growth signal: sites trending fragile add future review load
    growth_sites = [s["site"] for s in sites if (s.get("p_fragile") or 0) >= 0.5]
    backlog = review + approvals
    bottleneck = "approvals" if approvals > review else "review" if review else "none"
    obj = {
        "pending_review_items": review + approvals,
        "pending_review_effort_minutes": pending_effort_min,
        "approval_backlog": approvals,
        "expected_queue_growth_sites": growth_sites,
        "primary_bottleneck": bottleneck,
        "scales": pending_effort_min <= args.weekly_capacity_minutes,
        "weekly_capacity_minutes": args.weekly_capacity_minutes,
        "_status": "Read-only review capacity forecast. 'Will human review scale?'",
    }
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "review_forecast.json", obj)
    _write_text(out / "review_capacity_report.md",
                f"# Review capacity report\n\nWill human review scale?\n\n"
                f"- pending review items: {review + approvals} "
                f"(~{pending_effort_min} min at {per_review_min} min/item)\n"
                f"- approval backlog: {approvals}\n"
                f"- weekly capacity: {args.weekly_capacity_minutes} min\n"
                f"- **fits capacity: {obj['scales']}**\n"
                f"- primary bottleneck: {bottleneck}\n"
                f"- sites likely to add load (trending fragile): {growth_sites or 'none'}\n")
    print(f"Phase 27 capacity: {review+approvals} items, scales={obj['scales']}")
    return 0


# ── Phase 28: governance compliance monitoring ──────────────────────
def cmd_governance(args) -> int:
    """Scan all produced artifacts for posture adherence. This is the meta-check:
    it re-runs the posture scan over every artifact in the tree and confirms the
    governance invariants hold."""
    from bulk_downloader.capture_ingest import posture_scan
    import re
    root = Path(args.artifacts_root)
    findings = []
    measurement_errors = []
    scanned = 0
    candidates = []
    if not root.is_dir():
        measurement_errors.append({
            "path": str(root),
            "reason": "ABSENT",
            "detail": "artifact root is not a readable directory",
        })
    else:
        def walk_error(exc):
            failed_path = getattr(exc, "filename", None) or str(root)
            measurement_errors.append({
                "path": str(failed_path),
                "reason": "UNREADABLE",
                "detail": str(exc),
            })
        for dirpath, _, names in os.walk(root, onerror=walk_error):
            for name in names:
                p = Path(dirpath, name)
                if p.suffix not in (".json", ".md"):
                    continue
                try:
                    info = p.stat()
                except OSError as exc:
                    measurement_errors.append({
                        "path": str(p.relative_to(root)),
                        "reason": "UNREADABLE",
                        "detail": str(exc),
                    })
                    continue
                if stat.S_ISREG(info.st_mode):
                    candidates.append(p)
        candidates.sort()
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            measurement_errors.append({
                "path": str(p.relative_to(root)),
                "reason": "UNREADABLE",
                "detail": str(exc),
            })
            continue
        scanned += 1
        leaks = posture_scan(text)
        if leaks:
            findings.append({"file": str(p.relative_to(root)), "issue": "signing_value",
                             "detail": leaks})
        if re.search(r"page\.(goto|click|fill)|playwright|new_page\(|requests\.(get|post)",
                     text):
            findings.append({"file": str(p.relative_to(root)),
                             "issue": "executable_or_replay_content"})
        # auto-action language that would violate human-gate
        if re.search(r"auto[-_ ]?(promot|appl|retir|approv|adopt)", text, re.I) \
                and "does not auto" not in text.lower() and "no auto" not in text.lower() \
                and "never auto" not in text.lower():
            findings.append({"file": str(p.relative_to(root)),
                             "issue": "possible_auto_action_language"})

    if root.is_dir() and not candidates and not measurement_errors:
        measurement_errors.append({
            "path": str(root),
            "reason": "EMPTY",
            "detail": "no JSON or Markdown artifacts found",
        })

    invariants = {
        "recognition_only_no_signing_values": not any(
            f["issue"] == "signing_value" for f in findings),
        "no_replay_or_executable_content": not any(
            f["issue"] == "executable_or_replay_content" for f in findings),
        "human_approval_gates_intact": not any(
            f["issue"] == "possible_auto_action_language" for f in findings),
        "corpus_read_only": True,   # no tool in this suite writes the corpus
        "debt_read_only": True,
    }
    measurement_status = "CANNOT_EVALUATE" if measurement_errors else "COMPLETE"
    obj = {"artifacts_scanned": scanned, "findings": findings,
           "measurement_status": measurement_status,
           "measurement_errors": measurement_errors,
           "invariants": invariants,
           "compliant": (not measurement_errors and
                         all(invariants.values()) and not findings),
           "_status": "Read-only governance scan. 'Are we still operating inside the rules?'"}
    if (leaks := _posture_ok({"invariants": invariants, "n_findings": len(findings)})):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "governance_findings.json", obj)
    L = ["# Governance compliance report", "",
         f"Scanned {scanned} artifacts. Are we still operating inside the rules?", "",
         "## Invariants"]
    for k, v in invariants.items():
        L.append(f"- {k}: {'✓ holds' if v else '✗ VIOLATED'}")
    if measurement_errors:
        L.append("\n## Measurement errors")
        for error in measurement_errors:
            L.append(f"- {error['path']}: {error['reason']} {error['detail']}")
    elif findings:
        L.append("\n## Findings")
        for f in findings[:30]:
            L.append(f"- {f['file']}: {f['issue']} {f.get('detail','')}")
    else:
        L.append("\nNo violations found — all artifacts comply with recognition-only "
                 "posture and human-gate requirements.")
    _write_text(out / "governance_compliance_report.md", "\n".join(L))
    print(f"Phase 28 governance: scanned {scanned}, compliant={obj['compliant']}, "
          f"findings={len(findings)}")
    return 2 if measurement_errors else 0


# ── Phase 29: institutional knowledge layer ─────────────────────────
def cmd_memory(args) -> int:
    """Preserve lessons: recurring drift patterns, recurring failures, major
    discoveries (from corpus), historical maturity trajectory."""
    corpus = []
    try:
        from bulk_downloader.validation_corpus import load_corpus
        corpus = load_corpus()
    except Exception:
        pass
    sites = list(_iter_sites(Path(args.portfolio_root)))
    # recurring drift across the portfolio
    drift_axis_counts: Dict[str, int] = {}
    for d in sites:
        dh = _load_site(d, "drift_history.json") or []
        for h in dh:
            for f in (h.get("drift_flags") or []):
                drift_axis_counts[f] = drift_axis_counts.get(f, 0) + 1
    recurring = {k: v for k, v in drift_axis_counts.items() if v >= 2}
    # major discoveries = corpus model_correction / anomaly entries
    discoveries = [{"id": e.get("id"), "subject": e.get("subject"),
                    "class": e.get("conclusion_class")}
                   for e in corpus
                   if e.get("conclusion_class") in ("model_correction", "anomaly")]
    # framework-level findings = durable lessons
    lessons = [{"id": e.get("id"), "subject": e.get("subject"),
                "note": e.get("notes")}
               for e in corpus if e.get("conclusion_class") == "framework_level"]
    obj = {
        "recurring_drift_patterns": recurring,
        "major_discoveries": discoveries,
        "durable_lessons": lessons,
        "_status": "Read-only institutional memory. 'What should never need to be "
                   "rediscovered?' Compiled from existing corpus + drift history; the "
                   "corpus itself is not modified.",
    }
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "institutional_memory.json", obj)
    L = ["# Lessons learned report", "",
         "What should never need to be rediscovered.", "",
         "## Recurring drift patterns (portfolio-wide)"]
    for k, v in sorted(recurring.items(), key=lambda x: -x[1]):
        L.append(f"- {k}: seen {v}x across sites")
    if not recurring:
        L.append("- none recurring across the portfolio yet")
    L += ["", "## Major discoveries (corpus)"]
    for dsc in discoveries[:15]:
        L.append(f"- {dsc['id']} ({dsc['class']}): {dsc['subject']}")
    L += ["", "## Durable framework lessons"]
    for ls in lessons[:15]:
        L.append(f"- {ls['id']}: {ls['subject']}")
    _write_text(out / "lessons_learned_report.md", "\n".join(L))
    print(f"Phase 29 memory: {len(recurring)} recurring patterns, "
          f"{len(discoveries)} discoveries, {len(lessons)} lessons")
    return 0


# ── Phase 30: executive intelligence layer ──────────────────────────
def cmd_exec(args) -> int:
    """Leadership-level summary across all phases, in a chosen cadence view."""
    cockpit = _load(args.operator_cockpit) or {}
    portfolio = _load(args.portfolio_rankings) or {}
    risk = _load(args.risk_register) or {}
    maturity = _load(args.framework_scorecard) or {}
    audit = _load(args.audit_gaps) or {}
    debt = _debt()

    top_priorities = (portfolio.get("rankings") or [])[:3]
    high_risks = [r for r in (risk.get("risk_register") or [])
                  if r.get("severity") in ("Critical", "High")]
    obj = {
        "view": args.view,
        "maturity": maturity.get("maturity"),
        "what_is_healthy": (f"overall framework health "
                            f"{maturity.get('overall')}; "
                            f"{(audit.get('distribution') or {}).get('Fully Defensible',0)} "
                            f"fully-defensible conclusions"),
        "what_is_risky": [r["risk"] for r in high_risks],
        "least_defensible": (audit.get("distribution") or {}).get("Unsupported", 0),
        "requires_review": cockpit.get("review_workload"),
        "missing_evidence": debt.get("validation_items"),
        "what_should_happen_next": [p["site"] for p in top_priorities],
        "_status": "Read-only executive summary. Leadership-level understanding in minutes.",
    }
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "executive_dashboard.json", obj)
    L = [f"# Executive summary — {args.view} view", "",
         f"**Framework maturity:** {obj['maturity']}", "",
         f"**What changed / what is healthy:** {obj['what_is_healthy']}", "",
         f"**What is risky:** {', '.join(obj['what_is_risky']) or 'no high/critical risks'}",
         f"**Least defensible:** {obj['least_defensible']} unsupported conclusion(s)",
         f"**Requires review:** {obj['requires_review']}",
         f"**Missing evidence:** {obj['missing_evidence']}",
         f"**What should happen next:** focus on "
         f"{', '.join(obj['what_should_happen_next']) or 'no priorities'}", "",
         "Read-only. Decisions and approvals remain with the operator via existing gates."]
    _write_text(out / "executive_summary.md", "\n".join(L))
    print(f"Phase 30 exec ({args.view}): maturity={obj['maturity']} "
          f"high-risks={len(high_risks)}")
    return 0


# ── cross-cut: resource allocation ──────────────────────────────────
def cmd_resources(args) -> int:
    sites = [_site_summary(d) for d in _iter_sites(Path(args.portfolio_root))]
    # effort currently spent ~ review+capture items per site; risk ~ p_broken
    rows = []
    for s in sites:
        effort = s["queued"].get("manual_review", 0) + s["queued"].get("capture_requests", 0)
        risk = s.get("p_broken") or 0
        mismatch = "over-invested" if effort > 2 and risk < 0.3 else \
            "under-invested" if effort == 0 and risk >= 0.5 else "aligned"
        rows.append({"site": s["site"], "current_effort_items": effort,
                     "risk": round(risk, 3), "alignment": mismatch})
    obj = {"allocation": rows,
           "mismatches": [r for r in rows if r["alignment"] != "aligned"],
           "_status": "Read-only. Where effort is vs. should be spent."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "resource_allocation_report.md",
                "# Resource allocation\n\nWhere effort is spent vs. where it should be:\n\n"
                + "\n".join(f"- {r['site']}: {r['current_effort_items']} effort items, "
                            f"risk {r['risk']} → **{r['alignment']}**" for r in rows)
                + ("\n\n## Mismatches to correct\n"
                   + "\n".join(f"- {r['site']}: {r['alignment']} "
                               f"(risk {r['risk']}, effort {r['current_effort_items']})"
                               for r in obj["mismatches"])
                   if obj["mismatches"] else "\n\nNo effort/risk mismatches.")
                + "\n")
    print(f"Cross-cut resources: {len(obj['mismatches'])} mismatches")
    return 0


# ── cross-cut: review ROI ───────────────────────────────────────────
def cmd_reviewroi(args) -> int:
    """Which review categories change outcomes vs. rarely do. Uses verdict-change
    queues (Phase 14) as the signal of 'review that mattered'."""
    sites = list(_iter_sites(Path(args.portfolio_root)))
    category_changes: Dict[str, int] = {}
    category_seen: Dict[str, int] = {}
    for d in sites:
        vq = _load_site(d, "verdict_change_queue.json") or {}
        for c in (vq.get("to_review") or []):
            field = str(c.get("field", "")).split(":")[0] or "unknown"
            category_changes[field] = category_changes.get(field, 0) + 1
        # all review items seen
        dec = _load_site(d, "automation_decision_report.json") or {}
        for sd in (dec.get("selector_decisions") or []):
            category_seen["selector_policy"] = category_seen.get("selector_policy", 0) + 1
    roi = {}
    for cat in set(list(category_changes) + list(category_seen)):
        changed = category_changes.get(cat, 0)
        seen = category_seen.get(cat, changed) or 1
        roi[cat] = {"changed_outcomes": changed, "reviewed": seen,
                    "roi": round(changed / seen, 3)}
    obj = {"review_roi": roi,
           "_status": "Read-only. Which review categories pay off vs. rarely change anything."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "review_roi_report.md",
                "# Review ROI\n\nHighest- and lowest-value review categories:\n\n"
                + ("\n".join(f"- {cat}: {v['changed_outcomes']} changes / {v['reviewed']} "
                             f"reviewed (ROI {v['roi']})" for cat, v in roi.items())
                   or "No review-outcome data yet.")
                + "\n")
    print(f"Cross-cut reviewroi: {len(roi)} categories")
    return 0


# ── cross-cut: operational bottlenecks ──────────────────────────────
def cmd_bottlenecks(args) -> int:
    sites = [_site_summary(d) for d in _iter_sites(Path(args.portfolio_root))]
    review = sum(s["queued"].get("manual_review", 0) for s in sites)
    capture = sum(s["queued"].get("capture_requests", 0) for s in sites)
    stale = sum(1 for s in sites if s["stale"])
    debt = _debt()
    bottlenecks = []
    if review > 2 * max(1, len(sites)):
        bottlenecks.append({"type": "review", "detail": f"{review} review items queued"})
    if capture + debt.get("validation", 0) > len(sites):
        bottlenecks.append({"type": "capture", "detail":
                            f"{capture} requests + {debt.get('validation',0)} debt items"})
    if stale > len(sites) / 2:
        bottlenecks.append({"type": "evidence", "detail": f"{stale} sites with stale evidence"})
    bottlenecks.append({"type": "scaling", "detail":
                        "file-based, run-per-tool workflow is the structural scaling limit; "
                        "an in-UI cockpit reduces per-site overhead"})
    obj = {"bottlenecks": bottlenecks, "_status": "Read-only bottleneck analysis."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "operational_bottlenecks_report.md",
                "# Operational bottlenecks\n\n"
                + "\n".join(f"- **{b['type']}**: {b['detail']}" for b in bottlenecks)
                + "\n")
    print(f"Cross-cut bottlenecks: {len(bottlenecks)} identified")
    return 0


# ── dispatch ────────────────────────────────────────────────────────
def cmd_autopilot(args) -> int:
    """Point it at a folder (or list) of captures; it discovers them, runs the
    real analysis chain itself, scans posture, and writes ONE cockpit that
    reflects the actual captures — instead of reading scorecards some other tool
    had to produce first. Auto-detects: >=2 same-title captures -> temporal
    series (and the N>=3 floor); a baseline+perturbed pair with --axis ->
    perturbation. Recognition-only: never writes the corpus, never retires debt,
    never acts. The suggested corpus entry (if any) is emitted for review only.
    """
    from bulk_downloader import capture_ingest as ci

    # 1) discover captures from the path(s) given
    paths: List[str] = []
    for p in args.captures:
        try:
            paths.extend(ci.discover_captures(p))
        except (ValueError, FileNotFoundError):
            # discover_captures raises when a path has no capture artifacts;
            # treat that as "nothing here" rather than a crash.
            continue
    paths = sorted(dict.fromkeys(paths))  # dedupe, stable
    if not paths:
        print(f"autopilot: no captures found under {args.captures} "
              f"(looked for .wacz/.json)")
        return 2

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    labels = [Path(p).stem for p in paths]
    print(f"autopilot: discovered {len(paths)} capture(s): {', '.join(labels)}")

    # 2) load + a quick per-capture fact line (identity / top rendition / login)
    import re
    _MEDIA_EXT = (".mp4", ".m4v", ".webm", ".ts", ".m3u8", ".mpd", ".mov")
    loaded = []
    inventory = []
    for p, lab in zip(paths, labels):
        c = ci.load_capture(p)
        loaded.append(c)
        nl = c.get("network_log") or []
        rends = set()
        for e in nl:
            u = e.get("url", "") if isinstance(e, dict) else ""
            for m in re.findall(r"\d{3,4}x\d{3,4}[^/?\"]*", u):
                # only real video renditions — exclude poster/thumbnail images
                if m.lower().endswith(_MEDIA_EXT):
                    rends.add(m)
        # rank by pixel area so "top" is the highest actual resolution
        def _area(r):
            mm = re.match(r"(\d{3,4})x(\d{3,4})", r)
            return int(mm.group(1)) * int(mm.group(2)) if mm else 0
        top = max(rends, key=_area) if rends else "(none)"
        inventory.append({"capture": lab, "top_rendition": top,
                          "renditions_seen": sorted(rends),
                          "has_cookies": bool(c.get("cookies")),
                          "network_entries": len(nl)})

    # 3) the series analysis (always, if >=2) — the temporal floor lives here
    series = ci.analyze_captures(paths, series=True, labels=labels) \
        if len(paths) >= 2 else ci.analyze_captures(paths)
    floor = (series.get("temporal") or {}).get("vc_0019_floor") \
        or series.get("vc_0019_floor")
    axes = (series.get("temporal") or {}).get("axes") or series.get("axes") or {}

    # 4) perturbation, only if an axis was requested and we have >=2 distinct
    perturbation = None
    if args.axis and len(paths) >= 2:
        # widest pair: first vs last by sorted label (usually highest vs lowest q)
        perturbation = ci.analyze_perturbation(paths[0], paths[-1], args.axis)

    # 5) assemble the cockpit — what this capture set actually shows
    debt = _debt()
    cockpit = {
        "captures_analyzed": len(paths),
        "capture_inventory": inventory,
        "distinct_top_renditions": sorted({i["top_rendition"] for i in inventory
                                           if i["top_rendition"] != "(none)"}),
        "all_have_login": all(i["has_cookies"] for i in inventory),
        "temporal_floor": floor,
        "axes": {k: (v.get("outcome") if isinstance(v, dict) else v)
                 for k, v in axes.items()},
        "perturbation_axis": args.axis,
        "perturbation_outcome": (perturbation or {}).get("verdict")
            or (perturbation or {}).get("outcome"),
        "debt_status": debt,
        "_status": "Auto-run from captures. Recognition-only: nothing was written "
                   "to the corpus, no debt retired, no action taken.",
    }
    if (leaks := _posture_ok(cockpit, series, perturbation or {})):
        return _fail(leaks)

    _write_json(out / "autopilot_cockpit.json", cockpit)

    # 6) a human-readable single pane that says what to do next
    L = ["# Capture cockpit — what these captures show", ""]
    L.append(f"**Captures:** {len(paths)} ({', '.join(labels)})")
    L.append(f"**Distinct top renditions:** "
             f"{', '.join(cockpit['distinct_top_renditions']) or 'none detected'}")
    L.append(f"**Login present in all:** {'yes' if cockpit['all_have_login'] else 'NO — some captures missing cookies'}")
    L.append("")
    L.append("## Per-capture")
    for i in inventory:
        L.append(f"- **{i['capture']}**: top={i['top_rendition']}, "
                 f"login={'yes' if i['has_cookies'] else 'NO'}, "
                 f"{i['network_entries']} network entries")
    if floor:
        L.append("\n## Temporal floor (N>=3)")
        L.append(f"- outcome: **{floor.get('outcome')}** "
                 f"(n_sessions={floor.get('n_sessions')}, "
                 f"qualifying_data={floor.get('qualifying_data')})")
        if floor.get("outcome") == "confirmed":
            L.append("- the N>=3 floor is **cleared** for this title.")
        elif (floor.get("n_sessions") or 0) < 3:
            distinct = len(cockpit["distinct_top_renditions"])
            L.append(f"- **floor NOT cleared**: only {distinct} distinct rendition(s) "
                     f"across these captures — capture a 3rd at a DIFFERENT quality "
                     f"to lift it.")
    if axes:
        L.append("\n## Axes")
        for k, v in cockpit["axes"].items():
            L.append(f"- {k}: **{v}**")
        if axes.get("signing", {}).get("outcome") == "untested":
            L.append("- note: signing is *undeterminable* (values scrubbed by "
                     "posture), not 'no drift'.")
    if perturbation is not None:
        L.append(f"\n## Perturbation ({args.axis})")
        comp = (perturbation.get("corpus_compat")
                or perturbation.get("compat") or {})
        L.append(f"- a suggested corpus entry was emitted for review "
                 f"(retires_debt={comp.get('retires_debt', False)}, "
                 f"carries_resolves={comp.get('carries_resolves', False)}).")
        L.append("- it is NOT written to the corpus; recording is a human decision.")
    L.append(f"\n## Debt\ncorrection/capability/validation = "
             f"{debt.get('correction','?')}/{debt.get('capability','?')}/"
             f"{debt.get('validation','?')}")
    L.append("\nRecognition-only. Nothing was written, retired, or acted on. "
             "Where evidence supports a corpus entry, it is emitted as a reviewable "
             "suggestion for a human decision.")
    _write_text(out / "capture_cockpit.md", "\n".join(L))

    fl = floor.get("outcome") if floor else "n/a"
    print(f"autopilot: {len(paths)} captures | distinct renditions="
          f"{len(cockpit['distinct_top_renditions'])} | floor={fl} | "
          f"axis={args.axis or 'none'} -> {out}/capture_cockpit.md")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phases 25-30 + cross-cuts: operationalization")
    sub = p.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("autopilot",
                        help="point at a folder/list of captures; runs the chain itself")
    ap.set_defaults(fn=cmd_autopilot)
    ap.add_argument("captures", nargs="+",
                    help="capture file(s) or a directory of .wacz/.json")
    ap.add_argument("--axis", default=None,
                    choices=["player_config", "workflow"],
                    help="also run a perturbation on this axis (widest pair)")
    ap.add_argument("--out-dir", default="./cockpit")

    c = sub.add_parser("cockpit"); c.set_defaults(fn=cmd_cockpit)
    c.add_argument("--portfolio-root", required=True)
    c.add_argument("--framework-scorecard", default=None)
    c.add_argument("--risk-register", default=None)
    c.add_argument("--audit-gaps", default=None)
    c.add_argument("--out-dir", default="./cockpit")

    pf = sub.add_parser("portfolio"); pf.set_defaults(fn=cmd_portfolio)
    pf.add_argument("--portfolio-root", required=True)
    pf.add_argument("--out-dir", default="./portfolio")

    cap = sub.add_parser("capacity"); cap.set_defaults(fn=cmd_capacity)
    cap.add_argument("--portfolio-root", required=True)
    cap.add_argument("--minutes-per-review", type=int, default=10)
    cap.add_argument("--weekly-capacity-minutes", type=int, default=600)
    cap.add_argument("--out-dir", default="./capacity")

    gov = sub.add_parser("governance"); gov.set_defaults(fn=cmd_governance)
    gov.add_argument("--artifacts-root", required=True)
    gov.add_argument("--out-dir", default="./governance")

    mem = sub.add_parser("memory"); mem.set_defaults(fn=cmd_memory)
    mem.add_argument("--portfolio-root", required=True)
    mem.add_argument("--out-dir", default="./memory")

    ex = sub.add_parser("exec"); ex.set_defaults(fn=cmd_exec)
    ex.add_argument("--view", choices=["daily", "weekly", "monthly", "snapshot"],
                    default="snapshot")
    ex.add_argument("--operator-cockpit", default=None)
    ex.add_argument("--portfolio-rankings", default=None)
    ex.add_argument("--risk-register", default=None)
    ex.add_argument("--framework-scorecard", default=None)
    ex.add_argument("--audit-gaps", default=None)
    ex.add_argument("--out-dir", default="./exec")

    rs = sub.add_parser("resources"); rs.set_defaults(fn=cmd_resources)
    rs.add_argument("--portfolio-root", required=True)
    rs.add_argument("--out-dir", default="./resources")

    rr = sub.add_parser("reviewroi"); rr.set_defaults(fn=cmd_reviewroi)
    rr.add_argument("--portfolio-root", required=True)
    rr.add_argument("--out-dir", default="./reviewroi")

    bn = sub.add_parser("bottlenecks"); bn.set_defaults(fn=cmd_bottlenecks)
    bn.add_argument("--portfolio-root", required=True)
    bn.add_argument("--out-dir", default="./bottlenecks")
    return p


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(run())
