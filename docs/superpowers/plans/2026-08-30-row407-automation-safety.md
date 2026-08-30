# Row 407 Automation Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship repository-owned candidate replay, durable replay adoption, integration proof, and watchdog identity/collapse primitives without touching or re-enabling the live automation.

**Architecture:** Candidate replay keeps the source immutable, claims one output through a deterministic `O_EXCL` transaction manifest, and rolls back only identities still owned by that transaction. Separate read-only tools validate replay manifests and merged ancestry. A procfs/pidfd utility classifies logical watchdog lineages, collapses only explicitly selected independent duplicates, and publishes an adoption record only after unique bounded settlement.

**Tech Stack:** Python 3 standard library, Git CLI, Linux procfs/pidfd, pytest

**Spec:** `docs/superpowers/specs/2026-08-30-row407-automation-safety-design.md`

## Global Constraints

- Base is frozen row409 HEAD `3aa5e2ce1fe75906381fdc7b29ca21f63a30f9e5`.
- Work only in `/home/mboyle/BulkDownloader/.worktrees/row407-hardening-1358` on `candidate/row407-hardening-1358`.
- Do not modify, install, invoke, copy over, or re-enable `/home/mboyle/bd-autorebase.sh`, `/home/mboyle/bd-night.sh`, `/home/mboyle/bd-watchdog.sh`, or any other live harness file/process.
- Do not touch main, the canonical improvement register, remotes, fleet, deployment, merge state, or service state.
- Keep batch capacity operationally one.
- Use `apply_patch` for tracked edits and stage only explicit inspected paths.
- Every new `tests/test*.py` file declares `BD_GATE_SCOPE = "module"`.
- No pytest, regeneration, build, or other heavy verification runs while the root strict verifier owns compute; notify root before starting tests.
- `UNKNOWN`, refusal, conflict, absence, and duplicate states are nonzero and never share the success path.

---

### Task 1: Hermetic, identity-owned candidate replay

**Files:**
- Modify: `scripts/bd_candidate_replay.py`
- Modify: `tests/test_row407_candidate_replay.py`

**Interfaces:**
- Consumes CLI: `--repo PATH --source PATH --expect-head SHA --main-ref REF --output PATH [--json]`.
- Produces success JSON with `manifest`, `source_head`, `source_state_sha256`, `merge_base`, `main_ref`, `main_sha`, `replayed_head`, `output_state_sha256`, `output`, `candidate_commits`, and filesystem identities.
- Uses deterministic manifest path `OUTPUT.parent / ("." + OUTPUT.name + ".bd-replay.json")`.
- Returns 0 only for `REPLAYED`, 2 for `REFUSED`, and 3 for `CONFLICT`; cancellation is rolled back and re-raised.

- [x] **Step 1: Add RED coverage for ambient Git poisoning and pure-uncommitted replay**

Add `BD_GATE_SCOPE = "module"`. Extend `RepoCase.run_replay()` so test callers can pass an environment. Add controls equivalent to:

```python
def test_poisoned_git_environment_cannot_retarget_replay(repo_case, tmp_path):
    poison = tmp_path / "poison"
    _git(poison, "init", "-b", "main")
    env = dict(os.environ, GIT_DIR=str(poison / ".git"),
               GIT_WORK_TREE=str(poison), GIT_INDEX_FILE=str(tmp_path / "index"))
    result = repo_case.run_replay(env=env)
    assert result.returncode == 0
    assert (repo_case.output / "candidate.txt").read_text() == "candidate\n"

def test_entirely_uncommitted_candidate_replays_onto_main(tmp_path):
    case = RepoCase(tmp_path, commit_candidate=False)
    source_before = _source_snapshot(case.source)
    result = case.run_replay(expect_head=case.base_head)
    assert result.returncode == 0
    assert json.loads(result.stdout)["candidate_commits"] == []
    assert _source_snapshot(case.source) == source_before
    assert (case.output / "candidate.txt").read_text() == "candidate\n"
```

- [x] **Step 2: Add RED concurrency and rollback fault controls**

Use a forwarding Git wrapper with a barrier around `worktree add` to launch two replay subprocesses against one output. Assert exactly one `REPLAYED`, one `OUTPUT_CLAIMED`, the winner output and manifest survive, and the loser never issues `worktree remove`. Inject `OSError`, `UnicodeError`, and a custom `BaseException` after worktree creation; assert the original object/type escapes, owned output is removed, and the unchanged source snapshot survives. Swap the claim path or output inode before rollback and assert cleanup reports retained uncertainty without deleting the replacement.

