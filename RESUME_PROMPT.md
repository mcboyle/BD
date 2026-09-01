Continue the BulkDownloader run. You are the integrator on test5 (10.0.70.164),
sole writer. GO QUIET IMMEDIATELY: 15-word status, 30-word updates, 30-char
notes on agent start/finish and row start/finish/merge/deploy. Matthew has
objected to narration three times. Detail goes to the checkpoint, not the chat.

READ FIRST, IN THIS ORDER. These are authority; your memory is not.
  /home/mboyle/bd-persist/OPERATOR_DECISIONS.md   <-- EVERY ANSWER HE HAS GIVEN.
      A question answered there is CLOSED. Re-asking it is the failure mode he
      objected to. If one truly needs revisiting, say what CHANGED.
  /home/mboyle/fleet-run-artifacts/2026-08-25/CHECKPOINT.md   read the last 200 lines
  /home/mboyle/bd-persist/README.md               what is archived, how to restore
  /home/mboyle/.config/bd/roles                   the 12 hosts and their roles
  /home/mboyle/bd-night-spec.txt                  the row list

NOTE ON THIS FILE: it lives at bd-persist/RESUME_PROMPT.md deliberately.
/home/mboyle/bd-resume-prompt.txt was OVERWRITTEN with an older 34-line version
at 19:23:41 on 2026-08-29, minutes after being written, by something I could not
identify -- bd-persist-loop.sh and capture_codex.sh both only copy home ->
archive, so neither explains it. Treat that path as unsafe for anything you care
about, and prefer this one. UNRESOLVED, stated rather than papered over.

MEASURE STATE, NEVER TRUST A WRITTEN NUMBER:
  git -C /home/mboyle/BulkDownloader fetch -q origin
  git -C /home/mboyle/BulkDownloader show origin/main:bulk_downloader/__init__.py | grep __version__
  bash /home/mboyle/bd-persist/verify.sh

START ANY PROCESS READING 0. Check with an ANCHORED match, never a substring:
  ps -eo args --no-headers | grep -cE '^bash (/home/mboyle/)?bd-night\.sh'
Start from /home/mboyle with:  setsid nohup bash <script> >/dev/null 2>&1 </dev/null &
  bd-night.sh  bd-watchdog.sh  bd-att-guard.sh  bd-autorebase.sh
  bd-persist-loop.sh  bd-checkpoint-loop.sh  bd-unstale-loop.sh
The watchdog revives the others; start it and it repairs the rest. Persist runs
every 1800s, checkpoint every 600s -- that is the operator's cadence, keep it.
EXACTLY ONE of each. Two watchdogs ran on 2026-08-29 and the older one held a
stale guard list in memory and revived processes that had been deliberately
killed. Collapse duplicates by PID, oldest first -- BUT CHECK ppid FIRST.
bd-night.sh re-execs itself, so the anchored count reads 2 BY DESIGN: a parent
and its own child (1642998 -> 1937575 on 2026-08-29). Killing the "older
duplicate" there kills the parent and ORPHANS THE RUNNING DRAIN. Only PIDs that
are not each other's ancestor are true duplicates.
Do NOT start bd-codex-pool.sh or bd-codex-refill.sh. Codex is out of credits
until 2026-09-03 19:27; every dispatch fails instantly.
Re-arm the two session monitors:
  Monitor: bash /home/mboyle/bd-progress.sh
  Monitor: tail -n 0 -F /home/mboyle/fleet-run-artifacts/2026-08-25/FINISH.log \
             | grep -E --line-buffered 'MERGED|DEPLOY|REFUSED|BLOCKED|drain starting'

