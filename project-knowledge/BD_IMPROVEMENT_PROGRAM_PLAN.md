<!-- verified-against: v3.66.805 -->
# BD Improvement Program — Execution Plan

> **PARTIALLY SHIPPED — status measured v3.66.805.** This is a forward plan; several
> items are now built, and two of its pinned numbers have moved:
>
> - **F1 endpoint-debt ratchet: BUILT.** `unwired_operator_endpoints` is a live ceiling
>   metric in `bd-ratchet`, enforced by `bd-precut --gate`. The plan pins it at "~280";
>   the live baseline is **223**. The debt fell rather than grew.
> - **E3 ratchets: PARTLY BUILT.** `coupling_ratio`, `defect_DP_total`, `spa_wired` and
>   `unwired_operator_endpoints` are all live ratchet metrics.
> - **A3 `bdtools_cache`: BUILT**, and its spec was corrected in the build — see the
>   note on A3 below (content-hash keyed, never mtime).
> - **A2 `bdtools_metrics` / `bd-metrics`: NOT BUILT** (verified absent from PK and
>   `bin/`). The ratchet capability landed in `bd-ratchet` instead.
> - The anti-bloat guardrail's "~154 tools" target has **not** been approached: live is
>   **249**. The subtractive thesis was tested at 805 and the retirement pool measured
>   effectively empty (87 listed-only, orphan 0, 80 carrying curated triggers).
>
> Re-derive any figure here before acting on it.

*Turns the 30-phase deep dive + the tool/capability recommendations into one
sequenced, dependency-ordered program. Grounded in two verified constraints:*

- **Toolchain work needs no version bump / stash cycle** (Waves are tool-only). So
  tool hardening runs on a **separate track** that does NOT compete with feature cuts.
- **The "449 unwired endpoints" is really ~280** operator-UI debt — 168 are cockpit
  governance endpoints (intentionally cockpit-served, not SPA) and are excluded. The
  ratchet scopes to the true 280.

Everything obeys the existing release discipline: **RED-first, single-concern cuts,
7 guards byte-identical, stash-GREEN before handoff, no bloat.**

---

## 0. Structure: two tracks + a ledger

| Track | What | Gate | Cadence |
|---|---|---|---|
| **T — Toolchain** | tool hardening, consolidation, new capability tools | `bd-*` selftests + `bd-mkbdsuite` repack; **no version bump** | continuous, parallel to F |
| **F — Feature/Product** | app changes: the endpoint-debt ratchet, decomp, live-evidence wiring | full RED-first cut → stash-GREEN → handoff | one concern per cut |

A single **`PROGRAM_LEDGER.md`** tracks every item: `id · track · phase-ref · status
· acceptance · evidence`. Each completed item cites its proof (band output / selftest
/ ratchet baseline), so the program is itself audit-complete.

**Guardrail (anti-bloat, enforced):** the program's *net tool count must fall*.
Consolidations (−74) run alongside new tools (+~10) → target end-state **~154 tools**.
Any PR that raises the count without a consolidation is rejected.

---

## PHASE A — Foundations (unblock everything) · Track T · ~1 session

These three are prerequisites for most later work; do them first.

**A1. Exit-code + interface standard** *(Phase 4, 3)*
Adopt `0=ok · 2=tool-error · 3=policy-block`; standardize the tree arg on
`--tree/--work`. Publish as `bdtools_convention.md` + a `bd-selfcheck` rule that
flags a tool violating it. **Acceptance:** `bd-selfcheck --conventions` passes; new
tools inherit it.

**A2. `bdtools_metrics`** *(Phase 28 — highest multiplier)*
A ~100-line shared module: per-cut append-only JSONL keyed by version; `record(name,
value)` + `bd-metrics --trend <name>` + `--ratchet <name> --baseline <zip>`.
**Acceptance:** `--selftest` with a real negative control (a regressed value fails
the ratchet); two tools write to it.

**A3. `bdtools_cache`** *(Phase 5/28)*
Shared AST + dep-graph + route-index cache (extends the `bdtools_sec`/`bdtools_taint`
shared-lib pattern). **Acceptance:** the 5 taint tools drop from SLOW to sub-second on
a warm cache; determinism selftest.

> **SHIPPED, AND THE SPEC WAS CORRECTED IN THE BUILD — noted v3.66.805.** This item
> originally read *"mtime-invalidated"*. The shipped `bdtools_cache.py` (present in
> BOTH the static PK and `bin/`) deliberately does the opposite and says why:
> *"KEY ON CONTENT, NEVER ON mtime. mtime lies: this session's `cp -a` tree restore
> preserved mtimes exactly, so an mtime-keyed cache would have served findings for the
> OLD file content."* Entries are keyed by `(file_sha256, logic_key)`. Do not
> "restore" the mtime wording — mtime-as-freshness is a catalogued failure shape, and
> a gate must never trust a cache keyed on it (see the 706 ratchet incident).

---

## PHASE B — Harden the load-bearing core (kill the illusion) · Track T · ~2 sessions