```python
assert sorted(result.returncode for result in results) == [0, 2]
assert sum(json.loads(r.stdout)["status"] == "REPLAYED" for r in results) == 1
assert case.output.is_dir()
assert case.manifest.is_file()
assert replacement.read_text() == "winner replacement\n"
```

- [x] **Step 3: Run the exact new replay nodes to prove RED**

After the root resource hold is released, run:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_row407_candidate_replay.py -k 'poisoned or uncommitted or concurrent or rollback'
```

Expected: intended failures because Git environment scrubbing, atomic output claims, durable manifests, and BaseException rollback do not yet exist.

- [x] **Step 4: Implement explicit Git environment scrubbing**

Replace ambient merging with an allow-by-construction helper and use it for every Git subprocess:

```python
def _git_environment(*, committer: bool = False) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    if committer:
        env.update(GIT_COMMITTER_NAME="BulkDownloader Candidate Replay",
                   GIT_COMMITTER_EMAIL="candidate-replay@example.invalid")
    return env
```

Do not accept arbitrary caller-supplied `GIT_*` additions. `_cherry_pick()` requests only the fixed committer identity.

- [x] **Step 5: Implement stable snapshots and unsupported-state refusals**

Add two-read quiescence without requiring cleanliness:

```python
def _stable_fingerprint(source: Path) -> str:
    first = _fingerprint(source)
    second = _fingerprint(source)
    if first != second:
        raise ReplayFailure("SOURCE_NOT_QUIESCENT", "source changed between snapshots")
    return first
```

Reject in-progress operations, unresolved entries, merge commits, index mode `160000` submodules, unsafe relative paths, special untracked files, nested output, non-directory/symlink final parents, and repository mismatch with distinct reason codes.

- [x] **Step 6: Implement the fixed atomic transaction record**

Add immutable filesystem identity and claim types:

```python
@dataclass(frozen=True)
class FsIdentity:
    device: int
    inode: int
    mode: int

@dataclass
class ReplayClaim:
    path: Path
    parent: Path
    token: str
    fd: int
    parent_fd: int
    path_identity: FsIdentity
    parent_identity: FsIdentity
    owner: dict[str, object]
```

Open the deterministic manifest with `O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC`, mode `0o600`, write a `CLAIMED` document containing the random token and owner boot/PID/PPID/start-ticks identity, and fsync it. The read/write descriptor permits exact token revalidation without reopening by path. Capture output root and registered Git-dir no-follow identities immediately after worktree creation, including a partial Git registration even if the output directory disappeared.

- [x] **Step 7: Implement identity-bound BaseException rollback**

Wrap every operation after claim acquisition in `except BaseException as primary`. Before each output removal and claim unlink, re-read and compare: claim token, claim inode, parent directory inode, final path via `lstat`, output inode, and registered Git-dir inode. Remove only on a complete match. Attach cleanup failures using `primary.add_note(...)` when available, then bare `raise` so the original exception object/type remains primary.

```python
try:
    return _run_claimed_replay(claim, ...)
except BaseException as primary:
    for note in _rollback_owned(claim, output_owner, repo):
        primary.add_note(note)
    raise
```

- [x] **Step 8: Finalize and fsync the replay manifest**

Rewrite the still-open owned claim descriptor in place with schema 1 and state `REPLAYED`, including its own recorded inode plus all source/output/Git identities and hashes. Use `ftruncate`, complete `os.write` loops, file fsync, verify the final path still names the held inode/token, then fsync the parent directory. Close the descriptor on every exit path.

- [x] **Step 9: Run the complete replay test file GREEN and commit**

After compute release:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q tests/test_row407_candidate_replay.py
```

Expected: all replay tests pass. Stage only the two Task 1 paths and commit `fix: make candidate replay an identity-owned transaction`.

---

### Task 2: Durable replay adoption and hermetic integration proof

**Files:**
- Create: `scripts/bd_candidate_adopt.py`
- Create: `tests/test_row407_candidate_adopt.py`
- Modify: `scripts/bd_integration_verdict.py`
- Modify: `tests/test_row407_integration_verdict.py`

**Interfaces:**
- Adoption CLI: `--manifest PATH [--json]`.
- Adoption results: `ADOPTABLE`/0, `NOT_ADOPTABLE`/1, `UNKNOWN`/2.
- Verdict results remain `INTEGRATED`/0, `NOT_INTEGRATED`/1, `UNKNOWN`/2.

- [x] **Step 1: Write replay-adoption RED tests**

