# bd-persist -- the durable state of BulkDownloader's integration lane

## START HERE, IN THIS ORDER

    KICKOFF_PROMPT.md    paste this into a new session. It is the entry point.
    HANDOFF.md           the full state as of 2026-09-01, v3.66.1388.
                         THIS FILE WINS over every other handoff here.
    OPERATOR_DECISIONS.md  rulings 1-44, all standing. 37-44 have ## headers;
                         1-36 are bare `N.` items under date headings.

## Current analysis, 2026-09-01

    REGISTER_LEDGER_2026-09-01.md  all 475 rows reconciled against the tree.
                                   8 disagreements, ranked, with confidence and
                                   a named coverage gap. NOT acted on.
    TRIAGE_PLAN_2026-09-01.md      8 batch-cuts over 60 rows + 24 named
                                   exclusions. PARKED by ruling 44.
    FLEET_SNAPSHOT.md              every one of the 12 hosts, measured.

## The archive

    harness/          129 live bd-* executables + 98 tests. `bash verify.sh`
                      proves ~/X is byte-identical to harness/X for every one.
                      If they differ, the live copy was edited and not archived.
    IMPROVEMENT_BACKLOG.md   a copy of the register; verify.sh checks it is not
                      behind the live one.
    continuity/CHECKPOINT.md   generated every 10 minutes. Read the LAST 200
                      lines first.

## SUPERSEDED -- do not start from these

Every one is frozen at an earlier release and every number in it is stale. They
are kept because their PROSE records how a defect was found, not because their
numbers are true.

    KICKOFF_NEXT_SESSION.md         v3.66.1380: says 473 rows, rulings 1-42,
                                    harness 66/66. All three are now wrong.
    HANDOFF_2026-08-30_1800_EST.md
    HANDOFF_2026-08-31_SESSION_END.md
    RESUME_PROMPT.md
    MORNING-REPORT.md
    RECOVERY_MANIFEST_2026-08-30.md
    PERSISTENCE_VERIFY_2026-08-30.md
    IDLE_AGENT_CONSOLIDATION_2026-08-30_2216Z.md
    CLAUDE.md                       a COPY. The live contract is
                                    ~/BulkDownloader/CLAUDE.md.
    harness-inventory.md            a snapshot of the harness; the live
                                    inventory is `ls ~/bd-*`.
    SESSION_RECORD_2026-08-31.md    still useful as narrative.
    SESSION_LEARNINGS_2026-08-31.md still useful as narrative.

---

## The previous README, kept verbatim below

# bd-persist — everything from the 2026-08-28/29 session

Written because most of this lived in volatile places: `/tmp` scratchpad scripts,
two throwaway VMs, and uncommitted codex worktrees. Verify with `bash verify.sh`
(exit 0 = nothing missing).

## What is here

| path | what | why it matters |
|---|---|---|
| `scripts/` | 43 tools written this session | were in `/tmp`, would not survive a reboot |
| `harness/` | 107 `bd-*.sh` lane/harness scripts | the running automation |
| `codex/` | 65 worktree patches + new-file tars | UNCOMMITTED codex work, incl. row363 (58 files) |
| `bd-codex-briefs/` | 102 task briefs | how each row was specified |
| `remote-bd1/`, `remote-test6/` | scripts + artifacts off the VMs | the VMs are throwaway |
| `fleet-artifacts/CHECKPOINT.md` | 885-line session record | the authority; read its last 200 lines first |
| `fleet-artifacts/hunt/` | hunt reports | earlier defect surveys |
| `bd-night-spec.txt` | the row queue, 78 rows | rows 359–372 filed tonight |
| `evidence/shots/` | screenshots | login form fills, the upsell interstitial |
| `bd/` | fleet roles + hosts config | which host does what |

## Restoring codex work

Each `codex/rowNNN.patch` starts with a provenance header naming its base commit:

    # worktree row363 base=<sha> changed=58 captured=<iso8601>

To restore into a worktree at that base:

    git -C <worktree> apply --3way codex/row363.patch
    tar xf codex/row363.newfiles.tar -C <worktree>     # untracked files, if any

The `.newfiles.tar` matters: `git diff HEAD` cannot see untracked files, and for
some rows a brand-new test file **is** the entire cut.

## The two capture bugs that nearly lost this

1. **SIGPIPE truncation.** The first capture piped its loop into `head -12`; the
   pipe closed, the loop took SIGPIPE and died after 13 of 87 worktrees —
   silently, with every live row missed. `capture_codex.sh` writes to a file and
   reads it afterwards. Never pipe a capture loop into a pager.
2. **Overwrite instead of merge.** `learn_login.py` rewrote its output file on
   every run, so 16 measured login forms were replaced by the last batch of 3.
   It now merges. Check what a tool does with its output before trusting it.

## Live hosts

- **bd1 10.0.70.51** — no GPU. 20 sites wired. noVNC :6080 pw `D6BTpqqv10hUmV0B`
- **test6 10.0.70.249** — Tesla T4, ollama (`qwen2.5:7b`, `qwen2.5vl:7b`,
  `qwen3:8b`). 20 sites wired, 19 login forms. noVNC :6080 pw `rvkhMlBQvlYSKOAz`
- API tokens live as FILENAMES in each host's `~/.bd-import/`; scripts read them
  from there. Credential CSVs remain in `~/.bd-import` by operator instruction —
  **do not shred**. Operator will rotate tokens and VNC passwords.

## Proven this session

- **evilangel logs in UNATTENDED**: `✓ OK — 9 cookies`, no manual window.
- **Full download chain**: 3860.3 MB delivered vs 3860 advertised (ratio 1.000),
  site title captured, correct 2160p chosen from 8 options.
- **Vision reads a download menu**: 8/8 heights, 0 hallucinated, 8.8s on the T4 —
  including `Web HD 540p`→540 and 160/240/360, where `normalize_resolution`
  answers 720 and 0.
- **Fresh-host bring-up works**: `VERDICT: READY` on five untouched VMs.

## Known-unfinished

- ultrafilms / wowgirls — fields wired, submit empty; both label the button
  "GET INSIDE". A structural submit finder was added, parses, did not fire.
- kink — gates clear ("Accept All" → "ENTER KINK") but the header LOG IN modal
  step does not open. Sequence confirmed by the operator.
- Row 369 caps everything: a service restart LOCKS THE VAULT and every login
  silently reverts to "SKIPPED — missing password".
