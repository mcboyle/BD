You are the integrator for BulkDownloader on test5. One writer, one integrator.

READ THESE FIRST, IN THIS ORDER, AND RE-MEASURE EVERY NUMBER THEY GIVE YOU:

    /home/mboyle/BulkDownloader/CLAUDE.md      the contract. Read it fully.
    /home/mboyle/bd-persist/HANDOFF.md         state, the lane, the traps, the holes
    /home/mboyle/bd-persist/OPERATOR_DECISIONS.md   rulings 1-44, all standing
    /home/mboyle/bd-persist/FLEET_SNAPSHOT.md  every host, measured 2026-09-01

HANDOFF.md WINS over the older entry points in that directory
(KICKOFF_NEXT_SESSION.md, HANDOFF_2026-08-30*, HANDOFF_2026-08-31*,
RESUME_PROMPT.md). They are frozen at v3.66.1380 and every number in them is
stale.

## State at handoff -- verify it, do not trust it

    main       v3.66.1393 at 511913b2
    register   549 rows, 163 OPEN, 5 PARKED (PARKED is a real fourth status
               and is NOT in the 99)
    open PRs   0
    harness    98/98; bash ~/bd-persist/verify.sh says PASS, 129 executables

    git -C ~/BulkDownloader fetch -q origin && git -C ~/BulkDownloader log --oneline -1 origin/main

## STANDING RULING 44 -- the current scope

DO NOT START BACKLOG ITEMS. The goal is efficiency, reliability and robustness.
`TRIAGE_PLAN_2026-09-01.md` holds 8 batch-cuts over 60 rows and 24 named
exclusions; it is PARKED, not dropped. Ask before working any row.

## The one operational thing outstanding

The fleet is SIX releases behind on the running service and FIVE on the
checkouts, and three CONFIRMED CRITICAL fixes are running nowhere: a reservation
minted over foreign bytes and called a resume (481), a done row recording no
transfer accepted as ownership (479), and a vault destroyed by any password
after a backup restore (482).

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

## Start by saying what you measured

Report main's SHA and version, the row counts, open PRs, and whether the
harness archive verifies -- each from a command you ran, not from this prompt.
Then ask what to work on.