Create a successful replay fixture, then independently vary one premise at a time:

```python
def test_complete_unchanged_manifest_is_adoptable(replayed_case):
    result, body = replayed_case.run_adopt()
    assert result.returncode == 0
    assert body["verdict"] == "ADOPTABLE"

@pytest.mark.parametrize("mutation", (
    "source_bytes", "source_head", "output_bytes", "output_head",
    "main_ref", "manifest_schema", "manifest_inode", "output_inode",
))
def test_any_identity_drift_is_not_adoptable(replayed_case, mutation):
    replayed_case.mutate(mutation)
    result, body = replayed_case.run_adopt()
    assert result.returncode != 0
    assert body["verdict"] in {"NOT_ADOPTABLE", "UNKNOWN"}
```

Also prove a `CLAIMED` partial record, symlink manifest, malformed JSON, missing common Git directory, and injected I/O/Unicode failure produce machine-readable `UNKNOWN`/2 without changing source, output, or manifest.

- [x] **Step 2: Add poisoned-environment verdict RED test**

Add `BD_GATE_SCOPE = "module"` to both existing row407 test files and the new adoption test. Run verdict with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, and `GIT_OBJECT_DIRECTORY` aimed at a divergent repository; assert the result still resolves the explicit `--repo` candidate/main SHAs.

- [x] **Step 3: Run adoption and poison nodes to prove RED**

After compute release:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_row407_candidate_adopt.py \
  tests/test_row407_integration_verdict.py -k 'adopt or poison'
```

Expected: adoption script missing and verdict retargeted by poison on the defective foundation.

- [x] **Step 4: Implement strict read-only manifest adoption**

Open the manifest using `O_RDONLY | O_NOFOLLOW`, bind `fstat` to the manifest's recorded device/inode, require absolute recorded path authorities, parse an exact schema/key set, and reuse replay's read-only fingerprint/common-Git helpers. Independently recompute the recorded merge base and ordered candidate commit list. Immediately before returning, reread the complete manifest and every recorded path identity. Collect readable mismatches as named false evidence and reserve `UNKNOWN` for malformed/unavailable measurement.

```python
evidence = {
    "manifest_identity_matches": manifest_identity == recorded_manifest_identity,
    "repository_matches": common_git_dir == recorded_common_git_dir,
    "source_unchanged": source_head == recorded_source_head and source_hash == recorded_source_hash,
    "output_unchanged": output_head == recorded_output_head and output_hash == recorded_output_hash,
    "main_ref_unchanged": resolved_main == recorded_main_sha,
    "merge_base_matches": actual_merge_base == recorded_merge_base,
    "candidate_commits_match": actual_commits == recorded_commits,
    "manifest_contents_match": final_manifest == initial_manifest,
}
verdict = "ADOPTABLE" if all(evidence.values()) else "NOT_ADOPTABLE"
```

The tool performs no fetch, lock-taking, file write, Git index refresh, worktree mutation, or manifest consumption.

- [x] **Step 5: Scrub integration-verdict Git environments**

Use the same remove-all-ambient-`GIT_*` posture as replay. Add `GIT_OPTIONAL_LOCKS=0`, system/global config isolation, and terminal-prompt refusal explicitly. Preserve the existing exact ancestry/version/row/path evidence contract.

- [x] **Step 6: Run complete adoption+verdict GREEN and commit**

After compute release:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_row407_candidate_adopt.py tests/test_row407_integration_verdict.py
```

Expected: all tests pass. Stage the four Task 2 paths and commit `fix: prove replay and integration adoption from immutable evidence`.

---

### Task 3: Identity-bound watchdog census, collapse, and adoption

**Files:**
- Create: `scripts/bd_watchdog_identity.py`
- Create: `tests/test_row407_watchdog_identity.py`

**Interfaces:**
- Read-only CLI: `--script PATH [--proc-root PATH] [--json]`.
- Action CLI: `--script PATH --adopt-record PATH [--collapse] [--settle-timeout SECONDS] [--json]`.
- Read-only states: `UNIQUE`/0, `ABSENT`/1, `UNKNOWN`/2, `DUPLICATES`/3.
- Action success: `ADOPTED`/0; every refusal, drift, timeout, duplicate, absence, and unknown state is nonzero.

- [x] **Step 1: Write fake-procfs census RED tests**

Create proc entries with NUL-separated cmdline, cwd symlink, robust stat rows whose comm contains spaces, and one boot-id file. Prove exact path matching and lineage grouping:

