#!/usr/bin/env python3
"""builder_gap_report.py — A6-0 manual-intervention gap measurement (read-only).

A6's goal is "new site families templatize with minimal hand-editing." This tool
makes "minimal" measurable WITHOUT waiting for per-site friction: it diffs what
the EXISTING builder can auto-derive against what a working REVIEWED GOLD actually
carries, and labels each load-bearing field AUTO / PARTIAL / MANUAL. The MANUAL +
PARTIAL set (excluding by-design-human fields) is the concrete A6-1/2/3 target
list — grounded in the one real gold we already have, not guessed.

Two profiles, both from real artifacts:
  * GOLD profile     — which load-bearing runtime fields a reviewed/enabled
                       template populates (read from templates/reviewed/*.json).
  * BUILDER profile  — which of those fields `build_template_from_wacz.build_template`
                       + `template_normalize.normalize_draft` actually emit. Probed
                       on a realistic serialized-node synthetic capture built in
                       process (the cloakbrowser encoding — rrweb serialized nodes,
                       not an html string), so NO F2 capture is needed. A real
                       capture may be supplied with --capture on the operator host.

Field classification:
  AUTO    — gold needs it AND the builder emits a usable form of it.
  PARTIAL — builder emits a HEURISTIC/weaker form than the gold's (known cases:
            `download.trigger` is a text/attr guess, never the observed click
            element; `download.row_selectors` cover role/[download] shapes only).
  MANUAL  — gold needs it, builder never emits it (e.g. runtime `api.base` and the
            named api endpoints; `template_logic`, which is human-only by schema).

POSTURE: read-only. Builds nothing durable, enables nothing, never touches
extraction_core or the live download path. The synthetic capture is fabricated
in-process; reviewed golds are already redaction-clean. Reports field names +
counts + classification, never capture values.

stdlib + project modules; browser-free; plain `python3`.

CLI:
    python3 tools/builder_gap_report.py [--gold PATH] [--capture PATH] [--json]
Exit: 0 always (measurement), 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_template_from_wacz as _BTW   # type: ignore  # noqa: E402
from bulk_downloader import template_normalize as _TN   # noqa: E402
from bulk_downloader.wacz_export import write_wacz       # noqa: E402


# Load-bearing runtime fields: presence of these is what makes a template usable.
# (template_logic is human-only by the v1 schema and tracked separately so it does
# not pollute the addressable target list.)
_FIELDS = (
    "download.trigger",
    "download.row_selectors",
    "api.base",
    "api.named_endpoints",
    "network_patterns",
    "resolutions",
    "match",
)
# Both heuristic/partial fields (download.trigger, download.row_selectors) are now
# graded by their derived value_kind / class-stability directly in classify().
# Human-only by schema — never an A6 target, reported separately.
_BY_DESIGN_HUMAN = ("template_logic",)


# ── synthetic, realistic, serialized-node capture (cloakbrowser encoding) ────

def _el(tag, attrs=None, kids=None, _c=[0]):
    _c[0] += 1
    return {"id": _c[0], "type": 2, "tagName": tag,
            "attributes": attrs or {}, "childNodes": kids or []}


def _txt(s, _c=[10000]):
    _c[0] += 1
    return {"id": _c[0], "type": 3, "textContent": str(s)}


def _synthetic_capture():
    """A download-modal capture stored as rrweb serialized nodes (the real
    cloakbrowser encoding), exercising every derivation path the builder has:
    a trigger button, an ant-modal dialog with repeating download rows (download
    anchors + resolution buttons), a resolution API call, and a media rendition.
    Entirely fabricated."""
    rows = []
    for res in (2160, 1080, 720):
        rows.append(_el("li", {"role": "listitem"}, [
            _el("a", {"download": "",
                      "href": "/api/v1/movie/9/download-resolution/%d" % res}),
            _el("button", {"role": "button"}, [_txt(res)]),
        ]))
    modal = _el("div", {"class": "ant-modal", "role": "dialog", "aria-modal": "true"},
                [_el("ul", {"class": "ant-list"}, rows)])
    root = _el("div", {"class": "app"},
               [_el("button", {"class": "download-open", "role": "button",
                               "data-tooltip": "Download Full Movie"}), modal])
    return {
        "host": "demo.example", "url": "https://demo.example/v/9", "title": "t",
        "network_log": [
            {"timestamp": 1, "type": "document", "method": "GET",
             "url": "https://demo.example/v/9", "response_status": 200},
            {"timestamp": 2, "type": "xhr", "method": "GET",
             "url": "https://api.demo.example/api/v1/movie/9/download-resolution/1080",
             "response_status": 200},
            {"timestamp": 3, "type": "media", "method": "GET",
             "url": "https://cdn.demo.example/9/file_1080.mp4", "response_status": 200},
        ],
        "dom_log": [
            {"timestamp": 1, "type": "meta",
             "data": {"href": "https://demo.example/v/9", "width": 1, "height": 1}},
            {"timestamp": 2, "type": "full_snapshot", "data": {"node": root}},
        ],
    }


# ── profiles ──────────────────────────────────────────────────────────────--

def _present(val):
    if isinstance(val, (list, dict, str)):
        return bool(val)
    return val is not None


def gold_profile(gold):
    """Which load-bearing fields a reviewed gold populates, with counts."""
    dl = (gold.get("selectors") or {}).get("download") or {}
    api = gold.get("api") or {}
    named = [k for k in api.keys() if k != "base"]
    p = {
        "download.trigger": {"present": _present(dl.get("trigger"))},
        "download.row_selectors": {"present": bool(dl.get("row_selectors")),
                                   "count": len(dl.get("row_selectors") or [])},
        "api.base": {"present": _present(api.get("base"))},
        "api.named_endpoints": {"present": bool(named), "count": len(named),
                                "names": sorted(named)},
        "network_patterns": {"present": bool(gold.get("network_patterns")),
                             "count": len(gold.get("network_patterns") or [])},
        "resolutions": {"present": bool(gold.get("resolutions")),
                        "count": len(gold.get("resolutions") or [])},
        "match": {"present": bool(gold.get("match") or gold.get("host"))},
    }
    p["_human"] = {f: _present(gold.get(f)) for f in _BY_DESIGN_HUMAN}
    return p


def builder_profile(capture_path):
    """Run the real builder + normalizer on a capture; report which load-bearing
    fields auto-derive, with counts. Reads candidate (normalized) shape, which is
    what a reviewer starts from."""
    draft = _BTW.build_template(Path(capture_path))
    cand = _TN.normalize_draft(draft)
    dl = (cand.get("selectors") or {}).get("download") or {}
    # A6-1 surfaces the derived base+named endpoints as a review-only candidate
    # (accepted at promotion), never the runtime api block — so the gap is
    # "did the builder auto-derive the shape the reviewer needs", measured here.
    api = cand.get("api_candidate") or {}
    named = [k for k in api.keys() if k != "base"]
    # the normalizer surfaces api host as a review-only *candidate*, never the
    # runtime api.base — so a populated api_base_candidate is still NOT api.base.
    return {
        "download.trigger": {"present": _present(dl.get("trigger")),
                             "value_kind": _trigger_kind(dl.get("trigger"))},
        "download.row_selectors": {"present": bool(dl.get("row_selectors")),
                                   "count": len(dl.get("row_selectors") or []),
                                   "class_stable": any(
                                       ("href*=" in str(s) or ":has-text(" in str(s))
                                       for s in (dl.get("row_selectors") or []))},
        "api.base": {"present": _present(api.get("base")),
                     "review_only_candidate": _present(cand.get("api_base_candidate"))},
        "api.named_endpoints": {"present": bool(named), "count": len(named)},
        "network_patterns": {"present": bool(cand.get("network_patterns")),
                             "count": len(cand.get("network_patterns") or [])},
        "resolutions": {"present": bool(cand.get("resolutions")),
                        "count": len(cand.get("resolutions") or [])},
        "match": {"present": bool(cand.get("match") or cand.get("host"))},
    }


def _trigger_kind(trig):
    if not trig:
        return None
    t = str(trig)
    if t.startswith("text="):
        return "text-heuristic"     # a label guess, not the observed click target
    if "[download]" in t:
        return "download-attr"      # the row link, not the modal-open button
    return "selector"


# ── gap classification ────────────────────────────────────────────────────--

def classify(gp, bp):
    """Per-field AUTO / PARTIAL / MANUAL given a gold profile + builder profile."""
    out = {}
    for f in _FIELDS:
        need = gp.get(f, {}).get("present")
        emit = bp.get(f, {}).get("present")
        if not need:
            out[f] = {"label": "N/A", "note": "gold does not use this field"}
            continue
        if not emit:
            extra = ""
            if f == "api.base" and bp[f].get("review_only_candidate"):
                extra = " (only a review-only api_base_candidate, never the runtime base)"
            out[f] = {"label": "MANUAL", "note": "builder never emits a usable form" + extra}
        elif f == "download.trigger" and bp[f].get("value_kind") in ("text-heuristic", "download-attr"):
            out[f] = {"label": "PARTIAL",
                      "note": f"emitted as {bp[f].get('value_kind')} — a guess, not the observed click element"}
        elif f == "download.row_selectors" and not bp[f].get("class_stable"):
            out[f] = {"label": "PARTIAL",
                      "note": f"emits {bp[f].get('count')} role/[download] shapes only "
                              f"(no class-stable href/resolution families derived)"}
        else:
            out[f] = {"label": "AUTO",
                      "note": f"builder count={bp[f].get('count')} gold count={gp[f].get('count')}"
                      if "count" in gp.get(f, {}) else "auto-derived"}
    return out


def _targets(cls, gp):
    """The addressable A6 target list: MANUAL+PARTIAL fields, mapped to the slice
    that would close them. Excludes by-design-human fields."""
    slice_of = {
        "api.base": "A6-1 (api host/base from observed download-API)",
        "api.named_endpoints": "A6-1 (named endpoints from request grouping)",
        "download.trigger": "A6-1 (trigger from click->modal-open timeline)",
        "download.row_selectors": "A6-2 (class-stable / menu row shapes)",
    }
    tl = []
    for f, c in cls.items():
        if c["label"] in ("MANUAL", "PARTIAL"):
            tl.append({"field": f, "label": c["label"],
                       "slice": slice_of.get(f, "A6 (unscoped)"), "why": c["note"]})
    human = [f for f, present in gp.get("_human", {}).items() if present]
    return tl, human


def report(gold_path=None, capture_path=None):
    golds = ([gold_path] if gold_path
             else sorted(glob.glob(str(_ROOT / "templates" / "reviewed" / "*.template.json"))))
    if not golds:
        return {"note": "no reviewed gold found under templates/reviewed/ — "
                "supply --gold; cannot measure the gap", "rows": []}
    # builder profile: real capture if given, else the in-process synthetic.
    synthetic = capture_path is None
    if synthetic:
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / "synthetic.wacz"
            write_wacz(_synthetic_capture(), str(w))
            bp = builder_profile(str(w))
    else:
        bp = builder_profile(capture_path)

    rows = []
    for g in golds:
        gold = json.loads(Path(g).read_text("utf-8"))
        gp = gold_profile(gold)
        cls = classify(gp, bp)
        targets, human = _targets(cls, gp)
        rows.append({"gold": Path(g).name, "host": gold.get("host"),
                     "classification": cls, "targets": targets,
                     "human_only_fields": human})
    return {"builder_source": "synthetic (serialized-node, in-process)" if synthetic
            else capture_path,
            "fields": list(_FIELDS), "rows": rows}


# ── render ──────────────────────────────────────────────────────────────────

def render_markdown(rep):
    if rep.get("note"):
        return rep["note"]
    L = ["=" * 72, "  Builder gap report (A6-0) — manual-intervention targets", "=" * 72,
         f"  builder profile from: {rep['builder_source']}"]
    for r in rep["rows"]:
        L += ["", f"  GOLD: {r['gold']}  (host {r['host']})", "  " + "-" * 50]
        for f, c in r["classification"].items():
            L.append(f"    {c['label']:<7} {f:<26} {c['note']}")
        if r["human_only_fields"]:
            L.append(f"    [human-only by schema, not an A6 target: "
                     f"{', '.join(r['human_only_fields'])}]")
        L.append(f"\n  ADDRESSABLE TARGETS ({len(r['targets'])}):")
        for t in r["targets"]:
            L.append(f"    - [{t['label']}] {t['field']}  ->  {t['slice']}")
    L.append("=" * 72)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="A6-0 builder-gap report (read-only).")
    ap.add_argument("--gold", help="reviewed gold template (default: all under templates/reviewed/)")
    ap.add_argument("--capture", help="real capture .wacz for the builder profile "
                    "(operator host; default: in-process synthetic)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        rep = report(gold_path=args.gold, capture_path=args.capture)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2, sort_keys=True) if args.json else render_markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
