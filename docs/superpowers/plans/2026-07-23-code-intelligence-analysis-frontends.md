# Code-Intelligence Analysis Frontends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the five standalone code-intelligence analysis frontends, their typed adapters, deterministic fixtures, tests, and operator documentation without changing production service behavior or wiring the composite audit gate.

**Architecture:** The frontends are thin CLIs over `tools/code_intelligence/` services and the existing graph, coverage, reachability, oracle, and focused-fuzzer tools. Every service returns a normalized result and one source-bound artifact envelope; dynamic application imports occur only inside bounded adapter calls. Existing specialist behavior remains authoritative and is wrapped rather than duplicated.

**Tech Stack:** Python 3 standard library (`argparse`, `ast`, `dataclasses`, `hashlib`, `json`, `multiprocessing`, `pathlib`, `sqlite3`, `subprocess`, `tempfile`, `unittest.mock`), pytest, existing BulkDownloader audit tools; optional `libcst`, `hypothesis`, and `radon` adapters only when explicitly selected.

## Global Constraints

1. Python standard library is the required runtime baseline. Optional packages such as `libcst`, `hypothesis`, `radon`, `bandit`, and `vulture` may enhance isolated audit runs but cannot be required by normal release gates.
2. Every durable artifact carries:
   - schema name and version;
   - tracked-tree source SHA;
   - tool version;
   - deterministic input hashes; and
   - generation timestamp separated from content used for deterministic comparisons.
3. Durable writes are validate-then-atomically-replace. A failed run must not leave a plausible partial artifact.
4. All paths are explicitly supplied or derived from a discovered repository root. `/home/claude`, `/root`, and workstation-specific paths are not defaults in canonical interfaces.
5. Outputs exclude secret values, credentials, cookies, authorization headers, signed queries, and raw captured bodies.
6. Advisory findings and release-blocking failures are distinct result states.
7. Existing CLI behavior remains available through compatibility wrappers or adapters.
8. New behavior follows RED -> GREEN -> refactor. Each test must be observed failing for the intended missing behavior before implementation.

**Additional scope constraint:** Do not modify `tools/bd-audit-gate.py`, `project-knowledge/bd-audit-gate.py`, or `toolchain/bin/bd-audit-gate.py` in this plan. Composite-gate wiring is a separate, later plan after all five standalone tools are independently green.

**Commit constraint:** Do not run `git commit`, merge, push, release, or advance an external static-KB pin. Every task ends with a **Pre-commit checkpoint (do not commit)**.

---

## Shared-core prerequisite contract

This plan consumes the exact foundation interfaces from
`docs/superpowers/plans/2026-07-23-code-intelligence-foundation-graph.md`.
The analysis implementation must not introduce a second artifact, path, result,
schema, or snapshot implementation:

```python
# tools/code_intelligence/snapshot.py
@dataclass(frozen=True)
class FileFact:
    path: str
    sha256: str
    size: int
    lines: int

@dataclass(frozen=True)
class TreeSnapshot:
    source_sha: str
    files: tuple[FileFact, ...]

def build_snapshot(
    root: Path,
    include: Callable[[str], bool] | None = None,
) -> TreeSnapshot: ...

# tools/code_intelligence/artifacts.py
def canonical_bytes(
    value: object,
    *,
    omit_keys: frozenset[str] = frozenset({"generated_at"}),
) -> bytes: ...

def atomic_write_json(
    path: Path,
    value: object,
    validator: Callable[[object], None],
) -> None: ...

def artifact_hash(value: object) -> str: ...

# tools/code_intelligence/results.py
class ResultState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ADVISORY = "advisory"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"

@dataclass(frozen=True)
class CheckResult:
    name: str
    state: ResultState
    summary: str
    evidence: Mapping[str, object]

def exit_code(results: Iterable[CheckResult], gate: bool) -> int: ...

# tools/code_intelligence/schemas.py
def make_envelope(
    schema_name: str,
    schema_version: int,
    source_sha: str,
    tool_version: str,
    input_hashes: Mapping[str, str],
) -> dict[str, object]: ...

def validate_envelope(
    value: object,
    expected_name: str | None = None,
    supported_version: int = 1,
) -> None: ...

def validate_projection(name: str, value: object) -> None: ...
```

`artifact_hash()` and `canonical_bytes()` exclude only `generated_at` by default.
Stored artifacts retain the timestamp. Analysis-local file-byte hashes use the
`sha256_path(path: Path) -> str` helper defined in Task 2; they are not added to
the foundation API.

Every durable frontend follows this exact composition:

```python
snapshot = build_snapshot(repo_root)
payload = {
    **make_envelope(
        SCHEMA_NAME, SCHEMA_VERSION, snapshot.source_sha, TOOL_VERSION,
        input_hashes,
    ),
    **content,
}
atomic_write_json(
    output_path,
    payload,
    lambda value: validate_envelope(value, SCHEMA_NAME, SCHEMA_VERSION),
)
```

`--check` compares `artifact_hash(existing)` with `artifact_hash(payload)`.
Every CLI returns `exit_code([check_result], gate=args.gate)`. When a frontend
loads a graph projection, it first calls `validate_projection()` with the
foundation name: `call_graph`, `security_surface`, `module_catalog`,
`taint_map`, or another name defined by the foundation plan.

## File map

| Path | Responsibility |
|---|---|
| `tools/code_intelligence/adapters.py` | Typed adapter protocol, budget/context/case records, registry, duplicate-name guard |
| `tools/code_intelligence/coverage_service.py` | Coverage ingestion, function/module aggregation, risk join, unknown handling, artifact content |
| `tools/risk_score.py` | Preserve the existing CLI and expose graph/radon-report scoring as a pure reusable function |
| `tools/code_intelligence/semantic_service.py` | AST snapshot comparison and policy classification |
| `tools/code_intelligence/reachability_service.py` | Evidence-preserving route classification without conflating auth, operator wiring, or navigation |
| `tools/code_intelligence/oracle_service.py` | Bounded differential adapter execution, normalization, allowed-divergence policy |
| `tools/code_intelligence/oracle_adapters.py` | Adapters for consumer agreement, schema, rollback, template/plugin, and URL truth checks |
| `tools/code_intelligence/fuzz_service.py` | Deterministic corpus replay, subprocess/process timeout, crash normalization, reproducer output |
| `tools/code_intelligence/fuzz_adapters.py` | Adapters for redaction, URL guard, path guard, import parser, plugin fuzzer |
| `tools/coverage_map.py` | Canonical `bd-coverage-map` CLI and compatibility entry points around the existing behavior |
| `toolchain/bin/bd-coverage-map` | Stable no-extension compatibility launcher for `tools/coverage_map.py` |
| `tools/semantic_diff.py` | Standalone semantic-diff CLI |
| `tools/reachability.py` | Standalone privilege-boundary reachability CLI |
| `tools/differential_oracle.py` | Standalone differential-oracle CLI |
| `tools/fuzz_harness.py` | Standalone fuzz/replay CLI; no work at import time |
| `tests/fixtures/code_intelligence/` | Small source-bound JSON/tree fixtures for all five tools |
| `tests/test_code_intelligence_adapters.py` | Registry, context, duplicate-name, and import-safety tests |
| `tests/test_coverage_map_frontend.py` | Coverage generation, unknown, hash, deterministic, check-mode, and compatibility tests |
| `tests/test_semantic_diff_frontend.py` | Signature/default/annotation/raises/return/decorator/auth/call/surface policy tests |
| `tests/test_reachability_frontend.py` | Public/authenticated/internal/unreachable/unknown evidence and gate-mode tests |
| `tests/test_differential_oracle_frontend.py` | Agreement, divergence, allowed divergence, timeout, malformed-adapter tests |
| `tests/test_fuzz_harness_frontend.py` | Seed, replay, timeout, crash, reproducer, secret-safety, and import-safety tests |
| `tests/test_code_intelligence_frontend_docs.py` | Help, exit-code documentation, and no-composite-gate-wiring tests |
| `docs/code-intelligence/ANALYSIS_FRONTENDS.md` | Standalone commands, inputs, outputs, exit codes, optional adapters, examples |

### Task 1: Typed Analysis Adapter Registry and Deterministic Fixtures

**Files:**
- Modify: `tools/code_intelligence/adapters.py`
- Create: `tests/test_code_intelligence_adapters.py`
- Create: `tests/fixtures/code_intelligence/coverage/coverage.json`
- Create: `tests/fixtures/code_intelligence/coverage/radon.json`
- Create: `tests/fixtures/code_intelligence/coverage/test_catalog.json`
- Create: `tests/fixtures/code_intelligence/semantic/before/sample.py`
- Create: `tests/fixtures/code_intelligence/semantic/after/sample.py`
- Create: `tests/fixtures/code_intelligence/oracle/cases.json`
- Create: `tests/fixtures/code_intelligence/fuzz/corpus.json`

**Interfaces:**
- Consumes: `CheckResult` and `ResultState` from `tools.code_intelligence.results`.
- Produces:

```python
JsonValue = bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]

@dataclass(frozen=True)
class AdapterBudget:
    timeout_seconds: float
    max_cases: int
    max_output_bytes: int

@dataclass(frozen=True)
class AdapterContext:
    repo_root: Path
    artifacts_dir: Path
    corpus_dir: Path
    seed: int
    budget: AdapterBudget

@dataclass(frozen=True)
class AdapterCase:
    case_id: str
    payload: JsonValue

class AnalysisAdapter(Protocol):
    name: str
    kind: Literal["oracle", "fuzz", "coverage", "reachability"]
    def cases(self, context: AdapterContext) -> Sequence[AdapterCase]: ...
    def run(self, case: AdapterCase, context: AdapterContext) -> CheckResult: ...

def register_adapter(adapter: AnalysisAdapter) -> None: ...
def get_adapter(name: str) -> AnalysisAdapter: ...
def list_adapters(*, kind: str | None = None) -> tuple[str, ...]: ...
def clear_adapters_for_test() -> None: ...
```

- [ ] **Step 1: Write the failing registry tests**

