# Code-Intelligence Governance and Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the source-bound review ledger, lossless governance registries, finding/invariant promotion workflow, bounded runtime gates, contention-safe review allocator, and final fail-closed composite audit gate.

**Architecture:** Governance state is stored in validated deterministic JSON and mutated only through compare-and-swap plus atomic replacement. Thin command frontends call `tools/code_intelligence/` services; runtime work is dispatched only through typed allowlisted adapters with normalized results and deadlines. The existing specialized audit tools remain authoritative adapters, while `bd-audit-gate.py` becomes the final orchestrator only after every standalone component is independently green.

**Tech Stack:** Python 3.11+ standard library (`argparse`, `ast`, `dataclasses`, `hashlib`, `json`, `multiprocessing`, `pathlib`, `subprocess`, `tempfile`, `time`, `typing`), pytest, existing Flask test-client fixtures, existing `tools/code_intelligence/` shared core.

## Global Constraints

1. Python standard library is the required runtime baseline. Optional packages such as `libcst`, `hypothesis`, `radon`, `bandit`, and `vulture` may enhance isolated audit runs but cannot be required by normal release gates.
2. Every durable artifact carries schema name and version; tracked-tree source SHA; tool version; deterministic input hashes; and generation timestamp separated from content used for deterministic comparisons.
3. Durable writes are validate-then-atomically-replace. A failed run must not leave a plausible partial artifact.
4. All paths are explicitly supplied or derived from a discovered repository root. `/home/claude`, `/root`, and workstation-specific paths are not defaults in canonical interfaces.
5. Outputs exclude secret values, credentials, cookies, authorization headers, signed queries, and raw captured bodies.
6. Advisory findings and release-blocking failures are distinct result states.
7. Existing CLI behavior remains available through compatibility wrappers or adapters.
8. New behavior follows RED -> GREEN -> refactor. Each test must be observed failing for the intended missing behavior before implementation.
9. No production service behavior changes.
10. No automatic code fixes or automatic promotion of findings.
11. No arbitrary Python expression evaluation in probes or contracts.
12. No network-dependent default analysis.
13. No replacement of specialized tools that already provide stronger domain behavior.
14. No declaration that heuristic call, taint, dead-code, or reachability results are proven facts.
15. No commit, merge, push, external static-KB pin advancement, or release cut during implementation. Every task ends with a pre-commit checkpoint and leaves the worktree uncommitted.

---

## Dependency Order

This plan is the governance/gates workstream. Execute it after `docs/superpowers/plans/2026-07-23-code-intelligence-foundation-graph.md` has created and tested:

- `tools/code_intelligence/snapshot.py` with `TreeSnapshot`, `FileFact`, and `build_snapshot(root, include=None)`;
- `tools/code_intelligence/artifacts.py` with `canonical_bytes(value)`, `atomic_write_json(path, value, validator) -> None`, and `artifact_hash(value)`;
- `tools/code_intelligence/results.py` with `ResultState`, `CheckResult(name, state, summary, evidence)`, and `exit_code(results, gate)`;
- `tools/code_intelligence/schemas.py` with `make_envelope(...)`, `validate_envelope(...)`, and `validate_projection(...)`;
- `tools/code_intelligence/paths.py` with repository discovery and normalized tracked paths;
- `tools/code_intelligence/adapters.py` with a typed registry that does not import Flask at module import time; and
- `tools/code_intelligence/locking.py` with the portable exclusive-file-lock context manager defined by the foundation plan.

Task 1 creates `tools/code_intelligence/governance_io.py` as a governance-local adapter. Its `load_json_object(path)`, `validate_governance_artifact(kind, value)`, and `write_validated_json(path, value, validator) -> str` wrappers are not foundation APIs: they compose `validate_envelope`, governance schema validation, `atomic_write_json`, and `artifact_hash`.

Tasks 1-6 may begin after those foundation interfaces are green. Task 7 additionally depends on `docs/superpowers/plans/2026-07-23-code-intelligence-analysis-frontends.md` producing current, source-bound `RISK_SCORES.json`, `COVERAGE_GAPS.json`, and graph projections. Task 8 additionally depends on that analysis-frontends plan's standalone `semantic_diff.py`, `reachability.py`, `differential_oracle.py`, `fuzz_harness.py`, and `bd-coverage-map` tests being green. Do not stub a missing dependency to make this plan appear complete.

## File Map

### New governance implementation files

- `tools/code_intelligence/review_state.py` - canonical review-state creation, live-tree reconciliation, re-audit manifest derivation, and compare-and-swap writes.
- `tools/code_intelligence/governance_io.py` - governance-local JSON loading, schema dispatch, atomic-write composition, and returned content hash.
- `tools/code_intelligence/registries.py` - lossless invariant and contract registry loading, migration, validation, and atomic rendering.
- `tools/code_intelligence/findings.py` - stable finding IDs, normalized finding upsert, expected-SHA enforcement, and deterministic RED-stub proposal rendering.
- `tools/code_intelligence/invariant_promotion.py` - confirmed-finding validation and atomic invariant promotion.
- `tools/code_intelligence/probe_allowlist.py` - explicit operation, pure-call, Flask-app, file assertion, and subprocess-tool allowlists.
- `tools/code_intelligence/probes.py` - bounded invariant-probe execution and evidence normalization.
- `tools/code_intelligence/contracts.py` - typed precondition/postcondition registry and bounded adapter execution.
- `tools/code_intelligence/review_allocator.py` - deterministic risk routing, lease validation, claim recovery, and contention-safe allocation.
- `tools/code_intelligence/audit_gate.py` - importable composite-gate component table, runner, bitmask policy, and machine result.
- `tools/bd_finding.py` - stable Python frontend for `bd-finding`.
- `tools/bd_invariant.py` - stable Python frontend for `bd-invariant`.
- `tools/invariant_probe.py` - stable probe frontend.
- `tools/contract_harness.py` - stable runtime contract frontend.
- `tools/bd_review_next.py` - stable review-allocation frontend.
- `toolchain/bin/bd-finding` - executable compatibility wrapper.
- `toolchain/bin/bd-invariant` - executable compatibility wrapper.
- `toolchain/bin/bd-review-next` - executable compatibility wrapper.

### Existing files to modify

- `tools/seed_review_state.py` - compatibility wrapper over canonical review-state APIs; remove workstation defaults.
- `tools/staleness.py` - compatibility wrapper over direct live-tree reconciliation; preserve old subcommands.
- `tools/review_merge.py` - use canonical compare-and-swap state mutation and explicit roots.
- `tools/invariants.py` - validate/render the canonical registry without rebuilding it from a lossy Python constant.
- `tools/consumer_agreement.py` - accept explicit repository root and normalized v2 contract records.
- `tools/bd-audit-gate.py` - final composite orchestration, unique bitmask, JSON result, and required-component policy.
- `project-knowledge/INVARIANTS.json` - losslessly migrate all 11 current records to the normalized source-bound schema.
- `project-knowledge/CONTRACTS.json` - migrate the existing producer/consumer record into the normalized contract schema.
- `project-knowledge/CODE_INTELLIGENCE_TOOLING.md` - document exact commands, exit codes, and standalone/composite use.
- `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md` - replace intended-only ledger/invariant/contract shapes with implemented schemas.

### New tests

- `tests/test_code_intelligence_review_state.py`
- `tests/test_code_intelligence_registries.py`
- `tests/test_bd_finding.py`
- `tests/test_bd_invariant.py`
- `tests/test_invariant_probe.py`
- `tests/test_contract_harness.py`
- `tests/test_bd_review_next.py`
- `tests/test_bd_audit_gate_composite.py`

---

### Task 1: Canonical REVIEW_STATE and Live-Tree Staleness Gate

**Files:**
- Create: `tools/code_intelligence/governance_io.py`
- Create: `tools/code_intelligence/review_state.py`
- Create: `tests/test_code_intelligence_review_state.py`
- Modify: `tools/seed_review_state.py`
- Modify: `tools/staleness.py`
- Modify: `tools/review_merge.py`

**Interfaces:**
- Consumes:
  - `TreeSnapshot.source_sha: str`
  - `TreeSnapshot.files: Mapping[str, FileFact]`
  - `FileFact.sha256: str`
  - `FileFact.lines: int`
  - `write_validated_json(path: Path, payload: dict[str, object], validator: Callable[[dict[str, object]], None]) -> str`
  - `exclusive_file_lock(lock_path: Path, *, timeout_seconds: float, stale_after_seconds: float) -> ContextManager[None]`
- Produces:
  - `new_review_state(snapshot: TreeSnapshot, *, tool_version: str, generated_at: str) -> dict[str, object]`
  - `reconcile_review_state(state: Mapping[str, object], snapshot: TreeSnapshot, *, generated_at: str) -> tuple[dict[str, object], tuple[str, ...]]`
  - `write_reconciled_review_state(*, state_path: Path, reaudit_path: Path, snapshot: TreeSnapshot, expected_state_sha: str | None, generated_at: str, gate: bool) -> ReviewStateResult`
  - `merge_audit_into_state(*, state: Mapping[str, object], audit: Mapping[str, object], expected_claim_id: str, expected_owner: str, generated_at: str) -> dict[str, object]`
  - `ReviewStateResult(status: str, state_sha: str, source_sha: str, stale_paths: tuple[str, ...], missing_paths: tuple[str, ...], added_paths: tuple[str, ...])`
  - Gate exit codes: `0=pass`, `1=stale reviewed files`, `2=invalid input/schema`, `3=compare-and-swap conflict`, `4=write/lock error`.

- [ ] **Step 1: Write the failing direct-live-tree reconciliation test**

```python
from pathlib import Path

from tools.code_intelligence.review_state import reconcile_review_state
from tools.code_intelligence.snapshot import build_snapshot


def test_reviewed_file_byte_drift_becomes_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "bulk_downloader").mkdir(parents=True)
    source = repo / "bulk_downloader" / "worker.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    snapshot_before = build_snapshot(repo)
    before_files = {fact.path: fact for fact in snapshot_before.files}
    rel = "bulk_downloader/worker.py"
    state = {
        "schema_name": "bd.review-state",
        "schema_version": 2,
        "source_sha": snapshot_before.source_sha,
        "tool_version": "1.0.0",
        "input_hashes": {"tracked_snapshot": snapshot_before.source_sha},
        "generated_at": "2026-07-23T00:00:00Z",
        "files": {
            rel: {
                "sha256": before_files[rel].sha256,
                "lines": 1,
                "status": "reviewed",
                "review_level": "L2",
                "reviewed_at_sha": before_files[rel].sha256,
                "finding_ids": [],
                "invariant_ids": [],
                "contract_ids": [],
                "test_ids": [],
                "evidence_hashes": ["e" * 64],
                "claim": None,
                "stale_reason": None,
                "reaudit_required": False,
            }
        },
        "findings": {},
    }
    source.write_text("VALUE = 2\n", encoding="utf-8")
    snapshot_after = build_snapshot(repo)

    reconciled, stale = reconcile_review_state(
        state,
        snapshot_after,
        generated_at="2026-07-23T00:01:00Z",
    )

    assert stale == (rel,)
    assert reconciled["files"][rel]["status"] == "stale"
    assert reconciled["files"][rel]["reaudit_required"] is True
    assert reconciled["files"][rel]["stale_reason"] == "tracked file SHA changed"
    assert reconciled["files"][rel]["reviewed_at_sha"] == before_files[rel].sha256
    assert reconciled["source_sha"] == snapshot_after.source_sha
```

- [ ] **Step 2: Run the test and observe the intended RED result**

Run:

```bash
python -m pytest tests/test_code_intelligence_review_state.py::test_reviewed_file_byte_drift_becomes_stale -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'tools.code_intelligence.review_state'`.

- [ ] **Step 3: Implement the immutable reconciliation core**

```python
# tools/code_intelligence/governance_io.py
import json
from pathlib import Path
from typing import Callable

from .artifacts import atomic_write_json, artifact_hash
from .schemas import validate_envelope, validate_projection


def load_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate_governance_artifact(kind: str, value: dict[str, object]) -> None:
    validate_envelope(value)
    validate_projection(kind, value)


def write_validated_json(
    path: Path,
    value: dict[str, object],
    validator: Callable[[dict[str, object]], None],
) -> str:
    atomic_write_json(path, value, validator)
    return artifact_hash(value)
```

```python
# tools/code_intelligence/review_state.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .artifacts import artifact_hash
from .governance_io import write_validated_json
from .locking import exclusive_file_lock
from .governance_io import validate_governance_artifact
from .snapshot import TreeSnapshot


@dataclass(frozen=True)
class ReviewStateResult:
    status: str
    state_sha: str
    source_sha: str
    stale_paths: tuple[str, ...]
    missing_paths: tuple[str, ...]
    added_paths: tuple[str, ...]


def _new_file_record(sha256: str, lines: int) -> dict[str, object]:
    return {
        "sha256": sha256,
        "lines": lines,
        "status": "unreviewed",
        "review_level": "none",
        "reviewed_at_sha": None,
        "finding_ids": [],
        "invariant_ids": [],
        "contract_ids": [],
        "test_ids": [],
        "evidence_hashes": [],
        "claim": None,
        "stale_reason": "new tracked file",
        "reaudit_required": True,
    }


def new_review_state(
    snapshot: TreeSnapshot,
    *,
    tool_version: str,
    generated_at: str,
) -> dict[str, object]:
    state: dict[str, object] = {
        "schema_name": "bd.review-state",
        "schema_version": 2,
        "source_sha": snapshot.source_sha,
        "tool_version": tool_version,
        "input_hashes": {"tracked_snapshot": snapshot.source_sha},
        "generated_at": generated_at,
        "files": {
            path: _new_file_record(f.sha256, f.lines)
            for path, f in sorted(
                ((fact.path, fact) for fact in snapshot.files),
                key=lambda item: item[0],
            )
        },
        "findings": {},
    }
    validate_governance_artifact("review_state", state)
    return state


def reconcile_review_state(
    state: Mapping[str, object],
    snapshot: TreeSnapshot,
    *,
    generated_at: str,
) -> tuple[dict[str, object], tuple[str, ...]]:
    out = deepcopy(dict(state))
    files = out["files"]
    tracked_by_path = {fact.path: fact for fact in snapshot.files}
    stale: list[str] = []
    for path, tracked in sorted(tracked_by_path.items()):
        record = files.get(path)
        if record is None:
            files[path] = _new_file_record(tracked.sha256, tracked.lines)
            continue
        old_sha = record["sha256"]
        if old_sha != tracked.sha256:
            if record["status"] in {"reviewed", "in_progress"}:
                stale.append(path)
            record["status"] = "stale"
            record["stale_reason"] = "tracked file SHA changed"
            record["reaudit_required"] = True
            record["claim"] = None
            record["sha256"] = tracked.sha256
            record["lines"] = tracked.lines
    for path in sorted(set(files) - set(tracked_by_path)):
        record = files[path]
        record["status"] = "stale"
        record["stale_reason"] = "tracked file removed"
        record["reaudit_required"] = True
        record["claim"] = None
        stale.append(path)
    out["source_sha"] = snapshot.source_sha
    out["input_hashes"]["tracked_snapshot"] = snapshot.source_sha
    out["generated_at"] = generated_at
    validate_governance_artifact("review_state", out)
    return out, tuple(sorted(set(stale)))


def merge_audit_into_state(
    *,
    state: Mapping[str, object],
    audit: Mapping[str, object],
    expected_claim_id: str,
    expected_owner: str,
    generated_at: str,
) -> dict[str, object]:
    if audit["claim_id"] != expected_claim_id or audit["owner"] != expected_owner:
        raise ValueError("audit ownership does not match claim")
    if audit["source_sha"] != state["source_sha"]:
        raise ValueError("audit source SHA differs from review state")
    out = deepcopy(dict(state))
    evidence = tuple(sorted(set(audit.get("evidence_hashes", []))))
    for path in audit["files"]:
        record = out["files"][path]
        claim = record.get("claim") or {}
        if claim.get("claim_id") != expected_claim_id:
            raise ValueError(f"file is not owned by claim: {path}")
        record["evidence_hashes"] = sorted(
            set(record["evidence_hashes"]) | set(evidence)
        )
    for finding in audit.get("findings", []):
        out["findings"][finding["id"]] = finding
    out["generated_at"] = generated_at
    validate_governance_artifact("review_state", out)
    return out
```

- [ ] **Step 4: Run the reconciliation test to GREEN**

Run:

```bash
python -m pytest tests/test_code_intelligence_review_state.py::test_reviewed_file_byte_drift_becomes_stale -q
```

Expected: `1 passed`.

- [ ] **Step 5: Add failing tests for atomic write, REAUDIT.txt, gate failure, and compare-and-swap**

