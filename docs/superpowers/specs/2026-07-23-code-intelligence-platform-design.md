# Unified Code-Intelligence Platform Design

**Date:** 2026-07-23
**Repository:** BulkDownloader v3.66.817 line
**State:** Approved architecture; implementation not started
**Commit policy:** Keep this specification and all subsequent work uncommitted and unmerged until the final integrated review.

## 1. Goal

Complete the outstanding code-intelligence and audit program as one deterministic platform rather than a collection of disconnected tools. Reuse the existing graph, coverage, contract, fuzzing, reachability, invariant, and review-ledger foundations; expose the requested exact command names as thin stable frontends over shared modules.

The completed platform must:

- generate current, source-SHA-bound code-intelligence artifacts;
- detect stale review and knowledge state;
- run static and bounded dynamic checks;
- allocate risk-ranked L2/L3 review work without duplicate claims;
- turn confirmed findings into RED-test and invariant workflows;
- generate current advanced project knowledge;
- feed one composite audit gate; and
- stage one final static-KB replacement after all tracked content is stable.

## 2. Non-goals

- No production service behavior changes.
- No automatic code fixes or automatic promotion of findings.
- No arbitrary Python expression evaluation in probes or contracts.
- No network-dependent default analysis.
- No replacement of specialized tools that already provide stronger domain behavior.
- No declaration that heuristic call, taint, dead-code, or reachability results are proven facts.
- No commit, merge, push, external static-KB pin advancement, or release cut during implementation.

## 3. Global constraints

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
8. New behavior follows RED → GREEN → refactor. Each test must be observed failing for the intended missing behavior before implementation.

## 4. Architecture

### 4.1 Shared core

Create `tools/code_intelligence/` as the reusable implementation package:

- `artifacts.py` — deterministic JSON/SQLite serialization, input hashes, validation, and atomic replacement;
- `paths.py` — repository discovery, normalized relative paths, include/exclude policy;
- `schemas.py` — schema validators and migrations for graph projections, review state, contracts, invariants, findings, claims, and audit results;
- `snapshot.py` — tracked-tree manifest and source-SHA calculation;
- `results.py` — normalized `pass`, `fail`, `advisory`, `unknown`, `timeout`, and `error` results;
- `adapters.py` — typed adapter registry for existing coverage, oracle, fuzz, contract, and reachability tools;
- `locking.py` — portable lock/claim protocol for review allocation.

The package has no Flask application import at module import time. Dynamic adapters import application modules only inside bounded executions.

### 4.2 Canonical graph

Extend `tools/l0_extract.py` and `tools/graph_build.py` rather than creating a second graph.

L0 function facts additionally retain:

- positional-only, positional, keyword-only, vararg, and kwargs parameters;
- normalized defaults and annotations;
- return annotation and structural return facts;
- raised exception types;
- decorators and explicit/manual auth-gate calls;
- configuration reads and writes;
- lock, thread, process, async, queue, and scheduler operations;
- metric emission sites;
- unresolved call details instead of only a count.

The graph builder continues to emit the existing projections and adds:

- `CONFIG_LINEAGE.json`;
- `CONCURRENCY_MAP.json`;
- `METRICS_CATALOG.json`;
- source/path-aware `TAINT_MAP.json`;
- reachability annotations in `SECURITY_SURFACE.json`;
- provenance-complete `MODULE_CATALOG.json`; and
- confidence/reason fields for heuristic `CALL_GRAPH.json` and `DEAD_CODE.json` entries.

`KNOWLEDGE_GRAPH.db` remains the canonical normalized store. JSON files are deterministic projections.

### 4.3 Analysis frontends

#### `bd-coverage-map`

A stable CLI over the existing `coverage_map.py`, `risk_score.py`, and optional test-catalog evidence.

Inputs:

- coverage.py JSON;
- knowledge graph;
- tracked source root;
- optional radon report and test catalog.

Outputs:

- `COVERAGE_GAPS.json`;
- per-function and per-module coverage/risk summaries;
- source and coverage input hashes;
- optional `--check` comparison against an existing artifact.

Coverage absence is `unknown`, never silently treated as zero coverage.

#### `semantic_diff.py`

Compares two deterministic L0 snapshots or two tracked trees. It reports:

