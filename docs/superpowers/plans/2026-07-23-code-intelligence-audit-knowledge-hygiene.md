# Code-Intelligence Audit, Advanced Knowledge, Hygiene, and Static-KB Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete source-bound L2/L3 audit coverage, render and fact-check advanced project knowledge v2, finish documentation hygiene, and produce one verified static-KB replacement whose external paste and pin remain operator-gated.

**Architecture:** This plan begins only after the unified code-intelligence platform and all standalone tools are green. It freezes the production-file set, generates one canonical graph and deterministic risk order, leases non-overlapping audit slices through `bd-review-next`, merges evidence into `REVIEW_STATE.json`, and renders advanced knowledge from the resulting source-bound artifacts. Documentation and tracker changes occur after production audit closure; the static KB is staged once from the final tracked content, while external paste and pin advancement are explicitly separate operator actions.

**Tech Stack:** Python 3 standard library, SQLite, pytest, BulkDownloader code-intelligence tools, deterministic JSON and Markdown, Git read-only/checkpoint commands, ZIP/sha256 utilities, optional isolated `detect-secrets`, and `bd-kb-sync`.

**Prerequisite plans:** Complete and independently verify
`docs/superpowers/plans/2026-07-23-code-intelligence-foundation-graph.md`,
`docs/superpowers/plans/2026-07-23-code-intelligence-analysis-frontends.md`, and
`docs/superpowers/plans/2026-07-23-code-intelligence-governance-gates.md` before
starting Task 1. This plan consumes their stable interfaces and does not duplicate
their platform implementation work.

## Global Constraints

- Python standard library is the required runtime baseline. Optional packages such as `libcst`, `hypothesis`, `radon`, `bandit`, and `vulture` may enhance isolated audit runs but cannot be required by normal release gates.
- Every durable artifact carries schema name and version, tracked-tree source SHA, tool version, deterministic input hashes, and a generation timestamp separated from content used for deterministic comparisons.
- Durable writes are validate-then-atomically-replace. A failed run must not leave a plausible partial artifact.
- All paths are explicitly supplied or derived from a discovered repository root. `/home/claude`, `/root`, and workstation-specific paths are not defaults in canonical interfaces.
- Outputs exclude secret values, credentials, cookies, authorization headers, signed queries, and raw captured bodies.
- Advisory findings and release-blocking failures are distinct result states.
- Existing CLI behavior remains available through compatibility wrappers or adapters.
- New behavior follows RED -> GREEN -> refactor. Each test must be observed failing for the intended missing behavior before implementation.
- No production service behavior changes, automatic fixes, automatic finding promotion, arbitrary Python-expression evaluation, network-dependent default analysis, release cut, deployment, commit, merge, or push.
- Keep the complete implementation uncommitted and unmerged for integrated user review. Every task ends with a recoverable checkpoint outside the repository; never run `git add`, `git commit`, merge, push, or `bd-kb-sync ... --pin`.
- Audit findings are evidence, not permission to change production code. Any source fix belongs to a later RED-first fix plan.
- Heuristic call, taint, dead-code, coverage, and reachability outputs must retain confidence and reason fields and must never be described as proven facts.
- Coverage absence is `unknown`, never zero and never pass.
- Every tracked production file must end with exactly one current disposition: `reviewed`, `excluded` with a policy identifier, or `blocked` with a recorded reason and re-audit requirement.
- Preserve non-circumvention: no DRM bypass, access-control bypass, challenge evasion, behavior-mimicry, or automation of paid-consent actions. Authenticated-capture evidence may describe only authorized, site-provided playback/download behavior.
- Do not include capture bodies, credentials, cookies, tokens, signed URLs, password-manager values, or raw secret candidates in audit, knowledge, tracker, checkpoint, or static-KB output.
- The existing `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md` remains available until v2 passes source/artifact fact-checking and static-KB validation.
- Local static-KB staging is not evidence of external paste. The external pin advances only after the operator pastes the replacement set and the pasted state passes integrity and freshness checks.

---

## File Map

### Created by this plan

- `tools/code_intelligence/render_advanced_knowledge.py` - deterministic renderer from validated graph/review artifacts to advanced project knowledge v2.
- `tools/code_intelligence/factcheck_advanced_knowledge.py` - verifies v2 provenance, source/input hashes, required sections, links, and generated facts.
- `tests/code_intelligence/advanced_knowledge_fixture.py` - complete minimal schema-valid artifact set shared by renderer and fact-check tests.
- `tests/code_intelligence/test_render_advanced_knowledge.py` - renderer determinism, source-binding, and secret-safe output tests.
- `tests/code_intelligence/test_factcheck_advanced_knowledge.py` - positive and negative fact-check fixtures.
- `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md` - generated current advanced-knowledge view; no hand-edited mechanical counts.
- `docs/repo/CODE_INTELLIGENCE_AUDIT_COMPLETION_2026-07-23.md` - secret-free completion evidence, caveats, dispositions, artifact hashes, and operator boundary.
- `reports/code-intelligence-v3_66_817/` - ignored working artifacts: graph DB, projections, coverage/risk output, batch claims, audit JSON, L3 results, review state, re-audit list, and generation logs.
- `reports/static-kb-v3_66_817-final/` - ignored final replacement ZIP, update note, extracted validation copy, and secret/link/integrity reports.

### Modified by this plan

- `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md` - remains the legacy document; add only a pointer to the fact-checked v2 after v2 passes.
- `project-knowledge/CODE_INTELLIGENCE_ARCHITECTURE.md`
- `project-knowledge/CODE_INTELLIGENCE_PROGRAM.md`
- `project-knowledge/CODE_INTELLIGENCE_SCHEMAS.md`
- `project-knowledge/CODE_INTELLIGENCE_TOOLING.md`
- `project-knowledge/CODE_REVIEW_INDEX.md`
- `project-knowledge/AUDIT_PLAN_v3_66_539.md` - retain as historical planning context and point to the current generated batch order.
- `project-knowledge/KB_SYNC_WORKFLOW.md` - correct the final paste/verify/pin order.
- `project-knowledge/STATIC_KB_MANIFEST.json` - regenerated only after all tracked content is stable.
- `docs/archive/2026-07-22-doc-hygiene/README.md`
- Operational Markdown files below `docs/archive/2026-07-22-doc-hygiene/` - top-of-file historical safety warning only; historical bodies remain unchanged.
- `docs/repo/DOC_HYGIENE_REPORT_2026-07-22.md`
- `docs/repo/OPV_LIVE_EVIDENCE_UPDATE_2026-07-23.md` - facts only if current evidence hashes require reconciliation.
- `TASK_TRACKER_DATA.json`
- `TASK_TRACKER.md` and `TASK_TRACKER.xlsx` - mechanically rendered from tracker data.
- `CHANGELOG.md`, `project-knowledge/CHANGELOG_RECENT.md`, current operations/plugin/automation/corpus docs - evidence-backed status refresh only; no release version bump.
- `tests/test_consolidation.py`
- `tests/test_documentation_hygiene_20260723.py` - exact archive-warning, canonical-pointer, and missing-OPV-source contract.

