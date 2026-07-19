<!-- verified-against: v3.66.446 -->
# DECOMPOSITION PROGRAM ROADMAP — @v3.66.446

Authoritative program doc, refreshed against the live tree @446. Supersedes the
rev-1/rev-2 roadmaps. **Status: the original program is essentially complete.** All
four large monoliths are decomposed; what remains is a robustness-hardening track
(below) plus an integrated leaf-target band.

Pairs with: `DECOMPOSITION_PLAYBOOK.md` (the reusable method), `DECOMP_HAZARD_REGISTER.md`
(numbered durable hazards), `DECOMPOSITION_LOG.md` (per-cut audit trail), and the
`TASK_TRACKER` (rows `DECOMP-*`, `LEAF`, `F5.1`).

---

## 0. Where the program stands @446

| Target | Before | After @446 | Phase | Status |
|---|---:|---|---|---|
| `dev_suite.py` | 9,098 | package, 10 submodules + `_common` (largest `capture_diag.py` 1,706) | 1 | **DONE** |
| `deep_detect.py` | 8,907 | package, 10 submodules (largest `orchestrate.py` 1,813) | 2 | **DONE** (forensic-pass gap closed) |
| `runner.py` | 12,065 | kernel `runner.py` 3,104 + 13 sibling mixins (largest `runner_extractors` 1,913) | 3 (397-404) | **DONE** |
| `app.py` /api (F5.1) | 20,503 | 16 SPA/shell routes only; 250→0 /api handlers across **149** `app_*.py` blueprints | 4 (405-446) | **DONE** |

On-stash @446: **10305 total / 10246 pass / 0 fail / 59 skip**, `/api/health=3.66.446`,
7 guards byte-identical throughout. `grep -cE '@app\.route("/api/' app.py == 0`.

**This is the last large-monolith split in the program.** The remaining work is
robustness hardening (§3) and the leaf band (§3, `DECOMP-LEAF`), neither of which
is a large monolith.

---

## 1. What actually shipped — the thin-core-shell pattern (record it)

F5.1 did **not** follow the rev-1 plan's blueprint-per-family grouping or its
state-stays-via-lazy-import-inside-bodies sketch verbatim. The shipped pattern,
proven across 149 modules:

- **app.py stays a file** (never became a package). It retains the app object, the
  13 hooks, the registration/wire block, the config kernel, the boot/state
  machinery, and the 16 non-/api SPA/shell/asset routes (`/m`,`/m2`,`/`,
  `/manifest.json`,`/icon.svg`,`/apple-touch-icon.png`,`/sw.js`,`/metrics`,
  `/stream/<token>`,`/screenshots/<path>`).
- **Each /api domain → its own `app_<name>.py` blueprint**, registered **fail-open**
  in the wire section.
- **Shared mutable state is reached via reference-identical lazy `_app_<name>()`
  `getattr` accessors** — a blueprint calls back into app.py at request time to get
  the one true `s_cfg`/`runners`/etc. object. app.py-local helper functions are
  **delegated at call time** the same way.
- **Invariant:** `route_map` snapshot diffed **EMPTY** on every route cut (the master
  pure-motion proof), built `build_release --skip-tests --baseline <prev>.zip` over
  the existing dist (FE 0/0/0), verified from the extracted zip.

The two giants landed as **single blueprints** (`app_sites.py` 3,584 / `app_dev.py`
1,966), not the planned sub-slices — a valid cuts-vs-module-size trade (see
`DECOMP-SITES-SUBSLICE`).

---

## 2. The ceiling reality @446 (and the robustness debt it exposes)

Largest files now:

