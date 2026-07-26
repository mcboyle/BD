# Code-Intelligence Foundation and Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the portable, deterministic artifact foundation and extend the existing L0/graph pipeline so every later code-intelligence tool consumes one source-SHA-bound schema.

**Architecture:** Add a small `tools/code_intelligence` standard-library package for repository discovery, tracked-tree snapshots, normalized results, schema validation, and atomic artifacts. Extend `tools/l0_extract.py` and `tools/graph_build.py` in place; keep `KNOWLEDGE_GRAPH.db` canonical and emit deterministic JSON projections with confidence and provenance instead of creating a second graph.

**Tech Stack:** Python 3 standard library (`argparse`, `ast`, `dataclasses`, `hashlib`, `json`, `pathlib`, `sqlite3`, `tempfile`), Git CLI for tracked-tree discovery, pytest fixtures.

## Global Constraints

- Python standard library is the required runtime baseline. Optional packages such as `libcst`, `hypothesis`, `radon`, `bandit`, and `vulture` may enhance isolated audit runs but cannot be required by normal release gates.
- Every durable artifact carries schema name and version, tracked-tree source SHA, tool version, deterministic input hashes, and a generation timestamp separated from content used for deterministic comparisons.
- Durable writes are validate-then-atomically-replace. A failed run must not leave a plausible partial artifact.
- All paths are explicitly supplied or derived from a discovered repository root. `/home/claude`, `/root`, and workstation-specific paths are not defaults in canonical interfaces.
- Outputs exclude secret values, credentials, cookies, authorization headers, signed queries, and raw captured bodies.
- Advisory findings and release-blocking failures are distinct result states.
- Existing CLI behavior remains available through compatibility wrappers or adapters.
- New behavior follows RED → GREEN → refactor. Each test must be observed failing for the intended missing behavior before implementation.
- Do not commit, merge, push, advance the external static-KB pin, or cut a release during execution of this plan. Every task ends at a pre-commit checkpoint for user review.

## File map

- Create `tools/code_intelligence/__init__.py` — package version and public shared interfaces.
- Create `tools/code_intelligence/paths.py` — repository discovery and normalized tracked paths.
- Create `tools/code_intelligence/snapshot.py` — tracked-tree manifest and deterministic source SHA.
- Create `tools/code_intelligence/results.py` — normalized result states and exit-code policy.
- Create `tools/code_intelligence/artifacts.py` — canonical JSON, validation-before-write, and atomic replacement.
- Create `tools/code_intelligence/schemas.py` — versioned artifact-envelope and graph-projection validators.
- Modify `tools/l0_extract.py` — richer Python function facts and source-bound graph metadata.
- Modify `tools/graph_build.py` — lossless unresolved calls and all remaining graph projections.
- Create `tests/test_code_intelligence_foundation.py` — paths, snapshots, results, artifacts, and schema tests.
- Create `tests/test_l0_extract_v2.py` — exact AST fact extraction tests.
- Create `tests/test_graph_projections_v2.py` — graph projection and determinism tests.
- Modify `tests/test_graph_source_hash_release_gate.py` — retain compatibility with the versioned canonical graph.
- Modify `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md` — document the implemented envelope and projection schemas only after tests pass.

---

### Task 1: Portable repository discovery and tracked-tree snapshots

**Files:**
- Create: `tools/code_intelligence/__init__.py`
- Create: `tools/code_intelligence/paths.py`
- Create: `tools/code_intelligence/snapshot.py`
- Test: `tests/test_code_intelligence_foundation.py`

**Interfaces:**
- Produces: `discover_repo_root(start: Path | str) -> Path`
- Produces: `normalize_repo_path(root: Path, path: Path | str) -> str`
- Produces: `tracked_files(root: Path) -> tuple[str, ...]`
- Produces: `FileFact(path: str, sha256: str, size: int, lines: int)`
- Produces: `TreeSnapshot(source_sha: str, files: tuple[FileFact, ...])`
- Produces: `build_snapshot(root: Path, include: Callable[[str], bool] | None = None) -> TreeSnapshot`
- Produces CLI: `python3 -m tools.code_intelligence.snapshot --root ROOT --scope tracked|production (--out SNAPSHOT_JSON | --check SNAPSHOT_JSON)`
- Source SHA definition: SHA-256 over sorted records `path + NUL + file_sha256 + LF`, not the mutable Git index or dirty-worktree-insensitive commit SHA.