### Read-only inputs

- `docs/superpowers/specs/2026-07-23-code-intelligence-platform-design.md`
- Canonical graph projections and `KNOWLEDGE_GRAPH.db` generated under `reports/code-intelligence-v3_66_817/artifacts/`
- `project-knowledge/INVARIANTS.json`
- `project-knowledge/CONTRACTS.json`
- `.superpowers/sdd/corpus-disposition-review-buckets.json`
- `docs/audit/AUDIT_CAP-01_v3_66_532.json`
- `docs/audit/AUDIT_RUN-01_v3_66_532.json`
- `docs/repo/OPV_LIVE_EVIDENCE_UPDATE_2026-07-23.md`

## Required Interfaces From the Green Platform

The platform implementation must expose these exact commands before Task 3 starts:

```text
python3 -m tools.code_intelligence.snapshot --root ROOT \
  --scope tracked|production --out SNAPSHOT_JSON

python3 -m tools.code_intelligence.snapshot --root ROOT \
  --scope tracked|production --check SNAPSHOT_JSON

python3 -m tools.code_intelligence.schemas validate \
  --kind SCHEMA_KIND --file JSON_FILE

python3 -m tools.code_intelligence.schemas migrate \
  --kind SCHEMA_KIND --input SOURCE_JSON --out NORMALIZED_JSON

python3 -m tools.code_intelligence.artifacts compare \
  --left LEFT_DIR --right RIGHT_DIR --ignore-generation-time

python3 toolchain/bin/bd-coverage-map --root ROOT --graph DB --coverage COVERAGE_JSON \
  --outdir OUTDIR --json

python3 toolchain/bin/bd-review-next claim --root ROOT --artifacts ARTIFACTS \
  --owner OWNER --level L2 --lease-seconds 7200 --out CLAIM_JSON

python3 toolchain/bin/bd-review-next complete --root ROOT --artifacts ARTIFACTS \
  --claim CLAIM_JSON --audit AUDIT_JSON

python3 toolchain/bin/bd-review-next status --root ROOT --artifacts ARTIFACTS --json

python3 toolchain/bin/bd-review-next attach-l3 --root ROOT --artifacts ARTIFACTS \
  --semantic RESULT_JSON --reachability RESULT_JSON --oracle RESULT_JSON \
  --fuzz RESULT_JSON --invariants RESULT_JSON --contracts RESULT_JSON \
  --out RESULT_JSON

python3 tools/review_merge.py --audit AUDIT_JSON --root ROOT \
  --artifacts ARTIFACTS

python3 tools/semantic_diff.py --base-tree BASE_TREE --head-tree HEAD_TREE \
  --out RESULT_JSON --json

python3 tools/reachability.py --root ROOT --graph DB --out RESULT_JSON \
  --mode gate --json

python3 tools/differential_oracle.py replay --registry REGISTRY_JSON \
  --out RESULT_JSON --json

python3 tools/fuzz_harness.py replay --registry REGISTRY_JSON \
  --corpus CORPUS_DIR --out RESULT_JSON --json

python3 tools/invariant_probe.py --root ROOT --registry INVARIANTS_JSON \
  --out RESULT_JSON --json

python3 tools/contract_harness.py --root ROOT --registry CONTRACTS_JSON \
  --out RESULT_JSON --json

python3 tools/bd-audit-gate.py --root ROOT --artifacts ARTIFACTS \
  --required all --json-out RESULT_JSON
```

All commands return `0` only for a valid pass, return nonzero for fail/error/missing-required-component, and emit normalized results with `state` in `pass|fail|advisory|unknown|timeout|error`. A command that lacks these flags or semantics blocks this plan and returns to the platform implementation plan; do not compensate with an ad hoc wrapper.

## Checkpoint Protocol

At the end of every task, replace `NN` with the task number shown in that task and run the exact block:

```bash
REPO="$(git rev-parse --show-toplevel)"
CHECKPOINT_ROOT="$(dirname "$REPO")/BulkDownloader-checkpoints/code-intelligence-audit-20260723"
TASK_DIR="$CHECKPOINT_ROOT/task-NN"
mkdir -p "$TASK_DIR"
git -C "$REPO" diff --binary --full-index > "$TASK_DIR/tracked.patch"
git -C "$REPO" status --porcelain=v1 > "$TASK_DIR/status.txt"
git -C "$REPO" ls-files --others --exclude-standard -z -- \
  docs tools/code_intelligence tests project-knowledge \
  AUTOMATION_POLICY.md kb/decomp toolchain \
  TASK_TRACKER_DATA.json TASK_TRACKER.md TASK_TRACKER.xlsx CHANGELOG.md \
  | tar -C "$REPO" --null -T - -czf "$TASK_DIR/untracked.tgz"
if test -d "$REPO/reports/code-intelligence-v3_66_817"; then
  tar -C "$REPO" -czf "$TASK_DIR/code-intelligence-reports.tgz" \
    reports/code-intelligence-v3_66_817
fi
sha256sum "$TASK_DIR"/* > "$TASK_DIR/SHA256SUMS"
git -C "$REPO" diff --check
```

Expected: `git diff --check` exits `0`; `SHA256SUMS` contains every checkpoint member. These are local recovery artifacts, not commits and not external publications.

---

### Task 1: Freeze scope and prove every prerequisite tool is independently green

**Files:**
- Read: `docs/superpowers/specs/2026-07-23-code-intelligence-platform-design.md`
- Read: `tools/code_intelligence/*.py`
- Read: all exact frontends listed under Required Interfaces
- Create ignored: `reports/code-intelligence-v3_66_817/preflight/`

**Interfaces:**
- Consumes: completed unified-platform implementation and current uncommitted worktree.
- Produces: a secret-free prerequisite result set and a discovered repository root used by every later task.

- [ ] **Step 1: Record the current worktree without blessing it**

```bash
REPO="$(git rev-parse --show-toplevel)"
RUN="$REPO/reports/code-intelligence-v3_66_817"
mkdir -p "$RUN/preflight" "$RUN/artifacts" "$RUN/audits" "$RUN/l3"
git status --short > "$RUN/preflight/git-status.txt"
git rev-parse HEAD > "$RUN/preflight/head-commit.txt"
python3 -m tools.code_intelligence.snapshot \
  --root "$REPO" --scope tracked --out "$RUN/preflight/TRACKED_TREE.json"
python3 -m tools.code_intelligence.snapshot \
  --root "$REPO" --scope production --out "$RUN/preflight/PRODUCTION_TREE.json"
: "${BD_COVERAGE_JSON:?Set BD_COVERAGE_JSON to the accepted stash coverage.py JSON}"
python3 -m tools.code_intelligence.schemas validate \
  --kind coverage-json --file "$BD_COVERAGE_JSON"
cp "$BD_COVERAGE_JSON" "$RUN/preflight/coverage.json"
```

