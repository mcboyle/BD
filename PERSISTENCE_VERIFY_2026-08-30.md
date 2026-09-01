# PERSISTENCE VERIFICATION REPORT — 2026-08-30 (~01:25-01:35 UTC)

Auditor: persistence-hardening pass (additive-only; no existing file edited).
Identity: host test5; repo /home/mboyle/BulkDownloader; origin
https://github.com/mcboyle/BD; main HEAD 5fe963d4e6bacdc7085dbe65d1a93b5d1f057e65;
__version__ 3.66.1355.

## VERDICT: ALL CHECKS PASS. No data loss found. Four discrepancies of record
## (not losses) are flagged inline: D1 truncated regression test name,
## D2 grep marker strings live in different files than expected, D3 row399
## file-list drift between original and rebuilt candidates, D4 the two ORIGINAL
## commits were un-anchored (a8603bb6 by NO ref, 526fb018 stash-only) until this
## audit tagged them.

## 1. Commit reachability — PASS
`git -C /home/mboyle/BulkDownloader cat-file -t <sha>` returned `commit` for ALL of:
- 72805b22 -> 72805b22921ca4a2b295bf450b40196ce04487f9
- 064700c3 -> 064700c3acf1ab66950f02d4d0dd4201ae684790
- a8603bb6 -> a8603bb60bd5cad8d09b9fef2244804113289342
- 526fb018 -> 526fb0184e56950f3e86be28011ed0ce7df8a2b5
- 9392a4cb -> 9392a4cb36a0ccc33a73e3c62b62225a619e0fbd (added mid-audit by coordinator)
- d45c9205 -> d45c9205301f185d5bcef48bfa74b7cac1fc3222 (added mid-audit by coordinator)

D4: ref-anchoring survey (`git rev-list --all` + per-ref ancestor checks) found
a8603bb6 reachable from NO ref (raw object only, gc-prunable) and 526fb018 only
via refs/stash. FIXED ADDITIVELY: new local tags recover/row399-orig ->
a8603bb6..., recover/row402-orig -> 526fb018... (names verified unused first;
`grep -rn 'recover/' /home/mboyle/*.sh /home/mboyle/bd-persist/harness/` found
no consumer of the tag namespace). Tags are local, NOT pushed.

## 2. Tag resolution — PASS
`git rev-parse` (live, full output):
- recover/row399        -> 72805b22921ca4a2b295bf450b40196ce04487f9  (expected 72805b22: MATCH)
- recover/row402        -> 064700c3acf1ab66950f02d4d0dd4201ae684790  (expected 064700c3: MATCH)
- recover/row399-onmain -> 9392a4cb36a0ccc33a73e3c62b62225a619e0fbd  (parent = 5fe963d4 main: MATCH)
- recover/row402-onmain -> d45c9205301f185d5bcef48bfa74b7cac1fc3222  (parent = 5fe963d4 main: MATCH)
- recover/row399-orig   -> a8603bb60bd5cad8d09b9fef2244804113289342  (created by this audit)
- recover/row402-orig   -> 526fb0184e56950f3e86be28011ed0ce7df8a2b5  (created by this audit)

## 3. Worktrees — PASS
- /home/mboyle/bd-codex-wt/row399 exists. First observed with STAGED changes on
  HEAD 5fe963d4; `diff --name-only origin/main` = candidate_filter.py,
  detect.py, runner.py, IMPROVEMENT_BACKLOG.md, test_row399_a_photo_gallery_...
  Mid-audit the coordinator committed it: HEAD now 9392a4cb, status clean.
  `git diff recover/row399-onmain -- bulk_downloader/ tests/` = 0 bytes.
- /home/mboyle/bd-codex-wt/row402 exists. Same pattern: app_health.py,
  app_secrets.py, secrets_store.py, IMPROVEMENT_BACKLOG.md, test_row402_...;
  HEAD now d45c9205, clean; diff vs recover/row402-onmain = 0 bytes.
  0-byte diffs prove tags+bundle fully capture the worktree content.

D3: CHECKPOINT lists row399 source as detect.py, runner.py, runner_transport.py
— that is the ORIGINAL a8603bb6 shape. Both rebuilt candidates carry
candidate_filter.py INSTEAD of runner_transport.py. Not a loss; recorded so a
checkpoint-vs-tag comparison does not misread the tag as wrong.

## 4. Harness neuters — PASS
- `grep -c 'sleep 86400' /home/mboyle/bd-autorebase.sh` -> 1
  (file is 390 bytes: comment header + `exec sleep 86400`)
- `grep -c bd-unstale-generated /home/mboyle/bd-night.sh` -> 1
  (line 266: `python3 /home/mboyle/bd-unstale-generated.py "$r" >> ... || true`)
- /home/mboyle/bd-persist/harness/bd-autorebase.sh.ORIGINAL-prewent-neuter
  exists, 1894 bytes; head inspected — real pre-neuter rebase loop, not a sleep.
- batch-cap: /home/mboyle/fleet-run-artifacts/2026-08-25/night/batch-cap
  contains `1` (2 bytes). PASS.

