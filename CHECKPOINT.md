# CHECKPOINT 2026-08-27 23:12Z

## State
main = v3.66.1302. **24 rows merged tonight** (v3.66.1282 -> v3.66.1302).
Fleet: all six non-integrator hosts verified SERVING v3.66.1302, health 200.
test5 is the integrator and is NEVER deployed (structural: `local` marker in
~/.config/bd/hosts).

## In flight (self-driving, no action needed)
- **v1303 `243 322`** -- PR #572, CI running. `bd-finish-1303.sh` merges on green
  then redeploys the fleet. Verdict was precut=0 prepush=0 band=0, 2457 passed.
- **codex row 292** -- amending its own gate: replacing the frozen-census
  assertion with the ratchet-plus-margin the same file already uses twice.
- **codex row 323** -- building the census-pin expiry declaration.

## Armed watchers (all self-healing; do not duplicate)
- `bd-finish-1303.sh` merge-on-green + fleet deploy
- `bd-watch-stalls.sh` re-dispatches any codex build finishing with ZERO changed
  paths (excluding venv/frontend/node_modules scaffolding), max 2 tries
- `bd-heartbeat.sh` 10-min status; `bd-night.sh` driver
- Monitors: row phases, gate alarm, ladder, contention

## THE RECIPE -- do this BEFORE dispatching any built row to the drain
Four cuts in a row passed first time once I started doing this. Skipping it cost
3-4 failed integrate attempts per row.
1. `bash /home/mboyle/bd-rebase-all.sh <row>` -- worktrees go stale every merge
2. **Register row**: a NEW row has none, and `bd-register-close` refuses
   ("row N not in the register"). Add `| N |  OPEN | ... |` after the last row
   line, then re-derive the header with
   `project-knowledge/build_current_overlay.py::derive_backlog` and assert it
   survives re-derivation.
3. **Changelog summary**: the builder scans the report tail for `- ` bullets, and
   a raw diff dump matches that on EVERY removed line. Replace
   `codex-cuts/row<N>.txt` with a clean summary; keep the full transcript as
   `row<N>.partial-20260827.txt`.
4. Run the row's own tests. Then unpark it in `/home/mboyle/bd-night-spec.txt`.

## Standing rulings
- One line per merged row; no interim summaries. NEVER send a push notification.
- Codex workers write ONLY in their own worktree. One integrator, one writer.
- Rows 243/245 stay OPEN in the register even when their cut merges
  (KEEP_OPEN_ROWS -- the integrator does this automatically).
- Do NOT touch BUNDLE (the bundle-<sha>.tar.gz in ~/).
- Briefs MUST carry the "IMPLEMENT IT. DO NOT STOP TO ASK." block, or workers
  end at a design proposal and produce nothing.

## Known-open, not defects
- **row 292's census** expires whenever any row adds a test file. Its amendment
  is being built. Until it lands, 292 can only merge as the LAST cut, alone,
  with its review re-run immediately before.
- Contention alarm fires on load>15 with a band + codex worker. It has been
  BENIGN every time; only the timing-test-by-name half is predictive.

## Measured facts worth keeping
- precut is ~373s, stable; the mutation gate alone is ~278s of it (measured
  directly). My earlier "316s = 85%" came from a 17-file BATCH summary line and
  was the wrong denominator.
- Timing tests `_1187_`/`_1190_` failed at load 11.3 pre-fix; green at load 29
  post-fix in the v1303 band. That is evidence the fix is real, not luck.

## UPDATE 23:15Z -- row 243 is parked on a REAL CI-only defect
PR #572 CLOSED, remote branch deleted, cut worktree removed. Row 322 re-cut
ALONE as v1304.

Row 243's bootstrap refuses a pytest launch when registration fails -- correct
fail-closed instinct -- but `bd-jobs` is NOT reachable in CI, so every bd-mutate
baseline execution refused there:

    BD-MUTATE-UNRUNNABLE: baseline execution is unproved
    (RuntimeError: owned pytest launch refused: bd-jobs is not reachable)

5 failures in `gate-suites (toolchain-verifiers)`. It passed the LOCAL band
(2457 passed, all four timing tests green at load 29) because test5 HAS bd-jobs.
Same shape as the three blockers fixed at the start of the night: validated in
the one environment where its precondition happens to hold.

**Its next brief must ask for:** the fail-closed property preserved WITHOUT
requiring a registrar that does not exist in every environment. An absent
registrar is not the same as a failed registration -- one is UNKNOWN-but-
expected, the other is a refusal. Do not let it simply skip registration when
bd-jobs is missing; that would silently restore the unattributed launch the row
exists to prevent.

## UPDATE 2026-08-27 23:45Z -- post-compact session

STATE: 24 rows merged, main v3.66.1302, six non-integrator hosts serving it.
test5 never deployed.

FIXED ON RESUME
- The armed finisher watched v1304; the re-cut actually took v1303. Killed it,
  armed bd-finish-1303.sh, confirmed BY PID. `pgrep -f`/`awk` self-matched my
  own shell TWICE more this session -- once falsely reporting the finisher
  already running, once killing the wrong PID. Always confirm by exact PID.

ROW 292 -- BUILD REJECTED, REBUILT BRIEF, RE-DISPATCHED 23:25Z
  The worker folded a parallel-lane expansion into the census cut: allowlist
  1253 -> 1418 (165 added, 0 removed) and SERIAL_EXACT_BASENAMES cut from six
  names to one, promoting test_perf_lab.py and test_dev_suite_tier1b.py, whose
  own comment forbids promotion on a green run. It then pinned a digest
  asserting "reviewed at v3.66.1297" over that larger set -- REPRODUCING ROW
  292'S OWN DEFECT one layer up (A7). Partly my fault: the old brief said to
  start from patches/row292-completed-review.patch, which already contained the
  expansion. New brief forbids touching capture_lanes.py or the allowlist and
  requires every constant derived from main's tree.
  Rejected attempt preserved: patches/row292-attempt-with-lane-expansion.patch
  The lane expansion needs its OWN row with per-file measured refutation.

ROW 322 (v1303) -- BAND RED, MERGE HELD PENDING CAUSATION
  2 failures in test_v3_66_1132_the_hunt_reaps_registration_lifecycle.py, a
  file the cut does not touch. Pass serially (4 passed). The only shared symbol
  whose behaviour changed is _w1_wait_for_path, and only on the content!=None
  path those tests never use. That family has failed in 27 logs this run and a
  matched experiment on 2026-08-25 (inflight/w1-matched/summary.txt) had the
  CONTROL failing too. A serial green does not retire a schedule-sensitive
  failure (A5), so bd-w1-control-1303.sh runs the identical band list on clean
  origin/main, -n 24 --dist loadfile, 3 rounds, after the lane goes quiet.
  bd-finish-1303.sh IS DELIBERATELY STOPPED so a green attempt-2 cannot merge
  on a single sample. RE-ARM IT only after the control reports.

ROW 243 -- ROOT-CAUSED, BRIEF WRITTEN, NOT YET DISPATCHED
  Not "CI lacks bd-jobs" -- toolchain/bin/bd-jobs is TRACKED. The launcher's
  4th candidate sweeps the INTERPRETER's parents; on test5 venv/bin/python
  lives inside the repo so it always resolves. CI uses actions/setup-python
  (tool-cache interpreter, no repo venv) and the verifier shard drives bd-mutate
  against synthetic tmp git trees, so candidates 1-3 miss too. The test5 green
  was never evidence. Brief:
  bd-codex-briefs/row243-registration-resolves-deterministically.md
  HOLD DISPATCH until the lane is quiet -- 292 and 323 are already running and
  load hit 16 with a band up.

ROW 323 -- still building (cx-row323, started 22:13Z).

## MATCHED EXPERIMENT VERDICT 2026-08-28 -- ROW 322 EXONERATED
Identical 55-file band, -n 24 --dist loadfile, alternating arms, matched load
(~4.5-6, both codex workers up in each arm). Log: inflight/w1-control-1303/.
  CONTROL (clean origin/main a1bc7b1):  RED(2) RED(1) GREEN  -- 2 of 3 red
  CANDIDATE (cut ace7e45, row 322):     GREEN RED(1) GREEN   -- 1 of 3 red
Every failure was a DIFFERENT test inside test_v3_66_1132_the_hunt_reaps_
{registration_lifecycle,what_it_abandons}.py. Clean main fails this family at a
HIGHER rate than the cut. Row 322 did not cause it; the band reds were the
family's own schedule sensitivity. Unparked 322 on this evidence.
SEPARATE DEFECT, NEEDS ITS OWN ROW: that two-file family fails ~2 of 3 runs on
clean main under -n 24. Every band that includes it is a coin flip, which is why
322 burned 3 attempts. This is a real reliability defect, not a 322 problem.

## SESSION CLOSE 2026-08-28 ~03:00Z
main v3.66.1307 (6c90b28a). 30 rows merged this run.
FLEET DELIBERATELY HELD AT v3.66.1303 -- the operator is testing the six hosts
by hand. /home/mboyle/.config/bd/DEPLOY_HOLD makes bd-fleet-deploy.sh exit 4,
and the bd-finish-13NN.sh finishers are hold-aware. REMOVE THAT FILE to resume
deploying; nothing else needs changing.

MERGED THIS SEGMENT
  v1303  292 (census pins the allowlist, not the tree) + 322 (wait for content)
  v1304  323 (census pins declare their expiry)
  v1305  326 (frontend security floor runs in CI and compares floors)
  v1306  324 (serial capture pins re-derived from measurement)
  v1307  243 (owned pytest launches register) -- row STAYS OPEN by KEEP_OPEN_ROWS

MATCHED EXPERIMENT, v1307 / test_v3_66_1190 descendant kill:
  control (clean main 0d53fd2) GREEN GREEN GREEN
  candidate (f5dfab5)          GREEN GREEN GREEN
  Attempt 1's band red was an ordinary flake, not row 243's process-tree change.
  Log: inflight/w1-control-1307/.

TWO PROCESS LESSONS WORTH KEEPING
1. KILLING bd-finish-13NN.sh DOES NOT HOLD A MERGE. The drain's own ship step
   merges on green CI. I claimed v1307 was held; it merged four minutes later.
   To actually hold a merge, stop the drain/row-chain, not just the finisher.
2. MATCH ps PATTERNS TO HOW THE PROCESS WAS LAUNCHED. bd-finish-1307.sh was
   running as a RELATIVE path, so an absolute-path awk matcher reported "none"
   while it was alive. Combined with `pgrep -f` self-matching the calling
   shell, wrong-process errors cost four separate mistakes this session.

STILL FILED, NOT BUILT
  325 the hunt reap family is a coin flip under -n 24 (operator killed the build)

## OVERNIGHT PLAN 2026-08-28 03:40Z -- operator asleep, full autonomy granted
RULINGS THIS SESSION (all from the operator, before sleeping):
 * Judgement calls: BUILD MY BEST JUDGEMENT AND SHIP IT, stating the assumption
   in the row and changelog. Do not stop to ask.
 * A fleet host that fails deploy and does not self-heal: RECORD IT AND CONTINUE
   with the other hosts. Never notify.
 * Stopping point: KEEP HUNTING UNTIL THE OPERATOR IS BACK.
 * Merge and deploy on green, as before.
 * Full suites, capture.sh and any tool may be run; use fleet resources.
 * Splitting slow test files for parallelism is explicitly sanctioned.
HOST ROLES (/home/mboyle/.config/bd/roles):
   runners (load freely): test2 test3 test6
   deploy targets (keep clean): test test4 test7
   test5: INTEGRATOR ONLY -- never run heavy suites here, a band must not
   compete with a capture on the writer's own tree.
QUEUE: 325 hunt-family, 327 re-pin (cutting), 328 defect-scan vanishing entry,
       329 T3T4 budget, 330 probe-fixtures leak, 331 guarded imports declared,
       332 capture critical path.
THEN: bug hunt via codex over four operator-chosen areas -- fail-open/UNKNOWN,
       install/deploy/bring-up, concurrency and isolation, budgets and timeouts.
POOL: bd-codex-pool.sh holds workers at MAXW (default 3) and pauses while a band
       runs. Queue file night/codex-pool-queue.txt, lines "<row> <brief>".

## ROW 332 TARGET, agreed with the operator 2026-08-28 03:45Z
COMMITTED TARGET: capture wall time 30 min -> 12 min, achieved by SPLITTING the
slow files, with no assertion relaxed and no denominator shrunk.
Arithmetic behind it: --dist loadfile pins a whole file to one worker, so
today's floor is 494.7s (test_v3_66_1046_gates_for_this_sessions_shapes) with
300.3s and 249.5s behind it. Split the top five so no file exceeds ~120s and the
critical path stops binding; wall then falls back to total work / workers.
BELOW 12 MIN IS NOT PROMISED. Nobody has measured total CPU-seconds across the
18,439 tests -- 02_SUMMARY.txt only records tests over 30s. Row 332's FIRST step
is to measure total CPU-seconds against wall time and report the real floor. If
the total is ~4 CPU-hours, 5 minutes is impossible at any worker count on one
box, and saying so is the honest result. Do not quote a floor nobody measured.

## CONTENTION DECISION 2026-08-28 03:45Z
Six codex workers plus a band put load at 24.6. Deliberately NOT killing workers
to protect the band: each worker is ~1-2h of real work and the band is ~5 min,
so retrying a contended band costs far less than discarding a build. The
matched-control discipline (inflight/w1-control-*) is what separates a real
failure from a contended one, and it has been right twice tonight -- row 322
exonerated, row 243 exonerated. Retry the band; do not trust one red sample.

## ROW 332 STRETCH GOAL, operator 2026-08-28 03:47Z
After 12 minutes is reached, PUSH FOR 6-8 MINUTES -- but "don't weaken the tests
at all" is absolute and outranks the number. No skipped test, no shrunk census,
no relaxed assertion, no xfail, no narrowed denominator buys time. If the
measured total CPU-seconds make 6-8 impossible on the available workers, REPORT
THAT AS THE ANSWER rather than trimming the suite to hit a target. A faster
capture that measures less is a regression.

## FINAL RULINGS 2026-08-28 04:00Z, operator asleep
 * REGRESSION IN A MERGED CUT: FIX FORWARD with a corrective row, as row 327 did
   for row 324. Never revert or rewrite main. The reasoning stays on record.
 * ROW 332: MERGE ANY REAL MEASURED IMPROVEMENT rather than sitting on a partial
   win, report the number honestly, and file a follow-up row for the remaining
   gap. The no-weakening rule still outranks the target.

## CAPTURE FLOOR MEASURED 2026-08-28 06:25Z (row 333)
The capture PARALLEL lane's arithmetic floor -- total work / workers -- is
210.332s (~3.5 min). After rows 332 and 333 the largest file is 199.743s and
the next is 159.214s, BOTH BELOW that floor, so further file splitting cannot
improve the parallel lane. IT IS DONE. Any capture wall time above ~3.5 minutes
is the SERIAL lane plus capture's own non-pytest steps, and the honest next
move is to MEASURE that split before splitting anything else. The operator's
12-minute target is reachable; 6-8 depends entirely on the serial half, which
nobody has measured yet. Do not quote a wall-time figure that has not been
observed from a real capture run.

## LANE OUTAGE 2026-08-28 07:22Z -> 10:49Z (~3.5 hours lost)
bd-night.sh died when the previous Claude Code process exited, mid-verify on
row 333 (v1315, tag 1315-879fe02-a1). No BAND_RC was ever written, so the verify
was killed in flight rather than failing. The 30-minute progress heartbeat was
orphaned in the same event and reported nothing for the whole window.
NOTHING WAS LOST OR CORRUPTED -- main stayed at v3.66.1314 and every worktree
survived -- but three and a half hours of lane time produced no cuts.
FIX APPLIED: driver restarted, 333 attempts cleared, heartbeat re-armed.
WHAT WOULD HAVE CAUGHT IT SOONER: the heartbeat itself died with the lane, so
its silence WAS the signal and nothing read it. A heartbeat that dies with the
thing it watches reports health by being quiet -- the same fail-open shape this
run has been fixing all night. A watchdog that restarts the driver, or a
heartbeat hosted outside the session, is the real fix.

