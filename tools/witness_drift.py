#!/usr/bin/env python3
"""witness_drift -- "nothing goes stale" made concrete ACROSS releases.

Within one session the witness suite proves the KB matches behavior. Across
sessions, the SAME witnesses are the belief-change detector: snapshot results per
version, diff two snapshots, and every flip is an unmissable, precise signal:

  claim green->red   : a KB belief no longer matches behavior -> RE-AUTHOR the
                       judgment (the code is the bug, or the invariant is outdated).
  claim red->green   : a previously-broken belief now holds (fix landed / was wrong).
  finding-repro green->red : the vuln no longer reproduces -> the FIX landed ->
                       mark the finding fixed + flip the witness to assert the fix.
  new / dropped      : a witness appeared or vanished (batch added/removed).

Subcommands:
  snapshot --version V    run all witness suites, write WITNESS_LOG_vV.json
  diff --from A --to B     compare two snapshots, emit the re-author worklist
Snapshot runs under `bd` (imports bulk_downloader); diff is pure JSON. Stdlib only.
"""
import argparse
import glob
import importlib.util
import json
import os

_RUNNER_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "run_witnesses.py")
_runner_spec = importlib.util.spec_from_file_location("_witness_drift_runner", _RUNNER_PATH)
if _runner_spec is None or _runner_spec.loader is None:
    raise ImportError(f"cannot load witness policy: {_RUNNER_PATH}")
_runner = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(_runner)
discover_suites = _runner.discover_suites
load_suite = _runner.load_suite

ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.path.join(ROOT, "review")
WDIR = os.path.join(REVIEW, "witnesses")
LOGDIR = os.path.join(REVIEW, "witness_logs")


def snapshot(version, *, root=ROOT, logdir=None):
    root = os.path.abspath(os.fspath(root))
    destination = os.path.abspath(os.fspath(logdir or os.path.join(root, "review", "witness_logs")))
    suites = discover_suites(root)
    if not suites:
        print(f"witness_drift snapshot v{version}: no witness suites found")
        return 2
    results = {}
    total = 0
    for s in suites:
        entries = load_suite(s)
        total += len(entries)
        for entry in entries:
            if isinstance(entry, dict):
                cid = entry.get("id"); ok = entry.get("ok")
                kind = entry.get("kind") or ("finding_repro" if str(cid).startswith("F-") else "claim")
                flips_to = entry.get("flips_to", "")
            else:
                cid, ok, _d = entry
                kind = "finding_repro" if str(cid).startswith("F-") else "claim"
                flips_to = ""
            results[cid] = {"ok": ok, "kind": kind, "flips_to": flips_to}
    if total == 0 or not results:
        print(f"witness_drift snapshot v{version}: suites produced zero results")
        return 2
    os.makedirs(destination, exist_ok=True)
    out = os.path.join(destination, f"WITNESS_LOG_v{version}.json")
    payload = {
        "version": version,
        "sources": [os.path.relpath(path, root) for path in suites],
        "results": results,
    }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"witness_drift snapshot v{version}: {len(results)} witnesses -> {out}")
    return 0


def diff(a_path, b_path):
    A = json.load(open(a_path))["results"]
    B = json.load(open(b_path))["results"]
    va = json.load(open(a_path)).get("version", "?")
    vb = json.load(open(b_path)).get("version", "?")
    reauthor, fixed, regressed, appeared, dropped = [], [], [], [], []
    for cid in sorted(set(A) | set(B)):
        ra, rb = A.get(cid), B.get(cid)
        if ra is None:
            appeared.append(cid); continue
        if rb is None:
            dropped.append(cid); continue
        if ra["ok"] == rb["ok"]:
            continue
        kind = rb.get("kind", "claim")
        if kind == "finding_repro":
            # green(reproduces)->red(cleared) = fix landed
            if ra["ok"] and not rb["ok"]:
                fixed.append(cid)
            else:
                regressed.append(cid)  # a fixed finding started reproducing again
        else:
            if ra["ok"] and not rb["ok"]:
                reauthor.append(cid)   # belief went stale
            else:
                regressed.append(cid)  # broken belief now holds (was wrong / fixed)
    print(f"witness_drift diff: v{va} -> v{vb}")
    print("=" * 64)
    if reauthor:
        print("RE-AUTHOR (a KB belief no longer matches behavior):")
        for c in reauthor: print(f"    claim {c} flipped green->red")
    if fixed:
        print("FINDING FIXED (repro cleared -> mark fixed + assert the fix in the witness):")
        for c in fixed: print(f"    {c} no longer reproduces")
    if regressed:
        print("FLIPPED red->green / repro-returned (review):")
        for c in regressed: print(f"    {c}")
    if appeared:
        print(f"NEW witnesses: {appeared}")
    if dropped:
        print(f"DROPPED witnesses: {dropped}")
    if not any([reauthor, fixed, regressed, appeared, dropped]):
        print("no belief changes between snapshots")
    print("=" * 64)
    # rc!=0 if anything needs human re-authoring
    return 0 if not (reauthor or regressed) else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot"); s.add_argument("--version", required=True)
    s.add_argument("--root", default=ROOT); s.add_argument("--logdir", default=None)
    d = sub.add_parser("diff"); d.add_argument("--from", dest="a", required=True); d.add_argument("--to", dest="b", required=True)
    a = ap.parse_args()
    if a.cmd == "snapshot":
        raise SystemExit(snapshot(a.version, root=a.root, logdir=a.logdir))
    raise SystemExit(diff(a.a, a.b))


if __name__ == "__main__":
    main()
