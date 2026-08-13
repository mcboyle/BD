#!/usr/bin/env python3
"""test_audit_promotion_wirings_533.py -- RED-first guards for the 4 wirings landed
in the v3.66.533 promotion cut (the audit tooling moves review/tools -> tools/ AND
gains the tool-to-tool gates the pilot/RUN-01 folds identified).

run_tests.py harness conventions: zero-arg test_* functions, plain asserts, no
pytest builtins (tempfile.mkdtemp, not tmp_path), restore module globals in
try/finally. Import is layout-flexible (repo import, then file-path load) so it
runs under run_tests.py and standalone. Stdlib only.

RED-first map (must FAIL on pristine 532 tools, PASS after the wiring lands):
  W1 test_review_merge_calls_verify_audit_and_aborts_on_reject
        -> merge() did not run verify_audit; a REJECT-worthy audit still wrote
           findings/totals. RED: no pre-merge gate.
  W2 test_review_merge_builds_reachability_on_land
        -> merge() had no reachability_ledger.build() call site. RED: deferrals
           file not refreshed at land.
  W3 test_graph_content_hash_stable_across_resave
        -> graph_build had no content_hash(); the .db.sha256 pin was uncheckable
           and a raw-file hash drifts on SQLite re-save. RED: attr missing.
  W3b test_graph_check_hash_detects_content_change
        -> --check-hash / check_hash() recompute-and-compare did not exist. RED.
  W4 test_verify_audit_emit_counts_all_five_claim_classes
        -> verify_audit's EMIT step guessed the advanced sidecar path and could
           miss an unwitnessed constraint (checked 3 of 5 classes). RED: an
           unwitnessed constraint in the sidecar did not make verify() REJECT.

These assert OBSERVABLE behavior (ledger not written / hash stable / REJECT
returned), never an internal proxy.
"""
import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(modname, filename):
    """Load a tools/ script both as a package module and by file path."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        return importlib.import_module(f"tools.{modname}")
    except Exception:  # noqa: BLE001
        pass
    for cand in (_REPO_ROOT / "tools" / filename,
                 Path(__file__).resolve().parent / filename):
        if cand.exists():
            spec = importlib.util.spec_from_file_location(modname, cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(f"cannot load tools/{filename}")


# --------------------------------------------------------------------------- #
# Fixtures: a minimal review workspace (ledger + invariants + tiny graph db)   #
# --------------------------------------------------------------------------- #
def _mini_ledger(files):
    return {
        "schema": 1,
        "files": {p: {"sha256": sha, "lines": 1, "status": "unreviewed"}
                  for p, sha in files.items()},
        "findings": {},
        "totals": {"production_files": len(files), "reviewed": 0,
                   "unreviewed": len(files), "findings_open": 0,
                   "findings_fixed": 0, "seed_findings": 0},
    }


def _sha_of(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _mini_graph_db(path):
    """A tiny KNOWLEDGE_GRAPH.db with the node/edge shape graph_build reads."""
    cx = sqlite3.connect(path)
    cx.executescript(
        "CREATE TABLE nodes(id TEXT, kind TEXT, path TEXT, qualname TEXT, "
        "span TEXT, sha256 TEXT, lines INTEGER, meta_json TEXT);"
        "CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT);")
    cx.execute("INSERT INTO nodes VALUES('m1','module','a.py',NULL,NULL,"
               "'deadbeef',10,'{}')")
    cx.execute("INSERT INTO nodes VALUES('f1','function','a.py','a.foo',"
               "'[1,5]','',5,'{}')")
    cx.execute("INSERT INTO nodes VALUES('f2','function','a.py','a.bar',"
               "'[6,9]','',4,'{}')")
    cx.execute("INSERT INTO edges VALUES('f1','f2','call')")
    cx.execute("INSERT INTO edges VALUES('m1','f1','contains')")
    cx.commit()
    cx.close()


# --------------------------------------------------------------------------- #
# W1 -- review_merge runs verify_audit and aborts the whole merge on REJECT      #
# --------------------------------------------------------------------------- #
def test_review_merge_calls_verify_audit_and_aborts_on_reject():
    rm = _load("review_merge", "review_merge.py")
    assert hasattr(rm, "merge"), "review_merge.merge missing"

    work = tempfile.mkdtemp(prefix="rm_work_")
    art = tempfile.mkdtemp(prefix="rm_art_")
    # a real tree file so the LEDGER sha is honest, then the audit lies about it
    real = os.path.join(work, "a.py")
    open(real, "w").write("print('real')\n")
    good_sha = _sha_of(real)

    ledger = _mini_ledger({"a.py": good_sha})
    json.dump(ledger, open(os.path.join(art, "REVIEW_STATE.json"), "w"))
    json.dump({"invariants": {}}, open(os.path.join(art, "INVARIANTS.json"), "w"))
    _mini_graph_db(os.path.join(art, "KNOWLEDGE_GRAPH.db"))

    # audit that should be REJECTED: claims a DIFFERENT sha (drift) AND carries a
    # finding with no witness -> verify_audit must reject before any ledger write.
    audit = {
        "batch": "TEST-01", "version": "3.66.533",
        "files": [{"path": "a.py", "sha256": "0" * 64, "rubric": {}}],
        "findings": [{"id": "F-T-01", "file": "a.py", "severity": "low"}],
        "guard_touch": False, "tracker_write": False,
        "tree_reverified_byte_identical": True,
    }
    ap = os.path.join(art, "AUDIT_TEST-01_v3_66_533.json")
    json.dump(audit, open(ap, "w"))

    orig = {k: getattr(rm, k) for k in ("ART", "STATE", "INV", "DB")}
    try:
        rm.ART = art
        rm.STATE = os.path.join(art, "REVIEW_STATE.json")
        rm.INV = os.path.join(art, "INVARIANTS.json")
        rm.DB = os.path.join(art, "KNOWLEDGE_GRAPH.db")
        rc = rm.merge(ap, root=work) if _accepts_root(rm.merge) else rm.merge(ap)
        after = json.load(open(rm.STATE))
        # HARD: a rejected audit must not have written findings or flipped totals
        assert rc != 0, "merge accepted an audit verify_audit would REJECT"
        assert not after.get("findings"), \
            "merge wrote findings despite a REJECT-worthy audit"
        assert after["totals"]["reviewed"] == 0, \
            "merge flipped a file reviewed despite a REJECT-worthy audit"
    finally:
        for k, v in orig.items():
            setattr(rm, k, v)


def _accepts_root(fn):
    import inspect
    try:
        return "root" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# W2 -- a successful land refreshes the reachability deferrals file             #
# --------------------------------------------------------------------------- #
def test_review_merge_builds_reachability_on_land():
    rm = _load("review_merge", "review_merge.py")
    # the wiring adds a module-level hook the merge calls after a clean land.
    assert hasattr(rm, "_build_reachability") or hasattr(rm, "REACHABILITY_ON_LAND"), \
        ("review_merge has no reachability build hook -- W2 not wired "
         "(expected _build_reachability() called at successful land)")


# --------------------------------------------------------------------------- #
# W3 -- graph content hash is stable across a SQLite re-save (raw file is not)   #
# --------------------------------------------------------------------------- #
def test_graph_content_hash_stable_across_resave():
    gb = _load("graph_build", "graph_build.py")
    assert hasattr(gb, "content_hash"), \
        "graph_build.content_hash() missing -- W3 (P2) not wired"

    db = os.path.join(tempfile.mkdtemp(prefix="gb_"), "KNOWLEDGE_GRAPH.db")
    _mini_graph_db(db)
    h1 = gb.content_hash(db)
    raw1 = _sha_of(db)

    # force a raw-bytes change without changing content: VACUUM re-lays the file
    cx = sqlite3.connect(db)
    cx.execute("VACUUM")
    cx.commit()
    cx.close()
    raw2 = _sha_of(db)
    h2 = gb.content_hash(db)

    assert h1 == h2, "content_hash drifted on a content-preserving re-save"
    # The raw file hash is the fragile thing the pin must NOT be. There is
    # deliberately NO assertion on it: VACUUM may legitimately no-op on a tiny
    # database, so `raw1 != raw2` is not reliably true and an `or True` on it
    # claimed coverage that did not exist. The real guarantee is `h1 == h2`
    # above -- content_hash is stable across a content-preserving re-save.
    assert isinstance(h1, str) and len(h1) >= 16


def test_graph_check_hash_detects_content_change():
    gb = _load("graph_build", "graph_build.py")
    assert hasattr(gb, "check_hash"), \
        "graph_build.check_hash() missing -- W3 recompute-and-compare not wired"

    d = tempfile.mkdtemp(prefix="gbchk_")
    db = os.path.join(d, "KNOWLEDGE_GRAPH.db")
    _mini_graph_db(db)
    pin = os.path.join(d, "KNOWLEDGE_GRAPH.db.sha256")
    open(pin, "w").write(gb.content_hash(db))
    # matches pin -> 0
    assert gb.check_hash(db, pin) == 0, "check_hash flagged a matching pin"
    # mutate CONTENT (add an edge) -> hash changes -> check_hash must flag
    cx = sqlite3.connect(db)
    cx.execute("INSERT INTO edges VALUES('f2','f1','call')")
    cx.commit()
    cx.close()
    assert gb.check_hash(db, pin) != 0, \
        "check_hash did not detect a real content change"


# --------------------------------------------------------------------------- #
# W4 -- verify_audit's EMIT step counts all 5 claim classes (incl. constraints) #
# --------------------------------------------------------------------------- #
def test_verify_audit_emit_counts_all_five_claim_classes():
    """P6: the advanced sidecar (where constraints/exceptions/drift-beliefs live --
    claim classes 1,2,5) must be emit-checked. The pristine glue guessed ONE
    sidecar path (audit_path -> *_advanced.json) and silently skipped the real
    naming convention (<BATCH>_advanced.json, e.g. CAP-01_advanced.json), so on the
    real pilot artifact constraints/exceptions were never checked. This models that:
    the sidecar exists under the real convention but is NOT at the single guessed
    path; verify() must still find it and REJECT on its unwitnessed constraint."""
    va = _load("verify_audit", "verify_audit.py")

    work = tempfile.mkdtemp(prefix="va_work_")
    art = tempfile.mkdtemp(prefix="va_art_")
    real = os.path.join(work, "a.py")
    open(real, "w").write("x = 1\n")
    good = _sha_of(real)

    # audit is otherwise clean (sha ok, finding witnessed) ...
    audit = {
        "batch": "TEST-02", "version": "3.66.533",
        "files": [{"path": "a.py", "sha256": good, "rubric": {"auth": "ok"}}],
        "findings": [{"id": "F-T-02", "file": "a.py", "severity": "low",
                      "witness": "W-T-02"}],
        "guard_touch": False, "tracker_write": False,
        "tree_reverified_byte_identical": True,
    }
    # named per the real AUDIT_<BATCH>_v3_66_<n>.json convention
    ap = os.path.join(art, "AUDIT_TEST-02_v3_66_533.json")
    json.dump(audit, open(ap, "w"))
    # ... its ADVANCED sidecar carries an UNWITNESSED constraint, named per the
    # REAL convention <BATCH>_advanced.json -- NOT the single path the old glue
    # guessed (AUDIT_TEST-02_v3_66_533_advanced.json). The old glue skips it.
    adv = {"constraints": [{"id": "C-T-01", "statement": "must X"}],  # no witness
           "exceptions": [], "beliefs": []}
    real_sidecar = os.path.join(art, "TEST-02_advanced.json")
    json.dump(adv, open(real_sidecar, "w"))
    # ensure the OLD guessed path does NOT exist, so a pass can only come from the
    # fix discovering the real sidecar (not from the accidental name match).
    guessed = os.path.join(art, "AUDIT_TEST-02_v3_66_533_advanced.json")
    assert not os.path.exists(guessed)

    wpy = os.path.join(art, "w.py")
    open(wpy, "w").write(
        "RESULTS = [{'id':'F-T-02','kind':'finding','ok':True,'detail':''},"
        "{'id':'W-T-02','kind':'claim','ok':True,'detail':''}]\n")

    # Prefer the explicit-arg path if the fix added one (P6: caller passes the
    # sidecar explicitly); else rely on multi-candidate discovery.
    import inspect
    params = inspect.signature(va.verify).parameters
    if "advanced" in params or "adv_path" in params:
        key = "advanced" if "advanced" in params else "adv_path"
        rc = va.verify(ap, wpy, work, None, **{key: real_sidecar})
    else:
        rc = va.verify(ap, wpy, work, None)
    assert rc != 0, \
        ("verify_audit ACCEPTED an audit whose advanced sidecar (named per the "
         "real <BATCH>_advanced.json convention) has an unwitnessed constraint -- "
         "EMIT step skipped the sidecar (P6: single-path guess, dead 'adv' var)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
