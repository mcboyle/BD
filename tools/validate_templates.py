#!/usr/bin/env python3
"""validate_templates.py — validate every template's required fields (H). Read-only.
Reuses template_inventory. Flags malformed (unreadable), missing-required (the
promote-gate fields), and unknown top-level keys (vs the gold contract). Exit 1 if
any reviewed/enabled template is malformed or missing required fields.
CLI: --root, --json
"""
import argparse, glob, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_inventory as TI  # type: ignore

_KNOWN_TOP = {"api", "confidence", "evidence_counts", "host", "network_patterns",
              "promotion_notes", "resolutions", "review_notes", "safety_notes",
              "schema", "selectors", "source_capture", "status", "template_logic",
              "hostname", "site"}


def validate(root="."):
    scan = TI.scan(root)
    results = []
    hard_fail = False
    for name, items in scan["dirs"].items():
        for a in items:
            missing = []
            if not (a["download_trigger"] or a["row_selectors_count"]):
                missing.append("trigger_or_rows")
            if not a["resolutions_count"]:
                missing.append("resolutions")
            entry = {"source": a["source"], "host": a["host"], "status": a["status"],
                     "missing_required": missing, "blocked_terms": a["blocked_terms"]}
            results.append(entry)
            if name in ("reviewed", "enabled") and missing:
                hard_fail = True
    # unknown top-level keys (read files directly)
    unknown = {}
    for sub in ("reviewed", "enabled", "drafts", "review_candidates"):
        for p in glob.glob(os.path.join(root, "templates", sub, "*.json")):
            try:
                t = json.load(open(p))
            except (OSError, ValueError):
                unknown[os.path.relpath(p, root)] = ["<malformed json>"]
                hard_fail = hard_fail or sub in ("reviewed", "enabled")
                continue
            extra = sorted(set(t.keys()) - _KNOWN_TOP)
            if extra:
                unknown[os.path.relpath(p, root)] = extra
    return {"results": results, "unknown_top_keys": unknown,
            "sanity": scan["sanity"], "hard_fail": hard_fail}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = validate(a.root)
    if a.json:
        print(json.dumps(d, indent=2))
    else:
        for r in d["results"]:
            flag = ("MISSING:" + ",".join(r["missing_required"])) if r["missing_required"] else "ok"
            print(f"{r['source']:<40} {flag}")
        if d["unknown_top_keys"]:
            print("unknown top keys:", d["unknown_top_keys"])
        print("VERDICT:", "FAIL" if d["hard_fail"] else "ok")
    return 1 if d["hard_fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
