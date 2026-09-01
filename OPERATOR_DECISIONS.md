# OPERATOR DECISIONS -- ANSWERED, DO NOT ASK AGAIN
Every ruling Matthew gave in the 2026-08-29 session. A question answered here is
CLOSED. Re-asking wastes his time and is the thing he explicitly objected to.
If a decision needs revisiting, say WHY it needs revisiting and cite the change
in circumstances -- do not present it as an open question.

## STANDING AUTHORITY
- Merge and deploy on green WITHOUT asking. NEVER deploy test5 (integrator).
- NEVER send a push notification, for any reason, including a host being down.
- Status answers 15 words max; updates 30 words max. Detail goes to the
  checkpoint, not the transcript. He has objected to narration THREE times.
- Quiet mode is the default working state. 30-char updates on agent start/finish
  and on row start/finish/merge/deploy.
- Ask questions interactively as they come up -- but only genuinely open ones.
- SEARCH THE INTERNET freely to find no-login sites and pull corpus/family data.
- PULL TOOLS AND REPOS (GitHub, Google) for any row if they assist or improve
  the app. If a pulled dependency is actually implemented, UPDATE REQUIREMENTS.
  Check what BD already has FIRST -- browsertrix-crawler was the obvious pull
  for .wacz and was NOT needed, because wacz_export.py already writes them.

## FLEET -- 12 HOSTS. ~/.config/bd/roles IS AUTHORITATIVE; hosts is DERIVED.
  integrator test5 .164          -- sole writer, never a deploy target
  runner     test2 .95, test3 .80, test6 .249
  deploy     test .83, test4 .85, test7 .84, bd .50, bd1 .51
  capacity   bd2 .52, bd3 .53, bd4 .54  -- NO BD, bands/captures/agents only
- bd and bd1 were PROMOTED to deploy targets on 2026-08-29. This SUPERSEDES the
  2026-08-28 roles comment saying these five VMs must never be deployed to.
- bd2/bd3/bd4 may be WIPED AND REBUILT FREELY.
- Deploy-role hosts track main; capacity boxes get fetched to the CUT's SHA.
- The monitor's "fleet N/N" counts RUNNER hosts, not the fleet.

## THE ROW-388 DOWNLOAD HOLD
- Downloads STOPPED on every host that can download, until 388 lands and is
  verified: test6, bd, bd1, and test2/test3 once they get real sites.
- Deploy each merge anyway; downloads stay stopped.
- History row 121 (wrong scene, 5.1 GB) FLAGGED needs_review, FILE KEPT.
- The hold is runtime-only and a restart defeats it -- row 390 fixes that.

## WORK ORGANISATION
- Agents implement; I stay integrator (audit, unstale, gate refusals, merge,
  deploy). Implementation worktrees stay on test5 -- one writer, A4.
- THREE agents at a time on test5. More is unsafe while bands run there: at
  load ~29 row 388's capture-parity verdict came back UNRESOLVED. Once bands
  run remote on capacity boxes, higher counts become safe.
- Drain SMALLEST FIRST, except row 386 goes RIGHT AFTER 388.
- Batch width: the ladder may climb; 357+371 batched successfully at width 2
  once import_graph_baseline.json was dropped from worker diffs.

## ROW RULINGS
- 243, 244, 245: QUEUE them, but at the SAFEST POINT, with a checkpoint and
  full persistence BEFORE firing. 245's six-host premise is STALE -- re-derive
  its denominator from roles at decision time, never a literal.
- 370: its 45 KB of login work was MIS-FILED. Re-filed as row 392; 370 returns
  to unimplemented. Row 392's submit.py carries UNRESOLVED stash conflict
  markers (lines 941-1002) -- both sides are real work and both must survive.
- 120/122/124/126: EVIDENCE-BOUND, not code-bound. Run captures FIRST, then
  dispatch. Row 120 is now UNBLOCKED (nbcnews capture, jwplayer+akamai).
- 127 (PostgreSQL cutover): UNPARKED, and the TWO-WEEK OPERATOR SOAK IS DROPPED
  by the operator who set it. A green canonical full suite post-cutover is now
  sufficient. Record it as a CHANGED bar, not a quietly weaker one.
- 369: NOT the collision it looked like. It is purely additive -- it keeps
  resolve_password_state and adds an atomic path. Not blocked.
- Challenge robustness: strengthen the human-in-the-loop path BD ALREADY OWNS.
  Classification first (393), then be-a-better-client (394). A third-party
  solver service MAY be wired (395) but OFF BY DEFAULT, per site, with the data
  egress and cost stated at the toggle.
- 396 (learned path), 397 (sibling flake): filed on worker recommendation.

