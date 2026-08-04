#!/usr/bin/env python3
"""bd-audit-gate -- the composite code-intelligence gate.

Runs the audit sub-gates and reports ONE three-state verdict:
  1. defect_patterns --check   (the project-native linter validates on its corpus)
  2. invariants --check        (no phantom guards; reports UNGUARDED invariants)
  3. review_state --check      (ledger staleness: reviewed files whose sha drifted
                                auto-flip to unreviewed; no finding on an absent file)
  4. witnesses                 (the KB announces its own lies -- a red claim-witness
                                means a belief no longer matches behavior)
  5. emit gate                 (no falsifiable claim without a witness)
  6. constraint topology       (optional; only with advanced sidecars present)
  7. consumer agreement        (optional; only with a CONTRACTS.json present)

Intended to run each cut alongside the release band. Extensible: add the
fuzz_harness/differential_oracle replays here as they land. stdlib + subprocess.

EXIT CODES -- the three-state contract (v3.66.855):
  0  every core sub-gate RAN and PASSED
  1  at least one sub-gate RAN and FAILED  (failures dominate)
  2  CANNOT-EVALUATE -- a core sub-gate's script, artifact or corpus is ABSENT,
     so a composite PASS cannot be asserted. A gate that cannot see its subject
     must never report OK (CLAUDE.md s0). Before v3.66.855 an absent sub-tool
     produced a `No such file` crash scored as FAIL, which reads as a detector
     regression rather than as a missing input.

PATHS (v3.66.855). The sub-tools are tracked at `<checkout>/tools/`, NOT beside
this script in `toolchain/bin/` -- with `HERE` as the only candidate every core
sub-gate crashed with `No such file or directory` on a clean checkout. They are
resolved `<root>/tools/` first, `HERE` second (the flat-layout fallback), and
--root/--artifacts/--corpus default inside the checkout via
`bdtools_sec.DEFAULT_WORK`, which walks up to `bulk_downloader/__init__.py` and
therefore follows a relocated checkout.

Usage: bd-audit-gate.py [--root TREE] [--artifacts DIR] [--corpus DIR] [--json]
       bd-audit-gate.py --selftest
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import bdtools_sec as sec  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

OK, FAIL, UNEVAL = "ok", "fail", "unevaluated"


def run(name, cmd):
    print(f"\n=== {name} ===")
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode


def resolve(name, root):
    """Locate a sub-tool. <root>/tools/ is where they are tracked; HERE is the
    flat-layout fallback (the sandbox shipped them beside this script)."""
    for d in (os.path.join(root, "tools"), HERE):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def verdict(results):
    """Fold per-sub-gate states into the process exit code. Pure, so --selftest
    can prove BOTH directions: it must fail on a real failure AND refuse on a
    blind one, and it must NOT refuse when every core gate really ran."""
    states = [s for _n, s, _d in results]
    if FAIL in states:
        return 1
    if UNEVAL in states or not states:
        return 2
    return 0


def _corpus_ready(corpus):
    """The defect_patterns corpus is a set of DP-*_{vuln,fixed}.py fixtures. An
    absent or empty dir is CANNOT-EVALUATE, never a detector failure."""
    if not os.path.isdir(corpus):
        return False, "corpus dir absent: %s" % corpus
    fixtures = [f for f in os.listdir(corpus)
                if f.endswith("_vuln.py") or f.endswith("_fixed.py")]
    if not fixtures:
        return False, "corpus dir holds no *_vuln.py/*_fixed.py fixtures: %s" % corpus
    return True, "%d fixtures" % len(fixtures)


def collect(a):
    """Run every sub-gate that can be evaluated; return [(name, state, detail)]."""
    py = sys.executable
    root, art, corpus = a.root, a.artifacts, a.corpus
    results = []

    def record(name, state, detail=""):
        results.append((name, state, detail))
        if state == UNEVAL:
            print(f"\n=== {name} ===")
            print(f"CANNOT-EVALUATE: {detail}")
            sys.stderr.write(f"CANNOT-EVALUATE {name}: {detail}\n")

    # --- 1. defect_patterns --check ------------------------------------------
    dp = resolve("defect_patterns.py", root)
    ready, why = _corpus_ready(corpus)
    if dp is None:
        record("defect_patterns", UNEVAL, "defect_patterns.py not found under "
               f"{os.path.join(root, 'tools')} or {HERE}")
    elif not ready:
        record("defect_patterns", UNEVAL, why)
    else:
        rc = run("defect_patterns --check", [py, dp, "--check", "--corpus", corpus])
        record("defect_patterns", OK if rc == 0 else FAIL, f"rc={rc} ({why})")

    # --- 2. invariants --check ------------------------------------------------
    inv = resolve("invariants.py", root)
    inv_json = os.path.join(art, "INVARIANTS.json")
    if inv is None:
        record("invariants", UNEVAL, "invariants.py not found")
    elif not os.path.isfile(inv_json):
        record("invariants", UNEVAL,
               f"{inv_json} absent -- seed it first: {inv} --root {root} "
               f"--out {inv_json}")
    else:
        rc = run("invariants --check",
                 [py, inv, "--check", "--out", inv_json, "--root", root])
        record("invariants", OK if rc == 0 else FAIL, f"rc={rc}")

    # --- 3. review_state --check ----------------------------------------------
    rs = resolve("seed_review_state.py", root)
    rs_json = os.path.join(art, "REVIEW_STATE.json")
    db = os.path.join(art, "KNOWLEDGE_GRAPH.db")
    if rs is None:
        record("review_state", UNEVAL, "seed_review_state.py not found")
    elif not os.path.isfile(rs_json) or not os.path.isfile(db):
        missing = [p for p in (rs_json, db) if not os.path.isfile(p)]
        record("review_state", UNEVAL,
               "absent: %s -- seed with l0_extract.py + %s --db %s --root %s "
               "--out %s" % (", ".join(missing), rs, db, root, rs_json))
    else:
        rc = run("review_state --check",
                 [py, rs, "--check", "--out", rs_json, "--db", db, "--root", root])
        record("review_state", OK if rc == 0 else FAIL, f"rc={rc}")

    # --- 4. witnesses ----------------------------------------------------------
    import glob as _glob
    rw = resolve("run_witnesses.py", root)
    wdir = next((d for d in (os.path.join(root, "tools", "audit", "witnesses"),
                             os.path.join(HERE, "audit", "witnesses"))
                 if os.path.isdir(d)), None)
    wfiles = _glob.glob(os.path.join(wdir, "*_witnesses.py")) if wdir else []
    if rw is None:
        record("witnesses", UNEVAL, "run_witnesses.py not found")
    elif not wfiles:
        record("witnesses", UNEVAL,
               "no *_witnesses.py under %s"
               % os.path.join(root, "tools", "audit", "witnesses"))
    else:
        rc = run("witnesses", [py, rw])
        record("witnesses", OK if rc == 0 else FAIL,
               "rc=%d (%d witness modules)" % (rc, len(wfiles)))

    # --- 5. emit gate ----------------------------------------------------------
    # No falsifiable claim without a witness. In-tree docs/audit/ first.
    eg = resolve("audit_emit_gate.py", root)
    adv_dirs = [d for d in (os.path.join(root, "docs", "audit"),
                            os.path.join(HERE, "..", "docs", "audit"))
                if os.path.isdir(d)]
    seen_adv = set()
    pairs = []
    for _dir in adv_dirs:
        for adv in sorted(_glob.glob(os.path.join(_dir, "*_advanced.json"))):
            base = os.path.basename(adv)
            if base in seen_adv:
                continue
            seen_adv.add(base)
            pairs.append((_dir, base, adv))
    if eg is None:
        record("emit_gate", UNEVAL, "audit_emit_gate.py not found")
    elif not pairs:
        record("emit_gate", UNEVAL,
               "no *_advanced.json sidecars under %s"
               % os.path.join(root, "docs", "audit"))
    else:
        for _dir, base, adv in pairs:
            # resolve the audit deliverable next to the sidecar by convention:
            # <BATCH>_advanced.json -> AUDIT_<BATCH>_v3_66_*.json in the same dir
            batch = base.replace("_advanced.json", "")
            aud = sorted(_glob.glob(os.path.join(_dir, f"AUDIT_{batch}_v3_66_*.json")))
            args = [py, eg, "--advanced", adv]
            if aud:
                args += ["--audit", aud[0]]
            rc = run(f"emit_gate({base})", args)
            record(f"emit_gate({base})", OK if rc == 0 else FAIL, f"rc={rc}")

    # --- 6/7. OPTIONAL extensions ---------------------------------------------
    # Conditional BY DESIGN (pre-v3.66.855 semantics preserved): they are declared
    # extensions with their own preconditions, so an absent precondition is "not
    # applicable" rather than a hole in the composite verdict. They still FAIL loudly.
    ci = resolve("constraint_incidence.py", root)
    if ci and seen_adv:
        rc = run("constraint_incidence topology", [py, ci, "topology", "--gate"])
        record("constraint_incidence", OK if rc == 0 else FAIL, f"rc={rc}")
    ca = resolve("consumer_agreement.py", root)
    cj = os.path.join(art, "CONTRACTS.json")
    if ca and os.path.isfile(cj):
        rc = run("consumer_agreement", [py, ca, "--contracts", cj, "--gate"])
        record("consumer_agreement", OK if rc == 0 else FAIL, f"rc={rc}")

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=sec.DEFAULT_WORK,
                    help="the source tree under audit (default: this checkout)")
    ap.add_argument("--artifacts",
                    default=os.path.join(sec.DEFAULT_WORK, "reports", "audit"),
                    help="dir holding INVARIANTS.json / REVIEW_STATE.json / "
                         "KNOWLEDGE_GRAPH.db (generated; gitignored under reports/)")
    ap.add_argument("--corpus",
                    default=os.path.join(sec.DEFAULT_WORK, "tools", "audit",
                                         "regression_corpus"),
                    help="defect_patterns regression corpus (DP-*_vuln.py pairs)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    results = collect(a)
    rc = verdict(results)

    print("\n" + "=" * 60)
    for name, state, detail in results:
        tag = {OK: "PASS", FAIL: "FAIL", UNEVAL: "CANNOT-EVALUATE"}[state]
        print(f"  {tag:<16} {name}{('  -- ' + detail) if detail else ''}")
    if rc == 0:
        print("bd-audit-gate: PASS -- every core sub-gate ran and passed")
    elif rc == 1:
        bad = [n for n, s, _ in results if s == FAIL]
        print(f"bd-audit-gate: FAIL -- {len(bad)} sub-gate(s) failed: {', '.join(bad)}")
    else:
        blind = [n for n, s, _ in results if s == UNEVAL]
        print(f"bd-audit-gate: CANNOT-EVALUATE -- {len(blind)} sub-gate(s) could not "
              f"see their subject: {', '.join(blind)}. Not a PASS.")
    if a.json:
        print(json.dumps({"exit": rc,
                          "sub_gates": [{"name": n, "state": s, "detail": d}
                                        for n, s, d in results]}, indent=1))
    sys.exit(rc)


def selftest():
    """Controls on the two things this wrapper actually decides: where the
    sub-tools live, and how sub-gate states fold into an exit code.

    The pre-v3.66.855 selftest asserted `bd-audit` was present and called that a
    "delegation target". bd-audit-gate never invokes bd-audit -- it was a
    copy-paste stub asserting a coupling that does not exist, which is a gate
    reporting on a subject it does not have. Replaced with real controls."""
    ok = True

    # verdict(): both directions, and the third state.
    cases = [
        ([("a", OK, ""), ("b", OK, "")], 0, "all core gates ran+passed -> 0"),
        ([("a", OK, ""), ("b", FAIL, "")], 1, "a real failure -> 1"),
        ([("a", OK, ""), ("b", UNEVAL, "")], 2, "a blind sub-gate -> 2, never 0"),
        ([("a", FAIL, ""), ("b", UNEVAL, "")], 1, "failure dominates cannot-evaluate"),
        ([], 2, "no sub-gate at all -> 2, never 0"),
    ]
    for results, want, label in cases:
        got = verdict(results)
        good = got == want
        print(("PASS" if good else "FAIL") + f"  verdict: {label} (got {got})")
        ok &= good

    # _corpus_ready(): NEG absent, NEG empty, POS populated -- an over-sensitive
    # check that refused a real corpus would be an equal soundness bug.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        missing = os.path.join(td, "nope")
        r1 = _corpus_ready(missing)[0] is False
        print(("PASS" if r1 else "FAIL") + "  corpus: absent dir -> not ready")
        ok &= r1
        empty = os.path.join(td, "empty"); os.makedirs(empty)
        open(os.path.join(empty, "README.md"), "w").write("x\n")
        r2 = _corpus_ready(empty)[0] is False
        print(("PASS" if r2 else "FAIL") + "  corpus: no fixtures -> not ready")
        ok &= r2
        full = os.path.join(td, "full"); os.makedirs(full)
        open(os.path.join(full, "DP-01_vuln.py"), "w").write("x = 1\n")
        open(os.path.join(full, "DP-01_fixed.py"), "w").write("x = 1\n")
        r3 = _corpus_ready(full)[0] is True
        print(("PASS" if r3 else "FAIL") +
              "  corpus: real fixtures -> READY (must not refuse real data)")
        ok &= r3

    # resolve(): the sub-tools must be findable from this checkout. If they are
    # not, say so -- this is the exact breakage v3.66.855 repaired.
    for name in ("defect_patterns.py", "invariants.py", "seed_review_state.py"):
        p = resolve(name, sec.DEFAULT_WORK)
        good = p is not None
        print(("PASS" if good else "FAIL") +
              f"  resolve {name}: {p or 'NOT FOUND under <root>/tools or ' + HERE}")
        ok &= good

    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    main()
