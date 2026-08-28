#!/usr/bin/env python3
"""verify_audit -- the pre-merge acceptance gate for a parallel audit deliverable.

Mechanizes the by-hand check a reviewer would otherwise run on every parallel
session's output (which does not scale). Given an AUDIT_<BATCH>.json (+ its witness
suite), it refuses the deliverable unless ALL of:

  (1) SCHEMA   -- required top-level + per-file + per-finding fields present;
                  files and findings are both non-empty measured populations.
  (2) SHA      -- every files[].sha256 matches the live tree (they audited the
                  pinned source, not a drifted copy).  [review_merge also checks
                  this at merge; verify_audit checks it BEFORE, with the witnesses.]
  (3) WITNESS  -- the batch's witness suite is present, runs, and EVERY witness
                  demonstrates (ok=True), and every finding in the JSON has a
                  matching witness in the suite.
  (4) EMIT     -- audit_emit_gate passes (no falsifiable claim without a witness).
  (5) COMPLETE -- for any finding carrying a `signature` regex, the cited sites
                  equal ALL matches in the file (catches the F-RUN01-03 :1041
                  undercount class). Findings citing a line_range with no signature
                  are WARNED (author should add one), not failed.

Usage:
  bd python3 verify_audit.py --audit AUDIT_RUN-01_v3_66_532.json \
      [--witnesses witnesses/run01_witnesses.py] [--root <repository>] \
      [--signatures sigs.json]   # {finding_id: regex} sidecar for (5)
rc!=0 on any hard failure (schema/sha/witness/emit or a signature mismatch).
Runs under `bd` (the witness suite imports bulk_downloader).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.environ.get("BD_WORK", os.path.dirname(HERE))
REQ_TOP = ["batch", "version", "files", "findings",
           "guard_touch", "tracker_write", "tree_reverified_byte_identical"]
REQ_FILE = ["path", "sha256", "rubric"]
REQ_FIND = ["id", "file", "severity", "witness"]


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _run_suite(wpath):
    spec = importlib.util.spec_from_file_location(os.path.basename(wpath)[:-3], wpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = []
    for e in getattr(mod, "RESULTS", []):
        if isinstance(e, dict):
            out.append((e.get("id"), e.get("ok"), e.get("kind"), e.get("detail", "")))
        else:
            cid, ok, d = e
            out.append((cid, ok, "finding" if str(cid).startswith("F-") else "claim", d))
    return out


def verify(audit_path, wpath, root, sig_path, adv_path=None):
    a = json.load(open(audit_path))
    fails, warns = [], []

    # (1) SCHEMA
    for k in REQ_TOP:
        if k not in a:
            fails.append(f"schema: missing top-level '{k}'")
    files = a.get("files")
    findings = a.get("findings")
    if not isinstance(files, list) or not files:
        fails.append("schema: 'files' must be a non-empty list")
    if not isinstance(findings, list) or not findings:
        fails.append("schema: 'findings' must be a non-empty list")
    files = files if isinstance(files, list) else []
    findings = findings if isinstance(findings, list) else []
    for frec in files:
        for k in REQ_FILE:
            if k not in frec:
                fails.append(f"schema: file {frec.get('path','?')} missing '{k}'")
    for fd in findings:
        for k in REQ_FIND:
            if k not in fd:
                fails.append(f"schema: finding {fd.get('id','?')} missing '{k}'")
        if "repro_test" not in fd:
            warns.append(f"finding {fd.get('id')} has no repro_test (recommended)")

    # (2) SHA
    for frec in files:
        p = os.path.join(root, frec["path"])
        if not os.path.exists(p):
            fails.append(f"sha: {frec['path']} not in tree at {root}")
            continue
        live = _sha(p)
        if live != frec.get("sha256"):
            fails.append(f"sha: {frec['path']} claimed {frec.get('sha256','')[:12]} "
                         f"!= live {live[:12]} (audited a drifted copy)")

    # (3) WITNESS
    witness_ids = set()
    if wpath and os.path.exists(wpath):
        results = _run_suite(wpath)
        not_shown = [(cid, d) for cid, ok, _k, d in results if not ok]
        witness_ids = {cid for cid, _o, _k, _d in results}
        if not_shown:
            for cid, d in not_shown:
                fails.append(f"witness: {cid} did NOT demonstrate -- {d[:70]}")
        # every finding must have a witness in the suite
        for fd in findings:
            wref = str(fd.get("witness", ""))
            fid = fd.get("id")
            if fid not in witness_ids and wref not in witness_ids:
                fails.append(f"finding {fid} witness ref '{wref[:40]}' not matched "
                             f"to a suite witness id")
    else:
        if wpath:
            fails.append(f"witness: suite not found: {wpath}")
        else:
            fails.append("witness: no witness suite supplied -- (3) cannot be measured")

    # (4) EMIT -- run audit_emit_gate over BOTH the audit findings/invariants AND
    # the advanced sidecar (constraints/exceptions/drift-beliefs = claim classes
    # 1,2,5). P6 fix: the sidecar path is passed EXPLICITLY by the caller; if not
    # given, try every real naming convention instead of a single guess (the old
    # glue guessed only <audit>_advanced.json and silently skipped the real
    # <BATCH>_advanced.json, so constraints were never checked on real artifacts).
    emit = os.path.join(HERE, "audit_emit_gate.py")
    args = [sys.executable, emit, "--audit", audit_path]
    d = os.path.dirname(audit_path) or "."
    base = os.path.basename(audit_path)
    batch = a.get("batch", "")
    version = a.get("version", "")
    cands = []
    if adv_path:                                   # explicit wins
        cands.append(adv_path)
    # real conventions, most-specific first
    cands.append(audit_path.replace(".json", "_advanced.json"))          # AUDIT_<b>_v3_66_<n>_advanced.json
    if batch:
        cands.append(os.path.join(d, f"{batch}_advanced.json"))          # <BATCH>_advanced.json  (real pilot naming)
        vshort = version.replace("3.66.", "") if version else ""
        if vshort:
            cands.append(os.path.join(d, f"{batch}_{vshort}_advanced.json"))
    sidecar = next((c for c in cands if c and os.path.exists(c)), None)
    if sidecar:
        args += ["--advanced", sidecar]
    else:
        warns.append("no advanced sidecar found (constraints/exceptions/drift-beliefs "
                     "not emit-checked); pass --advanced or name it <BATCH>_advanced.json")
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        fails.append(f"emit: audit_emit_gate FAILED\n    " + r.stdout.strip().replace("\n", "\n    "))

    # (5) COMPLETE
    sigs = json.load(open(sig_path)) if sig_path and os.path.exists(sig_path) else {}
    # allow inline per-finding 'signature' too
    for fd in findings:
        sig = fd.get("signature") or sigs.get(fd.get("id"))
        lr = fd.get("line_range", "")
        cited = set(int(x) for x in re.findall(r"\d+", lr)) if lr else set()
        if not sig:
            if lr:
                warns.append(f"finding {fd.get('id')} cites lines but has no signature "
                             f"(completeness unverifiable)")
            continue
        p = os.path.join(root, fd["file"])
        if not os.path.exists(p):
            continue
        matches = set()
        for i, line in enumerate(open(p), 1):
            if re.search(sig, line):
                matches.add(i)
        uncited = sorted(matches - cited)
        if uncited:
            fails.append(f"complete: {fd.get('id')} signature /{sig}/ matches lines "
                         f"{sorted(matches)} but finding cites {sorted(cited)} "
                         f"-> UNCITED {uncited}")

    # report
    print(f"verify_audit: {a.get('batch')} @ {a.get('version')}")
    print("=" * 64)
    for w in warns:
        print(f"  WARN  {w}")
    for f in fails:
        print(f"  FAIL  {f}")
    print("=" * 64)
    if fails:
        print(f"REJECT -- {len(fails)} hard failure(s); do not merge")
        return 1
    print(f"ACCEPT -- schema+sha+witness+emit+completeness clean "
          f"({len(warns)} advisory warning(s))")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--witnesses")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--signatures")
    ap.add_argument("--advanced",
                    help="explicit advanced sidecar (constraints/exceptions/beliefs); "
                         "else discovered by convention")
    a = ap.parse_args()
    raise SystemExit(verify(a.audit, a.witnesses, a.root, a.signatures, a.advanced))


if __name__ == "__main__":
    main()
