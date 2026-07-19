"""dd-score-audit -- audit the deep_detect scoring heuristics across the
fixture corpus.

Walks every fixture, runs `deep_detect()` (offline path), and reports:

  1. Score distribution per source_type (count, min, max, median).
  2. best_download vs runner-up score gap. Small gaps flag brittle
     decisions where a single weight change could flip the winner.
  3. Same-score collisions across DIFFERENT source_types — the
     existing tiebreak (`_dedup_candidates` first-occurrence,
     INV-DEDUP-TIEBREAK) relies on the flatten order to break ties
     deterministically, so seeing collisions here is a signal that
     ordering, not score, is doing the work.
  4. Score outliers: candidates emitted with score <= 0 (almost
     certainly noise that escaped a filter), or score > 500 (above
     the v3.66.12 sanity ceiling — flag for review).

Usage:
    python3 tools/dd-score-audit.py                # human-readable
    python3 tools/dd-score-audit.py --json         # machine-readable
    python3 tools/dd-score-audit.py --only NAME    # filter by fixture

Exit codes:
    0 — audit complete (regardless of findings; this is a report, not
        a gate). Pass --strict to make outliers fail (exit 1).
    2 — corpus missing / runtime error.

Added: v3.66.13 (P11). Reuses tools/dd-replay.py's runner so the same
normalization applies.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(REPO_ROOT))

# Sanity bounds. Tune as scoring evolves.
SCORE_FLOOR = 0       # below this is "noise that escaped the filter"
SCORE_CEILING = 500   # above this is "weights got out of hand"


def _replay_module():
    """Lazy-load tools/dd-replay.py (hyphenated filename)."""
    spec = importlib.util.spec_from_file_location(
        "dd_replay", TOOLS_DIR / "dd-replay.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"can't load {TOOLS_DIR/'dd-replay.py'}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect(only: str | None) -> dict[str, dict]:
    """Run every fixture through deep_detect and return
    {fixture_name: report}."""
    replay = _replay_module()
    fixtures = replay._list_fixtures(only)
    if not fixtures:
        raise FileNotFoundError(
            f"no fixtures in {replay.CORPUS_DIR}")
    return {d.name: replay._run_fixture(d) for d in fixtures}


def _audit(reports: dict[str, dict]) -> dict[str, Any]:
    """Compute the audit. Returns a structured result; callers render
    it as text or JSON."""
    # 1. Per-source-type histograms across the whole corpus.
    scores_by_type: dict[str, list[int]] = defaultdict(list)
    for fixture, report in reports.items():
        for c in (report.get("buckets") or {}).get("accepted") or report.get("download_candidates") or []:
            scores_by_type[c.get("source_type", "?")].append(
                int(c.get("score") or 0))

    histogram = {}
    for source_type, scores in sorted(scores_by_type.items()):
        scores_sorted = sorted(scores)
        histogram[source_type] = {
            "count": len(scores),
            "min": min(scores),
            "max": max(scores),
            "median": int(statistics.median(scores_sorted)),
            "mean": round(statistics.mean(scores_sorted), 1),
            # All distinct scores observed for this type (small list
            # — useful to eyeball clustering).
            "distinct": sorted(set(scores)),
        }

    # 2. best_download vs runner-up gap, per fixture.
    gaps = []
    for fixture, report in reports.items():
        cands = (report.get("buckets") or {}).get("accepted") or report.get("download_candidates") or []
        if len(cands) < 2:
            continue
        best_score = int(cands[0].get("score") or 0)
        next_score = int(cands[1].get("score") or 0)
        gap = best_score - next_score
        gaps.append({
            "fixture": fixture,
            "best_score": best_score,
            "next_score": next_score,
            "gap": gap,
            "best_source": cands[0].get("source_type"),
            "next_source": cands[1].get("source_type"),
        })

    # 3. Same-score collisions across different source_types.
    # For each fixture, walk pairs of candidates and flag any same-
    # score pair where source_type differs.
    collisions = []
    for fixture, report in reports.items():
        cands = (report.get("buckets") or {}).get("accepted") or report.get("download_candidates") or []
        by_score: dict[int, list[dict]] = defaultdict(list)
        for c in cands:
            by_score[int(c.get("score") or 0)].append(c)
        for score, group in by_score.items():
            types = {c.get("source_type") for c in group}
            if len(types) > 1:
                collisions.append({
                    "fixture": fixture,
                    "score": score,
                    "types": sorted(t for t in types if t),
                    "n_candidates": len(group),
                })

    # 4. Floor/ceiling outliers.
    outliers_floor = []
    outliers_ceiling = []
    for fixture, report in reports.items():
        for c in (report.get("buckets") or {}).get("accepted") or report.get("download_candidates") or []:
            score = int(c.get("score") or 0)
            if score <= SCORE_FLOOR:
                outliers_floor.append({
                    "fixture": fixture,
                    "score": score,
                    "source_type": c.get("source_type"),
                    "url": c.get("url"),
                })
            if score > SCORE_CEILING:
                outliers_ceiling.append({
                    "fixture": fixture,
                    "score": score,
                    "source_type": c.get("source_type"),
                    "url": c.get("url"),
                })

    return {
        "corpus_size": len(reports),
        "histogram": histogram,
        "gaps": sorted(gaps, key=lambda g: g["gap"]),
        "collisions": collisions,
        "outliers": {
            "floor": outliers_floor,    # score <= SCORE_FLOOR
            "ceiling": outliers_ceiling,  # score >  SCORE_CEILING
        },
        "thresholds": {
            "floor": SCORE_FLOOR,
            "ceiling": SCORE_CEILING,
        },
    }


def _render_text(audit: dict[str, Any]) -> None:
    print(f"corpus: {audit['corpus_size']} fixtures\n")

    print("─── Score histogram by source_type ───")
    if not audit["histogram"]:
        print("  (no download candidates in any fixture)")
    else:
        # column widths so things line up
        for st, stats in audit["histogram"].items():
            print(f"  {st:18s}  n={stats['count']:3d}  "
                  f"min={stats['min']:4d}  max={stats['max']:4d}  "
                  f"med={stats['median']:4d}  mean={stats['mean']:6.1f}  "
                  f"distinct={stats['distinct']}")
    print()

    print("─── best vs runner-up gap (sorted, narrow first) ───")
    if not audit["gaps"]:
        print("  (no fixture has ≥2 candidates)")
    else:
        for g in audit["gaps"]:
            tag = "  brittle" if g["gap"] <= 10 else ""
            print(f"  {g['fixture']:30s}  "
                  f"best={g['best_score']:4d} ({g['best_source']:15s})  "
                  f"next={g['next_score']:4d} ({g['next_source']:15s})  "
                  f"Δ={g['gap']:4d}{tag}")
    print()

    print("─── Same-score collisions across source_types ───")
    if not audit["collisions"]:
        print("  (none — every same-score tie is within one source_type)")
    else:
        for c in audit["collisions"]:
            print(f"  {c['fixture']:30s}  score={c['score']:4d}  "
                  f"types={c['types']}  ({c['n_candidates']} cands)")
    print()

    print(f"─── Outliers ─── "
          f"(floor ≤{audit['thresholds']['floor']}, "
          f"ceiling >{audit['thresholds']['ceiling']})")
    floor = audit["outliers"]["floor"]
    ceiling = audit["outliers"]["ceiling"]
    if not floor and not ceiling:
        print("  (no outliers)")
    else:
        for o in floor:
            print(f"  FLOOR    {o['fixture']:30s}  "
                  f"score={o['score']:4d}  "
                  f"type={o['source_type']}  {o['url']}")
        for o in ceiling:
            print(f"  CEILING  {o['fixture']:30s}  "
                  f"score={o['score']:4d}  "
                  f"type={o['source_type']}  {o['url']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only", default=None,
                   help="filter fixtures by name substring")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 if any outliers (floor/ceiling) found")
    args = p.parse_args(argv)

    try:
        reports = _collect(args.only)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    audit = _audit(reports)

    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
    else:
        _render_text(audit)

    if args.strict and (audit["outliers"]["floor"]
                        or audit["outliers"]["ceiling"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