- signature compatibility;
- defaults and annotation changes;
- raises/return-contract changes;
- decorator/auth changes;
- resolved and unresolved call-edge changes; and
- configuration, concurrency, metric, and sink-surface changes.

The required implementation uses `ast`; `libcst` is an optional position-preserving adapter. Policies classify changes as breaking, risky, informational, or unknown.

#### `reachability.py`

Combines, without conflating:

- Flask route enumeration;
- controlled unauthenticated and authenticated test-client probes;
- manual and decorator auth-gate facts;
- function call paths;
- existing operator-wiring evidence from `endpoint_reachability.py`;
- navigation evidence from `nav_reachability.py`; and
- deferred finding records from `reachability_ledger.py`.

Each route is classified `public`, `authenticated`, `internal`, `unreachable`, or `unknown`, with evidence and confidence. Unknown privilege-boundary reachability fails closed only when the caller requests gate mode.

### 4.4 Dynamic analysis

#### `differential_oracle.py`

Provides a registry of typed adapters expressing:

- implementation A and implementation B;
- input corpus provider;
- normalization function;
- comparison policy;
- allowed divergences; and
- time/resource budget.

The first adapters wrap `consumer_agreement.py`, schema/rollback oracles, template/plugin diff tools, and URL-classifier truth checks. It does not replace those tools.

#### `fuzz_harness.py`

Coordinates existing focused fuzzers behind one result and replay protocol:

- deterministic seed;
- frozen regression corpus;
- target adapter;
- timeout and resource budget;
- crash/failure normalization;
- minimal reproducer path;
- secret-safe finding record.

The standard-library runner is required. Hypothesis generation is optional in the isolated audit environment. Importing the module performs no fuzz execution.

## 5. Governance

### 5.1 Review ledger

Make `REVIEW_STATE.json` a canonical generated/merged artifact with:

- source SHA and per-file SHA;
- review level and status;
- audit ownership/claim lease;
- linked findings, invariants, contracts, and tests;
- L2/L3 evidence hashes;
- stale reason and re-audit requirement.

The staleness gate compares reviewed entries directly with tracked live-tree bytes. Stale reviewed files are marked stale, written atomically, included in `REAUDIT.txt`, and cause gate-mode failure.

### 5.2 `bd-finding`

Creates or updates a normalized finding and emits a deterministic RED-test stub proposal.

Rules:

- dry-run by default;
- no overwrite without exact expected finding SHA;
- stable finding IDs;
- no claim that a stub is a passing regression test;
- no source fix generation.

### 5.3 `bd-invariant`

Promotes a confirmed finding only when:

- the finding exists;
- a referenced RED test exists and was observed failing;
- the probe operation is allowlisted;
- schema validation passes; and
- the existing invariant registry is preserved losslessly.

It updates the canonical invariant source and regenerates `INVARIANTS.json` atomically.

### 5.4 `invariant_probe.py`

Executes registry-defined probes using allowlisted operations:

- import/attribute existence;
- pure function call with JSON-safe inputs;
- Flask test-client request;
- file/hash/schema assertion;
- subprocess command from an explicit tool allowlist.

Every probe has a timeout and produces a tri-state result plus evidence. No `eval`, `exec`, shell interpolation, or arbitrary import string is permitted.

### 5.5 Runtime pre/postcondition harness

Normalize `CONTRACTS.json` into a versioned contract registry while retaining the existing producer/consumer agreement.

Supported adapters:

- pure named function;
- Flask route;
- body-contract fixture;
- consumer-agreement contract;
- existing API-contract smoke probe.

Contracts declare typed fixtures, preconditions, postconditions, allowed raises, side-effect observations, and cleanup. Fixture execution is isolated and bounded.

### 5.6 `bd-review-next`

Consumes graph facts, risk scores, review state, staleness, coverage, and unresolved findings. It returns the next deterministic review slice and records a lease so concurrent reviewers cannot receive the same work.

Claims have owner, issued time, expiry, source SHA, and exact file/function scope. Expired claims are recoverable; a source-SHA change invalidates the claim.

## 6. Composite gate

Extend `tools/bd-audit-gate.py` only after each standalone tool is independently green.

The composite gate runs:

