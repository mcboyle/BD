"""capture_workbench_impl.skeleton -- verbatim functions from capture_workbench.py."""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

from ._common import (
    CLIENT_COMPUTED,
    IDENTITY,
    PROVENANCE,
    ROTATING_OPAQUE,
    SIGNING,
    _CANDIDATE_FLOOR,
    _HEXISH,
    _ID_SHAPES,
    _PATH_SIGN_TYPE,
    _RENDITION_SIGNAL,
    _SIGNING_SHAPES,
    _SIGN_MARKER,
    _STORAGE_HINT,
    _STRUCTURAL_WORD,
    _segment_regex,
    _segment_role,
    classify_value,
)


def _value_for_pattern(value: Any) -> Optional[str]:
    """A non-redacted, non-empty string usable to derive an extraction
    pattern; else None."""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("<scrubbed>") or value == "<redacted>":
        return None
    return value


def _verdict_for_param(p: Dict[str, Any]) -> str:
    """Map a synth param dict to a stability verdict.

    The synth param already carries the three signals we need: whether it is
    a credential, its shape ``type``, and its provenance ``source``. We layer
    a stability *reason* on top.
    """
    source = p.get("source") or ""
    shape = p.get("type") or "opaque"
    if p.get("credential") or source == "redacted_credential" \
            or shape in _SIGNING_SHAPES:
        return SIGNING
    # A value whose origin is an earlier captured response/header/page-context
    # source: the value rotates but the EDGE is structural and reusable. This
    # is the stable case for a drift-heavy site — the value changes, the
    # dependency doesn't.
    if source and source != "source_unknown":
        return PROVENANCE
    # No traceable source. Synth already searched the page, prior headers, and
    # retained bodies and found nothing, so we BELIEVE that: a value with no
    # observable origin is most likely computed client-side (opaque shape) or
    # a session-local rotating value. We do NOT promote it to a "stable id"
    # just because it happens to be id-shaped — that would contradict synth's
    # own source-unknown finding and produce an extraction pattern for a value
    # that isn't actually addressable. Such values are surfaced as
    # unrecoverable-by-observation, never as a draft pattern.
    return CLIENT_COMPUTED if shape == "opaque" else ROTATING_OPAQUE


def _candidate_signals(seg: str, goal_filename: str):
    """Score a path segment's IDENTITY confidence from signed signals; return
    (score, positive_signals, negative_signals).

    v3.66.82 — the heart of confidence-weighted candidate generation. Positive
    signals are evidence the segment is a per-title content key; negative signals
    are evidence it is signing, routing, or storage scaffolding. The pre-.82 gate
    (`_segment_is_addressable`) was the binary OR of the first three positives;
    one threshold could not be both strict enough to reject signing/routing tokens
    (t over-generation) and loose enough to admit a readable-slug identity (nubile
    loss). Scoring separates the two failure directions: corroboration raises a
    suppressed identity, anti-identity signals demote admitted noise.
    """
    pos: List[str] = []
    neg: List[str] = []
    score = 0
    shape = classify_value(seg)
    if shape in _ID_SHAPES:                       # uuid/sha/md5/id/filename
        pos.append("id_shape"); score += 3
    elif _HEXISH.match(seg) and any(c.isdigit() for c in seg):
        pos.append("hex_with_digit"); score += 2  # opaque hex id / bros shard
    elif any(c.isdigit() for c in seg) and not _STRUCTURAL_WORD.match(seg):
        pos.append("has_digit"); score += 1       # generic addressable
    # corroboration: a readable title slug is echoed in the goal asset filename;
    # structural scaffolding (hls, fame, videos) is not. The single-capture signal
    # that distinguishes nubile's lost identity from a literal (0/9 false positives
    # across the 5-site audit).
    if (len(seg) >= 6 and goal_filename and seg != goal_filename
            and seg.lower() in goal_filename.lower()):
        pos.append("filename_echo"); score += 3
    # anti-identity: a key=value path form is signing/routing material, never a
    # content id (t: key=/s=/end=/ip=, state=, reftag=, download2=).
    if "=" in seg:
        neg.append("signing_or_kv"); score -= 5
    # anti-identity: a bare 1-2 digit segment is a routing index, not an id.
    if re.fullmatch(r"\d{1,2}", seg):
        neg.append("tiny_numeric"); score -= 3
    if _STORAGE_HINT.match(seg):
        neg.append("storage_hint"); score -= 2
    return score, pos, neg


def _admit_candidate(seg: str, goal_filename: str):
    """Confidence-weighted admission, replacing the binary `_segment_is_addressable`
    gate. Return (admit, score, positive_signals, negative_signals). A segment is a
    candidate if it carries a rendition signal (always a rendition member) or its
    identity score clears the floor. Below the floor it stays a literal —
    scaffolding (no positive signal) and demoted signing/routing fall here."""
    score, pos, neg = _candidate_signals(seg, goal_filename)
    stem = seg.rsplit(".", 1)[0] if "." in seg else seg
    is_rendition = bool(_RENDITION_SIGNAL.search(stem)
                        or _RENDITION_SIGNAL.search(seg))
    return (is_rendition or score >= _CANDIDATE_FLOOR), score, pos, neg


