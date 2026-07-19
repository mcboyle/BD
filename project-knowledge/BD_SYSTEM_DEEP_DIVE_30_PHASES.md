<!-- verified-against: v3.66.805 -->
# BD System Deep Dive — 30 Phases

> **SNAPSHOT — figures re-measured v3.66.805. Do not cite this doc's numbers as current.**
> This is a point-in-time analysis from a **218-tool** era; the live toolchain is **249**.
> Measured drift on the headline figures:
>
> | Doc says | Live @805 |
> |---|---|
> | 218 tools | **249** |
> | 550 source modules | **490** |
> | 982 test files | **1,073** |
> | 1,012 routes | **1,014** |
> | `app.py` 7,022 LOC | **7,060** |
> | `runner.py` 3,241 LOC | **3,269** |
> | spa_wired 383 (37%) | **442** |
> | **449 unwired operator endpoints** | **223** |
>
> **Phase 27's #1 ranked risk has HALVED** (449 → 223), and **Phase 29's item 12** —
> "a per-cut ratchet that forbids *new* unwired operator endpoints" — **has since been
> built**: `unwired_operator_endpoints` is a live ceiling metric in `bd-ratchet`,
> enforced by `bd-precut --gate`. Phase 26's subtractive thesis was also tested at 805
> and did not survive contact: the SUBTRACT pool measured 87 listed-only of 249 with
> **orphan 0**, and 80 of those carry a curated session trigger, so the retirement pool
> is effectively empty. The doc's ANALYSIS and its failure taxonomy remain valuable; its
> COUNTS are historical.

*Every phase is grounded in data extracted this session (218 tools + the 176k-LOC
source tree + the 173k-LOC test suite), not assertion. Numbers are live measurements.
Findings marked ★ are new (not in prior docs).*

**Corpus measured:** 218 toolchain tools (26,047 LOC) · 550 source modules (176,753
LOC) · 982 test files (172,780 LOC, ~7,736 test fns) · 1,012 routes / 162 blueprints
· 7 guard files (4,257 LOC).

═══════════════════════════════════════════════════════════════════
## PART I — Toolchain anatomy (Phases 1–10)
═══════════════════════════════════════════════════════════════════

### Phase 1 — Inventory & scale
218 `bd-*` tools, 26,047 LOC. Largest: `bd-opv` (1,335), `bd-cut` (588),
`bd-optpack` (516), shared `bdtools_sec` (439). **The toolchain is itself a mid-size
codebase** — it deserves the same discipline as the product (which it mostly lacks;
see Phases 6–8).

### Phase 2 — Naming & verb taxonomy
`bd-` prefix is 100% consistent. But structure is *flat* (218 top-level names) where
domains beg for subcommands (`bd-plugin-*` ×11, `bd-*-taint` ×6). The
"where-to-look" arg is mostly `--tree`/`--work` (aliased, 134 each — good) but leaks
into `--dir`(8), `--root`(5), `--docs`(6), `--zip`(12). *Takeaway:* one arg
convention + subcommand grouping (Phase 26).

### Phase 3 — Interface consistency
`--json` 131/218 (60%) · `--selftest` 131/218 · `--tree` 135/218. **83 tools accept
no tree argument** — they hard-code paths. The interface is ~60% standardized; the
40% drift is a real friction and blocks a uniform harness.

### Phase 4 — Exit-code convention ★
**Only 4/218 tools use exit-3 as a gate/block code**; 120 use *some* nonzero exit
with no shared meaning. There is **no standard "this blocks a cut" contract** —
`bd-cut && ...` chaining is unreliable because a tool's nonzero could mean "error" or
"violation." A one-line exit-code standard (0=ok, 3=policy-block, 2=tool-error) would
make composition safe.

### Phase 5 — Tool→tool call graph ★
Only **38/218 tools call another tool; 134 are islands** (call nothing, called by
nothing). Most-depended-on: `bd-invariant-engine` (5), then `bd-pack`/`bd-precut`/
`bd-seam-finder` (3). **There is no hub and almost no composition** — 218 sharp
lenses, few decisions. This is the true capability ceiling (Phase 28).

