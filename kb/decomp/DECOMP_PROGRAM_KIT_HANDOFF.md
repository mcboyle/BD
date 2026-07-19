# DECOMP PROGRAM KIT — next-session handoff (built on v3.66.392)

Everything the next sandbox needs to resume (or regenerate) the monolith-decomposition
program. **Nothing here is landed in the work tree or cut** — the tree is byte-identical
to v3.66.392 (`bd-preflight` PASS). These are planning/authoring deliverables. Execution
(the cuts, landing anything in the tree) is gated and needs Matt's explicit `go` + a
depth declaration.

> Re-derive live state from `STATE.json` + the newest `KB_HANDOFF` each session; this
> README is the *program* state, not the live build state. Every count below is a
> v3.66.392 snapshot and decays on the first cut — re-run the generators.

---

## 0. Start here

1. `bd-boot`; confirm `bd-preflight` PASS, `STATE.json` live=built=v3.66.392.
2. Stage this kit's files to `/home/claude/` (and into `tools/`+`tests/` only when a
   cut authorizes landing them).
3. Read `DECOMPOSITION_PROGRAM_ROADMAP.md` first — it is the spine (sequencing +
   verification status + the deep-dig addendum A1–A7). Then the per-target plan for
   whatever phase you're on.

## 1. The program in one paragraph

Four monoliths (50,573 lines) → packages/blueprints, **serial, lowest-ripple-first**
(every cut bumps the single version line + regenerates global artifacts → collisions
force serial). Order: **dev_suite** (1 cut, DEPENDENCY_GRAPH) → **deep_detect** (1 cut,
DEPENDENCY_GRAPH) → **runner** (~13 mixin cuts, FUNCTION_INDEX+DEP_GRAPH, kernel-first)
→ **app.py/F5.1** (~30+ blueprint cuts, all in-sync docs, last). Method is the uploaded
`DECOMPOSITION_PLAYBOOK.md` (two laws: pure motion / preserve the import surface incl.
external-imported privates). All four targets are **verified against source**; the
program is plan-complete.

## 2. File inventory (what each is, and its provenance)

**Program spine (mine):**
- `DECOMPOSITION_PROGRAM_ROADMAP.md` — sequencing + max-verify results + A1–A7 deep dig.
- `CROSS_MONOLITH_IMPORT_GRAPH.md` — generated; the lazy-held cycle proof + watch-list.

**Per-target plans/forensics:**
- `F5.1_DECOMPOSITION_PLAN.md` (mine) — the app.py plan (~75-pass verified).
- `F5.1_KERNEL_CONTRACT.md` / `F5.1_CUT_RUNBOOK.md` / `F5.1_LEDGER.md` (mine) — the F5.1
  what-never-moves contract, the per-cut copy-paste ritual, the per-cut record.
- `DECOMP_FORENSICS_deep_detect_v3_66_392.md` (mine) — the deep_detect forensic pass
  (filled the gap); surface = 125 names (89 public + 36 external privates), tool-verified.
- `DECOMPOSITION_PLAYBOOK.md`, `DEVSUITE_*.md`, `DECOMP_FORENSICS_dev_suite_*.md`,
  `FORENSIC_PASS_LOG_dev_suite_*.md`, `RUNNER_DECOMPOSITION_PLAN.md` + `runner_kit/`
  (uploaded by Matt) — the dev_suite + runner plans/kits. **Re-stage from Matt's uploads
  if not in this kit** (some uploads evict mid-session).

**Generators (mine) — run these; never hand-maintain their outputs:**
- `app_decomp_map.py` — app.py AST membership map (views/helpers/globals, clusters,
  split-risk). `--domain <name>` for a single blueprint's movers.
- `route_map_snapshot.py` — app.py url_map (rule, methods, bare-name) snapshot.
- `deep_detect_surface.py` — deep_detect's 125-name external surface; `--emit-lock`
  prints the frozen sets for the surface-lock test.
- `cross_monolith_graph.py` — regenerates `CROSS_MONOLITH_IMPORT_GRAPH.md`; `--check`
  exits 1 on a module-level inter-monolith cycle (CI gate after any monolith cut).

