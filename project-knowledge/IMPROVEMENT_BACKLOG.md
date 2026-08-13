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

## Priority order, set 2026-08-12 at dc5943f (v3.66.1066)

Re-derive before working any of these -- section 1: roughly half of a stale
register's OPEN items turn out to be closed or mis-scoped, and this list is a
snapshot of a judgement, not a measurement.

1. ~~**22 + 48 + 91 + 94 as ONE campaign.**~~ -- ALL FOUR CLOSED. 22 and 94 at
   @1068/@1069, 91 at @1067+@1069. **91 sat OPEN through the two cuts that
   fixed it** and was still directing this list to "START WITH 91" when
   re-derived at @1072; the instrument had been reading comment-stripped source
   since @1067 and 1034 had carried a real restore since @1069. That is
   section 1's own statistic -- roughly half a stale register's OPEN items are
   already closed -- landing on the register that exists to prevent it. What
   survives the campaign: **92** and **93**, both still OPEN and both latent
   shapes rather than live failures.
2. ~~**46**~~ -- CLOSED @1072. It had failed EIGHT times by then, not four: the
   1062/1064/1067/1068 gates shipped on 2026-08-12 were the fifth through
   eighth, added by the session that wrote this very line. The successor is
   **99**, which is what the fix could not reach.