```python
def test_parent_child_matches_form_one_logical_lineage(proc_case):
    proc_case.add(100, ppid=1, start=1000, argv=["bash", str(proc_case.script)])
    proc_case.add(101, ppid=100, start=1001, argv=["bash", str(proc_case.script)])
    body = proc_case.census()
    assert body["status"] == "UNIQUE"
    assert body["lineages"] == [[100, 101]]

def test_independent_roots_are_duplicates_and_newest_is_authority(proc_case):
    proc_case.add(100, ppid=1, start=1000, argv=["bash", str(proc_case.script)])
    proc_case.add(200, ppid=1, start=2000, argv=["bash", str(proc_case.script)])
    body = proc_case.census()
    assert body["status"] == "DUPLICATES"
    assert body["authority_root"]["pid"] == 200
```

Add controls for a substring lookalike, relative argv resolved through proc cwd, malformed matching stat, PID identity changing between the two stat reads, and unreadable boot ID. Malformed/unreadable evidence must be `UNKNOWN`, not zero matches.

- [x] **Step 2: Write injected pidfd collapse RED tests**

Inject `_pidfd_open`, `_pidfd_send_term`, `_pidfd_wait_ready`, and `_pidfd_close`. Assert duplicate lineage members are signalled leaf-first, authority receives no signal, each exact identity is re-read after pidfd acquisition, and settlement uses fd readiness. Drift PPID/start ticks/argv/boot ID before signalling and timeout pidfd readiness in separate controls; assert no adoption record is published.

```python
assert signalled == [duplicate_child_pid, duplicate_root_pid]
assert authority_pid not in signalled
assert numeric_pid_polls == []
assert not adoption_record.exists()
```

- [x] **Step 3: Write atomic adoption-record RED tests**

Prove record publication occurs only after the post-collapse census returns the exact retained lineage; its document binds boot ID, canonical script, root, and all argv/PID/PPID/start ticks. A byte-identical valid existing record is idempotent. A stale, malformed, symlink, or different record is retained and returns nonzero. Inject temp write/fsync/link/directory-fsync failures and assert no partial final record becomes adoptable.

- [x] **Step 4: Run the watchdog test file to prove RED**

After compute release:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q tests/test_row407_watchdog_identity.py
```

Expected: fail because the watchdog identity utility does not exist.

- [x] **Step 5: Implement exact procfs census and logical lineages**

Use immutable records:

```python
@dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    ppid: int
    start_ticks: int
    argv: tuple[str, ...]
    script: str
    cwd: str
    cwd_device: int
    cwd_inode: int
    executable: str
    executable_device: int
    executable_inode: int
```

Parse `/proc/PID/stat` from its last `)`. Bracket two complete argv/cwd/executable receipts with three stat reads and require all fields to match. Parse Bash options conservatively so option operands are never mistaken for the script; require the proc executable device/inode to name a known Bash binary; then compare the resolved first script operand exactly to the canonical requested script. Build roots from PPID edges among matches, descendants in deterministic `(start_ticks, pid)` order, and select the newest independent root unless a valid existing adoption record names a still-current lineage.

- [x] **Step 6: Implement explicit pidfd collapse**

For action mode, lock `ADOPT_RECORD + ".lock"` with `flock(LOCK_EX)`, repeat census, and refuse duplicates unless `--collapse` is present. For each member in each duplicate lineage, deepest/newest first: open pidfd, repeat the full census, verify exact identity/root membership and the expected survivor set, then take a direct complete receipt with a boot-ID reread immediately before signalling. Signal `SIGTERM` via `signal.pidfd_send_signal`, wait through `poll()` on that pidfd only, and close it. Any revalidation, signal, wait, or close uncertainty aborts with `UNKNOWN`; there is no numeric PID signal/poll fallback and no SIGKILL escalation.

- [x] **Step 7: Implement no-overwrite adoption publication**

After collapse, take a settlement census and a final census and require exactly the original authority lineage with no additional matches. Serialize a canonical JSON record binding every argv/cwd/executable receipt, create an exclusive same-directory temp file, write completely, fsync it, publish with `os.link(temp, final)` so an existing final is never overwritten, fsync the parent directory, then identity-check and unlink only the owned temp. Reread and identity-check an existing record immediately before treating it as idempotent.

- [x] **Step 8: Run watchdog GREEN and commit**

After compute release:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q tests/test_row407_watchdog_identity.py
```

Expected: all tests pass without contacting `/home/mboyle` live processes. Stage the two Task 3 paths and commit `fix: bind watchdog collapse and adoption to process identity`.

