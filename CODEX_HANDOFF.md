# Codex Handoff

Date: 2026-07-25
Primary repository: `/root/BulkDownloader-main` in WSL Ubuntu 24.04
Policy: keep all work uncommitted, unmerged, unstaged, and unpushed until the
user explicitly authorizes otherwise.

## Current goal

Complete the BulkDownloader code-intelligence/audit program in the ordered SDD
plans under `docs/superpowers/plans/`.

Current progress:

- Foundation/graph Tasks 1-8: complete.
- Analysis frontend Tasks 1-3: complete.
- Analysis Task 4 (`reachability.py`): paused at a safe frozen checkpoint.
- Analysis Tasks 5-7: pending.
- Governance/gate Tasks 1-8: pending.
- Audit/knowledge/hygiene/static-KB Tasks 1-11: pending.

Eleven of 34 task groups are complete. Task 4 is implemented but is **not**
complete because one reviewed P2 remains.

## Repository state that must be preserved

- HEAD: `d095a639cc33d53c331920264ca231056035c1c6`
- Pre-existing staged logical tree:
  `fd78d3bcd5f4132b4cf4cddc7a6becb9f2230c93`
- Current index byte SHA-256:
  `00bb10004f9da43bfba94a12dfe2b6149ced7233f318d6a9fde95982f8496e89`

During a nominally read-only review, Git refreshed the index cache bytes from
the earlier `f3321ef8...` hash to `00bb1000...`. The staged logical tree and
working files remained intact. No byte-exact backup was found. **Do not reset,
checkout, overwrite, rebuild, stage, or otherwise replace the real index.**
Use `GIT_OPTIONAL_LOCKS=0` for live Git reads.

The repository contains extensive pre-existing documentation-hygiene changes.
They belong to the user and must be preserved.

## Files changed

### Analysis Task 3 â€” complete and independently approved

- `/root/BulkDownloader-main/tools/code_intelligence/semantic_service.py`
- `/root/BulkDownloader-main/tools/semantic_diff.py`
- `/root/BulkDownloader-main/tests/test_semantic_diff_frontend.py`
- `/root/BulkDownloader-main/DEPENDENCY_GRAPH.json`
- `/root/BulkDownloader-main/DEPENDENCY_GRAPH.md`

Task metadata/evidence:

- `.superpowers/sdd/analysis-task-3-brief.md`
- `.superpowers/sdd/analysis-task-3-report.md`
- `.superpowers/sdd/review-analysis-task-3.diff`
- `.superpowers/sdd/review-analysis-task-3-derived.diff`

Frozen Task 3 package hashes:

- Core:
  `969224f2b7bb0da7cc94e6a1887b0b563ee543c719dd3b4bb53ecbb02405fdf1`
- Derived graph:
  `4f95909b48e0963b785d5d5f4c7e3643f86e996ba5db20e7337fc73d5c315695`

### Analysis Task 4 â€” paused at safe checkpoint

- `/root/BulkDownloader-main/tools/code_intelligence/reachability_service.py`
- `/root/BulkDownloader-main/tools/reachability.py`
- `/root/BulkDownloader-main/tests/test_reachability_frontend.py`
- `/root/BulkDownloader-main/DEPENDENCY_GRAPH.json`
- `/root/BulkDownloader-main/DEPENDENCY_GRAPH.md`

Task metadata/evidence:

- `.superpowers/sdd/analysis-task-4-brief.md`
- `.superpowers/sdd/analysis-task-4-checkpoint.md`
- `.superpowers/sdd/review-analysis-task-4.diff`
- `.superpowers/sdd/review-analysis-task-4-derived.diff`
- `.superpowers/sdd/progress.md`
- Windows mirror:
  `C:\Users\Administrator\Documents\Codex\2026-07-20\how-to-give-you-full-access\work\sdd-progress.md`

Frozen Task 4 package hashes:

- Core:
  `6b3d11475a9ccc1f6b00cb13cd2d85f73e40c448d25cab435a9eb5af92d04fc4`
- Derived graph:
  `3b2c2b7783072ee13dacd3293903c0019b707c228a8a2b8a23f8c702bfe97813`

## Important decisions made

1. Preserve all existing dirty and staged hygiene work. Do not use destructive
   Git operations.
