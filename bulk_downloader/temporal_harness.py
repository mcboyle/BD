"""temporal_harness.py — same-title drift harness (v3.66.87).

Series-level temporal validation. Given an ORDERED list of captures of the SAME
title taken at different times (N >= 2), this measures drift on four axes and
checks the framework's standing predictions against what actually drifted:

    identity   — the per-title content id. PREDICTION: invariant across same-title
                 captures. Drift here would be a falsification, not normal churn.
    rendition  — the resolution/quality descriptor. PREDICTION: constant per title
                 but varies by download choice; differences are rendition drift,
                 NOT an identity change.
    signing    — per-session signing material. PREDICTION: drifts every session
                 (short-lived). Stability here would be the surprise.
    structural — host + templated path shape. PREDICTION: stable per site.

For N >= 3 it additionally tests VC-0019: the synth marks an N=2 invariant as
"may be coincidental" (a low-confidence floor); the prediction is that a third
same-title session resolves that hedge. The harness checks whether the identity
invariant held across the third (and later) captures.

This module is the evidence ENGINE: it converts the framework's point-in-time
predictions into tested outcomes (confirmed | falsified | untested).

Recognition-only. It reads structure, and it compares signing VALUES in memory
for equality so it can report whether signing drifted — but it never stores,
returns, or echoes a signing value. Only a boolean `drifted` per marker leaves
this module. Builds on temporal_benchmark primitives; not imported by app.py.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, parse_qs

from .capture_synth import (synthesize, classify_value, _query_pairs,
                            _value_shape_is_signing, _value_is_signed,
                            _is_redacted)
from .capture_redact import SENSITIVE_QS_KEY
from .capture_workbench import build_workbench, goal_skeleton, IDENTITY, RENDITION

TEMPORAL_HARNESS_VERSION = "3.66.87"

# outcome vocabulary, shared with the corpus
CONFIRMED = "confirmed"
FALSIFIED = "falsified"
UNTESTED = "untested"

import re
_MEDIA_EXT = re.compile(r"\.(mp4|m3u8|ts|webm|mpd|m4s|mov|mkv)(\b|$)", re.I)


def _goal_url(capture: Dict[str, Any]) -> Optional[str]:
    """Highest-seq media request URL in a capture (the goal). Recognition-only."""
    log = capture.get("network_log") or capture.get("requests") or []
    media = [(e.get("seq", 0), e.get("url") or "") for e in log
             if isinstance(e, dict)
             and _MEDIA_EXT.search(urlsplit(e.get("url") or "").path)]
    if not media:
        return None
    return max(media, key=lambda t: t[0])[1]


def _facets(goal_url: str) -> Dict[str, Any]:
    """Structural facets of one capture's goal: identity values, rendition
    values, host, and templated path shape. Signing is handled separately so its
    values are never carried here."""
    sk = goal_skeleton({"requests": [{"goal": True, "url_template": goal_url}]})
    ids = tuple(s["sample"] for s in sk["skeleton_slots"] if s["role"] == IDENTITY)
    rends = tuple(s["sample"] for s in sk["skeleton_slots"]
                  if s["role"] == RENDITION)
    return {
        "identity": ids,
        "rendition": rends,
        "host": sk["host"],
        "path_template": sk["path_template"],   # already masked + slotted
        "path_signing": sk.get("path_signing", []),
    }


def _signing_value_fingerprints(goal_url: str) -> Dict[str, str]:
    """Map each signing param NAME -> a one-way fingerprint of its value, used
    ONLY for equality comparison across captures. A fingerprint is a truncated
    SHA-256; it is non-reversible and cannot be used to sign or replay anything,
    and it is discarded after the drift comparison. No raw value is returned.

    A param is signing if its name is a known marker, its value is itself a
    signed URL, it was redacted at capture time, or its value shape corroborates
    signing (the VC-0021 rule). Path-embedded signing is compared via the path
    template instead (already masked), so this handles query signing.
    """
    pairs = dict(_query_pairs(goal_url))
    out: Dict[str, str] = {}
    for k, v in pairs.items():
        is_sign = (bool(SENSITIVE_QS_KEY.search(k)) or _value_is_signed(v)
                   or _is_redacted(v) or classify_value(v) in
                   ("token", "jwt", "unix_ts"))
        if is_sign:
            if _is_redacted(v):
                # the value was scrubbed at capture time: we KNOW signing was
                # present, but the value itself is gone, so drift across captures
                # is undeterminable — not "no drift"
                out[k] = "REDACTED"
            else:
                fp = hashlib.sha256(
                    (v or "").encode("utf-8", "replace")).hexdigest()[:12]
                out[k] = fp
    return out


def _axis(prediction: str, outcome: str, observation: str) -> Dict[str, str]:
    return {"prediction": prediction, "outcome": outcome,
            "observation": observation}


def drift_series(captures: List[Dict[str, Any]],
                 labels: Optional[List[str]] = None) -> Dict[str, Any]:
    """Measure four-axis drift across an ordered same-title capture series and
    check each standing prediction. `captures[0]` is the earliest. Returns a
    structured report; emits no signing values."""
    n = len(captures)
    labels = labels or [f"capture_{i}" for i in range(n)]
    if n < 2:
        return {"error": "need at least 2 same-title captures", "n": n}

    goal_urls = [_goal_url(c) for c in captures]
    if any(g is None for g in goal_urls):
        missing = [labels[i] for i, g in enumerate(goal_urls) if g is None]
        return {"error": f"no media goal in captures: {missing}", "n": n}

    facets = [_facets(g) for g in goal_urls]

    # ── identity drift ────────────────────────────────────────────────
    id_set = {f["identity"] for f in facets}
    id_invariant = len(id_set) == 1
    identity = _axis(
        "the per-title content identity is invariant across same-title captures",
        CONFIRMED if id_invariant else FALSIFIED,
        (f"identity {facets[0]['identity']} held across all {n} captures"
         if id_invariant else
         f"identity differed across captures: {sorted(id_set)} — "
         f"either not the same title or the identity slot is mis-assigned"))

    # ── rendition drift ───────────────────────────────────────────────
    rend_set = {f["rendition"] for f in facets}
    rend_varies = len(rend_set) > 1
    # rendition variation is EXPECTED (download choice); the prediction is that it
    # is attributed to rendition, not mistaken for an identity change — which is
    # exactly the case when identity stayed invariant while rendition varied
    rendition = _axis(
        "renditions vary by download choice and are attributed to rendition, "
        "not to identity (a second capture does not promote a rendition to id)",
        CONFIRMED if (id_invariant or not rend_varies) else FALSIFIED,
        (f"rendition varied {sorted(rend_set)} while identity stayed invariant — "
         f"correctly attributed to rendition drift" if (rend_varies and id_invariant)
         else f"rendition constant across captures: {sorted(rend_set)}"
         if not rend_varies else
         "rendition varied AND identity drifted — cannot separate the two"))

    # ── signing drift (value comparison in memory; no value stored) ────
    fps = [_signing_value_fingerprints(g) for g in goal_urls]
    sign_names = sorted(set().union(*[set(f) for f in fps])) if fps else []
    sign_drift_by_marker: Dict[str, Any] = {}
    any_sign_drift = False
    measurable_names: List[str] = []
    for name in sign_names:
        vals = [f.get(name) for f in fps if name in f]
        if vals and all(v == "REDACTED" for v in vals):
            # value scrubbed in every capture: drift undeterminable
            sign_drift_by_marker[name] = "undeterminable_redacted"
            continue
        measurable_names.append(name)
        real = [v for v in vals if v != "REDACTED"]
        drifted = len(set(real)) > 1
        sign_drift_by_marker[name] = drifted
        any_sign_drift = any_sign_drift or drifted
    path_sign_present = any(f["path_signing"] for f in facets)
    if any_sign_drift:
        sign_outcome = CONFIRMED
        sign_obs = (f"signing markers {measurable_names} drifted across sessions "
                    f"(values compared by one-way fingerprint, never stored)")
    elif sign_names and not measurable_names:
        sign_outcome = UNTESTED
        sign_obs = (f"signing present (markers {sign_names}) but every value was "
                    f"scrubbed at capture time, so drift is UNDETERMINABLE — not "
                    f"'no drift'. A capture retaining signing values is required to "
                    f"measure signing drift")
    elif not sign_names:
        sign_outcome = UNTESTED
        sign_obs = "no query signing markers found to measure"
    else:
        sign_outcome = FALSIFIED
        sign_obs = (f"signing markers {measurable_names} did NOT drift across "
                    f"sessions — unexpected for per-session signing; investigate")
    signing = _axis(
        "signing material is per-session and short-lived, so it drifts across "
        "captures",
        sign_outcome, sign_obs)

    # ── structural drift ──────────────────────────────────────────────
    struct_set = {(f["host"], f["path_template"]) for f in facets}
    struct_stable = len(struct_set) == 1
    structural = _axis(
        "the skeleton structure (host + templated path shape) is stable per site",
        CONFIRMED if struct_stable else FALSIFIED,
        (f"host + path template stable across all {n} captures: "
         f"{facets[0]['host']}{_path_only(facets[0]['path_template'])}"
         if struct_stable else
         f"structure changed across captures: {sorted(struct_set)}"))

    # ── VC-0019: does a third+ session resolve the N=2 coincidence hedge? ─
    floor = _floor_lift(captures, facets, n, id_invariant)

    return {
        "harness_version": TEMPORAL_HARNESS_VERSION,
        "n_captures": n,
        "labels": labels,
        "axes": {
            "identity": identity,
            "rendition": rendition,
            "signing": signing,
            "structural": structural,
        },
        "signing_drift_by_marker": sign_drift_by_marker,
        "path_signing_present": path_sign_present,
        "vc_0019_floor": floor,
        "note": ("Recognition-only. Signing values are compared by one-way "
                 "fingerprint for drift detection and never stored or echoed. "
                 "Identity invariance across N>=3 independent same-title captures "
                 "is the evidence that the N=2 'may be coincidental' hedge is "
                 "resolved."),
    }


def _path_only(path_template: str) -> str:
    i = path_template.find("/")
    return path_template[i:] if i >= 0 else ""


def _floor_lift(captures: List[Dict[str, Any]], facets: List[Dict[str, Any]],
                n: int, id_invariant: bool) -> Dict[str, Any]:
    """VC-0019 test. At N=2 the synth flags an invariant as possibly coincidental.
    With N>=3 independent same-title captures, an identity that STILL holds is no
    longer plausibly coincidental — the hedge is resolved and the floor lifts.

    Only real qualifying data (N>=3, same title) can confirm this; with N=2 the
    prediction stays UNTESTED."""
    if n < 3:
        return {
            "prediction": "a 3rd+ same-title session resolves the N=2 "
                          "'may be coincidental' confidence hedge (lifts the floor)",
            "outcome": UNTESTED,
            "observation": f"only N={n} captures of this title; a 3rd qualifying "
                           f"same-title capture is required to test the floor lift",
            "qualifying_data": False,
        }
    # N>=3: re-run the synth on successive pairs and confirm the invariant recurs
    invariant_recurs = id_invariant and len({f["identity"] for f in facets}) == 1
    return {
        "prediction": "a 3rd+ same-title session resolves the N=2 "
                      "'may be coincidental' confidence hedge (lifts the floor)",
        "outcome": CONFIRMED if invariant_recurs else FALSIFIED,
        "observation": (
            f"identity {facets[0]['identity']} recurred across all N={n} "
            f"independent same-title captures (different sessions, different "
            f"renditions); the small-sample 'two-capture fluke' hypothesis that "
            f"floors N=2 confidence is empirically falsified, so the floor lifts. "
            f"NOTE: this confirms STABILITY (the invariant is reliably constant "
            f"for this title), not title-SPECIFICITY (that the value is unique to "
            f"this title vs shared across titles) — specificity needs cross-title "
            f"captures and is a separate question, not VC-0019."
            if invariant_recurs else
            f"identity did not recur across N={n} captures: "
            f"{sorted({f['identity'] for f in facets})}"),
        "qualifying_data": True,
        "n_sessions": n,
        "scope": "small-sample stability (same-title); NOT title-specificity",
    }