## 5. bd-persist/verify.sh — PASS
`bash /home/mboyle/bd-persist/verify.sh` printed `VERIFY: PASS -- nothing
missing`, exit 0 (run before creation work; re-run after — see section 10).

## 6. CHECKPOINT tail and RESUME_PROMPT session-end — PASS with D2
- /home/mboyle/fleet-run-artifacts/2026-08-25/CHECKPOINT.md exists (217161
  bytes / 3899 lines after the coordinator's mid-audit append; 214413 bytes at
  first read). Tail carries "PARKED ROWS — worktrees intact, recovery refs
  below" (399/402/120/122), "THE MAINLINE REGRESSION -- FIX THIS FIRST",
  "-onmain" tags at lines 3870-3871, the a8603bb6/526fb018 SHAs at line 3873,
  and an "AFTER THE REBOOT" sequence.
- /home/mboyle/bd-persist/RESUME_PROMPT.md (11834 bytes) and
  /home/mboyle/RESUME_PROMPT.md are byte-identical (diff = empty). Line 155:
  "SESSION-END STATE 2026-08-30 01:20 (supersedes older status above)"; lines
  170-171 name commits 72805b22 / 064700c3.
- /home/mboyle/bd-codex-briefs/row120.txt exists, 2747 bytes.

D2: the suggested grep targets missed as literally specified —
`grep -c 'SESSION-END STATE 2026-08-30' CHECKPOINT.md` -> 0 (that string lives
in RESUME_PROMPT.md, not CHECKPOINT.md) and `grep -c 'recover/row399'
RESUME_PROMPT.md` -> 0 (the prompt cites the SHAs, not tag names). The
SUBSTANCE exists in both files as quoted above. Note: before this audit the
literal tag names appeared in no durable document; CHECKPOINT line 3873 and the
new manifest now carry them.

## 7. Mainline regression — MEASURED RED (as claimed) with D1
D1: the recorded node id `...::test_reviewed_template_feeds_modal_flow` DOES
NOT COLLECT (pytest exit 4, "no match"). The real name (file line 147) is:
  tests/test_v3_66_144_template_subsystem.py::test_reviewed_template_feeds_modal_flow_into_learned_dl
Run on clean main 5fe963d4 (env -u BD_INSTALL_DIR, BD_DISABLE_KEEPALIVE=1,
PYTHONDONTWRITEBYTECODE=1, -p no:cacheprovider): 1 failed in 0.82s —
  tests/test_v3_66_144_template_subsystem.py:156
  assert any("download-resolution" in s for s in rows)  ->  assert False
The regression claim is CONFIRMED; only the recorded test name was truncated.

## 8. Bundle — PASS (created and restore-tested)
/home/mboyle/bd-persist/RECOVERY/parked-work.bundle — 34309485 bytes, sha256
88e336172ad2a23d2d41b4275351ffe49d32fa6222a2ec611fec258ec9529fd6.
`git bundle verify` -> "is okay ... records a complete history"; list-heads
shows exactly the 6 recover/* tags with the SHAs in section 2. Restore test:
fresh `git init` + `git fetch <bundle> 'refs/tags/*:refs/tags/*'` in scratch —
all 6 SHAs cat-file as `commit`, all 6 tags resolve to the exact SHAs above.
(An earlier scratch test proved bare SHAs contribute NO refs to a bundle,
which is why the -orig tags were required for a8603bb6/526fb018 to be
restorable — D4.)

## 9. /tmp sweep (reboot-safety) — PASS
find over /tmp (mboyle-owned, bd-related): only disposable test residue
(bd-restore-test-*, bd-testrun-*, bd-row*-mutate scratch, bd-runctx, claude
scratchpads). No recovery-critical file exists ONLY in /tmp. The scratchpad
bd-autorebase.sh.bak mentioned in the checkpoint is redundant with
bd-persist/harness/bd-autorebase.sh.ORIGINAL-prewent-neuter.

## 10. Files created by this audit (ALL NEW paths; nothing overwritten)
- /home/mboyle/bd-persist/RECOVERY/                       (new dir)
- /home/mboyle/bd-persist/RECOVERY/parked-work.bundle     34309485 B
- /home/mboyle/bd-persist/RECOVERY/bd-autorebase.sh.SESSION-END-COPY  390 B (cmp-identical to live)
- /home/mboyle/bd-persist/RECOVERY/bd-night.sh.SESSION-END-COPY     21793 B (cmp-identical to live)
- /home/mboyle/bd-persist/RECOVERY_MANIFEST_2026-08-30.md  9313 B
- /home/mboyle/bd-persist/PERSISTENCE_VERIFY_2026-08-30.md (this file)
- git tags (local refs, additive): recover/row399-orig, recover/row402-orig
Final verify.sh re-run result is appended below.

## FINAL verify.sh RE-RUN (2026-08-30, after all audit writes)
```
  OK   fetch_one.py

VERIFY: PASS -- nothing missing
exit=0
```
