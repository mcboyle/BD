#!/usr/bin/env python3
"""
template_drift_report.py — compare a review candidate / reviewed template against
the proven gold template and report what drifted.

This is the turnkey form of the inline drift check in
REPTYLE_CAPTURE_RUNBOOK §4: it covers what that heredoc skipped — the `api`
block, `network_patterns`, the `download.row_selectors` set, and gaps in the
resolution ladder — and prints a single verdict.

Read-only (never writes a template). stdlib-only, so plain `python3` runs it on
stash; no venv needed.

Usage:
    python3 tools/template_drift_report.py <candidate.json> [--gold <path>]

If --gold is omitted it defaults to the proven gold backup
(templates/reviewed/<host>.template.json.bak) if present, else the live reviewed
template (templates/reviewed/<host>.template.json). Host is taken from the
candidate's "host" field.

Exit code: 0 = no drift, 1 = drift detected, 2 = usage/IO error.
"""
import argparse
import json
import os
import sys

SELECTOR_GROUPS = ("login", "player", "quality", "download")


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _canon(x):
    """Stable string form for set comparison of patterns (str or dict)."""
    if isinstance(x, (dict, list)):
        return json.dumps(x, sort_keys=True, ensure_ascii=False)
    return str(x)


def _default_gold(cand):
    host = cand.get("host") or cand.get("hostname") or cand.get("site")
    if not host:
        return None
    bak = f"templates/reviewed/{host}.template.json.bak"
    live = f"templates/reviewed/{host}.template.json"
    return bak if os.path.exists(bak) else live


def diff_selectors(cand, gold, out):
    """Per-key drift within each selector group (row_selectors handled separately)."""
    drift = 0
    cs_all = cand.get("selectors") or {}
    gs_all = gold.get("selectors") or {}
    for grp in sorted(set(cs_all) | set(gs_all) | set(SELECTOR_GROUPS)):
        cs = cs_all.get(grp) or {}
        gs = gs_all.get(grp) or {}
        if not isinstance(cs, dict) or not isinstance(gs, dict):
            continue
        keys = (set(cs) | set(gs)) - {"row_selectors"}
        for k in sorted(keys):
            cv, gv = cs.get(k), gs.get(k)
            if cv != gv:
                drift += 1
                if cv is None:
                    out.append(f"  [{grp}.{k}] REMOVED in capture (gold had: {gv!r})")
                elif gv is None:
                    out.append(f"  [{grp}.{k}] NEW in capture: {cv!r}")
                else:
                    out.append(f"  [{grp}.{k}] CHANGED\n      capture: {cv!r}\n      gold:    {gv!r}")
    return drift


def diff_row_selectors(cand, gold, out):
    cr = ((cand.get("selectors") or {}).get("download") or {}).get("row_selectors") or []
    gr = ((gold.get("selectors") or {}).get("download") or {}).get("row_selectors") or []
    cset, gset = set(map(_canon, cr)), set(map(_canon, gr))
    drift = 0
    out.append(f"  count: capture={len(cr)}  gold={len(gr)}")
    only_g = gset - cset
    only_c = cset - gset
    if only_g:
        drift += len(only_g)
        out.append(f"  MISSING from capture ({len(only_g)}):")
        for s in sorted(only_g):
            out.append(f"      - {s}")
    if only_c:
        drift += len(only_c)
        out.append(f"  NEW in capture ({len(only_c)}):")
        for s in sorted(only_c):
            out.append(f"      + {s}")
    if not only_g and not only_c:
        out.append("  no drift (row_selectors identical)")
    return drift


def diff_resolutions(cand, gold, out):
    cr = cand.get("resolutions") or []
    gr = gold.get("resolutions") or []
    drift = 0
    out.append(f"  capture: {cr}")
    out.append(f"  gold:    {gr}")
    missing = [r for r in gr if r not in cr]
    extra = [r for r in cr if r not in gr]
    if missing:
        drift += len(missing)
        out.append(f"  MISSING rungs (in gold, not capture): {missing}  "
                   "<- likely the manifest body wasn't captured; replay so the player streams")
    if extra:
        drift += len(extra)
        out.append(f"  EXTRA rungs (in capture, not gold): {extra}")
    if not missing and not extra and cr != gr:
        out.append(f"  same rungs, different order (capture order: {cr})")
    if not missing and not extra and cr == gr:
        out.append("  no drift (ladder identical)")
    return drift


def diff_api(cand, gold, out):
    ca = cand.get("api") or {}
    ga = gold.get("api") or {}
    drift = 0
    for k in sorted(set(ca) | set(ga)):
        cv, gv = ca.get(k), ga.get(k)
        if cv != gv:
            drift += 1
            out.append(f"  [api.{k}] CHANGED\n      capture: {cv!r}\n      gold:    {gv!r}")
    if not drift:
        out.append("  no drift (api block identical)")
    return drift


def diff_network_patterns(cand, gold, out):
    cp = cand.get("network_patterns") or []
    gp = gold.get("network_patterns") or []
    cset, gset = set(map(_canon, cp)), set(map(_canon, gp))
    drift = 0
    only_g = gset - cset
    only_c = cset - gset
    out.append(f"  count: capture={len(cp)}  gold={len(gp)}")
    if only_g:
        drift += len(only_g)
        out.append(f"  MISSING from capture ({len(only_g)}):")
        for s in sorted(only_g):
            out.append(f"      - {s}")
    if only_c:
        drift += len(only_c)
        out.append(f"  NEW in capture ({len(only_c)}):")
        for s in sorted(only_c):
            out.append(f"      + {s}")
    if not only_g and not only_c:
        out.append("  no drift (network_patterns identical)")
    return drift


def main(argv=None):
    ap = argparse.ArgumentParser(description="Report drift of a template candidate vs the gold template.")
    ap.add_argument("candidate", help="path to the review candidate / template to check")
    ap.add_argument("--gold", help="path to the gold template (default: backup, else live reviewed)")
    args = ap.parse_args(argv)

    try:
        cand = _load(args.candidate)
    except (OSError, ValueError) as e:
        print(f"error: cannot read candidate {args.candidate}: {e}", file=sys.stderr)
        return 2

    gold_path = args.gold or _default_gold(cand)
    if not gold_path:
        print("error: no --gold given and candidate has no 'host' to derive one", file=sys.stderr)
        return 2
    try:
        gold = _load(gold_path)
    except (OSError, ValueError) as e:
        print(f"error: cannot read gold {gold_path}: {e}", file=sys.stderr)
        return 2

    host = cand.get("host") or gold.get("host") or "?"
    print("=" * 70)
    print(f"  Template drift report — host {host}")
    print(f"  candidate: {args.candidate}")
    print(f"  gold:      {gold_path}")
    print("=" * 70)

    total = 0
    for title, fn in (
        ("SELECTORS (login/player/quality/download triggers)", diff_selectors),
        ("DOWNLOAD ROW_SELECTORS (modal-scoped)", diff_row_selectors),
        ("RESOLUTION LADDER", diff_resolutions),
        ("API BLOCK", diff_api),
        ("NETWORK_PATTERNS", diff_network_patterns),
    ):
        out = []
        total += fn(cand, gold, out)
        print(f"\n-- {title} --")
        for line in out:
            print(line)

    print("\n" + "=" * 70)
    if total == 0:
        print("  VERDICT: NO DRIFT — the capture reproduced the gold template.")
    else:
        print(f"  VERDICT: {total} drift point(s) — eyeball each; keep the fresh capture")
        print("  where the site genuinely moved, keep gold where the capture missed it.")
    print("=" * 70)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
