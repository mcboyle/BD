#!/usr/bin/env python3
"""check_frontend_present.py — standalone frontend-preservation gate."""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from typing import Dict, List, Tuple

CRITICAL_FRONTEND = [
    "frontend/dist/index.html",
    "frontend/src/App.tsx",
    "frontend/src/lib/api-types.ts",
    "frontend/src/routes/Advanced.tsx",
    "frontend/src/routes/Settings.tsx",
    "frontend/src/routes/SiteDetail.tsx",
    "frontend/src/routes/DryRunInspector.tsx",
    "frontend/src/routes/TemplateManager.tsx",
    "frontend/src/components/ProfileCard.tsx",
    "frontend/src/components/SiteTemplateCard.tsx",
]


def _is_frontend(path: str) -> bool:
    return path.startswith("frontend/") and "frontend/node_modules/" not in path


def frontend_shas(zip_path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith("/") or not _is_frontend(name):
                continue
            out[name] = hashlib.sha256(zf.read(name)).hexdigest()
    return out


def compare(baseline_zip: str, candidate_zip: str) -> Dict[str, List[str]]:
    base = frontend_shas(baseline_zip)
    cand = frontend_shas(candidate_zip)
    missing = sorted(p for p in base if p not in cand)
    changed = sorted(p for p in base if p in cand and base[p] != cand[p])
    added = sorted(p for p in cand if p not in base)
    return {"missing": missing, "changed": changed, "added": added}


def required_present(zip_path: str) -> List[str]:
    bad: List[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for req in CRITICAL_FRONTEND:
            if req not in names:
                bad.append(req + " (absent)")
            else:
                try:
                    if zf.getinfo(req).file_size == 0:
                        bad.append(req + " (empty)")
                except KeyError:
                    bad.append(req + " (absent)")
    return bad


def _run(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Frontend preservation gate")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--required", action="store_true",
                    help="check the critical-present set in --candidate")
    ap.add_argument("--approved", nargs="*", default=[],
                    help="frontend paths whose change/removal is approved")
    args = ap.parse_args(argv)

    failed = False

    if args.required:
        bad = required_present(args.candidate)
        if bad:
            failed = True
            print("FRONTEND REQUIRED-PRESENT: FAIL")
            for b in bad:
                print("  missing/empty:", b)
        else:
            print("FRONTEND REQUIRED-PRESENT: OK (%d critical files present)" % len(CRITICAL_FRONTEND))

    if args.baseline:
        res = compare(args.baseline, args.candidate)
        approved = set(args.approved)
        unapproved_missing = [p for p in res["missing"] if p not in approved]
        unapproved_changed = [p for p in res["changed"] if p not in approved]
        print("FRONTEND REGRESSION COMPARE  baseline=%s candidate=%s" % (args.baseline, args.candidate))
        print("  missing=%d changed=%d added=%d" % (len(res["missing"]), len(res["changed"]), len(res["added"])))
        for p in res["missing"]:
            print("  MISSING ", p, "(approved)" if p in approved else "")
        for p in res["changed"]:
            print("  CHANGED ", p, "(approved)" if p in approved else "")
        for p in res["added"]:
            print("  added   ", p)
        if unapproved_missing or unapproved_changed:
            failed = True
            print("FRONTEND REGRESSION: FAIL (%d unapproved missing, %d unapproved changed)"
                  % (len(unapproved_missing), len(unapproved_changed)))
        else:
            print("FRONTEND REGRESSION: OK")

    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(_run(sys.argv[1:]))
    except (OSError, zipfile.BadZipFile) as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(2)