- graph/source-hash verification;
- defect patterns;
- semantic diff policy;
- invariant schema and probes;
- contracts;
- review-ledger staleness;
- reachability privilege-boundary checks;
- differential oracle replay;
- frozen fuzz-corpus replay;
- coverage policy; and
- existing witness, topology, and consumer-agreement checks.

Each component has a unique bitmask value and machine-readable result. Optional expensive analysis is explicitly selected; absence cannot be mistaken for a pass.

## 7. Risk-routed L2/L3 audit

After the platform gates are green:

1. Generate current graph, projections, coverage, risk scores, and batch order against the final tracked tree.
2. Invalidate historical audit evidence whose file SHA differs.
3. Use `bd-review-next` to claim non-overlapping batches.
4. Run L2 source review and L3 runtime/contract/invariant checks.
5. Record findings without fixing them automatically.
6. Merge completed audits into `REVIEW_STATE.json`.
7. Re-run staleness and composite gates after every merged batch.

The audit is complete only when every tracked production file has a current disposition: reviewed, explicitly excluded with policy, or blocked with a recorded reason.

## 8. Advanced knowledge

Generate `project-knowledge/ADVANCED_PROJECT_KNOWLEDGE_v2.md` from:

- current graph projections;
- completed L2/L3 audits;
- confirmed invariants and contracts;
- current reachability and security surfaces;
- open findings and confidence;
- coverage and dead-code caveats; and
- exact source/artifact hashes.

Generated fact sections are machine-derived. Human judgment sections are explicitly labeled and source-linked. The existing `ADVANCED_PROJECT_KNOWLEDGE.md` remains available until v2 passes fact-checking and static-KB validation.

## 9. Documentation hygiene and static KB

Before final staging:

- preserve the project-knowledge automation policy as canonical and the root file as a pointer;
- preserve the project-knowledge BDSUITE changelog as canonical and the toolchain file as a pointer;
- preserve one archived decomposition roadmap plus compatibility pointers;
- keep superseded OPV and shipped plan/status files under the dated archive;
- ensure archive safety warnings appear at the top of operational historical files;
- document that OPV 262/265 sources were absent rather than fabricating archive copies;
- refresh current changelog, operations, corpus, automation, plugin, tracker, and code-intelligence status markers.

After every tracked file and generated artifact is stable:

1. regenerate the static manifest;
2. create and integrity-test one replacement ZIP;
3. run secret and link scans;
4. record hashes and the external-paste instructions;
5. re-paste the external static-KB set; and
6. advance the external pin only after the pasted state is verified.

Local staging is not evidence that the external paste occurred.

## 10. Testing strategy

Each component uses fixture repositories small enough to reason about:

- deterministic repeated output;
- malformed and future schema versions;
- source and input hash drift;
- interrupted/failed atomic writes;
- ambiguous and unresolved calls;
- public/authenticated/internal/unknown routes;
- oracle allowed and forbidden divergences;
- fuzzer timeout, crash, replay, and seed behavior;
- stale reviewed files;
- invariant probe allowlist rejection;
- contract precondition, postcondition, raises, and cleanup;
- duplicate review claim contention and lease expiry;
- secret and path leakage;
- compatibility behavior of existing tools.

Integration tests prove:

- all frontends consume the same canonical schemas;
- the composite gate cannot report pass when a required component is absent;
- graph and review artifacts bind to the same source SHA;
- advanced knowledge binds to the reviewed artifact set;
- static-KB packaging contains the exact final manifest.

## 11. Acceptance criteria

The program is ready for pre-commit review when:

1. every requested exact CLI exists and has help, deterministic JSON, self-test or fixture tests, and documented exit codes;
2. all graph projections are current and schema-valid;
3. `REVIEW_STATE.json`, `INVARIANTS.json`, and `CONTRACTS.json` are lossless and source-bound;
4. the expanded audit gate passes with no missing required component;
5. risk-routed L2/L3 coverage has a disposition for every tracked production file;
6. `ADVANCED_PROJECT_KNOWLEDGE_v2.md` is generated and fact-checked;
7. current documentation and tracker status match evidence;
8. the full relevant test and audit bands pass;
9. the replacement static-KB ZIP is integrity- and secret-clean; and
10. the complete worktree remains uncommitted and unmerged for user review.