Expected: both snapshot commands and coverage validation exit `0`; each snapshot includes a deterministic tree hash and no file contents or secret values; the copied coverage input has a recorded SHA before use.

- [ ] **Step 2: Run standalone platform tests before any audit generation**

```bash
cd "$REPO"
python3 -m pytest -q tests/code_intelligence
python3 tools/l0_extract.py --help
python3 tools/graph_build.py --help
python3 toolchain/bin/bd-coverage-map --help
python3 tools/semantic_diff.py --help
python3 tools/reachability.py --help
python3 tools/differential_oracle.py --help
python3 tools/fuzz_harness.py --help
python3 tools/invariant_probe.py --help
python3 tools/contract_harness.py --help
python3 toolchain/bin/bd-review-next --help
python3 tools/bd-audit-gate.py --help
```

Expected: tests report zero failures; every help command exits `0`; the exact interfaces above are present.

- [ ] **Step 3: Prove missing required components cannot produce a pass**

```bash
EMPTY="$(mktemp -d)"
set +e
python3 tools/bd-audit-gate.py \
  --root "$REPO" --artifacts "$EMPTY" --required all \
  --json-out "$RUN/preflight/missing-components.json"
RC=$?
set -e
rm -rf "$EMPTY"
test "$RC" -ne 0
python3 - <<'PY'
import json
from pathlib import Path
p = Path("reports/code-intelligence-v3_66_817/preflight/missing-components.json")
d = json.loads(p.read_text(encoding="utf-8"))
assert d["state"] == "fail"
assert d["missing_required_components"]
PY
```

Expected: the negative control returns nonzero and records at least one missing required component.

- [ ] **Step 4: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=01`.

---

### Task 2: Build the advanced-knowledge renderer and fact-checker before the audit snapshot

**Files:**
- Create: `tools/code_intelligence/render_advanced_knowledge.py`
- Create: `tools/code_intelligence/factcheck_advanced_knowledge.py`
- Create: `tests/code_intelligence/advanced_knowledge_fixture.py`
- Create: `tests/code_intelligence/test_render_advanced_knowledge.py`
- Create: `tests/code_intelligence/test_factcheck_advanced_knowledge.py`

**Interfaces:**
- Consumes: schema-validated artifact directory in which every required artifact has the same production source hash.
- Produces:
  - `render_advanced_knowledge.load_bundle(artifacts: Path) -> dict`
  - `render_advanced_knowledge.render(bundle: dict) -> str`
  - CLI `--artifacts DIR --out FILE [--check]`
  - `factcheck_advanced_knowledge.check(document: Path, artifacts: Path, root: Path) -> dict`
  - CLI `--document FILE --artifacts DIR --root ROOT --json-out FILE`
  - `advanced_knowledge_fixture.write_complete_fixture(root: Path, source_sha: str) -> Path`
  - `advanced_knowledge_fixture.mutate_artifact_source_sha(path: Path, source_sha: str) -> None`
  - `advanced_knowledge_fixture.inject_forbidden_value(path: Path, value: str) -> None`
  - `advanced_knowledge_fixture.fixture_root(root: Path) -> Path`

The fixture helper writes all filenames in `REQUIRED` plus
`PRODUCTION_TREE.json`, using the real schema constructors from
`tools.code_intelligence.schemas`. It contains exactly two reviewed modules, one
guarded invariant, one contract, one authenticated route, one advisory
heuristic dead-code row, no open security path, and no real secret value.

- [ ] **Step 1: Write failing renderer tests**

```python
import pytest

from tests.code_intelligence.advanced_knowledge_fixture import (
    inject_forbidden_value,
    mutate_artifact_source_sha,
    write_complete_fixture,
)
from tools.code_intelligence.render_advanced_knowledge import load_bundle, render


def test_renderer_is_deterministic_and_source_bound(tmp_path):
    artifacts = write_complete_fixture(tmp_path, source_sha="a" * 64)
    first = render(load_bundle(artifacts))
    second = render(load_bundle(artifacts))
    assert first == second
    assert "Production source SHA: `aaaaaaaa" in first
    assert "## Mechanical facts" in first
    assert "## L2/L3 reviewed knowledge" in first
    assert "## Security and reachability" in first
    assert "## Coverage and dead-code caveats" in first
    assert "## Open findings" in first


def test_renderer_rejects_mixed_source_hashes(tmp_path):
    artifacts = write_complete_fixture(tmp_path, source_sha="a" * 64)
    mutate_artifact_source_sha(artifacts / "REVIEW_STATE.json", "b" * 64)
    with pytest.raises(ValueError, match="mixed production source SHA"):
        load_bundle(artifacts)


def test_renderer_never_emits_secret_values(tmp_path):
    artifacts = write_complete_fixture(tmp_path, source_sha="a" * 64)
    inject_forbidden_value(artifacts / "SECURITY_SURFACE.json", "Bearer live-secret")
    with pytest.raises(ValueError, match="forbidden secret-bearing value"):
        load_bundle(artifacts)
```

Run:

```bash
cd "$REPO"
python3 -m pytest -q \
  tests/code_intelligence/test_render_advanced_knowledge.py
```

Expected: RED because the renderer module and functions do not exist.

- [ ] **Step 2: Implement the deterministic renderer**

Implement these exact sections in this order:

```python
REQUIRED = (
    "MODULE_CATALOG.json",
    "CALL_GRAPH.json",
    "TAINT_MAP.json",
    "SECURITY_SURFACE.json",
    "ERROR_CATALOG.json",
    "CONFIG_LINEAGE.json",
    "CONCURRENCY_MAP.json",
    "METRICS_CATALOG.json",
    "DEAD_CODE.json",
    "COVERAGE_GAPS.json",
    "REVIEW_STATE.json",
    "INVARIANTS.json",
    "CONTRACTS.json",
    "REACHABILITY.json",
)

SECTION_ORDER = (
    "Mechanical facts",
    "L2/L3 reviewed knowledge",
    "Invariants and contracts",
    "Security and reachability",
    "Configuration, concurrency, and metrics",
    "Coverage and dead-code caveats",
    "Open findings",
    "Provenance",
)

FORBIDDEN_VALUE_KEYS = {
    "password", "secret", "token", "cookie", "authorization",
    "signed_query", "raw_body", "credential",
}
```

`load_bundle()` must validate every input with `tools.code_intelligence.schemas`, require one production source SHA, retain each input SHA-256, reject forbidden value-bearing fields recursively, and sort modules/findings/invariants/contracts before rendering. `render()` must show heuristic confidence and reasons, render `unknown` literally, label L2/L3 judgment separately from mechanical facts, and omit generation timestamps from deterministic comparison content. Write through `tools.code_intelligence.artifacts.atomic_write_text()`.

- [ ] **Step 3: Write failing fact-checker tests**

```python
from tests.code_intelligence.advanced_knowledge_fixture import (
    fixture_root,
    write_complete_fixture,
)
from tools.code_intelligence.factcheck_advanced_knowledge import check
from tools.code_intelligence.render_advanced_knowledge import load_bundle, render


