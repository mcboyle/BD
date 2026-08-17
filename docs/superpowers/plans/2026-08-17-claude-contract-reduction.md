# CLAUDE Contract Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 2,399-line chronological `CLAUDE.md` with a concise sole agent contract while proving the disposition of all 306 base paragraphs and retiring the temporary paragraph-conservation subsystem.

**Architecture:** An external immutable semantic map binds every base paragraph to one of four dispositions and, where meaning survives, one of eight final contract sections. A new directly wired repo-wide gate validates the reduced contract and the physical/reference retirement of Cut 3 machinery. Existing focused runbooks and executable tools retain detailed procedures; no second prompt, casebook, or repository registry is created.

**Tech Stack:** Markdown, Python 3.12, pytest 9, Git, existing `bd-*` tools, GitHub Actions, JSON/SHA-256 evidence.

## Global Constraints

- Base source is `e0ca19444d40acad917f9cf21326270997d359b8`, tree `c4d7231ab33404ff292954e190617243c231db77`.
- The semantic-map denominator is exactly 306 base paragraphs and 306 unique 12-hex fingerprints.
- Final `CLAUDE.md` is the sole agent-facing contract and targets 250--400 lines without making length the semantic acceptance criterion.
- Allowed dispositions are exactly `RETAIN_CONCISELY`, `ENFORCED_AT_POINT_OF_USE`, `MOVED_TO_FOCUSED_RUNBOOK`, and `REMOVE_OBSOLETE_OR_DUPLICATE`.
- Final section IDs are exactly `A1` through `A8`; many source paragraphs may map to one section.
- No new permanent casebook, rule registry, prompt, framework, or authority is allowed.
- Use real pytest and `env -u BD_INSTALL_DIR`; the canonical full command remains fixed at `-n 24 --dist loadfile`.
- Do not trim CI, weaken tests, regenerate the old paragraph baseline, or use a prior-SHA result as final evidence.
- The design and implementation-plan documents are temporary execution artifacts and must be removed from the final candidate so Cut 6 plan/spec retirement remains true.

---

### Task 1: Build and validate the 306-row external semantic map

**Files:**
- Create external evidence: `/home/mboyle/agent-runs/cut10/base-e0ca194/semantic-map.json`
- Create external validator: `/home/mboyle/agent-runs/cut10/base-e0ca194/validate-semantic-map.py`
- Read: `CLAUDE.md`
- Read: `toolchain/bin/bd-contract-rules`
- Read: `project-knowledge/CONTRACT_RULES.baseline`

**Interfaces:**
- Consumes: base paragraph normalization and fingerprint semantics from `bd-contract-rules`.
- Produces: JSON object with `schema_version`, `base_sha`, `base_tree`, `source_sha256`, `paragraph_count`, `paragraphs`, and `completion_marker`; every paragraph row has `ordinal`, `fingerprint`, `paragraph_sha256`, `section`, `excerpt`, `disposition`, `final_rule_id`, `destination`, `evidence`, and `review_status`.

- [ ] **Step 1: Capture the immutable denominator**

Run:

```bash
git rev-parse HEAD HEAD^{tree}
venv/bin/python toolchain/bin/bd-contract-rules --root "$PWD"
sha256sum CLAUDE.md
```

Expected: source is the approved base lineage, the production gate reports 306 active and 306 corpus paragraphs with zero missing, and the source hash is recorded.

- [ ] **Step 2: Generate one draft row per paragraph**

Use the production parser via `SourceFileLoader`, enumerate in source order, and write atomically to the external evidence root. Every draft row initially carries a structurally invalid `review_status: "UNREVIEWED"`, so validation must fail before human classification.

- [ ] **Step 3: Prove the draft validator is RED**

Run:

```bash
venv/bin/python /home/mboyle/agent-runs/cut10/base-e0ca194/validate-semantic-map.py
```

Expected: exit 1 with a nonzero count of `UNREVIEWED` rows; it must not emit `COMPLETE`.

- [ ] **Step 4: Classify all 306 rows**

