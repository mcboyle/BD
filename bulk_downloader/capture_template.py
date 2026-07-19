"""Template-vs-capture drift detection — the symbiotic capture/template loop.

The workbench's `capture_synth` diffs TWO captures against each other to find
what varies. This module is the other half of the loop: it diffs ONE fresh
capture against a stored *template* — a frozen set of predictions distilled
from a previously confirmed `DetectorDraft`.

Why this is symbiotic rather than a replacement for either:

  * A capture is ground-truth observation of a single moment, but it cannot
    tell you whether what it observed will still hold next week or for another
    title.
  * A template is a durable generalization, but it has no evidence behind its
    assertions until something checks them.

`build_template()` distils a confirmed draft into the durable predictions (the
goal classification, the URL skeleton, the addressable-slot patterns, and the
signing expectation) plus the human-confirmable goal selection. `diff_template()`
then runs a fresh capture against that template and reports which predictions
still HELD and which DRIFTED. The template stops being a static artifact and
becomes something a capture continuously re-validates — and a site change (new
CDN host, an added path segment, newly-introduced signing) shows up as drift
*before* a download silently breaks.

It also closes, by accumulation, the one assumption no single capture can close
from scratch: goal selection. A template can store a human-confirmed goal pick;
every later diff then inherits that verified pick instead of re-guessing with a
heuristic.

POSTURE (load-bearing — see CAPTURE_SYNTHESIS_POSTURE.md):
This module is recognition-only. It compares a stored template's predictions
against observed capture *structure* — classification (via the real
`deep_detect.classify_url`), URL-shape match, and signing-marker presence. It
NEVER reconstructs, computes, or replays signed/short-lived material. Signing is
detected and surfaced (present / absent / newly-appeared), never extracted or
synthesized. There is no HTTP, no stream assembly, and no value synthesis here —
the diff terminates at "this prediction still holds" or "this prediction
drifted", exactly like the rest of the workbench.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, parse_qs

# extraction_core consolidation (step 4): the URL segmentation and recorded-rendition
# check this module shares with the other producers now live in the canonical core
# (proven byte-identical by tests/test_extraction_core.py + the characterization golden).
# split_segments is bound to the existing module name `_segments` so the call sites are
# unchanged; the _is_recorded_rendition closure becomes recorded_rendition_ok(sv, slots_meta).
from .extraction_core import split_segments as _segments, recorded_rendition_ok

# Drift statuses (module-level constants so tests and callers share them).
HELD = "held"            # observation still matches the template prediction
DRIFTED = "drifted"      # observation contradicts the template prediction
MISSING = "missing"      # the predicted thing was not observed at all
NEW = "new"              # something appeared that the template didn't predict

# v3.66.69 — five-way goal-match verdict. The pre-.69 matcher returned only
# HELD/DRIFTED/MISSING and a permissive filename regex let "the family still
# exists" read as "the same goal still holds". The real-data second-title
# validation falsified that. These finer verdicts distinguish the five things
# that can change between two captures of the same goal shape, which map 1:1
# onto the operator's churn taxonomy:
#   IDENTITY_CHANGE             — different title (content_id), structure holds.
#                                 EXPECTED/informational when testing a 2nd
#                                 title; NOT counted as drift.
#   RENDITION_DRIFT             — same family, but the recorded rendition member
#                                 is absent (same title no longer exposing the
#                                 same recorded asset). Counted as drift.
#   IDENTITY_AND_RENDITION_CHANGE — both moved. Title change is informational,
#                                 but the rendition mismatch is still called out
#                                 (counted as drift on the rendition axis).
#   STRUCTURAL_DRIFT           — host present but segment count / literals /
#                                 slot-shape changed (the family structure
#                                 itself moved). Counted as drift.
#   MISSING                    — the goal host was not seen at all.
IDENTITY_CHANGE = "identity_change"
RENDITION_DRIFT = "rendition_drift"
IDENTITY_AND_RENDITION_CHANGE = "identity_and_rendition_change"
STRUCTURAL_DRIFT = "structural_drift"

# Segment roles (kept in sync with capture_workbench).
IDENTITY_ROLE = "identity"
RENDITION_ROLE = "rendition"

TEMPLATE_VERSION = 2     # v1 = pre-.69 (no slot role/recorded); v2 = .69+


# ── building a template from a confirmed draft ─────────────────────
def build_template(draft: "Any") -> Dict[str, Any]:
    """Freeze a DetectorDraft's durable, confirmable predictions into a template.

    Only the parts that are meant to hold across captures are kept: the goal's
    classification + URL skeleton + slot patterns + signing expectation. The
    goal selection is recorded as a human-confirmable fact — it ships
    `unconfirmed` and a maintainer flips it to `confirmed` once verified, after
    which future diffs treat the pick as settled rather than heuristic.
    """
    sk = draft.skeleton or {}
    imp = draft.impact or {}
    gc = imp.get("goal_classification") or {}

    # signing_expected = the credential/signing params named on the goal request
    # (from the skeleton) unioned with any params the draft flagged signing.
    signing_expected = sorted({s["param"] for s in sk.get("signing_params", [])})

    # v3.66.69: freeze each slot's ROLE (identity | rendition) and its RECORDED
    # value, not just the regex. The recorded value is what the diff compares
    # observed-vs-recorded against, so a permissive rendition regex can no
    # longer turn "the family still exists" into "the same goal still holds".
    # Recorded values are PATH identifiers (content id, rendition descriptor) —
    # never signing material — so storing them is posture-clean. The query is
    # never read here.
    slots = [{"name": s["name"], "regex": s["regex"], "shape": s.get("shape"),
              "role": s.get("role", "identity"), "recorded": s.get("sample")}
             for s in sk.get("skeleton_slots", [])]

    return {
        "template_version": TEMPLATE_VERSION,
        "host": sk.get("host"),
        "built_from": {"host": draft.host,
                       "goal_request_key": getattr(draft, "goal_request_key",
                                                    None)},
        "goal": {
            "request_key": sk.get("request_key"),
            "host": sk.get("host"),
            "path_template": sk.get("path_template"),
            "url_template": sk.get("url_template"),
            "classification": gc.get("type"),
            "new_provider_required": imp.get("new_provider_required"),
            "slots": slots,
            "signing_expected": signing_expected,
        },
        "confirmed": {
            "goal_selection": {
                "status": "unconfirmed",
                "shape": sk.get("path_template"),
                "note": ("the goal pick is a heuristic until a maintainer "
                         "verifies it is the real download target; set status "
                         "to 'confirmed' to amortize that judgement across all "
                         "future diffs"),
            },
        },
        "provenance": ("distilled from a DetectorDraft; recognition-only. "
                       "Signing params are recorded as expected markers, never "
                       "as recoverable values."),
    }


# ── structural matcher: does a capture still contain the goal shape? ─
def _parse_template_path(path_template: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Split 'host/seg/{slot}/seg' into (host, [(kind, value), ...]) where kind
    is 'lit' for a literal segment or 'slot' for a '{name}' placeholder."""
    parts = (path_template or "").split("/")
    host = parts[0] if parts else ""
    segs: List[Tuple[str, str]] = []
    for seg in parts[1:]:
        if seg.startswith("{") and seg.endswith("}"):
            segs.append(("slot", seg[1:-1]))
        else:
            segs.append(("lit", seg))
    return host, segs


