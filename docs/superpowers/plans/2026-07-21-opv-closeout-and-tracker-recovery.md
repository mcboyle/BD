# OPV Closeout and Tracker Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the completed OPV evidence in Git, restore the canonical tracker from the historical Z: archive without dropping rows, merge PR #7, and deploy and validate the exact merged application revision on `stash`.

**Architecture:** Recover the last canonical `TASK_TRACKER_DATA.json` and generated artifacts as the denominator, reconcile only claims supported by repository or live evidence, and regenerate both rendered artifacts. Keep the pre-merge OPV report as an immutable acceptance snapshot, then record the post-merge deployment separately so the historical and final SHAs remain unambiguous.

**Tech Stack:** Git/GitHub CLI, Python 3, openpyxl, PowerShell, PuTTY Plink/PSCP, systemd, BulkDownloader release and capture tooling.

## Global Constraints

- Preserve all unrelated untracked `.superpowers/sdd/*` and `state/` files.
- Never fabricate closure for time-bound, real-challenge, real-tunnel, or operator-only observations.
- Never expose passwords, tokens, private keys, cookies, or vault material.
- Use `TASK_TRACKER_DATA.json` as canonical; generate `TASK_TRACKER.md` and `TASK_TRACKER.xlsx` from it.
- The deployment is complete only when the service is active, `/api/health` is healthy, and `capture.sh --workers=600 --summary` reports zero failed suites.
- Guard GitHub merge against a changed PR head SHA.

---

### Task 1: Recover and reconcile the canonical tracker and OPV documentation

**Files:**
- Create: `TASK_TRACKER_DATA.json`
- Create: `TASK_TRACKER.md`
- Create: `TASK_TRACKER.xlsx`
- Create: `reports/OPV_VALIDATION_REPORT_2026-07-21.md`
- Modify: `docs/PROCESS_CONVENTIONS.md`
- Modify: `docs/superpowers/plans/2026-07-21-template-host-alias-matching.md`
- Modify: `docs/superpowers/specs/2026-07-21-template-host-alias-matching-design.md`

- [ ] Recover the newest intact historical tracker denominator from `Z:\unzipped\files (1)`.
- [ ] Fix the two known completed-row schema defects without changing their meaning.
- [ ] Reconcile completed OPV items, preserve genuinely open observations, and add the three open reachability deferrals.
- [ ] Generate Markdown and XLSX artifacts, then run `--audit` and `--check`.
- [ ] Add the consolidated OPV validation snapshot and update process/current-state prose.
- [ ] Run documentation and focused regression checks.

### Task 2: Publish and merge PR #7 safely

**Interfaces:**
- Consumes: Task 1 commit and green CI.
- Produces: a GitHub merge commit whose SHA is captured for deployment.

- [ ] Stage only intended tracker/report/documentation paths.
- [ ] Commit, push, and wait for `gates` and `postgres-integration` to pass.
- [ ] Mark PR #7 ready for review.
- [ ] Re-read its head SHA and merge with `--match-head-commit`.
- [ ] Record the resulting merge commit from `origin/main`.

### Task 3: Build and deploy the exact merged revision

**Interfaces:**
- Consumes: the merge commit from Task 2.
- Produces: a verified `BulkDownloader_v3_66_811.zip` deployed to `/home/mboyle/BulkDownloader`.

- [ ] Create a clean worktree at the exact merge commit.
- [ ] Run the canonical release builder without `--quick` or `--skip-tests`.
- [ ] Compute SHA-256 and transfer the archive through the saved `stash` profile.
- [ ] Verify the remote archive hash, archive integrity, and embedded version.
- [ ] Apply the archive, clear bytecode caches, restart the service, and assert health.

### Task 4: Run final production acceptance and preserve the receipt

**Interfaces:**
- Consumes: deployed Task 3 archive.
- Produces: fresh evidence for service, health, and the full acceptance suite.

- [ ] Run `capture.sh --workers=600 --summary` and inspect its phase results rather than trusting process exit alone.
- [ ] Confirm zero failed suites and zero failed live tests.
- [ ] Record the deployed SHA, health evidence, and acceptance totals in a post-merge deployment receipt.
- [ ] Commit and publish the receipt without mixing unrelated worktree files.
