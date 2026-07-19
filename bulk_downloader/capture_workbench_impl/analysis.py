"""capture_workbench_impl.analysis -- verbatim functions from capture_workbench.py."""

from __future__ import annotations

from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

from ._common import (
    CLIENT_COMPUTED,
    CP_ADDITIONAL_CAPTURE,
    CP_CLASSIFIER_SUFFICIENT,
    CP_DETECTOR_CONFIG,
    CP_NO_ACTION,
    CP_SELECTOR_WORKFLOW,
    CP_UNRECOVERABLE,
    IDENTITY,
    PROVENANCE,
    RENDITION,
    ROTATING_OPAQUE,
    SIGNING,
    _BAND_ORDER,
    _FRAGILITY,
    _GENERALIZES_KIND,
    _GENERIC_BUCKETS,
    _NODE_WEIGHT,
    _OBSERVED_BASES,
    _REUSABLE_CLASS_KIND,
    _STAB,
    _UNC_W_EDGE,
    _UNC_W_FLOOR,
    _UNC_W_SKELETON,
    _UNC_W_SRC_UNKNOWN,
    _UNC_W_STRUCTURE,
)


def _slot_evidence(p: Dict[str, Any], verdict: str) -> List[str]:
    """Concrete observations behind a slot's verdict. NEVER echoes credential
    or signing values — for those the evidence is the *policy* match, not the
    secret."""
    ev: List[str] = []
    shape = p.get("type") or "opaque"
    if verdict == SIGNING:
        ev.append("key/value matches signing policy (credential, signed URL, "
                  "or redacted at capture) — value withheld")
        return ev
    va, vb = p.get("value_a"), p.get("value_b")
    if isinstance(va, str) and isinstance(vb, str) and va != vb:
        ev.append(f"value differs across captures: A={va!r} B={vb!r}")
    if shape not in ("opaque", "empty"):
        ev.append(f"value matches the {shape} shape rule")
    src = p.get("source")
    if src and src not in ("source_unknown", "redacted_credential"):
        ev.append(f"value located in {src} (observed)")
    elif src == "source_unknown":
        ev.append("value not found in page context, prior non-sensitive "
                  "headers, or retained response bodies (source-unknown)")
    return ev


def _slot_strengthen(p: Dict[str, Any], verdict: str) -> Optional[str]:
    """What additional capture would raise confidence in this slot."""
    src = p.get("source")
    if verdict in (CLIENT_COMPUTED, ROTATING_OPAQUE) and src == "source_unknown":
        return ("re-capture with BD_CAPTURE_BODIES=1 — if the value is emitted "
                "in an earlier response body it resolves to a provenance edge "
                "instead of staying unrecoverable")
    if verdict == PROVENANCE:
        return ("capture a 3rd session — confirms the provenance edge is "
                "stable, not an N=2 coincidence")
    return None


def _impact(synth: Dict[str, Any],
            skeleton: Optional[Dict[str, Any]],
            bodies_state: Optional[bool] = None) -> Dict[str, Any]:
    """Grounded change plan: which detector components a maintainer would
    likely touch to act on this draft. Calls the REAL ``deep_detect``
    classifier on the goal URL so the verdict is authoritative, not a
    reimplementation. Lazy import keeps the workbench importable independent
    of deep_detect's load path."""
    out: Dict[str, Any] = {
        "goal_classification": None,
        "new_provider_required": False,
        "likely_components": [],
        "effort_focus": None,
        "confidence_raising_captures": [],
        "summary": "",
    }
    goal = next((r for r in synth.get("requests", []) if r.get("goal")), None)
    if goal is None:
        out["summary"] = ("no goal media request identified — capture through "
                          "to the download to map detector impact")
        return out

    goal_base = (goal.get("url_template") or "").partition("?")[0]
    try:
        from .. import deep_detect
        cls = deep_detect.classify_url(goal_base)
        host = deep_detect._url_host(goal_base)
        provider_hosts = {h for _n, hosts, _m in deep_detect.PROVIDERS
                          for h in hosts}
        known_provider = any(h in host for h in provider_hosts)
    except Exception as e:  # deep_detect shape changed — degrade gracefully
        out["summary"] = f"goal classification unavailable ({e})"
        return out

    out["goal_classification"] = {"type": cls.get("type"),
                                  "reasons": cls.get("reasons", [])}
    comps: List[str] = []
    ctype = cls.get("type")

    if ctype in ("direct_file", "extensionless_file", "hls_manifest",
                 "dash_manifest"):
        out["new_provider_required"] = False
        comps.append(f"deep_detect.classify_url already buckets the goal as "
                     f"'{ctype}' (generic path) — NO new SOURCE_TYPES/PROVIDERS"
                     f"/_PROVIDER_ID_PATTERNS entry needed")
        out["effort_focus"] = (
            "workflow-reach, not URL classification: the maintainer effort is "
            "getting the session to the point that emits this URL (login + "
            "navigation + player init), expressed as selector_chains steps "
            "and/or a sites_config entry — plus the live session supplying the "
            "opaque signing params")
        comps.append("selector_chains (login/navigation step chain to reach "
                     "the player)")
        comps.append("sites_config entry for the host/workflow, if not present")
    elif known_provider:
        comps.append("_PROVIDER_ID_PATTERNS[<provider>] — host is a known "
                     "provider; the id-extraction pattern(s) may need to cover "
                     "the observed id shape")
        out["effort_focus"] = ("id-extraction: extend the provider's pattern "
                               "list to capture the goal's identifier")
    else:
        out["new_provider_required"] = True
        comps.append("3-place provider plumbing: SOURCE_TYPES += '<prov>_embed'"
                     "; PROVIDERS += (name, (hosts...), (markers...)); "
                     "_PROVIDER_ID_PATTERNS['<prov>'] += extraction patterns")
        out["effort_focus"] = ("new provider integration — the goal host is "
                               "neither a known provider nor a bare media file")

    if skeleton and skeleton.get("signing_params"):
        comps.append("credential acquisition is a workflow concern (live "
                     "session), not a classify rule — signing params stay "
                     "opaque")

    caps: List[str] = []
    if skeleton and skeleton.get("skeleton_slots"):
        # v3.66.69: only IDENTITY slots are promoted by a second title. A
        # rendition slot does NOT co-vary with title, so recommending "capture
        # a 2nd title to promote it" was the falsified claim. Name only the
        # identity slots here.
        id_names = [s["name"] for s in skeleton["skeleton_slots"]
                    if s.get("role", "identity") == "identity"]
        rend_names = [s["name"] for s in skeleton["skeleton_slots"]
                      if s.get("role", "identity") == "rendition"]
        if id_names:
            caps.append(
                "capture a 2nd DIFFERENT title — promotes the identity "
                f"slot(s) {', '.join(id_names)} from shape-inferred to an "
                "observed varying slot")
        if rend_names:
            caps.append(
                f"NOTE: rendition slot(s) {', '.join(rend_names)} are NOT "
                "promoted by a second title — they name a quality menu, "
                "constant per title; vary the download quality to enumerate "
                "them")
    # Only recommend a body re-capture when bodies were NOT already retained.
    # If they were on and values stayed source-unknown, re-running won't help —
    # recommending it would contradict the uncertainty layer (which ranks it 0%).
    if synth.get("unresolved") and bodies_state is not True:
        caps.append("re-capture with BD_CAPTURE_BODIES=1 — may resolve "
                    "source-unknown values to provenance edges")
    caps.append("capture a 3rd session — confirms N=2 invariants/edges are "
                "not coincidental")
    out["confidence_raising_captures"] = caps

    out["likely_components"] = comps
    np = "no new provider needed" if not out["new_provider_required"] \
        else "NEW PROVIDER likely required"
    out["summary"] = (
        f"goal classifies as '{ctype}' -> {np}. "
        f"Effort focus: {out['effort_focus']}")
    return out