For each row, choose exactly one allowed disposition. `RETAIN_CONCISELY` rows reference `CLAUDE.md#A1` through `#A8`; point-of-use rows name an existing tracked tool/test/CI path; runbook rows name an existing focused authority; removal rows cite a superseding final rule, current-source fact, or Git/history evidence. Set `review_status` to `REVIEWED` only after checking the complete original paragraph.

- [ ] **Step 5: Validate exact coverage and hash the map**

The validator must independently re-read base `CLAUDE.md` via `git show e0ca194:CLAUDE.md`, recompute normalization, fingerprints, and paragraph SHA-256 values, and reject wrong base/tree, missing or duplicate ordinals/fingerprints, invalid dispositions, unresolved tracked destinations, nonexistent final IDs, empty evidence, malformed JSON, or a completion marker other than `COMPLETE`.

Run:

```bash
venv/bin/python /home/mboyle/agent-runs/cut10/base-e0ca194/validate-semantic-map.py
sha256sum /home/mboyle/agent-runs/cut10/base-e0ca194/semantic-map.json
```

Expected: `306/306 REVIEWED`, each disposition count reported, exit 0, and a durable SHA-256.

### Task 2: Write the Cut 10 repo-wide RED gate

**Files:**
- Create: `tests/test_v3_66_1170_claude_is_concise_authority.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`

**Interfaces:**
- Consumes: final section IDs `A1`--`A8`, exact retired paths, and focused destinations from the approved design.
- Produces: a directly wired `BD_GATE_SCOPE = "repo-wide"` suite with production helpers `_tracked_paths(root)`, `_retired_residue(root)`, `_current_agent_surfaces(root)`, and `_section_bodies(text)`.

- [ ] **Step 1: Add exact structural and semantic tests**

The new suite must assert:

```python
EXPECTED_HEADINGS = (
    "## A1 | Authority and scope",
    "## A2 | Authorization and state",
    "## A3 | Change lifecycle",
    "## A4 | Writer and Git safety",
    "## A5 | Verification",
    "## A6 | Release and deployment",
    "## A7 | Engineering invariants",
    "## A8 | Focused authorities and commands",
)
RETIRED = (
    "toolchain/bin/bd-contract-rules",
    "project-knowledge/CONTRACT_RULES.baseline",
    "tests/test_v3_66_1141_no_paragraph_leaves_undeclared.py",
)
```

It also requires exact tokens for `UNKNOWN`, `hold`, `wait`, `RED-first`, one coherent feature per cut, sole writer, no broad staging, version/changelog/PIN regeneration, `env -u BD_INSTALL_DIR`, fixed `-n 24`, `--dist loadfile`, `--timeout=240`, `--timeout-method=thread`, `-p no:randomly`, split-or-ask, force-with-lease, gitleaks/secret scanning, GitHub merge-commit non-rewrite, deployment inode behavior, partial deployment failure, nonzero denominators, exact SHA/tree/host evidence, and the sole-agent-contract rule.

- [ ] **Step 2: Add physical/reference and adversarial tests**

Use `git ls-files -z` with a `>1000` canary and `os.path.lexists` to reject tracked files and dangling symlinks at all retired paths. Scan all tracked readable text for current invocations while allowing only `CHANGELOG.md`, this regression file, and explicitly historical audit prose. Add a temporary Git fixture proving helpers catch a tracked dangling retired path, a renamed `.md`/`.txt` agent prompt, a missing final heading, a duplicated heading, and a retired command invocation.

- [ ] **Step 3: Wire the gate directly into CI**

Add the file exactly once to the toolchain gate shard and exactly once to `_DECLARED`. Preserve `BD_GATE_SCOPE = "repo-wide"` and let the existing shard-union/uniqueness assertions police the wiring.

- [ ] **Step 4: Run RED on the pre-reduction tree**

Run:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_v3_66_1170_claude_is_concise_authority.py \
  tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
