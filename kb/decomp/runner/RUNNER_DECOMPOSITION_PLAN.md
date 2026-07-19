# RUNNER_DECOMPOSITION_PLAN.md  (v3 -- deep cold-start runbook)

Tier-A pure code **motion** for `bulk_downloader/runner.py` (12,065 lines @v3.66.392):
one ~10.9k-line `SiteRunner` class (167 methods) -> ~13 mixin modules + a ~2.5k-line
core. Sibling to app.py F5.1. **Planning doc** -- execution is gated: depth-announce +
explicit operator **go PER CUT**. Never batched with a behavior change.

This kit ships, ready to drop into `tools/` + `kb/`:
- **this plan** (runbook) + **`DECOMPOSITION_LOG.md`** (per-cut audit, cut-0 filled)
- **`tools/runner_api_snapshot.py`** -- the invariant GATE (validated)
- 4 generated structural docs: **`RUNNER_MODULE_MAP.md`** (method->module index),
  **`RUNNER_CALLGRAPH.md`** (seams), **`RUNNER_IMPORT_MAP.md`** (per-unit imports +
  cycle rule), **`RUNNER_STATE_CONTRACT.md`** (shared-state bus + persistence + locks)
- 3 generated contract docs: **`RUNNER_PUBLIC_API.md`** (rename-unsafe surface),
  **`RUNNER_EVENT_VOCAB.md`** (78 event kinds), **`RUNNER_TEST_COVERAGE.md`** (canary map)
- 3 regenerators: `tools/runner_struct.py`, `runner_seams.py`, `runner_contracts.py`

> Every line number / count is a v3.66.392 snapshot and **decays on the first cut**.
> The section-0 pre-flight re-derives them. The contract is the invariant in section 2.

---

## 0. COLD START -- before any cut

**Read:** `STATE.json` -> newest `KB_HANDOFF` -> this plan -> `RUNNER_MODULE_MAP.md` ->
`RUNNER_IMPORT_MAP.md` -> `RUNNER_STATE_CONTRACT.md`. Then:

```sh
cd /home/claude/work && bd-preflight            # tree == source zip, byte-for-byte
cp <kit>/tools/runner_*.py tools/ ; mkdir -p kb   # the gate + 3 doc-generators + the runner_extract_unit scaffolder
# re-derive structure (regenerates all 7 docs if the inventory shifted):
python3 tools/runner_struct.py && python3 tools/runner_seams.py && python3 tools/runner_contracts.py
# freeze the invariant baseline (ONCE, before cut 1):
python3 tools/runner_api_snapshot.py --write kb/runner_api_snapshot.json
python3 tools/runner_api_snapshot.py --check kb/runner_api_snapshot.json   # expect RESULT: PASS
```
Expected anchors: SiteRunner **167** methods, **12** module funcs, core stays **23
methods / 2504 lines**, **12** required exports, 0 duplicates. If the method count
drifted, re-derive the unit assignment before cutting.

---

## 1. Why runner.py is a different problem than app.py

app.py -> Flask **blueprints**, **url_map-identity** invariant. runner.py has **no
routes** (verified: 0 route decorators) -- it is one class. So:
- Unit of motion = a **method group**; mechanism = **mixins**; invariant =
  **method-inventory + import-surface identity** (section 2).
- **Free cross-check:** ENDPOINT_CATALOG / ROUTE_INDEX / gui_parity must stay
  byte-identical every cut. Drift = the cut leaked.
- runner.py is **NOT** a release guard -> every cut is **backend-only, no-vite**
  (zip the existing dist; zero FE re-hash). But it is the **hottest test path in the
  repo** -- motion is validated by keeping the suite green, not by new tests.

---

## 2. The invariant (and the gate that enforces it)

A cut is correct iff **all four** hold, checked from the EXTRACTED zip:
1. **Method-set identity.** SiteRunner method names unchanged. Baseline 167.
2. **Kind identity.** Each method keeps instance/`@staticmethod`/`@classmethod`/
   `@property` (4 are staticmethods -- section 3).