```python
import json

import pytest

from tools.code_intelligence.artifacts import artifact_hash
from tools.code_intelligence.review_state import (
    ReviewStateConflict,
    new_review_state,
    write_reconciled_review_state,
)


def test_gate_writes_state_and_reaudit_then_reports_fail(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "bulk_downloader").mkdir(parents=True)
    source = repo / "bulk_downloader" / "worker.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    before = build_snapshot(repo)
    state = new_review_state(before, tool_version="1.0.0", generated_at="2026-07-23T00:00:00Z")
    state["files"]["bulk_downloader/worker.py"]["status"] = "reviewed"
    before_files = {fact.path: fact for fact in before.files}
    state["files"]["bulk_downloader/worker.py"]["reviewed_at_sha"] = before_files[
        "bulk_downloader/worker.py"
    ].sha256
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    expected = artifact_hash(state)
    source.write_text("VALUE = 2\n", encoding="utf-8")

    result = write_reconciled_review_state(
        state_path=state_path,
        reaudit_path=tmp_path / "REAUDIT.txt",
        snapshot=build_snapshot(repo),
        expected_state_sha=expected,
        generated_at="2026-07-23T00:01:00Z",
        gate=True,
    )

    assert result.status == "fail"
    assert result.stale_paths == ("bulk_downloader/worker.py",)
    assert (tmp_path / "REAUDIT.txt").read_text(encoding="utf-8") == (
        "bulk_downloader/worker.py\n"
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))["files"][
        "bulk_downloader/worker.py"
    ]["status"] == "stale"


def test_expected_sha_conflict_writes_nothing(tmp_path: Path) -> None:
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text('{"schema":{"name":"bd.review-state","version":2}}\n', encoding="utf-8")
    before = state_path.read_bytes()
    with pytest.raises(ReviewStateConflict):
        write_reconciled_review_state(
            state_path=state_path,
            reaudit_path=tmp_path / "REAUDIT.txt",
            snapshot=build_snapshot(tmp_path),
            expected_state_sha="0" * 64,
            generated_at="2026-07-23T00:01:00Z",
            gate=True,
        )
    assert state_path.read_bytes() == before
    assert not (tmp_path / "REAUDIT.txt").exists()
```

- [ ] **Step 6: Implement locked compare-and-swap and deterministic REAUDIT rendering**

```python
# append to tools/code_intelligence/review_state.py
class ReviewStateConflict(RuntimeError):
    pass


def _render_reaudit(paths: tuple[str, ...]) -> bytes:
    return ("".join(f"{path}\n" for path in sorted(paths))).encode("utf-8")


def write_reconciled_review_state(
    *,
    state_path: Path,
    reaudit_path: Path,
    snapshot: TreeSnapshot,
    expected_state_sha: str | None,
    generated_at: str,
    gate: bool,
) -> ReviewStateResult:
    from .governance_io import load_json_object

    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with exclusive_file_lock(
        lock_path,
        timeout_seconds=10.0,
        stale_after_seconds=300.0,
    ):
        state = load_json_object(state_path)
        current_sha = artifact_hash(state)
        if expected_state_sha is not None and current_sha != expected_state_sha:
            raise ReviewStateConflict(
                f"review state changed: expected {expected_state_sha}, found {current_sha}"
            )
        reconciled, stale = reconcile_review_state(
            state,
            snapshot,
            generated_at=generated_at,
        )
        removed = tuple(
            sorted(set(state["files"]) - {fact.path for fact in snapshot.files})
        )
        added = tuple(
            sorted({fact.path for fact in snapshot.files} - set(state["files"]))
        )
        all_reaudit = tuple(
            sorted(
                path
                for path, record in reconciled["files"].items()
                if record.get("reaudit_required") is True
            )
        )
        write_validated_json(
            state_path,
            reconciled,
            lambda value: validate_governance_artifact("review_state", value),
        )
        reaudit_path.parent.mkdir(parents=True, exist_ok=True)
        temp = reaudit_path.with_suffix(reaudit_path.suffix + ".tmp")
        temp.write_bytes(_render_reaudit(all_reaudit))
        temp.replace(reaudit_path)
        new_sha = artifact_hash(reconciled)
        return ReviewStateResult(
            status="fail" if gate and stale else "pass",
            state_sha=new_sha,
            source_sha=snapshot.source_sha,
            stale_paths=stale,
            missing_paths=removed,
            added_paths=added,
        )
```

- [ ] **Step 7: Run the full review-state test file**

Run:

```bash
python -m pytest tests/test_code_intelligence_review_state.py -q
```

Expected: all review-state tests pass; no test is skipped.

- [ ] **Step 8: Convert legacy scripts into compatibility wrappers**

`tools/seed_review_state.py`, `tools/staleness.py`, and `tools/review_merge.py` must discover or accept the repository root, state path, graph path, and re-audit path. Preserve existing subcommand names, but route all writes through `write_reconciled_review_state()` or an equivalent locked compare-and-swap mutation in `review_state.py`. The canonical CLI arguments are:

```python
parser.add_argument("--repo", type=Path, default=None)
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--reaudit", type=Path, required=True)
parser.add_argument("--expected-state-sha")
parser.add_argument("--gate", action="store_true")
parser.add_argument("--json", action="store_true")
```

The JSON result is:

```json
{
  "status": "fail",
  "source_sha": "64-hex",
  "state_sha": "64-hex",
  "stale_paths": ["bulk_downloader/worker.py"],
  "missing_paths": [],
  "added_paths": []
}
```

- [ ] **Step 9: Prove compatibility and fail-closed behavior**

Run:

```bash
python -m pytest \
  tests/test_code_intelligence_review_state.py \
  tests/test_audit_promotion_wirings_533.py -q
python tools/staleness.py --help
python tools/seed_review_state.py --help
python tools/review_merge.py --help
```

Expected: tests pass; each help command exits `0`; no help text contains `/home/claude` or `/root` as a default.

- [ ] **Step 10: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/review_state.py \
  tools/seed_review_state.py \
  tools/staleness.py \
  tools/review_merge.py \
  tests/test_code_intelligence_review_state.py
git status --short
```

Expected: `git diff --check` is silent; the diff is limited to Task 1 files; all changes remain uncommitted.

---

### Task 2: Lossless INVARIANTS and Normalized CONTRACTS Registries

**Files:**
- Create: `tools/code_intelligence/registries.py`
- Create: `tests/test_code_intelligence_registries.py`
- Modify: `tools/invariants.py`
- Modify: `tools/consumer_agreement.py`
- Modify: `project-knowledge/INVARIANTS.json`
- Modify: `project-knowledge/CONTRACTS.json`

**Interfaces:**
- Consumes:
  - `validate_governance_artifact(kind: str, payload: Mapping[str, object]) -> None`
  - `artifact_hash(payload: object) -> str`
  - `write_validated_json(path: Path, payload: dict[str, object], validator: Callable[..., None]) -> str`
- Produces:
  - `load_invariant_registry(path: Path) -> InvariantRegistry`
  - `write_invariant_registry(path: Path, registry: InvariantRegistry, *, expected_sha: str | None) -> str`
  - `load_contract_registry(path: Path) -> ContractRegistry`
  - `write_contract_registry(path: Path, registry: ContractRegistry, *, expected_sha: str | None) -> str`
  - `migrate_invariants_v1(payload: Mapping[str, object], *, source_sha: str, tool_version: str, generated_at: str) -> dict[str, object]`
  - `migrate_contracts_v1(payload: Mapping[str, object], *, source_sha: str, tool_version: str, generated_at: str) -> dict[str, object]`
  - Canonical invariant record fields: `id`, `statement`, `at`, `why`, `status`, `guard_test`, `probe`, `provenance`, `extensions`.
  - Canonical contract record fields: `id`, `adapter`, `target`, `fixtures`, `preconditions`, `postconditions`, `allowed_raises`, `side_effects`, `cleanup`, `provenance`, `extensions`.

- [ ] **Step 1: Write failing lossless migration tests using the current CAP-01 records**

```python
from pathlib import Path

from tools.code_intelligence.registries import (
    load_contract_registry,
    load_invariant_registry,
    migrate_contracts_v1,
    migrate_invariants_v1,
)


def test_invariant_migration_preserves_unknown_fields_and_all_ids() -> None:
    old = {
        "schema": 1,
        "invariants": {
            "I0001": {
                "statement": "must hold",
                "at": "bulk_downloader/runner.py",
                "why": "deadlock",
                "status": "GUARDED",
                "guard_test": "tests/test_guard.py::test_guard",
                "custom_note": "retain me",
            },
            "I-CAP01-rec-url-shape": {
                "statement": "URL is validated",
                "at": "bulk_downloader/live_recorder.py",
                "status": "UNGUARDED",
                "guard_test": None,
                "added_by": "CAP-01",
            },
        },
    }

    migrated = migrate_invariants_v1(
        old,
        source_sha="a" * 64,
        tool_version="1.0.0",
        generated_at="2026-07-23T00:00:00Z",
    )

    assert tuple(migrated["invariants"]) == (
        "I-CAP01-rec-url-shape",
        "I0001",
    )
    assert migrated["invariants"]["I0001"]["extensions"]["custom_note"] == "retain me"
    assert migrated["invariants"]["I-CAP01-rec-url-shape"]["provenance"] == {
        "added_by": "CAP-01"
    }


def test_contract_migration_retains_consumer_agreement() -> None:
    old = {
        "contracts": [{
            "id": "CT-rec-url-shape",
            "file": "bulk_downloader/live_recorder.py",
            "symbol": "rec.url",
            "guard_signature": "^https?://",
            "producers": ["watch", "_load_state"],
            "consumers_relying": ["_is_room_live", "_build_cmd"],
            "note": "producer guard",
        }]
    }
    migrated = migrate_contracts_v1(
        old,
        source_sha="b" * 64,
        tool_version="1.0.0",
        generated_at="2026-07-23T00:00:00Z",
    )
    record = migrated["contracts"]["CT-rec-url-shape"]
    assert record["adapter"] == "consumer_agreement"
    assert record["target"]["file"] == "bulk_downloader/live_recorder.py"
    assert record["target"]["producers"] == ["watch", "_load_state"]
    assert record["postconditions"] == [{
        "op": "all_producers_match_guard",
        "guard_signature": "^https?://",
    }]
```

- [ ] **Step 2: Run the registry tests and observe RED**

Run:

```bash
python -m pytest tests/test_code_intelligence_registries.py -q
```

Expected: FAIL during collection because `tools.code_intelligence.registries` does not exist.

- [ ] **Step 3: Implement typed lossless registry models and v1 migrations**

```python
# tools/code_intelligence/registries.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .artifacts import artifact_hash
from .governance_io import (
    load_json_object,
    validate_governance_artifact,
    write_validated_json,
)


class RegistryConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class InvariantRegistry:
    payload: dict[str, object]
    content_sha: str


@dataclass(frozen=True)
class ContractRegistry:
    payload: dict[str, object]
    content_sha: str


def _envelope_fields(
    source_sha: str,
    tool_version: str,
    input_hash: str,
) -> dict[str, object]:
    return {
        "source_sha": source_sha,
        "tool_version": tool_version,
        "input_hashes": {"migration_input": input_hash},
    }


def migrate_invariants_v1(
    payload: Mapping[str, object],
    *,
    source_sha: str,
    tool_version: str,
    generated_at: str,
) -> dict[str, object]:
    known = {
        "statement", "at", "why", "status", "guard_test", "probe",
        "added_by", "dp",
    }
    records: dict[str, object] = {}
    for invariant_id, raw_value in sorted(payload["invariants"].items()):
        raw = dict(raw_value)
        provenance = {}
        if "added_by" in raw:
            provenance["added_by"] = raw["added_by"]
        if "dp" in raw:
            provenance["defect_pattern"] = raw["dp"]
        records[invariant_id] = {
            "id": invariant_id,
            "statement": raw.get("statement", ""),
            "at": raw.get("at", ""),
            "why": raw.get("why", ""),
            "status": raw.get("status", "UNGUARDED"),
            "guard_test": raw.get("guard_test"),
            "probe": raw.get("probe"),
            "provenance": provenance,
            "extensions": {
                key: raw[key] for key in sorted(set(raw) - known)
            },
        }
    out = {
        "schema_name": "bd.invariants",
        "schema_version": 2,
        **_envelope_fields(
            source_sha,
            tool_version,
            artifact_hash(dict(payload)),
        ),
        "generated_at": generated_at,
        "invariants": records,
    }
    validate_governance_artifact("invariants", out)
    return out


def migrate_contracts_v1(
    payload: Mapping[str, object],
    *,
    source_sha: str,
    tool_version: str,
    generated_at: str,
) -> dict[str, object]:
    records: dict[str, object] = {}
    for raw_value in sorted(payload["contracts"], key=lambda value: value["id"]):
        raw = dict(raw_value)
        contract_id = raw["id"]
        records[contract_id] = {
            "id": contract_id,
            "adapter": "consumer_agreement",
            "target": {
                "file": raw["file"],
                "symbol": raw["symbol"],
                "producers": list(raw["producers"]),
                "consumers_relying": list(raw.get("consumers_relying", [])),
            },
            "fixtures": [],
            "preconditions": [],
            "postconditions": [{
                "op": "all_producers_match_guard",
                "guard_signature": raw["guard_signature"],
            }],
            "allowed_raises": [],
            "side_effects": [],
            "cleanup": [],
            "provenance": {"note": raw.get("note", "")},
            "extensions": {},
        }
    out = {
        "schema_name": "bd.contracts",
        "schema_version": 2,
        **_envelope_fields(
            source_sha,
            tool_version,
            artifact_hash(dict(payload)),
        ),
        "generated_at": generated_at,
        "contracts": records,
    }
    validate_governance_artifact("contracts", out)
    return out
```

- [ ] **Step 4: Run migration tests to GREEN**

Run:

```bash
python -m pytest tests/test_code_intelligence_registries.py -q
```

Expected: migration tests pass.

- [ ] **Step 5: Add failing compare-and-swap and round-trip tests**

```python
import json

import pytest

from tools.code_intelligence.registries import (
    RegistryConflict,
    write_invariant_registry,
)


def test_invariant_round_trip_preserves_extensions(tmp_path: Path) -> None:
    path = tmp_path / "INVARIANTS.json"
    migrated = migrate_invariants_v1(
        {
            "schema": 1,
            "invariants": {
                "I-X": {
                    "statement": "x",
                    "at": "x.py",
                    "status": "UNGUARDED",
                    "guard_test": None,
                    "private_extension": {"answer": 42},
                }
            },
        },
        source_sha="a" * 64,
        tool_version="1.0.0",
        generated_at="2026-07-23T00:00:00Z",
    )
    path.write_text(json.dumps(migrated), encoding="utf-8")
    loaded = load_invariant_registry(path)
    write_invariant_registry(path, loaded, expected_sha=loaded.content_sha)
    again = load_invariant_registry(path)
    assert again.payload["invariants"]["I-X"]["extensions"] == {
        "private_extension": {"answer": 42}
    }


def test_registry_expected_sha_conflict_is_non_mutating(tmp_path: Path) -> None:
    path = tmp_path / "INVARIANTS.json"
    path.write_text(
        json.dumps({
            "schema_name": "bd.invariants",
            "schema_version": 2,
            "source_sha": "a" * 64,
            "tool_version": "1.0.0",
            "input_hashes": {"migration_input": "b" * 64},
            "generated_at": "2026-07-23T00:00:00Z",
            "invariants": {},
        }),
        encoding="utf-8",
    )
    loaded = load_invariant_registry(path)
    before = path.read_bytes()
    with pytest.raises(RegistryConflict):
        write_invariant_registry(path, loaded, expected_sha="0" * 64)
    assert path.read_bytes() == before
```

- [ ] **Step 6: Implement load/write operations without lossy regeneration**

```python
# append to tools/code_intelligence/registries.py
def load_invariant_registry(path: Path) -> InvariantRegistry:
    payload = load_json_object(path)
    validate_governance_artifact("invariants", payload)
    return InvariantRegistry(payload, artifact_hash(payload))


def load_contract_registry(path: Path) -> ContractRegistry:
    payload = load_json_object(path)
    validate_governance_artifact("contracts", payload)
    return ContractRegistry(payload, artifact_hash(payload))


def _write_registry(
    path: Path,
    payload: dict[str, object],
    *,
    kind: str,
    expected_sha: str | None,
) -> str:
    current = load_json_object(path)
    current_sha = artifact_hash(current)
    if expected_sha is not None and current_sha != expected_sha:
        raise RegistryConflict(
            f"{kind} registry changed: expected {expected_sha}, found {current_sha}"
        )
    return write_validated_json(
        path,
        payload,
        lambda value: validate_governance_artifact(kind, value),
    )


def write_invariant_registry(
    path: Path,
    registry: InvariantRegistry,
    *,
    expected_sha: str | None,
) -> str:
    return _write_registry(
        path,
        registry.payload,
        kind="invariants",
        expected_sha=expected_sha,
    )


def write_contract_registry(
    path: Path,
    registry: ContractRegistry,
    *,
    expected_sha: str | None,
) -> str:
    return _write_registry(
        path,
        registry.payload,
        kind="contracts",
        expected_sha=expected_sha,
    )
```

- [ ] **Step 7: Migrate the actual registries and pin record counts**

Run the migration frontends against the current tracked-tree SHA:

```bash
python tools/invariants.py migrate \
  --repo . \
  --registry project-knowledge/INVARIANTS.json \
  --expected-count 11
python tools/consumer_agreement.py migrate \
  --repo . \
  --contracts project-knowledge/CONTRACTS.json \
  --expected-count 1
```

Expected:

```text
invariants: migrated=11 preserved=11 lost=0
contracts: migrated=1 preserved=1 lost=0
```

The migrated invariant registry must still contain `I-CAP01-rec-url-shape`; the migrated contract registry must still contain `CT-rec-url-shape`.

- [ ] **Step 8: Adapt existing invariant and consumer-agreement CLIs**

`tools/invariants.py --check` must return nonzero for an invalid registry, a phantom `GUARDED` test, or an `UNGUARDED` record when `--gate-unguarded` is supplied. `tools/consumer_agreement.py` must use:

```python
def check(
    contracts_path: Path,
    *,
    repo_root: Path,
    gate: bool,
) -> tuple[int, list[dict[str, object]]]:
    ...
