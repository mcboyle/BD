# Defect Scanner Precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the 44 audited false positives without changing the `defect_DP_total=2314` ratchet ceiling or weakening positive controls.

**Architecture:** Improve seven detectors with narrow AST/data-flow rules. Keep syntactically ambiguous DP-13 cleanup cases visible to direct scans, but filter exact reviewed candidates during tree scans through fail-closed path-plus-AST fingerprints.

**Tech Stack:** Python 3.11+, stdlib `ast`, `hashlib`, and `json`; pytest; Git.

## Global Constraints

- `/root/.bd_metrics_baseline.json` must remain byte-for-byte unchanged.
- `defect_DP_total` remains `2314`; `coupling_ratio` remains `0.45`.
- The active scanner is `toolchain/bin/bd-defect-scan`.
- `project-knowledge/bd-defect-scan` must be byte-identical to the active scanner.
- `tools/defect_patterns.py` retains its distinct CLI but must carry equivalent detector behavior.
- Do not regenerate graph, GUI-parity, route, PIN, endpoint, or unrelated knowledge artifacts.
- New behavior follows red/green TDD: run each focused test before and after implementation.
- A changed suppressed AST must reappear automatically.

---

### Task 1: Correct DP-03, DP-08, and DP-10

**Files:**
- Create: `tests/test_defect_scan_precision.py`
- Modify: `toolchain/bin/bd-defect-scan`

**Interfaces:**
- Consumes: existing `scan_file(path: str, src: str, only: set[str] | None) -> list[dict]`
- Produces: `_scope_nodes(node)`, `_joinedstr_skeleton(node)`, and corrected `dp03`, `dp08`, `dp10`

- [ ] **Step 1: Create the scanner loader and failing actual-file tests**

Load the extensionless scanner with `importlib.machinery.SourceFileLoader`.
Add helpers:

```python
ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "toolchain" / "bin" / "bd-defect-scan"

def _load_scanner():
    loader = importlib.machinery.SourceFileLoader("bd_defect_scan_precision", str(SCANNER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

def _hits(scanner, relative, dp):
    source = (ROOT / relative).read_text(encoding="utf-8")
    return scanner.scan_file(relative, source, only={dp})
```

Assert zero hits for:

- DP-03: `tools/graph_build.py`
- DP-08: `tools/code_intelligence/adapters.py`
- DP-10: `tools/coverage_map.py` and `tools/l0_extract.py`

- [ ] **Step 2: Add synthetic positive controls**

Use literal source strings and assert exactly one hit:

```python
def post_conversion_bounds(v):
    n = float(v)
    if n < 0 or n > 1:
        raise ValueError
    return n
```

```python
_SECRET_QUERY_KEYS = {"token", "code"}
_SECRET_KV_KEYS = {"token"}
```

```python
def query(connection, table):
    return connection.execute(f"SELECT * FROM {table}")
```

Also assert the allowlisted SQL-table control is silent.

- [ ] **Step 3: Run the focused tests and record RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_defect_scan_precision.py \
  -k 'dp03 or dp08 or dp10'