2. Keep task work in immutable, baseline-relative review packages. Do not
   commit, merge, push, or stage.
3. Treat unknown or ambiguous semantic facts as unknown/fail-closed rather than
   inventing confidence.
4. Task 3 models ordered Python scope execution, `global`/`nonlocal`,
   comprehension walruses, CPython 3.12 header/decorator order, descriptor
   composition, receiver provenance, and post-decoration callability.
5. Task 3 uses explicit file/tree/AST/artifact/semantic-work bounds, strict
   artifact validation, secret redaction, tracked-tree fail-closed behavior,
   and atomic path-identity-checked output.
6. Task 4 preserves these evidence categories separately:
   `auth_probe`, `auth_gate_facts`, `operator_wiring`, `navigation`,
   `call_paths`, and `deferrals`.
7. Existing endpoint and navigation tools are adapters/evidence sources, not
   proof of a route's privilege class.
8. Do not probe unsafe mutating or unresolved parameterized Flask routes merely
   because probing occurs in a child process; a child is not a sandbox.
9. Authenticated classification requires a real unauthenticated denial/delta.
   Dual denial does not prove `internal`, and dual 404 does not prove
   `unreachable`.
10. Redirect evidence is retained only for safe fixed auth landing paths;
    dynamic/signed/credential-bearing paths are redacted.
11. Task 4 was stopped at the user's requested safe point. No product changes
    are authorized beyond the frozen checkpoint until the user resumes it.
12. CodeRabbit CLI 0.7.0 is installed and authenticated as `mcboyle`, but the
    final Task 3 review attempts repeatedly emitted no output and timed out.
    Do not claim a CodeRabbit issue count from those attempts. Task 3 instead
    received a successful independent manual approval.
13. The extracted-ZIP test-suite release gate remains waived unless the user
    explicitly requests it again.

## Commands already run

Commands used the repository virtual environment:
`/root/BulkDownloader-main/.venv/bin/python`.

### Frozen-package creation

```bash
cd /root/BulkDownloader-main
.superpowers/sdd/uncommitted-review-package.sh package analysis-task-3 \
  tools/code_intelligence/semantic_service.py \
  tools/semantic_diff.py \
  tests/test_semantic_diff_frontend.py
.superpowers/sdd/uncommitted-review-package.sh package analysis-task-3-derived \
  DEPENDENCY_GRAPH.json DEPENDENCY_GRAPH.md
.superpowers/sdd/uncommitted-review-package.sh package analysis-task-4 \
  tools/code_intelligence/reachability_service.py \
  tools/reachability.py \
  tests/test_reachability_frontend.py
.superpowers/sdd/uncommitted-review-package.sh package analysis-task-4-derived \
  DEPENDENCY_GRAPH.json DEPENDENCY_GRAPH.md
```

### Task 3 focused and compatibility tests

```bash
.venv/bin/python -m pytest tests/test_semantic_diff_frontend.py -q

.venv/bin/python -m pytest \
  tests/test_code_intelligence_foundation.py \
  tests/test_graph_projections_v2.py \
  tests/test_l0_extract_v2.py \
  tests/test_code_intelligence_adapters.py \
  tests/test_coverage_map_frontend.py \
  tests/test_semantic_diff_frontend.py \
  tests/test_dependency_graph_in_sync.py \
  tests/test_import_graph_no_new_edges.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_audit_promotion_wirings_533.py \
  tests/test_release_hygiene_gates.py \
  tests/test_build_release_f02.py -q
```

### Task 3 real-tree and graph verification

```bash
.venv/bin/python tools/semantic_diff.py \
  --before-tree . --after-tree . \
  --out /tmp/analysis-task3-final-semantic.json --json
.venv/bin/python tools/dependency_graph.py --check
.venv/bin/python tools/decomp/import_graph_gate.py --check
.venv/bin/python -m py_compile \
  tools/code_intelligence/semantic_service.py tools/semantic_diff.py
```

### Task 4 focused and compatibility tests

