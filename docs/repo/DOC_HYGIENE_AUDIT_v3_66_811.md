# Documentation hygiene audit -- .md / .json / .txt (as of v3.66.811)

ASCII-only. POINT-IN-TIME snapshot, NOT standing authority. Every "shipped /
superseded / stale" verdict below was re-derived from source at audit time, but
staleness moves -- RE-VERIFY each item before acting on it (CLAUDE.md sections 0,
1, 10). Nothing in this audit has been executed: no file was deleted, archived,
or gitignored. This is a recommendation register only.

Denominator: all git-tracked files -- 177 .md, 122 .json, 10 .txt (309 total).
Method: for each file, (a) code/test reference check
(`grep -rIl --include=*.py --include=*.sh --include=*.ts <basename> .`), (b) for
plan/status/audit docs, verify the described work against the actual source tree
(bulk_downloader/, tools/, tests/) rather than trusting the doc's own claims.
A file that could not be verified was KEPT (unknown is not a delete).

---

## Category 1 -- .txt (10 files): CLEAN, all KEEP

No stale/implemented docs. All are load-bearing or current:

- `VERSION.txt`, `requirements.txt` / `-dev` / `-optional` / `-cloak` -- manifests.
- `tests/route_map_baseline.txt`, `tests/SKIP_BASELINE.txt` -- gate baselines.
- `project-knowledge/bd_starting_message.txt` -- version-AGNOSTIC static-KB bootstrap.
- `project-knowledge/OPV_COWORK_EXECUTION_PROMPT.txt`,
  `project-knowledge/OPV_EXECUTION_PROMPT_CODEX.txt` -- current (v3.66.811) OPV
  orchestration prompts.

## Category 2 -- .json (122 files): CLEAN, all KEEP

Everything is structural/load-bearing:

- 75 `tests/*.json` -- recognizer/challenge corpus fixtures (test data).
- Generated + `--check`-gated: `ROUTE_INDEX`, `PIN_INDEX`, `DEPENDENCY_GRAPH`,
  `openapi`, `build_info`.
- Frozen baselines: `reports/config_gui_manifest`, `reports/config_parity_baseline`,
  `reports/legacy_parity_baseline`, `.gitleaks-baseline`, `tools/decomp/*_baseline`,
  `kb/*snapshot*`.
- App runtime data: locales, `static/manifest`, `extension/manifest`, `plugins`,
  `sites_config.example`, `templates/reviewed/*`, `tools/registry`, `tools/data/*`.
- Build config: `package.json` / `-lock`, `tsconfig*`.
- KB data + `docs/audit/*.json` (all consumed by a tool or test:
  `review_merge.py`, `verify_audit.py`, `test_audit_promotion_wirings_533.py`).

Side-notes (not deletion recs): `docs/framework/nodes.example.json` has zero refs
(a doc example); `spa/` (14 files, package name "spa") looks like a small separate
app distinct from `frontend/` (361 files) -- worth a separate look.

## Category 3 -- .md (177 files): 5 DELETE, 23 ARCHIVE, 0 GITIGNORE, rest KEEP

### DELETE -- redundant or doubly-superseded (5)

| File | Evidence |
| --- | --- |
| one of `AUTOMATION_POLICY.md` / `project-knowledge/AUTOMATION_POLICY.md` | BYTE-IDENTICAL (246L). Delete one; keep the code-referenced canonical path. |
| one of `project-knowledge/BDSUITE_CHANGELOG.md` / `toolchain/BDSUITE_CHANGELOG.md` | BYTE-IDENTICAL (287L). Delete one; keep canonical. |
| one of `kb/decomp/DECOMPOSITION_PROGRAM_ROADMAP.md` / `project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md` | Differ by 1 line (a `verified-against` stamp). Consolidate to one; survivor is an archive candidate. |
| `project-knowledge/OPV_OPERATOR_RUNBOOK_v3_66_266.md` | Self-says "supersedes 265/266"; superseded again by `OPV_COMPLETION_GUIDE_v3_66_810`. 0 code refs. |
| `project-knowledge/OPV_AUDIT_AND_GUIDE_v3_66_267.md` | Superseded by `_810`; the F1.4 fix it records is in-tree. 0 code refs. |

Both duplicate PAIRS have each copy referenced by code -- pick the canonical path
before deleting the other (do not assume which).

### ARCHIVE -- shipped/settled plan/status/audit, no load-bearing refs (23)

High confidence:
- `docs/PHASE4_RETIREMENT_PLAN.md` -- legacy shell deleted (index.html, app.js,
  widgets.js, captcha_relay.js all gone).
