# BulkDownloader OPV Validation Report

**Validation date:** 2026-07-21

**Overall result:** COMPLETE / PASS

**Deployment target:** `stash` — `/home/mboyle/BulkDownloader`

**Validated deployed commit:** `a68a6427cd612464e109cf78b1ab91f9858d1e1f`

**Validated build stamp:** `206687984e0e`

**Release archive SHA-256:** `8D8EC2FB0171FA755900C579D4DE4D7D39C7F25A5B7FDD6A00575F8D86B99E29`

> The deployed archive is built from the exact merged revision above. PRs #7
> through #12 are merged; the final release was installed on `stash` and
> validated against the running service.

## Executive result

The requested OPV implementation, corpus processing, telemetry corrections,
widget validation, live download proof, deployment acceptance, and GitHub
preservation work completed successfully. No failed acceptance tests, hung
workers, protocol errors, Critical review findings, or Important review
findings remained at handoff.

## Acceptance evidence

| Validation | Result |
|---|---:|
| Authoritative deployed capture (60 workers) | **12,628 total / 12,624 passed / 0 failed / 4 intentional skips** |
| Final live acceptance lane | **22 passed / 13 environment warnings / 0 failed** |
| Explicit private-corpus lane | **110 passed / 0 failed / 0 skipped** |
| Disposable PostgreSQL integration lane | **38 passed** |
| Post-merge backend compatibility set | **287 passed** |
| Frontend | Complete Vitest suite passed; production build passed |
| Widget catalog | **36/36 widgets** present and testable |
| Independent final review | **0 Critical / 0 Important findings** |
| GitHub CI after conflict resolution | `gates` passed; `postgres-integration` passed |

## Live deployment proof

- `bulkdownloader.service`: active and enabled.
- `ollama.service`: active.
- Health API: `ok=true`, `db_ok=true`.
- Queue after final reboot: 962 pending, zero active downloads.
- Sites loaded: 1.
- Earlier operator download proof observed 620,849.6 bytes/second, with the
  same active `.part` file growing **10,531,878 bytes in 20 seconds**.
- Hung workers: none.
- Protocol errors: zero.
- Action-required, stuck, retry, and captcha widget counts were zero at final
  sampling.
- The queue survived the live L28 service restart: 200 sampled URLs were
  preserved.
- Final live catalog: 35 checks, 22 pass, 13 environment warnings, 0 failures.

## Ollama and L19 GPU validation

The original L19 failure was an infrastructure issue: the Tesla T4 was not
visible in the guest, so `qwen2.5:7b` ran on CPU at about 0.745 output tokens
per second and hit the classifier's 15-second provider timeout. After restoring
VMware PCI passthrough:

- Tesla T4 detected with 15,360 MiB VRAM and NVIDIA driver 580.159.03.
- Ollama selected CUDA compute 7.5 and reported `100% GPU` offload.
- Configured `qwen2.5:7b` produced about 29.8 output tokens/second after load.
- `/api/ai/classify` returned HTTP 200 with `submit_btn`, confidence 100.
- Focused L19 passed in 1.654 seconds; the final full-catalog run passed L19 in
  1.726 seconds.

All six installed models were exercised with the same JSON classification
prompt and a 60-second wall-clock cap. `qwen2.5:7b`, `qwen2.5vl:7b`, Qwythos
Q6_K, and Qwythos Q4_K_M returned valid JSON. The larger Qwythos MTP Q5_K_M
and Q8_0 variants exceeded the cold-load cap. The configured model remains
`qwen2.5:7b`; no smaller model was installed and no AI configuration was
changed.

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

## Why 4 tests remain skipped

The final release-tree run has only four intentional environment gates:

- Three raw-capture development checks are inapplicable because raw capture
  artifacts are deliberately absent from release archives.
- One real network-namespace check requires root privileges and `iproute2`.

Private-corpus coverage ran explicitly from owner-only regular and strict
fixture roots, and PostgreSQL coverage ran against the dedicated local
`postgres:16-alpine` lane; neither remains hidden behind default skips in the
reported acceptance totals.

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
11. Preserved Unix executable modes in release ZIPs and excluded the root
    `.env.example` from production archives.
12. Hardened high-concurrency session and telemetry behavior, then refreshed
    the generated function index.
13. Restored Tesla T4 passthrough to the guest, enabling CUDA offload and
    turning the L19 AI text roundtrip from a timeout into a sub-two-second pass.

## Git and recovery state

- Final deployed merge: `a68a6427cd612464e109cf78b1ab91f9858d1e1f`.
- Merged PRs: #7, #8, #9, #10, #11, and #12.
- Final archive: `BulkDownloader_v3_66_811.zip`.
- Remote rollback archive:
  `/home/mboyle/BulkDownloader/BulkDownloader_v3_66_811.zip.pre-a68a642`.
- GitHub `gates` and `postgres-integration` checks passed on the merged work.
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

**Live service: HEALTHY; queue preserved; no active transfer at final sample.**

**GitHub preservation: COMPLETE.**

**PR integration: COMPLETE; CI GREEN; changes merged through PR #12.**