## VERIFICATION
- Bands move to capacity boxes via bd-band-remote.sh, OPT-IN and FAIL-SOFT: it
  exits 64 when no box is free and the caller runs locally. A capacity box that
  is down must never refuse a good cut. Wired into bd-verify-cut.sh.
- Persist every 30 minutes; checkpoint every 10 minutes or after any merge.

## 2026-08-30 22:5xZ -- batching restart, four rulings

Asked interactively after the three batching defects were fixed RED-first
(bd-persist/harness/tests/test_batching_safety.py; 4 fail on the pre-fix
originals under BD_HARNESS_HOME, 5 pass live).

1. BATCH WIDTH: set night/batch-cap to 2 AFTER row 399 merges -- stepped, not
   jumped. Require a full band per batch. Demote on the first real cross-row
   failure. Promote to 3 only after two consecutive clean width-2 batches.
   The ladder still has no automatic promotion path and must not gain one.
2. BURST CANDIDATES (rows 097, 379, 212, 387, 404, 383): REVALIDATE FIRST.
   They were dispatched off base de6240f4, which is now two releases stale.
   Re-run bd-row-audit and each row's focused evidence against current main
   before integrating; then integrate fastest-first.
3. ROW 407 (rebase safety): BUILD IT NEXT. It was one of the 12 burst workers
   that never finished. Until it lands, the neutered rebase step is the only
   thing preventing another worktree clobber.
4. AUTOMATION: DECIDE AFTER A CLEAN PASS. bd-night and bd-autorebase stay
   neutered. Run one supervised width-2 pass, then choose.

## 2026-08-30 23:1xZ -- three more rulings

5. THE THREE PRE-EXISTING MAIN FAILURES BECOME ROWS. Found by row 399's band,
   each reproduced on clean main bc6fc43d:
   - test_row390_download_hold_survives_restart::test_health_reports_the_hold_
     distinctly_from_idle -- health returns degraded credential_vault_
     uninitialized and downloads_allowed False while the hold is CLEAR.
   - test_v3_53_phase6.py -- 15 failures, all "RuntimeError: Flask server did
     not come up".
   - test_t3_t4_wired.py -- 4 failures from a worktree with no
     frontend/node_modules; the gate asserts vitest exists but nothing
     provisions it in a fresh worktree.
   File all three as OPEN rows with evidence; queue behind current work.
6. ROW 390 SEMANTICS: SEPARATE THE TWO STATES. downloads_allowed reflects the
   DOWNLOAD HOLD only; vault state gets its own field. Conflating them makes a
   locked vault look like an operator hold -- the same confusion row 408 just
   fixed in deploy.
7. ROW 387: THE INTEGRATOR RESOLVES THE REBASE by hand. v3.66.1360 added
   tests/test_register_append.py to a CI shard while 387 rewrites that
   gate-shard census test. Both edits are additive; reconcile, then revalidate
   before integrating. No re-dispatch.

## 2026-08-31 -- STANDING: continually improve the harness

Operator, 2026-08-31: "Continually improve and optimize your harness, tools,
integrator, processes, etc." This is standing authority, not a one-off task.
Treat harness and process defects found in passing as work to fix, not merely
to report. Same rules apply: RED-first where a test can exist, back up the
original into bd-persist/harness before editing, keep one writer, and never
re-enable neutered automation without reverifying why it was neutered.

## 2026-08-31 -- git maintenance and 1363 sequencing

