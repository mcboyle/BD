# DECOMP FORENSICS — dev_suite @ v3.66.392

Findings from a multi-pass forensic read of `bulk_downloader/dev_suite.py` (9,098 lines), ordered by
severity. Pairs with `DEVSUITE_DECOMPOSITION_PLAN_v3_66_392.md` (the plan) and
`DECOMPOSITION_PLAYBOOK.md` (the reusable method). **Each finding is a thing the split must handle that
the surface plan did not, or got wrong.**

**Reconciled inventory:** 84 tool-section headers (not the 68 a digit-only regex sees — see F3),
**174 top-level defs = 125 public + 49 private**, 0 classes, **89 dev_suite-related test files**.

---

## F1 — `__file__`-depth bug (SEVERITY: HIGH — invisible to import tests)

8 sites compute paths anchored on `__file__`, all assuming `__file__` lives in `bulk_downloader/`.
`dev_suite.py` → `dev_suite/<sub>.py` shifts the parent directory **one level deeper**, so every one
silently resolves too shallow (`repo` becomes the *package* dir, not the project root):

| Line | Section | Computation | Breaks to |
|---|---|---|---|
| 623 | `_repo_root()` | `Path(__file__).resolve().parents[1]` | returns `bulk_downloader/`, not repo root |
| 1817 | §U5 dispatch tracer | `Path(__file__).with_name("runner.py")` | looks for `dev_suite/runner.py` |
| 3859 | §U26 test-meta | `here=dirname(__file__); repo=dirname(here)` | `repo`=`bulk_downloader/` |
| 4012 | dep-pin checker | same idiom | `requirements.txt` not found |
| 4075 | §U27 security | same idiom | secret-scan root wrong |
| 4184 | §U27 security | same idiom | sast/dast dirs wrong |
| 4852 | §U31 systemd/dep-pin | `repo/requirements.txt` | not found |
| 5119 | §T2 fixture controller | `repo/tools/<module>.py` | fixture module not found |

