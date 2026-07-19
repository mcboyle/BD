"""capture_workbench: synth output -> reviewable detector draft (v3.66.60).

Behavioural tests on a controlled two-capture (A/B) fixture pair built to
exercise every stability verdict the workbench assigns, plus posture/static
guards that the workbench is recognition-only (no fetch/replay/synthesis, and
signing material never receives an extraction pattern).

The fixture models one "play a video" action across two sessions:
  * a config request whose JSON body carries a stable id (vid_*) and a
    rotating session token;
  * a media request that embeds the (rotating) id in its PATH and carries a
    rotating session token, a signing token, and an epoch expiry in the query.
Across A and B the ids/tokens/expiry all rotate — the drift-heavy case.
"""

from __future__ import annotations

import re

import pytest

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb


def _entry(seq, method, url, *, body=None, rtype="xhr"):
    return {
        "seq": seq, "iso": "2026-01-01T00:00:00Z", "timestamp": seq * 1000,
        "type": rtype, "method": method, "url": url,
        "request_headers": [], "request_body": None,
        "response_status": 200, "response_status_text": "OK",
        "response_headers": [{"name": "Content-Type",
                              "value": "application/json" if body else "text/plain"}],
        "response_body": body, "response_body_truncated": False,
        "response_body_skipped_reason": None, "duration_ms": 5, "error": None,
    }


def _capture(vid, sess, tok, exp, cb):
    """One capture of the play action. All five values rotate between A and B.

    The config body contains the stable id `vid` in cleartext (it is NOT
    signing material, so a real redactor would keep it) — this is the
    provenance source for the media request's path id. Body capture is
    modelled as ON (bodies retained) so _trace_source can find it.
    """
    host = "example.test"
    config_body = (
        '{"video_id":"%s","session":"%s",'
        '"playlist":"/play/%s/master.m3u8"}' % (vid, sess, vid))
    return {
        "capture_version": 1, "host": host,
        "url": "https://%s/watch?v=%s" % (host, vid),
        "origin": "https://%s" % host, "pathname": "/watch",
        "search": "?v=%s" % vid, "title": "watch", "user_agent": "ua",
        "cookies": "<scrubbed>",
        "network_log": [
            _entry(0, "GET", "https://%s/api/config?v=%s" % (host, vid),
                   body=config_body),
            # media request: rotating id in PATH; query carries a rotating
            # session token, a signing token, an epoch expiry, a cache-buster.
            _entry(1, "GET",
                   "https://cdn.%s/play/%s/master.m3u8"
                   "?session=%s&token=%s&expires=%s&_=%s"
                   % (host, vid, sess, tok, exp, cb),
                   rtype="xhr"),
        ],
    }


def _build():
    a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
    b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
    synth = synthesize(a, b)
    return wb.build_workbench(synth), synth


