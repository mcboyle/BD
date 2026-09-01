# RECOVERY MANIFEST — 2026-08-30 (independent redundant copy)

Written 2026-08-30 ~01:30 UTC by the persistence-hardening audit pass.
Every fact below is labeled MEASURED (read live from the named source at write
time) or RESTATED (copied from checkpoint/operator prose, not independently
verifiable from this host). This file deliberately duplicates the CHECKPOINT
tail and RESUME_PROMPT session-end state so the loss of any single file is
survivable. It is additive; it supersedes nothing.

## Identity (MEASURED)

- host: test5
- repo: /home/mboyle/BulkDownloader, origin https://github.com/mcboyle/BD
- branch main, HEAD 5fe963d4e6bacdc7085dbe65d1a93b5d1f057e65
  (Merge PR #649, cut/1355-reptyle-selectors-resolve-on-recorded-dom)
- __version__ = "3.66.1355" (bulk_downloader/__init__.py)
- integrator tree clean at audit time
- Fleet deployed at 1353 (RESTATED from CHECKPOINT; deploy of 1355 is owed)

## The six recovery commits and six tags (MEASURED, read live)

All tags are LOCAL to /home/mboyle/BulkDownloader (not pushed).

| tag | full SHA | meaning |
| --- | --- | --- |
| recover/row399-onmain | 9392a4cb36a0ccc33a73e3c62b62225a619e0fbd | row399 clean-on-main candidate, parent 5fe963d4 (main/1355). USE THIS to resume 399. |
| recover/row402-onmain | d45c9205301f185d5bcef48bfa74b7cac1fc3222 | row402 clean-on-main candidate, parent 5fe963d4 (main/1355). USE THIS to resume 402. |
| recover/row399 | 72805b22921ca4a2b295bf450b40196ce04487f9 | row399 rebuilt candidate on 1354 base (stale base, superseded by -onmain) |
| recover/row402 | 064700c3acf1ab66950f02d4d0dd4201ae684790 | row402 rebuilt candidate on 1354 base (stale base, superseded by -onmain) |
| recover/row399-orig | a8603bb60bd5cad8d09b9fef2244804113289342 | row399 ORIGINAL pre-incident commit (2026-08-29 21:41Z) |
| recover/row402-orig | 526fb0184e56950f3e86be28011ed0ce7df8a2b5 | row402 ORIGINAL pre-incident commit (2026-08-29 21:26Z) |

The two -orig tags were CREATED BY THIS AUDIT (2026-08-30): before them,
a8603bb6 was reachable from NO ref at all (gc-prunable raw object) and
526fb018 only via refs/stash (one stash drop from gone). Both are now
ref-anchored locally and in the bundle below.

## Commit file lists (MEASURED via git show --stat)

- 9392a4cb (399-onmain, 5 files): bulk_downloader/candidate_filter.py,
  bulk_downloader/detect.py, bulk_downloader/runner.py,
  project-knowledge/IMPROVEMENT_BACKLOG.md,
  tests/test_row399_a_photo_gallery_is_not_a_failed_video_page.py
- d45c9205 (402-onmain, 5 files): bulk_downloader/app_health.py,
  bulk_downloader/app_secrets.py, bulk_downloader/secrets_store.py,
  project-knowledge/IMPROVEMENT_BACKLOG.md,
  tests/test_row402_unlock_over_uninitialized_vault.py
- 72805b22 (399 on 1354 base): candidate_filter.py, detect.py, runner.py,
  test_row399*, plus INV_TAGS.md, PIN_INDEX.json, import_graph_baseline.json
- 064700c3 (402 on 1354 base): app_health.py, app_secrets.py, secrets_store.py,
  test_row402*, plus PIN_INDEX.json, ROUTE_INDEX.json, import_graph_baseline.json
- a8603bb6 (399 ORIGINAL): detect.py, runner.py, runner_transport.py,
  test_row399*, INV_TAGS.md, PIN_INDEX.json, import_graph_baseline.json
- 526fb018 (402 ORIGINAL): app_health.py, app_secrets.py, secrets_store.py,
  tests/test_row402_vault_unlock_state_coherence.py, PIN_INDEX.json,
  ROUTE_INDEX.json, import_graph_baseline.json

DRIFT NOTE (so a future reader does not conclude the tags are wrong): the
CHECKPOINT "Source files" line for 399 (detect.py, runner.py,
runner_transport.py) describes the ORIGINAL a8603bb6. The rebuilt candidates
(72805b22 and 9392a4cb) carry candidate_filter.py INSTEAD of
runner_transport.py. The 402 original's test file is named
test_row402_vault_unlock_state_coherence.py; the rebuilds renamed it
test_row402_unlock_over_uninitialized_vault.py.

## Worktrees (MEASURED)

- /home/mboyle/bd-codex-wt/row399: HEAD = 9392a4cb (== recover/row399-onmain),
  status clean (untracked venv/ and frontend/node_modules only).
  git diff recover/row399-onmain -- bulk_downloader/ tests/  ->  0 bytes.
- /home/mboyle/bd-codex-wt/row402: HEAD = d45c9205 (== recover/row402-onmain),
  status clean (same untracked residue).
  git diff recover/row402-onmain -- bulk_downloader/ tests/  ->  0 bytes.

So the worktree content is EXACTLY the -onmain tag content: tags + bundle fully
protect the parked work even if both worktrees are destroyed.

## Portable bundle (MEASURED — strongest protection, survives a repo reset)

- /home/mboyle/bd-persist/RECOVERY/parked-work.bundle
- 34309485 bytes, sha256
  88e336172ad2a23d2d41b4275351ffe49d32fa6222a2ec611fec258ec9529fd6
- carries all SIX tags above; `git bundle verify` = okay, complete history.
- restore-tested into a fresh `git init` repo: all six commits cat-file as
  commits and all six tags resolve to the SHAs above.
- To restore after a repo loss:
    git init fresh && cd fresh
    git fetch /home/mboyle/bd-persist/RECOVERY/parked-work.bundle 'refs/tags/*:refs/tags/*'

## The mainline regression — FIX FIRST (MEASURED RED at HEAD 5fe963d4)

CORRECT node id (the CHECKPOINT/RESUME_PROMPT truncate the name; the short form
does NOT collect — pytest exit 4 "no match"):

  tests/test_v3_66_144_template_subsystem.py::test_reviewed_template_feeds_modal_flow_into_learned_dl

Measured 2026-08-30 ~01:29Z on clean main 5fe963d4: FAILED in 0.82s at
tests/test_v3_66_144_template_subsystem.py:156:
  assert any("download-resolution" in s for s in rows)  ->  assert False
(trigger and modal-scope assertions above it pass; the row selectors no longer
contain a "download-resolution" entry). Attribution per CHECKPOINT: row 126
(reptyle template, merged 1355) — its band did not include this test, so it
merged RED.

## Rows 120 / 122 (RESTATED except file checks)

- 120 jwplayer-cdn-topology-auto-template: NEEDS RE-DISPATCH fresh on current
  main. Brief EXISTS (MEASURED): /home/mboyle/bd-codex-briefs/row120.txt,
  2747 bytes. Corpus work done; the recovered capture_model golden could not
  reconcile with row 124's readers (RESTATED).
- 122 challenge-resume-event: REFUTED — already implemented+tested on main
  (landed v3.66.1354). No code needed; register close folds into row 406.

## Harness neuters — deliberate, do NOT undo (MEASURED)

- /home/mboyle/bd-autorebase.sh (390 bytes) = comment header + `exec sleep 86400`.
  grep -c 'sleep 86400' -> 1. It hard-reset queued worktrees on 2026-08-29;
  re-enable only after bd-rebase provably REPLAYS row commits.
- /home/mboyle/bd-night.sh (21793 bytes): destructive rebase step replaced at
  line 266 with non-destructive `python3 /home/mboyle/bd-unstale-generated.py`.
  grep -c bd-unstale-generated -> 1.
- Original autorebase preserved at
  /home/mboyle/bd-persist/harness/bd-autorebase.sh.ORIGINAL-prewent-neuter
  (1894 bytes; content verified to be the real pre-neuter loop, not a sleep).
- Session-end copies of BOTH neutered files (byte-identical, cmp-verified):
  /home/mboyle/bd-persist/RECOVERY/bd-autorebase.sh.SESSION-END-COPY
  /home/mboyle/bd-persist/RECOVERY/bd-night.sh.SESSION-END-COPY
- batch-cap = 1 (MEASURED:
  /home/mboyle/fleet-run-artifacts/2026-08-25/night/batch-cap contains "1").
  Keep 1 until bd-drain audits per-row.

## Reboot notes (a reboot is planned before usage reset)

- /tmp IS WIPED BY REBOOT. Audit swept /tmp: contents are test residue
  (bd-restore-test-*, bd-testrun-*, mutate scratch, claude scratchpads) — no
  recovery-critical file lives ONLY in /tmp. The one checkpoint-mentioned
  scratchpad backup (bd-autorebase.sh.bak) is redundant with the persistent
  bd-persist/harness ORIGINAL copy named above.
- Everything recovery-critical is on persistent disk under /home/mboyle:
  the repo + its tags, both worktrees, bd-persist (this file, the bundle,
  harness copies, RESUME_PROMPT), fleet-run-artifacts (CHECKPOINT, night/).
- Git tags and the bundle survive reboot; anything running does not. Follow
  the AFTER THE REBOOT sequence in the CHECKPOINT tail (watchdog revives the
  loops; confirm neuters BEFORE starting anything).

## Ordered resume steps

1. Fix the mainline regression (correct node id above) — it blocks every band
   that includes it.
2. Recover rows 399/402 from recover/row399-onmain / recover/row402-onmain
   (worktrees already sit at those commits if they survived; otherwise
   `git worktree add` / cherry-pick from the tags, or restore from the bundle).
3. Re-dispatch row 120 fresh on current main from
   /home/mboyle/bd-codex-briefs/row120.txt.
4. Row 406 register reconciliation (folds in the 122 REFUTED close, the
   124/126 PARKED->CLOSED flips, and the stale-closed sweep).
5. Deploy 1355 (or later) with scripts/deploy.sh --expect-commit for bd/bd1,
   bd-fleet-deploy.sh for GitHub hosts; bd-vault-unlock.sh after (vaults
   re-lock on restart). Verify /api/health version and GET / = 200.

## Companion records

- /home/mboyle/fleet-run-artifacts/2026-08-25/CHECKPOINT.md (tail: PARKED ROWS,
  SESSION-END additions, AFTER THE REBOOT sequence)
- /home/mboyle/bd-persist/RESUME_PROMPT.md and /home/mboyle/RESUME_PROMPT.md
  (identical; "SESSION-END STATE 2026-08-30 01:20" at line ~155)
- /home/mboyle/bd-persist/PERSISTENCE_VERIFY_2026-08-30.md (this audit's
  check-by-check evidence)
- bash /home/mboyle/bd-persist/verify.sh must print "VERIFY: PASS"