- `kb/decomp/app_py_F5.1/F5.1_LEDGER.md` -- DEMONSTRABLY STALE (claims 0 cuts /
  app.py 20503; actually done: app.py 0 `/api` handlers, 180 `app_*.py` blueprints).
- `kb/decomp/app_py_F5.1/F5.1_DECOMPOSITION_PLAN.md`, `.../F5.1_CUT_RUNBOOK.md`
  -- F5.1 cuts 405-446 complete.
- `kb/decomp/runner/RUNNER_DECOMPOSITION_PLAN.md` -- Phase 3 @404 done
  (runner.py 12065 -> 3269, 13 mixins on disk).
- `docs/audit/CODE_INTELLIGENCE_DELIVERABLES.md` -- v532 snapshot, ~279 vers stale.
- `docs/repo/BUNDLE_VALIDATION_v3_66_805.md` -- one-time import validation,
  self-labeled "not standing authority".
- `project-knowledge/OPERATOR_VERIFICATION_GUIDE.md` -- v262 OPV walkthrough
  replaced by `_810`.

Med-high:
- `docs/repo/MOD1_C4_C8_SANDBOX_PROBE.md`, `docs/repo/MOD1_ARCH_B_STATUS.md`
  -- Arch B shipped (takeover_vnc.py in tree, remote_vnc live).
- `kb/decomp/runner/DECOMPOSITION_LOG.md` -- completed per-cut audit trail.
- `CAPTURE_CONVERGENCE_MAP.md` -- Phase-B golden shipped; v174, 637 vers stale.
- `DARK_CLUSTER_ADJUDICATION_v3_66_753.md` -- adjudication settled in source.
- `project-knowledge/PLUGIN_V3_PLAN.md` -- premise false: plugin API now MIN=2/MAX=8.
- `project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md` -- decomp complete (also the
  near-dup above).

Med / low:
- `kb/decomp/app_py_F5.1/F5.1_KERNEL_CONTRACT.md`, `kb/decomp/runner/README.md`,
  `kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md` -- scaffolding for a completed program.
- `docs/framework/PHASE2_SELECTOR_DESIGN.md` -- still says "NOT built" though
  `tools/selector_learning.py` (525L) shipped.
- `project-knowledge/AUTOMATION_PROGRAM_PLAN.md` -- A5/A-PIPE shipped past v593 table.
- `project-knowledge/BD_SYSTEM_DEEP_DIVE_30_PHASES.md` -- self-declared historical
  snapshot (218-tool era).

### GITIGNORE -- none

Deliberately zero. `ARCHITECTURE_INVENTORY.md`, `DEPENDENCY_GRAPH.md`,
`ENDPOINT_CATALOG.md`, `FUNCTION_INDEX.md` LOOK like gitignore bait but are
`--check`-gated by `build_release.py` and MUST stay tracked. Gitignoring them
would blind the build gate (the section-0 "gate that cannot see its subject" trap).

### KEEP but content-stale -- refresh, do NOT remove

- `docs/LEGACY_MIGRATION_PLAN.md`, `docs/NAV_CONSOLIDATION.md` -- shipped, but cited
  by live frontend + `tests/test_nav_reachability.py` ("three frontends" table stale).
- `docs/PHASE6_PLAN.md`, `docs/UX_IMPROVEMENT_PLAN.md` -- partial, test-anchored.
- `project-knowledge/CHANGELOG_RECENT.md` -- newest entry v3.66.732 (trails 811).
- `docs/framework/OPERATIONS_WORKFLOWS.md` + PHASES_31_40/4_6 docs -- quote stale
  corpus size ("34 entries"; actual `validation_corpus.jsonl` is 35).

### Flagged but KEPT (not shipped / unverifiable)

- `project-knowledge/PHASE_C_HARDENING_PLAN.md` -- DORMANT, not stale:
  `capture_bodies.py` guard SHA unchanged (6c7f5c9a...) proves the work never landed.
- `project-knowledge/AUDIT_PLAN_v3_66_539.md` -- UNVERIFIED: no audit artifacts in
  tree, but parent program maintained @805. Honest unknown -> keep.

---

## Logistics

- No `docs/archive/` directory exists yet -- archiving means `git mv` into a new one.
- `STATIC_KB_MANIFEST.json` is a byte-integrity list of all PK files, NOT a curated
  active-set signal; the real active-set is `KB_ACTIVE_INDEX.md` + `0_INDEX.md` cards.