class TestWorkbenchVerdicts:

    def test_builds_and_identifies_host_and_goal(self):
        draft, synth = _build()
        d = draft.to_dict()
        assert d["host"] == "example.test"
        # goal should be the media (.m3u8) request
        assert d["goal_request"] and "master.m3u8" in d["goal_request"]
        assert d["confidence"] == "low"  # N=2 floor inherited from synth

    def test_signing_slots_are_opaque_never_patterned(self):
        draft, _ = _build()
        d = draft.to_dict()
        opaque_params = {o["param"] for o in d["opaque_slots"]}
        # the signing token and the epoch expiry are credential-keyed
        assert "token" in opaque_params
        assert "expires" in opaque_params
        # and NONE of them appear as an extraction pattern
        pat_keys = {p["key"] for p in d["draft_patterns"]}
        assert "token" not in pat_keys
        assert "expires" not in pat_keys

    def test_provenance_edge_for_path_id(self):
        # vid_* appears in the config body and is reused in the media path,
        # so the path slot should be a provenance edge (carried, stable edge).
        draft, _ = _build()
        d = draft.to_dict()
        srcs = " ".join(str(e.get("from_source")) for e in d["provenance_edges"])
        # the path id's source should trace to the config response body
        assert "response_body" in srcs or any(
            s.verdict == wb.PROVENANCE for s in draft.slots)

    def test_client_computed_value_flagged_unrecoverable(self):
        # the cache-buster `_` has no observable source and an opaque shape
        # -> client-computed-suspected, surfaced as unrecoverable.
        draft, _ = _build()
        d = draft.to_dict()
        unrec_params = {u["param"] for u in d["unrecoverable"]}
        assert "_" in unrec_params or "session" in unrec_params

    def test_every_slot_has_confidence_and_rationale(self):
        draft, _ = _build()
        for s in draft.slots:
            assert s.confidence in ("high", "medium", "low")
            assert s.rationale
            assert s.verdict in (wb.SIGNING, wb.PROVENANCE, wb.STABLE_ID,
                                 wb.CLIENT_COMPUTED, wb.ROTATING_OPAQUE,
                                 wb.INVARIANT)

    def test_known_vs_inferred_marked(self):
        draft, _ = _build()
        d = draft.to_dict()
        # each slot dict carries a 'basis' of exactly observed|inferred
        for s in d["slots"]:
            assert s["basis"] in ("observed", "inferred")

    def test_slots_carry_evidence_and_affects(self):
        draft, _ = _build()
        d = draft.to_dict()
        # every slot has at least one concrete evidence line and a component map
        for s in d["slots"]:
            assert s.get("evidence"), f"no evidence for {s['param']}"
            assert s.get("affects"), f"no affects for {s['param']}"

    def test_signing_evidence_never_echoes_value(self):
        draft, _ = _build()
        d = draft.to_dict()
        for s in d["slots"]:
            if s["verdict"] == wb.SIGNING:
                joined = " ".join(s.get("evidence", []))
                # the actual signing token from the fixture must never appear
                assert "tokSIGN" not in joined
                assert "withheld" in joined.lower()


class TestPostureRecognitionOnly:
    """Static guards: the module must not contain fetch/replay/synthesis."""

    def _src(self):
        # capture_workbench is now an ADD-only shim over capture_workbench_impl/;
        # inspect.getsource on the package returns only __init__/shim and would silently
        # stop scanning the moved analysis code. Iterate every submodule so the posture
        # guard keeps covering the real implementation (DECOMP-LEAF cut 4).
        import importlib, inspect, pkgutil
        import bulk_downloader.capture_workbench_impl as pkg
        parts=[inspect.getsource(pkg)]
        for mi in pkgutil.iter_modules(pkg.__path__):
            parts.append(inspect.getsource(importlib.import_module(f"{pkg.__name__}.{mi.name}")))
        return "\n".join(parts)

    def test_no_network_or_replay_verbs(self):
        # No HTTP client / replay machinery in a recognition tool. We scan
        # only non-comment, non-docstring CODE lines so the module's own
        # posture prose ("no replay, no synthesis") doesn't false-trip the
        # guard — what we forbid is the *capability*, not the word.
        import io, tokenize
        src = self._src()
        code = []
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            code.append(tok.string)
        code_s = " ".join(code).lower()
        for bad in ("requests.get", "urlopen", "httpx", "aiohttp",
                    "session.get", ".send("):
            assert bad not in code_s, f"forbidden capability token: {bad}"
        # network/replay imports must be absent entirely
        for mod in ("import requests", "import httpx", "import aiohttp",
                    "from urllib.request"):
            assert mod not in src, f"forbidden import: {mod}"

    def test_posture_statement_present_in_output(self):
        draft, _ = _build()
        d = draft.to_dict()
        assert "never reconstructed" in d["posture"].lower() \
            or "not reconstructed" in d["posture"].lower()

    def test_draft_patterns_only_target_id_shapes(self):
        draft, _ = _build()
        # every emitted pattern's sample shape is an identifier shape, never
        # a signing shape
        for p in draft.draft_patterns:
            assert p.sample_shape in wb._ID_SHAPES + ("opaque",)
            assert "jwt" != p.sample_shape


