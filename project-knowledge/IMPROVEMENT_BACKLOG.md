# IMPROVEMENT BACKLOG -- machine-visible since v3.66.1052

ASCII-only.

## Why this file exists

It was **item 85 of itself**. The backlog was produced by a review that called
it "the most valuable artifact of that session", and it then lived in an
untracked text file in the operator's home directory that no gate read. CLAUDE.md
section 1 states the consequence in general terms:

> "A DEFERRAL THAT LIVES ONLY IN PROSE HAS NOT BEEN DEFERRED -- IT HAS BEEN
> DROPPED. The ITEM LEDGER works for exactly one reason: a test reads it."

The proof arrived on 2026-08-12: a session with full authority to work the
backlog could not find it, and the operator had to say where it was. That is the
whole failure mode -- not that the list was wrong, but that nothing could see it.

`tests/test_v3_66_1052_the_backlog_is_machine_visible.py` now reads this file.

## THIS NUMBERING IS NOT THE ITEM LEDGER'S

These IDs are the BACKLOG's own and they are a **different namespace** from the
`ITEM LEDGER` in `SESSION_CARRY.md`. Backlog **21** is the row whose subject is
ledger item **48**. Three numbering schemes were once reconciled at 15.35/15.36
after exactly this confusion; do not merge them, and when citing a row say
"backlog N" rather than "item N".

## Format, which the gate depends on

One row per item: `| <id> | <status>[ @<evidence>] | <text> |`

`OPEN` carries no evidence. `CLOSED` and `MOOT` must carry it -- a version
(`@1049`) or a commit -- because a close nobody can check is a claim, not a
record. Status is one of `OPEN`, `CLOSED`, `MOOT`.

`MOOT` is deliberately distinct from `CLOSED`: it means the SUBJECT went away
rather than the work being done. Collapsing the two would let a disappeared
problem read as a solved one.