## HOST PLAN 2026-08-28 11:35Z (operator)
 * 10.0.70.79 -- small VM, codex/claude only, SIGNIFICANTLY FEWER RESOURCES.
   Use as a HUNT-ONLY host: source analysis, call-path tracing, CI-membership
   checks. Needs a git clone plus a venv, because a hunt that cannot execute
   anything degrades into the source-level speculation this run keeps
   correcting. NEVER a deploy target, never in the band path, never a
   fix-build host -- mutation batteries and band checks would crawl there and
   an unverifiable build is discarded.
   Operator runs the authorized_keys one-liner; setup follows.
 * A SEPARATE VM LATER for a real fresh-host provisioning test, deliberately
   AFTER the current cuts land. Rows 341 and 343 fix the bring-up path itself
   (cloud-setup claims READY without the GUI-parity artifact; bd-provision
   returns READY without building the SPA; setup builds the venv before
   installing python3.12-venv; the runbook restores archives it never
   transfers). Provisioning against the UNFIXED scripts would only re-find what
   hunt/install.md already proved -- the test is only meaningful on the fixed
   tree.

## SIX CAPTURE FAILURES: BOUNDARY CONFIRMED 2026-08-28 15:06Z
Full captures on test6, same host, same command:
  v1306 (0d53fd2c)  0 failed
  v1307 (6c90b28a)  8 failed  <- boundary; all six plus two since fixed
  v1314 (dcd8201d)  6 failed
  v1317 (4c80a01f)  6 failed  (identical names to v1314)
THE WINDOW IS ONE CUT: row 243 at v3.66.1307, the registration bootstrap that
made owned pytest launches register with bd-jobs before the pytest import.
Three of the six failures are in bd_jobs/remote_job_registry.
The two extra failures at v1307 were test_v3_66_729_body_contract_fixtures,
which rows 327 and 330 have since fixed -- independent corroboration that these
captures measure real state rather than noise.
WHY THE LANE MISSED IT: row 243's band was green, its CI was green over 26
checks, and its focused gate was 38 passed. The defect is only reachable at
capture width. TWO NARROWER INSTRUMENTS ALSO MISSED IT -- a 4-file matched run
and a 6-version bisect, both 482-passed everywhere. Row 351 carries the fix and
its brief now names the confirmed boundary instead of a suspect.

## COMPACT CHECKPOINT 2026-08-28T17:19Z
MAIN: 3.66.1324 (1e549c2). Fleet: test/test4/test7 are deploy
targets, test2/test3/test6 runners, test5 integrator. 29 rows merged tonight.

STILL TO CUT (all built, verified, register rows FILED, freshcheck green):
  339 measured bounds, reconciled with 335's UNKNOWN contract
  344 capture prune proves its target before deleting
  347 provider band gate catches its own no-op mutant
  349 three shared caches bound to identity (IN VERIFY as v1325)
243 stays OPEN by KEEP_OPEN_ROWS; its code shipped at v1307.

FILED, NOT BUILT: 353 (row 348's M4 mutant anchors on an ABSOLUTE gate count and
rots on every neighbouring cut -- it broke on 341, 348 and 349 in succession).
WITHDRAWN: 352, on my own misreading; row 349 does fix the bd-venv cache.

THE BIG RESULT OF THE AFTERNOON: rows 351 and the capture campaign.
Six capture failures appeared at v1307 and persisted to v1317. Full captures on
test6 placed the boundary exactly: v1306 clean, v1307 eight failed, v1314 and
v1317 six failed with identical names. Cause: row 243's gate assigned
`module.time.sleep = lambda _seconds: None` on the SHARED time module and never
restored it, so every later test in that xdist worker ran with a no-op sleep --
which is why neighbours' timing FLOORS failed (`assert 0.03 <= 0.0065`), not
timeouts. Fixed at v3.66.1324 by scoping it with monkeypatch.context() and
asserting restoration. 416 passed with row 243's gate and both previously
failing neighbour files in one process.
TWO NARROWER INSTRUMENTS MISSED IT -- a 4-file matched run and a 6-version
bisect, both 482-passed everywhere. Only a full capture reproduces it.

OUTSTANDING VERIFICATION, DO THIS ONCE THE LANE DRAINS:
Run a full capture on test6 against current main and confirm the six failures
are gone. That is the ONLY instrument that ever showed them, so nothing else
closes the loop. Script pattern: bd-t6-capture-main.sh.

CAPTURE SPEED: parallel lane is DONE at a measured 210.332s arithmetic floor
(rows 332/333). Remaining wall time is the SERIAL lane plus non-pytest steps and
is UNMEASURED -- that is the honest next step toward the operator's 12-minute
target, not more splitting.

RECURRING TRAP THAT COST THE MOST TIME TODAY: rebuilding a codex worktree from
main DROPS the register row filed in the old one, and copying a whole file from
a stale worktree silently REVERTS merged work (it reverted row 338's measured
60s bound; the tracked mutation anchor caught it). File the register row AFTER
any rebuild, and rebase by applying per-file patches, never by copying files.

## THE HARNESS BUILT TONIGHT -- READ THIS BEFORE WRITING ANY NEW TOOL
Every script below exists, works, and has been exercised for hours. Do not
rebuild them from memory after a compact; that urge has cost this project 90
minutes and eight rediscovered defects before.

RUNNING CONTINUOUSLY (the watchdog restarts the first two if they die):
  bd-night.sh          the drain: batches, integrates, verifies, ships, merges.
  bd-codex-pool.sh     bounded codex worker pool. Cap is READ FROM A FILE every
                       pass (night/pool-max) so it can be tuned WITHOUT a
                       restart -- a baked constant cannot change mid-run because
                       bash runs the loop from memory. Allows 2 workers
                       alongside a band, night/pool-max when the lane is quiet.
                       Queue: night/codex-pool-queue.txt, lines "<row> <brief>".
  bd-watchdog.sh       restarts bd-night.sh or bd-codex-pool.sh if either
                       vanishes. Deliberately SEPARATE from what it guards,
                       because on 2026-08-28 the lane and its heartbeat died in
                       the same event and the silence read as health.
  bd-att-guard.sh      clears att-<row> when a row's ONLY refusal was
                       "ALREADY CLAIMED" -- a version collision is the serial
                       pipeline working, not a failure, and three of them used
                       to cap a good row out for the night.
  bd-progress.sh       one status line every PROGRESS_INTERVAL seconds (600).

DEPLOY SAFETY:
  bd-fleet-deploy.sh   now ROLE-AWARE: skips hosts marked `runner` in
                       ~/.config/bd/roles unless BD_DEPLOY_ALL=1, so a deploy
                       cannot restart a service under a running capture. It
                       FAILS OPEN by design -- a missing roles file deploys
                       everywhere, because the guard may narrow the target set
                       only when it can actually read the ruling.
                       ~/.config/bd/DEPLOY_HOLD makes it exit 4 entirely.

CAPTURE AND EXPERIMENT SCRIPTS (all write to inflight/<name>/summary.txt):
  bd-t6-capture-main.sh    full capture on test6 at current main.
  bd-t6-capture-1307.sh    same, pinned to a specific version. Copy and edit the
                           rev-list --grep to pin another version; it returns
                           the host to main afterwards, which matters or the
                           next reader thinks the fleet is behind.
  bd-w1-control-1303.sh    matched control: identical band list, alternating
                           arms, re-checks the lane is quiet BEFORE EVERY ROUND
                           and says what it saw.
  bd-t6-matched.sh, bd-bisect-1314.sh
                           KEPT AS EVIDENCE OF A MISTAKE. Both ran only the
                           failing files and passed 482/482 on every arm and
                           every version, proving nothing, because the defect
                           needed full capture co-residency. If a narrow
                           instrument comes back green on both arms, that is a
                           reason to widen it, not to conclude anything.

TUNING FILES, read live, no restart needed:
  night/pool-max      codex workers when the lane is quiet
  night/batch-cap     max rows per cut (bd-batch-rows.py honours it)
  ~/.config/bd/roles  host roles (runner / deploy / integrator)

THE INTEGRATION RECIPE THAT ACTUALLY WORKS, per row:
  1. verify the worker's own focused test passes
  2. check `git status` for a register row -- workers usually DO NOT file one
  3. file the register row (header rows/open/ids-sha256 recomputed)
  4. bd-freshcheck --repo-only
  5. uncomment the spec line, rm night/att-<row>
  6. let the drain cut it; fix anchors and gate counts as they surface
REBASE BY PER-FILE PATCH (git apply --3way), NEVER by copying whole files, and
file the register row AFTER any rebuild -- both rules were learned the hard way
tonight and each cost multiple failed cuts.

## WORKTREE REBASE STATE (in-head knowledge, written down before compaction)
REBASED ONTO MAIN BY THE INTEGRATOR, do not treat their bases as the worker's:
  row339  rebased; also reconciled with row 335 (its stub returned 0 for an
          UNMEASURED run and omitted summary_parsed -- the old fail-open shape
          inside the test that forbids it). Register row FILED.
  row340  rebased twice; register row re-filed AFTER the rebuild. Baseline line
          for test_deploy_script.py removed because the row declares its scope.
  row341  rebased; reconciled with row 343 (fixture now provides the gui-parity
          artifact and delegates the read-back to a real interpreter) and with
          row 338 (its measured 60s bound was restored after a wholesale file
          copy reverted it). Register row re-filed.
  row348  rebased; _EXPECTED_CONFIRMED_SAFETY_GATE_COUNT restored from the real
          set after a conflict resolution dropped its definition. Register row
          FILED.
  row349  rebased; bd-venv change RECOVERED from the previous cut worktree after
          my per-file patch list omitted it. All three caches fixed. Register
          row FILED. Row 348's M4 anchor re-seated for this cut's count.
NOT REBASED, base is whatever the worker used: row344, row347, row351.
EVERY row above has its register row filed and freshcheck green.

## SAFETY AUDIT OF THE RULES I AM RUNNING ON (written 2026-08-28, unprompted)
Recorded because the operator asked whether the standing authority is safe, and
because a rule that has been disproved should not keep authorising action.

1. DEPLOY-ON-GREEN IS RUNNING ON A PREMISE TONIGHT DISPROVED TWICE.
   Row 243 shipped with a green band, green exact-head CI over 26 checks and a
   38-passing focused gate, and introduced six capture failures that sat on main
   from v1307 to v1324. Row 324 did the same with a lane promotion refuted by
   its own release's capture. The gate that catches this class is a FULL
   CAPTURE, which the lane never runs. RECOMMENDED CHANGE: block fleet deploys
   while the newest capture on main is red. This is the one change that closes
   the hole actually demonstrated.
2. THE INTEGRATOR EDITS MERGED ROWS' ASSERTIONS WITH NO REVIEWER.
   Under "build your best judgement and ship it" I changed row 335's pinned
   timeout to 149, rewrote row 343's fixture, altered row 348's mutation anchor,
   and changed a stub's return values. Each was defensible and each is recorded
   -- but every one touched a gate another row shipped, and nothing independent
   distinguishes a good reconciliation from a quietly weakened test. This is the
   highest-variance authority currently held.
3. NEVER-NOTIFY PLUS FULL AUTONOMY MAKES SILENCE AMBIGUOUS.
   The watchdog covers process death. It does not cover a wrong-but-green
   outcome, which is exactly the failure mode this run keeps finding.
4. ONE FAIL-OPEN IS THE INTEGRATOR'S OWN.
   bd-fleet-deploy.sh deploys EVERYWHERE when ~/.config/bd/roles is unreadable,
   on the reasoning that a guard may narrow the target set only when it can read
   the ruling. Defensible, deliberate, and still a fail-open written on a night
   spent removing them.
WHAT IS GENUINELY SAFE: test5 is never a deploy target; nothing destructive runs
without proven identity; captures run only on runner hosts; fix-forward means
main's history is never rewritten; every decision is here rather than in
context.

## OWED VERIFICATION -- CLOSED 2026-08-28 18:40 UTC

Full capture on test6 (10.0.70.249) against origin/main 4b538aae, v3.66.1325,
bundle bd_capture-20260828T174211Z-4b538aa-693672.tar.gz:

  18607 total | 18586 passed | 0 failed | 0 errors | 21 skipped
  --- failing tests --- (EMPTY)
  BUDGET: >30.0s = 14

The six capture failures that appeared between v3.66.1306 and v3.66.1314 --
root-caused to row 243's gate assigning a no-op onto the SHARED time module and
never restoring it, fixed at v3.66.1324 -- are GONE. A full capture is the only
instrument that ever reproduced them, so this closes the loop. Two narrower
instruments (a 4-file matched run, a 6-version bisect) returned 482/482 on both
arms and proved nothing; that mistake is recorded above and stands.

Wall time 17:42:11 -> 18:40:16 = 58 min on test6 (parallel lane ~840s, serial
lane ~1560s+, then the live suite). The 13:00 run was 55 min. Capture wall time
is NOT near the 210.332s parallel-lane arithmetic floor on that host; the serial
lane and the non-pytest steps are where the time is, and both remain unmeasured
in detail. That is the honest path to the operator's 12 -> 6-8 minute target.

CAVEAT, FILED AS ROW 354: the headline verdict still reads
"CAPTURE VERDICT: FAIL - graph exit=1". That exit is capture.sh's graph
content-hash check against /var/lib/bulkdownloader/validation/
KNOWLEDGE_GRAPH.content.sha256 -- a DEPLOYMENT-LOCAL pin written only by
scripts/deploy.sh, last written on test6 at 10:31 TODAY -- test6 was pinned
this morning and has not been deployed since the roles ruling made it a
reserved runner. A runner's pin therefore only drifts further, and the
verdict reads FAIL there from then on no matter what the tests do. Do not read a runner's
capture VERDICT line as a test result; read the summary counts. Any policy keyed
on "newest capture on main is green" -- including the deploy hold recommended in
the SAFETY AUDIT above -- would block forever on this.

## 18:58Z -- THE TIMING FLAKE IS NOW THE BOTTLENECK (row 355)

Row 353 (v1326) has failed verify twice on the SAME family, never on its own
subject:
  attempt 1 (2f9e4ff), load [2.04,3.3,3.52] -> [8.65,9.68,6.3]:
    test_v3_66_1187 :: test_an_execution_timeout_is_UNKNOWN_exit_2_and_leaves_no_junit
  attempt 2 (b49ae26), load [1.94,5.06,5.33] -> [9.42,11.66,8.24]:
    test_v3_66_1187 :: test_a_collection_timeout_is_UNKNOWN_exit_2_and_restores_the_subject
    test_v3_66_1190 :: test_timeout_kills_descendants_before_restoring_subject[collection]
Both times PRE-PUSH was OK and the mutant-anchor rot 353 fixes was gone, so the
cut itself is sound. The same file passes 2/2 serially on the same tree.

I dispatched codex rows 354 and 355 at 18:54/18:55, INTO the verify window, and
attempt 2 ran at a higher load than attempt 1. I have set night/pool-max to 0 so
no further worker is dispatched while a verify is in flight. Restore it to 4
once the lane drains.

BE HONEST ABOUT WHAT THAT IS. Lowering the load is not a fix and must not be
recorded as one: it removes an interference source I introduced, nothing more.
The real defect is row 355 -- a bare `--timeout 1` racing interpreter start-up,
and a zero-row result that dies on ["rows"][0] with a bare IndexError naming
neither the timeout nor the load. Until 355 lands, every wide band on a busy box
can lose ~11 minutes to it, and a green retry is not evidence the tree changed.
The checkpoint's earlier note (green at load 29 post-v1303) is therefore NOT the
whole mechanism; v1303's fix was partial.

## 19:15Z -- MATCHED CONTROL: THE FLAKE IS ON MAIN, NOT IN ROW 353

I twice guessed at this and was wrong both times. The measurement settles it.

WRONG GUESS 1 (18:58): "load". Attempt 3 failed at load 3.30 after I dropped
pool-max to 0. Load is not the discriminator.
WRONG GUESS 2 (19:08): "3-for-3 on a cut that changes bd-mutate means row 353
regressed it". Also wrong.