```

Expected: actual-file negative controls fail on the pre-fix hits while synthetic positives pass.

- [ ] **Step 4: Implement the smallest detector corrections**

For DP-03:

- walk each function without descending into nested functions/classes;
- pair assignments `name = float(...)` with their statement line;
- consider only comparisons of the same `name` after conversion;
- treat same-name `isfinite`/`isnan`/`isinf` calls before the comparison as guards.

For DP-08:

- return immediately for irrelevant files;
- inspect module-level assignments only;
- classify names into equivalent redaction surfaces (`QUERY`/`URL` and `KV`/`BARE`/`FRAGMENT`);
- compare only different equivalent surfaces, never token representation sets.

For DP-10:

- render constant f-string parts plus `__BD_EXPR__` placeholders;
- match only `FROM|JOIN|INTO|UPDATE|TABLE` immediately followed by a placeholder;
- collect only interpolated names occupying those identifier positions;
- preserve the existing allowlist behavior.

- [ ] **Step 5: Run GREEN and commit**

Run the command from Step 3. Expected: all selected tests pass.

Commit only Task 1 files:

```bash
git add tests/test_defect_scan_precision.py toolchain/bin/bd-defect-scan
git commit -m "fix: improve numeric redaction and SQL defect precision"
```

---

### Task 2: Correct DP-06 and DP-13

**Files:**
- Modify: `tests/test_defect_scan_precision.py`
- Modify: `toolchain/bin/bd-defect-scan`

**Interfaces:**
- Consumes: Task 1 scanner loader and `_scope_nodes`
- Produces: lexical binding resolution for DP-06 and logger-shaped handler classification for DP-13

- [ ] **Step 1: Add failing DP-06 tests**

Assert zero DP-06 hits in:

- `tools/code_intelligence/fuzz_service.py`
- `tools/code_intelligence/oracle_adapters.py`
- `tools/code_intelligence/reachability_service.py`
- `tools/code_intelligence/semantic_service.py`

Add two synthetic controls:

```python
def broken():
    return getattr(missing_name, "value", None)
```

must emit one hit, while a binding in a sibling function must not satisfy `missing_name`.

- [ ] **Step 2: Add failing DP-13 tests**

Assert no DP-13 hit at handlers whose body performs non-logging recovery in:

- `tools/code_intelligence/fuzz_adapters.py`
- `tools/code_intelligence/fuzz_service.py`
- `tools/code_intelligence/oracle_service.py`
- `tools/code_intelligence/reachability_service.py`
- `tools/code_intelligence/schemas.py`
- `tools/graph_build.py`

Add synthetic positive controls for:

```python
try:
    feature()
except Exception:
    pass
```

and:

```python
try:
    feature()
except Exception:
    logger.exception("feature failed")
```

Both must remain candidates.

- [ ] **Step 3: Run focused tests and record RED**

```bash
.venv/bin/python -m pytest -q \
  tests/test_defect_scan_precision.py \
  -k 'dp06 or dp13'
```

Expected: actual-file negative controls fail; positive controls pass.

- [ ] **Step 4: Implement lexical DP-06 resolution**

Build a per-scope binding model including:

- all function arguments;
- imports;
- assignment, annotated-assignment, named-expression, loop, with, and exception targets;
- local function/class definitions;
- builtins and enclosing lexical/module bindings;
- `global` and `nonlocal` declarations.

Never use a file-wide union. Do not descend into sibling or nested scopes while collecting a scope's local bindings.

- [ ] **Step 5: Implement narrow DP-13 classification**

An expression call counts as logging only when:

- its method is `debug`, `info`, `warning`, `warn`, `error`, `exception`, or `critical`; and
- its receiver is `logging`, `log`, `logger`, `_logger`, or ends in `logger`.

Exclude pass-only exception predicates when their `try` has an `else` whose direct body returns or raises. Non-logging calls in a handler are recovery actions, not pass/log-only handling.

- [ ] **Step 6: Run GREEN and commit**

Run Step 3, then the full precision file:

```bash
.venv/bin/python -m pytest -q tests/test_defect_scan_precision.py
```

Commit:

```bash
git add tests/test_defect_scan_precision.py toolchain/bin/bd-defect-scan
git commit -m "fix: resolve defect candidates by lexical semantics"
```

---

### Task 3: Correct DP-15 and DP-18 and add reviewed DP-13 fingerprints

**Files:**
- Modify: `tests/test_defect_scan_precision.py`
- Modify: `toolchain/bin/bd-defect-scan`
- Create: `project-knowledge/DEFECT_PATTERN_SUPPRESSIONS.json`

**Interfaces:**
- Produces:
  - `_finding_fingerprint(dp: str, path: str, node: ast.AST) -> str`
  - `_load_suppressions(root: str) -> set[tuple[str, str, str]]`
  - `_apply_suppressions(root: str, results: dict) -> dict`

- [ ] **Step 1: Add failing DP-15 and DP-18 tests**

Assert zero DP-15 hits in:

- `tools/code_intelligence/reachability_service.py`
- `tools/code_intelligence/semantic_service.py`
- `tools/differential_oracle.py`

Assert zero DP-18 hits in `tools/code_intelligence/reachability_service.py`.

Positive DP-15 source must resolve the same literal `settings.json` through `$BD_HOME` in one function and CWD in another. Positive DP-18 source must contain nested unbounded iteration over `url.split("/")` and then its characters.

- [ ] **Step 2: Run DP-15/DP-18 tests and record RED**

```bash
.venv/bin/python -m pytest -q \
  tests/test_defect_scan_precision.py \
  -k 'dp15 or dp18'