def _safe_url(url: str) -> str:
    """Return scheme://host/path with the query string DROPPED. Echoing a
    matched goal URL verbatim would surface the live signing values
    (expires/token/...) in the diff output; the path shape is the durable,
    non-signing part and the only thing we ever echo. Signing presence is
    reported by param NAME elsewhere, never by value."""
    sp = urlsplit(url)
    return f"{sp.scheme}://{sp.netloc}{sp.path}" if sp.scheme else \
        f"{sp.netloc}{sp.path}"


def _match_goal(template: Dict[str, Any],
                capture: Dict[str, Any]) -> Dict[str, Any]:
    """Find every request in `capture` that matches the template's goal shape,
    then compare observed values against the RECORDED goal values per slot role.

    v3.66.69 — two changes from the pre-.69 matcher:
      1. It no longer returns the FIRST shape-matching request. It collects the
         WHOLE matched set and reports per-slot observed value sets, so a
         verdict can never read clean while quietly serving a different
         rendition than the recorded goal (the masked-drift bug the real-data
         validation exposed).
      2. It compares each slot's observed value(s) against the template's
         RECORDED value, bucketed by role (identity vs rendition), and emits a
         five-way `verdict`. `status` stays the coarse HELD/DRIFTED/MISSING for
         backward compatibility (a shape-matching request exists -> HELD;
         host present but no shape match -> DRIFTED/STRUCTURAL_DRIFT; host
         absent -> MISSING).

    Pre-.69 templates have no `recorded`/`role` on their slots; for those the
    recorded comparison is skipped and behaviour falls back to the old
    shape-only match (verdict HELD when a request matches). Run
    `migrate_template()` to upgrade such a template in place.

    Recognition-only: every echoed URL is query-stripped via `_safe_url`; the
    representative request's query (`_matched_query`, names+values) is used
    ONLY for the signing-NAME presence check and is dropped before the diff
    result is returned.
    """
    import re
    goal = template.get("goal", {})
    t_host, t_segs = _parse_template_path(goal.get("path_template", ""))
    slots_meta = {s["name"]: s for s in goal.get("slots", [])}
    slot_re = {name: s["regex"] for name, s in slots_meta.items()}

    host_seen = False
    matches: List[Tuple[str, Dict[str, str], Dict[str, Any]]] = []
    for e in capture.get("network_log") or []:
        url = e.get("url") or ""
        host, segs = _segments(url)
        if host != t_host:
            continue
        host_seen = True
        if len(segs) != len(t_segs):
            continue
        slot_values: Dict[str, str] = {}
        ok = True
        for (kind, val), observed in zip(t_segs, segs):
            if kind == "lit":
                if val != observed:
                    ok = False
                    break
            else:  # slot — non-empty and matches the slot regex if known
                rx = slot_re.get(val)
                if rx and not re.fullmatch(rx, observed):
                    ok = False
                    break
                slot_values[val] = observed
        if ok:
            matches.append((url, slot_values, parse_qs(urlsplit(url).query)))

    if not matches:
        # Host present but nothing matched the shape => the family STRUCTURE
        # changed (segment count / literals / slot-shape). Host absent => MISSING.
        return {
            "status": DRIFTED if host_seen else MISSING,
            "verdict": STRUCTURAL_DRIFT if host_seen else MISSING,
            "matched_url": None, "matched_urls": [], "_matched_query": {},
            "slot_values": {}, "observed_values": {}, "recorded_present": {},
            "host_seen": host_seen,
        }

    # Per-slot observed value SETS across ALL matches (the "don't grab the
    # first" fix). For a single title this is one identity value and the full
    # rendition menu actually served.
    observed_values: Dict[str, List[str]] = {}
    for _u, sv, _q in matches:
        for k, v in sv.items():
            observed_values.setdefault(k, [])
            if v not in observed_values[k]:
                observed_values[k].append(v)

    # Compare observed-vs-recorded per slot, bucketed by role.
    identity_changed = False
    rendition_missing = False
    recorded_present: Dict[str, Optional[bool]] = {}
    for name, meta in slots_meta.items():
        role = meta.get("role", IDENTITY_ROLE)
        recorded = meta.get("recorded")
        obs = observed_values.get(name, [])
        if recorded is None:
            recorded_present[name] = None      # pre-.69 template: no comparison
            continue
        present = recorded in obs
        recorded_present[name] = present
        if role == RENDITION_ROLE and not present:
            rendition_missing = True
        elif role == IDENTITY_ROLE and not present and obs:
            identity_changed = True

    # Representative match for echo + signing check: prefer one whose rendition
    # equals the recorded rendition (so "HELD" echoes the real recorded asset);
    # else the first match.
    rep_url, rep_sv, rep_q = next(
        (m for m in matches if recorded_rendition_ok(m[1], slots_meta)), matches[0])

    if not identity_changed and not rendition_missing:
        verdict = HELD
    elif not identity_changed and rendition_missing:
        verdict = RENDITION_DRIFT
    elif identity_changed and not rendition_missing:
        verdict = IDENTITY_CHANGE
    else:
        verdict = IDENTITY_AND_RENDITION_CHANGE

    return {
        "status": HELD,                       # coarse: a shape-matching request exists
        "verdict": verdict,                   # fine: the five-way taxonomy
        "matched_url": _safe_url(rep_url),
        "matched_urls": sorted({_safe_url(u) for u, _s, _q in matches}),
        "_matched_query": rep_q,
        "slot_values": rep_sv,
        "observed_values": observed_values,
        "recorded_present": recorded_present,
        "host_seen": True,
    }


