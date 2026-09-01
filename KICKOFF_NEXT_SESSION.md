You are the integrator for BulkDownloader on test5. Read these BEFORE acting,
in this order, and revalidate live state before relying on any number in them:

  /home/mboyle/BulkDownloader/CLAUDE.md          the contract; read it fully
  /home/mboyle/bd-resume-prompt.txt              generated from measured state
  /home/mboyle/bd-persist/continuity/CHECKPOINT.md   last 200 lines first
  /home/mboyle/bd-persist/SESSION_RECORD_2026-08-31.md
  /home/mboyle/bd-persist/OPERATOR_DECISIONS.md  rulings 1-42, standing
  /home/mboyle/BulkDownloader/docs/repo/FLEET_TOPOLOGY.md   new; the fleet

STATE AS OF 2026-08-31 19:35Z -- MEASURE IT AGAIN, do not trust this block.
  main            v3.66.1380 at 20f72e42, register 473 rows
  fleet           all twelve checkouts at 1380, all on the authoritative origin
                  nine serving hosts at v3.66.1379 health 200
                  three capacity hosts run no BD, by design
                  test5 serves v3.66.1378 -- integrator, never deployed
  vault           one password fleet-wide; verify OFFLINE against each vault's
                  `verifier` field, never by spending an unlock attempt
  archive         bd-persist 2.5G, verify.sh PASS, harness suite 66/66

WORK IN THE OPERATOR'S ORDER. Do not reorder without asking.

  1. THE EFFICIENCY BACKLOG. Ruled first because it halves the cost of
     everything after it. Each is specified in ruling 41:
       - a verdict cache keyed on (candidate tree SHA, merge-base SHA, gate
         name, gate script digest). PASS ONLY -- never cache a FAIL or an
         UNKNOWN, so a cache can never manufacture a green. Re-prove the tree
         SHA on a hit. Print REUSED naming the run it came from.
       - resume a failed verify from the failed gate when the tree is unchanged
       - a DENOMINATOR PREFLIGHT before any expensive gate. Both of the day's
         expensive re-runs were the same shape: a new file broke a pinned count
         elsewhere. Running the three pins by hand takes 21 seconds and would
         have saved two full cycles.
       - extract the failing assertion from a CI failure instead of reading logs
       - bd-verify-cut must REFUSE a second run on the same worktree, and add a
         real is-it-running checker
       - CI SHARD REBALANCING as its own cut: 30 shards, slowest 301s against a
         128s mean, so the critical path roughly halves. Durations come from the
         API; derive the packing, do not guess it. The gate-shard membership
         test already proves the property a rebalance could break.
       - drop the exact gate count and the exact markdown-doc total; KEEP
         membership, nonzero and uniqueness. The version pin's duplicated
         literal STAYS -- ruled deliberately, it is the forgot-to-bump tripwire.

  2. THE FOUR CRITICAL ULTRAREVIEW FINDINGS, all confirmed present on main:
     a reservation minted over foreign bytes and called a resume; a no-transfer
     done row accepted as proof of ownership; a restored vault destroyed by the
     next unlock under any password; a replay tool that force-removes a worktree
     it never created. Rows are in the register; read them there, not here.

  3. THE SIX BUILT FIX CUTS, tagged and needing only rebase and verify:
     recover/row421-alerts-unknown, recover/row429-probe-not-download,
     recover/runner-three-findings, recover/rows-428-430-431-transport,
     recover/row439-netns-confine, recover/row439-ffmpeg-vpn.

  MEASURE BEFORE NARROWING: bd-band-derive maps a docs or register change to
  245 test files and nobody has checked what those assert. Measure first.

HOW TO WORK, from what cost time on 2026-08-31:
  - PUSH BEFORE VERIFYING so CI runs inside the local verify window, never after
  - run independent cuts' verifies concurrently; they queue for no reason
  - run the cheap gates first; a 14-minute run must never start against a tree
    a 21-second check rejects
  - do NOT fix tool defects mid-cut unless they block the cut in front of you;
    record them and batch them
  - do not re-measure settled facts

TRAPS THAT ACTUALLY FIRED, all the same shape -- a check that returns clean:
  - a *.json glob reported zero vault copies while six sat there named
    .json.before. A GLOB IS A DENOMINATOR CHOICE.
  - pkill -f killed its own caller; a uniform count across hosts was the probe
    counting itself; an anchored absolute-path pattern matched nothing while
    FOUR runs were live, because the argv was relative. Use bd-kill-mine.sh and
    print the matching lines before trusting a count.
  - SHA containment decided zero of 24 tagged candidates; patch-id then called
    three landed tags unmerged. Only blob equality per changed file answers
    "has this shipped". Getting this wrong reported four rows merged when they
    were only tagged, and a whole release as shipped when it never landed.

SAFETY, unchanged:
  - NEVER send a push notification, for any reason.
  - NEVER deploy test5. Read ~/.config/bd/roles for who is a deploy target.
  - Never print a credential value; in ~/.bd-import the FILENAME is the secret.
  - Agents write only inside their own worktree. They do not push, merge or
    deploy. One integrator, one writer.
  - Merge and deploy on green without asking. A cut that changes no runtime
    path is merged and fast-forwarded but NOT deployed (ruling 42).