THE MISSION, and it is the only thing that matters: log into a members site,
find the highest-resolution file, download it, record the name the SITE shows.
FOUR defects in that chain were fixed on 2026-08-29 and ALL FOUR ARE MERGED AND
DEPLOYED: v3.66.1341 wrapper-outranks-leaf, 1342 photo-pixels-read-as-a-
resolution, 1346 direct-media-href-clicked-not-fetched, 1348 wrong-scene
(work_affinity). test6 (.249), bd (.50) and bd1 (.51) all run 3.66.1348.
1348 IS VERIFIED AS OF 2026-08-29 19:40, BY THE PAGE ITSELF, NOT BY BD.
  requested   .../members/content/item/0cbaa49c-lexi-s-wild-dreams
  page tiers  5, all under content id 0cbaa49c; highest 7680x4320_60FPS.mp4
  8K filename= lexi-s-wild-dreams_lexi-montana_7680x4320.mp4  (the SITE's name)
  on disk     that exact name, 7,347,597,744 bytes, transfer_mode http
  affordances 76 on the page, 5 media, ALL ONE SCENE -- no Related-Videos grid
So: highest tier, right scene, site's own filename, real bytes. 1341/1342/1346/
1348 all confirmed on one live download.
STILL OWED: the nubilefilms shape. Ultrafilms has no Related-Videos grid, so
this run cannot retire the 159-link page that produced the 5.1 GB wrong-scene
file. Give the first nubilefilms completion the same page-vs-filename check.
The hold is RUNTIME ONLY and a restart defeats it; row 390 fixes that (agent
running 2026-08-29 19:51).

AND THE INSTRUMENT LIED FIRST. bd-shoot.py printed "MEDIA LINKS ON PAGE: 0" for
that page: its filter was a[href] matching content2a|\.mp4, which is
nubilefilms' shape, while ultrafilms publishes tiers as DIV[data-href]. A
filtered zero read as a page fact, on the one tool whose whole purpose is to not
be inferred from. bd-shoot.py now prints ANCHORS / AFFORDANCES / MEDIA as three
separate numbers, sweeps the attribute set detect.py trusts, and says UNKNOWN
when the affordance total is zero. Copied to test6 and bd-persist/harness.

TRAPS THAT COST REAL TIME ON 2026-08-29 -- every one actually happened:
 * A GATE IN THE WRONG FILE IS NOT A GATE. bd-row-audit was wired into
   bd-night.sh and NEVER RAN once: bd-night does not launch chains, bd-drain.sh
   does. Row 243 reached an open PR carrying SEVEN duplicate
   _EXPECTED_DECLARED_GATE_COUNT assignments. The empty audit log was the tell.
 * A RUNNING SCRIPT KEEPS ITS OLD INODE. Editing bd-night.sh took effect only
   after the process was restarted, 31 hours later. Use bd-edit.py (atomic
   rename) AND restart the loop, or the edit is decorative.
 * DEPLOY OK CAN BE A LIE. bd/.50 and bd1/.51 clone from a LOCAL bare repo at
   /home/mboyle/bd.git ON EACH HOST. `git fetch origin` there SUCCEEDS and
   leaves the box where it was. deploy.sh reported "DEPLOY OK -- now running
   3.66.1326" while main was 1347. Push the object first:
     git push ssh://mboyle@<ip>/home/mboyle/bd.git <sha>:refs/heads/main
   Filed as row 391.
 * deploy.sh ALSO misdiagnoses a 503 as a missing SPA bundle when the real
   cause is the restart locking the vault. Read /api/health's `degraded` field,
   then run bd-vault-unlock.sh 10.0.70.<n>. Five occurrences. Row 285.
 * test6 and the other BD hosts listen on 5555, NOT 5000.
 * `git -C <repo>` always. Half of one session's false readings came from
   running git in /home/mboyle, which is not a repository.
 * A ps/pkill pattern matches the shell CARRYING it, including the ssh command
   and the shell that wrote the script. Anchor on '^bash /path' or use PIDs.
   An agent's unscoped `pkill -f 'pytest.*loadfile'` may have killed another
   worker's run; it could not be determined afterwards.
 * DON'T INFER, SCREENSHOT. The wrong-scene defect was invisible in every
   instrument and obvious in one screenshot: a Related Videos grid of ~25
   scenes each publishing its own .mp4 links, 159 media links on one page. The
   same rule then CLEARED three rows a filename audit had wrongly called
   mis-filed. Tool: bd-shoot.py. Now written into CLAUDE.md A7.
 * CHANGELOG bullets must be 20-300 chars, pure ASCII, >=2 of them in the last
   400 lines of the worker report, or the integrator aborts the whole cut.
 * A generated artifact in a worker diff that is IDENTICAL to main conflicts
   outside the append-only set. Reset it to HEAD and let the integrator regen.
 * Register prose citing a row main's register does not carry fails the
   dangling-reference gate. bd-row-audit's C4 catches it pre-dispatch now.

TOOLS -- READ THE INVENTORY BEFORE WRITING ANYTHING. 142 in
~/bd-persist/harness, 88 in /scripts. The ones that matter most:
  bd-row-audit.py     pre-dispatch coherence gate, C1-C6, wired into bd-drain.sh
  bd-band-remote.sh   run a band on bd2/bd3/bd4 at a frozen SHA; exits 64 when
                      none is free so the caller falls back to local
  bd-shoot.py         screenshot a page + list its media links
  bd-capture-url.py   capture a PUBLIC no-login page to .wacz (refuses /members)
  bd-vault-unlock.sh  a restart LOCKS the vault; this re-arms it
  bd-unstale-generated.py  drop stale generated artifacts from a worker diff
  bd-report-changelog.py / bd-register-open-row.py / bd-deref-register.py
  bd-edit.py          atomically replace a RUNNING script
  bd-rpy              run local Python on a remote host, no heredoc quoting
BD ALREADY WRITES .wacz (wacz_export.py) and already has captcha_relay,
challenge_classify, runner_challenge, cloak, curl_cffi and playwright-stealth.
Check before pulling anything; browsertrix-crawler was the obvious pull and was
not needed.

WHERE THE WORK IS
  MERGED 2026-08-29: 1340 crawler, 1341 wrapper, 1342 photo-pixels, 1343
  selftest-states, 1344 db-lease, 1345 (357+371 batched), 1346 direct-media,
  1347 CLAUDE.md A7, 1348 wrong-scene.
  OPEN: 385 (in the lane -- a REAL crawler defect, not a flaky test: an
  exhausted newest-N budget leaked a pager request and checkpointed on the
  wrong page), 386 (agent running: the mission gate in CI), 387, 390, 391,
  392, 393, 394, 395, 396, 397, plus briefs for unparked 120 and 127.
  120 is UNBLOCKED -- bd-persist/corpus-new/nbcnews_video_akamai_jw.wacz is
  jwplayer behind Akamai, the intersection the 238-capture corpus lacked.
  122/124/126 remain EVIDENCE-BOUND: capture first, then dispatch.
  392 reports UNKNOWN by design -- its submit.py has unresolved stash conflict
  markers at lines 941-1002 and both sides are real work.
  PARKED DELIBERATELY: 243, 244, 245 -- queue only at the safest point, after a
  checkpoint and a full persist. 243 currently FAILS bd-row-audit with seven
  duplicate pin assignments, and that refusal is correct.

THE ERROR PATTERN WORTH INHERITING: nearly every mistake in this run came from
believing an instrument instead of the system -- a gate in a file that never
runs, a deploy that reports the version it landed and compares it to nothing, a
filename audit that called three correct rows wrong, an audit log that was empty
because the check had never executed. When something reports zero, or reports
success, ask the system itself before forming a hypothesis. And when a result
looks wrong, or looks right for a reason you cannot name, LOOK AT THE PAGE.

================================================================
SESSION-END STATE 2026-08-30 01:20 (supersedes older status above)
================================================================
main v3.66.1355. Fleet deployed 1353 (1354/1355 undeployed). Downloads LIVE.
FULL detail + recovery refs: fleet-run-artifacts/2026-08-25/CHECKPOINT.md tail.

DO FIRST when resuming, in order:
1. FIX THE MAINLINE REGRESSION: test_v3_66_144_template_subsystem::
   test_reviewed_template_feeds_modal_flow is RED on main (row 126's reptyle
   template merged red; its band missed the test). Blocks 399/402/120 + all cuts.
2. HARNESS IS NEUTERED ON PURPOSE -- do NOT undo before row 407:
   bd-autorebase.sh = `exec sleep 86400`; bd-night.sh rebase step replaced with
   non-destructive bd-unstale (no original bd-night backup exists; archived copy
   IS the neutered form). batch-cap=1. These prevent the worktree-clobber that
   dropped committed candidates (incident 407). Original autorebase backed up as
   harness/bd-autorebase.sh.ORIGINAL-prewent-neuter.
3. PARKED, worktrees intact (recovery refs in CHECKPOINT): 399 (commit 72805b22,
   clean, blocked only by #1), 402 (commit 064700c3, needs one rebuild-on-main),
   120 (RE-DISPATCH, golden vs 124; brief row120.txt), 122 (REFUTED, already on
   main, close via 406).
4. Row 406 = register reconciliation (close 244/245/285/97/113/212 stale,
   PARKED->CLOSED 120/122/124/126, 127 unpark). 407 = the rebase fix.
5. DEPLOY 1355: push object to bd/bd1 mirrors, scripts/deploy.sh --expect-commit
   <sha> for them, bd-fleet-deploy.sh for GitHub hosts, bd-vault-unlock.sh after.
NOTHING WAS LOST. Every parked row's work is in a commit or on main.

REBOOT PLANNED (2026-08-30): disk survives, /tmp wiped (nothing critical there).
Parked work is COMMITTED+TAGGED reboot-proof: recover/row399-onmain=9392a4cb,
recover/row402-onmain=d45c9205 (use THESE, not the stale-base ones). Neutered
bd-night.sh/bd-autorebase.sh are on disk and survive. AFTER REBOOT: verify neuters
(grep -c 'sleep 86400' bd-autorebase.sh; grep -c bd-unstale-generated bd-night.sh
= 1 each), start ONE bd-watchdog.sh (it revives the rest), keep batch-cap=1, fix
test_v3_66_144 regression first. Full steps: CHECKPOINT.md "REBOOT PLANNED" tail.
Bundle of all parked commits: bd-persist/RECOVERY/parked-work.bundle.

CORRECTION: regression node full name = tests/test_v3_66_144_template_subsystem.py::test_reviewed_template_feeds_modal_flow_into_learned_dl (line 156 assert). Resume 399/402 from recover/row399-onmain=9392a4cb, recover/row402-onmain=d45c9205. Bundle: bd-persist/RECOVERY/parked-work.bundle (restore-tested).
