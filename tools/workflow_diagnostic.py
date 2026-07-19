#!/usr/bin/env python3
"""workflow_diagnostic — read-only workflow inference from a capture artifact.

Offline, stdlib-only (plus the project's `netlog_classify` + `bad_terms`). Consumes a
`capture.json` (or a `.wacz` containing `archive/capture.json`) and, optionally, a gold
template, and emits three layers (per WORKFLOW_EXTRACTION_AUDIT.md):

  1. Timeline      — dom + network + ws merged on the shared `_now_ms()` clock.
  2. Phases        — load -> auth -> browse -> select -> playback -> segment-stream.
  3. Template-diff — observed-vs-expected steps for a host (the Reptyle-readiness check).

Design rules from the audit, enforced here:
  * Every inferred step carries an explicit CONFIDENCE label + reason.
  * Observed evidence is reported INDEPENDENTLY of the template *before* diffing, so an
    incomplete capture cannot be flagged "complete" by confirmation bias.
  * Redaction blind spots are labeled as BLIND, never inferred as absent.
  * Analytics/CDN noise is excluded from phase/diff inference (kept, flagged, in the timeline).

This tool infers and reports; it asserts nothing causal and makes no acquisition/promotion
decision. It does not modify anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from bulk_downloader.netlog_classify import (  # noqa: E402
    classify_network_log, KIND_HLS_MANIFEST, KIND_DASH_MANIFEST, KIND_HLS_SEGMENT,
)
from bulk_downloader.bad_terms import contains_bad_term  # noqa: E402
from bulk_downloader import capture_ingest as _capture_ingest  # noqa: E402  (B1: canonical loader)

HIGH, MED, LOW, BLIND = "high", "medium", "low", "blind"

# fMP4 / DASH segment shapes the HLS-only classifier (`netlog_classify`) does NOT
# count as segments — e.g. Cloudflare Stream serves `.../video/240/seg_1.mp4`,
# `init.mp4`, and DASH `.m4s`. These are real played-stream segments; recognizing
# them here (readiness inference only) keeps the production classifier untouched.
import re as _re
_FMP4_SEG = _re.compile(r"(/seg_\d+\.(mp4|m4s))|(\.m4s)(\?|$)|(/(video|audio)/\d+/(init|seg_\d+)\.mp4)", _re.I)


def _fmp4_dash_segments(cap):
    """Segment-looking media URLs the HLS classifier misses (Cloudflare Stream /
    fMP4 / DASH: seg_N.mp4, init.mp4, .m4s). De-duplicated. The regex is specific
    enough that analytics (/g/collect) and base64 direct-download URLs do not
    match, so no extra noise filter is needed."""
    out = []
    for e in (cap.get("network_log") or []):
        if not isinstance(e, dict):
            continue
        url = e.get("url") or ""
        if url and _FMP4_SEG.search(url):
            out.append(url)
    return out

# rrweb incremental data.source ids we name; others fold into "other".
_SRC = {0: "mutation", 1: "mousemove", 2: "interaction", 3: "scroll",
        4: "viewport", 5: "input"}
_AUTHISH = ("login", "signin", "sign-in", "auth", "session", "token", "oauth", "/account")


# ── loading ───────────────────────────────────────────────────────
def load_capture(path: Path) -> Dict[str, Any]:
    """Load a capture dict from a ``.json`` file or ``.wacz`` archive.

    B1 (Phase-B convergence): delegates to the canonical loader
    ``bulk_downloader.capture_ingest.load_capture`` rather than walking the
    archive independently — one reader of the bytes, not two. The returned raw
    dict is unchanged (the capture-model golden pins this), so every downstream
    derivation here is byte-identical. capture_ingest selects the largest JSON
    resource carrying a ``network_log`` (a superset of the old
    ``archive/capture.json`` pick) and raises ``ValueError`` (not ``SystemExit``)
    on a missing capture — callers here already handle ``ValueError``.
    """
    return _capture_ingest.load_capture(str(path), require_network_log=False)


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc
    except Exception:
        return ""


# ── layer 1: timeline ─────────────────────────────────────────────
def build_timeline(cap: Dict[str, Any], report) -> List[Dict[str, Any]]:
    """Merge dom + network + ws into one typed, time-ordered event stream.
    Noise (analytics/CDN per bad_terms) is kept but flagged; phases/diff ignore it."""
    kind_by_url = {i.url: i.kind for i in report.items}
    ev: List[Dict[str, Any]] = []

    for e in cap.get("network_log") or []:
        if not isinstance(e, dict):
            continue
        url = e.get("url") or ""
        ev.append({
            "ts": e.get("timestamp"), "stream": "network",
            "kind": kind_by_url.get(url, (e.get("type") or "request")),
            "method": e.get("method"), "status": e.get("response_status"),
            "host": _host(url), "url": url, "noise": contains_bad_term(url),
        })

    for d in cap.get("dom_log") or []:
        if not isinstance(d, dict):
            continue
        t = d.get("type")
        if t == "incremental":
            label = _SRC.get(d.get("source"), "other")
        else:
            label = t  # full_snapshot | meta
        item = {"ts": d.get("timestamp"), "stream": "dom", "kind": label, "noise": False}
        if t == "meta":
            item["href"] = (d.get("data") or {}).get("href")
        ev.append(item)

    for c in cap.get("websocket_log") or []:
        if not isinstance(c, dict):
            continue
        ev.append({"ts": c.get("created_ms"), "stream": "ws", "kind": "ws_open",
                   "host": _host(c.get("url") or ""), "frames": c.get("frame_count"),
                   "noise": False})
        if c.get("closed_ms") is not None:
            ev.append({"ts": c.get("closed_ms"), "stream": "ws", "kind": "ws_close",
                       "noise": False})

    ev.sort(key=lambda x: (x.get("ts") is None, x.get("ts") or 0))
    return ev


# ── layer 2: phases ───────────────────────────────────────────────
def _step(present: bool, confidence: str, reason: str,
          evidence: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"observed": present, "confidence": confidence, "reason": reason,
            "evidence": evidence or []}


def infer_phases(cap: Dict[str, Any], report, timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    net = [e for e in timeline if e["stream"] == "network" and not e["noise"]]
    dom = [e for e in timeline if e["stream"] == "dom"]
    has_storage = bool((cap.get("storage_snapshot") or {}).get("local_storage")) or \
        bool((cap.get("storage_snapshot") or {}).get("session_storage"))

    phases: Dict[str, Any] = {}

    # load — first document/network request
    doc = next((e for e in net if (e["kind"] in ("document", "navigate"))), None) or \
        (net[0] if net else None)
    phases["load"] = _step(doc is not None, HIGH if doc else LOW,
                           "first document/network request" if doc else "no network observed",
                           [doc["url"]] if doc else [])

    # auth — a POST to an auth-ish path; semantics are BLIND (bodies off, inputs masked)
    auth = next((e for e in net if (e.get("method") == "POST"
                                    and any(k in (e["url"] or "").lower() for k in _AUTHISH))), None)
    if auth:
        phases["auth"] = _step(True, LOW,
                               "POST to an auth-ish URL; payload/inputs redacted, so semantics are "
                               "unverified (structure only)", [auth["url"]])
    else:
        phases["auth"] = _step(False, BLIND,
                               "no auth-ish POST seen; auth bodies/inputs are redacted, so absence "
                               "is not conclusive" + (" (storage present at stop)" if has_storage else ""))

    # select — a MouseInteraction (click). Target selector is NOT resolved here.
    click = next((e for e in dom if e["kind"] == "interaction"), None)
    phases["select"] = _step(click is not None, MED if click else BLIND,
                             "a click/interaction occurred (target selector not resolved)" if click
                             else "no interaction event; clicks may be unrecorded or redacted")

    # browse — navigations between load and playback (meta hrefs + non-media xhr/doc)
    navs = [e for e in dom if e["kind"] == "meta"]
    phases["browse"] = _step(bool(navs), MED if navs else LOW,
                             f"{len(navs)} navigation(s) via rrweb Meta" if navs
                             else "no Meta navigations (SPA route changes may not emit Meta)",
                             [e.get("href") for e in navs if e.get("href")][:5])

    # playback — manifest fetch (classifier-backed: HIGH)
    manifests = report.hls_manifests + report.dash_manifests
    phases["playback"] = _step(bool(manifests), HIGH if manifests else MED,
                               f"{len(manifests)} HLS/DASH manifest fetch(es)" if manifests
                               else "no manifest fetch observed",
                               [m.url for m in manifests][:3])

    # segment-stream — segment burst after manifest. Classifier counts HLS .ts;
    # add fMP4/DASH segments (Cloudflare Stream seg_N.mp4 / init.mp4 / .m4s) the
    # classifier misses, deduped by URL, so a real played stream is recognized.
    seg_urls = [s.url for s in report.segments]
    fmp4 = _fmp4_dash_segments(cap)
    seen = set(seg_urls)
    for u in fmp4:
        if u not in seen:
            seen.add(u)
            seg_urls.append(u)
    n_seg = len(seg_urls)
    phases["segment_stream"] = _step(n_seg >= 2, HIGH if n_seg >= 2 else MED,
                                     (f"{n_seg} segment fetch(es)"
                                      + (f" (+{len(fmp4)} fMP4/DASH)" if fmp4 else ""))
                                     if seg_urls
                                     else "no segment fetches observed",
                                     seg_urls[:3])
    return phases


# ── layer 3: template-diff (independent evidence FIRST, then diff) ─
def _template_expectations(tpl: Dict[str, Any]) -> Dict[str, Any]:
    api_base = ((tpl.get("api") or {}).get("base")) or ""
    np = tpl.get("network_patterns") or {}
    pats: List[str] = []
    if isinstance(np, dict):
        for v in np.values():
            if isinstance(v, list):
                pats += [str(x) for x in v]
    elif isinstance(np, list):
        pats = [str(x) for x in np]
    manifest_pats = [p for p in pats if any(k in p.lower() for k in ("m3u8", "mpd", "manifest"))]
    segment_pats = [p for p in pats if any(k in p.lower() for k in (".ts", "seg", "segment", "chunk"))]
    sel = tpl.get("selectors") or {}
    return {
        "host": tpl.get("host") or "",
        "api_base_host": _host(api_base) or api_base,
        "manifest_patterns": manifest_pats,
        "segment_patterns": segment_pats,
        "has_trigger_selector": bool(sel),
    }


def template_diff(cap: Dict[str, Any], report, tpl: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    # 1) INDEPENDENT observed evidence — computed from the capture ALONE.
    net = [e for e in (cap.get("network_log") or []) if isinstance(e, dict)]
    obs_hosts = sorted({_host(e.get("url") or "") for e in net
                        if e.get("url") and not contains_bad_term(e.get("url"))})
    observed = {
        "hosts": obs_hosts,
        "manifests": [m.url for m in (report.hls_manifests + report.dash_manifests)],
        "segments": len({s.url for s in report.segments} | set(_fmp4_dash_segments(cap))),
        "interactions": sum(1 for d in (cap.get("dom_log") or [])
                            if isinstance(d, dict) and d.get("type") == "incremental"
                            and d.get("source") == 2),
        "navigations": sum(1 for d in (cap.get("dom_log") or [])
                           if isinstance(d, dict) and d.get("type") == "meta"),
    }
    if tpl is None:
        return {"template_provided": False, "observed_independent": observed,
                "note": "No gold template supplied — observed evidence only; no readiness verdict."}

    exp = _template_expectations(tpl)
    steps: Dict[str, Any] = {}

    # api base host reached?
    if exp["api_base_host"]:
        hit = any(exp["api_base_host"] in h for h in obs_hosts)
        steps["api_base_reached"] = _step(hit, HIGH if hit else HIGH,
                                          f"expected api host {exp['api_base_host']} "
                                          + ("observed" if hit else "NOT observed"),
                                          [exp["api_base_host"]])
    else:
        steps["api_base_reached"] = _step(False, BLIND, "template has no api.base to check")

    # manifest pattern matched?
    if exp["manifest_patterns"]:
        hit = bool(observed["manifests"])
        steps["manifest_fetched"] = _step(hit, HIGH if hit else HIGH,
                                          "manifest fetch " + ("observed" if hit else "MISSING")
                                          + f" (template expects {len(exp['manifest_patterns'])} pattern(s))",
                                          observed["manifests"][:3])
    else:
        steps["manifest_fetched"] = _step(bool(observed["manifests"]), MED,
                                          "template declares no manifest pattern; "
                                          f"{len(observed['manifests'])} manifest(s) observed")

    # segment stream?
    steps["segment_stream"] = _step(observed["segments"] >= 2,
                                    HIGH if observed["segments"] >= 2 else HIGH,
                                    f"{observed['segments']} segment(s) observed"
                                    + (" (>=2 = stream)" if observed["segments"] >= 2 else " (insufficient)"))

    # trigger interaction (weak — selector not resolved)
    if exp["has_trigger_selector"]:
        steps["trigger_interaction"] = _step(observed["interactions"] > 0, MED,
                                            f"{observed['interactions']} click(s) seen; cannot confirm "
                                            "the click hit the template's trigger selector (not resolved)")

    missing = [k for k, v in steps.items() if not v["observed"]]
    # readiness is a SUMMARY of the diff, deliberately conservative.
    ready = (not missing) and bool(observed["manifests"]) and observed["segments"] >= 2
    return {
        "template_provided": True,
        "template_host": exp["host"],
        "observed_independent": observed,   # reported BEFORE the verdict, on purpose
        "expected": exp,
        "steps": steps,
        "missing_steps": missing,
        "readiness": "ready" if ready else "incomplete",
        "readiness_reason": ("all expected steps observed incl. manifest + segment stream"
                             if ready else f"missing/weak: {', '.join(missing) or 'segment stream or manifest'}"),
    }


# ── blind spots ───────────────────────────────────────────────────
def missing_signals(cap: Dict[str, Any]) -> List[str]:
    blind = [
        "Response/request bodies are off/redacted by default — call semantics (which title, "
        "what the API returned) are not verifiable.",
        "Input values are masked — form/login intent is structural only.",
        "URL query strings are scrubbed — parametrized/signed calls cannot be distinguished.",
        "Click targets are not resolved to selectors — 'select' is 'an interaction occurred'.",
        "Single capture (n=1) — session-specific accidents may look like the workflow.",
    ]
    if not (cap.get("dom_log")):
        blind.append("No dom_log present — interaction/navigation phases are unobservable.")
    if not any(isinstance(d, dict) and d.get("type") == "meta" for d in (cap.get("dom_log") or [])):
        blind.append("No rrweb Meta events — SPA route changes may be silent; navigation undercounted.")
    if not (cap.get("storage_snapshot") or {}):
        blind.append("No storage snapshot — auth-state transitions cannot be timed.")
    return blind


# ── report ────────────────────────────────────────────────────────
def analyze(cap: Dict[str, Any], tpl: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    report = classify_network_log(cap)
    timeline = build_timeline(cap, report)
    return {
        "capture": {"host": cap.get("host"), "url": cap.get("url"), "title": cap.get("title"),
                    "network_events": len(cap.get("network_log") or []),
                    "dom_events": len(cap.get("dom_log") or []),
                    "ws_connections": len(cap.get("websocket_log") or [])},
        "template_diff": template_diff(cap, report, tpl),
        "phases": infer_phases(cap, report, timeline),
        "timeline": timeline,
        "missing_signals": missing_signals(cap),
    }


def _conf(c: str) -> str:
    return {HIGH: "HIGH", MED: "MED", LOW: "LOW", BLIND: "BLIND"}.get(c, c.upper())


def render_markdown(a: Dict[str, Any], max_timeline: int = 60) -> str:
    L: List[str] = []
    c = a["capture"]
    L.append(f"# Workflow diagnostic — {c.get('host') or c.get('url') or 'capture'}")
    L.append("")
    L.append(f"Capture: {c.get('network_events')} network · {c.get('dom_events')} dom · "
             f"{c.get('ws_connections')} ws. **Read-only inference — every step carries a "
             f"confidence label; correlation is not asserted as causation.**")
    L.append("")

    # LEAD: template-diff / readiness
    td = a["template_diff"]
    L.append("## Template-diff (Reptyle-readiness)")
    if not td.get("template_provided"):
        L.append(f"_{td.get('note')}_")
        oi = td["observed_independent"]
        L.append(f"- observed hosts: {len(oi['hosts'])}; manifests: {len(oi['manifests'])}; "
                 f"segments: {oi['segments']}; interactions: {oi['interactions']}; "
                 f"navigations: {oi['navigations']}")
    else:
        oi = td["observed_independent"]
        L.append("**Observed evidence (computed from the capture alone, before diffing):**")
        L.append(f"- hosts={len(oi['hosts'])}, manifests={len(oi['manifests'])}, "
                 f"segments={oi['segments']}, interactions={oi['interactions']}, "
                 f"navigations={oi['navigations']}")
        L.append("")
        L.append(f"**Readiness: `{td['readiness']}`** — {td['readiness_reason']}")
        L.append("")
        L.append("| step | observed | confidence | reason |")
        L.append("|---|---|---|---|")
        for name, s in td["steps"].items():
            L.append(f"| {name} | {'yes' if s['observed'] else 'NO'} | {_conf(s['confidence'])} "
                     f"| {s['reason']} |")
    L.append("")

    # phases
    L.append("## Phases")
    L.append("| phase | observed | confidence | reason |")
    L.append("|---|---|---|---|")
    for name, s in a["phases"].items():
        L.append(f"| {name} | {'yes' if s['observed'] else 'no'} | {_conf(s['confidence'])} "
                 f"| {s['reason']} |")
    L.append("")

    # missing signals / blind spots
    L.append("## Missing signals / blind spots")
    for m in a["missing_signals"]:
        L.append(f"- {m}")
    L.append("")

    # timeline (truncated)
    tl = a["timeline"]
    L.append(f"## Timeline ({len(tl)} events" + (f", first {max_timeline} shown)" if len(tl) > max_timeline else ")"))
    for e in tl[:max_timeline]:
        ts = e.get("ts")
        tag = f"{e['stream']}/{e['kind']}"
        extra = e.get("url") or e.get("href") or ""
        if e.get("status"):
            extra = f"{e.get('method','')} {e.get('status')} {extra}"
        noise = " [noise]" if e.get("noise") else ""
        L.append(f"- `{ts}` {tag}{noise} {extra}".rstrip())
    if len(tl) > max_timeline:
        L.append(f"- … {len(tl) - max_timeline} more")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only workflow diagnostic from a capture.")
    ap.add_argument("capture", type=Path, help="capture.json or .wacz")
    ap.add_argument("--template", type=Path, default=None, help="gold template json (enables readiness diff)")
    ap.add_argument("--json", type=Path, default=None, help="write the full analysis as JSON")
    ap.add_argument("--max-timeline", type=int, default=60)
    args = ap.parse_args()

    cap = load_capture(args.capture)
    tpl = json.loads(args.template.read_text(encoding="utf-8")) if args.template else None
    a = analyze(cap, tpl)
    if args.json:
        args.json.write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render_markdown(a, max_timeline=args.max_timeline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
