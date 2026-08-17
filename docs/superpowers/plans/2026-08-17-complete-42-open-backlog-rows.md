# Complete the 42 Open Backlog Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-derive and terminally adjudicate all 42 canonical OPEN backlog rows through nine exact-SHA cuts, leaving unavailable live/operator predicates OPEN with complete runbooks rather than weakening their acceptance criteria.

**Architecture:** The program is a sequential integration pipeline of nine independently reviewable cuts. Each cut owns one coherent subject, starts from current `origin/main`, uses focused RED evidence before behavioral changes, and closes with exact-head verification, review, CI, merge-tree proof, applicable deployment, and canonical backlog reconciliation. Read-only discovery and capture classification may run ahead on immutable bases, but there is one authoritative integrator and no final evidence transfers across source SHAs.

**Tech Stack:** Python 3 repository tools and pytest, pytest-xdist fixed `-n 24`, Bash deployment/release tooling, React/Vitest/Vite frontend gates, Git/GitHub Actions, JSON/Markdown machine authorities, WACZ capture evidence, systemd service verification.

## Global Constraints

- Canonical task authority is only `project-knowledge/IMPROVEMENT_BACKLOG.md`.
- Starting authoritative merge is `3e8de4ff763a4c0942547ca39322e54ae2cc14c8`, tree `058af611c010dbadef926468db2c3d2daef37969`, version `3.66.1170`; fetch and rebind before execution.
- Every assigned row must end `CLOSED`, `MOOT`, or remain `OPEN` with a complete operator runbook.
- Never relax a criterion merely because retained evidence cannot meet it.
- Existing evidence in `/home/mboyle/captures/` is authorized for read-only inventory and analysis; unredacted captures and secrets never enter Git.
- Operator runbooks live under `/home/mboyle/agent-runs/backlog-42/operator-runbooks/` and explicitly disclaim task authority.
- One authoritative integrator owns branches, commits, pushes, PR metadata, merges, deployment, and backlog status.
- Use repository-required `venv/bin/python` and preserve `env -u BD_INSTALL_DIR` where the command contract requires it.
- Canonical real-pytest full-suite worker count is fixed `-n 24`; serial execution is diagnostic unless separately promoted.
- No concurrent lane shares a writable checkout, virtual environment, HOME, TMPDIR, cache, database, ports, generated directory, or result directory.
- Missing, malformed, stale, wrong-SHA, truncated, zero-denominator, digest-mismatched, or transport-failed evidence is `UNKNOWN/HOLD`.
- Deploy only when runtime, source delivery, generated runtime artifacts, or deployment state changes. A deployment must verify exact merged SHA, `/api/health`, version, database state, service state, and `GET / = 200`.
- The approved design is `docs/superpowers/specs/2026-08-17-complete-42-open-backlog-rows-design.md` at commit `68175b8`.

---

### Task 1: Establish the Program Ledger and Immutable Starting Evidence

**Files:**
- Create: `/home/mboyle/agent-runs/backlog-42/program.json`
- Create: `/home/mboyle/agent-runs/backlog-42/cut-map.json`
- Create: `/home/mboyle/agent-runs/backlog-42/evidence-schema.json`
- Read: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Read: `CLAUDE.md`
- Read: `docs/superpowers/specs/2026-08-17-complete-42-open-backlog-rows-design.md`

**Interfaces:**
- Consumes: current official `main`, the canonical backlog table, and the approved nine-cut design.
- Produces: immutable program identity, exact 42-ID ownership map, per-cut dependencies, and the result schema used by Tasks 2–11.

- [ ] **Step 1: Rebind the program to official current main**

Run:

```bash
git fetch origin main
git status -sb
git rev-parse HEAD origin/main HEAD^{tree}
git rev-list --left-right --count origin/main...HEAD
git status --porcelain=v1 -uall
```

Expected: the checkout is clean; any local planning commits are identified explicitly; the implementation base is an official-origin-contained commit and its tree is recorded rather than assumed from this document.

- [ ] **Step 2: Extract the exact OPEN-row denominator**

Run a repository-venv Python parser over the Markdown table. Reject duplicate IDs, unknown statuses, malformed rows, disagreement with the header's `rows`, `open`, or `ids-sha256`, and any OPEN set other than these 42 starting IDs:

