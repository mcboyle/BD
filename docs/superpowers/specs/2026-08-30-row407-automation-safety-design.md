# Row 407 Automation Safety Design

## Goal

Replace the unsafe, untracked recovery contract with repository-owned tools
that preserve committed and uncommitted candidates, serialize replay output,
prove any later adoption from durable identity evidence, and cannot mistake
duplicate watchdog lineages for one healthy process.

## Incident boundary

The disabled external recovery path assumed every worker held only uncommitted
changes. It ran stash, detached the worker at current main, then popped the
stash. Workflow-produced rows can commit their candidate. For those rows the
stash is empty, so detaching the worker removes the candidate head from the
visible recovery path. Pop or cherry-pick conflicts can also leave a partially
rewritten worker.

Six independently launched watchdogs made that destructive path recur. A text
count of matching command lines could not distinguish a parent/child re-exec
lineage from independently launched roots, and a numeric PID was reusable
identity. The recovery output also existed only as stdout JSON, so a later
driver had no durable record it could safely adopt.

The live external auto-rebaser and nightly retry step remain disabled. This
candidate does not install, copy, invoke, collapse, or re-enable them. Batch
capacity remains one.

## Components

### Transactional candidate replay

`scripts/bd_candidate_replay.py` takes a repository, source worktree, exact
expected source HEAD, main ref, and caller-chosen output worktree. It treats the
source as immutable evidence:

1. Build every Git subprocess environment by removing all inherited `GIT_*`
   entries, then add only the command's explicit safe Git variables. Both
   replay and verdict tools therefore honor their `-C` repository even when the
   caller has a poisoned `GIT_DIR`, `GIT_WORK_TREE`, or `GIT_INDEX_FILE`.
2. Resolve and validate the repository, source HEAD, main SHA, merge base, and
   quiescent source state. Quiescent means two equal complete snapshots, not a
   clean worktree: staged, unstaged, and untracked candidate state is expected.
   Fingerprint source HEAD, index, tracked worktree bytes, untracked paths,
   relevant modes, and symlink targets. Unsupported merge, submodule, special-
   file, and unsafe path states are refused rather than approximated.
3. Atomically claim the output through one deterministic sibling transaction
   record, `.<output-name>.bd-replay.json`, opened with `O_EXCL`. The record
   carries a random token plus its file device/inode. A second replay targeting
   the same output refuses before it can create, inspect, or remove that output.
4. Create a new detached output worktree at the exact resolved main SHA. Capture
   the output directory and registered Git-dir device/inode identities before
   any step that could require rollback.
5. Cherry-pick the candidate-only, non-merge commits in order. An entirely
   uncommitted candidate is valid: an empty commit list still replays staged,
   unstaged, and untracked state onto main.
6. Apply staged and unstaged binary diffs in their original index/worktree
   classes, then copy safe untracked files and symlinks without following them.
7. Re-fingerprint the source. On any `BaseException` after claim acquisition,
   run rollback first and re-raise the original exception unchanged. Rollback
   removes an output or transaction record only after revalidating the exact
   token, parent-directory identity, final-path no-follow identity, record
   inode, output inode, and registered Git-dir identity captured by this
   transaction. Those identities are re-read immediately before every removal.
   Failed cleanup is attached as secondary evidence and never authorizes
   deleting an unowned replacement.
8. On success, rewrite the still-open owned transaction-record descriptor with
   a complete `REPLAYED` manifest, fsync the file and parent directory, and
   leave both output and manifest in place. The manifest binds schema, token,
   common Git directory, source/output paths and filesystem identities, source
   HEAD and state hash, merge base, main SHA/ref, replayed HEAD and state hash,
   output Git-dir identity, and candidate commit list.

The source worktree never receives `checkout`, `reset`, `stash`, `rebase`,
`cherry-pick`, index writes, or file writes. Conflict rollback removes only a
new output whose complete ownership still matches; it never reconstructs or
rewrites the worker.

### Replay-manifest adoption

`scripts/bd_candidate_adopt.py` is a read-only validator. It accepts a replay
manifest and independently resolves all evidence rather than trusting a path or
status string. `ADOPTABLE` requires:

- the exact supported manifest schema and `REPLAYED` state;
- every recorded path authority to be absolute, and the manifest path to remain
  a regular file with the recorded parent and file device/inode;
- repository, source, and output to share the recorded common Git directory;
- source HEAD and complete source fingerprint to remain unchanged;
- output directory and registered Git-dir identities, replayed HEAD, and
  complete output fingerprint to remain unchanged; and
- the named main ref to still resolve to the frozen main SHA;
- the merge base and ordered non-merge candidate commit list to equal an
  independent derivation from the recorded source and main commits; and