3. **No MRO duplication.** No method name defined in >1 class in the MRO.
4. **Import-surface + behavior.** All **12** externally-imported symbols stay
   importable from `bulk_downloader.runner`; on-stash full suite holds
   **10204 / 0 / 59**.

`tools/runner_api_snapshot.py` enforces 1-3 + the export half of 4 -- AST-only,
stdlib-only (no import; runs anywhere). **Validated**: clean source -> PASS; a method
in two classes -> FAIL naming the collision; a real move -> PASS with "owner moved".
The 12 required exports (the complete tree-wide surface, several of which move to
runner_util and MUST be re-exported): `SiteRunner, _ts, set_global_concurrent_cap,
get_bandwidth_history, _check_video_magic_bytes, disk_free_gb, gate_candidate_url,
resolve_url_attribute, DEFAULT_MAX_CONCURRENT, DEFAULT_MIN_RESOLUTION,
_BD_TO_APPRISE_EVENT, _bw_history`. Run `--check` every cut.

---

## 3. Architecture -- mixins + the kernel, and THE cycle rule

`SiteRunner` has no base classes today (verified: no `super()`/`type(self)`/
`__class__`). Conversion:

```python
class SiteRunner(QueueMixin, SchedulerMixin, AuthMixin, IntegrationsMixin,
                 ExtractorsMixin, TransportMixin, BrowserMixin, ChallengeMixin,
                 TeachMixin, IntegrityMixin, AccountsMixin, ManualMixin,
                 TelemetryMixin):
    def __init__(self, ...):   # stays in runner.py (L1135-1307), unchanged
```

- **Mixins are pure method-holders -- NO `__init__`, no `super()`.** All state is
  created by the single `SiteRunner.__init__`; every moved method sees identical
  attributes. (`__init__` calls `_load_rl`, `_restore_queue`, `_start_auto_retry`,
  `start_scheduler` at construction -> those mixins must be bases when their cut lands;
  automatic under the mixin pattern.)

### THE cycle rule (the load-bearing constraint)
The codebase has **zero pre-existing import cycles** (runner imports 8 intra-pkg
modules; none import runner back). The decomposition is the FIRST place a cycle could
appear: a mixin that does `from .runner import X` while runner does
`from .runner_<x> import <Mixin>` is a cycle. **Rule: a mixin must import only from
`runner_util` (the kernel: imported by all, imports nothing back) or from a source
module -- NEVER from `runner`.** Therefore **anything a mixin needs that lives in
runner.py today must move to `runner_util`.**

**Cut 1 (util kernel) is therefore a HARD PREREQUISITE, demonstrated.** Method<->method
coupling between mixins goes via `self` and is order-independent -- but a mixin that
references a module-level KERNEL symbol cannot be extracted before the kernel exists:
its only import source would be `from .runner import X`, which cycles. Verified by
actually extracting `accounts` (needs `_resolve_safe`) before util: Python raised
`ImportError: cannot import name 'AccountsMixin' ... circular import`. Since most units
reference at least one kernel symbol (accounts->`_resolve_safe`, transport->
`record_bandwidth`/`gate_candidate_url`, scheduler/queue->`_ts`), **util must be cut 1**;
after it, the remaining mixin cuts are order-independent.

`RUNNER_IMPORT_MAP.md` lists exactly what
each unit needs; the kernel-bound names are tagged `[KERNEL]`.

Two consequences this forces:
- **GOTCHA A (mandatory rewrite).** `_looks_like_media`, `_integrity_size_ok`,
  `_probe_outcome`, `_promote_or_abort` are `@staticmethod` called as
  `SiteRunner._foo(...)` at 5 sites (all inside transport). On the transport cut,
  rewrite them to **`self._foo(...)`** (staticmethods are instance-callable) -- not
  cosmetic: it removes the only `SiteRunner` reference in transport, so `TransportMixin`
  needs no `from .runner import SiteRunner` and the cycle never forms.
