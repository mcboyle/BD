# Code-intelligence method and current tool boundary

This document describes the review method. It is not a task register or an
implementation queue. Current work exists only in
`project-knowledge/IMPROVEMENT_BACKLOG.md`.

## Method

Machine extraction supplies deterministic facts and graph projections. Reviewers
spend judgment on intent, invariants, contracts, security boundaries, and risk.
Confirmed defects receive a focused RED reproducer before a fix. Findings and
historical completion evidence remain distinct: a finding citation proves that a
finding existed, not that an entire audit batch completed.

The durable machine surfaces are the dependency/function/import graphs, root
`INVARIANTS.json`, executable defect patterns, and the current analysis frontends.
The historical 58-batch evidence census is
`project-knowledge/AUDIT_COMPLETION_LEDGER.json`.

## Current frontends

- `toolchain/bin/bd-coverage-map` is built and launches
  `tools/coverage_map.py`.
- `tools/semantic_diff.py`, `tools/reachability.py`,
  `tools/differential_oracle.py`, and `tools/fuzz_harness.py` are built and have
  focused tests.
- These five analyzers are standalone. They are intentionally not wired into a
  composite release gate without a separate integration decision; see
  `docs/code-intelligence/ANALYSIS_FRONTENDS.md`.

The historical command names `bd-review-next`, `bd-finding`, `bd-invariant`, and
`bd-dup` are absent. `toolchain/bin/bd-invariant-engine` is a separately named
built capability, not proof that `bd-invariant` exists. Do not restore absent
names merely to satisfy an old plan.

## Current residual ownership

- Backlog 139 owns reviewed, AST-bound `bd-defect-scan` suppressions.
- Backlog 140 is closed by the exact historical audit-evidence census; it does
  not claim that unknown batches completed.
- Any future analyzer integration must begin as a newly measured atomic backlog
  row, not as resurrection of the retired umbrella program.

## Review contract

1. Bind every result to exact source SHA/tree and a nonzero denominator.
2. Treat tool output as leads until a deterministic reproducer validates it.
3. Keep read-only discovery parallel and behavioral fixes serial through one
   integrator.
4. Preserve raw failures; retries diagnose but do not erase them.
5. Generate current facts from source rather than copying volatile counts into
   standing prose.