class TestGoalSkeleton:
    """goal_skeleton: goal url_template -> addressable path slots + opaque
    signing. Built on a hand-made synth-shaped dict for precision."""

    def _synth(self):
        return {
            "host": "cdn.example.test",
            "entry_url": "https://example.test/watch",
            "confidence": "low",
            "requests": [{
                "seq": 9, "method": "GET", "goal": True,
                "key": "GET dd.example.test/a1b2c3d4/1920x1080.mp4",
                "url_template": ("https://dd.example.test/a1b2c3d4/"
                                 "1920x1080.mp4?expires={expires}&token={token}"),
                "classification": "varying", "is_media": True,
                "params": [
                    {"key": "expires", "type": "redacted", "credential": True},
                    {"key": "token", "type": "redacted", "credential": True},
                ],
            }],
            "credentials_required": ["expires (query)", "token (query)"],
            "unresolved": [], "notes": [],
        }

    def test_skeleton_templates_addressable_segments(self):
        sk = wb.goal_skeleton(self._synth())
        assert sk is not None
        assert sk["host"] == "dd.example.test"
        # v3.66.69: addressable segments split by role. The opaque hex id is an
        # IDENTITY slot ("content_id"); a resolution-descriptor filename is a
        # RENDITION slot ("rendition"). (Pre-.69 both were named by shape and
        # the filename was wrongly called a per-title identifier.)
        names = {s["name"] for s in sk["skeleton_slots"]}
        roles = {s["name"]: s.get("role") for s in sk["skeleton_slots"]}
        assert "content_id" in names
        assert roles.get("content_id") == "identity"
        # the resolution filename is present as a rendition-role slot
        assert any(r == "rendition" for r in roles.values())
        # the templated path uses the slot names, not the literal values
        assert "{content_id}" in sk["path_template"]
        assert "a1b2c3d4" not in sk["path_template"]

    def test_skeleton_never_patterns_signing(self):
        sk = wb.goal_skeleton(self._synth())
        # signing params are surfaced as opaque, never as a path slot/regex
        sp = {p["param"] for p in sk["signing_params"]}
        assert sp == {"expires", "token"}
        slot_samples = {s["sample"] for s in sk["skeleton_slots"]}
        # no credential value leaked into a skeleton slot
        assert not (slot_samples & {"{expires}", "{token}", "expires", "token"})

    def test_skeleton_slots_have_regex_and_rationale(self):
        sk = wb.goal_skeleton(self._synth())
        for s in sk["skeleton_slots"]:
            assert s["regex"]
            assert s["rationale"]
            assert s["confidence"] in ("high", "medium", "low")

    def test_no_goal_returns_none(self):
        s = self._synth()
        s["requests"][0]["goal"] = False
        assert wb.goal_skeleton(s) is None

    def test_structural_words_stay_literal(self):
        s = self._synth()
        s["requests"][0]["url_template"] = (
            "https://dd.example.test/static/embed/a1b2c3d4/play.mp4")
        s["requests"][0]["params"] = []
        sk = wb.goal_skeleton(s)
        assert "static" in sk["literal_segments"]
        assert "embed" in sk["literal_segments"]
        # the hex id is still addressable
        assert any(s["sample"] == "a1b2c3d4" for s in sk["skeleton_slots"])


class TestDetectorImpact:
    """The impact layer maps findings to REAL detector components and calls
    the real classify_url on the goal."""

    def test_impact_present_and_grounded(self):
        draft, _ = _build()
        d = draft.to_dict()
        imp = d["impact"]
        assert imp is not None
        # goal in the fixture is a .m3u8 -> hls_manifest -> generic path,
        # so NO new provider should be required
        assert imp["new_provider_required"] is False
        assert imp["goal_classification"]["type"] in (
            "hls_manifest", "direct_file")
        assert imp["likely_components"]
        assert imp["effort_focus"]

    def test_impact_recommends_confidence_captures(self):
        draft, _ = _build()
        caps = " ".join(draft.to_dict()["impact"]["confidence_raising_captures"])
        # v3.66.69: this fixture's content id VARIES across the pair, so it is
        # an observed synth slot, not a shape-inferred identity skeleton slot;
        # its only skeleton slot is a RENDITION descriptor. The corrected impact
        # therefore does NOT claim a 2nd title promotes the rendition (the
        # falsified over-claim) — it flags the rendition as a quality menu and
        # still recommends a 3rd session for the N=2 invariants.
        assert "rendition" in caps and "NOT promoted by a second title" in caps
        assert "3rd session" in caps

    def test_unknown_provider_host_flags_new_provider(self):
        # a goal on a provider-LIKE host that isn't a bare media file and isn't
        # a known provider -> new provider plumbing flagged
        synth = {
            "host": "x", "entry_url": None, "confidence": "low",
            "requests": [{
                "seq": 1, "goal": True, "method": "GET",
                "key": "GET api.someprovider.io/v2/playback/abc",
                "url_template": "https://api.someprovider.io/v2/playback/abc",
                "classification": "varying", "is_media": False, "params": [],
            }],
            "credentials_required": [], "unresolved": [], "notes": [],
        }
        imp = wb.build_workbench(synth).to_dict()["impact"]
        # extensionless non-provider, non-binary -> not a generic media bucket
        if imp.get("goal_classification", {}).get("type") not in (
                "direct_file", "extensionless_file", "hls_manifest",
                "dash_manifest"):
            assert imp["new_provider_required"] is True
            assert any("3-place" in c for c in imp["likely_components"])