def _sharded_run_map(segs: List[str], goal_filename: str = "") -> Dict[int, str]:
    """Detect CDN path-sharding: a run of >=3 CONTIGUOUS short, opaque,
    identity-role segments — one logical identifier (typically a hash) split
    across many short directories (e.g. 3ca/96d/1e8/.../e1). Returns
    {segment_index: 'start'|'inside'} for indices inside such a run; empty for
    normal paths.

    v3.66.76 (bros correction): the pre-correction skeleton emitted one identity
    slot PER segment, so a sharded path read as many distinct identities (bros:
    11). Collapsing the run into ONE sharded-identity slot recovers the single
    logical id. The rule is general, not site-specific: it keys on the shape of
    sharding (a contiguous run of short opaque id segments), so it fires on any
    CDN that shards a hash and NOT on a single-segment id (ultrafilms' 8-char
    content_id, filthy's 36-char uuid) — those are length>4, never short shards.
    """
    is_shard: List[bool] = []
    for seg in segs:
        admitted = _admit_candidate(seg, goal_filename)[0] if seg else False
        ok = bool(
            seg
            and not (seg.startswith("{") and seg.endswith("}"))
            and len(seg) <= 4
            and admitted                          # v3.66.82: confidence-admitted,
            and _segment_role(seg) == IDENTITY    # not the old binary gate — so a
            and classify_value(seg) in ("opaque", "id"))  # demoted routing index
        is_shard.append(ok)                       # (t: 12, 1, ssd1) is not grouped
    out: Dict[int, str] = {}
    i, n = 0, len(segs)
    while i < n:
        if is_shard[i]:
            j = i
            while j < n and is_shard[j]:
                j += 1
            if j - i >= 3:                       # >=3 contiguous = sharding
                out[i] = "start"
                for k in range(i + 1, j):
                    out[k] = "inside"
            i = j
        else:
            i += 1
    return out


def _kv_pairs(seg: str) -> List[tuple]:
    """Split a key=value path segment into (key, has_value) pairs. The segment
    may pack several pairs comma- or ampersand-separated (t: key=…,s=,end=…)."""
    out: List[tuple] = []
    for part in re.split(r"[,&]", seg):
        if "=" in part:
            k, _, v = part.partition("=")
            if k:
                out.append((k, bool(v)))
    return out


def _mask_path_signing(seg: str):
    """For a path segment, return (masked_segment, signing_record_or_None).

    - No '=' → a plain literal, returned unchanged with no record.
    - '=' present → mask every non-empty value so nothing leaks into the
      skeleton. If the segment matches the shared signing vocabulary, also emit a
      signing record naming each key by inferred type. A key=value segment with
      no recognized marker is masked as opaque key-value but NOT asserted to be
      signing (conservative — avoids false signing claims).
    """
    if "=" not in seg:
        return seg, None
    pairs = _kv_pairs(seg)
    # mask: keep key names, replace any value with a fixed placeholder
    masked = ",".join(f"{k}=<masked>" if has_v else f"{k}=" for k, has_v in pairs)
    if not masked:
        masked = seg  # not actually key=value shaped; leave as-is
    is_signing = bool(_SIGN_MARKER.search(seg))
    if not is_signing:
        return masked, None
    markers = [{"name": k, "type": _PATH_SIGN_TYPE.get(k.lower(), "opaque")}
               for k, _ in pairs]
    return masked, {"masked": masked, "markers": markers, "location": "path"}