- **GOTCHA B (class constant travels).** `_RETRY_DELAYS_BY_KIND` (class-body L11999) is
  read by `_handle_failure` (telemetry). Define it on `TelemetryMixin`'s body so `self.`
  resolves it via the MRO. Likewise shared consts `DEFAULT_MIN_RESOLUTION` (used by
  extractors) and `_BD_TO_APPRISE_EVENT` (used by telemetry) -> `runner_util`, not
  runner.py, or they create the cycle.

### Style
No `from __future__`, no `typing` import, **zero `async def`** (runner is fully
thread-based; `login_async` is a misnomer). **Zero annotation type-refs** (so no
eager-eval `NameError` on move). Match this -- no `Optional[...]` / PEP-604 in new
modules.

---

## 4. What the deep dive verified is SAFE (so you don't re-check)

24 structural passes; these are confirmed NON-issues -> mixin conversion is mechanically
clean:
- No `global` statement inside any method (no rebind hazard) -- EXCEPT the cap setter,
  handled in section 6.
- No `super()` / `type(self)` / `__class__` / `self.__dict__` / `vars(self)` /
  pickle / `deepcopy(self)` -- no introspection or serialization surprises.
- No method-signature annotation references a module-level name -- no eager-eval break.
- No decorators beyond the 4 staticmethods (no decorator-import hazard).
- `build_release.py` walks the tree with `rglob("*")` -> new `runner_*.py` modules are
  **auto-included** in the zip (no manifest edit).
- `multi_conn` is a clean one-directional boundary (transport calls
  `multi_conn.probe/download`; multi_conn never imports runner).

---

## 5. Cut order (evidence + coverage)

Metrics from the call-graph (`in<-core`/`out->core` = cross-edges with the core).
**CORE = 23 methods / 2504 lines stays.** Test-file coverage from
`RUNNER_TEST_COVERAGE.md`.

| unit | meth | lines | risk | test files | note |
|---|---|---|---|---|---|
| util (module funcs) | 12 | ~310 | LOW | n/a | **cut 1**; shakes out the kernel + re-export pattern |
| integrations | 17 | 794 | LOW | 5 | cohesive, well-tested, big win |
| extractors | 10 | 1841 | LOW | 12 | large but self-contained |
| manual | 4 | 143 | LOW-but-**0 tests** | **0** | self-contained (`_ManualDownloadSession` + 4 methods); **STRUCTURAL-ONLY validation** |
| challenge | 5 | 327 | LOW | 3 | quarantine `_try_turnstile_solve_LEGACY` separately |
| teach | 10 | 289 | LOW | 2 | lean on snapshot |
| integrity | 6 | 352 | MED | 7 | |
| browser | 9 | 337 | MED | 4 | zero out->core (clean) but mostly live-only |
| queue | 18 | 513 | MED | 20 | |
| auth | 15 | 765 | MED | 9 | |
| accounts | 8 | 215 | MED | 2 | account/pool + rate-limit predicates |
| scheduler | 15 | 407 | MED | 8 | bg loops; owns `rl_{site}.json` persistence |
| telemetry | 12 | 405 | LOW-move | 10 | `log_event` called everywhere; owns `_RETRY_DELAYS_BY_KIND` + the 78-kind vocab |
| **transport** | 15 | 1796 | **HIGH** | 11 | **cut LAST, sliced** -- VPN/Track-K engine; Gotcha A |
| core (stays) | 23 | 2504 | n/a | 39 | `__init__` + lifecycle + worker loop + orchestration |

**Recommended sequence:** util -> integrations -> extractors -> {manual, challenge,
teach, integrity, browser} -> {queue, auth, accounts, scheduler, telemetry} ->
transport (last, sliced). **Order is correctness-independent ONLY among the mixin cuts,
and ONLY once cut 1 (util kernel) is done** -- see the prerequisite in section 3. The
sequence otherwise manages diff size + risk. Per-method spans + assignment: `RUNNER_MODULE_MAP.md`.

---

## 6. Cut 1, fully worked -- the kernel (REFINED)

The util kernel is `runner_util.py`: the genuinely-leaf funcs + the shared consts the
mixins will need (the kernel that breaks the cycle). **Method SET stays 167** (these
are funcs, not methods) -- cut 1 exercises the **export-shim + kernel** mechanics.

