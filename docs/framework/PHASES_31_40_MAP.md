# Phases 31-40 — what was built vs. what already existed

The Phase 31-40 proposal overlaps substantially with phases already delivered. Rebuilding
those under new filenames would add duplicate code that can drift out of sync — exactly what
the Phase 28 governance scan exists to catch. So six of the ten phases and all three proposed
dashboards are mapped to the existing tool that already does the job, and only the four
genuinely-new phases were built.

## Built new (in `tools/validation_harness.py`)

- **Phase 32 — decision consistency** (`consistency`): determinism check (identical evidence
  → identical verdict) + threshold-edge sensitivity (verdicts whose score sits within epsilon
  of a band boundary, where a tiny change would flip the conclusion). New capability.
- **Phase 33 — benchmark harness** (`benchmark`): freezes a canonical baseline of the
  framework's conclusions and measures stability on re-run. This is the snapshot the existing
  Phase-14/34 `regress` tool compares against — complementary, not duplicative.
- **Phase 35 — evidence acquisition planner** (`acquire`): "if only N captures can be run,
  which N reduce uncertainty most?" — composes existing signals into a budgeted plan, with
  validation-debt campaigns on a separate top-priority track. New optimization.
- **Phase 37 — release readiness** (`release`): a single go/no-go gate aggregating
  calibration + benchmark + regression + governance + risk + debt + maturity. New roll-up;
  the natural consumer of 32 and 33.

## Already exists — use the existing tool, do not rebuild

- **Phase 31 (confidence calibration)** → `trust_intelligence.py calibrate` (Phase 13).
  Same inputs, same overconfidence/underconfidence/reliability-by-subsystem outputs.
- **Phase 34 (regression of verdicts)** → `trust_intelligence.py regress` (Phase 14).
  Produces `verdict_change_queue.json` with the expected/benign/suspicious/regression framing.
- **Phase 36 (assumption challenge)** → `meta_intelligence.py assumptions` (Phase 19) for
  least-tested / highest-blast-radius, plus the `concentration` cross-cut for single-evidence-
  chain assumptions.
- **Phase 38 (change impact)** → `trust_intelligence.py simulate` (Phase 17). Previews the
  impact of a proposed change on maturity/confidence/queues without applying it.
- **Phase 39 (evidence coverage)** → `meta_intelligence.py blindspots` (Phase 24 cross-cut)
  for under-/over-studied and heavily-assumed areas, plus `ops_intelligence.py family` (Phase
  12) for family coverage gaps.
- **Phase 40 (sustainability)** → `operator_layer.py sustainability` (Phase 30 cross-cut) for
  workload/queue/capture/maintenance burden and scaling constraints.

## Proposed dashboards — already covered

- **Trust dashboard** → the Phase 25 `operator_layer.py cockpit` already surfaces calibration/
  risk/readiness signals; the new Phase 37 `release` scorecard is the readiness one-pager.
- **Evidence dashboard** → the Phase 35 `acquire` plan + existing capture-priority (Phase 9)
  and debt/freshness views already constitute this.
- **Sustainability dashboard** → the Phase 30 `sustainability` + `bottlenecks` cross-cuts.

If a literal single-file dashboard combining these is wanted later, it should be a thin
read-only view that *reads* these existing artifacts — not a reimplementation of their logic.

## Posture
All four new subcommands fail closed on a posture scan, keep the corpus and debt read-only
(measured at 35 entries on 2026-07-22, debt 0/0/2), and change nothing. Recognition-only,
human-gated, no replay, no new detector behavior.
