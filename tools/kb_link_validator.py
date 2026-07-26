#!/usr/bin/env python3
"""kb_link_validator.py — validate markdown links/file refs in docs (D). Read-only.
Scans *.md across the tree for [text](path) links and reports relative targets
that don't exist (skips fenced code, http(s), and #anchors). --json"""
import argparse, glob, json, os, re, sys
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import kb_core as _KC  # type: ignore

_DOCS = ["*.md", "docs/*.md", "docs/**/*.md"]


def _md_files(root):
    out = []
    for pat in _DOCS:
        out += glob.glob(os.path.join(root, pat), recursive=True)
    return sorted(set(out))


def validate(root="."):
    # thin wrapper over the shared core (single docs walk + read)
    return _KC.links(_KC.collect(root))


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = validate(a.root)
    print(json.dumps(d, indent=2) if a.json else
          f"docs {d['docs_scanned']} links {d['links_checked']} broken {d['broken_count']}")
    return 1 if d["broken"] else 0


if __name__ == "__main__":
    sys.exit(main())