```text
26 27 28 104 106 109 112 114 115 118 119 120 121 122 123 124 125 126
127 128 129 130 131 132 133 134 135 136 137 138 139 140 141 143 150
151 152 160 161 162 163 164
```

Expected: exactly 42 unique OPEN rows and no unexplained ID.

- [ ] **Step 3: Write the machine-readable cut map atomically**

Record exact arrays:

```json
{
  "A": [112,134,135,136,137,138,140,141,143],
  "B": [106,114,160],
  "C": [109,129,133,152],
  "D": [104,132,150,151,162],
  "E": [115,161],
  "F": [26,27,28,118,130,131],
  "G": [139],
  "H": [119,120,121,122,123,124,125,126,163],
  "I": [127,128,164]
}
```

Assert union equals the starting set, intersection between every pair is empty, and dependencies include `A -> B`, `132 -> {104,150,151,162}`, and `115 -> 161`.

- [ ] **Step 4: Define the external result schema**

Require each row record to contain `row_id`, `cut`, `base_sha`, `base_tree`, `candidate_sha`, `candidate_tree`, `disposition`, `acceptance_denominator`, `evidence_paths`, `evidence_sha256`, `tests`, `review_verdicts`, `pr`, `merge_sha`, `deployment`, `unknowns`, and `completion_marker`. Permit dispositions only `CLOSED`, `MOOT`, and `OPEN_RUNBOOK`.

- [ ] **Step 5: Validate and hash the program files**

Run strict JSON parsing with duplicate-key rejection, validate the 42-ID union, then record byte sizes and SHA-256 hashes in `/home/mboyle/agent-runs/backlog-42/program-files.sha256`.

- [ ] **Step 6: Commit only if repository files changed**

The external ledger is not committed. If no repository file changed, record `NO_REPOSITORY_COMMIT` and continue. Do not create a content-free version bump.

---

### Task 2: Cut A — Backlog Truth and Stale-Current Adjudication (v3.66.1171)

**Rows:** 112, 134, 135, 136, 137, 138, 140, 141, 143

**Files:**
- Modify or delete after re-derivation: `project-knowledge/ARCHITECTURE_INVENTORY.md`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify only when current facts require it: `project-knowledge/UI_TOKENS.md`, `project-knowledge/KNOWN_FLAKES.md`, `project-knowledge/CODE_INTELLIGENCE_PROGRAM.md`, `project-knowledge/OPERATOR_POLICY_DECISIONS.md`, `docs/template_health_cockpit.md`, `project-knowledge/DEFECT_PATTERN_CATALOG.md`, `project-knowledge/kb/decomp/DECOMP_TOOLS_README.md`, `project-knowledge/DECOMP_HAZARD_REGISTER.md`, `project-knowledge/KB_JUDGMENT.md`
- Create: `tests/test_v3_66_1171_backlog_truth_is_current.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`
- Modify generated/version files required by repository policy.

**Interfaces:**
- Consumes: Task 1 cut map and exact current source/history/document census.
- Produces: current document classifications, exact completion-ledger measurement, honest backlog statuses, and the document ownership map required by Cut B.

- [ ] **Step 1: Create an immutable Cut A discovery checkout and evidence root**

Create `/home/mboyle/agent-runs/backlog-42/cut-a/<base-sha>/`, record Git identity and clean state, and inventory every named document plus all tracked readers with `git grep` and generated-manifest references.

- [ ] **Step 2: Re-derive each row independently**

For row 112, compute current architecture counts from source rather than copying the snapshot. For rows 134–137, prove which program artifacts and deliverables exist. For row 138, enumerate the four absent CLIs, the present coverage-map CLI, and the five shipped-but-unwired frontends. For row 140, derive the 73 inline finding IDs and their 41 batch identities. For row 141, validate every named stale-current document. For row 143, parse every CLOSED row and identify text that expresses unfinished work without `PARTIAL`.

- [ ] **Step 3: Write the focused RED gate**

Add tests that fail on the current misstatements: stale architecture identity, absent completion ledger, wrong code-intelligence availability, stale-current document claims, and CLOSED rows hiding machine-invisible remainders. Include mutation fixtures proving that deleting one ledger batch, moving one remainder into prose, or changing a current tool's presence is detected.

- [ ] **Step 4: Run the RED gate**

Run:

```bash
env -u BD_INSTALL_DIR venv/bin/python -m pytest -q tests/test_v3_66_1171_backlog_truth_is_current.py
```

