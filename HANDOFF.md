# BulkDownloader handoff -- written 2026-09-01, account rolling over

READ THIS FIRST, THEN `CLAUDE.md`, THEN THE FILES IT NAMES. Everything here is
in `/home/mboyle/bd-persist` or in the repository, so it survives the account.

## 1. State, as measured

    main            v3.66.1393 at 511913b2   (git -C ~/BulkDownloader rev-parse origin/main)
    register        549 rows, 163 OPEN, 5 PARKED. PARKED is a real FOURTH
                    status (rows 120 122 124 126 127) and is NOT in the 163.
                    Rows 533-606 were filed 2026-09-01 from two adversarial
                    audits; 12 of them are now closed.
    open PRs        0
    working tree    tracked clean; 2 untracked SQLite sidecars
                    (downloader_history.db-shm/-wal) that are always there
    harness suite   98/98    (cd ~/bd-persist/harness && ~/BulkDownloader/venv/bin/python -m pytest tests/ -q)
    archive         VERIFY: PASS, 129 executables byte-identical (bash ~/bd-persist/verify.sh)

RE-MEASURE ALL OF IT. A finding without host, commit and tree identity is not
transferable evidence, and roughly half of a stale register's OPEN rows turn out
to be closed or mis-scoped.

## 2. THE ONE THING THAT NEEDS DOING

DO NOT DEPLOY UNTIL RANKS 4 AND 9 ARE CLEARED. The other five act-now
findings below are FIXED and merged (v3.66.1391, 1392, 1393); they are kept
here because each names a shape worth recognising again.

An adversarial refutation of everything shipped on 2026-08-31/09-01 confirmed 46
defects, and several of the day's own fixes REPRODUCE THE DEFECT THEY WERE
WRITTEN TO PREVENT. The fleet is six releases behind on the running service and
five on the checkouts, and that is now the SAFER state: production's v3.66.1379
carries the ORIGINAL defects, which are known; the candidate carries their shapes
re-manufactured by their own fixes, which is not proven better.