class TestChangePlan:
    """The prioritized maintainer change plan, derived from the draft's own
    computed fields (impact/skeleton/slots/unrecoverable)."""

    def test_ultrafilms_like_plan_categories(self):
        # the controlled fixture goal is a .m3u8 -> hls_manifest (generic),
        # with signing slots and unrecoverable telemetry -> expect:
        #  - selector/workflow investigation (generic bucket + signing)
        #  - additional capture recommended
        #  - existing classifier sufficient (no provider)
        #  - unrecoverable present
        draft, _ = _build()
        cats = [r["category"] for r in draft.to_dict()["change_plan"]]
        assert wb.CP_SELECTOR_WORKFLOW in cats
        assert wb.CP_CLASSIFIER_SUFFICIENT in cats
        assert wb.CP_ADDITIONAL_CAPTURE in cats
        assert wb.CP_UNRECOVERABLE in cats
        # no provider plumbing for a generic-bucket goal
        assert wb.CP_DETECTOR_CONFIG not in cats

    def test_plan_is_priority_sorted(self):
        draft, _ = _build()
        plan = draft.to_dict()["change_plan"]
        prios = [r["priority"] for r in plan]
        assert prios == sorted(prios)
        # every rec has an action + why
        for r in plan:
            assert r["action"] and r["why"]

    def test_new_provider_plan_leads_with_config_change(self):
        synth = {
            "host": "x", "entry_url": None, "confidence": "low",
            "requests": [{
                "seq": 1, "goal": True, "method": "GET",
                "key": "GET api.someprovider.io/v2/playback/abc",
                "url_template": "https://api.someprovider.io/v2/playback/abc",
                "classification": "varying", "is_media": False, "params": [],
            }],
            "credentials_required": [], "unresolved": [], "notes": [],
        }
        plan = wb.build_workbench(synth).to_dict()["change_plan"]
        if plan and plan[0]["category"] == wb.CP_DETECTOR_CONFIG:
            # if a new provider is implicated it should be the top priority
            assert plan[0]["priority"] == 1
            assert any("PROVIDERS" in r for r in plan[0]["refs"])

    def test_plan_never_empty(self):
        draft, _ = _build()
        assert len(draft.to_dict()["change_plan"]) >= 1