Expected: nonzero with each intended stale subject exercised; save JUnit, complete log, raw status, and clean pre/post state.

- [ ] **Step 5: Apply minimal adjudications**

Regenerate or retire the architecture inventory, correct current documentation, add the measured line-audit completion ledger at its focused owner, and update backlog statuses. Mark a program `MOOT` only when current evidence proves its subject obsolete; otherwise replace it with one atomic OPEN residual. Split every genuine hidden remainder into an OPEN row or use the sanctioned `PARTIAL` status form with explicit evidence.

- [ ] **Step 6: Wire the new gate directly into CI**

Declare its honest `BD_GATE_SCOPE`, add it once to the applicable CI shard and once to `_DECLARED`, and run the shard-union/uniqueness tests.

- [ ] **Step 7: Regenerate and verify metadata**

Bump to v3.66.1171, update changelog and settings pin, regenerate PIN/static/dependency artifacts through the sanctioned order, and require clean regeneration.

- [ ] **Step 8: Execute final Cut A verification**

Run focused tests, affected band, generated/freshness gates, release tests, frontend tests/build, package verification, and the exact canonical `-n 24` full suite. Preserve all failed/superseded attempts.

- [ ] **Step 9: Review, publish, merge, and transition**

Obtain implementation/security, test-integrity, and evidence reviews; push one coherent candidate; require exact-head CI; refresh/read back the PR body; merge only the reviewed tree; deploy only if runtime delivery changed. Update all nine result records and the canonical backlog.

---

### Task 3: Cut B — Freshness and Historical-Evidence Closure (v3.66.1172)

**Rows:** 106, 114, 160

**Files:**
- Modify: `toolchain/bin/bd-freshcheck`
- Modify or delete after migration: `project-knowledge/AUDIT_2026_07_29.md`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify/delete: the complete measured legacy-tool surfaces identified for row 160
- Create: `tests/test_v3_66_1172_nested_freshness_and_legacy_retirement.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`
- Modify generated/version files required by repository policy.

**Interfaces:**
- Consumes: Cut A's document ownership/classification map.
- Produces: recursive task-bearing-document freshness, migrated audit findings, and a complete guarded legacy-tool retirement.

- [ ] **Step 1: Enumerate freshness producers and consumers**

Use `git ls-files -z`, not filesystem glob assumptions, to derive nested task-bearing docs, current freshness readers, ignored/generated boundaries, and every CI/release consumer. Record a nonzero tracked denominator and exclusions with reasons.

- [ ] **Step 2: Re-derive row 114 findings**

Parse the old report's remaining findings, map each to current source/tests/backlog, and classify it `already fixed`, `atomic residual`, or `obsolete`. No finding may disappear merely because the report is deleted.

- [ ] **Step 3: Reconstruct row 160's exact tool set from history**

Identify the previously missing twelfth name through Git history and current surfaces. Enumerate executable, mirror, catalog, selftest, documentation, packaging, and CI occurrences. Add denominator guards to `bd-coretest` and `bd-tool-lint` before removal.

- [ ] **Step 4: Write and run RED tests**

The test must fail when a nested task document is omitted, a migrated audit finding lacks an owner, any of the exact retired tools survives as a file/dangling symlink/reference, or a tool denominator shrinks and certifies itself. Run the focused file and preserve exact RED evidence.

- [ ] **Step 5: Implement recursive freshness and retirement**

Use bounded tracked-path discovery, explicit exclusions, and fail-closed read errors. Migrate real findings into atomic backlog rows or point-of-use enforcement, retire the old report, and remove the exact measured legacy-tool set and consumers.

- [ ] **Step 6: Complete v1172 gates and merge**

Directly wire the test, regenerate metadata, run focused/affected/generated/release/frontend/package/full-`n24`, obtain three READY reviews and exact-head CI, merge with tree equivalence, and deploy only if runtime/tool delivery requires it.

---

### Task 4: Cut C — Gate-Scope and Declaration Integrity (v3.66.1173)

**Rows:** 109, 129, 133, 152

