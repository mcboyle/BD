# Batch plan for the 74 audit rows (533-606) -- 2026-09-01

Produced by the `batch-the-audit-rows` workflow against the register at
v3.66.1389. All 74 rows classified, 7 marked ACT NOW.

Waves are file-disjoint: the cuts inside one wave can verify CONCURRENTLY (at
most 3). A later wave touches a file an earlier one owns, so it is sequenced,
not overlapped -- the staging cluster in particular is one serial lane.

HARNESS ROWS DO NOT SHARE A CUT WITH REPOSITORY ROWS. The bd-* scripts live
outside the repository: no release trio, no CI, no band. They are archived to
bd-persist/harness and proved by bd-persist/verify.sh instead.

NOTE: /home/mboyle/bd-cuts/cut/1389-staging-release-lifetime already contains
row 533's fix (committed, trio NOT set) together with older rows 492, 489 and
506. Reconcile it against wave 1's first cut before starting that cut fresh.

## Wave 1

### `claim-ownership-is-proven-bytes` -- rows 533, 534, 535, 541, 575

**Contract.** The staging claim publishes, mints, or reclaims an owner only after the bytes that owner names are proven present and identified.

**Files.** `bulk_downloader/staging_claim.py`, `bulk_downloader/runner_transport.py`

**Why one piece of work.** 533 is act_now and 534/535 are its declared duplicates, so they fold here by rule rather than getting cuts of their own: all three are the same publish-before-prove ordering seen from three call sites. 541 (act_now) is the transport half of the same defect -- a job's own partial bytes set aside because the claim did not identify them -- and its runner_transport.py edit is what pins that file to wave 1. 575 (reclaim measures nothing, identity is sha256) is the same contract on the reclaim branch and is the only other staging_claim.py row, so folding it here avoids a second serial pass over the file. This cut owns the whole staging cluster's serial lane.

### `vault-writes-target-the-vault-they-validated` -- rows 537, 538, 539, 540

**Contract.** Every secrets-store write path validates and mutates the same vault it resolved, and refuses rather than laundering a damaged one.

**Files.** `bulk_downloader/secrets_store.py`

**Why one piece of work.** The three act_now secrets rows (537 save overwrites the path from the construction-time vault, 538 the row-482 re-probe is advisory only, 539 delete never validates the vault it mutates) are one sentence: the write acts on a vault it never proved. 540 is the same delete path turning a damaged ciphertexts container into not-present, so it must be fixed in the same RED block or the 539 fix will report clean over the damaged case. Deliberately excludes the read/probe-reporting rows so this stays one contract; secrets_store.py is then serialized across waves 1/2/3.

### `replay-adopts-only-proven-output` -- rows 542, 543, 557, 558

**Contract.** bd-candidate-replay adopts or refuses an output path only on measured ownership evidence, and the refusal names that evidence without leaking the claim.

**Files.** `scripts/bd_candidate_replay.py`

**Why one piece of work.** 542 is act_now (adopts a concurrently created empty output) and 543 is its duplicate (the window measurement consulted only on the add path) -- the duplicate folds in here, into the cut owning its original. 557 and its duplicate 558 are the refusal side of the same decision: the foreign-at-path branch leaks the claim it just declined. Fixing adoption without the refusal message would leave the tool unable to say why it refused. The remaining replay rows are diagnostics about git-side failures and are a separate contract (wave 3).

## Wave 2

### `runner-decisions-carry-their-whole-result` -- rows 536, 544, 545, 559, 562

**Contract.** A runner decision returns and propagates its complete result -- full tuple, force flag, ownership answer, and diagnostic -- instead of a truncated or defaulted one.

**Files.** `bulk_downloader/runner_extractors.py`, `bulk_downloader/runner_transport.py`, `bulk_downloader/runner_integrity.py`

**Why one piece of work.** 536 is act_now: try_ytdlp_fallback returns a 4-tuple where 8 are expected. It cannot be a cut on its own (one row, below the 4-row floor) and runner_extractors.py has no other row, so it leads the runner-subsystem cut instead. 545 (the skip arm eats force-download), 544 (default-on dedup preflight answers the ownership question), 559 (unproven diagnostic gated behind final-path-exists) and 562 (the needs-review db log can raise) are the same shape on the adjacent transport/integrity paths of the one download decision. This cut must follow wave 1 because 541 already edits runner_transport.py.

### `vault-probe-is-recorded-once` -- rows 548, 549, 550, 576, 577

**Contract.** The vault probe's result is recorded once and every reader, status endpoint, and existence test reports that same state.

**Files.** `bulk_downloader/secrets_store.py`

