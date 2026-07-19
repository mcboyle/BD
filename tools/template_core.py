#!/usr/bin/env python3
"""
template_core.py — shared core for the template-health ecosystem (consolidation).

Single source of truth for template scanning, scoring, and the derived views the
report tools wrap. There is exactly one scanner (`template_inventory.scan`) and one
scorer (`template_inventory.assess` / `_WEIGHTS`); this module composes them and
lets callers thread a single `scan` result through every derived view so a report
build performs ONE scan instead of four.

Public surface (consumed by the thin wrappers; names chosen to match what the
wrappers already returned, so output shapes are byte-for-byte preserved):

  scan(root)                 -> template_inventory.scan(root)            (the walk)
  assess, _WEIGHTS                                                       (the scorer)
  completeness(scan)         -> {"rows", "aggregate"}        (== score_tree output)
  warnings(scan)             -> {"findings","by_warning","total"} (== catalog output)
  analytics(root, scan=None) -> the template_analytics.analyze() dict
  health(root)               -> the template_health_report.build() dict (1 scan)

Read-only; never writes templates, promotes, fetches, or persists anything.
"""
import os
import statistics
from collections import Counter

import template_inventory as _TI  # type: ignore

# re-export the single scanner + scorer
scan = _TI.scan
assess = _TI.assess
_WEIGHTS = _TI._WEIGHTS

# Standard rungs we like to see covered (informational, not a gate).
_STANDARD_RUNGS = [2160, 1440, 1080, 720, 540, 480, 360, 240]


def _dist(scores):
    if not scores:
        return {"n": 0, "min": None, "mean": None, "median": None, "max": None}
    return {"n": len(scores), "min": min(scores),
            "mean": round(statistics.mean(scores), 1),
            "median": statistics.median(scores), "max": max(scores)}


def completeness(scan):
    """Per-template scores + aggregate (== template_completeness_score.score_tree)."""
    rows = []
    for name, items in scan["dirs"].items():
        for a in items:
            rows.append({"dir": name, "host": a["host"], "status": a["status"],
                         "score": a["completeness_score"], "missing": a["missing"],
                         "promotion_ready": a["promotion_ready"]})
    scores = [r["score"] for r in rows]
    agg = {"n": len(scores),
           "mean": round(sum(scores) / len(scores), 1) if scores else None,
           "min": min(scores) if scores else None,
           "max": max(scores) if scores else None,
           "fully_complete": sum(1 for s in scores if s == 100)}
    return {"rows": rows, "aggregate": agg}


def warnings(scan):
    """Warning catalog across the tree (== template_warning_catalog.catalog)."""
    findings = []
    counts = Counter()
    for name, items in scan["dirs"].items():
        for a in items:
            src = a["source"]

            def add(w):
                findings.append({"source": src, "host": a["host"], "warning": w})
                counts[w] += 1
            if not a["download_trigger"]:
                add("missing_trigger")
            if not a["row_selectors_count"]:
                add("missing_row_selectors")
            if not a["api_base"]:
                add("missing_api_base")
            if not a["resolutions_count"]:
                add("missing_resolutions")
            if not a["network_patterns_count"]:
                add("missing_network_patterns")
            for t in a["blocked_terms"]:
                add(f"blocked_term:{t}")
            if not a["promotion_ready"]:
                add("not_promotion_ready")
    for s in scan["sanity"]:
        findings.append({"source": "sanity", "host": None, "warning": s})
        counts["sanity_violation"] += 1
    return {"findings": findings, "by_warning": dict(counts.most_common()),
            "total": len(findings)}