### Phase 6 — Mutation surface ★
84/218 tools write. **46 of the writers have zero error handling** (no `try/except`
anywhere). A tool that *edits the source tree* with no guard is a corruption risk —
this is the class the review's "verify-after-write" footgun (bd-bump PIN-CORRUPTION)
was about, un-generalized across 46 tools.

### Phase 7 — Selftest QUALITY (the illusion) ★★
131 tools have `--selftest`, but **only ~10 contain a negative-control signal.** The
other ~121 selftests are **smoke tests** ("does it run"), not correctness tests. So
the suite's apparent 60% test coverage is largely *theater* — a selftest that only
proves the tool doesn't crash would not have caught `bd-envscan` v1's false-negative
or `bd-precut`'s F-03 blind spot. **This is the single most important toolchain
finding:** the tests on the tools mostly don't test the tools.

### Phase 8 — Error handling / silent failure ★
**116 tools use broad/bare `except` (255 sites total).** Broad exception swallowing
means a tool can *report success while having silently eaten the real error* — the
exact shape behind the review's "false all-clear" (bd-envscan). Pervasive.

### Phase 9 — Discipline claims vs enforcement
147 tools *claim* read-only in their docstring; 18 claim determinism/idempotence.
None are *verified* — the claims are prose. (Ironic given the whole thread: the
tools that audit the product for unverified claims make unverified claims.)

### Phase 10 — Complexity hotspots
`bd-opv` (1,335 LOC) is a *subsystem* wearing a tool's clothes; `bd-cut` (588),
`bd-optpack` (516), `bd-corpus` (425). These few need their own real test suites —
they're too big to trust on a smoke selftest (Phase 7).

═══════════════════════════════════════════════════════════════════
## PART II — Coverage & capability matrix (Phases 11–15)
═══════════════════════════════════════════════════════════════════

### Phase 11 — Domain coverage (lopsided)
Deepest: security/taint (22), plugin (11), the evidence/proof cluster (~10),
scrub/redaction (~12). Thinnest: **integration (3), decomp (5)** — i.e. the *active
programs* (app.py→blueprints decomposition; the yt-dlp/JD/interop integrations) are
the *least* tooled, while mature/defensive areas are over-tooled. Tooling has drifted
away from where the work is.

### Phase 12 — Detect / Repair / Gate matrix ★
Nearly every tool **detects**. Very few **repair** (`--fix`/`--update` exists on a
handful: bd-imports, bd-regen, bd-redaction-compiler). Only 4 truly **gate** (Phase
4). *The suite finds problems, rarely fixes them, rarely blocks on them* — the loop
is open at both ends.

### Phase 13 — Static / Runtime / Live evidence tiers ★
Static analysis dominates. Runtime evidence exists (chaos family, the proofs). **Live
evidence is rare** — `bd-dltest`, `bd-live`, `bd-verify-live`, `bd-runner-nav`,
`bd-render`. This is *why* the review's F-06 precision gap is structural: the toolset
mostly can't prove behavior, only shape, so "validated" tends to overstate.

### Phase 14 — Footgun coverage ★
The 8 in-sync gate tests + `bd-footguns` (7 seeded) mechanically cover ~15 footguns.
STATE carries **63** footguns. **~75% of footguns are prose-only** — documented,
forgettable, unenforced. The living-context system started closing this; it's ~25%
done.

### Phase 15 — Orphans (map hygiene is good)
Only **7/218 tools are absent from the `bd-tools` map** — and all 7 are shared libs
(`bdtools_sec/taint`), internal gates (`bd-audit-gate`, `bd-triage`), or this
session's new tools. *Bloat is NOT hiding in orphans* — it's in family redundancy
(Phase 26). Good discovery hygiene.

═══════════════════════════════════════════════════════════════════
## PART III — The source & test system (Phases 16–20)
═══════════════════════════════════════════════════════════════════

### Phase 16 — Source scale & the monolith
550 modules, 176,753 LOC. **`app.py` alone is 7,022 LOC** (4% of all source in one
file) — the standing decomposition target. `runner.py` (3,241), `runner_extractors`
(2,300). 18 modules >1,000 LOC; only 3 >2,000. The codebase is *mostly* well-factored
(550 modules) with **a few genuine monoliths** — the decomp program is correctly
scoped but under-tooled (Phase 11).

