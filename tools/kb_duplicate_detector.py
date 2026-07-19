#!/usr/bin/env python3
"""kb_duplicate_detector.py — find near-duplicate docs (D). Read-only.
Token-set Jaccard similarity between *.md files; pairs above the threshold are
flagged as possible duplicate guidance. --json --threshold 0.6"""
import argparse, glob, json, os, re, sys
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import kb_core as _KC  # type: ignore

_WORD = re.compile(r"[a-z0-9]{4,}")


def _tokens(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read().lower()
    except OSError:
        return set()
    return set(_WORD.findall(text))


def detect(root=".", threshold=0.6):
    # thin wrapper over the shared core (single docs walk + read)
    return _KC.duplicates(_KC.collect(root), threshold)


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default=".")
    ap.add_argument("--threshold", type=float, default=0.6); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = detect(a.root, a.threshold)
    print(json.dumps(d, indent=2) if a.json else
          f"docs {d['docs']} duplicate pairs (>= {d['threshold']}): {d['pair_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