3. **89** -- the capture corpus. Silent, and recurs on every rebuild.
4. **95 + 98** -- both cheap, both residue-class.
5. **97** -- makes 96 true for a FRESH host rather than for these four.
6. **3, 35, 5-remainder, 25, 27** -- real, not urgent.
7. **13, 26.** 26 is the trap: seductive, because it is section 0 mechanized,
   and the likeliest to ship a confidently wrong number everything downstream
   inherits.

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
| 13 | OPEN | dedicated agent SSH key, SCOPED. STILL OPEN ON PURPOSE: the identity half is staged, the RESTRICTION half is not, and the row stays OPEN because the restriction is the whole value. Note: adding a second unrestricted key closes nothing -- the value is the restriction and retiring the broad key, so a partial version of this row is worse than leaving it open  STAGED 2026-08-13 on operator instruction, additive only, because the retiring half can lock an unattended session out of the fleet and block everything after it: an ed25519 key commented `bd-agent-2026-08-13-UNRESTRICTED-see-backlog-13`, fingerprint SHA256:WZs/boseZi0qIhTmKJzRfenEfLfpydU1PgY7zcYxoyY, APPENDED (never rewritten) to test4/test6/test7 authorized_keys, each backed up to authorized_keys.pre-bd-agent-2026-08-13 first. PROVEN BOTH DIRECTIONS, because "the new key works" and "I did not break the old one" are different claims: the new key authenticates alone under IdentitiesOnly=yes on all three, and the pre-existing key still authenticates on all three. test5 DELIBERATELY NOT TOUCHED -- it does not accept the existing agent key either, so adding one there would create new REACH rather than a new IDENTITY. THE ROW'S OWN WARNING STANDS and this closes nothing on its own; all it buys is that agent access is now DISTINGUISHABLE from the operator's in the logs. INCIDENTAL FINDING: the fleet's authorized_keys are NOT UNIFORM -- before this, test4 carried 5 keys, test7 3 and test6 2, with `administrator@bittorrent` and `administrator@BattleStation` present on test4 and test7 and absent on test6. Nothing in the tree records why, and docs/repo/FRESH_HOST_BRINGUP.md is the runbook that would have to |
| 14 | MOOT @1051 | reimage .249 -- the clean-host role moved to .84/test7 instead, and the proof was retaken there |
| 16 | CLOSED @1044 | record machine load with every suite result |
| 17 | CLOSED @1043 | bd-ab, the A/B harness |
| 18 | CLOSED @1043 | bd-ladder, the parallel prefix ladder |
| 19 | CLOSED @1044 | auto-size -n from nproc and record the value |
| 20 | CLOSED @1053 -- was ALREADY in s1 before this cut; verified verbatim, not re-added | state the denominator in every reported count, mechanically |
| 21 | CLOSED @1049 | ledger item 48's SECOND MECHANISM: the leaker left sys.modules wiped, orphaning import-time bindings |
| 22 | CLOSED @1069 | fix the remaining sys.modules leakers. RE-DERIVED AT RUNTIME @1068 and the answer is THREE, not eleven or thirteen: of the 14 files the STATIC census listed, bd-modwatch in per-file mode found only 3 that actually orphan the module table, and 2 of those drop exactly `bulk_downloader.push`, which ZERO files bind at import time -- harmless. The one real leaker was `tests/test_v3_66_1034_guards_survive_a_module_wipe.py` (263 dropped, 5 swapped, including `bulk_downloader.app`). FIXED @1069 with a module-scoped save/restore so the wipe stops at its own file; the wipe itself stays, because the file exists to prove the guards survive one. Defining pairing 1034 -> 780 went 7 failed/12 passed -> 19 passed; the whole 8-file victim population passes at 154. Every earlier count was of a static heuristic that over-reports BY DESIGN, never of a leak |
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
| 46 | CLOSED @1072 | make _DECLARED derivable, or gate an undeclared repo-wide gate | DERIVABLE IS THE HALF THAT DOES NOT WORK, and that is a measurement rather than a judgement: against the eight files that actually went undeclared (944, 947, 1031, 1034, 1062, 1064, 1067, 1068), an AST census over real call nodes catches 3 of 8 on a `git ls-files` argument and 4 of 8 if you also match code naming repo infrastructure -- the second widening the candidate pool from 34 files to 136, so it costs a 124-entry exemption list to buy one hit. 947, 1031, 1067 and 1068 carry NO structural signal separating them from a feature test. Shipped instead: every tracked `tests/test*.py` declares a module-level `BD_GATE_SCOPE` or sits in the frozen 1314-entry `tests/gate_scope_baseline.txt` (may only shrink, no regenerator by design); `"repo-wide"` requires `_DECLARED`, which the union assertion forces into a shard. 6/6 mutants caught. THE WORST FINDING WAS INCIDENTAL: the `gates` job runs ZERO pytest and `test_v3_66_939` was in no shard, so from v3.66.939 to v3.66.1071 the gate against a suite falling out of every shard had itself fallen out of every shard. It and the four 2026-08-12 gates are now wired in |
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
| 89 | CLOSED @1083 -- PARTIAL, and the remainder is an operator decision | the capture corpus is not restored by deploy.sh and does not survive a rebuild; two hosts silently had zero files in captures/ while analytics reported an empty store  CLOSED @1083 for the half a walk can fix: the SILENCE. `scan_captures` skips a root with `if not ddir.is_dir(): continue`, so an absent corpus and an empty one were both `total: 0` -- which is why two hosts read as an empty store rather than a missing one. `scan_captures_summary` now reports `roots`, `roots_missing` and a four-valued `corpus_state` (present / empty / absent / partial); `partial` exists because that is the state that actually occurred and collapsing it into either neighbour loses it. 4 mutants, 4 caught, including the over-sensitive direction (a fresh install must read `empty`, not raise an incident). NOT CLOSED: nothing RESTORES the corpus. It is gitignored data, deploy.sh moves code, and a backup policy is an operator decision rather than something a walk can invent |
| 90 | CLOSED @1054 | a dead xdist worker hangs the suite unboundedly -- pytest-timeout runs INSIDE the worker, so nothing bounds a run whose worker died. Needs a whole-run cap outside the process |
| 91 | CLOSED @1067+@1069 -- re-derived and corrected at @1072; the row had sat OPEN through two cuts that fixed it | THE ITEM-48 RATCHET EXEMPTS ITSELF. `_module_wipe_leakers()` in `tests/test_v3_66_1034_guards_survive_a_module_wipe.py` matches its `restores` regex against the WHOLE FILE, so 1034 scores as restoring on its own PROSE -- its regex source literal and an assertion message quoting the restore call. Measured @1062: budget 13, found 13, and 1034 absent from its own census while being the one leaker causing live failures. CLAUDE.md section 0's comments-are-in-the-denominator trap, inside the gate written for this class; two independent agents' classifiers were fooled by the same prose. Fix: assert over comment-stripped source (tests/shell_source.py is the mechanized equivalent for shell)  MEASURED at v3.66.1072, both halves: the INSTRUMENT was fixed @1067 -- `_module_wipe_leakers` now calls `python_code_only` and classifies over comment- and string-stripped source; and the SUBJECT was fixed @1069 -- 1034 carries a real `sys.modules.update(saved)` in code. So the census excluding 1034 is now CORRECT rather than fooled, and the assertion message that used to answer for it is invisible to the classifier. Census size 13. Recorded because this row is section 1's own statistic landing on the backlog: it read OPEN while the fix had shipped, and a session working the priority order would have re-fixed it |
| 92 | OPEN | A CSRF MINT/CHECK BINDING ASYMMETRY is why the module-wipe class produces 403s specifically. `bulk_downloader/app.py:814` `_csrf_key` is module-level, so a fresh module EXECUTION mints a new key; `_check_csrf` reads it EARLY from the module dict frozen at the victim's collection time, while `bulk_downloader/app_csrf.py:16-19` and `app_pair.py:17-20` resolve `_csrf_token_for` LATE via importlib at call time. conftest's `_GUARD_REPATCH` covers three registered guards and `bulk_downloader.app` is outside that denominator. Recorded @1062; the FIX is NOT designed -- note CLAUDE.md's v3.66.1024 warning that a conftest guard in this exact area already fought a shipped position once |
| 93 | OPEN | THE VICTIM POPULATION for the CSRF split, recorded so the next occurrence is recognised rather than re-investigated: 8 tracked test files hold a module-scope `bulk_downloader` import AND send an X-CSRF-Token -- test_auth_throttle, test_secret_display_never, test_t44_request_replay, test_t6_login_security, test_v3_66_38_pwmgr_hardening, test_v3_66_43_pwmgr_remainder, test_v3_66_709_automation_gui_contract, test_v3_66_780_config_key_write_parity. Two independent AST censuses agree. Measured @1062 |
| 94 | CLOSED @1068 | bd-modwatch answered a DIFFERENT QUESTION depending on how it was invoked, and said nothing about which. `--all` planned one group per FILE; explicitly-named files collapsed into ONE group, so a batch of N could only ever report 0 or 1, could never name the offender, and a wipe in one file offset by an import in another netted out CLEAN. The verdict then called groups "file(s)". THE ORIGINAL ROW SAID THE BATCH "reports 0" -- re-measured @1068 it reports 1, so that specific number did NOT reproduce; the defect is the silent change of question, which is worse and explains the same disagreement. Fixed: per-file by default, `--together` opt-in for the co-batched question a real --dist loadfile run answers, and the verdict names its mode. Backlog row 22's "bd-modwatch reports 0" evidence is therefore VOID as written |
| 95 | CLOSED @1080 -- PARTIAL, and the remainder is named | LEAKED TMPDIRS ARE A POPULATION, NOT ONE FILE -- re-measured on test5 2026-08-12 after a capture round: **2906 entries under /tmp totalling 357MB**, from at least three tracked tests that call `tempfile.mkdtemp()` and never remove the result: `tests/test_phases_195_199.py` (531 dirs, prefix `bd-phases-test-`), `tests/test_repos_F2_F9.py` (468, `bd-repos-test-`) and `tests/test_v3_45_8_macro_replay.py` (288, `bd-macro-replay-`). One directory per band run per file, on every host, growing forever. CLAUDE.md section 0 already records this exact shape at 744 directories and states the rule -- creating a path is a promise to remove it -- so the fix is `tmp_path` or an explicit cleanup, and the DENOMINATOR is every mkdtemp in tests/, not the three named here  CLOSED @1080 by fixing the ROOT rather than the call sites: 579 mkdtemp sites exist in tests/ and 366 pass NO PREFIX, so 6793 entries (38%) could never be attributed to a test by name. mkdtemp resolves through `tempfile.tempdir`, so tests/_tmproot.py points it at one per-process root and removes it at session end -- covering every call site, present and future. MEASURED both directions on a full suite: /tmp grew 40 with the mechanism on, 1917 with it off, 15777 passed either way. THE ROW'S OWN FIGURES WERE A 5.3x UNDERCOUNT (they came from bd-fleet's two-glob litter column, fixed @1078). WHAT IS NOT CLOSED: the accumulated backlog already on the four hosts (~13000-18000 entries each) is untouched and is the operator's to reclaim with bd-gc; the box-only gate fails while it stands, correctly, and says so  THAT REMAINDER IS NOW CLOSED, MEASURED 2026-08-13 at f154aef after the operator's clear-and-reboot: test4 64, test6 68, test7 65, test5 83 entries under /tmp, against the gate's 5000 threshold. `test_the_fleet_leaks_no_tmpdirs` ARMED with FLEET_TMP_CHECK=1 passes on all four -- run on EACH host, because its predicate is `len(list(Path("/tmp").iterdir()))` on whatever machine executes it, so one host's pass is a fact about that host and not about the fleet its NAME claims. The gate is correct and its name overstates its denominator; that is worth one line and not a cut. This retires the operator-bound half of 95 |
| 96 | CLOSED @1065 | PROVISIONING IS NOT UNIFORM AND NOTHING INSTALLS THE DIFFERENCE. test4 alone has PostgreSQL + `MOD3_PG_TEST_DSN` (18 tests: mod3 cutover/rehearsal/pg_isolation/shadow_read/dual_write) and `bd_dev_inspect` (3 tests: redactor seam), so it runs 21 tests the other three SKIP -- 15722 pass / 5 skip against 15701 / 26 in the 2026-08-12 captures. Measured @1062: ZERO mentions of postgres, MOD3_PG_TEST_DSN or bd_dev_inspect in scripts/provision_test_host.sh, scripts/lib/system_deps.sh or install_linux.sh, and no installer for either anywhere in the tree. Consequence: a clean-host bring-up proof goes green partly by SKIPPING what is missing, and ledger item 31's EXIT-3 can only ever be exercised on one box | CLOSED for THIS fleet at @1064 (shared scripts/lib/dev_capabilities.sh sourced by both provisioners) and @1065 (the DSN is persisted on the already-serving path too); postgres, bd_dev_inspect and ~/.config/bd/mod3.env now present on all four hosts, DSN answering. See row 97 for the part that is NOT closed |
| 97 | OPEN | `bd_mod3_pg_provision` CONFIGURES postgres but does not INSTALL it -- it refuses with "postgresql-common absent (no pg_ctlcluster)" on a host where postgres is not already present. True in the cloud image, FALSE on bare Ubuntu, so on a fresh host the step WARNs forever and row 96 is closed for this fleet only. Measured @1065: test5/test6/test7 each needed `apt-get install postgresql` by hand first. Either the library installs it, or the WARN text must say "install postgresql first" instead of naming an internal binary |
| 98 | CLOSED @1079 | EDITING test5 DURING ITS OWN CAPTURE DRIFTS THE GRAPH PIN. test5's working tree IS the deployed tree, and capture step [2b] compares a source-derived graph hash against a pin written at deploy time -- so four uncommitted files turned a healthy capture into CAPTURE VERDICT: FAIL (graph exit=1) @1063, with unit 15709/0/0/26 and live 34/2/0 underneath it. Not a defect in the gate; the gate was right. Candidate fix: capture.sh refuses, or warns loudly, when `git status --porcelain` is non-empty -- it already knows it is running against a repo  CLOSED @1079: scripts/lib/tree_state.sh reports three states and capture.sh refuses a DIRTY tree at preflight, naming the paths, saying why it matters here, and warning off the stash-mid-run repair that makes it worse. A LIBRARY because backlog 35 asks the same question before a commit. UNKNOWN is NOT refused: making it fatal broke 13 tests that build a synthetic capture directory, which was the honest signal that a non-repo run cannot produce this hazard. Override CAPTURE_ALLOW_DIRTY (unprefixed on purpose), which REPORTS before honouring itself |
| 99 | CLOSED @1082 | TWENTY-SIX PRE-POLICY TEST FILES MAKE A REAL `git ls-files` CALL AND ARE IN NO CI SHARD. Measured at v3.66.1071 by AST over call nodes (not grep: `subprocess.run(["git","ls-files",...])` puts the literal inside a STRING, so a comment-stripped scan sees 3 and a docstring-inclusive grep over-reports). Several are named by CLAUDE.md section 4's own axis-6 table as repo-wide gates: `test_deploy_manifest_stays_retired`, `test_task_tracker_stays_retired`, `test_codex_handoff_stays_retired`, `test_gitignore_rules_actually_match`, `test_history_columns_go_through_migrations`, `test_playwright_engines_single_source`, `test_v3_66_820_share_tools_saw_no_session_keys`, `test_v3_66_944_static_kb_manifest_describes_the_tree`. The @1072 BD_GATE_SCOPE policy does NOT reach them -- they sit in the frozen baseline, exempt by construction, which is the price of adopting the policy without classifying 1314 files in one cut. Deciding each is a CI BUDGET question (the gates-job comment's one-minute rule is already breached at ~140s), so it is the operator's, not a mechanical sweep. Next step is to time the 26 and propose a shard split, not to bulk-add them  CLOSED @1082 on operator authorization (a CI budget change is a build change). RE-DERIVED at HEAD the population was 24, not 26 -- two had been declared by @1072 and @1080 in the interim, which is section 1's verify-then-act paying for itself. All 24 TIMED individually (196s total, all passing) and split across five shards drawn from measured time rather than count, per the @939 precedent: four balanced at ~33s each and `test_desandbox_tool_verifiers` alone at 65s, because no split puts every lane under the one-minute budget while that file stays whole and trimming a shard's list is forbidden. Classified rather than bulk-added: every one asserts an invariant over the TREE (tombstones, anti-duplication ratchets, denominator gates), which is exactly what CI's file-independent lane is for. NOT DONE, deliberately: they keep their `gate_scope_baseline.txt` entries rather than gaining `BD_GATE_SCOPE` markers -- _DECLARED is what CI reads, and marking 24 files is a mechanical cut of its own |
| 100 | OPEN | THE @1079 TREE GUARD CHECKS PREFLIGHT ONLY, so it cannot see the failure it was written for. `capture.sh` refuses a tree that is DIRTY WHEN THE RUN STARTS (scripts/lib/tree_state.sh, backlog 98) and that is the whole check -- a tree that goes dirty DURING the run passes it. That is exactly what invalidated test5 at the 1082 capture round: nine files edited mid-run, step [2b]'s graph pin drifted against a source-derived hash, and the capture reported `graph exit=1`. The guard shipped HOURS BEFORE that happened and could not have caught it. THE SHAPE GENERALISES: a gate whose denominator is one INSTANT cannot answer a question about an INTERVAL -- section 0's denominator rule in the time dimension rather than the set dimension. Candidate fix: record the tree sha at preflight and re-check it immediately before the graph gate, naming the paths that moved; tree_state.sh already computes both halves, so this is a second call site rather than a new predicate. NOT a candidate: checking only at the end, which spends a forty-minute run to report something knowable at the start |
| 101 | OPEN | `unittest.mock.patch.dict(sys.modules, ...)` RESTORES THE DICT TO ITS ENTRY SNAPSHOT, so any module FIRST IMPORTED INSIDE THE BLOCK IS DELETED ON EXIT. Nobody writes that deletion and no gate sees it. It poisons any identity-keyed lazy cache whose OWNER survives while its SUBJECT does not. MEASURED at v3.66.1085: httpx's module-global HTTPCORE_EXC_MAP survived while httpcore was evicted, the next import produced a second httpcore module object, and every isinstance() in that map failed for the rest of the worker process -- so httpx re-raised raw httpcore errors through the branch it marks `# pragma: no cover`. That reached production as test6's only capture failure at v3.66.1083. THE ASYMMETRY IS REQUIRED: evicting owner AND subject together is self-healing, because re-importing the owner resets the cache and it rebuilds from the live subject. DENOMINATOR, measured at f154aef: 28 call sites across 6 tracked test files use the idiom. v3.66.1085 closed the httpcore INSTANCE by pre-importing it in tests/conftest.py so it is in every later snapshot; THE CLASS IS OPEN, and the next victim is whichever lazily-imported module some future patch.dict block touches first. A general fix is NOT designed. Candidates: a lint forbidding the bare idiom (fights 28 shipped call sites); a helper that snapshots and restores by NAME rather than by dict identity; or a conftest hook that diffs sys.modules across each test and fails on a net DELETION. The third is closest to a real gate, but its cost is paid once per test, ~15600 times per capture, and section 2 rule 6 requires that cost be MEASURED before it is added |