| LOC | File | Note |
|---:|---|---|
| ~7,600 | **`app.py` shell** | the program ceiling — **not routes** (16 left), but the config kernel (`CFG_FIELDS` 436 + `DEFAULTS` 367 ≈ 800), 13 hooks, boot/state machinery, imports, comments |
| 3,584 | `app_sites.py` | single-blueprint giant (#2) |
| 3,103 | `runner.py` | intentional thin-shell kernel |
| 3,098 | `learn.py` | **uncut leaf** |
| 3,030 | `provider_resolve.py` | **uncut leaf** |
| 2,588 | `templates.py` | **uncut leaf** |
| 2,285 | `login.py` | **uncut leaf** |
| 2,172 | `capture_workbench.py` | **uncut leaf** |
| 1,966 | `app_dev.py` | single-blueprint giant |

Two robustness facts fall straight out of this:

1. **app.py's ceiling is structural state, not routes.** No further route cut lowers
   it; only the state/config kernel hoist (`DECOMP-R2`) does.
2. **The 149 lazy `_app_<name>()` accessors are a real coupling debt.** They keep
   app.py a hidden hub that ~every blueprint reaches into; the import graph is
   hub-and-spoke with back-edges, and cycle-safety rests on a *convention*. A
   2,500-line app.py that 149 modules reach into is more fragile than a 7,600-line
   one that is a clean leaf-of-the-DAG. Fixing the **state topology** (not the LOC)
   is the substance of the robustness track.

> **Principle:** split on cohesion, stop when cohesion stops improving. The runner
> kernel ~3,100 and a clean app.py shell are fine end states; forcing them smaller
> for the number's sake adds modules, edges, and shim indirection — more surface for
> exactly the sandbox-invisible coupling hazards the program already fights. The
> robustness track targets *fragility*, not file size.

---

## 3. Robustness track + leaf band (the remaining phases)

All sandbox-able; **separate from the standing gated chain** (BP-VH1/2/3 →
P3-T12-CALLSITE → Phase C). Tracker rows in parentheses.

### R0 — Import-graph regression gate (`DECOMP-R0`, Tier M, do first)
Build `tools/decomp/import_graph_gate.py` + `test_import_graph_no_new_edges`: freeze
the intended inter-module edge set; assert each cut adds only intended edges.
Complements surface-lock + `route_map` (which prove "nothing left," **not** "nothing
new crept in"). It is the gate that would flag the lazy-accessor sprawl and any
accidental coupling — the natural complement that the program has been missing.
**High ROI, low risk.** Land as a Phase-0 asset before any further cut.

### R1 — Shim-over-rm deploy default (`DECOMP-R1`, Tier S)
Adopt the templates rev-2 decision as the default: keep the old file as an **ADD-only
re-export shim** wherever preflight + the importer-set allow, instead of `.py`→package
+ `rm`-on-stash. Eliminates the overlay-can't-delete stale-shadow hazard (a forgotten
`rm` leaves the monolith shadowing the package → `dependency_graph` in-sync FAIL;
provider_resolve's confirmed-fired class). Trading a tiny residual shim for "deploy
can't leave a stale shadow" is a strict robustness win.

### LEAF — Leaf-target decomposition band (`DECOMP-LEAF`, Tier M)
The six rev-2 targets, all leaf module→package, **DEPENDENCY_GRAPH-only, 0 routes,
not guards**; kits delivered @395/396, files still whole @446. Order (risk-asc):

1. `templates.py` 2,588 → **shim** over `site_templates/` (ADD-only). Invariant =
   91-element list identity (hashed). **Never name a package `templates/`** — Flask
   `template_folder` + preflight `isfile(templates.py)` collision (hazard H-08).
2. `template_extractor.py` 1,237 → package + `_css.py`. Attribute-surface lock.
3. `capture_workbench.py` 2,172 → package + `_common`. Two **sandbox-invisible**
   couplings: a `getsource` guard the split de-fangs, and an exact-importer-set guard
   pinning the vanishing path. Band both.
4. `login.py` 2,285 → package. `ManualLoginSession` travels with the manual submodule;
   lens-#9 path-test gotcha.
5. `learn.py` 3,098 → package; `_assets.py` = `RECORDER_JS` + `TEACH_OVERLAY_JS` (two
   inert JS blobs, **move byte-identical**, 53% of the file). **Sequence after
   `deep_detect`** — import neighbor (lazy-imports `deep_detect._post_reveal_key`).
6. `provider_resolve.py` 3,030 → 8-submodule package. 🔴 **`_default_http_get`
   from-import REBIND trap** (hazard H-07): a test rebinds `pr._default_http_get`;
   the reader must resolve it via the **package object at call time** or the rebind
   no-ops post-split. `test_v3_66_16` from the **extracted zip** is the only gate.
   `rm`-on-deploy; the new `tools/` surface-lock file trips the gui_parity itemset
   footgun (hazard H-06).

### R2 — app.py state/config kernel hoist (`DECOMP-R2`, Tier A, LAST)
Extract `CFG_FIELDS` (436) + `DEFAULTS` (367) + the boot/state machinery
(`s_cfg`/`runners`/watcher/scheduler/keeper) into `config.py` / `app_state`,
**importable at module top**, so the 149 blueprints import shared state directly
instead of via the lazy `_app_<name>()` back-edges — converting hub-and-spoke into a
clean DAG (the robustness payoff) and taking the app.py shell ~7,600 → ~2,500–3,000.
**Highest-risk motion in the program:** state-identity (every reference must resolve
to the *same* object; init-order matters) is **invisible to the `route_map`
invariant**. Gate with characterization tests on the state accessors + the full
on-stash suite; run isolated, last, Tier-A (depth-announce + explicit `go`). Pairs
with R0 (the gate that flags any residual back-edge).

### SITES-SUBSLICE — giant sub-slice (`DECOMP-SITES-SUBSLICE`, Tier M)
`app_sites.py` 3,584 (the #2 file) and `app_dev.py` 1,966 landed as **single
blueprints** in F5.1 — they are the program's two remaining oversized modules and
**will be sub-sliced** to bring every blueprint under the cohesion ceiling:
`app_sites` → id_core/queue/teach/auth/lifecycle/integrations + collection;
`app_dev` → db/runtime/testci/config/lint/extract/auth/ai/net/obs/maint
(parity-EXEMPT; thin `dev_suite` wrappers). Same `route_map`-EMPTY pure-motion
invariant; DEPENDENCY_GRAPH regen per cut. Sequence after the leaf band, independent
of R2.

---

## 4. Execution order + collisions

**Order (serial; integration always serial because every cut bumps `__init__.py:26`
and regenerates *global* artifacts):**

R0 (gate) → R1 (deploy default) → LEAF (risk-asc: templates → template_extractor →
capture_workbench → login → learn → provider_resolve) → R2 (state/config hoist, last)
→ SITES-SUBSLICE.

**Collision note:** every leaf target regenerates **only** DEPENDENCY_GRAPH (all
1-artifact), so they serialize on it; a new `tools/` surface-lock file also touches
gui_parity (H-06). R2 touches FUNCTION_INDEX + DEPENDENCY_GRAPH (and possibly the
config-parity surface). None of the six leaves nor app.py/app_* is a release guard.

**Per-target invariant + surface-lock:**

| Target | Invariant | Lock |
|---|---|---|
| dev_suite/deep_detect | attribute-surface (`dir ⊇ FROZEN`) | surface-lock test |
| runner | `SiteRunner` method-set identity (167/12/0) | `runner_api_snapshot --check` |
| app.py /api | `(method,path)` url_map identity | `route_map` snapshot diff EMPTY |
| templates | 91-element list identity (hashed) | `templates_snapshot --check` |
| template_extractor / capture_workbench / login / learn | public-symbol / attribute-surface | per-target surface-lock |
| provider_resolve | surface + the call-time rebind-fix | surface-lock + `test_v3_66_16` (extracted zip) |

---

## 5. Definition of done (program)

- All four large monoliths decomposed — **met @446**.
- The six leaf targets decomposed (`DECOMP-LEAF`).
- The import-graph gate live (`DECOMP-R0`) and shim-over-rm adopted (`DECOMP-R1`).
- The app.py state/config kernel hoisted (`DECOMP-R2`) → shell a clean DAG leaf, the
  149 lazy back-edges retired.
- `app_sites.py` / `app_dev.py` sub-sliced (`DECOMP-SITES-SUBSLICE`) → every blueprint
  under the cohesion ceiling.
- `route_map`/surface-lock invariants held across the whole program; 7 guards
  byte-identical except where a guard cut is explicitly declared.
- Every cut deployed + on-stash full suite clean.

After the robustness track, the decomposition program is closed; the standing gated
chain (BP-VH → P3-T12-CALLSITE → Phase C) is the unrelated remaining work.
