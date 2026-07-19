# Phases 31-40 — usage (four new tools; the rest map to existing tools)

`tools/validation_harness.py` adds the four genuinely-new phases. See PHASES_31_40_MAP.md for
the six phases + three dashboards that already exist and which tool to use instead.

## Phase 32 — decision consistency
    python3 tools/validation_harness.py consistency --portfolio-root ./portfolio --epsilon 0.03
Outputs decision_consistency_report.md + unstable_decisions.json. Confirms determinism and
flags verdicts sitting on a band edge (a small evidence change would flip them).

## Phase 33 — benchmark harness
    # record a baseline:
    python3 tools/validation_harness.py benchmark --portfolio-root ./portfolio --out-dir ./bench
    # later, measure stability against it:
    python3 tools/validation_harness.py benchmark --portfolio-root ./portfolio \
        --baseline ./bench/benchmark_scorecard.json --out-dir ./bench2
Outputs benchmark_results.md + benchmark_scorecard.json. This is the canonical snapshot the
Phase-14/34 regress tool compares against.

## Phase 35 — evidence acquisition planner
    python3 tools/validation_harness.py acquire --portfolio-root ./portfolio --budget 3
Outputs evidence_acquisition_plan.md + prioritized_capture_campaigns.json. Picks the N
captures that reduce uncertainty most; validation-debt campaigns are a separate top track.

## Phase 37 — release readiness
    python3 tools/validation_harness.py release \
        --confidence-calibration calib.json --benchmark-scorecard bench.json \
        --verdict-changes verdict_changes.json --governance-findings gov.json \
        --risk-register risk.json --framework-scorecard scorecard.json
Outputs release_readiness_report.md + release_scorecard.json. Single go/no-go gate; critical
risk, governance non-compliance, or verdict regression are blockers.

## Boundaries (enforced)
All four fail closed on a posture scan, keep corpus + debt read-only, and change nothing.
Recognition-only, human-gated, no replay, no new detector behavior.
