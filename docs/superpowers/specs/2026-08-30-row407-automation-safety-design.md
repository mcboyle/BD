# Row 407 Automation Safety Design

## Goal

Replace the unsafe, untracked worktree-recovery contract with repository-owned
building blocks that cannot discard a committed candidate head and cannot call
a candidate integrated without proving it is contained in `origin/main`.

## Incident boundary

The disabled external recovery path assumed every worker held only uncommitted
changes. It ran stash, detached the worker at current main, then popped the
stash. Workflow-produced rows can commit their candidate. For those rows the
stash is empty, so detaching the worker removes the candidate head from the
visible recovery path. Pop or cherry-pick conflicts can also leave a partially
rewritten worker.

The live external auto-rebaser and nightly retry step remain disabled. This
candidate does not install, copy, invoke, or re-enable them.

## Components

### Transactional candidate replay

`scripts/bd_candidate_replay.py` takes a repository, source worktree, exact
expected source HEAD, main ref, and caller-chosen output worktree. It treats the
source as immutable evidence:

1. Resolve and validate the repository, source HEAD, main SHA, and merge base.
2. Refuse an in-progress source Git operation or an output path that already
   exists.
3. Fingerprint source HEAD, index, tracked worktree bytes, untracked paths, and
   relevant modes before doing any replay work.
4. Create a new detached output worktree at the exact resolved main SHA.
5. Cherry-pick the candidate-only, non-merge commits in order.
6. Apply staged and unstaged binary diffs in their original index/worktree
   classes, then copy safe untracked files and symlinks without following them.
7. Re-fingerprint the source. If it changed, or any replay step conflicts,
   abort and remove only the output worktree.
8. On success, leave the output worktree in place and emit a JSON manifest
   binding source HEAD, merge base, main SHA, replayed HEAD, dirty-state hash,
   and output path.

The source worktree never receives `checkout`, `reset`, `stash`, `rebase`,
`cherry-pick`, index writes, or file writes. This makes conflict rollback a
deletion of newly created output rather than an attempted reconstruction of the
worker.

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

Missing or unreadable evidence is UNKNOWN/refusal, never success. The JSON
result includes the resolved candidate and main SHAs so callers cannot confuse
a version-like directory or marker with a merged commit.

## Safety and scope

- No external harness, service, process, remote, or canonical register is
  changed.
- No automatic fetch is performed; the caller controls when `origin/main` was
  refreshed.
- Replay never changes the source worktree, even on success.
- The watchdog duplicate-process incident is intentionally excluded from this
  candidate.
- The existing batch cap remains one and no automation is re-enabled.

## Tests

Focused pytest tests create temporary local Git repositories and real linked
worktrees. They prove:

- a committed candidate is replayed onto newer main while its source HEAD stays
  visible and unchanged;
- staged, unstaged, untracked, binary, executable, and symlink state survives;
- a cherry-pick conflict removes the output and leaves source HEAD/index/files
  byte-for-byte unchanged;
- an expected-head mismatch refuses before creating output;
- integration is refused for non-ancestry, wrong version, a missing or non-
  CLOSED row, and a missing required test/path; and
- only the complete evidence set emits `INTEGRATED`.
