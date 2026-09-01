# Batch-cut plan for the open register, 2026-09-01

Produced by the `triage-open-rows` workflow (7 parallel subsystem readers, one
synthesis pass) against origin/main at v3.66.1388. It claimed 71 rows across 7
subsystems, 60 distinct, and produced 8 cuts and 24 named exclusions.

NOT STARTED. Operator ruling 2026-09-01: no backlog item is to be started; this
file exists so the analysis is not lost and so the next session does not pay for
it again.

RE-DERIVE BEFORE USING IT. The triage read a register snapshot taken BEFORE
v3.66.1388 merged, so row 481 is listed in cut 4 and row 479 in cut 6 and BOTH
ARE ALREADY CLOSED. Roughly half of a stale register's OPEN rows turn out to be
closed or mis-scoped (CLAUDE.md A1), and this file is a snapshot of a judgement,
not a measurement.

## The cuts, in order

Cuts 1, 2 and 3 are file-disjoint and form the first concurrent wave. Cut 4 is
strictly after cut 1: both own `staging_claim.py` and
`tests/test_part_staging_collision.py`, so the whole staging cluster is one
serial lane.

### 1. `staging-claim-release-lifetime` -- rows 492, 523, 489, 506, 507

**Contract.** Every path that ends, aborts, or cleans up a transfer releases exactly the staging claims and .part artifacts it can prove it owns, and reports what it actually released.

**Primary files.** `bulk_downloader/staging_claim.py`, `bulk_downloader/runner_transport.py`, `bulk_downloader/crash_recovery.py`, `bulk_downloader/app_crash_recovery.py`, `bulk_downloader/cleanup_helpers.py`, `bulk_downloader/app_sites_queue.py`, `tests/test_part_staging_collision.py`

**Why these are one piece of work.** All five are the release half of one object's lifetime: 492 changes staging_claim.release's signature to carry an ownership proof, 523 walks every post-reservation exit of do_download to reach that release, 489 moves the browser-fallback release to the caller exit its .part outlives, 506 makes a leaked claim visible to the sweeps, and 507 stops a bulk-delete route reporting a count for artifacts it never freed. Batching them is what dissolves their own warnings: 492's note forbids sequencing it behind 489/523 without re-derivation, and 506's note requires it land after the signature change -- one cut with one writer satisfies both without a measurement pass. Verified on HEAD 68a76384: release() at staging_claim.py:277 with call sites at runner_transport.py:824,832,1291,1319,1330,1422,1426,1878 and crash_recovery.py:219, so 523's structural test has a real nonzero denominator. This is the largest structural change in the staging cluster, so it goes first and every later staging cut rebases onto it rather than the reverse. 492's frontend/src/hooks/useOpsControls.ts touch is only the crash_recovery scan/act fetch wrappers (checked at HEAD), a mechanical follow needing no vitest lane. Detachable row if RED proves too heavy: 506, which still leaves a legal 4.

### 2. `vault-unreadable-is-not-absent` -- rows 436, 437, 488, 514, 495, 520

**Contract.** A vault or extension-secret read that could not complete is reported as locked, unreadable, or throttled -- never as 'nothing stored', 'nothing paired', or 'nothing to purge'.

**Primary files.** `bulk_downloader/app_secrets.py`, `bulk_downloader/secrets_store.py`, `bulk_downloader/extension_vault.py`, `bulk_downloader/vpn_config.py`, `tests/test_row432_unreadable_vault_is_not_uninitialized.py`, `frontend/src/components/ui/SecretsUsageList.tsx`

**Why these are one piece of work.** One mechanism, SecretsUnreadableError, read correctly at six consumers: the fetch_one route (436 serving a password without its throttle record, 437 auditing a locked vault as no-password-stored), the usage list (488 answering no-secrets-stored over a vault nothing read), its own gate module (514, whose unreadable arm currently measures a zero reference denominator), and the VPN consumers (495 reading an unmeasured inventory as permission to delete, 520 reporting a tunnel removal successful with its secrets unpurged). Batching is forced rather than merely legal: 488/514/495/520 all edit tests/test_row432_unreadable_vault_is_not_uninitialized.py and 436/437 share the /api/secrets/extension/fetch_one seam, so any split leaves rows that cannot run concurrently anyway and whose independent fixes conflict. Cross-subsystem on labels only -- 495/520 are tagged capture-vpn but share secrets_store.py and the row432 gate file with 488/514, which is the file-sharing arm of the batching rule. File-disjoint from cuts 1 and 3, so it runs in the first concurrent wave. Line cites in 436/437 have drifted (extension_vault.py:449 to ~532, app_secrets.py:784 to ~876) and must be re-read at the candidate base; no browser or live host needed anywhere in the cut.

