<!-- verified-against: v3.66.446 -->
# DECOMPOSITION PLAYBOOK — BulkDownloader

Reusable, **version-agnostic** method for splitting any monolith module in this project (`dev_suite`, `runner`, `deep_detect`, `app.py`, ...). Belongs in project knowledge (durable). §0-9 are the core method; §10-14 are execution-proven extensions added after the full program completed @v3.66.446. Per-target findings live in a `DECOMP_FORENSICS_<module>_<ver>.md`; hazards are catalogued in `DECOMP_HAZARD_REGISTER.md`.
`runner`, `deep_detect`, `app.py`, …). Belongs in project knowledge (durable). The per-target findings
live in a `DECOMP_FORENSICS_<module>_<ver>.md`; this is the method that produces them and the mechanics
that make the cut safe.

---

## 0. The two laws

1. **A decomposition is a pure structural move** — zero behavior change. No RED-first (nothing new to
   fail). The existing suite IS the spec; **identical-green before and after, run from the extracted zip,
   is the proof.** The only sanctioned body edits are the mechanical transforms in §3 (import depth,
   `__file__` depth) — everything else is byte-identical relocation.
2. **Preserve the import surface exactly.** Every name any caller uses must resolve identically after the
   move — *including private names that external modules import* (see §4, the guard trap).

---

## 1. The forensic pass (run BEFORE planning any split)

Run these greps on the target; each is a distinct failure class. Stop when passes stop yielding new
*classes* (typically ~10–12). Record every hit in the forensics doc.

| # | Lens | Grep / check | Why it bites |
|---|---|---|---|
| 1 | Duplicate fn names | `grep '^def ' \| sort \| uniq -d` | Python keeps the last def; a split can change which wins |
| 2 | **`__file__` depth** | `grep -nE '__file__\|parents\[\|with_name\|dirname'` | parent dir shifts one level on `.py`→package — **invisible to import tests** |
| 3 | Mutable module state | `grep -nE '^_[a-z]+ ?= ?(\{\}\|\[\])'` then trace writers | scattering touchers gives each submodule its own copy → silent divergence |
| 4 | Import-time side effects | col-0 lines that aren't def/class/import/const | re-export imports all submodules; any side effect runs once |
| 5 | Module-level `import <target>` | `grep '^.*import <target>'` (not in-fn) | forces the package to load mid-import → cycle/order risk |
| 6 | **Guard/tool import FORM** | in each importer: `from pkg.<target> import (...)` vs `import <target>; .X` | direct-name imports (esp. of **privates**) the shim must reproduce; guards can't be edited |
| 7 | Test import forms | same, across the test family | module-attr = shim-covered; direct-name = must be at package root |
| 8 | Already-extracted siblings | `grep 'from . import _<x>'` + `ls _*.py *_data.py` | delegations are transform targets; precedent for placement |
| 9 | **Literal/structure grep tests** | `grep -rln '<target>.py\|read_text.*<target>'` tests/ | a test that reads the file BY PATH breaks on package conversion — sandbox-invisible |
| 10 | Tag/floor tests | `grep -rln 'INV-\|floor\|rglob' tests/` | tree-wide count tests require preserving every tagged comment |
| 11 | Dynamic access | `grep 'getattr(<target>\|vars(<target>\|dir(<target>'` | reflective dispatch needs all names at root + risks `dir()` pollution |
| 12 | Name collisions | each planned submodule name vs `^def <name>(` | a fn named like a submodule clashes in `__init__` |
| 13 | Coverage map | per test file: which target fns it calls | finds untested movers (riskiest) AND **undercounted inventory** (a test names a tool your section walk missed) |
| 14 | Single-file assumptions | manifest walk / PIN / FUNCTION_INDEX referencing the path | usually none; confirm namelist `+N` is the only churn |
| 15 | **Cross-module attribute ref** | `grep -rnE '<module>\.<fn>\|getattr\(<module>' bulk_downloader/` (non-test) | a handler/fn referenced as `app.<moved>` becomes a runtime no-op after the move — **route_map cannot see it** (H-09, H-15) |
| 16 | **from-import rebind seam** | `grep -rn '<module>\.[A-Za-z_]\+ *=' tests/` (a test rebinds a module attr) | post-split the define-site and read-site are different namespaces → a `from .sub import NAME` freezes a separate binding and the rebind no-ops (H-07) |
| 17 | **Monkeypatch target** | `grep -rn 'monkeypatch\|setattr(<module>\|patch(.<module>' tests/` | a patch on `<module>.<fn>` retargets the old name when the fn moves to a submodule (H-13) |
| 18 | **Free-name-resolves-to-nothing** | for each moved fn, resolve every free name in the source module | a name the file never imported = a **pre-existing latent bug** (NameError on an untested path) — preserve byte-identically, fix RED-first separately (H-10) |