```

- [ ] **Step 3: Implement DP-15 and DP-18 corrections**

DP-15 must:

- classify only returned default branches using `BD_HOME`, `getcwd`/`Path.cwd`, or a relative literal passed to `abspath`;
- ignore `abspath(parameter)`;
- group by the same returned literal artifact name;
- emit only when the strategies differ.

DP-18 must:

- trace simple assignment and `.split(...)` propagation from string-like parameters named with `url`, `path`, `segment`, `char`, or `digit`;
- require both nested iterables to be derived from that data;
- treat literal slicing and explicit iteration/count/depth caps as bounded;
- avoid nested scopes.

- [ ] **Step 4: Add fail-closed suppression tests**

Add tests proving:

- exact `(dp, path, AST fingerprint)` ledger matches are removed by tree-scan filtering;
- changing the handler AST invalidates the fingerprint and returns the finding;
- unreadable, malformed, duplicate, unknown-DP, or escaping-path ledger entries raise a scanner error and cannot silently mean “no suppressions.”

The ledger schema is:

```json
{
  "schema": "bd-defect-suppressions/v1",
  "entries": [
    {
      "dp": "DP-13",
      "path": "tools/example.py",
      "fingerprint": "<64 lowercase hex>",
      "rationale": "reviewed best-effort cleanup"
    }
  ]
}
```

- [ ] **Step 5: Implement fingerprints and seed only audited residuals**

Fingerprint:

```python
hashlib.sha256(
    (dp + "\0" + path + "\0" + ast.dump(node, include_attributes=False)).encode("utf-8")
).hexdigest()
```

Attach it to DP-13 findings. Load the ledger once per tree scan, validate every field, reject duplicates, and filter exact matches only. Seed entries only for residual audited bare-pass cleanup handlers still reported after Tasks 1–3; each rationale names the cleanup contract.

- [ ] **Step 6: Run GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_defect_scan_precision.py
```

Commit:

```bash
git add tests/test_defect_scan_precision.py \
  toolchain/bin/bd-defect-scan \
  project-knowledge/DEFECT_PATTERN_SUPPRESSIONS.json
git commit -m "fix: calibrate path and cleanup defect triage"
```

---

### Task 4: Synchronize mirrors and prove the ratchet

**Files:**
- Modify: `project-knowledge/bd-defect-scan`
- Modify: `tools/defect_patterns.py`
- Modify: `project-knowledge/STATIC_KB_MANIFEST.json`
- Modify: `tests/test_defect_scan_precision.py`

**Interfaces:**
- Consumes: all corrected active detector functions and suppression schema
- Produces: synchronized deployed scanners and final release-gate evidence

- [ ] **Step 1: Add mirror parity behavior tests**

Load all three scanner frontends and run the same synthetic positive and negative matrix through `scan_file`. Assert identical findings after removing CLI-only metadata. Also assert:

```python
(ROOT / "toolchain/bin/bd-defect-scan").read_bytes() == \
    (ROOT / "project-knowledge/bd-defect-scan").read_bytes()
```

- [ ] **Step 2: Run parity tests and record RED**

```bash
.venv/bin/python -m pytest -q \
  tests/test_defect_scan_precision.py \
  -k 'mirror or parity'
```

- [ ] **Step 3: Synchronize only the intended files**

Copy the active scanner byte-for-byte to `project-knowledge/bd-defect-scan`.
Apply the same detector-core and fingerprint-filter behavior to
`tools/defect_patterns.py` without replacing its CLI.