| id | status | item |
| --- | --- | --- |
| 1 | CLOSED @1043 | bd-run: promote the scratchpad wrapper to a tracked tool |
| 2 | CLOSED @1053 | generalise "never filter at capture time" beyond `head` (partly done) |
| 3 | OPEN | every long job writes a heartbeat file |
| 4 | CLOSED @1053 | ban estimated progress in status reports |
| 5 | OPEN | per-run artifact directories keyed by run id. bd-run HALF DONE @1060 (<label>-<runid>.log + a symlink alias, prune counts real files only). STILL OPEN: capture.sh writes a fixed /tmp/bd_capture referenced by five test files, so consecutive captures overwrite each other -- a gate change and its own cut |
| 6 | CLOSED @1040 | bd-reap, shipped as `bd-jobs reap` |
| 7 | CLOSED @1040 | remote job registry, shipped as `bd-jobs` |
| 8 | CLOSED @1040 | ssh wrapper recording the remote PID, shipped as `bd-jobs run` |
| 9 | CLOSED @1037 | deploy preflight refuses if pytest is running on the target |
| 10 | CLOSED @1037 | deploy.sh restores the service on a post-stop failure |
| 11 | CLOSED @1043 | bd-fleet: version/tree/service/load/jobs for all hosts |
| 12 | CLOSED @1039 | fleet-wide deploy, scripts/deploy_fleet.sh |
| 13 | OPEN | dedicated agent SSH key, SCOPED. Note: adding a second unrestricted key closes nothing -- the value is the restriction and retiring the broad key, so a partial version of this row is worse than leaving it open |
| 14 | MOOT @1051 | reimage .249 -- the clean-host role moved to .84/test7 instead, and the proof was retaken there |
| 16 | CLOSED @1044 | record machine load with every suite result |
| 17 | CLOSED @1043 | bd-ab, the A/B harness |
| 18 | CLOSED @1043 | bd-ladder, the parallel prefix ladder |
| 19 | CLOSED @1044 | auto-size -n from nproc and record the value |
| 20 | CLOSED @1053 -- was ALREADY in s1 before this cut; verified verbatim, not re-added | state the denominator in every reported count, mechanically |
| 21 | CLOSED @1049 | ledger item 48's SECOND MECHANISM: the leaker left sys.modules wiped, orphaning import-time bindings |
| 22 | OPEN | fix the remaining sys.modules leakers. The "11 orphaners" figure is UNVERIFIED -- bd-modwatch reports 0 for those files in a two-file harness. Re-derive before acting; 1 fixed at @1049 |
| 23 | CLOSED @1044 | record the --dist loadfile worker assignment (recorded, NOT pinned) |
| 24 | CLOSED @1044 | per-worker chain logging |
| 25 | OPEN | quarantine or annotate known-rotating tests |
| 26 | OPEN | a vacuous-test detector (hard: "can this assertion fail?") |
| 27 | OPEN | cross-test over-sensitivity controls when the subject is a fixture |
| 28 | CLOSED @1055 | a WRITE-recorder: the filesystem analogue of the socket recorder |
| 29 | CLOSED @1038 | ast.parse -> import-and-resolve, recorded in CLAUDE.md s6 |
| 30 | CLOSED @1036 | record load-dominance |
| 31 | CLOSED @1036 | record "a failed deploy leaves the service down" |
| 32 | CLOSED @1036 | parameterise -n by host rather than the container's 4 |
| 33 | CLOSED @1058 | EXERCISE THE CANDIDATE WORKFLOW: run a tip on .85 before a merge. DONE ONCE, for real: v3.66.1058's tip ran on test4 in a detached worktree before merge -- symbol asserted present per section 2b, absolute venv interpreter, deployed tree untouched, 157 passed exit 0. The worktree (not the deployed checkout) is the shape that works |
| 34 | CLOSED @1059 | regenerate the socket recorder's blind-spot counts rather than hardcoding them. Derived at read time, denominator named beside each count, UNKNOWN when the tree cannot be read; ~0.26s once per process via PEP 562 __getattr__. The retired literals are deliberately not repeated here |
| 35 | OPEN | pre-commit self-check: tree clean, no orphans, services healthy, no scratch in tests/, ledger current |
| 36 | CLOSED @1035 | socket recorder directory leak (744 dirs) |
| 37 | CLOSED @1035 | new gates wired into CI (isolation shard + _DECLARED) |
| 38 | CLOSED @1035 | the 11th axis-6 gate recorded in section 4's table |
| 39 | CLOSED @1035 | ledger currency: data plus a gate that catches staleness |
| 43 | CLOSED @1043 | bd-gc: reap /tmp/bd-* litter across hosts |
| 44 | CLOSED @1035 | "creating a path is a promise to remove it" (s0) |
| 45 | CLOSED @1035 | 1034/1031 added to a shard and to _DECLARED |
| 46 | OPEN | make _DECLARED derivable, or gate an undeclared repo-wide gate |
| 47 | CLOSED @1035 | "a gate CI does not run is a gate that does not exist" (s7) |
| 48 | CLOSED @1035 | closing an item must update the newest ledger -- gated |
| 49 | CLOSED @1035 | no ledger declares OPEN what the inventory closed -- gated |
| 50 | MOOT @1051 | re-role or reimage .249 -- superseded by the role moving to test7 |
| 51 | CLOSED @1055 | .85 carried /etc/sudoers.d/90-mboyle-codex AND 90-mboyle-nopasswd with byte-identical rules; the codex one is vestigial from the retired CODEX_HANDOFF era and the other three hosts have only the latter. Removed, visudo -c OK, sudo -n verified, backup at /root/sudoers-codex-backup-2026-08-12 |
| 52 | CLOSED @1048 | decide whether streamlink belongs in system_deps.sh -- it does; nothing had ever installed the preferred backend |
| 53 | CLOSED @1036 | capture.sh cannot see cross-file state leaks (s7) |
| 54 | CLOSED @1058 | add a pytest-tests/-shaped lane to capture, or state the blind spot there. Took the SECOND option: capture.sh prints a blind-spot block after the verdict and tee's it to 11_BLIND_SPOTS.txt, naming cross-file state leaks (with the v3.66.1034 measurement) and timezone defects. An actual pytest-shaped lane remains unbuilt and would be a gate change |
| 55 | CLOSED @1036 | audit beats recollection (s1) |
| 56 | CLOSED @1036 | post-cut residue audit, folded into 55 |
| 57 | CLOSED @1053 | check any instrument you build for its OWN blind spots |
| 58 | CLOSED @1053 | when the contract records a mistake class, check the change in front of you BEFORE committing |
| 59 | CLOSED @1053 | measure the cost of anything added to every test run |
| 60 | CLOSED @1053 -- was ALREADY in s0 before this cut; verified verbatim, not re-added | treat "I read the contract" as worth nothing |
| 61 | CLOSED @1038 | never retype an anchor containing punctuation (s6) |
| 62 | CLOSED @1038 | edit scripts mutate in memory, write once (s6) |
| 63 | CLOSED @1038 | sed -i is not an applied-check (s6) |
| 64 | CLOSED @1053 | verify CI status by the status column, not a positional field |
| 65 | CLOSED @1053 | prefer doc-only cuts as a shape (cheap band, no source risk) |
| 66 | CLOSED @1037 | validate a stub against the real command's output format (s6) |
| 67 | CLOSED @1037 | beware shell tail-call exec in fixtures (s6) |
| 68 | CLOSED @1039 | a floor means ADD to it, never drop from it (s4) |
| 69 | CLOSED @1037 | /proc/exe is useless for venv processes |
| 70 | CLOSED @1037 | state a detector's blind spot IN THE CODE |
| 71 | CLOSED @1042 | deferrals must land somewhere machine-visible, not in prose |
| 72 | CLOSED @1042 | verify SUMMARY/VERDICT lines specifically |
| 73 | CLOSED @1039 | a tracked example file uses documentation-range data |
| 74 | CLOSED @1042 | TEST THE SEAM, not just the component |
| 75 | CLOSED @1042 | a clean battery is not coverage evidence -- check the primary path |
| 76 | CLOSED @1042 | a tool acting on other hosts must verify it CAN record before acting |
| 77 | CLOSED @1042 | when a function has N outcomes, assert each is reachable |
| 78 | CLOSED @1047 | bd-jobs run --script FILE |
| 79 | CLOSED @1047 | bd-mutate line-buffered |
| 80 | CLOSED @1046 | GATE: no test may hand its own file to a subprocess pytest |
| 81 | CLOSED @1046 | GATE: conftest defines each pytest hook name at most once |
| 82 | CLOSED @1046 | GATE: no test writes real tool state (/tmp/bd-jobs) |
| 83 | CLOSED @1046 | ANSI audit -- bd-ab and bd-ladder were BLIND, both fixed |
| 84 | CLOSED @1046 | "a green band at one -n does not retire a cross-file failure" (s4) |
| 85 | CLOSED @1052 | make this backlog machine-visible -- this file, and the gate that reads it |
| 86 | MOOT @1051 | run bd-gc --apply -- the 2026-08-12 rebuild collected the litter; bd-fleet measures 2/0/0 against a pre-rebuild 1349/435/68 |
| 87 | CLOSED @1050 | the killswitch auto-cycle thread hung full-suite runs: an unguarded daemon thread performed a real tunnel stop/start 30s later and killed an xdist worker |
| 88 | CLOSED @1054 | bd-jobs reap kills the registered pid but not its children -- reaping a driver shell orphaned the hung pytest beneath it, and the orphan then blocked every deploy |
| 89 | OPEN | the capture corpus is not restored by deploy.sh and does not survive a rebuild; two hosts silently had zero files in captures/ while analytics reported an empty store |
| 90 | CLOSED @1054 | a dead xdist worker hangs the suite unboundedly -- pytest-timeout runs INSIDE the worker, so nothing bounds a run whose worker died. Needs a whole-run cap outside the process |
