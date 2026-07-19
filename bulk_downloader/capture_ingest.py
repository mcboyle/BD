"""capture_ingest.py — offline capture ingestion and analysis orchestration (v3.66.89).

This module is the reusable core behind the ``tools/offline_capture_analyze.py`` CLI. It
takes capture artifacts that already exist on disk — a ``.json`` export, a ``.wacz``
archive, or a directory of them — normalizes each into a common internal capture model,
and then runs the framework's EXISTING analysis over them. It introduces no new detection
logic of its own: goal selection, candidate scoring, signing recognition, skeleton
synthesis, temporal drift, and perturbation checks all come from the modules that already
implement them (``capture_synth``, ``capture_workbench``, ``temporal_harness``,
``perturbation_harness``), and this module only loads, normalizes, dispatches, and collects.

Posture (enforced here, not merely promised):
  * recognition-only — nothing is fetched, replayed, reconstructed, or signed;
  * no signing value is ever surfaced — every URL placed in any result is query-stripped
    via ``capture_redact.redact_query``, path-embedded signing is masked by the existing
    ``goal_skeleton`` path-signing masker, and signing is reported by marker NAME and TYPE
    only;
  * value fingerprints are used only by the temporal harness for in-memory equality, and
    this module surfaces only the harness's boolean drift verdicts, never a fingerprint;
  * the corpus is never written — a suggested entry is produced as a reviewable artifact
    with no resolution pointer, and it cannot retire debt.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .capture_redact import redact_query, SENSITIVE_QS_KEY
from .netlog_classify import _SIGN_MARKER
from .capture_workbench import goal_skeleton, IDENTITY, RENDITION
from .temporal_harness import _goal_url, drift_series

CAPTURE_INGEST_VERSION = "3.66.89"


# ── loading ─────────────────────────────────────────────────────────
# Zip-bomb ceiling for a capture JSON member read out of a .wacz archive. A real
# capture.json (redacted metadata + truncated DOM) is far below this; a member
# declaring more is refused unread. 256 MiB.
_MAX_CAPTURE_JSON_BYTES = 256 * 1024 * 1024


def load_capture(path: str, *, require_network_log: bool = True) -> Dict[str, Any]:
    """Load a capture dict from a ``.json`` file or a ``.wacz`` archive.

    Mirrors the loader the project's other tools use: for an archive, pick the
    largest ``.json`` resource that parses to a dict carrying a ``network_log``.
    Reads local files only — never fetches.

    ``require_network_log`` (default True — the behaviour every existing caller
    relies on): an archive with no network_log-bearing JSON raises. The
    replayability validator (B1) loads DOM-only captures, so it passes
    ``require_network_log=False``; that path falls back to the ``capture.json``
    convention and then the largest parseable dict, matching the loader those
    DOM-only tools used before convergence.
    """
    p = Path(path)
    if p.suffix.lower() == ".wacz" or (p.is_file() and zipfile.is_zipfile(p)):
        with zipfile.ZipFile(p) as zf:
            cands = [n for n in zf.namelist() if n.lower().endswith(".json")]
            ordered = sorted(cands, key=lambda n: -zf.getinfo(n).file_size)
            parsed: Dict[str, Any] = {}
            for n in ordered:
                # Zip-bomb guard: never decompress a member whose DECLARED
                # uncompressed size exceeds the cap (zipfile honours the declared
                # size, so a bomb must declare a large size to deliver one). A
                # real capture.json is far smaller than this ceiling.
                if zf.getinfo(n).file_size > _MAX_CAPTURE_JSON_BYTES:
                    continue
                try:
                    d = json.loads(zf.read(n))
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                parsed.setdefault(n, d)
                if "network_log" in d:
                    return d
            if require_network_log:
                raise ValueError(f"no recon capture JSON (with a network_log) inside {path}")
            # DOM-only fallback: the capture.json convention, then the largest dict.
            for n in ordered:
                if n in parsed and (n.endswith("archive/capture.json") or n.endswith("capture.json")):
                    return parsed[n]
            if parsed:
                return parsed[ordered[0]] if ordered[0] in parsed else next(iter(parsed.values()))
            raise ValueError(f"no parseable capture JSON inside {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def discover_captures(path: str) -> List[str]:
    """Expand a path into capture files. A directory yields its ``.json`` and
    ``.wacz`` children (sorted); a file yields itself."""
    p = Path(path)
    if p.is_dir():
        out = sorted(str(c) for c in p.iterdir()
                     if c.suffix.lower() in (".json", ".wacz"))
        if not out:
            raise ValueError(f"no .json or .wacz capture artifacts found in {path}")
        return out
    return [str(p)]


# ── normalization into the common internal capture model ────────────
def _signing_markers_in(url: str) -> List[Dict[str, str]]:
    """Detect signing markers in a URL by NAME/TYPE only — never returns a value.

    A query key is a signing marker if it matches the project's sensitive-key
    pattern; a path-embedded marker is detected by the shared ``_SIGN_MARKER``.
    The value is deliberately never read into the result.
    """
    markers: List[Dict[str, str]] = []
    seen = set()
    q = url.split("?", 1)[1] if "?" in url else ""
    for pair in q.split("&"):
        if "=" not in pair:
            continue
        key = pair.split("=", 1)[0]
        if key and SENSITIVE_QS_KEY.search(key) and key not in seen:
            seen.add(key)
            markers.append({"name": key, "location": "query"})
    # path-embedded signing: surface only that a marker is present, by name
    for m in _SIGN_MARKER.finditer(url.split("?", 1)[0]):
        name = m.group(0).strip("/?&,=")
        if name and name not in seen:
            seen.add(name)
            markers.append({"name": name, "location": "path"})
    return markers


def _request_redaction_state(entry: Dict[str, Any]) -> str:
    """Report whether the captured request shows signs of redaction/scrubbing.

    Heuristic and conservative: 'redacted' if a signing value appears blanked or
    the capture marks a skipped body; 'present' if a query is retained; 'none' if
    there is nothing signing-like to redact.
    """
    url = entry.get("url") or ""
    if "REDACTED" in url or "redacted" in url:
        return "redacted"
    if entry.get("response_body_skipped_reason"):
        return "body_skipped"
    return "present" if "?" in url else "none"


def normalize_capture(raw: Dict[str, Any], *, source_name: str = "") -> Dict[str, Any]:
    """Normalize a raw capture dict into the common internal model.

    The model is intentionally explicit about what is and is not present in the
    source, because captures vary: response status, headers, and initiator are
    recorded when the source carries them and marked absent otherwise, rather than
    being assumed. Every URL stored here is query-stripped; signing is recorded by
    marker name/type only.
    """
    nl = raw.get("network_log") or raw.get("requests") or []
    requests: List[Dict[str, Any]] = []
    any_resp = any(("response_status" in e and e.get("response_status")) for e in nl)
    any_hdr = any((e.get("request_headers") or e.get("response_headers")) for e in nl)
    any_init = any(("initiator" in e or "type" in e) for e in nl)
    for e in nl:
        raw_url = e.get("url") or ""
        masked_url = redact_query(raw_url)            # never store a raw query
        rec = {
            "seq": e.get("seq"),
            "method": e.get("method"),
            "url": masked_url,                        # query-stripped/masked
            "type": e.get("type"),                    # resource/initiator hint
            "timestamp": e.get("timestamp") or e.get("time") or e.get("ts"),
            "iso": e.get("iso"),
            "response_status": e.get("response_status") if any_resp else None,
            "has_request_headers": bool(e.get("request_headers")),
            "has_response_headers": bool(e.get("response_headers")),
            "initiator": e.get("initiator"),          # explicit None if absent
            "redaction_state": _request_redaction_state(e),
            "signing_markers": _signing_markers_in(raw_url),  # names/types only
        }
        requests.append(rec)
    goal = _goal_url(raw)
    return {
        "source_name": source_name,
        "host": raw.get("host") or "",
        "title": raw.get("title") or "",
        "captured_at": raw.get("captured_at") or raw.get("session_start"),
        "n_requests": len(requests),
        "goal_url": redact_query(goal) if goal else None,
        "capabilities": {                              # what this source actually carries
            "has_responses": any_resp,
            "has_headers": any_hdr,
            "has_initiator": any_init,
        },
        "requests": requests,
        # the raw dict is retained ONLY for handing to existing analyzers, which
        # already mask their own output; it is never written to a report.
        "_raw": raw,
    }


# ── analysis orchestration (reuses existing framework logic) ─────────
def _single_capture_analysis(model: Dict[str, Any]) -> Dict[str, Any]:
    """Per-capture analysis via the existing ``goal_skeleton``: goal selection,
    candidate scoring, identity/rendition slots, and path-signing recognition.
    ``goal_skeleton`` already masks path-signing values in its output."""
    goal = model["goal_url"]
    if not goal:
        return {"error": "no media goal could be selected from this capture"}
    sk = goal_skeleton({"requests": [{"goal": True, "url_template": goal}]})
    slots = sk.get("skeleton_slots", [])
    return {
        "goal_url": goal,                              # already query-stripped
        "host": sk.get("host"),
        "path_template": sk.get("path_template"),
        "identity_slots": [s.get("sample") for s in slots if s.get("role") == IDENTITY],
        "rendition_slots": [s.get("sample") for s in slots if s.get("role") == RENDITION],
        "candidate_slots": [
            {"sample": s.get("sample"), "role": s.get("role"),
             "score": s.get("score"),
             "positive_signals": s.get("positive_signals"),
             "negative_signals": s.get("negative_signals")}
            for s in slots],
        "path_signing": sk.get("path_signing"),        # values already masked
        "query_signing_markers": model["requests"] and next(
            (r["signing_markers"] for r in model["requests"]
             if r["url"] == goal), []),
    }


def analyze_captures(paths: List[str],
                     *, series: bool = False,
                     labels: Optional[List[str]] = None) -> Dict[str, Any]:
    """Load, normalize, and analyze a set of capture paths with existing logic.

    Always produces an inventory and a per-capture analysis. Runs the temporal
    harness when given two or more captures that share an identity (or when
    ``series`` is set). Never fetches, never writes the corpus.
    """
    files: List[str] = []
    for p in paths:
        files.extend(discover_captures(p))
    labels = labels or [Path(f).name for f in files]
    models = [normalize_capture(load_capture(f), source_name=Path(f).name)
              for f in files]

    per_capture = [{"source": m["source_name"], "analysis": _single_capture_analysis(m)}
                   for m in models]

    # temporal: same-identity series of >=2 (or explicitly requested as a series)
    temporal = None
    identities = {tuple(pc["analysis"].get("identity_slots") or [])
                  for pc in per_capture if "analysis" in pc and "error" not in pc["analysis"]}
    same_identity = len(models) >= 2 and len(identities) == 1
    if len(models) >= 2 and (series or same_identity):
        temporal = drift_series([m["_raw"] for m in models], labels=labels)

    return {
        "ingest_version": CAPTURE_INGEST_VERSION,
        "n_captures": len(models),
        "files": files,
        "labels": labels,
        "models": models,
        "per_capture": per_capture,
        "temporal": temporal,
        "same_identity": same_identity,
    }


def analyze_perturbation(baseline_path: str, perturbed_path: str, axis: str
                         ) -> Dict[str, Any]:
    """Run the existing perturbation harness on a real baseline/perturbed pair.

    Uses ``evidence='real'``, which the harness treats as data-decides-the-verdict:
    it does NOT pre-force the debt/confidence/sensitivity flags the way a synthetic
    run does, so a real capture's outcome is determined by the observation.
    """
    from .perturbation_harness import perturbation_run
    base = load_capture(baseline_path)
    pert = load_capture(perturbed_path)
    return perturbation_run(base, pert, axis, evidence="real",
                            change_manifest={"baseline": Path(baseline_path).name,
                                             "perturbed": Path(perturbed_path).name,
                                             "axis": axis, "evidence": "real"})


# ── posture guard ───────────────────────────────────────────────────
# a signing leak is a key=value pair whose KEY (bounded by a path or query
# delimiter, or the start of the token) is sensitive and whose VALUE is a real,
# unmasked value. Bounding the key to a delimiter avoids matching a signing
# substring inside an ordinary path word (e.g. "sig" inside "signed-urls").
_SCAN_KV = re.compile(r"(?:^|[/?&])([A-Za-z0-9_.\-]+)=([^/?&\s]+)")


def posture_scan(text: str) -> List[str]:
    """Scan generated report text for any raw signing value that escaped masking.

    Returns a list of offending ``key=value`` fragments; an empty list means the
    text is clean. A masked marker such as ``expires=<masked>`` or
    ``token=REDACTED`` is clean; only a real, unmasked value on a sensitive key is
    flagged. This is the runtime counterpart to the build's posture gate.
    """
    offenders: List[str] = []
    for token in text.split():
        for m in _SCAN_KV.finditer(token):
            key, val = m.group(1), m.group(2)
            if (SENSITIVE_QS_KEY.search(key) and val
                    and not val.startswith("<") and val.lower() != "redacted"):
                offenders.append(f"{key}={val}"[:40])
    return offenders
