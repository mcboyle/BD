<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
<!-- verified-against: v3.66.805 -->
# Code-Intelligence Program

The umbrella for the **100% line-by-line** review/debug + **knowledge-capture**
effort. One read pass over the tree produces **two** durable outputs:

1. **Findings** — defects, filed RED-first, fixed later as consolidated cuts.
2. **Knowledge** — per-module understanding (intent, invariants, contracts, data
   flow, risk) that consolidates into an **advanced project-knowledge** layer.

It does **not** invent a parallel process. It extends the established review set
(`CODE_REVIEW_METHODOLOGY.md`, `CODE_REVIEW_TOOLCHAIN.md`, `REVIEW_FINDINGS.md`)
and plugs into release discipline (RED-first, consolidated cuts, guard integrity,
source-as-ground-truth). Companion docs in this set:
`CODE_INTELLIGENCE_ARCHITECTURE.md` (the layers + the unified graph),
`CODE_INTELLIGENCE_SCHEMAS.md` (every artifact schema + its gate),
`CODE_INTELLIGENCE_TOOLING.md` (the custom tools), `DEFECT_PATTERN_CATALOG.md`
(the seed patterns). Read `CODE_REVIEW_INDEX.md` first for the full order.

> **Status markers:** `[LIVE]` built/verified · `[PLANNED]` designed, not built ·
> `[CONFIRMED]` real defect, unfixed.

---

## 1. The governing idea — machine derives the derivable; sessions spend budget only on judgment

The tree is large (re-derive: ~270K production LOC across `bulk_downloader/` +
`tools/` + `frontend/src`, plus the test tree; several monoliths >3K lines). A flat
sequential read would burn the entire budget on boilerplate a script can extract
for free, and still miss the **cross-file** defects that actually matter here
(extraction residue, broken trust boundaries, swallowed exceptions, contract drift
between a caller and a callee — the `F0001` shape).

So the method is **machine-first, risk-ordered, gated, and incremental**:
- **Machine extracts** signatures, raises, return-contracts, the call graph, every
  sink/auth-gate/secret site, complexity, churn — exhaustively, with zero session
  tokens. This is L0/L1.
- **Sessions read** the highest-risk units first, each arriving **pre-digested**
  (its taint paths, callers, sinks, test file already in view), and contribute only
  the irreducible judgment — L2.
- **Everything is gated**: every audited file carries a content-SHA; a file whose
  SHA moved is auto-flipped back to `unreviewed`. Coverage can't silently rot.
- **Incremental**: the 270K one-time cost amortizes — each later release re-audits
  only what changed (~1–2 sessions/release forever).

The compounding asset is `defect_patterns` (see §6): every confirmed bug-class
becomes a permanent AST gate, so the same bug can never silently return. The audit
becomes a flywheel, not a one-time snapshot.

---

## 2. Layers (L0 → L4)

| Layer | What | Budget | Owner |
|---|---|---|---|
| **L0** Mechanical sweep | the whole static-analysis battery, normalized into ledger findings; diff-aware | ~0 reading | machine (`bd-scan`) |
| **L1** Deterministic extraction + graph | per-function facts + the unified `KNOWLEDGE_GRAPH`; MODULE_CATALOG/CALL_GRAPH/TAINT_MAP/SECURITY_SURFACE are projections of it | ~0 reading | machine (`l0_extract`, `graph_build`) |
| **L2** Risk-ordered deep read | read source for the highest-risk units, neighborhood + test in view; emit findings + knowledge + invariants + new patterns | the budget | session |
| **L3** Single-concern cross-cutting passes | one pass per defect class across the tree (trust boundaries, swallow, leaks, sync/async, input-validation, taint) | medium | session, seeded by L0 |
| **L4** Verify + fix | RED test per confirmed finding, then fix as a separate consolidated cut after the slice | per-cut | session (fix chain) |

L0/L1 are detailed in `CODE_INTELLIGENCE_ARCHITECTURE.md`; the per-tool battery
and the verified offline install in `CODE_INTELLIGENCE_TOOLING.md`.

---

## 3. Risk ordering (where deep budget goes first)

```
risk = cyclomatic_complexity × (1 / (1 + line_coverage)) × churn × danger_map_weight
       × (1 + taint_reach) × (1 + prior_defect_proximity)
```
The first four factors are the existing methodology's score (radon complexity,
on-stash `coverage.json`, git churn, DANGER_MAP membership). The program **adds**
two: `taint_reach` (does user input flow here — from the graph) and
`prior_defect_proximity` (near a past confirmed finding). `risk_score` is **BUILT**
(`work/tools/risk_score.py`, corrected v3.66.805); `bd-coverage-map` remains
`[PLANNED]` (verified absent). Until the composite is wired end-to-end, radon rank
is the proxy.

Highest-risk known units to seed L2 (re-derive from radon each session): the
`SiteRunner._process_one` family (CC ~163), `_update_job`, `_collect_data`,
`TransportMixin._do_download`, `api_global_config`, and the monoliths `app.py`,
`tools/cockpit_console.py`, `tools/cockpit_core.py`, `runner.py`.

---

## 4. Session model — parallel reads, serial fixes

**Reads parallelize; fixes do not.** L0–L3 are read-only, so audit sessions split
with zero collision (exactly like the verify pass). L4 fixes are a serial version
chain (one bump + one deploy each).

- **Audit session** (read-only, parallel): bootstrap → `bd-review-next` hands the
  next risk-ordered slice (module + neighborhood + open findings + DANGER_MAP
  invariants + test file, all pre-digested by L0/L1) → read to the per-file rubric
  (§5) → emit findings + knowledge notes + new invariants + new patterns → attest
  `guard_touch=false`/`tracker_write=false` → update the ledger. Writes ONLY its own
  audit artifacts.
