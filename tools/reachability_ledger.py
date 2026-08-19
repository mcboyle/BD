#!/usr/bin/env python3
"""reachability_ledger -- so boundary-deferred findings don't get forgotten.

Findings whose defect is confirmed at the function boundary but whose exploitability
depends on a caller in ANOTHER batch (F-CAP01-01 -> APP route; F-RUN01-02 -> the
APP/CORE_BD enqueue paths) pile up as 'probable' and nobody circles back. This
ledger extracts them from the AUDIT_*.json set, records which subsystem(s) resolve
each, and -- when that batch is audited -- emits the 'now-resolvable' worklist.

A finding is DEFERRED when its confidence marks the reachability as unestablished
(matches /probable|boundary|at-boundary/i). The resolving subsystem(s) are the
audit-batch prefixes named in its reachability_note (APP, CORE_BD, REC, RUN, AUTH,
COCKPIT, FE, SETTINGS).

  build     scan review/AUDIT_*.json -> REACHABILITY_DEFERRALS.json
  report    open deferrals grouped by the subsystem that resolves them
  resolvable --subsys APP   the worklist to close when APP-* is audited
  resolve --finding F-XXX [--by BATCH]   mark one resolved
Stdlib only.
"""
import argparse
import glob
import json
import os
import re

ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.path.join(ROOT, "review")
LEDGER = os.path.join(REVIEW, "artifacts", "REACHABILITY_DEFERRALS.json")
AUDIT_GLOB = os.path.join(REVIEW, "AUDIT_*.json")
SUBSYS = ["APP", "CORE_BD", "COCKPIT", "REC", "RUN", "AUTH", "SETTINGS", "FE"]
DEFERRED_RE = re.compile(r"probable|boundary", re.I)


def _load():
    if os.path.exists(LEDGER):
        return json.load(open(LEDGER))
    return {"deferrals": {}}


def _save(d):
    json.dump(d, open(LEDGER, "w"), indent=1)


def _subsys_in(note):
    hits = []
    for s in SUBSYS:
        # word-ish boundary; CORE_BD and APP etc.
        if re.search(rf"\b{s}\b", note):
            hits.append(s)
    # 'site_editor' / 'settings-center' -> SETTINGS
    if re.search(r"site_editor|settings.center|settings-center", note, re.I) and "SETTINGS" not in hits:
        hits.append("SETTINGS")
    # 'enqueue' / 'route' -> APP if not already
    if re.search(r"enqueue|route", note, re.I) and "APP" not in hits:
        hits.append("APP")
    return hits


def build():
    led = _load()
    added = 0
    for ap in sorted(glob.glob(AUDIT_GLOB)):
        a = json.load(open(ap))
        batch = a.get("batch", "?")
        for fd in a.get("findings", []):
            conf = str(fd.get("confidence", ""))
            note = str(fd.get("reachability_note") or fd.get("reachability") or "")
            if not DEFERRED_RE.search(conf) and not DEFERRED_RE.search(note):
                continue
            fid = fd.get("id")
            rec = led["deferrals"].get(fid, {})
            if rec.get("status") == "resolved":
                continue
            resolvers = _subsys_in(note) or ["UNKNOWN"]
            # a finding's OWN batch never resolves its own reachability
            resolvers = [s for s in resolvers if not batch.startswith(s)] or resolvers
            led["deferrals"][fid] = {
                "finding": fid, "from_batch": batch,
                "severity": fd.get("severity"), "category": fd.get("category"),
                "confidence": conf, "resolved_by_subsys": resolvers,
                "note": note[:200], "status": rec.get("status", "open")}
            if fid not in {k for k in led["deferrals"]} or rec.get("status") != "open":
                added += 1
    _save(led)
    openn = [d for d in led["deferrals"].values() if d["status"] == "open"]
    print(f"reachability_ledger build: {len(openn)} open deferral(s) tracked -> {LEDGER}")
    for d in openn:
        print(f"    {d['finding']} [{d['severity']}/{d['category']}] from {d['from_batch']} "
              f"-> resolved by {d['resolved_by_subsys']}")
    return 0


def report():
    led = _load()
    by_sub = {}
    for d in led["deferrals"].values():
        if d["status"] != "open":
            continue
        for s in d["resolved_by_subsys"]:
            by_sub.setdefault(s, []).append(d)
    print("REACHABILITY DEFERRALS (open) — grouped by the batch that resolves them")
    print("=" * 66)
    for s in sorted(by_sub):
        print(f"## {s}-* audit resolves {len(by_sub[s])}:")
        for d in by_sub[s]:
            print(f"    {d['finding']} [{d['severity']}] from {d['from_batch']} — {d['category']}")
    tot = len([d for d in led['deferrals'].values() if d['status'] == 'open'])
    print("=" * 66)
    print(f"{tot} open; resolved: "
          f"{len([d for d in led['deferrals'].values() if d['status']=='resolved'])}")
    return 0


def resolvable(subsys):
    led = _load()
    hits = [d for d in led["deferrals"].values()
            if d["status"] == "open" and subsys in d["resolved_by_subsys"]]
    print(f"reachability_ledger: auditing {subsys}-* can now close {len(hits)} deferral(s):")
    for d in hits:
        print(f"    {d['finding']} [{d['severity']}/{d['category']}] from {d['from_batch']}")
        print(f"       {d['note'][:120]}")
    print("  -> trace each caller; run consumer_agreement on the shared symbol; "
          "then `resolve --finding <id>`.")
    return 0


def resolve(fid, by):
    led = _load()
    if fid not in led["deferrals"]:
        print(f"no deferral {fid}"); return 1
    led["deferrals"][fid]["status"] = "resolved"
    led["deferrals"][fid]["resolved_by_batch"] = by
    _save(led)
    print(f"reachability_ledger: {fid} marked resolved" + (f" by {by}" if by else ""))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("report")
    r = sub.add_parser("resolvable"); r.add_argument("--subsys", required=True)
    v = sub.add_parser("resolve"); v.add_argument("--finding", required=True); v.add_argument("--by", default="")
    a = ap.parse_args()
    if a.cmd == "build":
        raise SystemExit(build())
    if a.cmd == "report":
        raise SystemExit(report())
    if a.cmd == "resolvable":
        raise SystemExit(resolvable(a.subsys))
    raise SystemExit(resolve(a.finding, a.by))


if __name__ == "__main__":
    main()