### 3. `candidate-verdict-truth` -- rows 493, 526, 497, 472, 464

**Contract.** A candidate-tooling verdict states only what it actually measured, on the exact tree and identity it names, and never derives its answer from the artifact it is judging.

**Primary files.** `scripts/bd_integration_verdict.py`, `scripts/bd_candidate_adopt.py`, `scripts/bd_candidate_replay.py`, `toolchain/bin/bd-ci-verdict`, `tools/precut_check.py`, `tests/test_row407_integration_verdict.py`, `tests/test_row407_candidate_adopt.py`

**Why these are one piece of work.** Five instances of one defect shape in the cut-verification toolchain: a verdict returning true over measurements it never took (493), the same verdict carrying no base/tree/host identity so it is not transferable evidence (526), adoptability derived from the artifact under judgement (497), an advisory reviewer left inside the merge denominator so a non-gate can decide green (472), and a predictor's guess carried inside a gate tool as if measured (464). 493 and 526 edit the same two files and must be one cut or strictly sequenced; putting them together removes that constraint. Confirmed at HEAD: bd_integration_verdict.py is blob 0cafdaf3 and bd_candidate_adopt.py blob 56937218, both unchanged from their cited refs, so those line cites hold; bd_candidate_replay.py moved (3a864f8e to 51b331a8) so 497's replay-side lines are the one re-read this cut owes. 464's DEPENDENCY 463 is satisfied in substance -- I confirmed _derive_baseline at toolchain/bin/bd-precut:61 and tests/test_row463_precut_derives_its_baseline.py are both on HEAD -- so this cut may precede the register cut that formally closes 463. Touches no application Python and no register row bodies, so it is file-disjoint from cuts 1 and 2 and completes the first concurrent wave. 472's RED uses a recorded GitHub rollup fixture, not a live PR; 464's blocking consumer bd-verify-cut.sh lives outside the repo, so the cut must state that no repository gate covers the consumer side.

### 4. `staging-claim-identity` -- rows 481, 483, 528, 501

**Contract.** A staging claim proves the bytes it guards are this resource for this job before those bytes are resumed, promoted, or left to wedge a name.

**Primary files.** `bulk_downloader/staging_claim.py`, `bulk_downloader/runner_transport.py`, `bulk_downloader/resume.py`, `bulk_downloader/crash_recovery.py`, `bulk_downloader/ramdisk_stage.py`, `tests/test_part_staging_collision.py`

**Why these are one piece of work.** The reserve/claim half of the same object cut 1 fixed the release half of: 481 mints a reservation over foreign bytes and calls it a resume, 483 treats a claim match as proof the staged bytes are this resource, 528 leaves a zero-byte claim that wedges every candidate name in its family forever, and 501 writes the bytes to a ramdisk path no claim guards at all. All four edit staging_claim.py and the one gate file tests/test_part_staging_collision.py, which is why they cannot run concurrent with cut 1 and are ordered strictly after it -- the whole staging cluster is one serial lane on that file and on runner_transport.py. 501 stays in rather than being excluded: ramdisk_stage.py carries its own release() wrapper at line 184 calling into the same claim, so excluding it would leave the cut's contract false for one staging arm; flag the precondition that it needs a real tmpfs to exercise use_ramdisk_stage (test5 has /dev/shm) and record UNKNOWN rather than skipping if the lane cannot provide one. 483's acceptance explicitly forbids fixing it by demanding a validator, and 528's negative control pins calls["n"] == 2 -- both are per-row assertions inside one shared harness, not separate contracts.

### 5. `wire-truth` -- rows 430, 428, 429, 431

**Contract.** Every byte count and completion verdict the transfer loop records is measured from the wire and from the durable file, never inferred from the disk or from a row that produced no file.

**Primary files.** `bulk_downloader/runner_transport.py`, `bulk_downloader/db.py`, `bulk_downloader/resume.py`, `bulk_downloader/runner.py`, `bulk_downloader/runner_integrity.py`

**Why these are one piece of work.** One seam -- what the transfer loop is entitled to claim about bytes it moved: 430 computes transferred as final_size minus _dl_initial_bytes so it measures the disk rather than the wire (re-verified live at runner_transport.py:2122 with _dl_initial_bytes = resume_from at :1806; only line numbers drifted from v3.66.1362), 428 promotes a .part on a 416 without proving it complete (the string Content-Range still occurs zero times in runner_transport.py at HEAD), 429 lets a done row that produced no file dedup every future download, and 431 checkpoints bytes still sitting in the worker write buffer. This cut resolves the 429-versus-485 placement question: 429 is a done-verdict-versus-disk-reality row and belongs here, while 485 shares the skip gate file and belongs in cut 6. Ordered before cut 6 because row 479 declares DEPENDENCY 430 -- the corrected wire measurement is the evidence 479's skip proof consumes. Serialized after cut 4 because runner_transport.py is the shared file across the whole transport chain. Risk row is 431, whose RED needs a SIGKILL of live workers plus os-visible-versus-buffered instrumentation; contingency if it will not converge is to drop 431, fold 430 into cut 6 as its fifth row, and defer 428/429 -- stated as a contingency, not pre-executed.

