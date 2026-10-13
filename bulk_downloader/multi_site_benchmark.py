"""multi_site_benchmark.py — test the generalization layer's PREDICTION against
unrelated ecosystems.

The workbench already commits to a label for every conclusion: framework_level
(transfers verbatim), reusable_class (the rule transfers, the instance is local),
or site_specific (local). This harness checks those commitments by building a
workbench per site and comparing the labels head-to-head. It does NOT produce a
fresh impressionistic "is this general?" claim — it confirms or falsifies the
labels the layer already made, the same way the temporal harness tests the churn
taxonomy.

Confirmation criteria (from MULTI_SITE_BENCHMARK_SPEC.md):
  * a reusable_class is CONFIRMED when the same class reproduces on >=2 sites
    with DIFFERENT instances (rule transfers, instance is local).
  * a framework-level method property (e.g. goal_selection being the lone robust
    sensitivity conclusion) is CONFIRMED when it holds on ALL sites.
  * a site_specific label is CONFIRMED when the conclusion's OUTCOME differs
    across sites (e.g. goal classification: direct_file here, unknown there) —
    i.e. it correctly did not over-generalize.
  * an ANOMALY is flagged when a rule reproduces but with wrong granularity —
    e.g. the segment-role identity detector over-splitting a CDN-sharded path
    into many identity slots.

POSTURE (load-bearing): recognition-only. This reads workbench OUTPUT — which is
itself recognition-only (it terminates at classification / candidate detectors,
never at bytes). It never reassembles HLS, computes signing material, or replays.
Signing is compared by marker NAME only. For HLS sites whose goal classifies as
`unknown`/new-provider, that classification is surfaced, not "resolved".
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple

from .capture_synth import synthesize
from .capture_workbench import build_workbench

# An identity-slot count above this on a single goal path suggests the path is
# CDN-sharded (one logical id split across many short dirs), not many distinct
# identities — the role detector reproducing but over-splitting.
_OVER_SPLIT_THRESHOLD = 3


def site_profile(wb_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the comparison fields from one site's workbench to_dict()."""
    sk = wb_dict.get("skeleton") or {}
    imp = wb_dict.get("impact") or {}
    gen = wb_dict.get("generalization") or {}
    sens = wb_dict.get("sensitivity") or {}
    slots = sk.get("skeleton_slots", [])
    return {
        "goal_host": sk.get("host"),
        "goal_classification": (imp.get("goal_classification") or {}).get("type"),
        "new_provider_required": imp.get("new_provider_required"),
        "signing_params": [p.get("param") for p in sk.get("signing_params", [])],
        "identity_slot_count": sum(1 for s in slots if s.get("role") == "identity"),
        "rendition_slot_count": sum(1 for s in slots
                                    if s.get("role") == "rendition"),
        "reusable_classes": [c.get("class") for c in
                             gen.get("reusable_classes", [])],
        # which instances each class was seen with on this site; None when the
        # workbench output carries only the count (no "instance_ids")
        "reusable_class_instances": {
            c.get("class"): _instance_ids(c.get("instance_ids"))
            for c in gen.get("reusable_classes", [])},
        "framework_level_count": len(gen.get("framework_level", [])),
        "site_specific_count": len(gen.get("site_specific", [])),
        "sensitivity_robust": list(sens.get("robust_conclusions") or []),
    }


def _instance_ids(raw: Any) -> Any:
    """A sorted list of instance-id strings, or None when ``raw`` is not a list
    of ids (absent, a bare count, anything else)."""
    if not isinstance(raw, list):
        return None
    return sorted(str(i) for i in raw)


def _instance_decision(by_site: Dict[str, Any]) -> str:
    """For one class seen on >=2 sites: do its instances DIFFER across sites?
    'undecidable' when any of those sites names no instances (count-only
    output, or an empty id list) -- the count alone cannot tell; 'same' when
    every site shows the identical instances; else 'different'."""
    if any(not ids for ids in by_site.values()):
        return "undecidable"
    return "different" if len({tuple(ids) for ids in by_site.values()}) > 1 else "same"