def test_factcheck_accepts_exact_render(tmp_path):
    artifacts = write_complete_fixture(tmp_path, source_sha="a" * 64)
    doc = tmp_path / "ADVANCED_PROJECT_KNOWLEDGE_v2.md"
    doc.write_text(render(load_bundle(artifacts)), encoding="utf-8")
    result = check(doc, artifacts, fixture_root(tmp_path))
    assert result["state"] == "pass"
    assert result["errors"] == []


def test_factcheck_rejects_changed_count_and_stale_source(tmp_path):
    artifacts = write_complete_fixture(tmp_path, source_sha="a" * 64)
    doc = tmp_path / "ADVANCED_PROJECT_KNOWLEDGE_v2.md"
    text = render(load_bundle(artifacts)).replace("Reviewed files: 2", "Reviewed files: 3")
    doc.write_text(text, encoding="utf-8")
    result = check(doc, artifacts, fixture_root(tmp_path))
    assert result["state"] == "fail"
    assert "document differs from deterministic render" in result["errors"]


def test_factcheck_rejects_broken_relative_link(tmp_path):
    artifacts = write_complete_fixture(tmp_path, source_sha="a" * 64)
    doc = tmp_path / "ADVANCED_PROJECT_KNOWLEDGE_v2.md"
    doc.write_text(
        render(load_bundle(artifacts)) + "\n[missing](missing.md)\n",
        encoding="utf-8",
    )
    result = check(doc, artifacts, fixture_root(tmp_path))
    assert result["state"] == "fail"
    assert result["broken_links"] == ["missing.md"]
```

Run the two test files. Expected: the fact-check tests fail before implementation.

- [ ] **Step 4: Implement fact-checking and make both suites green**

The checker must:

1. validate every required artifact;
2. compare the document byte-for-byte with `render(load_bundle(...))`;
3. compare the production snapshot hash with the artifacts;
4. verify all relative Markdown links after stripping fenced code;
5. verify every referenced file/line span exists;
6. report missing/unknown fields without converting them to pass;
7. return normalized JSON without source excerpts or secret values.

Run:

```bash
cd "$REPO"
python3 -m pytest -q \
  tests/code_intelligence/test_render_advanced_knowledge.py \
  tests/code_intelligence/test_factcheck_advanced_knowledge.py
```

Expected: all tests pass.

- [ ] **Step 5: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=02`.

---

### Task 3: Generate the final production graph, coverage, risk scores, and deterministic batch order

**Files:**
- Create ignored: `reports/code-intelligence-v3_66_817/artifacts/KNOWLEDGE_GRAPH.db`
- Create ignored: all graph projections required by the approved design
- Create ignored: `COVERAGE_GAPS.json`, `RISK_SCORES.json`, `BATCH_ORDER.json`
- Create ignored: `PRODUCTION_TREE.json`, `L0_SNAPSHOT.json`, `GENERATION.json`

**Interfaces:**
- Consumes: green platform tools and the production snapshot after Task 2.
- Produces: one source-SHA-bound artifact set consumed by review allocation and advanced-knowledge rendering.

- [ ] **Step 1: Recompute and freeze the production snapshot**

```bash
cd "$REPO"
RUN="$REPO/reports/code-intelligence-v3_66_817"
ART="$RUN/artifacts"
python3 -m tools.code_intelligence.snapshot \
  --root "$REPO" --scope production --out "$ART/PRODUCTION_TREE.json"
cp "$ART/PRODUCTION_TREE.json" "$RUN/preflight/FROZEN_PRODUCTION_TREE.json"
```

Expected: the snapshot contains every tracked production file exactly once and excludes tests, reports, archives, captures, credential stores, SQLite sidecars, and generated release artifacts according to the platform include/exclude policy.

- [ ] **Step 2: Generate L0 and the canonical graph atomically**

```bash
python3 tools/l0_extract.py \
  --root "$REPO" \
  --db "$ART/KNOWLEDGE_GRAPH.db"
python3 tools/graph_build.py \
  --db "$ART/KNOWLEDGE_GRAPH.db" \
  --outdir "$ART"
```

Expected: zero parse errors for supported production files; the DB and every projection validate; all projection source hashes equal `PRODUCTION_TREE.json`; unresolved calls are retained as rows with reason/confidence, not reduced to a count.

- [ ] **Step 3: Import measured coverage without treating absence as zero**

Use the current stash-produced coverage JSON already accepted by the platform preflight:

```bash
test -s "$RUN/preflight/coverage.json"
python3 toolchain/bin/bd-coverage-map \
  --root "$REPO" \
  --graph "$ART/KNOWLEDGE_GRAPH.db" \
  --coverage "$RUN/preflight/coverage.json" \
  --outdir "$ART" \
  --json > "$RUN/preflight/coverage-map-result.json"
```

Expected: command exits `0`; uncovered functions are distinct from unknown coverage; coverage input SHA is recorded.

- [ ] **Step 4: Generate current manifests and risk order**

```bash
python3 project-knowledge/audit_partition.py 5500
python3 tools/risk_score.py \
  --db "$ART/KNOWLEDGE_GRAPH.db" \
  --root "$REPO" \
  --manifests "$REPO/audit_manifests" \
  --outdir "$ART"
```

Expected: manifests are disjoint and complete over `PRODUCTION_TREE.json`; `BATCH_ORDER.json` contains every manifest once, sorted deterministically by measured risk and stable tie-breakers.

- [ ] **Step 5: Run determinism and schema checks**

Generate a second set under a temporary directory, compare deterministic content hashes, and remove it:

```bash
SECOND="$(mktemp -d)"
python3 tools/l0_extract.py --root "$REPO" --db "$SECOND/KNOWLEDGE_GRAPH.db"
python3 tools/graph_build.py --db "$SECOND/KNOWLEDGE_GRAPH.db" --outdir "$SECOND"
python3 -m tools.code_intelligence.artifacts compare \
  --left "$ART" --right "$SECOND" --ignore-generation-time
rm -rf "$SECOND"
```

Expected: deterministic comparison reports zero differences.

- [ ] **Step 6: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=03`.

---

### Task 4: Seed `REVIEW_STATE.json` and invalidate historical evidence by live file SHA

**Files:**
- Create ignored: `reports/code-intelligence-v3_66_817/artifacts/REVIEW_STATE.json`
- Create ignored: `reports/code-intelligence-v3_66_817/artifacts/REAUDIT.txt`
- Read: `docs/audit/AUDIT_CAP-01_v3_66_532.json`
- Read: `docs/audit/AUDIT_RUN-01_v3_66_532.json`

**Interfaces:**
- Consumes: frozen production snapshot, graph DB, batch order, and historical audit JSON.
- Produces: canonical source-bound ledger with stale reasons and no false carry-forward.

- [ ] **Step 1: Seed every production file as unreviewed**

```bash
cd "$REPO"
RUN="$REPO/reports/code-intelligence-v3_66_817"
ART="$RUN/artifacts"
python3 tools/seed_review_state.py \
  --db "$ART/KNOWLEDGE_GRAPH.db" \
  --root "$REPO" \
  --out "$ART/REVIEW_STATE.json"
