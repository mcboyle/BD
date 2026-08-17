# Complete the 42 Open Backlog Rows — Program Design

**Status:** Approved design

**Date:** 2026-08-17

**Canonical task authority:** `project-knowledge/IMPROVEMENT_BACKLOG.md`

**Starting state:** `main` at `3e8de4ff763a4c0942547ca39322e54ae2cc14c8`, tree `058af611c010dbadef926468db2c3d2daef37969`, version `3.66.1170`

## Objective

Adjudicate all 42 rows that are OPEN in the canonical backlog. Work is complete for a row only when current evidence supports one of these states:

- `CLOSED`: the acceptance criteria were implemented or directly proved;
- `MOOT`: re-derivation proves the subject no longer exists or is no longer desired;
- `OPEN` with an operator runbook: every safe deterministic step is complete, but a named live, credentialed, operator, or time-bound predicate remains unavailable.

The program must not manufacture code changes for stale proposals, relax acceptance criteria to consume old evidence, or treat a merged PR as proof that every assigned row closed.

## Program Shape

Use nine medium-sized cuts. This balances throughput against exact-SHA invalidation, reviewer load, rollback size, and dependency ordering. Execute the fastest evidence and authority corrections first, then enabling infrastructure, then high-ROI verification work, and finally live/operator evidence.

Each cut begins from current `origin/main`, re-derives every assigned row, owns one coherent subject, and produces a separate reviewed candidate and PR. A row may move to a later cut if discovery proves that its implementation overlaps a different ownership boundary; the move must be recorded in the canonical backlog and program evidence rather than silently dropped.

## Cut A — Backlog Truth and Stale-Current Adjudication

**Rows:** 112, 134, 135, 136, 137, 138, 140, 141, 143

Rebuild or retire the architecture inventory; re-derive stale improvement, audit, governance, and knowledge-hygiene programs; correct code-intelligence documentation; reconstruct the line-audit completion ledger; adjudicate stale-current assertions; and identify CLOSED rows whose text hides unfinished work.

This cut comes first because it is primarily measurement and documentation, and because later work must not inherit false task state. Broad historical programs may become `MOOT`; genuinely current residuals must be represented atomically in the canonical backlog.

## Cut B — Freshness and Historical-Evidence Closure

**Rows:** 106, 114, 160

Extend freshness coverage to nested task-bearing documentation, migrate remaining actionable audit findings before retiring their old report, and re-derive the missing twelfth legacy tool before retiring the complete measured set.

This cut consumes Cut A's current-document classification. Denominators must be derived independently; no guessed tool name or hand-maintained nested-document list may certify completeness.

## Cut C — Gate-Scope and Declaration Integrity

**Rows:** 109, 129, 133, 152

Make affected-band wiring stable across test-file renames, verify `BD_GATE_SCOPE` semantically, adjudicate pre-policy scope-baseline entries, and verify footgun severity/status declarations against current enforcement behavior.

Runtime consumers may derive from canonical authorities, but tests must retain independent nonzero denominators so deletion or relabeling cannot certify itself.

## Cut D — Failure Retention and Identity-Safe Cleanup

**Rows:** 104, 132, 150, 151, 162

First resolve the failed-run retention-versus-cleanup policy. Then make garbage collection and orphan reaping identity-safe; include `.bdrm-*`, `bdcut_*`, and nested partial files in exact bounded denominators; and inject interruption, rename, verification, and deletion failures.

Row 132 is the policy dependency for the other four. Do not ship cleanup behavior while retention and forensic ownership remain contradictory. Filesystem-security tests must run on the persistent filesystem used by production, not tmpfs or overlayfs.

## Cut E — Release-Path Retirement and Sandbox Residue

**Rows:** 115, 161

Retire `scripts/build_release.sh` only after moving all live consumers to the supported release implementation. After that retirement is complete, perform the sandbox-path sweep in three measured phases: executable defaults, current operator prose, and hand-adjudicated test fixtures.

The dependency is strict: row 115 precedes row 161. Historical citations and adversarial fixtures remain when they are the subject; blanket string replacement is forbidden.

## Cut F — Test-Assurance Expansion

**Rows:** 26, 27, 28, 118, 130, 131

Expand vacuous-test detection beyond constant expressions; add evidence-backed over-sensitivity checks; build a filesystem-write recorder; make mutation specifications reproducible and tracked; detect shifts within source windows; and measure the serial full-suite population and outcomes once.

The serial suite is a distinct diagnostic denominator. It does not replace fixed canonical `-n 24` without a separate promotion proof. New detectors must include mutation or adversarial controls demonstrating that they catch their claimed defect without rejecting valid tests.

## Cut G — Reviewed Defect Suppressions

**Row:** 139

Add fail-closed suppressions for `bd-defect-scan`. Each suppression binds detector ID, repository-relative path, normalized-AST SHA-256, and rationale. Semantic edits make the finding reappear. Malformed, duplicate, stale, or unreadable records fail closed, and positive detector controls remain active.

This is separate because it creates security-sensitive machine authority and deserves an isolated review and rollback boundary.

## Cut H — Capture and Authenticated-Scene Evidence

**Rows:** 119, 120, 121, 122, 123, 124, 125, 126, 163