```python
# tests/test_code_intelligence_adapters.py
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.code_intelligence.adapters import (
    AdapterBudget, AdapterCase, AdapterContext, clear_adapters_for_test,
    get_adapter, list_adapters, register_adapter,
)
from tools.code_intelligence.results import CheckResult, ResultState


@dataclass(frozen=True)
class _Adapter:
    name: str = "fixture-oracle"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("same", {"left": 1, "right": 1}),)

    def run(self, case, context):
        return CheckResult(self.name, ResultState.PASS, case.case_id, {"equal": True})


def setup_function():
    clear_adapters_for_test()


def test_registry_is_sorted_and_kind_filtered(tmp_path):
    register_adapter(_Adapter())
    assert list_adapters() == ("fixture-oracle",)
    assert list_adapters(kind="oracle") == ("fixture-oracle",)
    assert list_adapters(kind="fuzz") == ()
    ctx = AdapterContext(
        tmp_path, tmp_path / "artifacts", tmp_path / "corpus", 7,
        AdapterBudget(1.0, 5, 4096),
    )
    assert get_adapter("fixture-oracle").cases(ctx)[0].case_id == "same"


def test_duplicate_adapter_name_is_rejected():
    register_adapter(_Adapter())
    with pytest.raises(ValueError, match="duplicate adapter: fixture-oracle"):
        register_adapter(_Adapter())


def test_invalid_budget_is_rejected():
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        AdapterBudget(0.0, 1, 1024)


def test_unknown_adapter_is_explicit():
    with pytest.raises(KeyError, match="unknown adapter: missing"):
        get_adapter("missing")
```

- [ ] **Step 2: Run the tests and observe the intended RED**

Run:

```bash
python -m pytest tests/test_code_intelligence_adapters.py -q
```

Expected: collection fails with `ImportError` for `AdapterBudget`, `AdapterCase`, or the registry functions because the typed registry does not exist yet.

- [ ] **Step 3: Implement the minimal registry**

```python
# tools/code_intelligence/adapters.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence, TypeAlias

from .results import CheckResult

JsonValue: TypeAlias = (
    bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True)
class AdapterBudget:
    timeout_seconds: float
    max_cases: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_cases <= 0:
            raise ValueError("max_cases must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


@dataclass(frozen=True)
class AdapterContext:
    repo_root: Path
    artifacts_dir: Path
    corpus_dir: Path
    seed: int
    budget: AdapterBudget


@dataclass(frozen=True)
class AdapterCase:
    case_id: str
    payload: JsonValue


class AnalysisAdapter(Protocol):
    name: str
    kind: Literal["oracle", "fuzz", "coverage", "reachability"]

    def cases(self, context: AdapterContext) -> Sequence[AdapterCase]: ...
    def run(self, case: AdapterCase, context: AdapterContext) -> CheckResult: ...


_REGISTRY: dict[str, AnalysisAdapter] = {}


def register_adapter(adapter: AnalysisAdapter) -> None:
    if adapter.name in _REGISTRY:
        raise ValueError(f"duplicate adapter: {adapter.name}")
    _REGISTRY[adapter.name] = adapter


def get_adapter(name: str) -> AnalysisAdapter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter: {name}") from None


def list_adapters(*, kind: str | None = None) -> tuple[str, ...]:
    return tuple(sorted(
        name for name, adapter in _REGISTRY.items()
        if kind is None or adapter.kind == kind
    ))


def clear_adapters_for_test() -> None:
    _REGISTRY.clear()
```

- [ ] **Step 4: Add deterministic, secret-free fixture content**

`tests/fixtures/code_intelligence/coverage/coverage.json`:

```json
{
  "meta": {"version": "7.6.0"},
  "files": {
    "bulk_downloader/sample.py": {
      "executed_lines": [1, 2, 6],
      "missing_lines": [3, 7, 8],
      "summary": {"covered_lines": 3, "num_statements": 6, "percent_covered": 50.0}
    }
  }
}
```

`tests/fixtures/code_intelligence/coverage/radon.json`:

```json
{"bulk_downloader/sample.py": [{"name": "parse", "lineno": 1, "complexity": 4}]}
```

`tests/fixtures/code_intelligence/coverage/test_catalog.json`:

```json
{"mapped": {"sample.py": ["test_sample.py"]}, "gap_candidates": []}
```

```python
# tests/fixtures/code_intelligence/semantic/before/sample.py
def fetch(value: int = 1) -> int:
    return value
```

```python
# tests/fixtures/code_intelligence/semantic/after/sample.py
@require_login
def fetch(value: str, *, strict: bool = True) -> str:
    if strict:
        raise ValueError("invalid")
    emit_metric("fetch")
    return value
```

`tests/fixtures/code_intelligence/oracle/cases.json`:

```json
{"cases": [{"id": "equal", "left": {"value": 1}, "right": {"value": 1}}, {"id": "different", "left": 1, "right": 2}]}
```

`tests/fixtures/code_intelligence/fuzz/corpus.json`:

```json
{"schema": 1, "cases": [{"id": "empty-object", "payload": {}}, {"id": "nested-list", "payload": {"value": [1, [2, [3]]]}}]}
```

- [ ] **Step 5: Run registry tests and fixture parse checks**

Run:

```bash
python -m pytest tests/test_code_intelligence_adapters.py -q
python -c "import json,pathlib; [json.loads(p.read_text()) for p in pathlib.Path('tests/fixtures/code_intelligence').rglob('*.json')]; print('fixture JSON: PASS')"
```

Expected: `4 passed` and `fixture JSON: PASS`.

- [ ] **Step 6: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- tools/code_intelligence/adapters.py tests/test_code_intelligence_adapters.py tests/fixtures/code_intelligence
git diff --stat -- tools/code_intelligence/adapters.py tests/test_code_intelligence_adapters.py tests/fixtures/code_intelligence
python -m pytest tests/test_code_intelligence_adapters.py -q
```

Expected: no whitespace errors, only Task 1 paths in the scoped diff, and `4 passed`. Do not run `git add` or `git commit`.

### Task 2: `bd-coverage-map` Stable Frontend

**Files:**
- Create: `tools/code_intelligence/coverage_service.py`
- Modify: `tools/coverage_map.py`
- Modify: `tools/risk_score.py`
- Create: `toolchain/bin/bd-coverage-map`
- Create: `tests/test_coverage_map_frontend.py`

**Interfaces:**
- Consumes:
  - `build_snapshot(root: Path, include=None) -> TreeSnapshot`
  - `artifact_hash(value: object) -> str`
  - `make_envelope(...) -> dict[str, object]`
  - `validate_envelope(value, expected_name, supported_version) -> None`
  - function nodes from `KNOWLEDGE_GRAPH.db` with columns `path`, `qualname`, `span`, `lines`, `meta_json`
  - optional radon JSON and test-catalog JSON
- Produces from `tools/risk_score.py`:

```python
def score_from_reports(
    *,
    graph_path: Path,
    radon_report: Mapping[str, JsonValue] | None,
) -> dict[str, dict[str, JsonValue]]: ...
```

- Produces:

```python
def sha256_path(path: Path) -> str: ...

def build_coverage_content(
    *,
    coverage_path: Path | None,
    graph_path: Path,
    repo_root: Path,
    radon_path: Path | None = None,
    test_catalog_path: Path | None = None,
) -> tuple[CheckResult, dict[str, JsonValue]]: ...

def run_coverage_map(
    *,
    coverage_path: Path | None,
    graph_path: Path,
    repo_root: Path,
    output_path: Path,
    radon_path: Path | None,
    test_catalog_path: Path | None,
    check_path: Path | None,
    gate: bool,
) -> CheckResult: ...

def main(argv: Sequence[str] | None = None) -> int: ...
```

Artifact schema: `bd.coverage-gaps`, version `2`. Content keys are `status`, `functions`, `modules`, and `summary`. Missing coverage produces `status: "unknown"`, `functions: []`, and a non-blocking `unknown` result unless `--gate` is supplied.

- [ ] **Step 1: Write coverage frontend tests**

```python
# tests/test_coverage_map_frontend.py
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tools.code_intelligence.artifacts import artifact_hash
from tools.code_intelligence.coverage_service import run_coverage_map
from tools.code_intelligence.results import ResultState

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "code_intelligence" / "coverage"


def _graph(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE nodes(id TEXT, kind TEXT, path TEXT, qualname TEXT, span TEXT, sha256 TEXT, lines INTEGER, meta_json TEXT)")
    db.execute("CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, meta_json TEXT)")
    db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)", (
        "bulk_downloader/sample.py", "module", "bulk_downloader/sample.py",
        "bulk_downloader/sample.py", "", "fixture-sha", 8, "{}",
    ))
    db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)", (
        "sample::parse", "function", "bulk_downloader/sample.py", "parse",
        "1-4", "", 4, '{"sinks":[],"secrets":[]}',
    ))
    db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)", (
        "sample::emit", "function", "bulk_downloader/sample.py", "emit",
        "6-8", "", 3, '{"sinks":[{"kind":"fetch","at":7}],"secrets":[]}',
    ))
    db.commit()
    db.close()


def test_generates_function_and_module_coverage(tmp_path):
    graph = tmp_path / "graph.db"
    out = tmp_path / "COVERAGE_GAPS.json"
    _graph(graph)
    result = run_coverage_map(
        coverage_path=FIX / "coverage.json", graph_path=graph, repo_root=ROOT,
        output_path=out, radon_path=FIX / "radon.json",
        test_catalog_path=FIX / "test_catalog.json", check_path=None, gate=False,
    )
    payload = json.loads(out.read_text())
    assert result.state is ResultState.ADVISORY
    assert payload["schema_name"] == "bd.coverage-gaps"
    assert payload["summary"]["functions_with_gaps"] == 2
    assert payload["functions"][0]["path"] == "bulk_downloader/sample.py"
    assert payload["modules"][0]["risk"]["complexity_max"] == 4
    assert payload["modules"][0]["test_evidence"] == ["test_sample.py"]
    assert payload["input_hashes"]["coverage_json"]


def test_missing_coverage_is_unknown_not_zero(tmp_path):
    graph = tmp_path / "graph.db"
    out = tmp_path / "COVERAGE_GAPS.json"
    _graph(graph)
    result = run_coverage_map(
        coverage_path=None, graph_path=graph, repo_root=ROOT, output_path=out,
        radon_path=None, test_catalog_path=None, check_path=None, gate=False,
    )
    payload = json.loads(out.read_text())
    assert result.state is ResultState.UNKNOWN
    assert payload["status"] == "unknown"
    assert payload["functions"] == []


