# Row 407 Automation Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository-owned, immutable-source candidate replay transaction and a read-only integration verdict that cannot confuse a built candidate with a commit merged into main.

**Architecture:** Candidate replay creates a new linked worktree at an exact main SHA and applies candidate commits and dirty state there; it never rewrites the source worker. Integration proof is a separate read-only command requiring candidate ancestry plus release-, row-, and path-level evidence. Both commands use only Python's standard library and the Git CLI and expose machine-readable JSON.

**Tech Stack:** Python 3 standard library, Git CLI, pytest

**Spec:** `docs/superpowers/specs/2026-08-30-row407-automation-safety-design.md`

## Global Constraints

- Base is `origin/main` commit `44c8701c30b1ec2347aea712bf57cb620140818e`.
- Do not modify or invoke the live external auto-rebaser, nightly lane, watchdog, deployment, remotes, processes, or canonical improvement register.
- Do not run checkout, reset, stash, rebase, cherry-pick, add, apply, or file writes against the source worker.
- Do not automatically fetch; every ref is resolved from the caller's local repository.
- Keep the live batch cap at one and do not re-enable any automation.
- Edit tracked files with `apply_patch`; tests may create isolated temporary repositories.

---

### Task 1: Transactional candidate replay

**Files:**
- Create: `scripts/bd_candidate_replay.py`
- Create: `tests/test_row407_candidate_replay.py`

**Interfaces:**
- Consumes CLI: `--repo PATH --source PATH --expect-head SHA --main-ref REF --output PATH [--json]`
- Produces exit 0 plus a JSON manifest with `source_head`, `merge_base`, `main_sha`, `replayed_head`, `source_state_sha256`, `output_state_sha256`, `output`, and candidate commit list.
- Returns exit 2 for invalid/unsafe/identity-refused input and exit 3 for replay conflict; failed runs leave no output worktree.
- Test utility `RepoCase` owns a temporary repository, a linked source worker,
  Git commit helpers, exact state snapshots, and a `run_replay()` subprocess
  helper; every assertion below drives the real command.

- [ ] **Step 1: Write the replay RED tests**

Create temporary repositories with real linked worktrees and add these behavior tests:

```python
def test_committed_candidate_replays_onto_new_main_without_touching_source(repo_case):
    source_before = repo_case.source_snapshot()
    result = repo_case.run_replay(expect_head=repo_case.source_head)
    assert result.returncode == 0
    assert repo_case.source_snapshot() == source_before
    assert (repo_case.output / "candidate.txt").read_text() == "candidate\n"
    assert (repo_case.output / "main.txt").read_text() == "new main\n"

def test_expected_head_mismatch_refuses_before_output_is_created(repo_case):
    result = repo_case.run_replay(expect_head=repo_case.base_head)
    assert result.returncode == 2
    assert not repo_case.output.exists()
```

The dirty-state test asserts exact cached diff, worktree diff, untracked bytes,
executable mode, and symlink target in the output. The two conflict tests assert
exit 3, absent output, and equality of the complete pre/post source snapshot.
The command-log test uses the same successful committed replay and asserts no
recorded source-targeted argv contains a forbidden mutating Git subcommand.

The command-log test places a forwarding Git executable first on `PATH`, records
real subprocess argv, and fails if any source-targeted command is `checkout`,
`reset`, `stash`, `rebase`, `cherry-pick`, `add`, `apply`, or `rm`.

- [ ] **Step 2: Run the replay tests and verify RED**

Run:

```bash
/home/mboyle/BulkDownloader/venv/bin/python -m pytest -q tests/test_row407_candidate_replay.py
```

Expected: FAIL because `scripts/bd_candidate_replay.py` does not exist.

- [ ] **Step 3: Implement the minimal replay transaction**

The command must:

```python
source_before = fingerprint_source(source)
validate_expected_head_and_no_in_progress_operation(source, expect_head)
main_sha = resolve_commit(repo, main_ref)
candidate_commits = candidate_only_non_merge_commits(source, main_sha)
git(repo, "worktree", "add", "--detach", output, main_sha)
try:
    cherry_pick_each(output, candidate_commits)
    apply_staged_then_unstaged_binary_diffs(source, output)
    copy_safe_untracked_entries_without_following_symlinks(source, output)
    require_same_source_fingerprint(source_before, source)
except ReplayFailure:
    remove_only_created_output_worktree(repo, output)
    require_same_source_fingerprint(source_before, source)
    raise
```

All source Git reads use capture-only commands. Replay commands target only the
new output. Reject merge commits, special untracked files, path traversal,
pre-existing output, source/output repository mismatch, unresolved index state,
and any source fingerprint drift.

- [ ] **Step 4: Run replay tests and verify GREEN**

Run the same focused pytest command. Expected: all replay tests PASS.

- [ ] **Step 5: Commit the replay transaction**