```bash
.venv/bin/python -m pytest tests/test_reachability_frontend.py -q

.venv/bin/python -m pytest \
  tests/test_code_intelligence_foundation.py \
  tests/test_graph_projections_v2.py \
  tests/test_l0_extract_v2.py \
  tests/test_code_intelligence_adapters.py \
  tests/test_coverage_map_frontend.py \
  tests/test_semantic_diff_frontend.py \
  tests/test_reachability_frontend.py \
  tests/test_dependency_graph_in_sync.py \
  tests/test_import_graph_no_new_edges.py \
  tests/test_graph_source_hash_release_gate.py \
  tests/test_audit_promotion_wirings_533.py \
  tests/test_release_hygiene_gates.py \
  tests/test_build_release_f02.py -q

.venv/bin/python tools/dependency_graph.py --check
.venv/bin/python tools/decomp/import_graph_gate.py --check
.venv/bin/python -m py_compile \
  tools/code_intelligence/reachability_service.py tools/reachability.py
.venv/bin/python tools/reachability.py --help
```

### Integrity checks

```bash
GIT_OPTIONAL_LOCKS=0 git rev-parse HEAD
GIT_OPTIONAL_LOCKS=0 git write-tree
sha256sum \
  /root/bd-audit-20260722-125717-local/.git/worktrees/BulkDownloader-main/index
```

## Tests passed

### Task 3

- Focused semantic frontend: `90 passed`.
- Standing compatibility controller: `521 passed`.
- Independent disposable-package review suite: `104 passed`.
- Real self-diff: 24,480 function locations per side, zero semantic changes.
- Real extraction: 24,480 functions.
- Exhaustive CPython descriptor probe: all 127
  `staticmethod`/`classmethod` stacks through depth six matched.
- Dependency graph check: pass, 1,366 edges.
- Import graph gate: pass, 1,366 edges.
- Compilation and scoped whitespace checks: pass.
- Independent final review: approved.

### Task 4 checkpoint

- Focused reachability frontend: `26 passed`.
- Standing compatibility controller: `547 passed`.
- Dependency graph check: pass, 1,366 edges.
- Import graph gate: pass, 1,366 edges.
- Compilation and CLI help: pass.

## Tests failed or blocked

The legacy four-suite reachability selection has three failures. All three
reproduce identically on a disposable pristine HEAD, so they are baseline
failures and are not attributed to Task 4:

- `test_every_dark_endpoint_is_classified`: 101 unexplained dark endpoints.
- `test_dark_count_is_ratcheted`: missing
  `reports/endpoint_reachability.json`.
- `test_dark_ratchet_fell`: the same missing ledger.

These failures are **not** accepted as passes; they are recorded baseline debt.

CodeRabbit's final Task 3 automated reviews timed out without emitting review
events. The issue count is unknown.

## Remaining TODOs

### Resume and finish Analysis Task 4

1. Add a RED regression using a custom sensitive exception class name such as
   `Bearer_ultra_private_value`.
2. Route exception types through a small bounded allowlist/sanitizer.
3. Emit generic `ProbeError` for unknown, invalid, or sensitive exception class
   names in artifacts and `CheckResult` evidence.
4. Rerun the 26-test focused suite, controller, compilation, CLI help, graph
   checks, secret/path/resource probes, and scoped whitespace checks.
5. Re-freeze both Task 4 packages; their hashes must change intentionally.
6. Obtain a new independent review of the exact final packages.
7. Write the final Task 4 report and mark Task 4 complete only after approval.

### Continue the wider program

- Analysis Tasks 5-7.
- Governance/gate Tasks 1-8.
- Audit/knowledge/hygiene/static-KB Tasks 1-11.
- The audit phase includes the risk-routed L2/L3 review and 445-row corpus
  disposition work.
- Static-KB replacement and external re-paste remain operator-gated.

## Exact next command to continue

Run this first to verify that the frozen safe-stop packages and focused Task 4
baseline are intact before making the P2 fix:

```powershell
wsl -e bash -lc 'cd /root/BulkDownloader-main && sha256sum .superpowers/sdd/review-analysis-task-4.diff .superpowers/sdd/review-analysis-task-4-derived.diff && .venv/bin/python -m pytest tests/test_reachability_frontend.py -q'
```

Expected package hashes before resuming edits:

```text
6b3d11475a9ccc1f6b00cb13cd2d85f73e40c448d25cab435a9eb5af92d04fc4
3b2c2b7783072ee13dacd3293903c0019b707c228a8a2b8a23f8c702bfe97813
```

Expected focused result: `26 passed`.