**Fix:** add ONE depth-correct helper to `_common.py` — e.g. `_pkg_dir()` (the `bulk_downloader` dir) and
`_repo_root()` (its parent). Because `_common.py` sits at `bulk_downloader/dev_suite/_common.py`, its own
`_repo_root` must use `.parents[2]` (one deeper than today's `.parents[1]`). Rewrite all 8 sites to call
these — they then become depth-independent. **This is the unavoidable non-pure-move edit.** The
surface-lock test (import + name check) will NOT catch a wrong path; only behavioral tests will —
fortunately `test_dispatch_chain` (the runner.py read), `test_t2_fixture_controller`,
`test_t17_filesystem_audit`, `test_dev_suite_tier0` (zip-manifest/auth-surface), and the §U27 security
tests all exercise `__file__`-dependent paths. **Band every one.**

## F2 — Guard shim contract (SEVERITY: HIGH — coupled to a byte-locked guard)

`tools/build_release.py` (release guard, SHA `e8142436`) imports names **directly from the package**:

```python
from bulk_downloader.dev_suite import (_manifest_excluded, zip_manifest_check)
```

`_manifest_excluded` is **private** — and the guard cannot be edited to change the import. Therefore
`dev_suite/__init__.py` **must** re-export `_manifest_excluded` (private) AND `zip_manifest_check`
(public), both from `release_lint`. A naive "export only public names" shim breaks the build_release
guard at import time. `verify_release.py` (`dev_suite.zip_manifest_check`) and
`check_version_consistency.py` (`dev_suite.version_consistency` / `.changelog_lint`) use module-attribute
access — covered by any re-export. **Net rule: the shim must include guard/tool-imported privates, not
just the 125 public names.** Confirmed guard-imported private set on 392: `{_manifest_excluded}`.

## F3 — Inventory was undercounted (SEVERITY: MED — bucketing completeness)

A digit-only section-header regex (`── 1.`) misses **T-numbered** headers (`── T34.`). The full set adds
12 sections **T34–T45**: dead-CSS finder (T34), storage-tier inspector (T35), maintenance-mode status
(T36), token/size estimator (T37), i18n coverage (T38), Ollama model-pull (T39), feature-flag console
(T40), download-window simulator (T41), golden-file manager (T42), TLS/cert checker (T43), request-replay
inspector (T44), login-flow recorder inspector (T45). Their functions (`dead_css_finder`,
`storage_tier_status`, `maintenance_mode_status`, `token_estimate`, `i18n_coverage`, `model_pull_check`,
`feature_flags_status`, …) must be placed too. **Use the frozen 125-name list, not a section walk, as the
completeness check.**

## F4 — INV-tag floor count (SEVERITY: MED — sandbox-invisible test)

`test_inv_tags_not_regressed` does a tree-wide `rglob("*.py")` and asserts the count of `# INV-` lines is
≥ a floor. dev_suite.py carries **11 `# INV-NNN`** inline tags. The rglob is location-independent (so a
package is fine), but the move must **preserve every tag comment** (dropping one regresses the floor →
FAIL). It's sandbox-invisible unless banded. **Band `test_inv_tags_not_regressed`; verify the post-move
tree-wide `# INV-` count is unchanged.**

## F5 — `_FIXTURE_SERVERS` mutable module dict (SEVERITY: MED — resolved)

The fixture-site controller keeps `_FIXTURE_SERVERS: dict = {}` at module level (running-server registry,
mutated by start/stop/status). A split that scattered its readers/writers across submodules would give
each its own copy → silent divergence. **Verified safe:** all touchers are one contiguous block
(L5094–5204, §T2) → they all land in `test_meta` together → the shared reference is preserved. (Its
docstring even notes "IMPORT-CLEAN: just an empty dict at import time.") The general rule still stands:
*any* mutable module-level state forces its touchers to be co-located.

## F6 — Already-extracted sibling modules (SEVERITY: LOW — precedent + transform target)

dev_suite already delegates to standalone siblings: `bulk_downloader/_bat_lint.py` (§19 `.bat` lint
delegates here; `test_dev_suite_tier0` imports `_bat_lint` directly) and
`bulk_downloader/login_templates_data.py` (§U18 login-template tools). Note the asymmetry: `.bat` lint is
extracted to `_bat_lint`, but `.sh` lint (§20) is still inline. The delegations `from . import _bat_lint`
/ `login_templates_data` are subject to the F-transform below (→ absolute imports).

## F7 — The one systematic body transform (SEVERITY: LOW — mechanical, fails loud)

**97 `from . import X`** lazy in-function imports (db, secrets_store, session_keeper, aiassist, login,
login_templates_data, runner, …). In the monolith `.`=`bulk_downloader`; in a submodule `.`=
`bulk_downloader.dev_suite` — wrong. Convert each to **absolute** `from bulk_downloader import X`, kept
**lazy/in-function** (the lazy app import is what dodges the dev_suite↔app cycle). A missed one raises
`ImportError` on first call → the surface-smoke test catches all 97.

## F8 — Coupling is a clean DAG (SEVERITY: NONE — confirmed safe)

- **No public→public cross-section calls of consequence:** only 5 public functions are called internally
  at all (`config_schema_audit`, `dispatch_chain`, `fixture_site_start`, `wal_checkpoint`,
  `zip_manifest_check` — 1 caller each), and each caller is almost certainly in the *same* section as its
  callee (dispatch_dry_run↔dispatch_chain, fixture start↔status, …). **Verify co-location during the move;
  otherwise add an explicit `from .sibling import fn`.** No cycles possible at this fan-out.
- **`_common` membership** (private helpers used across sections that land in *different* submodules):
  `_repo_root`(9), `_percentile`(8), `_redact`(5), `_collect_cred_refs`(5), `_human_secs`(4),
  `_read_version`(3), `_iter_route_sources`(3), `_dev_mode`(2), `_proc_uptime_seconds`(2). Section-cluster-
  local privates (`_iter_site_jars`, `_classify_credential`, `_probe_hls/_probe_dash`, `_manifest_*`,
  `_norm_ref`, `_DISPATCH_CHAIN`, `_SQL_FORBIDDEN`, …) move WITH their section. Rule: ≥2 submodules use it →
  `_common`; else local.

## F9 — Confirmed-safe properties (no action; record so they're not re-litigated)

- **No duplicate function names** (the repeated section *numbers* 33/34/35 are comment glitches only).
- **No dynamic/reflective access** to the module (`getattr/vars/dir(dev_suite)`) anywhere → explicit
  re-export is sufficient; no `dir()` pollution concern for callers.
- **No import-time side effects** — the module docstring guarantees "Nothing here runs at import time";
  only constants + an empty dict are assigned at module level → importing all submodules is side-effect-free.
- **No module-level `import dev_suite`** anywhere (app.py and all callers import it lazily, in-function) →
  the `.py`→package conversion triggers no import cycle at load time.
- **No submodule-name ↔ function-name collisions** (no function named `logs`/`maintenance`/`config_tools`/…).
- **No machinery assumes dev_suite is one file** — the manifest walk just enumerates `.py` files (expect
  `+~13` namelist entries, normal churn); neither PIN_INDEX nor FUNCTION_INDEX references dev_suite as a path.

## F10 — Deploy (SEVERITY: HIGH — operator step, from the plan)

`.py`→package: `bulk_downloader/dev_suite.py` must be **physically removed** on stash before the overlay
(`unzip -o` can't delete; a stale `dev_suite.py` shadowing `dev_suite/` is ambiguous-resolution hazard),
and stale `dev_suite.pyc`/`__pycache__` cleared. **Not a pure overlay** — see the plan §5 deploy block.

---

---

# Round 2 — deeper passes (F11–F18)

A second forensic round (≈17 further lenses) past F1–F10. Net-new material: **F11, F15, F17**. The rest
confirmed clean and are recorded so future digs don't re-litigate. After this round the investigation has
**converged** — ~33 distinct lenses total; further passes return confirmed-clean, not new risk classes.

## F11 — Fixture loader is a dynamic FILE-PATH import (SEVERITY: MED — compounds F1)

§T2's fixture controller doesn't just compute a path from `__file__` (F1, L5119) — it then **dynamically
loads** the module from that path: `importlib.util.spec_from_file_location(...)` → `module_from_spec` →
`spec.loader.exec_module` (L5116–5127). So the F1 miscomputation doesn't just return a wrong string; it
feeds a dynamic import that will fail to find `tools/<module>.py`. The centralized `_common._repo_root()`
fix (F1) resolves it, but treat this site as **doubly load-bearing** — it's the one place a wrong path
becomes a failed dynamic import rather than a benign wrong string. `test_t2_fixture_controller` covers it.

## F12 — Two move-safe dynamic patterns that must NOT be transformed (SEVERITY: LOW — trap-avoidance)

Recorded so the F7 transform doesn't "fix" them into breakage:
- **`_sys.modules.get("bulk_downloader.app")`** (L6051, prompt previewer) — reads the already-loaded app
  via a **string key**, not a relative import. The string stays correct after the move (app doesn't move).
  Do **not** rewrite it as `from . import app`.
- **`importlib.import_module(module)`** (L6163) where `module` comes from `_PROMPT_REGISTRY` — a string
  module path, not a relative import. Move-safe; not an F7 target.

## F13 — `inspect.getsource` targets app.py, not dev_suite (SEVERITY: NONE — confirmed safe)

L1074 `inspect.getsource(view)` and L1104 `inspect.getsource(hook)` read the source of **route views /
before-request hooks**, which live in `app.py`. Nothing does `inspect.getsource(dev_suite)` on the module
itself. The `.py`→package conversion does not affect these — they inspect *other* modules' functions.
(Recorded because a naive reading flags getsource as package-fragile; here it isn't.)

## F14 / F16 — Complete sibling-import transform set is ~45 modules (SEVERITY: LOW — scope correction)

The F7 transform (relative→absolute lazy imports) is larger than first stated. The full distinct set
dev_suite imports via `from . import X` / `from .X import …`: `ai_provider, aiassist, app, backup_verify,
batch_ops, captcha_relay, csv_bulk, db, dedup, detect, dev_events, dev_metrics, dev_tools,
download_window, extractors, feature_flags, flaresolverr_client, fname, heuristic_scoring, hls_downloader,
i18n, jd_bridge, log, login, login_flow_recorder, login_templates_data, maintenance, migrations, perf_lab,
qb_bridge, request_replay, secrets_store, session_keeper, site_editor, sse_broker, stealth, storage_tier,
vpn, vpn_config, __version__` + `capture_artifact_redact, constants, cookies, learn, runner`. **All ~41
exist as real files** (verified — no stale import the move would expose). §19 already uses the absolute
form (`from bulk_downloader import _bat_lint`) — precedent that the transform is idiomatic here.

## F15 — Constants need `_common` classification too (SEVERITY: MED — refines F8)

F8 covered cross-section private *functions*; module-level *constants* are a second `_common` class.
Cross-section constants → `_common`: **`_SECRET_KEY_HINTS`** (4 refs: redaction used by config_dump +
leak_scan + security cluster, i.e. different submodules) and **`_BD_ENV_VARS`** (2, env-var audit).
Section-local constants stay with their section: `_DISPATCH_CHAIN` (9 refs, all §U5), `_SQL_FORBIDDEN`
(db_tools), `_STATE_METHODS` (route-source), `_MANIFEST_EXCLUDE_*` (release_lint), `_REQ_FILES` /
`_SECRET_PATTERNS` (security/release). Rule, generalized: **any name (fn OR const) used by ≥2 submodules →
`_common`.**

## F17 — Submodule-name ↔ SIBLING-module collision (SEVERITY: MED — the sharpest round-2 find)

The proposed submodule **`maintenance`** collides with the existing sibling **`bulk_downloader/maintenance.py`**,
which dev_suite imports at L8171 (`from . import maintenance as _mw`, inside `maintenance_mode_status`).
Pass-14 (round 1) only checked submodule-name vs *function*-name and found no collision — it never checked
submodule-name vs *sibling-module*-name. Consequences:
1. A relative `from . import maintenance` inside `dev_suite/maintenance.py` would resolve to the **submodule
   itself**, not the sibling → wrong/circular. This makes the **F7 absolute-import transform mandatory**,
   not merely preferred, for any submodule whose name shadows a sibling.
2. Cleanest fix: **rename the submodule** `maintenance` → `housekeeping` (or `ops_diag`). Only this one name
   collides; the other 12 proposed names are not siblings (verified).
**Add an executable guard** (see the hardened surface-lock test): assert no planned submodule name equals a
`bulk_downloader/*.py` sibling stem.

## F18 — `_PROMPT_REGISTRY` hardcodes sibling module-path strings (SEVERITY: NONE — documented coupling)

`_PROMPT_REGISTRY` (L6120) maps prompt names → `("bulk_downloader.aiassist" | ".ai_login" |
".template_extractor", attribute, samples)` and loads them via `import_module` (F12). Move-safe for the
dev_suite split (those modules don't move), but a real cross-module coupling: if any of those modules were
*also* decomposed, these strings would need updating. Noted for the cross-target program.

## Round-2 confirmed-clean (no action; recorded to bound the search)

No `global` statements; no `__module__`/`__qualname__` of dev_suite's own fns in output (`__name__` uses
are all `type(e).__name__`, move-invariant); no module-level threading/async primitives (Lock/Event); **zero
decorated functions**; no pre-existing `__all__`; dev_suite owns **no routes/Blueprint**; no module-level
`re.compile`; no module-level optional-import try/except guards (all imports are top-of-file stdlib or lazy
in-function); no top-level conditional/redefined defs; **no `import *`** anywhere (so the 125-def list ==
the public `dir()` surface — the frozen list is complete); no literal `"bulk_downloader.dev_suite"` in the
tree (nothing expects dev_suite to be a single module via string path); `_TEST_PNG_B64` is **not** asserted
by any test (safe to relocate with §T9).

---

## Updated landing deltas (round 2)

- **Rename** the `maintenance` submodule → `housekeeping` (F17).
- `_common` carries **constants too** (`_SECRET_KEY_HINTS`, `_BD_ENV_VARS`) alongside the function helpers
  (F15).
- The F7 transform set is **~45 sibling modules** (F14/F16) — convert all to absolute lazy imports; the
  shadowing names (`maintenance`, and guard against future ones) *require* it.
- Leave the two dynamic patterns (F12) untouched.

---

---

# Round 3 — consumption & packaging (F19–F21)

Targeted the angles asserted from memory or unchecked in rounds 1–2: the *consumers* (app.py's 125 sites,
all 89 tests, conftest), packaging/build enumeration, and the `__init__`/`dev_tools` cycle. Net-new
material: **F19, F20**. This round justified itself — verifying the consumption side, not re-reading
internals, is where the remaining real items were.

## F19 — Complete shim-export private set is 5 names (SEVERITY: HIGH — expands F2 precisely)

F2 found the guard imports `_manifest_excluded`. The full set of dev_suite privates imported **directly**
(`from bulk_downloader.dev_suite import _X`) by *any* consumer is five — one function and four constants,
all in the manifest-exclusion section (→ `release_lint`):

| Name | Kind | Imported by |
|---|---|---|
| `_manifest_excluded` | fn | `build_release.py` (guard) + test_v3_66_263/39/43, test_v3_64_3, test_v3_66_59 |
| `_MANIFEST_EXCLUDE_DIRS` | const | test_v3_64_3 |
| `_MANIFEST_EXCLUDE_NAMES` | const | test_v3_66_39, test_v3_64_3 |
| `_MANIFEST_EXCLUDE_PATHS` | const | test_v3_66_263 |
| `_MANIFEST_EXCLUDE_SUFFIXES` | const | test_v3_64_3 |

`__init__.py` must `from .release_lint import (_manifest_excluded, _MANIFEST_EXCLUDE_DIRS,
_MANIFEST_EXCLUDE_NAMES, _MANIFEST_EXCLUDE_PATHS, _MANIFEST_EXCLUDE_SUFFIXES, zip_manifest_check, …)`. Now
enforced executably by `test_manifest_exclude_constants_importable` + the expanded `GUARD_REQUIRED` set in
the surface-lock test (6/6).

## F20 — A TEST reads `dev_suite.__file__` for path math (SEVERITY: MED — external mirror of F1)

`test_u27_security_cluster.py:54`: `here = os.path.dirname(os.path.abspath(ds.__file__))`. On `.py`→package,
`ds.__file__` becomes `…/dev_suite/__init__.py`, so `dirname` resolves to `bulk_downloader/dev_suite/`
instead of `bulk_downloader/` — one level too deep, exactly like F1 but from *outside* the module. Since
it's a test (not a guard), it can be updated with the cut — change to `dirname(dirname(ds.__file__))` or
anchor on the package root via `bulk_downloader.__file__`. **Band `test_u27_security_cluster` and fix this
line in the cut.** (No *non-test* consumer reads `ds.__file__`/`__path__` — verified.)

## F21 — File-count assertions are relative, not absolute (SEVERITY: NONE — confirmed safe)

Checked because +13 submodule files could break a pinned count. All such assertions are **relative**:
`test_dev_suite_tier0` asserts `file_count > 0` and `clean + with_issues == file_count` (counts `.bat`/`.sh`
lint targets, not modules); `test_u26_test_meta` asserts `test_file_count > 50` (counts test files). None is
an absolute module-count pin → adding submodules is safe.

## Round-3 confirmed-clean

All **125 app.py call sites** use `from . import dev_suite as _ds` (module-attribute — shim-covered; **zero**
direct-name imports); **no** `__init__.py` or `dev_tools.py` import of dev_suite (no `_common`→`__init__`→
dev_suite cycle; gating is one-way); **no** module-level `__getattr__`/`__dir__` (no PEP-562 shim to
replicate); build `_walk_tree` uses `root.rglob("*")` — **recursive**, so `dev_suite/*.py` submodule files
ship automatically with no path-list edit; `tests/conftest.py` is dev_suite-neutral (generic
`isolated_bd_home` BD_HOME/chdir fixture).

---

## Behavioral test net (band these — they are the safety net the surface-lock can't be)

The 89-file family maps almost 1:1 to sections. The decomposition cut's `--suites` must include, at
minimum: `test_dev_suite`, `test_dev_suite_tier0`, `test_dev_suite_tier1`, `test_dev_suite_tier1b`,
`test_dispatch_chain` (F1 runner.py path), `test_inv_tags_not_regressed` (F4), `test_u29_config_snapshot`,
`test_config_snapshot_diff`, `test_u20_pipeline_preview`, `test_v3_66_303_import_preview_redaction`,
`test_u26_test_meta`, the `test_t*` family (esp. `test_t2_fixture_controller` F5, `test_t17_filesystem_audit`
F1), the new `test_dev_suite_surface_lock`, plus `test_dependency_graph_in_sync` and
`test_function_index_in_sync` (prove the only-DEPENDENCY_GRAPH-moves claim). Run from the **extracted zip**.
