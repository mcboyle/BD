# BulkDownloader OPV Validation Report

**Validation date:** 2026-07-21
**Overall result:** COMPLETE / PASS
**Deployment target:** `stash` — `/home/mboyle/BulkDownloader`
**Validated deployed commit:** `b60f58f0d25cbfb5d3bda07b81ee113e10650218`
**GitHub PR head:** `51c63341de697bb3f585055ba73f84e03fe8658b`
**Pull request:** `mcboyle/BD#7` — draft, CLEAN, MERGEABLE

> The deployed commit is the fully validated live OPV deployment. The later PR
> head merges the current GitHub `main` into the OPV branch. That merge passed
> local compatibility checks and GitHub CI but has not been deployed to `stash`.

## Executive result

The requested OPV implementation, corpus processing, telemetry corrections,
widget validation, live download proof, deployment acceptance, and GitHub
preservation work completed successfully. No failed acceptance tests, hung
workers, protocol errors, Critical review findings, or Important review
findings remained at handoff.

## Acceptance evidence

| Validation | Result |
|---|---:|
| Authoritative deployed capture | **12,610 total / 12,537 passed / 0 failed / 73 gated skips** |
| Live acceptance lane | **21 passed / 14 environment warnings / 0 failed** |
| Explicit private-corpus lane | **110 passed / 0 failed / 0 skipped** |
| Disposable PostgreSQL integration lane | **38 passed** |
| Post-merge backend compatibility set | **287 passed** |
| Frontend | Complete Vitest suite passed; production build passed |
| Widget catalog | **36/36 widgets** present and testable |
| Independent final review | **0 Critical / 0 Important findings** |
| GitHub CI after conflict resolution | `gates` passed; `postgres-integration` passed |

## Live deployment proof

- `bulkdownloader.service`: active.
- `bd-filthykings-quota.service`: active and enabled.
- Health API: `ok=true`, `db_ok=true`.
- Site `026255e0`: running.
- Queue: 963 pending, one active download.
- Workers: 1 active / 1 available.
- Sites: 1 active / 1 configured.
- Observed transfer rate: 620,849.6 bytes/second during final sampling.
- The same active `.part` file grew **10,531,878 bytes in 20 seconds**.
- Hung workers: none.
- Protocol errors: zero.
- Action-required, stuck, retry, and captcha widget counts were zero at final
  sampling.
- The site was restarted through `POST /api/sites/026255e0/start`; the export
  was intentionally left running.

## Chrome and widget verification

The final dashboard was tested through the user-controlled Chrome session.

- Desktop header showed 963 queued and 1 of 1 workers.
- Queue depth, throughput, worker capacity, active sites, action-required,
  stuck, retries, captcha, bandwidth, and average-speed widgets used live data.
- The widget library exposed all 36 catalog entries.
- A representative widget was added, rendered, removed, and the original
  selection was restored.
- Mobile viewport validation used 393 x 851 pixels.
- Mobile document width settled to the viewport width with no horizontal
  overflow.
- Browser warning/error log was empty.
- The dashboard was returned to normal desktop mode and left open.

## WACZ and JSON corpus processing

- WACZ files discovered: 1,086.
- Valid WACZ files: 1,083.
- Invalid archives retained for review: 3.
- Unique valid WACZ payloads: 626.
- External JSON files parsed: 1,100.
- Unique JSON payloads: 778.
- JSON parse failures retained for review: 2.
- Builder and normalization processing: 626/626.
- Classification: 181 `review_ready`; 445 `draft_review_required`.
- Raw fixture promotions: zero.
- Synthetic fixtures created: zero.
- Original corpus sources were not modified.

The initial strict processing run produced 95 passed and 15 semantic failures.
Those failures were classified as eight extra-strict over-redaction cases, six
incorrect source mappings, and one obsolete capture-specific literal contract.
The private fixture lane was isolated behind explicit absolute-root variables,
sources were remapped through the canonical project redactor and unchanged
privacy floor, and the final result was **110/110 passed**.

## Why 73 tests remain skipped by default

The skips are opt-in environment and privacy gates, not permanently disabled
coverage:

- 54 private-corpus cases run in the explicit 110-test private lane.
- PostgreSQL-specific cases run in the disposable database lane, which passed
  38 tests.
- Raw development inspection routes are intentionally absent from release
  builds.
- A real network-namespace test requires root privileges and `iproute2`.

## Main fixes that produced the passing result

1. Added generation fencing across stop/restart boundaries so stale workers
   cannot publish progress into a newer run.
2. Serialized lifecycle transitions and made queue claims and worker ownership
   cleanup atomic.
3. Rejected stale progress callbacks and overlapping start operations.
4. Reported progress during long downloads and kept health/status endpoints
   truthful while work is active.
5. Replaced placeholder widget data with live, time-windowed, site-scoped
   telemetry.
6. Corrected queue, throughput, worker, active-site, retry, captcha, stuck,
   success, and collection widget semantics.
7. Added dedicated synchronized captcha encounter tracking.
8. Scoped Site Detail widget requests to the selected site to prevent global
   data leakage.
9. Added deterministic browser/content fixtures, explicit private-corpus test
   roots, and a real PostgreSQL CI service.
10. Made the PIN index path format platform-independent and regenerated the
    endpoint, function, route, PIN, and dependency artifacts from source.

## Git and recovery state

- OPV implementation/deployment checkpoint: `b60f58f0d25cbfb5d3bda07b81ee113e10650218`.
- Conflict-resolution merge commit: `51c63341de697bb3f585055ba73f84e03fe8658b`.
- Remote branch: `codex/template-host-aliases`.
- Draft PR: https://github.com/mcboyle/BD/pull/7
- PR state at final verification: CLEAN and MERGEABLE.
- Local and remote branch SHAs matched.
- Tracked worktree changes were clean; operational review artifacts remained
  intentionally untracked.

## Reports already tracked in Git

- `.superpowers/sdd/wacz-processing-report.md` — detailed WACZ inventory,
  hashes, privacy checks, invalid archive handling, and first-pass concerns.
- `.superpowers/sdd/task-live-telemetry-7-report.md` — private-corpus failure
  taxonomy, fixture remapping, rollback hashes, and final 110/110 proof.
- `docs/superpowers/plans/2026-07-21-live-telemetry-and-widget-verification.md`
  — implementation and validation plan.

The older `.superpowers/sdd/task-live-telemetry-8-report.md` working file was
not tracked because it predates the final deployment commit and final acceptance
totals. This combined report supersedes it.

## Final disposition

**OPV operator validation: COMPLETE.**
**Live export: RUNNING.**
**GitHub preservation: COMPLETE.**
**PR conflict resolution: COMPLETE; CI GREEN; PR remains draft and unmerged.**
