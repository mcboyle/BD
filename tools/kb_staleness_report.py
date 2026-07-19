#!/usr/bin/env python3
"""kb_staleness_report.py — stale-doc detection (D). Read-only.
Reuses check_doc_drift for archival candidates; additionally flags docs whose body
references a version older than current __version__ in a 'current/latest' context.
--json"""
import argparse, glob, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_doc_drift as CDD  # type: ignore
import kb_core as _KC  # type: ignore


def report(root="."):
    # thin wrapper over the shared core (single docs walk + read);
    # pass the drift scan through so it is computed once.
    return _KC.staleness(_KC.collect(root), drift=CDD.scan(root))


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = report(a.root)
    print(json.dumps(d, indent=2) if a.json else
          f"archival candidates {d['archival_count']} | stale version refs {len(d['stale_version_refs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