**Files:**
- Modify: `toolchain/bin/bd-band-derive`
- Modify: `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`
- Modify: `tests/gate_scope_baseline.txt`
- Modify: `FOOTGUNS.json`
- Create: `tests/test_v3_66_1173_gate_declarations_are_semantically_honest.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify generated/version files required by policy.

**Interfaces:**
- Consumes: current CI shard membership, test dependency/reference graph, and canonical footgun registry.
- Produces: rename-stable affected-band wiring, adjudicated baseline entries, and machine-checked declaration honesty.

- [ ] **Step 1: Measure scope semantics**

For every gate test, derive what paths it reads or enumerates using AST/static references plus controlled mutation probes. Record declared scope, measured scope, CI placement, and baseline status. Independently enumerate every footgun's detector, status, severity, and enforcement outcome.

- [ ] **Step 2: Define conservative classification rules**

Classify repo-wide when a test enumerates tracked files, source trees, manifests, CI, packaging, or registries; classify module only when all inputs are bounded to its module fixtures. Ambiguous or dynamic cases remain repo-wide. A blocking footgun must have a blocking enforcement path or an explicit reviewed reason why it cannot.

- [ ] **Step 3: Write RED mutation controls**

Rename a temporary gate file while retaining content, misdeclare a repo-wide fixture as module, remove a baseline entry, and downgrade a mechanically blocking footgun to advisory. Require each mutation to fail for the intended semantic reason.

- [ ] **Step 4: Implement the smallest semantic verifier**

Bind tests by stable content/subject identity rather than filename alone, migrate or justify every baseline entry, and validate footgun declarations against resolved detector behavior. Do not create a second gate or footgun registry.

- [ ] **Step 5: Complete v1173 verification and merge**

Directly wire the new gate; regenerate metadata; run focused mutation, affected, generated, release, frontend, package, and full-`n24` lanes; obtain three READY reviews and exact-head CI; merge and deploy only if runtime behavior changed.

---

### Task 5: Cut D — Failure Retention and Identity-Safe Cleanup (v3.66.1174)

**Rows:** 104, 132, 150, 151, 162

**Files:**
- Modify: `tests/_tmproot.py`
- Modify: `toolchain/bin/bd-gc`
- Modify: `tools/reap_orphan_tempdirs.py`
- Modify: `bulk_downloader/crash_recovery.py`
- Create: `tests/test_v3_66_1174_identity_safe_failure_cleanup.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify CI/generated/version files required by policy.

**Interfaces:**
- Consumes: measured temporary-root producers, retention semantics, filesystem identities, and cleanup callers.
- Produces: explicit failed-run lifecycle states and identity-safe bounded removal of every owned residue class.

- [ ] **Step 1: Write the retention state machine before cleanup code**

Define `ACTIVE`, `PASSED_REAPABLE`, `FAILED_RETAINED`, `FAILED_RELEASED`, and `UNKNOWN`. Record which actor may transition each state, the evidence that authorizes it, retention duration/count, and how forensic preservation is released. Unknown identity or state always refuses deletion.

- [ ] **Step 2: Inventory every producer and cleanup denominator**

Measure pytest roots, `bd-*`, `bdcut_*`, `.bdrm-*`, nested `*.part`, private rename forms, and every caller. Distinguish tracked tool behavior from ambient `/tmp` data and prove no prefix can cross into unrelated directories.

- [ ] **Step 3: Write filesystem RED tests**

Use a persistent local filesystem and deterministic fixtures for replacement-after-inspection, symlink/dangling symlink, rename interruption, killed deletion, nested partials, permission denial, close failure, and failed-run retention. Assert inode identity, link state, call counts, survivors, raw failures, and no swallowed `BaseException`.

- [ ] **Step 4: Implement identity-safe cleanup**

Open/inspect the candidate, rename it to a private name within the same parent, revalidate identity through a held descriptor, and destroy only the held object. Record incomplete private names for later bounded recovery. Remove `ignore_errors=True`; actionable failures preserve the subject and return nonzero/UNKNOWN.

- [ ] **Step 5: Add failure injection and denominator reconciliation**

Prove missing/duplicate/omitted prefixes, zero candidates, unexpected symlinks, disk/permission failures, cancellation, and truncated state cannot yield CLEAN. Ensure failed retained roots are never reaped until policy release.

- [ ] **Step 6: Complete v1174 verification and deployment**

Run focused filesystem-security tests serially on persistent storage, affected lanes, generated/release/frontend/package, canonical `n24`, reviews, and exact-head CI. Merge with tree proof. Because cleanup tools affect delivered runtime/tooling, deploy exact merged main and verify service health.

---