8. GIT MAINTENANCE: archive everything, then let git's own auto-gc reclaim.
   The 19 stale .git/worktrees/*/gc.log locks that were blocking auto-
   maintenance are removed. No manual `git prune` was run: the repo holds 290
   live worktrees and 140 of them carry uncommitted tracked changes.
   FULL ARCHIVE FIRST, at bd-persist/RECOVERY/worktree-archive-20260831T004012Z:
   worktrees.tsv (290), uncommitted.tsv (140), a diff+status pair for each of
   those 140, 208 detached heads pinned as refs/archive/detached/<pathhash>,
   and all-refs.bundle (34M, 695 heads, verifies complete).
9. ROW 407 WAITS FOR 1362, THEN REBASES. Its worktree sat on 1361, so writing
   the 1363 trio before 1362 merged would anchor the CHANGELOG on the wrong
   previous release. One candidate per version; no stale anchor.

## 2026-08-31 00:5xZ -- acceleration rulings (ultracode session)

10. BATCH WIDTH 3 IMMEDIATELY: {097,379,212} then {404,387,407} then 383
    alone (383 shares runner.py with shipped 399 work; it goes last and gets
    a fresh band on its final base). Full band per batch, demote on first
    real cross-row failure.
11. PRECUT OVERLAPS CI: push + open PR, run precut while CI runs, merge only
    when BOTH are green. A precut failure means deleting the remote branch
    before any retry of that version (serial-pipeline hazard, accepted).
12. CI RESTRUCTURE CUT GOES FIRST, ahead of the queue -- it multiplies every
    later cut. A5 boundary: split/rebalance shards only, never trim a test or
    a gate; the shard-census gate must stay green with the same denominator.
13. ULTRACODE ON, all four workflow applications authorized: adversarial
    verification of agent outputs, register-truth audit (row 411), parked-row
    evidence sweep (120/122/124/126/127 + 243/244/245/285, read-only),
    codebase defect hunt feeding new rows.

## 2026-08-31 03:5xZ -- OVERNIGHT AUTHORITY (operator going to bed)

14. FULL AUTONOMY: integrate the queue, merge AND deploy on green
    (CI + band + precut). Park anything needing a ruling; report at wake.
15. ALL 31 CONFIRMED DEFECTS: file every one through bd-register-append, and
    FIX AS MANY AS POSSIBLE overnight. TEN OR MORE agents in parallel, working
    the whole list, each in its own isolated worktree. Integrator stays sole
    writer; agents never push/merge/deploy/touch the register.
16. ROWS 122 AND 126: operator GRANTS AUTHORITY to run the live authenticated
    captures overnight, on any host of the integrator's choosing, any site.
    DOWNLOADS RUN NORMALLY -- no hold. Close the rows on the real artifacts.
17. ROWS 243 AND 245: do NOT close as moot. RE-DERIVE FRESH IDENTITIES against
    the current post-reboot state and KEEP THE ROWS OPEN, so the underlying
    concern survives the loss of the original evidence. 244 still needs its own
    provenance adjudication.
18. ROW 124: UNPARK. Five real guided captures arrived after the park was set,
    so the facts changed. Re-derive acceptance against those captures.
19. ROWS 127 AND 285: do both overnight. 127 as a normal cut under the changed
    bar (soak dropped, a green full suite suffices); 285 closes on a real
    verified deploy to test4 with health and version proof.
20. BATCH WIDTH: GO TO 5. The operator overrode the stepped recommendation.
    Quality gates and the full band per batch remain binding; demote on a real
    cross-row failure as the ladder already does.
21. MUTANT ANCHOR FRAGILITY: file the row AND build the pre-push anchor check.
22. FAILURE POLICY: FIX FORWARD. On an unhealthy host or a broken main after a
    merge, diagnose and ship a corrective cut rather than rolling the fleet
    back. A4 still binds: never rewrite a merged commit; the correction is a
    new cut. Preserve the failing evidence before remediating (A6: a failed
    deploy is not a no-op).
23. AUTOMATION: RE-ENABLE BOTH bd-night AND bd-autorebase once row 407 merges,
    on the strength of its per-SHA replay protection. Before flipping either,
    reverify why each was neutered (the 2026-08-30 worktree clobber: six
    duplicate watchdogs revived bd-autorebase and the rebase step hard-reset
    six worktrees) and confirm 407's mechanism actually closes that path.

## 2026-08-31 -- bd-anchorcheck.py built (ruling 21)

Pre-push mutant-anchor check, at /home/mboyle/bd-anchorcheck.py and persisted
to bd-persist/harness. Answers ONE cheap question -- does every tracked mutant
anchor still occur exactly once -- in about a second over 1,169 anchors across
212 specs. It deliberately does NOT judge whether a new anchor is legitimate:
tests/test_row357_mutant_anchors_are_not_fragile.py owns that, and a second
authority could disagree with the first.

RED provenance is HISTORICAL, not synthetic. Both real 2026-08-31 breaks are
replayed by its test suite: 9e408dd1 (row310 M5 vs the ci.yml shard split) and
the commit before 726d547e (N12 vs the bd-jobs _ssh_argv rewrite). Each FAILS
at the broken tree naming the exact spec, mutant and file, and each PASSES at
its fix. Tests: bd-persist/harness/tests/test_anchorcheck.py, 10 passed,
including zero-specs, missing-subject, malformed-spec and invalid-regex all
returning CANNOT-EVALUATE rather than OK.

STILL TO DO: wire it into toolchain/bin/bd-precut so the refusal is automatic
rather than remembered. That is a repository change and needs its own cut.

## 2026-08-31 -- bd-retrio.py built (the recurring trio collision)

/home/mboyle/bd-retrio.py, persisted to bd-persist/harness. The release trio
makes EVERY stacked cut collide: __init__.py, the pin, the CHANGELOG anchor,
plus the generated pair regenerated from them. It happened three times on
2026-08-31 alone (1364 under 1363, 1365 under 1364, 1365 again under 1366).

The one thing it exists to stop is RETYPING the CHANGELOG entry. It recovers
that entry from the pre-rebase commit BYTE-FOR-BYTE and rewrites only its
version header, then re-anchors it on main's current head entry. Retyping a
punctuation-sensitive header is how an anchor goes quietly wrong, which is
why CLAUDE.md forbids it.

It refuses rather than guesses: not mid-rebase, a conflict outside the trio,
a version that already has an entry, or an entry it cannot slice all return
CANNOT-EVALUATE. It does not continue the rebase, stage, commit, or push --
the integrator reads the result first, and the generated pair is REGENERATED
by bd-regen-order rather than merged, because a derived file has no merge.

Tests: bd-persist/harness/tests/test_retrio.py, 4 passed 1 skipped. Its first
run found a real bug: the tool assumed PIN_INDEX.json always exists on main
and crashed when it did not, which would have refused a resolvable rebase.

## 2026-08-31 -- AUTOMATION NOT RE-ENABLED, and why (ruling 23)

Ruling 23 authorized re-enabling bd-night AND bd-autorebase once row 407
merged, AFTER confirming 407's mechanism actually closes the clobber path.
407 is merged (v3.66.1366) and deployed. THE MECHANISM IS NOT WIRED IN.

MEASURED: 407 ships four repository scripts -- bd_candidate_replay.py,
bd_candidate_adopt.py, bd_integration_verdict.py, bd_watchdog_identity.py.
Grepping every ~/*.sh and ~/*.py for those names returns NOTHING. The
destructive path is unchanged: bd-autorebase.sh -> bd-rebase ->
bd-rebase-all.sh, which does stash push -u, checkout --detach "$MAIN",
stash pop. Re-enabling would have restored the 2026-08-30 sequence verbatim.
407 is NECESSARY BUT NOT SUFFICIENT, which the merge alone does not show.

DONE INSTEAD: bd-rebase-all.sh now pins the candidate HEAD as a real ref
(refs/candidate-safety/row<N>/<utc>) before anything destructive runs, and
detects the actual incident shape -- an EMPTY stash means the work is
COMMITTED, not dirty, so a detaching checkout would move away from it and pop
would restore nothing. In that case it rebases the COMMIT, and refuses with a
restore if the worktree ends up empty against main.

Tests: bd-persist/harness/tests/test_rebase_all_preserves_candidates.py, 4
passed. RED against the pre-fix original: 2 fail, and the decisive one reports
"the committed candidate was discarded. file_present=False pinned=[]" -- the
incident reproduced in a sandbox. The dirty-worktree path still rebases
normally (negative control) and an already-current worktree is untouched.

STILL NOT SUFFICIENT FOR RE-ENABLING. The pin makes the path RECOVERABLE, not
safe. Full safety is routing the replay through 407's bd_candidate_replay.py,
which never touches the source worktree at all. Automation stays neutered
until that wiring exists and is proven.

## 2026-08-31 12:0xZ -- three more rulings

24. ROW 439 (HLS egress): CONFINE, DO NOT REFUSE. Extend the netns_exec_argv
    confinement already applied to yt-dlp and gallery-dl to the ffmpeg
    subprocess, so the transfer keeps working AND stays inside the tunnel.
    The agent's fail-closed refusal is NOT merged as-is: it would stop every
    VPN-tunnel-mapped site's HLS downloads, because ffmpeg has no SOCKS
    support and BD's tunnels expose socks5://127.0.0.1:PORT. Work preserved
    at tag recover/row439-ffmpeg-vpn (04336ae7) as the reference RED and the
    per-arm enumeration; the seam it introduced is still the right shape.
25. EXTENSION_VAULT RENAME-ASIDE (sibling of row 432): file it AND fix it
    tonight. bulk_downloader/extension_vault.py:86 carries the identical
    defect on vault_tokens.json -- a damaged file is renamed to
    .corrupt-<ts> rather than preserved. Same treatment: preserve in place,
    distinct unreadable state, fail closed through every caller.
26. SEQUENCING: one cut per high-severity fix, merged as each goes green.
    They touch overlapping core files (runner.py, runner_transport.py,
    download_hold.py, secrets_store.py), so batching would make one band
    judge them together and make a single bad fix unrevertable alone.

## 2026-08-31 12:3xZ -- rulings 27-29

27. BATCH BY FILE: one agent per FILE, never per finding, so no two agents
    touch the same file. runner.py (3 findings) and runner_transport.py (3)
    each go to ONE agent as a coherent cut; the other 19 findings sit in 19
    distinct files and run fully parallel. Rolling refill: ~8 in flight,
    dispatch a replacement as each lands, until all 25 are done.
28. UNPARK ALL FOUR remaining PARKED rows (120, 122, 126, 127) via successor
    OPEN rows plus a pointer amendment on each original -- the 124/452 shape.
    bd-register-amend refuses status changes by design; that refusal is the
    anti-laundering property, so successors are the sanctioned route.
29. ROW 244: RATIFY the 2026-08-26 agent dedupe as authoritative provenance.
    It is the only record that exists and the identities it adjudicated were
    destroyed by the 2026-08-30 reboot, so re-derivation is impossible.

## 2026-08-31 -- bd-edgecheck.py (the import-graph friction)

/home/mboyle/bd-edgecheck.py, persisted to bd-persist/harness. Operator asked
whether the import-graph gate should be RETIRED. Measured answer: no, narrow
the ceremony, keep the gate.

Over the 4,141-edge baseline at v3.66.1371:
    test -> product     2443   59%
    product -> product  1698   41%
    test -> test           0
    product -> test        0
The gate's own docstring names its hazard as accidental coupling / lazy-
accessor sprawl, which lives ENTIRELY in the 41%. Four cuts on 2026-08-31 were
refused by it and every refusal was a new test file importing its own subject:
four CI round trips, zero coupling defects found.

bd-edgecheck classifies a cut's new edges and declares only the routine half.
A product->product edge REFUSES and demands --allow-coupling with a stated
reason. An edge touching tests/ on the far side refuses regardless of flags,
because zero such edges have ever existed and that zero is the evidence.

IT DOES NOT RE-DERIVE THE GRAPH. It snapshots the baseline, lets the real gate
--update into place, diffs before against after, and restores on a dry run --
a second derivation could disagree with the one that actually gates the merge,
which is the defect shape this repo keeps finding.

Tests: bd-persist/harness/tests/test_edgecheck.py, 11 passed, including a
refusing gate returning CANNOT-EVALUATE and restoring the baseline, and a test
asserting the real tree still has the measured shape -- if product->test edges
ever appear, the split is no longer safe and that test says so.

STILL OPEN: row 457 proposes the same narrowing for the release trio. A
sibling row for narrowing the FROZEN SET itself (product->product only) is
worth filing; this tool automates the ceremony without changing the gate.

## 2026-08-31 -- rulings 30-33

30. ROWS 413/414/417/418: ONE BATCH CUT. They are file-disjoint (6/3/3/1
    files), share a cause (v3.66.1359's vault hardening broke four test
    fixtures), and are already individually verified. They cause ~19
    recurring band failures; clearing them makes every later band readable.
    I had WRONGLY reported these four as merged -- they were only tagged.
31. IMPORT GRAPH: NARROW THE FROZEN SET NOW, as a cut. Measured at v3.66.1371:
    2443 test->product edges (59%) carry none of the gate's stated hazard,
    1698 product->product (41%) carry all of it, and zero edges touch tests/
    on the far side. bd-edgecheck already proves the classification. Keep a
    guard asserting the product->test count stays 0, or narrowing also
    retires the proof that it never happens.
32. BD3 STAYS PINNED at v3.66.1371 until the ultrareview runs. Re-pointing
    mid-review invalidates the identity the review recorded. Re-point after,
    for a second pass over the newer cuts.
33. MUTATION COVERAGE: file the deferral row now; extend row 357's exception
    registry in DAYLIGHT as its own reviewed cut. The registry is empty and
    has never been used, so extending it means editing the anchor-integrity
    gate's own test file in three places (the dict, _STABLE_VALUE_EXCEPTION_MAX,
    and a _semantic_intent branch) beside a pinned len(_FRAGILE_RULES) == 49.
    That is the single riskiest edit available and must not be made unattended.
    NOTE THE PATTERN: three agents independently reported "no mutant anchor
    covers my changed files". This is not one gap, it is most fixes.

## 2026-08-31 -- rulings 34-36

34. FROZEN-SET NARROWING ABANDONED. It worked (4141 -> 1698 edges, new test
    files accepted, planted coupling still refused by name) but it reverses
    row 889, which deliberately WIDENED the graph because the gate was blind
    to 57% of the internal import surface -- 2132 edges over 1234 test files.
    My "59% is noise" and its "57% was a blind spot" are the SAME NUMBER read
    two ways: mine measured where the hazard lives, theirs measured what the
    gate can see. Row 889 also shipped a second half on purpose (bd-band-derive
    fires the import-edges flag only for bulk_downloader/ changes, so widening
    the gate alone makes a tests-only cut fail on the box instead of in the
    sandbox -- a shape CLAUDE.md records costing five releases). bd-edgecheck
    already removed the friction that motivated the narrowing, so the frozen
    set stays complete. Work preserved on branch candidate/edge-narrow.
35. PER-MERGE REPORTING: one line -- cuts left, rows open, new rows.
36. QUEUE ORDER: FASTEST-FIRST to drain. Smallest bands first (421, 422, 423,
    429), then the two large ones (446, 439), then rows 457-459 as one
    register cut. Supersedes the severity-first ordering for the remainder.

## 37 -- Continuity is supervised, not written at the end (2026-08-31)

RULING: supervise the continuity tools that already exist, and keep a bounded
resume file. Not a new tool and not a new document.

WHY IT WAS ASKED: five continuity tools existed and none was running. There is
no crontab, no systemd timer, and Linger was off, so every loop died with the
terminal that started it. FLEET_RUN_CHECKPOINT.md was five days stale and
FLEET_RUN_STATE.json seven; all four loops still wrote into
fleet-run-artifacts/2026-08-25, one night's directory. The end-of-session rush
the operator wants to avoid was the direct consequence.

AS BUILT:
  bd-checkpoint.timer   every 10 min -> bd-checkpoint-write
  bd-persist.timer      every 30 min -> bd-persist-loop.sh --once
  loginctl enable-linger mboyle       so both survive logout and reboot
  units archived at bd-persist/harness/systemd/

bd-checkpoint-write was changed rather than replaced: it now writes into
bd-persist/continuity (so persistence needs no second step), bounds the
checkpoint at 400 lines with older blocks moved to CHECKPOINT.archive.md,
measures the running-harness list instead of naming seven loops in prose that
had all stopped, and derives the queue from unmerged recover/* tags instead of
the retired bd-night-spec.txt.

## 38 -- Hygiene executed (2026-08-31)

Killed by exact PID after re-verifying each argv: two dashboards idle 26h and
25h, a 14h monitor loop polling a band that had finished, and the 31h leaked
pytest child. bd-gc --apply removed 326 of 335 eligible /tmp paths; the 9
failures were permission-denied and reported rather than swallowed, and the
506 KEPT_FOR_FORENSICS directories were left alone by design.

## 39 -- Three rows filed (2026-08-31)

460 the reaper that does not reap, 461 four auditors and four denominators,
462 the 43 corpus-arg-unguarded tools. Filed into the staged register cut
through bd-register-append.

## 40 -- One-shot archival: DEFERRED pending the audit, which has now landed

49 of 143 harness scripts are one-shots. The audit is at
bd-persist/harness-inventory.md. No move has been made; the operator asked to
decide once the audit landed.

## 41 -- Efficiency rulings (2026-08-31)

MEASURED FIRST, then ruled. The three heaviest pinned assertions by churn since
2026-08-01: tests/test_settings_center_slice4.py 507 commits, the CI gate-shard
file 128, the nested-freshness doc denominator 13.

  VERSION PIN -- KEEP THE LITERAL. It is a deliberate forgot-to-bump tripwire:
  it fails loudly if a cut bumps __init__.py and nothing else, and the trio
  contract exists so a version claim is provable from the tree rather than from
  a build system. 507 edits a year is the price. Row 465 still enumerates the
  readers; this ruling settles the pin itself, not the CHANGELOG anchor.

  GATE COUNT -- DROP THE EXACT TOTAL, KEEP MEMBERSHIP. Membership already
  proves every repo-wide file is declared and every declared gate is sharded.
  The total only catches a simultaneous add-and-remove. Queued as its own cut.

  DOC DENOMINATOR -- KEEP NONZERO AND UNIQUENESS, DROP THE EXACT TOTAL. A zero
  denominator means the freshness gate checks nothing and must still fail; the
  exact number costs an edit per document and protects only against silent
  disappearance, which the historical-exclusion list already governs. Same cut.

OTHER RULINGS, all to be built:
  - always overlap CI with the local verify; never serialize them
  - run independent cuts' verifies concurrently; dispatch precut to a fleet host
  - deploy hosts in parallel, bounded, aborting unstarted hosts on first failure
  - a verdict cache keyed on (tree SHA, merge-base, gate name, gate digest),
    PASS only, re-proving the tree SHA on a hit, printing REUSED with its source
  - resume a failed verify from the failed gate when the tree SHA is unchanged
  - a preflight that runs every denominator pin before any expensive gate
  - extract the failing assertion from a CI failure rather than reading logs
  - bd-verify-cut must refuse a second run on the same worktree, and no
    hand-written ps or grep may decide whether something is running
  - stop fixing tool defects mid-cut, stop verifying what CI already proves,
    stop re-measuring settled facts

## 42 -- v3.66.1380 is merged but NOT deployed (2026-08-31)

It changes no runtime path -- 66 register rows and three documents. Deploying it
would restart BD on eight serving hosts, each through the locked-vault 503
window of row 478, to ship a backlog file. Checkouts are fast-forwarded on all
twelve hosts; no service is restarted. Row 530 proposes making this a lane.

## 43 -- the four preventions ruled after v3.66.1381 (2026-08-31)

v3.66.1381 cost a 13m48s verify and a failed CI run for one broken mutant
anchor. `bd-anchorcheck.py` already existed, already answered exactly that
question over 1168 anchors in 212 specs, and takes 0.30 seconds. Nothing ran it.
That is the same shape as the morning's preflight work: a cheap check that
exists, not wired into the path that needed it. All four preventions accepted:

1. THE PREFLIGHT GREW FROM 36s TO 47s and now refuses more.
   - `bd-anchorcheck --catchers --base origin/main` (0.7s). `--catchers` is new
     and asks the other half of the question: a mutant whose catcher test has
     been renamed away can never fail, which is a battery that only looks armed.
     `--base` names the specs whose SUBJECT this cut touches, so a failure points
     at the co-change instead of at a spec the author did not know they moved.
   - THE THREE CHEAP UNDERIVED GATES. bd-precut runs six gates whose subject is
     the TREE, so bd-band-derive can never select them. Measured: 4.2s, 1.7s,
     5.0s, 104s, 61s, 21s. The cheap half moves forward; the expensive half stays
     in precut. `test_row357` -- the gate that caught v1381's break -- is in the
     cheap half.

2. PRECUT NOW RUNS CONCURRENTLY WITH THE BAND. They are two readers of one
   frozen checkout and prepush is the only mutating step, which has already
   finished and been re-proved. Serial cost ~5 minutes of every verify.
   Neither lane is cancelled by the other's failure (A5); a precut that leaves
   no exit status is UNKNOWN, never a pass.

3. BD-VERIFY-CUT PUBLISHES THE CANDIDATE ITSELF -- push, then open or refresh the
   PR -- immediately after the preflight passes and before the expensive gates.
   CI only starts on a pull request, and on 2026-08-31 a candidate sat pushed
   with no CI for four minutes because opening it was a separate thing to
   remember. It NEVER forces: a diverged remote branch is reported and the local
   verify continues. BD_VERIFY_CUT_NO_PUBLISH=1 keeps a run entirely local.

4. ROW PLUMBING.
   - `bd-next-row` prints the next free id. The register holds 474 rows and its
     ids run to 531 with 57 unused below the max, so the COUNT is not the next
     id -- which is how `row475` was written for an id already taken.
   - `bd-register-insert.py` now REPAIRS the canonical header with the
     repository's own parser after every insertion, and exits 4 saying STALE if
     it cannot. Reaching for it instead of the sanctioned tool produced a header
     reading rows=473 over a 474-row table, and bd-register-append refused the
     whole cut three steps away from the cause.
   - ROUTING, and this is the one-line answer: ADDING A ROW IS
     `toolchain/bin/bd-register-append` THEN `bd-register-close`.
     `bd-register-insert.py` is bd-rebase's conflict tool and nothing else.

## 44 -- stop starting rows; the goal is efficiency, reliability, robustness (2026-09-01)

OPERATOR RULING, 2026-09-01: do not start any of the ~100 open register items.
Record the triage workflow's output persistently and work only on making the
lane faster, more reliable and more robust. Finish whatever is in hand, then
stop.

WHAT WAS IN HAND AND IS NOW FINISHED: prepush runs off the integrator.

  - bd-band-remote.sh gained MODES. prepush and precut are the same shape as the
    band -- read a frozen checkout at an exact SHA, run something, return an
    exit code -- so they go through the SAME mirror/slot/worktree mechanism
    rather than a second implementation of it (A8). Measured: remote prepush
    4m26s, all gates OK including the pinned secret scan.
  - bd-verify-cut dispatches prepush remotely and FALLS BACK locally. rc 64
    (REMOTE-UNAVAILABLE) is converted to 98 first, because read as a gate result
    64 is a pass, and a pass is what an unrun gate must never produce (A2).
  - The pinned Gitleaks 8.24.3 binary was copied to all six band hosts; without
    it the secret scan FAILED remotely rather than being skipped.
  - Two defects the first remote prepush found in this work itself: the shipped
    script was written INSIDE the worktree and prepush's own untracked-drift
    check counted it, so a gate reported the harness that invoked it; and the
    band's `no tests given` precondition refused every selector-free mode.

REMOTE PRECUT IS NOT DONE, and the reason is worth keeping: bd-precut's CI
shard-headroom check shells out to `gh run list`, which needs authentication
against a private repo. Four of six band hosts have no gh, and shipping a token
to them to satisfy an ADVISORY check is secret sprawl for no gain. That check's
subject is CI, not the candidate tree, so it belongs where the credentials
already are. Doing it properly needs a small in-repo change giving that one
check its own opt-out -- a cut, not a harness edit.

THE TRIAGE PLAN IS PARKED, NOT DROPPED: bd-persist/TRIAGE_PLAN_2026-09-01.md
carries 8 batch-cuts over 60 rows and, more valuably, 24 named exclusions --
including rows that CANNOT be certified by pytest at all (285 needs a real
deploy; 452/454/455 need live authenticated captures) and rows whose blast
radius forbids batching (470 edits ~43 toolchain tools). It records its own
staleness: it read the register before v3.66.1388 merged, so rows 479 and 481
appear in it and are already closed.

WORK LEFT UNCOMMITTED ON PURPOSE: /home/mboyle/bd-cuts/cut/1389-staging-release-lifetime
holds a half-built cut for rows 492/489/506 -- staging_claim.release now proves
identity AND that the .part is gone, and a leaked .owner is visible to the
cleanup sweep; 9/9 on tests/test_row492_a_release_proves_what_it_frees.py. Rows
523 and 507 are NOT done. Nothing is committed and nothing is pushed.

## 45 -- the efficiency queue is struck, on measurement (2026-09-01)

Six queued efficiency items were measured before any was built. FIVE WERE
STRUCK. See `EFFICIENCY_PREMISES_STRUCK.md` for the numbers; the short form:

  remote precut          11.8s mean / 0s median, not ~5 min. precut ALREADY runs
                         concurrent with the band, and dispatching it breaks
                         every verify because gh is absent on capacity hosts.
  stamp trio at merge    18 of 24 sibling pairs still collide without the trio.
                         Unbuildable anyway: the trio is atomic and
                         test_register_closed_versions_exist blocks it.
  local band minus CI    33 of 236 files, ~55s -- and those 33 are the box-only
                         gates, so it saves most where the band is cheapest.
  skip regen             ~0.5s, not ~90s. The obvious digest was built and it
                         shipped a stale artifact 3 times in 300 commits.
  reuse checkout/band    ~7s of a ~790s attempt. Already covered by the verdict
                         cache, and reuse destroys the disposability that makes
                         "this evidence is about this tree" provable.

SURVIVED AND BUILT: the harness fast lane. The whole harness suite is 370.73s;
minus the four preflight tests it is 53.58s for 94 of 98. Those four are not
deleted -- they are a pre-freeze lane, where five minutes is justified. They are
also the only users of the scratch_wt fixture, so the fast lane never runs
`git worktree add` against the live integrator repo.

  fast:  ~/bd-harness-test          (94/98 in ~54s)
  full:  ~/bd-harness-test --full   (98/98 in ~371s) -- before archiving or freezing

DO NOT PARALLELISE THE HARNESS SUITE. Measured: -n 12 --dist loadfile is
372.68s against 370.73s serial, worse than nothing, because the four expensive
tests share a module-scoped fixture. --dist load would fire concurrent
`git worktree add` against the live repo.

SEVEN ITEMS IN ONE DAY died this way, counting the two in HANDOFF.md section 6.
The correct read is that the assumed-number well is dry, not that an eighth item
is waiting. If the real goal is fewer DISCARDED verdicts rather than raw
seconds, the measured target is bd-verify-cut.sh's bare SHA-equality guard on
origin/main and bd-land's unconditional sibling rebase: 2 of 24 attempts died
there, against 1 on the trio. That is research for a session that has time.

## 46 -- three PRs from the audit rows, and the two tool defects that blocked them (2026-09-01)

Wave 1 of BATCH_PLAN_AUDIT_ROWS_2026-09-01.md was built as three concurrent PRs:

  #681  v3.66.1390  rows 537 538 539 540  a vault write proves it is writing
                    over the vault it read; delete validates what it mutates
  #682  v3.66.1391  rows 492 489 506 533  a staging claim proves what it frees,
                    and unwinds what it cannot
  #683  v3.66.1392  rows 542 557          the replay tool CLAIMS its output
                    instead of probing for it

TWO DEFECTS IN MY OWN TOOLING SURFACED WHILE LANDING THEM, both fixed:

  bd-land computed its containment merge-base against origin/main AFTER the
  merge, which already contains the candidate -- so the merge base WAS the
  candidate, the changed set was always empty, the loop never ran, and every
  land printed "containment LANDED" over ZERO files compared. It is now against
  the PRE-merge main, it compares deletions, and it refuses a zero denominator.
  That refusal is what surfaced it, on its first use. ANY EARLIER LAND'S
  CONTAINMENT CLAIM PROVED NOTHING.

  bd-verify-cut's whole-band attribution replayed the candidate's selector list
  unchanged at the merge base -- including test files the base does not have --
  so pytest collected nothing, exited 5, and the verdict was UNATTRIBUTED. It
  blocked two GREEN cuts. The replay now drops selectors absent from the base: a
  file that is not there cannot have failed there, so dropping it excuses
  nothing.

A THIRD, IN THE FIX ITSELF: the vault write-guard first refused an ABSENT path
as well as a foreign one, which turned an ordinary I/O failure into an integrity
error and broke _save()'s documented contract of returning False on a failed
write. An empty path has nothing to clobber; the guard is now narrowed to a file
that EXISTS and is not ours.
