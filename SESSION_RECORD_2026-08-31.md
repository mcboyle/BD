# Session record -- 2026-08-31

The resume prompt at /home/mboyle/bd-resume-prompt.txt is GENERATED from
measured state every 10 minutes and is the entry point. This file is the part
that cannot be re-derived: what was decided, what was learned, and why.

## Shipped

    v3.66.1378   bd-precut derives its baseline from origin/main
    v3.66.1379   the four fixtures v3.66.1359 broke (rows 413/414/417/418)
    v3.66.1380   66 register rows + fleet topology doc + CLAUDE.md A6/A7
                 merged at 20f72e42, 30/30 required CI checks green.
                 DELIBERATELY NOT DEPLOYED -- it changes no runtime path, and a
                 deploy would restart BD on eight hosts through the locked-vault
                 window of row 478 to ship a backlog file (ruling 42).

FLEET, measured after the merge: all twelve checkouts at v3.66.1380, all on the
authoritative origin, one vault password. Nine serving hosts at v3.66.1379 with
health 200; three capacity hosts run no BD, as designed. test5 serves v3.66.1378
because it is the integrator and is never a deploy target -- row 474 is about
nothing measuring that gap, not about closing it.

The register is 473 rows. trio-row is retired: its six rows landed as 465-470
byte-identical apart from the id, its tip is preserved at
refs/archive/trio-row-superseded-by-1380, and the worktree and branch are gone.

## The correction that mattered most

I reported 17 cuts merged. Three version numbers in that range are absent from
main -- 1365, 1372 and 1374 -- and 1374 was not a renumbering. It was the
four-fixture cut. Its tests were not on main, its CHANGELOG entry did not
exist, and all four rows were still OPEN. It shipped today as v3.66.1379.

What caught it was not care, it was WIDENING A MEASUREMENT. Counting the queue
by SHA containment under-reports a rebase; by patch-id under-reports a conflict
resolution; only comparing THIS BLOB for every file the candidate touched
answers "has this shipped". The first two were not wrong about what they
measured -- they were wrong about what I asked them. That is now CLAUDE.md A7.

## What the ultrareview found

80 agents, 67 findings, 46 minutes on bd3. Re-validated against current main:
65 STILL-PRESENT, 1 FIXED at v3.66.1373, 1 refuted by its own verifier.
Filed as rows 479-529 after a merge pass folded 72 drafts into 51 and recorded
437 CORRECTIONS TO THE DRAFTS' OWN EVIDENCE -- wrong line numbers, symbols that
do not exist, two RED steps unsatisfiable as written.

The four criticals are data-corruption paths, all confirmed present:
  - a reservation minted over foreign bytes and called a resume
  - a no-transfer done row accepted as proof of ownership
  - a restored vault destroyed by the next unlock, under any password
  - a replay tool that force-removes a worktree it never created

ONE FINDING VANISHED IN THE MERGE and was recovered only because a critic was
asked what was MISSING rather than what was wrong. It is row 529, and it is
about exactly that: a coverage statement in prose is a claim, not a
denominator.

## Instruments that lied to me today, and how

  A GLOB. `$E/*.json` reported "0 vault copies present" while six sat there
  named .json.before and .json.after. A wrong glob returns clean.

  A PROCESS PROBE, three times. pkill -f killed its own caller. A uniform
  count of 1 across twenty scripts was the probe counting its own shell. And
  `^bash /home/mboyle/bd-verify-cut.sh` matched nothing while FOUR verifies
  ran, because the real argv was the relative `bash bd-verify-cut.sh` -- so I
  read zero and started a fifth.

  A DIAGNOSTIC. bd-vault-unlock printed "pairing fallback failed" for every
  failure in a four-request block, so a 401 incorrect password looked like a
  broken endpoint. Opposite diagnoses, opposite actions, wrong one pursued.

  MY OWN ATTRIBUTION. It proved a band failure inherited and then refused the
  cut anyway, because classify_pytest_rc had already recorded the RED and
  finish() reads that list first.

  A TEST I WROTE THIS MORNING. It pinned `ok=$((ok+1)); continue` rather than
  the contract, and failed on a refactor that changed the spelling and not the
  behaviour.

## Tools changed, all with tests

  bd-rebase-cut.py   --renumber for a parked cut; refuses an UNDECLARED
                     backwards renumber; a rule for frozen declaration files;
                     parks scaffolding symlinks that stopped rebases dead
                     twice with NO unmerged paths; checks the trio
                     UNCONDITIONALLY, because when a parked cut carries the
                     number main just took, the two sides are identical, git
                     auto-merges, and a conflict-only renumber leaves a 1379
                     changelog against a 1378 version file
  bd-verify-cut.sh   attributes a band failure against the merge base and
                     withdraws only the band's RED; refuses when the tree moved
                     mid-run
  bd-fleet-deploy.sh parallel, capped, aborting unstarted hosts on failure;
                     ROLES and HOSTS overridable so the FAILURE path is testable
  bd-precut          derives its baseline from origin/main (shipped @1378)
  bd-vault-unlock.sh names the step and carries the server's own words
  bd-retrio.py       RETIRED into superseded/ -- it duplicated bd-rebase-cut.py,
                     which had done the job since 2026-08-25 with three callers
                     to its zero

## Fleet work

  769 GB reclaimed from agent-runs on five hosts; 291,053 report and log files
  archived to bd-persist/fleet-archive-20260831 first, every tarball verified
  decompressible with a spot-restore.
  The vault password was split 6/3 across the fleet with nothing recording it.
  Unified onto one, verified OFFLINE against each vault's stored verifier so no
  attempt was charged against the throttle. See docs/repo/FLEET_TOPOLOGY.md.
  bd and bd1 fetched from a mirror nothing pushed into -- the cause of a deploy
  failure on this release and on 2026-08-30. All twelve now use the
  authoritative origin.

## What is queued, in the operator's order

  1. THE EFFICIENCY BACKLOG (ruled first): verdict cache keyed on tree SHA plus
     gate identity, PASS only, printing REUSED; resume a failed verify from the
     failed gate; a denominator preflight before any expensive gate; CI failure
     extraction; a worktree lock plus a real is-it-running checker; drop the
     exact gate count and the exact doc total, keeping membership, nonzero and
     uniqueness. CI SHARD REBALANCING as its own cut -- 30 shards, slowest 301s
     against a 128s mean, so the critical path halves.
  2. The four critical ultrareview findings.
  3. The six built-and-tagged fix cuts: 421, 429, 446, the transport trio, 439.

  Measure first, before narrowing anything: bd-band-derive maps a docs or
  register change to 245 test files, and nobody has checked what those assert.

## Rulings

bd-persist/OPERATOR_DECISIONS.md, entries 1 through 42.