class TestUncertaintyPlan:
    """Ranked uncertainty reduction: which next capture buys the most
    confidence. Built with captures so body-retention is known."""

    def _build_with_caps(self):
        # reuse the module-level fixture builders
        a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
        b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
        synth = synthesize(a, b)
        return wb.build_workbench(synth, captures=(a, b)), (a, b)

    def test_uncertainty_present_and_ranked(self):
        draft, _ = self._build_with_caps()
        unc = draft.to_dict()["uncertainty"]
        assert unc is not None
        assert unc["ranked"]
        # sorted by weighted_resolved descending
        ws = [c["weighted_resolved"] for c in unc["ranked"]]
        assert ws == sorted(ws, reverse=True)

    def test_bodies_already_on_zeroes_the_bodies_candidate(self):
        # the fixture's config request carries a real response body, so bodies
        # are "retained" -> the retained_bodies candidate must resolve nothing
        draft, _ = self._build_with_caps()
        unc = draft.to_dict()["uncertainty"]
        assert unc["bodies_retained"] is True
        rb = next(c for c in unc["ranked"] if c["evidence"] == "retained_bodies")
        assert rb["weighted_resolved"] == 0
        assert rb["estimated_uncertainty_reduction_pct"] == 0
        assert "ALREADY retained" in rb["note"]

    def test_second_title_outranks_bodies_when_bodies_on(self):
        draft, _ = self._build_with_caps()
        unc = draft.to_dict()["uncertainty"]
        order = [c["evidence"] for c in unc["ranked"]]
        assert order.index("second_different_title") < order.index("retained_bodies")

    def test_candidates_carry_justification(self):
        draft, _ = self._build_with_caps()
        for c in draft.to_dict()["uncertainty"]["ranked"]:
            assert c["note"]
            assert "estimated_uncertainty_reduction_pct" in c
            assert "slots_that_move_category" in c

    def test_bodies_unknown_when_no_captures(self):
        # build_workbench without captures -> body state unknown, still ranks
        a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
        b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
        draft = wb.build_workbench(synthesize(a, b))  # no captures
        unc = draft.to_dict()["uncertainty"]
        assert unc["bodies_retained"] is None
        assert unc["ranked"]


class TestUncertaintyFlow:
    """The dependency graph: point at an assumption, see what rests on it;
    and which inferred assumption carries the most downstream."""

    def test_flow_present_with_nodes_and_edges(self):
        draft, _ = _build()
        flow = draft.to_dict()["uncertainty_flow"]
        assert flow is not None
        assert flow["nodes"] and flow["edges"]
        # every edge references existing nodes
        ids = {n["id"] for n in flow["nodes"]}
        for e in flow["edges"]:
            assert e["depends"] in ids and e["on"] in ids

    def test_goal_selection_is_a_top_carrier(self):
        # the impact + plan chain all rests on the goal-selection heuristic,
        # so it should carry significant downstream weight
        draft, _ = _build()
        flow = draft.to_dict()["uncertainty_flow"]
        carriers = {c["node"]: c for c in flow["highest_carry"]}
        assert "assume:goal_selection" in carriers
        gc = carriers["assume:goal_selection"]
        # the classification/provider/effort impact nodes rest on it
        assert any("impact:" in d for d in gc["carries"])

    def test_carriers_sorted_by_downstream_weight(self):
        draft, _ = _build()
        flow = draft.to_dict()["uncertainty_flow"]
        ws = [c["downstream_weight"] for c in flow["highest_carry"]]
        assert ws == sorted(ws, reverse=True)

    def test_downstream_closure_is_transitive(self):
        # goal_selection -> goal_classification -> new_provider -> effort_focus
        # -> plan items: the closure must include the transitive plan nodes
        draft, _ = _build()
        flow = draft.to_dict()["uncertainty_flow"]
        nmap = {n["id"]: n for n in flow["nodes"]}
        gs = nmap.get("assume:goal_selection")
        assert gs is not None
        # transitive: a plan node downstream of effort_focus is reachable
        assert any(d.startswith("plan:") for d in gs["downstream"])

    def test_capture_resolvable_flag_marks_handcheck_assumptions(self):
        # goal_selection is NOT resolved by any capture candidate -> flagged
        draft, _ = _build()
        flow = draft.to_dict()["uncertainty_flow"]
        carriers = {c["node"]: c for c in flow["highest_carry"]}
        if "assume:goal_selection" in carriers:
            assert carriers["assume:goal_selection"]["capture_resolvable"] is False

    def test_what_if_provenance_edge_when_none_present(self):
        # fixture has unrecoverable slots and no edges -> a what-if is offered
        draft, _ = _build()
        flow = draft.to_dict()["uncertainty_flow"]
        if draft.unrecoverable and not draft.provenance_edges:
            assert flow["what_if"]
            assert "plan:" + wb.CP_UNRECOVERABLE in flow["what_if"][0]["would_change"]