Refresh only the `bd-defect-scan` and new suppression-ledger entries in
`project-knowledge/STATIC_KB_MANIFEST.json`; preserve all unrelated manifest
entries byte-for-byte.

- [ ] **Step 4: Run focused and scanner gates**

```bash
.venv/bin/python -m pytest -q tests/test_defect_scan_precision.py
BDTOOLS_CACHE=0 .venv/bin/python toolchain/bin/bd-defect-scan \
  --scan /root/BulkDownloader-main --json > /tmp/bd-defects-818.json
.venv/bin/python toolchain/bin/bd-defect-scan --selftest
```

Assert from `/tmp/bd-defects-818.json`:

- no finding has `precision == "error"`;
- `total_findings <= 2314`;
- all 44 audited candidates are absent from the filtered tree result.

- [ ] **Step 5: Prove the baseline did not move and run the ratchet**

Record the baseline SHA before implementation and compare it now:

```bash
sha256sum /root/.bd_metrics_baseline.json
.venv/bin/python toolchain/bin/bd-ratchet \
  --check --tree /root/BulkDownloader-main
```

Expected: the original baseline SHA, exit zero, and no regressed metric.

- [ ] **Step 6: Run the expanded suite and commit**

```bash
.venv/bin/python -m pytest -q \
  tests/test_defect_scan_precision.py \
  tests/test_code_intelligence_schemas.py \
  tests/test_code_intelligence_graph.py \
  tests/test_coverage_map_frontend.py \
  tests/test_differential_oracle_frontend.py \
  tests/test_fuzz_harness_frontend.py \
  tests/test_invariant_probe_frontend.py \
  tests/test_reachability_frontend.py \
  tests/test_semantic_diff_frontend.py
git diff --check
```

Expected: the previously established 719 tests plus the new precision tests all pass.

Commit:

```bash
git add project-knowledge/bd-defect-scan \
  tools/defect_patterns.py \
  project-knowledge/STATIC_KB_MANIFEST.json \
  tests/test_defect_scan_precision.py
git commit -m "test: lock defect scanner precision and mirror parity"
```

---

### Task 5: Resume the v3.66.818 release cut

**Files:**
- Modify only files written by `project-knowledge/bd-cut`
- Create: `/tmp/bd-release-818/BulkDownloader_v3_66_818.zip`

**Interfaces:**
- Consumes: green ratchet and unchanged baseline
- Produces: verified v3.66.818 release commit and ZIP

- [ ] **Step 1: Re-run the guarded cut**

```bash
.venv/bin/python project-knowledge/bd-cut \
  --version 3.66.818 \
  --baseline /tmp/BulkDownloader_v3_66_816.zip \
  --changelog "Code-intelligence foundation and premerge hardening" \
  --work /root/BulkDownloader-main \
  --out /tmp/bd-release-818 \
  --skip-fe
```

Do not use `--no-gate` and do not run `bd-ratchet --baseline`.

- [ ] **Step 2: Verify the release output**

Run the release inventory, version consistency, archive integrity, and gate
verification commands emitted by `bd-cut`. The extracted-ZIP full-suite gate
remains waived unless the operator explicitly requests it again.

- [ ] **Step 3: Commit, push, and open the release PR**

Review the exact generated diff, exclude `codex-wip.patch` and `state/`, commit
only intended release files, push `release/3.66.818`, and open a PR against
`main`.

- [ ] **Step 4: Merge only after green CI/review**

Verify all required GitHub checks and review state. Merge only after they are
green. Preserve the release branch until merged-main SHA and archive SHA are
recorded.

- [ ] **Step 5: Transfer the verified ZIP to Stash**

Use the saved `stash` SFTP/Plink configuration without exposing credentials.
Transfer the exact verified `BulkDownloader_v3_66_818.zip` to
`/home/mboyle/`. Stop for the operator to install it; after confirmation run:

```bash
cd /home/mboyle/BulkDownloader
./capture.sh --workers=60 --summary
```
