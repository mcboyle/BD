#!/usr/bin/env python3
"""
check_doc_drift.py — documentation presence + drift report (P1 gate + P10 aid).

Three classes of finding:

  1. REQUIRED docs (hard gate) — the operational docs a release zip must ship.
     Missing any of these → exit 1.
  2. GENERATED indices — ENDPOINT_CATALOG.md / FUNCTION_INDEX.md must exist;
     whether they are IN SYNC is enforced by tests/test_endpoint_catalog_in_sync.py
     (and the function-index regen step), so this tool only checks presence and
     points at that test rather than re-deriving the catalog.
  3. ARCHIVAL candidates (informational) — historical per-release handoffs that
     KB_ACTIVE_INDEX says to archive (root v3_66_*_handoff.md, SESSION_HANDOFF*,
     DEBUG_FINDINGS_*, docs/HANDOFF_v3_66_*). Never deleted by this tool.

It also reports the canonical KB set (charter / active index / automation policy /
operating instructions / schemas / newest handoff / runbook): these live in
PROJECT KNOWLEDGE and are NOT expected in the source tree — reported as
informational so the tree⇄project-knowledge split is visible, not silently
assumed.

STRICTLY READ-ONLY. stdlib-only.

Exit 0 = all required docs present. Exit 1 = a required doc missing. Exit 2 = bad root.
"""
import argparse
import glob
import json
import os
import re
import sys

REQUIRED = [
    "README.md", "CHANGELOG.md", "SANDBOX.md", "SETUP.md",
    "ENDPOINT_CATALOG.md", "FUNCTION_INDEX.md",
]
GENERATED = ["ENDPOINT_CATALOG.md", "FUNCTION_INDEX.md"]
# Canonical KB set — lives in project knowledge, not the tree (informational).
KB_SET = [
    "KB_ACTIVE_INDEX.md", "PROJECT_CHARTER.md", "PROJECT_GOALS.md",
    "AUTOMATION_POLICY.md", "PROJECT_OPERATING_INSTRUCTIONS.md", "SCHEMAS.md",
]
ARCHIVE_GLOBS = [
    "v3_66_*_handoff.md", "SESSION_HANDOFF*.md", "DEBUG_FINDINGS_*.md",
    "docs/HANDOFF_v3_66_*.md",
]
_VER_RE = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")


def _read_version(root):
    try:
        with open(os.path.join(root, "bulk_downloader", "__init__.py")) as fh:
            for ln in fh:
                m = re.search(r'__version__\s*=\s*["\'](\d+\.\d+\.\d+)', ln)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def scan(root="."):
    out = {"required": {}, "generated": {}, "archive_candidates": [],
           "kb_set_in_tree": {}, "newest_handoff_in_tree": None, "version": None}
    out["version"] = _read_version(root)
    for d in REQUIRED:
        out["required"][d] = os.path.isfile(os.path.join(root, d))
    out["docs_dir_nonempty"] = bool(glob.glob(os.path.join(root, "docs", "*")))
    for d in GENERATED:
        out["generated"][d] = os.path.isfile(os.path.join(root, d))
    for g in ARCHIVE_GLOBS:
        out["archive_candidates"] += [
            os.path.relpath(p, root) for p in glob.glob(os.path.join(root, g))]
    out["archive_candidates"] = sorted(set(out["archive_candidates"]))
    for d in KB_SET:
        out["kb_set_in_tree"][d] = os.path.isfile(os.path.join(root, d))
    # newest KB_HANDOFF in tree, if any
    hs = sorted(glob.glob(os.path.join(root, "KB_HANDOFF_v3_66_*.md")))
    out["newest_handoff_in_tree"] = os.path.basename(hs[-1]) if hs else None
    return out


def _print(d):
    print("=" * 70)
    print(f"  Documentation presence + drift — release {d['version']}")
    print("=" * 70)
    print("\n-- REQUIRED docs (gate) --")
    for name, present in d["required"].items():
        print(f"  {'OK ' if present else '!! '}{name}" + ("" if present else "  MISSING"))
    print(f"  {'OK ' if d['docs_dir_nonempty'] else '!! '}docs/ non-empty")
    print("\n-- GENERATED indices (sync enforced by test_endpoint_catalog_in_sync) --")
    for name, present in d["generated"].items():
        print(f"  {'OK ' if present else '!! '}{name}")
    print(f"\n-- ARCHIVAL candidates ({len(d['archive_candidates'])}) "
          "— historical handoffs KB_ACTIVE_INDEX says to archive (NOT deleted here) --")
    for p in d["archive_candidates"]:
        print(f"  · {p}")
    print("\n-- canonical KB set (lives in PROJECT KNOWLEDGE; tree presence is informational) --")
    for name, present in d["kb_set_in_tree"].items():
        print(f"  {'in-tree' if present else 'project-knowledge only'}: {name}")
    print(f"  newest KB_HANDOFF in tree: {d['newest_handoff_in_tree'] or '(none — PK only)'}")
    missing = [k for k, v in d["required"].items() if not v] + \
              ([] if d["docs_dir_nonempty"] else ["docs/"])
    print("\n" + "=" * 70)
    print("  VERDICT: " + ("all required docs present"
                           if not missing else f"MISSING required: {', '.join(missing)}"))
    print("=" * 70)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Doc presence + drift (read-only).")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isdir(os.path.join(args.root, "bulk_downloader")):
        print(f"error: {args.root!r} doesn't look like the repo root", file=sys.stderr)
        return 2
    d = scan(args.root)
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        _print(d)
    missing = [k for k, v in d["required"].items() if not v] or (not d["docs_dir_nonempty"])
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