def _change_plan(draft: "DetectorDraft",
                 bodies_state: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Aggregate the draft's findings into a prioritized list of maintainer
    recommendations. Pure derivation — reads only already-computed fields.
    Lower ``priority`` sorts first (1 = do this next)."""
    imp = draft.impact or {}
    sk = draft.skeleton or {}
    gc = imp.get("goal_classification") or {}
    ctype = gc.get("type")
    generic_bucket = ctype in _GENERIC_BUCKETS
    new_provider = bool(imp.get("new_provider_required"))
    known_provider_pattern = any(
        "_PROVIDER_ID_PATTERNS[<provider>]" in c
        for c in imp.get("likely_components", []))

    n_signing = sum(1 for s in draft.slots if s.verdict == SIGNING)
    n_unrec = len(draft.unrecoverable)
    n_skel = len(sk.get("skeleton_slots", []))
    n_edges = len(draft.provenance_edges)

    recs: List[Dict[str, Any]] = []

    # The primary action: a detector-config change (new provider / extend a
    # provider's patterns) OR a workflow investigation. At most one of these is
    # the headline; both can appear when relevant.
    if new_provider:
        recs.append({
            "category": CP_DETECTOR_CONFIG, "priority": 1,
            "action": ("Add provider plumbing for the goal host (3 places: "
                       "SOURCE_TYPES, PROVIDERS, _PROVIDER_ID_PATTERNS)."),
            "why": (f"goal host is neither a known provider nor a bare media "
                    f"file; classify_url returned '{ctype}'"),
            "refs": ["deep_detect.SOURCE_TYPES", "deep_detect.PROVIDERS",
                     "deep_detect._PROVIDER_ID_PATTERNS"]})
    elif known_provider_pattern:
        recs.append({
            "category": CP_DETECTOR_CONFIG, "priority": 1,
            "action": ("Extend the provider's id-extraction pattern list to "
                       "cover the observed identifier shape."),
            "why": "goal host is a known provider but the id may not be matched",
            "refs": ["deep_detect._PROVIDER_ID_PATTERNS"]})

    if generic_bucket or n_signing > 0:
        recs.append({
            "category": CP_SELECTOR_WORKFLOW,
            "priority": 1 if (generic_bucket and not new_provider) else 2,
            "action": ("Investigate the login/navigation path that reaches the "
                       "goal; express it as selector_chains steps and/or a "
                       "sites_config entry. The live authenticated session "
                       f"supplies the {n_signing} opaque signing input(s)."),
            "why": (imp.get("effort_focus")
                    or "credential acquisition + reach is a workflow concern"),
            "refs": ["bulk_downloader.selector_chains", "sites_config"]})

    # Confidence: what additional capture would strengthen the conclusions.
    if imp.get("confidence_raising_captures"):
        bits = []
        if n_skel:
            bits.append("a 2nd different title (confirms the content-id slot)")
        # only suggest a body re-run when bodies weren't already retained
        if n_unrec and bodies_state is not True:
            bits.append("a body-capture re-run (BD_CAPTURE_BODIES=1)")
        bits.append("a 3rd session (confirms N=2 findings)")
        recs.append({
            "category": CP_ADDITIONAL_CAPTURE, "priority": 2,
            "action": "Capture more sessions to raise confidence: " +
                      "; ".join(bits) + ".",
            "why": ("N=2 is a structural confidence floor; the items above "
                    "promote inferred conclusions to observed ones"),
            "refs": list(imp.get("confidence_raising_captures", []))})

    # The work-saving negative: classifier already handles the URL.
    if generic_bucket and not new_provider:
        recs.append({
            "category": CP_CLASSIFIER_SUFFICIENT, "priority": 3,
            "action": ("No new URL-classification rule needed — the existing "
                       f"classifier already recognizes the goal as '{ctype}'."),
            "why": "; ".join(gc.get("reasons", [])) or "generic classifier path",
            "refs": ["deep_detect.classify_url"]})

    # The scope boundary: what cannot be recovered by observation.
    if n_unrec:
        recs.append({
            "category": CP_UNRECOVERABLE, "priority": 4,
            "action": (f"Do not attempt to pattern {n_unrec} parameter(s) — "
                       "they are client-computed / telemetry / session-local "
                       "and must be produced by the live session."),
            "why": ("no observable source in the capture (page, prior headers, "
                    "or retained bodies)"),
            "refs": []})

    # Terminal: nothing actionable surfaced.
    actionable = {CP_DETECTOR_CONFIG, CP_SELECTOR_WORKFLOW,
                  CP_ADDITIONAL_CAPTURE}
    if not any(r["category"] in actionable for r in recs):
        recs.append({
            "category": CP_NO_ACTION, "priority": 5,
            "action": ("No detector change indicated — the flow is recognized "
                       "and carries no rotating/credential slots needing work."),
            "why": "no provider gap, no workflow signing, no open slots",
            "refs": []})

    recs.sort(key=lambda r: r["priority"])
    return recs


def _bodies_retained(captures) -> Optional[bool]:
    """True if any capture retained a real (non-marker) response body, False if
    none did, None if captures weren't provided. Lets the ranker tell
    'bodies already on, resolved nothing' from 'bodies were off, try them'."""
    if not captures:
        return None
    any_body = False
    for cap in captures:
        if not isinstance(cap, dict):
            continue
        for e in cap.get("network_log") or []:
            b = e.get("response_body")
            if isinstance(b, str) and b and not b.startswith("<scrubbed>"):
                any_body = True
                break
        if any_body:
            break
    return any_body


def _uncertainty_plan(draft: "DetectorDraft",
                      bodies_state: Optional[bool]) -> Dict[str, Any]:
    """Rank candidate evidence collections by estimated uncertainty reduction
    across the whole draft. Returns total uncertainty weight, the body state,
    and a ranked list (highest-leverage first)."""
    sk = draft.skeleton or {}

    # 1) Enumerate the draft's *uncertain* conclusions (things not yet observed
    #    / confident that more evidence could firm up). Each is (id, kind,
    #    weight). Settled facts (signing is opaque; an observed varying slot)
    #    are NOT uncertainty — they carry no residual to reduce.
    uncertain: List[tuple] = [("overall_confidence_floor", "overall",
                               _UNC_W_FLOOR)]
    # v3.66.69: an identity slot's uncertainty IS resolved by a second title;
    # a rendition slot's is not (it is resolved by varying download quality,
    # and is lower-stakes for the detector target). Bucket them as separate
    # kinds so a second-title capture does not get credited with resolving
    # rendition uncertainty it cannot resolve.
    for s in sk.get("skeleton_slots", []):
        if s.get("role", "identity") == "rendition":
            uncertain.append((f"skeleton_slot:{s['name']}", "rendition_slot",
                              _UNC_W_SRC_UNKNOWN))
        else:
            uncertain.append((f"skeleton_slot:{s['name']}", "skeleton",
                              _UNC_W_SKELETON))
    if sk.get("skeleton_slots"):
        uncertain.append(("skeleton_structure_title_invariant", "skeleton",
                          _UNC_W_STRUCTURE))
    for e in draft.provenance_edges:
        uncertain.append((f"provenance_edge:{e.get('param')}", "edge",
                          _UNC_W_EDGE))
    # Source-unknown slots are reducible by retained bodies ONLY if bodies
    # weren't already on. If they WERE on and these stayed unresolved, they are
    # effectively confirmed client-computed — near-zero residual uncertainty.
    bodies_can_help = bodies_state is not True
    if bodies_can_help:
        for u in draft.unrecoverable:
            uncertain.append((f"source_unknown:{u.get('param')}",
                              "source_unknown", _UNC_W_SRC_UNKNOWN))

    total = sum(w for _i, _k, w in uncertain) or 1

    def _resolved(kinds: set) -> List[tuple]:
        return [(i, k, w) for (i, k, w) in uncertain if k in kinds]

    _IMPACT_DEP = {
        "overall": "overall draft confidence (currently low at N=2)",
        "skeleton": "the content-id extraction pattern in the goal skeleton",
        "rendition_slot": ("which rendition member the recorded goal names "
                           "(a quality choice, not the title key)"),
        "edge": "stability of the discovered provenance edge(s)",
        "source_unknown": ("whether the source-unknown values are recoverable "
                           "or genuinely client-computed"),
    }

    def _candidate(name: str, resolved: List[tuple], note: str,
                   moves: str) -> Dict[str, Any]:
        wsum = sum(w for _i, _k, w in resolved)
        kinds = {k for _i, k, _w in resolved}
        return {
            "evidence": name,
            "estimated_uncertainty_reduction_pct": round(100 * wsum / total),
            "weighted_resolved": wsum, "of_total": total,
            "promotes_to_observed": [i for i, _k, _w in resolved],
            "slots_that_move_category": moves,
            "impact_dependencies": sorted(_IMPACT_DEP[k] for k in kinds),
            "note": note,
        }

    ranked: List[Dict[str, Any]] = []
    # A 2nd DIFFERENT title makes the content-id path segment vary -> the
    # shape-inferred skeleton slot becomes an observed varying slot, and the
    # surrounding template is confirmed title-invariant.
    ranked.append(_candidate(
        "second_different_title", _resolved({"skeleton"}),
        ("the content id is invariant across same-title captures, so it is "
         "only shape-inferred now; a different title makes it vary and become "
         "an observed slot, and confirms the URL template is title-invariant"),
        "skeleton content-id: inferred -> observed (becomes a varying slot)"))
    # A 3rd session lifts the N=2 floor and confirms any provenance edges.
    ranked.append(_candidate(
        "third_session", _resolved({"overall", "edge"}),
        ("N=2 cannot tell a true invariant from a coincidence; a 3rd session "
         "lifts the overall confidence floor and confirms edges are stable"),
        "none directly; raises confidence on existing classifications"))
    # Retained bodies — honestly conditioned on whether they were already on.
    if bodies_state is True:
        rb_note = ("response bodies are ALREADY retained in these captures and "
                   "resolved 0 source-unknown values — a re-run will not help; "
                   "those values appear genuinely client-computed")
        rb_moves = "none expected (bodies already retained, resolved nothing)"
        rb_resolved: List[tuple] = []
    elif bodies_state is False:
        rb_note = ("bodies were not retained; a BD_CAPTURE_BODIES=1 re-capture "
                   "may resolve source-unknown values to provenance edges")
        rb_moves = "source-unknown -> provenance (for any value emitted in a body)"
        rb_resolved = _resolved({"source_unknown"})
    else:
        rb_note = ("body-retention state unknown (pass captures for a sharper "
                   "estimate); if bodies were off, this could resolve "
                   "source-unknown values")
        rb_moves = "source-unknown -> provenance (conditional)"
        rb_resolved = _resolved({"source_unknown"})
    ranked.append(_candidate("retained_bodies", rb_resolved, rb_note, rb_moves))

    ranked.sort(key=lambda c: -c["weighted_resolved"])
    return {
        "total_uncertainty_weight": total,
        "bodies_retained": bodies_state,
        "ranked": ranked,
        "note": ("estimated_uncertainty_reduction_pct is a weighted count of "
                 "the uncertain conclusions each evidence type would resolve, "
                 "over the draft's total — a transparent heuristic, not a "
                 "probability"),
    }


def _uncertainty_flow(draft: "DetectorDraft") -> Dict[str, Any]:
    """Build the dependency graph: typed nodes + 'depends_on' edges, then the
    transitive downstream closure per node and a ranking of which inferred
    assumptions carry the most downstream weight."""
    sk = draft.skeleton or {}
    imp = draft.impact or {}
    has_goal = bool((imp.get("goal_classification") or {}).get("type"))
    n_signing = sum(1 for s in draft.slots if s.verdict == SIGNING)

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[tuple] = []  # (child, parent): child depends_on parent

    def node(nid: str, ntype: str, label: str, basis: str):
        nodes[nid] = {"id": nid, "type": ntype, "label": label, "basis": basis}

    def dep(child: str, parent: str):
        if child in nodes and parent in nodes:
            edges.append((child, parent))

    # ── root assumptions (the uncertain foundations) ──
    if has_goal:
        node("assume:goal_selection", "assume",
             "the goal request is correctly identified (highest-seq media "
             "heuristic)", "inferred")
    node("assume:n2_floor", "assume",
         "N=2 invariants are real, not coincidental", "inferred")
    skel_slots = sk.get("skeleton_slots", [])
    if skel_slots:
        node("assume:title_invariant", "assume",
             "the goal URL template is stable across titles", "inferred")
    for s in skel_slots:
        # v3.66.69: role-aware. A rendition slot is NOT a per-title identity
        # claim; labelling it as one (the pre-.69 behaviour) is the falsified
        # belief. Identity slots carry the title-identity assumption; rendition
        # slots carry only the (weaker) claim that the recorded member is the
        # one being served.
        if s.get("role", IDENTITY) == RENDITION:
            node(f"assume:skeleton:{s['name']}", "assume",
                 f"the {s['name']} path segment ({s['sample']!r}) is a "
                 f"rendition descriptor; the recorded member is the one served "
                 f"(does NOT identify the title)", "shape_heuristic")
        else:
            node(f"assume:skeleton:{s['name']}", "assume",
                 f"the {s['name']} path segment ({s['sample']!r}) is an "
                 f"addressable per-title IDENTITY key", "inferred")
    for u in draft.unrecoverable:
        node(f"assume:src_unknown:{u['param']}", "assume",
             f"{u['param']} is client-computed (no observable source)",
             "inferred")
    for e in draft.provenance_edges:
        node(f"edge:{e['param']}", "edge",
             f"{e['param']} is carried from {e.get('from_source')}",
             "observed")

    # ── findings ──
    if n_signing:
        node("finding:signing", "finding",
             f"{n_signing} signing slot(s) present (opaque, live-supplied)",
             "observed")

    # ── skeleton patterns / structure ──
    for s in skel_slots:
        node(f"pattern:skeleton:{s['name']}", "pattern",
             f"extraction pattern for {s['name']}: {s['regex']}", "inferred")
        dep(f"pattern:skeleton:{s['name']}", f"assume:skeleton:{s['name']}")
    if skel_slots:
        node("skeleton:structure", "skeleton",
             f"URL template: {sk.get('path_template')}", "inferred")
        dep("skeleton:structure", "assume:title_invariant")

    # ── impact conclusions ──
    if has_goal:
        node("impact:goal_classification", "impact",
             f"goal classifies as "
             f"{(imp.get('goal_classification') or {}).get('type')}",
             "observed")
        dep("impact:goal_classification", "assume:goal_selection")
        node("impact:new_provider_required", "impact",
             f"new provider required: {imp.get('new_provider_required')}",
             "inferred")
        dep("impact:new_provider_required", "impact:goal_classification")
        if imp.get("effort_focus"):
            node("impact:effort_focus", "impact",
                 "effort focus: " + (imp.get("effort_focus") or "")[:60],
                 "inferred")
            dep("impact:effort_focus", "impact:new_provider_required")
            if n_signing:
                dep("impact:effort_focus", "finding:signing")

    # ── change-plan items ──
    for r in draft.change_plan:
        cat = r["category"]
        nid = f"plan:{cat}"
        node(nid, "plan", r["action"][:70], "derived")
        if cat == CP_CLASSIFIER_SUFFICIENT:
            dep(nid, "impact:new_provider_required")
        elif cat == CP_SELECTOR_WORKFLOW:
            dep(nid, "impact:effort_focus")
        elif cat == CP_DETECTOR_CONFIG:
            dep(nid, "impact:new_provider_required")
        elif cat == CP_UNRECOVERABLE:
            for u in draft.unrecoverable:
                dep(nid, f"assume:src_unknown:{u['param']}")
        elif cat == CP_ADDITIONAL_CAPTURE:
            # this recommendation exists to resolve the inferred foundations
            for pid in ("assume:n2_floor", "assume:title_invariant"):
                dep(nid, pid)
            for s in skel_slots:
                dep(nid, f"assume:skeleton:{s['name']}")

    # ── transitive downstream closure (parent -> dependents) ──
    kids: Dict[str, List[str]] = {nid: [] for nid in nodes}
    for child, parent in edges:
        kids[parent].append(child)

    def downstream(nid: str) -> List[str]:
        seen, stack = set(), list(kids.get(nid, []))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(kids.get(n, []))
        return sorted(seen)

    for nid, nd in nodes.items():
        ds = downstream(nid)
        nd["depends_on"] = sorted(p for c, p in edges if c == nid)
        nd["dependents"] = sorted(kids.get(nid, []))
        nd["downstream"] = ds
        nd["downstream_weight"] = sum(
            _NODE_WEIGHT.get(nodes[d]["type"], 1) for d in ds)

    # ── which inferred assumption carries the most uncertainty downstream ──
    carriers = sorted(
        ({"node": nid, "label": nd["label"],
          "downstream_weight": nd["downstream_weight"],
          "carries": nd["downstream"]}
         for nid, nd in nodes.items()
         if nd["basis"] == "inferred" and nd["downstream"]),
        key=lambda c: -c["downstream_weight"])

    # capture-resolvable assumptions (the three evidence candidates touch
    # these); a high-carry assumption NOT in this set is one a maintainer must
    # verify by hand — the evidence ranking won't help it.
    capture_resolvable = {"assume:n2_floor", "assume:title_invariant"} | {
        f"assume:skeleton:{s['name']}" for s in skel_slots} | {
        f"assume:src_unknown:{u['param']}" for u in draft.unrecoverable}
    for c in carriers:
        c["capture_resolvable"] = c["node"] in capture_resolvable

    # ── what-if: which nodes change if a provenance edge appeared ──
    what_if: List[Dict[str, Any]] = []
    if draft.unrecoverable and not draft.provenance_edges:
        affected = ["plan:" + CP_UNRECOVERABLE]
        if any(r["category"] == CP_ADDITIONAL_CAPTURE
               for r in draft.change_plan):
            affected.append("plan:" + CP_ADDITIONAL_CAPTURE)
        if "impact:effort_focus" in nodes:
            affected.append("impact:effort_focus")
        what_if.append({
            "hypothesis": "a provenance edge appears (e.g. a source-unknown "
                          "value is found in an earlier body)",
            "would_change": [a for a in affected if a in nodes],
            "effect": ("source-unknown slots reclassify to provenance, "
                       "shrinking the unrecoverable set; the value can be "
                       "carried from its source rather than requiring the live "
                       "session to compute it"),
        })

    return {
        "nodes": list(nodes.values()),
        "edges": [{"depends": c, "on": p} for c, p in edges],
        "highest_carry": carriers[:5],
        "what_if": what_if,
        "note": ("edges read 'depends on'; downstream_weight sums dependent "
                 "node weights (plan=3, impact=2, pattern/finding=1). A "
                 "high-carry assumption that is not capture_resolvable must be "
                 "verified by hand — more captures won't address it."),
    }


def _assumption_kind(node_id: str) -> str:
    if node_id == "assume:goal_selection":
        return "goal_selection"
    if node_id == "assume:n2_floor":
        return "n2_floor"
    if node_id == "assume:title_invariant":
        return "title_invariant"
    # v3.66.76: the skeleton slot's fragility is ROLE-dependent. A second title
    # validated the identity slot (content_id co-varies) but FALSIFIED the
    # rendition slot's inherited 'different_title -> validates' (filename is
    # resolution-keyed, not title-keyed — corpus VC-0005/VC-0006). Route by role
    # so each gets the rule its evidence supports.
    if node_id.startswith("assume:skeleton:content_id"):
        return "skeleton_identity"
    if node_id.startswith("assume:skeleton:rendition"):
        return "skeleton_rendition"
    if node_id.startswith("assume:skeleton:"):
        # any other addressable opaque segment defaults to identity, matching
        # _segment_role's default for non-rendition addressable segments
        return "skeleton_identity"
    if node_id.startswith("assume:src_unknown:"):
        return "src_unknown"
    return "skeleton_identity"


def _assumption_stability(draft: "DetectorDraft",
                          flow: Dict[str, Any],
                          n_captures: int) -> Dict[str, Any]:
    """Per-assumption trust profile, and a verify-first risk ranking that
    crosses fragility with downstream weight."""
    weight_of = {n["id"]: n.get("downstream_weight", 0)
                 for n in flow.get("nodes", [])}
    profiles: List[Dict[str, Any]] = []
    for n in flow.get("nodes", []):
        if n["type"] != "assume":
            continue
        kind = _assumption_kind(n["id"])
        spec = _FRAGILITY[kind]
        stab = _STAB[spec["basis"]]
        profiles.append({
            "node": n["id"],
            "label": n["label"],
            "basis": spec["basis"],
            "stability_band": stab["band"],
            "survival": {"captures_examined": n_captures,
                         "held_in_all": True,
                         "strength": spec["survival_strength"]},
            "would_invalidate": spec["perturbation"],
            "scope": spec["scope"],
            "downstream_weight": weight_of.get(n["id"], 0),
            "_mult": stab["mult"],
        })

    # verify-first risk = downstream weight × fragility multiplier. Surfaces the
    # assumptions that are both load-bearing AND fragile. Homogeneous families
    # (e.g. dozens of source-unknown telemetry params with an identical profile)
    # are collapsed into one grouped row so they don't bury the real risks —
    # a maintainer triages them as a block, not individually.
    raw = sorted(
        ({"node": p["node"], "label": p["label"], "basis": p["basis"],
          "stability_band": p["stability_band"],
          "downstream_weight": p["downstream_weight"],
          "risk_score": round(p["downstream_weight"] * p["_mult"], 1)}
         for p in profiles),
        key=lambda r: -r["risk_score"])
    for p in profiles:
        p.pop("_mult", None)

    # group by identical profile; collapse only families larger than 3.
    from collections import OrderedDict
    buckets: "OrderedDict[tuple, List[Dict[str, Any]]]" = OrderedDict()
    for r in raw:
        fam = r["node"].split(":")[1] if r["node"].startswith("assume:") \
            else r["node"]
        key = (fam, r["basis"], r["stability_band"], r["risk_score"])
        buckets.setdefault(key, []).append(r)
    verify_first: List[Dict[str, Any]] = []
    for (fam, basis, band, score), members in buckets.items():
        if len(members) > 3:
            verify_first.append({
                "group": fam, "count": len(members), "basis": basis,
                "stability_band": band, "risk_score": score,
                "downstream_weight": members[0]["downstream_weight"],
                "members": [m["node"] for m in members],
                "note": (f"{len(members)} params with an identical profile — "
                         f"triage as a block")})
        else:
            verify_first.extend(members)
    verify_first.sort(key=lambda r: -r["risk_score"])

    return {
        "assumptions": profiles,
        "verify_first": verify_first,
        "note": ("stability is independent of downstream weight: a low-band "
                 "(heuristic/untested) assumption deserves less trust "
                 "regardless of how much rests on it. verify_first crosses the "
                 "two (weight x fragility) — the top item is both load-bearing "
                 "and fragile. 'would_invalidate' shows what effect a different "
                 "title / session / player config / workflow would have."),
    }


def _blast_radius(draft: "DetectorDraft") -> Dict[str, Any]:
    flow = draft.uncertainty_flow or {}
    nodes = {n["id"]: n for n in flow.get("nodes", [])}
    total_nodes = max(len(nodes), 1)
    radii: List[Dict[str, Any]] = []
    for nid, nd in nodes.items():
        if nd["type"] != "assume":
            continue
        ds = nd.get("downstream", [])
        invalid_findings = [d for d in ds
                            if nodes[d]["type"] in ("finding", "pattern",
                                                    "skeleton")]
        collapsed_impact = [d for d in ds if nodes[d]["type"] == "impact"]
        changed_plan = [d for d in ds if nodes[d]["type"] == "plan"]
        radii.append({
            "assumption": nid,
            "label": nd["label"],
            "if_it_fails": {
                "invalid_findings": invalid_findings,
                "collapsed_impact": collapsed_impact,
                "changed_recommendations": changed_plan,
            },
            "nodes_to_reconsider": len(ds),
            "fraction_of_draft_pct": round(100 * len(ds) / total_nodes),
            "blast_weight": nd.get("downstream_weight", 0),
        })
    radii.sort(key=lambda r: -r["blast_weight"])
    return {
        "by_assumption": radii,
        "note": ("blast radius is what becomes invalid IF the assumption is "
                 "wrong (its flow-graph downstream closure), categorized by "
                 "node type. fraction_of_draft_pct is the share of all graph "
                 "nodes that would need reconsidering. Cross with stability: a "
                 "large blast radius on a low-stability assumption is the "
                 "real exposure."),
    }


def _generalization(draft: "DetectorDraft") -> Dict[str, Any]:
    framework: List[Dict[str, str]] = []   # transfers to other detectors as-is
    site_specific: List[Dict[str, str]] = []  # holds only for this capture/host
    reusable_classes: List[Dict[str, str]] = []  # the rule-class transfers, instances local

    stab = draft.assumption_stability or {}
    for a in stab.get("assumptions", []):
        basis = a.get("basis", "")
        entry = {"node": a["node"], "label": a["label"], "basis": basis,
                 "why": a.get("scope", "")}
        if basis in _GENERALIZES_KIND:
            framework.append(entry)
        elif basis in _REUSABLE_CLASS_KIND:
            # don't list every telemetry param as framework — record the CLASS
            # once and treat the instances as local.
            site_specific.append(entry)
        else:  # method-only or local -> the conclusion is site-specific
            site_specific.append(entry)

    # The reusable *class* for the source-unknown telemetry block (recorded once,
    # not per-param), so it shows as transferable knowledge without inflating
    # the framework list with dozens of local instances.
    n_src = sum(1 for a in stab.get("assumptions", [])
                if a.get("basis") == "negative_observation")
    if n_src:
        reusable_classes.append({
            "class": "client_computed_telemetry_is_unrecoverable",
            "instances": n_src,
            "why": ("the RULE generalizes (telemetry computed client-side can't "
                    "be recovered by observation); the specific params are "
                    "local to this player/site")})

    # Findings / patterns.
    n_signing = sum(1 for s in draft.slots if s.verdict == SIGNING)
    if n_signing:
        reusable_classes.append({
            "class": "signing_marker_policy",
            "instances": n_signing,
            "why": ("the signing-marker recognition is structural and reusable "
                    "across detectors; the specific tokens are local")})
    sk = draft.skeleton or {}
    for s in sk.get("skeleton_slots", []):
        framework.append({
            "node": f"pattern:skeleton:{s['name']}",
            "label": f"{s['name']} extraction pattern SHAPE ({s['regex']})",
            "basis": "shape", "why": "the shape transfers; the value does not"})
        site_specific.append({
            "node": f"value:skeleton:{s['name']}",
            "label": f"{s['name']} value {s['sample']!r}",
            "basis": "value", "why": "the specific identifier is this title only"})

    imp = draft.impact or {}
    gc = imp.get("goal_classification") or {}
    if gc.get("type"):
        reusable_classes.append({
            "class": f"classify_url:{gc['type']}",
            "instances": 1,
            "why": "the classifier rule is framework-level"})
        site_specific.append({
            "node": "impact:goal_host",
            "label": f"this host needs no new provider ({gc['type']})",
            "basis": "conclusion",
            "why": "a conclusion about this specific host/workflow"})

    return {
        "framework_level": framework,
        "reusable_classes": reusable_classes,
        "site_specific": site_specific,
        "summary": (
            f"{len(framework)} directly-reusable item(s), "
            f"{len(reusable_classes)} reusable rule-class(es) whose instances "
            f"are local, {len(site_specific)} site-specific conclusion(s). "
            f"Most conclusions here rest on general methods but are themselves "
            f"local — only the patterns and rule-classes transfer as-is."),
    }


def _contradictions(draft: "DetectorDraft",
                    bodies_state: Optional[bool]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    imp = draft.impact or {}
    unc = draft.uncertainty or {}

    # C1 — a capture recommendation that was already tried: bodies recommended
    # while bodies were already retained.
    recs_text = " ".join(imp.get("confidence_raising_captures", []))
    plan_caps = " ".join(
        r.get("action", "") for r in draft.change_plan
        if r.get("category") == CP_ADDITIONAL_CAPTURE)
    if bodies_state is True and ("BD_CAPTURE_BODIES" in recs_text
                                 or "BD_CAPTURE_BODIES" in plan_caps):
        out.append({
            "check": "stale_capture_recommendation", "severity": "medium",
            "detail": ("a body re-capture is recommended, but bodies were "
                       "already retained in these captures and resolved "
                       "nothing"),
            "nodes": ["impact.confidence_raising_captures",
                      "plan:" + CP_ADDITIONAL_CAPTURE]})

    # C2 — ranking vs attempted: the bodies candidate ranks >0% while bodies
    # were already retained.
    for c in unc.get("ranked", []):
        if c.get("evidence") == "retained_bodies" and bodies_state is True \
                and c.get("estimated_uncertainty_reduction_pct", 0) > 0:
            out.append({
                "check": "ranking_recommends_attempted_evidence",
                "severity": "high",
                "detail": ("the uncertainty ranking gives the bodies candidate "
                           ">0% even though bodies were already retained"),
                "nodes": ["uncertainty.retained_bodies"]})

    # C3 — a param classified BOTH as an addressable skeleton id AND as
    # unrecoverable/client-computed (incompatible: a recoverable target cannot
    # also be unrecoverable).
    sk = draft.skeleton or {}
    skel_names = {s["name"] for s in sk.get("skeleton_slots", [])}
    unrec_params = {u["param"] for u in draft.unrecoverable}
    for p in sorted(skel_names & unrec_params):
        out.append({
            "check": "param_both_addressable_and_unrecoverable",
            "severity": "high",
            "detail": (f"{p!r} is templated as an addressable skeleton id and "
                       f"also listed as unrecoverable/client-computed"),
            "nodes": [f"skeleton:{p}", f"unrecoverable:{p}"]})

    # C4 — plan says "no new rule needed" while impact says a new provider IS
    # required (direct contradiction).
    cats = {r["category"] for r in draft.change_plan}
    if CP_CLASSIFIER_SUFFICIENT in cats and imp.get("new_provider_required"):
        out.append({
            "check": "classifier_sufficient_vs_new_provider_required",
            "severity": "high",
            "detail": ("the plan says the existing classifier is sufficient, "
                       "but impact says a new provider is required"),
            "nodes": ["plan:" + CP_CLASSIFIER_SUFFICIENT,
                      "impact:new_provider_required"]})

    # C5 — goal classified as a provider embed/stream while new_provider is
    # False AND no known provider host matched (tension worth surfacing).
    ctype = (imp.get("goal_classification") or {}).get("type") or ""
    if (ctype.endswith("_embed") or ctype.endswith("_stream")) \
            and imp.get("new_provider_required") is False:
        out.append({
            "check": "provider_type_but_no_provider_change", "severity": "low",
            "detail": (f"goal classified as '{ctype}' (a provider type) but no "
                       f"provider change is flagged — verify the host is a "
                       f"known provider"),
            "nodes": ["impact:goal_classification",
                      "impact:new_provider_required"]})

    return out


def _upstream_assumptions(flow: Dict[str, Any], start: str) -> List[str]:
    """Walk the flow's depends-on edges UP from `start` and return every
    assume:* node it transitively rests on. Edges read child depends_on parent,
    so we follow child->parent to reach the root assumptions."""
    parents: Dict[str, List[str]] = {}
    for e in flow.get("edges", []):
        parents.setdefault(e["depends"], []).append(e["on"])
    seen: set = set()
    out: List[str] = []
    stack = list(parents.get(start, []))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if node.startswith("assume:"):
            out.append(node)
        stack.extend(parents.get(node, []))
    return out


def _assumptions_for_recommendation(rec: Dict[str, Any],
                                    flow: Dict[str, Any]) -> List[str]:
    cat = rec.get("category", "")
    ids = {n["id"] for n in flow.get("nodes", []) if n["type"] == "assume"}
    skel = sorted(i for i in ids if i.startswith("assume:skeleton:"))
    srcun = sorted(i for i in ids if i.startswith("assume:src_unknown:"))
    has = lambda nid: nid in ids  # noqa: E731
    if cat == CP_ADDITIONAL_CAPTURE:
        deps = list(skel)
        if has("assume:title_invariant"):
            deps.append("assume:title_invariant")
        if has("assume:n2_floor"):
            deps.append("assume:n2_floor")
        return deps
    if cat in (CP_SELECTOR_WORKFLOW, CP_CLASSIFIER_SUFFICIENT):
        return ["assume:goal_selection"] if has("assume:goal_selection") else []
    if cat == CP_DETECTOR_CONFIG:
        # provider/detector config rests on the goal pick + the id pattern shape
        deps = ["assume:goal_selection"] if has("assume:goal_selection") else []
        return deps + list(skel)
    if cat == CP_UNRECOVERABLE:
        return list(srcun)
    return []


def _decision_confidence(draft: "DetectorDraft",
                         flow: Dict[str, Any],
                         stability: Dict[str, Any]) -> Dict[str, Any]:
    """Derive a weakest-link confidence band + audit trace for each decision the
    draft asserts. Pure function of the dependency graph (flow) and the
    per-assumption trust profiles (stability) already computed upstream.
    """
    # Index the stability profiles by node id for O(1) lookup. The stability
    # layer returns the per-assumption profiles under the "assumptions" key.
    prof_by_node = {p["node"]: p for p in stability.get("assumptions", [])}

    def _trace(support_nodes: List[str]) -> Dict[str, Any]:
        """Build the supporting-assumption trace and the weakest-link band."""
        supports = []
        for nid in support_nodes:
            p = prof_by_node.get(nid)
            if not p:
                continue
            supports.append({
                "assumption": nid,
                "label": p.get("label"),
                "stability_band": p.get("stability_band"),
                "basis": p.get("basis"),
                # inferred vs observed: a flow assume:* node is always inferred
                # unless the stability basis is an observation. (All assume:*
                # roots are inferred by construction; observed facts are edges/
                # findings, which are not assumptions and so never cap here.)
                "kind": ("observed" if p.get("basis") in _OBSERVED_BASES
                         else "inferred"),
                "would_invalidate": p.get("would_invalidate"),
                "scope": p.get("scope"),
            })
        if not supports:
            # No load-bearing assumption -> the decision is not contingent on a
            # fragile inference. Report high but say so explicitly.
            return {"confidence": "high", "capped_by": None,
                    "rationale": ("rests on no inferred assumption in the "
                                  "dependency graph"),
                    "supporting_assumptions": []}
        # weakest link = the support with the lowest stability band ordinal.
        weakest = min(supports,
                      key=lambda s: _BAND_ORDER.get(s["stability_band"], 0))
        n_inferred = sum(1 for s in supports if s["kind"] == "inferred")
        return {
            "confidence": weakest["stability_band"],
            "capped_by": weakest["assumption"],
            "rationale": (
                f"capped at '{weakest['stability_band']}' by "
                f"{weakest['assumption']} (basis {weakest['basis']}, "
                f"{weakest['kind']}); {n_inferred} of {len(supports)} "
                f"supporting assumption(s) are inferred"),
            "supporting_assumptions": supports,
        }

    decisions: List[Dict[str, Any]] = []
    imp = draft.impact or {}

    # 1) the impact conclusions that are flow nodes — walk the graph for support
    if (imp.get("goal_classification") or {}).get("type"):
        decisions.append({
            "decision": "goal_classification",
            "statement": (f"goal classifies as "
                          f"{imp['goal_classification'].get('type')}"),
            **_trace(_upstream_assumptions(flow, "impact:goal_classification")),
        })
        decisions.append({
            "decision": "new_provider_required",
            "statement": (f"new provider required: "
                          f"{imp.get('new_provider_required')}"),
            **_trace(_upstream_assumptions(flow,
                                           "impact:new_provider_required")),
        })

    # 2) each change-plan recommendation — support via the explicit category map
    for rec in draft.change_plan:
        decisions.append({
            "decision": "recommendation",
            "category": rec.get("category"),
            "statement": rec.get("action"),
            **_trace(_assumptions_for_recommendation(rec, flow)),
        })

    # Surface the most fragile decisions first (lowest band, most inferred).
    decisions.sort(key=lambda d: (
        _BAND_ORDER.get(d.get("confidence"), 3),
        -len([s for s in d.get("supporting_assumptions", [])
              if s.get("kind") == "inferred"])))

    return {
        "decisions": decisions,
        "method": "weakest_link",
        "note": ("confidence is the stability band of a decision's LEAST stable "
                 "load-bearing assumption (weakest-link, not an average). The "
                 "band is always shown with its supporting-assumption trace "
                 "(band, basis, inferred/observed, perturbation) so the "
                 "derivation is auditable. A decision resting on an inferred "
                 "shape-heuristic or untested assumption is capped accordingly "
                 "BEFORE a capture confirms or refutes it."),
    }


def _downstream_sets(flow: Dict[str, Any]) -> Dict[str, set]:
    """Transitive downstream closure per node, from the depends-on edges. Edges
    read child depends_on parent, so the downstream of A (the conclusions
    resting on A) is everything that can reach A by following depends->on."""
    children_of: Dict[str, List[str]] = {}
    for e in flow.get("edges", []):
        children_of.setdefault(e["on"], []).append(e["depends"])
    out: Dict[str, set] = {}
    for n in flow.get("nodes", []):
        nid = n["id"]
        seen: set = set()
        stack = list(children_of.get(nid, []))
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            stack.extend(children_of.get(c, []))
        out[nid] = seen
    return out


def _rank_by(score: Dict[str, float]) -> Dict[str, int]:
    """Return {node: rank} (1 = highest score). Ties share the sorted position
    by a stable key so a pure tie does not read as a spurious rank change."""
    order = sorted(score, key=lambda k: (-score[k], k))
    return {nid: i + 1 for i, nid in enumerate(order)}


def _node_weight_regimes(types_present: List[str]) -> Dict[str, Dict[str, int]]:
    base = {t: _NODE_WEIGHT.get(t, 0) for t in types_present}
    vals = base.values()
    lo, hi = (min(vals), max(vals)) if base else (0, 0)
    regimes: Dict[str, Dict[str, int]] = {
        "baseline": dict(base),
        "all_equal": {t: 1 for t in types_present},
        # reflect each weight around the midpoint -> same value set, order reversed
        "rank_reversal": {t: (hi + lo) - base[t] for t in types_present},
    }
    # ±1 on each integer node weight, independently (floor at 0)
    for t in types_present:
        regimes[f"plus1:{t}"] = {**base, t: base[t] + 1}
        regimes[f"minus1:{t}"] = {**base, t: max(0, base[t] - 1)}
    return regimes


def _sensitivity(draft: "DetectorDraft") -> Dict[str, Any]:
    flow = draft.uncertainty_flow or {}
    nodes = flow.get("nodes", [])
    if not nodes:
        return {"robustness": {}, "modeling_dependencies": [],
                "robust_conclusions": [], "contingent_conclusions": [],
                "note": "no dependency graph to perturb"}

    type_of = {n["id"]: n["type"] for n in nodes}
    assume_nodes = [n["id"] for n in nodes if n["type"] == "assume"]
    downstream = _downstream_sets(flow)
    types_present = sorted({n["type"] for n in nodes})

    # ── 1) downstream-weight ranking of assumptions, swept over node weights ──
    # (drives flow highest_carry + blast_radius ordering)
    nw_regimes = _node_weight_regimes(types_present)
    dw_ranks: Dict[str, Dict[str, int]] = {}   # regime -> {assume: rank}
    for regime, w in nw_regimes.items():
        score = {a: sum(w.get(type_of[m], 0) for m in downstream[a])
                 for a in assume_nodes}
        dw_ranks[regime] = _rank_by(score)

    # ── 2) verify-first ranking, swept over fragility multipliers ──
    # risk = baseline downstream_weight × fragility multiplier(basis)
    base_dw = {a: sum(_NODE_WEIGHT.get(type_of[m], 0) for m in downstream[a])
               for a in assume_nodes}
    basis_of = {a: _FRAGILITY[_assumption_kind(a)]["basis"] for a in assume_nodes}
    base_mult = {b: _STAB[b]["mult"] for b in _STAB}
    rev_vals = sorted(base_mult.values())
    # reversed band->mult: most-fragile band gets the smallest multiplier
    by_band_desc = sorted(base_mult, key=lambda b: -base_mult[b])
    reversed_mult = {b: rev_vals[i] for i, b in enumerate(by_band_desc)}
    mult_regimes = {
        "baseline": base_mult,
        "all_equal_fragility": {b: 1.0 for b in base_mult},
        "fragility_reversal": reversed_mult,
    }
    vf_ranks: Dict[str, Dict[str, int]] = {}
    for regime, m in mult_regimes.items():
        score = {a: base_dw[a] * m[basis_of[a]] for a in assume_nodes}
        vf_ranks[regime] = _rank_by(score)

    # ── classify each assumption robust vs contingent ──
    def _classify(rank_table: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
        baseline = rank_table["baseline"]
        rows = []
        for a in assume_nodes:
            ranks = {r: rank_table[r][a] for r in rank_table}
            moved = sorted({r for r in rank_table
                            if rank_table[r][a] != baseline[a]})
            rows.append({
                "assumption": a,
                "baseline_rank": baseline[a],
                "robust": not moved,
                "moved_under": moved,
                "rank_range": [min(ranks.values()), max(ranks.values())],
            })
        rows.sort(key=lambda r: r["baseline_rank"])
        return rows

    dw_rows = _classify(dw_ranks)
    vf_rows = _classify(vf_ranks)

    # ── 3) hand-authored-rule dependency flags (say it plainly) ──
    # Every _FRAGILITY perturbation prediction and every weight is authored, not
    # measured. Flag the ones a conclusion's standing rests on — especially a
    # 'validates'/'resolves' prediction on an inferred assumption, which a real
    # capture can disprove. This DID happen for the rendition slot: pre-v3.66.76
    # it inherited the identity rule (different_title -> validates), a second
    # title returned may_invalidate, and the rule was split (skeleton_rendition
    # now correctly predicts may_invalidate; corpus VC-0005/VC-0006). Remaining
    # flagged 'validates' rules (e.g. skeleton_identity) are still authored — now
    # VALIDATED by that second title, but authored, so they stay flagged.
    deps: List[Dict[str, Any]] = []
    for a in assume_nodes:
        kind = _assumption_kind(a)
        spec = _FRAGILITY[kind]
        basis = spec["basis"]
        pert = spec["perturbation"]
        authored = [k for k, v in pert.items() if v in ("validates", "resolves")]
        if basis in ("heuristic", "shape_heuristic", "assumption_untested") and authored:
            deps.append({
                "assumption": a,
                "basis": basis,
                "hand_authored_rule": {k: pert[k] for k in authored},
                "flag": (
                    f"the claim that {', '.join(authored)} confirm "
                    f"{a} is a HAND-AUTHORED _FRAGILITY prediction on an "
                    f"inferred ({basis}) assumption, not a built-in observation "
                    f"— a capture can disprove it (the rendition slot's inherited "
                    f"'different_title -> validates' was disproved and split out "
                    f"in v3.66.76)."),
            })

    robust = [r["assumption"] for r in dw_rows if r["robust"]]
    contingent = [{"assumption": r["assumption"], "moved_under": r["moved_under"]}
                  for r in dw_rows if not r["robust"]]

    return {
        "robustness": {
            "by_downstream_weight": dw_rows,    # flow highest_carry / blast_radius
            "by_verify_first": vf_rows,         # stability verify_first
            "node_weight_regimes": sorted(nw_regimes),
            "fragility_regimes": sorted(mult_regimes),
        },
        "robust_conclusions": robust,
        "contingent_conclusions": contingent,
        "modeling_dependencies": deps,
        "note": (
            "robustness sweeps the hand-authored weights/fragility rules over a "
            "FIXED dependency structure. An assumption's ranking is 'robust' "
            "only if it is unchanged across every regime (±1 node weight, "
            "all-equal, rank-reversal, fragility-band reorder); 'contingent' "
            "means a modeling choice — not the data — determines its position. "
            "modeling_dependencies names conclusions whose standing rests on an "
            "unverified hand-authored perturbation rule."),
    }


def _request_key_of(r: Dict[str, Any]) -> str:
    method = (r.get("method") or "GET").upper()
    tmpl = r.get("url_template") or ""
    base = tmpl.partition("?")[0]
    try:
        sp = urlsplit(base)
        loc = f"{sp.netloc}{sp.path}" if sp.netloc else base
    except Exception:
        loc = base
    return f"{method} {loc}"