### Task 6: Cut E — Release-Path Retirement and Sandbox Residue (v3.66.1175)

**Rows:** 115, 161

**Files:**
- Delete: `scripts/build_release.sh`
- Modify: `tests/test_sandbox_home_stays_retired.py`
- Modify: `tests/test_generated_artifact_workflow.py`
- Modify: `tools/build_release.py`
- Modify: release and generated-workflow tests that currently inspect the shell script
- Modify: current executable defaults and operator documentation containing retired sandbox paths
- Create: `tests/test_v3_66_1175_legacy_release_and_sandbox_paths_are_retired.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify CI/generated/version files required by policy.

**Interfaces:**
- Consumes: current release implementation, package member contract, tracked-source denominator, and sandbox-path census.
- Produces: one supported release path and a machine-visible three-class residue disposition.

- [ ] **Step 1: Prove release-path parity before deletion**

Run both release implementations in isolated exact-base checkouts with identical prepared generated/frontend inputs. Compare archive member sets, hashes where deterministic, required exclusions, version/build metadata, failure behavior, and exit status. Any unique required behavior in the shell path must move to `tools/build_release.py` with a focused RED test.

- [ ] **Step 2: Write the retirement RED gate**

Assert the shell path is tracked/physical on base, all current callers are enumerated, and a synthetic dangling symlink or renamed wrapper is detected. Assert the supported builder alone satisfies package tests.

- [ ] **Step 3: Retire row 115 completely**

Migrate consumers, delete the shell script, update tracked-source denominators and generated-workflow order checks, and prove no live executable, CI, docs, packaging, or test consumer remains.

- [ ] **Step 4: Perform row 161's three-phase sweep**

Classify every retired sandbox literal as `LIVE_EXECUTABLE`, `CURRENT_OPERATOR_PROSE`, `HISTORICAL`, or `ADVERSARIAL_FIXTURE`. Replace only the first two with repository-relative or explicit operator-controlled paths. Preserve historical and adversarial subjects with exact exemptions.

- [ ] **Step 5: Complete v1175 verification and deployment**

Run retirement adversaries, full release/package equivalence, affected/generated/frontend/full-`n24`, three reviews, and exact-head CI. Merge with tree equivalence and deploy because release/source-delivery tooling changed.

---

### Task 7: Cut F — Test-Assurance Expansion (v3.66.1176)

**Rows:** 26, 27, 28, 118, 130, 131

**Files:**
- Modify: `tests/test_v3_66_1098_no_assertion_can_be_trivially_true.py`
- Modify or extend: current mutation tooling under `toolchain/bin/bd-mutate`
- Create: `tests/mutation_specs/v1098_vacuous_assertions.json`
- Create: `tests/mutation_specs/v1108_false_assertions.json`
- Create: `tests/mutation_specs/v1176_assurance_controls.json`
- Modify: `tests/test_source_windows_do_not_shift.py`
- Create: `tests/test_v3_66_1176_test_assurance_denominators.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify CI/generated/version files required by policy.

**Interfaces:**
- Consumes: current test ASTs, fixture graph, write surfaces, mutation runner, source windows, and canonical suite population.
- Produces: reproducible assurance detectors and a measured serial-suite record without changing the canonical worker policy.

- [ ] **Step 1: Define bounded detector slices**

For vacuity, cover variable-bound constants, unreachable assertions, and assertions independent of the subject under test. For over-sensitivity, compare asserted output fields with the behavioral contract and fixture-owned nondeterminism. Refuse unverifiable semantic guesses rather than reporting a false denominator.

- [ ] **Step 2: Build the filesystem-write recorder**

Instrument Python file mutations and subprocess-visible target paths in an isolated test process. Record operation, resolved path, pre/post identity, caller, and outcome. Inject direct writes, atomic renames, symlink traversal attempts, subprocess writes, and denied writes; any unobserved positive control fails the recorder.

- [ ] **Step 3: Make mutation specifications reproducible**

Define strict duplicate-rejecting JSON specs with source SHA/tree, target, exact mutation, expected RED tests, restoration hash, and completion marker. Add specs for the v1098 and recent roadmap mutation batteries. `bd-mutate` refuses dirty/wrong-SHA/stale-target specs and always restores exact bytes.

- [ ] **Step 4: Strengthen source-window identity**

Hash normalized window contents and stable anchors, not only window counts. Add RED controls for moving content inside a constant-count window, duplicate anchors, deleted anchors, and ambiguous matches.