Begin with a read-only, hashed inventory of `/home/mboyle/captures/`. Map existing WACZs, redacted WACZs, templates, drafts, and metadata to exact acceptance predicates before requesting a new live capture. Existing material includes Reptyle authenticated captures and broad template-onboarding/corpus evidence, but filenames alone are not provenance.

The campaign covers challenge settle/resume, signed JWPlayer template evidence, cross-origin login, explicit detector-cleared/resume observation, the Reptyle API-pattern criterion, enrichment slices A6.2/A6.3, selector-kind review, and authenticated host-alias acceptance. A legacy criterion may be retired only by an explicit evidence-backed decision, never silently relaxed because retained evidence does not meet it.

Unredacted captures remain outside the repository. Only minimum necessary derived, redacted, non-secret evidence may enter a candidate.

## Cut I — Operator and Service-Bound Decisions

**Rows:** 127, 128, 164

Adjudicate whether to start the PostgreSQL soak, decide whether OPV-F3.1 closes on retained evidence or restarts, and make capture observe and gate the AI companion without making the main service wait for model warm-up.

Row 127 may remain OPEN after its enabling cut because its acceptance requires a two-week soak. Starting a clock is not completing it. Row 128 requires an operator decision; elapsed time alone cannot choose between closure and restart. Row 164 must bound and expose companion retry contention while retaining sequential text/vision and GPU-runtime proof semantics.

## Evidence Deferral and Operator Runbooks

When required evidence is unavailable from `/home/mboyle/captures/` or safely accessible systems:

1. Complete every safe deterministic implementation, test, inventory, and offline-analysis step.
2. Record exact base, candidate, parent, tree, environment, and selected evidence identities.
3. Name the unresolved acceptance predicate and why it remains UNKNOWN.
4. Leave the canonical backlog row `OPEN`.
5. Write an operator runbook at `/home/mboyle/agent-runs/backlog-42/operator-runbooks/<row-id>-<subject>.md`.
6. Continue to the next dependency-eligible row rather than blocking the entire program.

Each runbook records the exact target, prerequisites, required operator or credential action, commands or UI sequence, expected observable events, evidence artifacts and hashes, redaction rules, pass/fail/UNKNOWN interpretation, rollback, cleanup, and the steps needed to update the canonical backlog.

Runbooks are non-authoritative execution guidance. They must state that `project-knowledge/IMPROVEMENT_BACKLOG.md` remains the sole task authority.

## Per-Cut Execution Contract

Every cut must:

1. Fetch the official origin and bind discovery to exact `main` SHA/tree.
2. Re-derive every assigned row against current source, history, tests, documents, generated artifacts, packaging, CI, deployment consumers, captures, and external/operator dependencies.
3. Record a complete nonzero acceptance denominator and explicit ownership/collision map.
4. Add focused RED tests or deterministic failure evidence before behavioral implementation.
5. Keep one authoritative integrator and one coherent candidate.
6. Run focused, affected, generated/freshness, release, frontend/build, packaging, and canonical fixed `-n 24` lanes as applicable.
7. Preserve complete raw statuses, denominators, clean pre/post state, logs, hashes, and superseded failures.
8. Obtain independent implementation/security, test-integrity/denominator, and CI/evidence reviews.
9. Require exact-head GitHub CI and a current PR body before merge.
10. Prove the merged tree equals the reviewed tree.
11. Deploy only when runtime, source delivery, generated runtime artifacts, or deployment state changes; verify `/api/health`, version, merged SHA, database state, service state, and `GET / = 200`.
12. Update backlog rows only with exact evidence, then write terminal program evidence and continue to the next eligible cut.

Stage-A draft evidence may reject a bad candidate early but never substitutes for final-SHA evidence. Missing, malformed, stale, wrong-SHA, truncated, zero-denominator, or transport-failed evidence is `UNKNOWN/HOLD`.

## Scheduling and Efficiency

Cuts are sequential at the integration boundary, but read-only discovery, capture classification, test mapping, and independent review preparation may run concurrently on immutable bases. Cut H capture inventory can be prepared early, but its repository integration follows Cuts A–G so it consumes current authorities and test machinery.

The fixed `-n 24` suite remains canonical. Verification lanes run concurrently on isolated fleet hosts where their semantics permit it. No concurrent lane shares a writable checkout, virtual environment, HOME, TMPDIR, cache, database, ports, generated directory, or result directory.

The performance target is the maximum duration of mandatory parallel gates, not their sum. A behavioral blocker found before final freeze invalidates only affected draft evidence; any final-candidate change invalidates all applicable final evidence.

## Program-Level Completion

The program is terminal when:

- every one of the starting 42 OPEN IDs has a current re-derivation record;
- every row is `CLOSED`, `MOOT`, or remains `OPEN` with a complete operator runbook;
- no assigned row disappears, duplicates, or moves without a recorded mapping;
- all merged cuts have exact-SHA tests, reviews, CI, merge-tree proof, and applicable deployment proof;
- the canonical backlog metadata and ordered-ID digest reconcile;
- a final report lists each original ID, disposition, evidence, PR/merge/deployment state, and any remaining operator action;
- the report verifies that no runbook or evidence index became a competing task authority.

The program does not require every row to become CLOSED. It requires every row to be honestly adjudicated as far as available evidence permits.