**Move to `runner_util.py`:** the funcs `_ts, _resolve_safe, _check_video_magic_bytes,
resolve_url_attribute, gate_candidate_url, _bump_learned_stat, _bump_per_selector,
_maybe_demote_selectors, record_bandwidth, get_bandwidth_history`; the bandwidth ledger
`_bw_lock, _bw_current, _bw_history`; the url-gating consts `_GATE_URL_ATTRS,
_RUNTIME_NAV_REJECTIONS`; and the mixin-shared consts `DEFAULT_MIN_RESOLUTION,
_BD_TO_APPRISE_EVENT`.

**KEEP in runner.py (do NOT move):** `_global_sem, _global_sem_size, _global_sem_lock`
+ `set_global_concurrent_cap, get_global_concurrent_cap` + `DEFAULT_MAX_CONCURRENT`.
**Why:** the worker loop reads the bare global `_global_sem` (L5325-5340) and the setter
**rebinds** it via `global`; moving it to runner_util would make
`from runner_util import _global_sem` capture the stale `None` (the cap would silently
never apply). It is worker-coupled -- it stays with core.

**Re-export from runner.py** (these 9 move to util AND are externally imported, plus
the noqa to pass the static-analysis gate):
```python
from .runner_util import (  # noqa: F401  -- external re-export surface
    _ts, _resolve_safe, _check_video_magic_bytes, resolve_url_attribute,
    gate_candidate_url, _bump_learned_stat, _bump_per_selector,
    _maybe_demote_selectors, record_bandwidth, get_bandwidth_history,
    _bw_history, DEFAULT_MIN_RESOLUTION, _BD_TO_APPRISE_EVENT,
)
```
(`disk_free_gb` is already imported-then-re-exported -- leave it.)

**Gate:** `runner_api_snapshot --check` -> PASS (167, 12 exports). Smoke:
`from bulk_downloader.runner import get_bandwidth_history, set_global_concurrent_cap,
gate_candidate_url, DEFAULT_MIN_RESOLUTION, _BD_TO_APPRISE_EVENT, _check_video_magic_bytes`
and `from bulk_downloader.perf_lab import *` (exercises `runner._bw_history`). Then the
per-cut loop.

---

## 7. The per-cut loop (commands + expected output)

