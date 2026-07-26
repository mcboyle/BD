# Documentation hygiene and static-KB refresh report -- 2026-07-22

Scope: documentation consolidation, recoverable archival, stale-content
refresh, and static-KB synchronization against the v3.66.817 tree at
`d095a63`. No product code, release guard, runtime state, credential, token,
cookie, host address, or operator secret was added to this report.

## Canonicalization decisions

| Subject | Canonical source | Compatibility location |
| --- | --- | --- |
| Automation policy | `project-knowledge/AUTOMATION_POLICY.md` | root `AUTOMATION_POLICY.md` is a pointer |
| BDSUITE changelog | `project-knowledge/BDSUITE_CHANGELOG.md` | `toolchain/BDSUITE_CHANGELOG.md` is a pointer; the builder fallback and consumer graph already resolve the project-knowledge path |
| Completed decomposition roadmap | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md` | both former live paths are archive pointers |

The displaced duplicate bodies remain recoverable under
`docs/archive/2026-07-22-doc-hygiene/duplicates/`; they are historical copies,
not competing live sources.

## Archive map

All moves used tracked renames. Nothing was discarded.

| Former path | Archive path |
| --- | --- |
| `docs/PHASE4_RETIREMENT_PLAN.md` | `docs/archive/2026-07-22-doc-hygiene/docs/PHASE4_RETIREMENT_PLAN.md` |
| `docs/audit/CODE_INTELLIGENCE_DELIVERABLES.md` | `docs/archive/2026-07-22-doc-hygiene/docs/audit/CODE_INTELLIGENCE_DELIVERABLES.md` |
| `docs/framework/PHASE2_SELECTOR_DESIGN.md` | `docs/archive/2026-07-22-doc-hygiene/docs/framework/PHASE2_SELECTOR_DESIGN.md` |
| `docs/repo/BUNDLE_VALIDATION_v3_66_805.md` | `docs/archive/2026-07-22-doc-hygiene/docs/repo/BUNDLE_VALIDATION_v3_66_805.md` |
| `docs/repo/MOD1_ARCH_B_STATUS.md` | `docs/archive/2026-07-22-doc-hygiene/docs/repo/MOD1_ARCH_B_STATUS.md` |
| `docs/repo/MOD1_C4_C8_SANDBOX_PROBE.md` | `docs/archive/2026-07-22-doc-hygiene/docs/repo/MOD1_C4_C8_SANDBOX_PROBE.md` |
| `kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md` |
| `kb/decomp/app_py_F5.1/F5.1_CUT_RUNBOOK.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_CUT_RUNBOOK.md` |
| `kb/decomp/app_py_F5.1/F5.1_DECOMPOSITION_PLAN.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_DECOMPOSITION_PLAN.md` |
| `kb/decomp/app_py_F5.1/F5.1_KERNEL_CONTRACT.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_KERNEL_CONTRACT.md` |
| `kb/decomp/app_py_F5.1/F5.1_LEDGER.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_LEDGER.md` |
| `kb/decomp/runner/DECOMPOSITION_LOG.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/runner/DECOMPOSITION_LOG.md` |
| `kb/decomp/runner/README.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/runner/README.md` |
| `kb/decomp/runner/RUNNER_DECOMPOSITION_PLAN.md` | `docs/archive/2026-07-22-doc-hygiene/kb/decomp/runner/RUNNER_DECOMPOSITION_PLAN.md` |
| `project-knowledge/AUTOMATION_PROGRAM_PLAN.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/AUTOMATION_PROGRAM_PLAN.md` |
| `project-knowledge/BD_SYSTEM_DEEP_DIVE_30_PHASES.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/BD_SYSTEM_DEEP_DIVE_30_PHASES.md` |
| `project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md` |
| `project-knowledge/OPERATOR_VERIFICATION_GUIDE.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPERATOR_VERIFICATION_GUIDE.md` |
| `project-knowledge/OPV_AUDIT_AND_GUIDE_v3_66_267.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPV_AUDIT_AND_GUIDE_v3_66_267.md` |
| `project-knowledge/OPV_OPERATOR_RUNBOOK_v3_66_266.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPV_OPERATOR_RUNBOOK_v3_66_266.md` |
| `project-knowledge/PLUGIN_V3_PLAN.md` | `docs/archive/2026-07-22-doc-hygiene/project-knowledge/PLUGIN_V3_PLAN.md` |
| `CAPTURE_CONVERGENCE_MAP.md` | `docs/archive/2026-07-22-doc-hygiene/root/CAPTURE_CONVERGENCE_MAP.md` |
| `DARK_CLUSTER_ADJUDICATION_v3_66_753.md` | `docs/archive/2026-07-22-doc-hygiene/root/DARK_CLUSTER_ADJUDICATION_v3_66_753.md` |

The superseded v3.66.732--753 `CHANGELOG_RECENT.md` snapshot was additionally
preserved at
`docs/archive/2026-07-22-doc-hygiene/project-knowledge/CHANGELOG_RECENT_v3_66_732_753.md`
before regeneration.

The requested OPV v3.66.262 and v3.66.265 guides were absent from both the
working tree and reachable Git history, so there were no repository copies to
archive. They were not imported from `Z:`; the repository-sourced v3.66.266 and
v3.66.267 documents are the earliest guides preserved by this archive pass.

## Content refresh

- `project-knowledge/CHANGELOG_RECENT.md` is now the literal 20-release
  v3.66.817 through v3.66.798 excerpt from `CHANGELOG.md`.
- `project-knowledge/AUTOMATION_POLICY.md` now reflects source-tested A0--A9
  implementation, default-off posture, keystone gates, and master off-switch.
- `docs/plugin_examples/README.md` now documents the live plugin API overlap
  range `[2,8]` and separate payload schema.
- Framework workflow/map docs now use the measured 35-entry validation corpus.
- Legacy migration and navigation docs now identify their shipped historical
  status and the root-SPA/current redirect disposition.
- References in active project-knowledge docs now point to the dated archive.
- The two dead `CHANGELOG_archive.md` links now state that source-control
  history is the available pre-v3.46 record.

## Static-KB refresh

`bd-kb-sync stage` produced a full 363-file replacement set:

- `reports/doc-hygiene-static-kb-2026-07-22/BulkDownloader_project_files_v3_66_817.zip`
- `reports/doc-hygiene-static-kb-2026-07-22/PROJECT_KNOWLEDGE_UPDATE.md`
- zip SHA-256: `d9ab75f99a1aa855d09fe089999cb2c39fe606fe08f53bac7078a189e1c3f9d8`
- manifest SHA-256: `7a074005469a5814e0dd9cacb1bb81b0d79835bb8a1621c91b1988ee422ca6d3`
- reconciled ledger SHA-256: `4ff75f70bb8eaa07a9c41ad3d67de6d427d18e433ad66be76593174f623d683b`

The manifest delta is deterministic by sorted path: six retired static files
removed and seven retained files changed. The stage includes the
`REACHABILITY_DEFERRALS.json` reconciliation from commit `2beba3b`.

The replacement archive is ready, but the external project-file re-paste and
external STATE pin advance are operator actions. The pin was deliberately not
advanced before the paste. The stage was rerun after the 2026-07-23 tracker and
evidence-ledger refresh; the static set remained 363 files and the replacement
archive/update note above are the final generated artifacts for this tree.

## Validation

| Check | Result |
| --- | --- |
| Current-doc relative Markdown link audit (archive excluded as historical) | 6 relative links checked, 0 broken |
| `bd-kb-sync diff` after stage | CURRENT, exit 0 |
| `unzip -t` on replacement set | 363-file archive clean, exit 0 |
| `project-knowledge/test_bd_kb_sync.py` | 27 passed, 0 failed |
| Generated index gates: function, endpoint, dependency graph, PIN, route | 32 passed, 0 failed |
| Documentation drift gate | 9 passed, 0 failed |
| Legacy/nav, automation A0--A9, lifecycle drift, plugin API range band | 134 passed, 0 failed |

## Known follow-ups and boundaries

- `tools/cross_monolith_graph.py` still describes emitting the now-archived
  completed-program snapshot. It was not changed because no current gate
  consumes that output; if the decomposition program is reopened, decide a new
  live output contract before running it.
- Historical tracker notes still mention `DECOMPOSITION_LOG.md` by basename.
  Their evidence is preserved in the archive map above; generated tracker data
  was not rewritten solely to alter historical prose.
- Internal links inside archived point-in-time documents were not modernized;
  changing them would rewrite history. Current docs and compatibility pointers
  are the link-gated surface.
