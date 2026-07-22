# Dependency and Graph-Pin Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all moderate, high, and critical frontend dependency advisories while preserving Node 18, then make stash certification execute a real graph content-hash comparison.

**Architecture:** Upgrade only the first secure Node-18-compatible Router, Vite, and Vitest lines, and lock the fixed transitive `form-data` release. At deployment acceptance, generate a graph from the canonical installed tree into a temporary directory and write its content pin outside the release tree. Every later certification regenerates a temporary graph and compares it to that external trust anchor.

**Tech Stack:** npm 9, Node.js 18, React Router 6, Vite 6, Vitest 3, Python 3, SQLite, systemd, BulkDownloader release tooling.

## Global Constraints

- Preserve the declared Node.js runtime floor `>=18.0.0`.
- Use `react-router-dom ^6.30.4`, `vite ^6.4.3`, and `vitest ^3.2.7`.
- The lockfile must resolve `form-data >=4.0.6`, `esbuild >=0.25.0`, Router `>=6.30.4`, Vite `>=6.4.3`, and Vitest `>=3.2.7`.
- `npm audit --omit=dev` must report zero vulnerabilities.
- `npm audit --audit-level=moderate` must exit zero; the known low `@babel/core 7.29.0` advisory may remain because no fixed Babel 7 release exists and Babel 8 violates `@vitejs/plugin-react`'s peer contract.
- Keep `/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256` outside the install tree. Never ship `KNOWLEDGE_GRAPH.db`, its SQLite sidecars, or graph pins in the release ZIP.
- Preserve the standing waiver of the extracted-ZIP test band; retain frontend, focused Python, CI, release verification, deployment rehearsal, and 60-worker stash certification gates.

---

### Task 1: Frontend dependency security floors

**Files:**
- Create: `tests/test_frontend_dependency_security_floor.py`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Consumes: npm lockfile schema v3 under `packages`.
- Produces: version-floor regression coverage and an installable Node-18-compatible dependency graph.

- [x] **Step 1: Write the failing version-floor test**

Create a Python test that loads `frontend/package.json` and `frontend/package-lock.json`, asserts the three exact direct ranges from Global Constraints, and compares resolved versions for `node_modules/form-data`, `node_modules/esbuild`, `node_modules/react-router`, `node_modules/react-router-dom`, `node_modules/vite`, `node_modules/vite-node`, and `node_modules/vitest` against their secure floors.

- [x] **Step 2: Verify the test is red on 3.66.816**

Run: `PYTHONPATH=$PWD:/tmp/prestaged_site_packages python3 -m pytest -q tests/test_frontend_dependency_security_floor.py`

Expected: failure because the direct ranges and lockfile still resolve vulnerable Router, Vite, Vitest, and `form-data` versions.

- [x] **Step 3: Regenerate dependencies mechanically**

Run from `frontend/`:

```bash
npm install --package-lock-only --save-dev 'vite@^6.4.3' 'vitest@^3.2.7'
npm install --package-lock-only 'react-router-dom@^6.30.4'
npm update --package-lock-only form-data
npm ci
```

Do not hand-edit `package-lock.json` and do not use `npm audit fix --force`.

- [x] **Step 4: Verify the security floor and audit policy**

Run:

```bash
PYTHONPATH=$PWD:/tmp/prestaged_site_packages python3 -m pytest -q tests/test_frontend_dependency_security_floor.py
cd frontend
npm ls @babel/core @vitest/mocker esbuild form-data react-router react-router-dom vite vite-node vitest --all
npm audit --omit=dev
npm audit --audit-level=moderate
```

Expected: Python test passes; production audit is clean; moderate-level audit exits zero with only the documented low Babel advisory remaining in the full audit.

- [x] **Step 5: Run frontend compatibility gates**

Run from `frontend/`:

```bash
./node_modules/.bin/tsc --noEmit -p tsconfig.json
npm test
npm run build
```

Expected: every command exits zero.

- [x] **Step 6: Commit Task 1**

```bash
git add tests/test_frontend_dependency_security_floor.py frontend/package.json frontend/package-lock.json docs/superpowers/plans/2026-07-22-dependency-graph-hardening.md
git commit -m "Harden frontend dependency floors"
```