def _drift_counts(root):
    """Per draft/candidate with a reviewed gold, count drift items per category
    (reuses template_drift_report). Point-in-time only — no history store."""
    try:
        import template_drift_report as DR  # type: ignore
    except Exception:  # noqa: BLE001
        return {"available": False}
    summary = {"available": True, "compared": 0, "by_category": Counter(),
               "per_file": {}}
    for sub in ("drafts", "review_candidates"):
        d = os.path.join(root, "templates", sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                cand = DR._load(os.path.join(d, fn))
                gold_rel = DR._default_gold(cand)
                if not gold_rel:
                    continue
                gold_path = os.path.join(root, gold_rel)
                if not os.path.isfile(gold_path):
                    continue
                gold = DR._load(gold_path)
                out = []
                DR.diff_selectors(cand, gold, out)
                DR.diff_row_selectors(cand, gold, out)
                DR.diff_resolutions(cand, gold, out)
                DR.diff_api(cand, gold, out)
                DR.diff_network_patterns(cand, gold, out)
            except Exception:  # noqa: BLE001
                continue
            summary["compared"] += 1
            summary["per_file"][f"{sub}/{fn}"] = len(out)
            for item in out:
                cat = (item.get("kind") or item.get("category")
                       or item.get("type") or "drift") if isinstance(item, dict) else "drift"
                summary["by_category"][cat] += 1
    summary["by_category"] = dict(summary["by_category"])
    return summary


def analytics(root=".", scan=None):
    """Aggregate analytics (== template_analytics.analyze). Accepts a pre-computed
    scan so callers can avoid re-scanning."""
    if scan is None:
        scan = _TI.scan(root)
    all_scores, per_dir_scores = [], {}
    blocked = Counter()
    rung_freq = Counter()
    group_cov = Counter()
    ladder_lens = []
    api_base_present = total = gate_ready = 0
    for name, items in scan["dirs"].items():
        per_dir_scores[name] = []
        for a in items:
            total += 1
            all_scores.append(a["completeness_score"])
            per_dir_scores[name].append(a["completeness_score"])
            for t in a["blocked_terms"]:
                blocked[t] += 1
            for r in a["resolutions"]:
                rung_freq[int(r) if str(r).isdigit() else r] += 1
            ladder_lens.append(a["resolutions_count"])
            for g in a["selector_groups"]:
                group_cov[g] += 1
            if a["api_base"]:
                api_base_present += 1
            if a["promotion_ready"]:
                gate_ready += 1
    return {
        "root": os.path.abspath(root),
        "counts": scan["counts"],
        "total_templates": total,
        "completeness": {
            "overall": _dist(all_scores),
            "per_dir": {k: _dist(v) for k, v in per_dir_scores.items()},
        },
        "gate_ready": {"count": gate_ready, "total": total,
                       "rate": round(gate_ready / total, 3) if total else None},
        "api_base_present": {"count": api_base_present, "total": total,
                             "rate": round(api_base_present / total, 3) if total else None},
        "blocked_term_frequency": dict(blocked.most_common()),
        "resolution_coverage": {
            "rungs_seen": sorted((r for r in rung_freq if isinstance(r, int)), reverse=True),
            "standard_rungs": _STANDARD_RUNGS,
            "standard_covered": sorted((r for r in _STANDARD_RUNGS if rung_freq.get(r)),
                                       reverse=True),
            "ladder_length": _dist(ladder_lens),
        },
        "selector_group_coverage": dict(group_cov.most_common()),
        "drift": _drift_counts(root),
        "sanity": scan["sanity"],
    }


def health(root="."):
    """Umbrella health view (== template_health_report.build) using ONE scan."""
    s = _TI.scan(root)                       # the single scan
    a = analytics(root, scan=s)              # reuse it
    w = warnings(s)                          # reuse it
    comp = completeness(s)                   # reuse it
    per_template = []
    for name, items in s["dirs"].items():
        for t in items:
            per_template.append({
                "dir": name, "host": t["host"], "status": t["status"],
                "trigger_present": t["download_trigger"],
                "row_selectors_present": bool(t["row_selectors_count"]),
                "api_block_present": bool(t["api_base"]),
                "resolutions_present": bool(t["resolutions_count"]),
                "network_patterns_present": bool(t["network_patterns_count"]),
                "blocked_terms": t["blocked_terms"],
                "completeness_score": t["completeness_score"],
                "promotion_ready": t["promotion_ready"],
            })
    return {
        "root": os.path.abspath(root),
        "summary": {
            "total": a["total_templates"],
            "counts": a["counts"],
            "gate_ready_rate": a["gate_ready"]["rate"],
            "completeness": a["completeness"]["overall"],
        },
        "per_template": per_template,
        "warnings": w["by_warning"],
        "completeness": comp["aggregate"],
        "drift_current": a["drift"],
        "drift_history": {"note": "history accrues once per-run snapshots are "
                          "persisted; current drift only (no store created while away)"},
        "sanity": s["sanity"],
    }