### Phase 17 — Test scale (the crown jewel)
982 test files, 172,780 LOC, ~7,736 test functions — **nearly 1:1 test:source LOC.**
This is an extraordinary test investment and the system's real asset. The RED-first
discipline runs on it. (Contrast Phase 7: the *product* is deeply tested; the *tools*
are not.)

### Phase 18 — In-sync gate coverage ★
The integrity of 7 derived-doc artifacts rests on **only 8 in-sync/invariant gate
tests** — and 2 of those (env-tranche, config-danger) were mis-filed on-stash-only
(Phase 25). A thin, load-bearing layer; a single wrong regen slips through if its gate
isn't banded (the F-01/F-03 class).

### Phase 19 — Route / parity reality ★★
1,012 routes across 162 blueprints (980 API, 32 pages). spa_wired = 383 (**37%**).
operator_facing = 849. **449 operator-facing API endpoints are UNWIRED to the SPA** —
i.e. **~53% of operator-facing functionality has no UI.** This is the **largest
product debt in the entire system**, dwarfing any toolchain issue, and it's
invisible in the daily cut flow. (The session's 696 "Check JD coverage" button moved
this by exactly 1.)

### Phase 20 — Guard floor (healthy)
7 guard files, 4,257 LOC, byte-pinned SHAs. Tight, well-defined, and it held all 8
cuts this session. **This part of the system is in excellent shape** — a model for
the rest.

═══════════════════════════════════════════════════════════════════
## PART IV — Process & failure architecture (Phases 21–25)
═══════════════════════════════════════════════════════════════════

### Phase 21 — The cut pipeline as a state machine
bootstrap (8 chained steps) → edit → RED-first → regen (≤6 derived docs) → bump
(3-part atomic) → cut (band + verify + guard) → present → **stash-GREEN** → handoff →
pack. **~7 gates, each a failure point.** The review's failures clustered at the two
*model-based* gates (band selection, env-tranche) — the ones requiring judgment, not
a mechanical check.

### Phase 22 — Session-close order
build+verify+present → STOP → stash-GREEN → handoff → pack. `bd-ship` exists to
orchestrate the mirror of `bd-boot`, yet I hit 3-iteration friction (missing
STATE/Backlog/Roadmap). **The order is enforced by *convention*, not a hard
interlock** — a stash-GREEN gate before handoff would make it unbreakable.

### Phase 23 — Memory / context architecture ★
**Four overlapping stores with unclear authority:** (1) `STATE.json` (per-session,
mechanical, authoritative-per-cut); (2) static PK in `/mnt/project` (drifts unless
re-pasted); (3) agent memory (stale-by-construction); (4) `FOOTGUNS.json` (new,
executable). The review's stale-memory failures (F-02/F-20) live in the seam between
these. `bd-brief` now derives (1) live; the others still need a single authority
model.

### Phase 24 — Review-finding coverage (F-01…F-24) ★
Mapping the 24 findings to *current* mechanical coverage: **now caught** — F-01
(bd-footguns + bd-envscan v2), F-03 (bd-precut generators), F-13 (bd-envscan
delegate). **Partially** — F-06/F-07 (needs `bd-egress` tiered evidence), F-05 (needs
live netns smoke). **Still process-only** — F-04/F-19 (capability probe now in
bd-brief but not enforced), F-20 (footgun corpus 25% mechanized). **~40% of the
review's findings now have a mechanical catch; 60% remain discipline-dependent.**

### Phase 25 — Gate observability: the false barrier, quantified ★
Of the load-bearing gates: the env-tranche + config-danger + route-map + function-
index + import-graph + pin gates **all run in-sandbox** (proven this session). Only
**~2** genuinely need stash — the full 11.7k parallel suite (flake behavior) and a
live capture/egress. **The sandbox↔stash barrier is ~90% imaginary** — most of what
was deferred "to stash" is runnable locally. This is the single correctable bias
behind the SEV-1.