---

### Task 2: Mandatory deployment-local source-graph pin

**Files:**
- Create: `tests/test_graph_source_hash_release_gate.py`
- Modify: `capture.sh`
- Modify: `bulk_downloader/dev_suite/release_lint.py`
- Modify: `reports/config_gui_manifest.json`
- Modify: `project-knowledge/OPERATOR_POLICY_DECISIONS.md`

**Interfaces:**
- Consumes: the deployed source root and an external trusted content pin.
- Produces: a deterministic logical graph comparison through a temporary SQLite database that is deleted after every check.

- [x] **Step 1: Write failing source-pin and release-hygiene tests**

Cover matching source/pin success, mismatch after a production-source mutation, missing-pin failure when required, cleanup of temporary DB files, capture final-stage wiring, and exact graph DB release exclusions.

Expected before implementation: the new tests fail because capture only checks optional persistent DBs and skips when none is found.

- [x] **Step 2: Add ephemeral capture-time graph checking**

Teach `capture.sh` to read `BD_GRAPH_HASH_PIN` (default `/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256`), build a graph under `mktemp -d`, check it with the existing canonical-row hash logic, and clean it with a trap. `BD_REQUIRE_GRAPH_HASH=1` makes a missing pin fail the graph stage instead of reporting UNKNOWN.

- [x] **Step 3: Add deployment-acceptance pin generation**

After the exact release archive passes SHA/version/build gates on stash, explicitly generate one temporary graph from the installed tree and write the trusted external pin. Delete the temporary DB. Never write the pin immediately before a routine check, because doing so would bless drift.

- [x] **Step 4: Harden release hygiene and policy**

Exclude the graph DB, WAL, SHM, journal, and graph pins by exact name. Clarify policy decision 14 to distinguish this external deployment-source pin from mutable audit-state pins. Do not modify guarded `tools/build_release.py` or its CI hash.

- [x] **Step 5: Regenerate and verify**

Run the new and affected graph/release tests, dependency/import graph checks, generated-artifact order, shell syntax, and release build verification. On stash, generate the external pin once during deployment acceptance, then run capture with `BD_REQUIRE_GRAPH_HASH=1`; graph must report `OK`, never UNKNOWN/skipped.

---

### Task 3: Canonical release, deployment, and certification

**Files:**
- Modify mechanically: `CHANGELOG.md`, `PIN_INDEX.json`, `bulk_downloader/__init__.py`, and version-coupled tests for `3.66.817`.
- Build: `/root/out-3.66.817-final/BulkDownloader_v3_66_817.zip`.

**Interfaces:**
- Consumes: reviewed dependency commit and deployment-local graph workflow.
- Produces: merged Git commit, pinned release ZIP/build identity, deployed 3.66.817 service, rollback archive, and certification evidence.

- [ ] **Step 1: Run focused Python and generated-artifact gates**

Run the new dependency-floor test, graph wiring tests, version-index tests, function-index test, and previously promoted Filthykings regression suites. Require zero failures.

- [ ] **Step 2: Push the hardening branch and merge only after review**

Open a PR to `main`; require GitHub `gates` and `postgres-integration` success plus CodeRabbit review or the established timeout waiver.

- [ ] **Step 3: Prepare and merge release 3.66.817**

Cut release-source changes on a dedicated release branch, verify the release archive, open a second PR, and require the same review/CI gates. Build the deployable ZIP again from the exact merged `main` commit in a fresh output directory.

- [ ] **Step 4: Rehearse and deploy with rollback armed**

Archive 3.66.816 first, verify local/remote ZIP SHA-256, rehearse the overlay with scoped stale-frontend cleanup, deploy without SHA/backend bypass flags, and require active service plus exact version/build stamp.

- [ ] **Step 5: Certify and archive evidence**

Run `BD_REQUIRE_GRAPH_HASH=1 DISPLAY=:99 ./capture.sh --workers=60 --summary`. Require zero unit/live failures, graph check-hash `OK`, active/healthy 3.66.817 with exact archive build stamp, an archived 3.66.817 rollback ZIP, and a durable copy of `/tmp/bd_capture.tar.gz` under `/home/mboyle/`.