```

The compatibility CLI arguments are:

```python
parser.add_argument("--contracts", type=Path, required=True)
parser.add_argument("--repo", type=Path, default=None)
parser.add_argument("--gate", action="store_true")
parser.add_argument("--json", action="store_true")
```

- [ ] **Step 9: Run registry, existing consumer, and promotion-wiring tests**

Run:

```bash
python -m pytest \
  tests/test_code_intelligence_registries.py \
  tests/test_audit_promotion_wirings_533.py -q
python tools/invariants.py --check \
  --registry project-knowledge/INVARIANTS.json \
  --repo .
python tools/consumer_agreement.py \
  --contracts project-knowledge/CONTRACTS.json \
  --repo . \
  --gate \
  --json
```

Expected: tests pass; invariant validation reports `total=11`; consumer agreement reports `status=pass` for `CT-rec-url-shape`.

- [ ] **Step 10: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/registries.py \
  tools/invariants.py \
  tools/consumer_agreement.py \
  project-knowledge/INVARIANTS.json \
  project-knowledge/CONTRACTS.json \
  tests/test_code_intelligence_registries.py
git status --short
```

Expected: no whitespace errors; 11 invariants and one contract remain represented; all changes remain uncommitted.

---

### Task 3: `bd-finding` Dry-Run Finding and RED-Stub Proposal Workflow

**Files:**
- Create: `tools/code_intelligence/findings.py`
- Create: `tools/bd_finding.py`
- Create: `toolchain/bin/bd-finding`
- Create: `tests/test_bd_finding.py`
- Modify: `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`

**Interfaces:**
- Consumes:
  - `load_json_object(path: Path) -> dict[str, object]`
  - `artifact_hash(payload: object) -> str`
  - `write_validated_json(path: Path, payload: dict[str, object], validator: Callable[..., None]) -> str`
  - `exclusive_file_lock(...)`
- Produces:
  - `normalize_finding(raw: Mapping[str, object], *, source_sha: str) -> dict[str, object]`
  - `stable_finding_id(normalized: Mapping[str, object]) -> str`
  - `render_red_stub_proposal(finding: Mapping[str, object]) -> str`
  - `propose_finding(*, state_path: Path, proposal_root: Path, raw: Mapping[str, object], expected_state_sha: str | None, expected_finding_sha: str | None, write: bool) -> FindingProposal`
  - `FindingProposal(status: str, finding_id: str, state_sha_before: str, state_sha_after: str | None, proposal_path: str, proposal_sha: str, wrote: bool)`
  - CLI: `bd-finding --state PATH --proposal-root PATH --input PATH [--expected-state-sha SHA] [--write] [--json]`
  - Exit codes: `0=valid dry-run or write`, `2=invalid finding`, `3=SHA conflict or overwrite refusal`, `4=write/lock error`.

- [ ] **Step 1: Write failing tests for stable IDs, dry-run default, and deterministic RED proposals**

```python
import json
from pathlib import Path

from tools.code_intelligence.findings import propose_finding


def _state() -> dict[str, object]:
    return {
        "schema_name": "bd.review-state",
        "schema_version": 2,
        "source_sha": "a" * 64,
        "tool_version": "1.0.0",
        "input_hashes": {"tracked_snapshot": "b" * 64},
        "generated_at": "2026-07-23T00:00:00Z",
        "files": {
            "bulk_downloader/worker.py": {
                "sha256": "c" * 64,
                "lines": 10,
                "status": "reviewed",
                "review_level": "L2",
                "reviewed_at_sha": "c" * 64,
                "finding_ids": [],
                "invariant_ids": [],
                "contract_ids": [],
                "test_ids": [],
                "evidence_hashes": [],
                "claim": None,
                "stale_reason": None,
                "reaudit_required": False,
            }
        },
        "findings": {},
    }


def _finding() -> dict[str, object]:
    return {
        "file": "bulk_downloader/worker.py",
        "line_range": [4, 6],
        "category": "logic",
        "severity": "high",
        "confidence": "confirmed",
        "title": "Worker accepts a stale lease",
        "detail": "The source-SHA mismatch does not invalidate the lease.",
        "repro_test": "tests/test_worker_lease.py::test_source_sha_invalidates_lease",
        "status": "open",
        "source": "manual:L2",
    }


def test_finding_is_dry_run_by_default_and_stable(tmp_path: Path) -> None:
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps(_state()), encoding="utf-8")

    first = propose_finding(
        state_path=state_path,
        proposal_root=tmp_path / "proposals",
        raw=_finding(),
        expected_state_sha=None,
        expected_finding_sha=None,
        write=False,
    )
    second = propose_finding(
        state_path=state_path,
        proposal_root=tmp_path / "proposals",
        raw=_finding(),
        expected_state_sha=None,
        expected_finding_sha=None,
        write=False,
    )

    assert first.finding_id == second.finding_id
    assert first.finding_id.startswith("F-")
    assert first.wrote is False
    assert first.status == "advisory"
    assert json.loads(state_path.read_text(encoding="utf-8"))["findings"] == {}
    assert not (tmp_path / "proposals").exists()


def test_red_stub_is_a_proposal_and_is_intentionally_red(tmp_path: Path) -> None:
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    result = propose_finding(
        state_path=state_path,
        proposal_root=tmp_path / "proposals",
        raw=_finding(),
        expected_state_sha=None,
        expected_finding_sha=None,
        write=True,
    )
    proposal = tmp_path / result.proposal_path
    text = proposal.read_text(encoding="utf-8")
    assert proposal.parts[-3] == "proposals"
    assert "pytest.fail(" in text
    assert "RED proposal" in text
    assert "source fix" not in text.lower()
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/test_bd_finding.py -q
```

Expected: FAIL during collection because `tools.code_intelligence.findings` does not exist.

- [ ] **Step 3: Implement finding normalization, stable IDs, and RED proposal rendering**

```python
# tools/code_intelligence/findings.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .artifacts import artifact_hash
from .governance_io import (
    load_json_object,
    validate_governance_artifact,
    write_validated_json,
)
from .locking import exclusive_file_lock


class FindingConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class FindingProposal:
    status: str
    finding_id: str
    state_sha_before: str
    state_sha_after: str | None
    proposal_path: str
    proposal_sha: str
    wrote: bool


def normalize_finding(
    raw: Mapping[str, object],
    *,
    source_sha: str,
) -> dict[str, object]:
    secret_pattern = re.compile(
        r"(?i)(authorization\s*:\s*bearer|cookie\s*:|password\s*=|"
        r"token\s*=|signature=|x-amz-signature=)"
    )
    for field in ("title", "detail", "source"):
        if secret_pattern.search(str(raw.get(field, ""))):
            raise ValueError(f"secret-shaped value rejected in {field}")
    line_range = tuple(int(value) for value in raw["line_range"])
    normalized = {
        "file": str(raw["file"]).replace("\\", "/"),
        "line_range": [line_range[0], line_range[1]],
        "category": str(raw["category"]),
        "severity": str(raw["severity"]),
        "confidence": str(raw["confidence"]),
        "title": " ".join(str(raw["title"]).split()),
        "detail": " ".join(str(raw["detail"]).split()),
        "repro_test": str(raw["repro_test"]),
        "status": str(raw.get("status", "open")),
        "source": str(raw["source"]),
        "source_sha": source_sha,
    }
    validate_governance_artifact("finding", normalized)
    return normalized


def stable_finding_id(normalized: Mapping[str, object]) -> str:
    identity = {
        key: normalized[key]
        for key in ("file", "line_range", "category", "title", "source_sha")
    }
    body = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "F-" + sha256(body).hexdigest()[:12].upper()


def render_red_stub_proposal(finding: Mapping[str, object]) -> str:
    finding_id = str(finding["id"])
    test_name = "test_red_" + re.sub(r"[^a-z0-9]+", "_", finding_id.lower()).strip("_")
    return (
        '"""RED proposal generated by bd-finding; move into tests only after review."""\n'
        "import pytest\n\n\n"
        f"def {test_name}() -> None:\n"
        f"    pytest.fail({('RED proposal for ' + finding_id + ': ' + str(finding['title']))!r})\n"
    )
```

- [ ] **Step 4: Implement dry-run and atomic write behavior**

```python
# append to tools/code_intelligence/findings.py
def propose_finding(
    *,
    state_path: Path,
    proposal_root: Path,
    raw: Mapping[str, object],
    expected_state_sha: str | None,
    expected_finding_sha: str | None,
    write: bool,
) -> FindingProposal:
    state = load_json_object(state_path)
    state_sha = artifact_hash(state)
    if expected_state_sha is not None and expected_state_sha != state_sha:
        raise FindingConflict(
            f"review state changed: expected {expected_state_sha}, found {state_sha}"
        )
    finding = normalize_finding(raw, source_sha=state["source_sha"])
    finding_id = stable_finding_id(finding)
    finding["id"] = finding_id
    stub = render_red_stub_proposal(finding)
    stub_bytes = stub.encode("utf-8")
    slug = finding_id.lower()
    relative = Path(finding_id) / f"test_{slug}.py"
    proposal_path = proposal_root / relative
    if not write:
        return FindingProposal(
            status="advisory",
            finding_id=finding_id,
            state_sha_before=state_sha,
            state_sha_after=None,
            proposal_path=(Path(proposal_root.name) / relative).as_posix(),
            proposal_sha=sha256(stub_bytes).hexdigest(),
            wrote=False,
        )

    lock = state_path.with_suffix(state_path.suffix + ".lock")
    with exclusive_file_lock(lock, timeout_seconds=10.0, stale_after_seconds=300.0):
        latest = load_json_object(state_path)
        latest_sha = artifact_hash(latest)
        if latest_sha != state_sha:
            raise FindingConflict(
                f"review state changed: expected {state_sha}, found {latest_sha}"
            )
        existing = latest["findings"].get(finding_id)
        if existing is not None:
            existing_sha = artifact_hash(existing)
            if existing == finding:
                return FindingProposal(
                    status="pass",
                    finding_id=finding_id,
                    state_sha_before=state_sha,
                    state_sha_after=latest_sha,
                    proposal_path=(Path(proposal_root.name) / relative).as_posix(),
                    proposal_sha=sha256(stub_bytes).hexdigest(),
                    wrote=False,
                )
            if expected_finding_sha is None or expected_finding_sha != existing_sha:
                raise FindingConflict(
                    f"{finding_id} overwrite requires expected finding SHA {existing_sha}"
                )
        if proposal_path.exists() and proposal_path.read_bytes() != stub_bytes:
            raise FindingConflict(f"proposal exists with different content: {proposal_path}")
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        proposal_temp = proposal_path.with_suffix(".py.tmp")
        proposal_temp.write_bytes(stub_bytes)
        proposal_temp.replace(proposal_path)
        updated = deepcopy(latest)
        updated["findings"][finding_id] = finding
        file_record = updated["files"][finding["file"]]
        file_record["finding_ids"] = sorted(
            set(file_record["finding_ids"]) | {finding_id}
        )
        write_validated_json(
            state_path,
            updated,
            lambda value: validate_governance_artifact("review_state", value),
        )
    return FindingProposal(
        status="pass",
        finding_id=finding_id,
        state_sha_before=state_sha,
        state_sha_after=artifact_hash(updated),
        proposal_path=(Path(proposal_root.name) / relative).as_posix(),
        proposal_sha=sha256(stub_bytes).hexdigest(),
        wrote=True,
    )
```

- [ ] **Step 5: Add overwrite and secret-safety tests**

```python
import pytest

from tools.code_intelligence.findings import FindingConflict


def test_existing_different_proposal_is_not_overwritten(tmp_path: Path) -> None:
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    dry = propose_finding(
        state_path=state_path,
        proposal_root=tmp_path / "proposals",
        raw=_finding(),
        expected_state_sha=None,
        expected_finding_sha=None,
        write=False,
    )
    proposal = tmp_path / dry.proposal_path
    proposal.parent.mkdir(parents=True)
    proposal.write_text("operator content\n", encoding="utf-8")
    with pytest.raises(FindingConflict):
        propose_finding(
            state_path=state_path,
            proposal_root=tmp_path / "proposals",
            raw=_finding(),
            expected_state_sha=None,
            expected_finding_sha=None,
            write=True,
        )
    assert proposal.read_text(encoding="utf-8") == "operator content\n"


def test_secret_shaped_finding_value_is_rejected(tmp_path: Path) -> None:
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    raw = _finding()
    raw["detail"] = "Authorization: Bearer abc.def.ghi"
    with pytest.raises(ValueError, match="secret-shaped"):
        propose_finding(
            state_path=state_path,
            proposal_root=tmp_path / "proposals",
            raw=raw,
            expected_state_sha=None,
            expected_finding_sha=None,
            write=False,
        )
```

- [ ] **Step 6: Implement the frontend and exact executable wrapper**

```python
# tools/bd_finding.py
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.code_intelligence.findings import FindingConflict, propose_finding


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bd-finding")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--proposal-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-state-sha")
    parser.add_argument("--expected-finding-sha")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        result = propose_finding(
            state_path=args.state,
            proposal_root=args.proposal_root,
            raw=raw,
            expected_state_sha=args.expected_state_sha,
            expected_finding_sha=args.expected_finding_sha,
            write=args.write,
        )
    except FindingConflict as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 3
    payload = asdict(result)
    print(json.dumps(payload, sort_keys=True) if args.json else (
        f"bd-finding: {result.status} {result.finding_id} "
        f"proposal={result.proposal_path} wrote={str(result.wrote).lower()}"
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
#!/usr/bin/env python3
# toolchain/bin/bd-finding
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
from tools.bd_finding import main

raise SystemExit(main())
```

- [ ] **Step 7: Run focused tests and CLI help**

Run:

```bash
python -m pytest tests/test_bd_finding.py -q
python tools/bd_finding.py --help
toolchain/bin/bd-finding --help
```

Expected: all tests pass; both help commands exit `0`; help states that dry-run is the default and `--write` is required to mutate state.

- [ ] **Step 8: Document exit codes and safe workflow**

Add this exact sequence to `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`:

```bash
bd-finding --state REVIEW_STATE.json --proposal-root review/proposals \
  --input finding.json --json > finding-dry-run.json
state_sha=$(python -c "import json; print(json.load(open('finding-dry-run.json'))['state_sha_before'])")
bd-finding --state REVIEW_STATE.json --proposal-root review/proposals \
  --input finding.json --expected-state-sha "$state_sha" --write --json
```

Document that the first command is non-mutating, the second is compare-and-swap, the generated file is outside the collected `tests/` tree, and a human must convert the proposal into a real failing regression test.

- [ ] **Step 9: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/findings.py \
  tools/bd_finding.py \
  toolchain/bin/bd-finding \
  tests/test_bd_finding.py \
  project-knowledge/CODE_INTELLIGENCE_TOOLING.md
git status --short
```

Expected: no whitespace errors; no production source file is changed; all changes remain uncommitted.

---

### Task 4: `bd-invariant` Confirmed-Finding Promotion Gate

**Files:**
- Create: `tools/code_intelligence/probe_allowlist.py`
- Create: `tools/code_intelligence/invariant_promotion.py`
- Create: `tools/bd_invariant.py`
- Create: `toolchain/bin/bd-invariant`
- Create: `tests/test_bd_invariant.py`
- Modify: `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`

**Interfaces:**
- Consumes:
  - Canonical `REVIEW_STATE.json` and `INVARIANTS.json`.
  - RED-run evidence schema `bd.red-run` version 1:
    - `test: str`
    - `source_sha: str`
    - `outcome: "fail"`
    - `exit_code: int`
    - `output_hash: str`
    - `observed_at: str`
  - Probe validation: `validate_probe_spec(probe: Mapping[str, object]) -> None`.
- Produces:
  - `promote_invariant(*, state_path: Path, registry_path: Path, finding_id: str, red_run_path: Path, probe: Mapping[str, object], expected_registry_sha: str, write: bool, generated_at: str) -> InvariantPromotion`
  - `InvariantPromotion(status: str, invariant_id: str, registry_sha_before: str, registry_sha_after: str | None, wrote: bool)`
  - CLI: `bd-invariant --state PATH --registry PATH --finding-id ID --red-run PATH --probe PATH --expected-registry-sha SHA [--write] [--json]`
  - Exit codes: `0=valid dry-run or write`, `2=precondition/schema rejection`, `3=SHA conflict`, `4=write/lock error`.

- [ ] **Step 1: Write failing promotion-precondition tests**

```python
import json
from pathlib import Path

import pytest

from tools.code_intelligence.invariant_promotion import (
    PromotionRejected,
    promote_invariant,
)