```bash
git add scripts/bd_candidate_replay.py tests/test_row407_candidate_replay.py
git commit -m "fix: replay row candidates without rewriting workers"
```

---

### Task 2: Evidence-backed integration verdict

**Files:**
- Create: `scripts/bd_integration_verdict.py`
- Create: `tests/test_row407_integration_verdict.py`

**Interfaces:**
- Consumes CLI: `--repo PATH --candidate SHA --main-ref REF --expected-version X.Y.Z [--row N] [--require-path PATH ...] [--json]`
- Produces `INTEGRATED`/exit 0 only for the complete evidence set, `NOT_INTEGRATED`/exit 1 for disproven evidence, and `UNKNOWN`/exit 2 for unreadable or invalid evidence.
- Test utility `VerdictRepo` creates literal version files, canonical register
  rows, required paths, and divergent or merged candidate histories; its
  `run_verdict()` helper invokes the real command in JSON mode.

- [ ] **Step 1: Write the integration-verdict RED tests**

Use temporary repositories and literal expected results:

```python
def test_integrated_requires_candidate_ancestry_version_closed_row_and_required_test(verdict_repo):
    result, body = verdict_repo.run_verdict(candidate=verdict_repo.merged_candidate)
    assert result.returncode == 0
    assert body["verdict"] == "INTEGRATED"
    assert body["candidate_sha"] == verdict_repo.merged_candidate
    assert body["main_sha"] == verdict_repo.main_head

def test_non_ancestor_candidate_is_not_integrated_even_when_other_evidence_matches(verdict_repo):
    result, body = verdict_repo.run_verdict(candidate=verdict_repo.divergent_candidate)
    assert result.returncode == 1
    assert body["verdict"] == "NOT_INTEGRATED"
    assert body["evidence"]["candidate_is_ancestor"] is False
```

The remaining tests independently vary only candidate version, main version,
canonical row count/status, required path presence, or ref readability and
assert the documented exit code and evidence key for that boundary.

- [ ] **Step 2: Run verdict tests and verify RED**

Run:

```bash
/home/mboyle/BulkDownloader/venv/bin/python -m pytest -q tests/test_row407_integration_verdict.py
```

Expected: FAIL because `scripts/bd_integration_verdict.py` does not exist.

- [ ] **Step 3: Implement the minimal read-only verdict**

Resolve full candidate/main commits, run `git merge-base --is-ancestor`, read the
version files through `git show`, parse numeric versions, parse exactly one
canonical register table row when `--row` is supplied, and verify each required
path through `git cat-file -e`. Collect every evidence result before emitting:

```python
verdict = "INTEGRATED" if all(required_evidence) else "NOT_INTEGRATED"
```

Invalid refs, malformed versions, unreadable register data, unsafe paths, or Git
execution errors produce `UNKNOWN`, never exit 0.

- [ ] **Step 4: Run verdict tests and verify GREEN**

Run the same focused pytest command. Expected: all verdict tests PASS.

- [ ] **Step 5: Commit the verdict gate**

```bash
git add scripts/bd_integration_verdict.py tests/test_row407_integration_verdict.py
git commit -m "fix: prove candidate ancestry before integration verdict"
```

---

### Task 3: Focused static verification and candidate handoff

**Files:**
- Verify: `scripts/bd_candidate_replay.py`
- Verify: `scripts/bd_integration_verdict.py`
- Verify: `tests/test_row407_candidate_replay.py`
- Verify: `tests/test_row407_integration_verdict.py`

- [ ] **Step 1: Compile and lint the changed Python files**

```bash
/home/mboyle/BulkDownloader/venv/bin/python -m py_compile scripts/bd_candidate_replay.py scripts/bd_integration_verdict.py tests/test_row407_candidate_replay.py tests/test_row407_integration_verdict.py
/home/mboyle/BulkDownloader/venv/bin/python -m ruff check scripts/bd_candidate_replay.py scripts/bd_integration_verdict.py tests/test_row407_candidate_replay.py tests/test_row407_integration_verdict.py
```

- [ ] **Step 2: Re-run the complete focused row407 suite**

```bash
/home/mboyle/BulkDownloader/venv/bin/python -m pytest -q tests/test_row407_candidate_replay.py tests/test_row407_integration_verdict.py
```

- [ ] **Step 3: Verify repository scope and patch hygiene**

```bash
git diff --check origin/main...HEAD
git status --short
git diff --stat origin/main...HEAD
```

Expected: only the design, plan, two tracked scripts, and two focused tests are changed; no register or live-harness path is present.

- [ ] **Step 4: Request independent code review, address Critical/Important findings, and re-run Steps 1–3**

- [ ] **Step 5: Report branch, worktree, commits, RED/GREEN outputs, exact safety evidence, and the explicit fact that live automation remains neutered**
