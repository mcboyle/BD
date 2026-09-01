# BulkDownloader session handoff -- 2026-08-31

Written at 96% context. This is a checkpoint, not authority: revalidate refs,
worktrees, processes, PR state and fleet health before acting.

## Where things stand

    main / fleet   v3.66.1377, all five deploy targets 200/healthy
    register       405 rows, 44 open
    merged         17 cuts this session, v3.66.1360 -> v3.66.1377,
                   every one deployed ok=5 healed=0 bad=0

Read /home/mboyle/bd-persist/OPERATOR_DECISIONS.md FIRST -- rulings 1 through
36 are the operator's standing decisions for this work, including autonomy,
batch width 5, fastest-first ordering, fix-forward on failure, and the four
rulings that are deliberately NOT executed (see "Do not" below).

## The queue: nine fixes, built and tagged, unmerged

Every one is RED-first with negative controls, band-attributed, and reachable
by tag. VERIFY BY COMMIT, NEVER BY BRANCH NAME -- one agent used its own branch
name and the predictable ref pointed at base with zero files.

    recover/runner-three-findings        rows 446/434/448  8 files
    recover/rows-428-430-431-transport   the transport trio
    recover/row439-netns-confine         HLS egress confinement
    recover/row429-probe-not-download    probe rows blocking real downloads
    recover/row421-alerts-unknown        alert silence
    recover/row423-backup-empty          MERGED at 1376
    recover/row422-budget-unknown        MERGED at 1377
    recover/extension-vault-unreadable   MERGED at 1373
    recover/row433-hold-barrier          MERGED at 1375

Also unmerged: candidate/edge-narrow (see "Do not"), and rows 457/458/459
staged in .worktrees/trio-row awaiting one register cut.

Operator ruling 36: FASTEST-FIRST. Remaining order by band size: 421, 429,
446, then the transport trio and 439, then 457/458/459 as one register cut.

## Do not

- DO NOT re-enable bd-night or bd-autorebase. Row 407 merged at v3.66.1366 but
  NOTHING IN THE HARNESS CALLS ITS SCRIPTS -- grep bd_candidate_replay across
  ~/*.sh and ~/*.py returns empty. The destructive path is unchanged. The
  2026-08-30 clobber sequence is now RECOVERABLE (bd-rebase-all.sh pins the
  candidate as a ref first) but not SAFE.
- DO NOT ship candidate/edge-narrow without re-reading row 889. It works, but
  it reverses a deliberate decision; the operator agreed to abandon it.
- DO NOT re-point bd3 while an ultrareview is running there.

## bd3 (10.0.70.53) holds an unstarted ultrareview

    tmux session   ultrareview
    branch         review/tonight at 3c3a1975 (v3.66.1371)
    local main     de6240f4 (v3.66.1359), pinned so the diff is the session
    reserved       out of the band population in ~/.config/bd/roles --
                   RESTORE bd3 TO `capacity` WHEN THE REVIEW IS DONE

It is six merges behind main. Re-point before reviewing if the operator wants
the settled tree; the prompt is in the session transcript.

## Tools built this session, all RED-proven and persisted

    bd-anchorcheck.py   1169 mutant anchors in ~1s; RED replayed against the
                        two real 2026-08-31 anchor breaks
    bd-edgecheck.py     classifies new import edges; declares test->product,
                        REFUSES product->product coupling
    bd-retrio.py        the release-trio rebase collision; recovers the
                        CHANGELOG entry byte-for-byte rather than retyping
    bd-revalidate.sh    revalidates a parked candidate against current main
    bd-rebase-cut.py    EXTENDED (it already existed -- read the inventory):
                        now refuses a rebase still in progress and a detached
                        HEAD, both of which cost cycles this session

## Two lessons worth carrying

1. A DETACHED HEAD AFTER A REBASE PRODUCES A PR WITH ZERO CI CHECKS, and zero
   checks reads as pending rather than broken. It can wait forever.
2. THREE AGENTS INDEPENDENTLY REPORTED that no mutant anchor covers their
   changed files, so their anchorcheck PASS said nothing about their change.
   Row 458 carries this; the registry that would fix it is empty and unused.
