<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
<!-- verified-against: v3.66.805 -->
# Code-Review & Code-Intelligence — documentation index

The complete documentation for the 100% line-by-line review/debug effort **and** the
knowledge-capture program that turns it into a self-maintaining code-intelligence
layer feeding the advanced project knowledge. Two halves of one read pass:
**findings** (defects → fixes) and **knowledge** (→ advanced PK → static KB).

All docs are **version-agnostic** durable reference. The live state (the ledger
`REVIEW_STATE.json`, the graph, the audit notes, the advanced-PK draft) is volatile
and travels in the session `version.zip`. Re-derive every count/SHA/version from
source each session — never trust a number copied into a doc.

## Read in this order

**Foundations (the existing review program):**
1. **`CODE_REVIEW_METHODOLOGY.md`** — why a flat read is the wrong tool at this
   scale; the L0–L4 method; risk ordering; the `REVIEW_STATE.json` ledger design;
   FP calibration; integration with release discipline. Start here.
2. **`CODE_REVIEW_TOOLCHAIN.md`** — the static-analysis battery: each tool, what it
   catches, offline install, and the per-tool invocation + hard-won gotchas.
3. **`REVIEW_FINDINGS.md`** — the findings register; the first mechanical-pass
   aggregate signal and the first confirmed defect (`F0001`), to ledger granularity.

**The code-intelligence program (the advanced layer):**
4. **`CODE_INTELLIGENCE_PROGRAM.md`** — the umbrella: machine-first/risk-ordered/
   gated/incremental method; the dual output; the session model (parallel reads,
   serial fixes); the per-file rubric; the knowledge→advanced-PK→static-KB pipeline;
   build order. Read after the foundations.
5. **`CODE_INTELLIGENCE_ARCHITECTURE.md`** — L0/L1 layers; the one
   `KNOWLEDGE_GRAPH.db` and the artifacts that are projections of it; taint
   propagation; the invariant registry; the drift gates incl. `bd-audit-gate`.
6. **`CODE_INTELLIGENCE_SCHEMAS.md`** — the exact schema + gate for every artifact
   (ledger, coverage ledger, module catalog, call/taint/security/contract/
   error/config/concurrency/dead-code, the graph DB).
7. **`CODE_INTELLIGENCE_TOOLING.md`** — the custom tools to build (`bd-scan`,
   `l0_extract`, `graph_build`, `defect_patterns`, `semantic_diff`,
   `differential_oracle`, `fuzz_harness`, `reachability`, `invariant_probe`,
   `bd-audit-gate`) + the **verified offline install** of the merged
   `bd_review_tools_FULL_kit`.
8. **`DEFECT_PATTERN_CATALOG.md`** — the seed for `defect_patterns.py`: every
   confirmed bug-class (the verify-pass 16 + F0001) as a detectable pattern. Grow it
   on every new confirmed bug.

## Status at a glance (re-derive — do not trust this line)
- **Codebase:** re-derive version + LOC + file count from the tree each session.
  (~270K production + the test tree; monoliths >3K lines; on-stash suite GREEN.)
- **Battery:** `[LIVE]` — merged `bd_review_tools_FULL_kit` installs offline on
  sandbox + stash (ruff/black via precommit_kit, pyright/jedi via lsp_kit).
- **Custom review + code-intelligence tooling:** `[BUILT]` — corrected v3.66.805.
  `bd-scan.py`, `l0_extract.py`, `bd-audit-gate.py`, `coverage_map.py` and
  `reachability_ledger.py` all ship in the static PK; the line previously read
  `[PLANNED] — not yet built`, which was false. Build order remains documented in
  `CODE_INTELLIGENCE_PROGRAM.md` §9 for the parts still outstanding.
- **Confirmed findings carried in:** `F0001` (api_status NameError) + the v3.66.520
  verify register (`VERIFY_MATRIX`, 16 deduped) — fold both into the ledger when it
  is instantiated.

## Conventions
- **Source is the final ground truth.** Every count/version/SHA in these docs is a
  snapshot; the ledger's SHA-diff gate is the mechanical enforcement.
- **RED-first.** A finding is `confirmed` only with a test that fails on pristine
  source. Green suite is evidence of nothing for untested paths.
- **Filed now, fixed later** as separate RED-first consolidated cuts after a slice —
  never mid-scan.
- **Read-only attestation.** Audit sessions never bump/cut/guard-edit/baseline-
  `--update`/tracker-write/stash-touch; attest it and re-verify the tree after.
- **Volatile vs static.** Capture + consolidate in `version.zip` every session;
  promote to static KB only at a deliberate `bd-handoff --kb-dir` step.
