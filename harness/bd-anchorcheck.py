#!/usr/bin/env python3
"""bd-anchorcheck -- prove every tracked mutant anchor still occurs exactly once.

WHY THIS EXISTS. On 2026-08-31 three separate cuts moved source text that a
mutation spec anchors on, and each cost a full CI round trip to discover:

  * v3.66.1363 split the CI shard matrix, and row310's M5 anchor -- three
    literal lines of ci.yml -- occurred zero times afterwards.
  * Historical label 212 rewrote _ssh_argv in toolchain/bin/bd-jobs, and
    N12's anchor went to zero.
  * Row 387 added two import edges, a different gate, same shape: a tree-wide
    judgement that bd-band-derive can never select.

The gates are RIGHT to refuse; a stale anchor means the mutant no longer
judges the line it was written for, so the battery silently stops testing
something. The defect is only that the refusal arrives from CI minutes later
instead of from the candidate tree in seconds.

WHAT IT DOES NOT DO. It does not decide whether a NEW anchor is legitimate --
tests/test_row357_mutant_anchors_are_not_fragile.py owns that judgement, and
duplicating it here would create a second authority that can disagree. This
answers exactly one question, the cheap one: does every anchor still resolve?

  bd-anchorcheck.py [--work DIR] [--json]

Exit 0 = every anchor resolves exactly once.
Exit 1 = at least one anchor is stale (reports each with its spec and label).
Exit 2 = CANNOT-EVALUATE: no specs found, or a spec is unreadable/malformed.
         A zero denominator is UNKNOWN, never OK (CLAUDE.md A7).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _mutants(spec: dict | list) -> list[dict]:
    if isinstance(spec, dict) and "mutants" in spec:
        return spec["mutants"]
    if isinstance(spec, list):
        return spec
    raise ValueError("spec is neither a mutant list nor an object carrying one")


def check(work: Path) -> tuple[int, dict]:
    spec_dir = work / "tests" / "mutants"
    if not spec_dir.is_dir():
        return 2, {"error": f"no mutant spec directory at {spec_dir}"}
    specs = sorted(spec_dir.glob("*.json"))
    if not specs:
        return 2, {"error": f"zero mutant specs under {spec_dir} -- UNKNOWN, not OK"}

    sources: dict[str, str] = {}
    checked = 0
    stale: list[dict] = []
    unreadable: list[dict] = []

    for spec_path in specs:
        rel_spec = str(spec_path.relative_to(work))
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            mutants = _mutants(spec)
        except Exception as exc:  # a malformed spec is UNKNOWN, not "no anchors"
            unreadable.append({"spec": rel_spec, "error": f"{type(exc).__name__}: {exc}"})
            continue

        for mutant in mutants:
            target = mutant.get("file")
            label = mutant.get("label", "<unlabelled>")
            if not target:
                unreadable.append({"spec": rel_spec, "label": label,
                                   "error": "mutant names no file"})
                continue
            if target not in sources:
                path = work / target
                if not path.is_file():
                    unreadable.append({"spec": rel_spec, "label": label,
                                       "error": f"subject {target} does not exist"})
                    sources[target] = None  # type: ignore[assignment]
                    continue
                sources[target] = path.read_text(encoding="utf-8", errors="surrogateescape")
            source = sources[target]
            if source is None:
                continue

            if "old" in mutant:
                occurrences = source.count(mutant["old"])
                field = "old"
            elif "old_regex" in mutant:
                try:
                    occurrences = len(re.findall(mutant["old_regex"], source))
                except re.error as exc:
                    unreadable.append({"spec": rel_spec, "label": label,
                                       "error": f"invalid regex anchor: {exc}"})
                    continue
                field = "old_regex"
            else:
                unreadable.append({"spec": rel_spec, "label": label,
                                   "error": "mutant carries neither old nor old_regex"})
                continue

            checked += 1
            if occurrences != 1:
                stale.append({"spec": rel_spec, "label": label, "file": target,
                              "field": field, "occurrences": occurrences})

    report = {"work": str(work), "specs": len(specs), "anchors_checked": checked,
              "stale": stale, "unreadable": unreadable}
    if not checked:
        report["error"] = "zero anchors checked -- UNKNOWN, not OK"
        return 2, report
    if unreadable:
        return 2, report
    return (1 if stale else 0), report


def _comment_spans(source: str):
    """Character spans of every COMMENT token, or None if the file cannot tokenize.

    Row 532. bd-mutate finds its anchor by TEXT and never asks whether the text
    is executable, so an anchor can resolve exactly once onto prose: the mutation
    edits a comment, behaviour is unchanged, the catcher passes, and the battery
    records a caught regression it never caused. A STRING is deliberately not
    prose -- a string literal is executable source and mutating one is real.
    """
    import io, tokenize
    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    spans = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((line_starts[tok.start[0] - 1] + tok.start[1],
                              line_starts[tok.end[0] - 1] + tok.end[1]))
    except Exception:
        return None
    return spans


def check_prose(work: Path) -> tuple[int, dict]:
    """Does any python-subject anchor resolve ONLY into a comment?

    Measured clean on 2026-08-31: 536 anchors, zero offenders. The repository
    gate is tests/test_row532_a_mutant_anchor_must_resolve_into_code.py; this is
    the 0.7-second local mirror so bd-denom-preflight refuses it before a
    thirteen-minute verify does.
    """
    spec_dir = work / "tests" / "mutants"
    if not spec_dir.is_dir():
        return 2, {"error": f"no mutant spec directory at {spec_dir}"}
    sources: dict[str, str | None] = {}
    spans: dict[str, list | None] = {}
    examined = 0
    offenders: list[dict] = []
    for spec_path in sorted(spec_dir.glob("*.json")):
        rel_spec = str(spec_path.relative_to(work))
        try:
            mutants = _mutants(json.loads(spec_path.read_text(encoding="utf-8")))
        except Exception:
            continue
        for mutant in mutants:
            target = mutant.get("file")
            if not target or not target.endswith(".py"):
                continue
            if target not in sources:
                path = work / target
                src = (path.read_text(encoding="utf-8", errors="surrogateescape")
                       if path.is_file() else None)
                sources[target] = src
                spans[target] = _comment_spans(src) if src is not None else None
            src, sp = sources[target], spans[target]
            if src is None or sp is None:
                continue
            if "old" in mutant:
                offs = [m.start() for m in re.finditer(re.escape(mutant["old"]), src)]
            elif "old_regex" in mutant:
                try:
                    offs = [m.start() for m in re.finditer(mutant["old_regex"], src)]
                except re.error:
                    continue
            else:
                continue
            if not offs:
                continue                      # resolution is check()'s question
            examined += 1
            if all(any(a <= o < b for a, b in sp) for o in offs):
                offenders.append({"spec": rel_spec, "label": mutant.get("label", "?"),
                                  "file": target})
    report = {"examined": examined, "offenders": offenders}
    if not examined:
        # NOT UNKNOWN. "Resolves only into a comment" is a property of anchors
        # that RESOLVE, and resolution is check()'s question -- which is already
        # refusing if none do. Returning UNKNOWN here reported the same defect
        # twice and masked the stale-anchor verdict underneath it, which a
        # sibling test caught immediately. An empty denominator is recorded and
        # judged by the check that owns it.
        report["note"] = ("no python-subject anchor resolved, so there is nothing "
                          "for this check to judge; resolution is check()'s question")
        return 0, report
    return (1 if offenders else 0), report


def check_catchers(work: Path) -> tuple[int, dict]:
    """Does every mutant's declared CATCHER still exist?

    Added 2026-08-31 after row348's M4 broke. Resolution alone is not enough: a
    mutant whose anchor resolves but whose catcher test has been renamed or
    deleted is a mutation battery that can never fail, which is the fail-open
    shape this tool exists to refuse. A missing catcher is UNKNOWN, never OK.
    """
    spec_dir = work / "tests" / "mutants"
    if not spec_dir.is_dir():
        return 2, {"error": f"no mutant spec directory at {spec_dir}"}
    specs = sorted(spec_dir.glob("*.json"))
    if not specs:
        return 2, {"error": f"zero mutant specs under {spec_dir} -- UNKNOWN, not OK"}
    sources: dict[str, str | None] = {}
    checked = 0
    broken: list[dict] = []
    unreadable: list[dict] = []
    for spec_path in specs:
        rel_spec = str(spec_path.relative_to(work))
        try:
            mutants = _mutants(json.loads(spec_path.read_text(encoding="utf-8")))
        except Exception as exc:
            unreadable.append({"spec": rel_spec, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for mutant in mutants:
            catcher = mutant.get("catcher")
            label = mutant.get("label", "<unlabelled>")
            if not catcher:
                continue                      # not every spec declares one
            # A node id may be class-qualified: path::Class::test_name. Split on
            # the FIRST separator for the path and take the LAST segment as the
            # function -- an earlier version looked for "def Class::test_name("
            # and reported five perfectly good catchers as broken.
            rel, _, node = catcher.partition("::")
            node = node.rsplit("::", 1)[-1] if node else node
            if not node:
                unreadable.append({"spec": rel_spec, "label": label,
                                   "error": f"catcher names no test node: {catcher!r}"})
                continue
            # Count it as EXAMINED before the file lookup. An earlier version
            # only counted catchers whose file existed, so a spec whose one
            # catcher file had vanished reported "zero catchers checked --
            # UNKNOWN" and buried the named BROKEN entry underneath it. The
            # question was answered; the answer was bad.
            checked += 1
            if rel not in sources:
                path = work / rel
                sources[rel] = (path.read_text(encoding="utf-8", errors="surrogateescape")
                                if path.is_file() else None)
            source = sources[rel]
            if source is None:
                broken.append({"spec": rel_spec, "label": label, "catcher": catcher,
                               "why": "catcher file does not exist"})
                continue
            bare = node.split("[")[0]         # drop a parametrisation id
            if not re.search(r"def " + re.escape(bare) + r"\s*\(", source):
                broken.append({"spec": rel_spec, "label": label, "catcher": catcher,
                               "why": "catcher test is not defined in that file"})
    report = {"work": str(work), "specs": len(specs), "catchers_checked": checked,
              "broken": broken, "unreadable": unreadable}
    if not checked:
        report["error"] = "zero catchers checked -- UNKNOWN, not OK"
        return 2, report
    if unreadable:
        return 2, report
    return (1 if broken else 0), report


def co_changed(work: Path, base: str) -> tuple[int, dict]:
    """Which mutant specs and their subjects changed in the SAME cut?

    That co-change is the exact shape that cost 20 minutes on 2026-08-31: the
    cut deleted the line row348's M4 was anchored to. This does not decide
    anything on its own -- resolution and catcher existence do -- but a named
    pair tells the operator WHERE to look instead of leaving them to read a
    pytest traceback about a spec they did not know they had touched.
    """
    import subprocess
    cp = subprocess.run(["git", "-C", str(work), "diff", "--name-only", base],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        return 2, {"error": f"cannot diff against {base}: {cp.stderr.strip()[:200]}"}
    changed = {line for line in cp.stdout.splitlines() if line}
    pairs = []
    for spec_path in sorted((work / "tests" / "mutants").glob("*.json")):
        rel_spec = str(spec_path.relative_to(work))
        try:
            mutants = _mutants(json.loads(spec_path.read_text(encoding="utf-8")))
        except Exception:
            continue
        subjects = {m.get("file") for m in mutants if m.get("file")}
        hit = sorted(subjects & changed)
        if hit and (rel_spec in changed or hit):
            pairs.append({"spec": rel_spec, "subjects_changed": hit,
                          "spec_changed": rel_spec in changed})
    return 0, {"base": base, "changed": len(changed), "co_changed": pairs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--catchers", action="store_true",
                    help="also prove every declared catcher test still exists")
    ap.add_argument("--base", default=None, metavar="REF",
                    help="report mutant specs whose subject changed since REF")
    args = ap.parse_args(argv)
    work = Path(args.work).resolve()

    rc, report = check(work)

    extra_rc = 0
    catcher_report = None
    prose_report = None
    if args.catchers:
        extra_rc, catcher_report = check_catchers(work)
        report["catchers"] = catcher_report
        prose_rc, prose_report = check_prose(work)
        report["prose"] = prose_report
        extra_rc = max(extra_rc, prose_rc)
    if args.base:
        _, co = co_changed(work, args.base)
        report["co_changed"] = co
        if co.get("error"):
            extra_rc = max(extra_rc, 2)

    if args.json:
        print(json.dumps(report, indent=1))
        return max(rc, extra_rc)

    if args.base:
        co = report["co_changed"]
        if co.get("error"):
            print(f"CANNOT-EVALUATE co-change: {co['error']}")
        else:
            pairs = co["co_changed"]
            print(f"co-change vs {co['base']}: {len(pairs)} spec(s) whose subject this cut touches")
            for pair in pairs:
                mark = "spec+subject" if pair["spec_changed"] else "subject only"
                print(f"  {pair['spec']}  [{mark}]  {', '.join(pair['subjects_changed'])}")
            if pairs:
                print("  ^ a mutant anchored into one of those files is the 2026-08-31 shape;")
                print("    the resolution and catcher checks below are what actually decide.")

    if prose_report is not None:
        if prose_report.get("error"):
            print(f"CANNOT-EVALUATE prose: {prose_report['error']}")
        else:
            print(f"code-resolving anchors: {prose_report['examined']} examined")
            for o in prose_report["offenders"]:
                print(f"  PROSE {o['spec']}::{o['label']} -> {o['file']}: "
                      f"resolves only inside a COMMENT")
            if prose_report["offenders"]:
                print("  ^ mutating a comment changes no behaviour; the catcher passes")
                print("    and the battery records a regression it never caused.")

    if catcher_report is not None:
        if extra_rc == 2:
            print(f"CANNOT-EVALUATE catchers: {catcher_report.get('error', 'unreadable specs')}")
            for u in catcher_report.get("unreadable", [])[:20]:
                print(f"  {u.get('spec')}::{u.get('label', '')} -- {u['error']}")
        else:
            print(f"catchers: {catcher_report['catchers_checked']} checked")
            for b in catcher_report["broken"]:
                print(f"  BROKEN {b['spec']}::{b['label']} -> {b['catcher']}: {b['why']}")
            if catcher_report["broken"]:
                print("  ^ a mutant whose catcher does not exist can never fail.")

    if rc == 2:
        print(f"CANNOT-EVALUATE: {report.get('error', 'unreadable mutant specs')}")
        for u in report.get("unreadable", [])[:20]:
            print(f"  {u.get('spec')}::{u.get('label', '')} -- {u['error']}")
        return rc
    print(f"anchors: {report['anchors_checked']} checked across {report['specs']} spec(s)")
    if rc == 0:
        print("ANCHORCHECK OK -- every anchor occurs exactly once")
        return max(rc, extra_rc)
    print(f"ANCHORCHECK FAIL -- {len(report['stale'])} stale anchor(s):")
    for s in report["stale"]:
        print(f"  {s['spec']}::{s['label']}")
        print(f"     {s['file']}: {s['field']} occurs {s['occurrences']} times, expected 1")
    print("\nA cut that moves anchored source must re-anchor its mutant IN THE SAME CUT,")
    print("or restructure so the anchored text survives byte-identical. Note that")
    print("tests/test_row357_mutant_anchors_are_not_fragile.py accepts a NEW anchor")
    print("only when it is structural, so re-anchoring onto quoted values will refuse.")
    return max(1, extra_rc)


if __name__ == "__main__":
    sys.exit(main())