- [ ] **Step 5: Measure the serial denominator**

Collect the exact current real-pytest population, then run one isolated serial full suite with the sanctioned environment except xdist removal. Record node IDs, outcomes, order, wall time, residue, and differences from canonical `n24`. Failures are findings, not promotion blockers for unrelated implementation; row 131 closes when the measurement is complete and honestly reported.

- [ ] **Step 6: Prove detector precision**

Run mutation/adversarial batteries with caught/escaped/invalid/error denominators. Inspect every new live finding; fix true defects or narrow an over-sensitive rule with an explicit positive control. Do not add blanket ignores.

- [ ] **Step 7: Complete v1176 verification and merge**

Run focused/affected/generated/release/frontend/package/canonical-`n24`, preserve the serial result separately, obtain three reviews and exact-head CI, merge with tree proof, and deploy only if delivered runtime tooling changed.

---

### Task 8: Cut G — Reviewed Defect Suppressions (v3.66.1177)

**Row:** 139

**Files:**
- Create: `DEFECT_SUPPRESSIONS.json`
- Modify: `toolchain/bin/bd-defect-scan`
- Modify: detector parity/source metadata tests
- Create: `tests/test_v3_66_1177_defect_suppressions_are_ast_bound.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Modify CI/generated/version/package files required by policy.

**Interfaces:**
- Consumes: detector IDs, repository-relative findings, normalized Python AST, and strict machine-authority conventions.
- Produces: reviewed suppressions that expire automatically on semantic change.

- [ ] **Step 1: Write the strict suppression schema and RED tests**

Require schema version, detector ID, repository-relative path, normalized-AST SHA-256, rationale, and reviewer provenance. Reject duplicate JSON keys, duplicate semantic identities, unknown detectors, absolute/traversing paths, missing files, malformed AST hashes, unreadable files, and empty authorities.

- [ ] **Step 2: Add positive and expiry controls**

Create a fixture that reports without suppression, disappears with an exact suppression, and reappears after a semantic AST edit. Whitespace/comment-only changes must retain the normalized AST identity; code changes must not.

- [ ] **Step 3: Implement one canonical loader**

Runtime, CI, and packaging consume the same strict interpretation. No embedded fallback suppression set is allowed. Missing authority behavior is explicit: an empty intentional registry is represented by a valid nonempty document with zero entries only if the schema and tests choose that policy.

- [ ] **Step 4: Review every initial suppression**

Run the scanner without suppressions, classify each finding, fix real defects, and add only reviewed false-positive or accepted-risk entries with exact rationale. Record unsuppressed, suppressed, stale, and error counts.

- [ ] **Step 5: Complete v1177 verification and deployment**

Run focused mutation, scanner parity, affected, generated, release, frontend, package, and canonical `n24`; obtain three READY reviews and exact-head CI; merge with tree proof and deploy because delivered scanner tooling changed.

---

### Task 9: Prepare the Capture Evidence Index Before Cut H

**Files:**
- Create: `/home/mboyle/agent-runs/backlog-42/captures/inventory.jsonl`
- Create: `/home/mboyle/agent-runs/backlog-42/captures/index.json`
- Create: `/home/mboyle/agent-runs/backlog-42/captures/files.sha256`
- Read only: `/home/mboyle/captures/`

**Interfaces:**
- Consumes: existing WACZ/redacted WACZ/template/draft files and row 119–126/163 acceptance predicates.
- Produces: a privacy-aware, content-addressed evidence index for Cut H; it is not merge evidence by itself.

- [ ] **Step 1: Inventory without opening unrelated content**

Record relative path, file type, byte size, mtime, SHA-256, redaction-name signal, WACZ structural validity, and paired raw/redacted identity. Do not print or store credentials, cookies, authorization headers, private keys, or unrelated payload bodies.

- [ ] **Step 2: Extract bounded metadata**

For WACZs, validate ZIP/WACZ structure and extract only required provenance, URL host classification, capture timestamps, resource counts, and the event/recognizer fields needed by rows 119–126/163. Store excerpts only as hashes plus byte/line coordinates unless a redacted non-secret excerpt is necessary.

- [ ] **Step 3: Map evidence to predicates**

For each target row, classify every candidate artifact as `DIRECT`, `SUPPORTING`, `INSUFFICIENT`, or `UNRELATED`, with a reason. A filename or later authenticated activity cannot substitute for an unobserved detector-cleared/resume event.

- [ ] **Step 4: Validate and freeze the index**

Require every indexed file to rehash, every row 119–126/163 to have an entry even when no evidence qualifies, and no absolute secret-bearing excerpt in output. Write atomically and hash the final index.

---

### Task 10: Cut H — Capture and Authenticated-Scene Evidence (v3.66.1178)

**Rows:** 119, 120, 121, 122, 123, 124, 125, 126, 163

**Files:**
- Modify only when an offline or live reproducer proves a code/test/document defect in the relevant capture/template/login/selector components.
- Create: `tests/test_v3_66_1178_capture_evidence_contracts.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Create external runbooks as needed under `/home/mboyle/agent-runs/backlog-42/operator-runbooks/`.
- Modify CI/generated/version files required by any repository change.

