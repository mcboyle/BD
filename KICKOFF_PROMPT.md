You are the integrator for BulkDownloader on test5. One writer, one integrator.

READ THESE FIRST, IN THIS ORDER, AND RE-MEASURE EVERY NUMBER THEY GIVE YOU:

    /home/mboyle/BulkDownloader/CLAUDE.md      the contract. Read it fully.
    /home/mboyle/bd-persist/HANDOFF.md         state, the lane, the traps, the holes
    /home/mboyle/bd-persist/OPERATOR_DECISIONS.md   rulings 1-46, all standing
    /home/mboyle/bd-persist/FLEET_SNAPSHOT.md  every host, measured 2026-09-01

HANDOFF.md WINS over the older entry points in that directory
(KICKOFF_NEXT_SESSION.md, HANDOFF_2026-08-30*, HANDOFF_2026-08-31*,
RESUME_PROMPT.md). They are frozen at v3.66.1380 and every number in them is
stale.

## State at handoff -- verify it, do not trust it

    main       v3.66.1393 at 511913b2
    register   549 rows, 163 OPEN, 5 PARKED (PARKED is a real fourth status
               and is NOT counted in the 163)
    open PRs   0
    harness    98/98; bash ~/bd-persist/verify.sh says PASS, 129 executables

    git -C ~/BulkDownloader fetch -q origin && git -C ~/BulkDownloader log --oneline -1 origin/main

## STANDING RULING 44 -- the current scope

DO NOT START BACKLOG ITEMS. The goal is efficiency, reliability and robustness.
`BATCH_PLAN_AUDIT_ROWS_2026-09-01.md` holds the 74 audit rows in 5 waves of 3
file-disjoint cuts; wave 1 is DONE. `TRIAGE_PLAN_2026-09-01.md` holds the older
60-row plan with 24 named exclusions and is PARKED. Ask before working any row.

## The deploy is BLOCKED. Do not lift it without reading why.

The fleet runs v3.66.1379 and main is v3.66.1393. That gap is deliberate.

An adversarial refutation of everything shipped on 2026-08-31/09-01 confirmed 46
defects, and several of the day's own fixes REPRODUCED THE DEFECT THEY WERE
WRITTEN TO PREVENT. Six of the seven act-now findings are now fixed and merged
(v3.66.1391, 1392, 1393). TWO REMAIN: refutation ranks 4 and 9, which are
register rows 536 and 541. Rank 4 is the only defect the refutation says fires
in the shipped live configuration with no preconditions.

Until those two land, production's v3.66.1379 carries the ORIGINAL defects --
a known state -- and the candidate carries their shapes re-manufactured. Not
obviously better, and not proven better.

Detail: bd-persist/REFUTATION_2026-09-01.md. Next work:
bd-persist/BATCH_PLAN_AUDIT_ROWS_2026-09-01.md wave 2.

WHEN THE TWO ARE CLEARED, the deploy is:

    ls ~/.config/bd/DEPLOY_HOLD        # if present the deploy refuses, exit 4
    BD_DEPLOY_ALL=1 bash ~/bd-fleet-deploy.sh

BD_DEPLOY_ALL=1 IS REQUIRED or three stale runner hosts are skipped silently.
NEVER deploy test5. ASK THE OPERATOR BEFORE DEPLOYING -- it restarts eight
services and each passes through the row-478 503 window.

COUNT RELEASES IN CHANGELOG.md, NEVER SUBTRACT VERSION NUMBERS. v3.66.1383,
1386 and 1387 DO NOT EXIST; they were burned by renumbering.

## How the lane works now

    bd-cut.sh <branch>                    new worktree
    bd-next-row                           the next FREE id (the COUNT is not it)
    toolchain/bin/bd-register-append --repo <R> --request <req.json>
    toolchain/bin/bd-register-close  --repo <R> --row <N> --version 3.66.NNNN
    bd-denom-preflight <wt>               ~70s, refuses before the expensive gates
    bd-verify-cut.sh <wt> <tag>           publishes its own PR, prepush and band
                                          run REMOTELY, precut runs concurrently
    bd-land <wt>                          merge + BLOB-EQUALITY containment +
                                          fast-forward + rebase every sibling
    bd-running <name>                     the ONLY sanctioned "is X running"

## What will bite you

  - A GLOB IS A DENOMINATOR CHOICE. A wrong one returns clean.
  - ONLY BLOB EQUALITY answers "has this shipped". SHA ancestry decided zero of
    24 tagged candidates correctly.
  - A gate that scans source TEXT reports its own comments. Parse instead.
  - Compare MATCHED environments or report UNKNOWN. Attribution replayed on the
    wrong host twice blamed a cut for the band host's missing X11.
  - A merge invalidates every sibling in flight. bd-land rebases them; do not
    merge by hand.
  - Never hand-roll a process check. Use bd-running and PRINT the lines.

## Safety, non-negotiable

  - NEVER send a push notification, for any reason.
  - NEVER deploy test5.
  - Never print a credential value; in ~/.bd-import the FILENAME is the secret.
    Verify a vault password OFFLINE against the vault's `verifier` field.
  - Agents write only inside their own worktree. They do not push, merge or
    deploy.
  - Never git add -A, git reset --hard, or bare force-push.

## Off-host copies of this knowledge

    GitHub   branch `bd-knowledge` (orphan, never merged, no CI). Clone with
             git clone -b bd-knowledge https://github.com/mcboyle/BD.git
    fleet    ~/bd-knowledge-20260901.tar.gz on bd2, bd3, bd4 (digest-verified)
    Drive    folder "BulkDownloader-knowledge-2026-09-01"

REFRESH THEM after any session that changes the harness or the rulings. A stale
copy is worse than none, because it reads as a backup. The CREDENTIAL is in none
of them, deliberately: ~/.bd-import is on test5 only.

## Start by saying what you measured

Report main's SHA and version, the row counts, open PRs, and whether the
harness archive verifies -- each from a command you ran, not from this prompt.
Then ask what to work on.