```

Expected: meaningful failures for absent A1--A8 headings and all three present retirement paths; the adversarial helper controls themselves pass.

- [ ] **Step 5: Commit the RED battery**

```bash
git add .github/workflows/ci.yml tests/test_v3_66_1170_claude_is_concise_authority.py tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
git commit -m "test Cut 10 concise contract and retirement"
```

### Task 3: Replace `CLAUDE.md` with the eight-section contract

**Files:**
- Modify: `CLAUDE.md`
- Modify only when a mapped unique procedure needs clarification: `project-knowledge/TOUCHED_FILE_TO_TEST.md`
- Modify only when a mapped unique environment procedure needs clarification: `docs/repo/ENVIRONMENT_PROVISIONING.md`
- Modify only when a mapped unique deployment procedure needs clarification: `docs/repo/FRESH_HOST_BRINGUP.md`

**Interfaces:**
- Consumes: all 306 reviewed map rows and the A1--A8 architecture.
- Produces: one concise standing contract whose heading anchors satisfy Task 2 and whose detailed links resolve to existing focused owners.

- [ ] **Step 1: Draft A1--A4 from the mapped rules**

Write authority/scope, authorization/state, lifecycle, and writer/Git safety without incident chronology or volatile counts. Preserve task/merge/deploy boundaries, UNKNOWN, machine-visible deferrals, RED-first, one-feature-per-cut, sole-writer/path ownership, staging, no-reset, post-merge cleanup, force-with-lease, secret scanning, and merge-commit safety.

- [ ] **Step 2: Draft A5--A8 from the mapped rules**

Include the exact sanctioned command verbatim:

```bash
env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ -n 24 --dist loadfile --timeout=240 --timeout-method=thread -p no:randomly'
```

State that affected derivation is a floor; use real pytest; require complete denominators/raw status/logs; split or ask rather than trim CI. Preserve version/changelog/PIN steps, canonical regeneration, deploy inode/partial-failure semantics, health verification, denominator integrity, seam/negative tests, mutation safety, environment isolation, and a compact focused-authority table.

- [ ] **Step 3: Reconcile every retained/migrated map row**

For each non-removal row, verify its `final_rule_id` heading or destination exists and carries the material rule. For each removal row, verify its evidence identifies obsolete measurement, duplicated example, or superseding rule. Do not use the old baseline as proof.

- [ ] **Step 4: Run the structural gate while retirement remains RED**

Run the new suite and confirm all semantic/heading/link assertions pass while only the three physical retirement assertions remain failing. This separates contract correctness from deletion.

- [ ] **Step 5: Commit the coherent contract rewrite**

```bash
git add CLAUDE.md project-knowledge/TOUCHED_FILE_TO_TEST.md docs/repo/ENVIRONMENT_PROVISIONING.md docs/repo/FRESH_HOST_BRINGUP.md
git commit -m "cut/1170 reduce CLAUDE to standing authority"
```

Stage only paths that actually changed.

### Task 4: Retire Cut 3 conservation machinery

**Files:**
- Delete: `toolchain/bin/bd-contract-rules`
- Delete: `project-knowledge/CONTRACT_RULES.baseline`
- Delete: `tests/test_v3_66_1141_no_paragraph_leaves_undeclared.py`
- Modify: `tests/test_toolchain_534.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`
- Delete before final candidate: `docs/superpowers/specs/2026-08-17-claude-contract-reduction-design.md`
- Delete before final candidate: `docs/superpowers/plans/2026-08-17-claude-contract-reduction.md`

**Interfaces:**
- Consumes: green semantic structure from Task 3 and the durable external map.
- Produces: no tracked/invokable paragraph-conservation subsystem and no restored superpowers plan/spec tree.

- [ ] **Step 1: Remove direct consumers before deleting producers**

Remove `bd-contract-rules` from the tool selftest tuple, remove v1141 from the CI shard and `_DECLARED`, and retain the new v1170 direct wiring.

- [ ] **Step 2: Delete the exact retirement set**

Delete the tool, baseline, dedicated v1141 test, and the temporary approved design/plan documents. Do not delete history from `CHANGELOG.md`.

- [ ] **Step 3: Run retirement and consumer tests**

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_v3_66_1170_claude_is_concise_authority.py \
  tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py \
  tests/test_toolchain_534.py \
  tests/test_v3_66_1166_historical_docs_are_adjudicated.py
```