class TestAssumptionStability:
    """Per-assumption trust: basis kind, survival, perturbation fragility,
    scope, and the verify-first ranking crossing fragility with weight."""

    def test_stability_present_for_each_assumption(self):
        draft, _ = _build()
        stab = draft.to_dict()["assumption_stability"]
        assert stab is not None
        assert stab["assumptions"]
        for a in stab["assumptions"]:
            assert a["basis"] in ("heuristic", "shape_heuristic",
                                  "assumption_untested", "negative_observation",
                                  "structural_limitation")
            assert a["stability_band"]
            assert set(a["would_invalidate"]) == {
                "different_title", "more_sessions", "player_config", "workflow"}

    def test_goal_selection_is_low_stability_heuristic(self):
        draft, _ = _build()
        stab = draft.to_dict()["assumption_stability"]
        by = {a["node"]: a for a in stab["assumptions"]}
        if "assume:goal_selection" in by:
            gs = by["assume:goal_selection"]
            assert gs["basis"] == "heuristic"
            assert gs["stability_band"] == "low"

    def test_src_unknown_is_high_stability_observation(self):
        draft, _ = _build()
        stab = draft.to_dict()["assumption_stability"]
        srcs = [a for a in stab["assumptions"]
                if a["node"].startswith("assume:src_unknown:")]
        for a in srcs:
            assert a["basis"] == "negative_observation"
            assert a["stability_band"] == "high"

    def test_verify_first_crosses_weight_and_fragility(self):
        # goal_selection (high weight + low stability) should top verify_first,
        # above the high-stability source-unknown assumptions
        draft, _ = _build()
        vf = draft.to_dict()["assumption_stability"]["verify_first"]
        scores = [r["risk_score"] for r in vf]
        assert scores == sorted(scores, reverse=True)
        if vf and any(r.get("node") == "assume:goal_selection" for r in vf):
            gs_score = next(r["risk_score"] for r in vf
                            if r.get("node") == "assume:goal_selection")
            src_scores = [r["risk_score"] for r in vf
                          if r.get("node", "").startswith("assume:src_unknown:")
                          or r.get("group") == "src_unknown"]
            if src_scores:
                assert gs_score > max(src_scores)

    def test_large_homogeneous_family_is_collapsed(self):
        # many source-unknown telemetry params -> one grouped verify_first row
        synth = {
            "host": "x", "entry_url": None, "confidence": "low",
            "requests": [{
                "seq": 9, "goal": True, "method": "GET",
                "key": "GET cdn.x/a1b2c3d4/v.mp4",
                "url_template": "https://cdn.x/a1b2c3d4/v.mp4?expires={expires}",
                "classification": "varying", "is_media": True,
                "params": [{"key": "expires", "type": "redacted",
                            "credential": True}],
            }],
            "credentials_required": ["expires (query)"],
            "unresolved": [{"request": "GET telem", "param": f"t{i}",
                            "reason": "source-unknown"} for i in range(10)],
            "notes": [],
        }
        vf = wb.build_workbench(synth).to_dict()[
            "assumption_stability"]["verify_first"]
        groups = [r for r in vf if r.get("group") == "src_unknown"]
        assert len(groups) == 1 and groups[0]["count"] == 10
        assert not [r for r in vf
                    if r.get("node", "").startswith("assume:src_unknown:")]

    def test_skeleton_perturbation_is_role_split(self):
        # v3.66.76: the skeleton perturbation is role-dependent — identity
        # validates under a different title (VC-0006), rendition may_invalidate
        # (filename is resolution-keyed, not title-keyed; VC-0005).
        draft, _ = _build()
        stab = draft.to_dict()["assumption_stability"]
        sk = [a for a in stab["assumptions"]
              if a["node"].startswith("assume:skeleton:")]
        assert sk, "fixture should have skeleton slots"
        for a in sk:
            dt = a["would_invalidate"]["different_title"]
            if a["node"].startswith("assume:skeleton:content_id"):
                assert dt == "validates", a["node"]
            elif a["node"].startswith("assume:skeleton:rendition"):
                assert dt == "may_invalidate", a["node"]