- [ ] **Step 1: Write repository and snapshot RED tests**

```python
def test_snapshot_hash_changes_with_tracked_dirty_bytes(git_repo):
    target = git_repo / "bulk_downloader" / "sample.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    first = build_snapshot(git_repo)
    target.write_text("VALUE = 2\n", encoding="utf-8")
    second = build_snapshot(git_repo)
    assert first.source_sha != second.source_sha
    assert second.files[0].path == "bulk_downloader/sample.py"


def test_normalize_repo_path_rejects_escape(git_repo, tmp_path):
    with pytest.raises(ValueError, match="outside repository"):
        normalize_repo_path(git_repo, tmp_path / "escape.py")


def test_snapshot_cli_check_detects_dirty_tracked_tree(git_repo, tmp_path):
    output = tmp_path / "snapshot.json"
    run_module("tools.code_intelligence.snapshot", "--root", git_repo,
               "--scope", "tracked", "--out", output, check=True)
    (git_repo / "bulk_downloader" / "sample.py").write_text(
        "VALUE = 3\n", encoding="utf-8")
    checked = run_module("tools.code_intelligence.snapshot", "--root", git_repo,
                         "--scope", "tracked", "--check", output)
    assert checked.returncode != 0
    assert "source SHA differs" in checked.stdout
```

- [ ] **Step 2: Run the tests and observe the intended RED state**

Run:

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py \
  -k "snapshot_hash_changes or normalize_repo_path" -vv
```

Expected: collection fails with `ModuleNotFoundError: No module named 'tools.code_intelligence'`.

- [ ] **Step 3: Implement repository discovery and snapshots**

```python
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


def build_snapshot(root: Path, include=None) -> TreeSnapshot:
    root = discover_repo_root(root)
    facts = []
    for rel in tracked_files(root):
        if include is not None and not include(rel):
            continue
        raw = (root / rel).read_bytes()
        lines = raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)
        facts.append(FileFact(
            rel, hashlib.sha256(raw).hexdigest(), len(raw), lines
        ))
    digest = hashlib.sha256()
    for fact in facts:
        digest.update(f"{fact.path}\0{fact.sha256}\n".encode("utf-8"))
    return TreeSnapshot(digest.hexdigest(), tuple(facts))
```

Use `git -C <root> ls-files -z --cached` for the canonical tracked set, decode with `surrogateescape`, normalize separators to `/`, sort once, and reject symlink/path resolution outside the repository.

The module CLI serializes `TreeSnapshot` through the shared envelope. `--scope
production` applies the same production-file predicate exported by
`l0_extract.py`; `--check` compares the canonical content and returns nonzero
without rewriting the supplied snapshot.

- [ ] **Step 4: Run the focused and full foundation tests**

Run:

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py -vv
```

Expected: all Task 1 tests pass, including dirty tracked bytes, deterministic ordering, ignored-file exclusion, subdirectory discovery, and path-escape rejection.

- [ ] **Step 5: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/code_intelligence tests/test_code_intelligence_foundation.py
git status --short -- tools/code_intelligence tests/test_code_intelligence_foundation.py
```

Expected: no whitespace errors; only the four Task 1 files are newly listed.

---

### Task 2: Normalized results and atomic deterministic artifacts

**Files:**
- Create: `tools/code_intelligence/results.py`
- Create: `tools/code_intelligence/artifacts.py`
- Modify: `tests/test_code_intelligence_foundation.py`

**Interfaces:**
- Produces: `ResultState` with `PASS`, `FAIL`, `ADVISORY`, `UNKNOWN`, `TIMEOUT`, `ERROR`
- Produces: `CheckResult(name: str, state: ResultState, summary: str, evidence: Mapping[str, object])`
- Produces: `exit_code(results: Iterable[CheckResult], gate: bool) -> int`
- Produces: `canonical_bytes(value: object, *, omit_keys: frozenset[str] = frozenset({"generated_at"})) -> bytes`
- Produces: `atomic_write_json(path: Path, value: object, validator: Callable[[object], None]) -> None`
- Produces: `artifact_hash(value: object) -> str`
- Produces: `compare_artifact_dirs(left: Path, right: Path, *, ignore_generation_time: bool = True) -> tuple[ArtifactDifference, ...]`
- Produces CLI: `python3 -m tools.code_intelligence.artifacts compare --left LEFT_DIR --right RIGHT_DIR --ignore-generation-time`

- [ ] **Step 1: Add RED tests for state policy and failed atomic replacement**

```python
def test_unknown_is_nonzero_only_in_gate_mode():
    result = CheckResult("coverage", ResultState.UNKNOWN, "coverage absent", {})
    assert exit_code([result], gate=False) == 0
    assert exit_code([result], gate=True) != 0