Expected: all pass; exact retired paths are untracked and `lexists` false; plans/specs remain absent.

- [ ] **Step 4: Commit retirement**

```bash
git add -u
git add tests/test_v3_66_1170_claude_is_concise_authority.py .github/workflows/ci.yml tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
git commit -m "retire temporary paragraph conservation machinery"
```

### Task 5: Version, changelog, and canonical regeneration

**Files:**
- Modify: `bulk_downloader/__init__.py`
- Modify: `tests/test_settings_center_slice4.py`
- Modify: `CHANGELOG.md`
- Regenerate as required: `PIN_INDEX.json`, `project-knowledge/STATIC_KB_MANIFEST.json`, `DEPENDENCY_GRAPH.json`, `DEPENDENCY_GRAPH.md`, `FUNCTION_INDEX.md`, and other outputs selected by `bd-regen-order`

**Interfaces:**
- Consumes: final source/test/doc tree.
- Produces: coherent version `3.66.1170` and deterministic generated artifacts.

- [ ] **Step 1: Write RED version/release expectations**

Update the version pin and prepend an ASCII-only changelog entry that names the 306-row semantic map, eight-section contract, retired paths, direct gate, and historical preservation. Before changing `__version__`, run the settings pin and confirm it fails against the old value.

- [ ] **Step 2: Bump source version and regenerate once, last**

Set `bulk_downloader.__version__ = "3.66.1170"`, then run:

```bash
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

Do not manually edit generator output.

- [ ] **Step 3: Run focused release/generated gates**

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest -q \
  tests/test_settings_center_slice4.py tests/test_versync_gate.py \
  tests/test_pin_index_in_sync.py tests/test_function_index_in_sync.py \
  tests/test_dependency_graph_in_sync.py tests/test_import_graph_no_new_edges.py \
  tests/test_v3_66_944_static_kb_manifest_describes_the_tree.py \
  tests/test_v3_66_947_the_kb_manifest_can_be_regenerated.py \
  tests/test_generated_artifact_workflow.py tests/test_release_hygiene_gates.py
```

- [ ] **Step 4: Commit the final coherent candidate**

```bash
git add bulk_downloader/__init__.py tests/test_settings_center_slice4.py CHANGELOG.md PIN_INDEX.json project-knowledge/STATIC_KB_MANIFEST.json DEPENDENCY_GRAPH.json DEPENDENCY_GRAPH.md FUNCTION_INDEX.md
git add -u
git diff --cached --check
git commit -m "release v3.66.1170 concise agent contract"
```

Stage only generated files that changed.

### Task 6: Exact-candidate verification and review

**Files:**
- Create external evidence: `/home/mboyle/agent-runs/cut10/$CANDIDATE/...`, where `CANDIDATE=$(git rev-parse HEAD)` is recorded before any lane starts
- No repository source changes unless a verified blocker requires refreeze.

**Interfaces:**
- Consumes: immutable pushed candidate SHA/tree.
- Produces: exact-SHA focused, affected, generated, release, frontend, packaging, canonical-full, review, and CI records with atomic completion markers and hashes.

- [ ] **Step 1: Freeze and push the candidate**

Record candidate, parent, base, tree, origin containment, clean status, changed paths, and version. Push the branch and open a draft PR with exact scope.

- [ ] **Step 2: Run focused and affected lanes**

Derive the band from every changed path with `bd-band-derive`; union it with the new v1170 gate, CI shard gate, toolchain suite, stale-plan retirement gate, freshness gates, generated gates, and release gates. Run real pytest with `env -u BD_INSTALL_DIR`, JUnit, nonzero expected/collected/executed denominators, clean pre/post status, raw exit, complete logs, and hashes.