**Phase 7 finding: 92% of selftests are smoke.** Fix it where it's most dangerous
*first*, not everywhere.

**B1. Negative-control selftest backfill — the 10 release-gating mutators**
`bd-cut · bd-bump · bd-handoff · bd-pack · bd-regen · bd-guardcheck · bd-checkpoint ·
bd-blast · bd-suites · bd-touched`. Each gets a selftest with a *proven-failing*
control (e.g. `bd-bump` selftest must catch a corrupted pin; `bd-guardcheck` must
catch a 1-byte guard change). **Acceptance:** each selftest RED on an injected fault,
GREEN on clean — the pattern this whole thread earned.

> **Retired at rev-702 (note added v3.66.805):** `bd-blast`, `bd-suites` and
> `bd-touched` no longer exist — all three merged into `bd-band-derive` (see D1
> below, which is the plan that shipped). The names are RETAINED here because
> this is a historical plan record and they were the real subjects at authoring
> time; rewriting them would falsify it. For current work use `bd-band-derive`.

**B2. Broad-except audit on the 46 unguarded writers** *(Phase 6/8)*
For each tree-mutating tool with zero error handling: add verify-after-write
(`ast.parse` before trusting an edit) + narrow the broad excepts that can mask a
failed write. **Acceptance:** `bd-selfcheck --writers` reports 0 unguarded writers.

**B3. Docstring backfill** on the 8 undocumented core tools (bootstrap chain).

---

## PHASE C — Consolidate the families (subtractive, −74 tools) · Track T · ~4 sessions

**Phase 26.** One family per work-unit; each is: build the shared engine → port the
N tools to subcommands → keep thin back-compat shims → repack. Ordered by ROI:

| Order | Consolidation | Tools | Net | Emergent capability |
|---|---|---|---|---|
| C1 | **`bd-evidence`** (chain + views) | 10 | −8 | one continuous evidence chain; `bd-review-pack` becomes a query |
| C2 | **`bd-scrub <verb>`** | 12 | −10 | one redaction rule source → scan/prove/bundle |
| C3 | **`bd-plugin <verb>`** | 11 | −9 | unified plugin governance |
| C4 | **`bd-taint <kind>`** (over `bdtools_taint`) | 6 | −5 | +`--fix` (suggest sanitizer site) |
| C5 | **`bd-kb <verb>`** (detect+repair) | 9 | −6 | closed-loop doc/memory truth (folds `bd-memhygiene`, `bd-factcheck` fix) |
| C6 | **`bd-config` / `bd-fuzz` / `bd-chaos` / `bd-rollback` / `bd-contract` / `bd-history` / `bd-schema`** | 34 | −26 | uniform subcommand engines |
| C7 | **`bd-egress`** (static+runtime+live, tiered) | 7 | −6 | the complete confinement story in one verdict |

**Acceptance per family:** the subcommand tool's selftest covers each former tool's
core case; `bd-tools` map updated; net count drops. **Discipline:** each merged
engine *delegates to the authoritative source* (the v2 lesson) — no re-derivation.

---

## PHASE D — Unify + wire the gate (kill the F-01 class) · Track T · ~1 session

**Phases 12/21/25 + the review's INV-2.**

**D1. `bd-band <derive|check|run>`** — merge `bd-blast`/`bd-suites`/`bd-touched`/
`bd-bandcheck` into one diff-derived band engine (the capability already exists,
triplicated). **Acceptance:** given a diff, emits the exact `--suites` incl. the
env-tranche/route-map gates when the diff warrants them.

**D2. `bd-precut` absorbs `bd-ready`/`bd-doctor`/`bd-treecheck`/`bd-footguns`** into
one gate that runs the footgun registry + derived-doc staleness + version/pin.
**D3. Wire `bd-precut --gate` as `bd-cut` step 0** — the cut *cannot run* past a live
footgun or a stale doc. **Acceptance:** replays of the 699 tree BLOCK; the clean 700
tree passes; `bd-cut` refuses on a ratchet regression (via A2).

This single phase mechanically closes F-01, F-03, F-13, and the gate fragmentation.

---

## PHASE E — Capability layer (compose + prove behavior) · Track T · ~2 sessions

**E1. `bdtools_evidence` chain** *(Phase 13)* — every gate/proof appends a signed
record; a cut's bundle auto-assembles.
**E2. Live-evidence tier** *(Phase 13, F-05/F-06/F-07)* — `bd-netns-egress-live` +
fold into `bd-egress`; a claim states which tier proved it (fixes the overstatement
class).
**E3. Ratchets on the top metrics** *(via A2)* — coupling, mutation score, coverage,
open-env-vars, **and the endpoint-debt counter** (see F1). Each: a cut fails if the
metric regresses.
**E4. Co-pilots** — `bd-cut --plan` (blast+risk+behavior+footguns) and `bd-decomp`
advisor (Phase 11 — tool the under-tooled decomp program) over the shared cache.
**E5. `bd-footgun-mine`** — drive the 75%-prose corpus toward detectors (Phase 14).

