#!/usr/bin/env python3
"""
verify_release.py — one command to answer "is this release sound?"

Composes the existing, validated pieces (it does not re-implement them):

  * tools/check_version_consistency.py  → version banners + CHANGELOG alignment
  * tools/check_doc_drift.py            → required docs present
  * tools/template_inventory.py         → template inventory + status sanity
  * bulk_downloader/dev_suite.zip_manifest_check()  → zip ⇄ tree manifest (with --zip)
  * tools/run_tests.py (optional, --tests)          → expected test pass criteria

Default (structural, fast): version + changelog + docs + template inventory, and
— if --zip is given — the zip manifest. Add --tests to run the suite.

THE TEST RUNNER ENCODES THE HARD-WON LESSONS (see docs/VERIFY_RELEASE.md):
  * ONE fresh BD_HOME per file (never share a BD_HOME across files — shared state
    makes 'fresh-DB'/global tests false-fail).
  * Inherits the AMBIENT environment (GTK typelib/lib paths, DISPLAY/Xvfb,
    PYTHONPATH) — it does NOT hardcode sandbox paths, so it runs on stash (venv
    env) and in the sandbox (prestaged env) alike. Run it inside the loaded env
    (`bd python3 tools/verify_release.py --tests` on stash).
  * test_perf_lab.py is isolated with its own bounded timeout (the whole-dir hang
    is an interaction; isolated it passes).
  * Failures are CLASSIFIED: a GTK/DISPLAY import failure (e.g. tray_app without a
    display) is reported as a HARNESS/ENV artifact, distinct from a real
    regression. Skip totals are reported; gate skip-count drift with
    tools/check_skip_baseline.py.

STRICTLY READ-ONLY: never promotes, enables, swaps, builds, or bumps anything.
stdlib-only.

Exit 0 = all selected gates pass. Exit 1 = a gate failed (or a real test
regression). Exit 2 = setup/IO error.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

_HARNESS_SIGNATURES = (
    "Namespace Gtk not available",
    "Bad display name",
    "Can't connect to display",
    "cannot open shared object file: libgtk",
    "launched a headed browser without having a XServer",
)
_SUMMARY_RE = re.compile(
    r"Total:\s*(\d+)\s*\|\s*Passed:\s*(\d+)\s*\|\s*Failed:\s*(\d+)\s*\|\s*Skipped:\s*(\d+)")


def _tools_dir(root):
    return os.path.join(root, "tools")


def _import_siblings(root):
    sys.path.insert(0, _tools_dir(root))
    sys.path.insert(0, root)
    import check_version_consistency as CVC  # type: ignore
    import check_doc_drift as CDD  # type: ignore
    import template_inventory as TI  # type: ignore
    return CVC, CDD, TI


# ── structural checks ──────────────────────────────────────────────

def check_version(root, CVC):
    code, report = CVC.run(root)
    lines = []
    if report is None:
        return False, ["could not import dev_suite (run from repo root)"]
    vc, cl = report["version_consistency"], report["changelog_lint"]
    lines.append(f"version {vc['version']}; banners: {vc['verdict']}; changelog: {cl['verdict']}")
    for m in vc["mismatches"]:
        lines.append(f"  stale banner {m['file']}:{m['line']} -> {m['found']}")
    return code == 0, lines


def check_docs(root, CDD):
    d = CDD.scan(root)
    missing = [k for k, v in d["required"].items() if not v]
    if not d["docs_dir_nonempty"]:
        missing.append("docs/")
    lines = [f"required docs: {'all present' if not missing else 'MISSING ' + ','.join(missing)}"]
    if d["archive_candidates"]:
        lines.append(f"  {len(d['archive_candidates'])} archival candidate(s) in tree "
                     "(historical handoffs — informational)")
    return not missing, lines


def check_templates(root, TI):
    data = TI.scan(root)
    c = data["counts"]
    lines = [f"reviewed={c['reviewed']} enabled={c['enabled']} drafts={c['drafts']} "
             f"candidates={c['review_candidates']}"]
    for name, items in data["dirs"].items():
        for a in items:
            tag = "gate-ready" if a["promotion_ready"] else "needs-review"
            warn = (" BLOCKED:" + ",".join(a["blocked_terms"])) if a["blocked_terms"] else ""
            lines.append(f"  {name}/{a['host']} status={a['status']} "
                         f"score={a['completeness_score']}/100 {tag}{warn}")
    for v in data["sanity"]:
        lines.append(f"  ! SANITY: {v}")
    # gate only on dir-level sanity (status mismatches), not on incomplete drafts
    return not data["sanity"], lines


def check_zip(root, zip_path):
    sys.path.insert(0, root)
    try:
        from bulk_downloader import dev_suite  # type: ignore
    except Exception as e:  # noqa: BLE001
        return False, [f"cannot import dev_suite: {e}"]
    r = dev_suite.zip_manifest_check(zip_path)
    lines = [f"{r['verdict']} (tree={r['tree_file_count']} zip={r['zip_file_count']})"]
    for f in (r.get("missing_from_zip") or [])[:20]:
        lines.append(f"  missing from zip: {f}")
    for f in (r.get("extra_in_zip") or [])[:20]:
        lines.append(f"  extra in zip: {f}")
    return bool(r.get("ok")), lines


# ── in-zip STATE.json staleness (Lever 2 — the 191 defect) ──────────
_GUARD_FILES = (
    "bulk_downloader/extraction_core.py",
    "bulk_downloader/session_capture.py",
    "tools/capture_session.py",
    "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py",
    "bulk_downloader/capture_bodies.py",
    "tools/build_release.py",
)


def check_state_in_zip(zip_path):
    """Verify the STATE.json BUNDLED IN THE ZIP matches the zip's own
    contents. Catches the defect class where a stale prior-release
    STATE.json rides along inside a newer zip (e.g. 189's STATE shipped
    in the first 191 cut). Checks live/built_version vs the zip's
    bulk_downloader/__init__.py, zip.file_count vs the zip's non-dir
    member count, and the 7 guard SHA prefixes vs the zip members.

    zip.sha256 is intentionally NOT checked: a zip cannot contain its own
    hash, so the in-zip value is always a prior build's and is advisory
    only — bd-state verifies the real sha against the artifact downstream.
    """
    import hashlib
    import re as _re
    import zipfile
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception as e:  # noqa: BLE001
        return False, [f"cannot open zip: {e}"]
    with zf:
        names = set(zf.namelist())
        if "STATE.json" not in names:
            return True, ["no STATE.json in zip (nothing to verify)"]
        try:
            st = json.loads(zf.read("STATE.json").decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return False, [f"STATE.json in zip is unreadable: {e}"]
        problems = []
        try:
            init_src = zf.read("bulk_downloader/__init__.py").decode("utf-8")
            m = _re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_src)
            zip_ver = m.group(1) if m else None
        except Exception:  # noqa: BLE001
            zip_ver = None
        # Only built_version describes THIS artifact. live_version is the
        # separately-tracked deploy state on stash and legitimately lags a
        # freshly-built-but-undeployed cut, so it is NOT checked here.
        sv = st.get("built_version")
        if zip_ver and sv and sv != zip_ver:
            problems.append(f"STATE.built_version={sv} != zip __version__={zip_ver}")
        actual_fc = len([n for n in names if not n.endswith("/")])
        sfc = (st.get("zip") or {}).get("file_count")
        if isinstance(sfc, int) and sfc != actual_fc:
            problems.append(f"STATE.zip.file_count={sfc} != zip member count={actual_fc}")
        sg = st.get("guards") or {}
        for gpath in _GUARD_FILES:
            decl = sg.get(gpath)
            if not decl:
                continue
            if gpath not in names:
                problems.append(f"guard {gpath} in STATE but missing from zip")
                continue
            actual = hashlib.sha256(zf.read(gpath)).hexdigest()
            if not actual.startswith(decl):
                problems.append(f"guard {gpath} STATE={decl} != zip={actual[:len(decl)]}")
        lv, bv = st.get("live_version"), st.get("built_version")
        lines = [f"in-zip STATE (build-stamped, NON-authoritative — canonical = session pack): "
                 f"built_version={bv} file_count={sfc} (zip ver={zip_ver}, members={actual_fc})"]
        if lv and bv and lv != bv:
            lines.append(f"  NOTE: in-zip live_version={lv} lags built_version={bv} "
                         f"(display only; the canonical pin is the session pack STATE).")
        for p in problems:
            lines.append(f"  STALE: {p}")
        return (not problems), lines


# ── optional test-criteria runner ──────────────────────────────────

def _gate_files(root, version):
    parts = version.split(".")
    suite_glob = f"test_v3_{parts[1]}_{parts[2]}_*.py" if len(parts) == 3 else "test_v3_66_*_*.py"
    files = ["tests/test_contracts.py", "tests/test_endpoint_catalog_in_sync.py"]
    files += sorted(os.path.relpath(p, root)
                    for p in glob.glob(os.path.join(root, "tests", suite_glob)))
    # de-dup, keep existing
    seen, out = set(), []
    for f in files:
        if f not in seen and os.path.isfile(os.path.join(root, f)):
            seen.add(f)
            out.append(f)
    return out


def _run_one(root, relpath, timeout):
    env = os.environ.copy()
    env["BD_HOME"] = tempfile.mkdtemp(prefix="vr_")  # fresh per file — never shared
    env["BD_DISABLE_KEEPALIVE"] = "1"
    try:
        p = subprocess.run([sys.executable, "run_tests.py", relpath],
                           cwd=root, env=env, capture_output=True, text=True,
                           timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        rc = 124
    m = _SUMMARY_RE.search(out)
    total, passed, failed, skipped = (map(int, m.groups()) if m else (0, 0, 0, 0))
    harness = any(sig in out for sig in _HARNESS_SIGNATURES)
    return {"file": relpath, "rc": rc, "total": total, "passed": passed,
            "failed": failed, "skipped": skipped, "harness": harness,
            "timeout": rc == 124}


def run_tests_criteria(root, scope, version):
    if scope == "full":
        files = sorted(os.path.relpath(p, root)
                       for p in glob.glob(os.path.join(root, "tests", "test_*.py")))
    else:
        files = _gate_files(root, version)
    results, real_fail, harness_fail, slow = [], [], [], []
    for f in files:
        is_perf = os.path.basename(f) == "test_perf_lab.py"
        r = _run_one(root, f, timeout=60 if is_perf else 120)
        results.append(r)
        if r["timeout"]:
            slow.append(f)
        if r["failed"] > 0 or (r["rc"] != 0 and r["total"] == 0):
            (harness_fail if r["harness"] else real_fail).append(r)
    agg = {"files": len(files),
           "passed": sum(r["passed"] for r in results),
           "failed": sum(r["failed"] for r in results),
           "skipped": sum(r["skipped"] for r in results),
           "real_failures": real_fail, "harness_failures": harness_fail,
           "timeouts": slow, "results": results}
    return agg


def _print_tests(agg):
    print(f"  files={agg['files']} passed={agg['passed']} "
          f"failed={agg['failed']} skipped={agg['skipped']}")
    if agg["harness_failures"]:
        print(f"  HARNESS/ENV (not regressions): "
              f"{', '.join(r['file'] for r in agg['harness_failures'])}")
        print("    -> GTK typelib/DISPLAY not loaded; run inside the env (e.g. via bd). "
              "See docs/VERIFY_RELEASE.md.")
    if agg["timeouts"]:
        print(f"  TIMEOUTS (bounded): {', '.join(agg['timeouts'])}")
    if agg["real_failures"]:
        print("  REAL REGRESSIONS:")
        for r in agg["real_failures"]:
            print(f"    !! {r['file']}  failed={r['failed']} rc={r['rc']}")
    else:
        print("  no real regressions")
    print("  (skip identity/reason drift: gate complete JUnit with "
          "tools/check_skip_baseline.py --junit <path>)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify a BulkDownloader release (read-only).")
    ap.add_argument("--root", default=".")
    ap.add_argument("--zip", dest="zip_path", help="also verify this release zip's manifest")
    ap.add_argument("--tests", nargs="?", const="gate", choices=["gate", "full"],
                    help="run test criteria: 'gate' (default) or 'full' (slow)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(root, "bulk_downloader")):
        print(f"error: {root!r} is not the repo root", file=sys.stderr)
        return 2
    try:
        CVC, CDD, TI = _import_siblings(root)
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot import sibling tools from {_tools_dir(root)}: {e}",
              file=sys.stderr)
        return 2

    results = {}
    gates = []

    ok_v, l_v = check_version(root, CVC)
    gates.append(("version_consistency", ok_v)); results["version"] = l_v
    ok_d, l_d = check_docs(root, CDD)
    gates.append(("required_docs", ok_d)); results["docs"] = l_d
    ok_t, l_t = check_templates(root, TI)
    gates.append(("template_sanity", ok_t)); results["templates"] = l_t
    if args.zip_path:
        ok_z, l_z = check_zip(root, args.zip_path)
        gates.append(("zip_manifest", ok_z)); results["zip"] = l_z
        ok_s, l_s = check_state_in_zip(args.zip_path)
        gates.append(("state_in_zip", ok_s)); results["state_in_zip"] = l_s

    test_agg = None
    if args.tests:
        ver = _read_version(root)
        test_agg = run_tests_criteria(root, args.tests, ver)
        gates.append(("tests:" + args.tests, not test_agg["real_failures"]))

    if args.json:
        print(json.dumps({"gates": dict(gates), "detail": results,
                          "tests": test_agg}, indent=2, default=str))
    else:
        print("=" * 70)
        print(f"  verify_release — {root}")
        print("=" * 70)
        for title, key in (("VERSION CONSISTENCY", "version"),
                           ("REQUIRED DOCS", "docs"),
                           ("TEMPLATE INVENTORY", "templates"),
                           ("ZIP MANIFEST", "zip"),
                           ("IN-ZIP STATE", "state_in_zip")):
            if key in results:
                print(f"\n-- {title} --")
                for ln in results[key]:
                    print(f"  {ln}")
        if test_agg is not None:
            print(f"\n-- TEST CRITERIA ({args.tests}) --")
            _print_tests(test_agg)
        print("\n" + "=" * 70)
        failed_gates = [g for g, ok in gates if not ok]
        if failed_gates:
            print(f"  RESULT: FAIL — {', '.join(failed_gates)}")
        else:
            print("  RESULT: PASS — all selected gates green")
        print("=" * 70)

    return 1 if any(not ok for _, ok in gates) else 0


def _read_version(root):
    try:
        with open(os.path.join(root, "bulk_downloader", "__init__.py")) as fh:
            for ln in fh:
                m = re.search(r'__version__\s*=\s*["\'](\d+\.\d+\.\d+)', ln)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "0.0.0"


if __name__ == "__main__":
    sys.exit(main())