**Interfaces:**
- Consumes: Task 9 capture index and current capture/template/login implementations.
- Produces: evidence-backed row dispositions, focused fixes where proved necessary, and exact operator runbooks for unavailable predicates.

- [ ] **Step 1: Re-derive nine exact acceptance checklists**

Write one machine-readable checklist per row with required observable event, accepted artifact source, denominator, redaction constraints, and terminal condition. Preserve row 123's `api_patterns >= 1` requirement unless an explicit decision retires it.

- [ ] **Step 2: Execute all offline evidence analyses**

Replay or inspect authorized redacted captures through current production parsers in isolated state. Cover challenge settle/resume, JWPlayer signing/template recognition, cross-origin step transitions, explicit detector-cleared/resume events, Reptyle API patterns, A6.2/A6.3 recognizers, five selector kinds, and host/sibling-domain matching.

- [ ] **Step 3: Add RED tests for proved implementation gaps**

For each actual defect, create a minimal redacted/synthetic fixture derived from the mechanism, assert a nonzero production-path seam, run it RED on the exact base, and implement the smallest correction. Do not commit an unredacted WACZ or replace a live acceptance predicate with a synthetic unit test.

- [ ] **Step 4: Close or moot rows supported by exact evidence**

Update a row only when the checklist is completely satisfied. An explicit operator decision may retire a legacy criterion as `MOOT`; the evidence must state why the subject no longer belongs to the current product contract.

- [ ] **Step 5: Write runbooks for unavailable evidence**

For each remaining row, create `<row-id>-<subject>.md` with exact target, prerequisites, operator action, safe commands/UI steps, expected events, artifact paths/hashes, redaction, pass/fail/UNKNOWN semantics, rollback, cleanup, and canonical backlog update procedure. Leave the row OPEN.

- [ ] **Step 6: Complete v1178 verification and deployment**

If repository behavior changed, run focused/affected/generated/release/frontend/package/full-`n24`, reviews, exact-head CI, merge, and deployment. If the cut is evidence-only, make only justified backlog/test/document changes and do not restart the service. Record each of the nine row dispositions independently.

---

### Task 11: Cut I — Operator and Service-Bound Decisions (v3.66.1179)

**Rows:** 127, 128, 164

**Files:**
- Modify: `bulk_downloader/ai_boot_readiness.py`
- Modify: `bulk_downloader/ai_boot_status.py`
- Modify: `bulk_downloader/app_ai.py`
- Modify: `tests/test_ai_boot_readiness.py`
- Modify: `tests/test_ai_boot_status.py`
- Modify: `tests/test_ai_boot_status_api.py`
- Modify: `tests/test_ai_boot_service_install.py`
- Modify: `project-knowledge/IMPROVEMENT_BACKLOG.md`
- Create: `tests/test_v3_66_1179_ai_companion_observability.py`
- Create external runbooks: row 127 PostgreSQL soak and row 128 OPV decision when not immediately resolvable
- Modify CI/generated/version files required by policy.

**Interfaces:**
- Consumes: current PostgreSQL preflight/production state, retained OPV evidence, AI companion systemd/runtime behavior, and operator decisions.
- Produces: bounded AI companion observability and exact decision/soak procedures without falsely closing elapsed-time work.

- [ ] **Step 1: Re-run PostgreSQL preflight without changing production**

Record database reachability, dual-write state, shadow-read state, comparison denominator, disagreement count, cutover state, and current soak clock. If prerequisites are not met, prepare the exact enabling/rollback runbook; do not start cutover or a soak as a side effect of inspection.