python3 tools/invariants.py \
  --root "$REPO" \
  --out "$ART/INVARIANTS.json"
python3 -m tools.code_intelligence.schemas migrate \
  --kind contracts \
  --input project-knowledge/CONTRACTS.json \
  --out "$ART/CONTRACTS.json"
```

Expected: `totals.production_files` equals the production snapshot count; every file starts `unreviewed` unless a historical audit passes exact SHA validation; invariant and contract registries are lossless, schema-valid, and source-bound.

- [ ] **Step 2: Evaluate historical CAP-01 and RUN-01 evidence without overriding drift**

```bash
python3 tools/review_merge.py \
  --audit docs/audit/AUDIT_CAP-01_v3_66_532.json \
  --root "$REPO" \
  --artifacts "$ART"
CAP_RC=$?
python3 tools/review_merge.py \
  --audit docs/audit/AUDIT_RUN-01_v3_66_532.json \
  --root "$REPO" \
  --artifacts "$ART"
RUN_RC=$?
printf 'CAP-01=%s\nRUN-01=%s\n' "$CAP_RC" "$RUN_RC" \
  > "$RUN/preflight/historical-audit-disposition.txt"
```

Expected: unchanged files may retain review evidence; changed/missing files are refused and written to `REAUDIT.txt` with `sha_drift`, never silently marked reviewed. Do not use `--no-verify`.

- [ ] **Step 3: Prove staleness is fail-closed**

Run the platform fixture negative control that changes one reviewed file byte, then runs ledger check.

```bash
python3 -m pytest -q \
  tests/code_intelligence/test_review_state.py \
  -k 'stale or reaudit or source_sha'
python3 tools/seed_review_state.py \
  --db "$ART/KNOWLEDGE_GRAPH.db" \
  --root "$REPO" \
  --out "$ART/REVIEW_STATE.json" \
  --check
```

Expected: fixture proves drift marks stale and returns nonzero in gate mode; live ledger check returns `0`.

- [ ] **Step 4: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=04`.

---

### Task 5: Execute one independently reviewable L2 audit per leased batch

**Files:**
- Create ignored: `reports/code-intelligence-v3_66_817/claims/*.json`
- Create ignored: the exact audit filename returned in each claim's `audit_output` field under `reports/code-intelligence-v3_66_817/audits/`
- Update atomically: `reports/code-intelligence-v3_66_817/artifacts/REVIEW_STATE.json`
- Update atomically: graph L2 fields and invariant/finding links

**Interfaces:**
- Consumes: deterministic risk order and ledger.
- Produces: non-overlapping source review with one current disposition and full rubric per claimed file.

- [ ] **Step 1: Claim the next L2 slice**

Each worker runs:

```bash
cd "$REPO"
RUN="$REPO/reports/code-intelligence-v3_66_817"
ART="$RUN/artifacts"
OWNER="$(hostname)-$$"
CLAIM="$RUN/claims/$OWNER.json"
mkdir -p "$RUN/claims"
python3 toolchain/bin/bd-review-next claim \
  --root "$REPO" \
  --artifacts "$ART" \
  --owner "$OWNER" \
  --level L2 \
  --lease-seconds 7200 \
  --out "$CLAIM"
```

Expected: one active source-SHA-bound claim; no file/function scope overlaps any unexpired claim.

- [ ] **Step 2: Review every claimed file to the complete rubric**

For every file in the claim, read the full current file plus its callers, callees, tests, graph facts, security/reachability facts, and open findings. Record all rubric keys:

```text
auth
authorization
sql_command_path_injection
ssrf_url_trust
secret_read_write_mask_log
error_raise_status_swallow
type_none_nan_inf
concurrency_shared_state
resource_lifecycle
input_validation
dead_code_unreachable
purpose
data_flow
public_api
invariants
contracts
positive_assurances
```

Each file receives exactly one disposition:

```text
reviewed
excluded:<policy_id>
blocked:<reason_code>
```

`blocked` must include the missing evidence, risk, and re-audit condition. Tool hits remain `advisory` or `probable` until supported by source reasoning and a RED reproducer; do not fix source.

- [ ] **Step 3: Validate and complete the batch**

```bash
AUDIT_JSON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["audit_output"])' "$CLAIM")"
python3 -m tools.code_intelligence.schemas validate \
  --kind audit-batch --file "$AUDIT_JSON"
python3 toolchain/bin/bd-review-next complete \
  --root "$REPO" \
  --artifacts "$ART" \
  --claim "$CLAIM" \
  --audit "$AUDIT_JSON"
python3 tools/seed_review_state.py \
  --db "$ART/KNOWLEDGE_GRAPH.db" \
  --root "$REPO" \
  --out "$ART/REVIEW_STATE.json" \
  --check
```

Expected: audit schema passes; the exact leased scope merges once; file SHAs still match; findings contain no source excerpts or secret values.

- [ ] **Step 4: Checkpoint the completed batch before another claim**

Run the Checkpoint Protocol with `NN=05`. Repeat Task 5 from Step 1 until `bd-review-next status` reports no unclaimed L2 scope. Every repetition is a separate reviewer checkpoint.

---

### Task 6: Run current L3 cross-cutting evidence and attach dispositions

**Files:**
- Create ignored: `reports/code-intelligence-v3_66_817/l3/semantic-diff.json`
- Create ignored: `reachability.json`, `differential-oracle.json`, `fuzz-replay.json`
- Create ignored: `invariant-probes.json`, `contracts.json`, `l3-summary.json`
- Update atomically: `REVIEW_STATE.json` links and finding dispositions

**Interfaces:**
- Consumes: completed L2 ledger, graph, registries, frozen regression corpus, and base/head snapshots.
- Produces: bounded L3 evidence with normalized result states and links to affected reviewed files.

- [ ] **Step 1: Run semantic and reachability passes**

```bash
cd "$REPO"
RUN="$REPO/reports/code-intelligence-v3_66_817"
ART="$RUN/artifacts"
L3="$RUN/l3"
BASE_TREE="$(mktemp -d)"
git archive HEAD | tar -x -C "$BASE_TREE"
python3 tools/semantic_diff.py \
  --base-tree "$BASE_TREE" \
  --head-tree "$REPO" \
  --out "$L3/semantic-diff.json" \
  --json
rm -rf "$BASE_TREE"
python3 tools/reachability.py \
  --root "$REPO" \
  --graph "$ART/KNOWLEDGE_GRAPH.db" \
  --out "$L3/reachability.json" \
  --mode gate \
  --json
```

Expected: every changed or unknown contract/auth/config/concurrency/metric surface relative to `HEAD` is evidence-linked; unknown privilege boundaries fail gate mode.

- [ ] **Step 2: Run bounded oracle and frozen fuzz replay**

