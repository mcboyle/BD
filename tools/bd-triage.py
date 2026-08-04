#!/usr/bin/env python3
"""bd-triage -- the FP-suppression layer the battery cannot live without.

The raw battery over-reports (3223 findings tree-wide; 14 confirmed-FP in CAP-01
alone). Without suppression, every wave drowns and the ledger gets abandoned.
bd-triage consumes the `false_positive_confirmations[]` an audit emits, turns each
into a reusable suppression rule, and applies them across ANY findings stream so a
known-FP class is silenced tree-wide -- and crucially, surfaces only the findings
that are NEW and NOT yet triaged.

Subcommands:
  seed   --from-audits DIR     scan AUDIT_*.json -> append rules to TRIAGE_RULES.json
  apply  --findings F.json      suppress matching findings; write FILTERED + report
  new    --findings F --baseline B   show findings present now, absent in baseline,
                                     and not suppressed (the genuinely-new surface)

A rule matches a finding when file matches AND (line in rule's line/line_range, if
given) AND (code/source/title-substr match, if given). Confirmed-FP sites become
exact (file,line) rules; class verdicts become file+class rules. Stdlib only.
"""
import argparse
import glob
import json
import os
import os as _os_bd, sys as _sys_bd
_sys_bd.path.insert(0, _os_bd.path.dirname(_os_bd.path.realpath(__file__)))
import bdtools_sec as sec

RULES = os.path.join(sec.DEFAULT_WORK, "project-knowledge", "TRIAGE_RULES.json")


def _load_rules():
    if os.path.exists(RULES):
        return json.load(open(RULES))
    return {"rules": []}


def _save_rules(r):
    json.dump(r, open(RULES, "w"), indent=1)


def _parse_at(at):
    """'path:line' or 'path:line-line' or 'path:a,b,c' -> (path, lines:set|None)."""
    if ":" not in at:
        return at, None
    path, _, ln = at.rpartition(":")
    lines = set()
    for part in ln.replace(",", " ").split():
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                lines.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                lines.add(int(part))
            except ValueError:
                return path, None
    return path, (lines or None)


def seed(from_dir):
    r = _load_rules()
    existing = {x["id"] for x in r["rules"]}
    added = 0
    for ap in sorted(glob.glob(os.path.join(from_dir, "AUDIT_*.json"))):
        a = json.load(open(ap))
        batch = a.get("batch", "?")
        for i, fp in enumerate(a.get("false_positive_confirmations", [])):
            path, lines = _parse_at(fp.get("at", ""))
            rid = f"TR-{batch}-{i:02d}"
            if rid in existing:
                continue
            r["rules"].append({
                "id": rid, "from_batch": batch,
                "match": {"file": path,
                          "line_set": sorted(lines) if lines else None,
                          "class": fp.get("scanner_class")},
                "verdict": fp.get("verdict", "FP"),
                "reason": fp.get("reason", ""),
                "triage_rule": fp.get("triage_rule", "")})
            added += 1
    _save_rules(r)
    print(f"bd-triage seed: +{added} rules (total {len(r['rules'])}) from {from_dir}")
    return 0


def _norm(f):
    return {"file": f.get("file") or f.get("path"),
            "line": f.get("line"),
            "code": f.get("code"), "source": f.get("source"),
            "title": f.get("title", "")}


def _matches(rule, f):
    m = rule["match"]
    if m.get("file") and f["file"] != m["file"]:
        return False
    ls = m.get("line_set")
    if ls and f["line"] not in set(ls):
        return False
    cls = m.get("class")
    if cls and cls not in (f.get("source") or "") and cls.lower() not in (f.get("title") or "").lower() \
            and cls not in (f.get("code") or ""):
        # class is advisory: if the rule has no line_set, require a class signal;
        # if it has a line_set, the line match already pinned it.
        if not ls:
            return False
    return True


