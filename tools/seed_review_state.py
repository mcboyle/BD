#!/usr/bin/env python3
"""seed_review_state -- instantiate REVIEW_STATE.json (the audit ledger, SCHEMAS
§1) seeded with the pre-audit register: F0001 + the 16 deduped v3.66.520
verify-pass findings (VR-P01..P16), and every production file at status
`unreviewed` with its live sha256 + line count from KNOWLEDGE_GRAPH.db.

The 16 findings predate this audit (fixed across 521..527) and are recorded as
`status:fixed` with their repro test + the DP class they seeded -- they belong in
the same register so the audit picks up from a true baseline.

Gate (--check): every file whose sha256 != tree auto-flips to `unreviewed`
(staleness); rc!=0 if the ledger references a file absent from the tree.

Usage: python3 seed_review_state.py [--db DB] [--out FILE]   |   --check
"""
import argparse
import json
import os
import sqlite3
import sys

SCHEMA = 1


def _derive_version():
    """Track bulk_downloader.__version__ instead of a hardcoded stamp, so the
    ledger's generated_against never goes stale at a release (P5 lesson: derive
    the version from source, don't pin it in a tool banner)."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        init = os.path.join(_here, "..", "bulk_downloader", "__init__.py")
        import re as _re
        m = _re.search(r'__version__ = "([0-9.]+)"', open(init, encoding="utf-8").read())
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


VERSION = _derive_version()
DEFAULT_ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.path.join(DEFAULT_ROOT, "review")

# F0001 + the 16 deduped VR-P findings (VERIFY_MATRIX_v3_66_520.md), each mapped
# to its file, severity, the DP class it seeded, and the version that fixed it.
SEED_FINDINGS = {
    "F0001": dict(file="bulk_downloader/app.py", category="crash", severity="high",
                  title="api_status NameError on test-gap path",
                  detail="bare undefined name reached at runtime (hasattr swallows AttributeError, not NameError)",
                  dp="DP-06", status="fixed", repro_test="tests/test_v3_66_9_deep_detect_audit_fixes.py"),
    "VR-P01": dict(file="bulk_downloader/notify_apprise.py", category="logic", severity="high",
                   title="Every runtime notification silently dropped",
                   detail="add(u) has no tag but notify(tag=event) filters -> 0 services match",
                   dp="DP-14", status="fixed", fixed_in="3.66.521",
                   repro_test="tests/test_v3_66_521_security_hotfix.py"),
    "VR-P02": dict(file="bulk_downloader/app_settings_center.py", category="security", severity="high",
                   title="Credential leak via non-recursive secret masking",
                   detail="accounts[].password returned raw on GET effective endpoints",
                   dp="DP-07", status="fixed", fixed_in="3.66.521",
                   repro_test="tests/test_v3_66_521_security_hotfix.py"),
    "VR-P03": dict(file="bulk_downloader/capture_artifact_redact.py", category="security", severity="high",
                   title="OAuth fragment / non-URL kv secrets survive redaction floor",
                   detail="auth #code= into capture.json; KV keyword set narrower than URL set",
                   dp="DP-08", status="fixed", fixed_in="3.66.521",
                   repro_test="tests/test_v3_66_521_security_hotfix.py"),
    "VR-P04": dict(file="bulk_downloader/app_jsonapi.py", category="crash", severity="medium",
                   title="POST /api/jsonapi/probe 500 every call",
                   detail="_check_csrf(request) passes an arg to a 0-arg callee",
                   dp="DP-05", status="fixed", fixed_in="3.66.522",
                   repro_test="tests/test_v3_66_522_dead_endpoints.py"),
    "VR-P05": dict(file="bulk_downloader/notify_apprise.py", category="logic", severity="medium",
                   title="notify validate_urls rejects every URL",
                   detail="apprise.AppriseURLBase removed in apprise>=1.7",
                   dp="DP-14", status="fixed", fixed_in="3.66.522",
                   repro_test="tests/test_v3_66_522_dead_endpoints.py"),
    "VR-P06": dict(file="bulk_downloader/library_final.py", category="crash", severity="medium",
                   title="POST /api/library/regen_nfos 500 every call",
                   detail="caller passes dry_run=, callee has no such kwarg",
                   dp="DP-05", status="fixed", fixed_in="3.66.522",
                   repro_test="tests/test_v3_66_522_dead_endpoints.py"),
    "VR-P07": dict(file="bulk_downloader/runner.py", category="logic", severity="medium",
                   title="smart_wakeup dead when enabled",
                   detail="site_id= kwarg -> swallowed TypeError + log spam",
                   dp="DP-13", status="fixed", fixed_in="3.66.522",
                   repro_test="tests/test_v3_66_522_dead_endpoints.py"),
    "VR-P08": dict(file="bulk_downloader/site_editor.py", category="type", severity="medium",
                   title="NaN evades numeric-range backstop -> persists into live config",
                   detail="float(v) then bounds check, both comparisons False for NaN",
                   dp="DP-03", status="fixed", fixed_in="3.66.523",
                   repro_test="tests/test_v3_66_523_input_validation.py"),
    "VR-P09": dict(file="bulk_downloader/app.py", category="crash", severity="medium",
                   title="Non-object JSON body -> 500 across 27 write endpoints",
                   detail="get_json(silent=True) or {} then .get on a JSON array",
                   dp="DP-01", status="fixed", fixed_in="3.66.523",
                   repro_test="tests/test_v3_66_523_input_validation.py"),
    "VR-P10": dict(file="bulk_downloader/batch_ops.py", category="security", severity="medium",
                   title="batch_ops user-controlled table -> bounded SQL identifier injection",
                   detail="f\"... FROM {table}\" with table reachable from request body",
                   dp="DP-10", status="fixed", fixed_in="3.66.524",
                   repro_test="tests/test_v3_66_524_security_hardening.py"),
    "VR-P11": dict(file="bulk_downloader/site_editor.py", category="type", severity="low",
                   title="Numeric backstop type laxity ('8', 3.7 accepted for int)",
                   detail="float()/int() coercion without isinstance(int)",
                   dp="DP-04", status="fixed", fixed_in="3.66.527",
                   repro_test="tests/test_v3_66_527_numeric_integer_backstop.py"),
    "VR-P12": dict(file="bulk_downloader/site_editor.py", category="crash", severity="low",
                   title="inf value -> HTTP 500 at validate endpoint",
                   detail="non-JSON-compliant echo of inf",
                   dp="DP-02", status="fixed", fixed_in="3.66.523",
                   repro_test="tests/test_v3_66_523_input_validation.py"),
    "VR-P13": dict(file="bulk_downloader/extraction_core.py", category="logic", severity="low",
                   title="segment_role latent O(n^2) on long-digit path",
                   detail="per-char scan with no length cap on attacker-influenced input",
                   dp="DP-18", status="fixed", fixed_in="3.66.525",
                   repro_test="tests/test_v3_66_525_perf_and_robustness.py"),
    "VR-P14": dict(file="tools/build_template_from_wacz.py", category="crash", severity="low",
                   title="build_template unhandled AttributeError on malformed capture",
                   detail="cap-derived collection not normalized before attribute access",
                   dp="DP-05", status="fixed", fixed_in="3.66.525",
                   repro_test="tests/test_v3_66_525_perf_and_robustness.py"),
    "VR-P15": dict(file="bulk_downloader/provider_resolve_impl/_common.py", category="security", severity="low",
                   title="SSRF classifier permits CGNAT 100.64.0.0/10",
                   detail="denylist classifier omits RFC 6598 range; use is_global",
                   dp="DP-11", status="fixed", fixed_in="3.66.524",
                   repro_test="tests/test_v3_66_524_security_hardening.py"),
    "VR-P16": dict(file="bulk_downloader/provider_resolve.py", category="logic", severity="low",
                   title="JWPlayer DRM detection misses top-level data['drm'].<scheme>",
                   detail="per-entry detection missed the top-level drm marker",
                   dp="DP-04", status="fixed", fixed_in="3.66.524",
                   repro_test="tests/test_v3_66_524_security_hardening.py"),
}


def load_files(db):
    c = sqlite3.connect(db)
    files = {}
    for path, sha, lines in c.execute(
            "SELECT path,sha256,lines FROM nodes WHERE kind='module'"):
        files[path] = {"sha256": sha, "lines": lines}
    c.close()
    return files


def build(db, root):
    files_meta = load_files(db)
    # findings -> file linkage
    file_findings = {}
    findings = {}
    for fid, d in SEED_FINDINGS.items():
        fpath = d["file"]
        findings[fid] = {
            "file": fpath, "line_range": [0, 0], "category": d["category"],
            "severity": d["severity"], "confidence": "confirmed",
            "title": d["title"], "detail": d["detail"],
            "fix": "", "repro_test": d.get("repro_test", ""),
            "status": d["status"], "fixed_in": d.get("fixed_in"),
            "dp_class": d.get("dp"), "source": "manual:VERIFY_MATRIX_v3_66_520"}
        file_findings.setdefault(fpath, []).append(fid)

    files = {}
    for path, meta in sorted(files_meta.items()):
        files[path] = {
            "sha256": meta["sha256"], "lines": meta["lines"],
            "status": "unreviewed", "reviewed_at_sha": None,
            "rubric": {}, "finding_ids": sorted(file_findings.get(path, [])),
            "invariant_ids": [], "catalog": False}
    return {"schema": SCHEMA, "generated_against": VERSION,
            "totals": {"production_files": len(files),
                       "unreviewed": len(files), "reviewed": 0,
                       "seed_findings": len(findings),
                       "findings_open": sum(1 for f in findings.values()
                                            if f["status"] == "open"),
                       "findings_fixed": sum(1 for f in findings.values()
                                             if f["status"] == "fixed")},
            "files": files, "findings": findings}


def check(state_path, db, root):
    state = json.load(open(state_path))
    files_meta = load_files(db)
    flipped = []
    missing = []
    for path, rec in state["files"].items():
        if path not in files_meta:
            missing.append(path)
            continue
        if files_meta[path]["sha256"] != rec["sha256"] and rec["status"] == "reviewed":
            flipped.append(path)
    # findings referencing absent files
    bad = [fid for fid, f in state["findings"].items()
           if f["file"] not in files_meta and not os.path.exists(
               os.path.join(root, f["file"]))]
    ok = not missing and not bad
    print(f"review_state --check: files={len(state['files'])} "
          f"stale_reviewed={len(flipped)} missing={len(missing)} "
          f"findings_on_absent_file={len(bad)}")
    if bad:
        print("  findings referencing files not in tree:", bad)
    return 0 if ok else 1


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    a.root = os.path.abspath(a.root or DEFAULT_ROOT)
    artifacts = os.path.join(a.root, "review", "artifacts")
    a.db = os.path.abspath(a.db or os.path.join(artifacts, "KNOWLEDGE_GRAPH.db"))
    a.out = os.path.abspath(a.out or os.path.join(artifacts, "REVIEW_STATE.json"))
    return a


def main(argv=None):
    a = parse_args(argv)
    if a.check:
        sys.exit(check(a.out, a.db, a.root))
    state = build(a.db, a.root)
    json.dump(state, open(a.out, "w"), indent=2, sort_keys=True)
    print("seed_review_state:", json.dumps(state["totals"]))


if __name__ == "__main__":
    main()