class TestBlastRadius:
    def test_blast_radius_present_and_sorted(self):
        draft, _ = _build()
        br = draft.to_dict()["blast_radius"]
        assert br and br["by_assumption"]
        ws = [r["blast_weight"] for r in br["by_assumption"]]
        assert ws == sorted(ws, reverse=True)

    def test_goal_selection_has_largest_blast(self):
        draft, _ = _build()
        br = draft.to_dict()["blast_radius"]["by_assumption"]
        top = br[0]
        # goal_selection should be the largest blast (impact + plan collapse)
        if any(r["assumption"] == "assume:goal_selection" for r in br):
            gs = next(r for r in br if r["assumption"] == "assume:goal_selection")
            assert gs["if_it_fails"]["collapsed_impact"]
            assert gs["if_it_fails"]["changed_recommendations"]

    def test_fraction_is_bounded(self):
        draft, _ = _build()
        for r in draft.to_dict()["blast_radius"]["by_assumption"]:
            assert 0 <= r["fraction_of_draft_pct"] <= 100


class TestGeneralization:
    def test_partitions_framework_and_site(self):
        draft, _ = _build()
        gen = draft.to_dict()["generalization"]
        # skeleton patterns are directly reusable; values/conclusions are local
        assert gen["framework_level"] and gen["site_specific"]
        assert "reusable_classes" in gen

    def test_skeleton_pattern_framework_value_local(self):
        draft, _ = _build()
        gen = draft.to_dict()["generalization"]
        fw = " ".join(g["node"] for g in gen["framework_level"])
        sp = " ".join(g["node"] for g in gen["site_specific"])
        if any("pattern:skeleton:" in g["node"] for g in gen["framework_level"]):
            assert "pattern:skeleton:" in fw
            assert "value:skeleton:" in sp

    def test_signing_is_a_reusable_class(self):
        draft, _ = _build()
        gen = draft.to_dict()["generalization"]
        # signing recognition is a reusable rule-class, not a per-instance
        # framework item
        assert any("signing" in c["class"] for c in gen["reusable_classes"])

    def test_telemetry_not_inflating_framework_list(self):
        # the 38-style telemetry params must NOT each be listed framework-level;
        # they're a single reusable class, instances local
        a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
        b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
        draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
        gen = draft.to_dict()["generalization"]
        # no src_unknown assumption should appear in framework_level
        assert not any("src_unknown" in g.get("node", "")
                       for g in gen["framework_level"])
        # but the telemetry class should be recorded once if any exist
        if draft.unrecoverable:
            assert any("telemetry" in c["class"]
                       for c in gen["reusable_classes"])


class TestContradictions:
    def test_clean_draft_has_no_contradictions(self):
        # with bodies threaded correctly, the fixture should be self-consistent
        a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
        b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
        draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
        assert draft.to_dict()["contradictions"] == []

    def test_detects_param_both_addressable_and_unrecoverable(self):
        # construct a draft where a skeleton slot name also appears unrecoverable
        a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
        b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
        draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
        # force a contradiction: inject a fake skeleton slot matching an unrec param
        if draft.unrecoverable:
            p = draft.unrecoverable[0]["param"]
            draft.skeleton["skeleton_slots"].append(
                {"name": p, "sample": "x", "shape": "id", "regex": r"\d+",
                 "confidence": "medium", "inferred": True, "rationale": "x"})
            c = wb._contradictions(draft, True)
            assert any(x["check"] == "param_both_addressable_and_unrecoverable"
                       for x in c)

    def test_detects_stale_bodies_recommendation(self):
        # a draft whose impact recommends bodies while bodies_state is True
        a = _capture("vid_AAA111", "sessAAA", "tokSIGNAAA", "1700000000", "111111")
        b = _capture("vid_BBB222", "sessBBB", "tokSIGNBBB", "1700009999", "999999")
        draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
        # inject the stale recommendation that the threading normally prevents
        draft.impact["confidence_raising_captures"].append(
            "re-capture with BD_CAPTURE_BODIES=1 — may resolve source-unknown")
        c = wb._contradictions(draft, True)
        assert any(x["check"] == "stale_capture_recommendation" for x in c)