def goal_skeleton(synth: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Derive a reviewable URL skeleton from the goal request's template.

    Pure function of synth output: it parses the goal request's
    ``url_template`` (signing/varying query params are already ``{slot}``
    there) and splits the PATH into structural literals vs addressable
    content-identifier segments, emitting a candidate extraction pattern for
    each addressable segment. This is what turns a same-title capture pair —
    where the content id is invariant and so never appears as a varying slot —
    into an actionable detector target.

    Signing material is never patterned here: it lives in the query and is
    already opaque in the template. Returns None if there is no goal request.
    """
    goal = next((r for r in synth.get("requests", []) if r.get("goal")), None)
    if goal is None:
        return None
    tmpl = goal.get("url_template") or ""
    base = tmpl.partition("?")[0]
    sp = urlsplit(base)
    segs = list(sp.path.split("/"))

    skeleton_slots: List[Dict[str, Any]] = []
    literal_segments: List[str] = []
    path_signing: List[Dict[str, Any]] = []  # v3.66.85 (VC-0026)
    templated_path_parts: List[str] = []
    seen_names: Dict[str, int] = {}
    # the goal asset filename (last non-templated path segment) — corroboration
    # input for filename_echo
    goal_filename = next((s for s in reversed(segs)
                          if s and not (s.startswith("{") and s.endswith("}"))), "")
    run_map = _sharded_run_map(segs, goal_filename)  # v3.66.76 grouping pass
    collapsed: Optional[Dict[str, Any]] = None  # the in-progress sharded slot
    for i, seg in enumerate(segs):
        if not seg:
            templated_path_parts.append(seg)
            continue
        if seg.startswith("{") and seg.endswith("}"):
            templated_path_parts.append(seg)  # already a synth slot
            continue
        if run_map.get(i) == "inside":
            # absorb into the sharded-identity slot started earlier; emit no
            # separate slot and no extra {slot} in the template
            collapsed["sample"] += "/" + seg
            collapsed["_regex_parts"].append(_segment_regex(seg))
            collapsed["sharded_segment_count"] += 1
            continue
        admit, score, pos_signals, neg_signals = _admit_candidate(seg, goal_filename)
        if admit:
            shape = classify_value(seg)
            role = _segment_role(seg)
            # Name by role, not by shape: an identity key is the per-title
            # content id (the detector target); a rendition key names a
            # resolution/quality member of a menu that is constant per title.
            base = "content_id" if role == IDENTITY else "rendition"
            name = base
            if name in seen_names:
                seen_names[name] += 1
                name = f"{name}{seen_names[name]}"
            else:
                seen_names[name] = 1
            templated_path_parts.append("{" + name + "}")
            if run_map.get(i) == "start":
                rationale = (
                    "a run of contiguous short opaque path segments — CDN "
                    "path-sharding of ONE logical identifier (a hash split "
                    "across short directories). Collapsed into a single "
                    "sharded-IDENTITY slot; capture a second title to confirm "
                    "the whole run co-varies with title. Candidate pattern "
                    "derived from shape.")
            elif role == IDENTITY:
                rationale = (
                    "path segment is an addressable, opaque content "
                    "identifier; constant across same-title captures, so it is "
                    "most likely the per-title IDENTITY key — capture a second "
                    "title to confirm it co-varies with title. Candidate "
                    "pattern derived from shape.")
            else:
                rationale = (
                    "path segment carries resolution/quality/fps structure — a "
                    "RENDITION descriptor (a member of a quality menu), NOT the "
                    "title key. It is constant per title and varies by download "
                    "choice; a second title does NOT promote it. The recorded "
                    "value is frozen so a drift check can tell 'same rendition' "
                    "from 'same family, different rendition'.")
            slot = {
                "segment_index": i,
                "name": name,
                "sample": seg,
                "shape": "sharded_id" if run_map.get(i) == "start" else shape,
                "role": role,                 # v3.66.69: identity | rendition
                "regex": _segment_regex(seg),
                "confidence": "medium",
                "inferred": True,
                # v3.66.82 — candidate-generation visibility: the same score/signal
                # transparency the downstream confidence/sensitivity layers provide
                "score": score,
                "positive_signals": pos_signals,
                "negative_signals": neg_signals,
                "rationale": rationale,
            }
            if run_map.get(i) == "start":
                slot["sharded"] = True
                slot["sharded_segment_count"] = 1
                slot["_regex_parts"] = [_segment_regex(seg)]
                collapsed = slot
            skeleton_slots.append(slot)
        else:
            masked, signing = _mask_path_signing(seg)
            templated_path_parts.append(masked)
            literal_segments.append(masked)
            if signing:
                path_signing.append(signing)

    # finalize sharded-identity slots: regex matches the whole collapsed run
    for s in skeleton_slots:
        s.pop("_regex_parts", None)
        if s.get("sharded"):
            s["regex"] = "/".join(_segment_regex(p)
                                  for p in s["sample"].split("/"))

    signing_params = [
        {"param": p.get("key"), "type": p.get("type")}
        for p in goal.get("params", [])
        if p.get("credential")]

    host = sp.netloc
    path_template = "/".join(templated_path_parts)
    # v3.66.85 (VC-0026) — rebuild url_template from the MASKED path parts and a
    # query-stripped base so no path-embedded signing value survives in any
    # returned field (the raw goal template would otherwise carry the token/IP).
    masked_url_template = f"{sp.scheme}://{host}{path_template}" if sp.scheme \
        else f"{host}{path_template}"
    return {
        "request_key": goal.get("key"),
        "host": host,
        "url_template": masked_url_template,
        "path_template": f"{host}{path_template}",
        "skeleton_slots": skeleton_slots,
        "literal_segments": literal_segments,
        "signing_params": signing_params,
        "path_signing": path_signing,
        "notes": [
            "Path-segment slots are constant across same-title captures; "
            "capture two DIFFERENT titles to confirm which segment is the "
            "per-title identifier (it will then also appear as a varying "
            "slot).",
            "Signing params are opaque and supplied by the live session — "
            "never extracted or computed.",
        ],
    }
