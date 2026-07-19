#!/usr/bin/env python3
"""validation_corpus.py — view and append to the permanent validation ledger.

  summary:  validation_corpus.py summary
            The failure-accumulation readout: what the framework has been wrong
            about, which assumption kinds fail most, predictive confidence caps
            and sensitivity flags, the perturbation-rule ledger, model changes,
            and pending corrections.

  list:     validation_corpus.py list [--category C] [--outcome O]
            List entries, optionally filtered.

  add:      validation_corpus.py add --subject S --category C --outcome O \\
              --prediction P --observation OBS --evidence E --version V \\
              [--basis-kind B] [--model-change M] [--notes N]
            Append one validation event (append-only; id + date auto-assigned).

Recognition-only: entries are prose metadata about predictions and outcomes —
no capture bytes, no signing values.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import validation_corpus as vc  # noqa: E402


def _cmd_summary(args):
    s = vc.summarize(vc.load_corpus(args.path))
    print(f"VALIDATION CORPUS — {s['total_events']} events  {s['by_outcome']}")
    print(f"  by category: {s['by_category']}")
    print("\n  WRONG ABOUT:")
    for f in s["falsifications"]:
        print(f"    {f['id']} {f['subject']} (basis {f['basis_kind']}) "
              f"-> {f['model_change']}")
    print(f"  falsification by basis_kind: {s['falsification_by_basis_kind']}")
    print("\n  PERTURBATION RULE LEDGER:")
    for rule, led in s["perturbation_rule_ledger"].items():
        print(f"    {rule}: {led}")
    print(f"\n  predictive confidence caps: "
          f"{[(c['id'], c['outcome']) for c in s['confidence_caps']]}")
    print(f"  predictive sensitivity flags: "
          f"{[(c['id'], c['outcome']) for c in s['sensitivity_flags']]}")
    print("\n  MODEL CHANGES (shipped):")
    for m in s["model_changes"]:
        print(f"    {m['id']} {m['subject']} -> {m['model_change']}")
    print("  PENDING corrections:")
    for p in s["pending_corrections"]:
        print(f"    {p['id']} {p['subject']} -> {p['model_change']}")


def _cmd_list(args):
    entries = vc.query(vc.load_corpus(args.path),
                       category=args.category, outcome=args.outcome)
    for e in entries:
        print(f"{e['id']} [{e['outcome']:9s}] [{e['category']:16s}] "
              f"{e['subject']}  (v{e.get('version')})")
        print(f"     predicted: {e['prediction']}")
        print(f"     observed:  {e['observation']}")
        if e.get("model_change"):
            print(f"     -> {e['model_change']}")


def _cmd_add(args):
    entry = {
        "subject": args.subject, "category": args.category,
        "outcome": args.outcome, "prediction": args.prediction,
        "observation": args.observation, "evidence": args.evidence,
        "version": args.version,
    }
    for k, v in (("basis_kind", args.basis_kind),
                 ("model_change", args.model_change), ("notes", args.notes)):
        if v:
            entry[k] = v
    e = vc.append_entry(entry, args.path)
    print(f"appended {e['id']}: {e['subject']} -> {e['outcome']}")


def _cmd_debt(args):
    r = vc.debt_report(vc.load_corpus(args.path))
    cp = r["checkpoint"]
    print("CORPUS DEBT REPORT\n")
    print("CORRECTION debt (known wrong):")
    for e in r["correction_debt"]:
        print(f"   {e['id']} {e['subject']} ({e['outcome']}) -> {e['model_change']}")
    print("   none" if not r["correction_debt"] else "")
    print("CAPABILITY debt (conservatively incomplete):")
    for e in r["capability_debt"]:
        print(f"   {e['id']} {e['subject']} -> {e['model_change']}")
    print("   none" if not r["capability_debt"] else "")
    print("VALIDATION debt (not yet challenged):")
    for e in r["validation_debt"]:
        print(f"   {e['id']} {e['subject']}")
    print("   none" if not r["validation_debt"] else "")
    print(f"\n  evidence with no action item: "
          f"{[e['id'] for e in r['evidence_without_action_item']] or 'none'}")
    print(f"  at clean correction checkpoint: {cp['at_clean_correction_checkpoint']}"
          f"  (capability gaps: {cp['open_capability_gaps']}, "
          f"validation debt: {cp['validation_debt_items']})")


def main(argv=None):
    p = argparse.ArgumentParser(description="validation corpus")
    p.add_argument("--path", default=None, help="corpus path (default: repo root)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary").set_defaults(func=_cmd_summary)
    sub.add_parser("debt").set_defaults(func=_cmd_debt)
    ls = sub.add_parser("list")
    ls.add_argument("--category")
    ls.add_argument("--outcome")
    ls.set_defaults(func=_cmd_list)
    a = sub.add_parser("add")
    for f in ("subject", "category", "outcome", "prediction", "observation",
              "evidence", "version"):
        a.add_argument(f"--{f}", required=True)
    a.add_argument("--basis-kind", dest="basis_kind")
    a.add_argument("--model-change", dest="model_change")
    a.add_argument("--notes")
    a.set_defaults(func=_cmd_add)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