> **The ones that hide:** #2 (`__file__`), #9/#10 (path/floor tests), and #15–#18 (cross-module
> attribute refs, from-import rebind, monkeypatch targets, free-name-resolves-to-nothing) do not surface
> at import time and are **sandbox-invisible** — they ship broken if you trust a green import + surface
> check. They surface only in the on-stash suite or the call-time band from the **extracted zip**. Hazard
> IDs (H-NN) reference `DECOMP_HAZARD_REGISTER.md`.

## 2. Target shape

```
pkg/<target>/
  __init__.py     # re-export shim: explicit `from .sub import (names…)` + __all__  (NO `import *`)
  _common.py      # privates used across ≥2 submodules; the ONE depth-correct _repo_root()/_pkg_dir()
  <domain>.py …   # sections grouped by responsibility; section-local privates + constants move WITH them
```

Grouping is by responsibility and is **tunable** — what's load-bearing is §3/§4, not the bucketing.
Keep any **guard-imported** function in a stable, named submodule (don't let it drift).

## 3. The two mechanical transforms (the only sanctioned body edits)

- **Relative-import depth.** Every `from . import X` (sibling-of-target) inside a moved function →
  `from <pkg> import X` (absolute), kept **lazy/in-function** if it was (preserves cycle-dodging). Absolute,
  not `from .. import`, so a future re-bucket never re-breaks the dot count. A miss raises `ImportError`
  on first call → the smoke test catches it.
- **`__file__` depth.** Route every `__file__`-anchored path through ONE `_common` helper whose depth is
  corrected for its new location (a function moved one dir deeper needs `.parents[N+1]`). Never leave
  inline `dirname(__file__)` path math in a moved function.

Intra-package (`from ._common import …`, `from .sibling import fn`) uses single-dot relative — correct
within the package.

## 4. The guard / external-private trap

Before writing the shim, list **exactly** what each external importer pulls:
`grep -nE 'from <pkg>.<target> import' tools/*.py bulk_downloader/*.py`. If a **byte-locked guard** (e.g.
`tools/build_release.py`) does `from <pkg>.<target> import (_private, public)`, the shim **must** export
those names — *including the privates* — or the guard fails to import and **cannot be fixed** (editing it
is a guard re-baseline). The shim's `__all__` ⊇ (public surface ∪ guard-required privates).

## 5. In-sync artifact decision table (what regenerates, by move type)

| Move type | FUNCTION_INDEX | ROUTE_INDEX / ENDPOINT_CATALOG / gui_parity / G12 | DEPENDENCY_GRAPH |
|---|---|---|---|
| **Module split** (no route, fn stays importable) | only if target ∈ {app.py, runner.py} | **no** (routes don't move) | **yes** (new submodule nodes/edges) |
| **Route group → blueprint** (app.py) | yes | **yes** (defining file changes) — regen catalog+index, flip gui_parity, G12 | maybe |
| **Helper extraction** (runner god-class) | yes (runner.py tracked) | no | yes |

`build_route_index` always runs **LAST** (after `gui_parity_inventory`). All structural/in-sync tests are
**sandbox-invisible until run from the extracted zip** — band the ones the move type touches, plus
`test_function_index_in_sync` to *prove* the ones that should NOT move didn't.

## 6. Validation — characterization, not RED-first

1. **Surface-lock test** (lands with the cut): freeze the public-name set (+ guard-required privates) from
   the **pre-move** tree; assert `dir(<target>)` public names == frozen set after; smoke-import every
   submodule (catches §3 import misses). See `test_dev_suite_surface_lock.py` for the template.
2. **Re-run the whole target test family unchanged** — identical green is the behavioral proof. Map sections
   → tests (lens #13); the `__file__`/path-dependent sections (#2) MUST have behavioral coverage in the
   band, or add a characterization test before moving them.
3. Everything from the **extracted zip**.

## 7. Deploy — the overlay-can't-delete rule

`.py`→package, or any file rename/removal, **cannot be overlaid** (`unzip -o` only adds/overwrites). The
cut's deploy note must instruct: `rm <old path>` (or `rsync --delete`), then clear `__pycache__`/`.pyc`
(stale bytecode also shadows), then overlay + restart + confirm `/api/health` version. A stale module
file next to its replacement package is an ambiguous-resolution hazard.

## 8. Landing & parallelization

- **One consolidated cut per module split** (a half-moved package is worse than either state). It's not a
  guard cut and (for a clean split) not a route cut → normal cut, large diff, surface-lock + family re-run
  is the net.
- **Collision matrix** (why splits can't land in parallel as separate cuts): every cut bumps the single
  version line and regenerates *global* artifacts. Two cuts touching the same regenerated artifact collide.

  | pair | collides on |
  |---|---|
  | `dev_suite` + `deep_detect` | DEPENDENCY_GRAPH |
  | `runner` + any `app.py` group | FUNCTION_INDEX |
  | two `app.py` groups | same file + ROUTE_INDEX/catalog/gui_parity/G12 |

  **Viable shapes:** (a) parallel *drafting* → serial integration lowest-ripple-first
  (`dev_suite` → `deep_detect` → `runner` helpers → `app.py` groups one at a time); or (b) **batch** the
  zero-route splits (`dev_suite`+`deep_detect`+`runner` helpers) into ONE cut, regenerate each artifact
  once, ship once. Never batch `app.py` route groups — they deserve isolated cuts.

## 9. Per-split execution checklist

1. Forensic pass (§1) → write `DECOMP_FORENSICS_<module>_<ver>.md`. 2. Freeze the surface (lens #13 list).
3. `_common` first (depth-correct helpers). 4. Move sections, applying §3 transforms as you go.
5. Generate `__init__.py` (explicit re-exports + `__all__` ⊇ guard privates). 6. `rm` the old file in the
work tree. 7. Smoke-import the package + every submodule. 8. Regen only the artifacts §5 says move; run
their in-sync tests + `test_function_index_in_sync` from the extracted zip. 9. Confirm guard SHAs
byte-identical. 10. `bd-cut`. 11. Deploy note leads with the §7 `rm` step.

---

## 10. The thin-core-shell pattern (route monoliths) — proven @v3.66.446

For a Flask-route monolith (`app.py`), the cut shape proven across 149 blueprints:

1. **The core file stays a file**, not a package — it keeps the app object, the request hooks, the
   registration/wire block, the config kernel, the boot/state machinery, and the non-domain shell routes.
2. **One blueprint module per domain**, registered **fail-open** in the wire block (a broken blueprint
   degrades that domain, never the whole app).
3. **Shared mutable state via reference-identical lazy accessors** (`_app_<name>()` `getattr` into the core
   at request time) so every blueprint sees the *same* `s_cfg`/`runners` object without a module-top import
   cycle. Core-local helpers are delegated at call time the same way.
4. **Invariant = `route_map` snapshot diffed EMPTY** on every cut. Build
   `build_release --skip-tests --baseline <prev>.zip` over the existing dist (no `--prebuild-spa`; FE 0/0/0);
   verify from the extracted zip.

**Known debt (H-14):** the lazy accessors are runtime back-edges into the core — they keep the core a
hidden hub. For a clean DAG, follow route extraction with a **state/config kernel hoist** (move shared
state to an importable `config.py`/`app_state`, retire the accessors). Separate, higher-risk motion — not
part of the route cuts.

**Giants:** a very large domain can land as a single blueprint (fewer cuts, chunkier module) or be
sub-sliced by sub-prefix (lower per-file ceiling, more cuts). Both preserve the EMPTY-`route_map`
invariant — a cuts-vs-module-size trade, not a correctness one.

## 11. Pure motion vs pre-existing bugs (hard rule)

A motion cut moves bytes and nothing else. If a lens (esp. #18) surfaces a **pre-existing** bug — a free
name resolving to nothing, a long-dead no-op — do **not** fix it inside the motion cut. Preserve it
byte-identically, **flag it**, and fix it **RED-first in a separate cut** (precedent: FIX-1 @446,
`scrape_listing`/`httpx` NameError). This keeps the motion provably pure and the fix properly tested.

## 12. Deploy default: prefer the shim, else physical `rm` (extends §7)

`unzip -o` cannot delete, so a `.py`→package split leaves the old file shadowing the package →
`dependency_graph` in-sync FAIL. **Prefer the ADD-only re-export shim** (keep `<target>.py` as a thin shim
over the package: no `rm`, no shadow, preflight `isfile` stays green) wherever the importer-set + preflight
allow (templates precedent). Otherwise **physical `rm <target>.py`** in the deploy step. Never name a data
package `templates/` (H-08) — it collides with Flask `template_folder` + the preflight `isfile(templates.py)`
check; use `site_templates/`.

## 13. The verification ladder (what actually proves a cut)

In strict order, each a distinct net:

1. **Surface-lock / `route_map` / `runner_api_snapshot`** — proves *nothing left*. Necessary, NOT sufficient.
2. **Import-graph gate** — proves *nothing new crept in* (only intended edges added). The complement that
   catches lazy-accessor sprawl (H-14) and accidental coupling. (Build it: `tools/decomp/import_graph_gate.py`
   + a baseline-frozen edge-set test.)
3. **Banded suite from the EXTRACTED zip** — the only place the sandbox-invisible classes (lenses #2/#9/#10/
   #15–#18) surface; include the by-path sweep, the consumer family, and the full sync/index/golden class.
4. **`verify_release --zip` gated on true `$?`** — banner/version-consistency (build does not catch a stale
   banner).
5. **On-stash full suite** — the **binding** gate; confirm `/api/health` flips first (a green sandbox against
   a stale process is worthless).

## 14. Robustness practices (program-level)

1. **Land the import-graph gate first** — the missing complement to the surface invariants and the only
   automated check for the back-edge debt.
2. **Default to shims over `rm`** — removes an entire deploy-time failure mode for a tiny residual file.
3. **Build the forensic pass as a tool, not by hand** — a reusable `tools/decomp/forensic_probe.py` emits
   the lens table (incl. an encoding report for emitted literals, per-submodule stdlib-import derivation,
   live-object surface flags, cross-const invariants) + `--emit-lock`. One command per target; reproducible
   and auditable.
4. **Split on cohesion; stop when cohesion stops improving** — do not split to hit a LOC number. Extra
   modules/edges/shims are more surface for the invisible coupling hazards. A god-class kernel and a clean
   shell are fine end states.
5. **Fix the state topology, not the file size** — the robustness win in a route monolith is retiring the
   lazy back-edges (the state/config kernel hoist), not shrinking the shell. A small file 149 modules reach
   into is more fragile than a larger leaf-of-the-DAG.