- **Consolidation session**: roll L1+L2 into the advanced-PK layer (§7).
- **Fix session** (serial): the existing release-cut flow, RED-first, per the
  defect register.

**Coverage math (re-derive):** at ~5K dense LOC/audit-session, ~270K production is
~53 line-by-line sessions; machine pre-digestion + risk-routing collapses the
*deep* fraction to ~12–20 and fast-confirms the rest. Read-only ⇒ run them in
waves (~4 waves at 5-wide). Incremental re-audit amortizes later releases to ~1–2.

---

## 5. Per-file audit rubric (every session, same depth)

So "100%" means the same thing everywhere. For each unit, check and record:
auth/authorization gate · injection (SQL/command/path) · SSRF / URL-trust ·
secret handling (read/write/mask/log) · error contract (raise→status, swallow) ·
type/None safety (the NaN/inf/`hasattr`-bare-name class) · concurrency/shared
state · resource lifecycle (open/close) · input validation (non-object bodies,
bounds) · dead code / unreachable. Each becomes a finding (with RED repro) or a
positive assurance (recorded — the "verified clean" half of max-verify).

---

## 6. The compounding gate — `defect_patterns` `[BUILT — corrected v3.66.805]`

> Measured @805: `work/tools/defect_patterns.py` EXISTS. Its promoters do not —
> `bd-invariant`, `bd-finding` and `bd-review-next` are all verified ABSENT from PK,
> `work/tools/` and `bin/`. So the pattern file is built but the promote-to-gate path
> named below is still design.

Every confirmed bug-class is codified as an AST/grep pattern in `defect_patterns.py`
(seeded by `DEFECT_PATTERN_CATALOG.md` from the verify pass's 16 + `F0001`),
extending the repo's own `tools/*.py` audits. `bd-invariant` promotes a pattern to
a permanent gate run by `bd-audit-gate` on every cut. The sandbox has **no**
third-party static-analysis baked in, so a project-native linter built from your
own bugs is the highest-ROI net-new tool — it catches the *next* instance for free.

---

## 7. Knowledge → advanced PK → static KB (the pipeline Matt wants)

The knowledge half of the program flows volatile → static, mirroring the existing
KB sync:

1. **Capture (volatile).** Audit sessions emit per-module knowledge into the
   `KNOWLEDGE_GRAPH` + audit notes (in a `review/` dir that travels in the session
   `version.zip`).
2. **Consolidate (volatile).** A consolidation session renders
   `ADVANCED_PROJECT_KNOWLEDGE_v2.md` (and a grown invariant registry / module
   catalog) **from the graph** — knowledge-as-data rendered to prose, the way
   `project-knowledge/IMPROVEMENT_BACKLOG.md` is the sole task register. These live in
   `version.zip`, not static KB.
3. **Promote (static).** When the advanced-PK layer is stable, run
   `bd-handoff --kb-dir <dir>` → `bd-kb-sync` stages the
   `BulkDownloader_project_files_v<ver>.zip` + a `PROJECT_KNOWLEDGE_UPDATE.md` flag
   and reseeds `STATIC_KB_MANIFEST.json`. `bd-boot` then verifies the pasted static
   set's integrity + freshness next session. See `KB_SYNC_WORKFLOW.md`.

**Rule:** capture and consolidate in volatile files every session; touch static KB
only at a deliberate promotion. A stale advanced-PK doc can't override current
state because "newest only" is enforced by what's attached, not by editing static KB.

---

## 8. Integration with existing discipline (non-negotiable)
- **RED-first** — a finding is `confirmed` only with a test that fails on pristine
  source; otherwise `probable`/`triage`. Green suite is evidence of nothing for
  untested paths.
- **Consolidated cuts** — one release per slice; regenerate all in-sync docs before
  `bd-cut`; never interleave fixes with scanning.
- **Guard integrity** — the 7 guard files stay byte-identical unless SHA-declared;
  re-derive from the extracted zip.
- **Source as ground truth** — re-derive every count/SHA each session; the ledger's
  diff-awareness is the mechanical enforcement.
- **Read-only attestation** — audit sessions never bump/cut/guard-edit/baseline-
  `--update`/tracker-write/stash-touch; they attest it and re-verify the tree after.

---

## 9. Build order (so audit sessions run against a system that's mostly done)

> **Progress measured v3.66.805:** step 1 is **DONE** (`l0_extract.py`,
> `graph_build.py`, `risk_score.py` all present in `work/tools/`); step 2 is
> **PARTLY DONE** (`defect_patterns.py` exists; its gate-promotion tooling
> `bd-invariant`/`bd-finding` does not). Steps 3-6 unverified here — re-derive
> before trusting.

1. `l0_extract` + `graph_build` + `risk_score` — one tooling session; now every file
   arrives pre-digested.
2. `defect_patterns.py` seeded from `DEFECT_PATTERN_CATALOG.md` + its gate.
3. `REVIEW_STATE.json` ledger + invariant registry + their `--check` gates.
4. The audit sessions (risk-routed, parallel) — emit L2 + invariants + patterns.
5. Consolidate L1+L2 → `ADVANCED_PROJECT_KNOWLEDGE_v2` in `version.zip`.
6. Promote to static KB via `bd-handoff --kb-dir`.

Steps 1–3 are the multiplier; skipping them is what makes the naive version cost
~53 sessions of mostly-wasted extraction.