- [ ] **Step 3: Run frontend, release, and packaging lanes**

Run 499 frontend tests and the production build. Build the release in an isolated exact-SHA checkout supplied with verified gitignored generated/frontend artifacts; prove the ZIP member set equals the source set and excludes retired paths.

- [ ] **Step 4: Run the exact canonical full suite**

```bash
CANDIDATE=$(git rev-parse HEAD)
EVIDENCE_ROOT=/home/mboyle/agent-runs/cut10/$CANDIDATE/full-n24
mkdir -p "$EVIDENCE_ROOT"
env -u BD_INSTALL_DIR bash -c "BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ -n 24 --dist loadfile --timeout=240 --timeout-method=thread -p no:randomly --junitxml=$EVIDENCE_ROOT/result.xml"
```

Require complete current collection, zero failure/error, explainable skip identities, no worker crash/internal error/timeout, and clean pre/post state. Preserve and classify every failed attempt.

- [ ] **Step 5: Obtain independent reviews and exact-head CI**

Run fresh implementation/scope, test-integrity/denominator, and evidence reviews against the immutable candidate. Run CodeRabbit if available. Require every GitHub Actions job to be terminal success on the exact head.

- [ ] **Step 6: Build and validate the terminal manifest**

Create a non-circular `terminal-evidence.sha256`, require every entry to rehash, and authenticate the final GitHub snapshot separately if it changes after manifest creation. Refresh the PR body with exact identities, complete changed paths, counts, manifest, review verdicts, CI run, packaging facts, and preserved failure classifications; read it back.

### Task 7: Merge, deploy, close the roadmap, and inventory remaining work

**Files:**
- Modify external roadmap: `/home/mboyle/agent-runs/BulkDownloader_MASTER_CUT_PLAN_2026-08-16.md`
- Create external report: `/home/mboyle/agent-runs/cut10/terminal-report.md`
- Create external backlog inventory: `/home/mboyle/agent-runs/cut10/remaining-canonical-backlog.json`

**Interfaces:**
- Consumes: merge commit/tree, deployment receipt, external semantic map, canonical backlog parser, and all terminal evidence.
- Produces: terminal MERGED/DEPLOYED Cut 10 record, complete master-roadmap closure, and the requested remaining-item list.

- [ ] **Step 1: Merge only the reviewed exact head**

Verify the PR is open/non-draft as intended, mergeable/clean, exact head, all required checks successful, and body current. Merge normally; fetch `origin/main`; prove candidate ancestry and merged tree equality.

- [ ] **Step 2: Deploy through the sanctioned path**

```bash
scripts/deploy.sh --dir /home/mboyle/BulkDownloader --timeout 180
```

Require service health version `3.66.1170`, `GET / = 200`, regenerated parity artifacts, graph pin, bytecode cleanup, and exact merged SHA.

- [ ] **Step 3: Update and hash terminal roadmap evidence**

Record Cut 10 base/candidate/merge/tree, 306-row map/hash/disposition counts, final CLAUDE line/word/byte counts, retired paths, test/review/CI/PR/deploy results, evidence root, rollback, roadmap line count, and SHA-256. Also reconcile the previously completed Cut 7--9 terminal facts if the master file remains stale.

- [ ] **Step 4: Generate the requested remaining-item inventory**

Parse `project-knowledge/IMPROVEMENT_BACKLOG.md` using its machine-visible schema. Record exact merged SHA/tree, total rows, status counts, every remaining `OPEN` row ID/label/text/evidence/dependencies, and separate operator-decision rows from engineering work. Validate unique IDs, exact open denominator, no rows sourced from historical plans/checklists, completion marker, and SHA-256. Produce a concise human grouping in the terminal report.

- [ ] **Step 5: Final completion audit**

Recheck every explicit master-roadmap requirement from row 148 through Cut 10 against current GitHub, repository, deployment, and roadmap evidence. Mark the persistent goal complete only when every required cut is terminal and no required evidence remains missing.