```bash
python3 tools/differential_oracle.py replay \
  --registry project-knowledge/DIFFERENTIAL_ORACLES.json \
  --out "$L3/differential-oracle.json" \
  --json
python3 tools/fuzz_harness.py replay \
  --registry project-knowledge/FUZZ_TARGETS.json \
  --corpus regression_corpus \
  --out "$L3/fuzz-replay.json" \
  --json
```

Expected: deterministic seeds and budgets are recorded; timeout/error cannot appear as pass; reproducer records are secret-safe.

- [ ] **Step 3: Run invariant and contract registries**

```bash
python3 tools/invariant_probe.py \
  --root "$REPO" \
  --registry "$ART/INVARIANTS.json" \
  --out "$L3/invariant-probes.json" \
  --json
python3 tools/contract_harness.py \
  --root "$REPO" \
  --registry "$ART/CONTRACTS.json" \
  --out "$L3/contracts.json" \
  --json
```

Expected: only allowlisted bounded operations execute; no `eval`, `exec`, shell interpolation, arbitrary imports, or unbounded subprocesses; cleanup evidence exists for side-effecting fixtures.

- [ ] **Step 4: Merge L3 evidence without auto-promoting findings**

```bash
python3 toolchain/bin/bd-review-next attach-l3 \
  --root "$REPO" \
  --artifacts "$ART" \
  --semantic "$L3/semantic-diff.json" \
  --reachability "$L3/reachability.json" \
  --oracle "$L3/differential-oracle.json" \
  --fuzz "$L3/fuzz-replay.json" \
  --invariants "$L3/invariant-probes.json" \
  --contracts "$L3/contracts.json" \
  --out "$L3/l3-summary.json"
```

Expected: every L3 record links to current source scope; confirmed findings remain open for a later fix plan; no invariant is promoted unless its RED-test and allowlisted-probe requirements are already met.

`attach-l3` must also atomically materialize
`$ART/REACHABILITY.json`, `$ART/SEMANTIC_DIFF.json`,
`$ART/DIFFERENTIAL_ORACLE.json`, `$ART/FUZZ_REPLAY.json`,
`$ART/INVARIANT_PROBES.json`, and `$ART/CONTRACT_RESULTS.json`, preserving the
same production source SHA used by `REVIEW_STATE.json`.

- [ ] **Step 5: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=06`.

---

### Task 7: Close the ledger and prove complete current dispositions

**Files:**
- Update atomically: `REVIEW_STATE.json`, `REAUDIT.txt`, graph L2 projections
- Create ignored: `reports/code-intelligence-v3_66_817/artifacts/AUDIT_GATE.json`

**Interfaces:**
- Consumes: all completed L2 claims and L3 evidence.
- Produces: auditable closure or an explicit blocked state; never partial-success wording.

- [ ] **Step 1: Reconcile claims, file set, and dispositions**

```bash
cd "$REPO"
RUN="$REPO/reports/code-intelligence-v3_66_817"
ART="$RUN/artifacts"
python3 toolchain/bin/bd-review-next status \
  --root "$REPO" --artifacts "$ART" --json \
  > "$RUN/preflight/final-review-status.json"
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("reports/code-intelligence-v3_66_817/preflight/final-review-status.json").read_text())
assert d["duplicate_active_claims"] == 0
assert d["unclaimed_files"] == 0
assert d["files_with_no_disposition"] == 0
assert d["disposition_total"] == d["production_file_count"]
PY
```

Expected: every production file has one disposition and no active claim overlaps.

- [ ] **Step 2: Re-run source staleness and the composite gate**

```bash
python3 -m tools.code_intelligence.snapshot \
  --root "$REPO" --scope production \
  --check "$ART/PRODUCTION_TREE.json"
python3 tools/bd-audit-gate.py \
  --root "$REPO" \
  --artifacts "$ART" \
  --required all \
  --json-out "$ART/AUDIT_GATE.json"
```

Expected: production snapshot check exits `0`; every required gate component is present; aggregate state is `pass`. Advisory findings may remain but must be enumerated separately.

- [ ] **Step 3: Verify the complete ledger mathematically**

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path("reports/code-intelligence-v3_66_817/artifacts/REVIEW_STATE.json")
d = json.loads(p.read_text(encoding="utf-8"))
files = d["files"]
allowed = {"reviewed", "excluded", "blocked"}
assert files
assert all(v["disposition"] in allowed for v in files.values())
assert sum(1 for v in files.values() if v["disposition"] in allowed) == len(files)
assert all(v.get("policy_id") for v in files.values() if v["disposition"] == "excluded")
assert all(v.get("blocked_reason") and v.get("reaudit_when")
           for v in files.values() if v["disposition"] == "blocked")
PY
```

Expected: assertions pass. If any file is blocked, the overall audit may be complete as a disposition inventory but must not be described as fully reviewed.

- [ ] **Step 4: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=07`.

---

### Task 8: Generate and fact-check `ADVANCED_PROJECT_KNOWLEDGE_v2.md`

**Files:**
- Create: `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md`
- Modify: `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md`
- Create ignored: `reports/code-intelligence-v3_66_817/advanced-knowledge-factcheck.json`

**Interfaces:**
- Consumes: the exact artifact set that passed Task 7.
- Produces: deterministic current knowledge plus a machine-readable fact-check result.

- [ ] **Step 1: Generate v2 atomically**

```bash
cd "$REPO"
RUN="$REPO/reports/code-intelligence-v3_66_817"
ART="$RUN/artifacts"
python3 tools/code_intelligence/render_advanced_knowledge.py \
  --artifacts "$ART" \
  --out project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md
```

Expected: output includes the exact production source SHA and input artifact hashes; mechanical, L2/L3, caveat, and open-finding sections are distinct; no unknown is rendered as pass.

- [ ] **Step 2: Fact-check the generated document**

```bash
python3 tools/code_intelligence/factcheck_advanced_knowledge.py \
  --document project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md \
  --artifacts "$ART" \
  --root "$REPO" \
  --json-out "$RUN/advanced-knowledge-factcheck.json"
python3 tools/code_intelligence/render_advanced_knowledge.py \
  --artifacts "$ART" \
  --out project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md \
  --check
```

Expected: fact-check state `pass`; renderer check reports byte-identical output.

- [ ] **Step 3: Add a legacy-to-v2 pointer without deleting history**

At the top of `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md`, immediately after its title, add:

```markdown
> **Current generated reference:** [`ADVANCED_PROJECT_KNOWLEDGE_v2.md`](ADVANCED_PROJECT_KNOWLEDGE_v2.md) is the source-bound, fact-checked view for the v3.66.817 audit. This file remains as the v3.66.464 historical knowledge layer and must not override current source or v2 evidence.
```

Expected: legacy content remains intact and is clearly historical relative to v2.

- [ ] **Step 4: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=08`.

---

### Task 9: Finish documentation hygiene and refresh tracker truth

