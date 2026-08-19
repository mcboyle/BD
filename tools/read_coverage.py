#!/usr/bin/env python3
"""read_coverage -- record lines READ, not just files touched.

"Read every line" is currently an honor-system line in the attestation. This tool
turns it into a measured, gated fact. For each audited file it pulls EVERY function
span from KNOWLEDGE_GRAPH.db, then computes which spans the deliverable actually
ACCOUNTS FOR -- either explicitly (a `spans_accounted` list: each function mapped to
finding | assurance | read-benign) or, for legacy deliverables, INFERRED from the
line numbers the findings / witnesses / fp-confirmations cite.

Output per file:
  total_spans / accounted_spans / coverage%  -- evidenced read coverage
  accounted_span_lines vs file SLOC          -- recorded-read vs merely-touched
  UNACCOUNTED functions                       -- read asserted but not evidenced

The RECORDING convention going forward: a deliverable carries
  "spans_accounted": { "qualname": "finding:F-X" | "assurance:A-Y" | "read-benign", ... }
so read coverage is 100% evidenced and un-accounted functions are a hard gap a
reviewer (or verify_audit, with --min-coverage) can reject on.

Usage: read_coverage.py --audit AUDIT_<B>.json [--db KG.db] [--min-coverage 0.0]
Stdlib only.
"""
import argparse
import json
import os
import re
import sqlite3

ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.path.join(ROOT, "review")
DB = os.path.join(REVIEW, "artifacts", "KNOWLEDGE_GRAPH.db")


def _spans(cx, path):
    rows = cx.execute("SELECT qualname, span FROM nodes WHERE kind='function' AND path=?",
                      (path,)).fetchall()
    out = []
    for q, s in rows:
        if s and "-" in s:
            a, b = s.split("-", 1)
            try:
                out.append((q, int(a), int(b)))
            except ValueError:
                pass
    return sorted(out, key=lambda x: x[1])


def _cited_lines(audit, path):
    """line numbers the deliverable evidences for this file (findings/fp/witness cites)."""
    lines = set()
    for coll in ("findings", "false_positive_confirmations"):
        for item in audit.get(coll, []):
            if item.get("file") not in (path, os.path.basename(path)) and coll == "findings":
                if item.get("file") != path:
                    continue
            lr = item.get("line_range") or item.get("at") or ""
            for n in re.findall(r"\d+", str(lr)):
                lines.add(int(n))
    return lines


def run(audit_path, db, min_cov):
    audit = json.load(open(audit_path))
    cx = sqlite3.connect(db)
    accounted_map = audit.get("spans_accounted", {})  # preferred, explicit
    print(f"read_coverage: {audit.get('batch')} @ {audit.get('version')}  "
          f"(mode={'explicit spans_accounted' if accounted_map else 'inferred from cites'})")
    print("=" * 70)
    worst = 1.0
    for frec in audit.get("files", []):
        path = frec["path"]
        spans = _spans(cx, path)
        if not spans:
            print(f"  {path}: no function spans in graph (module-level only)")
            continue
        total = len(spans)
        total_lines = sum(b - a + 1 for _q, a, b in spans)
        if accounted_map:
            accounted = [q for q, _a, _b in spans if q in accounted_map]
        else:
            cited = _cited_lines(audit, path)
            accounted = [q for q, a, b in spans if any(a <= n <= b for n in cited)]
        acc_lines = sum(b - a + 1 for q, a, b in spans if q in set(accounted))
        cov = len(accounted) / total
        worst = min(worst, cov)
        unacc = [q for q, _a, _b in spans if q not in set(accounted)]
        print(f"  {path}")
        print(f"    spans: {len(accounted)}/{total} accounted ({cov:.0%}) | "
              f"lines: {acc_lines}/{total_lines} in accounted spans "
              f"(file SLOC {frec.get('lines','?')})")
        if unacc:
            show = ", ".join(unacc[:8]) + (f" … +{len(unacc)-8} more" if len(unacc) > 8 else "")
            print(f"    UNACCOUNTED (read asserted, not evidenced): {show}")
    print("=" * 70)
    print(f"worst-file evidenced coverage: {worst:.0%}"
          + (f"  (below --min-coverage {min_cov:.0%})" if worst < min_cov else ""))
    if not accounted_map:
        print("  NOTE: inferred mode measures only what findings/witnesses CITE. To record")
        print("  true read coverage, emit spans_accounted{qualname: finding|assurance|read-benign}.")
    return 1 if worst < min_cov else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--min-coverage", type=float, default=0.0)
    a = ap.parse_args()
    raise SystemExit(run(a.audit, a.db, a.min_coverage))


if __name__ == "__main__":
    main()