**Why one piece of work.** 548 owns the defect (the re-probe records nothing, so store state is not observable) and 549 and 550 are both filed as its duplicates -- 549 the write refusal that every read ignores, 550 /api/secrets/status and /api/secrets/unlock reporting opposite answers -- so both fold in here rather than being padded into the API cut. 576 (load_index probes secrets-meta existence outside the vault) and 577 (secrets_file_exists is a target-resolvability test, not a file test) are the same question asked of the wrong artifact. Same file as wave 1's cut, so it is sequenced after it.

### `history-rows-distinguish-done-from-laundered` -- rows 547, 560, 561, 563

**Contract.** A history row's identity and byte counters distinguish a real completed download from one that only looks complete.

**Files.** `bulk_downloader/db.py`

**Why one piece of work.** 547 (SAME returned over another URL's bytes) is the identity half and 560 with its duplicate 561 (legitimate bytes_fetched=0 done rows, and the 416 resume-complete arm that produces them) is the counter half of one question: can the history table tell a finished job from a laundered one. 563 (pruning turns a healthy repeated skip into a failure) is the same table's derived verdict. All four are db.py only, so this runs concurrently with the runner and secrets cuts. Note the semantic seam with 562, which changes the caller of this logging -- file-disjoint, but the two cuts should be reviewed together.

## Wave 3

### `secrets-api-reports-the-real-outcome` -- rows 551, 552, 554, 555

**Contract.** A secrets mutation or endpoint surfaces the store's actual outcome instead of laundering a substituted backend, a discarded result, or an error into success.

**Files.** `bulk_downloader/secrets_store.py`, `bulk_downloader/app_secrets.py`

**Why one piece of work.** 551 (a silently substituted PlaintextBackend makes delete a no-op), 552 (/api/secrets/delete discards save_sites_config's success), 554 (delete 500s on SecretsPersistError, the third exception) and 555 (the probe-failure refusal asserts the wrong precondition -- that the vault file exists) are one sentence: the caller is told something the store did not do. This is the third and last serial pass over secrets_store.py, and the only one that also touches app_secrets.py. Row 553 is NOT here: it is a duplicate of 488, which is still OPEN, so it rides with 488's cut.

### `replay-refusals-name-the-step-that-failed` -- rows 546, 556, 578, 579

**Contract.** A replay refusal names the step and cause that actually failed rather than collapsing distinct git failures into one verdict.

**Files.** `scripts/bd_candidate_replay.py`, `scripts/bd_integration_verdict.py`

**Why one piece of work.** 546 (fail drops every rollback note, collapsing source mutation and other causes), 556 (a git-side checkout failure misdiagnosed), 578 (the foreign-occupant discriminator matches English-only git stderr) and 579 (a valid uppercase 40-hex object name refused) are all the CLAUDE.md A7 diagnostic-collapse shape on one tool. 578 is the only row reaching bd_integration_verdict.py, which no other cut touches. Sequenced after wave 1 because that cut already edited bd_candidate_replay.py.

### `gate-floors-are-derived-not-pinned` -- rows 566, 569, 570, 571

**Contract.** A gate's floor and denominator are derived from the current tracked population rather than pinned to a pre-cut one.

**Files.** `tests/test_row531_denominators_are_derived_not_pinned.py`, `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`

**Why one piece of work.** 569 (the monotonic gate floor does not refuse a silent shrink) and its duplicate 570 (the declared-gate floor carries the pre-cut population) fold together; 566 (the anti-re-pin guard is blind to its own shape) and 571 (the exact tracked-markdown denominator left the CI bidirectional check behind) are the same defect in the gate that was written to prevent exactly it. Grouping the two test files here leaves test_row532 free for wave 4 and keeps this cut file-disjoint from both other wave-3 cuts.

## Wave 4

### `no-scanner-or-record-silently-drops-members` -- rows 565, 567, 568, 572

**Contract.** No repository scanner or record may silently drop members of the population it claims to cover.

**Files.** `tests/test_row532_a_mutant_anchor_must_resolve_into_code.py`, `project-knowledge/IMPROVEMENT_BACKLOG.md`

**Why one piece of work.** 567 (the comment-anchor gate skips every extensionless script) with its duplicate 568 (the ratchet skips 457 of 914 Python files) and 572 (comment_spans builds its offset table with str.splitlines) are one file and one contract. THIS FOLD BENDS THE SHARED-FILE RULE AND I AM SAYING SO: 565 (the cut deleted five of the eight open claims from the backlog) shares the CONTRACT -- silent loss of population members -- not the subsystem. It is forced: the seven test rows cannot split into two cuts that both clear the 4-row floor, and 565 is a lone doc row that can form no cut of its own. Placed in a wave where no other cut edits IMPROVEMENT_BACKLOG.md structurally, so concurrency still holds.

### `cleanup-deletes-only-what-it-archived` -- rows 580, 581, 583, 597   (HARNESS, outside the repo)

**Contract.** No cleanup or archival step deletes a target whose bytes it has not proven archived and whose identity it has not proven.

**Files.** `/home/mboyle/bd-endgame2.sh`, `/home/mboyle/bd-worktree-archive.sh`, `/home/mboyle/bd-clean-residue.sh`, `/home/mboyle/bd-clean-vms.sh`

**Why one piece of work.** Read on disk: bd-endgame2.sh archives a diff plus a status file and then rm -rf's, and git diff cannot see an untracked file at all, so untracked bytes were destroyed permanently. 581 and 583 are the same archive-then-delete path in bd-worktree-archive.sh and bd-clean-residue.sh; 597 is the VM-side twin. Harness only -- no release trio, no CI, no band -- so this is verified by bd-persist/verify.sh and archived to bd-persist/harness, never mixed with a repository cut.

### `a-supervision-probe-must-see-live-state` -- rows 582, 585, 586, 598, 599, 602   (HARNESS, outside the repo)

**Contract.** The unattended supervision loop anchors its liveness probes on the invocation they mean and records the state it claims to have observed.

**Files.** `/home/mboyle/bd-watchdog.sh`, `/home/mboyle/bd-night.sh`, `/home/mboyle/bd-codex-cut.sh`, `/home/mboyle/bd-autorebase.sh`, `/home/mboyle/bd-att-guard.sh`, `/home/mboyle/bd-checkpoint-write`, `/home/mboyle/bd-ps.sh`, `/home/mboyle/bd-supervise.sh`, `/home/mboyle/NIGHT_POLICY.md`

**Why one piece of work.** Confirmed on disk that both 582 (bd-watchdog.sh ps -eo args regex over the guarded script list) and 585 (bd-checkpoint-write's _HRE, plus bd-ps.sh) are the ps/grep self-match and anchoring family, and 598/599 are the same probe in bd-att-guard.sh. 586 and 602 are the write side: a checkpoint that is not written and a NIGHT_POLICY.md rule the supervisor does not enforce are both silent no-ops, which is what makes a dead writer look like a live monitor. Six rows is the cap and justified -- these are one loop. Must precede the wave-5 landing cut because both touch bd-night.sh.

## Wave 5

### `landing-measures-this-attempt-completely` -- rows 584, 587, 588, 589, 591, 601   (HARNESS, outside the repo)

**Contract.** Every rebase and landing step measures the current attempt over its complete changed-path denominator and refuses rather than inheriting a prior attempt's or a failed measurement's answer.

**Files.** `/home/mboyle/bd-rebase`, `/home/mboyle/bd-rebase-all.sh`, `/home/mboyle/bd-night.sh`, `/home/mboyle/bd-land`, `/home/mboyle/bd-running`, `/home/mboyle/bd-verify-cut.sh`

**Why one piece of work.** Verified on disk: 591 is bd-land's containment loop filtering --diff-filter=AM so a deletion-only cut checked zero files and still printed LANDED; 589 is bd-verify-cut.sh reading a reused tag's appended attribute.log as this attempt's evidence; 584 and 587 are the empty-stash and dropped-row tells in the rebase path; 588 is grep -c ... || echo 0, which makes a failed measurement indistinguishable from a real zero; 601 is bd-land's fast-forward and sibling reconcile. One pipeline, one sentence. Sequenced after wave 4 because 588 edits bd-night.sh, which the supervision cut also touches.

### `register-tools-resolve-against-the-current-population` -- rows 564, 592, 593, 594, 605, 606   (HARNESS, outside the repo)

**Contract.** Every register and row tool resolves rows against the current backlog population and its current constant names rather than a stale, renamed, or assumed one.

**Files.** `/home/mboyle/bd-row-audit.py`, `/home/mboyle/bd-union-resolve.py`, `/home/mboyle/bd-register-merge.py`, `/home/mboyle/bd-integrate-row.sh`, `/home/mboyle/bd-register-open-row.py`, `/home/mboyle/bd-deref-register.py`, `/home/mboyle/bd-depipe-register.py`, `/home/mboyle/bd-batch-rows.py`

**Why one piece of work.** 564 states the contract outright: renaming the pin constants to FLOOR silently blinded two tools that still looked for the old names. 592 and 593 are the same staleness inside bd-row-audit.py, 594 pairs bd-register-merge.py with its bd-integrate-row.sh caller, 605 is the three register-rewriting tools that must agree on one row population, and 606 is bd-batch-rows.py selecting from it. Deliberately placed in a wave where no cut edits IMPROVEMENT_BACKLOG.md, since these tools all read it.

### `a-preflight-reports-unknown-not-pass` -- rows 590, 595, 596, 600, 603, 604   (HARNESS, outside the repo)

**Contract.** Each preflight and fleet probe gates on the current run's own verdict and reports an unmeasurable result as UNKNOWN rather than as a pass.

**Files.** `/home/mboyle/bd-preflight.sh`, `/home/mboyle/bd-denom-preflight`, `/home/mboyle/bd-fleet-deploy.sh`, `/home/mboyle/bd-fleet-audit-cmd.sh`, `/home/mboyle/bd-vm-bringup.sh`

**Why one piece of work.** Read on disk: 604 is bd-vm-bringup.sh waiting on grep -qE 'VERDICT: READY' over an appended provision log -- the exact stale-marker shape CLAUDE.md A5 names; 595 captures PREFLIGHT_RC from the wrong command inside a redirected brace group and 596 conflates an empty register-only patch with an error; 600 scrapes pass counts out of pytest text; 590 and 603 are the fleet-side version and health probes that decide a host is fine from a grep that can legitimately return nothing. All five files are disjoint from the other two wave-5 cuts.

## Notes

FLEET DEPLOY: unblocked at the end of WAVE 2, not wave 1. Six of the seven act_now rows (533, 537, 538, 539, 541, 542) clear in wave 1. The seventh, 536 (try_ytdlp_fallback returns a 4-tuple where 8 are expected), cannot: runner_extractors.py has exactly one row in this audit, a 1-row cut violates the 4-row floor, and the wave is already at the 3-concurrent cap with three file-disjoint cuts. It also cannot be merged into wave 1's staging cut, because 541 is act_now and pins runner_transport.py to wave 1, so any runner cut that also carried 541 would collide with the staging_claim.py half of the same row. I looked for a lever and there is exactly one: accept a single-row cut for 536 in wave 1 and unblock the deploy a wave earlier at the cost of ~20 minutes of cut overhead for one row. That is the operator's call, not mine; the plan as written takes the floor seriously and deploys after wave 2.

WOULD NOT SCHEDULE (3 rows, all verified against project-knowledge/IMPROVEMENT_BACKLOG.md just now):
- 553 (api/secrets/usage launders SecretsIntegrityError into a confident empty) is duplicate_of 488. Row 488 reads OPEN at line 515 of the backlog. A duplicate folds into the cut that owns its original, and that original is not in this set of 74, so 553 ships with 488's cut. Putting it in wave 3's secrets-API cut would have been convenient padding and would have split one defect across two cuts.
- 573 and 574 (ramdisk-stage resume-offset and claim/set-aside interactions) chain through duplicate_of 573 to 501, which reads OPEN at line 528. Same rule. Worth flagging to whoever schedules 501: it names runner_transport.py, ramdisk_stage.py and staging_claim.py, so 501's cut collides with wave 1's staging cut and wave 2's runner cut and must be sequenced after both -- effectively wave 3 or later on the same serial lane.

TWO THINGS I BENT, STATED RATHER THAN HIDDEN:
- Wave 4's scanner cut folds 565 (the backlog lost five of eight open claims) in with three tests/test_row532 rows. They share the contract -- silent loss of population members -- but not the subsystem. It is forced: seven test-gate rows cannot split into two cuts that both clear the floor, and 565 is a lone doc row. It is placed in the one wave where no other cut edits IMPROVEMENT_BACKLOG.md structurally, so the disjointness that concurrency depends on still holds. If 565's missing claims are needed to plan anything, restore them as bookkeeping before wave 1 rather than waiting for wave 4.
- Wave 2's runner cut (562, the needs-review db log can raise) and its db.py cut (560/563) are file-disjoint and so may run concurrently, but 562 is the caller of the logging 560/563 change. Review them together or expect one to invalidate the other's evidence.

STRUCTURE: 15 cuts, 5 waves, at most 3 per wave, every cut 4-6 rows. I flattened the final row_ids plus the unscheduled trio and asserted each of 533-606 appears exactly once: 71 scheduled + 3 unscheduled = 74, zero duplicates, zero missing. Harness and repository rows never share a cut -- the ten in_repo cuts carry the release trio, CI and band; the five harness cuts (28 rows across ~20 bd-* scripts in /home/mboyle) are archived to bd-persist/harness and verified by bd-persist/verify.sh. Same-file serialization holds throughout: secrets_store.py runs in waves 1/2/3, bd_candidate_replay.py in 1/3, runner_transport.py in 1/2, bd-night.sh in 4/5. Every contract sentence above is true of every row in its cut, and every duplicate sits in the cut owning its original.