---

## PHASE F — Product debts (the real leverage) · Track F · ongoing, RED-first cuts

The toolchain phases exist to make *this* safe. These are full feature cuts.

**F1. Endpoint-debt ratchet — STOP THE BLEEDING FIRST** *(Phase 19, the ~280)*
Add a test (`test_operator_endpoint_parity_ratchet`) that fails if the count of
*unwired operator-facing non-cockpit API* rises above a pinned baseline (~280).
Wire the counter into `bdtools_metrics`. **Acceptance:** a new unwired operator
endpoint fails the cut. *This is the highest-ROI product item* — it converts an
invisible, growing debt into a monotonic one, cheaply, before any UI work.

**F2. Drain the 280 — themed FE cuts** *(Track F, one blueprint-cluster per cut)*
Wire the highest-value unwired clusters to the SPA, ratchet-decrementing each cut:
`sites` (53) → `data_layer` (15) → `vpn_api` (12) → `settings_center` (6) → … Each a
standard FE cut (RED-first parity+source-literal, spa_wired++, stash-GREEN). The
ratchet (F1) guarantees the number only falls.

**F3. `app.py` decomposition** *(Phase 16, the 7k monolith)* — resume the standing
app.py→blueprints program, now *tooled* by the `bd-decomp` advisor (E4) which emits
the seam + blast + import-edge/route handling per extraction. Each extraction a
guarded, RED-first cut.

**F4. Live-validate F5** *(the deferred netns/browser work)* — once CAP_NET_ADMIN +
a wg tunnel are on stash, exercise `bd-netns-egress-live` (E2) as the acceptance gate,
then build the Phase-2 browser-in-netns wiring.

**F5. Memory-authority model** *(Phase 23)* — designate `STATE.json` (mechanical) +
`FOOTGUNS.json` (executable) as the two authorities; `bd-brief` derives both at
session start; static PK becomes a cache with a drift gate (`bd-kb --truth`).

---

## Sequencing & dependency graph

```
A1 exit-code std ─┐
A2 metrics ───────┼─→ D (gate+band) ─→ E3 ratchets ─→ F1 endpoint ratchet ─→ F2 drain
A3 cache ─────────┘        │                              (F1 needs A2)
                           │
B1 core selftests ─────────┤   (B before C: harden the tools you'll consolidate)
B2 writer audit ───────────┘
C1..C7 consolidations ─────────→ E1 evidence, E2 live-tier, E4 co-pilots, E5 mining
                                        │
                                 E4 bd-decomp ─→ F3 app.py decomp
                                 E2 live-tier  ─→ F4 F5 live-validate
```

**Critical path to the biggest wins:** A2 → D → F1 (invisible debt becomes a ratchet)
and A1/B1 → C (safe consolidation). Everything else parallelizes on Track T.

---

## Milestones & exit criteria

| Milestone | Definition of done | Proves |
|---|---|---|
| **M1 Foundations** | A1–A3 shipped; metrics+cache+convention live | multiplier + speed unlocked |
| **M2 Core trusted** | B1–B2: 10 gating tools have negative-control selftests; 0 unguarded writers | Phase 7 illusion gone where it matters |
| **M3 Gate unified** | D: `bd-cut` blocks on footgun/stale/ratchet; 699-replay BLOCKS | F-01/F-03 class closed mechanically |
| **M4 Lean suite** | C1–C7: **~154 tools** (from 218), all selftested | −34% surface, +capability |
| **M5 Debt ratcheted** | F1: unwired-operator-endpoint count can only fall | the 280 stops growing |
| **M6 Debt draining** | F2: spa_wired rising cut-over-cut; app.py shrinking (F3) | product debt actually paid down |

---

## Effort & honesty

- **Track T (A–E):** ~10 focused sessions, no stash cycles, fully parallel to product
  work. Low risk (tools, not release artifacts).
- **Track F (F):** ongoing standard cuts at the normal cadence; F1 is ~1 cut, F2 is
  many small cuts, F3/F4 are the long programs.
- **What this plan deliberately does NOT do:** no new dashboards, no vanity tools, no
  big-bang rewrite. Consolidations keep back-compat shims so nothing breaks mid-flight.
- **The plan is self-verifying:** `PROGRAM_LEDGER.md` + `bdtools_metrics` mean progress
  is *measured* (tool count, selftest coverage %, footgun-mechanization %, unwired-
  endpoint count) — the program ratchets itself, exactly as it asks the codebase to.

---

## One line

Two tracks: **harden and shrink the toolchain** (test the load-bearing core for real,
consolidate 15 families −34%, unify the gate, add the metrics/cache/evidence
platforms) on a no-stash track; and **ratchet then drain the real product debts** (the
~280 unwired endpoints and the 7k-LOC monolith) on the normal cut track. Sequenced so
`metrics → gate → ratchet` lands the biggest invisible win first, and every step is
measured by the same ratchet discipline it installs.