- [ ] **Step 2: Inventory OPV-F3.1 retained evidence**

Hash and validate the original window's start/end, continuity, event denominator, interruptions, and completion criteria. Present the exact evidence-backed choices: close on sufficient retained evidence or restart from zero. If no standing prior approval resolves the choice, leave row 128 OPEN with its runbook.

- [ ] **Step 3: Write the row 164 RED tests**

Simulate a healthy main service with an indefinitely restarting companion. Require capture/status to expose companion state, retry count/rate, last failure, next retry, and an UNKNOWN/failed gate without blocking main startup. Assert sequential text/vision initialization and GPU-runtime proof remain intact; `nvidia-smi` availability alone cannot pass.

- [ ] **Step 4: Implement bounded companion observability**

Add the smallest status/collection surface and retry-contention bounds. Preserve default-OFF and main-service independence. Ensure unavailable systemd/GPU/Ollama state is UNKNOWN, not healthy.

- [ ] **Step 5: Execute authorized operator transitions only**

If the PostgreSQL prerequisites and existing authority permit starting the soak, record its exact start identity and leave row 127 OPEN until at least two weeks of valid evidence complete. Apply the OPV decision only when explicitly established. Never call a started clock a completed row.

- [ ] **Step 6: Complete v1179 verification and deployment**

Run focused service/AI tests, affected/generated/release/frontend/package/full-`n24`, three reviews, exact-head CI, merge-tree proof, and exact merged deployment. Verify both main service health and companion observability after deploy.

---

### Task 12: Reconcile the 42-Row Program and Publish the Terminal Report

**Files:**
- Create: `/home/mboyle/agent-runs/backlog-42/terminal-report.md`
- Create: `/home/mboyle/agent-runs/backlog-42/terminal-results.json`
- Create: `/home/mboyle/agent-runs/backlog-42/terminal-evidence.sha256`
- Modify only if required for truthful final metadata: `project-knowledge/IMPROVEMENT_BACKLOG.md`

**Interfaces:**
- Consumes: Tasks 2–11 result records, merged SHAs/trees, CI runs, deployments, runbooks, and final canonical backlog.
- Produces: exact disposition of every starting row and proof that no task disappeared or moved into a competing authority.

- [ ] **Step 1: Reconcile all starting IDs**

For each of the original 42 IDs, require exactly one terminal record with disposition `CLOSED`, `MOOT`, or `OPEN_RUNBOOK`. For `OPEN_RUNBOOK`, require an existing hashed runbook and an unchanged OPEN canonical row. Reject missing, duplicate, or unrecognized IDs.

- [ ] **Step 2: Validate every merged cut**

Confirm candidate/tree/base/parent, review verdicts, exact-head CI, PR/body, merge SHA/tree equivalence, changed paths, selected lane denominators, preserved failures, repository cleanliness, and applicable deployment health.

- [ ] **Step 3: Reconcile canonical backlog metadata**

Strictly parse all rows, statuses, evidence annotations, header row/open counts, and ordered-ID digest. Prove the 42 starting IDs remain present and that every status transition cites exact evidence.

- [ ] **Step 4: Prove runbooks are not a second authority**

Scan runbooks and indexes for explicit non-authority language and canonical backlog pointers. Ensure no independent status field, task queue, or numbering system can disagree with the backlog.

- [ ] **Step 5: Write and hash the terminal report**

List all 42 IDs with starting text, final disposition, evidence, PR/merge/deployment state, and remaining operator action. Include program wall time, test denominators, deployments, evidence roots, file sizes, hashes, and any time-bound follow-up such as a running PostgreSQL soak.

- [ ] **Step 6: Perform the final repository and service audit**

Run:

```bash
git fetch origin main
git status -sb
git rev-parse HEAD origin/main HEAD^{tree}
git rev-list --left-right --count origin/main...HEAD
git status --porcelain=v1 -uall
curl -fsS http://localhost:5555/api/health
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:5555/
systemctl is-active bulkdownloader
```

Expected: clean synchronized repository, zero unpushed state, active healthy service at the applicable final deployed version, HTTP 200, and a terminal manifest that validates every listed artifact.

- [ ] **Step 7: Stop at the program boundary**

Report the final dispositions and operator runbooks. Do not start newly discovered backlog work or a successor roadmap without a new approved design.