def apply(findings_path):
    r = _load_rules()["rules"]
    doc = json.load(open(findings_path))
    findings = doc.get("findings", doc if isinstance(doc, list) else [])
    suppressed, surviving = [], []
    hit_by_rule = {}
    for raw in findings:
        f = _norm(raw)
        rule = next((R for R in r if _matches(R, f)), None)
        if rule:
            suppressed.append(raw)
            hit_by_rule[rule["id"]] = hit_by_rule.get(rule["id"], 0) + 1
        else:
            surviving.append(raw)
    out = findings_path.replace(".json", ".triaged.json")
    json.dump({"surviving": surviving, "suppressed_count": len(suppressed),
               "rules_applied": hit_by_rule}, open(out, "w"), indent=1)
    print(f"bd-triage apply: {len(findings)} findings -> suppressed={len(suppressed)} "
          f"surviving={len(surviving)} ({out})")
    if hit_by_rule:
        for rid, n in sorted(hit_by_rule.items(), key=lambda x: -x[1]):
            print(f"    {rid}: -{n}")
    # surviving-by-source summary (what a reviewer actually faces)
    from collections import Counter
    bysrc = Counter(_norm(x)["source"] for x in surviving)
    print("  surviving by source:", dict(bysrc))
    return 0


def new(findings_path, baseline_path):
    r = _load_rules()["rules"]
    cur = json.load(open(findings_path))
    base = json.load(open(baseline_path))

    def key(f):
        n = _norm(f)
        return (n["file"], n["line"], n["code"])
    cur_f = cur.get("findings", cur if isinstance(cur, list) else [])
    base_keys = {key(_norm(f)) for f in base.get("findings", base if isinstance(base, list) else [])}
    truly_new = []
    for raw in cur_f:
        f = _norm(raw)
        if key(f) in base_keys:
            continue
        if any(_matches(R, f) for R in r):
            continue
        truly_new.append(raw)
    print(f"bd-triage new: genuinely-new + un-suppressed findings = {len(truly_new)}")
    for raw in truly_new[:20]:
        f = _norm(raw)
        print(f"    {f['file']}:{f['line']} [{f['source']}/{f['code']}] {f['title'][:60]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed"); s.add_argument("--from-audits", default=os.path.join(sec.DEFAULT_WORK, "project-knowledge"))
    a = sub.add_parser("apply"); a.add_argument("--findings", required=True)
    n = sub.add_parser("new"); n.add_argument("--findings", required=True); n.add_argument("--baseline", required=True)
    args = ap.parse_args()
    if args.cmd == "seed":
        raise SystemExit(seed(args.from_audits))
    if args.cmd == "apply":
        raise SystemExit(apply(args.findings))
    raise SystemExit(new(args.findings, args.baseline))


def selftest():
    """DELEGATE: wraps TRIAGE_RULES.json. v3.66.799 -- context-aware
    candidates so ONE canonical body serves both the shipped tree and the
    sandbox bdsuite: script-dir and repo-root first, sandbox PK/bin as
    fallback. Where the rules file genuinely does not exist (stash ships
    no TRIAGE_RULES.json), this FAILS honestly -- a wrapper that cannot
    find what it wraps is broken, and a selftest that cannot verify must
    not report success."""
    import os as _o
    _here = _o.path.dirname(_o.path.realpath(__file__))
    cands = [_o.path.join(_here, "TRIAGE_RULES.json"),
             _o.path.join(_here, "..", "TRIAGE_RULES.json"),
             _o.path.join(_here, "..", "project-knowledge", "TRIAGE_RULES.json"),
             RULES]
    hit = next((p for p in cands if _o.path.isfile(p)), None)
    print(("PASS" if hit else "FAIL") +
          "  delegation target present: TRIAGE_RULES.json (%s)"
          % (hit or "NOT FOUND in script dir, repo root, PK, or bin"))
    print("SELFTEST PASS" if hit else "SELFTEST FAIL")
    return 0 if hit else 1


if __name__ == "__main__":
    import sys as _s
    if "--selftest" in _s.argv:
        raise SystemExit(selftest())
    main()