def test_atomic_write_preserves_previous_artifact_on_validation_failure(tmp_path):
    target = tmp_path / "artifact.json"
    target.write_text('{"old":true}\n', encoding="utf-8")
    with pytest.raises(SchemaError):
        atomic_write_json(target, {"schema": 99}, validate_envelope)
    assert target.read_text(encoding="utf-8") == '{"old":true}\n'
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_artifact_compare_ignores_only_generation_time(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    write_valid_artifact(left / "A.json", generated_at="2026-07-23T00:00:00Z")
    write_valid_artifact(right / "A.json", generated_at="2026-07-23T00:01:00Z")
    assert compare_artifact_dirs(left, right) == ()
    value = json.loads((right / "A.json").read_text(encoding="utf-8"))
    value["source_sha"] = "f" * 64
    atomic_write_json(right / "A.json", value, validate_envelope)
    assert compare_artifact_dirs(left, right)[0].state == "stale"
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py \
  -k "unknown_is_nonzero or atomic_write_preserves" -vv
```

Expected: import errors identify the missing `results.py` and `artifacts.py` interfaces.

- [ ] **Step 3: Implement the result and artifact primitives**

```python
class ResultState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ADVISORY = "advisory"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"


def atomic_write_json(path: Path, value: object, validator) -> None:
    validator(value)
    payload = canonical_bytes(value, omit_keys=frozenset())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                   dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
```

Canonical JSON uses UTF-8, `sort_keys=True`, `ensure_ascii=False`, separators `(",", ":")`, and one trailing newline. `artifact_hash()` excludes only `generated_at`; the stored artifact keeps it.

The `compare` subcommand validates JSON inputs, compares relative member sets,
and prints sorted `missing`, `unexpected`, `malformed`, or `stale` differences.
It returns 0 only when no differences remain.

- [ ] **Step 4: Run Task 2 tests**

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py -vv
```

Expected: all foundation tests pass, including deterministic hashes across timestamps, fsync/replace behavior, validator failure preservation, and gate/advisory exit policy.

- [ ] **Step 5: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/code_intelligence tests/test_code_intelligence_foundation.py
python3 -m compileall -q tools/code_intelligence
```

Expected: both commands exit 0.

---

### Task 3: Versioned artifact-envelope and graph schema validators

**Files:**
- Create: `tools/code_intelligence/schemas.py`
- Modify: `tools/code_intelligence/__init__.py`
- Modify: `tests/test_code_intelligence_foundation.py`

**Interfaces:**
- Produces: `SchemaError(ValueError)`
- Produces: `ArtifactEnvelope(schema_name: str, schema_version: int, source_sha: str, tool_version: str, input_hashes: Mapping[str, str], generated_at: str)`
- Produces: `make_envelope(...) -> dict[str, object]`
- Produces: `validate_envelope(value: object, expected_name: str | None = None, supported_version: int = 1) -> None`
- Produces: `validate_projection(name: str, value: object) -> None`
- Produces: `migrate_artifact(name: str, value: Mapping[str, object], *, target_version: int = 1) -> dict[str, object]`
- Produces CLI: `python3 -m tools.code_intelligence.schemas validate --kind SCHEMA_KIND --file JSON_FILE`
- Produces CLI: `python3 -m tools.code_intelligence.schemas migrate --kind SCHEMA_KIND --input SOURCE_JSON --out NORMALIZED_JSON`
- Projection names: `call_graph`, `module_catalog`, `security_surface`, `error_catalog`, `taint_map`, `dead_code`, `config_lineage`, `concurrency_map`, `metrics_catalog`.

- [ ] **Step 1: Add RED tests for malformed, future, and secret-bearing artifacts**

```python
@pytest.mark.parametrize("mutator,message", [
    (lambda x: x.pop("source_sha"), "source_sha"),
    (lambda x: x.__setitem__("schema_version", 2), "unsupported"),
    (lambda x: x.__setitem__("source_sha", "xyz"), "64 lowercase hex"),
])
def test_envelope_rejects_invalid_metadata(valid_envelope, mutator, message):
    mutator(valid_envelope)
    with pytest.raises(SchemaError, match=message):
        validate_envelope(valid_envelope, "call_graph")


def test_projection_rejects_secret_values(valid_projection):
    valid_projection["authorization"] = "Bearer abcdef"
    with pytest.raises(SchemaError, match="secret-like"):
        validate_projection("call_graph", valid_projection)


def test_migration_preserves_unknown_contract_payload_fields(legacy_contracts):
    migrated = migrate_artifact("contracts", legacy_contracts)
    contract = migrated["contracts"]["C0001"]
    assert contract["allowed_divergences"] == ["missing optional label"]
    validate_envelope(migrated, "contracts")
```

- [ ] **Step 2: Verify the new tests fail before implementation**

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py \
  -k "envelope_rejects or projection_rejects_secret" -vv
```

Expected: `ImportError` for `SchemaError` or `validate_projection`.

- [ ] **Step 3: Implement strict envelope and projection validation**

```python
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ENVELOPE = {
    "schema_name", "schema_version", "source_sha",
    "tool_version", "input_hashes", "generated_at",
}


def validate_envelope(value, expected_name=None, supported_version=1):
    if not isinstance(value, dict):
        raise SchemaError("artifact must be an object")
    missing = sorted(REQUIRED_ENVELOPE - value.keys())
    if missing:
        raise SchemaError(f"missing envelope fields: {', '.join(missing)}")
    if expected_name is not None and value["schema_name"] != expected_name:
        raise SchemaError(f"expected schema {expected_name}")
    if value["schema_version"] != supported_version:
        raise SchemaError("unsupported schema version")
    if not HEX64.fullmatch(value["source_sha"]):
        raise SchemaError("source_sha must be 64 lowercase hex characters")
```

Define exact per-projection required keys and value types in `PROJECTION_SCHEMAS`. Recursively reject dict keys matching password, passwd, secret, token, cookie, authorization, bearer, private_key, signing, otp, or credential unless the value is a boolean/count/redacted marker; never include the rejected value in the exception.

`migrate_artifact()` accepts only explicitly registered source versions. The
initial migration registry normalizes the observed schema-1 `_meta` envelopes
for `INVARIANTS.json`, `CONTRACTS.json`, and `COVERAGE_GAPS.json` without
discarding unknown payload fields. An unregistered version raises
`SchemaError("no migration path")`; the CLI validates the migrated value before
atomically writing it.

- [ ] **Step 4: Run all foundation tests**

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py -vv
```

Expected: all tests pass, including future schema rejection and error messages that do not echo secret values.

- [ ] **Step 5: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/code_intelligence tests/test_code_intelligence_foundation.py
git diff --stat -- tools/code_intelligence tests/test_code_intelligence_foundation.py
```

Expected: clean diff check and a bounded change limited to the foundation package/test.

---

### Task 4: Rich deterministic L0 Python function facts

**Files:**
- Modify: `tools/l0_extract.py`
- Create: `tests/test_l0_extract_v2.py`

**Interfaces:**
- Consumes: `build_snapshot(root)` and `atomic_write_json` foundation behavior.
- Produces in each function `meta_json`:
  - `parameters`: ordered records with `name`, `kind`, `default`, and `annotation`;
  - `returns`: `annotation`, `has_value`, `has_none`, `has_bare`, and normalized structural shapes;
  - `raises`: sorted exception names;
  - `decorators`, `auth_calls`, `calls`, `sinks`, and `unresolved_calls`;
  - `config_reads`, `config_writes`, `concurrency_ops`, and `metric_emits`.
- Produces graph `meta`: `schema_name=knowledge_graph`, `schema_version=2`, `source_sha`, `tool_version`, and `input_hashes`.
- Retains legacy `args` and `has_kwargs` fields for compatibility through this program.

- [ ] **Step 1: Write a RED extraction fixture**

```python
SOURCE = '''
@login_required
def sample(a: int, /, b="x", *items, enabled: bool = False, **opts) -> dict:
    current = app.config.get("LIMIT")
    app.config["LIMIT"] = b
    with state_lock:
        metrics.increment("sample.calls")
    if enabled:
        raise ValueError("bad")
    return {"value": a}
'''


def test_l0_records_signature_contract_config_lock_metric_and_auth(tmp_path):
    db = build_fixture_graph(tmp_path, SOURCE)
    meta = function_meta(db, "sample")
    assert [p["kind"] for p in meta["parameters"]] == [
        "positional_only", "positional_or_keyword", "var_positional",
        "keyword_only", "var_keyword",
    ]
    assert meta["parameters"][1]["default"] == '"x"'
    assert meta["returns"]["annotation"] == "dict"
    assert meta["returns"]["has_value"] is True
    assert meta["raises"] == ["ValueError"]
    assert meta["auth_calls"] == []
    assert meta["config_reads"] == [{"key": "LIMIT", "at": 4}]
    assert meta["config_writes"] == [{"key": "LIMIT", "at": 5}]
    assert meta["concurrency_ops"][0]["kind"] == "lock"
    assert meta["metric_emits"][0]["name"] == "sample.calls"
```

- [ ] **Step 2: Run the L0 fixture and observe RED**

```bash
python3 -m pytest tests/test_l0_extract_v2.py \
  -k "signature_contract_config_lock_metric_and_auth" -vv
```

Expected: assertion fails because current metadata has only flattened `args`, `has_kwargs`, decorators, raises, sinks, secrets, and flags.

- [ ] **Step 3: Implement normalized AST helpers and facts**

```python
def _expr(node):
    if node is None:
        return None
    return ast.unparse(node)


def _parameter_records(args):
    ordered = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        kind = "positional_only" if index < len(args.posonlyargs) else "positional_or_keyword"
        ordered.append({"name": arg.arg, "kind": kind,
                        "default": _expr(default), "annotation": _expr(arg.annotation)})
    if args.vararg:
        ordered.append({"name": args.vararg.arg, "kind": "var_positional",
                        "default": None, "annotation": _expr(args.vararg.annotation)})
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        ordered.append({"name": arg.arg, "kind": "keyword_only",
                        "default": _expr(default), "annotation": _expr(arg.annotation)})
    if args.kwarg:
        ordered.append({"name": args.kwarg.arg, "kind": "var_keyword",
                        "default": None, "annotation": _expr(args.kwarg.annotation)})
    return ordered
```

Add small AST recognizers for `os.environ`, `app.config`, settings/config get/set calls, `with` lock contexts, thread/process/async/queue/scheduler calls, and metric calls. Store only normalized names and line numbers; do not store secret values or raw bodies. Preserve each unresolved call record with `from`, `name`, `at`, and a later resolver reason.

- [ ] **Step 4: Bind the graph to the tracked-tree snapshot**

Write graph metadata only after all rows succeed:

```python
snapshot = build_snapshot(root)
meta = {
    "schema_name": "knowledge_graph",
    "schema_version": "2",
    "source_sha": snapshot.source_sha,
    "tool_version": TOOL_VERSION,
    "input_hashes": json.dumps(
        {f.path: f.sha256 for f in snapshot.files if f.path in files},
                               sort_keys=True),
}
```

Build into `db_path + ".tmp"` and atomically replace the destination after SQLite integrity checks. Keep CLI flags `--root` and `--db`; change defaults to discovered repository root and `<root>/artifacts/KNOWLEDGE_GRAPH.db`.

- [ ] **Step 5: Run L0 and compatibility tests**

```bash
python3 -m pytest tests/test_l0_extract_v2.py \
  tests/test_graph_source_hash_release_gate.py -vv
```

Expected: all tests pass; the existing logical content pin still changes on meaningful source changes and remains stable across SQLite resaves.

- [ ] **Step 6: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/l0_extract.py tests/test_l0_extract_v2.py \
  tests/test_graph_source_hash_release_gate.py
python3 tools/l0_extract.py --help
```

Expected: diff check exits 0 and help documents portable derived defaults.

---

### Task 5: Provenance-complete existing graph projections

**Files:**
- Modify: `tools/graph_build.py`
- Create: `tests/test_graph_projections_v2.py`

**Interfaces:**
- Consumes: graph schema version 2 and the shared artifact envelope.
- Produces `CALL_GRAPH.json` with `edges`, full `unresolved`, confidence, and reason.
- Produces `MODULE_CATALOG.json` with `depends_on`, `depended_by`, mechanical provenance, and legacy nullable L2 fields.
- Produces source/path-aware `TAINT_MAP.json`, annotated `SECURITY_SURFACE.json`, `ERROR_CATALOG.json`, and `DEAD_CODE.json`.
- Produces each projection through `atomic_write_json(..., validate_projection)`.

- [ ] **Step 1: Write RED projection tests**

```python
def test_call_graph_keeps_unresolved_details_and_provenance(graph_fixture):
    call_graph = graph_fixture["CALL_GRAPH.json"]
    assert call_graph["schema_name"] == "call_graph"
    assert call_graph["source_sha"] == graph_fixture.source_sha
    assert call_graph["unresolved"] == [{
        "from": "bulk_downloader/a.py::caller",
        "name": "dynamic_target",
        "reason": "missing",
        "confidence": 0.0,
    }]


def test_module_catalog_has_reverse_dependencies(graph_fixture):
    modules = graph_fixture["MODULE_CATALOG.json"]["modules"]
    assert modules["bulk_downloader/b.py"]["depended_by"] == [
        "bulk_downloader/a.py"
    ]
```

- [ ] **Step 2: Run projection tests and verify RED**

```bash
python3 -m pytest tests/test_graph_projections_v2.py \
  -k "unresolved_details or reverse_dependencies" -vv
```

Expected: current `CALL_GRAPH.json` lacks `unresolved` and current module records lack `depended_by`.

- [ ] **Step 3: Refactor graph loading and projection envelopes**

```python
@dataclass(frozen=True)
class GraphInput:
    source_sha: str
    input_hashes: dict[str, str]
    modules: dict[str, dict]
    functions: dict[str, dict]
    calls: tuple[tuple[str, str, dict], ...]
    contains: dict[str, tuple[str, ...]]


def projection(name, graph, payload):
    return {
        **make_envelope(name, 1, graph.source_sha, TOOL_VERSION,
                        graph.input_hashes),
        **payload,
    }
```

Update `content_hash()` to include edge `meta_json` and graph metadata rows in canonical order. Keep reading schema-1 fixture databases by supplying empty metadata defaults, so existing tests remain valid.

- [ ] **Step 4: Emit the six existing projections losslessly**

Keep `resolve_calls()` best-effort but return:

```python
{"from": src, "name": name, "reason": "ambiguous",
 "candidates": sorted(cands), "confidence": 0.0}
```

Resolved edges carry `confidence=1.0` only for an exact qualified/import resolution and `confidence=0.6` for a unique last-segment heuristic. Dead-code entries must state `reason`, `confidence`, and excluded dynamic/framework evidence. Taint paths remain heuristic and must never claim a source-to-sink proof without an explicit path.

- [ ] **Step 5: Run existing and new graph tests**

```bash
python3 -m pytest tests/test_graph_projections_v2.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_audit_promotion_wirings_533.py -vv
```

Expected: all graph tests pass and schema-1 fixture compatibility remains intact.

- [ ] **Step 6: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/graph_build.py tests/test_graph_projections_v2.py \
  tests/test_graph_source_hash_release_gate.py
git status --short -- tools/graph_build.py tests/test_graph_projections_v2.py
```

Expected: no whitespace errors and only intended graph/test paths appear.

---

### Task 6: Config-lineage, concurrency, and metrics projections

**Files:**
- Modify: `tools/graph_build.py`
- Modify: `tools/code_intelligence/schemas.py`
- Modify: `tests/test_graph_projections_v2.py`

**Interfaces:**
- Produces `CONFIG_LINEAGE.json` with `settings[key].readers`, `writers`, `effect`, `gui_exposure`, `runtime_tunable`, and provenance.
- Produces `CONCURRENCY_MAP.json` with `shared_state`, `locks`, `operations`, and confidence.
- Produces `METRICS_CATALOG.json` with normalized metric name, operation, call site, containing function, and confidence.
- Unknown human/L2 fields remain `null` with `confidence=0.0`; they are never guessed.

- [ ] **Step 1: Add RED tests for the three missing projections**

```python
def test_config_concurrency_and_metrics_are_emitted(graph_fixture):
    config = graph_fixture["CONFIG_LINEAGE.json"]["settings"]["LIMIT"]
    assert config["readers"] == ["bulk_downloader/a.py::sample"]
    assert config["writers"] == ["bulk_downloader/a.py::sample"]
    assert config["effect"] is None

    concurrency = graph_fixture["CONCURRENCY_MAP.json"]
    assert concurrency["locks"][0]["name"] == "state_lock"
    assert concurrency["locks"][0]["confidence"] == 0.7

    metrics = graph_fixture["METRICS_CATALOG.json"]["metrics"]
    assert metrics[0]["name"] == "sample.calls"
    assert metrics[0]["operation"] == "increment"
```

- [ ] **Step 2: Run the focused test and confirm RED**

```bash
python3 -m pytest tests/test_graph_projections_v2.py \
  -k "config_concurrency_and_metrics_are_emitted" -vv
```

Expected: `KeyError` or missing-file assertion for `CONFIG_LINEAGE.json`.

- [ ] **Step 3: Implement projection builders**

```python
def build_config_lineage(graph):
    settings = defaultdict(lambda: {"readers": set(), "writers": set()})
    for fid, fn in graph.functions.items():
        for item in fn["meta"].get("config_reads", []):
            settings[item["key"]]["readers"].add(fid)
        for item in fn["meta"].get("config_writes", []):
            settings[item["key"]]["writers"].add(fid)
    return {"settings": {
        key: {"readers": sorted(value["readers"]),
              "writers": sorted(value["writers"]),
              "effect": None, "gui_exposure": None,
              "runtime_tunable": None, "confidence": 0.5}
        for key, value in sorted(settings.items())
    }}
```

Implement equally small pure builders for concurrency operations and metric sites. Pure builders return payloads; `build()` supplies envelopes, validates, and writes them atomically.

- [ ] **Step 4: Verify deterministic regeneration**

```bash
python3 -m pytest tests/test_graph_projections_v2.py -vv
```

Expected: all projection tests pass, including two builds with byte-identical output after `generated_at` is fixed by the fixture clock.

- [ ] **Step 5: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/graph_build.py tools/code_intelligence/schemas.py \
  tests/test_graph_projections_v2.py
```

Expected: exit 0.

---

### Task 7: Projection check mode, interruption safety, and CLI compatibility

**Files:**
- Modify: `tools/graph_build.py`
- Modify: `tests/test_graph_projections_v2.py`
- Modify: `tests/test_graph_source_hash_release_gate.py`

**Interfaces:**
- Produces CLI `--root`, `--db`, `--outdir`, `--check`, `--check-hash`, and `--write-hash`.
- `--check` regenerates into a temporary directory, compares canonical content excluding `generated_at`, reports missing/stale/unexpected projections, and returns nonzero on drift.
- `--check` never overwrites the compared artifacts.
- Existing explicit `--db`, `--hash-pin`, `--check-hash`, and `--write-hash` invocations remain compatible.

- [ ] **Step 1: Add RED CLI tests**

```python
def test_check_mode_detects_projection_drift_without_overwrite(graph_cli_fixture):
    target = graph_cli_fixture.outdir / "CALL_GRAPH.json"
    original = target.read_bytes()
    value = json.loads(original)
    value["nodes"].append("fabricated")
    target.write_text(json.dumps(value), encoding="utf-8")
    changed = target.read_bytes()

    result = graph_cli_fixture.run("--check")

    assert result.returncode != 0
    assert "CALL_GRAPH.json: stale" in result.stdout
    assert target.read_bytes() == changed
```

- [ ] **Step 2: Run the CLI tests and confirm RED**

```bash
python3 -m pytest tests/test_graph_projections_v2.py \
  -k "check_mode_detects_projection_drift" -vv
```

Expected: `graph_build.py` rejects the unknown `--check` argument.

- [ ] **Step 3: Implement isolated regeneration and canonical comparison**

```python
def check_projections(db, outdir):
    with tempfile.TemporaryDirectory(prefix="bd-graph-check-") as scratch:
        build(db, scratch)
        differences = compare_artifact_dirs(Path(scratch), Path(outdir),
                                             expected=PROJECTION_FILENAMES)
    for path, state in differences:
        print(f"{path}: {state}")
    return 1 if differences else 0
```

The comparison validates both sides before hashing, excludes only `generated_at`, and treats missing or malformed artifacts as failure rather than pass.

- [ ] **Step 4: Run all graph foundation bands**

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py \
  tests/test_l0_extract_v2.py \
  tests/test_graph_projections_v2.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_audit_promotion_wirings_533.py -vv
```

Expected: all tests pass.

- [ ] **Step 5: Exercise help and a fixture regeneration**

```bash
python3 tools/l0_extract.py --help
python3 tools/graph_build.py --help
python3 tools/l0_extract.py --root . \
  --db /tmp/bd-ci-plan-graph.db
python3 tools/graph_build.py --db /tmp/bd-ci-plan-graph.db \
  --outdir /tmp/bd-ci-plan-projections
```

Expected: both help commands exit 0; fixture extraction reports zero parse errors; all nine projection files validate.

- [ ] **Step 6: Pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/code_intelligence tools/l0_extract.py \
  tools/graph_build.py tests/test_code_intelligence_foundation.py \
  tests/test_l0_extract_v2.py tests/test_graph_projections_v2.py \
  tests/test_graph_source_hash_release_gate.py
git status --short
```

Expected: no whitespace errors. Review the full status because the worktree intentionally contains earlier uncommitted hygiene changes.

---

### Task 8: Align the schema documentation with verified implementation

**Files:**
- Modify: `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md`
- Modify: `tests/test_graph_projections_v2.py`

**Interfaces:**
- Consumes: the passing schema validators and all nine generated projections.
- Produces: documentation whose examples use the actual `schema_name`, `schema_version`, `source_sha`, `tool_version`, `input_hashes`, and `generated_at` envelope.
- Does not mark analysis, governance, L2/L3 review, advanced knowledge, or external static-KB promotion complete.

- [ ] **Step 1: Add a RED documentation contract test**

```python
def test_schema_document_matches_implemented_projection_set():
    text = (ROOT / "project-knowledge" /
            "CODE_INTELLIGENCE_SCHEMAS.md").read_text(encoding="utf-8")
    for name in PROJECTION_FILENAMES:
        assert f"`{name}`" in text
    assert "`schema_name`" in text
    assert "`source_sha`" in text
    assert "verified-against: v3.66.817" in text
```

- [ ] **Step 2: Run the documentation test and verify RED**

```bash
python3 -m pytest tests/test_graph_projections_v2.py \
  -k "schema_document_matches" -vv
```

Expected: failure because the current document describes the intended old envelope and is marked verified against v3.66.805.

- [ ] **Step 3: Update only verified schema claims**

Replace the warning about unmet conventions with the implemented envelope, add `METRICS_CATALOG.json`, and update each projection example from the validator constants. Explicitly label reachability, L2 fields, contract execution, audit completion, and advanced knowledge as work governed by the sibling plans:

```markdown
> Implemented foundation scope: deterministic graph extraction and nine
> schema-validated projections. Runtime analysis, review dispositions, and
> external static-KB synchronization require their named downstream gates and
> are not implied by successful graph generation.
```

- [ ] **Step 4: Run the foundation suite and documentation/link checks**

```bash
python3 -m pytest tests/test_code_intelligence_foundation.py \
  tests/test_l0_extract_v2.py tests/test_graph_projections_v2.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_audit_promotion_wirings_533.py -vv
python3 tools/kb_link_validator.py --root .
```

Expected: all tests pass and the link validator reports zero broken links.

- [ ] **Step 5: Final foundation pre-commit checkpoint (do not commit)**

```bash
git diff --check -- tools/code_intelligence tools/l0_extract.py \
  tools/graph_build.py tests/test_code_intelligence_foundation.py \
  tests/test_l0_extract_v2.py tests/test_graph_projections_v2.py \
  tests/test_graph_source_hash_release_gate.py \
  project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md
git diff --stat
git status --short
```

Expected: clean diff check. Stop for review with the entire worktree still uncommitted and unmerged.