**Tests (mine):**
- `test_route_map_invariant.py` — the F5.1 prime gate (3 fns, **verified PASS** on the
  392 tree). Ships to `tests/` WITH `route_map_baseline.txt` beside it.
- (`test_dev_suite_surface_lock.py` — uploaded by Matt; the dev_suite equivalent.)

**Generated artifacts (regenerable; saved so the next sb need not recompute):**
- `route_map_baseline.txt` — 944 rules, sha256 `d92ccf3d8e05...` (the F5.1-OPEN frozen
  contract). Ship as `tests/route_map_baseline.txt`.
- `APP_DECOMP_MAP.json` — 70KB full app.py membership map (top_funcs 780, views 681,
  helpers 99, globals 47, movable 78.6%, 15 clusters, split-risk none).

## 3. Verified facts + corrections (don't re-litigate)

- **dev_suite:** 174 fns (125 pub + 49 priv), 0 classes, 97 lazy sibling imports, 8
  `__file__` code sites, `_FIXTURE_SERVERS: dict={}` @L5104, F19 guard import + F17
  maintenance sibling — all CONFIRMED. **CORRECTION: 5 INV tags (001,002,004,005,006),
  NOT 11** (tree-wide is 6 distinct/23 occurrences). Section count 82 vs 84 is a doc
  inconsistency (sections aren't a source marker).
- **runner:** SiteRunner = 167 methods exact (1 dunder+54 pub+112 priv), 12 top-level
  exports, 2nd class `_ManualDownloadSession`, 0 routes, FUNCTION_INDEX-tracked, not a
  guard. 13-unit map sums EXACTLY to 167 (144 mixin + 23 kernel). HIGH-risk units:
  extractors(1841L), transport(1800L), auth(765L). **`runner_util.py` (kernel) does NOT
  exist — Cut 1 creates it;** mixins import kernel from `.runner_util`, never `.runner`.
- **deep_detect:** 98 fns (38 pub + 60 priv), 0 classes; 0 `__file__`, 0 lazy sibling
  imports (TRUE LEAF), 0 INV, 0 dynamic, 0 routes — **simplest mechanics**. But surface =
  **125 names incl 36 external privates** (mostly its test family importing internals) —
  **widest surface**; `_DD_COUNTERS` is mutated STATE (co-locate metrics touchers).
- **cross-monolith:** SCC {app, dev_suite, runner} is a real cycle, but **only ONE
  module-level inter-monolith edge exists (app→runner)** and the module-level-only graph
  is ACYCLIC → invariant HOLDS. **`runner→app` must stay lazy** (the runner phase's prime
  constraint). External-private export is a PRIMARY lens program-wide (≥8 modules do it).

## 4. Regenerate any artifact (commands)

```bash
cd /home/claude/work
python3 tools/route_map_snapshot.py > /tmp/route_map_baseline.txt   # F5.1 baseline (sha must = d92ccf3d8e05)
python3 tools/app_decomp_map.py                                      # APP_DECOMP_MAP summary
python3 tools/deep_detect_surface.py --emit-lock                     # deep_detect 125-name frozen sets
python3 tools/cross_monolith_graph.py > CROSS_MONOLITH_IMPORT_GRAPH.md
python3 tools/cross_monolith_graph.py --check                        # CI gate
```
(The generators default to finding the package relative to themselves; pass `--root
/home/claude/work` if run from outside `tools/`.)

## 5. First action (pick one — both are Matt's call)

- **Execute Phase 1 (dev_suite OR deep_detect)** — GATED. Needs `go` + depth. dev_suite
  is most-ready (surface-lock exists; apply the INV-5 fix). deep_detect needs its
  surface-lock generated first (`deep_detect_surface.py --emit-lock`) — trivial.
- **Author remaining smaller pieces** — free (no `go`): the dev_suite/deep_detect
  surface-lock test files, a `FORENSIC_PASS_LOG_deep_detect`, or per-target ledgers.

## 6. What's still NOT made (if asked)
- `tests/test_deep_detect_surface_lock.py` (generate from `deep_detect_surface.py
  --emit-lock`) and a deep_detect responsibility-bucketing (the section→submodule map).
- Per-target ledgers for dev_suite/deep_detect/runner (the F5.1_LEDGER is the template).
- Nothing is landed in the tree; nothing is cut. All execution remains gated.