THE CONTROL. A fresh detached worktree at origin/main 4b538aa -- no row 353
content of any kind -- ran the EXACT 103-file band from the failing candidate
(the candidate's list minus tests/test_row353_*, which does not exist on main),
same shape: -n 24 --dist loadfile --timeout=240 --timeout-method=signal
--max-worker-restart=0 -p no:randomly.

  CONTROL (origin/main):   2 failed, 1939 passed, 1 skipped in 231.53s
    test_v3_66_1187 :: test_a_collection_timeout_is_UNKNOWN_exit_2_and_restores_the_subject
    test_v3_66_1187 :: test_an_execution_timeout_is_UNKNOWN_exit_2_and_leaves_no_junit

Same two tests, on main, with the candidate absent. Row 353 is exonerated.

WIDTH IS THE DISCRIMINATOR, not load and not the cut. The same two files run
ALONE pass 5/5 at BOTH -n 1 (25.95s) and -n 24 (21.49s). They only fail inside
the 103-file, ~1939-test band. That is the same width lesson as row 327 and the
capture six -- the third time this run that a narrow instrument would have
returned green and proved nothing.

THE MECHANISM, from the band log's own tracebacks:
  1187: `assert run.returncode == 2` PASSES -- bd-mutate does time out -- and
        then `json.loads(...)["rows"][0]` raises IndexError because the timed-out
        run emitted ZERO rows.
  1190: FileNotFoundError on .../test_timeout_kills_descendants1/descendant.pid
        -- the child was killed before it could write the pid the assertion reads.
Both are one cause: a bare `--timeout 1` that, under 24 workers of contention,
expires BEFORE the work being measured begins, so the evidence each assertion
needs is never created. The tests are not wrong about the contract; their budget
races interpreter start-up.

CONSEQUENCE FOR THE QUEUE. This blocks any cut whose band includes these files,
which is why row 353 failed three times without ever failing on its own subject.
Order is therefore 355 -> 353 -> 339/344/347. Row 353 is PAUSED in the spec
until 355 lands. Codex is building 355 but is touching only the 1187 file; 1190
fails identically and must be covered in the same cut or the band still refuses.

## 21:10Z -- LANE CHANGES, FIVE NEW VMs, AND TWO CORRECTIONS

MERGED SINCE THE LAST ENTRY: v3.66.1326 (rows 353+355, batched deliberately --
353 fixes the mutant-anchor rot that broke 355's cut, 355 fixes the band flake
that broke 353's, so neither could land alone) and v3.66.1327 (rows 339, 344,
347, 354 in one cut).

THE LANE NOW OVERLAPS THE BAND AND CI. bd-ship.sh gained BD_SHIP_PHASE:
  push  -- push, open the PR, exit 0 with CI running (takes NO merge-lane lock)
  merge -- resume that PR, poll exact-head CI, merge (takes the lock)
  all   -- unchanged default, so nothing that calls it plainly is affected
bd-row-chain.sh pushes BEFORE the band and runs them concurrently, then merges
only when BOTH are green. Measured phases that motivated it: QA ~2min, integrate
~1.5, precut ~172s, band ~3.6min, CI ~10min. CI is over half the lane and is the
only phase I cannot shorten, so overlapping it is the whole win: ~25min -> ~15.
NOTHING IS RELAXED. The merge still requires the band's ALL GREEN verdict AND
exact-head CI over a nonzero denominator, both judging the same frozen SHA. The
new cost is that a band-rejected candidate burns a CI run -- and _abandon() then
CLOSES the PR and DELETES the remote branch, because a green unmerged PR blocks
every retry of that version. Rollback: mv bd-{ship,row-chain}.sh.preoverlap back.

ALSO ADDED: bd-codex-refill.sh (the pool dispatched but never REFILLED, so it sat
idle with capacity 19:45-19:57) and bd-width-restore.sh (the demotion ladder
dropped 8->1 and never climbed back, taxing every later cut for one unrelated
failure; two consecutive clean merges now double it, ceiling 8). bd-qa-row.sh now
pulls TWO tree-wide gates into a row's own QA when they apply: the declaration
gate for any row adding a test file, and the mutant-anchor gate for any row
editing a file some spec anchors on. Both cost ~90s there and were each costing
an 11-minute precut discovery.

FIVE FRESH VMs (10.0.70.50-54, "bd" and bd1-bd4), 48cpu/344GB each, Ubuntu
24.04, passwordless sudo, operator-declared throwaway. Registered as role
`scratch` in ~/.config/bd/roles so no deploy can target them.

  *** THE DOCUMENTED FRESH-HOST BRING-UP WORKS. *** On bd, from a bare box:
  VERDICT: READY (436s, 0 warnings), first attempt. That is the proof rows 343
  (@v1321, the path was NOT EXECUTABLE) and 341 (@v1322, READY claimed without
  the artifact that defines it) were built for, and it had never been run on a
  fresh host since they merged. ollama + qwen2.5vl:7b installed there too, which
  removes the L17 hard-FAIL that FRESH_HOST_BRINGUP.md step 3b warns about.

  BLOCKED, OPERATOR ACTION: all five refuse test5's key. ~/.ssh/config sets
  IdentitiesOnly yes and offers ONLY bd_agent_ed25519, but the one-liner I gave
  the operator installed id_ed25519.pub -- the `mboyle@test4` key the bring-up
  doc explicitly RETIRES ("must not be reinstated"). My error. The operator has
  been given the sanctioned line:
    from="10.0.70.164",restrict,pty ssh-ed25519 AAAA...  bd-agent-2026-08-13-...
  bd-vm-bringup.sh (provision bd1-bd4 + install codex) and bd-capture-site-setup.sh
  (place the 21 site jars on bd, 0600, outside the repo) are written and gated on
  VERDICT: READY; both re-run once the key lands.

TWO CORRECTIONS I OWE THE RECORD:
1. I proposed unparking row 317 to cut ~4 min from precut. Row 317 is CLOSED
   @1301 and the collection is ALREADY concurrent (ThreadPoolExecutor, cpus//4).
   My number came from a checkpoint note written before 1301. Precut is now
   ~172s, measured over three consecutive runs. The lever was already spent.
2. Two of the three VM chains fired on a STALE completion line: run.log is
   APPENDED across attempts, so an earlier failed run's "BRING-UP COMPLETE"
   satisfied the wait. Re-gated on "VERDICT: READY" in the provision log, which
   only exists when the box really is ready. Monitors arm over history -- again.

ROW 356 FILED AND BUILDING (cookie_quality scores 100/ok over a jar it never
measured -- score_total starts at 100 and every check is guarded by whether it
CAN run; a session-only jar skips freshness entirely. MEASURED against the live
service on test4). ROW 357 FILED AND QUEUED (mutant anchors on re-derived values
rot on every neighbouring cut; two instances fixed by hand today, this converts
the population and adds a gate). Rows 301-306 FORMALLY DROPPED with per-row
CLOSED-BY commits after a read-only re-derivation -- they had been commented out
with no recorded reason, which the contract forbids.

## CHECKPOINT 2026-08-28 21:27Z -- written by bd-checkpoint-write

Written while validating the tool itself.

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1327 at 4d636df07ecd
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 311 CLOSED on origin/main
  queue           32 spec row(s) not yet closed on main
  codex           0 live session(s); 111 worker worktree(s) on disk
  harness         4 bd-*.sh process(es) running
  host            test5 load average: 0.83, 2.25, 4.29

## CHECKPOINT 2026-08-28 21:59Z -- written by bd-checkpoint-write

Session state after the VM bring-up, the overlapped lane, and the authenticated-capture findings (rows 359, 360).

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1328 at 127b6b5bdeeb
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 312 CLOSED on origin/main
  queue           33 spec row(s) not yet closed on main
  codex           2 live session(s); 102 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 7.40, 12.59, 10.15

## 22:40Z -- THE DOWNLOAD CHAIN IS PROVEN END TO END ON ONE VIDEO

Run on bd1 (10.0.70.51) against a live authenticated evilangel session, through
BD's own cloak backend, at main v3.66.1329:

  site title  : 'Indy Lix and Toby Dick'
  tiers learnt: {'low': 160, 'full hd': 1080, '4k': 2160}
  chosen      : 2160p mp4 advertised 3860 MB -- cascade matched 2160p exactly
  delivered   : 3860.3 MB in 62s (62.2 MB/s)
  verify      : OK (delivered/advertised = 1.000)

Every link held: session -> scene page -> affordance discovery -> eight options
enumerated with height/format/size -> cascade pick -> site title -> streamed to
the allowlisted download root -> delivered size matched advertised.

WHAT THIS SETTLES. The committed templates are not merely stale, they were never
measured: on this same page `evilangel`'s two row_selectors and `gamma_kosmos`'s
four download trigger_selectors ALL resolve zero times, and one of them
(`a.download-link[href*='.mp4']`) is MALFORMED and raises rather than missing.
The controls that do exist -- `[class*='ScenePlayerHeaderPlus-IconItem']` and
`a[class*='DownloadOption']` with hrefs `/movieaction/download/<id>/<height>/mp4`
-- appear in no template. gamma_kosmos also declares five login submit_btn
selectors, all of which miss; the real control is `#submit`.

THREE DEFECTS IN MY OWN TOOLING, each caught by measurement and not by review:
 1. HEIGHT COLLAPSE. Labels read "Web HD 540p" and "Full HD 1080p"; a leftmost
    regex match takes the literal "HD" and calls both 1080p, silently fetching
    the wrong file. Fixed: the URL segment wins, then digits in the label, then a
    named tier.
 2. AN INVENTED CEILING MODEL. I ranked by "highest at or below the preference".
    BD's own semantics (runner_extractors.py:917, _apply_quality_preference) are
    an ORDERED CASCADE with best/highest/max sentinels that leaves the pick
    unchanged when nothing matches. A discovery tool that ranks differently from
    the runner recommends files the runner then declines. Fixed to mirror it.
 3. BUFFERING. Playwright's response.body() dies on a 3.8GB file with "Cannot
    create a string longer than 0x1fffffe8 characters". BD's own downloader is
    chunked (chunk_size_mb, parallel_chunks, multi_conn) so the app is fine; the
    script now streams.

RESOLUTION VOCABULARY, per the operator: 8K/5K/4K/UHD/Ultra HD/Full HD/FHD/HD/
SD/2K/QHD are all handled, longest phrase first so "Ultra HD" is not read as
"HD". A scene with no 4K falls to the highest actually offered. Where a page
shows both a word and a real height, the site's OWN vocabulary is learned and
overrides the global convention -- the operator reports sites that label 720p
"SD" where SD conventionally means 480p, and that disagreement must not be
settled by a global guess. When nothing names a resolution, options are ranked by
SIZE; when a tier pick is not also the largest file, it says so.

ROWS FILED FROM THIS WORK: 359 (cloak spoofs Windows without Windows fonts),
360 (Turnstile bypass advertised in every site config, installed on no host --
proven 0/3 before and 3/3 after `pip install scrapling[fetchers]`), 361
(templates are unverified claims), 362 (no way to resolve a template against a
site; codex is attacking my findings adversarially), 363 (GUI: network capture
has NO frontend surface at all despite log_network being a real config field,
and there is no crawler), 364 (history has no column for the site-side title,
which extractors_aylo.py already extracts and discards; plus login-and-forget).

## 23:15Z -- CORRECTION: ADVERSARIAL VERIFICATION REFUTED TWO OF MY CLAIMS

Codex row 362 attacked the template findings recorded above. Two are WRONG:

1. "a.download-link[href*='.mp4'] is MALFORMED and RAISES rather than missing."
   REFUTED. It is valid CSS and resolves in Playwright. The exception came from
   MY OWN JS quoting when interpolating the selector into an eval string. A
   static audit of the whole corpus confirms it: 91 templates, 547/547 selectors,
   ZERO malformed. The selector is still WRONG for the live page (0 matches) --
   but wrong and malformed are different defects and I conflated them, then
   repeated the conflation in rows 361, 362 and this checkpoint.

2. "Match stable substrings, not exact variant classes." REFUTED AS STATED.
   [class*='ScenePlayerHeaderPlus-IconItem'] matches SEVEN controls per page,
   only one being Download -- 85.7% unintended. The icon-qualified compound
   selector matches 1/1. My learner only worked because it filters by visible
   text at RUNTIME; the template it EMITTED carries the unsafe bare substring.
   I committed the exact defect this row exists to describe, while describing it.

Also corrected: the four-trigger miss is an EvilAngel fact, not a family fact --
AdultTime hits on button:has-text('Download'). And the family claim covers 2 of
10 Gamma domains; eight remain unverified.

SURVIVED: the eight-option set is confirmed independently on an archived DFXtra
scene -- same heights 160/240/360/480/540/720/1080/2160, MP4/H264, sizes
monotonic. detect.res_score reads "Web HD 540p" correctly as 540.

NEW DEFECT FOUND BY THE VERIFIER, in BD's own code rather than mine:
aiassist.normalize_resolution reads "Web HD 540p" as 720 and misses 160/240/360
entirely -- the same height-collapse class as the bug I fixed in my harness,
shipping in the product. Filed as row 366.

THE LESSON IS THE PROCESS, NOT THE SELECTOR. Every one of tonight's template
findings came from measurement, and measurement still produced two confident
false claims because the INSTRUMENT was not itself checked. The adversarial pass
cost one codex worker and caught both.

## 00:05Z 2026-08-29 -- CREDENTIALS ARE IN THE VAULT BUT NOT WIRED. TWO OF MY BUGS.

STATE ON bd1 (10.0.70.51), which now holds the operator's real credentials:
  * secrets backend master_password, is_unlocked=true, is_initialized=TRUE,
    plaintext_count=0. The master password COMMITTED on the first write -- a
    fresh vault accepts any password until the first secret lands, and that is
    now fixed to whatever the operator unlocked with.
  * 20 real passwords stored under keys `bulkdl-import-bulkdl-site-<site>-<user>`.
  * 21 sites configured via POST /api/sites with production defaults, download
    dirs created under the allowlisted root, cookie jars attached.
  * BOTH exports and the staged selection were SHREDDED and verified gone;
    BD's own audit reports plaintext_count=0, plaintext_sites=[].

TWO DEFECTS, BOTH MINE, NEITHER RISKING THE CREDENTIALS:
 1. NO USERNAME WAS EVER SET. Every site config has username="". BD said so
    exactly: login_status "✗ Missing credentials: username; manual fallback also
    failed". import_apply stores a secret; it does NOT wire a site for login.
 2. DOUBLE INDIRECTION. I passed password="@cred:bulkdl-import-..." into
    POST /api/sites. BD stored that LITERAL REFERENCE STRING as a new secret
    under `bulkdl-site-<site_id>`, and wrote a reference to THAT into the config.
    So each site now points at a key whose contents are another key's NAME, which
    can never resolve to a password. That is the source of the ~24 extra vault
    entries. The real passwords are intact and unreachable from the configs.

USERNAMES ARE NOT RECOVERABLE FROM THE VAULT. The key names embed the account but
TRUNCATE it -- `ultrafilms-itdude1865-gmail-` and `wowgirls-itdude1865-gmail-co`
are cut mid-domain -- and an email cannot be reversed from `-pm-me` safely. The
exports were shredded, so the operator is re-adding them.

THE FIX, when the files return: parse again; write BOTH username and the password
through the path BD uses itself so exactly one resolvable @cred: reference lands
per site; delete the 24 bogus `bulkdl-site-<sid>` keys ONLY after a login proves
the new wiring works; verify username set, password starts with @cred:, and
plaintext_count still 0; then retry ONE site (naughtyamerica, already dead, so a
bad attempt costs nothing) before widening.

ALREADY CONFIRMED PRESENT IN THE PRODUCT: BD's login flow calls
_try_check_remember_me() at login_impl/submit.py:892, matching "remember",
"keep me", "keep signed", "stay logged", clicking either the native checkbox or
its label. The operator asked for remember-me; it is already implemented and
wired, and it converts session-only cookies to long-lived ones.

THE PATTERN IN MY OWN ERRORS TONIGHT, stated plainly because it is the useful
part: malformed-selector claim (my JS quoting), stable-substring recommendation
(85.7% unintended), three fresh-host hypotheses, a nav-menu false positive,
blaming my own fix for an already-degraded page, expecting the interstitial fix
to unlock other sites, and now these two. Every one came from acting on an
ASSUMED SHAPE rather than measuring it first -- an API's response shape, a
selector's real breadth, a config field's contract. The measurements were always
cheap and always available. The operator's correction -- look at the site when
something is not working -- is the same lesson from the other direction, and it
is now automatic in the tooling via screenshot-on-zero.

## 01:15Z 2026-08-29 -- LOGIN FORMS WIRED, GATES SOLVED, VISION MEASURED BOTH WAYS

HOSTS. bd1 10.0.70.51 (no GPU) and test6 10.0.70.249 (Tesla T4). BOTH have the
full stack: bd-xvfb / bd-x11vnc / bd-novnc / bulkdownloader all active, one Xvfb
each, health 200. noVNC on :6080 -- bd1 password D6BTpqqv10hUmV0B, test6
rvkhMlBQvlYSKOAz. Tokens live in ~/.bd-import as FILENAMES on each host; scripts
read them from there. Operator will rotate tokens and VNC passwords at the end.
CREDENTIALS: 20 sites wired on BOTH hosts, username + resolvable @cred:, cookie
jar, download dir under the allowlisted root, plaintext_count=0. The CSVs remain
in ~/.bd-import on both hosts by operator instruction -- DO NOT SHRED.

LOGIN FORMS: 19/20 MEASURED AND WIRED into sites_config.json, auto_teach_first_run
turned off for those so BD no longer opens a manual window it does not need.
  Corrected URLs the operator supplied (my originals were guesses, one a 404):
    naughtyamerica https://members.naughtyamerica.com/login
    adulttime      https://www.adulttime.xxx/en/login   (.xxx skips Cloudflare)
    pegasproductions https://pegasproductions.com/login/?langue=en
    nookies        https://nookies.com/membersarea
    vixenplus      https://login.vixen.com/i/vixenplus/login
  STILL INCOMPLETE, and the honest state:
    ultrafilms, wowgirls -- fields wired, SUBMIT EMPTY. Both label the button
      "GET INSIDE". A structural submit finder (locate the password field, take
      the submit control inside ITS form, ignore wording) was added and PARSES
      but did not fire on the retry; unfinished, cause unknown.
    kink -- gates now clear correctly (consent "Accept All", age "ENTER KINK")
      but the header LOG IN modal step does not open. Operator-supplied sequence:
      Accept All -> ENTER KINK -> LOG IN (header) -> modal with
      "Username or Email Address" + Password + "Stay logged in for 30 days".

GATES ARE A CLASS, NOT A SITE QUIRK (row 372). Four layers -- cookie consent, age
warning, upsell interstitial, login modal -- all produce ONE symptom: content that
reads as absent, indistinguishable from a stale session or a broken selector.
bd_gates.py implements the operator's ruling: per-site measured gates first, then
a generic always-on pass in the order a person meets them. THE DENYLIST IS
LOAD-BEARING: asked to dismiss kink's age overlay, qwen2.5vl chose
"I Disagree, Exit Here" -- the control that LEAVES -- and landed on Google SSO,
while my own check reported "password field present: True" because it never
verified the origin. Nine denylist cases pass, including that exact string.

AI, MEASURED BOTH DIRECTIONS (rows 370, 366):
  STRONG. qwen2.5vl:7b read the evilangel download menu from a SCREENSHOT:
  8/8 heights, 0 hallucinated, 8.8s, gpu_ratio 1.0 on the T4. It read
  "Web HD 540p" as 540 and returned 160/240/360 -- exactly where
  aiassist.normalize_resolution answers 720 and 0. MEASURED against BD's own
  readers: normalize_resolution wrong on 4/8 real labels, detect.res_score right
  on 8/8. The product disagrees with itself depending on which path reads a label.
  WEAK. On the kink gate it was confidently wrong in a way that would have been
  harmful if auto-applied. Perception is strong; INTENT is not. This is why the
  operator's "selectors first, vision as fallback, record which won" is right.

MY OWN ERROR PATTERN, UNCHANGED AND WORTH THE NEXT AGENT'S ATTENTION: acting on an
ASSUMED SHAPE instead of measuring it. Tonight: a response field named `id` not
`site_id` (cost a full pass on two hosts); PUT storing `password` verbatim (would
have written plaintext -- caught by reading the code first); learn_login.py
OVERWRITING its own output so 16 measurements were replaced by 3; a credential
resolve test run in a locked process reporting 0/20 and nearly filed as a
catastrophe; pkill -f matching the ssh command carrying its own pattern and
killing the caller. Every one was cheap to check first.

NEXT, IN ORDER (operator picked all three, order mine):
 1. Attempt ONE login on evilangel -- its form is the only one verified by hand
    with fake credentials. Single attempt, stop on failure, never retry blind.
 2. Prove the download loop end to end on whichever site logs in.
 3. Row 369 -- a service restart LOCKS THE VAULT and every login silently
    reverts to "SKIPPED - missing password". Until that is fixed, all of the
    above survives exactly until the next restart.
FINISH ALSO: ultrafilms/wowgirls submit, kink modal, and the 3 partial sites.

## CHECKPOINT 2026-08-29 01:11Z -- written by bd-checkpoint-write

Login forms wired 19/20; gates solved; vision measured both ways. Compaction point.

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           33 spec row(s) not yet closed on main
  codex           1 live session(s); 104 worker worktree(s) on disk
  harness         2 bd-*.sh process(es) running
  host            test5 load average: 3.56, 2.48, 2.22

## 2026-08-29 02:05Z -- THE DOWNLOAD IS 119x SLOWER THAN THE WIRE

The operator saw a 3.9 GB ultrafilms download running at 0.7 MB/s on a 10 GbE
NIC behind gigabit fiber and asked why. Everything else was excluded BY
MEASUREMENT before the cause was named, which is the only reason the answer is
trustworthy:

  raw WAN, test6                 63.5 MB/s   (bd1 80.3, bd .50 83.2, bd2 105.5,
                                              bd3 108.1, bd4 80.2 -- all 10GbE)
  NIC ens33 vmxnet3              10000Mb/s, all offloads on, 0 errors,
                                 390 dropped RX out of 4.2e9 packets
  disk, target filesystem        184 MB/s O_DIRECT / 129 MB/s buffered+fsync
  proxy / VPN                    proxy='', vpn_* all empty, no SOCKS listener,
                                 no tun/wg interface -- nothing in the path
  HTTP client, SAME url + cookies + host:
      curl_cffi impersonate=chrome124 (BD's own stack)   72.42 MB/s
      httpx                                              72.65 MB/s
      plain libcurl                                      68.75 MB/s
      curl_cffi WITHOUT impersonation                    43.62 MB/s
  4 parallel ranges                8.91 MB/s -- SLOWER than one stream, so the
                                 CDN penalises concurrency and turning on
                                 use_multi_conn would have made it worse. That
                                 was the obvious "fix" and the measurement
                                 killed it before it cost anything.

REPRODUCED, and it matches the live job to within 0.05 MB/s:
    iter_content, NO budget call          31.5 MB in  0.4s = 77.34 MB/s
    iter_content, WITH record_site_bytes  31.5 MB in 48.3s =  0.65 MB/s
    both: ~4900 buffers, median 8183, max 8183

CAUSE, and it is two false beliefs stacked:
  1. runner_transport.py computes chunk = 4 MB and passes it as
     iter_content(chunk_size=chunk). curl_cffi IGNORES IT. The buffers are
     8183 bytes -- median AND max, so it is not a distribution, it is the fixed
     size. The code comment beside it reads "Both yield bytes objects of roughly
     chunk_size each." It is false, and it means every per-buffer cost in that
     loop is paid ~500x more often than the author believed.
  2. The loop calls daily_budget.record_site_bytes() per buffer. That opens a
     connection, calls _ensure_table(), and runs TWO statements. MEASURED at
     6.104 ms, against 0.001 ms for record_bandwidth on the adjacent line.
     Its docstring says "Cheap: one UPSERT on a small indexed table."
     8183 bytes / 6.1 ms is the ceiling, and that is the number on the screen.

Filed as row 376 with the full brief in bd-codex-briefs/row376.txt; codex is
building it. The RED test is specified as a WRITE COUNT, not a wall-clock, and
the correctness control is that the daily total must stay byte-exact.

THE SHAPE WORTH REMEMBERING: both halves were comments asserting a cost. Neither
had been measured since it was written, and each was individually plausible. The
product of two plausible-but-wrong beliefs was a 119x defect sitting in the
application's core function.

## 2026-08-29 01:50Z -- LOGIN, AND WHY TWO SITES HAD NO SUBMIT

ultrafilms and wowgirls: submit control is a plain <div> (div.submit-button /
div.loginform-submit-button, both labelled "GET INSIDE"), outside the structural
finder's semantic candidate set (button[type=submit], input[type=submit],
button:not([type=button]), [role=button]). Zero candidates -> None. Widened the
finder with an evidence pass over div/span/a[href='#'] scored on class/id and
requiring a visible label, plus a uniqueness check that returns UNKNOWN rather
than a guess when a selector resolves != 1 time. First attempt at the ranking
picked div.loginform-18plus -- the age checkbox -- because a bare /login/ test
matched it and "the action sits last in the form" put it after the real button.
Scoring fixed it. BOTH SITES NOW LOG IN UNATTENDED: ultrafilms "OK - 3 cookies",
wowgirls "OK - 5 cookies", via "submit: click submit selector".

kink: the form is ALREADY COMPLETE in the DOM after the gates clear
(form#loginPopup: input[name=username], input[name=password],
button[type=submit], a remember checkbox, a g-recaptcha-response field) but
every element measures 0x0. The header trigger is a.nav-login -- TWO in the DOM,
exactly ONE visible, so an unscoped click takes the hidden mobile nav and times
out -- and its label is "LOG\xa0IN" with a NON-BREAKING SPACE, which is why
every :has-text('LOG IN') trigger missed it. After a.nav-login:visible is
clicked the password field measures 379x38. BD has NO login_trigger concept
(submit.py:541-575 reads only user_field/pass_field/submit_btn), so it reported
"Couldn't find username field; tried 28 selectors" -- 28 selectors cannot find a
field that is present and zero-sized. Filed as row 373.

## 2026-08-29 02:05Z -- CRAWLER AND TITLE MEASUREMENTS (rows 374, 375)

Four live authenticated listings measured. The most COMMON link pattern is never
the scenes: wowgirls' top pattern is /updates/genre/<n> at 208x (filter chips
labelled "4K"), nubilefilms' is /video/gallery/<n> at 364x (the pager). The
discriminator that survives the data is an <img> inside the anchor. Paging has
three coexisting shapes and evilangel has numbered pages AND infinite scroll on
one listing (84 -> 104 links, height 9207 -> 9617 on two wheel events).

TITLE: library.title exists (migrations.py:398) and is populated on 0 of 84 live
rows, because library_record()'s ONLY caller (db.py:1100) never passes title=.
library_final.py:680 then fills it with Path(fn).stem -- the download filename,
which is exactly what the operator ruled out. The real name is available but not
where you would look: ultrafilms' scene page has NO h1 at all, and carries
og:title == document.title == "UltraFilms / Members / Movie / With Leo In Bed",
while its LISTING CARD reads "LEONA MIA" -- the performer. Filed as row 375.

## 2026-08-29 02:55Z -- THE VAULT PASSWORD FILE DOES NOT OPEN EITHER VAULT

The operator supplied a master password so the vaults could be re-armed
unattended, following their API-token convention: ~/.bd-import/vault-master is a
DIRECTORY and the password is the NAME of the single file inside it (that file's
contents are an unrelated .webarchive binary -- do not read the contents).
Propagated to all six hosts.

IT DOES NOT WORK, and this was established WITHOUT burning throttle attempts:
  * /api/secrets/unlock returns HTTP 401, which app_secrets.py:138 emits only
    for "incorrect password". auth_throttle shares an escalating back-off
    between unlock and change_password, so guessing is expensive; after the
    first 401 the check moved OFFLINE.
  * Offline verification (read-only, no service call): derive PBKDF2-SHA256
    600000 over the vault's own salt and attempt AES-GCM on one ciphertext.
        bd1    60 ciphertexts  -> does NOT decrypt
        test6  34 ciphertexts  -> does NOT decrypt
    Both vaults were initialised earlier (bd1's secrets.json is stamped 00:07)
    with a master password that predates the operator's file.

CONSEQUENCE FOR THE OVERNIGHT PLAN. The operator authorised "merge on green;
deploy test6 after 369". THE DEPLOY HALF IS WITHHELD. A deploy restarts the
service; MasterPasswordBackend holds the derived key in process memory only, so
the restart drops it, and the supplied password cannot restore it. Deploying
would disarm all 34 configured logins with no unattended recovery. Merges
continue; test6 keeps running its current build.

MY ERROR, STATED PLAINLY: I locked bd1's vault deliberately to prove the unlock
path worked -- correct instinct, since an unexercised recovery path is exactly
the fail-open shape this session has been hunting -- and it did not work, so bd1
is now locked and I cannot re-open it. Bounded: cookie jars live in
BulkDownloader/cookies/<sid>.json, OUTSIDE the vault, so existing sessions on
bd1 still function; only FRESH logins are blocked there until it is unlocked.
test6 was never locked and retains all 34 sessions.
secrets.json was preserved to secrets.json.preserve-0829 on bd1 before anything.

RECOVERY, NOT YET EXECUTED (needs the operator, or their explicit go-ahead):
either (a) the operator supplies the real master password, or (b) re-key: the
credential CSVs are present on every host, so every site password can be
re-stored under a vault initialised with a KNOWN password. Note (b) must
preserve the existing site IDs -- the cookie jars are named by site id, so
re-creating sites the way rewire.py does would orphan 20 live sessions on bd1
and 34 on test6. Re-store under the SAME @cred: key names instead.

THE GENERAL LESSON, WHICH IS THE SAME ONE AS EVERY OTHER DEFECT TONIGHT: the
unlock path had never been executed. It looked configured. "Configured" and
"exercised" are different claims, and only the second one is evidence.

## CHECKPOINT 2026-08-29 03:26Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           40 spec row(s) not yet closed on main
  codex           15 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 44.28, 27.23, 18.37

## CHECKPOINT 2026-08-29 03:36Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           38 spec row(s) not yet closed on main
  codex           15 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 27.02, 25.77, 21.83

## CHECKPOINT 2026-08-29 03:46Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           35 spec row(s) not yet closed on main
  codex           15 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 30.04, 30.77, 28.01

## 2026-08-29 03:56Z -- THE FIX IS CONFIRMED IN PRODUCTION, ~100x

Measured on test6 with real queued scenes, after setting use_curl_cffi=False on
all sites (verified on disk, 32/32):

    wowgirls      704.0 MB / 2.8 GB   71.7 MB/s
    nubilefilms   552.0 MB / 3.6 GB   30.2 MB/s
    (before, same host, same night)   0.65 - 0.70 MB/s

curl_cffi treats iter_content(chunk_size=N) as ADVISORY and yields 8183-byte
buffers; httpx honours it and yields exactly N. With the per-buffer
daily_budget.record_site_bytes() write still present -- row 376 is NOT yet
merged -- that difference alone is the whole ~100x. Row 376 (batch the budget
write) and row 378 (db_conn is not cheap) remain the correct permanent fixes;
this is a config-level workaround that does not touch the defect.

CAVEAT, STATED SO IT IS NOT OVERCLAIMED: the earlier ultrafilms completion that
ran at ~23 MB/s recorded transfer_mode="browser", i.e. Playwright save_as, NOT
httpx -- so that one cannot be attributed to the flip. These two can: they are
running now, on the httpx path, at 30-72 MB/s.

TRADE-OFF ACCEPTED BY THE OPERATOR: dropping curl_cffi loses its chrome124 TLS
fingerprint on the PAYLOAD fetch only; the browser/login path keeps its own
cloaking. Ruling was "all 34 sites, revert any that break".

CRAWL -> QUEUE -> DOWNLOAD IS NOW DEMONSTRATED END TO END. 6 scenes queued from
three measured listings with the site-shown title captured at discovery time:
  nubilefilms  'A Bed Built For Two - S14:E10'
  wowgirls     'Fuck Me Nikki Hareniks'
  ultrafilms   'SARAH HEIZEL'  (this site's CARD shows the PERFORMER; the scene
               page's og:title carries the real name -- see row 375)
evilangel returned 0 scenes from /en/videos (148 links) and needs its own entry
point; not yet solved.

## CHECKPOINT 2026-08-29 03:56Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           36 spec row(s) not yet closed on main
  codex           15 live session(s); 119 worker worktree(s) on disk
  harness         5 bd-*.sh process(es) running
  host            test5 load average: 35.23, 27.34, 26.81

## 2026-08-29 04:25Z -- THE FULL MISSION CHAIN, DEMONSTRATED

crawl listing -> extract scene URL + site-shown title -> queue via BD's own
/api/queue/v2/add_url -> authenticated fetch -> highest-quality pick -> download
-> history row. Measured on test6, 8 real downloads, 32.8 GB:

  wowgirls     4.05 GB  NikkiHareniks_FuckMeNikkiHareniks_7680x4320_60fps
  ultrafilms   6.21 GB  girls-gone-wilder_gracie-sweet_vixi-rafi_7680x4320
  ultrafilms   4.14 GB  naughty-coquette_sarah-heizel_7680x4320
  wowgirls     4.66 GB  LeahMaus_NancyA_Sybil_CantBeHotterThanThis_5568x...
  nubilefilms  3.83 GB  nfbusty_she_will_take_care_of_anything_i_need_3840
  nubilefilms  2.70 GB  girlsonlyporn_a_bed_built_for_two_3840

THE TITLES ROUND-TRIP. Queued as 'Fuck Me Nikki Hareniks' and 'A Bed Built For
Two - S14:E10'; landed as NikkiHareniks_FuckMeNikkiHareniks_... and
..._a_bed_built_for_two_.... 8K (7680x4320) chosen where offered, which is the
quality cascade doing its job unattended.

WHAT IS STILL NOT DONE, so this is not overclaimed:
  * library.title is STILL EMPTY on these rows -- row 375 is not merged, so the
    website name is captured at DISCOVERY time by the queueing tool, NOT by BD
    itself. The operator's requirement is met only once 375 lands.
  * The crawler is my out-of-band tool against three listings whose entry points
    were measured by hand. Row 374 puts it in the GUI for all sites.
  * evilangel returns 0 scenes from /en/videos and needs its own entry point.

## CHECKPOINT 2026-08-29 04:06Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           32 spec row(s) not yet closed on main
  codex           15 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 27.97, 27.18, 27.46

## CHECKPOINT 2026-08-29 04:17Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1329 at a9a7e6e25da5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 313 CLOSED on origin/main
  queue           34 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         5 bd-*.sh process(es) running
  host            test5 load average: 1.07, 13.80, 22.06

## CHECKPOINT 2026-08-29 04:27Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 3.67, 4.91, 13.02

## CHECKPOINT 2026-08-29 04:37Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 3.24, 3.03, 8.06

## CHECKPOINT 2026-08-29 04:47Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 3.24, 4.11, 6.30

## CHECKPOINT 2026-08-29 04:57Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 3.56, 3.43, 4.85

## CHECKPOINT 2026-08-29 05:07Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 3.94, 3.68, 4.18

## CHECKPOINT 2026-08-29 05:17Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 20.90, 9.75, 6.09

## CHECKPOINT 2026-08-29 05:27Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 2.93, 7.82, 8.16

## CHECKPOINT 2026-08-29 05:37Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 23.07, 12.72, 9.30

## CHECKPOINT 2026-08-29 05:47Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 2.70, 6.74, 8.95

## CHECKPOINT 2026-08-29 05:57Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 24.10, 17.34, 12.35

## CHECKPOINT 2026-08-29 06:07Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 9.97, 10.64, 12.04

## CHECKPOINT 2026-08-29 06:17Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           56 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 24.93, 20.05, 15.39

## CHECKPOINT 2026-08-29 06:28Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1330 at b6278ea2c128
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 314 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 3.60, 8.19, 11.88

## CHECKPOINT 2026-08-29 06:34Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1331 at 748da34ac706
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 315 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         5 bd-*.sh process(es) running
  host            test5 load average: 2.80, 7.76, 10.78

## CHECKPOINT 2026-08-29 06:44Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1331 at 748da34ac706
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 315 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 22.44, 12.44, 10.59

## CHECKPOINT 2026-08-29 06:50Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1332 at 439406ccef72
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 316 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 5.65, 14.39, 12.81

## CHECKPOINT 2026-08-29 07:00Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1332 at 439406ccef72
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 316 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 23.74, 13.73, 11.70

## CHECKPOINT 2026-08-29 07:10Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1332 at 439406ccef72
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 316 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 3.88, 7.52, 10.05

## CHECKPOINT 2026-08-29 07:20Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1332 at 439406ccef72
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 316 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 2.18, 9.60, 11.14

## CHECKPOINT 2026-08-29 07:30Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1332 at 439406ccef72
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 316 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 25.20, 17.65, 13.34

## CHECKPOINT 2026-08-29 07:32Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1333 at 394c3f118067
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 317 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 8.09, 14.04, 12.48

## CHECKPOINT 2026-08-29 07:42Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1333 at 394c3f118067
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 317 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 10.71, 6.66, 8.72

## CHECKPOINT 2026-08-29 07:49Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1334 at 75ede5d3044e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 318 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 3.15, 10.96, 11.12

## CHECKPOINT 2026-08-29 07:59Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1334 at 75ede5d3044e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 318 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 22.91, 12.62, 10.51

## CHECKPOINT 2026-08-29 08:09Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1334 at 75ede5d3044e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 318 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 4.40, 9.86, 11.54

## CHECKPOINT 2026-08-29 08:20Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1334 at 75ede5d3044e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 318 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 8.23, 6.66, 9.01

## CHECKPOINT 2026-08-29 08:30Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1334 at 75ede5d3044e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 318 CLOSED on origin/main
  queue           47 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 30.51, 21.32, 14.42

## CHECKPOINT 2026-08-29 08:32Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1335 at a80e4986dea0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 319 CLOSED on origin/main
  queue           46 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         5 bd-*.sh process(es) running
  host            test5 load average: 7.72, 17.53, 14.24

## CHECKPOINT 2026-08-29 08:42Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1335 at a80e4986dea0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 319 CLOSED on origin/main
  queue           46 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 8.64, 6.43, 9.16

## CHECKPOINT 2026-08-29 08:50Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1336 at a61a68f94acf
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 320 CLOSED on origin/main
  queue           46 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         8 bd-*.sh process(es) running
  host            test5 load average: 0.92, 4.76, 7.50

## CHECKPOINT 2026-08-29 09:00Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1336 at a61a68f94acf
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 320 CLOSED on origin/main
  queue           46 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 4.60, 3.62, 5.46

## CHECKPOINT 2026-08-29 09:10Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1336 at a61a68f94acf
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 320 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 4.59, 3.70, 4.61

## CHECKPOINT 2026-08-29 09:21Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1336 at a61a68f94acf
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 320 CLOSED on origin/main
  queue           46 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 1.12, 1.67, 3.14

## CHECKPOINT 2026-08-29 09:31Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1336 at a61a68f94acf
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 320 CLOSED on origin/main
  queue           46 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 22.22, 11.11, 6.23

## CHECKPOINT 2026-08-29 09:36Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1337 at 6ac0e12a03c1
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 321 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 3.83, 10.73, 8.15

## CHECKPOINT 2026-08-29 09:46Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1337 at 6ac0e12a03c1
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 321 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 4.29, 4.49, 5.65

## CHECKPOINT 2026-08-29 09:56Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1337 at 6ac0e12a03c1
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 321 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 21.05, 10.08, 6.92

## CHECKPOINT 2026-08-29 10:06Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1337 at 6ac0e12a03c1
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 321 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 4.11, 8.01, 8.64

## CHECKPOINT 2026-08-29 10:16Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1337 at 6ac0e12a03c1
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 321 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         11 bd-*.sh process(es) running
  host            test5 load average: 2.70, 4.33, 6.27

## CHECKPOINT 2026-08-29 10:27Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1337 at 6ac0e12a03c1
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 321 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 3.19, 10.97, 10.29

## CHECKPOINT 2026-08-29 10:28Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 1.92, 9.20, 9.72

## CHECKPOINT 2026-08-29 10:38Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 3.43, 4.45, 6.90

## CHECKPOINT 2026-08-29 10:48Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 1.23, 2.96, 5.02

## CHECKPOINT 2026-08-29 10:58Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.37, 1.16, 3.26

## CHECKPOINT 2026-08-29 11:08Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.29, 0.45, 1.84

## CHECKPOINT 2026-08-29 11:18Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.36, 0.74, 1.36

## CHECKPOINT 2026-08-29 11:28Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.65, 0.45, 0.88

## CHECKPOINT 2026-08-29 11:38Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.46, 0.38, 0.63

## CHECKPOINT 2026-08-29 11:48Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.73, 0.40, 0.49

## CHECKPOINT 2026-08-29 11:58Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.32, 0.34, 0.41

## CHECKPOINT 2026-08-29 12:08Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.71, 0.48, 0.45

## CHECKPOINT 2026-08-29 12:19Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.40, 0.34, 0.37

## CHECKPOINT 2026-08-29 12:29Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           40 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.56, 0.47, 0.40

## CHECKPOINT 2026-08-29 12:39Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           39 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.74, 0.62, 0.48

## CHECKPOINT 2026-08-29 12:49Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           41 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.48, 0.74, 0.70

## CHECKPOINT 2026-08-29 12:59Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           41 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 1.61, 1.15, 0.90

## CHECKPOINT 2026-08-29 13:09Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1338 at 0e933be3d180
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 322 CLOSED on origin/main
  queue           40 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 22.15, 10.98, 5.11

## CHECKPOINT 2026-08-29 13:18Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           39 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 2.62, 12.26, 9.93

## CHECKPOINT 2026-08-29 13:28Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           40 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 4.13, 4.85, 6.85

## CHECKPOINT 2026-08-29 13:38Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           39 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.57, 1.87, 4.42

## CHECKPOINT 2026-08-29 13:48Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           40 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.56, 0.62, 2.48

## CHECKPOINT 2026-08-29 13:58Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           40 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 0.53, 0.60, 1.56

## CHECKPOINT 2026-08-29 14:08Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           42 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 3.29, 4.85, 3.37

## CHECKPOINT 2026-08-29 14:18Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           42 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 7.26, 10.42, 7.85

## 2026-08-29 14:20Z -- STATE AT 92% CONTEXT

MAIN IS v3.66.1339. TEST6 IS DEPLOYED ON IT and healthy (ok=true, GET / = 200,
credentials 32/32, deployed HEAD == origin/main exactly).

TEN ROWS MERGED THIS RUN:
  1330/363 affordance learner in the GUI     1331/361 template selectors resolved
  1332/367 interstitial is a state           1333/368 admin token scope
  1334/372 gate clearing + exit denylist     1335/373 login form behind a trigger
  1336/366 two resolution readers agree      1337/376 the per-8KB DB write (119x)
  1338/360 Turnstile advertised vs installed 1339/375 history records the website name

IN FLIGHT AT WRITE TIME
  374 (the in-GUI crawler) integrating as v3.66.1340, QA green at 94 passed.
  377, 378 -- two Claude opus agents running (codex is dead, see below).
  371 -- markers cleared but 5 of its OWN tests fail: they assert a
        `_page_gates_are_safe` design while its source calls `clear_gates`,
        which is row 372's design and is what merged. Its tests are stale
        against its own implementation. Real work, not a marker.

CODEX IS OUT OF CREDITS UNTIL 2026-09-03 19:27.
    ERROR: You've hit your usage limit.
Every dispatch fails instantly. Operator ruling: replace with Claude agents on
opus, high effort. bd-codex-pool.sh and bd-codex-refill.sh are KILLED and were
REMOVED FROM bd-watchdog's guard list first -- they had been revived twice
because THREE watchdogs were running and the oldest still held the pre-edit
guard list in memory. One watchdog now runs; it guards bd-night, bd-att-guard,
bd-width-restore, bd-autorebase, bd-persist-loop, bd-checkpoint-loop and
bd-unstale-loop.

I DESTROYED SEVEN WORKTREES AND RECOVERED THEM.
bd-codex-cut.sh begins `rm -rf "$W"` and re-adds from origin/main -- correct for
a NEW row, catastrophic for a repair. I dispatched seven repair tasks through it
having already read that line. Rows 357/370/371/374/375/377/378 went from 10-27
changed files to 1. All seven came back from ~/bd-persist/codex; row 378's
450-line test file survived ONLY because capture_codex.sh tars UNTRACKED files
separately (git diff HEAD cannot see a new file). bd-codex-repair.sh now runs in
place and REFUSES when the worktree holds fewer than 3 changed files.
Restorability is now PROVEN, not assumed: 102 rows dry-run `git apply --3way
--check` against origin/main in a scratch checkout; all apply.

deploy.sh MISDIAGNOSES ITS OWN FAILURE. Twice it said "FAIL [step 12]: 503 --
the SPA bundle was not found ... rebuild it with npm ci && npm run build" while
frontend/dist was complete (index.html, .bd-built-from, 34 assets) and GET / was
200. The health body it had ALREADY FETCHED said `"degraded":
"credential_vault_locked"`. Unlocking fixed it; nothing was rebuilt. Filed as
row 285 with that evidence.

STILL UNVERIFIED, DO NOT CLAIM IT WORKS: row 375's title fix. library.title is
empty on all 98 rows, but every one predates the 13:23 deploy, so the test is
INCONCLUSIVE rather than failed. Four fresh wowgirls scenes queued after the
deploy all returned "Clicked but no download started -- scored ok but no
download fired", producing no new library row. Two nubilefilms scenes are queued
to try again. NOTE THE SECOND POSSIBILITY: those four failures are all POST-merge
while the four that succeeded were at 04:09-04:16 PRE-merge, so a download
regression from tonight's merges is not excluded. That needs a fresh completed
download on any site to settle.

NEW TOOLS, all in ~/bd-persist/harness
  bd-codex-repair.sh      run codex/agents in an EXISTING worktree, never wipe
  bd-unstale-generated.py drop stale generated artifacts from a worker diff
  bd-unstale-loop.sh      re-run that after every merge (watchdog-guarded)
  bd-deref-register.py    strip dangling row citations from a register row
  bd-depipe-register.py   strip stray pipes that break the markdown table
  bd-report-changelog.py  derive changelog bullets from a row's real diff
  bd-register-open-row.py add an OPEN register row from the row's brief
  bd-rpy                  run local Python on a remote host, no heredoc quoting
  bd-vault-unlock.sh      unlock a host's vault from the operator's password dir
  bd-netprobe.sh          1-stream vs N-stream WAN measurement per host
  bd-wansat.sh            fleet-wide saturation (measured 2.59 Gbit/s aggregate)

THE FOUR LANE DEFECTS THAT COST THE MOST TIME, all now tooled:
  1. rows filed as `# NNN|slug|title` are COMMENTS and invisible to the drain;
  2. the integrator needs >=2 "- " bullets in the last 400 lines of the worker
     report or it aborts the whole cut;
  3. the integrator needs the row present in the canonical register, and my
     briefs had told workers NOT to touch it;
  4. every merge re-stales DEPENDENCY_GRAPH.json / FUNCTION_INDEX.md in every
     un-merged worktree, refusing rows on files nobody edited.

LEFT FOR THE OPERATOR
  369 -- design collision: its resolve_password_for_login vs the
         resolve_password_state ALREADY IN MAIN at secrets_store.py:899. Two
         implementations of one credential path. Not mine to choose.
  370 -- its conflict carries login_field_order / password-first-form work,
         which looks like another row's, not "vision reads what selectors
         cannot". Possible cross-row contamination; needs an eye before merge.

## CHECKPOINT 2026-08-29 14:28Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           42 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 24.69, 22.37, 15.34

## CHECKPOINT 2026-08-29 14:39Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           42 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 4.70, 12.39, 14.82

## CHECKPOINT 2026-08-29 14:49Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           42 spec row(s) not yet closed on main
  codex           0 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 2.15, 4.86, 9.56

## 2026-08-29 14:52Z -- THE DOWNLOAD REGRESSION IS REAL, AND NARROWED

MEASURED on test6 (10.0.70.249), history table in downloader_history.db:
  BEFORE the 13:23 deploy (02:00-05:18): 14 rows status='done' across SIX sites
    -- ultrafilms, nubilefilms, wowgirls (2 done at 03, 2 done at 04),
       filthykings, teenmegaworld.
  AFTER  the 13:23 deploy (13:24-14:41): 8 attempts, ZERO done.
    wowgirls 5x needs_review, nubilefilms 3x needs_review.
Same site, same credentials, opposite outcome across the deploy. The
"those four scenes simply differed" hypothesis is now excluded: wowgirls
succeeded twice at 03 and twice at 04 and has failed five times since.

WHAT THE FAILURE ACTUALLY IS. The message is "Clicked but no download started
-- scored ok but no download fired". The TOP-RANKED candidate is a CONTAINER
whose text is the whole block ("FULL MOVIE DOWNLOAD / 60 FPS / ..."), while the
REAL 8K button ("7680 x 4320", href content-vi...) is present at rank 3.
Clicking a container fires no Playwright download event. nubilefilms shows the
same shape ("Video Quality480x270640x360960..."). So this is a RANKING failure,
not a session failure.

NOT the session: the screenshot BD itself saved for history id 113
(screenshots/35295d1f/https___venus_wowgirls_com_film_h1b620ff_breathtaking_debut.png)
shows the page FULLY LOGGED IN with all four real download buttons visible
(1920x1080 1.99GB, 1280x720 395MB, 3840x2160 3.44GB, 7680x4320 3.01GB). Note
each size is SIBLING text under its button, not inside the anchor.

NOT config: /home/mboyle/BulkDownloader/sites_config.json on test6 has mtime
03:23 -- BEFORE the 04:09 successes AND before the deploy. The learned
selectors are identical across the working and failing periods.

NOT the scoring code, apparently: bulk_downloader/detect.py, which holds
find_best_download and builds _all_candidates, is BYTE-IDENTICAL across the ten
cuts (`git diff b6278ea2^ HEAD --stat -- bulk_downloader/detect.py` is empty),
and no line of the runner.py diff touches find_best_download / row_selector /
learned / candidate / affordance. That is the open contradiction: the ranking
changed while the ranking code did not.

An opus agent is running the decisive offline experiment -- capture the current
wowgirls DOM once, then run find_best_download against that SAME DOM at
b6278ea2^ and at 8cbffeec. Both picking the container means the SITE changed its
markup and the real defect is that a container can outrank a leaf anchor; only
the old commit picking the button makes it a regression, and then it bisects
across the ten merges.

Row 375's title question REMAINS UNSETTLED and is blocked behind this: it needs
one completed download, and no download has completed since the deploy.

ROW 374 / v3.66.1340 -- two frozen-surface gates refused it; both re-cut and
STATED (the sanctioned path, per the v3.66.716 precedent in the gate itself):
  * tests/route_map_baseline.txt 1003 -> 1005 lines, exactly two ADDED routes,
    POST /api/discovery/scenes/start and GET /api/discovery/scenes/status, the
    in-GUI crawler's start/poll pair. _BASELINE_SHA re-pinned with the reason
    recorded beside it. Nothing removed or re-pathed.
  * _API_TOKEN_ROUTE_POLICY 10 -> 12 for the same two routes (status=read,
    start=enqueue). The LITERAL denominator in the test was extended by hand,
    never derived from the policy object, so a missing entry still cannot
    disappear from both sides. Counts: read 8->10, enqueue 10->13, admin 16->19,
    method-sum 16->19, registered_actions 16->19, and the per-scope
    allowed/denied exercise 8/8,10/6,16/0 -> 10/9,13/6,19/0.
Both gate files then ran whole: 24 passed. Re-integrated as v3.66.1340 at
e69fe57b, PR #623, in verify.

## CHECKPOINT 2026-08-29 14:59Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           41 spec row(s) not yet closed on main
  codex           6 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 4.46, 13.59, 13.66

## 2026-08-29 15:02Z -- ROW 375 IS VERIFIED, AND THE REGRESSION IS NOT SYSTEMIC

A download COMPLETED on test6 at 15:00:12 on v3.66.1339 -- ultrafilms,
"Get Closer to Kitty", 7680x4320 (8K), 3,782,299,070 bytes, status done.
Chain proven end to end after the deploy: login -> listing -> fresh-scene pick
-> 8K selection -> download -> mp4 metadata -> history -> library.

ROW 375 PASSES. library id 99 -> history_id 114:
    title        = 'UltraFilms / Members / Movie / Get Closer to Kitty'
    title_source = 'og:title'
Every earlier library row (98, 97, 96 ...) still has title='' and
title_source='', and every one of them predates the 13:23 deploy. So the first
download after the fix is the first row that carries a title, with provenance.
This was INCONCLUSIVE until now; it is now PASS on real evidence.
Caveat, not a defect: the recorded title still carries the site's template
prefix ("UltraFilms / Members / Movie / "). strip_repeated_title_template only
fires once a SECOND distinct scene on the same site repeats the template, so a
second ultrafilms download should normalize it. Worth one more download to
confirm that half.

THE DOWNLOAD REGRESSION IS NOT SYSTEMIC. Corrected scope: v3.66.1330..1339 did
NOT break downloading. ultrafilms works on the deployed code. The failures are
confined to wowgirls (5x) and nubilefilms (3x), both with the same shape -- a
CONTAINER outranking the real leaf anchor. That makes a site-side markup change
the leading hypothesis and a code regression the trailing one, which is the
reverse of what the 14:52 entry above assumed. The A/B experiment already
running (same DOM, old commit vs new commit) still decides it either way.

## CHECKPOINT 2026-08-29 15:09Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1339 at 8cbffeecbfea
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 323 CLOSED on origin/main
  queue           41 spec row(s) not yet closed on main
  codex           6 live session(s); 119 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 25.04, 16.40, 13.63

## 2026-08-29 15:15Z -- THE TWO STRUCTURAL FIXES, AND WHAT THE AUDIT FOUND

OPERATOR RULINGS TAKEN: worktree lockfile; mission gate reports loudly but does
not block; audit the 9 live worktrees; re-rank the backlog with a proposed cut
list for approval. Merges remain held; 374/v3.66.1340 is the only cut in flight.

FIX 1 -- ONE WRITER PER WORKTREE.  /home/mboyle/bd-wt-lane.sh, same flock idiom
as bd-merge-lane.sh but per ROW, so unrelated rows still integrate in parallel.
bd-row-chain.sh now runs bd-integrate-row.sh inside it. Both edits went through
bd-edit.py (atomic rename), because bd-row-chain.sh was RUNNING: an in-place
rewrite makes the live bash resume at a byte offset in new text. The running
instance keeps the old inode; the lock takes effect on the next invocation.
This is the defect that cost 26 minutes today -- the row 374 fix landed at
14:42, the candidate froze at 14:43 from a diff captured at 14:41, and the band
spent 13 minutes judging a tree that never contained the fix. Twice.

FIX 2 -- A ROW MUST CONTAIN ITS OWN WORK.  /home/mboyle/bd-row-audit.py, wired
into bd-night.sh row selection. A refusal there is NOT charged as an attempt:
the row never reached a cut. Three MECHANICAL checks, no model:
  C1  the diff carries an implementation file OR a new test
  C2  no module gains a duplicate top-level assignment of a pin constant
      (PARSED with ast, not grepped -- a comment naming the constant is not an
      assignment, and this gate must not fire on prose)
  C3  a raised _EXPECTED_DECLARED_GATE_COUNT comes with a new gate file
UNKNOWN is a third state and is NOT a pass: an unparseable file returns UNKNOWN.

WHAT IT FOUND -- 9 audited: 5 PASS (357, 371, 374, 377, 378), 4 REFUSED.
  312, 313  10 insertions each. No implementation, no test. Both RAISE the
            declared-gate count for a gate file that does not exist. Their
            ARCHIVED patches hold the same two files and nothing else, so the
            implementation was never destroyed -- it never existed.
  309, 310  the same phantom gate, plus UNRESOLVED GIT CONFLICT MARKERS in four
            Python files: build_navigator.py, capture_all.py,
            test_v3_66_1173_gate_scope_debt_is_paid.py, test_secret_display_never.py.
  All four  stack duplicate assignments of _EXPECTED_DECLARED_GATE_COUNT.
            CONTROL: main has ONE. Row 312's copy has EIGHT (228, 227, 226, 225,
            224, 184, 179, 177). Python takes the LAST, so merging it would put
            the declared-gate denominator at 177 against main's 228 -- a gate
            that does not exist would be counted, the exact inverse of "a gate
            CI does not run does not exist".
The row-377 worker hit the same duplicate-assignment defect independently and
fixed it at the measured 229, so this is a recurring worker failure mode rather
than four coincidences.

NOT ATTEMPTED, deliberately: C4, "does this diff implement this subject?" That
is the row-370 shape -- 45 KB of real, working LOGIN code filed under "the
download menu is read from the page" -- and nothing mechanical separates good
code for the wrong row from good code. It is the one genuinely model-shaped
check and is left undone rather than faked.

## CHECKPOINT 2026-08-29 15:14Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1340 at 2860c739eff0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 324 CLOSED on origin/main
  queue           43 spec row(s) not yet closed on main
  codex           6 live session(s); 119 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 8.73, 17.15, 15.38

## CHECKPOINT 2026-08-29 15:24Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1340 at 2860c739eff0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 324 CLOSED on origin/main
  queue           43 spec row(s) not yet closed on main
  codex           6 live session(s); 119 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 5.44, 8.57, 11.38

## CHECKPOINT 2026-08-29 15:35Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1340 at 2860c739eff0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 324 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           9 live session(s); 120 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 24.21, 20.75, 15.62

## 2026-08-29 15:40Z -- THE DOWNLOAD DEFECT IS FOUND, AND IT IS NOT A REGRESSION

I was wrong TWICE and the investigation corrected both. It is not a regression
from v3.66.1330..1339, and it is not a site-side markup change. It is a
PRE-EXISTING latent defect in bulk_downloader/detect.py's wide sweep.

PROOF THE CODE DID NOT CHANGE: detect.py is blob
570771dfae9789d2078b90899a9e5502c3b9155b at ALL TWELVE commits from b6278ea2^
through 2860c739. sites_config.json mtime 08-29 03:23 predates both the 03:54
successes and the 13:23 deploy. The prescribed old-vs-new A/B was therefore
DECIDED BY CONSTRUCTION and could not discriminate; the discriminating
experiment was learned-path vs forced-wide-sweep on the same live page.

THE MECHANISM, three detect.py behaviours composing:
  1. general_selectors admits div.content_download.video_downloads through
     [class*='download' i] -- no leaf/ancestor de-nesting, no visibility check.
  2. gather_text reads inner_text, so the ANCESTOR INHERITS EVERY DESCENDANT'S
     LABEL. res_score gives the wrapper its 7680x4320 child's score: a TIE with
     the real anchor at 4325.
  3. parse_size_bytes takes the FIRST size in the wrapper's text -- 1.99GB,
     which belongs to the 1080p tier -- while the real 8K anchor keeps its size
     in a SIBLING caption and parses 0.
Then detect.py:517 sorts key=(score,size) reverse. Equal score, larger size, so
the WRAPPER WINS, and clicking a div fires no Playwright download event.
2,136,746,229 B is the "2.0 GB" in history row 113, byte for byte.

FILED AND BUILT AS ROW 380, verified by me on test5 at 2860c739:
  RED  on the pristine base: 1 failed, 3 passed --
       "winner is a DIV wrapper with 16 descendants ... assert 'DIV' == 'A'"
  GREEN after the 43-line guard: 4 passed
  BAND 119 files: 1 failed, 2385 passed. The single failure is
       test_import_graph_no_new_edges, one edge --
       tests/test_row380_wrapper_never_outranks_leaf.py -> detect.py -- which
       is the intended edge and which the integrator re-baselines.
The guard drops a candidate ONLY when it carries no affordance of its own AND
contains a descendant that does, and FAILS OPEN on any inspection error. Built
from get_attribute/locator, not page.evaluate: an evaluate-based draft failed
CLOSED against the stub page in tests/test_v3_66_50_f3_dom_visibility.py.

TWO MORE ROWS FILED, both commented and NOT queued:
  381  res_score reads photo-set pixel dimensions (Large 6000x4000px) as a
       video resolution. This is why nubilefilms STILL mis-ranks after 380 --
       a second, distinct, also pre-existing defect. Separate cut.
  382  a learned selector that raises leaves NO breadcrumb; the scan degrades
       silently to the wide sweep. This project's canonical shape -- an
       unavailable measurement returning a permissive answer.

RESIDUAL UNKNOWN, stated rather than papered over: why the learned fast path
returned nothing during 13:24-14:41 is unexplained. Code, config, clear_gates
(0 gate events, [] live), persistent profile, cookies, viewport and settle
timing 0/200/6000ms are all ruled out. The worker's actual DOM in that window
is UNMEASURABLE -- BD saved PNGs, not HTML. Row 382 exists so the next
occurrence leaves evidence.

CLEARED: v3.66.1339's _capture_website_title runs AFTER detection and releases
its lock before any work; v3.66.1334's exit denylist rejected only a genuine
off-site upsell (row 106), never a download navigation.

MISSION GATE, first use, post-deploy on v3.66.1340:
  ultrafilms  PASS  done in 3.7 min, 7,279,729,298 bytes,
                    title='UltraFilms / Members / Movie / Seduced Into Passion'
                    via og:title  -- row 375 confirmed a SECOND time
  wowgirls    UNKNOWN  all 8 listed scene(s) already in history
  nubilefilms FAIL     no dl event
  MISSION FAIL
It caught the nubilefilms failure in four minutes rather than eighty, and it
refused to call an unmeasurable site green. That is the whole point.

## CHECKPOINT 2026-08-29 15:45Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1340 at 2860c739eff0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 324 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           8 live session(s); 120 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 5.64, 11.68, 14.27

## CHECKPOINT 2026-08-29 15:55Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1340 at 2860c739eff0
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 324 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           9 live session(s); 121 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 19.77, 20.92, 17.55

## CHECKPOINT 2026-08-29 15:59Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1341 at 4c2e3d3f21c5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 325 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           9 live session(s); 121 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 20.93, 23.07, 19.41

## 2026-08-29 16:05Z -- v3.66.1341 MERGED AND DEPLOYED; 381 IN THE LANE

v3.66.1341 (row 380, the wrapper fix) merged at 15:58 and is deployed on test6:
version 3.66.1341, build 4c2e3d3f21c5, credentials 32/32, GET / = 200.
deploy.sh MISDIAGNOSED THE 503 A FOURTH TIME -- "the SPA bundle was not found,
rebuild it with npm ci && npm run build" while the health body it had already
fetched read state=locked, resolved_count=0, and GET / returned 200 throughout.
bd-vault-unlock.sh fixed it in one call, as on every prior occurrence. Row 285
now has four data points and is the only OPEN row costing real time.

ROW 381 (photo pixels are not a video resolution) built inline, QA ok at 45
passed, integrating as v3.66.1342. RED 3 failed / 8 passed on the pristine base;
GREEN 11 passed; band 119 files + 2 gate tests: 1 failed, 2407 passed, the one
failure being the expected import-graph edge for its own new test.

TWO OF MY OWN TEST EXPECTATIONS WERE WRONG AND WERE CORRECTED, NOT FORCED:
  1. res_score('Medium 3000x2000px') == -1 was asserted. It returns 360, and
     that is CORRECT -- res_score's third rule reads named tier WORDS and
     'Medium' means 360p. The dimension contributes nothing after the fix; the
     word does. The case now pins both halves.
  2. res_score('1280x720_60FPS.mp4') == 720 was asserted. It is 725 -- the
     documented +5 60fps boost.
Asserting either would have been asserting that a working rule is broken.

AND THE BAND CAUGHT A REAL REGRESSION I INTRODUCED. The first fix anchored the
second dimension group with \b before rejecting a px suffix. "_" IS A WORD
CHARACTER, so \b also killed "1280x720_60FPS.mp4" and broke
test_v3_43_65_cascade.py::Test60FpsTiebreaker::test_60fps_boosts_score
(assert -1 > 720). The shipped rule uses (?!\d)(?!\s*px) instead: (?!\d) blocks
the backtrack to a truncated ("6000","400") match without caring about "_".
All three variants were measured; both regressions are pinned by their own
negative-control cases rather than left to a comment.

THE WORKTREE LOCK IS PROVEN IN PRODUCTION. /home/mboyle/.bd-wt-locks/row380.owner
read "bash bd-integrate-row.sh 380 1341 a-wrapper-must-not-outrank-... (pid
1094979) since 15:46:20" during the integrate, and lock files exist for rows
357, 371, 374, 380. Every integrate since 15:09 has taken it.

STILL HELD: 309, 310, 312, 313 (stale worktrees of ALREADY-MERGED rows -- their
work is in main, verified three ways), 357, 371, 377, 378.

## 2026-08-29 16:07Z -- THE WRAPPER FIX IS VERIFIED ON THE DEPLOYED CODE

Not inferred from a test: measured against the LIVE page that failed five times,
on test6 running v3.66.1341 (build 4c2e3d3f21c5), forcing the wide sweep so it
exercises exactly the failing path. Probe archived as bd-persist/scripts/probe_winner.py.

  https://venus.wowgirls.com/film/h1b620ff/breathtaking-debut
  WINNER tag=A score=4325
    href=https://content-video2.wowgirls.com/download/h1b620ff/7680x4320_60FPS.mp4?...
    cand1 4325 '7680 x 4320 https://content-video2.wowgirls.'
    cand2 4320 '7680 x 4320\n             https://content-vid'
    cand3 4320 '8K /updates/genre/103'
    cand4 3160 '6K /films-6K/'
  VERDICT: PASS -- a clickable leaf

The div.content_download.video_downloads wrapper that previously ranked FIRST at
score 4325 / size 2,136,746,229 is GONE from the candidate list entirely, and
the real signed 8K URL is rank 1. Before the fix the same page produced
"WINNER score=4325 size=2136746229 DIV class='content_download video_downloads'".

MISSION GATE on v3.66.1341:
  nubilefilms FAIL     8K(?):Large 7952x5304px -- the PHOTO-DIMENSION defect,
                       which is row 381 and is NOT in 1341. Note the real anchor
                       is now visible at rank 2 as 4K(4.0 GB):3840x2160 4K MP4
                       WITH a parsed size, so 381 should let it win outright.
  wowgirls    UNKNOWN  all 8 listed scenes already in history
  ultrafilms  PASS     done in 1.3 min, 3,355,129,815 bytes,
                       title='UltraFilms / Members / Movie / Playful Princess'
  MISSION FAIL

WOWGIRLS CANNOT BE MEASURED BY THE MISSION GATE TODAY and this is a real gap,
not a pass: its /updates/ listing is JS-driven, so pick_scene.py sees only the
first 8 scenes and all 8 are already downloaded. ?page=2, /page/2/ and /films/
all return the SAME 8 -- measured. That is precisely what row 374's in-GUI
crawler (merged at v3.66.1340) exists to solve, and wiring the mission gate to
it is the obvious next improvement. Until then the probe above is the
wowgirls evidence, and it is direct.

ROW 381 integrated as v3.66.1342 at 16:05, in verify.

## CHECKPOINT 2026-08-29 16:09Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1341 at 4c2e3d3f21c5
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 325 CLOSED on origin/main
  queue           43 spec row(s) not yet closed on main
  codex           8 live session(s); 121 worker worktree(s) on disk
  harness         11 bd-*.sh process(es) running
  host            test5 load average: 4.90, 13.09, 16.72

## CHECKPOINT 2026-08-29 16:14Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1342 at 34bfad02e545
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 326 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           8 live session(s); 121 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 8.48, 14.00, 16.36

## CHECKPOINT 2026-08-29 16:24Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1342 at 34bfad02e545
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 326 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           4 live session(s); 121 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 1.73, 3.35, 9.37

## 2026-08-29 16:30Z -- BOTH RANKING FIXES ARE DEPLOYED AND VERIFIED LIVE

v3.66.1341 (row 380, wrapper) and v3.66.1342 (row 381, photo pixels) are merged
and deployed on test6. Verified against the LIVE pages with the deployed code,
forced wide sweep, via bd-persist/scripts/probe_winner.py:

  wowgirls  breathtaking-debut   WINNER tag=A score=4325
            href=.../download/h1b620ff/7680x4320_60FPS.mp4   PASS
            (the div.content_download wrapper that ranked FIRST at size
             2,136,746,229 is now absent from the candidate list entirely)
  nubilefilms we-should-share...  WINNER tag=A score=2160 size=5368709120
            href=https://content2a.nubilefilms.com/exclusive/...   PASS
            (the 'Large 7952x5304px' photo caption that ranked FIRST is gone)

BOTH SITES NOW CHOOSE THE RIGHT LINK. nubilefilms STILL fails to download, and
that is a FOURTH, distinct defect, filed as row 384: the chosen href is a direct
media URL on another host and the click produces no Playwright download event,
while _do_download's direct-url path -- which exists precisely to skip
expect_download for such URLs -- did not claim it. Establish why before changing
anything.

MISSION GATE on v3.66.1342:
  nubilefilms FAIL     ranking correct, no dl event -> row 384
  ultrafilms  PASS     3.0 min, 7,288,076,829 bytes,
                       title='UltraFilms / Members / Movie / Naughty Girlfriends 2'
  wowgirls    UNKNOWN  listing exhausted AND the crawler surfaced nothing new
Three ultrafilms completions today at 3.3-7.3 GB prove the chain end to end on
the deployed binary; the failure is per-site, not global.

THE MISSION GATE NOW ASKS THE CRAWLER before returning UNKNOWN (row 374's
/api/discovery/scenes/start, newest_n=5 max_pages=3 max_scrolls=12). It still
returned UNKNOWN for wowgirls, which is the honest answer -- 8 scenes listed, 8
downloaded, and the crawler found no ninth.

LANE MECHANICS FIXED THIS STRETCH
  * batch width PINNED TO 1 and bd-width-restore.sh stopped and removed from the
    watchdog guard list. The ladder climbed to 2, batched 357+371, and BOTH were
    marked BLOCKED on rebase -- they had each re-frozen
    tools/decomp/import_graph_baseline.json, which conflicts "outside the
    append-only set". Two good rows lost to a batching choice, not to a defect.
  * tools/decomp/import_graph_baseline.json added to bd-unstale-generated.py's
    drop list: the integrator RE-DERIVES it and names the moved edges in the
    changelog, so a worker copy is redundant and only ever collides.
  * templates_snapshot_baseline.json was added to that list and then REMOVED
    again -- the integrate log shows it applied CLEANLY and only the import
    graph conflicted, so dropping it would have discarded row 371's measured
    --freeze for no reason. Re-frozen in place (91 elements, rollup f4f3609d).

ROW 371's AGENT reported an INCIDENT worth keeping: at ~15:29:47 it ran an
unscoped `pkill -f 'pytest.*loadfile'` to stop its own band, and a row380 pytest
started at 15:29:52. Whether it killed a predecessor is UNDETERMINED. It later
aborted a second cleanup after finding 8 pytest processes in its own candidate
list. Same self-match hazard as ps/grep; the lesson is unchanged and now has a
second instance.

## CHECKPOINT 2026-08-29 16:34Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1342 at 34bfad02e545
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 326 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           4 live session(s); 121 worker worktree(s) on disk
  harness         11 bd-*.sh process(es) running
  host            test5 load average: 3.25, 6.27, 8.14

## CHECKPOINT 2026-08-29 16:35Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1343 at 6a38abbea350
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 327 CLOSED on origin/main
  queue           44 spec row(s) not yet closed on main
  codex           4 live session(s); 121 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 2.25, 5.39, 7.71

## CHECKPOINT 2026-08-29 16:45Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1343 at 6a38abbea350
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 327 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           5 live session(s); 122 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 25.54, 14.87, 10.16

## CHECKPOINT 2026-08-29 16:52Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1344 at 859a8440de8e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 328 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           5 live session(s); 122 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 7.58, 10.77, 10.19

## 2026-08-29 17:00Z -- SIX MERGED, ROW 384 BUILT, THE BATCH COLLISION IS FIXED

MERGED THIS STRETCH: 1340 (374 crawler), 1341 (380 wrapper), 1342 (381 photo
pixels), 1343 (377 selftest states), 1344 (378 db connection lease).
IN VERIFY: 1345 = rows 357 + 371 BATCHED, 41 files, 5,475 insertions.
QUEUED: row 384.

THE BATCH COLLISION IS FIXED AND PROVEN. At 16:22 the ladder batched 357+371
and both were marked BLOCKED on rebase: each had re-frozen
tools/decomp/import_graph_baseline.json, which conflicts "outside the
append-only set". After adding that path to bd-unstale-generated.py's drop list
-- the integrator RE-DERIVES it and names the moved edges in the changelog -- the
SAME PAIR integrated cleanly at 16:54. The ladder's own promotion back to width
2 was therefore justified, and bd-width-restore.sh stays stopped only because
bd-night climbs on its own after clean merges.
INV_TAGS.md was also added: its .json sibling was listed and the .md was not,
and it was the only other real overlap between 371 and 378.

ROW 384 BUILT INLINE AND BAND-CLEAN.
  RED, pristine 6a38abbe: 15 failed -- _direct_media_route did not exist, and
       the precondition test says so BY NAME so the rest cannot assert
       vacuously about None.
  GREEN: 15 passed. Manifest neighbours, same tree: 46 passed.
  BAND 37 files + 2 gate tests: 1 failed, 422 passed -- the one failure is the
       expected import-graph edge for its own new test.
  REGEN COMPLETE, frozen baselines untouched.

THE BAND CAUGHT ME REPRODUCING THE DEFECT'S SHAPE. My first implementation
refused a manifest with its own `.endswith((".m3u8", ".mpd"))` tuple. TWO
existing gates refuse exactly that by name:
  "runner_transport.py carries streaming extensions as its own string
   constants: line 680: '.m3u8'  line 680: '.mpd'"
  "_direct_media_route reports 'no download fired' without ever CALLING
   is_streaming_url. hls_downloader owns that question ... A hint that merely
   says 'manifest' while the check is gone is a message promising an answer
   nobody computed."
It now ASKS hls_downloader.is_streaming_url through the same soft import the
PWTimeout branch uses, and declines the direct fetch if that module cannot be
reached -- falling back to the CLICK path, today's behaviour, rather than
guessing ffmpeg's work into httpx. This was twenty minutes after I wrote a brief
quoting A7's "every fix tends to reproduce the defect's shape".

CONTEXT HYGIENE: the row-371 agent's completed task kept firing "stale poller"
notifications, dozens of them, each costing context and carrying no
information. Killed with TaskStop on the task id -- NOT by pattern-matching
processes, which is what the agent itself had refused to do after finding 8
live pytest sessions in its own candidate kill list.

## CHECKPOINT 2026-08-29 17:02Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1344 at 859a8440de8e
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 328 CLOSED on origin/main
  queue           45 spec row(s) not yet closed on main
  codex           4 live session(s); 122 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 23.18, 12.84, 10.19

## CHECKPOINT 2026-08-29 17:08Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1345 at 0c31e3a316c6
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 330 CLOSED on origin/main
  queue           50 spec row(s) not yet closed on main
  codex           4 live session(s); 122 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 5.16, 12.54, 11.75

## CHECKPOINT 2026-08-29 17:19Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1345 at 0c31e3a316c6
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 330 CLOSED on origin/main
  queue           50 spec row(s) not yet closed on main
  codex           4 live session(s); 122 worker worktree(s) on disk
  harness         11 bd-*.sh process(es) running
  host            test5 load average: 3.50, 4.25, 7.60

## CHECKPOINT 2026-08-29 17:27Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           4 live session(s); 122 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 2.92, 6.56, 7.70

## CHECKPOINT 2026-08-29 17:37Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           49 spec row(s) not yet closed on main
  codex           4 live session(s); 122 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 1.81, 2.27, 4.73

## CHECKPOINT 2026-08-29 17:47Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           48 spec row(s) not yet closed on main
  codex           4 live session(s); 122 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 1.08, 1.33, 3.03

## 2026-08-29 17:55Z -- v3.66.1346 WORKS AND EXPOSED A CORRECTNESS DEFECT

DRAIN COMPLETE, 0 unmerged. Merged this run: 1340 (374), 1341 (380), 1342 (381),
1343 (377), 1344 (378), 1345 (357+371 BATCHED), 1346 (384). test6 deployed on
1346, 32/32 credentials, GET / = 200.

ROW 384 WORKS. The journal shows my new line BY NAME and the download completed:
  download: direct media href -> nfbusty_new_years_with_my_ex_3840.mp4
  [4fc6c70f][state] ... done: Saved: ... 5,102,802,950 bytes
nubilefilms had failed EIGHT times today and now downloads.

AND IT DOWNLOADED THE WRONG SCENE. history 121 requested
.../254796/seeing-red-s50e30 and saved nfbusty_new_years_with_my_ex_3840.mp4
from /exclusive/new_years_with_my_ex_with_octavia_red/. SEEN, NOT INFERRED --
operator's instruction, and it was the right one. The screenshot shows the page
carries its own six download tiers AND a Related Videos grid of ~25 scenes, each
card exposing its own direct .mp4 links: 159 media links on one page, SIX of
which belong to the requested scene. The page's own correct 4K link
(nubilefilms_seeing_red_3840.mp4) was present and BD did not take it.

detect.py sorts by (score, size) descending and has NO notion of "belongs to the
scene on this page", so a related scene with a larger file outranks the real one.
THE WRONG PICK IS PRE-EXISTING. Clicking a cross-host signed .mp4 fired no
download event, so it used to fail LOUDLY. Row 384 made the pick succeed, which
converted a visible failure into a SILENT WRONG FILE -- a row marked done, a
library title that reads correctly, and the wrong bytes. That is the worst shape
this project has, and my own fix produced it.

FILED AS ROW 388, ACTIVE. Do not fix it by reverting 384: a loud failure that
hides a wrong pick is not a fix.

OPERATOR RULINGS, all three taken:
  * downloads STOPPED on test6 (both queued sites stopped; running 0, waiting 0)
  * history 121 flagged needs_review with the measured reason, FILE KEPT
  * all 106 completed downloads audited for page/file mismatch

THE AUDIT, and my first instrument over-reported. A token-set comparison called
wowgirls wrong four times -- the page reads "dreaming-of-japan" and the file
"DreamingOfJapan", so concatenation means no token ever matches. Hardened with a
de-delimited containment test. Final: 15 MATCH, 4 MISMATCH, 87 UNKNOWN of 106.
Of the 4, THREE ARE FALSE POSITIVES confirmed by screenshot: teenmegaworld names
files studio_performer_resolution, so "After-shower satisfaction" correctly saved
TeenSexMania_Adell_3840x2160.mp4 -- the page shows performer Adell and studio tag
TeenSexMania. Only history 121 is genuinely mis-filed. The 87 UNKNOWN are bdseed
fixture rows and filthykings rows whose filename is literally "mp4.mp4"; UNKNOWN
is not a pass and those cannot be judged by filename at all.

SO THE DAMAGE IS ONE ROW, and it is flagged rather than hidden.

## CHECKPOINT 2026-08-29 17:57Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           51 spec row(s) not yet closed on main
  codex           4 live session(s); 123 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 3.89, 1.43, 2.08

## CHECKPOINT 2026-08-29 18:07Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           51 spec row(s) not yet closed on main
  codex           4 live session(s); 123 worker worktree(s) on disk
  harness         9 bd-*.sh process(es) running
  host            test5 load average: 21.56, 10.73, 5.62

## 2026-08-29 18:12Z -- THE FLEET IS 12 HOSTS, NOT 7, AND ROLES IS NOW THE SOURCE

I HAD THE WRONG DENOMINATOR ALL DAY. ~/.config/bd/hosts listed SEVEN machines.
The operator asked "what happened to .50-.54" and a scan found five more:
  bd  10.0.70.50  BD v3.66.1326      bd1 10.0.70.51  BD v3.66.1327
  bd2 10.0.70.52  bd3 10.0.70.53  bd4 10.0.70.54   -- 48cpu/344GB/2TB each,
  repo present at 4d636df, venv Python 3.12.3, no BD on 5555
Two of them were RUNNING BD twenty versions behind main and nothing was
watching them. The monitor's "fleet3/3" was never the fleet -- it counts hosts
with role `runner`, and it now reads 5/5 because the roles file changed.

~/.config/bd/roles IS NOW AUTHORITATIVE and hosts is DERIVED from it:
  integrator 1 (test5)  runner 3 (test2, test3, test6)
  deploy     5 (test, test4, test7, bd, bd1)   capacity 3 (bd2, bd3, bd4)
The derived hosts file therefore cannot list a capacity box as a deploy target,
which is the failure the old file invited.

A DIRECT CONFLICT WAS SURFACED RATHER THAN SILENTLY RESOLVED. roles carried an
operator ruling of 2026-08-28: "five fresh throwaway VMs ... NOT deploy targets
-- bd-fleet-deploy.sh must never touch them." The operator's 2026-08-29 answer
promotes bd and bd1 to deploy. Asked; today's ruling stands; the superseded
comment is rewritten in place so the file never states something untrue.

OPERATOR RULINGS TAKEN THIS ROUND
  * unpark ALL FIVE Ledger-31 rows (120, 122, 124, 126, 127), including the
    PostgreSQL cutover -- but RE-DERIVE each brief first: they were parked at
    v3.66.1195/1253 and main is v3.66.1347, ~150 versions later.
  * queue 243/244/245 too, but at the SAFEST POINT, with a checkpoint and full
    persistence BEFORE firing. This entry is that checkpoint.
  * row 245's premise is stale twice over: it cites a SIX-host dry-run at
    deployed HEAD 3372dbeb. The fleet is 12 and main is 1347. Re-derive it so
    its denominator is MEASURED from roles at decision time, never a literal.
  * row 370: re-file its login work as its own row, then re-open 370 with its
    actual subject unimplemented. Nothing lost; each row means what it says.
  * drain SMALLEST FIRST.
  * agents implement; I stay integrator (audit, unstale, gate refusals, merge,
    deploy). Implementation worktrees stay on test5 -- one writer, A4.
  * deploy-role hosts track main; capacity boxes get fetched to the CUT's SHA,
    which is what a band needs anyway. One rule per role.
  * the row-388 downloads hold covers EVERY host that can download: test6, bd,
    bd1, and test2/test3 once they get the real site config.
  * test2/test3 get test6's real 32-site config -- they are marked runner and
    were carrying sites_loaded=1, queue_depth=0, i.e. no load at all.
  * the band moves to bd2/bd3/bd4 via an OPT-IN WRAPPER that falls back to
    local when no capacity box is reachable, so a bad remote can never block a
    cut, and bd-verify-cut.sh is edited atomically because the lane runs it.

WHY THE BAND MOVE MATTERS BEYOND SPEED: it makes A6's "never edit a checkout
while an authoritative run uses it" STRUCTURAL rather than a rule I remember.
That exact overlap cost 26 minutes today on row 374, twice.

## CHECKPOINT 2026-08-29 18:18Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           50 spec row(s) not yet closed on main
  codex           4 live session(s); 123 worker worktree(s) on disk
  harness         11 bd-*.sh process(es) running
  host            test5 load average: 3.49, 6.15, 6.12

## CHECKPOINT 2026-08-29 18:28Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1346 at ba0744d9496c
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 331 CLOSED on origin/main
  queue           52 spec row(s) not yet closed on main
  codex           5 live session(s); 124 worker worktree(s) on disk
  harness         11 bd-*.sh process(es) running
  host            test5 load average: 9.89, 8.95, 8.59

## 2026-08-29 18:30Z -- THE FLEET IS WIRED UP, AND DEPLOY REPORTED OK ON A STALE TREE

REMOTE BAND PROVEN END TO END. bd2 ran 30 tests green at the exact cut SHA,
including row 380's and 384's own tests which exist only at that SHA -- so the
subject was proven, not assumed. Then bd-band-remote.sh ran 15 more through the
wrapper. Capacity boxes are 48cpu/344GB/2TB with the Playwright cache, ffmpeg
and a working pytest already present.

THE MECHANISM NOBODY HAD WRITTEN DOWN: capacity boxes clone from a LOCAL bare
repo at /home/mboyle/bd.git ON EACH HOST, not from GitHub. `git fetch origin`
there SUCCEEDS and leaves the box where it was; only the checkout fails, with
"reference is not a tree". The fix is to push the exact object over SSH first:
  git push ssh://mboyle@<ip>/home/mboyle/bd.git <sha>:refs/heads/main
bd-band-remote.sh does that, proves HEAD == the requested SHA before running a
single test, and exits 64 REMOTE-UNAVAILABLE so the caller falls back to local.
A verification lane that can refuse a good cut is worse than a slow one.

AND THAT SAME STALE MIRROR MADE deploy.sh LIE. bd and bd1 were deployed while
main was 1347 and both finished:
  [step 13] DEPLOY OK -- now running 3.66.1326 (aa08372da...)
  [step 13] DEPLOY OK -- now running 3.66.1327 (4d636df07...)
Both statements TRUE, both deploys WRONG: deploy.sh has no notion of an INTENDED
SHA, so it reported the version it landed and compared it to nothing. Filed as
row 391. After pushing main into each host's bare repo they redeployed to
v3.66.1346 for real.

DOWNLOADS HOLD VERIFIED on every host that can download:
  bd   10.0.70.50   v3.66.1346  vault UNINITIALIZED, no API token -- cannot download
  bd1  10.0.70.51   v3.66.1346  unlocked, 0 sites queued, running=0 waiting=0
  test6 10.0.70.249 v3.66.1346  0 sites queued, running=0 waiting=0
The hold is still RUNTIME ONLY and a restart can defeat it -- filed as row 390,
which requires a startup-checked hold that fails CLOSED.

TOOLS ADDED THIS ROUND, all archived to bd-persist/harness (142 files):
  bd-shoot.py         screenshot a members page + list its media links
  bd-band-remote.sh   run a band on a capacity box at a frozen SHA, fail-soft
  bd-row-audit.py     extended with C4/C5/C6 pre-flight (see below)

PRE-DISPATCH PRE-FLIGHT, wired into the drain. Three cuts refused today for
mechanical register/report defects, not for anything wrong with the work:
  C4 register prose citing a row main's register does not carry (1347, twice)
  C5 a report without two bullets of 20..300 ASCII chars (1346, twice: the
     bullets were 537/336/309 and only one was usable)
  C6 a regenerated artifact in the diff that is identical to main and will
     therefore conflict outside the append-only set (1346: FUNCTION_INDEX.md)
AND THE FIRST DRAFT OF THAT GATE HAD THE DEFECT IT EXISTS TO CATCH: C1 refused
row 389 because a documentation row carries no implementation file and no new
test. A whole class of valid row was outside its denominator. Doc rows are now
a named class, judged by bd-freshcheck; a merged row reports PASS with its merge
commit rather than "the diff is EMPTY".

ROWS FILED THIS ROUND: 388 (wrong scene, ACTIVE, agent running), 389 (CLAUDE.md
A7, in the lane), 390 (hold must survive a restart), 391 (deploy OK on a stale
origin). 385/386/387 filed earlier and still queued.

## 2026-08-29 18:40Z -- THE LEDGER-31 ROWS ARE EVIDENCE-BOUND, NOT CODE-BOUND

Re-deriving the five unparked rows first showed FOUR of them cannot be handed to
an implementer at all, because what they lack is real-world evidence:
  120 JW-TMPL       needs captures whose page host is behind AKAMAI/CLOUDFLARE;
                    the CDN topology is the acceptance criterion, not decoration
  122 P3-T12        needs a detector-cleared/resume event observed LIVE, and was
                    DELIBERATELY not closed by inferring resume from later auth
  124 FR-A6.2       says it outright: recognizers implemented and synthetically
                    tested, what is missing is real guided-capture corpus
  126 2c-DATA       re-capture COMPLETE; five selector kinds need review plus a
                    test against the LIVE DOM
An agent cannot manufacture any of that, and dispatching one would buy an hour
of it restating the row. OPERATOR RULING: run the captures FIRST, then dispatch,
so the rows arrive code-bound.

ROW 127 (PostgreSQL cutover) UNPARKED AND ITS BAR CHANGED BY THE OPERATOR: the
"operator soak of at least two weeks" is DROPPED; a green canonical full suite
post-cutover is now sufficient. Written into the brief as a CHANGE made by the
person entitled to make it, not as a quietly weaker bar -- CLAUDE.md forbids
weakening acceptance criteria to obtain green, and the next reader must be able
to see which of the two happened here.

## CHECKPOINT 2026-08-29 18:38Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1347 at 233be02c0223
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 332 CLOSED on origin/main
  queue           56 spec row(s) not yet closed on main
  codex           8 live session(s); 127 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 14.47, 14.96, 11.67

## CHECKPOINT 2026-08-29 18:38Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1347 at 233be02c0223
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 332 CLOSED on origin/main
  queue           57 spec row(s) not yet closed on main
  codex           8 live session(s); 127 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 18.78, 15.79, 12.06

## CHECKPOINT 2026-08-29 18:48Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1347 at 233be02c0223
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 332 CLOSED on origin/main
  queue           56 spec row(s) not yet closed on main
  codex           10 live session(s); 127 worker worktree(s) on disk
  harness         7 bd-*.sh process(es) running
  host            test5 load average: 36.91, 27.54, 18.99

## 2026-08-29 18:58Z -- THE CORPUS, AND A NEW STANDING AUTHORIZATION

OPERATOR GRANT 2026-08-29, STANDING, applies to EVERY task and row:
  * search the internet to find no-login sites and pull corpus/family data
  * pull tools and repos (GitHub, Google) that would assist or improve the app
  * IF a pulled dependency is actually implemented, UPDATE THE REQUIREMENTS
This is a real widening of scope and it is recorded here so it survives a
compaction. It does NOT widen the rules it sits inside: no formal test against
an authenticated site (A6), one writer (A4), and a pulled tool still has to be
checked against what already exists before anything is written.

THE CONSOLIDATED CORPUS, measured on bd2 from
~/CONSOLIDATED_corpus_FULL_v3_66_301.zip (194 MB, 427 files, 187 MB unpacked):
  238 .wacz captures + analysis/A2_scorecard_v300.json with 118
  OPERATOR-CONFIRMED family labels over 41 distinct hosts.
  CDN across all 238: cloudflare 49, akamai 19, fastly 11, none 173.
  Families over 118: videojs 39, native_custom 19, theoplayer 13, jwplayer 13,
  vidstack 12, then singletons. 7 thin.
  Labelled AND behind a CDN: 28 -- and ZERO of them jwplayer.
So row 120 was still corpus-bound WITH the corpus in hand, and that was
measured before asking for anything more.

FOUND AND CAPTURED THE MISSING INTERSECTION. www.nbcnews.com is Akamai
(CNAME -> www.nbcnews.com.edgekey.net) and /video serves jwplayer.js -- public,
no login. Captured with a new tool:
  bd-capture-url.py  ->  corpus-new/nbcnews_video_akamai_jw.wacz  842,559 bytes
  network_log_count 397, verify ok, resources 2
  jwplayer 6 hits, akamai 212, signed-style query params 18
  top hosts: media-cldnry.s-nbcnews.com 70, nodeassets.nbcnews.com 68
Row 120's gap is now filled with a real artifact rather than an argument.

I DID NOT PULL A CRAWLER, AND THAT WAS THE POINT. browsertrix-crawler was the
obvious pull for .wacz -- but BD ALREADY WRITES WACZ: bulk_downloader/
wacz_export.py has build_wacz_bytes/write_wacz, and session_capture.py has
SessionCapture + capture_via_cdp with capture-time redaction on by default. The
new tool is ~40 lines wiring those together. Reading the inventory first saved a
dependency, and the requirements file needs no change.

TWO GUARDS IN THAT TOOL EARNED THEIR PLACE IMMEDIATELY:
  * it REFUSES a url containing /members, /account, login or signin -- A6, and
    it passes no cookies and loads no profile so it cannot reach one by accident
  * it refuses to WRITE a wacz when the capture recorded zero requests. The
    first run did exactly that: the page loaded, the title was right, and the
    capture was empty because I had guessed the accessor name (to_dict rather
    than to_capture_dict). Without the guard it would have written an 842-byte
    lie that looked like corpus evidence.

THE ROW-COHERENCE GATE IS NOW AT THE REAL CHOKEPOINT AND PROVEN FIRING. It was
added to bd-night.sh and NEVER RAN -- bd-night does not launch chains, bd-drain.sh
does. Row 243 walked past it to PR #634 carrying SEVEN duplicate
_EXPECTED_DECLARED_GATE_COUNT assignments. PR closed, remote branch deleted,
gate moved into bd-drain.sh, and the log now shows
"row 243 REFUSED by bd-row-audit" on every attempt. The empty audit log was the
tell: a gate that has never written one has never run.

## CHECKPOINT 2026-08-29 18:58Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1347 at 233be02c0223
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 332 CLOSED on origin/main
  queue           56 spec row(s) not yet closed on main
  codex           10 live session(s); 127 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 27.84, 29.88, 25.12

## 2026-08-29 19:05Z -- FIVE NEW CAPTURES, AND THE CHALLENGE ROWS

NEW CORPUS, captured with bd-capture-url.py on bd2/bd3 in parallel, all
verify ok, archived to bd-persist/corpus-new:
  nbcnews_video_akamai_jw.wacz  822K  397 req  fam=jwplayer,hlsjs  cdn=akamai+cloudflare+fastly
  twitch_hlsjs.wacz             805K  385 req  fam=-               cdn=fastly
  ted_talks.wacz                730K  372 req  fam=hlsjs           cdn=cloudflare
  dailymotion.wacz              266K  149 req  fam=hlsjs           cdn=-
  archiveorg_item.wacz          137K   51 req  fam=jwplayer        cdn=-
ROW 120 IS UNBLOCKED: nbcnews is jwplayer behind Akamai, the intersection the
238-capture corpus did not contain. archiveorg is jwplayer with NO cdn, which is
the control that isolates the CDN variable -- worth having, and it was free.
ROW 124 (non-CF, non-video.js breadth) gains dailymotion (hlsjs, no cdn) and
archiveorg (jwplayer, no cdn). None of the five is videojs, which is the point.

CHALLENGE ROBUSTNESS, operator ruling 2026-08-29: strengthen the human-in-the-
loop path BD ALREADY OWNS rather than adding a bypass. Filed:
  393 a challenge is NAMED before it is handed off. Turnstile that self-clears,
      hCaptcha needing a human, a reCAPTCHA v3 SCORE WALL NO HUMAN CAN SOLVE, an
      age interstitial and a 429 are five situations with five correct
      responses, and BD currently spends the operator's attention identically on
      all five. Unknown stays UNKNOWN -- a wrong label is worse than none
      because it tells the operator to do the wrong thing.
  394 be a client that EARNS FEWER CHALLENGES: honour Retry-After, pace per host
      adaptively, reuse the session instead of re-earning it, use conditional
      requests. Triggering fewer challenges beats solving more, needs no third
      party and costs nothing. Before/after over a MATCHED window with load
      recorded, or it is not evidence.
  395 a solver service is OPT-IN and states its cost: off by default, per site,
      and the toggle itself must say that page content and challenge tokens
      LEAVE THE NETWORK, what a solve costs, and that many sites' terms prohibit
      it even for your own account. The important test is the negative control:
      with it off, instrument the egress boundary and prove ZERO calls.
Row 122's missing detector-cleared/resume event is the other half of 393 -- a
handoff that does not reliably resume wastes the operator every occurrence.

## CHECKPOINT 2026-08-29 19:09Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1347 at 233be02c0223
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 332 CLOSED on origin/main
  queue           56 spec row(s) not yet closed on main
  codex           10 live session(s); 127 worker worktree(s) on disk
  harness         6 bd-*.sh process(es) running
  host            test5 load average: 12.43, 20.79, 23.72

## CHECKPOINT 2026-08-29 19:19Z -- written by bd-checkpoint-write

MEASURED AT WRITE TIME (nothing here was passed in or copied from prose):
  main            v3.66.1347 at 233be02c0223
  integrator tree 0 dirty path(s), 0 unpushed commit(s)
  register        4 OPEN / 332 CLOSED on origin/main
  queue           56 spec row(s) not yet closed on main
  codex           8 live session(s); 127 worker worktree(s) on disk
  harness         10 bd-*.sh process(es) running
  host            test5 load average: 1.95, 8.53, 16.79
