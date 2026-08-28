#!/usr/bin/env python3
"""bd-scan -- run the L0 battery across the production tree and normalize every
tool's output into a single findings ledger (SCAN_FINDINGS.json).

Battery (offline, via the ~/rev throwaway venv):
  * defect_patterns.py  -- the project-native linter (DP-01..18)
  * bandit              -- B-rule security scan (B6xx subprocess/sql, etc.)
  * vulture             -- dead-code (>=90 confidence only, per calibration)
radon CC is consumed by risk_score (not re-run here). semgrep is heavy; gated
behind --semgrep.

Each finding: {file, line, source, code, severity, title}. Diff-aware hook
(--changed-only) is a stub for the cut loop. stdlib + subprocess; deterministic
(sorted).

Usage: python3 bd-scan.py [--root TREE] [--venv ~/rev] [--out FILE] [--semgrep]

Every default analyzer must produce a parseable measurement. A launch, exit, or
ingest failure is recorded as status=UNKNOWN and exits 2; zero findings is CLEAN
only when all three default analyzers are status=MEASURED.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_ROOT = os.environ.get(
    "BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
REVIEW_ROOT = os.path.join(DEFAULT_ROOT, "review")

PROD_DIRS = ["bulk_downloader", "tools"]


class AnalyzerUnavailable(RuntimeError):
    """A selected analyzer produced no trustworthy measurement."""


def run(cmd, timeout=600):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def _require_analyzer_run(source, rc, out, err, accepted=(0,)):
    if rc not in accepted:
        detail = (err or out or "no diagnostic output").strip().replace("\n", " ")
        raise AnalyzerUnavailable(
            f"{source} did not complete (exit {rc}): {detail[:240]}"
        )
    return out


def from_defect_patterns(root, py):
    fd, report = tempfile.mkstemp(prefix="bd-scan-defect-patterns-", suffix=".json")
    os.close(fd)
    os.unlink(report)
    rc, out, err = run([py, os.path.join(os.path.dirname(__file__), "defect_patterns.py"),
                        "--scan", root, "--out", report])
    findings = []
    try:
        _require_analyzer_run("defect_patterns", rc, out, err)
        with open(report) as stream:
            d = json.load(stream)
        if not isinstance(d, dict) or not isinstance(d.get("findings"), dict):
            raise ValueError("report has no findings mapping")
        for fl, fnd in d["findings"].items():
            for f in fnd:
                findings.append({"file": fl, "line": f["line"], "source": "defect_patterns",
                                 "code": f["dp"], "severity": f["severity"],
                                 "precision": f["precision"], "title": f["title"]})
    except Exception as e:
        if isinstance(e, AnalyzerUnavailable):
            raise
        raise AnalyzerUnavailable(f"defect_patterns report is unreadable: {e}") from e
    finally:
        try:
            os.unlink(report)
        except FileNotFoundError:
            pass
    return findings


def from_bandit(root, venv):
    bandit = os.path.join(venv, "bin", "bandit")
    targets = [os.path.join(root, d) for d in PROD_DIRS]
    rc, out, err = run([bandit, "-r", *targets, "-f", "json", "-q",
                        "--exclude", "*/node_modules/*,*/__pycache__/*"], timeout=900)
    findings = []
    try:
        _require_analyzer_run("bandit", rc, out, err, accepted=(0, 1))
        d = json.loads(out)
        if not isinstance(d, dict) or not isinstance(d.get("results"), list):
            raise ValueError("report has no results list")
        for r in d["results"]:
            rel = os.path.relpath(r["filename"], root)
            findings.append({"file": rel, "line": r.get("line_number", 0),
                             "source": "bandit", "code": r.get("test_id", ""),
                             "severity": (r.get("issue_severity") or "low").lower(),
                             "title": r.get("issue_text", "")[:140]})
    except Exception as e:
        if isinstance(e, AnalyzerUnavailable):
            raise
        raise AnalyzerUnavailable(f"bandit report is unreadable: {e}") from e
    return findings


def from_vulture(root, venv):
    vulture = os.path.join(venv, "bin", "vulture")
    targets = [os.path.join(root, d) for d in PROD_DIRS]
    # min-confidence 90 to drop framework/dynamic-dispatch noise (calibration)
    rc, out, err = run([vulture, *targets, "--min-confidence", "90"], timeout=300)
    _require_analyzer_run("vulture", rc, out, err)
    findings = []
    for ln in (out or "").splitlines():
        # format: path:line: unused X 'name' (NN% confidence)
        if ":" not in ln:
            raise AnalyzerUnavailable(f"vulture report is unreadable: {ln[:120]}")
        try:
            fpath, lineno, rest = ln.split(":", 2)
            rel = os.path.relpath(fpath, root)
            findings.append({"file": rel, "line": int(lineno), "source": "vulture",
                             "code": "dead-code", "severity": "low",
                             "title": rest.strip()[:140]})
        except ValueError as e:
            raise AnalyzerUnavailable(f"vulture report is unreadable: {ln[:120]}") from e
    return findings


def from_semgrep(root, venv):
    semgrep = os.path.join(venv, "bin", "semgrep")
    rc, out, err = run([semgrep, "scan", "--config", "auto", "--json", "-q",
                        os.path.join(root, "bulk_downloader")], timeout=1800)
    findings = []
    try:
        d = json.loads(out or "{}")
        for r in d.get("results", []):
            rel = os.path.relpath(r["path"], root)
            findings.append({"file": rel, "line": r.get("start", {}).get("line", 0),
                             "source": "semgrep", "code": r.get("check_id", "")[:60],
                             "severity": (r.get("extra", {}).get("severity") or "info").lower(),
                             "title": (r.get("extra", {}).get("message") or "")[:140]})
    except Exception as e:
        print(f"  semgrep ingest error: {e}", file=sys.stderr)
    return findings


def from_semgrep_ts(root, venv):
    """TS/TSX-aware scan of frontend/src (E @534). The knowledge graph is
    grep-level for frontend (no offline TS compiler); this closes the gap by
    pointing semgrep at frontend/src with TS-aware registry configs (p/typescript
    for language rules + p/react for the component idioms) instead of the auto
    Python config. Findings land under source='semgrep-ts' so they are distinct
    from the Python semgrep pass in by_source counts."""
    semgrep = os.path.join(venv, "bin", "semgrep")
    fe = os.path.join(root, "frontend/src")
    if not os.path.isdir(fe):
        return []
    rc, out, err = run(
        [semgrep, "scan", "--config", "p/typescript", "--config", "p/react",
         "--json", "-q", "--exclude", "node_modules", fe], timeout=1800)
    findings = []
    try:
        d = json.loads(out or "{}")
        for r in d.get("results", []):
            rel = os.path.relpath(r["path"], root)
            findings.append({"file": rel, "line": r.get("start", {}).get("line", 0),
                             "source": "semgrep-ts", "code": r.get("check_id", "")[:60],
                             "severity": (r.get("extra", {}).get("severity") or "info").lower(),
                             "title": (r.get("extra", {}).get("message") or "")[:140]})
    except Exception as e:
        print(f"  semgrep-ts ingest error: {e}", file=sys.stderr)
    return findings


def from_jscpd(root, venv):
    """Copy-paste / clone detection across frontend/src + tools (P2 @535). jscpd
    finds duplicated blocks semgrep/bandit do not -- a distinct defect class
    (drift risk: a fix applied to one clone but not its twin). Reads the jscpd
    JSON report and emits one finding per clone instance, source='jscpd'. The
    binary lives under the bd-rev venv's npm prefix; best-effort (a missing jscpd
    is a warn, not a failure)."""
    jscpd = os.path.join(venv, "npm", "bin", "jscpd")
    if not os.path.exists(jscpd):
        jscpd = shutil.which("jscpd")
    if not jscpd:
        print("  jscpd not found (run bd-rev to install) -- skipped", file=sys.stderr)
        return []
    targets = [os.path.join(root, d) for d in ("frontend/src", "tools")
               if os.path.isdir(os.path.join(root, d))]
    rpt = "/tmp/_jscpd_report"
    os.makedirs(rpt, exist_ok=True)
    rc, out, err = run(
        [jscpd, *targets, "--reporters", "json", "--output", rpt,
         "--ignore", "**/node_modules/**", "--silent"], timeout=900)
    findings = []
    jf = os.path.join(rpt, "jscpd-report.json")
    try:
        if os.path.exists(jf):
            d = json.loads(open(jf).read())
            for dup in d.get("duplicates", []):
                first = dup.get("firstFile", {})
                fp = first.get("name", "")
                rel = os.path.relpath(fp, root) if fp else "?"
                ln = first.get("start", 0)
                secondf = dup.get("secondFile", {}).get("name", "")
                secrel = os.path.relpath(secondf, root) if secondf else "?"
                findings.append({
                    "file": rel, "line": ln, "source": "jscpd",
                    "code": f"clone:{dup.get('lines', 0)}L",
                    "severity": "low",
                    "title": f"duplicated block ({dup.get('lines', 0)} lines) also in {secrel}"[:140]})
    except Exception as e:
        print(f"  jscpd ingest error: {e}", file=sys.stderr)
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--venv", default=os.path.expanduser("~/rev"))
    ap.add_argument("--out", default=os.path.join(REVIEW_ROOT, "artifacts", "SCAN_FINDINGS.json"))
    ap.add_argument("--semgrep", action="store_true",
                    help="run semgrep over the Python tree (heavy)")
    ap.add_argument("--ts", action="store_true",
                    help="run the TS/TSX-aware semgrep scan over frontend/src "
                         "(p/typescript + p/react); implied by --semgrep")
    ap.add_argument("--jscpd", action="store_true",
                    help="run jscpd copy-paste/clone detection over frontend/src + "
                         "tools (P2); the jscpd binary comes from bd-rev")
    a = ap.parse_args()
    py = os.path.join(a.venv, "bin", "python")

    all_f = []
    analyzers = {}
    unknown_reasons = {}
    default_collectors = [
        ("defect_patterns", lambda: from_defect_patterns(a.root, py)),
        ("bandit", lambda: from_bandit(a.root, a.venv)),
        ("vulture", lambda: from_vulture(a.root, a.venv)),
    ]
    for source, collect in default_collectors:
        print(f"bd-scan: {source} ...")
        try:
            measured = collect()
        except AnalyzerUnavailable as exc:
            analyzers[source] = {"status": "UNKNOWN", "findings": 0}
            unknown_reasons[source] = str(exc)
            print(f"  UNKNOWN {source}: {exc}", file=sys.stderr)
        else:
            analyzers[source] = {"status": "MEASURED", "findings": len(measured)}
            all_f += measured
    if a.semgrep:
        print("bd-scan: semgrep (python) ...")
        all_f += from_semgrep(a.root, a.venv)
    if a.semgrep or a.ts:
        print("bd-scan: semgrep-ts (frontend/src) ...")
        all_f += from_semgrep_ts(a.root, a.venv)
    if a.jscpd:
        print("bd-scan: jscpd (clones) ...")
        all_f += from_jscpd(a.root, a.venv)

    all_f.sort(key=lambda f: (f["file"], f["line"], f["source"], f["code"]))
    by_source = {}
    by_sev = {}
    for f in all_f:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    status = "UNKNOWN" if unknown_reasons else ("CLEAN" if not all_f else "FINDINGS")
    payload = {"schema": 1, "root": a.root, "status": status,
               "analyzers": dict(sorted(analyzers.items())),
               "unknown_reasons": dict(sorted(unknown_reasons.items())),
               "total": len(all_f),
               "by_source": dict(sorted(by_source.items())),
               "by_severity": dict(sorted(by_sev.items())), "findings": all_f}
    json.dump(payload, open(a.out, "w"), indent=2, sort_keys=True)
    if unknown_reasons:
        print(f"\nbd-scan: UNKNOWN -- {len(unknown_reasons)} analyzer(s) unavailable "
              f"-> {a.out}")
    elif not all_f:
        print(f"\nbd-scan: CLEAN -- 0 findings -> {a.out}")
    else:
        print(f"\nbd-scan: FINDINGS -- {len(all_f)} findings -> {a.out}")
    print("  by source  :", json.dumps(dict(sorted(by_source.items()))))
    print("  by severity:", json.dumps(dict(sorted(by_sev.items()))))
    return 2 if unknown_reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