---

### Task 4: Exact-scope verification and handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-08-30-row407-automation-safety-design.md`
- Modify: `docs/superpowers/plans/2026-08-30-row407-automation-safety.md`
- Verify all Task 1-3 code/tests.

- [x] **Step 1: Compile and record focused-lint availability after compute release**

```bash
venv/bin/python -m py_compile \
  scripts/bd_candidate_replay.py scripts/bd_candidate_adopt.py \
  scripts/bd_integration_verdict.py scripts/bd_watchdog_identity.py \
  tests/test_row407_candidate_replay.py tests/test_row407_candidate_adopt.py \
  tests/test_row407_integration_verdict.py tests/test_row407_watchdog_identity.py
venv/bin/python -m ruff check \
  scripts/bd_candidate_replay.py scripts/bd_candidate_adopt.py \
  scripts/bd_integration_verdict.py scripts/bd_watchdog_identity.py \
  tests/test_row407_candidate_replay.py tests/test_row407_candidate_adopt.py \
  tests/test_row407_integration_verdict.py tests/test_row407_watchdog_identity.py
```

Do not install a new formatter into the frozen candidate environment. Record
`ruff` as unavailable if the checked-in virtual environment has no such module;
that is a missing optional static signal, not a passing lint result.

- [x] **Step 2: Run the complete focused row407 floor after compute release**

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_row407_candidate_replay.py tests/test_row407_candidate_adopt.py \
  tests/test_row407_integration_verdict.py tests/test_row407_watchdog_identity.py
```

Record expected, collected, executed, pass/fail/error/skip counts and the exact HEAD/tree after the final source edit.

- [x] **Step 3: Verify scope, generated impact, and patch hygiene**

```bash
git diff --check 3aa5e2ce1fe75906381fdc7b29ca21f63a30f9e5...HEAD
git status --short
git diff --name-status 3aa5e2ce1fe75906381fdc7b29ca21f63a30f9e5...HEAD
git diff --stat 3aa5e2ce1fe75906381fdc7b29ca21f63a30f9e5...HEAD
```

Assert no live `/home/mboyle` harness, register, version, application runtime, remote, fleet, or deployment path is present. Ask root before any regeneration because generated docs may be integration-owned.

- [x] **Step 4: Obtain independent code/test-integrity review**

Give the reviewer exact HEAD/tree plus the six incident blockers. Address every Critical/Important and actionable Minor finding, rerun invalidated focused evidence, and freeze a new exact head.

- [x] **Step 5: Finalize docs and commit explicit paths**

Run placeholder/contradiction scans, update the spec/plan only to match shipped behavior, stage exactly the two docs, and commit `docs: specify identity-bound automation recovery`.

#### Execution evidence (2026-08-30)

- Frozen base: `3aa5e2ce1fe75906381fdc7b29ca21f63a30f9e5`.
- Source-code freeze: `b047e4b36cd34012ecdec76a0a044d055328bf36`;
  final focused-test checkpoint before this documentation commit:
  `947fc06d48df7a05d1bb6ce2c11d1609ec927222`.
- Checkpoints: `2a7ab01a` (replay transaction), `8333afe4` (adoption and
  integration proof), `4f302956` (watchdog identity), `d9a6e947` (publication
  revalidation), `bad7ece9` (independent-review gaps), `9bcfde3f` (replay
  derivation), `b047e4b3` (final adoption receipts), and `947fc06d`
  (measurement-fault controls).
- The focused four-module floor passed 81/81 with four local pytest workers:
  replay 22, adoption 26, integration verdict 12, watchdog 21. All eight
  changed Python files compile. The frozen virtual environment has no `ruff`
  module, so no lint pass is claimed and no package was installed.
- Independent review identified process exec/argv races, late output claims,
  partial registration cleanup, intent-to-add handling, evidence error
  classification, replay-derivation trust, relative path authorities, and
  post-read adoption races. Each actionable finding has a focused regression
  and was rerun after its fix.
- Scope is exactly the two documents, four repository utilities, and four
  focused test modules listed above. No register, release, runtime, live
  harness, remote, fleet, deployment, or service path was changed. No live
  watchdog census/collapse or automation activation was run; batch capacity
  remains one.

- [ ] **Step 6: Report the candidate without activating it**

Report branch/worktree/base/head/tree, commits, RED/GREEN provenance, test denominators, review disposition, changed paths, and explicit facts that live automation remains neutered, no watchdog collapse was run, batch capacity remains one, and no main/register/remote/fleet/deploy state was touched.
