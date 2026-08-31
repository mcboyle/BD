#!/usr/bin/env python3
"""precut_check — predict the release gates BEFORE you bump + build.

The build gates (version-consistency, in-sync-doc drift, guard SHAs) currently
fire as *failures mid-build*, after the version is already bumped — e.g. a new
tool tripped DEPENDENCY_GRAPH 145->147 only at build time, forcing a regen +
rebuild. This tool runs the cheap predictions up front against the working tree
vs the live baseline zip, so you know what a cut will require before committing
to it.

Stdlib only. Usage:
  python3 tools/precut_check.py --baseline <live-release.zip>
  python3 tools/precut_check.py --baseline <zip> --json

Reports:
  • version-consistency (__init__ L26 vs CHANGELOG top vs the pinned test)
  • changed files vs baseline (added / changed / removed, by content)
  • which GUARD SHAs moved (-> must be declared)
  • which in-sync docs WILL need regen (dep-graph / function-index / endpoint+gui_parity)
  • suggested band suite (touched-file -> test map)
This is a PREDICTOR, not a gate; build_release remains authoritative.
"""
import argparse
import glob
import hashlib
import importlib.util
import json
import os
import re
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _import(name, fname):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

GUARDS = [
    "bulk_downloader/extraction_core.py", "bulk_downloader/session_capture.py",
    "tools/capture_session.py", "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py", "bulk_downloader/capture_bodies.py",
    "tools/build_release.py",
]
# touched-file -> band tests (mirrors TOUCHED_FILE_TO_TEST.md; extend as it grows)
TOUCH_MAP = [
    (r"^bulk_downloader/__init__\.py$|^CHANGELOG\.md$",
     ["test_contracts", "test_settings_center_slice4"]),
    (r"^tools/build_release\.py$|^tools/verify_release\.py$",
     ["test_build_release_f02", "test_release_hygiene_gates"]),
    (r"^bulk_downloader/runner\.py$",
     ["test_extraction_core", "test_v3_66_273_gcw_download_gate",
      "test_v3_66_274_probe_mode", "test_v3_66_282_media_verdict",
      "test_v3_66_284_integrity", "test_v3_66_285_cloak_parity"]),
    (r"^bulk_downloader/app\.py$", ["test_contracts", "test_endpoint_catalog_in_sync",
      "test_v3_66_285_cloak_parity"]),
    (r"^bulk_downloader/(cloak|auto_detect|macro_replay|selector_playground)\.py$",
     ["test_v3_66_285_cloak_parity"]),
    (r"frontend/src/.*CaptureWorkflow", ["test_gui_parity", "test_v3_66_274_capture_ux", "test_v3_66_292_dom_analyzer_link"]),
    # v3.66.841: the tasktracker_gen/_sync band entries are gone with the tool.
    # The lesson they carried is kept because it outlives them: a band map is a
    # DENOMINATOR, and one that omits a test guarding the file makes the cut
    # green because nothing looked, not because nothing broke.
    (r"^bulk_downloader/global_config\.py$",
     ["test_v3_66_285_config_validation", "test_u28_config_cache",
      "test_u29_config_snapshot"]),
    # v3.66.730: the EXEC BRIDGE. Highest-consequence file in the tree -- the one
    # place the GUI can subprocess into an allowlisted binary. Until now its band
    # came from exactly ONE signal (bd-band-derive's mechanical consumer grep);
    # this curated row is the second, independent signal, so a regression in
    # either one leaves the bridge covered by the other.
    (r"^bulk_downloader/tool_bridge\.py$",
     ["test_v3_66_717_exec_bridge", "test_v3_66_719_tools_control",
      "test_contracts"]),
]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _git_tracked_files(root):
    """The TRACKED denominator, for a baseline derived from git rather than a zip.

    _tree_files reuses build_release's walk, so its denominator is "what the
    release zip would contain" -- which includes generated artifacts that are
    gitignored. Compared against a git-derived baseline those all read as ADDED,
    and a surface report where every generated file is new says nothing. When
    both sides come from git, both sides must be the tracked set.
    """
    import subprocess as _sp
    r = _sp.run(["git", "-C", str(root), "ls-files", "-z"],
                capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = {}
    for rel in r.stdout.split("\0"):
        if not rel:
            continue
        f = os.path.join(root, rel)
        if os.path.isfile(f) and not os.path.islink(f):
            with open(f, "rb") as fh:
                out[rel] = _sha(fh.read())
    return out


def _tree_files(root):
    # Authoritative: reuse build_release's exclusion walk so the predicted file
    # set matches what the build will actually zip (no ad-hoc divergence).
    try:
        br = _import("build_release", "build_release.py")
        excluded, _ = br._load_exclusions(Path(root))
        out = {}
        for p in br._walk_tree(Path(root), excluded):
            rel = str(p.relative_to(root)).replace("\\", "/")
            out[rel] = _sha(p.read_bytes())
        return out
    except (Exception, SystemExit):  # noqa: BLE001 — fall back to a self-contained walk
        out = {}
        skip = ("/__pycache__/", "/node_modules/", "/.git/", "/venv/")
        for p in glob.glob(os.path.join(root, "**", "*"), recursive=True):
            if not os.path.isfile(p):
                continue
            rel = os.path.relpath(p, root).replace("\\", "/")
            if rel.endswith(".pyc") or any(s in "/" + rel + "/" for s in skip):
                continue
            with open(p, "rb") as fh:
                out[rel] = _sha(fh.read())
        return out


def _zip_files(zp):
    out = {}
    with zipfile.ZipFile(zp) as zf:
        for n in zf.namelist():
            if not n.endswith("/"):
                out[n] = _sha(zf.read(n))
    return out


def _version_consistency(root):
    v_init = None
    initp = os.path.join(root, "bulk_downloader", "__init__.py")
    if os.path.isfile(initp):
        for ln in open(initp, encoding="utf-8"):
            m = re.search(r'__version__\s*=\s*"([^"]+)"', ln)
            if m:
                v_init = m.group(1); break
    cl = open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8").read() \
        if os.path.isfile(os.path.join(root, "CHANGELOG.md")) else ""
    pin = None
    tp = os.path.join(root, "tests", "test_settings_center_slice4.py")
    if os.path.isfile(tp):
        m = re.search(r'__version__\s*==\s*"([^"]+)"', open(tp, encoding="utf-8").read())
        pin = m.group(1) if m else None
    consistent = bool(v_init) and pin == v_init and (f"## v{v_init}" in cl)
    # Authoritative pin gate: run the SAME scanner the build runs (tests-only,
    # now fixture-aware) so precut catches the exact pins the build would fail on.
    stale = []
    if v_init:
        try:
            svp = _import("scan_version_pins", "scan_version_pins.py")
            hard, _soft = svp.scan_test_pins(root, v_init)
            stale = [f"{p}:{ln} pins {v}" for (p, ln, v, _line) in hard]
        except Exception:  # noqa: BLE001
            stale = ["(scan_version_pins unavailable)"]
    return {
        "init": v_init,
        "changelog_has_entry": bool(v_init) and (f"## v{v_init}" in cl),
        "test_pin": pin,
        "consistent": consistent and not stale,
        "stale_test_pins": stale,
    }


def predict(root, baseline, tracked_only=False):
    tree = _git_tracked_files(root) if tracked_only else _tree_files(root)
    if tree is None:
        raise SystemExit("UNKNOWN: --tracked-only was asked for and git ls-files "
                         "failed, so there is no denominator to compare against")
    base = _zip_files(baseline)
    added = sorted(p for p in tree if p not in base)
    removed = sorted(p for p in base if p not in tree)
    changed = sorted(p for p in tree if p in base and tree[p] != base[p])
    moved_guards = [g for g in GUARDS if g in changed]
    touched = added + changed + removed
    # in-sync predictions
    regen = []
    if any(p.startswith("tools/") and p.endswith(".py") for p in added + removed):
        regen.append("DEPENDENCY_GRAPH (tools set changed)")
    if any(p in ("bulk_downloader/app.py", "bulk_downloader/runner.py") for p in changed):
        regen.append("FUNCTION_INDEX (app.py/runner.py changed)")
    if "bulk_downloader/app.py" in changed:
        regen.append("ENDPOINT_CATALOG + gui_parity (verify route count; G12)")
    # any module add/remove can shift dep-graph edges
    if any(p.endswith(".py") and (p.startswith("bulk_downloader/") or p.startswith("tools/"))
           for p in added + removed) and "DEPENDENCY_GRAPH (tools set changed)" not in regen:
        regen.append("DEPENDENCY_GRAPH (module set changed)")
    band = []
    for pat, tests in TOUCH_MAP:
        if any(re.search(pat, p) for p in touched):
            band += tests
    band = sorted(set(band))
    return {
        "version": _version_consistency(root),
        "added": added, "changed": changed, "removed": removed,
        "moved_guards": moved_guards,
        "predicted_regens": regen,
        "suggested_band": band,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tracked-only", action="store_true",
                    help="compare the TRACKED file set on both sides; required when "
                         "the baseline is a git archive rather than a release zip")
    a = ap.parse_args(argv)
    r = predict(a.root, a.baseline, a.tracked_only)
    if a.json:
        print(json.dumps(r, indent=2)); return 0
    v = r["version"]
    print(f"PRECUT (vs {os.path.basename(a.baseline)})")
    print(f"  version: init={v['init']} pin={v['test_pin']} "
          f"changelog={'yes' if v['changelog_has_entry'] else 'NO'} "
          f"-> {'CONSISTENT' if v['consistent'] else 'INCONSISTENT (bump all 3 together)'}")
    if v.get("stale_test_pins"):
        print("    STALE TEST PINS (build will FAIL — fix before bump):")
        for s in v["stale_test_pins"]:
            print(f"      {s}")
    print(f"  changed={len(r['changed'])} added={len(r['added'])} removed={len(r['removed'])}")
    if r["moved_guards"]:
        print("  GUARDS MOVED (must DECLARE):")
        for g in r["moved_guards"]:
            print(f"    {g}")
    else:
        print("  guards: none moved")
    print("  predicted in-sync regens:" + (" none" if not r["predicted_regens"] else ""))
    for g in r["predicted_regens"]:
        print(f"    - {g}")
    print("  suggested band:" + (" (none matched)" if not r["suggested_band"] else ""))
    for t in r["suggested_band"]:
        print(f"    - tests/{t}.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