def test_promotion_requires_confirmed_finding_and_observed_red_failure(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_guard.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_guard():\n    assert False\n", encoding="utf-8")
    state = {
        "schema_name": "bd.review-state",
        "schema_version": 2,
        "source_sha": "a" * 64,
        "tool_version": "1.0.0",
        "input_hashes": {"tracked_snapshot": "b" * 64},
        "generated_at": "2026-07-23T00:00:00Z",
        "files": {},
        "findings": {
            "F-ABC": {
                "id": "F-ABC",
                "file": "bulk_downloader/worker.py",
                "confidence": "probable",
                "status": "open",
                "title": "guard gap",
                "detail": "gap",
                "repro_test": "tests/test_guard.py::test_guard",
                "source_sha": "a" * 64,
            }
        },
    }
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    registry_path = tmp_path / "INVARIANTS.json"
    registry_path.write_text(json.dumps({
        "schema_name": "bd.invariants",
        "schema_version": 2,
        "source_sha": "a" * 64,
        "tool_version": "1.0.0",
        "input_hashes": {"migration_input": "c" * 64},
        "generated_at": "2026-07-23T00:00:00Z",
        "invariants": {},
    }), encoding="utf-8")
    red_run = tmp_path / "red-run.json"
    red_run.write_text(json.dumps({
        "schema_name": "bd.red-run",
        "schema_version": 1,
        "test": "tests/test_guard.py::test_guard",
        "source_sha": "a" * 64,
        "outcome": "fail",
        "exit_code": 1,
        "output_hash": "d" * 64,
        "observed_at": "2026-07-23T00:01:00Z",
    }), encoding="utf-8")

    with pytest.raises(PromotionRejected, match="confirmed"):
        promote_invariant(
            repo_root=repo,
            state_path=state_path,
            registry_path=registry_path,
            finding_id="F-ABC",
            red_run_path=red_run,
            probe={"operation": "attribute_exists", "adapter": "app_factory", "attribute": "x"},
            expected_registry_sha=None,
            write=False,
            generated_at="2026-07-23T00:02:00Z",
        )
```

- [ ] **Step 2: Run the focused test and observe RED**

Run:

```bash
python -m pytest tests/test_bd_invariant.py::test_promotion_requires_confirmed_finding_and_observed_red_failure -q
```

Expected: FAIL during collection because `tools.code_intelligence.invariant_promotion` does not exist.

- [ ] **Step 3: Implement precondition validation and stable invariant IDs**

First add the promotion-safe probe validator. Task 5 extends the same file with runtime adapter maps:

```python
# tools/code_intelligence/probe_allowlist.py
from typing import Mapping

PROMOTABLE_OPERATIONS = frozenset({
    "attribute_exists",
    "pure_function_call",
    "flask_request",
    "file_hash_assertion",
    "schema_assertion",
    "subprocess_tool",
})


def validate_probe_spec(probe: Mapping[str, object]) -> None:
    operation = probe.get("operation")
    if operation not in PROMOTABLE_OPERATIONS:
        raise ValueError(f"probe operation is not allowlisted: {operation!r}")
    timeout = float(probe.get("timeout_seconds", 5.0))
    if not 0.05 <= timeout <= 30.0:
        raise ValueError("timeout_seconds must be in [0.05, 30.0]")
    if any(key in probe for key in ("expression", "code", "shell", "import")):
        raise ValueError("probe contains a forbidden execution field")
```

```python
# tools/code_intelligence/invariant_promotion.py
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

from .artifacts import artifact_hash
from .governance_io import load_json_object
from .probe_allowlist import validate_probe_spec
from .registries import (
    InvariantRegistry,
    RegistryConflict,
    load_invariant_registry,
    write_invariant_registry,
)


class PromotionRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class InvariantPromotion:
    status: str
    invariant_id: str
    registry_sha_before: str
    registry_sha_after: str | None
    wrote: bool


def _invariant_id(finding_id: str, statement: str) -> str:
    digest = sha256(f"{finding_id}\0{statement}".encode("utf-8")).hexdigest()
    return "I-" + digest[:12].upper()


def _validate_red_run(
    red_run: Mapping[str, object],
    *,
    finding: Mapping[str, object],
    repo_root: Path,
    source_sha: str,
) -> None:
    if (
        red_run.get("schema_name") != "bd.red-run"
        or red_run.get("schema_version") != 1
    ):
        raise PromotionRejected("invalid RED-run schema")
    if finding.get("confidence") != "confirmed":
        raise PromotionRejected("finding confidence must be confirmed")
    if finding.get("status") != "open":
        raise PromotionRejected("finding status must be open")
    if red_run.get("outcome") != "fail" or int(red_run.get("exit_code", 0)) == 0:
        raise PromotionRejected("RED test was not observed failing")
    if red_run.get("source_sha") != source_sha:
        raise PromotionRejected("RED-run source SHA does not match review state")
    if red_run.get("test") != finding.get("repro_test"):
        raise PromotionRejected("RED-run test does not match finding repro_test")
    test_path = str(red_run["test"]).split("::", 1)[0]
    if not (repo_root / test_path).is_file():
        raise PromotionRejected(f"RED test does not exist: {test_path}")
```

- [ ] **Step 4: Add failing tests for allowlist rejection, source mismatch, dry-run, and lossless write**

```python
def make_confirmed_promotion_fixture(tmp_path: Path) -> dict[str, object]:
    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_guard.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_guard():\n    assert False\n", encoding="utf-8")
    state_path = tmp_path / "REVIEW_STATE.json"
    state_path.write_text(json.dumps({
        "schema_name": "bd.review-state",
        "schema_version": 2,
        "source_sha": "a" * 64,
        "tool_version": "1.0.0",
        "input_hashes": {"tracked_snapshot": "b" * 64},
        "generated_at": "2026-07-23T00:00:00Z",
        "files": {},
        "findings": {
            "F-ABC": {
                "id": "F-ABC",
                "file": "bulk_downloader/worker.py",
                "confidence": "confirmed",
                "status": "open",
                "title": "guard gap",
                "detail": "the guard is absent",
                "repro_test": "tests/test_guard.py::test_guard",
                "source_sha": "a" * 64,
            }
        },
    }), encoding="utf-8")
    registry_path = tmp_path / "INVARIANTS.json"
    registry_path.write_text(json.dumps({
        "schema_name": "bd.invariants",
        "schema_version": 2,
        "source_sha": "a" * 64,
        "tool_version": "1.0.0",
        "input_hashes": {"migration_input": "c" * 64},
        "generated_at": "2026-07-23T00:00:00Z",
        "invariants": {
            "I0001": {
                "id": "I0001",
                "statement": "existing",
                "at": "bulk_downloader/runner.py",
                "why": "existing guard",
                "status": "GUARDED",
                "guard_test": "tests/test_guard.py::test_guard",
                "probe": None,
                "provenance": {},
                "extensions": {},
            }
        },
    }), encoding="utf-8")
    red_run_path = tmp_path / "red-run.json"
    red_run_path.write_text(json.dumps({
        "schema_name": "bd.red-run",
        "schema_version": 1,
        "test": "tests/test_guard.py::test_guard",
        "source_sha": "a" * 64,
        "outcome": "fail",
        "exit_code": 1,
        "output_hash": "d" * 64,
        "observed_at": "2026-07-23T00:01:00Z",
    }), encoding="utf-8")
    return {
        "repo_root": repo,
        "state_path": state_path,
        "registry_path": registry_path,
        "finding_id": "F-ABC",
        "red_run_path": red_run_path,
        "generated_at": "2026-07-23T00:02:00Z",
    }


def test_promotion_rejects_arbitrary_probe_operation(tmp_path: Path) -> None:
    fixture = make_confirmed_promotion_fixture(tmp_path)
    with pytest.raises(PromotionRejected, match="probe"):
        promote_invariant(
            **fixture,
            probe={"operation": "eval", "expression": "__import__('os').environ"},
            expected_registry_sha=None,
            write=False,
        )


def test_dry_run_preserves_registry_and_write_preserves_existing_ids(
    tmp_path: Path,
) -> None:
    fixture = make_confirmed_promotion_fixture(tmp_path)
    before = fixture["registry_path"].read_bytes()
    dry = promote_invariant(
        **fixture,
        probe={
            "operation": "attribute_exists",
            "adapter": "app_factory",
            "attribute": "create_app",
            "timeout_seconds": 2.0,
        },
        expected_registry_sha=None,
        write=False,
    )
    assert dry.wrote is False
    assert fixture["registry_path"].read_bytes() == before

    written = promote_invariant(
        **fixture,
        probe={
            "operation": "attribute_exists",
            "adapter": "app_factory",
            "attribute": "create_app",
            "timeout_seconds": 2.0,
        },
        expected_registry_sha=dry.registry_sha_before,
        write=True,
    )
    registry = json.loads(fixture["registry_path"].read_text(encoding="utf-8"))
    assert written.wrote is True
    assert "I0001" in registry["invariants"]
    assert written.invariant_id in registry["invariants"]
```

- [ ] **Step 5: Implement dry-run and atomic lossless promotion**

```python
# append to tools/code_intelligence/invariant_promotion.py
def promote_invariant(
    *,
    repo_root: Path,
    state_path: Path,
    registry_path: Path,
    finding_id: str,
    red_run_path: Path,
    probe: Mapping[str, object],
    expected_registry_sha: str | None,
    write: bool,
    generated_at: str,
) -> InvariantPromotion:
    state = load_json_object(state_path)
    finding = state["findings"].get(finding_id)
    if finding is None:
        raise PromotionRejected(f"finding does not exist: {finding_id}")
    red_run = load_json_object(red_run_path)
    _validate_red_run(
        red_run,
        finding=finding,
        repo_root=repo_root,
        source_sha=state["source_sha"],
    )
    try:
        validate_probe_spec(probe)
    except ValueError as exc:
        raise PromotionRejected(f"probe rejected: {exc}") from exc

    loaded = load_invariant_registry(registry_path)
    if loaded.payload["source_sha"] != state["source_sha"]:
        raise PromotionRejected("registry and review state bind different source SHAs")
    statement = str(finding["title"])
    invariant_id = _invariant_id(finding_id, statement)
    record = {
        "id": invariant_id,
        "statement": statement,
        "at": finding["file"],
        "why": finding["detail"],
        "status": "GUARDED",
        "guard_test": finding["repro_test"],
        "probe": dict(probe),
        "provenance": {
            "finding_id": finding_id,
            "red_run_hash": artifact_hash(red_run),
        },
        "extensions": {},
    }
    updated = deepcopy(loaded.payload)
    existing = updated["invariants"].get(invariant_id)
    if existing is not None and existing != record:
        raise PromotionRejected(f"{invariant_id} already exists with different content")
    updated["invariants"][invariant_id] = record
    updated["invariants"] = dict(sorted(updated["invariants"].items()))
    updated["generated_at"] = generated_at
    if not write:
        return InvariantPromotion(
            status="advisory",
            invariant_id=invariant_id,
            registry_sha_before=loaded.content_sha,
            registry_sha_after=None,
            wrote=False,
        )
    after = write_invariant_registry(
        registry_path,
        InvariantRegistry(updated, artifact_hash(updated)),
        expected_sha=expected_registry_sha,
    )
    return InvariantPromotion(
        status="pass",
        invariant_id=invariant_id,
        registry_sha_before=loaded.content_sha,
        registry_sha_after=after,
        wrote=True,
    )
```

- [ ] **Step 6: Implement the frontend and exact executable wrapper**

`tools/bd_invariant.py` must parse the exact arguments in the Interfaces block, load the probe JSON, call `promote_invariant()`, print a sorted JSON object when `--json` is selected, and map `PromotionRejected` to exit `2` and `RegistryConflict` to exit `3`. `toolchain/bin/bd-invariant` uses the same seven-line repository-relative import wrapper as `bd-finding`, importing `tools.bd_invariant.main`.

The machine-readable success shape is:

```json
{
  "invariant_id": "I-12HEXCHARS",
  "registry_sha_after": null,
  "registry_sha_before": "64-hex",
  "status": "advisory",
  "wrote": false
}
```

- [ ] **Step 7: Run focused tests and prove no automatic promotion**

Run:

```bash
python -m pytest tests/test_bd_invariant.py -q
python tools/bd_invariant.py --help
toolchain/bin/bd-invariant --help
```

Expected: all tests pass; help states dry-run is default; no registry mutation occurs without `--write`.

- [ ] **Step 8: Document the RED-evidence handoff**

Add an exact example to `project-knowledge/CODE_INTELLIGENCE_TOOLING.md` showing:

```bash
python -m pytest tests/test_worker_lease.py::test_source_sha_invalidates_lease -q
python -c "import hashlib,json; p='tests/test_worker_lease.py'; print(json.dumps({'schema':{'name':'bd.red-run','version':1},'test':p+'::test_source_sha_invalidates_lease','source_sha':'a'*64,'outcome':'fail','exit_code':1,'output_hash':hashlib.sha256(b'observed pytest failure').hexdigest(),'observed_at':'2026-07-23T00:01:00Z'},sort_keys=True))" > red-run.json
bd-invariant --state REVIEW_STATE.json \
  --registry project-knowledge/INVARIANTS.json \
  --finding-id F-ABC \
  --red-run red-run.json \
  --probe invariant-probe.json \
  --expected-registry-sha "$(python -c 'import json; from tools.code_intelligence.artifacts import artifact_hash; print(artifact_hash(json.load(open("project-knowledge/INVARIANTS.json"))))')" \
  --json > invariant-dry-run.json
registry_sha=$(python -c "import json; print(json.load(open('invariant-dry-run.json'))['registry_sha_before'])")
bd-invariant --state REVIEW_STATE.json \
  --registry project-knowledge/INVARIANTS.json \
  --finding-id F-ABC \
  --red-run red-run.json \
  --probe invariant-probe.json \
  --expected-registry-sha "$registry_sha" \
  --write --json
```

State that a failing pytest command must be captured into a schema-valid `bd.red-run` record by the execution workflow; `bd-invariant` does not run or fabricate that evidence.

- [ ] **Step 9: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/invariant_promotion.py \
  tools/bd_invariant.py \
  toolchain/bin/bd-invariant \
  tests/test_bd_invariant.py \
  project-knowledge/CODE_INTELLIGENCE_TOOLING.md
git status --short
```

Expected: no whitespace errors; no production source fix exists in the diff; all changes remain uncommitted.

---

### Task 5: Allowlisted, Bounded `invariant_probe.py`

**Files:**
- Modify: `tools/code_intelligence/probe_allowlist.py`
- Create: `tools/code_intelligence/probes.py`
- Create: `tools/invariant_probe.py`
- Create: `tests/test_invariant_probe.py`
- Modify: `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`

**Interfaces:**
- Consumes:
  - Canonical invariant records from `load_invariant_registry()`.
  - `ResultState` and `CheckResult(name, state, summary, evidence)`.
  - Foundation adapter registry; Flask application imports occur only in child workers.
- Produces:
  - `validate_probe_spec(probe: Mapping[str, object]) -> None`
  - `run_probe(probe_id: str, probe: Mapping[str, object], *, repo_root: Path, timeout_seconds: float | None = None) -> CheckResult`
  - `run_invariant_registry(*, registry_path: Path, repo_root: Path, only: tuple[str, ...], gate: bool) -> tuple[CheckResult, ...]`
  - Operations:
    - `attribute_exists`
    - `pure_function_call`
    - `flask_request`
    - `file_hash_assertion`
    - `schema_assertion`
    - `subprocess_tool`
  - CLI: `python tools/invariant_probe.py --registry PATH --repo PATH [--only ID] [--gate] [--json]`
  - Exit codes: `0=all selected probes pass or advisory outside gate`, `1=fail/unknown/timeout/error in gate mode`, `2=schema/allowlist rejection`.

- [ ] **Step 1: Write failing tests that reject arbitrary execution surfaces**

```python
import pytest

from tools.code_intelligence.probe_allowlist import (
    ATTRIBUTE_ADAPTERS,
    FLASK_ADAPTERS,
    PURE_FUNCTION_ADAPTERS,
    SUBPROCESS_TOOLS,
    validate_probe_spec,
)


@pytest.mark.parametrize(
    "probe",
    [
        {"operation": "eval", "expression": "1 + 1"},
        {"operation": "exec", "code": "pass"},
        {
            "operation": "pure_function_call",
            "adapter": "os_system",
            "args": ["whoami"],
        },
        {
            "operation": "subprocess_tool",
            "tool": "bash",
            "args": ["-c", "whoami"],
        },
        {
            "operation": "attribute_exists",
            "adapter": "__import__",
            "attribute": "os",
        },
    ],
)
def test_probe_spec_rejects_non_allowlisted_execution(probe: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_probe_spec(probe)
```

- [ ] **Step 2: Run the allowlist tests and observe RED**

Run:

```bash
python -m pytest tests/test_invariant_probe.py::test_probe_spec_rejects_non_allowlisted_execution -q
```

Expected: FAIL during collection because the Task 4 promotion-only validator does not yet export the four runtime adapter maps.

- [ ] **Step 3: Implement explicit probe and adapter allowlists**

```python
# tools/code_intelligence/probe_allowlist.py
from __future__ import annotations

from typing import Mapping


OPERATIONS = frozenset({
    "attribute_exists",
    "pure_function_call",
    "flask_request",
    "file_hash_assertion",
    "schema_assertion",
    "subprocess_tool",
})

ATTRIBUTE_ADAPTERS = {
    "app_factory": ("bulk_downloader.app", None),
    "package": ("bulk_downloader", None),
}

PURE_FUNCTION_ADAPTERS = {
    "url_classifier": (
        "bulk_downloader.provider_resolve_impl._common",
        "classify_url",
    ),
    "redaction_scan": (
        "bulk_downloader.capture_artifact_redact",
        "scan_for_secrets",
    ),
}

FLASK_ADAPTERS = {
    "app_factory": ("bulk_downloader.app", "app"),
}

SUBPROCESS_TOOLS = {
    "defect_patterns": ("tools/defect_patterns.py", "--check"),
    "consumer_agreement": ("tools/consumer_agreement.py", "--gate"),
}


def validate_probe_spec(probe: Mapping[str, object]) -> None:
    operation = probe.get("operation")
    if operation not in OPERATIONS:
        raise ValueError(f"probe operation is not allowlisted: {operation!r}")
    timeout = float(probe.get("timeout_seconds", 5.0))
    if not 0.05 <= timeout <= 30.0:
        raise ValueError("timeout_seconds must be in [0.05, 30.0]")
    if operation == "attribute_exists" and probe.get("adapter") not in ATTRIBUTE_ADAPTERS:
        raise ValueError("attribute adapter is not allowlisted")
    if operation == "pure_function_call" and probe.get("adapter") not in PURE_FUNCTION_ADAPTERS:
        raise ValueError("pure-function adapter is not allowlisted")
    if operation == "flask_request" and probe.get("adapter") not in FLASK_ADAPTERS:
        raise ValueError("Flask adapter is not allowlisted")
    if operation == "subprocess_tool" and probe.get("tool") not in SUBPROCESS_TOOLS:
        raise ValueError("subprocess tool is not allowlisted")
    if operation == "subprocess_tool":
        args = probe.get("args", [])
        if not isinstance(args, list) or any(not isinstance(value, str) for value in args):
            raise ValueError("subprocess args must be a list of strings")
```

- [ ] **Step 4: Write failing tests for pass, fail, unknown, timeout, and secret-safe evidence**

```python
from pathlib import Path

from tools.code_intelligence.probes import run_probe
from tools.code_intelligence.results import ResultState


def test_file_hash_probe_passes_with_bounded_evidence(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("safe\n", encoding="utf-8")
    result = run_probe(
        "I-HASH",
        {
            "operation": "file_hash_assertion",
            "path": "x.txt",
            "expected_sha256": "84aa2f63c756f3d49b6167cc8012faacc9e483107499043dc3bd7b92e8a0f2bf",
            "timeout_seconds": 1.0,
        },
        repo_root=tmp_path,
    )
    assert result.state is ResultState.PASS
    assert result.evidence == {
        "path": "x.txt",
        "matched": True,
        "observed_sha256": "84aa2f63c756f3d49b6167cc8012faacc9e483107499043dc3bd7b92e8a0f2bf",
    }


def test_probe_timeout_is_not_a_pass(tmp_path: Path) -> None:
    tool = tmp_path / "tools" / "defect_patterns.py"
    tool.parent.mkdir(parents=True)
    tool.write_text(
        "import time\ntime.sleep(2)\n",
        encoding="utf-8",
    )
    result = run_probe(
        "I-TIMEOUT",
        {
            "operation": "subprocess_tool",
            "tool": "defect_patterns",
            "args": [],
            "timeout_seconds": 0.05,
        },
        repo_root=tmp_path,
    )
    assert result.state is ResultState.TIMEOUT


def test_unknown_attribute_is_unknown_not_pass(tmp_path: Path) -> None:
    result = run_probe(
        "I-ATTR",
        {
            "operation": "attribute_exists",
            "adapter": "package",
            "attribute": "missing",
            "timeout_seconds": 1.0,
        },
        repo_root=tmp_path,
    )
    assert result.state is ResultState.UNKNOWN
```

- [ ] **Step 5: Implement child-process execution with a hard deadline**

```python
# tools/code_intelligence/probes.py
from __future__ import annotations

from hashlib import sha256
import importlib
import json
import multiprocessing
from pathlib import Path
import queue
import subprocess
import sys
import time
from typing import Mapping

from .probe_allowlist import (
    ATTRIBUTE_ADAPTERS,
    FLASK_ADAPTERS,
    PURE_FUNCTION_ADAPTERS,
    SUBPROCESS_TOOLS,
    validate_probe_spec,
)
from .results import CheckResult, ResultState


def _result(
    probe_id: str,
    status: str,
    summary: str,
    evidence: Mapping[str, object],
    duration_ms: int,
) -> CheckResult:
    return CheckResult(
        name=f"invariant:{probe_id}",
        state=ResultState(status),
        summary=summary,
        evidence={**dict(evidence), "duration_ms": duration_ms},
    )


def _file_hash(
    probe_id: str,
    probe: Mapping[str, object],
    repo_root: Path,
) -> CheckResult:
    started = time.monotonic()
    relative = str(probe["path"]).replace("\\", "/")
    target = (repo_root / relative).resolve()
    target.relative_to(repo_root.resolve())
    observed = sha256(target.read_bytes()).hexdigest()
    matched = observed == probe["expected_sha256"]
    return _result(
        probe_id,
        "pass" if matched else "fail",
        "file hash matched" if matched else "file hash drifted",
        {"path": relative, "matched": matched, "observed_sha256": observed},
        int((time.monotonic() - started) * 1000),
    )


def _worker(
    output: multiprocessing.Queue,
    probe_id: str,
    probe: dict[str, object],
    repo_root: str,
) -> None:
    try:
        sys.path.insert(0, repo_root)
        operation = probe["operation"]
        if operation == "attribute_exists":
            module_name, _ = ATTRIBUTE_ADAPTERS[probe["adapter"]]
            module = importlib.import_module(module_name)
            exists = hasattr(module, probe["attribute"])
            output.put(("pass" if exists else "unknown", {"exists": exists}))
            return
        if operation == "pure_function_call":
            module_name, function_name = PURE_FUNCTION_ADAPTERS[probe["adapter"]]
            function = getattr(importlib.import_module(module_name), function_name)
            value = function(*probe.get("args", []), **probe.get("kwargs", {}))
            output.put(("pass", {"result_hash": sha256(
                json.dumps(value, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()}))
            return
        if operation == "subprocess_tool":
            relative, *fixed = SUBPROCESS_TOOLS[probe["tool"]]
            completed = subprocess.run(
                [str(Path(repo_root) / relative), *fixed, *probe.get("args", [])],
                cwd=repo_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                shell=False,
                timeout=float(probe["timeout_seconds"]),
            )
            output.put((
                "pass" if completed.returncode == 0 else "fail",
                {
                    "returncode": completed.returncode,
                    "stdout_hash": sha256(completed.stdout.encode("utf-8")).hexdigest(),
                    "stderr_hash": sha256(completed.stderr.encode("utf-8")).hexdigest(),
                },
            ))
            return
        if operation == "flask_request":
            module_name, app_name = FLASK_ADAPTERS[probe["adapter"]]
            module = importlib.import_module(module_name)
            app = getattr(module, app_name)
            client = app.test_client()
            response = client.open(
                str(probe["path"]),
                method=str(probe.get("method", "GET")),
                json=probe.get("json"),
            )
            payload = response.get_json(silent=True)
            expected = set(int(value) for value in probe["expected_status"])
            output.put((
                "pass" if response.status_code in expected else "fail",
                {
                    "status_code": response.status_code,
                    "content_type": response.content_type,
                    "json_keys": sorted(payload) if isinstance(payload, dict) else [],
                },
            ))
            return
        if operation == "schema_assertion":
            from .governance_io import validate_governance_artifact
            relative = str(probe["path"]).replace("\\", "/")
            target = (Path(repo_root) / relative).resolve()
            target.relative_to(Path(repo_root).resolve())
            payload = json.loads(target.read_text(encoding="utf-8"))
            validate_governance_artifact(str(probe["artifact_kind"]), payload)
            output.put(("pass", {
                "path": relative,
                "artifact_hash": sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }))
            return
        raise ValueError(f"worker operation not dispatched: {operation}")
    except Exception as exc:
        output.put(("error", {"error_type": type(exc).__name__}))


def run_probe(
    probe_id: str,
    probe: Mapping[str, object],
    *,
    repo_root: Path,
    timeout_seconds: float | None = None,
) -> CheckResult:
    validate_probe_spec(probe)
    if probe["operation"] == "file_hash_assertion":
        return _file_hash(probe_id, probe, repo_root)
    deadline = timeout_seconds or float(probe.get("timeout_seconds", 5.0))
    started = time.monotonic()
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_worker,
        args=(output, probe_id, dict(probe), str(repo_root)),
        daemon=True,
    )
    process.start()
    process.join(deadline)
    duration = int((time.monotonic() - started) * 1000)
    if process.is_alive():
        process.terminate()
        process.join(1.0)
        return _result(probe_id, "timeout", "probe exceeded deadline", {}, duration)
    try:
        status, evidence = output.get_nowait()
    except queue.Empty:
        return _result(probe_id, "error", "probe worker returned no result", {}, duration)
    return _result(probe_id, status, "probe completed", evidence, duration)
```

`file_hash_assertion` runs through `_file_hash`; the worker code above dispatches the remaining five operations. `flask_request` returns only status, content type, and JSON key names; `schema_assertion` returns only normalized path and artifact hash.

- [ ] **Step 6: Implement registry iteration and CLI**

```python
# tools/invariant_probe.py
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from tools.code_intelligence.paths import discover_repo_root
from tools.code_intelligence.probes import run_probe
from tools.code_intelligence.registries import load_invariant_registry
from tools.code_intelligence.results import CheckResult, ResultState, exit_code
from tools.code_intelligence.schemas import make_envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invariant_probe.py")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo.resolve() if args.repo else discover_repo_root()
    registry = load_invariant_registry(args.registry)
    selected = set(args.only)
    results: list[CheckResult] = []
    for invariant_id, record in registry.payload["invariants"].items():
        if selected and invariant_id not in selected:
            continue
        if record["probe"] is None:
            results.append(CheckResult(
                name=f"invariant:{invariant_id}",
                state=ResultState.UNKNOWN,
                summary="no executable probe",
                evidence={"duration_ms": 0},
            ))
            continue
        results.append(run_probe(
            invariant_id,
            record["probe"],
            repo_root=repo,
        ))
    payload = {
        **make_envelope(
            "bd.invariant-probe-results",
            1,
            registry.payload["source_sha"],
            "1.0.0",
            {"invariant_registry": registry.content_sha},
        ),
        "state": "pass" if exit_code(results, args.gate) == 0 else "fail",
        "results": [{
            "name": result.name,
            "state": result.state.value,
            "summary": result.summary,
            "evidence": dict(result.evidence),
        } for result in results],
    }
    print(json.dumps(payload, sort_keys=True) if args.json else (
        f"invariant-probe: {payload['state']} selected={len(results)}"
    ))
    return exit_code(results, args.gate)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Add and run operation-specific fixture tests**

Add tests for:

```python
def test_flask_request_never_returns_body_or_authorization_header(tmp_path: Path) -> None:
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text(
        "from flask import Flask, jsonify\n"
        "app = Flask(__name__)\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return jsonify(ok=True)\n",
        encoding="utf-8",
    )
    result = run_probe(
        "I-HTTP",
        {
            "operation": "flask_request",
            "adapter": "app_factory",
            "method": "GET",
            "path": "/health",
            "expected_status": [200],
            "timeout_seconds": 2.0,
        },
        repo_root=tmp_path,
    )
    assert result.state is ResultState.PASS
    assert "body" not in result.evidence
    assert "headers" not in result.evidence


def test_schema_assertion_rejects_future_version(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(
        '{"schema":{"name":"bd.invariants","version":999}}',
        encoding="utf-8",
    )
    result = run_probe(
        "I-SCHEMA",
        {
            "operation": "schema_assertion",
            "path": "artifact.json",
            "artifact_kind": "invariants",
            "timeout_seconds": 1.0,
        },
        repo_root=tmp_path,
    )
    assert result.state is ResultState.FAIL
```

Run:

```bash
python -m pytest tests/test_invariant_probe.py -q
python tools/invariant_probe.py --help
```

Expected: all tests pass; help exits `0`; registry records without probes are `unknown`, never pass.

- [ ] **Step 8: Document probe schemas and exit behavior**

Document every operation's exact required keys in `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`, including the 30-second maximum, explicit adapter names, hashed-only subprocess evidence, and the rule that `unknown`, `timeout`, and `error` block only in `--gate` mode.

- [ ] **Step 9: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/probe_allowlist.py \
  tools/code_intelligence/probes.py \
  tools/invariant_probe.py \
  tests/test_invariant_probe.py \
  project-knowledge/CODE_INTELLIGENCE_TOOLING.md
git status --short
```

Expected: no whitespace errors; no `eval(`, `exec(`, `shell=True`, or registry-controlled import string exists; all changes remain uncommitted.

---

### Task 6: Runtime Pre/Postcondition Contract Harness

**Files:**
- Create: `tools/code_intelligence/contracts.py`
- Create: `tools/contract_harness.py`
- Create: `tests/test_contract_harness.py`
- Modify: `tools/consumer_agreement.py`
- Modify: `project-knowledge/CONTRACTS.json`
- Modify: `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md`
- Modify: `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`

**Interfaces:**
- Consumes:
  - `load_contract_registry(path: Path) -> ContractRegistry`
  - Foundation `AdapterRegistry`.
  - Existing `tools/body_contract.py`, `tools/consumer_agreement.py`, and `tools/api_contract_probe.py` through explicit adapters.
- Produces:
  - `ContractContext(repo_root: Path, source_sha: str, adapter_registry: AdapterRegistry)`
  - `evaluate_predicate(predicate: Mapping[str, object], *, fixture: Mapping[str, object], observation: Mapping[str, object]) -> tuple[bool, dict[str, object]]`
  - `run_contract(contract: Mapping[str, object], *, context: ContractContext) -> CheckResult`
  - `run_contract_registry(*, contracts_path: Path, context: ContractContext, only: tuple[str, ...], gate: bool) -> tuple[CheckResult, ...]`
  - Adapter names: `pure_function`, `flask_route`, `body_contract_fixture`, `consumer_agreement`, `api_contract_smoke`.
  - CLI: `python tools/contract_harness.py --contracts PATH --repo PATH [--only ID] [--gate] [--json]`
  - Exit codes: `0=selected contracts pass or advisory outside gate`, `1=blocking contract result`, `2=schema/adapter rejection`.

- [ ] **Step 1: Write failing pure-function pre/post/raises/cleanup tests**

```python
from pathlib import Path

from tools.code_intelligence.adapters import AdapterRegistry
from tools.code_intelligence.contracts import ContractContext, run_contract
from tools.code_intelligence.results import ResultState


def _context(tmp_path: Path, function) -> ContractContext:
    registry = AdapterRegistry()
    registry.register_pure_function("fixture.double", function)
    return ContractContext(
        repo_root=tmp_path,
        source_sha="a" * 64,
        adapter_registry=registry,
    )


def test_failed_precondition_does_not_call_target(tmp_path: Path) -> None:
    calls = []

    def target(value: int) -> int:
        calls.append(value)
        return value * 2

    result = run_contract({
        "id": "C-DOUBLE",
        "adapter": "pure_function",
        "target": {"name": "fixture.double"},
        "fixtures": [{"args": [-1], "kwargs": {}}],
        "preconditions": [{"op": "arg_int_range", "index": 0, "min": 0, "max": 10}],
        "postconditions": [{"op": "return_equals", "value": -2}],
        "allowed_raises": [],
        "side_effects": [],
        "cleanup": [],
    }, context=_context(tmp_path, target))
    assert result.state is ResultState.FAIL
    assert calls == []
    assert result.evidence["phase"] == "precondition"


def test_allowed_raise_passes_and_cleanup_always_runs(tmp_path: Path) -> None:
    marker = tmp_path / "marker"

    def target() -> None:
        marker.write_text("dirty", encoding="utf-8")
        raise ValueError("expected")

    registry = AdapterRegistry()
    registry.register_pure_function("fixture.raises", target)
    registry.register_cleanup("fixture.remove_marker", lambda: marker.unlink(missing_ok=True))
    result = run_contract({
        "id": "C-RAISES",
        "adapter": "pure_function",
        "target": {"name": "fixture.raises"},
        "fixtures": [{"args": [], "kwargs": {}}],
        "preconditions": [],
        "postconditions": [],
        "allowed_raises": ["ValueError"],
        "side_effects": [{"op": "file_created", "path": "marker"}],
        "cleanup": [{"adapter": "fixture.remove_marker"}],
    }, context=ContractContext(tmp_path, "a" * 64, registry))
    assert result.state is ResultState.PASS
    assert not marker.exists()
```

- [ ] **Step 2: Run contract tests and observe RED**

Run:

```bash
python -m pytest tests/test_contract_harness.py -q
```

Expected: FAIL during collection because `tools.code_intelligence.contracts` does not exist.

- [ ] **Step 3: Implement typed context, predicate evaluation, and guaranteed cleanup**

```python
# tools/code_intelligence/contracts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Mapping

from .adapters import AdapterRegistry
from .results import CheckResult, ResultState


@dataclass(frozen=True)
class ContractContext:
    repo_root: Path
    source_sha: str
    adapter_registry: AdapterRegistry


def evaluate_predicate(
    predicate: Mapping[str, object],
    *,
    fixture: Mapping[str, object],
    observation: Mapping[str, object],
) -> tuple[bool, dict[str, object]]:
    operation = predicate["op"]
    if operation == "arg_int_range":
        value = fixture["args"][int(predicate["index"])]
        passed = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and int(predicate["min"]) <= value <= int(predicate["max"])
        )
        return passed, {"operation": operation, "passed": passed}
    if operation == "return_equals":
        passed = observation.get("return_value") == predicate["value"]
        return passed, {"operation": operation, "passed": passed}
    if operation == "status_in":
        passed = observation.get("status_code") in predicate["values"]
        return passed, {"operation": operation, "passed": passed}
    if operation == "json_keys":
        keys = set(observation.get("json_keys", []))
        required = set(predicate["values"])
        passed = required <= keys
        return passed, {"operation": operation, "passed": passed, "missing": sorted(required - keys)}
    raise ValueError(f"contract predicate is not allowlisted: {operation}")


def run_contract(
    contract: Mapping[str, object],
    *,
    context: ContractContext,
) -> CheckResult:
    started = time.monotonic()
    contract_id = str(contract["id"])
    adapter = context.adapter_registry.require_contract_adapter(str(contract["adapter"]))
    cleanup_adapters = [
        context.adapter_registry.require_cleanup(item["adapter"])
        for item in contract.get("cleanup", [])
    ]
    try:
        for fixture in contract["fixtures"] or [{"args": [], "kwargs": {}}]:
            for predicate in contract["preconditions"]:
                passed, evidence = evaluate_predicate(
                    predicate,
                    fixture=fixture,
                    observation={},
                )
                if not passed:
                    return CheckResult(
                        name=f"contract:{contract_id}",
                        state=ResultState.FAIL,
                        summary="precondition failed",
                        evidence={
                            "phase": "precondition",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            **evidence,
                        },
                    )
            try:
                observation = adapter.execute(
                    contract["target"],
                    fixture,
                    timeout_seconds=float(contract.get("timeout_seconds", 5.0)),
                )
            except Exception as exc:
                allowed = type(exc).__name__ in contract["allowed_raises"]
                return CheckResult(
                    name=f"contract:{contract_id}",
                    state=ResultState.PASS if allowed else ResultState.FAIL,
                    summary="allowed raise observed" if allowed else "unexpected raise",
                    evidence={
                        "phase": "execute",
                        "exception_type": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
            for predicate in contract["postconditions"]:
                passed, evidence = evaluate_predicate(
                    predicate,
                    fixture=fixture,
                    observation=observation,
                )
                if not passed:
                    return CheckResult(
                        name=f"contract:{contract_id}",
                        state=ResultState.FAIL,
                        summary="postcondition failed",
                        evidence={
                            "phase": "postcondition",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            **evidence,
                        },
                    )
        return CheckResult(
            name=f"contract:{contract_id}",
            state=ResultState.PASS,
            summary="contract satisfied",
            evidence={
                "fixture_count": len(contract["fixtures"]) or 1,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
    finally:
        for cleanup in reversed(cleanup_adapters):
            cleanup()
```

- [ ] **Step 4: Add failing adapter tests for Flask, body contract, consumer agreement, and API smoke**

```python
def test_flask_route_adapter_records_shape_not_body(tmp_path: Path) -> None:
    context = fixture_flask_context(tmp_path)
    result = run_contract({
        "id": "C-HEALTH",
        "adapter": "flask_route",
        "target": {"app": "fixture.app", "method": "GET", "path": "/health"},
        "fixtures": [{"headers": {}, "json": None}],
        "preconditions": [],
        "postconditions": [
            {"op": "status_in", "values": [200]},
            {"op": "json_keys", "values": ["ok", "version"]},
        ],
        "allowed_raises": [],
        "side_effects": [],
        "cleanup": [],
    }, context=context)
    assert result.state is ResultState.PASS
    assert "body" not in result.evidence
    assert "headers" not in result.evidence


def test_consumer_agreement_adapter_uses_existing_checker(tmp_path: Path) -> None:
    context = fixture_consumer_agreement_context(tmp_path)
    result = run_contract(
        normalized_consumer_agreement_contract(),
        context=context,
    )
    assert result.state is ResultState.PASS
    assert result.name == "contract:CT-rec-url-shape"
```

- [ ] **Step 5: Implement bounded specialized adapters without replacing existing tools**

Add these exact adapter classes to the foundation adapter registry:

```python
class PureFunctionContractAdapter:
    def execute(
        self,
        target: Mapping[str, object],
        fixture: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        function = self.registry.require_pure_function(str(target["name"]))
        value = self.deadline.call(
            function,
            tuple(fixture.get("args", [])),
            dict(fixture.get("kwargs", {})),
            timeout_seconds=timeout_seconds,
        )
        return {"return_value": value}


class FlaskRouteContractAdapter:
    def execute(
        self,
        target: Mapping[str, object],
        fixture: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        response = self.deadline.flask_request(
            app_adapter=str(target["app"]),
            method=str(target["method"]),
            path=str(target["path"]),
            headers=dict(fixture.get("headers", {})),
            json_body=fixture.get("json"),
            timeout_seconds=timeout_seconds,
        )
        payload = response.get_json(silent=True)
        return {
            "status_code": response.status_code,
            "json_keys": sorted(payload) if isinstance(payload, dict) else [],
            "content_type": response.content_type,
        }
```

The `body_contract_fixture`, `consumer_agreement`, and `api_contract_smoke` adapters invoke the existing tools through direct functions when available or `subprocess.run([...], shell=False, timeout=...)`; return only exit code, result counts, and output hashes.

- [ ] **Step 6: Implement registry runner and CLI**

`tools/contract_harness.py` mirrors `invariant_probe.py`: explicit `--contracts`, optional `--repo`, repeatable `--only`, `--gate`, and `--json`. It validates that the registry's `source_sha` equals `build_snapshot(repo).source_sha` before execution. A mismatch yields `ResultState.UNKNOWN`, and `exit_code(results, gate=True)` makes it blocking.

The JSON envelope is:

```json
{
  "status": "pass",
  "source_sha": "64-hex",
  "results": [
    {
      "blocking": false,
      "component": "contract:CT-rec-url-shape",
      "duration_ms": 12,
      "evidence": {"fixture_count": 1},
      "status": "pass",
      "summary": "contract satisfied"
    }
  ]
}
```

- [ ] **Step 7: Run contract, body-contract, API-contract, and existing contract tests**

Run:

```bash
python -m pytest \
  tests/test_contract_harness.py \
  tests/test_contracts.py \
  tests/test_v3_66_726_body_contract.py \
  tests/test_v3_66_729_body_contract_fixtures.py \
  tests/test_v3_66_764_contract_probe_csrf_derived.py -q
python tools/contract_harness.py --help
```

Expected: all selected tests pass; no contract test is skipped because an adapter is absent.

- [ ] **Step 8: Update schema and operator documentation**

Update `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md` with the implemented v2 contract record and state:

```json
{
  "id": "C-HEALTH",
  "adapter": "flask_route",
  "target": {"app": "app_factory", "method": "GET", "path": "/api/health"},
  "fixtures": [{"headers": {}, "json": null}],
  "preconditions": [],
  "postconditions": [{"op": "status_in", "values": [200]}],
  "allowed_raises": [],
  "side_effects": [],
  "cleanup": [],
  "timeout_seconds": 5.0,
  "provenance": {},
  "extensions": {}
}
```

Document that raw response bodies, request authorization, cookies, and signed URLs never enter results.

- [ ] **Step 9: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/contracts.py \
  tools/contract_harness.py \
  tools/consumer_agreement.py \
  project-knowledge/CONTRACTS.json \
  project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md \
  project-knowledge/CODE_INTELLIGENCE_TOOLING.md \
  tests/test_contract_harness.py
git status --short
```

Expected: no whitespace errors; the existing `CT-rec-url-shape` contract is still present; all changes remain uncommitted.

---

### Task 7: `bd-review-next` Risk Routing, Leases, and Contention-Safe Claims

**Files:**
- Create: `tools/code_intelligence/review_allocator.py`
- Create: `tools/bd_review_next.py`
- Create: `toolchain/bin/bd-review-next`
- Create: `tests/test_bd_review_next.py`
- Modify: `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`

**Interfaces:**
- Consumes:
  - Canonical `REVIEW_STATE.json`.
  - Source-bound analysis artifacts `RISK_SCORES.json`, `COVERAGE_GAPS.json`, and `MODULE_CATALOG.json`.
  - `build_snapshot(repo_root) -> TreeSnapshot`.
  - `exclusive_file_lock(...)`.
- Produces:
  - `ReviewScope(files: tuple[str, ...], functions: tuple[str, ...])`
  - `ReviewClaim(claim_id: str, owner: str, issued_at: str, expires_at: str, source_sha: str, scope: ReviewScope)`
  - `ReviewCandidate(path: str, risk_score: float, stale: bool, coverage_status: str, unresolved_findings: int, functions: tuple[str, ...])`
  - `rank_candidates(*, review_state: Mapping[str, object], risk_scores: Mapping[str, object], coverage: Mapping[str, object], module_catalog: Mapping[str, object], source_sha: str, now: datetime) -> tuple[ReviewCandidate, ...]`
  - `claim_next_slice(*, state_path: Path, repo_root: Path, risk_path: Path, coverage_path: Path, module_catalog_path: Path, owner: str, lease_seconds: int, max_files: int, expected_state_sha: str | None, now: datetime, write: bool) -> ClaimResult`
  - `complete_claim(*, state_path: Path, repo_root: Path, claim_path: Path, audit_path: Path, expected_state_sha: str | None, generated_at: str) -> ClaimResult`
  - `review_status(*, state_path: Path, repo_root: Path, now: datetime) -> dict[str, object]`
  - `attach_l3_evidence(*, state_path: Path, repo_root: Path, artifact_paths: Mapping[str, Path], out_path: Path, expected_state_sha: str | None, generated_at: str) -> dict[str, object]`
  - `ClaimResult(status: str, claim: ReviewClaim | None, state_sha_before: str, state_sha_after: str | None, wrote: bool)`
  - Claim CLI: `bd-review-next claim --root ROOT --artifacts ARTIFACTS --owner OWNER --level L2 --lease-seconds N --out CLAIM_JSON`
  - Complete CLI: `bd-review-next complete --root ROOT --artifacts ARTIFACTS --claim CLAIM_JSON --audit AUDIT_JSON`
  - Status CLI: `bd-review-next status --root ROOT --artifacts ARTIFACTS --json`
  - L3 CLI: `bd-review-next attach-l3 --root ROOT --artifacts ARTIFACTS --semantic SEMANTIC.json --reachability REACHABILITY.json --oracle ORACLE.json --fuzz FUZZ.json --invariants INVARIANTS.json --contracts CONTRACTS.json --out RESULT_JSON`
  - Compatibility claim alias: `bd-review-next --state PATH --risk PATH --coverage PATH --module-catalog PATH --owner OWNER [--lease-seconds 3600] [--max-files 5] [--expected-state-sha SHA] [--peek] [--json]`
  - Exit codes: `0=claim or valid peek`, `1=no eligible work`, `2=invalid/stale artifact`, `3=claim/SHA conflict`, `4=lock/write error`.

- [ ] **Step 1: Write failing deterministic ranking tests**

```python
from datetime import datetime, timezone

from tools.code_intelligence.review_allocator import rank_candidates


NOW = datetime(2026, 7, 23, 1, 2, 3, tzinfo=timezone.utc)


def test_rank_prefers_stale_then_risk_then_path() -> None:
    state = {
        "source_sha": "a" * 64,
        "files": {
            "bulk_downloader/a.py": {
                "status": "unreviewed",
                "reaudit_required": False,
                "finding_ids": [],
                "claim": None,
            },
            "bulk_downloader/b.py": {
                "status": "stale",
                "reaudit_required": True,
                "finding_ids": ["F-1"],
                "claim": None,
            },
            "bulk_downloader/c.py": {
                "status": "unreviewed",
                "reaudit_required": False,
                "finding_ids": [],
                "claim": None,
            },
        },
        "findings": {"F-1": {"status": "open"}},
    }
    risk = {
        "source_sha": "a" * 64,
        "modules": {
            "bulk_downloader/a.py": {"risk_score": 9.0},
            "bulk_downloader/b.py": {"risk_score": 2.0},
            "bulk_downloader/c.py": {"risk_score": 9.0},
        },
    }
    coverage = {
        "source_sha": "a" * 64,
        "modules": {
            "bulk_downloader/a.py": {"status": "known"},
            "bulk_downloader/b.py": {"status": "unknown"},
            "bulk_downloader/c.py": {"status": "known"},
        },
    }
    catalog = {
        "source_sha": "a" * 64,
        "modules": {
            path: {"functions": []}
            for path in state["files"]
        },
    }

    ranked = rank_candidates(
        review_state=state,
        risk_scores=risk,
        coverage=coverage,
        module_catalog=catalog,
        source_sha="a" * 64,
        now=NOW,
    )

    assert tuple(item.path for item in ranked) == (
        "bulk_downloader/b.py",
        "bulk_downloader/a.py",
        "bulk_downloader/c.py",
    )
```

- [ ] **Step 2: Run the ranking test and observe RED**

Run:

```bash
python -m pytest tests/test_bd_review_next.py::test_rank_prefers_stale_then_risk_then_path -q
```

Expected: FAIL during collection because `review_allocator.py` does not exist.

- [ ] **Step 3: Implement source-bound deterministic ranking and lease eligibility**

```python
# tools/code_intelligence/review_allocator.py
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

from .artifacts import atomic_write_json, artifact_hash
from .governance_io import (
    load_json_object,
    validate_governance_artifact,
    write_validated_json,
)
from .locking import exclusive_file_lock
from .review_state import merge_audit_into_state
from .schemas import make_envelope, validate_envelope
from .snapshot import build_snapshot


class ClaimConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewScope:
    files: tuple[str, ...]
    functions: tuple[str, ...]


@dataclass(frozen=True)
class ReviewClaim:
    claim_id: str
    owner: str
    issued_at: str
    expires_at: str
    source_sha: str
    scope: ReviewScope


@dataclass(frozen=True)
class ReviewCandidate:
    path: str
    risk_score: float
    stale: bool
    coverage_status: str
    unresolved_findings: int
    functions: tuple[str, ...]


@dataclass(frozen=True)
class ClaimResult:
    status: str
    claim: ReviewClaim | None
    state_sha_before: str
    state_sha_after: str | None
    wrote: bool


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _eligible(record: Mapping[str, object], *, source_sha: str, now: datetime) -> bool:
    if record.get("status") == "reviewed" and not record.get("reaudit_required"):
        return False
    claim = record.get("claim")
    if not claim:
        return True
    if claim.get("source_sha") != source_sha:
        return True
    return _parse_time(str(claim["expires_at"])) <= now


def rank_candidates(
    *,
    review_state: Mapping[str, object],
    risk_scores: Mapping[str, object],
    coverage: Mapping[str, object],
    module_catalog: Mapping[str, object],
    source_sha: str,
    now: datetime,
) -> tuple[ReviewCandidate, ...]:
    for name, artifact in (
        ("review_state", review_state),
        ("risk_scores", risk_scores),
        ("coverage", coverage),
        ("module_catalog", module_catalog),
    ):
        if artifact["source_sha"] != source_sha:
            raise ValueError(f"{name} source SHA differs from tracked tree")
    candidates: list[ReviewCandidate] = []
    for path, record in sorted(review_state["files"].items()):
        if not _eligible(record, source_sha=source_sha, now=now):
            continue
        unresolved = sum(
            1 for finding_id in record.get("finding_ids", [])
            if review_state["findings"].get(finding_id, {}).get("status") == "open"
        )
        candidates.append(ReviewCandidate(
            path=path,
            risk_score=float(
                risk_scores["modules"].get(path, {}).get("risk_score", 0.0)
            ),
            stale=bool(record.get("reaudit_required")),
            coverage_status=str(
                coverage["modules"].get(path, {}).get("status", "unknown")
            ),
            unresolved_findings=unresolved,
            functions=tuple(sorted(
                module_catalog["modules"].get(path, {}).get("functions", [])
            )),
        ))
    return tuple(sorted(
        candidates,
        key=lambda item: (
            -int(item.stale),
            -item.unresolved_findings,
            -item.risk_score,
            0 if item.coverage_status == "unknown" else 1,
            item.path,
        ),
    ))
```

- [ ] **Step 4: Add failing tests for contention, expiry recovery, and source-SHA invalidation**

```python
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta


def test_two_workers_cannot_claim_the_same_scope(tmp_path: Path) -> None:
    fixture = write_allocator_fixture(tmp_path, file_count=1)

    def claim(owner: str):
        return claim_next_slice(
            **fixture,
            owner=owner,
            lease_seconds=3600,
            max_files=1,
            expected_state_sha=None,
            now=NOW,
            write=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))

    claims = [result.claim for result in results if result.claim is not None]
    assert len(claims) == 1
    assert claims[0].scope.files == ("bulk_downloader/m0.py",)


def test_expired_claim_is_recoverable(tmp_path: Path) -> None:
    fixture = write_allocator_fixture(tmp_path, file_count=1)
    first = claim_next_slice(
        **fixture,
        owner="worker-a",
        lease_seconds=60,
        max_files=1,
        expected_state_sha=None,
        now=NOW,
        write=True,
    )
    second = claim_next_slice(
        **fixture,
        owner="worker-b",
        lease_seconds=60,
        max_files=1,
        expected_state_sha=None,
        now=NOW + timedelta(seconds=61),
        write=True,
    )
    assert first.claim is not None
    assert second.claim is not None
    assert second.claim.owner == "worker-b"
    assert second.claim.scope == first.claim.scope


def test_source_sha_change_invalidates_existing_claim(tmp_path: Path) -> None:
    fixture = write_allocator_fixture(tmp_path, file_count=1)
    first = claim_next_slice(
        **fixture,
        owner="worker-a",
        lease_seconds=3600,
        max_files=1,
        expected_state_sha=None,
        now=NOW,
        write=True,
    )
    rebind_allocator_fixture(fixture)
    second = claim_next_slice(
        **fixture,
        owner="worker-b",
        lease_seconds=3600,
        max_files=1,
        expected_state_sha=None,
        now=NOW + timedelta(seconds=1),
        write=True,
    )
    assert first.claim is not None
    assert second.claim is not None
    assert second.claim.source_sha != first.claim.source_sha


def test_complete_validates_claim_and_clears_lease_atomically(tmp_path: Path) -> None:
    fixture = write_allocator_fixture(tmp_path, file_count=1)
    claimed = claim_next_slice(
        **fixture,
        owner="worker-a",
        lease_seconds=3600,
        max_files=1,
        expected_state_sha=None,
        now=NOW,
        write=True,
    )
    claim_path, audit_path = write_claim_and_audit(tmp_path, claimed.claim)
    completed = complete_claim(
        state_path=fixture["state_path"],
        repo_root=fixture["repo_root"],
        claim_path=claim_path,
        audit_path=audit_path,
        expected_state_sha=claimed.state_sha_after,
        generated_at="2026-07-23T02:00:00Z",
    )
    state = json.loads(fixture["state_path"].read_text(encoding="utf-8"))
    record = state["files"]["bulk_downloader/m0.py"]
    assert completed.status == "pass"
    assert record["claim"] is None
    assert record["status"] == "reviewed"
    assert record["review_level"] == "L2"


def test_attach_l3_rejects_mismatched_source_without_writes(tmp_path: Path) -> None:
    fixture = write_allocator_fixture(tmp_path, file_count=1)
    artifact_paths = write_l3_artifacts(tmp_path, source_sha="0" * 64)
    before = fixture["state_path"].read_bytes()
    with pytest.raises(ValueError, match="source SHA"):
        attach_l3_evidence(
            state_path=fixture["state_path"],
            repo_root=fixture["repo_root"],
            artifact_paths=artifact_paths,
            out_path=tmp_path / "L3_RESULT.json",
            expected_state_sha=None,
            generated_at="2026-07-23T02:00:00Z",
        )
    assert fixture["state_path"].read_bytes() == before
    assert not (tmp_path / "L3_RESULT.json").exists()


def test_status_reports_active_and_expired_claims(tmp_path: Path) -> None:
    fixture = write_allocator_fixture(tmp_path, file_count=2)
    claim_next_slice(
        **fixture,
        owner="worker-a",
        lease_seconds=60,
        max_files=1,
        expected_state_sha=None,
        now=NOW,
        write=True,
    )
    status = review_status(
        state_path=fixture["state_path"],
        repo_root=fixture["repo_root"],
        now=NOW + timedelta(seconds=61),
    )
    assert status["claims"] == {"active": 0, "expired": 1}
    assert status["source_sha"] == build_snapshot(fixture["repo_root"]).source_sha
```

- [ ] **Step 5: Implement locked claim creation and atomic ledger update**

```python
# append to tools/code_intelligence/review_allocator.py
def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claim_id(
    owner: str,
    source_sha: str,
    scope: ReviewScope,
    issued_at: str,
) -> str:
    body = json.dumps({
        "owner": owner,
        "source_sha": source_sha,
        "files": scope.files,
        "functions": scope.functions,
        "issued_at": issued_at,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "CL-" + sha256(body).hexdigest()[:16].upper()


def claim_next_slice(
    *,
    state_path: Path,
    repo_root: Path,
    risk_path: Path,
    coverage_path: Path,
    module_catalog_path: Path,
    owner: str,
    lease_seconds: int,
    max_files: int,
    expected_state_sha: str | None,
    now: datetime,
    write: bool,
) -> ClaimResult:
    if not owner.strip():
        raise ValueError("owner must be non-empty")
    if not 60 <= lease_seconds <= 86_400:
        raise ValueError("lease_seconds must be in [60, 86400]")
    if not 1 <= max_files <= 25:
        raise ValueError("max_files must be in [1, 25]")
    snapshot = build_snapshot(repo_root)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with exclusive_file_lock(
        lock_path,
        timeout_seconds=10.0,
        stale_after_seconds=300.0,
    ):
        state = load_json_object(state_path)
        state_sha = artifact_hash(state)
        if expected_state_sha is not None and state_sha != expected_state_sha:
            raise ClaimConflict(
                f"review state changed: expected {expected_state_sha}, found {state_sha}"
            )
        ranked = rank_candidates(
            review_state=state,
            risk_scores=load_json_object(risk_path),
            coverage=load_json_object(coverage_path),
            module_catalog=load_json_object(module_catalog_path),
            source_sha=snapshot.source_sha,
            now=now,
        )
        selected = ranked[:max_files]
        if not selected:
            return ClaimResult("advisory", None, state_sha, None, False)
        scope = ReviewScope(
            files=tuple(item.path for item in selected),
            functions=tuple(sorted({
                function
                for item in selected
                for function in item.functions
            })),
        )
        issued_at = _iso(now)
        expires_at = _iso(now + timedelta(seconds=lease_seconds))
        claim = ReviewClaim(
            claim_id=_claim_id(owner, snapshot.source_sha, scope, issued_at),
            owner=owner,
            issued_at=issued_at,
            expires_at=expires_at,
            source_sha=snapshot.source_sha,
            scope=scope,
        )
        if not write:
            return ClaimResult("advisory", claim, state_sha, None, False)
        updated = deepcopy(state)
        claim_payload = {
            **asdict(claim),
            "scope": asdict(claim.scope),
        }
        for path in scope.files:
            updated["files"][path]["claim"] = claim_payload
            updated["files"][path]["status"] = "in_progress"
        write_validated_json(
            state_path,
            updated,
            lambda value: validate_governance_artifact("review_state", value),
        )
        return ClaimResult(
            "pass",
            claim,
            state_sha,
            artifact_hash(updated),
            True,
        )


def complete_claim(
    *,
    state_path: Path,
    repo_root: Path,
    claim_path: Path,
    audit_path: Path,
    expected_state_sha: str | None,
    generated_at: str,
) -> ClaimResult:
    claim = load_json_object(claim_path)
    audit = load_json_object(audit_path)
    snapshot = build_snapshot(repo_root)
    if claim["source_sha"] != snapshot.source_sha or audit["source_sha"] != snapshot.source_sha:
        raise ValueError("claim/audit source SHA differs from tracked tree")
    with exclusive_file_lock(
        state_path.with_suffix(".json.lock"),
        timeout_seconds=10.0,
        stale_after_seconds=300.0,
    ):
        state = load_json_object(state_path)
        before = artifact_hash(state)
        if expected_state_sha is not None and before != expected_state_sha:
            raise ClaimConflict("review state compare-and-swap conflict")
        updated = merge_audit_into_state(
            state=state,
            audit=audit,
            expected_claim_id=claim["claim_id"],
            expected_owner=claim["owner"],
            generated_at=generated_at,
        )
        for path in claim["scope"]["files"]:
            updated["files"][path]["claim"] = None
            updated["files"][path]["status"] = "reviewed"
            updated["files"][path]["review_level"] = audit["review_level"]
        write_validated_json(
            state_path,
            updated,
            lambda value: validate_governance_artifact("review_state", value),
        )
        return ClaimResult("pass", None, before, artifact_hash(updated), True)


def attach_l3_evidence(
    *,
    state_path: Path,
    repo_root: Path,
    artifact_paths: Mapping[str, Path],
    out_path: Path,
    expected_state_sha: str | None,
    generated_at: str,
) -> dict[str, object]:
    snapshot = build_snapshot(repo_root)
    loaded = {name: load_json_object(path) for name, path in sorted(artifact_paths.items())}
    mismatched = sorted(
        name for name, value in loaded.items()
        if value["source_sha"] != snapshot.source_sha
    )
    if mismatched:
        raise ValueError(f"L3 source SHA mismatch: {mismatched}")
    hashes = {name: artifact_hash(value) for name, value in loaded.items()}
    with exclusive_file_lock(
        state_path.with_suffix(".json.lock"),
        timeout_seconds=10.0,
        stale_after_seconds=300.0,
    ):
        state = load_json_object(state_path)
        before = artifact_hash(state)
        if expected_state_sha is not None and before != expected_state_sha:
            raise ClaimConflict("review state compare-and-swap conflict")
        updated = deepcopy(state)
        for record in updated["files"].values():
            if record["status"] in {"reviewed", "in_progress"}:
                record["evidence_hashes"] = sorted(
                    set(record["evidence_hashes"]) | set(hashes.values())
                )
        write_validated_json(
            state_path,
            updated,
            lambda value: validate_governance_artifact("review_state", value),
        )
        result = {
            **make_envelope(
                "bd.review-l3-attachment",
                1,
                snapshot.source_sha,
                "1.0.0",
                hashes,
            ),
            "state_sha_before": before,
            "state_sha_after": artifact_hash(updated),
            "evidence_hashes": hashes,
        }
        atomic_write_json(out_path, result, validate_envelope)
        return result


def review_status(
    *,
    state_path: Path,
    repo_root: Path,
    now: datetime,
) -> dict[str, object]:
    state = load_json_object(state_path)
    snapshot = build_snapshot(repo_root)
    if state["source_sha"] != snapshot.source_sha:
        raise ValueError("review-state source SHA differs from tracked tree")
    active = expired = 0
    for record in state["files"].values():
        claim = record.get("claim")
        if not claim:
            continue
        if _parse_time(claim["expires_at"]) <= now:
            expired += 1
        else:
            active += 1
    return {
        **make_envelope(
            "bd.review-status",
            1,
            snapshot.source_sha,
            "1.0.0",
            {"review_state": artifact_hash(state)},
        ),
        "claims": {"active": active, "expired": expired},
        "statuses": dict(sorted(Counter(
            record["status"] for record in state["files"].values()
        ).items())),
    }
```

- [ ] **Step 6: Implement CLI and exact executable wrapper**

`tools/bd_review_next.py` must:

1. parse the exact arguments from the Interfaces block;
2. discover `--repo` only when omitted;
3. convert `--peek` to `write=False` and otherwise claim atomically;
4. use current UTC when no test-injected clock is supplied;
5. print a sorted JSON envelope with claim scope and both state SHAs; and
6. return `1` with `{"status":"advisory","claim":null}` when there is no eligible work.

Its subparser dispatches `claim`, `complete`, `status`, and `attach-l3` to the exact interfaces above. `complete` uses the canonical `merge_audit_into_state()` path shared with `review_merge.py`; it may not duplicate merge semantics in the CLI. `status` is read-only. `attach-l3` validates all six source SHAs before taking the lock or writing either state or its result file.

`toolchain/bin/bd-review-next` uses the repository-relative wrapper pattern and imports `tools.bd_review_next.main`.

- [ ] **Step 7: Add exact fixture helpers and run concurrency tests**

The fixture helper writes one Python module plus source-bound artifacts:

```python
def write_claim_and_audit(
    tmp_path: Path,
    claim: ReviewClaim,
) -> tuple[Path, Path]:
    claim_path = tmp_path / "claim.json"
    claim_payload = {**asdict(claim), "scope": asdict(claim.scope)}
    claim_path.write_text(json.dumps(claim_payload), encoding="utf-8")
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps({
        "schema_name": "bd.audit",
        "schema_version": 1,
        "source_sha": claim.source_sha,
        "tool_version": "1.0.0",
        "input_hashes": {"claim": artifact_hash(claim_payload)},
        "generated_at": "2026-07-23T01:30:00Z",
        "claim_id": claim.claim_id,
        "owner": claim.owner,
        "review_level": "L2",
        "files": list(claim.scope.files),
        "evidence_hashes": ["e" * 64],
        "findings": [],
    }), encoding="utf-8")
    return claim_path, audit_path


def write_l3_artifacts(
    tmp_path: Path,
    *,
    source_sha: str,
) -> dict[str, Path]:
    paths = {}
    for name in (
        "semantic", "reachability", "oracle", "fuzz", "invariants", "contracts"
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({
            "schema_name": f"bd.{name}",
            "schema_version": 1,
            "source_sha": source_sha,
            "tool_version": "1.0.0",
            "input_hashes": {"fixture": "a" * 64},
            "generated_at": "2026-07-23T01:30:00Z",
            "results": [],
        }), encoding="utf-8")
        paths[name] = path
    return paths


def allocator_state(snapshot, paths: tuple[str, ...]) -> dict[str, object]:
    facts_by_path = {fact.path: fact for fact in snapshot.files}
    return {
        "schema_name": "bd.review-state",
        "schema_version": 2,
        "source_sha": snapshot.source_sha,
        "tool_version": "1.0.0",
        "input_hashes": {"tracked_snapshot": snapshot.source_sha},
        "generated_at": "2026-07-23T00:00:00Z",
        "files": {
            path: {
                "sha256": facts_by_path[path].sha256,
                "lines": facts_by_path[path].lines,
                "status": "unreviewed",
                "review_level": "none",
                "reviewed_at_sha": None,
                "finding_ids": [],
                "invariant_ids": [],
                "contract_ids": [],
                "test_ids": [],
                "evidence_hashes": [],
                "claim": None,
                "stale_reason": None,
                "reaudit_required": False,
            }
            for path in paths
        },
        "findings": {},
    }


def source_bound_modules(
    snapshot,
    paths: tuple[str, ...],
    field: str,
    value: object,
) -> dict[str, object]:
    return {
        "schema_name": f"fixture.{field}",
        "schema_version": 1,
        "source_sha": snapshot.source_sha,
        "tool_version": "1.0.0",
        "input_hashes": {"tracked_snapshot": snapshot.source_sha},
        "generated_at": "2026-07-23T00:00:00Z",
        "modules": {path: {field: value} for path in paths},
    }


def write_allocator_fixture(tmp_path: Path, *, file_count: int) -> dict[str, object]:
    repo = tmp_path / "repo"
    package = repo / "bulk_downloader"
    package.mkdir(parents=True)
    for index in range(file_count):
        (package / f"m{index}.py").write_text(
            f"VALUE = {index}\n",
            encoding="utf-8",
        )
    snapshot = build_snapshot(repo)
    paths = tuple(sorted(fact.path for fact in snapshot.files))
    state = allocator_state(snapshot, paths)
    risk = source_bound_modules(snapshot, paths, "risk_score", 1.0)
    coverage = source_bound_modules(snapshot, paths, "status", "known")
    catalog = source_bound_modules(snapshot, paths, "functions", [])
    state_path = tmp_path / "REVIEW_STATE.json"
    risk_path = tmp_path / "RISK_SCORES.json"
    coverage_path = tmp_path / "COVERAGE_GAPS.json"
    catalog_path = tmp_path / "MODULE_CATALOG.json"
    for path, payload in (
        (state_path, state),
        (risk_path, risk),
        (coverage_path, coverage),
        (catalog_path, catalog),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    return {
        "state_path": state_path,
        "repo_root": repo,
        "risk_path": risk_path,
        "coverage_path": coverage_path,
        "module_catalog_path": catalog_path,
    }


def rebind_allocator_fixture(fixture: dict[str, object]) -> None:
    repo = fixture["repo_root"]
    module = repo / "bulk_downloader" / "m0.py"
    module.write_text("VALUE = 999\n", encoding="utf-8")
    snapshot = build_snapshot(repo)
    paths = tuple(sorted(fact.path for fact in snapshot.files))
    payloads = (
        (fixture["state_path"], allocator_state(snapshot, paths)),
        (fixture["risk_path"], source_bound_modules(snapshot, paths, "risk_score", 1.0)),
        (fixture["coverage_path"], source_bound_modules(snapshot, paths, "status", "known")),
        (fixture["module_catalog_path"], source_bound_modules(snapshot, paths, "functions", [])),
    )
    for path, payload in payloads:
        path.write_text(json.dumps(payload), encoding="utf-8")
```

Run:

```bash
python -m pytest tests/test_bd_review_next.py -q
python tools/bd_review_next.py --help
toolchain/bin/bd-review-next --help
```

Expected: all tests pass repeatedly; contention never yields duplicate file scopes; help lists `claim`, `complete`, `status`, and `attach-l3`; the flat alias documents `--peek` as non-mutating.

- [ ] **Step 8: Document claim recovery and operator use**

Add to `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`:

```bash
bd-review-next --state REVIEW_STATE.json \
  --risk RISK_SCORES.json \
  --coverage COVERAGE_GAPS.json \
  --module-catalog MODULE_CATALOG.json \
  --owner audit-worker-01 \
  --lease-seconds 3600 \
  --max-files 5 \
  --peek --json
```

Then show the same command without `--peek` and with the printed `--expected-state-sha`. Document expiry recovery, source-SHA invalidation, maximum 25 files, and the fact that claiming changes only audit state.

- [ ] **Step 9: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
git diff -- \
  tools/code_intelligence/review_allocator.py \
  tools/bd_review_next.py \
  toolchain/bin/bd-review-next \
  tests/test_bd_review_next.py \
  project-knowledge/CODE_INTELLIGENCE_TOOLING.md
git status --short
```

Expected: no whitespace errors; no duplicate-claim path is visible in tests; all changes remain uncommitted.

---

### Task 8: Final Fail-Closed Composite `bd-audit-gate` Wiring

**Files:**
- Create: `tests/test_bd_audit_gate_composite.py`
- Create: `tools/code_intelligence/audit_gate.py`
- Modify: `tools/bd-audit-gate.py`
- Modify: `project-knowledge/bd-audit-gate.py`
- Modify: `toolchain/bin/bd-audit-gate.py`
- Modify: `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`
- Modify: `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md`

**Interfaces:**
- Consumes standalone component commands from the foundation, analysis-frontends, and Tasks 1-7.
- Produces:
  - `GateComponent(name: str, bit: int, required: bool, expensive: bool, command: tuple[str, ...])`
  - `run_component(component: GateComponent, *, repo_root: Path, timeout_seconds: float) -> CheckResult`
  - `run_composite_gate(*, repo_root: Path, artifact_root: Path, baseline_root: Path, required: tuple[str, ...], include_expensive: tuple[str, ...], timeout_seconds: float) -> CompositeGateResult`
  - `CompositeGateResult(status: str, bitmask: int, source_sha: str, results: tuple[CheckResult, ...])`
  - Unique bit values:
    - `1` graph/source hash
    - `2` defect patterns
    - `4` semantic diff
    - `8` invariant schema
    - `16` invariant probes
    - `32` contracts
    - `64` review staleness
    - `128` reachability
    - `256` differential oracle
    - `512` frozen fuzz replay
    - `1024` coverage policy
    - `2048` witnesses
    - `4096` constraint topology
    - `8192` consumer agreement
  - Required compatibility CLI: `--root ROOT --artifacts ARTIFACTS --required all --json-out RESULT.json`.
  - Additional aliases/options: `--repo`, `--baseline`, repeatable `--require`, repeatable `--include-expensive`, `--component-timeout`, and `--json`.
  - Exit codes: `0=all required selected components pass`, `1=one or more blocking results`, `2=invalid configuration/schema`.

- [ ] **Step 1: Write failing tests proving missing required components cannot pass**

```python
from pathlib import Path

from tools.code_intelligence.audit_gate import GateComponent, run_composite_gate
from tools.code_intelligence.results import ResultState


def test_missing_required_component_sets_bit_and_fails(tmp_path: Path) -> None:
    repo, artifacts, baseline = write_composite_fixture(tmp_path)
    result = run_composite_gate(
        repo_root=repo,
        artifact_root=artifacts,
        baseline_root=baseline,
        required=("contracts",),
        include_expensive=(),
        timeout_seconds=1.0,
        component_override={
            "contracts": GateComponent(
                name="contracts",
                bit=32,
                required=True,
                expensive=False,
                command=("tools/does-not-exist.py",),
            )
        },
    )
    assert result.status == "fail"
    assert result.bitmask & 32
    contract = next(item for item in result.results if item.name == "contracts")
    assert contract.state is ResultState.ERROR


def test_unselected_expensive_component_is_advisory_not_pass(tmp_path: Path) -> None:
    repo, artifacts, baseline = write_composite_fixture(tmp_path)
    result = run_composite_gate(
        repo_root=repo,
        artifact_root=artifacts,
        baseline_root=baseline,
        required=(),
        include_expensive=(),
        timeout_seconds=1.0,
    )
    fuzz = next(item for item in result.results if item.name == "fuzz_replay")
    assert fuzz.state is ResultState.ADVISORY
    assert fuzz.summary == "expensive component not selected"
```

- [ ] **Step 2: Run the composite tests and observe RED**

Run:

```bash
python -m pytest tests/test_bd_audit_gate_composite.py -q
```

Expected: FAIL because the current `bd-audit-gate.py` has no importable `run_composite_gate`.

- [ ] **Step 3: Refactor the gate into explicit component data and a machine result**

```python
# tools/code_intelligence/audit_gate.py
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Mapping

from tools.code_intelligence.results import CheckResult, ResultState
from tools.code_intelligence.snapshot import build_snapshot


@dataclass(frozen=True)
class GateComponent:
    name: str
    bit: int
    required: bool
    expensive: bool
    command: tuple[str, ...]


@dataclass(frozen=True)
class CompositeGateResult:
    status: str
    bitmask: int
    source_sha: str
    results: tuple[CheckResult, ...]


COMPONENTS = (
    GateComponent("graph_source_hash", 1, True, False, ("tools/graph_build.py", "--check-hash")),
    GateComponent("defect_patterns", 2, True, False, ("tools/defect_patterns.py", "--check")),
    GateComponent("semantic_diff", 4, True, False, ("tools/semantic_diff.py", "--gate")),
    GateComponent("invariant_schema", 8, True, False, ("tools/invariants.py", "--check")),
    GateComponent("invariant_probes", 16, True, False, ("tools/invariant_probe.py", "--gate")),
    GateComponent("contracts", 32, True, False, ("tools/contract_harness.py", "--gate")),
    GateComponent("review_staleness", 64, True, False, ("tools/staleness.py", "stale", "--gate")),
    GateComponent("reachability", 128, True, False, ("tools/reachability.py", "--gate")),
    GateComponent("differential_oracle", 256, False, True, ("tools/differential_oracle.py", "--replay", "--gate")),
    GateComponent("fuzz_replay", 512, False, True, ("tools/fuzz_harness.py", "--replay", "--gate")),
    GateComponent("coverage_policy", 1024, True, False, ("toolchain/bin/bd-coverage-map", "--check")),
    GateComponent("witnesses", 2048, True, False, ("tools/run_witnesses.py",)),
    GateComponent("constraint_topology", 4096, True, False, ("tools/constraint_incidence.py", "topology", "--gate")),
    GateComponent("consumer_agreement", 8192, True, False, ("tools/consumer_agreement.py", "--gate")),
)
```

- [ ] **Step 4: Add failing tests for source-SHA mismatch, timeout, output hashing, and bit uniqueness**

```python
def test_component_bits_are_unique_powers_of_two() -> None:
    bits = [component.bit for component in COMPONENTS]
    assert len(bits) == len(set(bits))
    assert all(bit > 0 and bit & (bit - 1) == 0 for bit in bits)


def test_component_timeout_blocks_required_gate(tmp_path: Path) -> None:
    repo, artifacts, baseline = write_composite_fixture(tmp_path)
    result = run_composite_gate(
        repo_root=repo,
        artifact_root=artifacts,
        baseline_root=baseline,
        required=("contracts",),
        include_expensive=(),
        timeout_seconds=0.05,
        component_override={
            "contracts": GateComponent(
                "contracts", 32, True, False,
                ("tests/fixtures/sleep_tool.py",),
            )
        },
    )
    contract = next(item for item in result.results if item.name == "contracts")
    assert contract.state is ResultState.TIMEOUT
    assert result.bitmask & 32


def test_graph_and_review_source_sha_mismatch_fails_before_components(
    tmp_path: Path,
) -> None:
    repo, artifacts, baseline = write_composite_fixture(tmp_path)
    review = json.loads((artifacts / "REVIEW_STATE.json").read_text(encoding="utf-8"))
    review["source_sha"] = "0" * 64
    (artifacts / "REVIEW_STATE.json").write_text(json.dumps(review), encoding="utf-8")
    result = run_composite_gate(
        repo_root=repo,
        artifact_root=artifacts,
        baseline_root=baseline,
        required=(),
        include_expensive=(),
        timeout_seconds=1.0,
    )
    assert result.status == "fail"
    assert result.bitmask & 1
```

- [ ] **Step 5: Implement bounded component execution and required/expensive policy**

```python
# append to tools/code_intelligence/audit_gate.py
def run_component(
    component: GateComponent,
    *,
    repo_root: Path,
    timeout_seconds: float,
) -> CheckResult:
    started = time.monotonic()
    executable = repo_root / component.command[0]
    if not executable.is_file():
        return CheckResult(
            name=component.name,
            state=ResultState.ERROR,
            summary="required component missing",
            evidence={"path": component.command[0], "duration_ms": 0},
        )
    command = (
        [sys.executable, str(executable), *component.command[1:]]
        if executable.suffix == ".py"
        else [str(executable), *component.command[1:]]
    )
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name=component.name,
            state=ResultState.TIMEOUT,
            summary="component exceeded deadline",
            evidence={"duration_ms": int((time.monotonic() - started) * 1000)},
        )
    evidence = {
        "returncode": completed.returncode,
        "stdout_sha256": sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    passed = completed.returncode == 0
    return CheckResult(
        name=component.name,
        state=ResultState.PASS if passed else ResultState.FAIL,
        summary="component passed" if passed else "component failed",
        evidence=evidence,
    )


def run_composite_gate(
    *,
    repo_root: Path,
    artifact_root: Path,
    baseline_root: Path,
    required: tuple[str, ...],
    include_expensive: tuple[str, ...],
    timeout_seconds: float,
    component_override: Mapping[str, GateComponent] | None = None,
) -> CompositeGateResult:
    snapshot = build_snapshot(repo_root)
    components = {
        component.name: component for component in COMPONENTS
    }
    components.update(component_override or {})
    required_names = set(required) or {
        component.name for component in components.values() if component.required
    }
    expensive_names = set(include_expensive)
    results: list[CheckResult] = []
    bitmask = 0

    for artifact_name in ("REVIEW_STATE.json", "INVARIANTS.json", "CONTRACTS.json"):
        path = artifact_root / artifact_name
        if not path.is_file():
            result = CheckResult(
                name="graph_source_hash",
                state=ResultState.ERROR,
                summary=f"{artifact_name} missing",
                evidence={"path": path.name, "duration_ms": 0},
            )
            results.append(result)
            bitmask |= 1
            break
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["source_sha"] != snapshot.source_sha:
            result = CheckResult(
                name="graph_source_hash",
                state=ResultState.FAIL,
                summary=f"{artifact_name} source SHA mismatch",
                evidence={
                    "artifact": artifact_name,
                    "artifact_source_sha": payload["source_sha"],
                    "tracked_source_sha": snapshot.source_sha,
                },
            )
            results.append(result)
            bitmask |= 1
            break

    for name, component in sorted(components.items(), key=lambda item: item[1].bit):
        selected = not component.expensive or name in expensive_names
        is_required = name in required_names
        effective = GateComponent(
            component.name,
            component.bit,
            is_required,
            component.expensive,
            component.command,
        )
        if component.expensive and not selected:
            results.append(CheckResult(
                name=name,
                state=ResultState.ADVISORY,
                summary="expensive component not selected",
                evidence={"duration_ms": 0},
            ))
            continue
        result = run_component(
            effective,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
        results.append(result)
        if is_required and result.state is not ResultState.PASS:
            bitmask |= component.bit
    return CompositeGateResult(
        status="fail" if bitmask else "pass",
        bitmask=bitmask,
        source_sha=snapshot.source_sha,
        results=tuple(results),
    )
```

- [ ] **Step 6: Preserve compatibility while adding the new CLI contract**

`tools/bd-audit-gate.py` imports `run_composite_gate` from `tools.code_intelligence.audit_gate`. Its `main()` preserves the existing `--root`, `--artifacts`, and `--corpus` forms and must accept the audit/knowledge workflow's exact `--required all --json-out PATH` form. Aliases map as follows:

```python
repo_root = args.repo or args.root or discover_repo_root()
artifact_root = args.artifacts or repo_root / "project-knowledge"
baseline_root = args.baseline or artifact_root / "baselines"
required = tuple(component.name for component in COMPONENTS if component.required) \
    if args.required == "all" else tuple(args.require)
```

When `--json` is supplied, print exactly one sorted JSON object. When `--json-out PATH` is supplied, write that same object with `atomic_write_json(PATH, payload, validate_envelope)`; using both prints and writes identical content:

```json
{
  "bitmask": 0,
  "results": [],
  "source_sha": "64-hex",
  "status": "pass"
}
```

Human output prints one line per component and the final bitmask. Do not print raw stdout/stderr; only hashes and return codes are evidence.

- [ ] **Step 7: Add a required-component integration fixture and run the composite tests**

The integration fixture creates executable scripts for all components that emit `{"status":"pass"}` and exit `0`, plus source-bound `REVIEW_STATE.json`, `INVARIANTS.json`, and `CONTRACTS.json`. One parametrized test replaces each required script with an exit-1 script and asserts its exact bit:

```python
@pytest.mark.parametrize(
    ("name", "bit"),
    [
        ("graph_source_hash", 1),
        ("defect_patterns", 2),
        ("semantic_diff", 4),
        ("invariant_schema", 8),
        ("invariant_probes", 16),
        ("contracts", 32),
        ("review_staleness", 64),
        ("reachability", 128),
        ("coverage_policy", 1024),
        ("witnesses", 2048),
        ("constraint_topology", 4096),
        ("consumer_agreement", 8192),
    ],
)
def test_each_required_component_has_exact_failure_bit(
    tmp_path: Path,
    name: str,
    bit: int,
) -> None:
    fixture = write_composite_fixture(tmp_path)
    overrides = passing_component_overrides(fixture[0])
    overrides[name] = failing_component(name, bit, fixture[0])
    result = run_composite_gate(
        repo_root=fixture[0],
        artifact_root=fixture[1],
        baseline_root=fixture[2],
        required=tuple(
            item for item, _ in (
                ("graph_source_hash", 1),
                ("defect_patterns", 2),
                ("semantic_diff", 4),
                ("invariant_schema", 8),
                ("invariant_probes", 16),
                ("contracts", 32),
                ("review_staleness", 64),
                ("reachability", 128),
                ("coverage_policy", 1024),
                ("witnesses", 2048),
                ("constraint_topology", 4096),
                ("consumer_agreement", 8192),
            )
        ),
        include_expensive=(),
        timeout_seconds=2.0,
        component_override=overrides,
    )
    assert result.bitmask == bit
```

Run:

```bash
python -m pytest tests/test_bd_audit_gate_composite.py -q
```

Expected: all composite tests pass; every required component has a unique tested bit.

- [ ] **Step 8: Copy the verified canonical frontend and run the full governance band**

After `tools/bd-audit-gate.py` is green, copy it byte-for-byte to:

- `project-knowledge/bd-audit-gate.py`
- `toolchain/bin/bd-audit-gate.py`

Then run:

```bash
sha256sum \
  tools/bd-audit-gate.py \
  project-knowledge/bd-audit-gate.py \
  toolchain/bin/bd-audit-gate.py
python -m pytest \
  tests/test_code_intelligence_review_state.py \
  tests/test_code_intelligence_registries.py \
  tests/test_bd_finding.py \
  tests/test_bd_invariant.py \
  tests/test_invariant_probe.py \
  tests/test_contract_harness.py \
  tests/test_bd_review_next.py \
  tests/test_bd_audit_gate_composite.py \
  tests/test_audit_promotion_wirings_533.py \
  tests/test_v3_66_799_audit_tool_selftests.py -q
```

Expected: all three SHA-256 values match; all selected tests pass with zero skips.

- [ ] **Step 9: Run the real standalone gates before the composite gate**

Run from the tracked repository root:

```bash
python tools/invariants.py --check \
  --registry project-knowledge/INVARIANTS.json \
  --repo .
python tools/invariant_probe.py \
  --registry project-knowledge/INVARIANTS.json \
  --repo . --gate --json
python tools/contract_harness.py \
  --contracts project-knowledge/CONTRACTS.json \
  --repo . --gate --json
python tools/staleness.py stale \
  --state project-knowledge/REVIEW_STATE.json \
  --reaudit project-knowledge/REAUDIT.txt \
  --repo . --gate --json
python tools/bd-audit-gate.py \
  --root . \
  --artifacts project-knowledge \
  --required all \
  --json-out project-knowledge/AUDIT_GATE_RESULT.json
```

Expected: each standalone command emits one machine-readable result; the composite reports `status:"pass"` and `bitmask:0`. If a required artifact or command is absent, expected result is fail, and execution stops for repair rather than waiving the component.

- [ ] **Step 10: Update implemented status and exit-code documentation**

Update both code-intelligence docs with:

- the exact 14-component bit table;
- which two components are expensive and opt-in;
- the rule that unselected expensive work is `advisory`, never `pass`;
- source-SHA equality across graph/review/invariant/contract artifacts;
- the no-raw-output evidence policy;
- all CLI exit codes; and
- the acceptance sequence: standalone green first, composite green second.

- [ ] **Step 11: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check
python -m compileall -q tools/code_intelligence tools
python -m pytest \
  tests/test_code_intelligence_review_state.py \
  tests/test_code_intelligence_registries.py \
  tests/test_bd_finding.py \
  tests/test_bd_invariant.py \
  tests/test_invariant_probe.py \
  tests/test_contract_harness.py \
  tests/test_bd_review_next.py \
  tests/test_bd_audit_gate_composite.py -q
git diff --stat
git status --short
git log -1 --oneline
```

Expected: `git diff --check` and compileall are silent; all governance tests pass with zero skips; `git status --short` shows the complete uncommitted worktree; `git log -1` is unchanged from the pre-implementation baseline. Do not commit, merge, push, cut a release, or advance an external KB pin.

---

## Cross-Plan Acceptance Check

After Tasks 1-8 and both prerequisite plans are complete:

- [ ] `REVIEW_STATE.json`, `INVARIANTS.json`, and `CONTRACTS.json` validate, preserve every pre-existing record, and bind to the exact tracked source SHA.
- [ ] Direct byte drift in a reviewed file atomically marks it stale, releases its claim, writes `REAUDIT.txt`, and fails gate mode.
- [ ] `bd-finding` is dry-run by default and cannot overwrite a proposal or state without an exact expected SHA.
- [ ] `bd-invariant` refuses probable findings, missing tests, non-failing RED evidence, source-SHA mismatches, and non-allowlisted probes.
- [ ] `invariant_probe.py` exposes no `eval`, `exec`, shell interpolation, arbitrary import string, raw response body, authorization value, cookie, or signed query.
- [ ] The runtime contract harness proves precondition short-circuiting, postcondition checking, allowed raises, side-effect observation, and cleanup.
- [ ] `bd-review-next` cannot duplicate a live claim, recovers expired claims, and invalidates claims on source-SHA change.
- [ ] Every composite component has a unique tested bit; required absence, unknown, timeout, error, or fail cannot yield a composite pass.
- [ ] Optional expensive analysis runs only when selected and is reported as advisory when omitted.
- [ ] Existing specialized invariant, body-contract, API-contract, witness, topology, and consumer-agreement behavior remains covered.
- [ ] The worktree remains uncommitted and unmerged for integrated user review.