```sh
cd /home/claude/work
# (a) MOVE: `python3 tools/runner_extract_unit.py <unit> --apply` scaffolds the move
#     (extracts each method's decorator-inclusive span into `class <Unit>Mixin:` in
#     bulk_downloader/runner_<unit>.py, removes from runner.py, adds the base + import).
#     VALIDATED end-to-end on `accounts` (compiles + snapshot PASS). Then by hand: add the
#     import block per RUNNER_IMPORT_MAP.md (kernel names from .runner_util, NEVER .runner),
#     carry any owned class const, and move any associated top-level class (manual only).
# (b) INVARIANT
python3 tools/runner_api_snapshot.py --check kb/runner_api_snapshot.json   # PASS + owners moved: N
# (c) DOCS THAT MOVE (both are TEST-ENFORCED -- a stale one BLOCKS the release):
python3 tools/build_function_index.py     # test_function_index_in_sync; runner.py lines shift every cut
python3 tools/dependency_graph.py         # test_dependency_graph_in_sync (+ precut check); new import edge
#     CROSS-CHECK byte-identical (runner has no routes):
python3 tools/gui_parity_inventory.py --check
#     ARCHITECTURE_INVENTORY: robust (count>100, largest=app.py, hotspot=db.py) -- runs as canary, no regen.
# (d) BUMP (Tier-A ships per cut): __init__ L26 + slice4 pin + CHANGELOG (ASCII, anchor on prev `## v`); PIN_INDEX.
# (e) BUILD no-vite over existing dist -> FRONTEND missing/changed/added = 0; new module auto-included (rglob).
# (f) VERIFY from the extracted zip (true $?, never pipe): band the unit's canary set
#     (RUNNER_TEST_COVERAGE.md) Failed:0; verify_release.py --zip RESULT:PASS. For transport add
#     test_track_k_vpn_bind + test_v3_43_31_rate_limit + test_vpn_egress_followons + test_worker_pipeline.
# (g) 7 guards byte-identical (runner isn't one; confirm none drifted).
# (h) DEPLOY (Matt): overlay + pycache clear + restart + /api/health version; capture.sh --summary holds 10204/0/59.
# (i) RECORD: DECOMPOSITION_LOG.md row + flip RUNNER_MODULE_MAP.md status/SHA + re-pack.
```

**FUNCTION_INDEX coverage extension (do once, early):** the index covers 5 files
(app/runner/db/login/extractors). Methods moved into new `runner_*.py` modules vanish
from it unless `build_function_index.py`'s file list is **extended to include the
runner_*.py modules**. Extend it as modules are created so the index keeps documenting
the moved methods (the regen + drift test then stay green).

---

## 8. Gotchas (consolidated)

- **Cycle rule (section 3):** mixins import only from `runner_util`/source, never
  `runner`. The two forced rewrites: Gotcha A (transport staticmethods ->`self.`),
  Gotcha B (`_RETRY_DELAYS_BY_KIND`/`DEFAULT_MIN_RESOLUTION`/`_BD_TO_APPRISE_EVENT`).
- **`_global_sem` stays in runner.py** (worker-coupled, rebound) -- cut 1 does NOT move it.
- **No mixin `__init__`/`super()`.** `__init__` calls 4 mixin methods at construction.
- **Decorator-inclusive line spans:** AST `lineno` points at `def`; decorators sit
  above. `RUNNER_MODULE_MAP.md` spans already include them -- copy the full span.
- **Re-export shim needs `# noqa: F401`** (static-analysis gate).
- **`extractors.py` already exists** -> name the new module `runner_extractors.py`.
- **Thread targets + static calls are invisible to the Call-graph:** 13 methods are
  `threading.Thread(target=self._foo)` entry points (`_worker_loop`, `_watchdog_loop`,
  `_sched_loop`, the 3 `_*_enrich_after_scan`, ...) and 4 are `SiteRunner._foo`
  statics -> they show no callers but are live. Only **`_try_turnstile_solve_LEGACY`**
  is genuinely dead -- confirm zero callers, remove in a SEPARATE cut.
- **`_`-prefix is not private here:** 7 underscore methods are externally called
  (`_launch_browser, _persist_pool_state, _rotate_account_if_available, _search_site,
  _stash_scrape_preview, _stop_auto_retry, _update_job`) -- the snapshot's set-freeze
  protects them; do not "tidy" names. See `RUNNER_PUBLIC_API.md`.
- **Event vocab is a silent contract:** 78 `log_event` kinds + the
  `_BD_TO_APPRISE_EVENT` map drive SSE + notifications; almost none are named in tests.
  A dropped/renamed kind breaks them silently -> diff `RUNNER_EVENT_VOCAB.md` after any
  telemetry-touching or emitting-unit cut.
- **`manual` has ZERO direct tests** (and browser/teach/accounts are low) -> validate
  those cuts STRUCTURALLY (import-smoke + snapshot + live operator), not by sandbox tests.
- **transport last, sliced** -- the VPN/Track-K path you shipped (390/392); the 391
  lesson (fixed-window structural test overflow) applies: `test_v3_43_31_rate_limit`
  is banded; grep `tests/` for any method literal you relocate.
- **One decomposition at a time** (runner vs app.py F5.1): shared no files, but
  interleaving makes FUNCTION_INDEX/DEPENDENCY_GRAPH diffs unattributable.
- **Backend-only != low-stakes.** The real gate is the on-stash suite holding
  **10204/0/59** against `/api/health` reporting the new version.
- **Source carries 2075 non-ASCII chars** (banner rules, em-dashes, a few `+/-`,`x`,check
  marks in strings). There is **no source-file ASCII gate** (only CHANGELOG) -- extracted
  modules carry them verbatim, which is fine; preserve any inside event/log strings exactly.
