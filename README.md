# BulkDownloader operator knowledge -- OFF-HOST COPY

This branch is NOT part of `main` and is never merged into it. It exists so the
integration lane's knowledge survives the loss of test5, which until 2026-09-01
held the only copy.

WHY IT IS SEPARATE. The operator harness lives OUTSIDE the repository on
purpose (CLAUDE.md A8): it drives real browsers, real hosts and real
credentials, which A6 forbids a repository test from doing. Nothing here is
part of the product, nothing here is on `main`, and CI does not run on this
branch.

## What is here

    HANDOFF.md          start here. State, the lane, the traps, what is blocked.
    KICKOFF_PROMPT.md   paste into a new session.
    OPERATOR_DECISIONS.md   rulings 1-46, all standing.
    harness/            the ~129 bd-* operator scripts and their 98 tests.
    continuity/         the generated checkpoint.
    *_2026-09-01.md     the adversarial audits: refutation, harness fail-open,
                        register reconciliation, batch plan, struck premises.

## What is NOT here, deliberately

THE CREDENTIAL. `~/.bd-import` is on test5 only, mode 700. In that directory the
FILENAME is the secret and the file is empty. It is excluded from the archive and
from this branch. So this branch is sufficient to resume the WORK and is NOT
sufficient to recover the FLEET; that needs the operator.

Also absent: fleet archives, codex session snapshots and worktree recovery
tarballs -- 2.5G of bulk that is not knowledge.

## Restoring

    git clone -b bd-knowledge <repo> /tmp/kb && cp -a /tmp/kb/. ~/bd-persist/

Then `bash ~/bd-persist/verify.sh`, which proves every live `~/bd-*` executable
is byte-identical to `harness/`. If they differ, the live copy was edited and
never archived.

REFRESH THIS BRANCH after any session that changes the harness or the rulings.
A stale copy is worse than none, because it reads as a backup.