**Files:**
- Modify: documentation and tracker files listed in the File Map.
- Test: `tests/test_consolidation.py`
- Create: `tests/test_documentation_hygiene_20260723.py`

**Interfaces:**
- Consumes: completed ledger, v2 fact-check, OPV evidence report, and existing archive map.
- Produces: current docs/tracker with no duplicate canonicals, unsafe archive entrypoints, fabricated OPV sources, or stale code-intelligence status.

- [ ] **Step 1: Write or extend failing hygiene tests**

Tests must assert:

```python
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "docs/archive/2026-07-22-doc-hygiene"
WARNING = "Historical archive - do not execute"


def test_operational_archives_warn_at_top():
    paths = sorted(
        p for p in ARCHIVE.rglob("*.md")
        if p.name != "README.md"
    )
    assert paths
    for path in paths:
        first_ten = "\n".join(path.read_text(encoding="utf-8").splitlines()[:10])
        assert WARNING in first_ten, path.relative_to(REPO)


def test_canonical_compatibility_pointers():
    root_auto = (REPO / "AUTOMATION_POLICY.md").read_text(encoding="utf-8")
    toolchain = (REPO / "toolchain/BDSUITE_CHANGELOG.md").read_text(encoding="utf-8")
    decomp_root = (
        REPO / "kb/decomp/DECOMPOSITION_PROGRAM_ROADMAP.md"
    ).read_text(encoding="utf-8")
    decomp_pk = (
        REPO / "project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md"
    ).read_text(encoding="utf-8")
    target = (
        "docs/archive/2026-07-22-doc-hygiene/project-knowledge/"
        "DECOMPOSITION_PROGRAM_ROADMAP.md"
    )
    assert "project-knowledge/AUTOMATION_POLICY.md" in root_auto
    assert "../project-knowledge/BDSUITE_CHANGELOG.md" in toolchain
    assert target in decomp_root
    assert "../" + target in decomp_pk


def test_missing_opv_sources_are_documented_not_fabricated():
    report = (
        REPO / "docs/repo/DOC_HYGIENE_REPORT_2026-07-22.md"
    ).read_text(encoding="utf-8")
    assert "v3.66.262 and v3.66.265" in report
    assert "absent from both the working tree and reachable Git history" in report
    archived_names = {p.name for p in ARCHIVE.rglob("*.md")}
    assert not any("262" in name or "265" in name for name in archived_names)
```

Run focused tests before edits. Expected: any still-missing top warning/status refresh fails.

- [ ] **Step 2: Put the archive warning at the top of every operational historical file**

Use this exact warning within the first ten lines:

```markdown
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
```

Do not rewrite historical measurements, findings, or bodies merely to modernize them.

- [ ] **Step 3: Refresh active code-intelligence and operations truth**

Update current docs to state:

- artifact source SHA and audit completion/disposition counts come from `REVIEW_STATE.json`;
- graph projections now available and remaining heuristic limits;
- exact standalone/composite tool states;
- L2/L3 coverage status and blocked dispositions;
- v2 is current and v1 is historical;
- current corpus disposition is `445 retain_review_required`, `0 promoted`, `0 auto-enabled`;
- F3.1 and EXIT-3 remain clocks/gates described in `OPV_LIVE_EVIDENCE_UPDATE_2026-07-23.md`;
- local static staging is not external paste;
- `KB_SYNC_WORKFLOW.md` orders operations as stage -> operator paste -> verify pasted set -> pin.

Do not change code or claim EXIT-3 soak started.

- [ ] **Step 4: Refresh tracker data and render both views**

Update `TASK_TRACKER_DATA.json` from evidence only, then run:

```bash
python3 tools/tasktracker_gen.py --render "$REPO"
python3 tools/tasktracker_gen.py --check "$REPO"
```

Expected: `TASK_TRACKER.md` and `TASK_TRACKER.xlsx` match data; check exits `0`.

- [ ] **Step 5: Run documentation gates**

```bash
python3 -m pytest -q \
  tests/test_consolidation.py \
  tests/test_documentation_hygiene_20260723.py \
  tests/test_tasktracker_gen.py
python3 tools/kb_link_validator.py --root "$REPO" --json \
  > reports/code-intelligence-v3_66_817/current-links.json
python3 tools/kb_duplicate_detector.py --root "$REPO" --json \
  > reports/code-intelligence-v3_66_817/current-duplicates.json
```

Expected: tests pass; active-doc broken-link count is zero; canonical duplicate groups for automation policy, BDSUITE changelog, and decomposition roadmap are zero.

- [ ] **Step 6: Write the audit completion report**

`docs/repo/CODE_INTELLIGENCE_AUDIT_COMPLETION_2026-07-23.md` must include:

- production source SHA;
- graph/projection/review/fact-check hashes;
- disposition totals;
- open finding counts by confidence/severity without secret values;
- L3 result states;
- heuristic/unknown caveats;
- v2 document hash;
- no-production-code-fix attestation;
- no-commit/no-merge/no-push attestation;
- static-KB paste and pin still pending.

- [ ] **Step 7: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=09`.

---

### Task 10: Stage one final static-KB replacement and verify it independently

**Files:**
- Modify mechanically: `project-knowledge/STATIC_KB_MANIFEST.json`
- Create ignored: `reports/static-kb-v3_66_817-final/BulkDownloader_project_files_v3_66_817.zip`
- Create ignored: `reports/static-kb-v3_66_817-final/PROJECT_KNOWLEDGE_UPDATE.md`
- Update: `docs/repo/DOC_HYGIENE_REPORT_2026-07-22.md`

**Interfaces:**
- Consumes: stable tracked docs, tracker, v2, and current project-knowledge root.
- Produces: one paste-ready ZIP whose members exactly match its internal manifest, plus redacted validation evidence. Does not pin.

- [ ] **Step 1: Prove tracked content is stable before staging**

```bash
cd "$REPO"
python3 tools/code_intelligence/render_advanced_knowledge.py \
  --artifacts reports/code-intelligence-v3_66_817/artifacts \
  --out project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md \
  --check
python3 tools/tasktracker_gen.py --check "$REPO"
python3 -m pytest -q \
  tests/code_intelligence \
  tests/test_consolidation.py \
  tests/test_documentation_hygiene_20260723.py \
  project-knowledge/test_bd_kb_sync.py
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 2: Stage without advancing the external pin**

```bash
STATIC_OUT="$REPO/reports/static-kb-v3_66_817-final"
case "$STATIC_OUT" in
  "$REPO"/reports/static-kb-*) ;;
  *) echo "unsafe static output path: $STATIC_OUT" >&2; exit 2 ;;
esac
rm -rf -- "$STATIC_OUT"
mkdir -p "$STATIC_OUT"
python3 project-knowledge/bd-kb-sync \
  stage project-knowledge \
  --out "$STATIC_OUT" \
  --version v3.66.817
```

Expected: replacement ZIP and update note are created; no `--pin` is used; output says external paste is required.

- [ ] **Step 3: Validate ZIP members, bytes, and hashes against the internal manifest**

