# runner_decomp_kit -- start the runner.py decomposition cold

Everything a fresh session needs to execute the runner.py (SiteRunner) decomposition
without re-deriving structure from a 12,065-line file. Derived from source @v3.66.392
via ~24 AST/grep passes.

## Read order
`STATE.json` -> newest `KB_HANDOFF` -> `RUNNER_DECOMPOSITION_PLAN.md` (section 0) ->
`RUNNER_MODULE_MAP.md` -> `RUNNER_IMPORT_MAP.md` -> `RUNNER_STATE_CONTRACT.md`.

## Contents
Docs (drop in kb/ or project knowledge):
- `RUNNER_DECOMPOSITION_PLAN.md` -- the runbook: cold-start, invariant+gate, the mixin
  architecture + **cycle rule**, what's verified safe, evidence cut-order, **cut 1 fully
  worked**, the per-cut loop, consolidated gotchas, and the full KB-doc taxonomy (sec 9).
- `RUNNER_MODULE_MAP.md` -- target layout + complete 167-method -> module index (spans).
- `RUNNER_CALLGRAPH.md` -- per-method callers/callees (the seam map).
- `RUNNER_IMPORT_MAP.md` -- per-unit imports + the cycle rule (kernel-tagged).
- `RUNNER_STATE_CONTRACT.md` -- shared-state bus + persistence + locks + exceptions + config.
- `RUNNER_PUBLIC_API.md` -- rename-unsafe external surface + the runners registry.
- `RUNNER_EVENT_VOCAB.md` -- 78 log_event kinds + apprise map (silent-breakage guard).
- `RUNNER_TEST_COVERAGE.md` -- per-unit canary set + structural-only units.
- `DECOMPOSITION_LOG.md` -- per-cut audit-trail skeleton (cut-0 filled).

Tools (drop in tools/):
- `runner_api_snapshot.py` -- the invariant GATE (method-set+kind+MRO+exports). Validated.
  **Run `--check` every cut.**
- `runner_struct.py` -- regenerates MODULE_MAP + CALLGRAPH.
- `runner_seams.py` -- regenerates IMPORT_MAP + STATE_CONTRACT.
- `runner_contracts.py` -- regenerates PUBLIC_API + EVENT_VOCAB + TEST_COVERAGE.
- `runner_extract_unit.py` -- **scaffolds a unit's mixin extraction** (mechanical move;
  validated end-to-end on `accounts`). Run `<unit> --apply`, then add imports + review.
(Keep the `UNITS` grouping in sync across the 4 tools if the inventory changes.)

## Start (cold)
```sh
cd /home/claude/work && bd-preflight
cp <kit>/tools/runner_*.py tools/ ; mkdir -p kb
python3 tools/runner_struct.py && python3 tools/runner_seams.py && python3 tools/runner_contracts.py
python3 tools/runner_api_snapshot.py --write kb/runner_api_snapshot.json
python3 tools/runner_api_snapshot.py --check kb/runner_api_snapshot.json   # expect PASS
```
Then PLAN section 5 (cut 1, the kernel) -> section 7 (the per-cut loop). Depth-announce
+ operator go PER CUT (Tier-A). Fold the 8 docs into the per-session version.zip as you go.

## Baseline (@v3.66.392, decays on first cut -- the pre-flight re-derives)
- 12,065 lines; SiteRunner 167 methods; 12 module funcs; core stays 23 methods/2504 lines.
- 12 required exports; 0 pre-existing import cycles; 0 async; not a release guard.
- **cut 1 (util kernel) is a HARD prerequisite** (demonstrated: extracting any kernel-
  referencing unit first -> circular ImportError). After cut 1, mixin cuts are order-free.
- Order: util -> integrations -> extractors -> {manual,challenge,teach,integrity,browser}
  -> {queue,auth,accounts,scheduler,telemetry} -> transport last. Real gate: on-stash 10204/0/59.
