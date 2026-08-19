#!/usr/bin/env python3
"""staleness -- Tier-3 (coverage ledger -> action) + Tier-4 (incremental re-audit).

Two subcommands turn "the ledger flips a flag" into "staleness is a visible,
decreasing worklist that blocks a release if it grows too big."

  incremental --changed f1,f2,...   (or --changed-file LIST)
      Flip the changed files that were 'reviewed' back to 'unreviewed' in the
      ledger and emit a MINIMAL re-audit manifest containing only them. In a real
      repo the changed set comes from `git diff --name-only <last_audited_sha>`;
      here it is supplied, so the MECHANISM is exercised independent of git.

  stale [--threshold N] [--core-subsys CAP,REC,RUN,AUTH]
      Regenerate STALE.md (which batches/files need re-reading) and run the
      release gate: rc!=0 if the count of UNREVIEWED files in a CORE subsystem
      exceeds the threshold (a release blocker, not a per-cut blocker).

Stdlib only. Reads the staged ledger + audit_manifests.
"""
import argparse
import json
import os
import sys

ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.path.join(ROOT, "review")
ART = os.path.join(REVIEW, "artifacts")
STATE = os.path.join(ART, "REVIEW_STATE.json")
MAN = os.path.join(REVIEW, "audit_manifests")
STALE_MD = os.path.join(REVIEW, "STALE.md")


def _load_state():
    return json.load(open(STATE))


def _save_state(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def _file_to_batch():
    """Map each production file -> its batch from the manifests."""
    m = {}
    for f in sorted(glob_txt()):
        batch = os.path.basename(f)[:-4]
        for line in open(f):
            p = line.strip()
            if p:
                m[p] = batch
    return m


def glob_txt():
    import glob
    return glob.glob(os.path.join(MAN, "*.txt"))


def incremental(changed):
    s = _load_state()
    files = s["files"]
    flipped, manifest = [], []
    for p in changed:
        rec = files.get(p)
        if rec is None:
            continue
        manifest.append(p)
        if rec.get("status") == "reviewed":
            rec["status"] = "unreviewed"
            rec.pop("audit_of_record", None)
            rec["reaudit_reason"] = "source changed since last audit"
            flipped.append(p)
    # recompute totals
    statuses = [v.get("status") for v in files.values()]
    s["totals"]["reviewed"] = sum(1 for x in statuses if x == "reviewed")
    s["totals"]["unreviewed"] = sum(1 for x in statuses if x != "reviewed")
    _save_state(s)
    out = os.path.join(MAN, "REAUDIT.txt")
    with open(out, "w") as fh:
        fh.write("\n".join(manifest) + ("\n" if manifest else ""))
    print(f"staleness incremental: changed={len(changed)} "
          f"flipped_reviewed->unreviewed={len(flipped)} "
          f"reaudit_manifest={out} ({len(manifest)} files)")
    if flipped:
        print("  flipped:", ", ".join(flipped))
    print(f"  ledger now: reviewed={s['totals']['reviewed']} "
          f"unreviewed={s['totals']['unreviewed']}")
    return 0


def stale(threshold, core_subsys):
    s = _load_state()
    files = s["files"]
    f2b = _file_to_batch()
    # group unreviewed by subsystem + batch
    by_sub = {}
    for p, rec in files.items():
        if rec.get("status") == "reviewed":
            continue
        batch = f2b.get(p, "UNASSIGNED")
        sub = batch.split("-")[0]
        by_sub.setdefault(sub, {}).setdefault(batch, []).append(p)

    lines = ["# STALE — re-audit worklist", "",
             "*Regenerated each cut. Files whose source changed since their last "
             "audit, or never audited. A decreasing worklist, not a silent flag.*", ""]
    core_unrev = 0
    for sub in sorted(by_sub):
        n = sum(len(v) for v in by_sub[sub].values())
        mark = " **(CORE)**" if sub in core_subsys else ""
        if sub in core_subsys:
            core_unrev += n
        lines.append(f"## {sub}{mark} — {n} unreviewed")
        for batch in sorted(by_sub[sub]):
            lines.append(f"- `{batch}` — {len(by_sub[sub][batch])} files")
        lines.append("")
    with open(STALE_MD, "w") as fh:
        fh.write("\n".join(lines))

    reviewed = s["totals"].get("reviewed", 0)
    total = len(files)
    over = core_unrev > threshold
    print(f"staleness stale: reviewed={reviewed}/{total} "
          f"core_unreviewed={core_unrev} threshold={threshold} -> "
          f"{'RELEASE-BLOCK' if over else 'ok-for-release'}")
    print(f"  wrote {STALE_MD}")
    print(f"  NOTE: this is a RELEASE gate (not a per-cut gate) -- a cut may land "
          f"with stale files; a release may not if core surface exceeds threshold.")
    return 1 if over else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("incremental")
    pi.add_argument("--changed", default="")
    pi.add_argument("--changed-file")
    ps = sub.add_parser("stale")
    ps.add_argument("--threshold", type=int, default=0)
    ps.add_argument("--core-subsys", default="CAP,REC,RUN,AUTH")
    a = ap.parse_args()
    if a.cmd == "incremental":
        changed = [x.strip() for x in a.changed.split(",") if x.strip()]
        if a.changed_file:
            changed += [l.strip() for l in open(a.changed_file) if l.strip()]
        sys.exit(incremental(changed))
    else:
        sys.exit(stale(a.threshold, set(a.core_subsys.split(","))))


if __name__ == "__main__":
    main()