def compare_sites(profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Cross-site comparison of the generalization labels. Pure function of the
    per-site profiles."""
    names = list(profiles)

    # reusable_class: reproduced on >=2 sites (confirmed) vs only 1 (site-local)
    cls_count: Counter = Counter()
    for p in profiles.values():
        for c in set(p["reusable_classes"]):
            cls_count[c] += 1
    reproduced = sorted(c for c, n in cls_count.items() if n >= 2)
    site_local = sorted(c for c, n in cls_count.items() if n == 1)
    # a reproduced class is CONFIRMED only "with DIFFERENT instances" (module
    # docstring): decide that from the instance ids, never from the count
    cls_instances: Dict[str, Dict[str, Any]] = {}
    for c in reproduced:
        by_site = {n: _instance_ids((p.get("reusable_class_instances") or {}).get(c))
                   for n, p in profiles.items() if c in p["reusable_classes"]}
        cls_instances[c] = {"decision": _instance_decision(by_site),
                            "instances_by_site": by_site}
    confirmed = [c for c in reproduced if cls_instances[c]["decision"] == "different"]
    same_instances = [c for c in reproduced if cls_instances[c]["decision"] == "same"]
    undecidable = [c for c in reproduced
                   if cls_instances[c]["decision"] == "undecidable"]

    # framework-level method property: robust on ALL sites (set intersection)
    robust_sets = [set(p["sensitivity_robust"]) for p in profiles.values()]
    robust_across = sorted(set.intersection(*robust_sets)) if robust_sets else []

    # segment-role: did identity detection reproduce everywhere? where over-split?
    seg = {n: {"identity": p["identity_slot_count"],
               "rendition": p["rendition_slot_count"]}
           for n, p in profiles.items()}
    # "reproduced on all sites" needs a site: all() of nothing is not a result
    role_reproduced_all = bool(profiles) and all(
        p["identity_slot_count"] >= 1 for p in profiles.values())
    over_split = {n: p["identity_slot_count"] for n, p in profiles.items()
                  if p["identity_slot_count"] > _OVER_SPLIT_THRESHOLD}

    # goal classification: site-specific OUTCOME (varies => the local label held)
    classification = {n: p["goal_classification"] for n, p in profiles.items()}
    # a site with NO classification (None) has no outcome to differ by; the
    # string "unknown" is an outcome, a missing one is not
    classification_varies = len({c for c in classification.values()
                                 if c is not None}) > 1

    # signing schemes by site — if all distinct, signing-opacity was tested
    # across genuinely different schemes
    signing = {n: p["signing_params"] for n, p in profiles.items()}
    # an unsigned site (no signing params) is not a signing scheme
    distinct_schemes = len({tuple(sorted(s)) for s in signing.values() if s})

    return {
        "sites": names,
        # reproduced = the class was seen on >=2 sites (count level, as before)
        "reusable_classes_reproduced": reproduced,
        "reusable_classes_site_local": site_local,
        # CONFIRMED reusable (rule reproduced, instances differ) / reproduced
        # with the same instances / instances not named by >=1 site
        "reusable_classes_confirmed": confirmed,
        "reusable_classes_same_instances": same_instances,
        "reusable_classes_undecidable": undecidable,
        "reusable_class_instances": cls_instances,
        # CONFIRMED framework-level method property
        "sensitivity_robust_across_all": robust_across,
        # segment-role rule
        "segment_role_reproduced_all_sites": role_reproduced_all,
        "segment_role_slots": seg,
        "over_split_flag": over_split,
        # CONFIRMED site-specific (outcome varies => label held, no over-reach)
        "goal_classification_by_site": classification,
        "goal_classification_varies": classification_varies,
        # signing surfaced by name across schemes
        "signing_schemes_by_site": signing,
        "distinct_signing_schemes": distinct_schemes,
        "verdict": {
            "confirmed_reusable": confirmed + (
                ["sensitivity:" + r for r in robust_across]),
            "undecidable_reusable": undecidable,
            "reproduced_same_instances": same_instances,
            "confirmed_site_specific": (
                ["goal_classification"] if classification_varies else []),
            "anomalies": ([f"segment_role over-split on {n} "
                           f"({c} identity slots — likely CDN path-sharding)"
                           for n, c in over_split.items()]),
            "signing_opacity_tested_across_schemes": distinct_schemes,
        },
        "note": ("a confirmed reusable class reproduces on >=2 sites with "
                 "different instances (decided from instance ids; a class whose "
                 "instances a site does not name is undecidable, not confirmed); "
                 "a confirmed framework-level property is "
                 "robust on ALL sites; a confirmed site_specific label has an "
                 "outcome that VARIES across sites (it correctly did not "
                 "over-generalize); an anomaly is a rule that reproduced but "
                 "with wrong granularity. Recognition-only: HLS goals that "
                 "classify as unknown are surfaced, never resolved."),
    }


def multi_site_run(site_pairs: Dict[str, Tuple[Dict, Dict]]
                   ) -> Dict[str, Any]:
    """Build a workbench per site (synth of its same-title pair), profile each,
    and compare. `site_pairs` maps a site name to its two loaded captures."""
    profiles: Dict[str, Dict[str, Any]] = {}
    for name, (a, b) in site_pairs.items():
        wb = build_workbench(synthesize(a, b), captures=(a, b)).to_dict()
        profiles[name] = site_profile(wb)
    return {"profiles": profiles, "comparison": compare_sites(profiles)}