```bash
ZIP="reports/static-kb-v3_66_817-final/BulkDownloader_project_files_v3_66_817.zip"
unzip -t "$ZIP"
python3 - "$ZIP" <<'PY'
import hashlib, json, sys, zipfile
zp = sys.argv[1]
with zipfile.ZipFile(zp) as z:
    names = {n for n in z.namelist() if not n.endswith("/")}
    manifest = json.loads(z.read("STATIC_KB_MANIFEST.json"))
    expected = set(manifest["files"])
    assert names == expected | {"STATIC_KB_MANIFEST.json"}
    assert manifest["file_count"] == len(expected)
    for name, meta in manifest["files"].items():
        data = z.read(name)
        assert len(data) == meta["bytes"], name
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], name
print(f"PASS static-KB members={len(expected)}")
PY
```

Expected: ZIP test is clean; member/hash validator prints `PASS`.

- [ ] **Step 4: Run secret-safe and link scans on the extracted replacement**

```bash
EXTRACT="$REPO/reports/static-kb-v3_66_817-final/extracted"
case "$EXTRACT" in
  "$REPO"/reports/static-kb-v3_66_817-final/extracted) ;;
  *) echo "unsafe extraction path: $EXTRACT" >&2; exit 2 ;;
esac
rm -rf -- "$EXTRACT"
mkdir -p "$EXTRACT"
unzip -q "$ZIP" -d "$EXTRACT"
python3 tools/kb_link_validator.py --root "$EXTRACT" --json \
  > reports/static-kb-v3_66_817-final/link-scan.json
python3 -m detect_secrets scan "$EXTRACT" \
  > reports/static-kb-v3_66_817-final/secret-scan.json
python3 - <<'PY'
import json
from pathlib import Path
links = json.loads(Path("reports/static-kb-v3_66_817-final/link-scan.json").read_text())
secrets = json.loads(Path("reports/static-kb-v3_66_817-final/secret-scan.json").read_text())
assert links["broken_count"] == 0
assert sum(len(v) for v in secrets.get("results", {}).values()) == 0
PY
```

Expected: zero broken links and zero secret candidates. `detect-secrets` runs only in the isolated audit environment; if unavailable, this step is blocked rather than waived.

- [ ] **Step 5: Record final hashes and re-run sync checks**

```bash
sha256sum \
  "$ZIP" \
  project-knowledge/STATIC_KB_MANIFEST.json \
  reports/static-kb-v3_66_817-final/PROJECT_KNOWLEDGE_UPDATE.md \
  > reports/static-kb-v3_66_817-final/SHA256SUMS
python3 project-knowledge/bd-kb-sync check project-knowledge
```

Expected: working static root is current against the regenerated manifest. Update `DOC_HYGIENE_REPORT_2026-07-22.md` with these exact hashes and state explicitly that external paste/pin remain pending. Freshness against the external set is intentionally deferred to Task 11.

- [ ] **Step 6: Checkpoint only; do not commit**

Run the Checkpoint Protocol with `NN=10`, and additionally archive `reports/static-kb-v3_66_817-final/` into the task checkpoint after the secret scan passes.

---

### Task 11: Operator-gated external re-paste, pasted-state verification, and pin advance

**Files:**
- Operator input: `reports/static-kb-v3_66_817-final/BulkDownloader_project_files_v3_66_817.zip`
- External target: `/mnt/project`
- Operator-provided state path: environment variable `BD_SESSION_STATE`
- Update after success: completion report and tracker evidence only

**Interfaces:**
- Consumes: final verified replacement ZIP and an explicit operator confirmation that `/mnt/project` may be replaced.
- Produces: verified external static-KB set and a pin that references the manifest actually pasted.

- [ ] **Step 1: Stop and request explicit operator confirmation**

Present the ZIP path, SHA-256, member count, zero-secret result, and zero-broken-link result. Do not delete or replace `/mnt/project`, and do not pin, until the operator explicitly approves the external re-paste.

Expected: task pauses. Local stage remains valid evidence only for local packaging.

- [ ] **Step 2: Operator replaces the external set**

The operator deletes the old project-file set and pastes every member from the final ZIP, including `STATIC_KB_MANIFEST.json`, into `/mnt/project`.

Expected: no mixture of old/new project files remains.

- [ ] **Step 3: Verify the pasted state before pinning**

```bash
: "${BD_SESSION_STATE:?Set BD_SESSION_STATE to the current session STATE.json}"
python3 project-knowledge/bd-kb-sync verify /mnt/project
python3 project-knowledge/bd-kb-sync diff \
  /mnt/project project-knowledge/STATIC_KB_MANIFEST.json
```

Expected: strict integrity exits `0`; freshness reports current. Any failure blocks pin advancement.

- [ ] **Step 4: Advance the pin only after pasted-state verification**

```bash
python3 project-knowledge/bd-kb-sync \
  pin /mnt/project --state "$BD_SESSION_STATE"
python3 project-knowledge/bd-kb-sync \
  verify /mnt/project --state "$BD_SESSION_STATE"
```

Expected: pin points to the manifest inside the verified pasted set; verify-with-state exits `0`.

- [ ] **Step 5: Record operator evidence without committing**

Update the tracker and completion report with:

- paste timestamp;
- pasted ZIP SHA-256;
- pasted manifest SHA-256;
- verify exit code `0`;
- freshness exit code `0`;
- pin verification exit code `0`;
- operator identity as a non-secret label only.

Run tracker render/check and the Checkpoint Protocol with `NN=11`. Do not commit, merge, push, release, or deploy.

---

## Final Integrated Review Gate

Before handing the checkpoint to the user:

```bash
cd "$REPO"
python3 -m tools.code_intelligence.snapshot \
  --root "$REPO" --scope production \
  --check reports/code-intelligence-v3_66_817/artifacts/PRODUCTION_TREE.json
python3 tools/bd-audit-gate.py \
  --root "$REPO" \
  --artifacts reports/code-intelligence-v3_66_817/artifacts \
  --required all \
  --json-out reports/code-intelligence-v3_66_817/artifacts/FINAL_AUDIT_GATE.json
python3 tools/code_intelligence/factcheck_advanced_knowledge.py \
  --document project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md \
  --artifacts reports/code-intelligence-v3_66_817/artifacts \
  --root "$REPO" \
  --json-out reports/code-intelligence-v3_66_817/FINAL_FACTCHECK.json
python3 tools/tasktracker_gen.py --check "$REPO"
python3 project-knowledge/bd-kb-sync check project-knowledge
python3 -m pytest -q \
  tests/code_intelligence \
  tests/test_consolidation.py \
  tests/test_documentation_hygiene_20260723.py \
  tests/test_tasktracker_gen.py \
  project-knowledge/test_bd_kb_sync.py
git diff --check
git status --short
```

Expected: every machine gate exits `0`; the worktree remains intentionally uncommitted; the final report distinguishes advisory/open/blocked items from failures and states whether the external re-paste/pin was completed or remains operator-pending.