### 6. `skip-proof-evidence` -- rows 479, 503, 519, 485

**Contract.** A skip is only taken when recorded evidence proves the same work already produced the file that is still on disk, and any failure of that proof is classified rather than silently re-downloaded.

**Primary files.** `bulk_downloader/db.py`, `bulk_downloader/runner_transport.py`, `bulk_downloader/library.py`, `bulk_downloader/migrations.py`, `bulk_downloader/runner.py`, `bulk_downloader/runner_telemetry.py`, `tests/test_a_skip_must_prove_it_is_the_same_work.py`

**Why these are one piece of work.** The four rows that edit tests/test_a_skip_must_prove_it_is_the_same_work.py, all confirmed-on-main, all on the db_skip_identity seam: 479 accepts a no-transfer done row as proof of ownership, 503 never checks the size it recorded and then overwrites it, 519 leaves a file that vanishes after the skip proof as an unclassified worker error, and 485 lets a missing attribution link turn a skip into a silent full-size re-download. The shared gate file makes them one serial group regardless, and the seam is identical, so a single RED harness -- an upgraded host's pre-existing rows rather than the clean DB every existing test builds, which 479 requires -- serves all four. 485 is the widest (batch/history routes and batch_ops as readers of the attribution link) and is the detachable row if the RED block overruns. Two declared dependencies must be handled as findings rather than blockers: 479's DEPENDENCY 430 is satisfied by cut 5 landing first, and 503's DEPENDENCY 426 is stale on its face (db_skip_identity now gates that branch with a three-state answer as of v3.66.1368), so it must be re-derived and recorded, not treated as blocking.

### 7. `ffmpeg-pin-and-transfer-gate` -- rows 439, 440, 441, 442

**Contract.** Every media or transfer subprocess is spawned through the pinned binary and the fail-closed network gate, proven by instrumenting the real spawn boundary rather than source text.

**Primary files.** `bulk_downloader/ffmpeg_bin.py`, `bulk_downloader/thumbnail_sheets.py`, `bulk_downloader/thumbnail_gen.py`, `bulk_downloader/dedup.py`, `bulk_downloader/hls_downloader.py`, `bulk_downloader/runner_extractors.py`, `bulk_downloader/runner_transport.py`

