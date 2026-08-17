<!-- verified-against: v3.66.446 -->
# DECOMP HAZARD REGISTER — @v3.66.446

Numbered, durable catalogue of decomposition hazards proven in this program. Each
future `DECOMP_FORENSICS_<module>_<ver>.md` references hazards by ID. Project
knowledge (version-agnostic). The decomposition program is CLOSED; its per-cut history
lives in the CHANGELOG plus canonical backlog rows. The runner/db/blueprint invariants are
consolidated in `ADVANCED_PROJECT_KNOWLEDGE.md` section I (full registry `DANGER_MAPv2.md`).

Legend — **Visible?**: does the hazard surface at import time / in the sandbox, or
only on-stash? The on-stash-only ones are the dangerous class: a green import +
surface check is **not** proof.

| ID | Hazard | Trigger | Why it bites | Visible? | Mitigation |
|---|---|---|---|---|---|
| **H-01** | Overlay-can't-delete (stale shadow) | `.py`→package split deployed by `unzip -o` overlay | The old `<target>.py` stays on stash, shadowing the new package → `dependency_graph` re-counts the monolith → in-sync FAIL | On-stash only | Physical `rm <target>.py` on stash in the deploy step **OR** prefer the **shim** default (`DECOMP-R1`): keep `<target>.py` as an ADD-only re-export shim — no `rm`, no shadow |
| **H-02** | `__file__` depth shift | `__file__`/`parents[]`/`with_name`/`dirname` in the moved code | Parent dir shifts one level on `.py`→package; paths resolve wrong | Import-INVISIBLE | Lens #2 pre-scan; rewrite to the package-aware depth; keep an asset `_common` if needed |
| **H-03** | Path/floor tests | a test reads the file **by path** or counts a tree-wide floor/INV tag | Package conversion or a moved tagged-comment breaks the read/count | Sandbox-INVISIBLE | Lens #9/#10; glob-aggregate the path (`_AggregateSrc`/`_bd_runner_src`); preserve every tagged comment; band the test |
| **H-04** | Guard/importer privacy trap | an external module (esp. a release **guard**, which can't be edited) does `from pkg.<target> import _private` | The shim must re-export that **private** at package root or the importer breaks; guards are immovable | Import-visible (if you test the right importer) | Lens #6/#7; re-export ALL externally-imported privates at the package `__init__` |
| **H-05** | Manual-bump PIN drift | hand-editing `__init__.py:26` without regen | `test_pin_index_in_sync` + `kb_golden_questions(what-pins)` + `version_pins_match_live` fail | Sandbox-INVISIBLE | Every version bump runs `python tools/build_pin_index.py` / `bd_regen` |
| **H-06** | gui_parity itemset footgun | adding a new `tools/` file (e.g. a surface-lock emitter) | `gui_parity_inventory` itemset shifts → `test_v3_66_302`/`test_parity_method_aware` | Sandbox-visible if banded | Regen `reports/gui_parity_inventory.*` + band the two parity tests |
| **H-07** | from-import REBIND trap | a test rebinds `pkg.NAME = x`; a *different* function reads `NAME` as a bare global; the two become different namespaces post-split | `from .sub import NAME` freezes a separate binding → the monkeypatch no-ops at the read site | Sandbox-INVISIBLE (call-time only) | Reader resolves via the **package object at call time** (`import pkg as _p; use _p.NAME`); `__init__` re-exports `NAME`; gate with the **call-time behavioral test from the extracted zip** (provider_resolve: `test_v3_66_16`) |
| **H-08** | Forbidden package name `templates/` | naming the data package `templates/` | Collides with Flask `template_folder="templates"` and `test_preflight_checklist`'s `isfile(templates.py)` | Mixed | Use `site_templates/`; keep `templates.py` as an ADD-only shim |
| **H-09** | Cross-module handler-attribute ref | non-test code reads `app.<moved_fn>` / `getattr(app,'<fn>')` after the fn moved to a blueprint | A real **runtime** regression class; the `route_map` invariant cannot see it (routes unchanged) | Sandbox-visible if swept; else runtime-only | Sweep ALL non-test code for `<module>.<moved_name>`/`getattr` on every handler move (lesson @436); retarget to the use-site or a lazy accessor |
| **H-10** | Latent NameError on untested path | an endpoint references a module the file never imported; only errors on an untested path | A pre-existing bug a pure-motion cut would otherwise "carry"; can also be mis-"fixed" inside a motion cut | Hidden until that path runs | When extracting, a free name resolving to **nothing** in the source module is a **pre-existing bug**: preserve byte-identically in the motion cut, **FLAG it**, fix RED-first in a **separate** cut (FIX-1 @446: scrape_listing/httpx) |
| **H-11** | ROUTE_INDEX ownership flip | a route moves `(app)`→`(<group>)` | `test_kb_golden_questions` reads ROUTE_INDEX ownership | On-stash-capture ONLY | grep tests for `kb.query_routes(path=...)` on moved paths; update golden ownership in the same cut |
| **H-12** | Handler body bound on bare `@app.route` literal | a structural-grep test bounds a handler body off a literal `@app.route(...)` line | Blueprint move changes the decorator → the body bound breaks (lens #9) | Sandbox-INVISIBLE | Re-bound the test on the next `def`/`@` boundary, not the literal (caught @443) |
| **H-13** | Monkeypatch seam break | a test patches `<module>.<fn>`; the fn moves to a mixin/submodule | The patch retargets the old name; the use-site now reads the new home | Sandbox-visible if family-swept | Retarget the patch to the **use-site** module (runner Phase 3: 6 seams @402, 1 @403) |
| **H-14** | Lazy-accessor state back-edge debt | the thin-core-shell `_app_<name>()` `getattr` accessor pattern (shipped across 149 blueprints) | Every blueprint reaches back into app.py for shared state → hub-and-spoke with back-edges; cycle-safety rests on a convention; app.py stays a hidden hub | Architectural (not a test failure) | The robustness target `DECOMP-R2` (hoist state to `config.py`/`app_state`, import at module top) + `DECOMP-R0` (the import-graph gate that flags any residual back-edge) |
| **H-15** | Capture-surfaced runtime no-op | a moved fn is resolved off the wrong module and silently no-ops | e.g. `housekeeping.cache_clear()` resolved `api_status` from `app.api_status` (no-op since the move) until capture surfaced it @436 | Runtime/on-stash only | Part of the H-09 sweep; verify cross-module resolutions actually resolve post-move |

## The two meta-rules these reduce to

1. **A green import + surface-lock is necessary, not sufficient.** H-02/H-03/H-05/
   H-07/H-09/H-10/H-11/H-12/H-15 are all sandbox-invisible or runtime-only. The
   binding gate is the **on-stash full suite**, and the call-time behavioral band
   from the **extracted zip** is what catches the rebind/no-op classes.
2. **A pure-motion cut moves bytes and nothing else.** Pre-existing bugs (H-10) are
   FLAGGED and fixed RED-first in a separate cut; the only sanctioned edits in a
   motion cut are the mechanical import-depth / `__file__`-depth transforms.