# ── the diff ───────────────────────────────────────────────────────
def migrate_template(template: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade a v1 (pre-.69) template to v2 in place-ish (returns a new dict).

    v1 templates have slots with {name, regex, shape} but no ``role`` or
    ``recorded`` value, so the .69 matcher cannot do its observed-vs-recorded
    comparison and falls back to shape-only matching. This re-derives both from
    the template's OWN recorded goal URL — no recapture needed:
      * the recorded per-slot value comes from the goal's ``url_template`` /
        ``path_template`` (the path segments the template was built from);
      * the role (identity | rendition) comes from the same shape classifier
        the workbench now uses.
    Recognition-only: only PATH segments are read; the query (signing) is never
    parsed. Idempotent — a v2 template is returned unchanged.
    """
    if template.get("template_version", 1) >= 2:
        return template
    # Import the role classifier from the pure derivation core (leaf module,
    # so there is no cycle). Previously routed via capture_workbench, which
    # re-exports these from extraction_core anyway.
    from .extraction_core import segment_role as _segment_role, IDENTITY, RENDITION  # noqa

    out = dict(template)
    goal = dict(out.get("goal", {}) or {})
    # Recover the CONCRETE recorded goal path. The recorded per-slot values come
    # from request_key (the actual recorded request, "METHOD host/path"), NOT
    # from url_template: url_template stores the *templated* path
    # (".../{content_id}/{rendition}"), whose segments are placeholder tokens
    # ("{content_id}"), not recorded values. Reading url_template here made a
    # migrated v1 template store placeholders as recorded values and mis-role
    # every slot to identity, so it reported identity_change and MASKED rendition
    # drift (exactly the failure the .69 migration test guards). Strip the method
    # prefix and the query (signing); only the PATH is read (recognition-only).
    req_key = goal.get("request_key") or ""
    concrete = req_key.split(" ", 1)[-1] if req_key else ""
    if concrete and "://" not in concrete:
        concrete = "https://" + concrete   # give urlsplit a scheme so the host splits off
    if not concrete:
        concrete = goal.get("url_template") or ""   # last resort (templated; imperfect)
    recorded_path = _safe_url(concrete.partition("?")[0]) if concrete else ""
    _, rec_segs = _segments(recorded_path) if recorded_path else ("", [])
    # path_template tells us which segments are slots and their names/order.
    _, t_segs = _parse_template_path(goal.get("path_template", ""))
    # Map slot-position -> recorded segment value by walking both in order.
    slot_recorded: Dict[str, str] = {}
    for (kind, name), seg in zip(t_segs, rec_segs):
        if kind == "slot":
            slot_recorded[name] = seg

    new_slots = []
    for s in goal.get("slots", []):
        s2 = dict(s)
        recorded = slot_recorded.get(s2["name"])
        if recorded is not None and "recorded" not in s2:
            s2["recorded"] = recorded
        if "role" not in s2:
            basis = recorded if recorded is not None else (s2.get("shape") or "")
            s2["role"] = _segment_role(basis) if basis else IDENTITY
        new_slots.append(s2)
    goal["slots"] = new_slots
    out["goal"] = goal
    out["template_version"] = 2
    out["_migrated_from"] = template.get("template_version", 1)
    return out


# ── the diff (uses _match_goal + migrate-on-read) ──────────────────
def diff_template(template: Dict[str, Any],
                  capture: Dict[str, Any]) -> Dict[str, Any]:
    """Diff a fresh capture against a stored template; report HELD vs DRIFTED
    per prediction, an overall drift verdict, and the goal-selection check.

    Recognition-only: classification via classify_url, structural URL match,
    signing-marker presence. Nothing here reconstructs or computes a signed
    value.
    """
    import re
    # v3.66.69: auto-upgrade a v1 template so the role/recorded comparison runs
    # even on templates built before .69 (no recapture needed). Idempotent.
    template = migrate_template(template)
    goal = template.get("goal", {})
    checks: List[Dict[str, Any]] = []
    decayed: List[str] = []

    match = _match_goal(template, capture)

    # 1) goal shape match (the durable URL skeleton)
    _vd = match.get("verdict", match["status"])
    _shape_detail = {
        HELD: "a request matches the goal URL shape and the recorded "
              "identity + rendition are present",
        IDENTITY_CHANGE: "the goal shape holds; the content identity changed "
                         "(expected when testing a different title) — the "
                         "identity slot is validated, not broken",
        RENDITION_DRIFT: "the goal family still matches, but the recorded "
                         "rendition member is no longer served — same title is "
                         "exposing a different/missing rendition",
        IDENTITY_AND_RENDITION_CHANGE: "the goal shape holds; the title changed "
                         "(expected) AND the recorded rendition member is "
                         "absent — rendition mismatch is called out",
        STRUCTURAL_DRIFT: "the goal host appears but no request matches the "
                          "templated shape — the site's URL structure changed",
        MISSING: "the goal host was not seen in this capture at all",
    }.get(_vd, "")
    checks.append({
        "prediction": "goal_url_shape",
        "expected": goal.get("path_template"),
        "observed": match["matched_url"],
        "status": match["status"],
        "verdict": _vd,
        "detail": _shape_detail,
    })
    if match["status"] != HELD:
        decayed.append("goal_url_shape")

    matched_url = match["matched_url"]  # already query-stripped (safe to echo)

    # 2) classification (re-run the REAL classifier on the matched request).
    #    The query-stripped URL is sufficient: direct_file/embed classification
    #    keys off host + path/extension, not the signing query.
    if matched_url:
        try:
            from .deep_detect import classify_url
            observed_type = None
            res = classify_url(matched_url)
            # classify_url returns a tuple/obj depending on version; be defensive
            if isinstance(res, tuple) and res:
                observed_type = res[0]
            elif isinstance(res, dict):
                observed_type = res.get("type")
            else:
                observed_type = getattr(res, "type", None) or str(res)
        except Exception as exc:  # pragma: no cover - defensive
            observed_type = f"<classify error: {exc}>"
        exp = goal.get("classification")
        status = HELD if observed_type == exp else DRIFTED
        checks.append({
            "prediction": "goal_classification",
            "expected": exp, "observed": observed_type, "status": status,
            "detail": ("classifies the same as when the template was built"
                       if status == HELD else
                       "the goal now classifies differently — the detector "
                       "bucket changed"),
        })
        if status != HELD:
            decayed.append("goal_classification")

    # 3) per-slot check — role-aware (v3.66.69).
    #    The pre-.69 check only asked "does the matched segment match the
    #    regex". That is the bug the validation exposed: a permissive rendition
    #    regex matches ANY rendition, so a member change passed as HELD. Now we
    #    compare observed-vs-RECORDED per role:
    #      * rendition slot: HELD iff the recorded member is present in the set
    #        actually served; DRIFTED if the recorded member is absent (this is
    #        what surfaces same-title rendition drift). Counts toward drift.
    #      * identity slot: HELD if the regex still matches; if the value
    #        changed we flag `identity_changed` for transparency but do NOT
    #        count it as drift (a different title is expected/informational).
    #      * pre-.69 template (recorded is None): fall back to regex-only, the
    #        old behaviour.
    obs_sets = match.get("observed_values", {})
    rec_present = match.get("recorded_present", {})
    for s in goal.get("slots", []):
        name = s["name"]
        role = s.get("role", IDENTITY_ROLE)
        recorded = s.get("recorded")
        observed_val = match["slot_values"].get(name)
        observed_set = obs_sets.get(name, [])
        identity_changed = False
        if matched_url is None:
            status = MISSING
        elif recorded is None:
            # pre-.69 slot: regex-only (matches old behaviour exactly)
            status = HELD if (observed_val is not None
                              and re.fullmatch(s["regex"], observed_val)) \
                else DRIFTED
        elif role == RENDITION_ROLE:
            status = HELD if rec_present.get(name) else DRIFTED
        else:  # identity slot
            regex_ok = (observed_val is not None
                        and re.fullmatch(s["regex"], observed_val))
            status = HELD if regex_ok else DRIFTED
            identity_changed = regex_ok and rec_present.get(name) is False
        detail = (
            "the recorded rendition member is still served"
            if role == RENDITION_ROLE and status == HELD else
            "the recorded rendition member is no longer served — same family, "
            "different/missing rendition" if role == RENDITION_ROLE else
            "the content identity changed (expected for a different title) — "
            "the identity slot pattern still holds" if identity_changed else
            "the addressable segment still matches the pattern shape"
            if status == HELD else
            "the addressable segment no longer matches — the id scheme changed"
            if status == DRIFTED else
            "no goal request matched, so the slot was not observed")
        check = {
            "prediction": f"slot:{name}",
            "role": role,
            "expected_regex": s["regex"],
            "recorded": recorded,
            "observed_value": observed_val,
            "observed_set": observed_set,
            "status": status,
            "detail": detail,
        }
        if identity_changed:
            check["identity_changed"] = True   # informational, not drift
        checks.append(check)
        # An identity change is expected/informational and must NOT decay the
        # verdict; only a genuine non-HELD status (rendition miss, regex fail,
        # missing) counts.
        if status != HELD:
            decayed.append(f"slot:{name}")

    # 4) signing expectation — markers present / absent / newly-appeared.
    #    We only check PRESENCE of the expected signing param names plus flag
    #    new signing-looking params. We read the query KEYS only — never the
    #    values — and the matched_url we echo has the query stripped entirely.
    if matched_url is not None:
        q = match.get("_matched_query", {})
        present = sorted(p for p in goal.get("signing_expected", []) if p in q)
        absent = sorted(p for p in goal.get("signing_expected", [])
                        if p not in q)
        _SIGNING_HINT = ("expires", "token", "signature", "sig", "key", "hmac",
                         "policy", "jwt", "exp")
        new_signing = sorted(
            k for k in q
            if k not in goal.get("signing_expected", [])
            and any(h in k.lower() for h in _SIGNING_HINT))
        status = HELD if not absent and not new_signing else DRIFTED
        checks.append({
            "prediction": "signing",
            "expected_present": goal.get("signing_expected", []),
            "observed_present": present,
            "observed_absent": absent,
            "observed_new": new_signing,
            "status": status,
            "detail": ("the same signing markers are present (values never "
                       "read)" if status == HELD else
                       "the signing markers changed — some expected ones are "
                       "absent or new ones appeared; the live session still "
                       "supplies the values"),
        })
        if status != HELD:
            decayed.append("signing")

    # 5) goal-selection accumulation check
    conf = (template.get("confirmed", {}) or {}).get("goal_selection", {})
    gs = {
        "human_confirmed": conf.get("status") == "confirmed",
        "shape_still_present": match["status"] == HELD,
        "note": ("the stored goal pick still matches a request in this capture"
                 if match["status"] == HELD else
                 "the stored goal shape was not found — re-verify the goal pick"),
    }
    if conf.get("status") != "confirmed":
        gs["note"] += (" | goal selection is not yet human-confirmed; confirming "
                       "it once amortizes the check across future diffs")

    verdict = "clean" if not decayed else "drifted"
    # v3.66.69 — surface the fine goal-match verdict at top level. The aggregate
    # drift_verdict stays "clean" for IDENTITY_CHANGE because an identity change
    # is deliberately NOT added to `decayed` (expected when testing a 2nd
    # title); RENDITION_DRIFT / IDENTITY_AND_RENDITION_CHANGE add slot:<rendition>
    # to decayed, and STRUCTURAL_DRIFT / MISSING add goal_url_shape, so all of
    # those read "drifted".
    goal_verdict = match.get("verdict", match["status"])
    _GV_NOTE = {
        HELD: "same title, same rendition, same structure — the template "
              "fully recognizes this capture.",
        IDENTITY_CHANGE: "different title (content identity), structure holds. "
              "Informational/expected when validating another title — the "
              "identity slot is validated, not broken.",
        RENDITION_DRIFT: "same title/family, but the recorded rendition member "
              "is no longer served. Surfaced as drift: the same title is not "
              "exposing the same recorded asset.",
        IDENTITY_AND_RENDITION_CHANGE: "title changed (informational) AND the "
              "recorded rendition member is absent (called out as drift on the "
              "rendition axis).",
        STRUCTURAL_DRIFT: "host present but the templated URL structure no "
              "longer matches — segment count / literals / slot shape changed.",
        MISSING: "the goal host was not seen in this capture.",
    }
    # the internal query (names+values) was used only for the signing-name check;
    # strip it so it never reaches the emitted result.
    safe_match = {k: v for k, v in match.items() if k != "_matched_query"}
    return {
        "host": template.get("host"),
        "template_version": template.get("template_version"),
        "verdict": goal_verdict,
        "verdict_note": _GV_NOTE.get(goal_verdict, ""),
        "goal_match": safe_match,
        "checks": checks,
        "goal_selection_check": gs,
        "drift_verdict": verdict,
        "decayed": decayed,
        "note": ("recognition-only drift check: each prediction is HELD or "
                 "DRIFTED against observed capture structure. Signing is "
                 "checked for marker presence only — values are never read or "
                 "reconstructed. A 'clean' verdict means the stored template "
                 "still recognizes this capture's goal; 'drifted' names which "
                 "predictions decayed."),
    }