**Why these are one piece of work.** Four instances of one shape -- a mandatory gate consulted and then bypassed at exec time: 440 and 441 gate on the pin then exec from PATH (identical fix pattern, and 440's own DEPENDENCY says they share the stub-pin fixture and should be cut together), 442 is the third instance in dedup's ffprobe metadata path, and 439 is the same bypass against the VPN fail-closed gate across six segmented-transfer arms. The coherence is the harness: all four are proven the same way, by counting spawns and reading the env at the subprocess boundary, with a stub pin directory and a PATH stripped of both binaries. Placed in the transport serial chain rather than beside it, because 439 edits runner_transport.py and 442 edits dedup.py, which cut 1 also touches through 506 -- running it concurrently with any transport cut would collide. 442 differs from its siblings in needing a real ffmpeg and ffprobe under the pin plus a real fixture media file to assert nonzero duration and codec, and 439 requires either one shared seam or an exact per-arm count of 6; both are per-row assertions under the one contract. All four are likely rather than confirmed, so the cut opens with a re-derivation of the four cited call sites against the candidate base.

### 8. `register-truth` -- rows 496, 511, 473, 463

**Contract.** The register mechanically reflects the tree: every deferral produces a machine-visible row, every landed fix closes one, and the guards over that file can actually fire.

**Primary files.** `toolchain/bin/bd-register-append`, `project-knowledge/IMPROVEMENT_BACKLOG.md`, `toolchain/bin/bd-precut`, `toolchain/bin/bd-decision-memory`, `tests/test_register_append.py`, `project-knowledge/REACHABILITY_DEFERRALS.json`

**Why these are one piece of work.** The four rows that write the register or the tools that write it, which is why they are one cut and why they are strictly last. 496 makes a decision to defer produce a tracked intake record a gate can see, 511 fixes a suffix guard in bd-register-append that cannot currently fire (blob 4acb197f at HEAD, RED reachable only through a bd-mutate splice into the tool itself), 473 reconciles the both-directions disagreement between register and tree, and 463 is pure closure bookkeeping -- its fix is already on HEAD, so the outstanding work is closing the row at the version that shipped it, which is exactly the shape 473 describes. Ordering is load-bearing rather than incidental: 473's reconciliation is stale on arrival unless cuts 1 through 7 have already closed their rows, and every row here regenerates the register header and digest, so they collide with each other and with any concurrent register writer. 473 is needs-rederivation and costs a measurement pass -- all 24 recover/* candidate tags present locally plus a blob-equality containment audit, which per the contract is the only containment test that can answer the shipped question. 511's test must publish into an isolated register fixture, never project-knowledge/IMPROVEMENT_BACKLOG.md itself.

## Excluded, with the reason

This half is the more valuable one: it names what CANNOT be batched and why,
so a future session does not rediscover it. Several rows are not certifiable by
pytest at all.

- **285** -- Cannot be certified by pytest at all: its unwaivable acceptance demands a real deploy on test4 plus an induced failing-deploy arm, and it may be mostly landed with only evidence outstanding.

- **424** -- Self-contained but its session-keeper heartbeat contract is shared by no other capture-vpn row once the ffmpeg/VPN spawn cohort is formed.

- **425** -- Pytest-only and clean, but after the four live-fleet rows are excluded it and 521 are the only batchable rows left in fleet-deploy-health -- two, below the four-row floor.

- **426** -- Substantially superseded on HEAD -- db_skip_identity now gates that branch with a three-state answer -- so the residual must be re-derived before it can be scheduled at all.

- **427** -- Needs-rederivation and likely half-closed already, since the atomic reservation its acceptance demands shipped at v3.66.1370, and its remaining half needs a two-worker concurrency harness.

- **438** -- Vault leftover: its single-writer idempotence contract is not the unreadable-is-not-absent contract of cut 2, and no other vault row shares it, leaving it below the four-row floor.

- **447** -- Needs-rederivation and overlaps 427 heavily; its runner_transport caller already routes through staging_claim.reserve, so it must be re-scoped to the residual runner_extractors/runner_browser callers first.

- **449** -- Its own note says it does not batch with single-file rows -- one lock decision at eight iteration sites across seven files with a deterministic thread-interleaving fixture -- and its subsystem holds only one other row.

- **452** -- Evidence work rather than code: it requires five specific real guided captures by file and sha256 plus a re-derivation of row 124's acceptance against them.

- **453** -- Blocked on obtaining a capture in an intersection measured empty, and its cited corpus is untracked operator material outside the repository.

- **454** -- No code change expected; closure needs a live held-open capture against a challenge-fronted authenticated site with a human completing a CAPTCHA.

- **455** -- Requires the operator harness against a live DOM, which A6 forbids a repository test from doing, before any offline test can be pinned.

- **467** -- Its three siblings were fixed on a tagged-but-unmerged v3.66.1374 absent from main, so any cut risks colliding with that stranded branch; its subsystem holds only one other row.

- **469** -- Its denominator change would invalidate every other tool verdict taken in the same batch, and its only near-sibling 470 is explicitly forbidden from sharing a candidate with it.

- **470** -- Edits roughly 43 separate toolchain tools, a blast radius too wide to pair with anything, and it cannot share a candidate with 469.

- **471** -- Needs the live fleet and the bare mirrors on five hosts, all outside the repository, with no roles-file path named in the prose.

- **474** -- Names no repo-relative source path at all; the work is a new fleet-wide probe requiring live hosts and a declared integrator exemption.

- **475** -- Vault leftover whose deliverable is a new first-class toolchain tool rather than a defect fix, so it shares no contract with any cohort of three other rows.

- **478** -- Its primary edit target bd-fleet-deploy is an out-of-repo operator harness script, and acceptance requires timing a real degraded window across a fleet deploy.

- **494** -- Confirmed and staging-cluster, but blocked on node/vitest provisioning that rows 415 and 509 record as unprovisioned; it is cut 1's natural sixth row once that lands, and an order-1 cut must not inherit an unprovisioned precondition.

- **515** -- Vault leftover: frontend-only render branches needing a vitest harness that row 509 records as unprovisioned, with no three contract-siblings left after cut 2.

- **521** -- Same as 425: the only remaining pytest-only partner in its subsystem, and a two-row cut is below the floor while crossing subsystems to reach four is forbidden.

- **522** -- Verification runs through the sandboxed shell probe against capture.sh's fifteen phase banners, which its own note says batches poorly with pure-Python cuts.

- **527** -- Borderline subsystem ownership -- its subject is the fleet sweep runner rather than candidate tooling -- and it has no contract-coherent cohort on either side.
