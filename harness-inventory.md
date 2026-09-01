# BulkDownloader operator harness inventory

Host `test5` | snapshot `2026-08-31T15:28:11Z` | subject `/home/mboyle/bd-*.sh` + `/home/mboyle/bd-*.py` | population **149** (measured, `ls | wc -l`).

> **The population moved during this inventory.** It was 143 at 15:0xZ and 149 at 15:26Z. Six scripts -- `bd-codex-session-snapshot.py`, `bd-depipe-register.py`, `bd-resolve-stash-conflict.py`, `bd-worker-dashboard.sh`, `bd-worker-dashboard-v2.sh`, `bd-worker-probe.sh` -- appeared in `/home/mboyle` with **ctime 2026-08-31 15:25** and preserved mtimes of Aug 29-30, i.e. they were restored from a `bd-persist` copy by the concurrent `claude-integrator` session, not authored now. `bd-anchorcheck.py` also grew 6338 -> 6348 bytes mid-run. All 149 are tabulated; the six restored ones are the last six rows of the class counts. The commissioned figure of 143 refers to the pre-15:25 set.

## Method

- **Purpose** is the script's own leading comment block or module docstring; where there is none it is read from the first ~20 code lines.
- **Class**: GENERAL = takes its subject as an argument or from a re-read spec/queue file. ONE-SHOT = a version, row id, branch, host address, worktree path or log path is baked into the body. Classified from content, not from the filename.
- **Refs**: basename occurrences across `/home/mboyle/*.sh`, `/home/mboyle/*.py`, all of `/home/mboyle/bd-persist/`, `/etc/systemd/system/`, `/etc/cron.d/`, `/etc/crontab`, `~/.bashrc`, `~/.profile`, `~/.tmux.conf` (2,178 files read). Self-references excluded.
  - `exec` = referrer is an executable (a sibling `.sh`/`.py`, a systemd unit, a cron entry). This is the load-bearing number.
  - `doc` = referrer is prose (checkpoints, handoffs, session notes, logs under `bd-persist/`).
  - `/home/mboyle/bd-persist/harness/`, `/bd-persist/scripts/` and `/bd-persist/RECOVERY/` are **mirror copies of this same harness** and are excluded from both counts; counting them would double every number. KNOWN LIMITATION: `/bd-persist/remote-test6/` and `/bd-persist/remote-bd1/` are remote-host copies that were NOT excluded, so `bd-clean-residue.sh` (exec 1) and `bd-night.sh` (1 of 6) count a self-copy as a referrer. This changes no ranking and no orphan verdict.
- **Broken**: literal (non-variable) absolute paths, sibling `bd-*` invocations and `toolchain/bin/*` invocations were existence-tested. No script invokes a missing sibling and no script invokes a missing `toolchain/bin` tool; every finding is a missing data/worktree path.
- `crontab -l` for mboyle: empty. `/etc/cron.d`: no bd entries. systemd: only `bd-novnc/openbox/x11vnc/xvfb.service` (VNC desktop, not harness). No harness script is scheduled; every one is launched by hand or by another script.
- **atime is not usable as "recently used"** here: this inventory's own reads touched every file. mtime is used instead.

## Counts

| | count |
| --- | --- |
| Total | 149 |
| GENERAL | 99 |
| ONE-SHOT | 50 |
| Referenced by an executable (exec>0) | 51 |
| Referenced only by prose (exec=0, doc>0) | 38 |
| Referenced anywhere (exec>0 or doc>0) | 89 |
| Orphaned (zero references of any kind) | 60 |

Counts restricted to the commissioned 143 (excluding the six restored at 15:25): total 143, GENERAL 94, ONE-SHOT 49, exec-referenced 50, referenced anywhere 84, orphaned 59.

## Most load-bearing scripts

Ranked by executable references; size and mtime shown because the task named them as tie-breakers. These ten are the harness's actual spine -- every lane driver reaches them.

| Rank | Script | exec refs | doc refs | bytes | mtime | Role |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `bd-integrate-row.sh` | 21 | 46 | 21,000 | 2026-08-27 18:31 | worktree -> cut assembly; every lane calls it |
| 2 | `bd-row-chain.sh` | 20 | 7 | 9,726 | 2026-08-29 15:09 | the per-row QA/integrate/verify/ship chain |
| 3 | `bd-ship.sh` | 18 | 9 | 20,335 | 2026-08-30 16:29 | push, exact-head CI wait, merge, prove merged tree |
| 4 | `bd-codex-cut.sh` | 17 | 36 | 1,507 | 2026-08-28 19:53 | one codex task in its own worktree |
| 5 | `bd-fleet-deploy.sh` | 17 | 19 | 9,555 | 2026-08-31 00:24 | deploy to every fleet host |
| 6 | `bd-verify-cut.sh` | 14 | 12 | 24,173 | 2026-08-30 16:43 | verify one immutable candidate (largest file in the harness) |
| 7 | `bd-queue-run.sh` | 14 | 0 | 6,073 | 2026-08-26 02:10 | serial integrate/verify/ship queue |
| 8 | `bd-merge-lane.sh` | 13 | 5 | 695 | 2026-08-25 17:16 | the flock that keeps one PR in CI |
| 9 | `bd-qa-row.sh` | 13 | 3 | 7,570 | 2026-08-28 20:42 | integrator QA on a returned row |
| 10 | `bd-union-resolve.py` | 11 | 2 | 2,926 | 2026-08-27 11:54 | append-only registry conflict resolver |

Two more are load-bearing by weight and prose rather than by call count, and belong on any short list:

- `bd-night.sh` -- 22,791 B (2nd largest), 2026-08-30 22:54, 6 exec / **39 doc** refs. The overnight driver and the single relaunch authority.
- `bd-shoot.py` -- 4,258 B, 2026-08-29 19:36, 0 exec / **71 doc** refs, and named in `CLAUDE.md` A7 as the operator instrument for "a rendered page is evidence". Zero executable refs because it is invoked by hand, not by a lane.

## Per-script table

Sorted by executable references descending, then name. `mtime` is local host time.