def test_check_ignores_generation_timestamp(tmp_path):
    graph = tmp_path / "graph.db"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _graph(graph)
    kwargs = dict(
        coverage_path=FIX / "coverage.json", graph_path=graph, repo_root=ROOT,
        radon_path=None, test_catalog_path=None, gate=False,
    )
    run_coverage_map(output_path=first, check_path=None, **kwargs)
    result = run_coverage_map(output_path=second, check_path=first, **kwargs)
    assert result.state in {ResultState.PASS, ResultState.ADVISORY}
    assert artifact_hash(json.loads(first.read_text())) == artifact_hash(json.loads(second.read_text()))


def test_cli_help_and_unknown_gate_exit(tmp_path):
    help_run = subprocess.run(
        [sys.executable, "tools/coverage_map.py", "--help"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert help_run.returncode == 0
    assert "--coverage" in help_run.stdout and "--check" in help_run.stdout
```

- [ ] **Step 2: Run the tests and observe RED**

Run:

```bash
python -m pytest tests/test_coverage_map_frontend.py -q
```

Expected: collection fails with `ModuleNotFoundError: tools.code_intelligence.coverage_service`.

- [ ] **Step 3: Extract reusable risk scoring without changing the existing CLI**

Add this pure entry point to `tools/risk_score.py`; retain `score()`, `rerank()`, `main()`, existing defaults, and existing output:

```python
def score_from_reports(*, graph_path, radon_report):
    sink_weight, secret_count, lines = load_facts(str(graph_path))
    complexity = {}
    for path, blocks in (radon_report or {}).items():
        complexity[path] = max(
            (int(block.get("complexity", 0)) for block in blocks if isinstance(block, dict)),
            default=0,
        )
    max_cc = max(complexity.values(), default=1)
    max_sink = max(sink_weight.values(), default=1)
    result = {}
    for path in sorted(lines):
        line_count = max(1, int(lines[path]))
        parts = {
            "complexity": norm(complexity.get(path, 0), 0, max_cc),
            "sink": norm(sink_weight.get(path, 0), 0, max_sink),
            "secret": norm(secret_count.get(path, 0) / line_count * 100, 0, 5),
            "taint_proxy": norm(sink_weight.get(path, 0) / line_count * 100, 0, 10),
            "prior_defect": prior(path),
        }
        value = round(
            WEIGHTS["cc"] * parts["complexity"]
            + WEIGHTS["sink"] * parts["sink"]
            + WEIGHTS["secret"] * parts["secret"]
            + WEIGHTS["taint"] * parts["taint_proxy"]
            + WEIGHTS["prior"] * parts["prior_defect"],
            4,
        )
        result[path] = {
            "score": value,
            "complexity_max": complexity.get(path, 0),
            "sink_weight": sink_weight.get(path, 0),
            "secret_count": secret_count.get(path, 0),
            "components": parts,
        }
    return result
```

- [ ] **Step 4: Implement coverage aggregation and unknown behavior**

```python
# tools/code_intelligence/coverage_service.py
from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path

from tools.risk_score import score_from_reports

from .artifacts import artifact_hash, atomic_write_json
from .results import CheckResult, ResultState
from .schemas import make_envelope, validate_envelope
from .snapshot import build_snapshot

SCHEMA = "bd.coverage-gaps"
SCHEMA_VERSION = 2
TOOL_VERSION = "1.0.0"


def sha256_path(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spans(graph_path: Path) -> list[tuple[str, str, int, int, dict]]:
    db = sqlite3.connect(graph_path)
    try:
        rows = db.execute(
            "SELECT path,qualname,span,meta_json FROM nodes WHERE kind='function' ORDER BY path,qualname"
        ).fetchall()
    finally:
        db.close()
    result = []
    for path, qualname, span, meta_json in rows:
        if not span or "-" not in span:
            continue
        start, end = (int(value) for value in span.split("-", 1))
        result.append((path, qualname, start, end, json.loads(meta_json or "{}")))
    return result


def build_coverage_content(*, coverage_path, graph_path, repo_root, radon_path=None, test_catalog_path=None):
    if coverage_path is None:
        return (
            CheckResult("bd-coverage-map", ResultState.UNKNOWN, "coverage input absent", {"coverage": "absent"}),
            {"status": "unknown", "functions": [], "modules": [], "summary": {"functions_with_gaps": 0}},
        )
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    radon_report = json.loads(radon_path.read_text(encoding="utf-8")) if radon_path else None
    test_catalog = json.loads(test_catalog_path.read_text(encoding="utf-8")) if test_catalog_path else {}
    risk = score_from_reports(graph_path=graph_path, radon_report=radon_report)
    functions = []
    for path, qualname, start, end, meta in _spans(graph_path):
        file_coverage = coverage.get("files", {}).get(path)
        if file_coverage is None:
            continue
        missing = sorted(set(file_coverage.get("missing_lines", [])) & set(range(start, end + 1)))
        if not missing:
            continue
        fraction = len(missing) / max(1, end - start + 1)
        functions.append({
            "path": path, "function": qualname, "span": f"{start}-{end}",
            "missing_lines": missing, "uncovered_fraction": round(fraction, 6),
            "classification": "wholly" if fraction > 0.85 else "partial",
            "sink_count": len(meta.get("sinks", [])),
        })
    functions.sort(key=lambda row: (-row["uncovered_fraction"], row["path"], row["function"]))
    by_module = {}
    for row in functions:
        module_name = Path(row["path"]).name
        rec = by_module.setdefault(row["path"], {
            "path": row["path"], "gap_count": 0, "max_uncovered_fraction": 0.0,
            "risk": risk.get(row["path"], {
                "score": 0.0, "complexity_max": 0, "sink_weight": 0,
                "secret_count": 0, "components": {},
            }),
            "test_evidence": sorted(test_catalog.get("mapped", {}).get(module_name, [])),
        })
        rec["gap_count"] += 1
        rec["max_uncovered_fraction"] = max(rec["max_uncovered_fraction"], row["uncovered_fraction"])
    content = {
        "status": "measured",
        "functions": functions,
        "modules": [by_module[key] for key in sorted(by_module)],
        "summary": {"functions_with_gaps": len(functions), "modules_with_gaps": len(by_module)},
    }
    state = ResultState.ADVISORY if functions else ResultState.PASS
    return CheckResult("bd-coverage-map", state, f"{len(functions)} function coverage gaps", content["summary"]), content


def run_coverage_map(*, coverage_path, graph_path, repo_root, output_path, radon_path, test_catalog_path, check_path, gate):
    snapshot = build_snapshot(repo_root)
    result, content = build_coverage_content(
        coverage_path=coverage_path, graph_path=graph_path, repo_root=repo_root,
        radon_path=radon_path, test_catalog_path=test_catalog_path,
    )
    hashes = {"knowledge_graph": sha256_path(graph_path)}
    if coverage_path is not None:
        hashes["coverage_json"] = sha256_path(coverage_path)
    if radon_path is not None:
        hashes["radon_json"] = sha256_path(radon_path)
    if test_catalog_path is not None:
        hashes["test_catalog_json"] = sha256_path(test_catalog_path)
    payload = {
        **make_envelope(SCHEMA, SCHEMA_VERSION, snapshot.source_sha, TOOL_VERSION, hashes),
        **content,
    }
    validator = lambda value: validate_envelope(value, SCHEMA, SCHEMA_VERSION)
    atomic_write_json(output_path, payload, validator)
    if check_path is not None:
        prior = json.loads(check_path.read_text(encoding="utf-8"))
        if artifact_hash(prior) != artifact_hash(payload):
            return CheckResult("bd-coverage-map", ResultState.FAIL, "coverage artifact drift", {"check": str(check_path)})
    if result.state is ResultState.UNKNOWN and gate:
        return CheckResult("bd-coverage-map", ResultState.FAIL, "coverage required in gate mode", {"coverage": "absent"})
    return result
```

- [ ] **Step 5: Replace hard-coded CLI paths and add the stable launcher**

```python
# tools/coverage_map.py - retain import-compatible run(), add canonical main()
def main(argv=None):
    parser = argparse.ArgumentParser(prog="bd-coverage-map")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--radon-json", type=Path)
    parser.add_argument("--test-catalog", type=Path)
    parser.add_argument("--out", type=Path, default=Path("COVERAGE_GAPS.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root or discover_repo_root(Path.cwd())
    result = run_coverage_map(
        coverage_path=args.coverage, graph_path=args.graph, repo_root=root,
        output_path=args.out, radon_path=args.radon_json,
        test_catalog_path=args.test_catalog, check_path=args.check, gate=args.gate,
    )
    print(json.dumps(asdict(result), sort_keys=True) if args.json else f"{result.state.upper()}: {result.summary}")
    return exit_code([result], gate=args.gate)
```

```python
#!/usr/bin/env python3
# toolchain/bin/bd-coverage-map
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
runpy.run_path(str(root / "tools" / "coverage_map.py"), run_name="__main__")
```

- [ ] **Step 6: Run focused and compatibility tests**

Run:

```bash
python -m pytest tests/test_coverage_map_frontend.py tests/test_audit_promotion_wirings_533.py tests/test_graph_source_hash_release_gate.py -q
python tools/coverage_map.py --help
python toolchain/bin/bd-coverage-map --help
```

Expected: focused tests pass; existing graph tests stay green; both help commands exit `0` and show `--coverage`, `--graph`, `--out`, `--check`, and `--gate`.

- [ ] **Step 7: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- tools/code_intelligence/coverage_service.py tools/coverage_map.py tools/risk_score.py toolchain/bin/bd-coverage-map tests/test_coverage_map_frontend.py
git diff --stat -- tools/code_intelligence/coverage_service.py tools/coverage_map.py tools/risk_score.py toolchain/bin/bd-coverage-map tests/test_coverage_map_frontend.py
python -m pytest tests/test_coverage_map_frontend.py tests/test_audit_promotion_wirings_533.py tests/test_graph_source_hash_release_gate.py -q
```

Expected: no whitespace errors, only Task 2 paths in the scoped diff, and all selected tests pass. Do not run `git add` or `git commit`.

### Task 3: `semantic_diff.py` AST Frontend

**Files:**
- Create: `tools/code_intelligence/semantic_service.py`
- Create: `tools/semantic_diff.py`
- Create: `tests/test_semantic_diff_frontend.py`

**Interfaces:**
- Consumes deterministic tracked trees or JSON L0 snapshots.
- Produces:

```python
PolicyClass = Literal["breaking", "risky", "informational", "unknown"]

@dataclass(frozen=True)
class FunctionSemantics:
    path: str
    qualname: str
    positional_only: tuple[str, ...]
    positional: tuple[str, ...]
    keyword_only: tuple[str, ...]
    vararg: str | None
    kwargs: str | None
    defaults: tuple[tuple[str, str], ...]
    annotations: tuple[tuple[str, str], ...]
    return_annotation: str | None
    return_shapes: tuple[str, ...]
    raises: tuple[str, ...]
    decorators: tuple[str, ...]
    auth_gates: tuple[str, ...]
    calls_resolved: tuple[str, ...]
    calls_unresolved: tuple[str, ...]
    config_ops: tuple[str, ...]
    concurrency_ops: tuple[str, ...]
    metric_ops: tuple[str, ...]
    sinks: tuple[str, ...]

def snapshot_tree(repo_root: Path) -> dict[str, FunctionSemantics]: ...
def compare_semantics(before: Mapping[str, FunctionSemantics], after: Mapping[str, FunctionSemantics]) -> list[dict[str, JsonValue]]: ...
def classify_change(field: str, before: JsonValue, after: JsonValue) -> PolicyClass: ...
def run_semantic_diff(
    *,
    before_tree: Path | None,
    after_tree: Path | None,
    before_snapshot: Path | None,
    after_snapshot: Path | None,
    output_path: Path,
    check_path: Path | None,
    gate: bool,
    cst_adapter: Literal["none", "libcst"],
) -> CheckResult: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Artifact schema: `bd.semantic-diff`, version `1`. `ast` is mandatory; `--cst-adapter libcst` may add positions but cannot alter the standard-library verdict.

- [ ] **Step 1: Write semantic policy tests**

```python
# tests/test_semantic_diff_frontend.py
import json
import subprocess
import sys
from pathlib import Path

from tools.code_intelligence.semantic_service import (
    classify_change, compare_semantics, snapshot_tree,
)

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "code_intelligence" / "semantic"


def test_snapshot_retains_full_signature_and_surfaces():
    functions = snapshot_tree(FIX / "after")
    fetch = functions["sample.py::fetch"]
    assert fetch.positional == ("value",)
    assert fetch.keyword_only == ("strict",)
    assert fetch.defaults == (("strict", "True"),)
    assert fetch.return_annotation == "str"
    assert fetch.raises == ("ValueError",)
    assert "require_login" in fetch.auth_gates
    assert "emit_metric" in fetch.metric_ops


def test_policy_distinguishes_breaking_risky_and_information():
    assert classify_change("positional", ["value"], ["value", "required"]) == "breaking"
    assert classify_change("auth_gates", [], ["require_login"]) == "risky"
    assert classify_change("metric_ops", [], ["emit_metric"]) == "informational"
    assert classify_change("calls_unresolved", ["x"], ["y"]) == "unknown"


def test_compare_reports_each_changed_contract_surface():
    changes = compare_semantics(snapshot_tree(FIX / "before"), snapshot_tree(FIX / "after"))
    fields = {row["field"] for row in changes}
    assert {"keyword_only", "defaults", "annotations", "return_annotation", "raises", "decorators", "auth_gates", "metric_ops"} <= fields
    assert any(row["policy"] == "breaking" for row in changes)


def test_cli_requires_one_before_and_one_after_source():
    run = subprocess.run(
        [sys.executable, "tools/semantic_diff.py", "--before-tree", str(FIX / "before")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert run.returncode == 2
    assert "one after source is required" in run.stderr
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/test_semantic_diff_frontend.py -q
```

Expected: collection fails with `ModuleNotFoundError: tools.code_intelligence.semantic_service`.

- [ ] **Step 3: Implement AST normalization and policy**

```python
# tools/code_intelligence/semantic_service.py
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Mapping

PolicyClass = Literal["breaking", "risky", "informational", "unknown"]
AUTH_NAMES = ("auth", "login", "csrf", "token", "admin", "permission", "require")
CONFIG_NAMES = ("config", "getenv", "settings")
CONCURRENCY_NAMES = ("lock", "thread", "process", "queue", "asyncio", "scheduler")
METRIC_NAMES = ("metric", "counter", "histogram", "gauge", "observe")
SINK_NAMES = ("execute", "popen", "run", "open", "send_file", "fetch", "request")


@dataclass(frozen=True)
class FunctionSemantics:
    path: str
    qualname: str
    positional_only: tuple[str, ...]
    positional: tuple[str, ...]
    keyword_only: tuple[str, ...]
    vararg: str | None
    kwargs: str | None
    defaults: tuple[tuple[str, str], ...]
    annotations: tuple[tuple[str, str], ...]
    return_annotation: str | None
    return_shapes: tuple[str, ...]
    raises: tuple[str, ...]
    decorators: tuple[str, ...]
    auth_gates: tuple[str, ...]
    calls_resolved: tuple[str, ...]
    calls_unresolved: tuple[str, ...]
    config_ops: tuple[str, ...]
    concurrency_ops: tuple[str, ...]
    metric_ops: tuple[str, ...]
    sinks: tuple[str, ...]


def _text(node):
    return ast.unparse(node) if node is not None else None


def _call_name(node):
    return ast.unparse(node.func) if isinstance(node, ast.Call) else ""


def _function(path, node):
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    default_pairs = [(arg.arg, ast.unparse(value)) for arg, value in zip(positional, defaults) if value is not None]
    default_pairs.extend(
        (arg.arg, ast.unparse(value))
        for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
        if value is not None
    )
    calls = sorted({_call_name(item) for item in ast.walk(node) if isinstance(item, ast.Call)})
    raises = sorted({
        ast.unparse(item.exc.func if isinstance(item.exc, ast.Call) else item.exc)
        for item in ast.walk(node) if isinstance(item, ast.Raise) and item.exc is not None
    })
    returns = sorted({
        type(item.value).__name__ if item.value is not None else "None"
        for item in ast.walk(node) if isinstance(item, ast.Return)
    })
    decorators = tuple(sorted(ast.unparse(item) for item in node.decorator_list))
    lowered = {name: name.lower() for name in calls + list(decorators)}
    pick = lambda needles: tuple(sorted(name for name, low in lowered.items() if any(key in low for key in needles)))
    annotations = tuple(sorted(
        (arg.arg, ast.unparse(arg.annotation))
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if arg.annotation is not None
    ))
    return FunctionSemantics(
        path=path, qualname=node.name,
        positional_only=tuple(arg.arg for arg in node.args.posonlyargs),
        positional=tuple(arg.arg for arg in node.args.args),
        keyword_only=tuple(arg.arg for arg in node.args.kwonlyargs),
        vararg=node.args.vararg.arg if node.args.vararg else None,
        kwargs=node.args.kwarg.arg if node.args.kwarg else None,
        defaults=tuple(sorted(default_pairs)), annotations=annotations,
        return_annotation=_text(node.returns), return_shapes=tuple(returns),
        raises=tuple(raises), decorators=decorators, auth_gates=pick(AUTH_NAMES),
        calls_resolved=(), calls_unresolved=tuple(calls),
        config_ops=pick(CONFIG_NAMES), concurrency_ops=pick(CONCURRENCY_NAMES),
        metric_ops=pick(METRIC_NAMES), sinks=pick(SINK_NAMES),
    )


def snapshot_tree(repo_root):
    result = {}
    for path in sorted(repo_root.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                semantics = _function(rel, node)
                result[f"{rel}::{semantics.qualname}"] = semantics
    return result


def classify_change(field, before, after):
    if field in {"positional_only", "positional", "keyword_only", "vararg", "kwargs", "defaults", "annotations", "return_annotation", "return_shapes", "raises"}:
        return "breaking"
    if field in {"decorators", "auth_gates", "config_ops", "concurrency_ops", "sinks"}:
        return "risky"
    if field == "metric_ops":
        return "informational"
    return "unknown"


def compare_semantics(before, after):
    rows = []
    for key in sorted(set(before) | set(after)):
        if key not in before or key not in after:
            rows.append({"function": key, "field": "presence", "before": key in before, "after": key in after, "policy": "breaking"})
            continue
        left, right = asdict(before[key]), asdict(after[key])
        for field in sorted(set(left) - {"path", "qualname"}):
            if left[field] != right[field]:
                rows.append({"function": key, "field": field, "before": left[field], "after": right[field], "policy": classify_change(field, left[field], right[field])})
    return rows
```

- [ ] **Step 4: Implement snapshot-file support, envelope output, check/gate policy, and CLI**

```python
# tools/semantic_diff.py
def main(argv=None):
    parser = argparse.ArgumentParser(prog="semantic_diff.py")
    parser.add_argument("--before-tree", type=Path)
    parser.add_argument("--after-tree", type=Path)
    parser.add_argument("--before-snapshot", type=Path)
    parser.add_argument("--after-snapshot", type=Path)
    parser.add_argument("--out", type=Path, default=Path("SEMANTIC_DIFF.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--cst-adapter", choices=("none", "libcst"), default="none")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if sum(value is not None for value in (args.before_tree, args.before_snapshot)) != 1:
        parser.error("one before source is required")
    if sum(value is not None for value in (args.after_tree, args.after_snapshot)) != 1:
        parser.error("one after source is required")
    result = run_semantic_diff(
        before_tree=args.before_tree, after_tree=args.after_tree,
        before_snapshot=args.before_snapshot, after_snapshot=args.after_snapshot,
        output_path=args.out, check_path=args.check, gate=args.gate,
        cst_adapter=args.cst_adapter,
    )
    print(json.dumps(asdict(result), sort_keys=True) if args.json else f"{result.state.upper()}: {result.summary}")
    return exit_code([result], gate=args.gate)
```

`run_semantic_diff()` must classify a `breaking` change as `fail` only in `gate=True`; otherwise it returns `advisory`. If an unresolved call set changes, the result includes an `unknown` row and gate mode fails closed.

- [ ] **Step 5: Run focused and graph compatibility tests**

Run:

```bash
python -m pytest tests/test_semantic_diff_frontend.py tests/test_audit_promotion_wirings_533.py tests/test_graph_source_hash_release_gate.py -q
python tools/semantic_diff.py --help
```

Expected: all tests pass; help exits `0` and shows both tree/snapshot input pairs, `--check`, `--gate`, and optional `--cst-adapter`.

- [ ] **Step 6: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- tools/code_intelligence/semantic_service.py tools/semantic_diff.py tests/test_semantic_diff_frontend.py
git diff --stat -- tools/code_intelligence/semantic_service.py tools/semantic_diff.py tests/test_semantic_diff_frontend.py
python -m pytest tests/test_semantic_diff_frontend.py tests/test_audit_promotion_wirings_533.py tests/test_graph_source_hash_release_gate.py -q
```

Expected: no whitespace errors, only Task 3 paths in the scoped diff, and all selected tests pass. Do not run `git add` or `git commit`.

### Task 4: `reachability.py` Evidence-Preserving Frontend

**Files:**
- Create: `tools/code_intelligence/reachability_service.py`
- Create: `tools/reachability.py`
- Create: `tests/test_reachability_frontend.py`

**Interfaces:**
- Consumes:
  - a lazily loaded Flask application factory or `module:attribute`
  - graph `SECURITY_SURFACE` and `CALL_GRAPH` JSON, validated with
    `validate_projection("security_surface", value)` and
    `validate_projection("call_graph", value)`
  - existing `endpoint_reachability.build(root)` operator-wiring evidence
  - existing `nav_reachability.check_server/check_spa/check_external_nav`
  - existing `REACHABILITY_DEFERRALS.json`
- Produces:

```python
RouteClass = Literal["public", "authenticated", "internal", "unreachable", "unknown"]

@dataclass(frozen=True)
class ProbeObservation:
    status: int | None
    location: str | None
    exception: str | None

def classify_route(
    *,
    rule: str,
    methods: Sequence[str],
    unauthenticated: ProbeObservation,
    authenticated: ProbeObservation | None,
    auth_gate_facts: Sequence[str],
    operator_wiring: str | None,
    navigation: str | None,
    call_paths: Sequence[Sequence[str]],
) -> dict[str, JsonValue]: ...

def analyze_reachability(
    *,
    app_target: str,
    repo_root: Path,
    security_surface_path: Path,
    call_graph_path: Path,
    deferrals_path: Path | None,
    authenticated_fixture: str | None,
    timeout_seconds: float,
) -> tuple[CheckResult, dict[str, JsonValue]]: ...

def run_reachability_cli(*, args: argparse.Namespace, repo_root: Path) -> CheckResult: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Artifact schema: `bd.reachability`, version `1`. Evidence fields remain separate: `auth_probe`, `auth_gate_facts`, `operator_wiring`, `navigation`, `call_paths`, `deferrals`. No field may be promoted into another evidence category.

- [ ] **Step 1: Write pure route-classification and CLI tests**

```python
# tests/test_reachability_frontend.py
from pathlib import Path

from tools.code_intelligence.reachability_service import ProbeObservation, classify_route


def _obs(status, location=None, exception=None):
    return ProbeObservation(status, location, exception)


def test_public_route_requires_unauthenticated_success():
    row = classify_route(
        rule="/public", methods=("GET",), unauthenticated=_obs(200),
        authenticated=None, auth_gate_facts=(), operator_wiring="spa",
        navigation="linked", call_paths=(),
    )
    assert row["classification"] == "public"
    assert row["confidence"] == "high"
    assert row["evidence"]["operator_wiring"] == "spa"


def test_authenticated_route_requires_auth_delta():
    row = classify_route(
        rule="/private", methods=("GET",),
        unauthenticated=_obs(302, "/login"), authenticated=_obs(200),
        auth_gate_facts=("login_required",), operator_wiring="spa",
        navigation="linked", call_paths=(),
    )
    assert row["classification"] == "authenticated"
    assert row["confidence"] == "high"


def test_internal_is_not_inferred_from_dark_operator_wiring_alone():
    row = classify_route(
        rule="/api/internal", methods=("POST",), unauthenticated=_obs(403),
        authenticated=_obs(403), auth_gate_facts=(), operator_wiring="dark",
        navigation=None, call_paths=(),
    )
    assert row["classification"] == "unknown"
    assert row["evidence"]["operator_wiring"] == "dark"


def test_probe_exception_is_unknown():
    row = classify_route(
        rule="/broken", methods=("GET",),
        unauthenticated=_obs(None, exception="RuntimeError"), authenticated=None,
        auth_gate_facts=(), operator_wiring=None, navigation=None, call_paths=(),
    )
    assert row["classification"] == "unknown"
    assert row["confidence"] == "low"
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/test_reachability_frontend.py -q
```

Expected: collection fails with `ModuleNotFoundError: tools.code_intelligence.reachability_service`.

- [ ] **Step 3: Implement the classification matrix**

```python
# tools/code_intelligence/reachability_service.py
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProbeObservation:
    status: int | None
    location: str | None
    exception: str | None


def classify_route(*, rule, methods, unauthenticated, authenticated, auth_gate_facts, operator_wiring, navigation, call_paths):
    evidence = {
        "auth_probe": {
            "unauthenticated": asdict(unauthenticated),
            "authenticated": asdict(authenticated) if authenticated else None,
        },
        "auth_gate_facts": sorted(auth_gate_facts),
        "operator_wiring": operator_wiring,
        "navigation": navigation,
        "call_paths": [list(path) for path in call_paths],
    }
    if unauthenticated.exception:
        classification, confidence, reason = "unknown", "low", "unauthenticated probe raised"
    elif unauthenticated.status is not None and 200 <= unauthenticated.status < 300:
        classification, confidence, reason = "public", "high", "unauthenticated request succeeded"
    elif authenticated and not authenticated.exception and authenticated.status is not None and 200 <= authenticated.status < 300:
        classification, confidence, reason = "authenticated", "high", "authenticated request succeeded after unauthenticated denial"
    elif authenticated and authenticated.status in {401, 403} and unauthenticated.status in {401, 403} and auth_gate_facts:
        classification, confidence, reason = "internal", "medium", "both probes denied and graph records an explicit gate"
    elif unauthenticated.status == 404 and authenticated and authenticated.status == 404:
        classification, confidence, reason = "unreachable", "medium", "route enumerated but both probes returned 404"
    else:
        classification, confidence, reason = "unknown", "low", "evidence does not establish a privilege class"
    return {
        "rule": rule, "methods": sorted(methods), "classification": classification,
        "confidence": confidence, "reason": reason, "evidence": evidence,
    }
```

- [ ] **Step 4: Add bounded Flask probing and lazy imports**

`analyze_reachability()` must:

1. parse `app_target` as `module:attribute`;
2. import the module only inside the bounded child process;
3. enumerate `app.url_map.iter_rules()` in deterministic `(rule, methods)` order;
4. issue unauthenticated requests through `app.test_client()`;
5. invoke `authenticated_fixture(client)` only when the explicit `module:function` option is supplied;
6. treat child timeout as `CheckResult("reachability", ResultState.TIMEOUT, "probe exceeded timeout", {"timeout_seconds": timeout_seconds})`;
7. load existing endpoint/nav/deferral evidence through adapters;
8. write `bd.reachability` atomically;
9. fail in gate mode only when a route with privilege-boundary evidence remains `unknown`.

Load graph inputs through the canonical projection validator:

```python
def _load_projection(path, name):
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_projection(name, value)
    return value


security_surface = _load_projection(security_surface_path, "security_surface")
call_graph = _load_projection(call_graph_path, "call_graph")
```

Use this child-process boundary:

```python
def _probe_in_child(queue, app_target, authenticated_fixture):
    module_name, attribute = app_target.split(":", 1)
    app = getattr(importlib.import_module(module_name), attribute)
    client = app.test_client()
    auth_client = None
    if authenticated_fixture:
        fixture_module, fixture_name = authenticated_fixture.split(":", 1)
        auth_client = getattr(importlib.import_module(fixture_module), fixture_name)(client)
    rows = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: str(item.rule)):
        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"})
        rows.append(_probe_rule(client, auth_client, str(rule.rule), methods))
    queue.put(rows)
```

- [ ] **Step 5: Add CLI**

```python
# tools/reachability.py
def main(argv=None):
    parser = argparse.ArgumentParser(prog="reachability.py")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--app", required=True, help="module:attribute")
    parser.add_argument("--authenticated-fixture", help="module:function")
    parser.add_argument("--security-surface", type=Path, required=True)
    parser.add_argument("--call-graph", type=Path, required=True)
    parser.add_argument("--deferrals", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out", type=Path, default=Path("REACHABILITY.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root or discover_repo_root(Path.cwd())
    result = run_reachability_cli(args=args, repo_root=root)
    print(json.dumps(asdict(result), sort_keys=True) if args.json else f"{result.state.upper()}: {result.summary}")
    return exit_code([result], gate=args.gate)
```

- [ ] **Step 6: Run focused and existing reachability tests**

Run:

```bash
python -m pytest tests/test_reachability_frontend.py tests/test_v3_66_714_endpoint_reachability.py tests/test_nav_reachability.py tests/test_v3_66_719_tools_control.py -q
python tools/reachability.py --help
```

Expected: all selected tests pass; help exits `0` and documents `--app`, `--authenticated-fixture`, `--security-surface`, `--call-graph`, `--timeout`, and `--gate`.

- [ ] **Step 7: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- tools/code_intelligence/reachability_service.py tools/reachability.py tests/test_reachability_frontend.py
git diff --stat -- tools/code_intelligence/reachability_service.py tools/reachability.py tests/test_reachability_frontend.py
python -m pytest tests/test_reachability_frontend.py tests/test_v3_66_714_endpoint_reachability.py tests/test_nav_reachability.py tests/test_v3_66_719_tools_control.py -q
```

Expected: no whitespace errors, only Task 4 paths in the scoped diff, and all selected tests pass. Do not run `git add` or `git commit`.

### Task 5: `differential_oracle.py` and First Typed Adapters

**Files:**
- Create: `tools/code_intelligence/oracle_service.py`
- Create: `tools/code_intelligence/oracle_adapters.py`
- Create: `tools/differential_oracle.py`
- Create: `tests/test_differential_oracle_frontend.py`

**Interfaces:**
- Consumes `AnalysisAdapter`, `AdapterCase`, `AdapterContext`, and `AdapterBudget`.
- Produces:

```python
@dataclass(frozen=True)
class OracleComparison:
    case_id: str
    left: JsonValue
    right: JsonValue
    normalized_left: JsonValue
    normalized_right: JsonValue
    equal: bool
    allowed: bool
    reason: str

def run_oracle_adapter(
    adapter: AnalysisAdapter,
    context: AdapterContext,
) -> tuple[CheckResult, tuple[OracleComparison, ...]]: ...

def register_builtin_oracles() -> tuple[str, ...]: ...
def run_oracle_cli(args: argparse.Namespace) -> int: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Built-in names: `consumer-agreement`, `schema-oracle`, `rollback-oracle`, `template-diff`, `plugin-diff`, `plugin-permission-diff`, `url-classifier-truth`.

Artifact schema: `bd.differential-oracle`, version `1`.

- [ ] **Step 1: Write bounded comparison tests**

```python
# tests/test_differential_oracle_frontend.py
import time
from dataclasses import dataclass
from pathlib import Path

from tools.code_intelligence.adapters import AdapterBudget, AdapterCase, AdapterContext
from tools.code_intelligence.oracle_service import run_oracle_adapter
from tools.code_intelligence.results import CheckResult, ResultState


@dataclass(frozen=True)
class PairAdapter:
    name: str = "pairs"
    kind: str = "oracle"

    def cases(self, context):
        return (
            AdapterCase("same", {"left": "A", "right": "a", "allow": False}),
            AdapterCase("allowed", {"left": 1, "right": 2, "allow": True}),
            AdapterCase("forbidden", {"left": 1, "right": 3, "allow": False}),
        )

    def run(self, case, context):
        payload = case.payload
        normalize = lambda value: value.lower() if isinstance(value, str) else value
        equal = normalize(payload["left"]) == normalize(payload["right"])
        return CheckResult(
            self.name,
            ResultState.PASS if equal or payload["allow"] else ResultState.FAIL,
            case.case_id,
            {"left": payload["left"], "right": payload["right"], "normalized_left": normalize(payload["left"]), "normalized_right": normalize(payload["right"]), "equal": equal, "allowed": payload["allow"], "reason": "fixture policy"},
        )


def _context(tmp_path, timeout=1.0):
    return AdapterContext(tmp_path, tmp_path / "artifacts", tmp_path / "corpus", 17, AdapterBudget(timeout, 10, 4096))


def test_forbidden_divergence_fails_but_allowed_divergence_does_not(tmp_path):
    result, rows = run_oracle_adapter(PairAdapter(), _context(tmp_path))
    assert result.state is ResultState.FAIL
    assert [row.case_id for row in rows] == ["same", "allowed", "forbidden"]
    assert next(row for row in rows if row.case_id == "allowed").allowed is True
    assert next(row for row in rows if row.case_id == "forbidden").allowed is False


@dataclass(frozen=True)
class SlowAdapter:
    name: str = "slow"
    kind: str = "oracle"
    def cases(self, context):
        return (AdapterCase("sleep", {}),)
    def run(self, case, context):
        time.sleep(1.0)
        return CheckResult(self.name, ResultState.PASS, case.case_id, {})


def test_timeout_is_not_a_pass(tmp_path):
    result, rows = run_oracle_adapter(SlowAdapter(), _context(tmp_path, timeout=0.05))
    assert result.state is ResultState.TIMEOUT
    assert rows == ()
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/test_differential_oracle_frontend.py -q
```

Expected: collection fails with `ModuleNotFoundError: tools.code_intelligence.oracle_service`.

- [ ] **Step 3: Implement process-bounded oracle execution**

```python
# tools/code_intelligence/oracle_service.py
from dataclasses import dataclass
import multiprocessing


@dataclass(frozen=True)
class OracleComparison:
    case_id: str
    left: JsonValue
    right: JsonValue
    normalized_left: JsonValue
    normalized_right: JsonValue
    equal: bool
    allowed: bool
    reason: str


def _worker(queue, adapter, cases, context):
    queue.put([adapter.run(case, context) for case in cases])


def run_oracle_adapter(adapter, context):
    cases = tuple(adapter.cases(context))
    if len(cases) > context.budget.max_cases:
        return CheckResult(adapter.name, ResultState.ERROR, "adapter exceeded max_cases", {"cases": len(cases)}), ()
    queue = multiprocessing.get_context("spawn").Queue()
    process = multiprocessing.get_context("spawn").Process(target=_worker, args=(queue, adapter, cases, context))
    process.start()
    process.join(context.budget.timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        return CheckResult(adapter.name, ResultState.TIMEOUT, "oracle budget exceeded", {"timeout_seconds": context.budget.timeout_seconds}), ()
    if process.exitcode != 0 or queue.empty():
        return CheckResult(adapter.name, ResultState.ERROR, "oracle worker failed", {"exitcode": process.exitcode}), ()
    raw = queue.get()
    rows = tuple(
        OracleComparison(
            case.case_id,
            item.evidence.get("left"), item.evidence.get("right"),
            item.evidence.get("normalized_left"), item.evidence.get("normalized_right"),
            bool(item.evidence.get("equal")), bool(item.evidence.get("allowed")),
            str(item.evidence.get("reason", "")),
        )
        for case, item in zip(cases, raw)
    )
    forbidden = [row for row in rows if not row.equal and not row.allowed]
    state = ResultState.FAIL if forbidden else ResultState.PASS
    return CheckResult(adapter.name, state, f"{len(rows)} comparisons; {len(forbidden)} forbidden divergences", {"comparisons": len(rows), "forbidden": len(forbidden)}), rows
```

- [ ] **Step 4: Implement built-in adapters as wrappers**

`tools/code_intelligence/oracle_adapters.py` must wrap, not reimplement:

```python
BUILTIN_ORACLE_COMMANDS = {
    "consumer-agreement": ("tools/consumer_agreement.py", "--gate"),
    "schema-oracle": ("toolchain/bin/bd-schema-oracle", "--json"),
    "rollback-oracle": ("toolchain/bin/bd-rollback-oracle", "--json"),
    "template-diff": ("toolchain/bin/bd-template-diff", "--json"),
    "plugin-diff": ("toolchain/bin/bd-plugin-diff", "--json"),
    "plugin-permission-diff": ("toolchain/bin/bd-plugin-permission-diff", "--json"),
    "url-classifier-truth": ("toolchain/bin/bd-fuzz-urlguard", "--json"),
}
```

Each adapter:

- receives explicit fixture/input paths in `AdapterCase.payload`;
- invokes `[sys.executable, script, ...]` with `cwd=context.repo_root`;
- never uses `shell=True`;
- caps captured stdout/stderr at `max_output_bytes`;
- parses JSON only when the wrapped command documents JSON output;
- converts a missing wrapper into `unknown`, not `pass`;
- records only command-relative paths, exit code, counts, and secret-safe summaries.

`register_builtin_oracles()` instantiates and registers all seven names, returning the sorted names.

- [ ] **Step 5: Implement CLI**

```python
# tools/differential_oracle.py
def main(argv=None):
    parser = argparse.ArgumentParser(prog="differential_oracle.py")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--adapter", action="append", dest="adapters")
    parser.add_argument("--list-adapters", action="store_true")
    parser.add_argument("--corpus", type=Path, required=False)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-cases", type=int, default=1000)
    parser.add_argument("--max-output-bytes", type=int, default=1048576)
    parser.add_argument("--out", type=Path, default=Path("DIFFERENTIAL_ORACLE.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = register_builtin_oracles()
    if args.list_adapters:
        print("\n".join(names))
        return 0
    if not args.adapters:
        parser.error("at least one --adapter is required")
    return run_oracle_cli(args)
```

- [ ] **Step 6: Run focused and wrapped-tool self-tests**

Run:

```bash
python -m pytest tests/test_differential_oracle_frontend.py -q
python tools/differential_oracle.py --list-adapters
python toolchain/bin/bd-schema-oracle --selftest
python toolchain/bin/bd-template-diff --selftest
python toolchain/bin/bd-plugin-diff --selftest
python toolchain/bin/bd-fuzz-urlguard --selftest
```

Expected: focused tests pass; adapter listing contains all seven exact names in sorted order; each available wrapped-tool self-test reports `SELFTEST PASS`. A genuinely absent optional wrapper must be reported `unknown` by the frontend and must not be relabeled pass.

- [ ] **Step 7: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- tools/code_intelligence/oracle_service.py tools/code_intelligence/oracle_adapters.py tools/differential_oracle.py tests/test_differential_oracle_frontend.py
git diff --stat -- tools/code_intelligence/oracle_service.py tools/code_intelligence/oracle_adapters.py tools/differential_oracle.py tests/test_differential_oracle_frontend.py
python -m pytest tests/test_differential_oracle_frontend.py -q
```

Expected: no whitespace errors, only Task 5 paths in the scoped diff, and all focused tests pass. Do not run `git add` or `git commit`.

### Task 6: `fuzz_harness.py` Deterministic Replay Frontend

**Files:**
- Create: `tools/code_intelligence/fuzz_service.py`
- Create: `tools/code_intelligence/fuzz_adapters.py`
- Create: `tools/fuzz_harness.py`
- Create: `tests/test_fuzz_harness_frontend.py`

**Interfaces:**
- Consumes `AnalysisAdapter`, deterministic JSON corpus, explicit seed, and focused fuzzer wrappers.
- Produces:

```python
@dataclass(frozen=True)
class FuzzFinding:
    adapter: str
    case_id: str
    state: Literal["fail", "timeout", "error"]
    fingerprint: str
    summary: str
    reproducer: str | None

def load_corpus(path: Path, *, max_cases: int) -> tuple[AdapterCase, ...]: ...
def run_fuzz_adapter(
    adapter: AnalysisAdapter,
    context: AdapterContext,
    *,
    reproducer_dir: Path,
) -> tuple[CheckResult, tuple[FuzzFinding, ...]]: ...
def register_builtin_fuzzers() -> tuple[str, ...]: ...
def run_fuzz_cli(args: argparse.Namespace) -> int: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Built-in names: `redaction`, `url-guard`, `path-guard`, `import-parser`, `plugin`.

Artifact schema: `bd.fuzz-results`, version `1`. Reproducer filenames are `<adapter>--<case-id>--<fingerprint-prefix>.json`. Reproducer payloads pass the shared secret scanner before atomic write.

- [ ] **Step 1: Write replay, timeout, crash, and import-safety tests**

```python
# tests/test_fuzz_harness_frontend.py
import importlib
import json
import time
from dataclasses import dataclass

from tools.code_intelligence.adapters import AdapterBudget, AdapterCase, AdapterContext
from tools.code_intelligence.fuzz_service import load_corpus, run_fuzz_adapter
from tools.code_intelligence.results import CheckResult, ResultState


@dataclass(frozen=True)
class FixtureFuzzer:
    name: str = "fixture"
    kind: str = "fuzz"
    def cases(self, context):
        return load_corpus(context.corpus_dir / "corpus.json", max_cases=context.budget.max_cases)
    def run(self, case, context):
        if case.case_id == "crash":
            raise RuntimeError("fixture crash")
        if case.case_id == "sleep":
            time.sleep(1.0)
        return CheckResult(self.name, ResultState.PASS, case.case_id, {"case_id": case.case_id})


def _context(tmp_path, timeout=1.0, seed=42):
    return AdapterContext(tmp_path, tmp_path / "artifacts", tmp_path, seed, AdapterBudget(timeout, 10, 4096))


def test_corpus_order_is_seeded_and_repeatable(tmp_path):
    corpus = {"schema": 1, "cases": [{"id": "b", "payload": 2}, {"id": "a", "payload": 1}]}
    (tmp_path / "corpus.json").write_text(json.dumps(corpus))
    first = load_corpus(tmp_path / "corpus.json", max_cases=10)
    second = load_corpus(tmp_path / "corpus.json", max_cases=10)
    assert first == second
    assert [case.case_id for case in first] == ["a", "b"]


def test_crash_writes_minimal_secret_safe_reproducer(tmp_path):
    (tmp_path / "corpus.json").write_text(json.dumps({"schema": 1, "cases": [{"id": "crash", "payload": {"value": 1}}]}))
    result, findings = run_fuzz_adapter(FixtureFuzzer(), _context(tmp_path), reproducer_dir=tmp_path / "repro")
    assert result.state is ResultState.FAIL
    assert findings[0].state == "error"
    repro = tmp_path / findings[0].reproducer
    assert json.loads(repro.read_text())["case_id"] == "crash"
    assert "RuntimeError" not in repro.read_text()


def test_import_does_not_execute_fuzzing(monkeypatch):
    called = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: called.append(args))
    importlib.reload(importlib.import_module("tools.fuzz_harness"))
    assert called == []
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/test_fuzz_harness_frontend.py -q
```

Expected: collection fails with `ModuleNotFoundError: tools.code_intelligence.fuzz_service`.

- [ ] **Step 3: Implement deterministic corpus loading and bounded replay**

```python
# tools/code_intelligence/fuzz_service.py
from __future__ import annotations

import hashlib
import json
import multiprocessing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FuzzFinding:
    adapter: str
    case_id: str
    state: str
    fingerprint: str
    summary: str
    reproducer: str | None


def load_corpus(path, *, max_cases):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("fuzz corpus must be schema 1 with cases[]")
    rows = tuple(
        AdapterCase(str(row["id"]), row.get("payload"))
        for row in sorted(payload["cases"], key=lambda row: str(row["id"]))
    )
    if len(rows) > max_cases:
        raise ValueError(f"fuzz corpus has {len(rows)} cases; max_cases={max_cases}")
    return rows


def _fingerprint(adapter, case_id, state):
    return hashlib.sha256(f"{adapter}\0{case_id}\0{state}".encode()).hexdigest()


def _case_worker(queue, adapter, case, context):
    try:
        queue.put(("result", adapter.run(case, context)))
    except BaseException:
        queue.put(("error", None))


def validate_fuzz_reproducer(payload):
    if payload.get("schema") != 1:
        raise ValueError("fuzz reproducer schema must be 1")
    if not all(key in payload for key in ("adapter", "case_id", "seed", "payload")):
        raise ValueError("fuzz reproducer is missing a required field")
    assert_secret_safe(payload)


def run_fuzz_adapter(adapter, context, *, reproducer_dir):
    findings = []
    saw_unknown = False
    for case in tuple(adapter.cases(context)):
        queue = multiprocessing.get_context("spawn").Queue()
        process = multiprocessing.get_context("spawn").Process(target=_case_worker, args=(queue, adapter, case, context))
        process.start()
        process.join(context.budget.timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join()
            state, summary = "timeout", "case exceeded timeout"
        elif queue.empty():
            state, summary = "error", "adapter raised"
        else:
            kind, case_result = queue.get()
            if kind == "error":
                state, summary = "error", "adapter raised"
            elif case_result.state in {ResultState.FAIL, ResultState.TIMEOUT, ResultState.ERROR}:
                state, summary = case_result.state.value, case_result.summary
            elif case_result.state is ResultState.UNKNOWN:
                saw_unknown = True
                continue
            else:
                state, summary = "pass", case_result.summary
        if state == "pass":
            continue
        fingerprint = _fingerprint(adapter.name, case.case_id, state)
        reproducer_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{adapter.name}--{case.case_id}--{fingerprint[:12]}.json"
        payload = {"schema": 1, "adapter": adapter.name, "case_id": case.case_id, "seed": context.seed, "payload": case.payload}
        assert_secret_safe(payload)
        atomic_write_json(reproducer_dir / filename, payload, validate_fuzz_reproducer)
        findings.append(FuzzFinding(adapter.name, case.case_id, state, fingerprint, summary, str((reproducer_dir / filename).relative_to(context.repo_root)) if (reproducer_dir / filename).is_relative_to(context.repo_root) else filename))
    final_state = ResultState.FAIL if findings else (ResultState.UNKNOWN if saw_unknown else ResultState.PASS)
    return CheckResult(adapter.name, final_state, f"{len(findings)} fuzz failures", {"findings": len(findings), "seed": context.seed}), tuple(findings)
```

- [ ] **Step 4: Add focused-fuzzer adapters**

```python
# tools/code_intelligence/fuzz_adapters.py
BUILTIN_FUZZ_COMMANDS = {
    "redaction": "toolchain/bin/bd-fuzz-redaction",
    "url-guard": "toolchain/bin/bd-fuzz-urlguard",
    "path-guard": "toolchain/bin/bd-fuzz-pathguard",
    "import-parser": "toolchain/bin/bd-fuzz-import",
    "plugin": "toolchain/bin/bd-plugin-fuzz",
}
```

Each adapter invokes the existing tool with `--json`, explicit `--work <repo_root>` when supported, `cwd=context.repo_root`, no shell, bounded output, and one adapter case per frozen corpus entry. A wrapper that supports only its internal corpus receives one synthetic `AdapterCase("builtin-corpus", {})`; its existing corpus remains authoritative. Optional Hypothesis generation is exposed only as `--generator hypothesis` and returns `CheckResult("hypothesis", ResultState.UNKNOWN, "hypothesis is unavailable", {"generator": "hypothesis"})` if Hypothesis is unavailable outside gate mode.

- [ ] **Step 5: Implement side-effect-free CLI**

```python
# tools/fuzz_harness.py
def main(argv=None):
    parser = argparse.ArgumentParser(prog="fuzz_harness.py")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--adapter", action="append", dest="adapters")
    parser.add_argument("--list-adapters", action="store_true")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-cases", type=int, default=1000)
    parser.add_argument("--max-output-bytes", type=int, default=1048576)
    parser.add_argument("--generator", choices=("none", "hypothesis"), default="none")
    parser.add_argument("--reproducer-dir", type=Path, default=Path("regression_corpus/reproducers"))
    parser.add_argument("--out", type=Path, default=Path("FUZZ_RESULTS.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = register_builtin_fuzzers()
    if args.list_adapters:
        print("\n".join(names))
        return 0
    if not args.adapters:
        parser.error("at least one --adapter is required")
    return run_fuzz_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run focused and existing fuzzer self-tests**

Run:

```bash
python -m pytest tests/test_fuzz_harness_frontend.py -q
python tools/fuzz_harness.py --list-adapters
python toolchain/bin/bd-fuzz-redaction --selftest
python toolchain/bin/bd-fuzz-urlguard --selftest
python toolchain/bin/bd-fuzz-pathguard --selftest
python toolchain/bin/bd-fuzz-import --selftest
python toolchain/bin/bd-plugin-fuzz --selftest
```

Expected: focused tests pass; adapter listing contains the five exact names; every available wrapped-tool self-test reports `SELFTEST PASS`. Importing `tools.fuzz_harness` performs no fuzz run.

- [ ] **Step 7: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- tools/code_intelligence/fuzz_service.py tools/code_intelligence/fuzz_adapters.py tools/fuzz_harness.py tests/test_fuzz_harness_frontend.py
git diff --stat -- tools/code_intelligence/fuzz_service.py tools/code_intelligence/fuzz_adapters.py tools/fuzz_harness.py tests/test_fuzz_harness_frontend.py
python -m pytest tests/test_fuzz_harness_frontend.py -q
```

Expected: no whitespace errors, only Task 6 paths in the scoped diff, and all focused tests pass. Do not run `git add` or `git commit`.

### Task 7: Standalone Documentation, Help Contracts, and Final Analysis-Frontend Band

**Files:**
- Create: `docs/code-intelligence/ANALYSIS_FRONTENDS.md`
- Create: `tests/test_code_intelligence_frontend_docs.py`
- Verify only: `tools/bd-audit-gate.py`
- Verify only: `project-knowledge/bd-audit-gate.py`
- Verify only: `toolchain/bin/bd-audit-gate.py`

**Interfaces:**
- Consumes the five standalone CLI contracts.
- Produces documented commands, exit codes, artifact schemas, compatibility boundaries, optional-dependency behavior, and explicit non-wiring confirmation.

- [ ] **Step 1: Write documentation contract tests**

```python
# tests/test_code_intelligence_frontend_docs.py
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "code-intelligence" / "ANALYSIS_FRONTENDS.md"
TOOLS = (
    "tools/coverage_map.py",
    "tools/semantic_diff.py",
    "tools/reachability.py",
    "tools/differential_oracle.py",
    "tools/fuzz_harness.py",
)


def test_every_frontend_has_help():
    for tool in TOOLS:
        run = subprocess.run([sys.executable, tool, "--help"], cwd=ROOT, capture_output=True, text=True)
        assert run.returncode == 0, (tool, run.stderr)
        assert "--json" in run.stdout and "--gate" in run.stdout


def test_docs_name_all_commands_and_schemas():
    text = DOC.read_text(encoding="utf-8")
    for name in ("bd-coverage-map", "semantic_diff.py", "reachability.py", "differential_oracle.py", "fuzz_harness.py"):
        assert name in text
    for schema in ("bd.coverage-gaps", "bd.semantic-diff", "bd.reachability", "bd.differential-oracle", "bd.fuzz-results"):
        assert schema in text


def test_docs_define_exit_codes_and_unknown():
    text = DOC.read_text(encoding="utf-8")
    assert "| 0 |" in text and "| 1 |" in text and "| 2 |" in text
    assert "unknown is never a synonym for pass" in text.lower()


def test_composite_gate_is_not_wired_by_this_plan():
    gate = (ROOT / "tools" / "bd-audit-gate.py").read_text(encoding="utf-8")
    for name in ("semantic_diff.py", "reachability.py", "differential_oracle.py", "fuzz_harness.py"):
        assert name not in gate
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/test_code_intelligence_frontend_docs.py -q
```

Expected: fails because `docs/code-intelligence/ANALYSIS_FRONTENDS.md` does not exist.

- [ ] **Step 3: Write standalone operator documentation**

Create `docs/code-intelligence/ANALYSIS_FRONTENDS.md` with these exact sections and commands:

````markdown
# Code-Intelligence Analysis Frontends

These commands are standalone. They do not change production service behavior and
are not wired into `bd-audit-gate` until a later integration review.

## Common result states

`pass`, `fail`, `advisory`, `unknown`, `timeout`, and `error` are distinct.
Unknown is never a synonym for pass. `--gate` converts a required unknown or a
blocking policy violation into exit 1.

| Exit | Meaning |
|---:|---|
| 0 | completed with pass, advisory, or non-gating unknown |
| 1 | blocking failure, gate-required unknown, timeout, or execution error |
| 2 | CLI usage or input-validation error |

## bd-coverage-map

Schema: `bd.coverage-gaps` version 2.

```bash
python toolchain/bin/bd-coverage-map \
  --root . \
  --coverage /path/to/coverage.json \
  --graph /path/to/KNOWLEDGE_GRAPH.db \
  --radon-json /path/to/radon.json \
  --test-catalog /path/to/test_catalog.json \
  --out /path/to/COVERAGE_GAPS.json \
  --json
```

Omit `--coverage` only to record an explicit `unknown` artifact. Use `--check
/path/to/COVERAGE_GAPS.json` to compare deterministic content.

## semantic_diff.py

Schema: `bd.semantic-diff` version 1.

```bash
python tools/semantic_diff.py \
  --before-tree /path/to/old \
  --after-tree /path/to/new \
  --out /path/to/SEMANTIC_DIFF.json \
  --json
```

Tree and snapshot inputs are mutually exclusive per side. `--cst-adapter libcst`
is optional and cannot change the standard-library policy verdict.

## reachability.py

Schema: `bd.reachability` version 1.

```bash
python tools/reachability.py \
  --root . \
  --app bulk_downloader.app:app \
  --security-surface /path/to/SECURITY_SURFACE.json \
  --call-graph /path/to/CALL_GRAPH.json \
  --deferrals /path/to/REACHABILITY_DEFERRALS.json \
  --out /path/to/REACHABILITY.json \
  --json
```

Authenticated probing requires an explicit `--authenticated-fixture
module:function`. Operator wiring, navigation, auth probes, graph paths, and
deferrals remain separate evidence fields.

## differential_oracle.py

Schema: `bd.differential-oracle` version 1.

```bash
python tools/differential_oracle.py --list-adapters
python tools/differential_oracle.py \
  --root . \
  --adapter consumer-agreement \
  --adapter url-classifier-truth \
  --corpus /path/to/oracle-cases.json \
  --seed 17 \
  --out /path/to/DIFFERENTIAL_ORACLE.json \
  --json
```

Allowed divergences remain visible and do not hide forbidden divergences.

## fuzz_harness.py

Schema: `bd.fuzz-results` version 1.

```bash
python tools/fuzz_harness.py --list-adapters
python tools/fuzz_harness.py \
  --root . \
  --adapter redaction \
  --adapter path-guard \
  --corpus /path/to/fuzz-corpus.json \
  --seed 42 \
  --timeout 10 \
  --reproducer-dir /path/to/reproducers \
  --out /path/to/FUZZ_RESULTS.json \
  --json
```

The standard-library replay runner is always available. `--generator hypothesis`
is optional and belongs in the isolated audit environment. Importing the module
does not execute fuzzing.

## Secret and path safety

Artifacts and reproducers contain normalized relative paths, hashes, case IDs,
counts, and scrubbed summaries. They must not contain credentials, cookies,
authorization headers, signed queries, or raw captured bodies.
````

- [ ] **Step 4: Run the complete standalone frontend band**

Run:

```bash
python -m pytest \
  tests/test_code_intelligence_adapters.py \
  tests/test_coverage_map_frontend.py \
  tests/test_semantic_diff_frontend.py \
  tests/test_reachability_frontend.py \
  tests/test_differential_oracle_frontend.py \
  tests/test_fuzz_harness_frontend.py \
  tests/test_code_intelligence_frontend_docs.py \
  tests/test_audit_promotion_wirings_533.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_v3_66_714_endpoint_reachability.py \
  tests/test_nav_reachability.py \
  tests/test_v3_66_719_tools_control.py -q
```

Expected: all selected tests pass with no skip introduced by these frontends.

- [ ] **Step 5: Run standalone help and deterministic fixture checks**

Run:

```bash
python tools/coverage_map.py --help
python toolchain/bin/bd-coverage-map --help
python tools/semantic_diff.py --help
python tools/reachability.py --help
python tools/differential_oracle.py --list-adapters
python tools/fuzz_harness.py --list-adapters
python -c "import json,pathlib; [json.loads(p.read_text()) for p in pathlib.Path('tests/fixtures/code_intelligence').rglob('*.json')]; print('fixture JSON: PASS')"
```

Expected: all commands exit `0`; oracle list contains seven built-ins; fuzz list contains five built-ins; fixture parse prints `fixture JSON: PASS`.

- [ ] **Step 6: Verify composite-gate files are untouched**

Run:

```bash
git diff --exit-code -- tools/bd-audit-gate.py project-knowledge/bd-audit-gate.py toolchain/bin/bd-audit-gate.py
```

Expected: exit `0` with no output. If any file differs, stop and remove only the analysis-frontend wiring; do not discard unrelated pre-existing user changes.

- [ ] **Step 7: Pre-commit checkpoint (do not commit)**

Run:

```bash
git diff --check -- \
  tools/code_intelligence/adapters.py \
  tools/code_intelligence/coverage_service.py \
  tools/code_intelligence/semantic_service.py \
  tools/code_intelligence/reachability_service.py \
  tools/code_intelligence/oracle_service.py \
  tools/code_intelligence/oracle_adapters.py \
  tools/code_intelligence/fuzz_service.py \
  tools/code_intelligence/fuzz_adapters.py \
  tools/coverage_map.py \
  toolchain/bin/bd-coverage-map \
  tools/semantic_diff.py \
  tools/reachability.py \
  tools/differential_oracle.py \
  tools/fuzz_harness.py \
  tests/fixtures/code_intelligence \
  tests/test_code_intelligence_adapters.py \
  tests/test_coverage_map_frontend.py \
  tests/test_semantic_diff_frontend.py \
  tests/test_reachability_frontend.py \
  tests/test_differential_oracle_frontend.py \
  tests/test_fuzz_harness_frontend.py \
  tests/test_code_intelligence_frontend_docs.py \
  docs/code-intelligence/ANALYSIS_FRONTENDS.md
git status --short
python -m pytest \
  tests/test_code_intelligence_adapters.py \
  tests/test_coverage_map_frontend.py \
  tests/test_semantic_diff_frontend.py \
  tests/test_reachability_frontend.py \
  tests/test_differential_oracle_frontend.py \
  tests/test_fuzz_harness_frontend.py \
  tests/test_code_intelligence_frontend_docs.py \
  tests/test_audit_promotion_wirings_533.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_v3_66_714_endpoint_reachability.py \
  tests/test_nav_reachability.py \
  tests/test_v3_66_719_tools_control.py -q
```

Expected: no whitespace errors; `git status --short` shows the analysis-frontend work plus any clearly pre-existing unrelated changes; the complete selected band passes. Do not stage, commit, merge, push, package, or update the static KB.

## Final pre-commit review checklist

- [ ] All five exact frontends have `--help`, deterministic JSON, documented exit codes, and fixture tests.
- [ ] Missing coverage and unavailable optional packages produce `unknown`, never pass.
- [ ] Every durable output is source-SHA-bound, input-hashed, schema-versioned, validated, and atomically replaced.
- [ ] No output includes secrets, raw bodies, signed queries, authorization headers, cookies, or credentials.
- [ ] Reachability evidence categories remain separate.
- [ ] Existing specialist tools are wrapped, not reimplemented.
- [ ] Importing `tools.fuzz_harness` performs no analysis.
- [ ] Standard-library execution works without `libcst`, `hypothesis`, or `radon`.
- [ ] No composite-gate file changed.
- [ ] The worktree remains uncommitted and unmerged.