═══════════════════════════════════════════════════════════════════
## PART V — Synthesis (Phases 26–30)
═══════════════════════════════════════════════════════════════════

### Phase 26 — Redundancy quantification (subtractive gain)
15 tool families with severe overlap (evidence ×10, scrub ×12, plugin ×11, KB ×9,
taint ×6, config ×6, fuzz ×5, chaos ×5, rollback ×5, band-derivers ×4, gates ×6,
contract ×4, lineage ×4, schema ×5, egress ×7). Consolidating each into a subcommand
tool over a shared engine: **218 → ~144 (−34%)** with *more* capability (unified
evidence chain, detect+repair KB, tiered egress). **The biggest single lever is
removal.**

### Phase 27 — System-wide risk register (ranked) ★
1. **449 unwired operator endpoints** (53% of the product has no UI) — largest, most
   invisible debt.
2. **Selftest illusion** — 92% of tool selftests are smoke, not correctness.
3. **46 unguarded tree-mutating tools** — corruption risk.
4. **`app.py` 7k-LOC monolith** — the decomp target, under-tooled.
5. **75% prose-only footguns** — forgettable, unenforced.
6. **134 island tools + no exit-code standard** — no safe composition.
7. **4-store memory architecture with unclear authority** — stale-context source.

### Phase 28 — Capability frontier (genuinely missing)
No metrics/trend/ratchet layer · no shared analysis cache · no continuous evidence
chain · thin live-evidence tier · rare detect→repair · no negative-control selftest
standard · no diff→band wired into `bd-cut` · no fleet rollup for per-site tools.

### Phase 29 — Prioritized program (ROI-sequenced)
1. **Negative-control selftest backfill** on the ~10 release-gating core mutators
   (kills the selftest illusion where it's most dangerous).
2. **`bdtools_metrics` + `--ratchet`** (biggest capability multiplier).
3. **Consolidate top-5 families** (evidence/scrub/plugin/taint/KB) = −44 tools.
4. **Unify gate+band into `bd-precut`→`bd-band`, wired as `bd-cut` step 0** (kills the
   F-01 class + fragmentation).
5. **Exit-code standard + broad-except audit** on the 46 unguarded writers.
6. **`bdtools_cache`** → interactive; enables `bd-scan-all`.
7. **`bdtools_evidence` chain** → `bd-review-pack` becomes a query.
8. **Live-evidence tier (`bd-egress` + netns smoke)** → closes F-05/F-06/F-07.
9. **Footgun mining** → drive the 75% prose corpus toward detectors.
10. **Memory-authority model** → one source of truth across the 4 stores.
11. **`bd-decomp` advisor** → tool the under-tooled decomposition program.
12. **Chip the 449-endpoint parity debt** → a per-cut ratchet that forbids *new*
    unwired operator endpoints (stop the bleeding, then drain).

### Phase 30 — Meta-review (limits & confidence)
- **What this analysis cannot see:** true selftest efficacy (I detected negative-
  control *signals*, not proof — some smoke tests may test more than they appear;
  confidence *medium*); runtime behavior of the tools; the FE/UX quality behind the
  449 endpoints (count ≠ severity — some may be intentionally headless).
- **Confidence by part:** Phases 1–10 (tool structure) **high** (direct source
  metrics); 11–15 (coverage) **high**; 16–20 (source/tests/routes) **high** (direct
  counts); 21–25 (process) **medium-high** (structural + observed); 26–29
  (synthesis) **medium** (judgment on top of data); Phase 7 & 19 findings **high and
  material**.
- **The single thesis, 30 phases in:** the system is **product-strong and
  tool-sprawling** — a deeply-tested 176k-LOC product with a healthy guard floor,
  carried by a toolchain that is **redundant (−34% possible), under-tested (92% smoke
  selftests), under-composed (134 islands), and pointed away from the active work**
  (decomp/integration least-tooled). The highest-leverage moves are *subtractive and
  hardening*: **consolidate the families, give the load-bearing tools real
  (negative-control) tests, standardize composition, and turn the biggest invisible
  debts — 449 unwired endpoints and 75% prose footguns — into ratchets that can't
  regress.** Not one of these is a new feature; all of them make what exists provably
  trustworthy.
