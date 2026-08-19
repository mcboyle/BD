#!/usr/bin/env python3
"""review_merge -- roll an AUDIT_<batch>.json into the review ledger + graph.

THE PILOT GAP: AUDIT_PLAN.md S6 step 1 ("Merge AUDIT_*.json into REVIEW_STATE.json
+ upsert the graph") and the per-session directive both require a merge step, but
the Session-1 pack shipped only seed_review_state.py -- no merge tool existed. This
is that tool (written during the CAP-01 pilot, per the directive's "fix the
schema/tooling now" mandate).

What it does (all idempotent):
  1. VALIDATE: every audited file's audit-sha256 must equal the ledger sha256
     (which seed_review_state pinned == live tree). A drift => refuse that file
     (an audit of a since-changed file is stale and must not flip it reviewed).
  2. LEDGER: flip each validated file unreviewed -> reviewed; stamp
     reviewed_in_batch / reviewed_version / reviewed_at / audit_of_record.
  3. FINDINGS: append new findings keyed by id (collision-safe; re-run no-ops).
  4. INVARIANTS: add new_invariants as status=UNGUARDED (collision-safe). A later
     serial fix-cut promotes them to GUARDED with a RED guard_test.
  5. GRAPH: upsert the L2 (purpose/data_flow/public_api/invariants/risk) into each
     module node's meta_json in KNOWLEDGE_GRAPH.db (mechanical fields already set
     by l0_extract; this adds the audit's irreducible L2).
  6. TOTALS: recompute reviewed/unreviewed/findings counts.

Gate (--check): every reviewed file still matches the tree sha (no post-merge
drift) AND every reviewed file carries an audit_of_record. rc!=0 on violation.
This is additive to seed_review_state --check (which catches reviewed-but-drifted).

Usage:
  review_merge.py --audit AUDIT_CAP-01_v3_66_532.json        # merge
  review_merge.py --check                                     # gate only
Stdlib only; offline; runs under plain python3.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import time

DEFAULT_ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.path.join(DEFAULT_ROOT, "review")
ART = os.path.join(REVIEW, "artifacts")
STATE = os.path.join(ART, "REVIEW_STATE.json")
INV = os.path.join(ART, "INVARIANTS.json")
DB = os.path.join(ART, "KNOWLEDGE_GRAPH.db")

_HERE = os.path.dirname(os.path.abspath(__file__))
# W2 marker: this module builds the reachability ledger at a successful land.
REACHABILITY_ON_LAND = True


def _load(p):
    with open(p) as f:
        return json.load(f)


def _save(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
    os.replace(tmp, p)


def _load_sibling(modname, filename):
    """Load a sibling tools/ script by file path (they are hyphen-free modules but
    we avoid depending on package import context)."""
    cand = os.path.join(_HERE, filename)
    if not os.path.exists(cand):
        return None
    spec = importlib.util.spec_from_file_location(modname, cand)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verify_audit(audit_path, root):
    """W1: run the verify_audit acceptance gate BEFORE any ledger write. Returns
    0 (ACCEPT) / non-zero (REJECT). Best-effort witness-suite + advanced-sidecar
    discovery by convention; verify_audit itself handles absence gracefully."""
    va = _load_sibling("verify_audit", "verify_audit.py")
    if va is None:
        print("  WARN verify_audit.py not found -- pre-merge gate SKIPPED")
        return 0
    audit = _load(audit_path)
    batch = audit.get("batch", "")
    d = os.path.dirname(audit_path) or "."
    # witness suite by convention: <batch-lower-no-dash>_witnesses.py, searched
    # in-tree (tools/audit/witnesses/) first, then the sandbox review/ fallback.
    wcand = None
    if batch:
        stem = batch.lower().replace("-", "")
        fname = f"{stem}_witnesses.py"
        for c in (os.path.join(_HERE, "audit", "witnesses", fname),
                  os.path.join(d, "witnesses", fname),
                  os.path.join(_HERE, "..", "witnesses", fname),
                  os.path.join(REVIEW, "witnesses", fname)):
            if os.path.exists(c):
                wcand = c
                break
    return va.verify(audit_path, wcand, root, None)


def _build_reachability():
    """W2: refresh REACHABILITY_DEFERRALS.json at a successful land so
    boundary-deferred findings re-map to their resolving batch without a manual
    build. Best-effort: a missing tool/ledger is a warning, not a merge failure
    (the merge already succeeded; reachability is a downstream index)."""
    rl = _load_sibling("reachability_ledger", "reachability_ledger.py")
    if rl is None:
        print("  WARN reachability_ledger.py not found -- deferrals not refreshed")
        return
    for fn in ("build", "rebuild", "main"):
        f = getattr(rl, fn, None)
        if callable(f):
            try:
                if fn == "build":
                    f()
                else:
                    f()
                print("  reachability: deferrals refreshed at land")
            except SystemExit:
                pass
            except Exception as e:  # noqa: BLE001
                print(f"  WARN reachability build failed (non-fatal): {e}")
            return


def merge(audit_path: str, root: str = DEFAULT_ROOT,
          verify: bool = True) -> int:
    # W1: acceptance gate BEFORE any state read/write. A REJECT aborts the whole
    # merge (rc=2, nothing written) unless verify is disabled.
    if verify:
        vrc = _verify_audit(audit_path, root)
        if vrc != 0:
            print(f"review_merge: ABORT -- verify_audit REJECTED {os.path.basename(audit_path)} "
                  f"(rc={vrc}); no ledger/graph write. Re-run with --no-verify to override.")
            return 2
    audit = _load(audit_path)
    batch = audit["batch"]
    version = audit["version"]
    state = _load(STATE)
    files = state["files"]

    refused, flipped, already = [], [], []
    for fa in audit["files"]:
        p = fa["path"]
        lg = files.get(p)
        if lg is None:
            refused.append((p, "not in ledger"))
            continue
        if fa["sha256"] != lg["sha256"]:
            # audit was done against a since-changed file -> stale, do not flip
            refused.append((p, "sha drift vs ledger"))
            continue
        if lg.get("status") == "reviewed" and lg.get("audit_of_record") == os.path.basename(audit_path):
            already.append(p)
            continue
        lg["status"] = "reviewed"
        lg["reviewed_in_batch"] = batch
        lg["reviewed_version"] = version
        lg["reviewed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        lg["audit_of_record"] = os.path.basename(audit_path)
        lg["rubric"] = fa.get("rubric", {})
        flipped.append(p)

    # findings (collision-safe by id)
    fnd = state.setdefault("findings", {})
    if isinstance(fnd, list):
        fnd = {f["id"]: f for f in fnd}
        state["findings"] = fnd
    new_f = 0
    for f in audit.get("findings", []):
        fid = f["id"]
        if fid not in fnd:
            rec = dict(f)
            rec.setdefault("status", "open")
            rec["batch"] = batch
            fnd[fid] = rec
            new_f += 1

    # totals
    statuses = [v.get("status") for v in files.values()]
    state["totals"] = {
        "production_files": len(files),
        "reviewed": sum(1 for s in statuses if s == "reviewed"),
        "unreviewed": sum(1 for s in statuses if s != "reviewed"),
        "findings_open": sum(1 for f in fnd.values() if f.get("status") == "open"),
        "findings_fixed": sum(1 for f in fnd.values() if f.get("status") == "fixed"),
        "seed_findings": state.get("totals", {}).get("seed_findings"),
    }
    _save(STATE, state)

    # invariants (add as UNGUARDED, collision-safe)
    inv_doc = _load(INV)
    invs = inv_doc["invariants"]
    new_i = 0
    for ni in audit.get("new_invariants", []):
        iid = ni["id"]
        if iid not in invs:
            invs[iid] = {
                "statement": ni["statement"],
                "at": ni.get("at", ""),
                "status": ni.get("status", "UNGUARDED"),
                "guard_test": ni.get("guard_test"),
                "added_by": batch,
            }
            new_i += 1
    _save(INV, inv_doc)

    # graph L2 upsert into module node meta_json
    upserts = 0
    if os.path.exists(DB):
        cx = sqlite3.connect(DB)
        try:
            for fa in audit["files"]:
                if fa["path"] not in flipped and fa["path"] not in already:
                    continue
                row = cx.execute(
                    "SELECT meta_json FROM nodes WHERE kind='module' AND path=?",
                    (fa["path"],)).fetchone()
                meta = {}
                if row and row[0]:
                    try:
                        meta = json.loads(row[0])
                    except Exception:
                        meta = {}
                meta["audit_l2"] = {
                    "purpose": fa.get("purpose"),
                    "data_flow": fa.get("data_flow"),
                    "public_api": fa.get("public_api", []),
                    "invariants": fa.get("invariants", []),
                    "risk": fa.get("risk"),
                    "batch": batch, "version": version,
                }
                cx.execute(
                    "UPDATE nodes SET meta_json=? WHERE kind='module' AND path=?",
                    (json.dumps(meta), fa["path"]))
                upserts += 1
            cx.commit()
        finally:
            cx.close()

    print(f"review_merge[{batch}]: flipped={len(flipped)} already={len(already)} "
          f"refused={len(refused)} +findings={new_f} +invariants={new_i} graph_upserts={upserts}")
    if refused:
        print("  REFUSED (not flipped):")
        for p, why in refused:
            print(f"    {p}: {why}")
    print(f"  ledger totals: reviewed={state['totals']['reviewed']} "
          f"unreviewed={state['totals']['unreviewed']} "
          f"findings_open={state['totals']['findings_open']}")

    # W2: refresh the reachability deferrals index at a successful land (a land
    # is any run that reached here past the verify gate; even a partial-refusal
    # run may have introduced boundary-deferred findings worth re-mapping).
    _build_reachability()

    return 1 if refused else 0


def check() -> int:
    """Post-merge consistency: every reviewed file matches tree sha + has an
    audit_of_record. (Tree-sha drift on a reviewed file is also caught by
    seed_review_state --check; this adds the audit_of_record completeness check.)"""
    import hashlib
    state = _load(STATE)
    root = DEFAULT_ROOT
    bad = []
    rev = 0
    for p, rec in state["files"].items():
        if rec.get("status") != "reviewed":
            continue
        rev += 1
        if not rec.get("audit_of_record"):
            bad.append((p, "reviewed but no audit_of_record"))
            continue
        fp = os.path.join(root, p)
        try:
            live = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        except OSError:
            bad.append((p, "file missing from tree"))
            continue
        if live != rec["sha256"]:
            bad.append((p, "reviewed file drifted vs tree"))
    print(f"review_merge --check: reviewed={rev} inconsistencies={len(bad)}")
    for p, why in bad:
        print(f"    {p}: {why}")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="tree root for the verify_audit sha check")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the pre-merge verify_audit acceptance gate (W1 override)")
    a = ap.parse_args()
    if a.check:
        raise SystemExit(check())
    if not a.audit:
        ap.error("--audit AUDIT_<batch>.json required (or --check)")
    raise SystemExit(merge(a.audit, root=a.root, verify=not a.no_verify))


if __name__ == "__main__":
    main()