| # | Script | mtime | bytes | Class | exec | doc | Purpose | Executable referrers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `bd-integrate-row.sh` | 2026-08-27 18:31 | 21000 | GENERAL | 21 | 46 | Turn a QA'd codex worktree into a proper cut: own worktree, patch applied, release trio, register closed, regen last, verify. | `bd-batch-rows.py`, `bd-bridge-named-row.sh`, `bd-edit.py`, `bd-endgame.sh`, `bd-fanout-lane.sh`, `bd-finish-run.sh`, `bd-night.sh`, `bd-parallel-lane.sh`, `bd-qa-row.sh`, `bd-queue-run.sh`, `bd-register-insert.py`, `bd-register-open-row.py`, `bd-report-changelog.py`, `bd-retry-lane.sh`, `bd-row-chain.sh`, `bd-serial-lane.sh`, `bd-wt-lane.sh` |
| 2 | `bd-row-chain.sh` | 2026-08-29 15:09 | 9726 | GENERAL | 20 | 7 | One row end to end: QA -> integrate -> verify -> ship, refusing loudly at each non-green step. | `bd-drain.sh`, `bd-finish-run.sh`, `bd-install-overlap.sh`, `bd-night.sh`, `bd-parallel-lane.sh`, `bd-pipe-lane.sh`, `bd-qa-row.sh`, `bd-retry-lane.sh`, `bd-serial-lane.sh` |
| 3 | `bd-ship.sh` | 2026-08-30 16:29 | 20335 | GENERAL | 18 | 9 | Push a frozen candidate, wait for exact-head CI under a bound and nonzero denominator, merge only the reviewed head, prove the merged tree. | `bd-1241-chain.sh`, `bd-ci-slot.sh`, `bd-endgame.sh`, `bd-fanout-lane.sh`, `bd-finish.sh`, `bd-heartbeat.sh`, `bd-install-overlap.sh`, `bd-pipeline.sh`, `bd-queue-run.sh`, `bd-row-chain.sh`, `bd-run-tail.sh`, `bd-trio-resume.sh`, `bd-trio-resume2.sh` |
| 4 | `bd-codex-cut.sh` | 2026-08-28 19:53 | 1507 | GENERAL | 17 | 36 | Run one codex implementation task inside its own git worktree (rm -rf's and re-adds the worktree). | `bd-arm-317.sh`, `bd-checkpoint30.sh`, `bd-codex-batch.sh`, `bd-codex-fleet.sh`, `bd-codex-pool.sh`, `bd-codex-pump.sh`, `bd-codex-queue.sh`, `bd-codex-repair.sh`, `bd-endgame.sh`, `bd-night.sh`, `bd-persist/scratch-notes/row243.sibling-import-REJECTED__tests_test_v3_66_1255_pytest_launches_register_before_work.py`, `bd-watch-stalls.sh` |
| 5 | `bd-fleet-deploy.sh` | 2026-08-31 00:24 | 9555 | GENERAL | 17 | 19 | Deploy the merged tree to every fleet host; honours ~/.config/bd/DEPLOY_HOLD and excludes the integrator. | `bd-arm-deploy.sh`, `bd-finish-1300.sh`, `bd-finish-1301.sh`, `bd-finish-1302.sh`, `bd-finish-1303.sh`, `bd-finish-1304.sh`, `bd-finish-1305.sh`, `bd-finish-1306.sh`, `bd-finish-1307.sh`, `bd-finish-1308.sh`, `bd-night.sh` |
| 6 | `bd-verify-cut.sh` | 2026-08-30 16:43 | 24173 | GENERAL | 14 | 12 | Verify one immutable release candidate over the complete net cut (merge-base..candidate) in a disposable exact-SHA worktree. | `bd-1241-chain.sh`, `bd-endgame.sh`, `bd-heartbeat.sh`, `bd-integrate-row.sh`, `bd-persist/scratch-notes/row243.sibling-import-REJECTED__tests_test_v3_66_1255_pytest_launches_register_before_work.py`, `bd-pipeline.sh`, `bd-queue-run.sh`, `bd-rebase-cut.py`, `bd-revert-scaffold.sh`, `bd-row-chain.sh`, `bd-trio-resume.sh` |
| 7 | `bd-queue-run.sh` | 2026-08-26 02:10 | 6073 | GENERAL | 14 | 0 | Serial integrate -> verify -> ship for every QA-green codex row; a failing row is skipped, not blocking. | `bd-codex-pump.sh`, `bd-endgame.sh`, `bd-finish.sh`, `bd-queue-after-1247.sh`, `bd-queue-final.sh`, `bd-queue-last.sh`, `bd-queue2.sh`, `bd-queue3.sh`, `bd-row-chain.sh`, `bd-trio-resume.sh`, `bd-trio-resume2.sh` |
| 8 | `bd-merge-lane.sh` | 2026-08-25 17:16 | 695 | GENERAL | 13 | 5 | flock wrapper enforcing ONE PR in CI at a time around the push/merge step. | `bd-1241-chain.sh`, `bd-ci-slot.sh`, `bd-endgame.sh`, `bd-fanout-lane.sh`, `bd-finish.sh`, `bd-pipe-lane.sh`, `bd-pipeline.sh`, `bd-queue-run.sh`, `bd-row-chain.sh`, `bd-run-tail.sh`, `bd-trio-resume.sh`, `bd-trio-resume2.sh`, `bd-wt-lane.sh` |
| 9 | `bd-qa-row.sh` | 2026-08-30 20:16 | 8341 | GENERAL | 13 | 3 | Integrator QA on a returned codex row in its own worktree: the row's new/changed tests plus a parse check. | `bd-endgame.sh`, `bd-finish-run.sh`, `bd-make-patch.sh`, `bd-night.sh`, `bd-persist/verify.sh`, `bd-qa-watch.sh`, `bd-refile-row.py`, `bd-retry-lane.sh`, `bd-row-chain.sh` |
| 10 | `bd-union-resolve.py` | 2026-08-27 11:54 | 2926 | GENERAL | 11 | 2 | Resolve conflicts in append-only registry files (ci.yml, shard-gate test) by keeping both sides; refuses elsewhere. | `bd-batch-rows.py`, `bd-endgame.sh`, `bd-integrate-row.sh`, `bd-preflight.sh`, `bd-resolve-owned.py` |
| 11 | `bd-night.sh` | 2026-08-30 22:54 | 22791 | GENERAL | 6 | 39 | THE OVERNIGHT LANE. Single relaunch authority; re-reads /home/mboyle/bd-night-spec.txt every loop. | `bd-drain.sh`, `bd-persist/remote-test6/bd-watchdog.sh`, `bd-watchdog.sh`, `bd-width-restore.sh` |
| 12 | `bd-ps.sh` | 2026-08-25 18:13 | 965 | GENERAL | 6 | 0 | Find live processes by argv from /proc without self-match or exit races. | `bd-endgame.sh`, `bd-heartbeat.sh`, `bd-queue-final.sh`, `bd-queue-last.sh`, `bd-queue3.sh` |
| 13 | `bd-register-merge.py` | 2026-08-28 21:25 | 3843 | GENERAL | 5 | 39 | Merge a worker's own register rows into a cut worktree's backlog by identity (bd-integrate-row.sh's tool). | `bd-endgame.sh`, `bd-integrate-row.sh`, `bd-refile-row.py`, `bd-register-insert.py`, `bd-resolve-owned.py` |
| 14 | `bd-pipeline.sh` | 2026-08-25 17:16 | 1295 | GENERAL | 5 | 0 | One cut end to end: rebase onto current main -> verify -> ship; refuses at every non-green step. | `bd-1241-chain.sh`, `bd-run-tail.sh`, `bd-trio-resume.sh`, `bd-trio-resume2.sh` |
| 15 | `bd-drain.sh` | 2026-08-30 22:54 | 3302 | GENERAL | 3 | 12 | No-halt drain of the cut batch: a failing cut is skipped and recorded, the rest keep going. | `bd-night.sh`, `bd-supervise.sh` |
| 16 | `bd-rebase-cut.py` | 2026-08-31 14:36 | 17646 | GENERAL | 3 | 5 | Rebase one frozen cut onto origin/main and resolve the release-trio collision the same way every time. | `bd-endgame.sh`, `bd-parallel-lane.sh`, `bd-pipeline.sh` |
| 17 | `bd-unstale-generated.py` | 2026-08-29 16:29 | 2972 | GENERAL | 2 | 10 | Drop stale generated artifacts (DEPENDENCY_GRAPH.json, FUNCTION_INDEX.md, ...) from blocked worker trees. | `bd-night.sh`, `bd-unstale-loop.sh` |
| 18 | `bd-register-open-row.py` | 2026-08-29 07:25 | 3884 | GENERAL | 2 | 7 | Add an OPEN register row for a night-spec row inside its codex worktree. | `bd-depipe-register.py`, `bd-deref-register.py` |
| 19 | `bd-preflight.sh` | 2026-08-26 16:16 | 5877 | GENERAL | 2 | 6 | Pre-flight a queued cut on an idle fleet host so 'will it go green' is measured before its lane slot. | `bd-endgame.sh`, `bd-persist/scratch-notes/row243.sibling-import-REJECTED__tests_test_v3_66_1255_pytest_launches_register_before_work.py` |
| 20 | `bd-row-audit.py` | 2026-08-30 22:54 | 13273 | GENERAL | 2 | 6 | Refuse a row whose worktree does not contain the work the row claims. | `bd-drain.sh`, `bd-night.sh` |
| 21 | `bd-worker-probe.sh` | 2026-08-30 12:56 | 2420 | GENERAL | 2 | 3 | One read-only delimiter-safe worker/host probe emitting a single pipe-delimited line. | `bd-codex-session-snapshot.py`, `bd-worker-dashboard-v2.sh` |
| 22 | `bd-batch-rows.py` | 2026-08-27 19:54 | 5865 | GENERAL | 2 | 2 | Group eligible register rows into file-disjoint batches that can share one version/cut. | `bd-arm-risky-batch.sh`, `bd-night.sh` |
| 23 | `bd-wt-lane.sh` | 2026-08-29 15:09 | 1993 | GENERAL | 2 | 2 | One writer per worktree: serialize integrator edits against bd-integrate-row.sh's freeze. | `bd-row-chain.sh` |
| 24 | `bd-retry-lane.sh` | 2026-08-26 14:22 | 2396 | GENERAL | 2 | 1 | bd-finish-run.sh minus the endgame call: drain every ready cut serially and stop. | `bd-actions-watch.sh`, `bd-resume-now.sh` |
| 25 | `bd-serial-lane.sh` | 2026-08-26 04:48 | 1488 | GENERAL | 2 | 1 | Run QA-green cuts through bd-row-chain.sh one at a time in the given order; stops on first failure. | `bd-resume-queue-batch1.sh` |
| 26 | `bd-clean-vms.sh` | 2026-08-26 03:00 | 4070 | GENERAL | 2 | 0 | Archive then kill every codex/claude-code session on the fleet so deploy and the full suite run quiet; self-excludes. | `bd-endgame.sh` |
| 27 | `bd-vault-unlock.sh` | 2026-08-30 17:30 | 6567 | GENERAL | 1 | 16 | Unlock a host's secrets vault from the operator-supplied master password file after a deploy/restart. | `bd-fleet-deploy.sh` |
| 28 | `bd-autorebase.sh` | 2026-08-29 22:37 | 390 | GENERAL | 1 | 15 | Auto-rebase queued worktrees onto main. NEUTERED 2026-08-29 (it hard-reset worktrees and dropped commits); now sleeps. | `bd-watchdog.sh` |
| 29 | `bd-band-remote.sh` | 2026-08-31 00:03 | 8738 | GENERAL | 1 | 14 | Run a verify band on a capacity box rather than on the tree being edited (A6). | `bd-verify-cut.sh` |
| 30 | `bd-codex-pool.sh` | 2026-08-28 11:31 | 1717 | GENERAL | 1 | 9 | Bounded codex worker pool (MAXW); pauses entirely while a verify band runs. | `bd-codex-refill.sh` |
| 31 | `bd-endgame.sh` | 2026-08-26 03:04 | 3833 | GENERAL | 1 | 6 | After the last row merges: deploy the fleet, then run the one sanctioned full suite on test5. | `bd-persist/scratch-notes/row243.sibling-import-REJECTED__tests_test_v3_66_1255_pytest_launches_register_before_work.py` |
| 32 | `bd-fullsuite-remote.sh` | 2026-08-25 20:24 | 1396 | GENERAL | 1 | 6 | Run the sanctioned full suite against current origin/main on an idle fleet host. | `bd-persist/scratch-notes/row243.sibling-import-REJECTED__tests_test_v3_66_1255_pytest_launches_register_before_work.py` |
| 33 | `bd-edit.py` | 2026-08-25 20:02 | 1079 | GENERAL | 1 | 5 | Atomically replace a script via temp-file + rename so a running bash does not resume mid-text. | `bd-endgame.sh` |
| 34 | `bd-remaining.sh` | 2026-08-25 18:52 | 1187 | GENERAL | 1 | 4 | How many queued task items remain unmerged, measured from origin/main's register at call time. | `bd-endgame.sh` |
| 35 | `bd-unstale-loop.sh` | 2026-08-29 08:54 | 1582 | GENERAL | 1 | 4 | Run bd-unstale-generated.py after every merge to keep blocked worker trees free of stale generated artifacts. | `bd-watchdog.sh` |
| 36 | `bd-att-guard.sh` | 2026-08-28 05:25 | 961 | GENERAL | 1 | 3 | Clear the attempt counter for rows whose only refusal was a version-collision ALREADY CLAIMED. | `bd-watchdog.sh` |
| 37 | `bd-persist-loop.sh` | 2026-08-29 03:26 | 1938 | GENERAL | 1 | 3 | Persist everything volatile (codex worktrees included, untracked files tarred) every 30 minutes. | `bd-watchdog.sh` |
| 38 | `bd-checkpoint-loop.sh` | 2026-08-29 03:26 | 1307 | GENERAL | 1 | 2 | Append a measured checkpoint block every 10 minutes and immediately after any merge. | `bd-watchdog.sh` |
| 39 | `bd-heartbeat.sh` | 2026-08-27 19:08 | 5190 | GENERAL | 1 | 2 | One line, max 100 chars, every 10 minutes; staleness wins the space over progress. | `bd-endgame.sh` |
| 40 | `bd-prepush.sh` | 2026-08-30 16:02 | 6683 | GENERAL | 1 | 2 | Everything CI would tell you, locally in ~2 minutes (locale pins, env ledger, mutant anchors). | `bd-verify-cut.sh` |
| 41 | `bd-repin-baseline.py` | 2026-08-27 18:30 | 2809 | GENERAL | 1 | 1 | Re-derive the gate-scope baseline count and digest pins from the assembled cut. | `bd-integrate-row.sh` |
| 42 | `bd-1241-finish.py` | 2026-08-25 17:46 | 4455 | ONE-SHOT | 1 | 0 | Apply the cwd= isolation fix to v3.66.1241 and prove it RED-first. **[BROKEN: worktree bd-cuts/cut/1241-owner-observation-deadline is gone.]** | `bd-1241-chain.sh` |
| 43 | `bd-ci-slot.sh` | 2026-08-26 11:33 | 1505 | GENERAL | 1 | 0 | N-slot CI admission control (N=3); replaces bd-merge-lane.sh's single flock. | `bd-parallel-lane.sh` |
| 44 | `bd-clean-residue.sh` | 2026-08-26 11:34 | 2583 | GENERAL | 1 | 0 | Archive irreproducible /tmp/bd-* residue plus a manifest of everything else, then delete. | `bd-persist/remote-test6/bd-clean-residue.sh` |
| 45 | `bd-codex-fleet.sh` | 2026-08-25 17:15 | 1001 | ONE-SHOT | 1 | 0 | Dispatch a fixed remaining queue to codex, ordered by speed/efficiency/robustness. | `bd-codex-pump.sh` |
| 46 | `bd-endgame2.sh` | 2026-08-26 12:17 | 5211 | ONE-SHOT | 1 | 0 | Endgame variant with a hardcoded keep-list (row243/row261/rowa-2/rowg-toctou). | `bd-finish-run.sh` |
| 47 | `bd-make-brief.py` | 2026-08-25 17:12 | 5146 | GENERAL | 1 | 0 | Generate one codex brief per backlog row: COMMON.md + the row's own verbatim text + operator rulings. | `bd-endgame.sh` |
| 48 | `bd-prbody.py` | 2026-08-25 18:03 | 2518 | GENERAL | 1 | 0 | Build a PR body from a worker's own report tail; refuses rather than emitting a thin body. | `bd-endgame.sh` |
| 49 | `bd-resolve-owned.py` | 2026-08-27 11:29 | 2949 | GENERAL | 1 | 0 | Resolve the three integrator-owned conflict-prone files (backlog, ci.yml, shard-gate test) after a rebase pop. | `bd-rebase-all.sh` |
| 50 | `bd-supervise.sh` | 2026-08-27 01:16 | 2821 | ONE-SHOT | 1 | 0 | Detached supervisor that relaunches bd-drain.sh with only still-open rows; carries a hardcoded ORDER/row map. | `bd-night.sh` |
| 51 | `bd-w1-matched.sh` | 2026-08-25 16:55 | 2114 | ONE-SHOT | 1 | 0 | Interleaved matched-environment experiment for the two W1 band failures at v3.66.1241. **[BROKEN: worktree bd-cuts/cut/1241-owner-observation-deadline is gone.]** | `bd-checkpoint30.sh` |
| 52 | `bd-shoot.py` | 2026-08-29 19:36 | 4258 | GENERAL | 0 | 72 | Capture what the browser sees on a members page plus every media link (operator instrument, never a gate). | -- |
| 53 | `bd-codex-session-snapshot.py` | 2026-08-30 18:25 | 14413 | GENERAL | 0 | 26 | Persist a no-signal immutable snapshot of live Codex, tmux, repo and fleet state; never attaches to or signals a process. | -- |
| 54 | `bd-checkpoint.sh` | 2026-08-25 01:27 | 1835 | GENERAL | 0 | 20 | Rewrite FLEET_RUN_CHECKPOINT.md's MEASURED block every 30 minutes; every fact re-derived at write time. | -- |
| 55 | `bd-t6-capture-main.sh` | 2026-08-28 13:00 | 1830 | ONE-SHOT | 0 | 12 | Full 18,499-test capture on test6 at current origin/main (bd-t6-capture-1307.sh with the pin removed). | -- |
| 56 | `bd-watch-stalls.sh` | 2026-08-27 22:23 | 1453 | GENERAL | 0 | 12 | Re-dispatch any codex worker that ended having produced no work. | -- |
| 57 | `bd-finish-1301.sh` | 2026-08-27 22:19 | 1887 | ONE-SHOT | 0 | 10 | Merge v1301 when CI greens, then deploy the fleet once the lane is quiet. | -- |
| 58 | `bd-finish-1303.sh` | 2026-08-27 22:58 | 1701 | ONE-SHOT | 0 | 8 | Watch v1303 to merge, then fleet deploy. | -- |
| 59 | `bd-width-restore.sh` | 2026-08-28 20:48 | 1707 | GENERAL | 0 | 8 | Climb the batch width back up after bd-night's ladder demotes it, on two consecutive clean merges. | -- |
| 60 | `bd-capture-url.py` | 2026-08-29 18:54 | 3101 | GENERAL | 0 | 5 | Capture a public no-login page into a .wacz in the corpus format (corpus gap-filling). | -- |
| 61 | `bd-codex-refill.sh` | 2026-08-28 21:28 | 1505 | GENERAL | 0 | 5 | Top the codex pool's queue file up from a standing worklist every 10 minutes. | -- |
| 62 | `bd-progress.sh` | 2026-08-28 21:59 | 3078 | GENERAL | 0 | 5 | One terse event line every 10 minutes; supersedes bd-heartbeat.sh. | -- |
| 63 | `bd-rebase-all.sh` | 2026-08-31 11:41 | 5731 | GENERAL | 0 | 5 | Re-base every unmerged worker worktree onto current origin/main before batching. | -- |
| 64 | `bd-report-changelog.py` | 2026-08-29 04:48 | 3540 | GENERAL | 0 | 5 | Append a CHANGELOG block to a codex worker report derived from its real diff, in the shape integrate requires. | -- |
| 65 | `bd-watchdog.sh` | 2026-08-29 16:23 | 1223 | GENERAL | 0 | 5 | Separate watchdog that revives the lane and its heartbeat, and says out loud when it acts. | -- |
| 66 | `bd-codex-repair.sh` | 2026-08-29 12:41 | 1960 | GENERAL | 0 | 4 | Run codex against an EXISTING worktree without destroying it (bd-codex-cut.sh rm -rf's). | -- |
| 67 | `bd-w1-control-1303.sh` | 2026-08-27 23:37 | 3028 | ONE-SHOT | 0 | 4 | Matched control arm for the v1303 band failures on clean origin/main. | -- |
| 68 | `bd-anchorcheck.py` | 2026-08-31 11:19 | 6338 | GENERAL | 0 | 3 | Prove every tracked mutant anchor still occurs exactly once in the tree (pre-freeze gate). | -- |
| 69 | `bd-deref-register.py` | 2026-08-29 18:12 | 1843 | GENERAL | 0 | 3 | Rewrite dangling 'row NNN' citations in inserted register rows to 'a separate row'. | -- |
| 70 | `bd-edgecheck.py` | 2026-08-31 13:45 | 7501 | GENERAL | 0 | 3 | Classify a cut's new import edges and declare the routine ones for the import-graph gate. | -- |
| 71 | `bd-netprobe.sh` | 2026-08-29 02:07 | 2358 | GENERAL | 0 | 3 | Measure one host's WAN capacity single-stream and multi-stream, and say what each number means. | -- |
| 72 | `bd-retrio.py` | 2026-08-31 11:30 | 6517 | GENERAL | 0 | 3 | Resolve the release-trio (+PIN_INDEX/STATIC_KB_MANIFEST) collision a rebase always produces. | -- |
| 73 | `bd-wansat.sh` | 2026-08-29 02:08 | 2573 | GENERAL | 0 | 3 | Saturate the WAN from the whole fleet at once and report aggregate bytes-over-wall-clock throughput. | -- |
| 74 | `bd-arm-batching.sh` | 2026-08-27 16:45 | 1147 | ONE-SHOT | 0 | 2 | Wait for row 320 to merge, then apply the operator-authorized deferral+batching change exactly once. | -- |
| 75 | `bd-bisect-1314.sh` | 2026-08-28 12:39 | 2275 | ONE-SHOT | 0 | 2 | Bisect which cut between v1306 and v1314 introduced the six test6 capture failures. | -- |
| 76 | `bd-capture-site-setup.sh` | 2026-08-28 20:49 | 2469 | GENERAL | 0 | 2 | Install the authenticated session cookie jar on a throwaway capture VM. | -- |
| 77 | `bd-depipe-register.py` | 2026-08-29 07:25 | 1864 | GENERAL | 0 | 2 | Remove stray `|` delimiters from register rows inserted from brief prose, so the backlog gate parses the right row count. | -- |
| 78 | `bd-finish-1307.sh` | 2026-08-28 02:16 | 2079 | ONE-SHOT | 0 | 2 | Watch v1307 to merge, then fleet deploy. | -- |
| 79 | `bd-register-insert.py` | 2026-08-28 21:25 | 2078 | GENERAL | 0 | 2 | Insert one known register row into a backlog file in numeric order (bd-rebase's tool). | -- |
| 80 | `bd-remote-suite.sh` | 2026-08-26 15:35 | 2473 | GENERAL | 0 | 2 | Run the sanctioned full suite on a remote fleet host and refuse rather than report an unmeasured claim. | -- |
| 81 | `bd-t6-capture-1307.sh` | 2026-08-28 14:12 | 1963 | ONE-SHOT | 0 | 2 | Full 18,499-test capture on test6 pinned at v3.66.1307. | -- |
| 82 | `bd-t6-matched.sh` | 2026-08-28 11:20 | 1995 | ONE-SHOT | 0 | 2 | Matched alternating-arm control on test6: did v1314 introduce the six capture failures, or is the box the variable? | -- |
| 83 | `bd-vm-bringup.sh` | 2026-08-28 20:49 | 2592 | ONE-SHOT | 0 | 2 | Provision bd1..bd4, install codex, register them as build hosts; gated on host bd reaching VERDICT: READY. | -- |
| 84 | `bd-codex-pump.sh` | 2026-08-26 08:12 | 2134 | GENERAL | 0 | 1 | Keep N codex workers busy continuously from a directory queue (pending/ -> running/ -> done/). | -- |
| 85 | `bd-codex-queue.sh` | 2026-08-27 22:09 | 3194 | GENERAL | 0 | 1 | Sequential codex build runner: one worker at a time, never during a band. | -- |
| 86 | `bd-resolve-stash-conflict.py` | 2026-08-29 08:13 | 1648 | ONE-SHOT | 0 | 1 | Resolve row 373's stash conflict in two named TypeScript files by keeping both sides in upstream-then-stashed order. | -- |
| 87 | `bd-resume-now.sh` | 2026-08-26 15:26 | 2690 | GENERAL | 0 | 1 | The go button: one probe, then either resume the whole merge lane or say why not. No polling. | -- |
| 88 | `bd-revalidate.sh` | 2026-08-31 00:19 | 5073 | GENERAL | 0 | 1 | Revalidate a parked worker candidate against current main when its recorded evidence has gone stale. | -- |
| 89 | `bd-worker-dashboard-v2.sh` | 2026-08-30 14:04 | 8178 | GENERAL | 0 | 1 | Read-only operations dashboard for managed tasks and all 12 authoritative hosts (tmux bd-workers); env-overridable paths. | -- |
| 90 | `bd-1241-chain.sh` | 2026-08-25 17:47 | 5412 | ONE-SHOT | 0 | 0 | Wait for the v1241 matched experiment to release its worktree, then apply the cwd= isolation fix, re-verify and ship it. **[BROKEN: worktrees bd-cuts/cut/1241-owner-observation-deadline, /1242-t2-history-runtime, /1243-t8-cluster-runtime are gone.]** | -- |
| 91 | `bd-actions-watch.sh` | 2026-08-26 15:17 | 4193 | GENERAL | 0 | 0 | Wait out a GitHub Actions outage, probe with a real PR re-fire, then resume the merge lane unattended. | -- |
| 92 | `bd-arm-317.sh` | 2026-08-27 21:22 | 1395 | ONE-SHOT | 0 | 0 | Dispatch row 317's codex build only when the lane is idle. | -- |
| 93 | `bd-arm-deploy.sh` | 2026-08-27 20:11 | 1630 | ONE-SHOT | 0 | 0 | Deploy the fleet once v3.66.1299 (rows 296+243) is merged. | -- |
| 94 | `bd-arm-risky-batch.sh` | 2026-08-27 18:25 | 2348 | ONE-SHOT | 0 | 0 | Wait until only the four named risky rows remain, then clear SOLO_ROWS so they batch together. | -- |
| 95 | `bd-board.sh` | 2026-08-25 17:51 | 3759 | ONE-SHOT | 0 | 0 | Live tmux task board redrawn every 15s showing what each job is doing; carries a hardcoded row list. | -- |
| 96 | `bd-bridge-named-row.sh` | 2026-08-26 04:56 | 1781 | GENERAL | 0 | 0 | Bridge a descriptively-named codex brief to the numbered row bd-integrate-row.sh needs. | -- |
| 97 | `bd-brief-from-spec.py` | 2026-08-29 03:13 | 3715 | GENERAL | 0 | 0 | Generate a codex brief for a row from its verbatim canonical spec text; refuses on a too-thin row. | -- |
| 98 | `bd-capture-on-50.sh` | 2026-08-28 21:49 | 1507 | ONE-SHOT | 0 | 0 | Run the FRESH_HOST_BRINGUP step-4 capture gate on host 10.0.70.50 (bd). | -- |
| 99 | `bd-checkpoint30.sh` | 2026-08-25 17:03 | 2883 | GENERAL | 0 | 0 | Rewrite only the marker-delimited LIVE STATE block at the top of FLEET_RUN_CHECKPOINT.md every 30 min. | -- |
| 100 | `bd-codex-batch.sh` | 2026-08-25 16:43 | 1350 | ONE-SHOT | 0 | 0 | Wait for the 1241 band, then dispatch codex rows 183/184/242 in parallel. | -- |
| 101 | `bd-coldstate.sh` | 2026-08-24 14:54 | 1993 | GENERAL | 0 | 0 | Refresh everything a cold successor session needs; idempotent, safe mid-cut. | -- |
| 102 | `bd-corpus-mirror.sh` | 2026-08-27 02:32 | 2479 | GENERAL | 0 | 0 | Mirror every fleet host's captures/ to test5 with rsync, deliberately without --delete. | -- |
| 103 | `bd-cut.sh` | 2026-08-25 15:23 | 1156 | GENERAL | 0 | 0 | Start an integrator cut in its own git worktree. | -- |
| 104 | `bd-exit-tracer-sitecustomize.py` | 2026-08-14 11:58 | 3782 | GENERAL | 0 | 0 | sitecustomize tracer that catches the deliberate exit(1) wedging a pytest xdist worker (backlog row 102). | -- |
| 105 | `bd-fanout-lane.sh` | 2026-08-26 22:31 | 3493 | GENERAL | 0 | 0 | Fan-out lane: integrate serially, verify in parallel across the fleet, ship in version order. | -- |
| 106 | `bd-finish-1300.sh` | 2026-08-27 21:26 | 1990 | ONE-SHOT | 0 | 0 | Carry v1300 to merge, then deploy the fleet. | -- |
| 107 | `bd-finish-1302.sh` | 2026-08-27 22:58 | 1701 | ONE-SHOT | 0 | 0 | Byte-identical copy of bd-finish-1303.sh (still watches cut/1303 -- MISNAMED). | -- |
| 108 | `bd-finish-1304.sh` | 2026-08-28 00:54 | 2079 | ONE-SHOT | 0 | 0 | Watch v1304 to merge, then fleet deploy. | -- |
| 109 | `bd-finish-1305.sh` | 2026-08-28 01:30 | 2079 | ONE-SHOT | 0 | 0 | Watch v1305 to merge, then fleet deploy. | -- |
| 110 | `bd-finish-1306.sh` | 2026-08-28 01:49 | 2079 | ONE-SHOT | 0 | 0 | Watch v1306 to merge, then fleet deploy. | -- |
| 111 | `bd-finish-1308.sh` | 2026-08-28 03:22 | 2079 | ONE-SHOT | 0 | 0 | Watch v1308 to merge, then fleet deploy. | -- |
| 112 | `bd-finish-296.sh` | 2026-08-27 20:40 | 1542 | ONE-SHOT | 0 | 0 | Carry row 296 to merge so bd-arm-deploy can fire. | -- |
| 113 | `bd-finish-run.sh` | 2026-08-26 11:51 | 2347 | GENERAL | 0 | 0 | Drain every ready cut serially, then run bd-endgame2.sh. Designed to need nobody. | -- |
| 114 | `bd-finish.sh` | 2026-08-25 21:59 | 972 | ONE-SHOT | 0 | 0 | Ship the v3.66.1250 fleet batch once its re-verify clears, then exec bd-queue-run.sh. **[BROKEN: reads the verdict from inflight/1250-fix3-verify.log and ships a hardcoded cut/1250 branch that merged long ago.]** | -- |
| 115 | `bd-fleet-audit-cmd.sh` | 2026-08-26 04:12 | 1664 | GENERAL | 0 | 0 | Per-host audit snippet: head/version/dirty, load, node, service health, bundle marker, testruns, ssh keys. | -- |
| 116 | `bd-freshhost-50.sh` | 2026-08-28 20:18 | 2171 | ONE-SHOT | 0 | 0 | Fresh-host bring-up test on 10.0.70.50 following FRESH_HOST_BRINGUP.md steps 1-2 exactly. | -- |
| 117 | `bd-install-overlap.sh` | 2026-08-28 20:32 | 1160 | ONE-SHOT | 0 | 0 | Atomically mv bd-ship.sh.new / bd-row-chain.sh.new over the live scripts when no chain is mid-flight. **[BROKEN: mv sources /home/mboyle/bd-ship.sh.new and /home/mboyle/bd-row-chain.sh.new do not exist; the one-shot swap has nothing to install.]** | -- |
| 118 | `bd-kill-mine.sh` | 2026-08-26 12:27 | 980 | GENERAL | 0 | 0 | Kill processes by argv pattern while excluding self and ancestors by PID. | -- |
| 119 | `bd-lane-experiment.sh` | 2026-08-26 18:20 | 2790 | GENERAL | 0 | 0 | Whole-tree lane experiment: run the entire suite in one parallel lane, then retry every failure serially. | -- |
| 120 | `bd-make-patch.sh` | 2026-08-26 16:11 | 2787 | GENERAL | 0 | 0 | Build a worker patch correctly, including untracked files and excluding regen artifacts. | -- |
| 121 | `bd-ollama-50.sh` | 2026-08-28 20:22 | 1874 | ONE-SHOT | 0 | 0 | Install ollama on host bd (10.0.70.50) after provisioning, per FRESH_HOST_BRINGUP step 3b. | -- |
| 122 | `bd-parallel-lane.sh` | 2026-08-26 11:38 | 1804 | GENERAL | 0 | 0 | Pipelined lane running up to N cuts through bd-row-chain.sh concurrently, with an integrate lock. | -- |
| 123 | `bd-pipe-lane.sh` | 2026-08-26 22:31 | 2348 | GENERAL | 0 | 0 | Depth-2 pipelined lane overlapping cut N's CI wait with cut N+1's verify. | -- |
| 124 | `bd-provision-fan.sh` | 2026-08-28 20:18 | 1617 | ONE-SHOT | 0 | 0 | Fan scripts/provision_test_host.sh out to bd1-bd4 only after host bd proves the documented path works. | -- |
| 125 | `bd-publish.sh` | 2026-08-24 11:40 | 732 | GENERAL | 0 | 0 | Publish the integrator's current working tree to the continuous-validation hosts by content digest. | -- |
| 126 | `bd-qa-watch.sh` | 2026-08-25 17:55 | 970 | GENERAL | 0 | 0 | As each codex row returns, run bd-qa-row.sh on it automatically, once per row, recording QA_RC. | -- |
| 127 | `bd-queue-after-1247.sh` | 2026-08-25 20:32 | 501 | ONE-SHOT | 0 | 0 | Wait for v3.66.1247 to merge, then run the remaining three cuts. | -- |
| 128 | `bd-queue-final.sh` | 2026-08-25 22:41 | 171 | ONE-SHOT | 0 | 0 | Wait for the running bd-queue-run.sh to exit, then exec it again. Byte-identical to bd-queue-last.sh. | -- |
| 129 | `bd-queue-last.sh` | 2026-08-25 23:21 | 171 | ONE-SHOT | 0 | 0 | Wait for the running bd-queue-run.sh to exit, then exec it again. Byte-identical to bd-queue-final.sh. | -- |
| 130 | `bd-queue2.sh` | 2026-08-25 19:12 | 434 | ONE-SHOT | 0 | 0 | Second queue pass over the rows not in the first SPECS list, plus the batched 183+184 cut. | -- |
| 131 | `bd-queue3.sh` | 2026-08-25 20:00 | 382 | ONE-SHOT | 0 | 0 | Re-run the cuts the first batched pass skipped, after it finishes. | -- |
| 132 | `bd-refile-row.py` | 2026-08-26 12:29 | 3177 | GENERAL | 0 | 0 | Re-file a worker's row onto current main as OPEN with a free id. | -- |
| 133 | `bd-renumber-row.py` | 2026-08-26 05:17 | 2078 | GENERAL | 0 | 0 | Renumber a colliding row a worker filed in its own worktree to a free id; never moves the incumbent. | -- |
| 134 | `bd-repair-brief.py` | 2026-08-29 12:39 | 4931 | GENERAL | 0 | 0 | Write a REPAIR brief for a row whose worktree already holds work that fails for a named, measured reason. | -- |
| 135 | `bd-resume-codex.sh` | 2026-08-27 20:20 | 752 | ONE-SHOT | 0 | 0 | Resume the codex workers suspended for the v1299 band once the lane is quiet. | -- |
| 136 | `bd-resume-queue-batch1.sh` | 2026-08-26 22:31 | 1702 | ONE-SHOT | 0 | 0 | Resume batch 1: the 13 queued rows in the brief-specified order, keeping 243/245 open. | -- |
| 137 | `bd-revert-scaffold.sh` | 2026-08-26 13:53 | 1453 | ONE-SHOT | 0 | 0 | Deferred removal of the frontend/dist scaffold from bd-verify-cut.sh once no instance is running. | -- |
| 138 | `bd-revert-scan.sh` | 2026-08-26 15:58 | 2394 | GENERAL | 0 | 0 | Find lines a held candidate REMOVES that a merge in the window ADDED (silent-revert detector). | -- |
| 139 | `bd-run-tail.sh` | 2026-08-25 17:16 | 2410 | ONE-SHOT | 0 | 0 | Autonomous tail of the 1241/1242/1243 trio, then batch-deploy the fleet. **[BROKEN: worktrees bd-cuts/cut/1242-t2-history-runtime and /1243-t8-cluster-runtime are gone.]** | -- |
| 140 | `bd-strip-generated.sh` | 2026-08-26 07:45 | 943 | GENERAL | 0 | 0 | Drop regen-produced artifacts from a worker's worktree so its patch cannot conflict on them. | -- |
| 141 | `bd-t6-capture-control.sh` | 2026-08-28 11:31 | 2115 | ONE-SHOT | 0 | 0 | Full-capture matched control at the control version on the same host as the 1307 arm. | -- |
| 142 | `bd-trio-resume.sh` | 2026-08-25 18:25 | 1506 | ONE-SHOT | 0 | 0 | Resume the trio at 1242 (changelog order repaired), then 1243. **[BROKEN: worktrees bd-cuts/cut/1242-t2-history-runtime and /1243-t8-cluster-runtime are gone.]** | -- |
| 143 | `bd-trio-resume2.sh` | 2026-08-25 18:44 | 1326 | ONE-SHOT | 0 | 0 | Ship the already-verified 1242 candidate 865ae8e without re-verifying, then run 1243. **[BROKEN: worktree bd-cuts/cut/1243-t8-cluster-runtime is gone.]** | -- |
| 144 | `bd-unpark-after-355.sh` | 2026-08-28 19:40 | 1076 | ONE-SHOT | 0 | 0 | Unpark the five rows blocked behind row 355 the moment it merges. | -- |
| 145 | `bd-w1-candidate-1303.sh` | 2026-08-27 23:39 | 1585 | ONE-SHOT | 0 | 0 | Candidate arm of the v1303 matched band experiment; runs after the control. **[BROKEN: worktree bd-cuts/cut/1303-wait-for-content-not-existence is gone.]** | -- |
| 146 | `bd-w1-control-1307.sh` | 2026-08-28 02:42 | 2326 | ONE-SHOT | 0 | 0 | Matched control arm for the v1307 (row 243) 1190 descendant-kill failure. **[BROKEN: worktree bd-cuts/cut/1307-owned-pytest-launches-register is gone.]** | -- |
| 147 | `bd-watch-merge.sh` | 2026-08-26 18:26 | 844 | GENERAL | 0 | 0 | Report one row's merge phases reading only the current lane's lines of FINISH.log (anchored, not grepped). | -- |
| 148 | `bd-worker-dashboard.sh` | 2026-08-30 12:47 | 3131 | GENERAL | 0 | 0 | Read-only live dashboard for the current integration lane and capacity workers; superseded by the v2 script. | -- |
| 149 | `bd-worktree-archive.sh` | 2026-08-28 21:39 | 3743 | GENERAL | 0 | 0 | Archive then delete finished worker worktrees, checking all three finished conditions per tree. | -- |


## Byte-identical duplicates (md5, measured)

| md5 | files |
| --- | --- |
| `6ed5077e20a47863893ebbb72e71c66a` | `bd-finish-1302.sh`, `bd-finish-1303.sh` -- 1302 still watches `cut/1303` and logs `[finish-1303]`; it is a misnamed copy, not a 1302 watcher |
| `ebc27c6e6f07a980ea6978ac21cba821` | `bd-queue-final.sh`, `bd-queue-last.sh` |

Near-identical (diff is a literal only):

- `bd-t6-capture-1307.sh` vs `bd-t6-capture-main.sh` -- 4 hunks, all the version pin (`rev-list --grep=v3.66.1307` + detached checkout vs `rev-parse origin/main` + `reset --hard`).
- `bd-finish-run.sh` vs `bd-retry-lane.sh` -- **one line** differs (line 55: `bash bd-endgame2.sh` vs a `say` that the endgame already ran).
- `bd-w1-control-1303.sh` vs `bd-w1-control-1307.sh` -- header prose plus the band file list.
- `bd-finish-1300.sh` .. `bd-finish-1308.sh` -- the same 20-line merge-watcher with the version integer substituted.

## Overlap clusters

Canonical is chosen by the commissioned rule: most recently modified + most executable references + most complete. Where recency and reference count disagree, both facts are stated.

| Cluster | Members | Canonical | Superseded / subsumed |
| --- | --- | --- | --- |
| **Overnight / batch driver** | bd-night.sh, bd-supervise.sh, bd-drain.sh, bd-finish-run.sh, bd-retry-lane.sh, bd-serial-lane.sh, bd-parallel-lane.sh, bd-pipe-lane.sh, bd-fanout-lane.sh, bd-resume-queue-batch1.sh | **bd-night.sh** (22,791 B, 2026-08-30 22:54, 6 exec / 39 doc refs; its own header: "Replaces bd-supervise.sh -- there is ONE relaunch authority"; spec re-read from `bd-night-spec.txt` each loop) | bd-supervise.sh (hardcoded ORDER row map), bd-finish-run.sh, bd-retry-lane.sh (1-line variant of it), bd-serial-lane.sh, bd-parallel-lane.sh, bd-pipe-lane.sh, bd-fanout-lane.sh, bd-resume-queue-batch1.sh |
| **Batch drain (no-halt)** | bd-drain.sh, bd-serial-lane.sh, bd-fanout-lane.sh | **bd-drain.sh** (2026-08-30 22:54, 3 exec refs, called by bd-night.sh and bd-supervise.sh; the other two abort the whole queue on first refusal) | bd-serial-lane.sh, bd-fanout-lane.sh |
| **Per-row chain** | bd-row-chain.sh, bd-pipeline.sh, bd-wt-lane.sh | **bd-row-chain.sh** (20 exec refs, QA -> integrate -> verify -> ship) | bd-pipeline.sh (5 exec refs, rebase -> verify -> ship, no QA step). bd-wt-lane.sh is an adjunct one-writer-per-worktree wrapper, not a duplicate |
| **Serial queue passes** | bd-queue-run.sh, bd-queue2.sh, bd-queue3.sh, bd-queue-final.sh, bd-queue-last.sh, bd-queue-after-1247.sh | **bd-queue-run.sh** (6,073 B, 14 exec refs) | the other five are wait-then-`exec bd-queue-run.sh` shims; bd-queue-final.sh and bd-queue-last.sh are byte-identical to each other |
| **Merge-watcher / finish** | bd-finish-1300..1308 (9), bd-finish-296.sh, bd-finish.sh, bd-watch-merge.sh | **bd-finish-1308.sh** (2026-08-28 03:22, newest of an otherwise identical family) | bd-finish-1300..1307, bd-finish-296.sh, bd-finish.sh (pinned to v1250). All ONE-SHOT; bd-finish-1302.sh is a misnamed byte-copy of 1303 |
| **CI admission control** | bd-merge-lane.sh, bd-ci-slot.sh | **bd-merge-lane.sh** by adoption (13 exec refs; every lane calls it) -- but bd-ci-slot.sh is NEWER (2026-08-26 11:33 vs 08-25 17:16) and its header declares it "Replaces bd-merge-lane.sh's single flock". The replacement was written and never adopted (1 exec ref) | -- |
| **Checkpoint writers** | bd-checkpoint.sh, bd-checkpoint30.sh, bd-checkpoint-loop.sh, bd-coldstate.sh | **bd-checkpoint-loop.sh** (2026-08-29 03:26, driven by bd-watchdog.sh; delegates WHAT to bd-checkpoint-write and only decides WHEN) | bd-checkpoint.sh (20 doc refs but 0 exec), bd-checkpoint30.sh, bd-coldstate.sh |
| **Heartbeat / progress line** | bd-heartbeat.sh, bd-progress.sh | **bd-progress.sh** (2026-08-28 21:59; header: "Supersedes the 10-minute heartbeat"; a copy is RUNNING now, pid 1389767) | bd-heartbeat.sh |
| **Live dashboards** | bd-worker-dashboard-v2.sh, bd-worker-dashboard.sh, bd-board.sh | **bd-worker-dashboard-v2.sh** (8,178 B, 2026-08-30 14:04, env-overridable paths, 12 hosts; two copies RUNNING now, pids 1303697 / 1903918) | bd-worker-dashboard.sh (3,131 B, hardcoded paths), bd-board.sh (hardcoded row list) |
| **Codex worker pool** | bd-codex-pool.sh, bd-codex-pump.sh, bd-codex-queue.sh, bd-codex-fleet.sh, bd-codex-batch.sh, bd-codex-refill.sh, bd-watch-stalls.sh | **bd-codex-pool.sh** (2026-08-28 11:31, bounded MAXW, pauses during a band, fed by bd-codex-refill.sh) | bd-codex-pump.sh, bd-codex-queue.sh, bd-codex-fleet.sh, bd-codex-batch.sh |
| **Codex worktree runner** | bd-codex-cut.sh, bd-codex-repair.sh | **bd-codex-cut.sh** (17 exec refs) | none -- bd-codex-repair.sh is complementary (existing worktree; bd-codex-cut.sh begins `rm -rf "$W"`) |
| **Brief generators** | bd-make-brief.py, bd-brief-from-spec.py, bd-repair-brief.py | **bd-brief-from-spec.py** (2026-08-29 03:13, verbatim spec text, refuses a thin row) | bd-make-brief.py (2026-08-25). bd-repair-brief.py is a distinct variant (repair briefs against an existing worktree) |
| **Rebase / release-trio collision** | bd-rebase-cut.py, bd-retrio.py, bd-rebase-all.sh, bd-autorebase.sh | **bd-rebase-cut.py** (17,646 B, 2026-08-31 14:36, 3 exec refs) | bd-retrio.py (6,517 B, 2026-08-31 11:30, 0 exec refs, same stated job), bd-autorebase.sh (NEUTERED 2026-08-29 after dropping worker commits). bd-rebase-all.sh is the multi-worktree driver, not a duplicate |
| **Conflict resolvers** | bd-union-resolve.py, bd-resolve-owned.py, bd-resolve-stash-conflict.py | **bd-union-resolve.py** (11 exec refs, explicit file allowlist, refuses elsewhere) | bd-resolve-stash-conflict.py (ONE-SHOT, row 373). bd-resolve-owned.py wraps bd-union-resolve.py plus the backlog rule -- complementary |
| **Register row editors** | bd-register-merge.py, bd-register-insert.py, bd-register-open-row.py, bd-refile-row.py, bd-renumber-row.py, bd-deref-register.py, bd-depipe-register.py | **bd-register-merge.py** (5 exec / 37 doc refs, bd-integrate-row.sh's tool) | none merged deliberately -- bd-register-insert.py's own header says "do not merge the two; I clobbered that one on 2026-08-28". bd-deref-register.py and bd-depipe-register.py are two single-purpose fixups of the SAME bd-register-open-row.py output and could be one tool |
| **Remote full suite** | bd-remote-suite.sh, bd-fullsuite-remote.sh | **bd-remote-suite.sh** (2026-08-26 15:35; refuses rather than reporting an unmeasured claim) | bd-fullsuite-remote.sh (2026-08-25 20:24) |
| **Stale generated artifacts** | bd-unstale-loop.sh, bd-unstale-generated.py, bd-strip-generated.sh | **bd-unstale-loop.sh** + bd-unstale-generated.py (2026-08-29, driven by bd-watchdog.sh after every merge) | bd-strip-generated.sh (2026-08-26, one-shot equivalent, 1 exec ref) |
| **Endgame** | bd-endgame.sh, bd-endgame2.sh | **bd-endgame2.sh** by adoption (newer 2026-08-26 12:17, called by bd-finish-run.sh) -- but it is ONE-SHOT (hardcoded row243/row261 keep-list); bd-endgame.sh is the documented general form | -- |
| **Process find / kill** | bd-ps.sh, bd-kill-mine.sh | **bd-ps.sh** (6 exec refs) | none -- complementary (find vs kill), same self-match lesson |
| **Fresh-host bring-up** | bd-vm-bringup.sh, bd-freshhost-50.sh, bd-provision-fan.sh, bd-capture-on-50.sh, bd-ollama-50.sh, bd-capture-site-setup.sh | **bd-vm-bringup.sh** (bd1..bd4, most complete) | bd-freshhost-50.sh, bd-provision-fan.sh, bd-capture-on-50.sh, bd-ollama-50.sh -- all pinned to host bd / 10.0.70.50 |
| **test6 six-failure investigation** | bd-t6-capture-main.sh, bd-t6-capture-1307.sh, bd-t6-capture-control.sh, bd-t6-matched.sh, bd-bisect-1314.sh | **bd-t6-capture-main.sh** (2026-08-28 13:00, 12 doc refs) | bd-t6-capture-1307.sh (same file, version pinned), bd-t6-capture-control.sh, bd-t6-matched.sh, bd-bisect-1314.sh (both narrower shapes that the headers record as having failed to reproduce) |
| **W1 matched-band experiments** | bd-w1-control-1307.sh, bd-w1-control-1303.sh, bd-w1-candidate-1303.sh, bd-w1-matched.sh | **bd-w1-control-1307.sh** (2026-08-28 02:42, newest template) | bd-w1-control-1303.sh, bd-w1-candidate-1303.sh, bd-w1-matched.sh |
| **Arm-on-condition triggers** | bd-arm-317.sh, bd-arm-batching.sh, bd-arm-deploy.sh, bd-arm-risky-batch.sh, bd-unpark-after-355.sh, bd-queue-after-1247.sh, bd-resume-codex.sh, bd-install-overlap.sh, bd-revert-scaffold.sh | **bd-arm-risky-batch.sh** (2026-08-27 18:25, largest at 2,348 B) | all others -- every member is a wait-for-condition-then-act-once shim; none is general |

## Broken scripts (literal reference that no longer resolves)

Existence-tested at snapshot time. No script invokes a missing sibling `bd-*` script and none invokes a missing `toolchain/bin/*` tool -- every finding below is a missing data path or worktree.

| Script | Reason |
| --- | --- |
| `bd-install-overlap.sh` | Its entire body is `mv -f /home/mboyle/bd-ship.sh.new` and `/home/mboyle/bd-row-chain.sh.new` over the live scripts. **Neither `.new` file exists** -- the swap already ran (the pre-swap originals sit beside them as `bd-ship.sh.preoverlap` and `bd-row-chain.sh.preoverlap`, both 2026-08-28 21:01), consuming its inputs. Re-running it fails both moves and installs nothing. |
| `bd-finish.sh` | Gates on `fleet-run-artifacts/2026-08-25/inflight/1250-fix3-verify.log` and ships hardcoded branch `cut/1250-fleet-and-runtime-truth`, merged long ago; then `exec`s bd-queue-run.sh regardless. |
| `bd-1241-chain.sh` | Waits on worktrees `bd-cuts/cut/1241-owner-observation-deadline`, `/1242-t2-history-runtime`, `/1243-t8-cluster-runtime` -- all three removed. |
| `bd-1241-finish.py` | Targets `bd-cuts/cut/1241-owner-observation-deadline` -- removed. |
| `bd-run-tail.sh` | Targets `bd-cuts/cut/1242-t2-history-runtime` and `/1243-t8-cluster-runtime` -- removed. |
| `bd-trio-resume.sh` | Same two removed worktrees. |
| `bd-trio-resume2.sh` | Targets `bd-cuts/cut/1243-t8-cluster-runtime` -- removed. |
| `bd-w1-matched.sh` | Targets `bd-cuts/cut/1241-owner-observation-deadline` -- removed. |
| `bd-w1-candidate-1303.sh` | Targets `bd-cuts/cut/1303-wait-for-content-not-existence` -- removed. |
| `bd-w1-control-1307.sh` | Targets `bd-cuts/cut/1307-owned-pytest-launches-register` -- removed. |
| `bd-autorebase.sh` | Not broken by a path: **deliberately neutered** 2026-08-29 22:37 after it hard-reset queued worktrees and dropped implementation commits. The body now only sleeps, so bd-watchdog.sh's revive is a no-op. |

Checked and NOT broken (verified false positives of a naive path scan):

- `/home/mboyle/bd.git` in bd-band-remote.sh, bd-freshhost-50.sh, bd-vm-bringup.sh -- a bare repo on the **remote** host, created by the script itself.
- `/home/mboyle/.config/bd/DEPLOY_HOLD` in bd-fleet-deploy.sh and bd-finish-13xx -- a sentinel whose ABSENCE means "not held".
- `/home/mboyle/.bd-ci-slots` in bd-ci-slot.sh -- `mkdir -p`'d on first use.
- `/home/mboyle/.bd-session-jar.txt` in bd-capture-site-setup.sh -- the remote destination; the local source `/home/mboyle/session cookies.txt` exists (88,721 B).
- `bd-codex-wt/row`, `bd-codex-briefs/row`, `fleet-run-artifacts/2026-08-25/codex-cuts/row` -- all prefixes immediately followed by a shell variable; the parent directories exist.

## The commissioned glob is not the whole harness

`bd-*.sh` + `bd-*.py` misses six extensionless executables in `/home/mboyle` that the tabulated scripts call. This matters because `bd-checkpoint-loop.sh` invokes `$R/bd-checkpoint-write` and a naive "does it exist" scan restricted to the glob would have called that script broken.

| File | mtime | bytes | Called by |
| --- | --- | --- | --- |
| `bd-checkpoint-write` | 2026-08-28 21:26 | 5007 | `bd-checkpoint-loop.sh` (does all the measuring; the loop only decides when) |
| `bd-rebase` | 2026-08-28 21:25 | 5696 | named as the caller in `bd-register-insert.py`'s header |
| `bd-review-ready` | 2026-08-18 14:26 | 8216 | -- |
| `bd-rpy` | 2026-08-29 03:31 | 2531 | -- |
| `bd-watch` | 2026-08-14 13:37 | 2154 | -- |
| `bd-hunt-snapshot` | 2026-08-14 04:01 | 1359 | -- |

Also present in `/home/mboyle` and NOT counted anywhere above: `bd-codex-cut.sh.bak-195319`, `bd-fleet-deploy.sh.bak`, `bd-row-chain.sh.preoverlap`, `bd-ship.sh.preoverlap` -- backup copies, not harness members.

`/home/mboyle/bd-persist/harness/` additionally holds `bd-mission-check.py`, `bd-autorebase.sh.ORIGINAL-prewent-neuter` and `bd-band-remote.sh.ORIGINAL-pre-worktree`, which have no `~` counterpart.

## Live evidence at snapshot time

Three harness processes running, all executing the `bd-persist/harness` **mirror** copy rather than the `~` original:

```
1303697  Aug 30 12:55  bash /home/mboyle/bd-persist/harness/bd-worker-dashboard-v2.sh
1389767  Aug 30 13:57  /bin/bash /home/mboyle/bd-persist/harness/bd-progress.sh
1903918  Aug 30 17:36  bash /home/mboyle/bd-persist/harness/bd-worker-dashboard-v2.sh
```

tmux sessions: `0` (codex), `bd-workers` (pane pid 1903918 = the dashboard above), `claude-integrator`.
