# Generated-Artifact Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated-artifact regeneration a single deterministic pre-review, CI, and release workflow so graph and GUI-parity drift cannot recur unnoticed.

**Architecture:** Repair the existing canonical `bd-regen-order` command instead of adding another generator. CI and the release shell wrapper invoke it, tracked-output drift fails closed, and repository policy requires it before review packaging or controller suites.

**Tech Stack:** Python 3.12, Bash, pytest, GitHub Actions.

## Global Constraints

- Preserve the exact order: GUI parity, route index, endpoint catalog, dependency graph, function index, pin index, route-count gate.
- Never auto-refresh route-map, import-graph, or reachability intent baselines.
- Prefer `<repo>/.venv/bin/python`, then `<repo>/venv/bin/python`, then the running interpreter only when the repository environments are absent.
- Do not edit the SHA-pinned `tools/build_release.py`; integrate through `scripts/build_release.sh`.
- Do not commit, stage, merge, push, deploy, or dispatch an independent reviewer.
- Use RED-first TDD and prove the final generator is byte-idempotent.

---

### Task 1: Canonical generated-artifact workflow

**Files:**
- Modify: `toolchain/bin/bd-regen-order`
- Modify: `project-knowledge/bd-regen-order`
- Modify: `scripts/build_release.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `CLAUDE.md`
- Create: `tests/test_generated_artifact_workflow.py`

**Interfaces:**
- Produces: `repo_root(script_file: str) -> str`
- Produces: `python_for(work: str) -> str`
- Preserves: `CHAIN` and `VERIFY` ordered command contracts
- Consumed by: local pre-review command, CI generated-artifact step, release wrapper

- [ ] **Step 1: Write the failing workflow tests**

Create tests that assert:

```python
assert labels == [
    "gui_parity",
    "ROUTE_INDEX",
    "ENDPOINT_CATALOG",
    "DEPENDENCY_GRAPH",
    "FUNCTION_INDEX",
    "PIN_INDEX",
]
assert repo_root(tool_path) == str(REPO_ROOT)
assert python_for(tmp_repo) == str(tmp_repo / ".venv" / "bin" / "python")
```

Also assert that CI runs the canonical command and fails on tracked generated
diffs, the release wrapper invokes the canonical command before
`tools/build_release.py`, `CLAUDE.md` requires the command before review, and
the toolchain/project-knowledge copies remain byte-identical.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_generated_artifact_workflow.py -q
```

Expected: failures for the hard-coded `/home/claude/work` default, missing
Python-selection interface, and absent CI/release/policy wiring.

- [ ] **Step 3: Repair the canonical command**

Implement `repo_root()` from `toolchain/bin/bd-regen-order`'s location and
`python_for()` with `.venv`, `venv`, then `sys.executable` precedence. Use the
selected interpreter for every generator and reachability subprocess. Keep
`--work` authoritative when provided and preserve all declaration flags.

- [ ] **Step 4: Wire fail-fast entry points**

Add a CI step that runs:

```bash
python toolchain/bin/bd-regen-order --work "$GITHUB_WORKSPACE"
git diff --exit-code -- \
  ROUTE_INDEX.json ENDPOINT_CATALOG.md DEPENDENCY_GRAPH.json \
  DEPENDENCY_GRAPH.md FUNCTION_INDEX.md PIN_INDEX.json
```

Add the equivalent canonical regeneration before the release wrapper invokes
the pinned Python release builder. Update `CLAUDE.md` to require this exact
pre-review command:

```bash
.venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

- [ ] **Step 5: Mirror and run focused tests GREEN**

Keep `project-knowledge/bd-regen-order` byte-identical to the canonical
toolchain copy. Run:

```bash
.venv/bin/python -m pytest \
  tests/test_generated_artifact_workflow.py \
  tests/test_decomp_regen.py \
  tests/test_dependency_graph_in_sync.py \
  tests/test_function_index_in_sync.py \
  tests/test_endpoint_catalog_in_sync.py \
  tests/test_pin_index_in_sync.py \
  tests/test_route_index_in_sync.py -q
```

Expected: all pass.

- [ ] **Step 6: Prove byte-idempotence and compatibility**

Hash the six tracked generated outputs, run the canonical command, hash them,
run it again, and hash them again. Require the post-first and post-second hash
sets to match exactly. Then run the standing Task 5 controller suite and record
the commands, exit codes, and counts in the task report.

- [ ] **Step 7: Stop before review**

Freeze an uncommitted review package covering only the files in this task.
Do not dispatch a reviewer until the operator explicitly releases the hold.
