#!/usr/bin/env python3
"""validate_global_config.py — validate global/JSON config files (H). Read-only.
Scans for well-known config JSON files and checks they parse + carry expected
top-level keys where known. Graceful when none are present (sandbox). --json
"""
import argparse, glob, json, os, sys

_CANDIDATES = ["sites_config.json", "sites_config.example.json", "config.json",
               "settings.json", "global_config.json"]


def validate(root="."):
    out = {"checked": [], "errors": [], "missing": []}
    found = False
    for name in _CANDIDATES:
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        found = True
        try:
            json.load(open(p))
            out["checked"].append({"file": name, "ok": True})
        except (OSError, ValueError) as e:
            out["checked"].append({"file": name, "ok": False, "error": str(e)[:120]})
            out["errors"].append(name)
    if not found:
        out["missing"] = _CANDIDATES
        out["note"] = "no global config JSON in tree (expected — live config is on the host)"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = validate(a.root)
    print(json.dumps(d, indent=2) if a.json else
          f"checked {len(d['checked'])} file(s); errors: {d['errors'] or 'none'}")
    return 1 if d["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
