#!/usr/bin/env python3
"""capture_model_golden.py — B0 capture-model characterization golden.

Phase B converges three independent readers of the same raw capture
(`capture_ingest.load_capture`/`normalize_capture`, `workflow_diagnostic.load_capture`,
and `build_template_from_wacz.build_template`'s inline extraction) onto one
canonical model. Before ANY of those is rerouted (B1/B2), this golden pins their
CURRENT derived output on one fixed synthetic capture, so a reroute that changes
behaviour by even one field fails loudly instead of silently.

Same discipline as the extraction_core step-5 consolidation golden and the A3
dependency-graph gate: a deterministic regen + a `--check` that diffs against the
committed golden and exits non-zero on drift. Wire `--check` into the build before
landing B1.

The three projections keep DERIVED fields and drop passthrough/nondeterministic
ones (the raw echoed logs, the capture sha256, the temp capture filename), because
those are inputs, not behaviour.

POSTURE: read-only. The fixture is fabricated in-process; the projections carry
scrubbed urls + signing-marker NAMES only (never values) — the capture_ingest /
workflow_diagnostic normalizers already redact. Synthetic only.

stdlib + project modules; browser-free; plain `python3`.

CLI:
    python3 tools/capture_model_golden.py            # print current projection
    python3 tools/capture_model_golden.py --write     # (re)write the golden
    python3 tools/capture_model_golden.py --check      # exit 1 on drift (build gate)
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bulk_downloader import capture_ingest as _CI          # noqa: E402
from bulk_downloader.wacz_export import write_wacz          # noqa: E402
import workflow_diagnostic as _WD                           # type: ignore  # noqa: E402
import build_template_from_wacz as _BTW                     # type: ignore  # noqa: E402

GOLDEN_PATH = _ROOT / "tests" / "golden" / "capture_model.golden.json"


# ── the fixed synthetic capture (serialized-node; deterministic) ─────────────

def fixed_capture():
    """One deterministic capture exercising the convergence-relevant paths:
    a query-signed page url, a signed download-resolution API call, a media
    rendition, and a serialized-node download modal."""
    _ctr = [0]

    def el(tag, attrs=None, kids=None):
        _ctr[0] += 1
        return {"id": _ctr[0], "type": 2, "tagName": tag,
                "attributes": attrs or {}, "childNodes": kids or []}

    rows = [el("li", {"role": "listitem"}, [el("a", {"download": ""})]) for _ in range(2)]
    root = el("div", {"class": "app"},
              [el("button", {"class": "dl"}),
               el("div", {"class": "ant-modal", "role": "dialog"},
                  [el("ul", {}, rows)])])
    return {
        "host": "demo.example", "url": "https://demo.example/v/9?token=SECRET",
        "title": "t", "captured_at": "2020-01-01T00:00:00Z",
        "network_log": [
            # Realistic same-clock ordering (epoch-ms, shared with action_timeline
            # ``ts``): page loads, the operator clicks button.dl at ts=1000, and the
            # download-resolution + media rendition the click TRIGGERS arrive after
            # it. So REC-3's attribution sees no media in flight before the click
            # (autoplay=False) and a fresh download after it (fresh_download=True) —
            # a genuine click-triggered download, not autoplay carry-over.
            {"seq": 1, "timestamp": 1, "type": "document", "method": "GET",
             "url": "https://demo.example/v/9?token=SECRET", "response_status": 200},
            {"seq": 2, "timestamp": 1100, "type": "xhr", "method": "GET",
             "url": "https://api.demo.example/v1/movie/9/download-resolution/1080?sig=ABC",
             "response_status": 200},
            {"seq": 3, "timestamp": 1200, "type": "media", "method": "GET",
             "url": "https://cdn.demo.example/9/file_1080.mp4", "response_status": 200},
        ],
        "dom_log": [
            {"timestamp": 1, "type": "meta",
             "data": {"href": "https://demo.example/v/9", "width": 1, "height": 1}},
            {"timestamp": 2, "type": "full_snapshot", "data": {"node": root}},
        ],
        # Wave B: operator-recorded action_timeline (inspect_pick entries as
        # persisted into the WACZ) — the download click whose effect produced the
        # media rendition. Structural + kinds/counts only; no URLs/values.
        "action_timeline": [
            {"ts": 1000, "selector": "button.dl", "xpath": "//div/button",
             "role": "download link", "confidence": 0.9, "tag": "button",
             "excerpt": "<button class=\"dl\">",
             "effect": {"req_count": 2, "manifest": 0, "segments": 0,
                        "direct_media": 1, "signed": True, "nav": False}},
        ],
        "action_timeline_count": 1,
    }


# ── projections: keep DERIVED behaviour, drop passthrough/nondeterministic ───

def _proj_ingest(m):
    m = dict(m)
    m.pop("_raw", None)                 # passthrough of the input, not behaviour
    return m


def _proj_workflow(wc):
    # the readers echo dom_log/network_log unchanged; pin the DERIVED fields only
    return {
        "host": wc.get("host"), "url": wc.get("url"), "title": wc.get("title"),
        "captured_at": wc.get("captured_at"),
        "capture_health": wc.get("capture_health"),
        "redaction_profile": wc.get("redaction_profile"),
        "dom_log_len": len(wc.get("dom_log") or []),
        "network_log_len": len(wc.get("network_log") or []),
    }


def _proj_builder(d):
    d = json.loads(json.dumps(d))       # deep copy
    src = d.get("source") or {}
    src.pop("capture_sha256", None)     # depends on wacz bytes — input, not behaviour
    src.pop("capture_file", None)       # temp filename
    return d


def build_projection():
    cap = fixed_capture()
    ingest = _proj_ingest(_CI.normalize_capture(cap, source_name="synthetic"))
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "fixed.wacz"
        write_wacz(cap, str(w))
        workflow = _proj_workflow(_WD.load_capture(w))
        builder = _proj_builder(_BTW.build_template(w))
    return {
        "_schema": "bulk_downloader.capture_model_golden.v1",
        "_note": "B0 characterization golden — readers/normalizers' current derived "
                 "output on a fixed synthetic capture; guards Phase-B reroutes.",
        "capture_ingest.normalize_capture": ingest,
        "workflow_diagnostic.load_capture": workflow,
        "build_template_from_wacz.build_template": builder,
    }


def _canonical(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def write_golden():
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(_canonical(build_projection()) + "\n", "utf-8")
    return GOLDEN_PATH


def check_golden():
    """Return (ok, diff_lines). ok=False on any drift from the committed golden."""
    if not GOLDEN_PATH.is_file():
        return False, [f"golden missing: {GOLDEN_PATH} (run --write)"]
    want = GOLDEN_PATH.read_text("utf-8").rstrip("\n")
    have = _canonical(build_projection())
    if want == have:
        return True, []
    import difflib
    diff = list(difflib.unified_diff(want.splitlines(), have.splitlines(),
                                     "golden", "current", lineterm=""))
    return False, diff


def main(argv=None):
    ap = argparse.ArgumentParser(description="B0 capture-model characterization golden.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="(re)write the golden file")
    g.add_argument("--check", action="store_true", help="exit 1 on drift (build gate)")
    args = ap.parse_args(argv)
    if args.write:
        p = write_golden()
        print(f"wrote {p}")
        return 0
    if args.check:
        ok, diff = check_golden()
        if ok:
            print("capture-model golden: OK (no drift)")
            return 0
        print("capture-model golden: DRIFT")
        print("\n".join(diff[:200]))
        return 1
    print(_canonical(build_projection()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