- **Out of scope for this motion (future sub-extraction):** 15 methods are >180 lines and
  total ~44% of the class (`_process_one` 749, `_do_download` 468, `_update_job` 432,
  `_http_download` 428, the 5 `_try_*_extractor` 219-349 each, `start`, `_worker_loop`).
  The integration client-factories + `_*_enrich_after_scan` methods, and 4 of 5 extractors
  (shared `_progress` skeleton), are internally near-duplicated. These are real but are a
  SEPARATE refactor -- never fold a giant-method split or a dedup into a motion cut.

---

## 9. The KB doc set -- what to create as you go (for a robust, efficient KB)

Four roles. Generators are stdlib-only/AST-only/path-relative; regenerate as noted and
fold the outputs into the per-session `version.zip` so each session inherits structure
instead of re-deriving it from a 12k-line file. **That inheritance is the KB win.**

**(1) The GATE (the binding invariant)**
- `kb/runner_api_snapshot.json` + `tools/runner_api_snapshot.py` -- method-set + kind +
  MRO-uniqueness + export-surface. Freeze pre-cut-1; `--check` every cut.

**(2) STRUCTURAL NAVIGATION (the durable "where/what/how" maps -- the biggest win)**
- `RUNNER_MODULE_MAP.md` -- target layout + complete 167-method->module index (spans).
  *The* "where does X live now" lookup. Flip status/SHA each cut. (`runner_struct.py`)
- `RUNNER_CALLGRAPH.md` -- per-method callers/callees; drives ordering + shows each
  seam before cutting. Regen if grouping changes. (`runner_struct.py`)
- `RUNNER_IMPORT_MAP.md` -- per-unit module-level import needs + the cycle rule
  (kernel-tagged). Makes each cut's import block mechanical. (`runner_seams.py`)
- `RUNNER_STATE_CONTRACT.md` -- the 9-attr shared-state bus, __init__ vs lazy, the
  on-disk persistence surface (per unit), the lock/thread surface, the cross-unit
  exception contract, and the 145-key config input. The inter-mixin "API". (`runner_seams.py`)

**(3) CONTRACTS (cross-boundary surfaces a cut can break silently)**
- `RUNNER_PUBLIC_API.md` -- distinctive methods called outside runner (~41) + the
  rename-unsafe `_`-prefixed set + the `runners={}` registry pattern. (`runner_contracts.py`)
- `RUNNER_EVENT_VOCAB.md` -- 78 `log_event` kinds + `_BD_TO_APPRISE_EVENT` map; the
  SSE/notification regression guard. (`runner_contracts.py`)
- `RUNNER_TEST_COVERAGE.md` -- per-unit canary set + the structural-only units. Tells
  each cut what to run and which cuts can't be sandbox-validated. (`runner_contracts.py`)

**(4) HISTORY (audit + rollback)**
- `DECOMPOSITION_LOG.md` -- per-cut row: unit, lines moved, runner.py-after, snapshot
  PASS, band, on-stash suite, guards, zip sha, deploy. The rollback map.

**Existing docs to keep current (don't re-invent):**
- `FUNCTION_INDEX.md` -- regen each cut; **extend `build_function_index.py` to cover the
  new `runner_*.py`** (test-enforced by `test_function_index_in_sync`).
- `DEPENDENCY_GRAPH.json/.md` -- regen each cut (test-enforced by
  `test_dependency_graph_in_sync` + the precut check).
- `ENDPOINT_CATALOG` / `ROUTE_INDEX` / `gui_parity` -- cross-check byte-identical (leak
  detector; runner has no routes).
- `ARCHITECTURE_INVENTORY.md` -- robust to new modules (count>100, largest=app.py,
  hotspot=db.py); runs as a canary.
- `config_surface_inventory` -- already tracks the 145 config keys (the template schema).
- `PIN_INDEX.json` -- on the version bump.

**The compounding rule:** the same taxonomy applies to app.py F5.1 -- swap
`runner_api_snapshot`->`route_map_snapshot`, `RUNNER_MODULE_MAP`->`APP_BLUEPRINT_MAP`,
and the contract docs become route/CSRF/blueprint contracts. Build the generators once;
each later session boots with an accurate map of the decomposed code.