- a final reread to reproduce the complete manifest while the parent,
  repository, source, output, common-Git, and output-Git identities still name
  their recorded inodes.

Any malformed, missing, moved, stale, or unreadable evidence is `UNKNOWN`,
never adoption. A disproved but readable identity is `NOT_ADOPTABLE`. Adoption
does not mutate either worktree or consume/delete the manifest.

### Integration verdict

`scripts/bd_integration_verdict.py` is read-only. It returns `INTEGRATED` only
when all requested evidence holds:

- the full candidate commit is an ancestor of the resolved main ref;
- the candidate declares exactly the expected release version;
- current main declares that version or a later one;
- when a row is supplied, main's canonical register contains exactly one
  CLOSED row for it; and
- every explicitly required path (normally the row regression test) exists in
  main.

Missing or unreadable evidence is `UNKNOWN`, never success. The result includes
the resolved candidate and main SHAs so a version-like directory or marker
cannot stand in for merged ancestry.

### Watchdog identity, collapse, and adoption

`scripts/bd_watchdog_identity.py` is read-only by default. It scans procfs for
an exact Bash invocation of one canonical script path; substring matches,
option operands, fake `argv[0]`, and non-Bash executables do not count. Each
match is bound to kernel boot ID, PID, PPID, start ticks, exact argv, canonical
script, and resolved cwd/executable paths plus their device/inode identities.
The complete argv/cwd/executable receipt is reread between three stable stat
reads. Matching parent/child chains form one logical lineage. Independent roots
are duplicates.

Default census reports `ABSENT`, `UNIQUE`, `DUPLICATES`, or `UNKNOWN` and never
signals or writes. An explicit `--adopt-record` action takes a cooperating
exclusive lock and may write a record only after a fresh unique census. If
duplicates exist, it refuses unless `--collapse` is also explicit.

Collapse deterministically retains the newest independent root by
`(start_ticks, pid)`, except that a still-valid prior adoption record remains
authoritative. Duplicate lineages are processed leaf-first. Immediately before
each signal the tool opens a pidfd and revalidates boot ID, PID, PPID, start
ticks, exact argv, cwd/executable receipts, and lineage membership, then takes
one final complete post-pidfd receipt immediately before signalling. It sends
`SIGTERM` only through that pidfd, waits a bounded interval for pidfd readiness,
and never polls or escalates by numeric PID. Identity drift, unavailable
pidfd/procfs evidence, close uncertainty, timeout, a new duplicate, or any
survivor yields `UNKNOWN` and forbids adoption.

After two consecutive action snapshots prove exactly one unchanged lineage,
the tool publishes a no-overwrite adoption record through an exclusive
temporary file, file fsync, atomic link, and directory fsync. The record binds
schema, boot ID, canonical script, authority root, and every lineage member's
exact argv/PID/PPID/start ticks plus cwd/executable identities. An existing
byte-identical, currently valid record is reread immediately before idempotent
return; malformed, stale, replaced, or different records are never overwritten.

## Safety and scope

- No external harness, service, live process, remote, deployment, or canonical
  register is changed.
- No automatic fetch is performed; the caller controls when refs are refreshed.
- Replay never changes the source worktree, even on success.
- Watchdog collapse exists as a reviewed repository capability but is not run
  against live `/home/mboyle` processes in this cut.
- The existing batch cap remains one and no automation is re-enabled.
- Every `UNKNOWN` or refusal has a nonzero exit status machine-distinct from
  success.

## Tests

Focused pytest tests use temporary Git repositories, linked worktrees, fake
procfs trees, and injected pidfd operations. They prove:

- committed and entirely uncommitted candidates replay onto newer main while
  their source HEAD/index/files remain byte-for-byte unchanged;
- staged, unstaged, untracked, binary, executable, and symlink state survives;
- inherited Git repository/index/worktree variables cannot retarget replay or
  manufacture an integration verdict;
- ordinary I/O/Unicode failures and cancellation run identity-bound rollback;
- simultaneous same-output replays produce one owner and one refusal, and the
  loser cannot remove the winner;
- manifest adoption refuses source/output/main drift, schema faults, partial
  transaction records, replay-derivation tampering, relative authorities, and
  filesystem-identity replacement before or after evidence collection;
- parent/child watchdog matches are one lineage while independent roots are
  duplicates, exact argv excludes substring lookalikes, collapse never signals
  the retained lineage, and identity drift/timeout forbids record publication;
- integration is refused for non-ancestry, wrong version, a missing or non-
  CLOSED row, and a missing required path; and
- only the complete evidence set emits `ADOPTABLE` or `INTEGRATED`.