Full detail and rank order: `REFUTATION_2026-09-01.md`, and register rows
533-579. The seven act-now findings, with what is left:

    RANK 4  OPEN -- BLOCKS THE DEPLOY   row 536
    RANK 9  OPEN -- BLOCKS THE DEPLOY   row 541
    RANK 1  fixed v3.66.1391      RANK 5  fixed v3.66.1392
    RANK 6  fixed v3.66.1392      RANK 7  fixed v3.66.1392
    RANK 10 fixed v3.66.1393


  RANK 1  rows 481  bulk_downloader/staging_claim.py   [FIXED v3.66.1391]
    Leaked .owner after a failed set-aside turns the next attempt into an unexamined adoption of foreign .part bytes
    A single transient rename failure — or a deploy restart between the owner mint and the replace, which needs no error at all — leaves a claim on disk that makes the retry resume over another scene's bytes, splice them, and promote the concatenation as `done` under the right title: the exact 2026-08-29 corruption, reproduced by row 481's own fix; the precondition (an ownerless non-empty .part) is me

  RANK 4  rows 479  bulk_downloader/runner_extractors.py   [OPEN -- BLOCKS THE DEPLOY]
    _try_ytdlp_fallback returns a 4-tuple on 8 of 10 paths while runner_challenge unpacks 5
    The only defect here that fires in the shipped live configuration with no preconditions — the sole configured site has use_ytdlp_fallback=False and captcha_api_key='', so every captcha the auto-solver cannot clear dies at the unpack, the job is recorded as `failed: worker error: not enough values to unpack`, and the entire needs_review + screenshot + captcha_type + "Take over to solve it manually"

  RANK 5  rows 482, 487, 510  bulk_downloader/secrets_store.py   [FIXED v3.66.1392]
    _save() overwrites the vault path from the construction-time snapshot, destroying a restored vault
    After POST /api/backup/restore writes secrets.json (same relative path, cached backend never invalidated), the next ordinary credential save serialises the stale pre-restore dict over it — silently destroying the restored vault, its salt and every credential in it, with no error and no warning, on a host that has a real 1,824-byte vault today.

  RANK 6  rows 482, 487, 510  bulk_downloader/secrets_store.py   [FIXED v3.66.1392]
    The row-482 re-probe is advisory only: the first-use branch still ends in an unconditional os.replace with no exclusion against the restore writer
    Same restore-vs-vault-writer root cause as #5 and must be fixed with it — a restore landing in the ~2 ms between the presence probe and the rename destroys the operator's vault and re-initialises it under whatever password the unlock caller typed (measured 67 clobbers in 400 natural-race trials).

  RANK 7  rows 502  bulk_downloader/secrets_store.py   [FIXED v3.66.1392]
    delete() never validates the vault it mutates, so a damaged-but-readable store is silently mutated and reported 200 ok:true
    Over a vault whose KDF metadata or commitment envelope is damaged — recoverable by repairing one field — POST /api/secrets/delete destroys the ciphertext permanently while locked, never having unlocked, and answers 200 ok:true, while /api/secrets/status answers 409 over the same bytes.

  RANK 9  rows 481  bulk_downloader/staging_claim.py   [OPEN -- BLOCKS THE DEPLOY]
    A job's own partial bytes are set aside whenever its .owner is missing, destroying resume
    runner_transport.py:1435 releases the claim while a multi-GB .part survives (violating release()'s own stated precondition), so the retry renames the job's own bytes to .orphaned-* and restarts a 5 GB scene at byte 0 — and nothing ever reaps those orphans, so they accumulate unbounded and manufacture exactly the ownerless-.part population #1 needs.

  RANK 10  rows 480, 500  scripts/bd_candidate_replay.py   [FIXED v3.66.1393]
    bd_candidate_replay adopts a concurrently-created empty output directory and later force-removes it
    `git worktree add` returns 0 into a pre-existing empty directory, so occupied_before_add is computed and then ignored on the success branch: the tool records a foreign inode as its own, reports REPLAYED, and on any later conflict runs `git worktree remove --force` on another worker lane's directory, taking whatever was written into it and naming nothing — the destruction row 480 was cut to prevent

WHEN THEY ARE CLEARED, the deploy is:

    ls ~/.config/bd/DEPLOY_HOLD          # if present the deploy refuses, exit 4
    BD_DEPLOY_ALL=1 bash ~/bd-fleet-deploy.sh

BD_DEPLOY_ALL=1 IS REQUIRED or the script silently skips every runner-role host
-- test2, test3 and test6, three of the eight stale servers. NEVER test5. It is
parallel (CONC=4), bounded, and aborts unstarted hosts on the first failure.
Expect the locked-vault 503 window of row 478 on each restart;
bd-vault-unlock.sh clears it automatically.

READ `docs/repo/FLEET_TOPOLOGY.md` for how each host gets code. CLAUDE.md A6
still says "two hosts fetch from a per-host bare mirror"; FLEET_TOPOLOGY.md,
measured later on 2026-08-31, says ALL TWELVE now fetch from the github origin
and the mirrors are inert. FLEET_TOPOLOGY.md is the later measurement and wins;
A6's sentence is STALE. The lesson behind it stands: A FETCH EXITING 0 IS NOT
DELIVERY.

### Version numbers that were BURNED and never shipped

v3.66.1383, v3.66.1386 and v3.66.1387 DO NOT EXIST. They were consumed by
renumbering as cuts collided at merge. Releases after 1379 are, in order:
1380, 1381, 1382, 1384, 1385, 1388, 1389. Do not compute "releases behind" by
subtraction.

The branch slugs do not match their release numbers either, because `bd-land`
renumbers siblings on merge:

    cut/1384-ownership-needs-evidence          shipped as v3.66.1388
    cut/1385-vault-state-is-measured           shipped as v3.66.1384
    cut/1386-candidate-tools-own-what-they-made shipped as v3.66.1385

## 2a. Wave 1 of the audit plan -- ALL THREE LANDED

    v3.66.1391  rows 492 489 506 533   a staging claim proves what it frees and
                unwinds what it cannot
    v3.66.1392  rows 537 538 539 540   a vault write proves it is writing over
                the vault it read; delete validates what it mutates
    v3.66.1393  rows 542 557           the replay tool CLAIMS its output with an
                exclusive create instead of probing for it

Twelve of the seventy-four audit rows are closed, including refutation ranks 1,
5, 6, 7, 10 and 25. THE DEPLOY IS STILL BLOCKED: ranks 4 and 9 remain, which are
rows 536 and 541.

NEXT: wave 2 of BATCH_PLAN_AUDIT_ROWS_2026-09-01.md. Its first cut,
`runner-decisions-carry-their-whole-result` (rows 536 544 545 559 562), carries
rank 4 -- _try_ytdlp_fallback returning a 4-tuple where 5 are unpacked, which the
refutation calls the only defect that fires in the shipped live configuration
with no preconditions. Row 541 (rank 9) is in wave 1's first cut, which was
partly absorbed by v3.66.1391; re-derive it before starting.

## 2b. Worktree and tag inventory, measured 2026-09-01

    git worktree list        333 registrations
    ~/bd-codex-wt/           123 worker worktrees
    git tag -l 'recover/*'    24 tags, of which the checkpoint reports 21 whose
                              content is NOT in origin/main

TREAT THE TAGS AS A RECOVERY NET, NOT A QUEUE, and test containment by BLOB
EQUALITY per changed file -- SHA ancestry decided ZERO of 24 correctly, and
patch-id then called three landed tags unmerged. DELETE NOTHING: bd-endgame2.sh
and bd-clean-residue.sh both had confirmed work-destroying defects, fixed on
2026-09-01 but NOT yet re-tested end to end. Four stale remote branches exist:
cut/1180-aggressive-train-a, cut/1252-parallel-capture-services,
cut/1282-bd-opv-isolates-every-store, roadmap/backlog-42-plan.

## 3. What shipped 2026-08-31 into 2026-09-01

Nine releases. Twenty-three rows closed and seventy-four filed, so OPEN went
108 -> 99 -> 173 -> 163.

    v3.66.1381  row 531  gate/doc denominators derived, not pinned to a literal
    v3.66.1382  row 532  a mutant anchor may not resolve only into a comment
    v3.66.1384  rows 432 482 487 502 510  the vault's state is measured, never inferred
    v3.66.1385  rows 480 500  the replay tool owns only what it made
    v3.66.1388  rows 479 481  ownership needs evidence, not a done row and not a filename
    v3.66.1389  rows 533-606 FILED  seventy-four measured defects made machine-visible
    v3.66.1391  rows 492 489 506 533  a staging claim proves what it frees
    v3.66.1392  rows 537 538 539 540  a vault write proves its target
    v3.66.1393  rows 542 557  the replay tool claims its output

## 4. The lane, and what changed in it

A cut now costs roughly 20 minutes of fixed overhead. The pieces:

    bd-cut.sh <branch>            new worktree under ~/bd-cuts
    bd-next-row [<backlog>] [--json]   the next FREE row id. 549 rows, ids to 606:
                                  THE COUNT IS NOT THE NEXT ID.
    ADDING A ROW IS THESE TWO AND NOTHING ELSE, and the first takes a REQUEST FILE:
      venv/bin/python toolchain/bin/bd-register-append --repo <R> --request <req.json>
      venv/bin/python toolchain/bin/bd-register-close  --repo <R> --row <N> --version 3.66.NNNN
    req.json is  {"schema": "bd-register-append/v1",
                  "expected_ids_sha256": "<the ids-sha256 from the register header>",
                  "rows": ["| <id> | OPEN | <PROSE> |"]}
    ASCII only. The digest is a handshake: it refuses if the register moved under you.
    bd-denom-preflight <wt>       ~70s. Refuses before the expensive gates.
    bd-verify-cut.sh <wt> <tag>   preflight -> publish -> prepush -> derive -> precut || band -> attribute
    bd-land <wt>                  merge + containment + fast-forward + REBASE EVERY SIBLING WITH AN OPEN PR
    bd-rebase-cut.py --work W --version V [--renumber]
    bd-band-remote.sh <sha> [selectors]   BD_REMOTE_MODE=band|precut|prepush
    bd-ci-why [run|branch]        the failing assertion, not the log
    bd-anchorcheck.py --work <R> --catchers --base origin/main   (note the .py)
    bd-running <name>             the ONLY sanctioned "is X running"
    bd-kill-mine.sh <pat>         kill without killing yourself
    bd-verdict-cache              PASS-only, keyed on (tree, base, gate, gate digest)

Built 2026-08-31/09-01: remote band dispatch REPAIRED (repointing the fleet at
the authoritative origin had silently broken it, so every band ran on the
integrator and manufactured false REDs); prepush moved off the integrator;
precut made concurrent with the band; the verifier publishes its own PR;
attribution became a MATCHED experiment; `bd-land` auto-rebases siblings;
`bd-anchorcheck --catchers --base`; ten named remote refusals in place of one
exit code; the preflight grew mutant anchors, catchers, three cheap tree gates,
import-graph edges and an unstaged-drift check.

## 5. Rulings that are still standing

`OPERATOR_DECISIONS.md`, 1-46. The ones that bite most often:

  - 44 (2026-09-01) DO NOT START BACKLOG ITEMS. Work only on efficiency,
    reliability and robustness. `TRIAGE_PLAN_2026-09-01.md` is parked, not
    dropped.
  - 42  a cut with no runtime path is merged and fast-forwarded, NOT deployed.
  - 43  the four preventions, all built.
  - 45  the efficiency queue is STRUCK on measurement: 5 of 6 items died.
  - 46  the three PRs, and the two tool defects that blocked them.
  - autonomy: merge and deploy on green without asking.
  - NEVER send a push notification, for any reason.
  - NEVER deploy test5.
  - Never print a credential value; in `~/.bd-import` the FILENAME is the secret.
    Verify a vault password OFFLINE against the vault's `verifier` field.
  - Agents write only inside their own worktree. One integrator, one writer.

## 6. Premises that were MEASURED AND KILLED

Do not re-queue these from the same wrong numbers.

  - "CI shard rebalancing roughly halves the critical path." MEASURED: 273s ->
    256s, a 17s saving, because four shards are provisioned by NAME
    (`if: matrix.name == ...`) and pin the ceiling. Only if provisioning moves
    to a matrix flag does it reach 187s, and that is a 32-lane matrix rewrite
    for 86s a run.
  - "bd-band-derive maps a docs/register change to 245 test files." MEASURED:
    19 for register-only, 2 for docs-only, 53 with the release trio, 57 with
    generated artifacts. THERE IS NOTHING TO NARROW.

## 7. Traps that actually fired, all the same shape

A check that returns clean.

  - A `*.json` glob reported zero vault copies while six sat there named
    `.json.before`. A GLOB IS A DENOMINATOR CHOICE.
  - `pkill -f` killed its own caller. An anchored absolute-path pattern matched
    NOTHING while four runs were live, because the argv was relative.
  - SHA containment decided ZERO of 24 tagged candidates; patch-id then called
    three landed tags unmerged. ONLY BLOB EQUALITY per changed file answers
    "has this shipped". `bd-land` does it correctly -- copy it.
  - One exit code for ten causes hid a broken fetch for hours.
  - A gate that scans source TEXT reports its own comments. Parse instead. This
    fired twice in one cut.
  - Attribution replayed failing node ids ALONE and ON THE INTEGRATOR while the
    band ran the whole list on a capacity host, and twice told an operator a cut
    broke nodes it does not touch. Compare matched environments or report
    UNKNOWN.
  - A merge invalidates every sibling cut in flight. `bd-land` now rebases them;
    before it did, two fully-green cuts were discarded by a four-second merge.

## 8. Work left in a known place

NOTHING IS HALF-BUILT. Every worktree that carried work has landed and been
removed; `git worktree list` and `gh pr list --state open` were both clean at
handover. The staging cut that was half-built earlier is now v3.66.1391.

Rows 523 and 507 were carved OUT of that cut and remain OPEN.

## 8b. The 2026-09-01 audits, and how to work them

    REFUTATION_2026-09-01.md              46 confirmed defects in the fixes
                                          shipped 08-31/09-01, ranked. 7 act now.
                                          THIS FILE BLOCKS THE DEPLOY.
    HARNESS_FAIL_OPEN_AUDIT_2026-09-01.md 31 confirmed, 27 distinct, in the ~129
                                          bd-* scripts no CI covers. 5 fixed.
    REGISTER_LEDGER_2026-09-01.md         all 475 rows reconciled; 8 disagreements
    EFFICIENCY_PREMISES_STRUCK.md         5 of 6 queued items STRUCK on measurement
    BATCH_PLAN_AUDIT_ROWS_2026-09-01.md   the 74 rows in 5 waves of 3 file-disjoint
                                          cuts, act-now first. START HERE.

WAVE 1 IS DONE -- all three cuts landed as v3.66.1391, 1392 and 1393. WAVE 2 IS
NEXT, and its first cut carries the deploy blocker.

## 8c. A fail-open in bd-land, found and fixed on its own guard

bd-land computed its containment merge-base against origin/main AFTER the merge,
which already contains the candidate -- so the merge base WAS the candidate, the
changed set was always EMPTY, the comparison loop never ran, and every land
printed "containment LANDED" over ZERO files compared. It is now merge-base
against the PRE-merge main, it compares DELETIONS as well (a deleted path must be
absent from main), and it REFUSES a zero denominator. That refusal is what
surfaced the defect, on its first use.

If you rely on an earlier land's containment claim, it proved nothing. Re-check
by blob equality per changed path against the pre-merge base.

## 8d. What survives what

  THE REPOSITORY is on GitHub (mcboyle/BD). Code, the register, every doc and
  the CHANGELOG survive the loss of any host.

  BD-PERSIST LIVES ON test5 AND NOWHERE ELSE. Checked 2026-09-01 against all
  eleven other hosts: none had a copy. It is 2.5G, but the KNOWLEDGE in it is
  896K compressed -- harness/ (1.8M), continuity/ (120K) and the top-level
  markdown (1.4M). The rest is fleet archives and codex snapshots: bulk, not
  knowledge.

  That 896K slice is now replicated, digest-verified, to the three capacity
  hosts as ~/bd-knowledge-20260901.tar.gz:

      bd2  10.0.70.52     bd3  10.0.70.53     bd4  10.0.70.54
      sha256 9d304ba1119cd391... , identical on all three

  It was secret-scanned with the pinned Gitleaks 8.24.3 before it left this
  host: no leaks in either the harness or the documents. REFRESH IT after any
  session that changes the harness or the rulings -- a stale copy is worse than
  none, because it reads as a backup.

  THE CREDENTIAL IS NOT IN ANY OF THIS, deliberately. ~/.bd-import is on test5
  only, mode 700, and is excluded from the archive and from the slice. So
  bd-persist is sufficient to resume the WORK and is NOT sufficient to recover
  the FLEET on its own; that needs the operator.

## 9. Where the analysis lives

    TRIAGE_PLAN_2026-09-01.md   8 batch-cuts over 60 rows + 24 NAMED EXCLUSIONS
                                (rows that cannot be certified by pytest at all;
                                rows whose blast radius forbids batching). It
                                records its own staleness: rows 479 and 481
                                appear in it and are already closed.
    FLEET_SNAPSHOT.md           every host's checkout, service and health
    SESSION_RECORD_2026-08-31.md
    OPERATOR_DECISIONS.md       rulings 1-46
    continuity/CHECKPOINT.md    generated; read the last 200 lines first
    harness/                    the 129 executables + 98 tests, byte-verified

## 10. THIS FILE WINS

`bd-persist` also holds older entry points, ALL SUPERSEDED by this file:
`KICKOFF_NEXT_SESSION.md` (frozen at v3.66.1380 -- says 473 rows, rulings 1-42,
harness 66/66, every number stale), `HANDOFF_2026-08-30_1800_EST.md`,
`HANDOFF_2026-08-31_SESSION_END.md`, `RESUME_PROMPT.md`, and a second
`CLAUDE.md`. If they disagree with this file, this file is later.

`OPERATOR_DECISIONS.md` numbering: rulings 37-46 have `## N` headers; 1-36 are
bare `N.` list items under date headings. All 46 are there.

TOOL LOCATIONS, three of them: `~/X` is LIVE. `~/bd-persist/harness/X` is the
archive and `verify.sh` proves the two are byte-identical -- if they differ, the
live copy was edited and not archived. `toolchain/bin/*` are IN-REPO tools
(bd-precut, bd-band-derive, bd-freshcheck, bd-regen-order, bd-register-*) and
are the ones a cut may depend on; some are also on `$PATH` via /usr/local/bin.

STILL RUNNING ON THIS HOST: `bd-worker-dashboard-v2.sh`, up over a day. It is a
display loop, safe to leave or kill. `bd-running <name>` is the ONLY sanctioned
way to ask what is live; a clean answer looks like `0 live match(es)`.

UNLANDED CANDIDATES: `git tag -l 'recover/*'` shows 24 tags and the checkpoint
reports 21 whose content is NOT in origin/main. Treat them as a recovery net,
not a queue -- and test containment by BLOB EQUALITY per changed file, never by
SHA ancestry, which decided zero of 24 correctly. Four stale remote branches
exist: cut/1180-aggressive-train-a, cut/1252-parallel-capture-services,
cut/1282-bd-opv-isolates-every-store, roadmap/backlog-42-plan.

AN ATTRIBUTED RED IS SHIPPABLE. When the band fails and the whole-band replay at
the merge base fails the same nodes, the verifier prints `inherited: <node>` and
`SHIPPABLE WITH INHERITED FAILURES -- not green`. `bd-land` accepts that verdict
and prints the inherited nodes into the merge. It is deliberately never called
green.

## 11. The 2026-09-01 analyses

Four workflows and one reconstruction test ran. Their outputs are FILES in this
directory, not appendices to this one:

    REGISTER_LEDGER_2026-09-01.md   all 475 rows reconciled against the tree;
                                    8 disagreements, ranked, with confidence and
                                    a named coverage gap (row 447 unmeasured)
    TRIAGE_PLAN_2026-09-01.md       8 batch-cuts + 24 named exclusions
    FLEET_SNAPSHOT.md               every host measured

Anything else they produced is under
`~/.claude/projects/-home-mboyle-BulkDownloader/*/subagents/workflows/*/journal.jsonl`,
one JSON result per agent.

THIS FILE WAS ITSELF TESTED. A fresh agent was given only this file and
CLAUDE.md and asked to reconstruct the working state; it found eight false or
misleading claims, all of which are corrected above. The corrections included a
deploy command that would have skipped three stale hosts, three release numbers
that do not exist, and a "nine releases behind" that was really six.
