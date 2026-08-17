#!/usr/bin/env python3
"""test_runtime_report.py — test health report (G). Read-only except the report.
Composes test_inventory + test_coverage_catalog + the exact skip-identity baseline, and
parses a SUMMARY.txt (from `run_tests.py --summary`) if one is present — it does
NOT run the suite itself. Writes reports/test_health.md.
CLI: --root, --summary PATH, --outdir
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_inventory as TI  # type: ignore
import test_coverage_catalog as TC  # type: ignore

_SUM = re.compile(r"Total:\s*(\d+)\s*\|\s*Passed:\s*(\d+)\s*\|\s*Failed:\s*(\d+)\s*\|\s*Skipped:\s*(\d+)")


class ReportEvidenceError(ValueError):
    """The report cannot truthfully render its skip authority."""


def _skip_baseline(root):
    p = os.path.join(root, "tests", "SKIP_BASELINE.json")
    try:
        payload = json.loads(Path(p).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReportEvidenceError(f"skip authority is unreadable: {p}") from exc
    except (ValueError, AttributeError) as exc:
        raise ReportEvidenceError(f"skip authority is malformed: {p}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "bd-skip-baseline/1":
        raise ReportEvidenceError(f"skip authority has the wrong schema: {p}")
    rows = payload.get("skips")
    if not isinstance(rows, list):
        raise ReportEvidenceError(f"skip authority skips must be a list: {p}")
    return len(rows)


def build(root=".", summary=None):
    inv = TI.inventory(root)
    cov = TC.catalog(root)
    base = _skip_baseline(root)
    run = None
    if summary and os.path.isfile(summary):
        m = _SUM.search(open(summary).read())
        if m:
            run = dict(zip(("total", "passed", "failed", "skipped"),
                           map(int, m.groups())))
    return {"inventory": inv, "coverage": cov, "skip_baseline": base, "last_run": run}


def _md(d):
    inv, cov = d["inventory"], d["coverage"]
    L = ["# Test health", "",
         f"- test files: **{inv['test_files']}** · total test functions: **{inv['total_tests']}**",
         f"- pinned skip baseline: {d['skip_baseline']}",
         f"- source modules with a matching test file: "
         f"{cov['with_test_match']}/{cov['modules']} "
         f"({len(cov['gap_candidates'])} gap candidates)", ""]
    if d["last_run"]:
        r = d["last_run"]
        L += [f"- last SUMMARY.txt: total {r['total']}, passed {r['passed']}, "
              f"failed {r['failed']}, skipped {r['skipped']}", ""]
    else:
        L += ["- last run: no SUMMARY.txt provided "
              "(`run_tests.py --summary` then pass --summary)", ""]
    L += ["## Largest test files", ""]
    for f in inv["largest"]:
        L.append(f"- `{f['file']}`: {f['tests']} tests")
    L += ["", "## Coverage gap candidates (no name-matched test file)", ""]
    for g in cov["gap_candidates"][:40]:
        L.append(f"- `{g}`")
    L += ["", "_Note: coverage is a name-token heuristic, not execution coverage._"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--summary")
    ap.add_argument("--outdir", default="reports")
    a = ap.parse_args(argv)
    d = build(a.root, a.summary)
    p = _RC.write_report(a.outdir, "test_health.md", _md(d))
    print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
