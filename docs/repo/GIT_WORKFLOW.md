# Git Workflow -- Clean Tree Invariants, Sandboxes, Train Stacking, and In-Place Rebase

<!-- verified-against: v3.66.1901 -->

This document codifies the core Git contracts, worktree invariants, sandbox conventions,
train stacking rules, and in-place rebase protocols across the repository and fleet.
Every agent, PM, builder, and human operator is bound by these rules.

---

## 1. Clean Working Tree Invariants

1. **Production Checkout Integrity**:
   - The integrator checkout at `/home/mboyle/BulkDownloader` represents ground truth.
   - No untracked, modified, or uncommitted files may linger in the production checkout.
   - `git status --porcelain` must be clean before and after every integration or release pass.

2. **Fleet Rule 22 Floor Invariant**:
   - `NO WORKTREE DELETED OR RESET, EVER.`
   - Soft or hard resets (`git reset --hard`, `git reset --soft`) and aggressive directory wipes
     are strictly forbidden outside authorized sandboxes.
   - Worktrees are retained for post-mortem forensics and auditability. If an operation fails,
     retain the worktree and allocate a fresh sandbox.

3. **Generated Artifacts & Integrity Gates**:
   - Gitignored generated artifacts (such as `reports/gui_parity_inventory.json`) must be
     regenerated using canonical tools (`toolchain/bin/bd-regen-order`), never hand-edited.
   - Generated paths must remain porcelain-clean relative to the declared base.

---

## 2. Sandbox Branch Naming Conventions & Layout

1. **Authorized Sandboxes (Rule 22 Parity)**:
   - All mutation, test runs, and candidate code development must occur exclusively in authorized sandboxes:
     - `/home/mboyle/bd-cuts/<cut-name>`: Primary feature, fix, and doc cuts.
     - `/home/mboyle/bd-local-wt/<wt-name>`: Local review and ephemeral verification trees.
   - The Antigravity Anti-Hallucination Shield (`~/.gemini/config/hooks.json` -> `shield.py`)
     intercepts shell commands at the PreToolUse boundary and blocks destructive git actions
     outside these directories.

2. **Branch Naming Standard**:
   - Canonical cut branches follow the prefix `cut/<cut-name>`:
     - Feature/Bugfix cuts: `cut/<ticket-or-issue-slug>` (e.g. `cut/fleet-git-doc-modernization`).
     - Rebase/restack cuts: `cut/<slug>-restack-<round>` or `cut/<slug>-r<N>`.
     - Small-fix fast track: `cut/fix-<component>-<id>`.

3. **Worktree Provisioning**:
   - Always branch from `origin/main` (or the explicitly declared BASE commit SHA):
     ```bash
     bash /home/mboyle/bd-cut.sh cut/<cut-name>
     # Or git worktree command:
     git -C /home/mboyle/BulkDownloader worktree add -b cut/<cut-name> /home/mboyle/bd-cuts/<cut-name> origin/main
     ```
   - **SAFETY**: Never run `npm ci` inside a cut worktree if `frontend/node_modules` is symlinked
     to the integrator's shared tree, as `npm ci` will wipe the shared directory.

---

## 3. Train Stacking Rules & Batch Integration

1. **Batch Overlap Detection**:
   - Before stacking or landing multiple cuts, run batch collision analysis:
     ```bash
     venv/bin/python toolchain/bin/bd-batch-overlap-check.py cut/cut1 cut/cut2 --json
     ```
   - The check mechanically inspects all touched files across candidates, including tests
     and generated release files.

2. **Stacking Protocols**:
   - **Independent Partitions**: Cuts with zero file overlap may proceed concurrently and merge
     independently.
   - **Overlapping Sets**: Cuts modifying the same source or test files MUST be sequenced
     in a train. Dependent cuts are stacked directly on the active commit of the preceding cut:
     `cut-B` branches from `cut-A`'s HEAD rather than `origin/main`.
   - **Front-of-Train Priority**: Security patches, contract fixes, and CI shard definitions lead
     the train; documentation and broad refactors stack behind.

3. **Train Assembly Invariants**:
   - Every candidate in a train retains its own distinct BASE commit and review receipt.
   - The train integration sequence preserves individual candidate authorship and audit trails.

---

## 4. In-Place Rebase Protocols

When upstream `origin/main` advances, candidate branches must be updated using in-place rebase
protocols that preserve functional intent and evidence provenance:

1. **Identify and Snapshot Evidence**:
   - Record declared BASE commit: `git rev-parse <declared-base>`.
   - Save the exact index diff and patch digest before rebasing:
     ```bash
     git diff --cached --binary --full-index <declared-base> > /tmp/candidate-pre-rebase.patch
     sha256sum /tmp/candidate-pre-rebase.patch
     ```
   - Diff the INDEX against the declared BASE, never against HEAD (Fleet Rule 9).

2. **In-Place Conflict Resolution**:
   - Rebase candidate onto updated `origin/main` in place within the assigned sandbox worktree.
   - Never send unresolved conflicts downstream.
   - Preserve authored functional intent while adopting upstream imports, API signatures,
     and CI shard declarations.

3. **Digest & Provenance Preservation**:
   - Never reuse an old `PATCH-SHA256` after code changes; compute and record the new
     `PATCH-SHA256` alongside the original provenance.
   - Verify that non-conflicting code retains its exact content digest.

4. **Lens Re-finalization & Landed Verification**:
   - Validate rebased trees with `bd-lens-refinalize-check.sh`:
     ```bash
     bash /home/mboyle/bd-persist/harness/bd-lens-refinalize-check.sh verdict --worktree <path> --base <new-base>
     ```
   - Confirm landing status with `bd-landed-decision.sh` before declaring done:
     ```bash
     bash /home/mboyle/bd-persist/harness/bd-landed-decision.sh --repo /home/mboyle/BulkDownloader <cut-name>
     ```
   - Landed status codes: 0 = LANDED, 1 = OPEN, 4 = UNKNOWN. UNKNOWN is never permission.
